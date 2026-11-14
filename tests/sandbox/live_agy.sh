# shellcheck shell=bash
# The real Antigravity (agy) on a real throwaway Mac: the steps `prepare agy`, `login agy` and `go agy` of
# tests/sandbox/live.sh, which sources this once it has defined what these use (check, wait_for, window,
# idle, the_repo, first_run_screens, pid_of, screen_of, and OUT, ROOT, CONFIG, STATE). Design and what each
# check proves: docs/design/antigravity.md, "Testing".
#
# What is different from the other agents:
#   - A person signs in, in the VM's desktop session, with a browser (`login`). The login is a Keychain item
#     that agy reads only in that session, never over ssh. So bosun's windows are started by its launchd job
#     in the desktop session (launchctl kickstart), never by `bosun start` from this ssh session, and what
#     needs the login (the installer's and doctor's checks) runs through `gui_do`. Everything else is read
#     over ssh: `bosun sessions --json`, files, `tmux capture-pane`.
#   - macOS 27.2 shows no process's environment to `ps eww`, so what agy's environment holds is read from
#     agy's own hooks: a copy of bosun's plugin records every hook's payload and answer (live/agy-hook-record).
#   - The repo window runs agy; the orchestrator stays on Claude Code, with the Anthropic key as for the
#     other agents. The person's Google login stays in the clone, which mac.sh deletes at the end.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here. A function a check runs
# is run in a subshell, so what it passes on goes through files in $OUT. bash 3.2.
# shellcheck disable=SC2016

AGY_HOME="$HOME/.gemini"
AGY_PLUGIN_DIR="$AGY_HOME/config/plugins/bosun"
PLUGIN_SOURCE="$HOME/.local/share/bosun/agents/agy-plugin" # the install link's copy: what bosun installs
MORNING_LABEL="com.bosun.morning"
GUI_UID="$(id -u)"
AGY_WINDOW="exporter"
FACT_WINDOW="fresh"
FACT_REPO="$HOME/Development/factrepo"
HOOKLOG="$HOME/hooklog"
FACTS="$HOME/agy-facts.txt" # across the steps, which are processes of their own
TREE_BEFORE="$HOME/gemini-tree-before.txt"
FSMON_MARKER="$HOME/fsmonitor-ran"
WT_WINDOW="freshwt"
WT_REPO="$HOME/Development/factrepo-wt" # a worktree of FACT_REPO: its .git is a file
HOOK_MARKER="$HOME/pre-commit-hook-ran"
SIGN_IN_SECONDS=900
CODE_WORD="ZEBRAFISH"

REMEMBER_PROMPT="Remember this: the code word is $CODE_WORD. Count from 1 to 40 in words, one number per line, and end with the line: noted"
RECALL_PROMPT="What was the code word I told you before? Reply with just that word, in lowercase letters."
LONG_PROMPT="Write the numbers from 1 to 400 in words, one per line, each followed by a short sentence about that number. Do not use any tools."
APPROVAL_PROMPT="In this repo, do these steps in order. Run each as its own shell command with your command-running tool (never your file tools, never two steps in one command), and do not ask me anything. 1. git checkout -b feature-live 2. echo feature > FEATURE.txt 3. git add FEATURE.txt 4. git commit -m 'a feature' 5. git push origin feature-live 6. gh pr list 7. ls. Then reply with just: done"
PUSH_MAIN_PROMPT="In this repo, do these steps in order. Run each as its own shell command with your command-running tool (never your file tools, never two steps in one command), and do not ask me anything. 1. git checkout main 2. echo a note > NOTES.txt 3. git add NOTES.txt 4. git commit -m note 5. git push origin main. Then reply with just: done"
SLOW_PROMPT="Run exactly one shell command, with your command-running tool: gh pr list. Then reply with just: done"
CWD_PROMPT="With your command-running tool, run pwd in the folder /tmp: if the tool has a working-directory option, use it, otherwise use cd. Then reply with just: done"
GIT_DIR_PROMPT="Run each of these as its own shell command with your command-running tool: touch .git/probe-top, then touch nested/.git/probe-nested. Then reply with just: done"
BARE_PROMPT="Run each of these as its own shell command with your command-running tool: git push probe HEAD:refs/heads/probe-branch, then cd probe-bare.git && git rev-parse --git-dir > ../bare-discovery.txt 2>&1. Then reply with just: done"
STATUS_PROMPT="Run git status with your command-running tool, then reply with just: done"
GH_VERSION_PROMPT="Run gh --version with your command-running tool, then reply with just: done"
SHELL_SET_PROMPT='Run this as one shell command with your command-running tool, exactly as written: BOSUN_PROBE_VAR=kept; export BOSUN_PROBE_VAR; probe_fn() { echo fn; }; echo $$ > shell-pid-1.txt. Then reply with just: done'
SHELL_CHECK_PROMPT='Run this as one shell command with your command-running tool, exactly as written: echo "var=${BOSUN_PROBE_VAR:-unset} fn=$(type probe_fn 2>&1 | head -1) pid=$$" > shell-reuse.txt. Then reply with just: done'
WORKTREE_PROMPT="Run each of these as its own shell command with your command-running tool: cp .git git-file-copy.txt, then echo probe >> .git. Then reply with just: done"
HOOKS_PATH_PROMPT="Run git rev-parse --git-path hooks with your command-running tool, then reply with just the line it printed"
COMMIT_PROMPT="Run git commit --allow-empty -m probe with your command-running tool, then reply with just: done"

fact() { # <name> <what the run saw>: kept for the end of the run, and said now
  printf '%s: %s\n' "$1" "$2" >>"$FACTS"
  printf '  fact  %s: %s\n' "$1" "$2" | cut -c1-420
}

# ---- the desktop session, and the login ---------------------------------------------------------------

# <command...> in the desktop session, the only one where agy's Keychain login is seen: root asks launchd to run
# it in the user's session, as the user, with what get.sh and bosun need passed along.
# Not proven to work in this VM, so it is tried once, and without it the command is run as it is: the Keychain
# item is found over ssh too (asked for without -w), which is all the installer's and doctor's checks need.
GUI_OK="$(sudo -n launchctl asuser "$GUI_UID" true >/dev/null 2>&1 && echo 1)"
gui_do() {
  if [ -n "$GUI_OK" ]; then
    sudo launchctl asuser "$GUI_UID" sudo -u "$(id -un)" -H env "PATH=$PATH" "BOSUN_REPO=$BOSUN_REPO" \
      "BOSUN_BRANCH=$BOSUN_BRANCH" BOSUN_HARNESS=agy HOMEBREW_NO_AUTO_UPDATE=1 "$@"
  else
    "$@"
  fi
}
# agy's login is a Keychain item whose service is `gemini`: asked for with neither -w nor -g, which would
# read the secret.
agy_keychain_over_ssh() { security find-generic-password -s gemini >/dev/null 2>&1; }
agy_keychain_in_desktop() { gui_do security find-generic-password -s gemini >/dev/null 2>&1; }
agy_onboarded() { jq -e '.onboardingComplete == true' "$AGY_HOME/antigravity-cli/cache/onboarding.json" >/dev/null 2>&1; }
agy_signed_in() { agy_onboarded && { agy_keychain_over_ssh || agy_keychain_in_desktop; }; }

agy_wait_for_sign_in() {
  local deadline=$((SECONDS + SIGN_IN_SECONDS)) noted=$SECONDS
  until agy_signed_in; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 10
    if [ $((SECONDS - noted)) -ge 60 ]; then
      echo "  ... still waiting for the sign-in: $(((deadline - SECONDS) / 60)) minutes left"
      noted=$SECONDS
    fi
  done
}

agy_login() {
  echo "a person signs in to Antigravity"
  cat <<'EOF'

  >>> Your turn, in the window of the throwaway Mac this run started:
  >>>   1. Open Terminal there (Cmd-Space, type Terminal, Return; or Finder, Applications, Utilities,
  >>>      Terminal).
  >>>   2. Run:  ~/.local/bin/agy
  >>>   3. Sign in with your Google account in the browser it opens, and go through agy's first-run
  >>>      screens until it shows its prompt. If macOS asks whether agy may use the Keychain item
  >>>      "gemini", click "Always Allow".
  >>>   4. Quit agy (type /exit, or press Ctrl-C twice). You can then close Terminal.
  >>> This waits up to 15 minutes, checking every 10 seconds. Your sign-in stays in that Mac, which is
  >>> deleted when this run ends. If this script is killed before that, `tests/sandbox.sh mac clean`
  >>> removes the clone that is left.

EOF
  agy_wait_for_sign_in || echo "  the sign-in timed out after 15 minutes"
  check "you signed in and went through agy's first-run screens (its Keychain item is there, onboarding done)" agy_signed_in
  # The only check of the login: what else is found about it is a fact, so that no unproven path ends the run
  # after the person signed in.
  agy_signed_in || return 0
  echo "  signed in: quit agy now (/exit) and leave the VM alone"
  agy_wait_for_agy_to_quit
  if agy_keychain_over_ssh; then fact keychain-over-ssh "the item is found over ssh"; else fact keychain-over-ssh "the item is not found over ssh"; fi
  if [ -z "$GUI_OK" ]; then
    fact keychain-in-desktop "launchctl asuser does not work here (sudo -n failed): bosun was run directly"
  elif agy_keychain_in_desktop; then
    fact keychain-in-desktop "the item is found in the desktop session (launchctl asuser)"
  else
    fact keychain-in-desktop "the item is not found in the desktop session"
  fi
  fact agy-version "$("$HOME/.local/bin/agy" --version 2>&1 | head -1)"
}

# Whether an agy that is the person's own runs: any agy but bosun's windows', which have their log file in
# bosun's log folder in their command line.
agy_person_running() {
  ps -axww -o command= | awk -v logs="$STATE/logs/agy-" '$1 ~ /(^|\/)agy$/ && index($0, logs) == 0 { found = 1 } END { exit !found }'
}
agy_wait_for_agy_to_quit() {
  local deadline=$((SECONDS + 120))
  while agy_person_running; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "  note  agy is still running after 120 seconds: the run goes on with it"
      return 0
    fi
    sleep 5
  done
}

# ---- before the login: what a new user does -------------------------------------------------------------

agy_installs() { # Google's own installer, which puts agy in ~/.local/bin and not on PATH
  if curl -fsSL --max-time 120 https://antigravity.google/cli/install.sh -o "$OUT/agy-install.sh"; then
    bash "$OUT/agy-install.sh" >"$OUT/agy-install.txt" 2>&1 </dev/null || {
      tail -15 "$OUT/agy-install.txt"
      return 1
    }
  else
    echo "could not fetch Google's installer"
    return 1
  fi
  [ -x "$HOME/.local/bin/agy" ]
}
agy_version_is_enough() { # 1.5.5, the version bosun has been run against, or newer
  local version
  version="$("$HOME/.local/bin/agy" --version 2>/dev/null | head -1)"
  echo "agy $version"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  printf '%s\n' 1.5.5 "$version" | sort -V | head -1 | grep -qx 1.5.5
}

agy_prepare() {
  local out="$OUT/first.txt" line
  echo "a Mac with none of it: Antigravity for the repo window, Claude Code for the orchestrator"
  the_repo
  check "the installer stops and says what is missing" bash -c '! sh "$1/get.sh" >"$2" 2>&1' _ "$ROOT" "$out"
  check "... with the line to run" says "brew install" "$out"
  check "... and how to get Claude Code" says "claude.com/claude-code" "$out"
  check "... and Antigravity" says "antigravity.google/cli" "$out"
  check "... and the next line keeps asking for Antigravity" says "BOSUN_HARNESS=agy sh" "$out"
  line="$(grep -oE "brew install [a-z0-9 ]+" "$out" | head -1)"
  # shellcheck disable=SC2086 # the words of the line it printed
  check "that line works" $line
  check "Claude Code installs" brew install --cask claude-code
  check "Antigravity installs with its own installer" agy_installs
  check "... and its version is at least the one bosun has been run against" agy_version_is_enough
}

# ---- bosun installed, with the repo window on agy --------------------------------------------------------

agy_install_bosun() {
  local out="$OUT/second.txt"
  echo "logged in, then the rest of what it says (through the desktop session, where the login is seen)"
  check "the installer says Claude Code's first screens are for a person" gui_do bash -c '! sh "$1/get.sh" >"$2" 2>&1 && grep -q "first-run screens not done" "$2"' _ "$ROOT" "$out"
  first_run_screens
  check "... and going through them is enough" jq -e '.hasCompletedOnboarding == true' "$HOME/.claude.json"
  check "the same line then installs" gui_do bash -c 'sh "$1/get.sh" --unattended >"$2" 2>&1 || { tail -15 "$2"; exit 1; }' _ "$ROOT" "$out"
  echo '{"repos": [{"dir": "~/Development/exporter"}], "harness": "agy", "menu_app": false}' >"$OUT/answers.json"
  check "the repo's window, named with bosun init --answers" gui_do bash -c 'bosun init --answers "$1" >"$2" 2>&1 || { tail -15 "$2"; exit 1; }' _ "$OUT/answers.json" "$OUT/answers.txt"
  check "... in sessions.yml" grep -q "name: exporter" "$CONFIG/sessions.yml"
  check "the config says agy" says 'BOSUN_HARNESS="agy"' "$CONFIG/bosun.env"
  check "doctor passes" gui_do bash -c 'bosun doctor >"$1" 2>&1 || { tail -15 "$1"; exit 1; }' _ "$OUT/doctor.txt"
}

# launchd sets SSH_AUTH_SOCK in a desktop session; if this one does not, it is set for the job, since what
# the run asks is whether it hides agy's login. What the job's windows then see is read from agy's hooks.
agy_launchd_env() {
  local before
  before="$(gui_do launchctl getenv SSH_AUTH_SOCK 2>/dev/null)"
  if [ -n "$before" ]; then
    fact launchd-ssh-auth-sock "set by launchd in the desktop session ($(dirname "$before" | cut -c1-40)/...)"
  else
    gui_do launchctl setenv SSH_AUTH_SOCK "$HOME/.sandbox/agent.sock" 2>/dev/null
    fact launchd-ssh-auth-sock "not set by launchd here: set to a path for this run, so the job's windows have it"
  fi
}
agy_has_no_servers() { agy mcp list 2>&1 | grep -q "No MCP servers configured"; }
morning_runs() { cat "$STATE"/logs/morning-*.log 2>/dev/null | grep -c "morning run complete\|morning run finished"; }
more_morning_runs_than() { [ "$(morning_runs)" -gt "$1" ]; } # <count before>

# bosun's morning job, run by launchd in the desktop session, starts its windows.
agy_kickstart() {
  local before
  before="$(morning_runs)"
  launchctl kickstart -k "gui/$GUI_UID/$MORNING_LABEL" || return 1
  wait_for 480 more_morning_runs_than "$before"
}
agy_update_logged() { grep -qhE "updated Antigravity|could not update Antigravity" "$STATE"/logs/morning-*.log; }

# <window>'s status read every second or so after bosun send, in $OUT/seen without repeats; ends when its turn is
# over (idle, after it was seen busy) or it waits for a person; fails when that does not come in <seconds>, or when
# it never reads busy (a turn too short to see, or a prompt not taken).
agy_ask() { # <prompt> <seconds> [window]
  local window="${3:-$AGY_WINDOW}" deadline=$((SECONDS + $2)) started=$SECONDS status last=""
  : >"$OUT/seen"
  bosun send --no-item "$window" "$1" >"$OUT/send.txt" 2>&1 || {
    cat "$OUT/send.txt"
    return 1
  }
  while [ "$SECONDS" -lt "$deadline" ]; do
    status="$(window "$window" status)"
    [ "$status" = "$last" ] || echo "$status" >>"$OUT/seen"
    last="$status"
    [ "$status" = waiting ] && return 0
    if [ "$status" = idle ] && grep -qx busy "$OUT/seen"; then
      sleep 5 # a turn can read idle for a moment between two model calls: it ends when it still does
      [ "$(window "$window" status)" = idle ] && return 0
      continue
    fi
    if [ "$status" = idle ] && [ $((SECONDS - started)) -gt 25 ]; then
      echo "it never read busy: statuses $(tr '\n' ' ' <"$OUT/seen")"
      return 1
    fi
    sleep 0.5
  done
  echo "not idle after $2 seconds: statuses $(tr '\n' ' ' <"$OUT/seen")"
  return 1
}
agy_note_seen() { echo "  note  the statuses it read: $(tr '\n' ' ' <"$OUT/seen")"; }
agy_busy() { [ "$(window "$1" status)" = busy ]; }

# A window's pane, with its history: agy_screen [window]; agy_screen_has <text> [window].
agy_screen() { tmux -L bosun capture-pane -p -J -S -300 -t "=bosun:=${1:-$AGY_WINDOW}" 2>/dev/null; }
agy_screen_has() { # grep -q ends a pipeline early: the screen is read first
  local screen
  screen="$(agy_screen "${2:-$AGY_WINDOW}")"
  grep -q -- "$1" <<<"$screen"
}
# Whether the window's visible pane shows agy's idle footer: its UI is up (a resumed launch is recorded idle
# at once, and agy can take a minute to start).
agy_ui_ready() { # <window>
  local screen
  screen="$(tmux -L bosun capture-pane -p -t "=bosun:=$1" 2>/dev/null)"
  grep -qF "? for shortcuts" <<<"$screen"
}

# <window>: Esc, and again if it is still not idle.
agy_dismiss() {
  for _ in 1 2 3; do
    tmux -L bosun send-keys -t "=bosun:=$1" Escape
    wait_for 20 idle "$1" && return 0
  done
  return 1
}
agy_another() { # <window> <pid before>: an agy with a record now, not the one that was
  local now
  now="$(pid_of "$1")"
  [ -n "$now" ] && [ "$now" != "$2" ]
}
# bosun refresh --restart-idle, at once. The launch resumes the conversation it knows and records it, so the
# restarted window is on record (a new agy, idle) before any prompt: agy fires no hook until one.
agy_restart() { # <window>
  local pid
  pid="$(pid_of "$1")"
  BOSUN_RESTART_MIN_IDLE_SECONDS=0 bosun refresh --restart-idle "$1" >>"$OUT/refresh.txt" 2>&1
  wait_for 90 agy_another "$1" "$pid" || {
    echo "no record of a restarted $1 after 90 seconds; the refresh said: $(tail -2 "$OUT/refresh.txt" | tr '\n' ' ')"
    screen_of "$1"
    return 1
  }
  wait_for 150 agy_ui_ready "$1" || {
    echo "agy's footer is not on the screen of $1 after 150 seconds"
    screen_of "$1"
    return 1
  }
}
agy_status_says() { # <jq test>: on the status file of the repo window's conversation, which bosun's hooks write
  local id
  id="$(window "$AGY_WINDOW" sessionId)"
  jq -e --arg id "$id" "select(.conversationId == \$id) | $1" "$STATE"/agy/*.json
}
agy_remote_control() {
  ps -o command= -p "$(pid_of "$AGY_WINDOW")" | grep -q -- '--remote-control' || {
    echo "--remote-control is not on its command line"
    return 1
  }
  ls "$STATE"/logs/agy-*.log >/dev/null || return 1
  ! grep -ihE 'remote.?control.*(error|fail|unknown|unsupported|invalid)|(error|fail|unknown|unsupported).*remote.?control' "$STATE"/logs/agy-*.log
}

agy_refresh_and_resume() {
  local id
  id="$(window "$AGY_WINDOW" sessionId)"
  check "bosun refresh --restart-idle brings the window straight back: a new agy, on record, before any prompt" agy_restart "$AGY_WINDOW"
  check "... idle, from the launch, with no prompt sent" wait_for 20 idle "$AGY_WINDOW"
  check "... on the same conversation, not a new one" test "$(window "$AGY_WINDOW" sessionId)" = "$id"
  check "... a prompt sent by bosun send goes through" agy_ask "$RECALL_PROMPT" 120
  agy_note_seen
  check "... and it recalls the word it was given before the restart (shown in lowercase: it was given in capitals)" wait_for 30 agy_screen_has "$(tr '[:upper:]' '[:lower:]' <<<"$CODE_WORD")"
}

agy_first_window() {
  local id
  echo "a window on agy, started by bosun's launchd job in the desktop session"
  agy_launchd_env
  check "agy has no MCP server of its own here, so a window with no connectors may start" agy_has_no_servers
  fact agy-version-before "$(agy --version 2>&1 | head -1)"
  check "launchd runs bosun's morning job, which starts the windows" agy_kickstart
  check "... its morning run ran agy update (a line in its log)" agy_update_logged
  fact agy-update "$(grep -hE 'updated Antigravity|could not update Antigravity' "$STATE"/logs/morning-*.log | tail -1 | cut -c1-200); agy is now $(agy --version 2>&1 | head -1)"
  check "the orchestrator reaches a session" wait_for 120 idle orchestrator
  check "the repo window reaches a session" wait_for 240 idle "$AGY_WINDOW"
  check "... and its screen shows agy's footer" wait_for 150 agy_ui_ready "$AGY_WINDOW"
  idle "$AGY_WINDOW" || {
    screen_of "$AGY_WINDOW"
    return 0
  }
  id="$(window "$AGY_WINDOW" sessionId)"
  check "the repo window runs agy" test "$(window "$AGY_WINDOW" harness)" = agy
  check "the orchestrator runs Claude Code" test "$(window orchestrator harness)" = claude
  check "bosun's launch installed its plugin in agy" cmp -s "$PLUGIN_SOURCE/hooks.json" "$AGY_PLUGIN_DIR/hooks.json"
  check "bosun knows the conversation by its window's name" jq -e --arg id "$id" '.exporter.id == $id' "$STATE/agy-sessions.json"
  check "bosun status lists the window" bash -c 'bosun status 2>&1 | grep -q "$1"' _ "$AGY_WINDOW"
  check "doctor passes with the windows up" gui_do bash -c 'bosun doctor >"$1" 2>&1 || { tail -15 "$1"; exit 1; }' _ "$OUT/doctor-up.txt"
  check "the window reads busy, then idle, on a turn of its own" agy_ask "$REMEMBER_PROMPT" 180
  agy_note_seen
  check "... and the hooks' own record says idle, on that conversation" wait_for 15 agy_status_says '.status == "idle"'
  check "--remote-control is accepted at startup: on its command line, with no error about it in agy's log" agy_remote_control
  fact remote-control-log "$(grep -ih remote "$STATE"/logs/agy-*.log 2>/dev/null | head -3 | cut -c1-160 | tr '\n' ' ')"
  fact process-name "$(ps -o comm= -p "$(pid_of "$AGY_WINDOW")" 2>&1)"
  agy_refresh_and_resume
}

# ---- bosun's plugin, recorded ----------------------------------------------------------------------------

# A copy of bosun's plugin, its hooks run by live/agy-hook-record (which records and then does bosun's own),
# in <folder>; with <pause> above 0, its PreToolUse hook sleeps that long first and has a timeout of 2 seconds.
agy_plugin_copy() { # <folder> <pause seconds>
  rm -rf "$1"
  mkdir -p "$1"
  cp "$PLUGIN_SOURCE/plugin.json" "$1/plugin.json"
  jq -n --arg recorder "$ROOT/tests/sandbox/live/agy-hook-record" --argjson pause "$2" '
    def guard: "[ -n \"${BOSUN_HOOK_CMD:-}\" ] && [ -x \"$BOSUN_HOOK_CMD\" ] || exit 0; ";
    def hook($event; $pause): {type: "command",
      command: (guard + (if $pause > 0 then "sleep \($pause); " else "" end) + "exec \"\($recorder)\" \($event)"),
      timeout: (if $pause > 0 then 2 else 30 end)};
    {bosun: {SessionStart: [hook("SessionStart"; 0)], PreInvocation: [hook("PreInvocation"; 0)],
      PreToolUse: [{matcher: "run_command", hooks: [hook("PreToolUse"; $pause)]}], Stop: [hook("Stop"; 0)]}}' \
    >"$1/hooks.json"
}
agy_install_copy() { # <folder>: agy's own plugin commands, as bosun runs them
  agy plugin uninstall bosun >/dev/null 2>&1
  agy plugin install "$1" >"$OUT/plugin-install.txt" 2>&1 || {
    cat "$OUT/plugin-install.txt"
    return 1
  }
  cmp -s "$1/hooks.json" "$AGY_PLUGIN_DIR/hooks.json"
}

agy_esc_reads_idle() {
  local since
  bosun send --no-item "$AGY_WINDOW" "$LONG_PROMPT" >/dev/null 2>&1 || {
    echo "bosun send failed"
    return 1
  }
  wait_for 60 agy_busy "$AGY_WINDOW" || {
    echo "it never read busy"
    return 1
  }
  sleep 4
  agy_busy "$AGY_WINDOW" || {
    echo "the turn was over before Esc: it reads $(window "$AGY_WINDOW" status)"
    return 1
  }
  since="$(date +%s)"
  tmux -L bosun send-keys -t "=bosun:=$AGY_WINDOW" Escape
  wait_for 40 idle "$AGY_WINDOW" || {
    echo "it reads $(window "$AGY_WINDOW" status) 40 seconds after Esc"
    screen_of "$AGY_WINDOW"
    return 1
  }
  sleep 3
  [ "$(jq -s --argjson since "$since" '[.[] | select(.ts >= $since)] | length' "$HOOKLOG/Stop.jsonl")" = 0 ] || {
    echo "a Stop hook fired after Esc"
    return 1
  }
}

agy_asks_without_dialog() { # <prompt> <seconds>: agy's own dialog never stops it
  agy_ask "$1" "$2" || return 1
  if grep -qx waiting "$OUT/seen"; then
    echo "a dialog stopped it: $(agy_screen | grep -v '^[[:space:]]*$' | tail -8 | cut -c1-100 | tr '\n' '|')"
    agy_dismiss "$AGY_WINDOW"
    return 1
  fi
}
# The recorded PreToolUse rows since the approvals' turn began, for a command of the window matching <regex>:
# there is one at least and each was answered with BypassSandbox (approved) or with nothing (none).
agy_answered() { # <regex> <approved|none>
  jq -e -s --arg re "$1" --arg want "$2" --argjson since "$(cat "$OUT/approvals-since")" '
    [.[] | select(.ts >= $since and ((.payload | fromjson | .toolCall.args.CommandLine // "") | test($re)))] as $rows
    | ($rows | length) > 0 and ($rows | all(.[]; if $want == "approved" then (.answer | contains("\"BypassSandbox\":true")) else .answer == "" end))' \
    "$HOOKLOG/PreToolUse.jsonl"
}
agy_command_fact() { # <fact name> <regex>: how the recorded PreToolUse rows for a command were answered
  fact "$1" "$(jq -r -s --arg re "$2" '
    [.[] | select((.payload | fromjson | .toolCall.args.CommandLine // "") | test($re)) | if .answer == "" then "no answer" else .answer end]
    | unique | if length == 0 then "never asked" else join(" | ") end' "$HOOKLOG/PreToolUse.jsonl")"
}

agy_approvals() {
  date +%s >"$OUT/approvals-since"
  echo "what bosun's plugin approves, from what agy's hooks recorded"
  check "a turn of git, gh and ls commands ends with no dialog of agy's own" agy_asks_without_dialog "$APPROVAL_PROMPT" 420
  agy_note_seen
  check "a git push of a feature branch to the local bare remote is answered with BypassSandbox" agy_answered '^git push .*feature-live' approved
  check "... and it landed there" git -C "$HOME/remotes/exporter.git" rev-parse --verify -q refs/heads/feature-live
  check "a gh command of the allow-list (gh pr list) is answered with BypassSandbox" agy_answered '^gh pr list' approved
  check "... and agy ran it outside its sandbox: the stand-in gh wrote its call log, outside the workspace (a real gh with no token would show an auth error, never could-not-resolve-host)" grep -qx "gh pr list" "$HOME/.sandbox/gh/calls.log"
  check "an ls gets no answer" agy_answered '^ls( |$)' none
  agy_command_fact git-commit-answer '^git commit'
}

agy_waits_at_dialog() {
  agy_ask "$PUSH_MAIN_PROMPT" 360 || return 1
  [ "$(window "$AGY_WINDOW" status)" = waiting ] || {
    echo "it reads $(window "$AGY_WINDOW" status): $(tr '\n' ' ' <"$OUT/seen")"
    return 1
  }
}
agy_send_refused() {
  if bosun send --no-item "$AGY_WINDOW" "hello" >"$OUT/refused.txt" 2>&1; then
    echo "bosun send delivered into a window at a dialog"
    return 1
  fi
  grep -qiE "asking something|waiting|permission|question" "$OUT/refused.txt" || {
    cat "$OUT/refused.txt"
    return 1
  }
}
agy_dialog() {
  echo "agy's own reviewer: a push to main"
  check "a push to main stops at agy's own dialog: the window reads waiting" agy_waits_at_dialog
  agy_note_seen
  agy_screen | grep -v '^[[:space:]]*$' | tail -8 | cut -c1-100 | sed 's/^/  dialog  /'
  check "bosun send refuses a window at a dialog" agy_send_refused
  agy_command_fact main-push-answer '^git push .*main'
  check "Esc ends the dialog, and the window reads idle again" agy_dismiss "$AGY_WINDOW"
}

agy_env_is_set() { # what the launch set, as the repo window's hooks saw it
  jq -e -s 'map(select(.env.BOSUN_WINDOW == "exporter")) as $rows | ($rows | length) > 0
    and ($rows | all(.[]; .env.AGY_CLI_DISABLE_AUTO_UPDATE == "true" and (.env | has("SSH_CLIENT") | not) and (.env | has("SSH_CONNECTION") | not)))' \
    "$HOOKLOG/PreInvocation.jsonl"
}
agy_saw_ssh_auth_sock() {
  jq -e -s 'map(select(.env.BOSUN_WINDOW == "exporter")) | any(.[]; .env.SSH_AUTH_SOCK != null)' "$HOOKLOG/PreInvocation.jsonl" &&
    ! agy_screen_has "You are currently not signed in"
}
agy_log_says_updates_off() {
  grep -ihE 'auto.?-?update.*(disabled|off)|(disabled|off).*auto.?-?update' "$STATE"/logs/agy-*.log || {
    grep -ih update "$STATE"/logs/agy-*.log | head -5
    return 1
  }
}
agy_environment() {
  echo "agy's environment, as its hooks saw it"
  check "AGY_CLI_DISABLE_AUTO_UPDATE=true is set and neither SSH_CLIENT nor SSH_CONNECTION" agy_env_is_set
  check "SSH_AUTH_SOCK is set, and the login still works: the window answered, and never shows its sign-in screen" agy_saw_ssh_auth_sock
  fact ssh-auth-sock-in-agy "$(jq -r -s 'map(select(.env.BOSUN_WINDOW == "exporter") | .env | has("SSH_AUTH_SOCK")) | unique | join(",")' "$HOOKLOG/PreInvocation.jsonl")"
  check "agy's own log says automatic updates are off" agy_log_says_updates_off
}

# ---- facts, in a window of its own on a repo of its own -----------------------------------------------------

agy_fact_repo() { # a repo with: a nested one, a bare one and a remote of it, and a submodule whose own config runs a program
  local sub="$HOME/sub-origin"
  rm -rf "$FACT_REPO" "$WT_REPO" "$sub" "$FSMON_MARKER" "$HOOK_MARKER"
  mkdir -p "$FACT_REPO" "$sub"
  git -C "$sub" init -q && git -C "$sub" commit -q --allow-empty -m "a submodule" || return 1
  git -C "$FACT_REPO" init -q && echo facts >"$FACT_REPO/README" && git -C "$FACT_REPO" add -A &&
    git -C "$FACT_REPO" commit -qm "a repo for facts" || return 1
  git init -q "$FACT_REPO/nested"
  git init -q --bare "$FACT_REPO/probe-bare.git" && git -C "$FACT_REPO" remote add probe ./probe-bare.git || return 1
  git -C "$FACT_REPO" -c protocol.file.allow=always submodule add -q "$sub" planted || return 1
  printf '#!/bin/sh\ntouch "%s"\nexit 1\n' "$FSMON_MARKER" >"$HOME/fsmon.sh"
  chmod +x "$HOME/fsmon.sh"
  git config -f "$FACT_REPO/.git/modules/planted/config" core.fsmonitor "$HOME/fsmon.sh"
  printf '#!/bin/sh\ntouch "%s"\nexit 0\n' "$HOOK_MARKER" >"$FACT_REPO/.git/hooks/pre-commit" &&
    chmod +x "$FACT_REPO/.git/hooks/pre-commit" || return 1
  git -C "$FACT_REPO" worktree add -q "$WT_REPO" -b wt-probe
}
agy_fact_turn() { # <prompt> [window]: a turn in the fact window; a dialog it stops at is left with Esc, and said
  local window="${2:-$FACT_WINDOW}"
  agy_ask "$1" 150 "$window" >"$OUT/fact-turn.txt" 2>&1
  if [ "$(window "$window" status)" = waiting ]; then
    echo asked >"$OUT/fact-asked"
    agy_dismiss "$window"
  else
    rm -f "$OUT/fact-asked"
  fi
}
asked_word() { if [ -e "$OUT/fact-asked" ]; then echo "yes, a dialog"; else echo "no"; fi; }
written() { if [ -e "$1" ]; then echo "written"; else echo "not written"; fi; }
ran_word() { if [ -e "$FSMON_MARKER" ]; then echo "$1"; else echo "$2"; fi; } # <if the planted program ran> <if not>

agy_facts_from_hooks() {
  local busy idle
  fact session-start-payload "$(jq -r -s 'map(select(.env.BOSUN_WINDOW == "fresh")) | .[0] // empty | .payload | fromjson | "keys: \(keys | join(",")); cwd: \(if (.cwd // "") == "" then "empty or absent" else .cwd end); workspacePaths: \(.workspacePaths | tostring)"' "$HOOKLOG/SessionStart.jsonl")"
  busy="$(jq -s '[.[] | select(.screen | test("esc to cancel"))] | length' "$HOOKLOG/PreInvocation.jsonl")"
  idle="$(jq -s '[.[] | select(.screen | test("\\? for shortcuts"))] | length' "$HOOKLOG/PreInvocation.jsonl")"
  fact footer-at-pre-invocation "$busy hooks ran with the busy footer on the screen, $idle with the idle footer (of $(jq -s length "$HOOKLOG/PreInvocation.jsonl"))"
  fact footer-1s-after-pre-invocation "$(grep -c 'esc to cancel' "$HOOKLOG/after-pre-invocation.txt") with the busy footer, $(grep -c '? for shortcuts' "$HOOKLOG/after-pre-invocation.txt") with the idle footer (of $(wc -l <"$HOOKLOG/after-pre-invocation.txt" | tr -d ' '))"
  fact pre-tool-use-payload "keys: $(jq -r -s '[.[] | .payload | fromjson | keys[]] | unique | join(",")' "$HOOKLOG/PreToolUse.jsonl")"
  fact run-command-args "$(jq -r -s '[.[] | .payload | fromjson | .toolCall | select(.name == "run_command") | .args | keys[]] | unique | join(",")' "$HOOKLOG/PreToolUse.jsonl")"
}
agy_facts_window() {
  echo "facts, from a window on a repo of its own"
  check "a repo for facts: a nested repo, a bare one with a remote, a submodule that runs a program" agy_fact_repo
  git -C "$FACT_REPO" status >/dev/null 2>&1
  fact planted-fsmonitor-control "a plain git status here $(ran_word "runs the program of the planted submodule" "does not run it, so this plant proves nothing")"
  rm -f "$FSMON_MARKER"
  check "a window on that repo starts, new" bash -c 'bosun send --new "$1" "$2" "Reply with just: ready" >"$3" 2>&1 || { cat "$3"; exit 1; }' _ "$FACT_WINDOW" "$FACT_REPO" "$OUT/fresh.txt"
  check "... and reads idle" wait_for 120 idle "$FACT_WINDOW"
  check "... with agy's footer on its screen" wait_for 150 agy_ui_ready "$FACT_WINDOW"
  agy_fact_turn "$CWD_PROMPT"
  agy_fact_turn "$GIT_DIR_PROMPT"
  fact sandbox-git-dirs "the top .git: $(written "$FACT_REPO/.git/probe-top"); a nested .git: $(written "$FACT_REPO/nested/.git/probe-nested"); the window asked: $(asked_word)"
  agy_fact_turn "$BARE_PROMPT"
  fact bare-repo "the push to a bare repo inside the work tree: $(git -C "$FACT_REPO/probe-bare.git" rev-parse --verify -q refs/heads/probe-branch >/dev/null && echo landed || echo did not land); git rev-parse --git-dir inside it said: $(head -c 160 "$FACT_REPO/bare-discovery.txt" 2>/dev/null | tr '\n' ' '); asked: $(asked_word)"
  agy_fact_turn "$GH_VERSION_PROMPT"
  agy_command_fact gh-version-answer '^gh --version'
  agy_fact_turn "$STATUS_PROMPT"
  fact planted-fsmonitor-window "on the window's git status, with the git config bosun puts in its environment, the program of the planted submodule $(ran_word RAN "did not run"); asked: $(asked_word)"
  agy_shell_fact
  agy_past_the_sandbox_fact
  agy_worktree_fact
  agy_facts_from_hooks
}

# Whether run_command keeps one shell between calls: the first call sets a variable and a function and writes
# its shell's pid; the second says what it finds (recorded, not judged).
agy_shell_fact() {
  rm -f "$FACT_REPO/shell-pid-1.txt" "$FACT_REPO/shell-reuse.txt"
  agy_fact_turn "$SHELL_SET_PROMPT"
  agy_fact_turn "$SHELL_CHECK_PROMPT"
  fact run-command-shell "the second call found: $(head -c 200 "$FACT_REPO/shell-reuse.txt" 2>/dev/null | tr '\n' ' '); the first call's shell pid: $(head -c 20 "$FACT_REPO/shell-pid-1.txt" 2>/dev/null | tr '\n' ' ') (the same pid with the variable and the function there means one shell is reused); asked: $(asked_word)"
}

ran_marker() { if [ -e "$1" ]; then echo "$2"; else echo "$3"; fi; } # <marker> <if it exists> <if not>

# What a command run past the sandbox (an approved git, with the plugin) sees of the git environment the launch
# pinned (GIT_CONFIG_COUNT, GIT_CONFIG_KEY_n, core.hooksPath=/dev/null): `git rev-parse --git-path hooks` prints
# the hooks folder git uses, and an approved commit runs the pre-commit hook planted in the repo only if the
# pin did not reach it.
agy_past_the_sandbox_fact() {
  local printed
  rm -f "$HOOK_MARKER"
  agy_fact_turn "$HOOKS_PATH_PROMPT"
  printed="$(agy_screen "$FACT_WINDOW" | grep -E '^[[:space:]]*(/dev/null|[^ ]*/hooks)[[:space:]]*$' | tail -1 | tr -d ' ')"
  agy_command_fact git-path-hooks-answer '^git rev-parse --git-path hooks'
  fact approved-git-sees-the-pin "git rev-parse --git-path hooks, as printed in the window: ${printed:-nothing found on the screen} (/dev/null means the GIT_CONFIG_* pins reached it)"
  agy_fact_turn "$COMMIT_PROMPT"
  agy_command_fact git-commit-probe-answer '^git commit --allow-empty'
  fact approved-commit-and-hooks "the planted pre-commit hook $(ran_marker "$HOOK_MARKER" "ran (the pin did not reach an approved commit)" "did not run (or the commit was not approved: see the answer above)"); asked: $(asked_word)"
}

# In a repo that is a git worktree (its .git is a file), whether a sandboxed command can rewrite that file.
agy_worktree_fact() {
  check "a window on a git worktree starts, new" bash -c 'bosun send --new "$1" "$2" "Reply with just: ready" >"$3" 2>&1 || { cat "$3"; exit 1; }' _ "$WT_WINDOW" "$WT_REPO" "$OUT/fresh-wt.txt"
  check "... and reads idle" wait_for 120 idle "$WT_WINDOW"
  check "... with agy's footer on its screen" wait_for 150 agy_ui_ready "$WT_WINDOW"
  rm -f "$WT_REPO/git-file-copy.txt"
  agy_fact_turn "$WORKTREE_PROMPT" "$WT_WINDOW"
  fact worktree-git-file "the .git of a worktree $(if [ -f "$WT_REPO/.git" ]; then echo "is still a file"; else echo "is no longer a file"; fi); a sandboxed command's write to it $(if grep -q '^probe$' "$WT_REPO/.git" 2>/dev/null; then echo "LANDED (it can be rewritten)"; else echo "did not land"; fi); the copy it made first: $(written "$WT_REPO/git-file-copy.txt"); asked: $(asked_word)"
  [ ! -f "$WT_REPO/git-file-copy.txt" ] || cp "$WT_REPO/git-file-copy.txt" "$WT_REPO/.git"
}

agy_recording_phase() {
  echo "bosun's plugin, recorded: every hook's payload and what bosun answered"
  mkdir -p "$HOOKLOG"
  touch "$HOOKLOG"/{SessionStart,PreInvocation,PreToolUse,Stop}.jsonl "$HOOKLOG/after-pre-invocation.txt"
  agy_plugin_copy "$OUT/plugin-record" 0
  check "a recording copy of bosun's plugin replaces it in agy, for this part only" agy_install_copy "$OUT/plugin-record"
  agy_restart "$AGY_WINDOW"
  check "the window restarts on it, idle" wait_for 120 idle "$AGY_WINDOW"
  check "Esc in a busy window reads idle afterwards, and no hook fired" agy_esc_reads_idle
  fact esc-screen "$(agy_screen_has Interrupted && echo 'the screen shows Interrupted' || echo 'no Interrupted on the screen')"
  agy_approvals
  agy_dialog
  agy_environment
  agy_facts_window
}

# ---- a hook slower than agy's timeout -------------------------------------------------------------------------

agy_slow_hook() {
  local since started
  echo "a hook slower than its timeout: recorded, not judged"
  agy_plugin_copy "$OUT/plugin-slow" 8
  check "a copy of the plugin whose PreToolUse hook sleeps 8 seconds past its timeout of 2 replaces it, for this part only" agy_install_copy "$OUT/plugin-slow"
  agy_restart "$AGY_WINDOW"
  check "the window restarts on it, idle" wait_for 120 idle "$AGY_WINDOW"
  since="$(date +%s)"
  started=$SECONDS
  check "the window ends in a state of its own (idle, or waiting) within 150 seconds" agy_ask "$SLOW_PROMPT" 150
  agy_note_seen
  fact slow-hook "after $((SECONDS - started)) s the window read $(window "$AGY_WINDOW" status) (statuses: $(tr '\n' ' ' <"$OUT/seen")); the hook's record $(jq -s --argjson since "$since" '[.[] | select(.ts >= $since)] | length' "$HOOKLOG/PreToolUse.jsonl" | sed 's/^0$/never came (agy ended the hook)/; s/^[1-9].*$/came after its sleep (agy let it finish)/'); the screen: $(agy_screen | grep -v '^[[:space:]]*$' | tail -4 | cut -c1-90 | tr '\n' '|')"
  idle "$AGY_WINDOW" || agy_dismiss "$AGY_WINDOW" >/dev/null 2>&1
}

# ---- connectors -----------------------------------------------------------------------------------------------

agy_restore_plugin() {
  echo "bosun installs its own plugin again"
  agy plugin uninstall bosun >/dev/null 2>&1
  agy_restart "$AGY_WINDOW"
  check "the window restarts, idle" wait_for 120 idle "$AGY_WINDOW"
  check "the plugin bosun's launch installed is its own again" cmp -s "$PLUGIN_SOURCE/hooks.json" "$AGY_PLUGIN_DIR/hooks.json"
}
agy_agent_stopped() { # <pid>
  [ -n "$1" ] && ! kill -0 "$1" 2>/dev/null
}
agy_remove_server() { # a dummy server the test added: agy's own command, else out of its config file
  local file="$AGY_HOME/config/mcp_config.json"
  agy mcp remove probe-dummy >/dev/null 2>&1 && return 0
  jq 'del(.mcpServers["probe-dummy"])' "$file" >"$file.new" && mv "$file.new" "$file"
}
agy_connectors() {
  local pid id log
  log="$STATE/logs/launch-$(date +%F).log"
  echo "connectors: a window whose list does not name a server agy has is refused"
  id="$(window "$AGY_WINDOW" sessionId)"
  check "agy takes a dummy stdio server" agy mcp add probe-dummy -- "$(command -v python3)" "$ROOT/tests/sandbox/mcp_probe_server.py" dummy "$HOME/probe-servers.log"
  fact agy-mcp "$(agy mcp --help 2>&1 | head -12 | tr '\n' '|' | cut -c1-300)"
  pid="$(pid_of "$AGY_WINDOW")"
  [ -n "$pid" ] && kill -TERM "$pid"
  check "its agent stopped" wait_for 60 agy_agent_stopped "$pid"
  bosun refresh "$AGY_WINDOW" >"$OUT/refusal.txt" 2>&1
  check "the window does not start, and the launch log names probe-dummy" wait_for 60 grep -qF "probe-dummy" "$log"
  check "... on its screen too: it does not start" bash -c 'tmux -L bosun capture-pane -p -J -S -200 -t "=bosun:=$1" | grep -q "does not start"' _ "$AGY_WINDOW"
  check "... with no agy in it" test -z "$(window "$AGY_WINDOW" pid)"
  grep -F "probe-dummy" "$log" | tail -1 | cut -c1-300 | sed 's/^/  note  launch log: /'
  check "the server is removed again" agy_remove_server
  check "... agy has none, as before" agy_has_no_servers
  agy_restart "$AGY_WINDOW"
  check "with none, the window starts again, idle" wait_for 120 idle "$AGY_WINDOW"
  check "... on its own conversation" test "$(window "$AGY_WINDOW" sessionId)" = "$id"
}

# ---- off again ------------------------------------------------------------------------------------------------

agy_tree() { (cd "$AGY_HOME" 2>/dev/null && find . -type f | sort); } # files only: uninstall may leave empty folders
# The paths that only one of the two listings has, but for agy's own conversations and logs, and the MCP
# config file this test's own `agy mcp add` may have made.
agy_tree_strays() {
  diff "$TREE_BEFORE" "$OUT/tree-after" | grep '^[<>]' |
    grep -vE '/(conversations|brain|logs?)(/|$)|\.log(\.|$)|/config/mcp_config\.json$' || true
}
agy_tree_is_as_before() {
  local stray
  agy_tree >"$OUT/tree-after"
  stray="$(agy_tree_strays)"
  [ -z "$stray" ] || {
    head -20 <<<"$stray"
    return 1
  }
}
agy_none_running() { ! pgrep -f -- "--log-file $STATE/logs/agy-" >/dev/null; }

agy_off_again() {
  local out="$OUT/uninstall.txt"
  echo "off again: bosun uninstall, and what is left of it in agy"
  check "uninstall passes" bash -c 'bosun uninstall --yes >"$1" 2>&1 || { tail -20 "$1"; exit 1; }' _ "$out"
  cat "$out"
  check "the windows have stopped" bash -c '! tmux -L bosun has-session -t "=bosun"'
  check "no agy of bosun's is left running" wait_for 30 agy_none_running
  check "the install link is gone" bash -c '[ ! -e "$1" ] && [ ! -L "$1" ]' _ "$HOME/.local/share/bosun"
  check "your ledger stays" test -s "$STATE/ledger.json"
  check "agy no longer has bosun's plugin" test ! -e "$AGY_PLUGIN_DIR"
  check "... nor what its uninstall leaves behind" bash -c '[ ! -e "$1/antigravity-cli/plugin_data/bosun" ] && [ ! -e "$1/config/import_manifest.json" ]' _ "$AGY_HOME"
  check "the file tree of ~/.gemini differs from before bosun only by agy's own conversation and log files" agy_tree_is_as_before
  agy_tree >"$OUT/tree-after"
  diff "$TREE_BEFORE" "$OUT/tree-after" | grep '^[<>]' | head -40 | sed 's/^/  tree  /'
  check "agy still works" agy --version
}

agy_facts_found() {
  echo
  echo "what the run found, for the stand-in and the docs (not judged)"
  sed 's/^/  fact  /' "$FACTS"
}

agy_go() {
  agy_tree >"$TREE_BEFORE"
  agy_install_bosun
  agy_first_window
  if wait_for 5 idle "$AGY_WINDOW"; then
    agy_recording_phase
    agy_slow_hook
    agy_restore_plugin
    agy_connectors
  else
    echo "  no repo window on agy: the rest of the run is skipped but the uninstall"
  fi
  agy_off_again
  agy_facts_found
}
