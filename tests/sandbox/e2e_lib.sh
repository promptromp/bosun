# shellcheck shell=bash
# What the scenarios on a real Mac share (tests/install_e2e.sh, tests/work_e2e.sh): the checks, a new
# machine, the piped installer, launchd for real, and an uninstall that keeps your records. Sourced first
# thing: it changes the machine it runs on, so it refuses to go on anywhere but a GitHub macOS runner or
# the throwaway Mac of tests/sandbox.sh mac.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
{ [ "${GITHUB_ACTIONS:-}" = "true" ] || [ -e /etc/bosun-sandbox ]; } && [ "$(uname -s)" = "Darwin" ] || {
  echo "$0 only runs on a GitHub macOS runner or in tests/sandbox.sh mac: it would change this machine" >&2
  exit 1
}
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UID_NOW="$(id -u)"
DOMAIN="gui/$UID_NOW"
LABEL="com.bosun.morning"
DIR="$HOME/.local/share/bosun"
CONFIG="$HOME/.config/bosun"
OUT="$(mktemp -d)"
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
    [ -n "$output" ] && tail -20 <<<"$output" | sed 's/^/          /'
  fi
}
wait_for() { # <seconds> <command...>
  local deadline=$((SECONDS + $1))
  shift
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 2
  done
}
windows() { tmux -L bosun list-windows -t "=bosun" -F '#{window_name}' 2>/dev/null | sort | tr '\n' ' '; }
has_window() { windows | grep -q "$1 "; }
morning_ran() { grep -q "morning run complete\|morning run finished" "$HOME"/.local/state/bosun/logs/morning-*.log; }

a_repo_by_hand() { # <name> <origin url>: on a new Mac there is no git until the installer has run
  local repo="$HOME/Development/$1/.git"
  mkdir -p "$repo/objects" "$repo/refs"
  echo "ref: refs/heads/main" >"$repo/HEAD"
  printf '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = %s\n' "$2" >"$repo/config"
}

a_new_machine() {
  echo "a new machine"
  mkdir -p "$HOME/.local/bin"
  cp "$ROOT/tests/sandbox/claude-stand-in" "$HOME/.local/bin/claude"
  # A person went through its first-run screens, and accepted the folder trust for the repos' folder.
  jq -n --arg dir "$HOME/Development" '{hasCompletedOnboarding: true, projects: {($dir): {hasTrustDialogAccepted: true}}}' >"$HOME/.claude.json"
  # With GH_TOKEN the real gh counts as logged in; without one, it needs its stand-in too.
  [ -n "${GH_TOKEN:-}" ] || cp "$ROOT/tests/sandbox/gh-stand-in" "$HOME/.local/bin/gh"
  a_repo_by_hand acme-api https://github.com/acme/api.git
  if [ -z "${BOSUN_REPO:-}" ]; then # on a runner: this checkout, under a name it can clone
    git -C "$ROOT" checkout -q -B install-test
    export BOSUN_REPO="$ROOT" BOSUN_BRANCH="install-test"
  fi
  check "nothing of bosun is here" bash -c '[ ! -e "$1" ] && [ ! -e "$2" ]' _ "$DIR" "$CONFIG"
  command -v tmux >/dev/null && echo "  note  tmux is already on this runner"
}

# What someone on a new Mac is told, followed to the letter. get.sh installs nothing itself.
follow_what_it_says() {
  echo "what bosun needs: it says what to do, and stops"
  check "a dry run changes nothing" bash -c 'sh "$1/get.sh" --dry-run; [ ! -e "$2" ]' _ "$ROOT" "$DIR"
  if ! command -v brew >/dev/null; then
    # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
    cat "$ROOT/get.sh" | sh >"$OUT/first.txt" 2>&1
    check "on a new Mac it stops and points at Homebrew" grep -q "https://brew.sh" "$OUT/first.txt"
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/null >/dev/null
  fi
  # shellcheck disable=SC2002
  cat "$ROOT/get.sh" | sh >"$OUT/second.txt" 2>&1
  cat "$OUT/second.txt"
  local line
  line="$(grep -oE "brew install [a-z0-9 ]+" "$OUT/second.txt" | head -1)"
  check "it says what to install" test -n "$line"
  # shellcheck disable=SC2086 # the words of the line it printed
  check "that line works" $line
}

install() {
  echo "the piped installer, unattended"
  # shellcheck disable=SC2002
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/install.txt" 2>&1
  local installed=$?
  cat "$OUT/install.txt"
  check "it finishes with bosun doctor passing" test "$installed" = 0
  check "Homebrew's tmux is here" command -v tmux
  check "python3 is new enough" python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))'
  check "it found the repo in the usual folder" grep -q "name: acme-api" "$CONFIG/sessions.yml"
  check "repo sessions are in auto mode" grep -q -- "--permission-mode auto" "$CONFIG/bosun.env"
  check "bosun is linked" test -L "$HOME/.local/bin/bosun"
}

launchd_for_real() {
  echo "launchd and pmset, for real"
  for job in "$LABEL" "$LABEL.watchdog" "$LABEL.learn"; do
    check "$job is loaded" launchctl print "$DOMAIN/$job"
    check "$job's plist is valid" plutil -lint -s "$HOME/Library/LaunchAgents/$job.plist"
  done
  check "the repeating wake is set" bash -c 'pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'
  launchctl kickstart -k "$DOMAIN/$LABEL"
  check "launchd really runs the morning job" wait_for 180 morning_ran
  check "... which brings up the repo's window" wait_for 60 has_window acme-api
  check "... and the orchestrator's" wait_for 60 has_window orchestrator
  check "... and ended well" grep -q "morning run complete" "$HOME"/.local/state/bosun/logs/morning-*.log
  tail -15 "$HOME"/.local/state/bosun/logs/morning-*.log
  launchctl print "$DOMAIN/$LABEL" | grep -E '^\s+(state|last exit code|runs) ' || true
  "$HOME/.local/bin/bosun" stop >/dev/null 2>&1
}

jobs_are_gone() {
  for job in "$LABEL" "$LABEL.watchdog" "$LABEL.learn"; do
    check "$job is unloaded" bash -c '! launchctl print "$1"' _ "$DOMAIN/$job"
    check "... launchd cannot run it" bash -c '! launchctl kickstart "$1"' _ "$DOMAIN/$job"
    check "... and its plist is gone" test ! -e "$HOME/Library/LaunchAgents/$job.plist"
  done
}
settings_sum() { cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum; }
trusted() { jq -e --arg dir "$1" '.projects[$dir].hasTrustDialogAccepted == true' "$HOME/.claude.json"; }
# Anything of bosun's anywhere in this home folder, by name. Not the agents' own folders, nor this test's.
named_bosun() {
  find "$HOME" \( -path "$ROOT" -o -path "$HOME/.claude" -o -path "$HOME/Library/Caches" -o -path "$HOME/repo.bundle" \
    -o -path "$HOME/work" -o -path "$HOME/runners" -o -path "$HOME/hostedtoolcache" \) -prune -o -iname '*bosun*' -print 2>/dev/null
}

# Through a real terminal, with real launchd and pmset: yes, then Enter at each question, so your records
# stay. <settings> and <ledger> are what settings_sum and cksum said of them before.
uninstall_keeping_records() { # <settings> <ledger>
  local bosun="$HOME/.local/bin/bosun" state="$HOME/.local/state/bosun" out="$OUT/uninstall.txt"
  check "yes, then Enter to keep your records" python3 "$ROOT/tests/sandbox/drive.py" "$out" \
    "Are you sure" "y<enter>" \
    "Keep your ledger" "<enter>" \
    "Keep your knowledge graph" "<enter>" \
    "bosun is uninstalled" "" \
    -- "$bosun" uninstall
  cat "$out"
  jobs_are_gone
  check "the wake is gone" bash -c '! pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'
  check "the windows have stopped" bash -c '! tmux -L bosun has-session -t "=bosun"'
  check "the checkout it made is gone" test ! -e "$DIR"
  check "the link is gone" test ! -e "$bosun"
  check "your settings are as you left them" test "$1" = "$(settings_sum)"
  check "your ledger is as you left it" test "$2" = "$(cksum <"$state/ledger.json")"
  check "your knowledge graph is there" test -s "$CONFIG/knowledge/facts.jsonl"
  check "the trust it gave a repo is gone" bash -c '! jq -e --arg dir "$1" ".projects[\$dir].hasTrustDialogAccepted" "$HOME/.claude.json"' _ "$HOME/Development/acme-api"
  check "the trust you gave stays" trusted "$HOME/Development"
}

install_again() { # <settings>: the piped installer once more, on what uninstall kept
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/back.txt" 2>&1
  check "installing again passes" test "$?" = 0
  check "... with your settings" test "$1" = "$(settings_sum)"
}
