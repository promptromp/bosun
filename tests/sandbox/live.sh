#!/bin/bash
# Usage: live.sh prepare <claude|codex> | go <claude|codex> [codex model]   (through tests/sandbox.sh mac live)
#
# The real agents, on a real throwaway Mac: what a new user does from nothing, following only what the
# installer prints, then a real task from a real orchestrator to a real repo session. GitHub is a stand-in
# (tests/sandbox/live/gh) and the remote a local bare repo, so nothing leaves the Mac but requests to the
# agents' APIs. It costs real money, about a dollar a run, so nothing runs it by itself.
#   prepare   the repo, the installer stopping for what is missing, and doing what it says
#   go        the first-run screens, the install, real windows, a restart and the hand-over
# The keys go in between, from mac.sh, so this script never sees where they come from.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
[ -e /etc/bosun-sandbox ] && [ "$(uname -s)" = "Darwin" ] || {
  echo "$0 only runs in tests/sandbox.sh mac: it would change this machine" >&2
  exit 1
}
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH" HOMEBREW_NO_AUTO_UPDATE=1

STEP="${1:?prepare or go}"
AGENT="${2:?claude or codex}"
CODEX_MODEL="${3:-}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONFIG="$HOME/.config/bosun"
STATE="$HOME/.local/state/bosun"
OUT="$(mktemp -d)"
export BOSUN_REPO="$HOME/repo.bundle" BOSUN_BRANCH="${BOSUN_BRANCH:?}"
[ "$AGENT" = "codex" ] && export BOSUN_HARNESS=codex
REQUEST="In the exporter repo, invoice_total ignores quantity, so one of the tests fails. Please get the exporter session to fix it on a branch and open a pull request."
HAND_OVER_SECONDS=480
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
    sleep 3
  done
}
says() { grep -q -- "$1" "$2"; }
installer() { sh "$ROOT/get.sh" "$@"; }
window() { bosun sessions --json | jq -r --arg name "$1" --arg key "$2" '.[] | select(.name == $name) | .agent[$key] // empty'; }
idle() { [ "$(window "$1" status)" = "idle" ]; }
repo_agent() { window exporter harness; }
reported() { bosun ledger list 2>/dev/null | grep -q '(reported)'; }
pushed_branch() { git -C "$HOME/remotes/exporter.git" branch --format='%(refname:short)' | grep -v '^main$' | head -1; }

the_repo() {
  git config --global user.name "A Sandbox"
  git config --global user.email sandbox@example.com
  git config --global init.defaultBranch main
  # Looks like GitHub to bosun; every fetch and push lands in a local bare repo instead.
  git config --global url."$HOME/remotes/".insteadOf "https://github.com/acme/"
  mkdir -p "$HOME/remotes" "$HOME/Development" "$HOME/.local/bin"
  git init -q --bare "$HOME/remotes/exporter.git"
  cp -R "$ROOT/tests/sandbox/live/exporter" "$HOME/Development/exporter"
  cp "$ROOT/tests/sandbox/live/gh" "$HOME/.local/bin/gh"
  (cd "$HOME/Development/exporter" && git init -q && git add -A && git commit -qm "the exporter" &&
    git remote add origin https://github.com/acme/exporter.git && git push -q -u origin main)
}

prepare() {
  local out="$OUT/first.txt" line
  echo "a Mac with none of it: $AGENT"
  the_repo
  check "the installer stops and says what is missing" bash -c '! sh "$1/get.sh" >"$2" 2>&1' _ "$ROOT" "$out"
  check "... with the line to run" says "brew install" "$out"
  check "... and how to get Claude Code" says "claude.com/claude-code" "$out"
  [ "$AGENT" = "codex" ] && check "... and Codex" says "developers.openai.com/codex" "$out"
  [ "$AGENT" = "codex" ] && check "... and the next line keeps asking for Codex" says "get.sh | BOSUN_HARNESS=codex sh" "$out"
  line="$(grep -oE "brew install [a-z0-9 ]+" "$out" | head -1)"
  # shellcheck disable=SC2086 # the words of the line it printed
  check "that line works" $line
  check "Claude Code installs" brew install --cask claude-code
  [ "$AGENT" = "codex" ] && check "Codex installs" brew install codex
}

first_run_screens() { # as a person goes through them: Return on each, which the last of them ends
  tmux -L person new-session -d -s first -x 110 -y 40 -c "$HOME" claude
  for _ in 1 2 3; do
    sleep 6
    tmux -L person send-keys -t first Enter 2>/dev/null
  done
  sleep 2
  tmux -L person kill-server 2>/dev/null
}

install_it() {
  local out="$OUT/second.txt"
  echo "logged in, then the rest of what it says"
  check "the installer says Claude Code's first screens are for a person" bash -c '! sh "$1/get.sh" >"$2" 2>&1 && grep -q "first-run screens not done" "$2"' _ "$ROOT" "$out"
  first_run_screens
  check "... and going through them is enough" jq -e '.hasCompletedOnboarding == true' "$HOME/.claude.json"
  check "the same line then installs" bash -c 'sh "$1/get.sh" --unattended >"$2" 2>&1 || { tail -15 "$2"; exit 1; }' _ "$ROOT" "$out"
  check "doctor passes" bash -c 'bosun doctor >"$1" 2>&1 || { tail -15 "$1"; exit 1; }' _ "$OUT/doctor.txt"
  if [ "$AGENT" = "codex" ]; then
    check "the config says Codex" says 'BOSUN_HARNESS="codex"' "$CONFIG/bosun.env"
    check "Codex trusts the repo" grep -q "Development/exporter" "$HOME/.codex/config.toml"
    [ -z "$CODEX_MODEL" ] || echo "BOSUN_CODEX_MODEL=\"$CODEX_MODEL\"" >>"$CONFIG/bosun.env"
  else
    check "repo sessions are in auto mode" says "permission-mode auto" "$CONFIG/bosun.env"
  fi
}

real_windows() {
  local before
  echo "real windows"
  bosun start >/dev/null 2>&1
  check "the orchestrator reaches a session" wait_for 120 idle orchestrator
  check "the repo window reaches a session" wait_for 120 idle exporter
  check "the repo window runs $AGENT" test "$(repo_agent)" = "$AGENT"
  check "the orchestrator runs Claude Code" test "$(window orchestrator harness)" = claude
  check "doctor passes with them up" bosun doctor
  check "the watchdog sees no window stalled" bash -c '! bosun watchdog --dry-run --only stalled-start 2>&1 | grep -q "alerts now"'
  # A session with nothing in it has nothing to come back to.
  check "the repo window takes a message" bosun send --no-item exporter "Reply with just: ready"
  check "... and answers" wait_for 120 idle exporter
  before="$(window exporter sessionId)"
  bosun stop >/dev/null 2>&1
  bosun start >/dev/null 2>&1
  check "after a restart the repo window is back" wait_for 120 idle exporter
  check "... on the same session" test "$(window exporter sessionId)" = "$before"
  check "the orchestrator is back too" wait_for 120 idle orchestrator
}

hand_over() {
  local deadline said_yes=0 started=$SECONDS
  echo "a real task, from the orchestrator to the repo window"
  check "the orchestrator takes the request" bosun send --no-item orchestrator "$REQUEST"
  deadline=$((SECONDS + HAND_OVER_SECONDS))
  until reported || [ "$SECONDS" -ge "$deadline" ]; do
    # A proposal waits for a yes, as it would from a person.
    if [ "$said_yes" = 0 ] && [ $((SECONDS - started)) -gt 45 ] && idle orchestrator && ! bosun ledger list | grep -q 'delegated'; then
      bosun send --no-item orchestrator "yes, go ahead" >/dev/null 2>&1 && said_yes=1
    fi
    sleep 10
  done
  echo "  (took $((SECONDS - started)) seconds)"
  check "the repo window got the task" bash -c 'bosun ledger list | grep -q "@exporter"'
  check "it reported to the ledger" reported
  check "it pushed a branch" test -n "$(pushed_branch)"
  check "the tests pass on that branch" bash -c 'rm -rf "$HOME/verify" && git clone -q -b "$1" "$HOME/remotes/exporter.git" "$HOME/verify" && cd "$HOME/verify" && python3 -m unittest' _ "$(pushed_branch)"
  check "it opened a pull request" test -e "$HOME/.sandbox/gh/pr-1.json"
  check "the orchestrator linked the pull request" wait_for 180 bash -c 'bosun ledger list | grep -q "mypr:"'
  check "nothing bosun said is recorded as yours" jq -e '[.said[]? | select(.text | test("nothing to do yet|Bosun resumed|Ledger id:"))] | length == 0' "$STATE/ledger.json"
  check "no errors in the hook's log" bash -c '! grep -h ERROR "$1"/logs/hook-*.log' _ "$STATE"
}

show_what_happened() {
  echo "what the windows show"
  bosun sessions 2>&1 | head -3 | cut -c1-100
  bosun ledger list 2>&1 | tail -4 | cut -c1-110
  tmux -L bosun capture-pane -p -t "=bosun:=exporter" | grep -v '^[[:space:]]*$' | tail -8 | cut -c1-100
}

case "$STEP" in
  prepare) prepare ;;
  go)
    install_it
    real_windows
    hand_over
    show_what_happened
    bosun stop >/dev/null 2>&1
    ;;
  *)
    echo "unknown step $STEP" >&2
    exit 2
    ;;
esac
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
