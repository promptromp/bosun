#!/bin/bash
# Usage: tests/app_e2e.sh before-reboot | after-reboot
#
# bosun.app on a real Mac, driven through every state and choice of its menu (docs/design/menu-bar.md): the
# app is started with `--test-hooks <dir>`, which makes it write its menu to menu.json after each render, take
# the clicks and the answers to its dialogs from actions/<n>.json, and log every command it runs and every address
# it would open to log.jsonl. The checks read those, `bosun status --json`, `ps`, launchctl and the tools of the
# desktop. It needs a logged-in desktop session (the status item), so it runs only in the throwaway Mac of
# `tests/sandbox.sh mac app`, which starts it in two parts with a restart of the Mac between them: the part
# before keeps its counts in ~/.app-e2e-results, the part after adds to them and ends with the summary.
#
# BOSUN_E2E_APP_FROM=build (the default): the app is the ad hoc one app/build.sh made, copied in as ~/e2e/app.zip
# and placed in ~/Applications the way `bosun app install` places an app (`bosun init` cannot offer an app that is
# not the publisher's: there is no Team ID), BOSUN_MENU_APP and the console's job set as `bosun init` sets them
# with the app. release: `bosun init` installs the published, notarized app, as a person gets it, and the suite
# checks Gatekeeper accepts it (that run needs a release and a Team ID in app/publisher.json).
#
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
# The guard (it refuses to run anywhere else), the checks and the steps tests/install_e2e.sh shares.
source "$(dirname "$0")/sandbox/e2e_lib.sh" || exit 1

PART="${1:-}"
FROM="${BOSUN_E2E_APP_FROM:-build}"
BOSUN="$HOME/.local/bin/bosun"
HOOKS="$HOME/menu-hooks" # no bosun in its name: the last check looks for what bosun left
APP="$HOME/Applications/bosun.app"
APP_EXEC="$APP/Contents/MacOS/bosun"
APP_JOB="$LABEL.app"
APP_PLIST="$HOME/Library/LaunchAgents/$APP_JOB.plist"
RESULTS="$HOME/.app-e2e-results"
STAND_IN="$HOME/.sandbox/claude"
WAIT_POLL=100 # seconds: the app asks for the status every 30, and a check on it may follow a change by that long

# ---- the app: its processes, its menu, its actions and its log -----------------------------------------------

app_pids() { pgrep -f "^$APP_EXEC( |\$)"; }
app_running() { app_pids >/dev/null; }
app_gone() { ! app_running; }
app_count() { app_pids | wc -l | tr -d ' '; }
app_parent_is_launchd() { [ "$(ps -o ppid= -p "$(app_pids | head -1)" | tr -d ' ')" = 1 ]; }
launch_app() { # with the hooks, from a fresh menu.json: `open`, as a person opens it
  mkdir -p "$HOOKS"
  rm -f "$HOOKS/menu.json"
  open "$APP" --args --test-hooks "$HOOKS"
}

menu_says() { jq -e "$1" "$HOOKS/menu.json" >/dev/null 2>&1; } # <jq filter over menu.json>
menu_title() {                                                 # <item id>: its title, whichever section or submenu it is in; empty when the menu has none
  jq -r --arg id "$1" '[.sections[][] | recurse(.children[])] | map(select(.id == $id)) | first | .title // empty' "$HOOKS/menu.json" 2>/dev/null
}
menu_title_is() { [ "$(menu_title "$1")" = "$2" ]; }
menu_title_starts() { [[ "$(menu_title "$1")" == "$2"* ]]; }
menu_green() { menu_says '.level == "ok" and .header.dot == "green"'; }
menu_green_with_windows() { menu_green && menu_says '.header.detail | any(test("[0-9]+ windows? open"))'; }
menu_stopped() { menu_says '.level == "stopped" and .header.dot == "grey"'; }
menu_matches_status() { # what the menu says is what `bosun status --json` says
  [ "$("$BOSUN" status --json | jq -c '[.level, .sentence, .detail]')" = "$(jq -c '[.level, .header.sentence, .header.detail]' "$HOOKS/menu.json")" ]
}

act() { # <json>: the next action for the app, written whole and then given its name (it takes <n>.json, in order)
  local n
  mkdir -p "$HOOKS/actions"
  n="$(($(cat "$HOOKS/.n" 2>/dev/null || echo 0) + 1))"
  echo "$n" >"$HOOKS/.n"
  printf '%s\n' "$1" >"$HOOKS/actions/$n.tmp" && mv "$HOOKS/actions/$n.tmp" "$HOOKS/actions/$n.json"
}
click() { act "$(jq -cn --arg item "$1" '{item: $item}')"; } # <item name>
reply() {                                                    # <button> [<the boxes, as JSON>]
  local boxes="${2:-}"
  [ -n "$boxes" ] || boxes='{}'
  act "$(jq -cn --arg b "$1" --argjson boxes "$boxes" '{answer: {button: $b, boxes: $boxes}}')"
}
actions_taken() { [ -z "$(ls "$HOOKS/actions" 2>/dev/null)" ]; }

log_count() { wc -l <"$HOOKS/log.jsonl" 2>/dev/null | tr -d ' ' || echo 0; }
log_has() { # <lines to skip> <jq test on one entry>: an entry after the first <lines to skip> is so
  tail -n +"$(($1 + 1))" "$HOOKS/log.jsonl" 2>/dev/null | jq -e -s "any(.[]; $2)" >/dev/null
}
log_has_no_errors() { # <lines to skip>: nothing the app itself could not do (a hook's error) after them
  ! tail -n +"$(($1 + 1))" "$HOOKS/log.jsonl" 2>/dev/null | jq -e -s 'any(.[]; .error? != null)' >/dev/null
}
last_opened() { jq -r 'select(.open? != null) | .open' "$HOOKS/log.jsonl" | tail -1; }

# The stand-in agent of a window: idle, or busy (a turn that goes on), read again every second.
agent() { mkdir -p "$STAND_IN" && echo "$2" >"$STAND_IN/$1.mode"; } # <window> idle|busy
busy_in_plan() { "$BOSUN" quit --plan --json | jq -e '.busy | index("acme-api")' >/dev/null; }

# ---- setting the Mac up ----------------------------------------------------------------------------------------

set_env() { # <KEY> <value>: in bosun.env, as bosun init writes it (replaced, or added)
  if grep -q "^$1=" "$CONFIG/bosun.env"; then
    sed -i '' "s|^$1=.*|$1=\"$2\"|" "$CONFIG/bosun.env"
  else
    printf '%s="%s"\n' "$1" "$2" >>"$CONFIG/bosun.env"
  fi
}

# bosun init's answers: the one repo window, the schedule open from 02:00 to 23:59 every day (so no check depends on the
# hour the run happens at; not from 00:00, whose wake `bosun doctor` reads as 12:01AM where pmset says 0:01AM) and no learning.
init_with_answers() {
  local menu_app=false answers="$OUT/answers.json"
  [ "$FROM" = release ] && menu_app=true
  jq -n --argjson app "$menu_app" '{repos: [{dir: "~/Development/acme-api"}], days: "MTWRFSU", start: "02:00",
    until: "23:59", learning: false, menu_app: $app}' >"$answers"
  check "bosun init --answers: the window acme-api, the schedule all day (menu_app: $menu_app)" bash -c '
    "$1" init --force --answers "$2" >"$3" 2>&1 || { tail -15 "$3"; exit 1; }' _ "$BOSUN" "$answers" "$OUT/init.txt"
  check "... sessions.yml has the window" grep -q "name: acme-api" "$CONFIG/sessions.yml"
}

# What `bosun init` sets with the app, which an app that is not the publisher's cannot be offered by it: the app's
# key, the console's job (install.sh loads it), and a quit that does not wait for busy agents (a 0-minute wait,
# the busy-agent steps' second dialog).
settings_for_the_app() {
  set_env BOSUN_QUIT_WAIT_MINUTES 0
  [ "$FROM" = release ] || {
    set_env BOSUN_MENU_APP 1
    set_env BOSUN_CONSOLE_ALWAYS 1
    check "install.sh loads the console's job, as bosun init does with the app" bash -c '"$1/install.sh" >"$2" 2>&1 || { tail -15 "$2"; exit 1; }' _ "$DIR" "$OUT/install-again.txt"
  }
  check "bosun.env says the app is wanted, the console always on, and a quit that waits 0 minutes" bash -c '
    grep -qx "BOSUN_MENU_APP=\"1\"" "$1" && grep -qx "BOSUN_CONSOLE_ALWAYS=\"1\"" "$1" && grep -qx "BOSUN_QUIT_WAIT_MINUTES=\"0\"" "$1"' _ "$CONFIG/bosun.env"
}

# The app as `bosun app install` leaves it, from the zip app/build.sh made (the test's own tarball holds no app/).
place_built_app() {
  mkdir -p "$HOME/Applications"
  check "the built app unpacks with ditto into ~/Applications" ditto -x -k "$ROOT/app.zip" "$HOME/Applications"
  check "... it is one signed bundle (codesign --verify --deep --strict)" codesign --verify --deep --strict "$APP"
  check "... and it is not notarized (an ad hoc build: spctl does not say Notarized Developer ID)" bash -c '
    ! spctl --assess --type execute -vv "$1" 2>&1 | grep -qx "source=Notarized Developer ID"' _ "$APP"
}

# --from release: bosun init installed the notarized app, and opened it at login at once. What a person gets.
check_the_release() {
  spctl --status | grep -q "assessments enabled" || { sudo spctl --global-enable && echo "  note  assessments were disabled on this Mac: enabled"; }
  check "Gatekeeper accepts the download: source=Notarized Developer ID" bash -c 'spctl --assess --type execute -vv "$1" 2>&1 | grep -qx "source=Notarized Developer ID"' _ "$APP"
  check "... and the app carries no quarantine mark, so it starts with no prompt" bash -c '! xattr -p com.apple.quarantine "$1" 2>/dev/null' _ "$APP"
  check "bosun init turned Open at Login on" test -f "$APP_PLIST"
  check "... which started the app: it runs" wait_for 60 app_running
  python3 "$DIR/lib/app_install.py" quit >/dev/null 2>&1
  check "the app, without hooks, is quit again, for the one that is driven" wait_for 60 app_gone
}

app_is_installed() {
  check "bosun app status --json: the app is installed, at the version the install wants, in ~/Applications" bash -c '
    "$1" app status --json | jq -e --arg path "$2" ".contract == 1 and .installed != null and .installed == .wanted and .path == \$path"' _ "$BOSUN" "$APP"
}

# ---- 1. install, the app launched, its menu green with the windows -------------------------------------------

install_with_the_app() {
  a_new_machine
  follow_what_it_says
  install
  check "... versions side by side, the install path a link to the version in use" side_by_side
  init_with_answers
  settings_for_the_app
  if [ "$FROM" = release ]; then check_the_release; else place_built_app; fi
  app_is_installed
}

step_1() {
  echo "1. bosun is up, the app is opened: its menu is green, with the windows"
  check "bosun morning --now --awake-minutes 30 brings the windows up, with a keep-awake" "$BOSUN" morning --now --awake-minutes 30
  check "... the orchestrator's window and the repo's" bash -c 'tmux -L bosun list-windows -t "=bosun" -F "#{window_name}" | grep -qx orchestrator && tmux -L bosun list-windows -t "=bosun" -F "#{window_name}" | grep -qx acme-api'
  check "... and the console's job runs" wait_for 30 console_answers
  launch_app
  check "the app runs, started by Launch Services (open): launchd is its parent, not this login" wait_for 30 app_parent_is_launchd
  check "... and writes its menu: the hooks' menu.json" wait_for 60 menu_says '.level != null'
  check "its menu is green, and says the windows are open" wait_for "$WAIT_POLL" menu_green_with_windows
  jq -c '.header' "$HOOKS/menu.json" | sed 's/^/  note  the header: /'
  check "... what bosun status --json says: the level, the sentence and the lines" wait_for 60 menu_matches_status
  check "... Quit is in it, with the dialog's dots, and Open Console is enabled" menu_says '[.sections[][] | recurse(.children[]) | select(.id == "quit" or .id == "openConsole")] | map(.enabled) == [true, true]'
  check "... and there is no Start (bosun runs) nor What's Wrong (nothing is)" bash -c '! jq -e "[.sections[][] | .id] | any(. == \"startBosun\" or . == \"showProblems\")" "$1"' _ "$HOOKS/menu.json"
  check "the app has not run anything but reading: status, the app's state and the update check" bash -c '
    jq -s -e "[.[] | select(.command != null) | .command[0]] | unique - [\"status\", \"app\", \"update\"] | length == 0" "$1"' _ "$HOOKS/log.jsonl"
}

# ---- 2. a problem: yellow, "Show What's Wrong…" lists it; and back --------------------------------------------

an_error_line() { printf '%s [checkin] ERROR: the app suite made this up\n' "$(date '+%Y-%m-%d %H:%M:%S')"; }

step_2() {
  local log="$STATE/logs/checkin.log" mark
  echo "2. an ERROR line in a log: yellow, Show What's Wrong lists it; the line gone: green"
  mark="$(log_count)"
  an_error_line >>"$log"
  check "the menu turns yellow, with a warning" wait_for "$WAIT_POLL" menu_says '.level == "warning" and .header.dot == "yellow" and .icon_badge == "warning"'
  check "... bosun status --json says the same, and names the line" bash -c '"$1" status --json | jq -e ".level == \"warning\" and (.problems | any(contains(\"made this up\")))"' _ "$BOSUN"
  check "... and the menu has Show What's Wrong…" menu_title_is showProblems "Show What's Wrong…"
  click showProblems
  reply OK
  check "choosing it opens the dialog, and OK closes it (both actions taken)" wait_for 30 actions_taken
  check "... with no error of the app's in its log" log_has_no_errors "$mark"
  sed -i '' '/the app suite made this up/d' "$log"
  check "the line removed, the menu is green again" wait_for "$WAIT_POLL" menu_green_with_windows
  check "... and Show What's Wrong is gone from it" bash -c '! jq -e "[.sections[][] | .id] | any(. == \"showProblems\")" "$1"' _ "$HOOKS/menu.json"
}

# ---- 3. Open Console -------------------------------------------------------------------------------------------

step_3() {
  local mark url port jar="$OUT/console.jar"
  echo "3. Open Console: the address it opens answers with its token"
  mark="$(log_count)"
  click openConsole
  check "the app asks bosun for the address and opens it (logged: the hooks open nothing)" wait_for 60 log_has "$mark" '.command == ["console", "--url"] and .outcome == "exited 0"'
  check "... an http address on this Mac, with a token" wait_for 30 bash -c 'last="$(jq -r "select(.open? != null) | .open" "$1" | tail -1)"; [[ "$last" =~ ^http://127\.0\.0\.1:[0-9]+/\?t=[A-Za-z0-9_-]+$ ]]' _ "$HOOKS/log.jsonl"
  url="$(last_opened)"
  port="$(sed -E 's#^http://127\.0\.0\.1:([0-9]+)/.*#\1#' <<<"$url")"
  check "curl with the token gets the console's page" bash -c 'curl -sfL -c "$2" "$1" | grep -qi "<html"' _ "$url" "$jar"
  check "... and with the cookie it set, its API answers" bash -c 'curl -sf -b "$2" "http://127.0.0.1:$1/api/version" >/dev/null' _ "$port" "$jar"
  check "... without the token it does not" bash -c '[ "$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$1/api/version")" != 200 ]' _ "$port"
}

# ---- 4. Check for Updates, the offer, the plan, the update and its wait ----------------------------------------

the_menu_has_the_update_title() { menu_title_starts checkForUpdates "$1"; } # <start of the title>

step_4() {
  local next="v2026.11.02" mark commit
  echo "4. a newer release: Check for Updates offers it, Update Now runs it, the menu shows it waiting"
  check "the next release is made: the patch commit, tagged $next, in the origin" a_next_release "$next"
  commit="$(git -C "$OUT/next" rev-parse HEAD)"
  agent acme-api busy
  check "a busy agent in acme-api (the update waits for it)" wait_for 60 busy_in_plan
  "$BOSUN" update --dry-run 2>&1 | sed 's/^/  note  the plan: /'
  mark="$(log_count)"
  check "the menu's update item is a plain check before" menu_title_is checkForUpdates "Check for Updates…"
  click checkForUpdates
  check "Check for Updates finds $next: the item reads Update Available, and the menu has the update badge" wait_for "$WAIT_POLL" bash -c '
    jq -e --arg v "$2" "(([.sections[][] | select(.id == \"checkForUpdates\")] | first | .title | startswith(\"Update Available: \" + \$v)) and .icon_badge == \"update\")" "$1"' _ "$HOOKS/menu.json" "$next"
  check "... the app ran the update check, which bosun answered" log_has "$mark" '.command == ["update", "--check", "--json"] and .outcome == "exited 0"'
  reply "Update Now"
  reply "Update"
  check "the offer is answered Update Now, the plan Update (both taken)" wait_for 90 actions_taken
  check "... the app asked bosun for the plan, then started the update, detached" wait_for 30 log_has "$mark" '.command == ["update", "--yes"] and .detached == true and .outcome == "started"'
  check "... the plan was asked first: update --dry-run --json" log_has "$mark" '.command == ["update", "--dry-run", "--json"] and .outcome == "exited 0"'
  check "the menu shows the update waiting for the busy window" wait_for "$WAIT_POLL" menu_title_is checkForUpdates "Updating… waiting for 1 window"
  check "... and bosun status --json says an update is running, waiting for acme-api" bash -c '"$1" status --json | jq -e ".update.waiting_for | index(\"acme-api\")"' _ "$BOSUN"
  check "... the install already points at the next release: the link is switched first, and the busy window is what is waited for" test "$(version_in_use)" = "$commit"
  agent acme-api idle
  check "the agent idle, the update finishes: the version in use is the next release's" wait_for 240 bash -c 'test "$(git -C "$(cd "$1" && pwd -P)" rev-parse HEAD)" = "$2"' _ "$DIR" "$commit"
  check "... bosun update --check --json: $next installed, up to date" wait_for 60 bash -c '
    "$1" update --check --json | jq -e --arg v "$2" ".installed.version == \$v and .available == false and .error == null"' _ "$BOSUN" "$next"
  check "... and the menu is green again, its item a plain check again, no badge" wait_for "$WAIT_POLL" bash -c '
    jq -e "(.level == \"ok\") and .header.dot == \"green\" and .icon_badge == null and
      ([.sections[][] | select(.id == \"checkForUpdates\")] | first | .title == \"Check for Updates…\")" "$1"' _ "$HOOKS/menu.json"
  check "... the app still runs, and one copy of it" test "$(app_count)" = 1
  check "the app's own login job is not made by the update (Open at Login is still off)" test ! -e "$APP_PLIST"
}

# ---- 5. Quit with no busy agent, all defaults ------------------------------------------------------------------

step_5() {
  local paused mark
  echo "5. Quit, no busy agent, all defaults: nothing of bosun is left, the app is gone"
  mark="$(log_count)"
  click quit
  reply "Quit bosun"
  check "Quit, and Quit bosun in the dialog (both taken)" wait_for 90 actions_taken
  check "... the app ran the plan, then the quit, with the defaults" wait_for 120 log_has "$mark" '.command[0:2] == ["quit", "--let-finish=0"] and (.command | index("--pause-morning") != null) and (.command | index("--sleep") != null) and .outcome == "exited 0"'
  check "... and the app is gone" wait_for 60 app_gone
  check "no tmux server" no_tmux_server
  check "... no caffeinate" bash -c '! pgrep -x caffeinate'
  check "... no console process" bash -c '! pgrep -f "lib/console_server\.py"'
  check "... quit.json says since when, with the morning paused" jq -e '.morning_paused == true and (.since | type == "string")' "$STATE/quit.json"
  check "... bosun status --json says stopped, the morning paused" status_says '.level == "stopped" and .stopped.morning_paused == true and (.windows == 0)'
  : >"$STATE/logs/watchdog.launchd.out"
  launchctl kickstart -k "$DOMAIN/$LABEL.watchdog"
  check "the watchdog, kickstarted, logs that it skipped" wait_for 90 watchdog_skipped
  check "... and started nothing" no_tmux_server
  paused="$(morning_lines "the morning start is paused")"
  launchctl kickstart -k "$DOMAIN/$LABEL"
  check "the morning job, kickstarted, logs that its start is paused" wait_for 90 morning_lines_above "the morning start is paused" "$paused"
  sleep 10
  check "... and starts nothing: no tmux server, no console, no caffeinate" bash -c '! tmux -L bosun info && ! pgrep -f "lib/console_server\.py" && ! pgrep -x caffeinate'
  check "... and the app was not brought back by any of it" app_gone
}

# ---- 6. opened again: it starts bosun; a busy agent; Keep bosun in the menu bar --------------------------------

step_6() {
  local mark
  echo "6. the app opened again: it starts bosun; Quit with a busy agent; Keep bosun in the menu bar"
  mark="$(log_count)"
  launch_app
  check "opened with bosun stopped, the app starts it: morning --now --background, logged" wait_for 90 log_has "$mark" '.command == ["morning", "--now", "--background"] and .outcome == "exited 0"'
  check "... the windows come up" wait_for 120 has_window orchestrator
  check "... the quit is over: no quit.json" test ! -e "$STATE/quit.json"
  check "... and the menu is green, with the windows" wait_for "$WAIT_POLL" menu_green_with_windows
  agent acme-api busy
  check "a busy agent in acme-api" wait_for 60 busy_in_plan
  mark="$(log_count)"
  click quit
  reply "Quit bosun"
  reply "Let Them Finish"
  reply "Stop It Now"
  check "Quit, Quit bosun, the busy dialog's Let Them Finish, the still-busy dialog's Stop It Now (all four taken)" wait_for 180 actions_taken
  check "... the quit that let them finish waited 0 minutes, found acme-api busy, and the app asked again" wait_for 120 log_has "$mark" '.command[0:2] == ["quit", "--let-finish=0"] and .outcome != null'
  check "... then stopped them now" wait_for 120 log_has "$mark" '.command[0:2] == ["quit", "--stop-now"] and (.command | index("--pause-morning") != null) and .outcome == "exited 0"'
  check "... the app is gone" wait_for 60 app_gone
  check "... and bosun is stopped: no tmux server, status says stopped" bash -c '! tmux -L bosun info && "$1" status --json | jq -e ".level == \"stopped\""' _ "$BOSUN"
  agent acme-api idle

  echo "6b. Quit with Keep bosun in the menu bar: the grey stopped menu, Start: green"
  mark="$(log_count)"
  launch_app
  check "opened again, it starts bosun and the menu is green" wait_for "$WAIT_POLL" menu_green_with_windows
  click quit
  reply "Quit bosun" '{"keepIcon": true}'
  check "Quit, with the icon kept (both taken)" wait_for 120 actions_taken
  check "... the menu is the grey stopped menu" wait_for "$WAIT_POLL" menu_stopped
  check "... bosun is stopped" no_tmux_server
  check "... the app stays: it runs" app_running
  check "... and offers Start bosun, with Open Console greyed" bash -c 'jq -e "([.sections[][] | select(.id == \"startBosun\")] | length == 1) and ([.sections[][] | select(.id == \"openConsole\")] | first | .enabled == false)" "$1"' _ "$HOOKS/menu.json"
  mark="$(log_count)"
  click startBosun
  check "Start bosun runs morning --now --background" wait_for 60 log_has "$mark" '.command == ["morning", "--now", "--background"] and .outcome == "exited 0"'
  check "... and the menu is green again, with the windows" wait_for "$WAIT_POLL" menu_green_with_windows
  check "... bosun's windows are up" has_window orchestrator
}

# ---- 8. launched again while running: still one process --------------------------------------------------------

step_8() {
  local pid
  echo "8. launched again while it runs: still one app process"
  check "one app process before" test "$(app_count)" = 1
  "$APP_EXEC" --at-login >/dev/null 2>&1 &
  pid=$!
  check "a copy started the way launchd's login job starts it (the executable, --at-login) exits at once" wait_for 20 bash -c '! kill -0 "$1" 2>/dev/null' _ "$pid"
  check "... still one app process" test "$(app_count)" = 1
  open "$APP"
  sleep 5
  check "open on the running app (LaunchServices) is still one process" test "$(app_count)" = 1
  check "... and its menu still answers: the hooks are the first copy's" menu_says '.level != null'
}

# ---- 7 (before the restart). Open at Login on; the Automation check ----------------------------------------------

an_automation_prompt() { pgrep -x UserNotificationCenter >/dev/null; }

# What macOS says when something asks the app to quit with an Apple Event (osascript, as `bosun app update` and
# `bosun app remove` do): reported, and a check that it went through with no prompt and no -1743.
ask_the_app_to_quit_from_here() {
  local out="$OUT/quit-from-ssh.txt" status started=$SECONDS
  echo "the Automation check, asked from this login (as a person's terminal would):"
  "$DIR/bin/bosun" app status >/dev/null 2>&1
  python3 "$DIR/lib/app_install.py" quit >"$out" 2>&1
  status=$?
  echo "  note  lib/app_install.py quit (osascript: tell application id \"$(jq -r .bundle_id "$DIR/app/publisher.json")\" to quit) exited $status after $((SECONDS - started)) s; it said: $(tr '\n' ' ' <"$out")"
  echo "  note  a UserNotificationCenter process (the TCC prompt's) after it: $(pgrep -x UserNotificationCenter | tr '\n' ' ')"
  sudo -n log show --predicate 'subsystem == "com.apple.TCC" AND eventMessage CONTAINS "AppleEvents"' --last 2m --style compact 2>&1 | grep -c 'kTCCServiceAppleEvents' | sed 's/^/  note  TCC log lines for kTCCServiceAppleEvents in the last 2 minutes: /'
  check "asking the app to quit from a login with an Apple Event: exit 0, no -1743, and no Automation prompt" bash -c '[ "$1" = 0 ] && ! grep -q "1743" "$2" && ! pgrep -x UserNotificationCenter' _ "$status" "$out"
  check "... and the app quit" wait_for 30 app_gone
}

step_7_login() {
  local mark
  echo "7. Open at Login on (the restart follows)"
  mark="$(log_count)"
  check "bosun app status says login false before" bash -c '"$1" app status --json | jq -e ".login == false"' _ "$BOSUN"
  click openAtLogin
  check "choosing it runs bosun app login on" wait_for 60 log_has "$mark" '.command == ["app", "login", "on"] and .outcome == "exited 0"'
  check "... the login job's plist is in LaunchAgents and loaded" bash -c 'test -f "$1" && launchctl print "$2/$3" >/dev/null' _ "$APP_PLIST" "$DOMAIN" "$APP_JOB"
  check "... bosun app status --json says login true" bash -c '"$1" app status --json | jq -e ".login == true"' _ "$BOSUN"
  check "... and the menu item is checked" wait_for "$WAIT_POLL" menu_says '[.sections[][] | select(.id == "openAtLogin")] | first | .checked == true'
  sleep 10
  check "loading the job ran the app at once (RunAtLoad): the second copy exited, one app process" test "$(app_count)" = 1
  check "... and it is still the first copy: it has the hooks" bash -c 'ps -o args= -p "$(pgrep -f "^$1( |\$)" | head -1)" | grep -q -- "--test-hooks"' _ "$APP_EXEC"
}

# The update the app starts runs the next version's own code. This one's first line asks the app to quit, as
# `bosun app update` does when it brings another app version (tests/sandbox/update_quit_probe.sh), so what macOS
# says to a quit that comes from a child of the app is seen.
step_automation_from_the_app() {
  local next="v2026.11.03" commit mark out="$HOME/.sandbox/quit-probe.log" seen=""
  echo "the Automation check, asked by the update the app itself started (a child of the app):"
  check "a third release is made, whose update first asks the app to quit: $next" a_next_release "$next" "$ROOT/tests/sandbox/update_quit_probe.sh"
  commit="$(git -C "$OUT/next" rev-parse HEAD)"
  mkdir -p "$HOME/.sandbox"
  : >"$HOME/.sandbox/quit-probe"
  : >"$out"
  mark="$(log_count)"
  click checkForUpdates
  reply "Update Now"
  reply "Update"
  check "Check for Updates, Update Now and Update (all three taken)" wait_for 180 actions_taken
  check "... the app started the update, detached" log_has "$mark" '.command == ["update", "--yes"] and .detached == true and .outcome == "started"'
  check "the update's first line asked the app to quit, and what it got is on record" wait_for 120 grep -q "probe exit" "$out"
  echo "  note  what the update's call said:"
  sed 's/^/  note    /' "$out"
  an_automation_prompt && seen="a UserNotificationCenter process is running"
  echo "  note  an Automation prompt after it: ${seen:-none (no UserNotificationCenter process)}"
  check "asking the app to quit from the update it started: exit 0, no -1743, and no Automation prompt" bash -c 'grep -q "probe exit 0" "$1" && ! grep -q "1743" "$1" && ! pgrep -x UserNotificationCenter' _ "$out"
  check "... and the app quit" wait_for 60 app_gone
  check "the update went on to the end: the version in use is the third release's" wait_for 240 bash -c 'test "$(git -C "$(cd "$1" && pwd -P)" rev-parse HEAD)" = "$2"' _ "$DIR" "$commit"
  rm -f "$HOME/.sandbox/quit-probe"
}

before_reboot() {
  install_with_the_app
  step_1
  step_2
  step_3
  step_4
  step_5
  step_6
  step_8
  step_7_login
  ask_the_app_to_quit_from_here
  launch_app
  check "the app, opened again with the hooks, shows its menu" wait_for 60 menu_says '.level != null'
  step_automation_from_the_app
  echo "the restart: bosun is stopped (bosun stop) so that nothing but the schedule could start it, and the app is not running"
  "$BOSUN" stop >/dev/null 2>&1
  check "bosun is stopped before the restart: no tmux server" no_tmux_server
  check "... Open at Login is still on" test -f "$APP_PLIST"
  echo "$passed $failed" >"$RESULTS"
}

# ---- after the restart: 7, and 9 -----------------------------------------------------------------------------------

app_args() { ps -o args= -p "$(app_pids | head -1)"; }

step_7_after() {
  echo "7. after the restart and the login: the app runs with --at-login, and bosun is not started"
  check "launchd's login job started the app: it runs" wait_for 180 app_running
  check "... as the login job runs it, with --at-login and no hooks" bash -c 'args="$(ps -o args= -p "$(pgrep -f "^$1( |\$)" | head -1)")"; echo "$args"; [[ "$args" == *--at-login* && "$args" != *--test-hooks* ]]' _ "$APP_EXEC"
  check "... one app process" test "$(app_count)" = 1
  check "... and the login job is loaded, its plist still in LaunchAgents" bash -c 'test -f "$1" && launchctl print "$2/$3" >/dev/null' _ "$APP_PLIST" "$DOMAIN" "$APP_JOB"
  sleep 20
  check "bosun was not started: no tmux server, no caffeinate" bash -c '! tmux -L bosun info && ! pgrep -x caffeinate'
  echo "  note  the console's job runs again at login, as its marker is still there (bosun stop does not remove it, bosun quit does): $(pgrep -f 'lib/console_server\.py' | tr '\n' ' ')"
  check "... and bosun status --json says stopped" status_says '.level == "stopped"'
  check "the other jobs came back with the login: the morning, watchdog and console jobs are loaded" bash -c 'for job in "$2" "$2.watchdog" "$2.console"; do launchctl print "$1/$job" >/dev/null || exit 1; done' _ "$DOMAIN" "$LABEL"
}

# ---- 9. the app moved to the Trash; uninstall ----------------------------------------------------------------------

step_9() {
  local trash="$HOME/.Trash"
  echo "9. the app moved to the Trash: the watchdog says so; bosun uninstall leaves nothing"
  check "with the app in ~/Applications the watchdog's app-missing check finds nothing" bash -c '"$1" watchdog --status 2>&1 | grep -i "app-missing" | grep -qi "is in ~/Applications"' _ "$BOSUN"
  mv "$APP" "$trash/bosun.app"
  check "moved to the Trash, the watchdog's app-missing check finds it gone (its alert, held no longer: the schedule is all day)" bash -c '"$1" watchdog --dry-run 2>&1 | tee "$2" | grep -q "bosun.app is not in ~/Applications"' _ "$BOSUN" "$OUT/watchdog-dry.txt"
  sed 's/^/  note  /' "$OUT/watchdog-dry.txt"
  check "bosun watchdog --once records and sends it: app-missing is alerted" bash -c '"$1" watchdog --once >"$2" 2>&1; grep -q "app-missing" "$3/watchdog.json"' _ "$BOSUN" "$OUT/watchdog-once.txt" "$STATE"
  sed 's/^/  note  /' "$OUT/watchdog-once.txt"
  check "bosun still runs: the Trash took the app only (the install and its jobs are there)" bash -c '"$1" status --json | jq -e ".contract == 1" && launchctl print "$2/$3" >/dev/null' _ "$BOSUN" "$DOMAIN" "$LABEL"
  mv "$trash/bosun.app" "$APP"
  check "the app put back, bosun app status sees it again" bash -c '"$1" app status --json | jq -e ".installed != null"' _ "$BOSUN"
  check "bosun uninstall --yes --purge ends well, asking the running app to quit (the login job's copy)" bash -c '"$1" uninstall --yes --purge >"$2" 2>&1 || { tail -20 "$2"; exit 1; }' _ "$BOSUN" "$OUT/uninstall.txt"
  cat "$OUT/uninstall.txt"
  check "the app is gone: no process" app_gone
  check "... not in ~/Applications" test ! -e "$APP"
  check "... the Open at Login job is unloaded and its plist gone" bash -c '! launchctl print "$1/$2" >/dev/null 2>&1 && [ ! -e "$3" ]' _ "$DOMAIN" "$APP_JOB" "$APP_PLIST"
  jobs_are_gone
  check "nothing named bosun is left in this home folder" bash -c '[ -z "$1" ] || { echo "$1"; exit 1; }' _ "$(named_bosun)"
}

after_reboot() {
  [ -f "$RESULTS" ] && read -r passed failed <"$RESULTS"
  step_7_after
  step_9
  rm -f "$RESULTS"
  echo
  echo "$passed passed, $failed failed"
  [ "$failed" -eq 0 ]
}

case "$PART" in
  before-reboot) before_reboot ;;
  after-reboot) after_reboot ;;
  *)
    echo "Usage: tests/app_e2e.sh before-reboot | after-reboot" >&2
    exit 2
    ;;
esac
