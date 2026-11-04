#!/bin/bash
# A week of real work on a real Mac, through an upgrade: the last release installed by the piped
# installer, three days of work on it (tests/sandbox/acme_week.sh: the ledger and the knowledge base),
# the upgrade the way a user does it (get.sh again), then two more days on the bosun whose writes go
# through the journals, with the clock moved, writers at once, the clock going back, writes cut short
# like a power cut, the knowledge files changed outside their journal (by hand, by git, by the release),
# launchd, the console, uninstall, and a year on. At each step the ledger, the knowledge base and their
# journals must hold up (`ledger validate --journal`, `kg validate`), and nothing of a person deleted on
# request may be left in the knowledge folder or its history.
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
KJOURNAL="$KNOWLEDGE/journal"
T_FIRST="" # the first task the new bosun makes
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

the_upgrade() {
  echo "the upgrade: the piped installer again, which pulls the new bosun"
  cp "$HOME/repo-tree.bundle" "$HOME/repo.bundle"
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/upgrade.txt" 2>&1
  local upgraded=$?
  cat "$OUT/upgrade.txt"
  check "it passes" test "$upgraded" = 0
  check "the checkout is the new bosun" test "$(git -C "$DIR" rev-parse HEAD)" = "$(tree_head)"
  check "bosun doctor passes" bash -c 'bosun doctor >"$1" 2>&1 || { cat "$1"; exit 1; }' _ "$OUT/doctor.txt"
  ledger_before_its_first_write
  check "doctor names the knowledge base from before journals as a note" \
    grep -E "^ +ok +the knowledge base and its journal: no journal yet" "$OUT/doctor.txt"
  check "kg validate says so, and passes" bash -c 'bosun kg validate | grep -q "no journal yet"'
  check "... and starts none, nor the key of the forgotten" no_knowledge_journal
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
  set_clock $((4 * DAY - 3600))
  check "the clock reads an hour earlier: $(date -u '+%a %F %R') UTC" clock_reads $((4 * DAY - 3600))
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

# Fifty cuts at random points of 0 to 300 ms: each cut's line in $OUT/<name>.txt (number, delay, where
# it fell, the id it printed) and the ids printed in $OUT/<name>.ids.
cut_randomly() { # <name> <id pattern> <command...>: @N@ in the command is the cut's number
  local name="$1" pattern="$2" delays n
  shift 2
  read -r -a delays <<<"$(python3 -c 'import random; print(*(random.randint(0, 300) for _ in range(50)))')"
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

power_cuts() {
  echo "power cuts: fifty writes killed after 0 to 300 ms"
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

knowledge_power_cuts() {
  echo "power cuts in the knowledge base: fifty proposals killed after 0 to 300 ms"
  cut_randomly kg-cuts 'f-[0-9a-hjkmnp-tv-z]{6}' bosun kg propose feature/csv-export caveat \
    --value "Power cut @N@: an export check" --source "ledger:$T_JSON"
  bosun kg validate 2>&1 | sed 's/^/  note  before the next write: /'
  [ -e "$KNOWLEDGE/.git/index.lock" ] && echo "  note  a cut left the knowledge history's git lock: .git/index.lock"
  kg_says "the next write works, and repairs what it must" "added" propose feature/csv-export caveat \
    --value "After the power cuts: the export check passed" --source "ledger:$T_JSON"
  as_notes "$KG_SAID"
  echo "  note  set aside: $(torn_files "$KJOURNAL") unfinished transaction(s)"
  echo "  note  $(csv_facts | jq '[.[] | (.value // "") | select(startswith("Power cut "))] | length') of 50 are in the knowledge base"
  kg_valid
  check "every id a cut write printed is in the knowledge base" all_facts_there "$OUT/kg-cuts.ids"
  check "each journal file's seq still runs 1..n with no gap" journal_lines seq "$KJOURNAL"
  check "the knowledge history still records each write" history_records
  if [ -e "$KNOWLEDGE/.git/index.lock" ]; then # the rest of the week needs a history: git's message says to remove it
    rm -f "$KNOWLEDGE/.git/index.lock"
    echo "  note  removed the git lock a cut left, by hand, as git's message says"
  fi
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
  check "the installed bosun is as it was" test "$(git -C "$DIR" rev-parse HEAD)" = "$(tree_head)"
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
check "... and the other two" bash -c 'grep -q "name: acme-web" "$1" && grep -q "name: invoice-exporter" "$1"' _ "$CONFIG/sessions.yml"
what_the_release_does
launchd_for_real
the_old_week
the_upgrade
the_first_write
the_first_knowledge_write
knowledge_privacy
the_new_days
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
