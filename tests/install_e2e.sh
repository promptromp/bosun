#!/bin/bash
# A new machine's install, for real: the piped installer with its real Homebrew, real launchd jobs that
# really fire, the real pmset and plutil, and a clean uninstall. Claude Code is a stand-in, since these
# machines have no login. It changes the machine it runs on, so it only runs on a GitHub macOS runner
# (.github/workflows/install.yml) or in the throwaway Mac of tests/sandbox.sh mac.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
{ [ "${GITHUB_ACTIONS:-}" = "true" ] || [ -e /etc/bosun-sandbox ]; } && [ "$(uname -s)" = "Darwin" ] || {
  echo "$0 only runs on a GitHub macOS runner or in tests/sandbox.sh mac: it would change this machine" >&2
  exit 1
}
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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

a_new_machine() {
  echo "a new machine"
  mkdir -p "$HOME/.local/bin"
  cp "$ROOT/tests/sandbox/claude-stand-in" "$HOME/.local/bin/claude"
  # With GH_TOKEN the real gh counts as logged in; without one, it needs its stand-in too.
  [ -n "${GH_TOKEN:-}" ] || cp "$ROOT/tests/sandbox/gh-stand-in" "$HOME/.local/bin/gh"
  # A repo by hand: on a new Mac there is no git until the installer has run.
  local repo="$HOME/Development/acme-api/.git"
  mkdir -p "$repo/objects" "$repo/refs"
  echo "ref: refs/heads/main" >"$repo/HEAD"
  printf '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = https://github.com/acme/api.git\n' >"$repo/config"
  if [ -z "${BOSUN_REPO:-}" ]; then # on a runner: this checkout, under a name it can clone
    git -C "$ROOT" checkout -q -B install-test
    export BOSUN_REPO="$ROOT" BOSUN_BRANCH="install-test"
  fi
  check "nothing of bosun is here" bash -c '[ ! -e "$1" ] && [ ! -e "$2" ]' _ "$DIR" "$CONFIG"
  command -v tmux >/dev/null && echo "  note  tmux is already on this runner"
}

install() {
  echo "the piped installer, unattended"
  check "a dry run changes nothing" bash -c 'sh "$1/get.sh" --dry-run && [ ! -e "$2" ]' _ "$ROOT" "$DIR"
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/install.txt" 2>&1
  local status=$?
  cat "$OUT/install.txt"
  check "it finishes with bosun doctor passing" test "$status" = 0
  check "Homebrew's tmux is here" command -v tmux
  check "python3 is new enough" python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))'
  check "it found the repo in the usual folder" grep -q "name: acme-api" "$CONFIG/sessions.yml"
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

run_again() {
  echo "a re-run"
  local before after
  before="$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  # shellcheck disable=SC2002
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/again.txt" 2>&1
  check "it updates and passes again" test "$?" = 0
  after="$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  check "the same answers change nothing" test "$before" = "$after"
  check "... and back nothing up" bash -c '! ls "$1"/*.pre-init-* >/dev/null 2>&1' _ "$CONFIG"
}

uninstall() {
  echo "uninstall"
  check "it runs" "$DIR/uninstall.sh" --with-wake --purge
  check "the link is gone" test ! -e "$HOME/.local/bin/bosun"
  check "the config is gone" test ! -e "$CONFIG"
  for job in "$LABEL" "$LABEL.watchdog" "$LABEL.learn"; do
    check "$job is gone" bash -c '! launchctl print "$1"' _ "$DOMAIN/$job"
  done
  check "the wake is gone" bash -c '! pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'
}

a_new_machine
install
launchd_for_real
run_again
uninstall
rm -rf "$OUT"
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
