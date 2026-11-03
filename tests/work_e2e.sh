#!/bin/bash
# A week of real work on a real Mac, through an upgrade: the last release installed by the piped
# installer, three days of work on it (tests/sandbox/acme_week.sh), the upgrade the way a user does it
# (get.sh again), then two more days on the bosun whose ledger writes go through its journal, with the
# clock moved, twenty writers at once, the clock going back, writes cut short like a power cut, launchd
# and uninstall. At each step the ledger and its journal must hold up (`ledger validate --journal`).
# Claude Code is a stand-in. It changes the machine it runs on (launchd, pmset, the clock), so it only
# runs in the throwaway Mac of `tests/sandbox.sh mac work`, which copies in the two bundles it installs
# from: ~/repo-base.bundle (the release) and ~/repo-tree.bundle (this checkout).
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
# The guard (it refuses to run anywhere else), the checks and the steps tests/install_e2e.sh shares.
source "$(dirname "$0")/sandbox/e2e_lib.sh" || exit 1
source "$(dirname "$0")/sandbox/acme_week.sh" || exit 1
# Not on a GitHub runner either: it needs the two bundles, and it sets the clock.
[ -e /etc/bosun-sandbox ] && [ -s "$HOME/repo-base.bundle" ] && [ -s "$HOME/repo-tree.bundle" ] || {
  echo "$0 only runs in tests/sandbox.sh mac work, which copies in ~/repo-base.bundle and ~/repo-tree.bundle" >&2
  exit 1
}

STATE="$HOME/.local/state/bosun"
JOURNAL="$STATE/journal/ledger"
DAY=86400
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
  set_clock $(($1 * DAY))
  check "the clock moves on to day $(($1 + 1)): $(date -u '+%a %F %R') UTC" clock_reads $(($1 * DAY))
  sleep 5
  check "... what launchd runs for it finishes" wait_for 300 jobs_quiet
  morning_runs_above "$runs" && echo "  note  launchd ran the morning job when the clock moved"
}

journal_lines() { # seq|hlc: every journal file's seq runs 1..n, or every clock is above the one before
  python3 - "$JOURNAL" "$1" <<'PY'
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
  check "the release writes no journal" test ! -e "$STATE/journal"
}

no_journal_yet() { { [ ! -e "$JOURNAL" ] || [ -z "$(ls -A "$JOURNAL")" ]; } && jq -e 'has("journal") | not' "$STATE/ledger.json"; }
tree_head() { git bundle list-heads "$HOME/repo-tree.bundle" | awk '{ print $1; exit }'; }

the_upgrade() {
  echo "the upgrade: the piped installer again, which pulls the new bosun"
  cp "$HOME/repo-tree.bundle" "$HOME/repo.bundle"
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/upgrade.txt" 2>&1
  local upgraded=$?
  cat "$OUT/upgrade.txt"
  check "it passes" test "$upgraded" = 0
  check "the checkout is the new bosun" test "$(git -C "$DIR" rev-parse HEAD)" = "$(tree_head)"
  check "bosun doctor passes" bosun doctor
  check "no journal yet: nothing has written since" no_journal_yet
  check "ledger validate --journal says so, and passes" bash -c 'bosun ledger validate --journal | grep -q "no journal yet"'
  check "... and starts none" no_journal_yet
}

same_as_before() { diff <(jq -S 'del(.journal) | .said //= []' "$STATE/ledger.json") "$OUT/snapshot.json"; }
replica_made() { jq -e '.replica | test("^[0-9a-f]{32}$")' "$STATE/replica.json" && [ "$(stat -f %Lp "$STATE/replica.json")" = 600 ]; }
starts_whole() { head -1 "$JOURNAL/$(jq -r .replica "$STATE/replica.json").jsonl" | jq -e '.seq == 1 and .cmd == "genesis"'; }

the_first_write() {
  echo "the first write on the new bosun"
  step "a write that changes nothing" render
  check "the journal appears" bash -c 'ls "$1"/*.jsonl' _ "$JOURNAL"
  check "... with this Mac's replica id, readable only by me" replica_made
  check "... starting with the old ledger, whole" starts_whole
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

the_new_days() {
  echo "two more days on the new bosun"
  next_day 3
  day_4
  valid
  next_day 4
  day_5
  valid
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

clock_goes_back() {
  echo "the clock goes back an hour"
  local n
  set_clock $((4 * DAY - 3600))
  check "the clock reads an hour earlier: $(date -u '+%a %F %R') UTC" clock_reads $((4 * DAY - 3600))
  for n in 1 2 3 4 5; do
    step "a write an hour back ($n)" note "$T_JSON" "export check $n after the clock went back"
  done
  check "every clock in the journal is above the one before it" journal_lines hlc
  tail -6 "$JOURNAL/$(jq -r .replica "$STATE/replica.json").jsonl" | jq -r '"  note  seq \(.seq) hlc \(.hlc) \(.cmd)"'
  valid
  set_clock $((4 * DAY))
  check "the clock is put right: $(date -u '+%a %F %R') UTC" clock_reads $((4 * DAY))
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
  kill -9 -- "-$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  case "${stage%% *}" in # its state: gone, a zombie or exiting
    "" | *Z* | *E*) stage="finished" ;;
  esac
  case "$stage" in # the same process is this script's fork, then bin/bosun, libexec, python
    finished) CUT_STAGE="finished" ;;
    *lib/ledger.py*) CUT_STAGE="python" ;;
    *libexec/bosun-ledger*) CUT_STAGE="libexec" ;;
    *bin/bosun\ ledger*) CUT_STAGE="bin/bosun" ;;
    *"${0##*/}"*) CUT_STAGE="starting" ;;
    *) CUT_STAGE="other(${stage:0:40})" ;;
  esac
}
torn_files() { find "$JOURNAL" -name '*.torn-*' | wc -l | tr -d ' '; }
the_write_after() { # <what>: one ordinary write; what it repaired goes to the output
  check "the next write works, and repairs what it must" bash -c 'bosun ledger note "$1" "$2" 2>"$3"' _ \
    "$T_JSON" "after $1" "$OUT/repair.txt"
  sed 's/^/  note  /' "$OUT/repair.txt"
}

power_cuts() {
  echo "power cuts: fifty writes killed after 0 to 300 ms"
  local delays n
  read -r -a delays <<<"$(python3 -c 'import random; print(*(random.randint(0, 300) for _ in range(50)))')"
  for n in $(seq 1 50); do
    cut_after "${delays[$((n - 1))]}" "$OUT/cut-$n.txt" bosun ledger spawn --title "Power cut $n: an export check" \
      --ask "Check export $n" --via "user: run the export checks"
    echo "$n ${delays[$((n - 1))]}ms $CUT_STAGE $(head -1 "$OUT/cut-$n.txt")" >>"$OUT/cuts.txt"
  done
  sed 's/^/  cut   /' "$OUT/cuts.txt"
  awk '{ print $3 }' "$OUT/cuts.txt" | sort | uniq -c | sed 's/^ */  note  where the cuts fell: /'
  grep -oE 'task:[^ ]+$' "$OUT/cuts.txt" >"$OUT/cut.ids"
  echo "  note  $(wc -l <"$OUT/cut.ids" | tr -d ' ') of 50 printed an id before the cut"
  check "some cuts fell while the Python writer ran" grep -q " python " "$OUT/cuts.txt"
  check "some cut writes printed an id first" test -s "$OUT/cut.ids"
  bosun ledger validate --journal 2>&1 | sed 's/^/  note  before the next write: /'
  the_write_after "the power cuts"
  echo "  note  set aside: $(torn_files) unfinished transaction(s)"
  echo "  note  $(jq '[.items[] | select(.title | startswith("Power cut"))] | length' "$STATE/ledger.json") of 50 are in the ledger"
  valid
  check "every id a cut write printed is in the ledger" in_the_ledger "$OUT/cut.ids"
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

swept_cuts() {
  echo "power cuts, swept: an approval (it writes both journal files) killed at each point of its run"
  local n ms took count=100 torn_before
  step "a proposal to approve" propose "$T_RETRY" --run "gh pr create --repo acme/web --title 'Retry an export that timed out'"
  took="$(for n in 1 2 3; do run_ms bosun ledger approve "$T_RETRY" --quote "yes, open the retry PR (timing $n)"; done |
    sort -n | sed -n 2p)" # the median of three
  echo "  note  an approval takes $took ms; $count cuts from 0 to $took ms"
  torn_before="$(torn_files)"
  for n in $(seq 0 $((count - 1))); do
    ms=$((n * took / (count - 1)))
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
  check "the journal's seq still runs 1..n with no gap" journal_lines seq
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

uninstall_keeps_the_journal() {
  echo "uninstall keeps your records, journal and all"
  local settings ledger journal
  bosun start >/dev/null 2>&1
  bosun kg import "$ROOT/tests/fixtures/knowledge/acme-import.jsonl" --apply --auto-confirm >/dev/null 2>&1
  settings="$(settings_sum)"
  ledger="$(cksum <"$STATE/ledger.json")"
  journal="$(cat "$JOURNAL"/*.jsonl | cksum)"
  uninstall_keeping_records "$settings" "$ledger"
  check "your journal is as you left it" test "$journal" = "$(cat "$JOURNAL"/*.jsonl | cksum)"
  check "... and this Mac's replica id is there" replica_made
  install_again "$settings"
  valid "ledger validate --journal passes on what was kept"
  check "... the week is there" bosun ledger show "$T_JSON"
}

start_clock
cp "$HOME/repo-base.bundle" "$HOME/repo.bundle"
a_new_machine
a_repo_by_hand acme-web https://github.com/acme/web.git
a_repo_by_hand invoice-exporter https://github.com/acme/invoice-exporter.git
follow_what_it_says
install
check "... and the other two" bash -c 'grep -q "name: acme-web" "$1" && grep -q "name: invoice-exporter" "$1"' _ "$CONFIG/sessions.yml"
launchd_for_real
the_old_week
the_upgrade
the_first_write
the_new_days
twenty_writers
clock_goes_back
power_cuts
swept_cuts
launchd_on_the_new_bosun
uninstall_keeps_the_journal
put_the_clock_back
rm -rf "$OUT"
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
