#!/bin/bash
# A week of real work on a real Mac, through two updates: the last release installed by the piped
# installer, three days of work on it (tests/sandbox/acme_week.sh: the ledger and the knowledge base),
# its windows at work, then the upgrade the way a user does it (get.sh again), which converts the
# release's checkout to versions side by side at a quiet moment (once with a migration that fails while
# writes are paused, which is put back, then for real). Two more days on the bosun whose writes go
# through the journals, then a mid-afternoon update to the next version (this checkout and
# tests/sandbox/update_patch.sh) on a year of data, with windows idle, busy and waiting on a background
# sleep, writes and hooks arriving all along and Slack messages during the hold; going back by hand,
# as docs/setup.md says, and forward again. Then writers at once, the clock going back, writes cut short
# like a power cut, the knowledge files changed outside their journal (by hand, by git, by the release),
# launchd, the console, uninstall, and a year on. At each step the ledger, the knowledge base and their
# journals must hold up (`ledger validate --journal`, `kg validate`), and nothing of a person deleted on
# request may be left in the knowledge folder, its history or an update's backup.
# Claude Code is a stand-in (tests/sandbox/claude-stand-in). It changes the machine it runs on (launchd,
# pmset, the clock), so it only runs in the throwaway Mac of `tests/sandbox.sh mac work`, which copies in
# the two bundles it installs from, ~/repo-base.bundle (the release) and ~/repo-tree.bundle (this
# checkout), and the release's own get.sh (~/release-get.sh), which installs the release as a user did
# then. The install's origin is ~/repo.bundle, which each head of the branch replaces.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
# The guard (it refuses to run anywhere else), the checks and the steps tests/install_e2e.sh shares.
source "$(dirname "$0")/sandbox/e2e_lib.sh" || exit 1
source "$(dirname "$0")/sandbox/acme_week.sh" || exit 1
# Not on a GitHub runner either: it needs the two bundles and the release's get.sh, and it sets the clock.
copied_in() {
  [ -e /etc/bosun-sandbox ] && [ -s "$HOME/repo-base.bundle" ] && [ -s "$HOME/repo-tree.bundle" ] && [ -s "$HOME/release-get.sh" ]
}
copied_in || {
  echo "$0 only runs in tests/sandbox.sh mac work, which copies in ~/repo-base.bundle, ~/repo-tree.bundle and ~/release-get.sh" >&2
  exit 1
}
GET_SH="$HOME/release-get.sh"

STATE="$HOME/.local/state/bosun"
JOURNAL="$STATE/journal/ledger"
KJOURNAL="$KNOWLEDGE/journal"
T_FIRST="" # the first task the new bosun makes
DAY=86400
TODAY=0        # the day the clock is on: days after the first
CLOCK_START="" # "<wall time> <monotonic time>" at the start: the real time, whatever `date` is set to later

real_time() {
  # shellcheck disable=SC2086 # two numbers
  python3 -c 'import sys, time; wall, mono = map(float, sys.argv[1:]); print(int(wall + time.monotonic() - mono))' $CLOCK_START
}
set_clock() { # <seconds ahead of the real time>
  local at
  at=$(($(real_time) + $1))
  sudo date -u "$(date -u -r "$at" +%m%d%H%M%Y.%S)" >/dev/null
}
clock_reads() { # <seconds ahead of the real time>: within a minute of it
  local off
  off=$(($(date +%s) - $(real_time) - $1))
  [ "${off#-}" -le 60 ] || {
    echo "the clock is $off seconds off: $(date)"
    return 1
  }
}
start_clock() { # the real time from here on; it is put back however this ends
  CLOCK_START="$(python3 -c 'import time; print(time.time(), time.monotonic())')"
  trap 'set_clock 0' EXIT
  trap 'exit 1' INT TERM HUP
  # Network time off, or timed may undo `sudo date`. It stays off: the clone is deleted after the run.
  sudo systemsetup -setusingnetworktime off </dev/null 2>&1 | sed 's/^/  note  /'
}
put_the_clock_back() {
  set_clock 0
  check "the clock is back at the real time: $(date -u '+%a %F %R') UTC" clock_reads 0
}

morning_runs() { cat "$STATE"/logs/morning-*.log 2>/dev/null | grep -c "morning run complete"; }
morning_runs_above() { [ "$(morning_runs)" -gt "$1" ]; }
jobs_quiet() { ! pgrep -f 'libexec/bosun-(morning|learn)' >/dev/null; }

next_day() { # <days after the first>: the clock moves on, and whatever launchd does about it settles
  local runs
  runs="$(morning_runs)"
  TODAY="$1"
  set_clock $(($1 * DAY))
  check "the clock moves on to day $(($1 + 1)): $(date -u '+%a %F %R') UTC" clock_reads $(($1 * DAY))
  sleep 5
  check "... what launchd runs for it finishes" wait_for 300 jobs_quiet
  morning_runs_above "$runs" && echo "  note  launchd ran the morning job when the clock moved"
}

journal_lines() { # seq|hlc [<journal folder>]: every journal file's seq runs 1..n, or every clock is
  # above the one before (the ledger's journal, unless another folder is named)
  python3 - "${2:-$JOURNAL}" "$1" <<'PY'
import json, sys
from pathlib import Path
root, what = Path(sys.argv[1]), sys.argv[2]
bad = []
for path in sorted(root.glob("*.jsonl")):
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    seqs, clocks = [c["seq"] for c in lines], [c["hlc"] for c in lines]
    print(f"{path.name}: {len(lines)} lines, seq {seqs[0]}..{seqs[-1]}, hlc {clocks[0]}..{clocks[-1]}")
    if what == "seq" and seqs != list(range(1, len(seqs) + 1)):
        bad.append(f"{path.name}: the seqs do not run 1..{len(seqs)}")
    if what == "hlc":
        bad += [f"{path.name}: seq {b['seq']} hlc {b['hlc']} after {a['hlc']}" for a, b in zip(lines, lines[1:]) if a["hlc"] >= b["hlc"]]
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
}
in_the_ledger() { # <file of ids>: each one shows (and there is at least one)
  local id gone=0
  [ -s "$1" ] || {
    echo "no ids to look for"
    return 1
  }
  while read -r id; do
    bosun ledger show "$id" >/dev/null 2>&1 || {
      echo "not in the ledger: $id"
      gone=1
    }
  done <"$1"
  return "$gone"
}
valid() { check "${1:-ledger validate --journal passes}" bosun ledger validate --journal; }
kg_valid() { check "${1:-kg validate passes}" bosun kg validate; }
# The entities and the fact log, each record with its keys sorted: what the knowledge base holds.
knowledge_files() { jq -cS . "$KNOWLEDGE/entities.jsonl" | sort && jq -cS . "$KNOWLEDGE/facts.jsonl"; }
same_knowledge() { diff <(knowledge_files) "$OUT/knowledge.txt"; }
no_knowledge_journal() { [ ! -e "$KJOURNAL" ] && [ ! -e "$KNOWLEDGE/view.json" ] && [ ! -e "$KNOWLEDGE/secret.key" ]; }

# What the release does: from before every journal, or one that journals the ledger already (and mints
# its ids); the knowledge base's journal is new in this checkout either way.
RELEASE_JOURNALS=0
what_the_release_does() {
  [ -e "$DIR/lib/journal.py" ] || return 0
  RELEASE_JOURNALS=1
  RELEASE_TASK_ID="$NEW_TASK_ID"
  echo "  note  the release journals the ledger already, and mints its ids; the knowledge base it does not"
}

the_old_week() {
  echo "a week on the last release: three days, the clock a day on between them"
  day_1
  next_day 1
  day_2
  next_day 2
  day_3
  jq -S 'del(.journal) | .said //= []' "$STATE/ledger.json" >"$OUT/snapshot.json"
  ITEMS="$(jq '.items | length' "$STATE/ledger.json")"
  echo "  note  the ledger before the upgrade: $ITEMS items"
  if [ "$RELEASE_JOURNALS" = 1 ]; then
    valid "the release journals the ledger: ledger validate --journal passes"
  else
    check "the release writes no journal" test ! -e "$STATE/journal"
  fi
  knowledge_files >"$OUT/knowledge.txt"
  bosun kg stats >"$OUT/kg-stats.txt"
  echo "  note  the knowledge base before the upgrade: $(cat "$OUT/kg-stats.txt")"
  check "... nor one for the knowledge base" no_knowledge_journal
}

no_journal_yet() { { [ ! -e "$JOURNAL" ] || [ -z "$(ls -A "$JOURNAL")" ]; } && jq -e 'has("journal") | not' "$STATE/ledger.json"; }
tree_head() { git bundle list-heads "$HOME/repo-tree.bundle" | awk '{ print $1; exit }'; }
# The release, as its checkout holds it; this checkout (A) once the conversion made it a version, and
# its folder; the next version (B: this checkout and tests/sandbox/update_patch.sh), and its folder; the
# pane of the shell I opened in bosun's tmux session by hand.
RELEASE_COMMIT="" A_COMMIT="" A_ROOT="" B_COMMIT="" B_ROOT="" MINE_PANE=""

# ---- windows, the check-in loop and launchd, as they are while an update runs ----------------------

REPO_WINDOWS=(acme-api acme-web invoice-exporter)
agent_of() { pgrep -f "remote-control $1( |\$)" | head -1; } # <window>: the Claude stand-in running in it
windows_up() {
  local window
  for window in orchestrator "${REPO_WINDOWS[@]}"; do [ -n "$(agent_of "$window")" ] || return 1; done
}
agents_now() { # each window and the Claude in it, a line each
  local window
  for window in orchestrator "${REPO_WINDOWS[@]}"; do echo "$window $(agent_of "$window")"; done
}
resumes_of() { grep -c "resume, session" "$STAND_IN/$1.log" 2>/dev/null; } # <window>: its stand-in's resumes so far
status_of() { bosun sessions --json | jq -r --arg w "$1" '.[] | select(.name == $w) | .agent.status // empty'; }
pane_of() { tmux -L bosun list-windows -t "=bosun" -F '#{window_name} #{pane_pid}' | awk -v w="$1" '$1 == w { print $2 }'; }
loop_pid() { # the check-in loop's process (not a subshell of it, which has its command line)
  local pid
  for pid in $(pgrep -f "bosun-checkin --loop"); do
    ps -o command= -p "$(ps -o ppid= -p "$pid")" 2>/dev/null | grep -q "bosun-checkin --loop" && continue
    echo "$pid"
    return 0
  done
  return 1
}
# <the loop's pid before>: that process is gone and another loop runs now
loop_restarted() {
  local now
  ! kill -0 "$1" 2>/dev/null && now="$(loop_pid)" && [ "$now" != "$1" ]
}
loop_runs_from() { # <folder>: the loop's command line runs bosun-checkin from there
  local command
  command="$(ps -o command= -p "$(loop_pid)")"
  [[ "$command" == *"$1/libexec/bosun-checkin --loop"* ]] && return 0
  echo "the loop runs: $command"
  return 1
}
my_own_shell() { # a plain shell I open in bosun's tmux session, as prefix-c does
  tmux -L bosun new-window -d -t "=bosun:" -n mine -- /bin/zsh -l
  MINE_PANE="$(pane_of mine)"
  [ -n "$MINE_PANE" ]
}
mine_untouched() { # the same pane, no bosun launch in it, and not in the update's record
  [ "$(pane_of mine)" = "$MINE_PANE" ] && ! tmux -L bosun capture-pane -p -J -S -500 -t "=bosun:mine" | grep -q "launch" &&
    ! jq -e '.windows | has("mine")' "$STATE/update.json" >/dev/null 2>&1 && return 0
  echo "the pane now: $(pane_of mine) (was $MINE_PANE)"
  return 1
}
JOBS=("$LABEL.watchdog" "$LABEL.learn")
jobs_loaded() { launchctl print "$DOMAIN/${JOBS[0]}" >/dev/null && launchctl print "$DOMAIN/${JOBS[1]}" >/dev/null; }
plists_sum() { cat "$HOME/Library/LaunchAgents/$LABEL".*plist | cksum; }

# In the background until $OUT/watch.stop is there: how often the install path was looked at and found
# missing, and how often launchd said each of the watchdog's and learning's jobs was not loaded.
watch_the_install() { # <file it writes>
  rm -f "$OUT/watch.stop"
  python3 - "$DIR/bin/bosun" "$DOMAIN/${JOBS[0]}" "$DOMAIN/${JOBS[1]}" "$OUT/watch.stop" >"$1" <<'PY' &
import os, subprocess, sys, time
path, jobs, stop = sys.argv[1], sys.argv[2:4], sys.argv[4]
looks = missing = 0
unloaded = dict.fromkeys(jobs, 0)
asked = 0.0
while not os.path.exists(stop):
    looks += 1
    missing += not os.path.exists(path)
    if time.monotonic() - asked > 0.25:
        asked = time.monotonic()
        for job in jobs:
            unloaded[job] += subprocess.run(["launchctl", "print", job], capture_output=True).returncode != 0
print(f"looked {looks} times, missing {missing}")
for job, count in unloaded.items():
    print(f"{job} unloaded {count}")
PY
}
watched() { # <pid of watch_the_install>: stopped, and what it saw as notes
  touch "$OUT/watch.stop"
  wait "$1"
}
jobs_were_unloaded() {
  ! grep -q "unloaded 0$" "$1" && [ "$(grep -c " unloaded " "$1")" = 2 ] && return 0
  cat "$1"
  return 1
}
never_missing() { grep -qE "^looked [1-9][0-9]* times, missing 0$" "$1" || { cat "$1" && return 1; }; }

# ---- the next head of the branch -----------------------------------------------------------------

# This checkout patched by its own tests/sandbox/update_patch.sh (with <flags>), committed, as the head
# of the branch in the install's origin (~/repo.bundle).
a_head() { # <name> [update_patch.sh flags...]
  local tree="$OUT/$1"
  shift
  rm -rf "$tree"
  git clone -q --branch sandbox-mac-tree "$HOME/repo-tree.bundle" "$tree" &&
    "$tree/tests/sandbox/update_patch.sh" "$@" "$tree" &&
    git -C "$tree" -c user.name=sandbox -c user.email=sandbox@example.com commit -qam "the next version" &&
    git -C "$tree" bundle create -q "$HOME/repo.bundle.new" sandbox-mac-tree &&
    mv "$HOME/repo.bundle.new" "$HOME/repo.bundle"
}

# ---- the release at work, and its first update -------------------------------------------------------

the_release_at_work() {
  echo "the release at work: its windows, its check-in loop, and a shell I opened by hand"
  RELEASE_COMMIT="$(git -C "$DIR" rev-parse HEAD)"
  check "the release is a checkout from before versions" bash -c '[ -d "$1/.git" ] && [ ! -L "$1" ] && [ ! -e "$2" ]' _ "$DIR" "$VERSIONS"
  check "... set up: its install.sh linked ~/.local/bin/bosun into it" test "$(readlink "$HOME/.local/bin/bosun")" = "$DIR/bin/bosun"
  bosun stop >/dev/null 2>&1
  bosun start >/dev/null 2>&1
  check "its windows come up: the orchestrator and the three repos" wait_for 120 windows_up
  check "... and the check-in loop in its checkin window" wait_for 60 loop_pid
  my_own_shell
  check "... and my own shell beside them" test -n "$MINE_PANE"
}

# A first update whose migration fails while writes are paused: everything as it was, and what it
# stopped of bosun's own work started again as it was (the jobs from their plists, the loop on the old
# code). Nothing past the switch ran, so no window was touched.
a_conversion_put_back() {
  echo "the first update converts the release, but its migration fails while writes are paused: all put back"
  local out="$OUT/put-back.txt" loop plists ledger agents watching rc
  check "a head whose ledger migration fails (update_patch.sh --failing)" a_head failing --failing
  loop="$(loop_pid)"
  plists="$(plists_sum)"
  ledger="$(cksum <"$STATE/ledger.json")"
  agents="$(agents_now)"
  watch_the_install "$OUT/put-back-watch.txt"
  watching=$!
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$out" 2>&1
  rc=$?
  watched "$watching"
  cat "$out"
  check "it fails (exit 1), saying what failed and that everything is as it was" bash -c '
    [ "$1" = 1 ] && grep -q "failed on purpose" "$2" && grep -q "Everything is as it was" "$2"' _ "$rc" "$out"
  check "... the watchdog's and learning's jobs were unloaded while it ran" jobs_were_unloaded "$OUT/put-back-watch.txt"
  check "... and are loaded again" jobs_loaded
  check "... from their plists as they were on disk, unchanged" test "$(plists_sum)" = "$plists"
  check "... the check-in loop ended at the update's SIGTERM (it says so; that process is gone), and another runs" \
    bash -c 'grep -q "stopped the check-in loop" "$1"' _ "$out"
  check "... started again" wait_for 60 loop_restarted "$loop"
  check "... on the old code: the checkout's own bosun-checkin" loop_runs_from "$DIR"
  check "... the install path is the release's checkout still" bash -c '
    [ ! -L "$1" ] && [ "$(git -C "$1" rev-parse HEAD)" = "$2" ]' _ "$DIR" "$RELEASE_COMMIT"
  check "... the ledger as it was, byte for byte" test "$(cksum <"$STATE/ledger.json")" = "$ledger"
  check "... the windows as they were" test "$(agents_now)" = "$agents"
  check "... my shell as it was" mine_untouched
  check "... and the mark of what it stopped is gone" test ! -e "$STATE/update/own-work.json"
}

# Each of bosun's windows restarted on the new version as one whose launch changed (the release
# recorded no launch), in the same conversation, and answered the update's prompt.
restarted_and_answered() { # <file of "<window> <pid before> <resumes before>" lines>
  local window before resumes
  while read -r window before resumes; do
    jq -e --arg w "$window" '.windows[$w][2:] == ["restarted", "answered"]' "$STATE/update.json" >/dev/null &&
      [ "$(agent_of "$window")" != "$before" ] && [ "$(resumes_of "$window")" -gt "${resumes:-0}" ] &&
      [ -s "$STATE/update/notes/$window.status" ] || {
      echo "$window: $(jq -c --arg w "$window" '.windows[$w]' "$STATE/update.json"), pid $(agent_of "$window") (was $before), resumes $(resumes_of "$window") (were $resumes)"
      return 1
    }
  done <"$1"
}
summary_says() { # <output> <from commit> <to commit>: it ends with the summary, from one to the other
  sed -n '/^bosun updated: /,$p' "$1" >"$1.summary"
  grep -qE "^bosun updated: ${2:0:7} -> ${3:0:7} \([0-9]+ commits?\) in " "$1.summary" &&
    grep -qE "^Backup: ~/.local/state/bosun/backups/[^ ]+ \([0-9.]+ (bytes|KB|MB|GB)\)$" "$1.summary" &&
    [ "$(tail -1 "$1.summary")" = "Going back: docs/setup.md#going-back" ] || {
    cat "$1.summary"
    return 1
  }
}
the_swap() { # <the checkout's inode before>: the checkout moved whole to legacy-<its commit>, the link in its place
  local legacy="$VERSIONS/legacy-${RELEASE_COMMIT:0:12}"
  [ -L "$DIR" ] && a_version "$A_ROOT" && [ "$(git -C "$legacy" rev-parse HEAD)" = "$RELEASE_COMMIT" ] &&
    [ "$(stat -f %i "$legacy")" = "$1" ] || {
    ls -la "$VERSIONS" "$DIR"
    return 1
  }
}
no_temporary_links() { [ -z "$(find "$(dirname "$DIR")" -maxdepth 1 -name '.bosun.link-*')" ]; }

the_upgrade() {
  echo "the upgrade: the piped installer again, which converts the release's checkout at a quiet moment"
  local out="$OUT/upgrade.txt" loop inode watching upgraded
  cp "$HOME/repo-tree.bundle" "$HOME/repo.bundle"
  loop="$(loop_pid)"
  inode="$(stat -f %i "$DIR")"
  agents_now | while read -r window pid; do echo "$window $pid $(resumes_of "$window")"; done >"$OUT/release-agents.txt"
  watch_the_install "$OUT/upgrade-watch.txt"
  watching=$!
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$out" 2>&1
  upgraded=$?
  watched "$watching"
  cat "$out"
  A_ROOT="$(cd "$DIR" && pwd -P)"
  A_COMMIT="$(git -C "$DIR" rev-parse HEAD)"
  check "it passes" test "$upgraded" = 0
  check "the checkout is the new bosun" test "$A_COMMIT" = "$(tree_head)"
  check "... versions side by side, the install path a link to the new one" side_by_side
  check "the release's checkout is set aside whole as legacy-<its commit>, swapped for the link in one step" the_swap "$inode"
  check "... the install path never missing meanwhile ($(head -1 "$OUT/upgrade-watch.txt"))" never_missing "$OUT/upgrade-watch.txt"
  check "... and no temporary link left beside it" no_temporary_links
  check "the watchdog's and learning's jobs were unloaded while it converted" jobs_were_unloaded "$OUT/upgrade-watch.txt"
  check "... and are loaded again, on the install path" jobs_loaded_on_the_link
  check "the check-in loop ended at the update's SIGTERM (it says so; that process is gone)" \
    bash -c 'grep -q "stopped the check-in loop" "$1"' _ "$out"
  check "... and another runs" wait_for 60 loop_restarted "$loop"
  check "... on the new code" loop_runs_from "$A_ROOT"
  check "each of bosun's windows restarted on the new version, resuming its conversation, and answered" \
    restarted_and_answered "$OUT/release-agents.txt"
  check "... my own shell left as it is" mine_untouched
  check "it ends with where the checkout is kept, and the summary" bash -c '
    grep -qF "The checkout is kept as $2 until the next update" "$1"' _ "$out" "$VERSIONS/legacy-${RELEASE_COMMIT:0:12}"
  check "... from the release to this checkout, with its backup" summary_says "$out" "$RELEASE_COMMIT" "$A_COMMIT"
  check "... and the mark of bosun's own work it stopped is gone" test ! -e "$STATE/update/own-work.json"
  the_orchestrator_keeps_its_connectors "$out.summary"
  check "bosun doctor passes" bash -c 'bosun doctor >"$1" 2>&1 || { cat "$1"; exit 1; }' _ "$OUT/doctor.txt"
  check "... and warns while the orchestrator keeps every connector" \
    grep -qE "^ +warn +orchestrator: all \(every MCP server and connector\): name the ones it needs" "$OUT/doctor.txt"
  ledger_before_its_first_write
  check "doctor names the knowledge base from before journals as a note" \
    grep -E "^ +ok +the knowledge base and its journal: no journal yet" "$OUT/doctor.txt"
  check "kg validate says so, and passes" bash -c 'bosun kg validate | grep -q "no journal yet"'
  check "... and starts none, nor the key of the forgotten" no_knowledge_journal
  bosun stop >/dev/null 2>&1
}
# The release had no connector lists, so its orchestrator reached every server: the update keeps that,
# writing the value to bosun.env (with a copy as it was), and its summary says so, and that repo windows
# now start without any. <summary>: the summary's lines.
the_orchestrator_keeps_its_connectors() {
  check "the update wrote BOSUN_ORCHESTRATOR_CONNECTORS=\"all\" to bosun.env, keeping a copy as it was" bash -c '
    grep -qx "BOSUN_ORCHESTRATOR_CONNECTORS=\"all\"" "$1/bosun.env" && ! grep -q "^BOSUN_CONNECTORS=" "$1/bosun.env" &&
      ls "$1"/bosun.env.pre-update-* >/dev/null' _ "$CONFIG"
  check "... its summary says so, with what each added key means" bash -c '
    grep -q "^  bosun\.env  *BOSUN_ORCHESTRATOR_CONNECTORS=\"all\" added (before: bosun\.env\.pre-update-" "$1" &&
      grep -q "^  BOSUN_ORCHESTRATOR_CONNECTORS  *the orchestrator keeps every connector, as before" "$1" &&
      grep -q "^  BOSUN_CONNECTORS  *repo windows now start without connectors" "$1" || { cat "$1"; exit 1; }' _ "$1"
}
jobs_loaded_on_the_link() {
  jobs_loaded && grep -qF "$DIR/bin/bosun" "$HOME/Library/LaunchAgents/${JOBS[0]}.plist"
}

ledger_before_its_first_write() {
  if [ "$RELEASE_JOURNALS" = 1 ]; then
    valid "the release's journal goes on: ledger validate --journal passes"
    return
  fi
  check "no journal yet: nothing has written since" no_journal_yet
  check "ledger validate --journal says so, and passes" bash -c 'bosun ledger validate --journal | grep -q "no journal yet"'
  check "... and starts none" no_journal_yet
}

same_as_before() { diff <(jq -S 'del(.journal) | .said //= []' "$STATE/ledger.json") "$OUT/snapshot.json"; }
replica_made() { jq -e '.replica | test("^[0-9a-f]{32}$")' "$STATE/replica.json" && [ "$(stat -f %Lp "$STATE/replica.json")" = 600 ]; }
starts_whole() { head -1 "$JOURNAL/$(jq -r .replica "$STATE/replica.json").jsonl" | jq -e '.seq == 1 and .cmd == "genesis"'; }
KG_FIRST=1 # the knowledge base's first write, beside the ledger's: its exit code

the_first_write() {
  echo "the first write on the new bosun"
  # The knowledge base's first write runs at the same moment: the two stores make this Mac's replica id
  # together (one id for both, R85), or take the one a release that journals the ledger made.
  bosun kg expire >"$OUT/kg-first.txt" 2>&1 &
  local kg=$!
  step "a write that changes nothing" render
  wait "$kg"
  KG_FIRST=$?
  check "the journal is there" bash -c 'ls "$1"/*.jsonl' _ "$JOURNAL"
  check "... with this Mac's replica id, readable only by me" replica_made
  if [ "$RELEASE_JOURNALS" = 1 ]; then
    check "... going on from the release's, its seq with no gap" journal_lines seq
  else
    check "... starting with the old ledger, whole" starts_whole
  fi
  valid
  check "the ledger is as it was" same_as_before
  check "... $ITEMS items" test "$(jq '.items | length' "$STATE/ledger.json")" = "$ITEMS"
  check "an id from the first day still works: $T_CSV" bosun ledger show "$T_CSV"
  check "... and lists" bash -c 'bosun ledger list | grep -qF "$1"' _ "$T_CSV"
  step "the morning's first chore" mark-run morning
  made T_FIRST "$NEW_TASK_ID" "a new task" spawn --title "Check the exports after the upgrade" \
    --ask "Make sure last night's exports ran" --via "user: check the exports"
  valid
}

replica_files() { (cd "$1" && ls -1 -- *.jsonl) | grep -vx 'local.jsonl'; }
one_replica() { # both stores write this Mac's changes under the one id in replica.json
  local id
  id="$(jq -r .replica "$STATE/replica.json")" || return 1
  [ "$(replica_files "$JOURNAL")" = "$id.jsonl" ] && [ "$(replica_files "$KJOURNAL")" = "$id.jsonl" ] && return 0
  echo "replica.json: $id; the ledger's: $(replica_files "$JOURNAL" | tr '\n' ' ')"
  echo "the knowledge base's: $(replica_files "$KJOURNAL" | tr '\n' ' ')"
  return 1
}
key_made() { [ -s "$KNOWLEDGE/secret.key" ] && [ "$(stat -f %Lp "$KNOWLEDGE/secret.key")" = 600 ]; }
not_in_history() { ! git -C "$KNOWLEDGE" ls-files | grep -E 'secret\.key|torn'; }
lists_fact() { bosun kg facts "$1" --status all --closed --json | jq -e --arg id "$2" 'any(.[]; .id == $id)'; }

the_first_knowledge_write() {
  echo "the knowledge base's first write on the new bosun, beside the ledger's"
  check "it went through" said "$KG_FIRST" "$(cat "$OUT/kg-first.txt")" "recorded 0 expired proposals"
  check "its journal appears" bash -c 'ls "$1"/*.jsonl' _ "$KJOURNAL"
  check "... under this Mac's one replica id, the ledger's too" one_replica
  kg_valid
  check "the entities and facts are as they were" same_knowledge
  check "... and so are its counts" bash -c 'bosun kg stats | diff - "$1"' _ "$OUT/kg-stats.txt"
  check "the key of the forgotten is made, readable only by me" key_made
  check "a fact id from the release still works: $K_CAVEAT" lists_fact feature/csv-export "$K_CAVEAT"
  kg_says "... and an old proposal is confirmed by its id" "confirmed" confirm "$(fact_of environment/staging-2 status)"
  kg_made K_FIRST "$NEW_FACT_ID" "a new fact gets a minted id" propose feature/csv-export caveat \
    --value "The CSV header row stays in English" --source "ledger:$T_FIRST" --source "report:$T_FIRST"
  check "the key is not in the knowledge history, nor any torn file" not_in_history
  kg_valid
}

# What never leaves this Mac stays in local.jsonl: who a fact names, and my words.
PEOPLE_WORDS=(person/ "Dana Example" "Lee Sample" Priya Marco)
MY_WORDS=("$QUOTE_OLD" "last business day" "$SESSION_OLD" "00000000-0000-0000-0000-00000000000")
in_file() { # <file> <text...>: each is in it (any case)
  local file="$1" text missing=""
  shift
  for text in "$@"; do grep -qiF -- "$text" "$file" || missing="$missing, $text"; done
  [ -z "$missing" ] && return 0
  echo "not in ${file##*/}:${missing#,}"
  return 1
}
not_in() { # <file> <text...>: none of them is in it (any case)
  local file="$1" text found=""
  shift
  for text in "$@"; do grep -qiF -- "$text" "$file" && found="$found, $text"; done
  [ -z "$found" ] && return 0
  echo "in ${file##*/}:${found#,}"
  return 1
}
replica_journal() { echo "$KJOURNAL/$(jq -r .replica "$STATE/replica.json").jsonl"; }
no_copy_holds() { # <text...>: no update backup or rehearsal copy holds any of them (any case)
  local text found
  for text in "$@"; do
    found="$(grep -rliF -- "$text" "$STATE/backups" "$STATE/update/rehearsal" 2>/dev/null)" || continue
    echo "$text, in: $found"
    return 1
  done
}
# <text>: the torn files holding it
torn_named() { grep -liF -- "$1" "$KJOURNAL"/*.torn-* 2>/dev/null; }
not_in_git() { # <text...>: none of them in the knowledge folder's git history (any case)
  local text log
  log="$(git -C "$KNOWLEDGE" log -p --all 2>&1)" || {
    echo "$log"
    return 1
  }
  for text in "$@"; do
    grep -qiF -- "$text" <<<"$log" || continue
    echo "in the knowledge history: $text"
    return 1
  done
}
nowhere() { # <text...>: in no file of the knowledge folder (torn ones too), nor in its git history
  local text found
  for text in "$@"; do
    found="$(grep -rliF -- "$text" "$KNOWLEDGE")" || continue
    echo "$text, in: $found"
    return 1
  done
  not_in_git "$@"
}
# Half of a change naming a person at the end of local.jsonl, as a power loss mid-append leaves it (a
# kill cannot: a change is one write).
a_torn_tail() { printf '{"body": {"id": "%s", "name": "%s"' "$1" "$2" >>"$KJOURNAL/local.jsonl"; }

knowledge_privacy() {
  echo "privacy, read from the knowledge journal's own files"
  check "no person and none of my words in the journal that can leave this Mac" \
    not_in "$(replica_journal)" "${PEOPLE_WORDS[@]}" "${MY_WORDS[@]}"
  check "... they are in local.jsonl" in_file "$KJOURNAL/local.jsonl" person/dana person/lee person/priya "${MY_WORDS[@]}"
  check "... but not Marco, forgotten on the release" not_in "$KJOURNAL/local.jsonl" person/marco Marco
  check "he is not added again without --allow-forgotten" refuses "deleted on request" \
    bosun kg add-entity person/marco --name "Marco Placeholder"
  a_torn_tail person/priya "Priya Placeholder"
  kg_says "a power loss left half a change naming Priya: the next write sets it aside" "moved it aside" \
    propose feature/csv-export caveat --value "CSV exports end with a newline" --source "ledger:$T_FIRST"
  check "... in a torn file" torn_named priya
  kg_says "Priya asked to be forgotten" "deleted the person" forget person/priya
  check "... and the update backups, which held her, saying so ($(grep -o 'deleted [0-9]* update backups*' <<<"$KG_SAID"))" \
    said 0 "$KG_SAID" "taken before this; going back past it is no longer possible"
  check "... no backup or rehearsal copy holds her" no_copy_holds person/priya "Priya Placeholder"
  check "... kept as a keyed hash" last_forgotten '.hash_kind == "hmac-sha256"'
  check "... nothing of her in local.jsonl" not_in "$KJOURNAL/local.jsonl" priya "export questions batched"
  check "... nor in a torn file" bash -c '! ls "$1"/*.torn-* 2>/dev/null | grep -F local.jsonl' _ "$KJOURNAL"
  check "... nor in the knowledge history" not_in_git priya
  check "... nor anywhere in the knowledge folder" nowhere priya
  kg_valid
}

the_new_days() {
  echo "two more days on the new bosun"
  next_day 3
  day_4
  valid
  kg_valid
  check "my words of today are not in the journal that can leave this Mac" not_in "$(replica_journal)" \
    "$QUOTE_NEW" "$SESSION_NEW"
  check "... they are in local.jsonl" in_file "$KJOURNAL/local.jsonl" "$QUOTE_NEW" "$SESSION_NEW"
  next_day 4
  day_5
  valid
  kg_valid
}

# ---- a mid-afternoon update ----------------------------------------------------------------------

# How the update ended, how long it paused writes, its steps while it holds bosun's own work (from
# marked to checked, writes are paused too), and a temporary link a switch cut short left before it.
UPDATE_RC="" FREEZE_MS="" LEFT_LINK=""
HOLD_STEPS="holding marked backed-up migrated switched checked"

year_made() {
  a_year_of_work >"$OUT/year.txt" 2>&1 && return 0
  cat "$OUT/year.txt"
  return 1
}
a_year_of_data() {
  echo "  a year of work in the ledger and the knowledge base"
  check "$YEAR_DAYS days of acme work, through the installed bosun's own commands" year_made
  sed 's/^/  note  /' "$OUT/year.txt"
  valid "... ledger validate --journal passes on it"
  kg_valid "... kg validate passes on it"
}
afternoon_statuses() {
  [ "$(status_of acme-api)" = idle ] && [ "$(status_of acme-web)" = shell ] &&
    [ "$(status_of invoice-exporter)" = busy ] && [ "$(status_of orchestrator)" = idle ]
}
the_afternoon() {
  echo "  the afternoon: the windows at work, one waiting on a background sleep, one in a long turn"
  bosun stop >/dev/null 2>&1
  afternoon_settings
  afternoon_windows
  afternoon_work
  bosun start >/dev/null 2>&1
  check "the windows come up on this checkout's version" wait_for 120 windows_up
  check "... acme-web waits on $BACKGROUND_SLEEP (shell), invoice-exporter is busy, the others idle" wait_for 30 afternoon_statuses
  check "... the check-in loop runs" wait_for 60 loop_pid
  my_own_shell
  check "... and my own shell beside them" test -n "$MINE_PANE"
  check "the Slack watcher looks before the update: nothing new" says " 0 new" bosun slack-watch --once
  LEFT_LINK="$(dirname "$DIR")/.bosun.link-0000dead"
  check "a temporary link a switch cut short is planted beside the install, an hour old" \
    bash -c 'ln -s "bosun-versions/gone" "$1" && touch -h -t "$(date -v-1H +%Y%m%d%H%M)" "$1" && [ -L "$1" ]' _ "$LEFT_LINK"
}

# What is there before the update, which must all be there after it: the ledger's items, both journals
# and the fact log (append-only, so each must begin as it was), the events queued, what I said in the
# windows, and the signal hooks' records.
afternoon_before() {
  mkdir -p "$OUT/before"
  jq -r '.items | keys[]' "$STATE/ledger.json" | sort >"$OUT/before/items"
  jq -c '.said[]?' "$STATE/ledger.json" >"$OUT/before/said"
  jq -r '.id' "$STATE/events/queue.jsonl" 2>/dev/null | sort >"$OUT/before/events"
  cp -R "$JOURNAL" "$OUT/before/journal"
  cp -R "$KJOURNAL" "$OUT/before/kjournal"
  cp "$KNOWLEDGE/facts.jsonl" "$OUT/before/facts.jsonl"
  cp -R "$STATE/signals" "$OUT/before/signals"
  afternoon_counts | sed 's/^/  note  before the update: /'
}
afternoon_counts() {
  echo "$(jq '.items | length' "$STATE/ledger.json") items, $(cat "$JOURNAL"/*.jsonl | wc -l | tr -d ' ') ledger changes," \
    "$(wc -l <"$KNOWLEDGE/facts.jsonl" | tr -d ' ') fact log lines, $(cat "$KJOURNAL"/*.jsonl | wc -l | tr -d ' ') knowledge changes," \
    "$(wc -l <"$STATE/events/queue.jsonl" | tr -d ' ') events, $(jq '.said | length' "$STATE/ledger.json") said," \
    "$(cat "$STATE"/signals/*.jsonl | wc -l | tr -d ' ') signals"
}
begins_as() { # <file before> <file now>: now begins with all of before
  cmp -s "$1" <(head -c "$(wc -c <"$1")" "$2") || {
    echo "$2 does not begin as $1 did"
    return 1
  }
}
all_kept() { # what was there before the update is there after it
  local file missing
  missing="$(comm -23 "$OUT/before/items" <(jq -r '.items | keys[]' "$STATE/ledger.json" | sort))"
  [ -z "$missing" ] || { echo "items gone: $missing" && return 1; }
  missing="$(comm -23 "$OUT/before/events" <(jq -r '.id' "$STATE/events/queue.jsonl" | sort))"
  [ -z "$missing" ] || { echo "events gone: $missing" && return 1; }
  if grep -vxF -f <(jq -c '.said[]?' "$STATE/ledger.json") "$OUT/before/said" | grep -q .; then
    echo "said entries gone"
    return 1
  fi
  for file in "$OUT"/before/journal/*.jsonl; do begins_as "$file" "$JOURNAL/${file##*/}" || return 1; done
  for file in "$OUT"/before/kjournal/*.jsonl; do begins_as "$file" "$KJOURNAL/${file##*/}" || return 1; done
  for file in "$OUT"/before/signals/*.jsonl; do begins_as "$file" "$STATE/signals/${file##*/}" || return 1; done
  begins_as "$OUT/before/facts.jsonl" "$KNOWLEDGE/facts.jsonl"
}

# In the background while the update runs: the steps it records ($OUT/steps.txt), two Slack messages
# once it holds bosun's own work, a write of each kind as soon as it pauses writes, and the busy window's
# long turn ending once the update waits only for it.
FREEZE_STEPS="marked backed-up migrated switched"
watch_the_update() { # <the update's pid>
  local step seen="" posted=0 paused=0 freed=0 kind
  while kill -0 "$1" 2>/dev/null; do
    step="$(jq -r '.step // empty' "$STATE/update.json" 2>/dev/null)"
    if [ -n "$step" ] && [ "$step" != "$seen" ]; then
      echo "$step" >>"$OUT/steps.txt"
      seen="$step"
    fi
    if [ "$posted" = 0 ] && [[ " $HOLD_STEPS " == *" $step "* ]]; then
      a_slack_message 1 && a_slack_message 2 && posted=1
      echo "$step" >"$OUT/slack-posted-at"
    fi
    if [ "$paused" = 0 ] && [[ " $FREEZE_STEPS " == *" $step "* ]]; then
      for kind in "${AFTERNOON_KINDS[@]}"; do afternoon_write "$kind" 901 & done
      paused=1
    fi
    if [ "$freed" = 0 ] && [ "$(jq -c '.pending // empty' "$STATE/update.json" 2>/dev/null)" = '["invoice-exporter"]' ]; then
      echo idle >"$STAND_IN/invoice-exporter.mode"
      freed=1
    fi
    sleep 0.2
  done
  wait
}

the_update_runs() {
  local writers watcher updating
  rm -f "$OUT/writers.stop" "$OUT/steps.txt"
  the_afternoon_writes &
  writers=$!
  sleep 3
  bosun update --yes >"$OUT/afternoon-update.txt" 2>&1 &
  updating=$!
  watch_the_update "$updating" &
  watcher=$!
  wait "$updating"
  UPDATE_RC=$?
  touch "$OUT/writers.stop"
  wait "$writers" "$watcher"
  cat "$OUT/afternoon-update.txt"
}

numbers_once() { # <kind> <the numbers found, one a line>: each write of <kind> that went through, once
  diff <(awk '{ print $1 }' "$OUT/afternoon-$1.ok" | sort -n) <(sort -n <<<"$2") >/dev/null && return 0
  echo "$1: went through $(wc -l <"$OUT/afternoon-$1.ok" | tr -d ' '), found $(grep -c . <<<"$2") ($(sort -n <<<"$2" | uniq -d | head -3 | tr '\n' ' ')twice)"
  return 1
}
found_notes() { bosun ledger show "$T_AFTERNOON" | jq -r '.history[] | .note // "" | capture("^Afternoon note (?<n>[0-9]+)$").n'; }
found_facts() { csv_facts | jq -r '.[] | .value // "" | capture("^Afternoon fact (?<n>[0-9]+):").n'; }
found_prompts() { jq -r '.said[]? | .text | capture("^afternoon prompt (?<n>[0-9]+)$").n' "$STATE/ledger.json"; }
found_prompt_events() { jq -r '.summary // "" | capture("^afternoon prompt (?<n>[0-9]+)$").n' "$STATE/events/queue.jsonl"; }
found_signals() {
  jq -r --arg id "$AFTERNOON_SESSION" 'select(.session == $id) | .running[0].what // "" | capture("^afternoon check (?<n>[0-9]+)$").n' \
    "$STATE/signals/acme-api.jsonl"
}
some_while_paused() { # <kind>: some of its writes began while writes were paused, and none failed
  [ ! -s "$OUT/afternoon-$1.failed" ] && grep -qE " (${FREEZE_STEPS// /|})$" "$OUT/afternoon-$1.ok" && return 0
  cat "$OUT/afternoon-$1.failed" 2>/dev/null
  awk '{ print $2 }' "$OUT/afternoon-$1.ok" | sort | uniq -c
  return 1
}

nothing_lost() {
  echo "  nothing lost: what was there, and every write and hook that arrived during the update"
  afternoon_counts | sed 's/^/  note  after the update: /'
  local kind
  for kind in "${AFTERNOON_KINDS[@]}"; do
    echo "  note  $kind: $(wc -l <"$OUT/afternoon-$kind.ok" | tr -d ' ') went through, by the update's step when they began: $(awk '{ print $2 }' "$OUT/afternoon-$kind.ok" | sort | uniq -c | tr -s ' ' | tr '\n' ',')"
    check "... $kind: none failed, and some began while writes were paused" some_while_paused "$kind"
  done
  check "everything there before the update is there after it, both journals and the fact log as they began" all_kept
  check "each ledger write that went through is in the ledger, once" numbers_once note "$(found_notes)"
  check "each knowledge write, once" numbers_once fact "$(found_facts)"
  check "each prompt I typed (the UserPromptSubmit hook), once in the ledger" numbers_once prompt "$(found_prompts)"
  check "... and once as an event for the orchestrator" numbers_once prompt "$(found_prompt_events)"
  check "each Stop hook's record, once" numbers_once signal "$(found_signals)"
  check "nothing bosun said to a window is recorded as mine" bash -c '! jq -e "[.said[]? | select(.text | test(\"^bosun (was updated|will restart)\"))] | length > 0" "$1"' _ \
    "$STATE/ledger.json"
}

brought_along() { # each window as the update's table says, by what it was doing
  jq -e '.windows.orchestrator == ["orchestrator", "re-read", "asked to re-read", "answered"]
    and .windows["acme-api"] == ["repo", "restart", "restarted", "answered"]
    and .windows["acme-web"] == ["repo", "take stock, then restart", "asked to take stock", "restarted", "answered"]
    and .windows["invoice-exporter"] == ["repo", "wait until idle", "restarted", "answered"]
    and (.windows | has("checkin") | not) and .pending == []' "$STATE/update.json" >/dev/null || {
    jq -c '{step, pending, windows}' "$STATE/update.json"
    return 1
  }
}
sleep_again() { # acme-web's background sleep: in its stock note, and running again under its new Claude
  local agent
  agent="$(agent_of acme-web)"
  grep -qF "$BACKGROUND_SLEEP" "$STATE/update/notes/acme-web.stock" && pgrep -P "$agent" -f "^$BACKGROUND_SLEEP\$" >/dev/null &&
    grep -qF "started again: $BACKGROUND_SLEEP" "$STATE/update/notes/acme-web.status" && return 0
  cat "$STATE/update/notes/acme-web."* 2>&1
  pgrep -fl "$BACKGROUND_SLEEP"
  return 1
}
on_the_next_version() { [ "$(git -C "$DIR" rev-parse HEAD)" = "$B_COMMIT" ] && a_version "$B_ROOT"; }
kept_running_and_reread() {
  [ "$(agent_of orchestrator)" = "$(awk '$1 == "orchestrator" { print $2 }' "$OUT/afternoon-agents.txt")" ] &&
    grep -q "Re-read .*orchestrator/policy.md" "$STAND_IN/orchestrator.log"
}
afternoon_summary() { # the summary names the versions, the migration, the prompt changed and the backup
  local summary="$OUT/afternoon-update.txt.summary"
  summary_says "$OUT/afternoon-update.txt" "$A_COMMIT" "$B_COMMIT" &&
    grep -qE "^  ledger +format 1 -> 2 \([0-9]+ items migrated\)$" "$summary" && grep -qx "Prompts changed: 1 (policy.md)" "$summary" &&
    grep -qF "Backup: ~/.local/state/bosun/backups/$(basename "$(jq -r .backup "$STATE/update.json")")" "$summary" && return 0
  cat "$summary"
  return 1
}
steps_in_order() { # the steps seen, from its plan on, each later than the one before, through the freeze to its windows
  awk -v order="planned rehearsed holding marked backed-up migrated switched checked released agents done" '
    BEGIN { n = split(order, steps, " "); for (i = 1; i <= n; i++) at[steps[i]] = i }
    $1 == "planned" { started = 1 }
    !started { next }
    { if (!($1 in at) || at[$1] <= last) bad = 1; last = at[$1]; seen[$1] = 1 }
    END { exit bad || !seen["agents"] || !(seen["marked"] || seen["backed-up"] || seen["migrated"] || seen["switched"] || seen["checked"]) }' \
    "$OUT/steps.txt"
}

the_update_itself() {
  echo "  the update"
  B_ROOT="$(cd "$DIR" && pwd -P)"
  FREEZE_MS="$(jq -r '.freeze_ms // empty' "$STATE/update.json")"
  check "it ends well (exit 0)" test "$UPDATE_RC" = 0
  check "... through the plan, the rehearsal, the hold, the freeze, the release and the windows ($(tr '\n' ' ' <"$OUT/steps.txt"))" steps_in_order
  check "the install path leads to the next version" on_the_next_version
  check "... the ledger in format 2, migrated" test "$(jq -r '"\(.version) \(.update_patch)"' "$STATE/ledger.json")" = "2 migrated"
  valid
  kg_valid
  echo "  note  writes were paused for $FREEZE_MS ms (freeze_ms), on a year of data"
  check "the freeze, on a year of data, is under 10 000 ms: $FREEZE_MS ms" test "${FREEZE_MS:-99999}" -lt 10000
  check "each window brought along by the table: the orchestrator re-read, acme-api restarted, acme-web took stock first, invoice-exporter was waited for" brought_along
  check "... the orchestrator kept running, asked to re-read policy.md" kept_running_and_reread
  check "... acme-web's $BACKGROUND_SLEEP listed before its restart, and started again from its stock note" sleep_again
  check "... the check-in loop restarted on the next version" loop_runs_from "$B_ROOT"
  check "... my own shell left as it is" mine_untouched
  check "the summary: the versions, the migration, policy.md, the backup" afternoon_summary
  check "the temporary link a switch cut short before it is swept" test ! -L "$LEFT_LINK"
}

# What restoring the backup costs on a year of data, for a recovering approval hook (8 s): restore's own
# check of the backup's files against its manifest (_verify), flushing each file of the backup to disk
# (what taking it skips), and a whole restore into a copy of the stores. As notes, and one check.
restore_costs() {
  python3 - "$B_ROOT/lib" "$(jq -r .backup "$STATE/update.json")" "$STATE" "$CONFIG" "$OUT/restore-copy" <<'PY'
import os, shutil, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import journal, update_backups
backup, state, config, copy = (Path(p) for p in sys.argv[2:6])
def took(start): return round((time.monotonic() - start) * 1000)
manifest = update_backups.read_manifest(backup)
files = [p for p in backup.rglob("*") if p.is_file() and not p.is_symlink()]
started = time.monotonic()
update_backups._verify(manifest, backup, update_backups.FROZEN)
verify = took(started)
started = time.monotonic()
for path in files:
    fd = os.open(path, os.O_RDONLY)
    try:
        journal.flush_to_disk(fd)
    finally:
        os.close(fd)
flush = took(started)
copy.mkdir(parents=True)
for side, folder in (("state", state), ("config", config)):
    subprocess.run(["cp", "-c", "-R", str(folder), str(copy / side)], check=True)
started = time.monotonic()
restored = update_backups.restore(backup, copy / "state", copy / "config", update_backups.FROZEN)
whole = took(started)
size = sum(p.stat().st_size for p in files) // 1024
put = sum(key in manifest.files for key in restored)  # the rest, what the manifest lacks, deleted
print(f"the backup: {len(files)} files, {size} KB; verify {verify} ms, flush {flush} ms, restore {whole} ms ({put} put back, {len(restored) - put} deleted)")
shutil.rmtree(copy)
sys.exit(0 if whole < 8000 else 1)
PY
}

the_backup_and_the_hooks() {
  echo "  the backup, and what runs from before the update"
  local backup costs restored
  backup="$(jq -r .backup "$STATE/update.json")"
  check "the backup, put in a scratch state and config folder, passes the version before's ledger validate --journal" \
    backup_valid_before "$backup"
  check "... and its kg validate" scratch_bosun "$OUT/scratch" kg validate
  costs="$(restore_costs 2>&1)"
  restored=$?
  echo "  note  $(tail -1 <<<"$costs")"
  check "restoring it, as a recovering approval hook does, takes less than the hook's 8 s" test "$restored" = 0
  check "each hook of the version before's launch settings, given its own event, does its job on the next version" \
    hooks_run "$OUT/base-hooks.txt"
  echo "  note  $(cat "$OUT/base-hooks.txt.ran" 2>/dev/null || echo 0) of the $(grep -c . "$OUT/base-hooks.txt") hooks did"
  check "... and the check fails a hook that does not: a signal hook that records nothing, a guard mode the next version does not know" \
    hooks_fail_their_controls
  check "... each through the install link, never a version's own folder" bash -c '! grep -q "bosun-versions/" "$1"' _ "$OUT/base-hooks.txt"
  check "the Slack watcher's next run catches up on the two messages that came during the hold (at $(cat "$OUT/slack-posted-at" 2>/dev/null))" \
    says " 2 new" bosun slack-watch --once
  check "... queued as two slack events, each once" slack_events_once
  grep -h "update in progress" "$STATE"/logs/checkin-*.log 2>/dev/null | tail -2 | sed 's/^/  note  the check-in loop: /'
}
slack_events_once() { # each Slack message, one slack event
  diff <(jq -r .permalink "$SLACK_STAND_IN/messages.jsonl" | sort) <(jq -r 'select(.type == "slack") | .ref' "$STATE/events/queue.jsonl" | sort)
}
scratch_bosun() { # <scratch folder> <bosun arguments...>: the version before's bosun, on the scratch folders
  local scratch="$1"
  shift
  BOSUN_STATE_DIR="$scratch/state" BOSUN_LOG_DIR="$scratch/state/logs" BOSUN_CONFIG_DIR="$scratch/config" "$A_ROOT/bin/bosun" "$@"
}
backup_valid_before() { # <backup>
  rm -rf "$OUT/scratch"
  mkdir -p "$OUT/scratch/state" "$OUT/scratch/config"
  cp -Rp "$1/state/." "$OUT/scratch/state/" && cp -Rp "$1/config/." "$OUT/scratch/config/" &&
    [ "$(jq .version "$OUT/scratch/state/ledger.json")" = 1 ] && [ "$(scratch_bosun "$OUT/scratch" ledger validate --journal)" = "ledger is valid" ]
}
# Each hook of the launch settings of the version updated from (A), approvals on: its event, matcher and
# command, apart by the unit separator (a tab is whitespace to read, which merges an empty matcher away).
base_hooks() {
  BOSUN_APPROVALS_ENABLED=1 BOSUN_EVENTS_ENABLED=1 bash -c 'source "$1/lib/common.sh"
    load_config
    orchestrator_extra_settings
    repo_session_settings acme-api
    cat "$1/orchestrator/settings.json"' _ "$A_ROOT" |
    jq -r '.hooks // {} | to_entries[] | .key as $event | .value[] | (.matcher // "") as $m | .hooks[]
      | "\($event)\u001f\($m)\u001f\(.command)"' | sort -u
}
hook_payload() { # <event> <matcher> [session id]: what Claude Code passes a hook of <event>
  jq -cn --arg event "$1" --arg matcher "$2" --arg session "${3:-00000000-0000-4000-8000-0000000000ad}" \
    --arg cwd "$HOME/Development/acme-api" '
    {session_id: $session, transcript_path: "/dev/null", cwd: $cwd, hook_event_name: $event}
    + ({PreToolUse: {tool_name: "Bash", tool_input: {command: "ls"}, tool_use_id: "toolu_0"},
        PermissionRequest: {tool_name: "Bash", tool_input: {command: "ls"}},
        PostToolUse: {tool_name: "Bash", tool_input: {command: "ls"}, tool_use_id: "toolu_0", tool_response: {stdout: ""}},
        PostToolUseFailure: {tool_name: "Bash", tool_input: {command: "ls"}, tool_use_id: "toolu_0", error: "exit 1"},
        UserPromptSubmit: {prompt: "how are the exports?"},
        Stop: {background_tasks: []},
        StopFailure: {error: "server_error", last_assistant_message: "API Error: 500"},
        SessionStart: {source: (if $matcher == "compact" then "compact" else "startup" end)},
        SessionEnd: {reason: "other"},
        Notification: {notification_type: "idle_prompt", message: "Claude is waiting for your input"}}[$event] // {})'
}
# Each hook of <file> (base_hooks' lines) run on the next version as Claude Code runs it, with its own
# event's payload under a session id of its own, and BOSUN_HOOK_SYNC=1, so what a hook records in the
# background is recorded before it returns. How many did their job, in <file>.ran.
hooks_run() {
  local file="$1" event matcher command n=0
  [ -s "$file" ] || return 1
  while IFS=$'\x1f' read -r event matcher command; do
    n=$((n + 1))
    hook_did "$n" "$event" "$matcher" "$command" || return 1
  done <"$file"
  echo "$n" >"$file.ran"
  [ "$n" -eq "$(grep -c . "$file")" ]
}
# <n> <event> <matcher> <command>: the hook did its job. Every hook bosun installs exits 0 by design, so
# that is not enough: it says nothing on stderr (the guard's "unknown mode", "no decision"), adds no error
# line to the hook log (where the signal and prompt hooks put theirs), and a signal hook adds its event's
# line, under its session id, to signals/<window>.jsonl.
hook_did() {
  local session logged errors window
  [ -n "$4" ] || { echo "$2 ($3): no command" && return 1; }
  session="$(printf '00000000-0000-4000-8000-%012d' "$1")"
  logged="$(hook_log | wc -l)"
  BOSUN_APPROVALS_ENABLED=1 BOSUN_EVENTS_ENABLED=1 BOSUN_HOOK_SYNC=1 bash -c "$4" \
    <<<"$(hook_payload "$2" "$3" "$session")" >/dev/null 2>"$OUT/hook.err" ||
    { echo "$2: $4: exit $?: $(tail -2 "$OUT/hook.err")" && return 1; }
  [ ! -s "$OUT/hook.err" ] || { echo "$2: $4: said on stderr: $(tail -2 "$OUT/hook.err")" && return 1; }
  errors="$(hook_log | tail -n +$((logged + 1)) | grep -E 'ERROR|Traceback|usage:|error:|unknown mode|no decision')"
  [ -z "$errors" ] || { echo "$2: $4: in the hook log: $errors" && return 1; }
  window="$(sed -n 's/.* hook signal --window \([^ ]*\).*/\1/p' <<<"$4")"
  [ -z "$window" ] || grep -q "\"event\":\"$2\".*\"session\":\"$session\"" "$STATE/signals/${window//\//_}.jsonl" ||
    { echo "$2: $4: no $2 line for its session in signals/$window.jsonl" && return 1; }
}
hook_log() { cat "$STATE"/logs/hook-*.log 2>/dev/null; }
# The check above fails a hook that does not do its job: a signal hook whose recording is off (it exits 0
# and says nothing), and a guard mode the next version does not know (exit 0, "unknown mode").
hooks_fail_their_controls() {
  local sep=$'\x1f' line
  line="$(grep -m1 "^Stop$sep$sep.* hook signal --window " "$OUT/base-hooks.txt")" || return 1
  printf 'Stop%s%sBOSUN_HOOK_SIGNALS=0 %s\n' "$sep" "$sep" "${line#Stop"$sep$sep"}" >"$OUT/control-signal.txt"
  printf 'PreToolUse%sBash%spython3 %q --renamed-mode\n' "$sep" "$sep" "$HOME/.local/share/bosun/lib/guard.py" \
    >"$OUT/control-guard.txt"
  ! hooks_run "$OUT/control-signal.txt" && ! hooks_run "$OUT/control-guard.txt"
}

a_mid_afternoon_update() {
  echo "a new day, and a mid-afternoon update to the next version, on a year of data"
  next_day 5
  a_year_of_data
  the_afternoon
  check "the next version: this checkout and update_patch.sh (the ledger's format 2 and its migration, a line of policy.md, the repo windows' hooks), at the head of the branch" \
    a_head next
  B_COMMIT="$(git -C "$OUT/next" rev-parse HEAD)"
  base_hooks >"$OUT/base-hooks.txt"
  agents_now >"$OUT/afternoon-agents.txt"
  afternoon_before
  the_update_runs
  the_update_itself
  nothing_lost
  the_backup_and_the_hooks
}

# ---- going back, by hand, and forward again ----------------------------------------------------------

all_stopped() { ! tmux -L bosun has-session -t "=bosun" 2>/dev/null && ! launchctl print "$DOMAIN/${JOBS[0]}" >/dev/null 2>&1 &&
  ! launchctl print "$DOMAIN/${JOBS[1]}" >/dev/null 2>&1; }
put_the_backup_back() { # <backup>: each entry of its state/ and config/ over the one in use, replacing it whole
  local side entry target
  for side in state config; do
    target="$STATE"
    [ "$side" = config ] && target="$CONFIG"
    for entry in "$1/$side"/* "$1/$side"/.[!.]*; do
      [ -e "$entry" ] || [ -L "$entry" ] || continue
      rm -rf "${target:?}/${entry##*/}" && cp -Rp "$entry" "$target/" || return 1
    done
  done
}
added_again() { # <commit>: its folder added again, whole, as the guide says when it is gone, then removed
  local folder="$OUT/added-again"
  git --git-dir "$VERSIONS/bosun.git" worktree add --detach "$folder" "$1" >/dev/null 2>&1 &&
    [ "$(git -C "$folder" rev-parse HEAD)" = "$1" ] && git --git-dir "$VERSIONS/bosun.git" worktree remove "$folder"
}

going_back() {
  echo "going back to the version before, by hand, as docs/setup.md#going-back says"
  local backup from
  backup="$(jq -r .backup "$STATE/update.json")"
  from="$(jq -r .from "$backup/manifest.json")"
  bosun stop >/dev/null 2>&1
  launchctl bootout "$DOMAIN/${JOBS[0]}" 2>/dev/null
  launchctl bootout "$DOMAIN/${JOBS[1]}" 2>/dev/null
  check "1. bosun stop, and the watchdog's and learning's jobs unloaded" all_stopped
  check "2. the version it came from is in bosun-versions, as the backup's manifest names it" a_version "$VERSIONS/${from:0:12}"
  check "... the summary's 7 characters turn into its whole commit" test "$(git --git-dir "$VERSIONS/bosun.git" rev-parse "${from:0:7}")" = "$from"
  check "... and were it gone, the guide's worktree add brings it back whole" added_again "$from"
  check "3. the backup put back: each entry of its state/ and config/ over the one in use" put_the_backup_back "$backup"
  check "4. the link led to the older version" ln -sfn "$VERSIONS/${from:0:12}" "$DIR"
  check "... its install.sh loads its jobs again" bash -c '"$1/install.sh" >"$2" 2>&1 || { tail -15 "$2"; exit 1; }' _ "$DIR" "$OUT/back-install.txt"
  check "... and bosun start" bosun start
  check "the version before runs again" test "$(git -C "$DIR" rev-parse HEAD)" = "$A_COMMIT"
  check "... its jobs loaded" jobs_loaded
  check "... the ledger as the backup has it, in format 1, valid" bash -c '[ "$(jq -r ".version" "$1/ledger.json")" = 1 ] && bosun ledger validate --journal' _ "$STATE"
  kg_valid
  check "... its windows up" wait_for 120 windows_up
  check "bosun update would take it forward again" says "ledger: format 1 -> 2" bosun update --dry-run
}

forward_again() {
  echo "forward again: bosun update, as the guide says"
  check "bosun update --yes takes it to the next version again" bash -c 'bosun update --yes >"$1" 2>&1 || { tail -20 "$1"; exit 1; }' _ "$OUT/forward.txt"
  check "... the ledger migrated to format 2 again" test "$(jq -r '"\(.version) \(.update_patch)"' "$STATE/ledger.json")" = "2 migrated"
  check "... on the next version" test "$(git -C "$DIR" rev-parse HEAD)" = "$B_COMMIT"
  valid
  kg_valid
  bosun stop >/dev/null 2>&1
  afternoon_settings_off
}

forgotten_after_the_update() {
  echo "Sam asks to be forgotten after the updates: no backup or rehearsal copy may hold him"
  check "the update backups hold Sam now" bash -c 'grep -rqF "$1" "$2"' _ "$K_SAM" "$STATE/backups"
  kg_says "Sam asked to be forgotten" "deleted the person" forget person/sam
  check "... which deletes every update backup, saying so ($(grep -o 'deleted [0-9]* update backups*' <<<"$KG_SAID"))" \
    said 0 "$KG_SAID" "taken before this; going back past it is no longer possible"
  check "... no backup or rehearsal copy holds his id, name or fact" no_copy_holds person/sam "Sam Placeholder" "$K_SAM"
  check "... nor the knowledge folder or its history" nowhere person/sam "Sam Placeholder"
  kg_valid
}

# ---- a token in a repository's URL -----------------------------------------------------------------

TOKEN_URL="https://0000tok@example.invalid/acme/bosun.git" # fictional and unreachable; gitleaks:allow
git_keeps_tokens_out() {
  echo "a repository URL with a token in it, which cannot be reached: what git and get.sh say"
  local scratch="$OUT/token" what
  mkdir -p "$scratch"
  GIT_TERMINAL_PROMPT=0 git clone --quiet --bare "$TOKEN_URL" "$scratch/clone.git" >"$scratch/clone.txt" 2>&1
  git init -q --bare "$scratch/fetch.git"
  # A made-up token at a host that cannot be reached: pragma: allowlist secret, gitleaks:allow
  git --git-dir="$scratch/fetch.git" remote add origin "https://user:0000tok@example.invalid/acme/bosun.git" # pragma: allowlist secret gitleaks:allow
  GIT_TERMINAL_PROMPT=0 git --git-dir="$scratch/fetch.git" fetch --quiet origin "+refs/heads/main:refs/remotes/origin/main" \
    >"$scratch/fetch.txt" 2>&1
  BOSUN_DIR="$scratch/share/bosun" BOSUN_REPO="$TOKEN_URL" BOSUN_BRANCH=main GIT_TERMINAL_PROMPT=0 \
    sh "$ROOT/get.sh" --unattended >"$scratch/get.txt" 2>&1
  echo "  note  $(git --version)"
  for what in clone fetch get; do
    sed "s/0000tok/[THE TOKEN]/g; s/^/  note  $what: /" "$scratch/$what.txt" | tail -3
    check "... what $what printed holds no token" bash -c '[ -s "$1" ] && ! grep -q 0000tok "$1"' _ "$scratch/$what.txt"
  done
}

twenty_writers() {
  echo "twenty writers at once"
  local n
  for n in $(seq 1 20); do
    bosun ledger spawn --title "Parallel export check $n" --ask "Run export check $n" \
      --via "user: run the export checks" >"$OUT/writer-$n.txt" 2>&1 &
  done
  wait
  for n in $(seq 1 20); do head -1 "$OUT/writer-$n.txt"; done >"$OUT/writers.ids"
  check "twenty ids, each different" test "$(grep '^task:' "$OUT/writers.ids" | sort -u | wc -l | tr -d ' ')" = 20
  check "... each in the ledger" in_the_ledger "$OUT/writers.ids"
  check "the journal's seq runs 1..n with no gap" journal_lines seq
  valid
}

csv_facts() { bosun kg facts feature/csv-export --status all --closed --json; }
each_once() { # <value prefix> <count>: that many facts of the CSV export start with it, each once
  csv_facts | jq -e --arg p "$1" --argjson n "$2" \
    '[.[] | (.value // "") | select(startswith($p))] | length == $n and (unique | length) == $n'
}
all_facts_there() { # <file of fact ids>: each one is a fact of the CSV export (and there is at least one)
  local missing
  [ -s "$1" ] || {
    echo "no ids to look for"
    return 1
  }
  missing="$(comm -23 <(sort -u "$1") <(csv_facts | jq -r '.[].id' | sort -u))"
  [ -z "$missing" ] && return 0
  echo "not in the knowledge base: $missing"
  return 1
}
kg_writer() { # <value> <output file>: one proposal, in the background
  bosun kg propose feature/csv-export caveat --value "$1" --source "ledger:$T_JSON" >"$2" 2>&1 &
}
printed_facts() { cat "$@" | grep -oE 'f-[0-9a-hjkmnp-tv-z]{6}'; } # <output file...>

knowledge_writers() {
  echo "ten knowledge writers at once"
  local n
  for n in $(seq 1 10); do kg_writer "Writer $n: the export check passed" "$OUT/kg-writer-$n.txt"; done
  wait
  printed_facts "$OUT"/kg-writer-*.txt >"$OUT/kg-writers.ids"
  check "ten facts, each once" each_once "Writer " 10
  check "... each id it printed" all_facts_there "$OUT/kg-writers.ids"
  check "each journal file's seq runs 1..n with no gap" journal_lines seq "$KJOURNAL"
  kg_valid
}

both_stores_at_once() {
  echo "ten knowledge and ten ledger writers at once"
  local n
  for n in $(seq 1 10); do
    kg_writer "Both stores $n: the export check passed" "$OUT/kg-both-$n.txt"
    bosun ledger spawn --title "Both stores $n: an export check" --ask "Run export check $n" \
      --via "user: run the export checks" >"$OUT/both-$n.txt" 2>&1 &
  done
  wait
  for n in $(seq 1 10); do head -1 "$OUT/both-$n.txt"; done >"$OUT/both.ids"
  check "ten knowledge facts, each once" each_once "Both stores " 10
  check "ten ledger tasks, each different" test "$(grep '^task:' "$OUT/both.ids" | sort -u | wc -l | tr -d ' ')" = 10
  check "... each in the ledger" in_the_ledger "$OUT/both.ids"
  kg_valid
  valid
  check "one replica id across both journals" one_replica
}

clock_goes_back() {
  echo "the clock goes back an hour"
  local n
  set_clock $((TODAY * DAY - 3600))
  check "the clock reads an hour earlier: $(date -u '+%a %F %R') UTC" clock_reads $((TODAY * DAY - 3600))
  for n in 1 2 3 4 5; do
    step "a write an hour back ($n)" note "$T_JSON" "export check $n after the clock went back"
  done
  check "every clock in the journal is above the one before it" journal_lines hlc
  tail -6 "$JOURNAL/$(jq -r .replica "$STATE/replica.json").jsonl" | jq -r '"  note  seq \(.seq) hlc \(.hlc) \(.cmd)"'
  valid
  for n in 1 2 3 4 5; do
    kg_says "a knowledge write an hour back ($n)" "added" propose feature/csv-export caveat \
      --value "Clock back $n: the export check passed" --source "ledger:$T_JSON"
  done
  check "every clock in the knowledge journal is above the one before it" journal_lines hlc "$KJOURNAL"
  tail -5 "$(replica_journal)" | jq -r '"  note  seq \(.seq) hlc \(.hlc) \(.cmd)"'
  kg_valid
  set_clock $((TODAY * DAY))
  check "the clock is put right: $(date -u '+%a %F %R') UTC" clock_reads $((TODAY * DAY))
}

CUT_STAGE=""  # where the last cut fell
cut_after() { # <milliseconds> <output file> <command...>: run it in a process group of its own and
  # kill the whole group after that long, like a power cut
  local ms="$1" out="$2" pid stage
  shift 2
  set -m
  "$@" >"$out" 2>&1 &
  pid=$!
  set +m
  sleep "$(printf '%d.%03d' $((ms / 1000)) $((ms % 1000)))"
  stage="$(ps -o stat= -o command= -p "$pid" 2>/dev/null)"
  pgrep -x -P "$pid" git >/dev/null && stage="R git" # the knowledge base commits to its history
  kill -9 -- "-$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  CUT_STAGE="$(stage_of "$stage")"
}

# <state and command of the cut process>: where the cut fell
stage_of() {
  case "${1%% *}" in # its state: gone, a zombie or exiting
    "" | *Z* | *E*)
      echo "finished"
      return
      ;;
  esac
  case "$1" in # the same process is this script's fork, then bin/bosun, libexec, python (and its git)
    *\ git) echo "git" ;;
    *lib/ledger.py* | *lib/knowledge.py*) echo "python" ;;
    *libexec/bosun-ledger* | *libexec/bosun-kg*) echo "libexec" ;;
    *bin/bosun\ ledger* | *bin/bosun\ kg*) echo "bin/bosun" ;;
    *"${0##*/}"*) echo "starting" ;;
    *) echo "other(${1:0:40})" ;;
  esac
}
# [<journal folder>]: how many torn files it holds
torn_files() { find "${1:-$JOURNAL}" -name '*.torn-*' | wc -l | tr -d ' '; }
the_write_after() { # <what>: one ordinary write; what it repaired goes to the output
  check "the next write works, and repairs what it must" bash -c 'bosun ledger note "$1" "$2" 2>"$3"' _ \
    "$T_JSON" "after $1" "$OUT/repair.txt"
  sed 's/^/  note  /' "$OUT/repair.txt"
}

# Fifty cuts at random points of 0 to CUT_MS: each cut's line in $OUT/<name>.txt (number, delay, where
# it fell, the id it printed) and the ids printed in $OUT/<name>.ids.
cut_randomly() { # <name> <id pattern> <command...>: @N@ in the command is the cut's number
  local name="$1" pattern="$2" delays n
  shift 2
  read -r -a delays <<<"$(python3 -c 'import random, sys; print(*(random.randint(0, int(sys.argv[1])) for _ in range(50)))' "$CUT_MS")"
  for n in $(seq 1 50); do
    cut_after "${delays[$((n - 1))]}" "$OUT/$name-$n.txt" "${@//@N@/$n}"
    echo "$n ${delays[$((n - 1))]}ms $CUT_STAGE $(grep -m 1 -oE "$pattern" "$OUT/$name-$n.txt")" >>"$OUT/$name.txt"
  done
  sed 's/^/  cut   /' "$OUT/$name.txt"
  awk '{ print $3 }' "$OUT/$name.txt" | sort | uniq -c | sed 's/^ */  note  where the cuts fell: /'
  awk 'NF > 3 { print $4 }' "$OUT/$name.txt" >"$OUT/$name.ids"
  echo "  note  $(wc -l <"$OUT/$name.ids" | tr -d ' ') of 50 printed an id before the cut"
  check "some cuts fell while the Python writer ran" grep -qE " (python|git) " "$OUT/$name.txt"
  check "some cut writes printed an id first" test -s "$OUT/$name.ids"
}

# How far into a write the random cuts reach: a write's time, measured, and the rebuild from the whole
# journal that a write starts with once a cut has left the file behind it (on a year of data most of its
# time), so that some cuts fall after a write printed its id.
CUT_MS=300
cut_range() { # ledger|kg <a write of that store, as a command...>
  local store="$1" write check
  shift
  write="$(run_ms "$@")"
  if [ "$store" = ledger ]; then check="$(run_ms bosun ledger validate --journal)"; else check="$(run_ms bosun kg validate)"; fi
  CUT_MS=$((write + check))
  echo "  note  a write takes $write ms, and a rebuild from the journal $check ms: cuts from 0 to $CUT_MS ms"
}

power_cuts() {
  echo "power cuts: fifty writes killed at random, from their start to past a write and a rebuild"
  cut_range ledger bosun ledger spawn --title "Timing an export check" --ask "Time it" --via "user: time the writes"
  cut_randomly cuts '^task:[^ ]+' bosun ledger spawn --title "Power cut @N@: an export check" \
    --ask "Check export @N@" --via "user: run the export checks"
  bosun ledger validate --journal 2>&1 | sed 's/^/  note  before the next write: /'
  the_write_after "the power cuts"
  echo "  note  set aside: $(torn_files) unfinished transaction(s)"
  echo "  note  $(jq '[.items[] | select(.title | startswith("Power cut"))] | length' "$STATE/ledger.json") of 50 are in the ledger"
  valid
  check "every id a cut write printed is in the ledger" in_the_ledger "$OUT/cuts.ids"
  check "the journal's seq still runs 1..n with no gap" journal_lines seq
}

# What a cut left behind: torn (an unfinished transaction at the end of a journal file), ahead (the
# journal holds a write ledger.json does not have yet), or clean. It asks the installed bosun's journal.
journal_state() {
  python3 - "$DIR/lib" "$JOURNAL" "$STATE/ledger.json" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from journal import Journal
journal, root = Journal(Path(sys.argv[2]), "ledger", "", 0), Path(sys.argv[2])
cut_line = any(path.read_bytes()[-1:] not in (b"", b"\n") for path in root.glob("*.jsonl"))
journal_key = json.loads(Path(sys.argv[3]).read_text()).get("journal") or {}
saved = {stem: seq for stem, seq in journal_key.items() if isinstance(seq, int)}  # the position, not the digest
if cut_line or journal.written() != journal.position():
    print("torn")
else:
    print("ahead" if journal.position() != saved else "clean")
PY
}
run_ms() { # <command...>: how long it takes, in milliseconds
  python3 -c 'import subprocess, sys, time; t = time.monotonic(); subprocess.run(sys.argv[1:], capture_output=True); print(int((time.monotonic() - t) * 1000))' "$@"
}
journaled_quotes() { cat "$JOURNAL"/*.jsonl | grep -oE 'yes, open the retry PR \(cut [0-9]+\)' | sort -u; }
approved_quotes() { bosun ledger show "$T_RETRY" | jq -r '.history[] | select(.event == "approved") | .note'; }
all_approved() { # <file of quotes>: each one is in the item's history (and there is at least one)
  local missing
  [ -s "$1" ] || {
    echo "no approval was printed before its cut"
    return 1
  }
  approved_quotes >"$OUT/approved.txt" || return 1
  missing="$(grep -vxF -f "$OUT/approved.txt" "$1")"
  case $? in
    1) return 0 ;; # grep found no quote missing
    0) echo "printed, but not in the ledger: $missing" ;;
  esac
  return 1
}

# A write that finds ledger.json behind its journal (a cut left it so) first rebuilds it from the whole
# journal, as `ledger validate --journal` does: on a year of data that is most of a write's time.
swept_cuts() {
  echo "power cuts, swept: an approval (it writes both journal files) killed at each point of its run"
  local n ms took catch_up count=100 torn_before
  step "a proposal to approve" propose "$T_RETRY" --run "gh pr create --repo acme/web --title 'Retry an export that timed out'"
  took="$(for n in 1 2 3; do run_ms bosun ledger approve "$T_RETRY" --quote "yes, open the retry PR (timing $n)"; done |
    sort -n | sed -n 2p)" # the median of three
  catch_up="$(run_ms bosun ledger validate --journal)"
  echo "  note  an approval takes $took ms, and a rebuild from the journal $catch_up ms; $count cuts from 0 to $((took + catch_up)) ms"
  torn_before="$(torn_files)"
  for n in $(seq 0 $((count - 1))); do
    ms=$((n * (took + catch_up) / (count - 1)))
    cut_after "$ms" "$OUT/approve-$n.txt" bosun ledger approve "$T_RETRY" --quote "yes, open the retry PR (cut $n)"
    grep -q "approved (" "$OUT/approve-$n.txt" && echo "yes, open the retry PR (cut $n)" >>"$OUT/approve.quotes"
    echo "$n ${ms}ms $CUT_STAGE $(journal_state)" >>"$OUT/approve-cuts.txt"
  done
  sed 's/^/  cut   /' "$OUT/approve-cuts.txt"
  awk '{ print $3 }' "$OUT/approve-cuts.txt" | sort | uniq -c | sed 's/^ */  note  where the cuts fell: /'
  awk '{ print $4 }' "$OUT/approve-cuts.txt" | sort | uniq -c | sed 's/^ */  note  what a cut left: /'
  the_write_after "the swept cuts"
  echo "  note  repairs: $(($(torn_files) - torn_before)) unfinished transaction(s) set aside"
  echo "  note  $(wc -l <"$OUT/approve.quotes" | tr -d ' ') of $count printed their approval; $(approved_quotes | grep -c '(cut ') are in the ledger"
  valid
  check "every approval a cut command printed is in the ledger" all_approved "$OUT/approve.quotes"
  journaled_quotes >"$OUT/journaled.quotes"
  echo "  note  $(wc -l <"$OUT/journaled.quotes" | tr -d ' ') of them reached the journal"
  check "... and every one the journal holds" all_approved "$OUT/journaled.quotes"
  check "the journal's seq still runs 1..n with no gap" journal_lines seq
}

GIT_LOCK="" # the knowledge history's index lock

# The git lock a cut during a write's commit leaves, as it looks a while after the crash (a minute old);
# one is made as such a cut leaves it if none of this run's cuts fell there.
a_stale_git_lock() {
  if [ -e "$GIT_LOCK" ]; then
    echo "  note  a cut left the knowledge history's git lock: .git/index.lock"
  else
    : >"$GIT_LOCK"
    echo "  note  no cut left the knowledge history's git lock this time: one is made, as such a cut leaves it"
  fi
  touch -t "$(date -v-1M +%Y%m%d%H%M.%S)" "$GIT_LOCK"
}
last_commit_is() { # <subject>: the knowledge history's last commit
  local subject
  subject="$(git -C "$KNOWLEDGE" log -1 --format=%s)"
  [ "$subject" = "$1" ] && return 0
  echo "the last commit: $subject"
  return 1
}

knowledge_power_cuts() {
  echo "power cuts in the knowledge base: fifty proposals killed at random, from their start to past a write and a rebuild"
  cut_range kg bosun kg propose feature/csv-export caveat --value "Timing: the export check passed" \
    --source "ledger:$T_JSON"
  GIT_LOCK="$KNOWLEDGE/.git/index.lock"
  cut_randomly kg-cuts 'f-[0-9a-hjkmnp-tv-z]{6}' bosun kg propose feature/csv-export caveat \
    --value "Power cut @N@: an export check" --source "ledger:$T_JSON"
  bosun kg validate 2>&1 | sed 's/^/  note  before the next write: /'
  a_stale_git_lock
  kg_says "the next write works, and repairs what it must" "added" propose feature/csv-export caveat \
    --value "After the power cuts: the export check passed" --source "ledger:$T_JSON"
  as_notes "$KG_SAID"
  check "... it removes the stale git lock, and says so" said 0 "$KG_SAID" "the knowledge history records again"
  check "... the knowledge history records that write" last_commit_is "kg propose feature/csv-export caveat"
  check "... and holds the files as they are" history_records
  echo "  note  set aside: $(torn_files "$KJOURNAL") unfinished transaction(s)"
  echo "  note  $(csv_facts | jq '[.[] | (.value // "") | select(startswith("Power cut "))] | length') of 50 are in the knowledge base"
  kg_valid
  check "every id a cut write printed is in the knowledge base" all_facts_there "$OUT/kg-cuts.ids"
  check "each journal file's seq still runs 1..n with no gap" journal_lines seq "$KJOURNAL"
}
history_records() { # the knowledge history holds the files as they are now
  local status
  status="$(git -C "$KNOWLEDGE" status --porcelain 2>&1)" && [ -z "$status" ] && return 0
  echo "${status:-git status failed}"
  ls -l "$KNOWLEDGE/.git/index.lock" 2>&1
  return 1
}

journal_fact_ids() { jq -r 'select(.op == "fact.add") | .target' "$KJOURNAL"/*.jsonl | sort -u; }
none_lost() { # every fact the journal holds is in the fact log
  local lost
  lost="$(comm -23 <(journal_fact_ids) <(jq -r 'select(.op == "add") | .fact.id' "$KNOWLEDGE/facts.jsonl" | sort -u))"
  [ -z "$lost" ] && return 0
  echo "in the journal, not in the files: $lost"
  return 1
}
after_it() { # <what>: an ordinary write after <what>, with what it said on stderr as notes
  kg_says "the next write works" "added" propose feature/csv-export caveat --value "After $1: the export check passed" \
    --source "ledger:$T_JSON"
  as_notes "$KG_SAID"
}
validate_notes() { bosun kg validate 2>&1 | sed 's/^/  note  before the next write: /'; }
as_notes() { # <text>: each line as a note
  local line
  while IFS= read -r line; do echo "  note  $line"; done <<<"$1"
}

edited_by_hand() {
  echo "the knowledge files changed outside their journal"
  echo "  a product fact's text, edited by hand"
  sed -i '' 's/tests compare amounts to the cent/tests compare amounts to a tenth of a cent/' "$KNOWLEDGE/facts.jsonl"
  validate_notes
  kg_says "the next write adopts it, and says so" "adopted" propose feature/csv-export caveat \
    --value "After the hand edit: the export check passed" --source "ledger:$T_JSON"
  as_notes "$KG_SAID"
  kg_valid
  check "... the fact reads as edited" bash -c \
    'bosun kg facts repo/invoice-exporter --predicate caveat --status all | grep -q "a tenth of a cent"'
}

files_two_commits_back() {
  echo "  the data files as git had them two commits ago"
  check "git checks them out" git -C "$KNOWLEDGE" checkout HEAD~2 -- entities.jsonl facts.jsonl
  validate_notes
  kg_says "the next write takes them as the rules say, and says so" "as changed outside their journal" \
    propose feature/csv-export caveat --value "After the files went back: the export check passed" --source "ledger:$T_JSON"
  as_notes "$KG_SAID"
  kg_valid
  check "no fact the journal holds is lost" none_lost
}

folder_two_commits_back() {
  echo "  the whole knowledge folder, its journal too, as git had it two commits ago"
  local minted
  csv_facts | jq -r '.[].id' >"$OUT/ids-before-checkout"
  check "git checks it out" git -C "$KNOWLEDGE" checkout HEAD~2 -- .
  validate_notes
  kg_made minted "$NEW_FACT_ID" "the next write" propose feature/csv-export caveat \
    --value "After the folder went back: the export check passed" --source "ledger:$T_JSON"
  as_notes "$KG_SAID"
  grep -qx -- "$minted" "$OUT/ids-before-checkout" &&
    echo "  note  $minted was minted again: it named a fact the checkout took away (docs/design/sync.md, open questions)"
  kg_valid
  check "no fact the journal holds is lost" none_lost
}

an_older_bosun_forgets() {
  echo "  the release, from a checkout of its own, forgets Lee in the same knowledge folder"
  check "the release, beside the installed bosun" git clone -q --branch sandbox-mac-tree "$HOME/repo-base.bundle" "$OUT/release"
  check "Lee asked to be forgotten: the release deletes him" says "deleted the person" "$OUT/release/bin/bosun" kg forget person/lee
  check "... kept as a plain hash, as the release keeps it" last_forgotten 'has("hash_kind") | not'
  check "the installed bosun is as it was" test "$(git -C "$DIR" rev-parse HEAD)" = "$B_COMMIT"
  validate_notes
  kg_says "the next write deletes him from the journal too, and says so" "deleted on request are gone" \
    propose feature/csv-export caveat --value "After the release's forget: the export check passed" --source "ledger:$T_JSON"
  as_notes "$KG_SAID"
  kg_valid
  check "nothing of Lee in local.jsonl" not_in "$KJOURNAL/local.jsonl" person/lee "Lee Sample" "questions as one table"
  check "... nor in the knowledge history" not_in_git person/lee "Lee Sample"
  check "... nor anywhere in the knowledge folder" nowhere person/lee "Lee Sample"
  check "he is not added again without --allow-forgotten" refuses "deleted on request" \
    bosun kg add-entity person/lee --name "Lee Sample"
}

launchd_on_the_new_bosun() {
  echo "launchd on the new bosun"
  local runs
  runs="$(morning_runs)"
  launchctl kickstart -k "$DOMAIN/$LABEL"
  check "launchd runs the morning job" wait_for 180 morning_runs_above "$runs"
  tail -5 "$STATE"/logs/morning-"$(date +%F)".log
  valid "ledger validate --journal stays clean"
  bosun stop >/dev/null 2>&1
}

console_url() { grep -m 1 -oE 'http://127\.0\.0\.1:[0-9]+/\?t=[A-Za-z0-9_-]+' "$OUT/console.txt"; }
console_get() { # <path>: the console's answer, with the cookie its URL's token becomes
  local url port
  url="$(console_url)"
  port="${url#http://127.0.0.1:}"
  port="${port%%/*}"
  curl -s -f -H "Cookie: bosun_console_$port=${url##*t=}" "http://127.0.0.1:$port$1"
}
console_counts() { # <what kg stats counts>: the console's knowledge view counts the same
  local said
  said="$(console_get /api/knowledge | jq -r 'select(.exists and .error == null) | "\(.totals.entities) entities, \(.totals.facts) facts"')"
  [ "$said" = "$1" ] && return 0
  echo "the console: ${said:-no knowledge view}; kg stats: $1"
  return 1
}
console_briefing() { console_get /api/knowledge/briefing | jq -e '.exists and .error == null'; }
RESTRICTED=(identifiers.jsonl forgotten.jsonl secret.key)

console_reads_knowledge() {
  echo "the console reads the knowledge base, with its restricted files unreadable"
  local counts modes mode file console
  counts="$(bosun kg stats | sed 's/ (.*//')"
  modes="$(cd "$KNOWLEDGE" && stat -f '%Lp %N' "${RESTRICTED[@]}")"
  (cd "$KNOWLEDGE" && chmod 000 "${RESTRICTED[@]}")
  bosun console --port 0 >"$OUT/console.txt" 2>&1 &
  console=$!
  check "it starts" wait_for 30 console_url
  check "its knowledge view answers, as kg stats counts: $counts" console_counts "$counts"
  check "... and its briefing" console_briefing
  kill "$console" 2>/dev/null
  wait "$console" 2>/dev/null
  while read -r mode file; do chmod "$mode" "$KNOWLEDGE/$file"; done <<<"$modes"
  check "the restricted files are as they were" test "$(cd "$KNOWLEDGE" && stat -f '%Lp %N' "${RESTRICTED[@]}")" = "$modes"
  kg_valid
}

knowledge_sum() { cat "$KJOURNAL"/*.jsonl "$KNOWLEDGE/secret.key" "$KNOWLEDGE/forgotten.jsonl" | cksum; }

uninstall_keeps_the_journal() {
  echo "uninstall keeps your records, journals and all"
  local settings ledger journal knowledge
  bosun start >/dev/null 2>&1
  settings="$(settings_sum)"
  ledger="$(cksum <"$STATE/ledger.json")"
  journal="$(cat "$JOURNAL"/*.jsonl | cksum)"
  knowledge="$(knowledge_sum)"
  uninstall_keeping_records "$settings" "$ledger"
  check "your journal is as you left it" test "$journal" = "$(cat "$JOURNAL"/*.jsonl | cksum)"
  check "... and this Mac's replica id is there" replica_made
  check "the knowledge journal, the key of the forgotten and their records: as you left them" \
    test "$knowledge" = "$(knowledge_sum)"
  check "... the key still readable only by me" key_made
  install_again "$settings"
  valid "ledger validate --journal passes on what was kept"
  check "... the week is there" bosun ledger show "$T_JSON"
  kg_valid "kg validate passes on what was kept"
  check "... the knowledge is there" lists_fact feature/csv-export "$K_DAY4"
}

expired_and_purged() { # what kg expire said: some proposals expired, some person facts deleted
  [[ "$KG_SAID" =~ recorded\ [1-9][0-9]*\ expired\ proposals\;\ [1-9][0-9]*\ person\ facts ]] && return 0
  echo "$KG_SAID"
  return 1
}

a_year_on() {
  echo "a year on: proposals no one reviewed expire, and person facts not seen again go"
  next_day 400
  kg_says "kg expire" "past retention deleted" expire
  echo "  note  $KG_SAID"
  check "... it recorded expired proposals and deleted person facts" expired_and_purged
  kg_valid
  check "nothing of Dana's facts in local.jsonl" not_in "$KJOURNAL/local.jsonl" "Europe/Lisbon" "Finance lead"
  check "... nor in the knowledge history" not_in_git Europe/Lisbon "Finance lead"
  check "... nor anywhere in the knowledge folder" nowhere Europe/Lisbon "Finance lead"
  valid "ledger validate --journal passes a year on"
}

start_clock
cp "$HOME/repo-base.bundle" "$HOME/repo.bundle"
a_new_machine
a_repo_by_hand acme-web https://github.com/acme/web.git
a_repo_by_hand invoice-exporter https://github.com/acme/invoice-exporter.git
follow_what_it_says
install
windows_by_answers acme-api acme-web invoice-exporter
what_the_release_does
git_keeps_tokens_out
launchd_for_real
the_old_week
the_release_at_work
a_conversion_put_back
the_upgrade
the_first_write
the_first_knowledge_write
knowledge_privacy
the_new_days
a_mid_afternoon_update
going_back
forward_again
forgotten_after_the_update
twenty_writers
knowledge_writers
both_stores_at_once
clock_goes_back
power_cuts
swept_cuts
knowledge_power_cuts
edited_by_hand
files_two_commits_back
folder_two_commits_back
an_older_bosun_forgets
launchd_on_the_new_bosun
console_reads_knowledge
uninstall_keeps_the_journal
a_year_on
put_the_clock_back
rm -rf "$OUT"
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
