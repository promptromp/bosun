#!/bin/bash
# Usage: live.sh prepare <claude|codex> | go <claude|codex> [codex model]   (through tests/sandbox.sh mac live)
#
# The real agents, on a real throwaway Mac: what a new user does from nothing, following only what the
# installer prints, then a real task from a real orchestrator to a real repo session, and an update while
# they work. GitHub is a stand-in (tests/sandbox/live/gh) and the remote a local bare repo, so nothing
# leaves the Mac but requests to the agents' APIs. It costs real money, about a dollar a run, so nothing
# runs it by itself.
#   prepare   the repo, the installer stopping for what is missing, and doing what it says
#   go        the first-run screens, the install, real windows, a restart and the hand-over; then whether a
#             running Claude picks up a changed command file of its plugin, and `bosun update` to the next
#             version (this checkout and tests/sandbox/update_patch.sh) while the repo window waits on a
#             background command: the take-stock and update prompts, to the real agents
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
UPDATE_SECONDS=600 # the most the update may take to bring every window along
UPDATE_RC=""
BACKGROUND_ASK="The nightly invoice export has to run to its end today. Start it as a background command: \`sleep 300\` stands for it here (with Claude Code, the Bash tool's run_in_background), so that it keeps running after this turn; when it ends, the export is done and I want to hear so. Do not wait for it now. Then reply with just: started."
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
  check "... with no repo window, since nobody chose one" grep -qx "sessions: \[\]" "$CONFIG/sessions.yml"
  echo '{"repos": [{"dir": "~/Development/exporter"}]}' >"$OUT/answers.json"
  check "the repo's window, named with bosun init --answers" bash -c 'bosun init --answers "$1" >"$2" 2>&1 || { tail -15 "$2"; exit 1; }' _ "$OUT/answers.json" "$OUT/answers.txt"
  check "... in sessions.yml" grep -q "name: exporter" "$CONFIG/sessions.yml"
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

# ---- an update while the agents work ---------------------------------------------------------------

version_folder() { cd "$HOME/.local/share/bosun" && pwd -P; }
# <folder> of a version reads as an update's prune reads one (version_work: its files changed, a commit
# made in it) and bosun uninstall reads the versions folder (versions_work, with every version): nothing
# of mine in it. lib/checkout_work.py of that version, run without writing its bytecode there. What git
# shows there besides the caches running bosun makes is a note. (checkout_work, for a checkout from
# before versions, would name bosun.git itself as another worktree of a version's repository.)
reads_clean() { # <folder>
  git -C "$1" status --porcelain --ignored --untracked-files=all | grep -v "__pycache__/" | sed 's/^/  note  git status there: /'
  python3 -B - "$1" <<'PY'
import sys
from pathlib import Path
folder = Path(sys.argv[1])
sys.path.insert(0, str(folder / "lib"))
import checkout_work
versions = checkout_work.version_folders(folder.parent)
work = checkout_work.version_work(folder) or checkout_work.versions_work(folder.parent, versions)
print(work or "nothing of mine in it")
sys.exit(1 if work else 0)
PY
}
pid_of() { window "$1" pid; }
screen_of() { # <window>: what it shows, as notes
  tmux -L bosun capture-pane -p -t "=bosun:=$1" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -25 | cut -c1-110 | sed "s/^/  $1  /"
}
is_under() { # <pid> <ancestor>
  local pid="$1"
  while [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
    [ "$pid" = "$2" ] && return 0
    pid="$(ps -o ppid= -p "$pid" | tr -d ' ')"
  done
  return 1
}
sleep_under() { # <pid>: a `sleep 300` runs among its descendants
  local pid
  for pid in $(pgrep -f "^sleep 300\$"); do is_under "$pid" "$1" && return 0; done
  pgrep -fl "sleep 300"
  return 1
}
note_of() { cat "$STATE/update/notes/$1.$2" 2>/dev/null; } # <window> <stock|status>
noted() { [ -s "$STATE/update/notes/$1.$2" ]; }            # <window> <stock|status>
shelled() { [ "$(window exporter status)" = shell ]; }
windows_entry() { jq -c --arg w "$1" '.windows[$w] // empty' "$STATE/update.json"; }

# This checkout and its own tests/sandbox/update_patch.sh, as the next head of the branch the install
# follows (its origin is ~/repo.bundle).
the_next_version() {
  local tree="$HOME/next"
  rm -rf "$tree"
  git clone -q --branch "$BOSUN_BRANCH" "$HOME/repo.bundle" "$tree" && "$tree/tests/sandbox/update_patch.sh" "$tree" &&
    git -C "$tree" commit -qam "the next version" && git -C "$tree" bundle create -q "$HOME/repo.bundle.new" "$BOSUN_BRANCH" &&
    mv "$HOME/repo.bundle.new" "$HOME/repo.bundle"
}

# A real Claude runs with a plugin folder reached through a link, as the orchestrator runs with
# --plugin-dir through the install link: its command asked for, the link switched to another folder (as
# an update switches the install link), the command asked for again; then the file in the folder it
# started from changed in place, and asked for once more. Whether each reply is the new text says whether a running Claude picks
# up a changed command file. A turn each on the smallest model.
PLUGINS="$HOME/plugin-test"
a_plugin() { # <folder> <word>: a plugin `marker` whose command `word` asks for <word>
  mkdir -p "$1/plugin/.claude-plugin" "$1/plugin/commands"
  echo '{"name": "marker", "version": "0.1.0", "description": "a marker for the sandbox"}' >"$1/plugin/.claude-plugin/plugin.json"
  printf -- '---\ndescription: Say the marker word\n---\nReply with exactly the word %s and nothing else. Use no tools.\n' "$2" >"$1/plugin/commands/word.md"
}
MARKER_WORDS=(ALPHA BRAVO CHARLIE)
said_times() { # each marker word, and how often the screen shows it, a line each
  local screen word
  screen="$(tmux -L person capture-pane -p -J -S -300 -t marker 2>/dev/null)"
  for word in "${MARKER_WORDS[@]}"; do echo "$word $(grep -c "$word" <<<"$screen")"; done
}
# The command asked for once more: the word it answered with, the one the screen shows once more.
asked_for_word() {
  local before deadline=$((SECONDS + 90)) said
  before="$(said_times)"
  tmux -L person send-keys -t marker "/marker:word"
  sleep 2
  tmux -L person send-keys -t marker Enter
  while [ "$SECONDS" -lt "$deadline" ]; do
    sleep 3
    said="$(join <(echo "$before") <(said_times) | awk '$3 > $2 { print $1; exit }')"
    [ -n "$said" ] && echo "$said" && return 0
  done
  echo "no answer"
}
picks_up_commands() {
  echo "a running Claude and a changed command file of its plugin"
  local said
  a_plugin "$PLUGINS/v1" ALPHA
  a_plugin "$PLUGINS/v2" BRAVO
  ln -sfn v1 "$PLUGINS/current"
  tmux -L person new-session -d -s marker -x 150 -y 50 -c "$HOME/Development/exporter" \
    claude --model haiku --plugin-dir "$PLUGINS/current/plugin"
  sleep 12
  said="$(asked_for_word)"
  check "its command answers as its file says: $said" test "$said" = ALPHA
  ln -s v2 "$PLUGINS/.current.new" && mv -h "$PLUGINS/.current.new" "$PLUGINS/current"
  said="$(asked_for_word)"
  echo "  note  after the link moved to another folder, the running Claude said: $said ($(claude --version))"
  # The file of the folder it started from, changed in place: CHARLIE if it reads that file again each time,
  # BRAVO if it follows the link each time, ALPHA if it read its commands once, at start.
  printf -- '---\ndescription: Say the marker word\n---\nReply with exactly the word CHARLIE and nothing else. Use no tools.\n' >"$PLUGINS/v1/plugin/commands/word.md"
  said="$(asked_for_word)"
  echo "  note  after the file of the folder it started from changed in place, it said: $said"
  check "... it went on answering" test "$said" != "no answer"
  tmux -L person capture-pane -p -t marker 2>/dev/null | grep -v '^[[:space:]]*$' | tail -12 | cut -c1-100 | sed 's/^/  screen  /'
  tmux -L person kill-server 2>/dev/null
}

# The take-stock and update prompts as the update words them (lib/update_agents.py of the version
# installed), for a window the update itself does not take stock in.
prompt_for() { # take-stock | restarted
  python3 -B - "$(version_folder)/lib" "$STATE" "$1" "$(note_of exporter stock)" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import update_agents, update_state
state = update_state.read(Path(sys.argv[2]))
if sys.argv[3] == "take-stock":
    print(update_agents.take_stock_prompt("exporter", state))
else:
    stock = update_agents.stock_lines(sys.argv[4] or None, "")
    print(update_agents.prompt_for("exporter", "repo", state, update_agents.HOW_RESTARTED, (), stock))
PY
}

background_task() {
  check "the repo window takes a background task" bosun send --no-item exporter "$BACKGROUND_ASK"
  if [ "$AGENT" = "claude" ]; then
    check "... and waits on it, in shell" wait_for 240 shelled
  else
    check "... its turn ends" wait_for 240 idle exporter
  fi
  check "... a sleep 300 runs under it" sleep_under "$(pid_of exporter)"
}

# bosun update to the next version, its output in $OUT/update.txt; Ctrl-C after UPDATE_SECONDS (the
# windows then wait for the check-in loop). Run so that Ctrl-C reaches it.
the_update() {
  local pid deadline=$((SECONDS + UPDATE_SECONDS))
  python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' \
    bosun update --yes >"$OUT/update.txt" 2>&1 &
  pid=$!
  while kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$deadline" ]; do
    sleep 10
    sleep_seen
  done
  if kill -0 "$pid" 2>/dev/null; then
    screen_of orchestrator
    screen_of exporter
    kill -INT "$pid"
  fi
  wait "$pid"
  UPDATE_RC=$?
  echo "  (bosun update took $((SECONDS + UPDATE_SECONDS - deadline)) seconds)"
  sed -n '/^bosun updated: /,$p' "$OUT/update.txt"
}

# Once the repo window answered the update, whether its background work runs again under its Claude, in
# $OUT/sleep-seen (the Claude's pid, or "none"): looked at then, as it may well have ended before the
# update's wait does.
sleep_seen() {
  [ ! -e "$OUT/sleep-seen" ] && noted exporter status || return 0
  if sleep_under "$(pid_of exporter)" >/dev/null; then pid_of exporter >"$OUT/sleep-seen"; else echo none >"$OUT/sleep-seen"; fi
}
started_again() { # <the window's Claude before>: another one ran the sleep once it answered
  local seen
  seen="$(cat "$OUT/sleep-seen" 2>/dev/null)"
  [ -n "$seen" ] && [ "$seen" != none ] && [ "$seen" != "$1" ] || {
    echo "when it answered: ${seen:-not looked at}"
    return 1
  }
}

# Codex records no background work (its window is never in shell), so the update leaves it, and its
# turn at taking stock is asked for by hand, as the update words it.
prompts_by_hand() {
  local before
  prompt_for take-stock >"$OUT/take-stock.txt"
  check "the take-stock prompt, to Codex" bosun send --no-item exporter - <"$OUT/take-stock.txt"
  check "... it lists its background work with bosun update note --stock" wait_for 240 noted exporter stock
  prompt_for restarted >"$OUT/update-prompt.txt"
  before="$(pid_of exporter)"
  check "... restarted with the update prompt" bosun refresh --update-restart "$OUT/update-prompt.txt" exporter
  check "... it answers with bosun update note --status" wait_for 240 noted exporter status
  check "... and started the sleep again" restarted_with_sleep "$before"
}
restarted_with_sleep() { # <the window's agent before>: another one now, with a sleep 300 under it
  local now
  now="$(pid_of exporter)"
  [ "$now" != "$1" ] && sleep_under "$now"
}
every_version_clean() { # each version folder in bosun-versions, the old one too
  local folder
  for folder in "$HOME"/.local/share/bosun-versions/*/; do
    [ "$(basename "$folder")" = bosun.git ] && continue
    reads_clean "${folder%/}" || return 1
  done
}
the_orchestrator_reread() { # <its agent before>: kept running, asked to re-read, and it answered
  [ "$(windows_entry orchestrator)" = '["orchestrator","re-read","asked to re-read","answered"]' ] &&
    [ "$(pid_of orchestrator)" = "$1" ]
}

update_with_agents_at_work() {
  echo "an update while the agents work: the repo window waits on a background command"
  local exporter orchestrator
  check "the next version is the head of the branch" the_next_version
  background_task
  exporter="$(pid_of exporter)"
  orchestrator="$(pid_of orchestrator)"
  check "the version folder reads clean while Claude runs with --plugin-dir into it" reads_clean "$(version_folder)"
  the_update
  check "the update ends well, with its summary" bash -c '[ "$1" = 0 ] && grep -q "^bosun updated: " "$2"' _ "$UPDATE_RC" "$OUT/update.txt"
  check "the orchestrator, kept running, re-read policy.md and answered" the_orchestrator_reread "$orchestrator"
  echo "  note  the orchestrator's status: $(note_of orchestrator status | head -3 | tr '\n' ' ')"
  if [ "$AGENT" = "claude" ]; then
    check "the repo window took stock, restarted, and answered" \
      test "$(windows_entry exporter)" = '["repo","take stock, then restart","asked to take stock","restarted","answered"]'
    check "... its stock note names the sleep" bash -c 'grep -q "sleep 300" <<<"$1"' _ "$(note_of exporter stock)"
    check "... it started the sleep again after the restart (looked at once it answered)" started_again "$exporter"
  else
    check "the Codex window, nothing it was launched with changed, was left running" \
      bash -c '[ -z "$1" ] && [ "$2" = "$3" ]' _ "$(windows_entry exporter)" "$(pid_of exporter)" "$exporter"
    prompts_by_hand
  fi
  echo "  note  the repo window's stock note: $(note_of exporter stock | head -3 | tr '\n' ' ')"
  echo "  note  ... and its status: $(note_of exporter status | head -3 | tr '\n' ' ')"
  check "each version folder reads clean after it, the old one too" every_version_clean
}

# While bosun uninstall runs, until $OUT/recorded is there: each process in the sessions of bosun's panes
# (as they were when it started) or started by one of them, and each file written in the state folder's
# logs and signals, when first seen and what it ends with: what the stopped windows leave running and
# writing. It only looks, every 20 ms, and the summary goes to $OUT/stopping.txt.
record_what_stops() {
  python3 - "$STATE" "$OUT/recorded" >"$OUT/stopping.txt" 2>&1 <<'PY'
import os, subprocess, sys, time
from pathlib import Path
state, stop = Path(sys.argv[1]), Path(sys.argv[2])
panes = subprocess.run(["tmux", "-L", "bosun", "list-panes", "-s", "-t", "=bosun", "-F", "#{pane_pid}"],
                       capture_output=True, text=True).stdout.split()
def sid(pid):
    try:
        return os.getsid(pid)
    except OSError:
        return None
sessions = {s for s in (sid(int(p)) for p in panes) if s}
start, seen, present, watched = time.monotonic(), {}, set(), set()
while not stop.exists():
    now = round(time.monotonic() - start, 2)
    rows = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True, errors="replace").stdout
    for line in rows.splitlines():
        pid, ppid, command = (line.split(None, 2) + [""])[:3]
        pid, ppid = int(pid), int(ppid)
        if pid in seen or not (sid(pid) in sessions or ppid in watched):
            continue
        watched.add(pid)
        seen[pid] = f"{now}s pid {pid} ppid {ppid} session {sid(pid)}: {command[:110]}"
    now_there = {path for folder in (state / "logs", state / "signals") if folder.is_dir() for path in folder.glob("*")}
    for path in sorted(now_there - present):  # each time a file appears: when, and the line it ends with
        last = path.read_text(errors="replace").strip().splitlines()[-1:] if path.is_file() else []
        print(f"{now}s {path.relative_to(state)} appears: {last[0][:150] if last else ''}")
    for path in sorted(present - now_there):
        print(f"{now}s {path.relative_to(state)} is gone")
    present = now_there
    time.sleep(0.02)
print(f"the panes' sessions: {sorted(sessions)}; processes in them or started by them:")
print("\n".join(seen.values()))
print(f"there at the end: {', '.join(str(p.relative_to(state)) for p in sorted(present)) or 'nothing'}")
PY
}

# What a plain Claude Code offers here, from its first message: about a cent, on the smallest model. Read to
# the end, so that no Claude is left running when this returns.
claude_offers() { # [flags...]
  local out
  out="$(claude -p "Reply with the single word OK. Do not use any tools." --model haiku --max-turns 1 \
    --no-session-persistence --output-format stream-json --verbose "$@" 2>/dev/null)"
  jq -r 'select(.type == "system" and .subtype == "init") | .slash_commands[]?' <<<"$out"
}
# What still runs from <folder>, with its command line, but for this test's own processes.
runs_from() { # <folder>
  # shellcheck disable=SC2009 # the command lines are the point: pgrep would only give the numbers
  ps -axo pid=,ppid=,etime=,command= | grep -F "$1/" | grep -vE "grep -F|ps -axo" || true
}
nothing_runs_from() { [ -z "$(runs_from "$1")" ]; } # <folder>
nothing_runs_from_bosun() { nothing_runs_from "$HOME/.local/share/bosun" && nothing_runs_from "$HOME/.local/share/bosun-versions"; }
offers_chores() { claude_offers "$@" | grep -q "bosun:chores"; } # [flags...]
offers_nothing_of_bosun() { ! claude_offers | grep -q "bosun:"; }

# After real windows on the real agents: nothing of bosun may stay behind in either of them.
off_again() {
  local checkout="$HOME/.local/share/bosun" out="$OUT/uninstall.txt"
  echo "off again"
  check "with its plugin, Claude Code offers /bosun:chores, so the next check can see one" offers_chores --plugin-dir "$checkout/orchestrator/plugin"
  record_what_stops &
  recording=$!
  check "uninstall passes" bash -c 'bosun uninstall --yes >"$1" 2>&1 || { tail -20 "$1"; exit 1; }' _ "$out"
  touch "$OUT/recorded"
  wait "$recording"
  sed 's/^/  note  while it stopped the windows: /' "$OUT/stopping.txt"
  cat "$out"
  check "the windows have stopped" bash -c '! tmux -L bosun has-session -t "=bosun"'
  check "no process runs from the install or a version" wait_for 30 nothing_runs_from_bosun
  runs_from "$checkout"
  runs_from "$checkout-versions"
  check "the install link is gone" bash -c '[ ! -e "$1" ] && [ ! -L "$1" ]' _ "$checkout"
  check "... and bosun-versions, every version in it" test ! -e "$checkout-versions"
  check "your ledger stays" test -s "$STATE/ledger.json"
  check "Claude Code no longer offers /bosun:chores" offers_nothing_of_bosun
  check "... and still works" bash -c 'claude -p "Reply with the single word OK." --model haiku --max-turns 1 --no-session-persistence | grep -q OK'
  check "its settings do not name bosun" bash -c '! grep -qi bosun "$HOME/.claude/settings.json" 2>/dev/null'
  check "nor do its skills, commands or plugins" bash -c '[ -z "$(find "$HOME/.claude/skills" "$HOME/.claude/commands" "$HOME/.claude/plugins" -iname "*bosun*" 2>/dev/null)" ]'
  check "it no longer trusts the repo" bash -c '! jq -e --arg dir "$HOME/Development/exporter" ".projects[\$dir].hasTrustDialogAccepted" "$HOME/.claude.json"'
  if [ "$AGENT" = "codex" ]; then
    check "Codex's config does not name bosun" bash -c '! grep -qi "bosun\|Development/exporter" "$HOME/.codex/config.toml"'
    check "nor its skills" bash -c '[ -z "$(find "$HOME/.codex/skills" -iname "*bosun*" 2>/dev/null)" ]'
    check "... and it still works" bash -c 'cd "$HOME" && codex exec --skip-git-repo-check "Reply with the single word OK." 2>/dev/null | grep -q OK'
  fi
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
    picks_up_commands
    update_with_agents_at_work
    show_what_happened
    off_again
    ;;
  *)
    echo "unknown step $STEP" >&2
    exit 2
    ;;
esac
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
