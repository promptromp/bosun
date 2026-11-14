#!/bin/bash
# A new user's first day in the sandbox (tests/sandbox.sh): `bosun init` on a machine with nothing
# of bosun on it, a morning run, re-runs that must keep what is theirs, and uninstall.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
[ -e /etc/bosun-sandbox ] || {
  echo "$0 only runs in tests/sandbox.sh's container: it would change this machine" >&2
  exit 1
}

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BOSUN="$ROOT/bin/bosun"
INSTALL="$HOME/.local/share/bosun"           # what get.sh installs: a link to the version in use,
VERSIONS="$HOME/.local/share/bosun-versions" # each version a folder here, beside bosun.git
CONFIG="$HOME/.config/bosun"
AGENTS="$HOME/Library/LaunchAgents"
LOG="$HOME/.sandbox/system.log"
DRIVE="$ROOT/tests/sandbox/drive.py"
export BOSUN_NETWORK_PROBE_URL="file:///etc/hostname" # there is no network here
passed=0
failed=0

check() { # <description> <command...>: on failure, show the end of its output
  local description="$1" output
  shift
  if output="$("$@" 2>&1)"; then
    passed=$((passed + 1))
    echo "  pass  $description"
  else
    failed=$((failed + 1))
    echo "  FAIL  $description"
    [ -n "$output" ] && tail -8 <<<"$output" | sed 's/^/          /'
  fi
}
wait_for() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 0.2
  done
}

says() { # <text> <file>
  grep -qF -- "$1" "$2" || {
    echo "expected to find: $1"
    tail -15 "$2"
    return 1
  }
}
absent() { # <text> <file>
  ! grep -qF -- "$1" "$2" || {
    echo "did not expect: $1"
    return 1
  }
}
files_hash() { cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum; }
windows() { tmux -L bosun list-windows -t "=bosun" -F '#{window_name}' 2>/dev/null | sort | tr '\n' ' '; }

# The publisher this machine's bosun has: its own, with a placeholder Team ID and bundle id, committed into
# the sandbox's copy of the checkout (the installs clone it), so what the app questions see never depends on
# the real app/publisher.json: that file's Team ID, once filled in, is what makes init offer the app.
# The menu bar app is offered (a valid Team ID), but there is no network here and the stand-ins cannot run an
# app: the wizard's answer is No, and `init --recommended`, which says yes, ends in a refused install.
sandbox_publisher() {
  local file="$ROOT/app/publisher.json"
  echo '{"bundle_id": "com.example.bosun", "team_id": "ABCDE12345", "releases": "acme/bosun"}' >"$file"
  git -C "$ROOT" add app/publisher.json
  git -C "$ROOT" -c user.name=sandbox -c user.email=sandbox@example.com commit -q -m "the sandbox's publisher"
}

first_look() {
  local out="$HOME/first-look.txt"
  echo "first look: the plan changes nothing"
  check "nothing of bosun is here yet" bash -c '[ ! -e "$1" ] && [ ! -e "$2" ]' _ "$HOME/.local/bin/bosun" "$CONFIG"
  check "bosun init prints a plan" bash -c '"$1" init >"$2"' _ "$BOSUN" "$out"
  check "... the most used repo, and no other" says "Windows    api (plus" "$out"
  check "... no connectors for the orchestrator" says "Connectors none, for the orchestrator" "$out"
  check "... the schedule from the history" says "Mon-Fri, 08:30 to 19:00" "$out"
  check "... the recommended features" says "events, approvals, learning on; Slack watcher off" "$out"
  check "... says why" says "your usual first prompt is 09:10" "$out"
  check "... says how sessions handle permissions" says "auto mode" "$out"
  check "... says what sudo is for" says "sudo pmset repeat wakeorpoweron MTWRF 08:31:00" "$out"
  check "... the menu bar app, which has a publisher here, is in it" says "bosun app install   adds bosun.app to ~/Applications" "$out"
  check "... and nothing says there is no build to install" absent "no signed build to install" "$out"
  check "... and changed nothing" bash -c '[ ! -e "$1" ] && [ ! -e "$2" ]' _ "$HOME/.local/bin/bosun" "$CONFIG"
}

set_up() {
  local out="$HOME/set-up.txt"
  echo "set up: bosun init --recommended"
  check "it finishes with bosun doctor passing" bash -c '"$1" init --recommended >"$2" 2>&1 || { grep -E "FAIL|ERROR" "$2"; tail -5 "$2"; exit 1; }' _ "$BOSUN" "$out"
  check "it links bosun" test -L "$HOME/.local/bin/bosun"
  check "the schedule is in bosun.env" says 'BOSUN_START_TIME="08:30"' "$CONFIG/bosun.env"
  check "the mask is the repos' owner" says 'BOSUN_REPO_MASK="acme"' "$CONFIG/bosun.env"
  check "learning runs before the window ends" says 'BOSUN_LEARN_AT="17:30"' "$CONFIG/bosun.env"
  check "bosun.env is short: only what differs" bash -c '[ "$(grep -c "^BOSUN_" "$1")" -lt 20 ]' _ "$CONFIG/bosun.env"
  check "the window is in sessions.yml" says "dir: ~/Development/api" "$CONFIG/sessions.yml"
  check "... and only that one" absent "web" "$CONFIG/sessions.yml"
  check "the orchestrator gets no connectors" says 'BOSUN_ORCHESTRATOR_CONNECTORS=""' "$CONFIG/bosun.env"
  check "the folders are trusted" bash -c 'jq -e --arg d "$HOME/Development/api" ".projects[\$d].hasTrustDialogAccepted" "$HOME/.claude.json"'
  check "the morning job is loaded" says "launchctl bootstrap gui/$(id -u) $AGENTS/com.bosun.morning.plist" "$LOG"
  check "the watchdog is loaded" says "com.bosun.morning.watchdog.plist" "$LOG"
  check "learning is loaded" says "com.bosun.morning.learn.plist" "$LOG"
  check "the wake went through sudo" says "sudo pmset repeat wakeorpoweron MTWRF 08:31:00" "$LOG"
  # The recommendation includes the menu bar app, which cannot be had here (no network): init says so in a
  # line, goes on, and bosun.env stops claiming it. On a machine with no release of the app yet it is the same.
  check "the menu bar app was tried, and its refusal is a line, not a failed init" says "bosun.app was not added" "$out"
  check "... the refusal's reason is shown" says "could not download https://github.com/acme/bosun/" "$out"
  check "... and bosun.env says it is not there" bash -c 'grep -q "^BOSUN_MENU_APP=\"0\"" "$1" && grep -q "^BOSUN_CONSOLE_ALWAYS=\"0\"" "$1"' _ "$CONFIG/bosun.env"
  check "... the console's job it would have needed is gone again" test ! -e "$AGENTS/com.bosun.morning.console.plist"
  check "... and there is no app" test ! -e "$HOME/Applications/bosun.app"
  check "it says what to do next" says "bosun is set up" "$out"
}

a_morning() {
  local out="$HOME/morning.txt"
  echo "a morning: the windows come up"
  check "the morning run succeeds" bash -c '"$HOME/.local/bin/bosun" morning --awake-minutes 1 >"$1" 2>&1 || { tail -8 "$1"; exit 1; }' _ "$out"
  check "the windows are up" bash -c '[ "$1" = "api checkin orchestrator " ] || { echo "windows: $1"; exit 1; }' _ "$(windows)"
  check "a morning is idempotent" bash -c '"$HOME/.local/bin/bosun" morning --awake-minutes 1 >/dev/null 2>&1 && [ "$1" = "api checkin orchestrator " ]' _ "$(windows)"
  check "bosun stop ends the session" bash -c '"$HOME/.local/bin/bosun" stop >/dev/null 2>&1; ! tmux -L bosun has-session -t "=bosun" 2>/dev/null'
}

run_again() {
  local before after sudo_before sudo_after
  echo "a re-run keeps what is theirs"
  before="$(files_hash)"
  sudo_before="$(grep -c "sudo pmset" "$LOG")"
  "$BOSUN" init --recommended >/dev/null 2>&1
  after="$(files_hash)"
  sudo_after="$(grep -c "sudo pmset" "$LOG")"
  check "the same answers change nothing" test "$before" = "$after"
  check "... and need no sudo" test "$sudo_before" = "$sudo_after"
  check "... and back nothing up" bash -c '! ls "$1"/*.pre-init-* >/dev/null 2>&1' _ "$CONFIG"
  printf '\n# mine\nBOSUN_ALERT_SLACK_USER="U000000"\n' >>"$CONFIG/bosun.env"
  printf 'setup_steps() { ensure_claude_login; run_step "mine" true; }\n' >"$CONFIG/setup.sh"
  cp "$CONFIG/setup.sh" "$HOME/my-setup.sh"
  echo '{"days": "MWF"}' >"$HOME/answers.json"
  check "new answers apply" "$BOSUN" init --answers "$HOME/answers.json"
  check "... in bosun.env" says 'BOSUN_DAYS="MWF"' "$CONFIG/bosun.env"
  check "... keeping what you added" says 'BOSUN_ALERT_SLACK_USER="U000000"' "$CONFIG/bosun.env"
  check "... and your setup.sh" says 'run_step "mine" true' "$CONFIG/setup.sh"
  check "... with a backup" bash -c 'grep -q "BOSUN_DAYS=\"MTWRF\"" "$1"/bosun.env.pre-init-*' _ "$CONFIG"
  check "... and the launchd job follows" absent "<key>Weekday</key><integer>2</integer>" "$AGENTS/com.bosun.morning.plist"
  check "... and the wake" says "sudo pmset repeat wakeorpoweron MWF 08:31:00" "$LOG"
}

other_windows() {
  echo "other windows need --force"
  echo '{"repos": [{"dir": "~/Development/toy"}]}' >"$HOME/answers.json"
  check "refused without it" bash -c '! "$1" init --answers "$2" >/dev/null 2>&1' _ "$BOSUN" "$HOME/answers.json"
  check "... and nothing changed" absent "toy" "$CONFIG/sessions.yml"
  check "with it they are replaced" "$BOSUN" init --force --answers "$HOME/answers.json"
  check "... in sessions.yml" says "name: toy" "$CONFIG/sessions.yml"
  check "... and setup.sh is still yours" cmp "$HOME/my-setup.sh" "$CONFIG/setup.sh"
  check "... with a backup" bash -c 'grep -q "name: api" "$1"/sessions.yml.pre-init-*' _ "$CONFIG"
  echo '{"repos": [{"dir": "~/Development/gone"}]}' >"$HOME/answers.json"
  check "a folder that is not there blocks" bash -c '! "$1" init --force --answers "$2" >/dev/null 2>&1' _ "$BOSUN" "$HOME/answers.json"
}

uninstall() {
  echo "uninstall, all of it"
  check "it runs" "$ROOT/uninstall.sh" --yes --purge
  check "the link is gone" test ! -e "$HOME/.local/bin/bosun"
  check "the config is gone" test ! -e "$CONFIG"
  # Only what uninstall knows goes, so this says that it knew everything a first day writes.
  check "the state is gone" test ! -e "$HOME/.local/state/bosun"
  check "the launchd jobs are gone" bash -c '[ -z "$(ls -A "$HOME/.sandbox/launchd" 2>/dev/null)" ]'
  check "their plists are gone" bash -c '[ -z "$(ls -A "$1" 2>/dev/null)" ]' _ "$AGENTS"
  check "the wake is gone" test ! -e "$HOME/.sandbox/pmset-repeat"
  check "the trust it gave is gone" bash -c '! jq -e --arg d "$HOME/Development/api" ".projects[\$d].hasTrustDialogAccepted" "$HOME/.claude.json"'
  check "the checkout it did not make stays" test -x "$ROOT/bin/bosun"
}

# Everything on this machine but the agents' own folders, the repos and what these tests write themselves.
machine_files() {
  find "$HOME" \( -path "$HOME/.sandbox" -o -path "$HOME/.claude" -o -path "$HOME/.codex" -o -path "$HOME/.cache" \
    -o -path "$HOME/Development" -o -path "$ROOT" \) -prune -o \( -type f -o -type l \) -print |
    grep -vE "^$HOME/[^/]+$|/\.local/bin/gh$|/__pycache__/" | sort
}
# The same, but for the folders that hold your settings and records.
other_files() { machine_files | grep -vE "/\.config/bosun/|/\.local/state/bosun/"; }
same_files() { diff <("$1") <(echo "$2"); } # <what lists them> <what it listed before>

# Through a real terminal, on a machine the piped installer set up, with windows up and records to keep.
the_uninstall() { # <what the machine held before it was installed>
  local out="$HOME/uninstall.txt" bosun="$HOME/.local/bin/bosun" state="$HOME/.local/state/bosun"
  local before_install="$1" settings ledger installed
  echo "uninstall: what it says, what it asks and what it leaves, in a real terminal"
  "$bosun" start >/dev/null 2>&1
  "$bosun" ledger spawn --title "Keep me" --ask "Stay through an uninstall" --via "user: test" >/dev/null
  "$bosun" kg import "$ROOT/tests/fixtures/knowledge/acme-import.jsonl" --apply --auto-confirm >/dev/null 2>&1
  settings="$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  ledger="$(cksum <"$state/ledger.json")"
  installed="$(other_files | cksum)"

  check "Enter at are-you-sure is a no" python3 "$DRIVE" "$out" "Are you sure" "<enter>" -- "$bosun" uninstall
  check "... which says so" says "Nothing was changed" "$out"
  check "it first says what goes" says "launchd job com.bosun.morning" "$out"
  check "... and that it stops the windows" says "orchestrator" "$out"
  check "... and what stays" says "your settings: " "$out"
  check "... and what it will ask" says "It will ask whether to keep" "$out"
  check "ctrl-c at the next question leaves too" python3 "$DRIVE" "$out" "Are you sure" "y<enter>" "Keep your ledger" "<ctrl-c>" -- "$bosun" uninstall
  check "... and says so" says "Nothing was changed" "$out"
  check "... with the windows still up" bash -c 'tmux -L bosun has-session -t "=bosun"'
  check "... and nothing gone" test "$installed" = "$(other_files | cksum)"
  check "a dry run takes nothing away" bash -c '"$1" uninstall --dry-run >/dev/null && [ -L "$1" ]' _ "$bosun"

  check "yes, then Enter to keep your records" python3 "$DRIVE" "$out" \
    "Are you sure" "y<enter>" \
    "Keep your ledger" "<enter>" \
    "Keep your knowledge graph" "<enter>" \
    "bosun is uninstalled" "" \
    -- "$bosun" uninstall
  check "the windows have stopped" bash -c '! tmux -L bosun has-session -t "=bosun"'
  check "the jobs are gone" bash -c '[ -z "$(ls -A "$HOME/.sandbox/launchd" 2>/dev/null)" ]'
  check "the wake is gone" test ! -e "$HOME/.sandbox/pmset-repeat"
  check "the install it made is gone: the link and every version" bash -c '[ ! -e "$1" ] && [ ! -L "$1" ] && [ ! -e "$2" ]' _ "$INSTALL" "$VERSIONS"
  check "the link is gone" test ! -e "$bosun"
  check "it said sudo first, and used it for the wake" bash -c 'grep -q "sudo.*pmset repeat cancel" "$1"' _ "$LOG"
  check "your settings are as you left them" test "$settings" = "$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  check "your ledger is as you left it" test "$ledger" = "$(cksum <"$state/ledger.json")"
  check "your knowledge graph is there" test -s "$CONFIG/knowledge/facts.jsonl"
  check "only your records are left of the state" bash -c '[ "$(ls -A "$1" | tr "\n" " ")" = "ledger.json ledger.json.bak ledger.md " ] || ls -A "$1"' _ "$state"
  check "it says where they are" says ".config/bosun" "$out"
  check "... and how to come back" says "get.sh | sh" "$out"
  check "nothing else on the machine changed" same_files other_files "$before_install"

  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  check "installing again picks it all up" bash -c 'cat "$1/get.sh" | sh -s -- --unattended >"$2" 2>&1 || { tail -5 "$2"; exit 1; }' _ "$ROOT" "$out"
  check "... your settings" test "$settings" = "$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  check "... your ledger" bash -c '"$1" ledger list | grep -q "Keep me"' _ "$bosun"
  check "... your knowledge graph" test -s "$CONFIG/knowledge/facts.jsonl"
  check "... the jobs" says "launchctl bootstrap gui/$(id -u) $AGENTS/com.bosun.morning.plist" "$LOG"

  check "this time no, and no" python3 "$DRIVE" "$out" \
    "Are you sure" "y<enter>" \
    "Keep your ledger" "n<enter>" \
    "Keep your knowledge graph" "n<enter>" \
    "bosun is uninstalled" "" \
    -- "$bosun" uninstall
  check "the state folder is gone" test ! -e "$state"
  check "the knowledge graph is gone" test ! -e "$CONFIG/knowledge"
  check "your settings still are not" test -f "$CONFIG/bosun.env"
  rm -rf "$CONFIG"
  check "what is left is what the machine held before" same_files machine_files "$before_install"
}

the_wizard_quit() {
  local out="$HOME/wizard-quit.txt"
  echo "the wizard: leaving changes nothing"
  check "q on the first screen" python3 "$DRIVE" "$out" "Which repos" "q" -- "$BOSUN" init
  check "... says so" says "Nothing was changed" "$out"
  check "... and nothing is there" test ! -e "$CONFIG"
  check "q on the plan" python3 "$DRIVE" "$out" "Which repos" "<enter>" "When should" "<enter>" "How proactive" "<enter>" "Which model" "<enter>" "handle permissions" "<enter>" "wake by itself" "<enter>" "Add bosun to the menu bar and Applications?" "n" "Nothing has changed yet" "q" -- "$BOSUN" init
  check "... and nothing is there" test ! -e "$CONFIG"
  check "ctrl-c leaves too" python3 "$DRIVE" "$out" "Which repos" "<ctrl-c>" -- "$BOSUN" init
  check "... and says so" says "Nothing was changed" "$out"
  # shellcheck disable=SC2088 # typed into the wizard, which expands it
  check "a typed folder, typed hours and the plan" python3 "$DRIVE" "$out" \
    "Which repos" "a" "Folder:" "~/Development/toy<enter>" "Development/toy" "<enter>" \
    "When should" "3" "Days, from" "MWF<enter>" "Start" "07:15<enter>" "Awake until" "16:45<enter>" \
    "How proactive" "<enter>" "Which model" "<enter>" "handle permissions" "<enter>" "wake by itself" "<enter>" \
    "Add bosun to the menu bar and Applications?" "n" \
    "Mon, Wed, Fri, 07:15 to 16:45" "q" -- "$BOSUN" init
  check "the menu bar app is asked, in init's words, and Yes puts it in the plan" python3 "$DRIVE" "$out" \
    "Which repos" "<enter>" "When should" "<enter>" "How proactive" "<enter>" "Which model" "<enter>" \
    "handle permissions" "<enter>" "wake by itself" "<enter>" \
    "Add bosun to the menu bar and Applications?" "y" "bosun app install   adds bosun.app to ~/Applications" "q" -- "$BOSUN" init
  check "... and No leaves it out" bash -c 'python3 "$1" "$2" "Which repos" "<enter>" "When should" "<enter>" "How proactive" "<enter>" "Which model" "<enter>" \
    "handle permissions" "<enter>" "wake by itself" "<enter>" "Add bosun to the menu bar and Applications?" "n" "Nothing has changed yet" "q" -- "$3" init &&
    ! grep -q "adds bosun.app to ~/Applications" "$2"' _ "$DRIVE" "$out" "$BOSUN"
  check "r goes back to the start" python3 "$DRIVE" "$out" "Which repos" "<enter>" "When should" "r" "Which repos" "q" -- "$BOSUN" init
  check "no answers without a terminal: only the plan" bash -c '"$1" init </dev/null | grep -q "Nothing has changed yet"' _ "$BOSUN"
  check "... and nothing is there" test ! -e "$CONFIG"
}

preflight_stops_early() {
  local out="$HOME/preflight.txt"
  echo "what bosun needs: it says what to do, and stops"
  mkdir -p "$HOME/.sandbox/packages"
  mv "$HOME/.local/bin/gh" "$HOME/.sandbox/packages/gh"
  check "something missing stops it, with the line to run" bash -c '! "$1" init >"$2" 2>&1 && grep -q "brew install gh" "$2"' _ "$BOSUN" "$out"
  check "... and where to get help" says "docs/setup.md#if-you-get-stuck" "$out"
  check "... with nothing written" test ! -e "$CONFIG"
  check "the line it gives works" bash -c '$(grep -oE "brew install [a-z0-9 ]+" "$1" | head -1) && "$2" preflight --quiet' _ "$out" "$BOSUN"
  rm "$HOME/.claude.json"
  check "first-run screens are for a person" bash -c '! "$1" init >"$2" 2>&1 && grep -q "run claude, then /exit" "$2"' _ "$BOSUN" "$out"
  check "doctor agrees" bash -c '! "$1" doctor >"$2" 2>&1; grep -q "first-run screens not done" "$2"' _ "$BOSUN" "$out"
  echo '{"hasCompletedOnboarding": true}' >"$HOME/.claude.json"
  check "then Claude in Chrome's screen, as windows start with --chrome" bash -c '! "$1" preflight --quiet >"$2" 2>&1 && grep -q "run claude --chrome" "$2"' _ "$BOSUN" "$out"
  echo '{"hasCompletedOnboarding": true, "hasCompletedClaudeInChromeOnboarding": true}' >"$HOME/.claude.json"
  check "and then it passes" "$BOSUN" preflight --quiet
  check "an OS with no profile says so" bash -c '[ "$(BOSUN_PLATFORM=plan9 "$1" preflight >/dev/null; echo $?)" = 2 ]' _ "$BOSUN"
  check "a harness with no profile says so" bash -c 'BOSUN_HARNESS=nosuch "$1" preflight | grep -q "no profile"' _ "$BOSUN"
}

the_installer() {
  local out="$HOME/installer.txt" branch before_install
  echo "the installer: curl | sh, then the wizard, in a real terminal"
  branch="$(git -C "$ROOT" branch --show-current)"
  export BOSUN_REPO="$ROOT" BOSUN_BRANCH="$branch"
  mv "$HOME/.local/bin/gh" "$HOME/.sandbox/packages/gh"
  before_install="$(other_files)" # bosun's own folders are not new here: earlier steps ran bosun too
  check "a dry run says what it would do: clone bosun.git bare, and link the install to its version" bash -c '
    sh "$1/get.sh" --dry-run >"$2" && grep -q "clone --quiet --bare --no-tags --single-branch --branch $3 $1 $4/bosun.git" "$2" &&
      grep -q "ln -s $4/<commit> $5" "$2"' _ "$ROOT" "$out" "$branch" "$VERSIONS" "$INSTALL"
  check "... and does none of it" bash -c '[ ! -e "$1" ] && [ ! -e "$2" ]' _ "$INSTALL" "$VERSIONS"
  check "it is plain sh" sh -n "$ROOT/get.sh"
  check "with something missing it gets bosun, says what to do and stops" bash -c '! cat "$1/get.sh" | sh >"$2" 2>&1 && grep -q "brew install gh" "$2"' _ "$ROOT" "$out"
  check "... and says to run the same line again" says "Then run this again: curl -fsSL" "$out"
  check "... which keeps the repository and the branch it was asked for" says "BOSUN_REPO=$ROOT BOSUN_BRANCH=$branch sh" "$out"
  brew install gh
  # What a person does: run the line it printed, with nothing of the branch in the environment now (the
  # curl is this checkout's get.sh). The install stopped at what bosun needs, so this is init again, which
  # has to know the install follows the branch.
  rerun="$(sed -n 's/^Then run this again: .* | //p' "$out" | head -1)"
  unset BOSUN_BRANCH
  check "the piped installer hands over to the wizard, from the line it printed" python3 "$DRIVE" "$out" \
    "Which repos" "<enter>" \
    "When should" "2" \
    "How proactive" "<enter>" \
    "Which model" "2" \
    "handle permissions" "<enter>" \
    "wake by itself" "<enter>" \
    "Add bosun to the menu bar and Applications?" "n" \
    "Nothing has changed yet" "y" \
    "Start the windows now?" "y" \
    "The windows are up" "" \
    -- sh -c "cat '$ROOT/get.sh' | $rerun"
  export BOSUN_BRANCH="$branch" # for the steps after: they install again, with a branch asked for
  check "it put bosun where it said" test -x "$INSTALL/bin/bosun"
  check "... the install a link to the version in use, a folder of its own beside bosun.git" bash -c '
    [ -L "$1" ] && [ "$(dirname "$(readlink "$1")")" = "$2" ] && [ -d "$2/bosun.git" ] &&
      [ "$(git -C "$(readlink "$1")" rev-parse HEAD)" = "$(git -C "$3" rev-parse HEAD)" ]' _ "$INSTALL" "$VERSIONS" "$ROOT"
  check "... bosun.git marked as made by the installer, following the branch it was asked for" bash -c '
    [ "$(git --git-dir="$1/bosun.git" config bosun.managed)" = true ] && [ "$(git --git-dir="$1/bosun.git" config bosun.branch)" = "$2" ]' _ "$VERSIONS" "$branch"
  check "... and, as it was asked for a branch, following that: init was told, and wrote it down" says 'BOSUN_UPDATE_CHANNEL="main"' "$CONFIG/bosun.env"
  check "it linked that one, through the install link" bash -c '[ "$(readlink "$HOME/.local/bin/bosun")" = "$1/bin/bosun" ]' _ "$INSTALL"
  check "... and its jobs run it through the link too" grep -qF "exec \"$INSTALL/bin/bosun\" " "$AGENTS/com.bosun.morning.plist"
  check "the repo not ticked has no window" absent "name: web" "$CONFIG/sessions.yml"
  check "... and the ticked one does" says "name: api" "$CONFIG/sessions.yml"
  check "the schedule it was given" says 'BOSUN_DAYS="MTWRF"' "$CONFIG/bosun.env"
  check "the model it was given" says 'BOSUN_MODEL="sonnet"' "$CONFIG/bosun.env"
  check "repo sessions are in auto mode" says "--permission-mode auto" "$CONFIG/bosun.env"
  check "the windows it started" bash -c '[ "$1" = "api checkin orchestrator " ] || { echo "windows: $1"; exit 1; }' _ "$(windows)"
  "$HOME/.local/bin/bosun" stop >/dev/null 2>&1
  check "running it again hands over to the install's own bosun update, which follows its branch: up to date" python3 "$DRIVE" "$out" \
    "It hands over to the bosun you run" "" "bosun is up to date" "" -- sh -c "cat '$ROOT/get.sh' | sh"
  check "... the link where it was, and no other version" bash -c '[ "$(ls "$1" | grep -vc "^bosun.git$")" = 1 ] && [ -L "$2" ]' _ "$VERSIONS" "$INSTALL"
  check "a download cut short does nothing" bash -c 'head -c 900 "$1/get.sh" | BOSUN_DIR="$HOME/cut-short" sh; [ ! -e "$HOME/cut-short" ]' _ "$ROOT"
  the_uninstall "$before_install"
  the_installer_unattended
  the_release_installer
  unset BOSUN_REPO BOSUN_BRANCH
}

# A first install that asks for no branch gets the newest release of the repository, and follows the
# releases. The repository is a clone of this checkout with a release tag (annotated, as the workflow makes
# them) on its head, and later a newer one.
the_release_installer() {
  local out="$HOME/release.txt" repo="$HOME/release-repo" bosun="$HOME/.local/bin/bosun" release
  local as_acme=(git -C "$HOME/release-repo" -c user.name=acme -c user.email=dev@example.com -c commit.gpgsign=false)
  echo "the installer, with a release: the newest one, and the install follows the releases"
  git clone -q "$ROOT" "$repo"
  "${as_acme[@]}" checkout -q -B main
  "${as_acme[@]}" tag -a v2026.11.12 -m "- A release"
  release="$("${as_acme[@]}" rev-parse 'v2026.11.12^{commit}')"
  unset BOSUN_BRANCH
  export BOSUN_REPO="$repo"
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  check "it sets bosun up" bash -c 'cat "$1/get.sh" | sh -s -- --unattended >"$2" 2>&1 || { tail -5 "$2"; exit 1; }' _ "$ROOT" "$out"
  check "... saying which release it got" says "Getting bosun v2026.11.12, the newest release" "$out"
  check "... the version in use is that release's commit" bash -c '[ "$(git -C "$(readlink "$1")" rev-parse HEAD)" = "$2" ]' _ "$INSTALL" "$release"
  check "... following the releases, which init writes down" says 'BOSUN_UPDATE_CHANNEL="releases"' "$CONFIG/bosun.env"
  check "... the release in a remote ref of bosun.git, and no tag of its own there" bash -c '
    git --git-dir="$1/bosun.git" rev-parse -q --verify refs/remotes/origin/release >/dev/null &&
      [ -z "$(git --git-dir="$1/bosun.git" for-each-ref refs/tags)" ]' _ "$VERSIONS"
  check "bosun update --check says it is up to date" bash -c '"$1" update --check | grep -q "up to date"' _ "$bosun"
  check "running the line again hands over to bosun update, which is up to date" bash -c '
    cat "$1/get.sh" | sh -s -- --unattended >"$2" 2>&1 && grep -q "bosun is up to date" "$2"' _ "$ROOT" "$out"
  "${as_acme[@]}" commit -q --allow-empty -m "Next"
  "${as_acme[@]}" tag -a v2026.11.13 -m "- The next"
  check "a newer release is offered" bash -c '"$1" update --check | grep -q "v2026.11.13 is available"' _ "$bosun"
  check "uninstall takes it all off, the versions with it: nothing of yours was in them" bash -c '
    "$1" uninstall --yes --purge >"$2" 2>&1 && [ ! -e "$1" ] && [ ! -e "$3" ] && [ ! -e "$4" ]' _ "$bosun" "$out" "$VERSIONS" "$INSTALL"
}

# On a first install, unattended: the recommendation, but nothing nobody chose, and it says so.
the_installer_unattended() {
  local out="$HOME/unattended.txt"
  echo "the installer, unattended: no repo windows or connectors, since nobody chose them"
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  check "it sets bosun up" bash -c 'cat "$1/get.sh" | sh -s -- --unattended >"$2" 2>&1 || { tail -5 "$2"; exit 1; }' _ "$ROOT" "$out"
  check "... with no repo window" says "sessions: []" "$CONFIG/sessions.yml"
  check "... no connectors for the orchestrator" says 'BOSUN_ORCHESTRATOR_CONNECTORS=""' "$CONFIG/bosun.env"
  check "... the rest as recommended" says 'BOSUN_REPO_MASK="acme"' "$CONFIG/bosun.env"
  check "... and it says so, last" bash -c '[ "$(tail -1 "$1")" = "No repo windows or connectors were set up, since nobody chose them: run bosun init to add them." ]' _ "$out"
  check "the repo it did not choose is not trusted" bash -c '! jq -e --arg d "$HOME/Development/api" ".projects[\$d].hasTrustDialogAccepted" "$HOME/.claude.json"'
  check "a window named with --answers needs no --force" bash -c 'echo "{\"repos\": [{\"dir\": \"~/Development/api\"}]}" >"$2" && "$1" init --answers "$2" >/dev/null 2>&1' _ "$HOME/.local/bin/bosun" "$HOME/answers.json"
  check "... and is in sessions.yml" says "name: api" "$CONFIG/sessions.yml"
  check "uninstall takes it off again" bash -c '"$1" uninstall --yes --purge >"$2" 2>&1 && [ ! -e "$1" ] && [ ! -e "$3" ]' _ "$HOME/.local/bin/bosun" "$out" "$CONFIG"
}

codex_turns() { cat "$HOME"/.codex/sessions/*/*/*/*.jsonl | grep -c task_started; }

the_first_day_on_codex() {
  local out="$HOME/codex.txt" sessions_before turns state="$HOME/.local/state/bosun"
  local bosun="$HOME/.local/bin/bosun"
  echo "the first day on Codex: the same steps, then real windows on the stand-in"
  rm -rf "$CONFIG"
  check "asking for Codex without it says where to get it" bash -c '! BOSUN_HARNESS=codex "$1" init --recommended >"$2" 2>&1 && grep -q "developers.openai.com/codex" "$2"' _ "$BOSUN" "$out"
  check "the installer's next line keeps that choice" bash -c '! cat "$1/get.sh" | BOSUN_DIR="$HOME/.local/share/bosun" BOSUN_REPO="$1" BOSUN_BRANCH="$(git -C "$1" branch --show-current)" BOSUN_HARNESS=codex sh >"$2" 2>&1 && grep -q "BOSUN_BRANCH=[^ ]* BOSUN_HARNESS=codex sh" "$2"' _ "$ROOT" "$out"
  brew install codex
  check "with Codex here, the wizard offers it" python3 "$DRIVE" "$out" \
    "Which repos" "<enter>" \
    "When should" "<enter>" \
    "How proactive" "<enter>" \
    "Which agent" "2" \
    "model should the orchestrator" "<enter>" \
    "wake by itself" "<enter>" \
    "Add bosun to the menu bar and Applications?" "n" \
    "Nothing has changed yet" "y" \
    "Start the windows now?" "y" \
    "The windows are up" "" \
    -- "$BOSUN" init
  check "the config says Codex" says 'BOSUN_HARNESS="codex"' "$CONFIG/bosun.env"
  check "Codex trusts the repos" grep -q "Development/api" "$HOME/.codex/config.toml"
  check "doctor agrees" bash -c '"$1" doctor >"$2" 2>&1 || { tail -12 "$2"; exit 1; }' _ "$bosun" "$out"
  check "the repo window runs Codex" wait_for 20 bash -c '"$1" sessions --json | jq -e ".[] | select(.name == \"api\") | .agent.harness == \"codex\" and .agent.status == \"idle\"" >/dev/null' _ "$bosun"
  check "the orchestrator stays on Claude Code" wait_for 20 bash -c '"$1" sessions --json | jq -e ".[] | select(.name == \"orchestrator\") | .agent.harness == \"claude\"" >/dev/null' _ "$bosun"
  check "lists say which agent" bash -c '"$1" sessions | grep -q "codex:idle"' _ "$bosun"
  check "bosun knows the session by its window" wait_for 10 jq -e '.api.id' "$state/codex-sessions.json"
  sessions_before="$(find "$HOME/.codex/sessions" -name '*.jsonl' | wc -l)"
  turns="$(codex_turns)"
  check "a message reaches it" "$bosun" send --no-item api "please look at the failing test"
  check "... as a turn of its own" wait_for 10 bash -c '[ "$(cat "$HOME"/.codex/sessions/*/*/*/*.jsonl | grep -c task_started)" -gt "$1" ]' _ "$turns"
  tmux -L bosun send-keys -t "=bosun:=api" "go with option b" Enter
  check "what you type there is recorded" wait_for 10 jq -e '[.said[]? | select(.text == "go with option b")] | length == 1' "$state/ledger.json"
  tmux -L bosun send-keys -t "=bosun:=api" "and keep the tests" Enter
  check "... once, though Codex lists every prompt again" wait_for 10 jq -e '[.said[]? | select(.text == "and keep the tests")] | length == 1' "$state/ledger.json"
  check "... and bosun's own prompts are not yours" jq -e '[.said[]? | select(.text | test("nothing to do yet|look at the failing test"))] | length == 0' "$state/ledger.json"
  "$bosun" stop >/dev/null 2>&1
  "$bosun" start >/dev/null 2>&1
  check "a restart picks the same session up" wait_for 20 bash -c '"$1" sessions --json | jq -e ".[] | select(.name == \"api\") | .agent.status == \"idle\"" >/dev/null' _ "$bosun"
  check "... with no new one" test "$sessions_before" = "$(find "$HOME/.codex/sessions" -name '*.jsonl' | wc -l)"
  "$bosun" stop >/dev/null 2>&1
  "$bosun" trust --revoke "$HOME/Development/api" >/dev/null 2>&1
  "$bosun" start >/dev/null 2>&1
  check "a folder it does not trust stops at its screen, and send refuses" wait_for 15 bash -c '! "$1" send --no-item api "hello" >"$2" 2>&1 && grep -qE "is not running|asking something" "$2"' _ "$bosun" "$out"
  "$bosun" stop >/dev/null 2>&1
  touch "$HOME/.sandbox/codex-logged-out"
  check "logged out, doctor says so" bash -c '! "$1" doctor >"$2" 2>&1; grep -q "codex login" "$2"' _ "$bosun" "$out"
  rm "$HOME/.sandbox/codex-logged-out"
  "$HOME/.local/bin/bosun" trust --any "$HOME/Development/api" >/dev/null 2>&1
  check "uninstall takes back what it told Codex" bash -c '"$1" uninstall --yes --purge >"$2" 2>&1; ! grep -q "Development/" "$HOME/.codex/config.toml"' _ "$HOME/.local/bin/bosun" "$out"
  check "... and leaves no link" test ! -e "$HOME/.local/bin/bosun"
  check "... and nothing of its state" test ! -e "$state"
}

# What the stand-in's conversations hold: the steps (prompts and replies) of all of them. Exported, for the
# checks that run it in a shell of their own.
agy_steps() {
  python3 -c '
import glob, sqlite3, sys
print(sum(sqlite3.connect(path).execute("select count(*) from steps").fetchone()[0] for path in glob.glob(sys.argv[1] + "/*.db")))
' "$HOME/.gemini/antigravity-cli/conversations"
}
export -f agy_steps

the_first_day_on_agy() {
  local out="$HOME/agy.txt" steps conversations state="$HOME/.local/state/bosun"
  local bosun="$HOME/.local/bin/bosun" cli="$HOME/.gemini/antigravity-cli"
  echo "the first day on agy: the same steps, then real windows on the stand-in"
  rm -rf "$CONFIG"
  rm -f "$HOME/.local/bin/codex" # a machine that has agy and no Codex, so agy is the wizard's second agent
  check "asking for agy without it says where to get it" bash -c '! BOSUN_HARNESS=agy "$1" init --recommended >"$2" 2>&1 && grep -q "antigravity.google/cli" "$2"' _ "$BOSUN" "$out"
  check "the installer's next line keeps that choice" bash -c '! cat "$1/get.sh" | BOSUN_DIR="$HOME/.local/share/bosun" BOSUN_REPO="$1" BOSUN_BRANCH="$(git -C "$1" branch --show-current)" BOSUN_HARNESS=agy sh >"$2" 2>&1 && grep -q "BOSUN_BRANCH=[^ ]* BOSUN_HARNESS=agy sh" "$2"' _ "$ROOT" "$out"
  brew install agy
  mkdir -p "$cli/cache"
  echo '{"onboardingComplete": true}' >"$cli/cache/onboarding.json" # a person signed in and went through its first-run screens
  check "with agy here, the wizard offers it" python3 "$DRIVE" "$out" \
    "Which repos" "<enter>" \
    "When should" "<enter>" \
    "How proactive" "<enter>" \
    "Which agent" "2" \
    "model should the orchestrator" "<enter>" \
    "wake by itself" "<enter>" \
    "Add bosun to the menu bar and Applications?" "n" \
    "Nothing has changed yet" "y" \
    "Start the windows now?" "y" \
    "The windows are up" "" \
    -- "$BOSUN" init
  check "the config says agy" says 'BOSUN_HARNESS="agy"' "$CONFIG/bosun.env"
  check "doctor agrees" bash -c '"$1" doctor >"$2" 2>&1 || { tail -12 "$2"; exit 1; }' _ "$bosun" "$out"
  check "the repo window runs agy" wait_for 30 bash -c '"$1" sessions --json | jq -e ".[] | select(.name == \"api\") | .agent.harness == \"agy\" and .agent.status == \"idle\"" >/dev/null' _ "$bosun"
  check "the orchestrator stays on Claude Code" wait_for 20 bash -c '"$1" sessions --json | jq -e ".[] | select(.name == \"orchestrator\") | .agent.harness == \"claude\"" >/dev/null' _ "$bosun"
  check "lists say which agent" bash -c '"$1" sessions | grep -q "agy:idle"' _ "$bosun"
  check "bosun's plugin is in agy, installed by agy itself" jq -e '.name == "bosun"' "$HOME/.gemini/config/plugins/bosun/plugin.json"
  check "bosun knows the conversation by its window" wait_for 10 jq -e '.api.id' "$state/agy-sessions.json"
  conversations="$(find "$cli/conversations" -name '*.db' | wc -l)"
  steps="$(agy_steps)"
  check "a message reaches it" "$bosun" send --no-item api "please look at the failing test"
  check "... as a turn of its own" wait_for 10 bash -c '[ "$(agy_steps)" -gt "$1" ]' _ "$steps"
  tmux -L bosun send-keys -t "=bosun:=api" "go with option b" Enter
  check "what you type there is recorded" wait_for 10 jq -e '[.said[]? | select(.text == "go with option b")] | length == 1' "$state/ledger.json"
  tmux -L bosun send-keys -t "=bosun:=api" "run echo and keep the tests" Enter
  check "... once, though agy repeats it at each of the turn's model calls" wait_for 10 jq -e '[.said[]? | select(.text == "run echo and keep the tests")] | length == 1' "$state/ledger.json"
  check "... and bosun's own prompts are not yours" jq -e '[.said[]? | select(.text | test("nothing to do yet|look at the failing test"))] | length == 0' "$state/ledger.json"
  "$bosun" stop >/dev/null 2>&1
  "$bosun" start >/dev/null 2>&1
  check "a restart picks the same conversation up" wait_for 30 bash -c '"$1" sessions --json | jq -e ".[] | select(.name == \"api\") | .agent.status == \"idle\"" >/dev/null' _ "$bosun"
  check "... with no new one" test "$conversations" = "$(find "$cli/conversations" -name '*.db' | wc -l)"
  "$bosun" stop >/dev/null 2>&1
  mv "$cli/cache/onboarding.json" "$HOME/onboarding.json"
  check "without its first-run screens done, doctor says so" bash -c '! "$1" doctor >"$2" 2>&1; grep -q "first-run screens" "$2"' _ "$bosun" "$out"
  mv "$HOME/onboarding.json" "$cli/cache/onboarding.json"
  check "uninstall takes back bosun's plugin" bash -c '"$1" uninstall --yes --purge >"$2" 2>&1; [ ! -e "$3/config/plugins/bosun" ] && [ ! -e "$3/antigravity-cli/plugin_data/bosun" ]' _ "$bosun" "$out" "$HOME/.gemini"
  check "... and agy's import record, which holds nothing else" test ! -e "$HOME/.gemini/config/import_manifest.json"
  check "... but not the conversations, which are agy's" test "$(find "$cli/conversations" -name '*.db' | wc -l)" -gt 0
  check "... and leaves no link" test ! -e "$HOME/.local/bin/bosun"
  check "... and nothing of its state" test ! -e "$state"
}

"$ROOT/tests/sandbox/world.sh"
sandbox_publisher
first_look
set_up
a_morning
run_again
other_windows
uninstall
the_wizard_quit
preflight_stops_early
the_installer
the_first_day_on_codex
the_first_day_on_agy
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
