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

first_look() {
  local out="$HOME/first-look.txt"
  echo "first look: the plan changes nothing"
  check "nothing of bosun is here yet" bash -c '[ ! -e "$1" ] && [ ! -e "$2" ]' _ "$HOME/.local/bin/bosun" "$CONFIG"
  check "bosun init prints a plan" bash -c '"$1" init >"$2"' _ "$BOSUN" "$out"
  check "... the busy repos, not the idle one" says "Windows   api, web (plus" "$out"
  check "... the schedule from the history" says "Mon-Fri, 08:30 to 19:00" "$out"
  check "... the recommended features" says "events, approvals, learning on; Slack watcher off" "$out"
  check "... says why" says "your usual first prompt is 09:10" "$out"
  check "... says how sessions handle permissions" says "auto mode" "$out"
  check "... says what sudo is for" says "sudo pmset repeat wakeorpoweron MTWRF 08:31:00" "$out"
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
  check "the windows are in sessions.yml" says "dir: ~/Development/web" "$CONFIG/sessions.yml"
  check "the folders are trusted" bash -c 'jq -e --arg d "$HOME/Development/api" ".projects[\$d].hasTrustDialogAccepted" "$HOME/.claude.json"'
  check "the morning job is loaded" says "launchctl bootstrap gui/$(id -u) $AGENTS/com.bosun.morning.plist" "$LOG"
  check "the watchdog is loaded" says "com.bosun.morning.watchdog.plist" "$LOG"
  check "learning is loaded" says "com.bosun.morning.learn.plist" "$LOG"
  check "the wake went through sudo" says "sudo pmset repeat wakeorpoweron MTWRF 08:31:00" "$LOG"
  check "it says what to do next" says "bosun is set up" "$out"
}

a_morning() {
  local out="$HOME/morning.txt"
  echo "a morning: the windows come up"
  check "the morning run succeeds" bash -c '"$HOME/.local/bin/bosun" morning --awake-minutes 1 >"$1" 2>&1 || { tail -8 "$1"; exit 1; }' _ "$out"
  check "the windows are up" bash -c '[ "$1" = "api checkin orchestrator web " ] || { echo "windows: $1"; exit 1; }' _ "$(windows)"
  check "a morning is idempotent" bash -c '"$HOME/.local/bin/bosun" morning --awake-minutes 1 >/dev/null 2>&1 && [ "$1" = "api checkin orchestrator web " ]' _ "$(windows)"
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
  echo "uninstall"
  check "it runs" "$ROOT/uninstall.sh" --with-wake --purge
  check "the link is gone" test ! -e "$HOME/.local/bin/bosun"
  check "the config is gone" test ! -e "$CONFIG"
  check "the launchd jobs are gone" bash -c '[ -z "$(ls -A "$HOME/.sandbox/launchd" 2>/dev/null)" ]'
  check "the wake is gone" test ! -e "$HOME/.sandbox/pmset-repeat"
}

the_wizard_quit() {
  local out="$HOME/wizard-quit.txt"
  echo "the wizard: leaving changes nothing"
  check "q on the first screen" python3 "$DRIVE" "$out" "Which repos" "q" -- "$BOSUN" init
  check "... says so" says "Nothing was changed" "$out"
  check "... and nothing is there" test ! -e "$CONFIG"
  check "q on the plan" python3 "$DRIVE" "$out" "Which repos" "<enter>" "When should" "<enter>" "How proactive" "<enter>" "Which model" "<enter>" "handle permissions" "<enter>" "wake by itself" "<enter>" "Nothing has changed yet" "q" -- "$BOSUN" init
  check "... and nothing is there" test ! -e "$CONFIG"
  check "ctrl-c leaves too" python3 "$DRIVE" "$out" "Which repos" "<ctrl-c>" -- "$BOSUN" init
  check "... and says so" says "Nothing was changed" "$out"
  # shellcheck disable=SC2088 # typed into the wizard, which expands it
  check "a typed folder, typed hours and the plan" python3 "$DRIVE" "$out" \
    "Which repos" "a" "Folder:" "~/Development/toy<enter>" "Development/toy" "<enter>" \
    "When should" "3" "Days, from" "MWF<enter>" "Start" "07:15<enter>" "Awake until" "16:45<enter>" \
    "How proactive" "<enter>" "Which model" "<enter>" "handle permissions" "<enter>" "wake by itself" "<enter>" \
    "Mon, Wed, Fri, 07:15 to 16:45" "q" -- "$BOSUN" init
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
  check "and then it passes" "$BOSUN" preflight --quiet
  check "an OS with no profile says so" bash -c '[ "$(BOSUN_PLATFORM=plan9 "$1" preflight >/dev/null; echo $?)" = 2 ]' _ "$BOSUN"
  check "a harness with no profile says so" bash -c 'BOSUN_HARNESS=nosuch "$1" preflight | grep -q "no profile"' _ "$BOSUN"
}

the_installer() {
  local out="$HOME/installer.txt" branch
  echo "the installer: curl | sh, then the wizard, in a real terminal"
  branch="$(git -C "$ROOT" branch --show-current)"
  export BOSUN_REPO="$ROOT" BOSUN_BRANCH="$branch"
  mv "$HOME/.local/bin/gh" "$HOME/.sandbox/packages/gh"
  check "a dry run says what it would do" bash -c 'sh "$1/get.sh" --dry-run >"$2" && grep -q "git clone" "$2"' _ "$ROOT" "$out"
  check "... and does none of it" test ! -e "$HOME/.local/share/bosun"
  check "it is plain sh" sh -n "$ROOT/get.sh"
  check "with something missing it gets bosun, says what to do and stops" bash -c '! cat "$1/get.sh" | sh >"$2" 2>&1 && grep -q "brew install gh" "$2"' _ "$ROOT" "$out"
  check "... and says to run the same line again" says "Then run this again: curl -fsSL" "$out"
  brew install gh
  check "the piped installer hands over to the wizard" python3 "$DRIVE" "$out" \
    "Which repos" "2<enter>" \
    "When should" "2" \
    "How proactive" "<enter>" \
    "Which model" "2" \
    "handle permissions" "<enter>" \
    "wake by itself" "<enter>" \
    "Nothing has changed yet" "y" \
    "Start the windows now?" "y" \
    "The windows are up" "" \
    -- sh -c "cat '$ROOT/get.sh' | sh"
  check "it put bosun where it said" test -x "$HOME/.local/share/bosun/bin/bosun"
  check "it linked that one" bash -c '[ "$(readlink "$HOME/.local/bin/bosun")" = "$HOME/.local/share/bosun/bin/bosun" ]'
  check "the toggled-off repo has no window" absent "name: web" "$CONFIG/sessions.yml"
  check "... and the chosen one does" says "name: api" "$CONFIG/sessions.yml"
  check "the schedule it was given" says 'BOSUN_DAYS="MTWRF"' "$CONFIG/bosun.env"
  check "the model it was given" says 'BOSUN_MODEL="sonnet"' "$CONFIG/bosun.env"
  check "repo sessions are in auto mode" says "--permission-mode auto" "$CONFIG/bosun.env"
  check "the windows it started" bash -c '[ "$1" = "api checkin orchestrator " ] || { echo "windows: $1"; exit 1; }' _ "$(windows)"
  "$HOME/.local/bin/bosun" stop >/dev/null 2>&1
  check "running it again updates, then asks again" python3 "$DRIVE" "$out" "Updating" "" "Which repos" "q" -- sh -c "cat '$ROOT/get.sh' | sh"
  check "a download cut short does nothing" bash -c 'head -c 900 "$1/get.sh" | BOSUN_DIR="$HOME/cut-short" sh; [ ! -e "$HOME/cut-short" ]' _ "$ROOT"
  "$HOME/.local/share/bosun/uninstall.sh" --with-wake --purge >/dev/null 2>&1
  check "uninstall leaves no link" test ! -e "$HOME/.local/bin/bosun"
  unset BOSUN_REPO BOSUN_BRANCH
}

"$ROOT/tests/sandbox/world.sh"
first_look
set_up
a_morning
run_again
other_windows
uninstall
the_wizard_quit
preflight_stops_early
the_installer
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
