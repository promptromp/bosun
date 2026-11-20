#!/bin/bash
# Hermetic smoke tests: temp HOME, fixture sessions, isolated tmux socket, no Claude calls,
# no launchd or pmset changes.
# Run: tests/smoke.sh [-x] [--with-unit] [--only <id>[,<id>...] | --last-failed | --changed <git ref>] [--shard K/N | --jobs N]
#   or: tests/smoke.sh --list           (the sections' ids and names)
#   or: tests/smoke.sh --changed <git ref> --list   (which sections that change would run, and why; runs nothing)
# The pre-push hook runs tests/smoke.sh --pre-push: see below.
# End to end only by default: the commit hook runs the unit tests a change reaches, and CI all of
# them. --with-unit runs them all first too, with a plain python3 (python3 -m unittest, no pytest).
# The checks are in sections, each begun by `section <id> "<name>" [needs <id>,...]`, in the order they
# run. --list prints the ids and names. --only runs the shared setup and the sections named (and, in file
# order, the ones they need) and nothing else, to iterate on one area in seconds (a few minutes with
# prerequisites); its summary says it was a partial run. Before committing, run the whole suite once: CI and the pre-push hook always do.
# -x (--exitfirst) stops at the first failing check, after its FAIL line, and the summary says in which section
# and check; the temporary folders and the tmux servers are cleaned up as always. Exit 1.
# --last-failed (--lf) runs only the sections whose last result was a failure (and, as for --only, the ones
# they need): every run, whole or partial, records each section it ran as pass or fail in
# tests/.smoke-cache/sections (git-ignored, per checkout; best effort, a run never fails over it), and a partial
# run changes only the sections it ran. With none recorded as failed it says so and exits 0. It does not combine
# with --only; it combines with -x and --with-unit. Checks outside any section (the unit tests of --with-unit,
# the last check on the repository) are not recorded. While iterating: --only, --last-failed, -x; before
# committing, one full run with none of them (CI and the pre-push hook run the full suite).
# --changed <git ref> runs only the sections the change since the merge-base of <ref> and HEAD can reach (committed,
# staged, unstaged and untracked files), each section's `covers` and tests/smoke_selection.txt being what that goes by
# (tools/smoke_select.py: its docstring has the rules; this file is read section by section), and prints which file
# selected which section. It fails closed:
# a changed file on the select-everything list, one no section covers and nothing ignores, a ref that does not resolve,
# a selection that fails, all run the whole suite; only ignored files (the docs) run nothing, exit 0. It does not
# combine with --only or --last-failed; it combines with -x and --with-unit. --changed <ref> --list prints that report
# and the answer and runs nothing (exit 0): the way to see the choice, since --changed alone runs it.
# --shard K/N runs shard K of N (CI runs the shards on N machines at once): of the sections the run would run (the
# whole suite, or what --only, --last-failed or --changed chose), each goes to exactly one shard, balanced by how long
# they take (tests/smoke_weights.txt; tools/smoke_shard.py has the rules), and the shard runs its own as --only does,
# with what they need and the shared setup. uninstall-files, which checks what the run left behind, runs in every
# shard that runs anything. Its summary says which shard of how many it was; a shard with no section exits 0. It
# combines with all of them but --pre-push; K/N that is not 1 <= K <= N is a usage error. Every run but a --jobs one
# records how long each section took when it passed in tests/.smoke-cache/durations, which --record of the tool turns
# into the weights.
# --jobs N runs what the run would run as N shards side by side on this machine, each `--shard K/N` in its own process
# (folders, tmux servers, cache files, process group), and prints each shard's output as it ends, then one summary; it
# exits 1 if any shard failed. It combines with --only, --last-failed, --changed, --pre-push, -x (the first shard to
# stop stops the others) and --with-unit (the unit tests once, before the shards), not with --shard or --list. The
# shards' results are merged into the checkout's cache (a failure in any shard wins; not their durations, times under
# load: the weights come from a serial run), and a whole run records its tree as one does.
# --pre-push (what the pre-push hook runs) skips the suite, saying so in one line, if the tree of the commit being
# pushed (pre-commit's PRE_COMMIT_TO_REF) passed a whole run in this checkout less than 24 hours ago, and
# otherwise runs what --changed origin/main selects (BOSUN_SMOKE_BASE=<ref> names another base; this is the
# build-out setting, to go back to the whole suite once the project is past its release: drop the selection from
# the `--pre-push` branch below). When that is the whole suite, or sections the hook would take more than
# BOSUN_SMOKE_DEFER_SECONDS to run (180 by default: the longest of its --jobs shards by tests/smoke_weights.txt; a
# build-out setting too), the hook leaves it to the merge queue, whose smoke job runs it (defer_whole_run,
# defer_long_selection). The hook runs what it runs as --jobs 3. BOSUN_SMOKE_FULL=1 runs the whole suite. A whole run (none of --only,
# --last-failed, -x) that ends with no failure records its tree in tests/.smoke-cache/passes, but only if the tree is the same at its end as at
# its start: the tree is the files on disk before the first section runs (what `git add -A` stages in a copy of
# the index: tracked files, and untracked ones that are not git-ignored, which a commit lacking them will not match,
# so the suite runs for it), and a run that changed or made any such file records nothing and names it.
# BOSUN_SMOKE_NO_CACHE=1 makes --pre-push ignore the pass record (the one way, and not SKIP=): it runs what the change
# reaches, and with BOSUN_SMOKE_FULL=1 the whole suite. Doubt of any kind
# (the record is unreadable or older, the tree differs, git fails) runs the suite. CI never uses --pre-push.
# A section that cannot run alone either sets up what it uses itself or says `needs <id>`.
# `always_run` marks where the code every run needs resumes after a section: the setup, and the last
# check, that the real repository is as it was.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail

# A marker is a line that starts with `section` (indented inside the one `if` that guards the install
# tests). tests/unit/test_smoke_sections.py holds the file to this: every marker parses, ids are unique.
SECTION_MARKER='^[[:space:]]*section[[:space:]]+([a-z0-9-]+)[[:space:]]+"([^"]*)"([[:space:]]+needs[[:space:]]+([a-z0-9,-]+))?([[:space:]]+covers[[:space:]]+'\''([^'\'']*)'\'')?[[:space:]]*$'

# section <id> "<name>" [needs <id>,...] [covers '<glob>,...']: the heading of a section, which runs up to the next
# marker. `covers` is what --changed (and the pre-push hook) go by: the files the section runs or reads (see below).
SECTION_NEEDS="" # a line `<id>:<need>,<need>` for each section this run has entered (needs_section reads them)
SESSION_OWNER="" # the section that started the bosun session that may be up (no_session records it)
section() {
  section_done
  SECTION_NEEDS="$SECTION_NEEDS$1:$([ "${3:-}" = needs ] && echo "$4")"$'\n'
  # A session a section left up for the ones that need it is no other section's: one that needs none of it (a
  # partial run, a shard) begins with none, so what it checks never meets another section's windows.
  if [ -n "$SESSION_OWNER" ] && ! needs_section "$1" "$SESSION_OWNER"; then
    kill_session
    SESSION_OWNER=""
  fi
  CURRENT_SECTION="$1"
  SECTION_FAILED_AT="$failed"
  SECTION_STARTED="$SECONDS"
  echo "$2"
  [ -z "${FAKES_READY:-}" ] || fakes_first
}
# needs_section <id> <other>: section <id> needs <other>, itself or through what it needs (of the sections entered).
needs_section() {
  local todo="$1" seen="," id need
  [ "$1" = "$2" ] && return 0
  while [ -n "$todo" ]; do
    id="${todo%%,*}"
    if [ "$todo" = "$id" ]; then todo=""; else todo="${todo#*,}"; fi
    case "$seen" in *",$id,"*) continue ;; esac
    seen="$seen$id,"
    for need in $(grep -m1 "^$id:" <<<"$SECTION_NEEDS" | cut -d: -f2 | tr ',' ' '); do
      [ "$need" = "$2" ] && return 0
      todo="${todo:+$todo,}$need"
    done
  done
  return 1
}
# always_run ["<heading>"]: what follows runs in every run, whichever sections were chosen.
always_run() {
  section_done
  [ -z "${1:-}" ] || echo "$1"
  [ -z "${FAKES_READY:-}" ] || fakes_first
}

smoke_usage() {
  echo "usage: tests/smoke.sh [-x] [--with-unit] [--only <id>[,<id>...] | --last-failed | --changed <git ref> | --pre-push] [--shard K/N | --jobs N]  |  tests/smoke.sh [--changed <git ref>] --list" >&2
  exit 2
}

# failed_sections <script> <cache file>: "id,id,..." the sections that exist now whose last result was a
# failure, in file order; empty if none (or no cache).
failed_sections() {
  local id needs name out=""
  [ -f "$2" ] || return 0
  while IFS='|' read -r id needs name; do
    grep -qx "$id fail" "$2" && out="${out:+$out,}$id"
  done < <(section_table "$1")
  echo "$out"
}

# section_done: the section that was running is over: its result goes in the cache, as a failure if a check
# failed in it, and if it passed, how long it took (what --shard balances by, once in tests/smoke_weights.txt).
CURRENT_SECTION="" # "" between sections: the setup, and what an always_run begins
SECTION_FAILED_AT=0
SECTION_STARTED=0
section_done() {
  [ -n "$CURRENT_SECTION" ] || return 0
  if [ "$failed" -gt "$SECTION_FAILED_AT" ]; then
    cache_update "$CACHE_FILE" "$CURRENT_SECTION" "$CURRENT_SECTION fail"
  else
    cache_update "$CACHE_FILE" "$CURRENT_SECTION" "$CURRENT_SECTION pass"
    cache_update "$DURATIONS_FILE" "$CURRENT_SECTION" "$CURRENT_SECTION $((SECONDS - SECTION_STARTED))"
  fi
  CURRENT_SECTION=""
}

# section_table <script>: one line per section, in file order: id|needs|name.
section_table() {
  local line
  while IFS= read -r line; do
    [[ $line =~ $SECTION_MARKER ]] && printf '%s|%s|%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[4]}" "${BASH_REMATCH[2]}"
  done < <(grep -E '^[[:space:]]*section[[:space:]]' "$1")
  return 0
}

list_sections() { # <script>
  local id needs name
  while IFS='|' read -r id needs name; do
    printf '%-24s %s%s\n' "$id" "$name" "${needs:+  (needs $needs)}"
  done < <(section_table "$1")
}

# chosen_sections <script> <ids>: ",id,id,..." those ids and, in turn, what they need. An id that is no
# section is a usage error that names it.
chosen_sections() {
  local table known id needs name need want=",$2," changed=1
  table="$(section_table "$1")"
  known=",$(cut -d'|' -f1 <<<"$table" | tr '\n' ','),"
  for id in ${2//,/ }; do
    case "$known" in
      *",$id,"*) ;;
      *)
        echo "tests/smoke.sh: no section '$id' (--list shows them)" >&2
        exit 2
        ;;
    esac
  done
  while [ "$changed" = 1 ]; do
    changed=0
    while IFS='|' read -r id needs name; do
      case "$want" in *",$id,"*) ;; *) continue ;; esac
      for need in ${needs//,/ }; do
        case "$want" in
          *",$need,"*) ;;
          *)
            want="$want$need,"
            changed=1
            ;;
        esac
      done
    done <<<"$table"
  done
  echo "$want"
}

# filtered_script <script> <chosen>: the script without the sections not chosen, each from its marker to
# the next one (a section is whole commands, so what is left parses as before).
filtered_script() {
  awk -v want="$2" '
    BEGIN { on = 1 }
    /^[ \t]*section[ \t]+[a-z0-9-]+[ \t]/ { on = (index(want, "," $2 ",") > 0) }
    /^[ \t]*always_run([ \t]|$)/ { on = 1 }
    on { print }' "$1"
}

# run_chosen <script> <ids>: runs the script again without the sections not named, and says which ones it
# runs (SMOKE_PARTIAL is what its summary says). Never returns.
run_chosen() {
  local chosen id needs="" script unit_flag="" exit_flag=""
  chosen="$(chosen_sections "$1" "$2")" || exit 2
  for id in ${chosen//,/ }; do
    case ",$2," in *",$id,"*) ;; *) needs="${needs:+$needs }$id" ;; esac
  done
  script="$(mktemp "${TMPDIR:-/tmp}/smoke-only.XXXXXX")"
  filtered_script "$1" "$chosen" >"$script"
  echo "partial run, not the whole suite: ${2//,/ }${needs:+ (and, as they need them: $needs)}"
  [ "$WITH_UNIT" = 1 ] && unit_flag=--with-unit
  [ "$EXITFIRST" = 1 ] && exit_flag=-x
  SMOKE_PARTIAL="${2//,/ }${needs:+ + $needs}" SMOKE_SHARD_OF="$SHARD_OF" SMOKE_FILTERED="$script" SMOKE_ROOT="$ROOT" \
    SMOKE_CACHE_DIR="$CACHE_DIR" exec "$BASH" "$script" ${unit_flag:+"$unit_flag"} ${exit_flag:+"$exit_flag"}
}

# shard_of <script> <ids>: of those sections (none: every section), the ones shard $SHARD runs as its own
# (tools/smoke_shard.py: each section in one shard, balanced by tests/smoke_weights.txt). Prints its line, and sets
# ONLY to them and SHARD_OF to what the summary says, or exits 0 when no section falls in this shard. Whatever goes
# wrong (no python3, no answer, no split) leaves ONLY as it was: this shard runs every one of them.
shard_of() {
  local out last
  [ -z "$2" ] || chosen_sections "$1" "$2" >/dev/null # an id that is no section is a usage error, as for --only
  out="$(python3 "$ROOT/tools/smoke_shard.py" --script "$1" --weights "$ROOT/tests/smoke_weights.txt" \
    --shard "$SHARD" ${2:+--sections "$2"} 2>&1)"
  last="${out##*$'\n'}"
  SHARD_OF="${SHARD%/*} of ${SHARD#*/}"
  case "$last" in
    "=> none")
      echo "${out%$'\n'*}"
      echo "smoke shard $SHARD_OF: no section falls in this shard, nothing to run"
      exit 0
      ;;
    "=> only "?*)
      echo "${out%$'\n'*}"
      ONLY="${last#=> only }"
      ;;
    "=> all") echo "${out%$'\n'*}" ;;
    *) echo "smoke shard $SHARD_OF: tools/smoke_shard.py gave no answer, so this shard runs every selected section${out:+: $out}" ;;
  esac
}

# select_changed <git ref>: the sections the change since <ref> can reach (tools/smoke_select.py reads their
# `covers` and tests/smoke_selection.txt; its docstring says how). Prints its report, and sets SELECTED to `full`,
# `none` or `only` (with SELECTED_IDS). Whatever goes wrong (no python3, no answer, a ref that does not resolve) is `full`.
SELECTED=full
SELECTED_IDS=""
select_changed() {
  local out last
  out="$(python3 "$ROOT/tools/smoke_select.py" --root "$ROOT" --script "$SCRIPT" \
    --selection "$ROOT/tests/smoke_selection.txt" --base "$1" 2>&1)"
  last="${out##*$'\n'}"
  case "$last" in
    "=> none" | "=> full") SELECTED="${last#=> }" ;;
    "=> only "?*)
      SELECTED=only
      SELECTED_IDS="${last#=> only }"
      ;;
    *) out="smoke selection: the whole suite: tools/smoke_select.py gave no answer${out:+: $out}" ;;
  esac
  echo "${out%$'\n'*}"
}

# The pre-push hook leaves a whole run to the merge queue, whose smoke job makes the same choice from the merge
# group's base (.github/workflows/ci.yml, `tests/smoke.sh --changed`): a change that reaches every section (a file on
# the select-everything list, one no section covers, a base that does not resolve) waited out the whole suite here
# and again there, about 15 minutes each. BOSUN_SMOKE_FULL=1 runs it here all the same.
defer_whole_run() {
  echo "smoke selection: the change reaches the whole suite, which the merge queue runs; BOSUN_SMOKE_FULL=1 runs it here"
  exit 0
}

# defer_long_selection <ids>: the same for a selection the hook would take more than BOSUN_SMOKE_DEFER_SECONDS (180 s by
# default; the build-out setting, as the selection is) to run: the longest of the shards it runs them as (--jobs), by
# tests/smoke_weights.txt, each with what its sections need. Whatever cannot weigh them (no python3, an unknown id, a
# setting that is not a number) runs them here.
defer_long_selection() {
  local seconds limit="${BOSUN_SMOKE_DEFER_SECONDS:-180}" shards="${JOBS:-1}" how
  seconds="$(python3 "$ROOT/tools/smoke_shard.py" --script "$SCRIPT" --weights "$ROOT/tests/smoke_weights.txt" \
    --seconds --shards "$shards" --sections "$1" 2>/dev/null)" || return 0
  case "$seconds,$limit" in *[!0-9,]* | ,* | *,) return 0 ;; esac
  [ "$seconds" -gt "$limit" ] || return 0
  how="as one run"
  [ "$shards" = 1 ] || how="the longest of $shards shards side by side"
  echo "smoke selection: those sections would take this hook about $seconds s by smoke_weights.txt ($how), more than BOSUN_SMOKE_DEFER_SECONDS ($limit), so the merge queue runs them; tests/smoke.sh --changed $CHANGED${JOBS:+ --jobs $JOBS} runs them here, BOSUN_SMOKE_FULL=1 the whole suite"
  exit 0
}

# run_jobs <ids>: what this run would run (those sections, or every one) as $JOBS shards side by side on this machine,
# each a run of this script with --shard K/$JOBS in its own process, folders and cache files (start_shard), each
# shard's output printed as it ends (wait_shards), then one summary over all and the shards' results merged into this
# checkout's cache (merge_shard_caches; not their durations, which are times under each other's load). --with-unit
# runs the unit tests once first (unit_tests_alone); with -x a failure there, or the first shard's, stops the rest. On
# exit (Ctrl-C too) the shards still running are stopped and waited for (settle_shards). A whole run records its tree
# as one does (full_run_end). Never returns.
JOBS_DIR=""
run_jobs() {
  local k
  JOBS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/smoke-jobs.XXXXXX")"
  trap 'settle_shards; cleanup; rm -rf "${JOBS_DIR:?}"' EXIT
  echo "$JOBS shards side by side (--jobs $JOBS), each a run of its own: tests/smoke.sh --shard K/$JOBS${1:+ --only ${1//,/ }}"
  [ "$WITH_UNIT" = 0 ] || unit_tests_alone
  if [ "$EXITFIRST" = 0 ] || [ "$WITH_UNIT" = 0 ] || [ "$(cat "$JOBS_DIR/unit/status")" = 0 ]; then
    for k in $(seq 1 "$JOBS"); do start_shard "$k" "$1"; done
    wait_shards
  fi
  merge_shard_caches
  for k in $([ "$WITH_UNIT" = 0 ] || echo unit) $(seq 1 "$JOBS"); do [ ! -d "$JOBS_DIR/$k" ] || count_shard "$k"; done
  PARTIAL="${1:+${1//,/ }}"
  full_run_end
  summary
  [ "$failed" -eq 0 ] && exit 0
  exit 1
}

# unit_tests_alone: --with-unit's unit tests, once, before the shards: the shared setup and the always_run parts alone.
unit_tests_alone() {
  local script="$JOBS_DIR/unit.sh"
  mkdir -p "$JOBS_DIR/unit/cache"
  filtered_script "$SCRIPT" ",," >"$script"
  SMOKE_PARTIAL="the unit tests (--with-unit)" SMOKE_FILTERED="$script" SMOKE_ROOT="$ROOT" \
    SMOKE_CACHE_DIR="$JOBS_DIR/unit/cache" "$BASH" "$script" --with-unit >"$JOBS_DIR/unit/log" 2>&1
  echo "$?" >"$JOBS_DIR/unit/status"
  print_shard unit "the unit tests"
}

# start_shard <k> <ids>: shard k, in the background, in a process group of its own (python3 starts it so; bash has no
# job control without a terminal), which stop_shards can stop whole, and with no terminal to read. A background job
# of a script starts with SIGINT ignored, which its children could not undo: python3 gives it back its default first,
# so a check that interrupts a command works as in a serial run (the process group keeps the terminal's Ctrl-C away).
# Its log, its exit status once it ends, and its pid (the group's, beside its wrapper's) are in $JOBS_DIR/<k>.
SHARD_START='import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.setpgid(0, 0); os.execv(sys.argv[1], sys.argv[1:])'
start_shard() {
  local out="$JOBS_DIR/$1" exit_flag=""
  mkdir -p "$out/cache"
  [ "$EXITFIRST" = 0 ] || exit_flag=-x
  (
    SMOKE_ROOT="$ROOT" SMOKE_CACHE_DIR="$out/cache" python3 -c "$SHARD_START" \
      "$BASH" "$SCRIPT" --shard "$1/$JOBS" ${2:+--only "$2"} ${exit_flag:+"$exit_flag"} </dev/null >"$out/log" 2>&1 &
    echo "$!" >"$out/pid"
    wait "$!"
    echo "$?" >"$out/status"
  ) &
  echo "$!" >"$out/wrapper"
}

# wait_shards: until every shard has ended, printing each as it does; with -x, the first that failed stops the others.
wait_shards() {
  local k running still stopping=0
  running="$(seq 1 "$JOBS" | tr '\n' ' ')"
  while [ -n "${running// /}" ]; do
    still=""
    for k in $running; do
      if [ ! -s "$JOBS_DIR/$k/status" ]; then
        still="$still $k"
        continue
      fi
      print_shard "$k" "shard $k of $JOBS"
      [ "$EXITFIRST" = 0 ] || [ "$(cat "$JOBS_DIR/$k/status")" = 0 ] || { stop_shards && stopping=1; }
    done
    running="$still"
    [ -z "${running// /}" ] || shards_waited "$stopping" || return 0
  done
}

# shards_waited <stopping>: a second of the wait; once -x stopped the shards, at most 30 of them (a failsafe: a stopped
# shard may be waiting on a child that ignores TERM), then it says so and the wait ends (the exit settles them again).
SHARDS_STOP_WAIT=0
shards_waited() {
  sleep 1
  [ "$1" = 1 ] || return 0
  SHARDS_STOP_WAIT=$((SHARDS_STOP_WAIT + 1))
  [ "$SHARDS_STOP_WAIT" -lt 30 ] && return 0
  echo "tests/smoke.sh: a shard -x stopped had not ended 30 s later; not waiting for it" >&2
  return 1
}

# stop_shards: each shard still running gets TERM once, its whole process group (what it waits on too; its EXIT trap
# cleans up after it), and never a pid that has come to be another process's: the shard is its wrapper's child still.
# A shard stopped so is marked, and not counted as a failure of its own.
stop_shards() {
  local out pid
  [ -n "$JOBS_DIR" ] || return 0
  for out in "$JOBS_DIR"/[0-9]*; do
    [ -d "$out" ] && [ ! -s "$out/status" ] && [ -s "$out/pid" ] && [ ! -e "$out/stopped" ] || continue
    pid="$(cat "$out/pid")"
    [ "$(command -p ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')" = "$(cat "$out/wrapper")" ] || continue
    touch "$out/stopped"
    kill -TERM -- "-$pid" 2>/dev/null
  done
  return 0
}

# settle_shards: on the way out (the end, Ctrl-C, -x), stop the shards still running, one whose pid is not written yet
# too, and wait until each has cleaned up after itself and its wrapper has written its status (30 s at most, a
# failsafe), before their folder goes.
settle_shards() {
  local tries
  [ -n "$JOBS_DIR" ] || return 0
  for tries in $(seq 1 150); do
    stop_shards
    shards_settled && return 0
    sleep 0.2
  done
  echo "tests/smoke.sh: a shard had not ended after it was stopped ($tries tries)" >&2
}
shards_settled() {
  local out
  for out in "$JOBS_DIR"/[0-9]*; do
    [ ! -d "$out" ] || [ -s "$out/status" ] || return 1
  done
}

print_shard() { # <k> <heading>
  echo
  echo "=== $2 (exit $(cat "$JOBS_DIR/$1/status")) ==="
  cat "$JOBS_DIR/$1/log"
}

# count_shard <k>: shard k's checks (or the unit tests', k=unit) in this run's counts, from its summary line; one that
# ended without one (it crashed) and not with 0 counts as a failure, as does one that failed with none. One that -x
# stopped is said, not counted: the failure that stopped it is (and one that ended on its own as the stop came counts
# what its summary says).
count_shard() {
  local line status stopped=0
  status="$(cat "$JOBS_DIR/$1/status" 2>/dev/null || echo "no status")"
  [ ! -e "$JOBS_DIR/$1/stopped" ] || stopped=1
  line="$(grep -E '^[0-9]+ passed, [0-9]+ failed' "$JOBS_DIR/$1/log" 2>/dev/null | tail -1)"
  if [ -n "$line" ]; then # (it ended on its own, before the stop or without one: what it says counts)
    passed=$((passed + ${line%% passed*}))
    line="${line#* passed, }"
    failed=$((failed + ${line%% failed*}))
    [ "$status" = 0 ] || [ "$stopped" = 1 ] || [ "${line%% failed*}" != 0 ] || failed=$((failed + 1)) # stopped short
  elif [ "$stopped" = 1 ]; then
    echo "shard $1 of $JOBS: stopped (-x), when another shard failed"
  elif [ "$status" != 0 ]; then
    failed=$((failed + 1))
    echo "shard $1 of $JOBS ended (exit $status) without its summary"
  fi
}

# merge_shard_caches: the shards' (and the unit tests') results into this checkout's cache, by one writer: a section's
# result is a failure if any shard that ran it failed it. Not their durations: the weights are measured by a serial
# run (tools/smoke_shard.py --record), and these are times under the other shards' load.
merge_shard_caches() {
  local id result
  while read -r id result; do
    cache_update "$CACHE_FILE" "$id" "$id $result"
  done < <(cat "$JOBS_DIR"/*/cache/sections 2>/dev/null | awk '{ if ($2 == "fail" || !($1 in r)) r[$1] = $2 } END { for (i in r) print i, r[i] }')
}

WITH_UNIT=0
ONLY=""
CHANGED=""
LIST=0
EXITFIRST=0
LAST_FAILED=0
PRE_PUSH=0
SHARD=""
JOBS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --with-unit) WITH_UNIT=1 ;;
    --list) LIST=1 ;;
    -x | --exitfirst) EXITFIRST=1 ;;
    --last-failed | --lf) LAST_FAILED=1 ;;
    --pre-push) PRE_PUSH=1 ;;
    --changed)
      [ $# -ge 2 ] && [ -n "$2" ] || smoke_usage
      CHANGED="$2"
      shift
      ;;
    --only)
      [ $# -ge 2 ] && [ -n "$2" ] || smoke_usage
      ONLY="$2"
      shift
      ;;
    --shard)
      [ $# -ge 2 ] && [ -n "$2" ] || smoke_usage
      SHARD="$2"
      shift
      ;;
    --jobs)
      [ $# -ge 2 ] && [ -n "$2" ] || smoke_usage
      JOBS="$2"
      shift
      ;;
    *) smoke_usage ;;
  esac
  shift
done
[ "$LAST_FAILED" = 0 ] || [ -z "$ONLY" ] || smoke_usage                                                  # the two choose sections in their own ways
[ "$PRE_PUSH" = 0 ] || { [ -z "$ONLY" ] && [ "$LAST_FAILED" = 0 ] && [ -z "$CHANGED" ]; } || smoke_usage # a hook chooses by itself
[ -z "$CHANGED" ] || { [ -z "$ONLY" ] && [ "$LAST_FAILED" = 0 ]; } || smoke_usage                        # three ways to choose sections
[ "$PRE_PUSH" = 0 ] || [ -z "$SHARD" ] || smoke_usage                                                    # nor does it split
[ -z "$JOBS" ] || { [ -z "$SHARD" ] && [ "$LIST" = 0 ]; } || smoke_usage                                 # --jobs runs the shards itself
if [ -n "$JOBS" ] && ! [[ $JOBS =~ ^[1-9][0-9]?$ ]]; then
  echo "tests/smoke.sh: --jobs wants how many shards to run side by side, 1 to 99, not '$JOBS'" >&2
  exit 2
fi
if [ -n "$SHARD" ] && ! { [[ $SHARD =~ ^([1-9][0-9]{0,2})/([1-9][0-9]{0,2})$ ]] && [ "${BASH_REMATCH[1]}" -le "${BASH_REMATCH[2]}" ]; }; then
  echo "tests/smoke.sh: --shard wants K/N with 1 <= K <= N (2/3: the second of three), not '$SHARD'" >&2
  exit 2
fi
export PYTHONDONTWRITEBYTECODE=1
# Never touch the repository this runs from: a git hook (pre-push) or another repository's tooling
# may export GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_COMMON_DIR, GIT_CONFIG*, ... and every git
# command below (git init, git log in scratch folders) would then read or write that repository.
for git_var in $(compgen -e | grep '^GIT_'); do unset "$git_var"; done
# Run from inside tmux (yours, or an agent's), the commands would see that tmux; in CI there is none.
# Checks that want one set TMUX themselves.
unset TMUX TMUX_PANE

SCRIPT="$0"
# A partial run (run_chosen) is a copy of this script in a temporary file, which says where the checkout is.
ROOT="${SMOKE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PARTIAL="${SMOKE_PARTIAL:-}"                    # which sections a partial run ran, for its summary
SHARD_OF="${SMOKE_SHARD_OF:-}"                  # which shard of how many (--shard), for the summary too
[ "${SMOKE_FILTERED:-}" != "$0" ] || rm -f "$0" # (an open script can be removed; bash reads it as it goes)
# Where each section's last result is kept (see cache_merge), in the checkout: git-ignored, and the copies of the
# tree that sections make leave it out.
CACHE_DIR="${SMOKE_CACHE_DIR:-$ROOT/tests/.smoke-cache}"
CACHE_FILE="$CACHE_DIR/sections"
PASSES_FILE="$CACHE_DIR/passes"
DURATIONS_FILE="$CACHE_DIR/durations"
# shellcheck source=tests/smoke_cache.sh
. "$ROOT/tests/smoke_cache.sh"
unset SMOKE_PARTIAL SMOKE_SHARD_OF SMOKE_FILTERED SMOKE_ROOT SMOKE_CACHE_DIR
if [ "$LIST" = 1 ] && [ -n "$CHANGED" ]; then # --changed <ref> --list: the choice and its reasons, nothing run
  select_changed "$CHANGED"
  case "$SELECTED" in
    only) echo "=> would run: ${SELECTED_IDS//,/ } (and what they need)" ;;
    none) echo "=> would run: nothing" ;;
    *) echo "=> would run: the whole suite" ;;
  esac
  exit 0
fi
if [ "$LIST" = 1 ]; then
  list_sections "$SCRIPT"
  exit 0
fi
if [ "$PRE_PUSH" = 1 ]; then # the hook: nothing to run if this tree already passed, here, a whole run
  pre_push_skip "$ROOT" "$PASSES_FILE" "${PRE_COMMIT_TO_REF:-}" "$(date +%s)"
  case $? in 0 | 3) exit 0 ;; esac
  # otherwise the sections the change from origin/main can reach (the build-out setting), or all of them
  if [ "${BOSUN_SMOKE_FULL:-}" = 1 ]; then
    echo "BOSUN_SMOKE_FULL=1: running the whole suite"
  else
    CHANGED="${BOSUN_SMOKE_BASE:-origin/main}"
  fi
fi
if [ -n "$CHANGED" ]; then
  select_changed "$CHANGED"
  case "$SELECTED" in
    none) exit 0 ;;
    only)
      ONLY="$SELECTED_IDS"
      [ "$PRE_PUSH" = 0 ] || defer_long_selection "$ONLY"
      ;;
    full) [ "$PRE_PUSH" = 1 ] && defer_whole_run ;;
  esac
fi
if [ "$LAST_FAILED" = 1 ]; then
  ONLY="$(failed_sections "$SCRIPT" "$CACHE_FILE")"
  if [ -z "$ONLY" ]; then
    echo "--last-failed: no section is recorded as failed; nothing to run"
    exit 0
  fi
  echo "--last-failed: rerunning the sections that failed: ${ONLY//,/ }"
fi
[ -z "$SHARD" ] || shard_of "$SCRIPT" "$ONLY"
[ -z "$ONLY" ] || [ "${JOBS:-1}" -gt 1 ] || run_chosen "$SCRIPT" "$ONLY" # (--jobs runs its shards below)
# What must not change during the run, in this checkout: its HEAD, what it has staged and core.bare (checked
# at the end). Not the shared repository's HEAD: with git worktrees that is the main checkout's, and it moves
# when a session in another worktree commits (tests/unit/repo_guard.py is the twin of this, and tests it).
repo_state() {
  local staged
  staged="$(git -C "$ROOT" diff --cached --raw --no-abbrev 2>/dev/null | shasum | cut -c1-12)"
  printf 'head=%s staged=%s bare=%s\n' "$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)" "$staged" \
    "$(git -C "$ROOT" config --get core.bare 2>/dev/null)"
}
REPO_STATE_BEFORE="$(repo_state)"
# A whole run (no partial one) is recorded as a pass of the tree it tested: the files on disk now, before the first
# section runs, and again at its end (full_run_end), since a run that changes the repository tested nothing
# reproducible.
START_TREE=""
START_STATUS=""
if [ -z "$PARTIAL" ] && { [ "${JOBS:-1}" -le 1 ] || [ -z "$ONLY" ]; }; then # (--jobs --only: a partial run too)
  START_TREE="$(tree_of_checkout "$ROOT")"
  START_STATUS="$(status_of_checkout "$ROOT")"
fi
BOSUN="$ROOT/bin/bosun"
WORK="$(mktemp -d)"
SOCKET="bosun-smoke-$$"
passed=0
failed=0
STOPPED_AT=""  # with -x, where the run stopped
CONSOLE_PID="" # the console test's server, while it runs
BG_PIDS=""     # commands a check runs in the background (a quit, a start, a lock's holder) until it waits for them

# bg_started <pid>: a command a check started in the background, stopped with the run if it still runs then.
bg_started() { BG_PIDS="$BG_PIDS $1"; }
# bg_done <pid>: wait for it, with its status, and drop it from the list: once it ended, its pid may name another
# process.
bg_done() {
  local status=0 pid rest=""
  wait "$1" 2>/dev/null || status=$?
  for pid in $BG_PIDS; do [ "$pid" = "$1" ] || rest="$rest $pid"; done
  BG_PIDS="$rest"
  return "$status"
}
# own_child <pid>: the pid names a child of this run's shell still. One that ended on its own was reaped by bash,
# and the system may have given its pid to anyone's process since.
own_child() { [ "$(command -p ps -o ppid= -p "$1" 2>/dev/null | tr -d ' ')" = "$$" ]; }
# stop_background: what bg_started recorded and no bg_done took back, if it is still this run's own (never a pid that
# has come to name another process).
stop_background() {
  local pid
  for pid in $BG_PIDS; do own_child "$pid" && kill "$pid" 2>/dev/null; done
  BG_PIDS=""
}

cleanup() {
  [ -n "$CONSOLE_PID" ] && kill "$CONSOLE_PID" 2>/dev/null
  stop_background
  tmux -L "$SOCKET" kill-server 2>/dev/null
  tmux -L "$SOCKET-update" kill-server 2>/dev/null # the update's windows (bosun update)
  tmux -L "$SOCKET-agy" kill-server 2>/dev/null    # the windows on the agy stand-in
  rm -f "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/${SOCKET:?}" "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/${SOCKET:?}-update" \
    "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/${SOCKET:?}-agy"
  rm -rf "${WORK:?}"
}
trap cleanup EXIT

# check <description> <command...>: run the command; on failure, show the end of its output.
check() {
  local description="$1" output
  shift
  if output="$("$@" 2>&1)"; then
    passed=$((passed + 1))
    echo "  pass  $description"
  else
    fail_check "$description" "$output"
  fi
}

# fail_check <description> [output]: a check failed: counted, shown with the end of its output; with -x the run
# stops here.
fail_check() {
  failed=$((failed + 1))
  echo "  FAIL  $1"
  [ -z "${2:-}" ] || tail -5 <<<"$2" | sed 's/^/          /'
  [ "$EXITFIRST" = 1 ] || return 0
  STOPPED_AT="${CURRENT_SECTION:+section $CURRENT_SECTION, }check: $1"
  [ -n "$CURRENT_SECTION" ] || STOPPED_AT="outside any section (the shared setup or an always_run part), $STOPPED_AT"
  full_run_end
  summary
  exit 1 # the EXIT trap cleans up
}

# full_run_end: the end of a whole run, or where -x stops one. A failure forgets the tree's earlier pass; a pass is
# recorded (the passes file) only without -x and only if the tree is the one the run began with, and a run that
# changed the repository says what it changed.
full_run_end() {
  local end_tree="" what
  [ -n "$START_TREE" ] || return 0
  [ "$failed" -gt 0 ] || [ "$EXITFIRST" = 1 ] || end_tree="$(tree_of_checkout "$ROOT")"
  [ "$failed" -eq 0 ] && [ "$EXITFIRST" = 1 ] && return 0
  record_full_run "$PASSES_FILE" "$START_TREE" "$end_tree" "$failed" "$passed" "$(date +%s)"
  [ $? = 2 ] && [ -n "$end_tree" ] || return 0
  what="$(changed_paths "$START_STATUS" "$(status_of_checkout "$ROOT")")"
  echo "the run changed the repository, so this tree is not recorded as passing: ${what:-a file that was already changed changed again}"
}

# summary: what the run did; the end of a run, or of one that -x stops.
summary() {
  local note="${PARTIAL:+a partial run, not the whole suite: $PARTIAL}"
  [ -z "$SHARD_OF" ] || note="shard $SHARD_OF${note:+, $note}"
  [ "${JOBS:-1}" -le 1 ] || note="$JOBS shards side by side${note:+, $note}"
  section_done
  echo
  echo "$passed passed, $failed failed${note:+ ($note)}"
  [ -z "$STOPPED_AT" ] || echo "stopped at the first failure (-x): $STOPPED_AT"
}

[ "${JOBS:-1}" -le 1 ] || run_jobs "$ONLY" # (what it needs of the harness is defined above)

not_contains() { ! grep -qF -- "$2" <<<"$1"; }
contains() { grep -qF -- "$2" <<<"$1" || {
  echo "expected to find: $2"
  echo "in: $(tail -3 <<<"$1")"
  return 1
}; }

# fails_with <text> <command...>: the command fails and says <text>, so a crash or a usage error
# does not count as the expected refusal.
fails_with() {
  local text="$1" output
  shift
  if output="$("$@" 2>&1)"; then
    echo "succeeded: $output"
    return 1
  fi
  contains "$output" "$text"
}

# fails_with_status <status> <text> <command...>: fails_with, with that exit status (2: a usage error), from one run.
fails_with_status() {
  local status="$1" text="$2" output rc
  shift 2
  output="$("$@" 2>&1)"
  rc=$?
  [ "$rc" -eq "$status" ] || {
    echo "exit $rc, not $status: $output"
    return 1
  }
  contains "$output" "$text"
}

# refused_quietly <text> <command...>: the command fails saying <text> on stderr, and writes no line with <text>
# in bosun's log folder, where the watchdog alerts on every ERROR line (a usage error or a refusal of what the
# person typed is not bosun failing).
refused_quietly() {
  local text="$1" errors
  shift
  if errors="$("$@" 2>&1 >/dev/null)"; then
    echo "succeeded"
    return 1
  fi
  contains "$errors" "ERROR: $text" || return 1
  ! grep -rnF -- "$text" "$BOSUN_LOG_DIR" || {
    echo "logged, so the watchdog would alert on it"
    return 1
  }
}

# copy_tree <dest>: a copy of the working tree (tracked files and untracked ones that are not ignored)
# in <dest>. The first is taken from git's list with tar; each one after is a copy of that one, as clones on
# macOS's APFS (cp -c: no data copied until a file is changed), else a plain copy (another file system, Linux).
copy_tree() {
  local first="$WORK/tree-copy"
  if [ ! -d "$first" ]; then
    mkdir -p "$first.part"
    git -C "$ROOT" ls-files -co --exclude-standard -z | tar -C "$ROOT" --null -T - -cf - | tar -C "$first.part" -xf -
    mv "$first.part" "$first"
  fi
  mkdir -p "$1"
  cp -cRp "$first/." "$1" 2>/dev/null || cp -Rp "$first/." "$1"
}

# version_folder <name>: a copy of the working tree in $WORK/share/bosun-versions/<name>, shaped like
# an installed version: beside the bare repository (bosun.git; empty is enough) that makes the folder
# a versions folder. Prints its path.
version_folder() {
  local dest="$WORK/share/bosun-versions/$1"
  mkdir -p "$WORK/share/bosun-versions/bosun.git"
  copy_tree "$dest"
  (cd "$dest" && pwd -P)
}

# wait_for <seconds> <command...>: poll until the command succeeds, instead of a fixed sleep. <seconds> is only a
# failsafe against a hang (30 s or more: CI's runners are slower than a laptop), never a measurement: a check asserts no
# duration (tests/unit/test_smoke_timing.py).
wait_for() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 0.2
  done
}

# console_started <stdout file> <stderr file>: the console printed its URL; if not, what it said.
console_started() {
  wait_for 30 grep -q '^bosun console: http://127.0.0.1:[0-9]*/?t=' "$1" && return 0
  echo "no URL; the console's stderr:"
  tail -5 "$2"
  return 1
}
has_windows() { [ "$(tmux -L "$SOCKET" list-windows -t =smoke -F '#{window_name}' 2>/dev/null | tr '\n' ' ')" = "$1" ]; }
# no_session: no tmux server on this run's socket, so no bosun session and nothing its windows ran. A section that
# starts bosun's session (or needs none) begins with it: in a whole run the section before has stopped its session,
# but a partial run (--only) or a shard may follow a section whose session a later section would have stopped,
# whose server keeps the environment it was started with (BOSUN_CLAUDE_BIN) for every window opened in it.
no_session() {
  kill_session
  SESSION_OWNER="$CURRENT_SECTION"
}
kill_session() {
  tmux -L "$SOCKET" kill-server 2>/dev/null
  wait_for 30 bash -c '! tmux -L "$1" list-sessions >/dev/null 2>&1' _ "$SOCKET"
}

# The run's own HOME, with two projects and named sessions, and the stand-ins, before the first section: no
# section and no unit test ever runs on the real HOME or reaches a real tool.
export HOME="$WORK/home"
export BOSUN_CONFIG_DIR="$WORK/config"
export BOSUN_STATE_DIR="$WORK/state"
export BOSUN_LOG_DIR="$WORK/state/logs"
export BOSUN_SESSION_NAME="smoke"
export BOSUN_TMUX_SOCKET="$SOCKET"
export BOSUN_LAUNCH_DRY_RUN=1
# The journal's flushes without macOS's F_FULLFSYNC (lib/journal.py): with several checkouts' suites writing on
# one drive, each full flush waits for the drive's whole cache, and the update section's 60-second waits ran out.
export BOSUN_PLAIN_FSYNC=1
# bosun's own waits between looks, which have a knob for tests: quit --let-finish's poll (5 s) and an update's wait
# between turns at the windows it brings along (5 s), short here; their defaults are held by the unit tests.
export BOSUN_QUIT_POLL_SECONDS=0.5 BOSUN_UPDATE_POLL_SECONDS=0.5
export BOSUN_ORCHESTRATOR="" # orchestrator tests below enable it explicitly
REPO_A="$WORK/repos/svc-a"
REPO_B="$WORK/repos/svc-b"
mkdir -p "$REPO_A/.git" "$REPO_B/.git" "$HOME/.claude/sessions" "$HOME/.local/bin" "$BOSUN_CONFIG_DIR"
REPO_A="$(cd "$REPO_A" && pwd -P)"
REPO_B="$(cd "$REPO_B" && pwd -P)"
L() { "$BOSUN" ledger "$@"; }
# This run's own process, as a running Claude's record: a live holder of beta's conversation.
printf '{"pid":%d,"sessionId":"22222222-0000-0000-0000-000000000001"}' $$ >"$HOME/.claude/sessions/$$.json"
# The tools that reach outside this run (a notification on the desktop, an AWS login in the browser, launchd,
# power settings, sudo, opening an app) get a stand-in that does nothing from the start, so that a run which
# skips the section that installs the real fake (--only) never reaches the real tool. Sections that need a
# stand-in with a behaviour replace the plain one, and put it back when they are done. fakes_first, which every
# section and every always_run begins with, stops the run if any of these tools would resolve to anything that
# is not under this run's folder, for bosun's own PATH (it puts $HOME/.local/bin first) or one a section made.
FAKED_TOOLS="osascript open caffeinate codesign ditto spctl pmset aws launchctl sudo"
plain_fake() { # <tool> [exit status] [home]
  printf '#!/bin/sh\nexit %s\n' "${2:-0}" >"${3:-$HOME}/.local/bin/$1"
  chmod +x "${3:-$HOME}/.local/bin/$1"
}
stand_ins_in() { # <home>: the plain stand-ins in <home>/.local/bin, where bosun looks first when HOME is <home>
  local tool
  mkdir -p "$1/.local/bin"
  for tool in $FAKED_TOOLS; do plain_fake "$tool" 0 "$1"; done
  plain_fake aws 127 "$1"
  plain_fake launchctl 113 "$1"
  plain_fake sudo 1 "$1"
}
stand_ins_in "$HOME"
fakes_first() { # [home]: with HOME as <home> (the run's, by default)
  local tool path
  for tool in $FAKED_TOOLS; do
    path="$(HOME="${1:-$HOME}" bash -c 'source "$1/lib/common.sh"; command -v "$2"' _ "$ROOT" "$tool")"
    case "$path" in
      "$WORK"/*) ;;
      *)
        echo "tests/smoke.sh: $tool would be ${path:-not found} with HOME ${1:-$HOME}, not a stand-in under $WORK: refusing to go on" >&2
        exit 1
        ;;
    esac
  done
}
fakes_first
FAKES_READY=1

# Python's syntax is ruff's and mypy's (the commit hook and CI's lint), and the console's JavaScript is the js-check
# hook's; bash -n here is CI's one parse of the scripts by macOS's /bin/bash 3.2 (tools/bash32_hook.sh skips on Linux).
section syntax "syntax" covers 'lib/**,libexec/**,install.sh,uninstall.sh,get.sh'
for script in "$ROOT"/bin/bosun "$ROOT"/libexec/bosun-* "$ROOT"/install.sh "$ROOT"/uninstall.sh "$ROOT"/lib/*.sh; do
  head -1 "$script" | grep -q python && continue
  check "bash -n $(basename "$script")" bash -n "$script"
done

# Each command's --help, and each usage error below, is a process of its own: they run side by side, and each says
# what it found in a file of its own, read in order.
check "every command's --help prints its usage header, reads no config and runs no code" bash -c '
  source "$1/lib/common.sh"
  cfg="$(mktemp -d)"
  echo "exit 7" >"$cfg/bosun.env"
  help_of() { # <name>: says what is wrong with its --help, if anything
    local name="$1" want out
    want="$(usage_of "$2/libexec/bosun-$name")"
    out="$(BOSUN_CONFIG_DIR="$cfg" "$2/bin/bosun" "$name" --help 2>&1)" || { echo "bosun $name --help: exit $?"; return; }
    [ "$out" = "$want" ] || { echo "bosun $name --help is not its header: $(head -1 <<<"$out")"; return; }
    grep -qi "usage:" <<<"$(head -3 <<<"$out")" || { echo "bosun $name: no usage line: $(head -1 <<<"$out")"; return; }
    ! grep -qE "^(set -uo|source |import )" <<<"$out" || { echo "bosun $name prints code"; return; }
    [ "$(BOSUN_CONFIG_DIR="$cfg" "$2/bin/bosun" "$name" -h 2>&1)" = "$want" ] || echo "bosun $name -h differs"
  }
  for cmd in "$1"/libexec/bosun-*; do
    name="${cmd##*/bosun-}"
    help_of "$name" "$1" >"$cfg/$name.found" &
  done
  wait
  found="$(cat "$cfg"/*.found)"
  [ -z "$found" ] || { echo "$found"; exit 1; }' _ "$ROOT"
check "the installers' --help prints its usage header and no code" bash -c '
  for cmd in "$1"/install.sh "$1"/uninstall.sh; do
    out="$("$cmd" --help 2>&1)" || { echo "$cmd: exit $?"; exit 1; }
    grep -qi "usage:" <<<"$(head -3 <<<"$out")" || { echo "$cmd: $(head -1 <<<"$out")"; exit 1; }
    ! grep -qE "^(set -uo|source |import )" <<<"$out" || { echo "$cmd prints code"; exit 1; }
  done' _ "$ROOT"
check "usage errors exit 2" bash -c '
  found="$(mktemp -d)"
  n=0
  for args in "send" "send --bogus" "send window-only" "send --id" "send --no-item window-only" "send --new w" "send --start" "start --bogus" "launch --bogus" "launch --fingerprint alpha extra" "refresh --bogus" "refresh --update-restart" "refresh --update-restart prompt alpha --unless-active" "refresh --checkin-loop alpha" "refresh --checkin-loop --unless-active" "trust --bogus" "watchdog --bogus" "watchdog --only" "sessions --bogus" "learn --bogus" "learn apply" "events --bogus" "slack-watch --bogus" "review-watch --bogus" "console --bogus" "quit" "quit --bogus" "quit --plan --sleep" "stop --bogus" "orphans --bogus" "update --bogus" "update note" "update note --window w --mood" "update advance --bogus"; do
    n=$((n + 1))
    # shellcheck disable=SC2086  # split the argument list on purpose
    { "$1" $args >/dev/null 2>&1 </dev/null; [ $? -eq 2 ] || echo "bosun $args did not exit 2"; } >"$found/$n" &
  done
  wait
  out="$(cat "$found"/*)"
  rm -rf "$found"
  [ -z "$out" ] || { echo "$out"; exit 1; }' _ "$BOSUN"

always_run
# unit_tests: the unit tests as they have always run, with none of this run's BOSUN_* settings, but in a home of
# its own (stand-ins in it too) and never the real one.
unit_tests() {
  local var
  for var in $(compgen -e | grep '^BOSUN_'); do unset "$var"; done
  stand_ins_in "$WORK/unit-home"
  fakes_first "$WORK/unit-home"
  HOME="$WORK/unit-home" python3 -m unittest discover -s "$ROOT/tests/unit"
}
if [ "$WITH_UNIT" = 1 ]; then
  echo "python unit tests (tests/unit)"
  unit_out="$(unit_tests 2>&1)"
  unit_end="$(tail -1 <<<"$unit_out")"
  # A plain python3 has no MkDocs, so the docs site's whole-build tests skip here (they run under uv);
  # the label says how many skipped, so a new skip shows.
  unit_ok='^OK( \(skipped=[0-9]+\))?$'
  check "$(grep -o 'Ran [0-9]* tests' <<<"$unit_out" || echo 'unit tests'), all passing$(grep -o ' (skipped=[0-9]*)' <<<"$unit_end")" grep -qE "$unit_ok" <<<"$unit_end"
  grep -qE "$unit_ok" <<<"$unit_end" || tail -25 <<<"$unit_out"
fi

echo "bosun-was-updated:check-in" >"$WORK/update-prompt" # the prompt of an update's restart (refresh --update-restart)
# Helpers (and what is a constant) that more than one section uses, so that a section can run without the one
# that first needed them. (What a section leaves behind besides is for `needs`.)
MINTED='[0-9a-hjkmnp-tv-z]{6}' # six Crockford base32 characters, minted from the replica and the journal
# journal_rebuilds <state dir>: ledger.json is the rebuild of its journal (docs/design/sync.md),
# with no conflict, at the journal's position with the digest of what it holds, and each journal
# file is numbered 1..n with no gap.
journal_rebuilds() {
  python3 - "$ROOT/lib" "$1" <<'EOF'
import fcntl, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import change_log, ledger, ledger_changes
from journal import Journal
state = Path(sys.argv[2])
with (state / "ledger.lock").open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_SH)
    saved = json.loads((state / "ledger.json").read_text())
    found = Journal(state / "journal" / "ledger", "ledger", "", ledger.RULES_VERSION)
    changes, position = found.changes(), found.position()
result = ledger_changes.rebuild(changes, ledger.RULES)
seqs = {}
for change in changes:
    seqs.setdefault("local" if change.get("local") else change["replica"], []).append(change["seq"])
problems = [
    *(["ledger.json is not at the journal's position"] if ledger.saved_position(saved) != position else []),
    *([] if saved["journal"].get(change_log.DIGEST) == ledger_changes.state_hash(saved) else ["ledger.json's digest is wrong"]),
    *(f"conflict: {conflict}" for conflict in result.conflicts),
    *([] if ledger_changes.state_hash(result.ledger) == ledger_changes.state_hash(saved) else ["the rebuild differs from ledger.json"]),
    *(f"{name}: seq {sorted(found)} has a gap" for name, found in seqs.items() if sorted(found) != list(range(1, len(found) + 1))),
]
print("\n".join(problems))
sys.exit(1 if problems or not changes else 0)
EOF
}
# The guard comes from the launch, naming the bosun that launched the window: this checkout's, never the
# installed one in ~/.local/bin, and not from the settings file.
runs_this_guard() { # <settings JSON>: its PreToolUse hooks run this checkout's `bosun guard`, and no other bosun's
  jq -e --arg guard "$(printf '%q' "$ROOT/bin/bosun") guard" '[.hooks.PreToolUse[].hooks[].command] as $c
    | ($c | index($guard)) and ($c | map(select(test("local/bin/bosun"))) | length == 0)' <<<"$1" >/dev/null ||
    { echo "$1" | jq -c .hooks.PreToolUse && return 1; }
}
lock_taken() { # <lock file>: someone holds it (exit 75, busy), not merely that the probe failed
  python3 "$ROOT/lib/run_locked.py" "$1" 0 probe -- true 2>/dev/null
  [ $? -eq 75 ]
}
# hold_lock <lock file> <name>: a process holds the lock (taken as bosun's commands take it: run_locked.py, the flock
# update_state and one_at_a_time take too) until release_lock <name>, never for a fixed time: a check that something
# waits for the lock, or gives up on it, cannot race the holder. It returns once this holder has the lock (its
# `.held` file, written under the lock), and fails the run's check if it never gets it. (The holder lets go by itself
# after 5 minutes, only so that a run cut short leaves none.)
hold_lock() {
  local release="$WORK/hold-lock.$2"
  : >"$release"
  rm -f "$release.held"
  python3 "$ROOT/lib/run_locked.py" "$1" 30 busy -- bash -c ': >"$1.held"
    for _ in $(seq 1 3000); do [ -e "$1" ] || exit 0; sleep 0.1; done' _ "$release" &
  echo $! >"$release.pid"
  bg_started $!
  wait_for 30 test -e "$release.held" || fail_check "hold_lock: $1 not taken (another holder, or none started)"
}
release_lock() { # <name>: the holder hold_lock started lets go, and is gone
  local release="$WORK/hold-lock.$1"
  rm -f "$release" "$release.held"
  bg_done "$(cat "$release.pid")"
  rm -f "$release.pid"
}
fake_claude() { # <window> <status> <version> <seconds-in-status> [name]; runs `sleep` in the pane as "Claude"
  local window_id pane_pid pid
  read -r pane_pid window_id <<<"$(tmux -L "$SOCKET" list-windows -t "=smoke" -F '#{window_name}	#{pane_pid} #{window_id}' | awk -F'\t' -v w="$1" '$1 == w { print $2 }')"
  tmux -L "$SOCKET" send-keys -t "$window_id" "sleep 600" Enter
  for _ in $(seq 1 25); do
    pid="$(pgrep -P "$pane_pid" -x sleep)" && break
    sleep 0.2
  done
  [ -n "$pid" ] || {
    echo "fake_claude: no sleep started in $1" >&2
    return 1
  }
  printf '{"pid":%d,"status":"%s","version":"%s","statusUpdatedAt":%d,"name":"%s"}' \
    "$pid" "$2" "$3" $((($(date +%s) - $4) * 1000)) "${5:-$1}" >"$HOME/.claude/sessions/$pid.json"
  echo "$pid"
}
KG_DIR="$WORK/kg-config/knowledge"
KG_FIXTURE="$ROOT/tests/fixtures/knowledge/acme-import.jsonl"
# A config dir of its own, so doctor and the rest of the suite see no knowledge base.
kg() { BOSUN_CONFIG_DIR="$WORK/kg-config" "$BOSUN" kg "$@"; }
probes() { cat "$WORK/login-probes.log" 2>/dev/null | wc -l | tr -d ' '; }
write_quit() { # <paused|running>: the file `bosun quit` writes
  python3 -I -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import quit_state; quit_state.write(Path(sys.argv[2]), sys.argv[3] == "paused")' "$ROOT/lib" "$BOSUN_STATE_DIR" "$1"
}
status_has() { # <python condition over d, the object bosun status --json printed>
  "$BOSUN" status --json </dev/null | python3 -I -c 'import json, sys; d = json.load(sys.stdin); sys.exit(not eval(sys.argv[1]))' "$1"
}
# What status says of a session that is not there depends on the awake window, which is the watchdog's gate: outside
# it bosun is resting; inside it, once the morning is due and has run, a `bosun stop` made today is "stopped". So
# each check sets its own window instead of meeting whichever one the clock gives: outside is a window two to four
# hours ahead, wherever now is (one that wraps midnight starts later today, so now is before it); inside is the
# whole day, which is only safe away from midnight, where the morning is not yet due.
awake_outside() { BOSUN_DAYS=MTWRFSU BOSUN_START_TIME="$(date -v+3H +%H:%M)" BOSUN_AWAKE_UNTIL="$(date -v+5H +%H:%M)" "$@"; }
awake_all_day() { BOSUN_DAYS=MTWRFSU BOSUN_START_TIME=00:00 BOSUN_AWAKE_UNTIL=23:59 "$@"; }
# auth status says $FAKE_AUTH (in or out); the login check request gets $FAKE_PROBE (ok or auth), and its
# arguments go to login-probe.args.
cat >"$WORK/login-claude" <<FAKE
#!/bin/bash
case "\$1" in
  --version) echo "2.0.0 (Claude Code)" ;;
  auth)
    [ "\${FAKE_AUTH:-in}" = in ] && { echo '{"loggedIn": true, "authMethod": "claude.ai"}'; exit 0; }
    echo '{"loggedIn": false, "authMethod": "none"}'
    exit 1
    ;;
  -p)
    echo probe >>"$WORK/login-probes.log"
    printf '%s\n' "\$@" >"$WORK/login-probe.args"
    if [ "\${FAKE_PROBE:-ok}" = ok ]; then
      echo '{"type": "result", "is_error": false, "result": "OK", "total_cost_usd": 0.001}'
    else
      echo '{"type": "assistant", "error": "authentication_failed", "message": {"content": []}}'
      echo '{"type": "result", "is_error": true, "result": "x", "api_error_status": 401}'
    fi
    ;;
esac
FAKE
chmod +x "$WORK/login-claude"
# ensure_watchdog_state: a run of the watchdog leaves its state file (past a quit, which it skips on), and a section
# that copies it needs it there.
ensure_watchdog_state() {
  local quit="$BOSUN_STATE_DIR/quit.json"
  [ ! -e "$BOSUN_STATE_DIR/watchdog.json" ] || return 0
  [ ! -e "$quit" ] || mv "$quit" "$quit.held"
  BOSUN_DAYS=MTWRFSU BOSUN_WATCHDOG_NOW="$(date -j -f '%Y-%m-%d %H:%M:%S' "$(date +%F) 12:00:00" +%s)" "$BOSUN" watchdog >/dev/null 2>&1
  [ ! -e "$quit.held" ] || mv "$quit.held" "$quit"
}
# launch_settings <launch output>: the --settings JSON its dry-run line hands Claude Code.
launch_settings() {
  local line i
  local -a args
  line="$(grep '^dry-run: would run:' <<<"$1" | tail -1)"
  [ -n "$line" ] || {
    echo "no dry-run line in: $(tail -3 <<<"$1")"
    return 1
  }
  eval "args=(${line#dry-run: would run:})" # printf %q's own quoting, read back
  for ((i = 0; i < ${#args[@]}; i++)); do
    if [ "${args[$i]}" = --settings ]; then printf '%s\n' "${args[$((i + 1))]}"; fi
  done
}
project_dir() { echo "$HOME/.claude/projects/${1//[^A-Za-z0-9-]/-}"; }
make_session() { # dir id title mtime
  local p
  p="$(project_dir "$1")"
  mkdir -p "$p"
  printf '{"type":"user"}\n{"type":"custom-title","customTitle":"%s","sessionId":"%s"}\n' "$3" "$2" >"$p/$2.jsonl"
  touch -t "$4" "$p/$2.jsonl"
}
make_session "$REPO_A" 11111111-0000-0000-0000-000000000001 alpha 202601010000
make_session "$REPO_A" 11111111-0000-0000-0000-000000000002 alpha 202601020000
make_session "$REPO_A" 11111111-0000-0000-0000-000000000003 alpha-extended 202601030000
make_session "$REPO_B" 22222222-0000-0000-0000-000000000001 beta 202601010000

cat >"$BOSUN_CONFIG_DIR/sessions.yml" <<EOF
sessions:
  - name: alpha
    dir: $REPO_A
  - name: beta
    dir: $REPO_B
  - name: gamma
    dir: $REPO_B
    model: sonnet
    effort: high
    autocompact: 500000
EOF

section resolve "resolve" covers 'libexec/bosun-resolve'
# The lookup's rules (prefixes, unreadable transcripts, idle and reused pids) are tests/unit/test_claude_state.py's,
# which loads this command; here, its dispatch through bin/bosun, this run's real pid and the exit code.
check "picks most recent exact match" test "$("$BOSUN" resolve "$REPO_A" alpha)" = 11111111-0000-0000-0000-000000000002
check "--live finds a running holder" test "$("$BOSUN" resolve --live 22222222-0000-0000-0000-000000000001)" = "$$"
check "unknown names exit 3, distinct from failures" bash -c '"$1" resolve "$2" gamma 2>/dev/null; [ $? -eq 3 ]' _ "$BOSUN" "$REPO_B"

section launch-dry-run "launch (dry run)" covers 'libexec/bosun-launch,libexec/bosun-hook,libexec/bosun-status,libexec/bosun-send'
# alpha's and gamma's launches with the defaults, each made once and read by every check of what it holds.
alpha_default="$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)"
out="$alpha_default"
check "resumes by UUID with name, remote control and drain prompt" contains "$out" "--name alpha --remote-control alpha --resume 11111111-0000-0000-0000-000000000002"
check "the prompt follows --, so a leading dash is not an option" contains "$out" "--resume 11111111-0000-0000-0000-000000000002 -- Bosun\\ resumed"
check "sessions start with the default model" contains "$out" "--model opus"
check "... and the default effort, as a session setting /effort can still change" contains "$out" 'effortLevel\":\"medium'
check "a dry-run launch records what the window was launched with: bosun's commit and a fingerprint" bash -c '
  jq -e --arg commit "$(git -C "$2" rev-parse HEAD)" ".window == \"alpha\" and .commit == \$commit and .harness == \"claude\"
    and (.fingerprint.args | test(\"^[0-9a-f]{64}$\")) and (.fingerprint.files | test(\"^[0-9a-f]{64}$\"))" "$1/launches/smoke/alpha.json"' _ "$BOSUN_STATE_DIR" "$ROOT"
check "launch --fingerprint prints the same fingerprint, from any directory" bash -c '
  [ "$(cd / && "$1" launch --fingerprint alpha | jq -cS .)" = "$(jq -cS .fingerprint "$2/launches/smoke/alpha.json")" ]' _ "$BOSUN" "$BOSUN_STATE_DIR"
resumed_fingerprint="$(jq -cS .fingerprint "$BOSUN_STATE_DIR/launches/smoke/alpha.json")"
# A fresh conversation, as refresh starts one: none by its name in the window's folder. In that same folder,
# since the folder is a launch input (the worktrees folder beside it).
mkdir -p "$BOSUN_STATE_DIR/conversations"
echo alpha-2026-01-05-1 >"$BOSUN_STATE_DIR/conversations/alpha"
out="$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)"
rm "$BOSUN_STATE_DIR/conversations/alpha"
check "a fresh launch records the same fingerprint as a resumed one" bash -c '
  grep -q "starting a new one" <<<"$1" && [ "$(jq -cS .fingerprint "$2/launches/smoke/alpha.json")" = "$3" ]' _ "$out" "$BOSUN_STATE_DIR" "$resumed_fingerprint"
printf '#!/bin/sh\ntouch "%s"\n' "$WORK/fingerprint-started-claude" >"$WORK/marker-claude"
chmod +x "$WORK/marker-claude"
out="$(BOSUN_LAUNCH_DRY_RUN=0 BOSUN_CLAUDE_BIN="$WORK/marker-claude" BOSUN_STATE_DIR="$WORK/fingerprint-state" "$BOSUN" launch --fingerprint alpha 2>&1)"
check "... and starts nothing, records nothing" bash -c '[ ! -e "$1/fingerprint-started-claude" ] && [ ! -e "$1/fingerprint-state/launches" ] && jq -e .args <<<"$2"' _ "$WORK" "$out"
# A repo window's worktrees folder, beside its git repository: made by a real launch only, of an agent that says
# whether the folder was there when it started.
WT_STATE="$WORK/worktrees-state"
WT_REPO="$WORK/worktrees/exporter"
WT_THERE="$WORK/worktrees/billing"
for repo in "$WT_REPO" "$WT_THERE"; do git init -q "$repo"; done
mkdir -p "$WT_THERE.worktrees"
WT_REPO="$(cd "$WT_REPO" && pwd -P)"
WT_THERE="$(cd "$WT_THERE" && pwd -P)"
printf '#!/bin/sh\ntouch "%s"\n[ -d "$PWD.worktrees" ] && touch "%s"\nexit 0\n' "$WORK/worktrees-agent-ran" "$WORK/worktrees-agent-saw-it" >"$WORK/worktrees-claude"
chmod +x "$WORK/worktrees-claude"
wt_launch() { # <folder> <launch's arguments...>
  local dir="$1"
  shift
  (cd "$dir" && BOSUN_LAUNCH_DRY_RUN=0 BOSUN_CLAUDE_BIN="$WORK/worktrees-claude" BOSUN_STATE_DIR="$WT_STATE" BOSUN_LOG_DIR="$WT_STATE/logs" "$BOSUN" launch "$@" 2>&1)
}
printf 'sessions:\n  - name: wt\n    dir: %s\n' "$WT_REPO" >"$WORK/worktrees-sessions.yml"
(cd "$WT_REPO" && BOSUN_STATE_DIR="$WT_STATE" BOSUN_LOG_DIR="$WT_STATE/logs" "$BOSUN" launch alpha >/dev/null 2>&1)
BOSUN_SESSIONS_FILE="$WORK/worktrees-sessions.yml" wt_launch "$WORK" --fingerprint wt >/dev/null
check "neither a dry run nor launch --fingerprint makes a repo window's worktrees folder" bash -c '[ ! -e "$1.worktrees" ] && [ ! -e "$2" ]' _ "$WT_REPO" "$WORK/worktrees-agent-ran"
wt_launch "$WT_REPO" alpha >/dev/null
check "a real launch of a repo window makes its worktrees folder before the agent starts" bash -c '[ -d "$1.worktrees" ] && [ -e "$2" ]' _ "$WT_REPO" "$WORK/worktrees-agent-saw-it"
check "... and puts it on record for uninstall" jq -e --arg d "$WT_REPO.worktrees" '.folders == [$d]' "$WT_STATE/agent-changes.json"
wt_launch "$WT_REPO" alpha >/dev/null
wt_launch "$WT_THERE" alpha >/dev/null
check "... once, and never one that was there before" jq -e --arg d "$WT_REPO.worktrees" '.folders == [$d]' "$WT_STATE/agent-changes.json"
rm -f "$WORK/worktrees-agent-ran"
rmdir "$WT_THERE.worktrees"
printf 'mine\n' >"$WT_THERE.worktrees"
out="$(wt_launch "$WT_THERE" alpha)"
check "one that cannot be made is a line, and the window starts anyway" bash -c '
  grep -q "warning: .alpha.: $2.worktrees is there, but is not a folder, so the window starts without it" <<<"$1" && [ -e "$3" ]' _ "$out" "$WT_THERE" "$WORK/worktrees-agent-ran"
gamma_default="$(cd "$REPO_B" && "$BOSUN" launch gamma 2>&1)"
check "a sessions.yml entry overrides model and effort" bash -c 'grep -q -- "--model sonnet" <<<"$1" && grep -q "effortLevel\\\\\":\\\\\"high" <<<"$1"' _ "$gamma_default"
check "... and its own auto-compact window" contains "$gamma_default" "--autocompact 500000"
check "starts a new named session when none exists" contains "$gamma_default" "starting a new one"
out="$(cd "$REPO_B" && "$BOSUN" launch --model haiku --effort low gamma 2>&1)"
check "launch --model/--effort override the entry" bash -c 'grep -q -- "--model haiku" <<<"$1" && grep -q "effortLevel\\\\\":\\\\\"low" <<<"$1"' _ "$out"
# Three settings left empty are independent of each other: one launch shows each one's default.
out="$(cd "$REPO_A" && BOSUN_MODEL='' BOSUN_EFFORT='' BOSUN_AUTOCOMPACT_TOKENS=0 "$BOSUN" launch alpha 2>&1)"
check "empty settings leave Claude Code's defaults" bash -c 'grep -q "dry-run: would run" <<<"$1" && ! grep -qE -- "--model|effortLevel" <<<"$1"' _ "$out"
check "an empty auto-compact setting leaves Claude Code's default" bash -c 'grep -q "dry-run: would run" <<<"$1" && ! grep -q -- "--autocompact" <<<"$1"' _ "$out"
check "an unknown effort is refused" fails_with "effort must be one of" "$BOSUN" launch --effort huge alpha
check "repo sessions launch with the default auto-compact window" contains "$alpha_default" "--autocompact 300000 --name alpha"
check "an auto-compact window out of range is refused" fails_with "must be auto or 100000-1000000" env BOSUN_AUTOCOMPACT_TOKENS=5000 "$BOSUN" launch alpha
check "repo sessions get the compaction hook" bash -c 'grep -q "SessionStart" <<<"$1" && grep -qE "compact-context.{0,2} --window.{0,2} alpha" <<<"$1"' _ "$alpha_default"
# The ontology's standing rules (orchestrator/knowledge.md), for a window on an agent whose quotes bosun can check.
# Off, the launch is what it was; the launch record's fingerprint tells the two apart (the policy's text itself is not in it).
off_fingerprint="$(jq -r .args <<<"$resumed_fingerprint")" # alpha's first launch, with the defaults
out_on="$(cd "$REPO_A" && BOSUN_KNOWLEDGE_ENABLED=1 "$BOSUN" launch alpha 2>&1)"
check "with the ontology on, a repo window's launch carries its rules, after the model and effort" grep -qE -- '--model opus --settings .*--append-system-prompt .*Look.before.asking.*--autocompact 300000 --name alpha' <<<"$out_on"
check "... recorded as a launch with a policy argument, so a window launched before is seen to differ" bash -c '
  [ "$(jq -r .fingerprint.args "$1/launches/smoke/alpha.json")" != "$2" ]' _ "$BOSUN_STATE_DIR" "$off_fingerprint"
out_off="$(cd "$REPO_A" && BOSUN_KNOWLEDGE_ENABLED=0 "$BOSUN" launch alpha 2>&1)"
check "with it off, a repo window launches with no system prompt, as before" bash -c '
  ! grep -q -- "--append-system-prompt" <<<"$1" && ! grep -q "Look.before.asking" <<<"$1"' _ "$out_off"
check "... and records the fingerprint it had" test "$(jq -r .fingerprint.args "$BOSUN_STATE_DIR/launches/smoke/alpha.json")" = "$off_fingerprint"
policy_off="$WORK/policy-ontology-off.txt"
policy_on="$WORK/policy-ontology-on.txt"
bash -c 'source "$1/lib/common.sh"; BOSUN_CONFIG_DIR="$2"; load_config; orchestrator_policy' _ "$ROOT" "$WORK/no-site-config" >"$policy_off"
BOSUN_KNOWLEDGE_ENABLED=1 bash -c 'source "$1/lib/common.sh"; BOSUN_CONFIG_DIR="$2"; load_config; orchestrator_policy' _ "$ROOT" "$WORK/no-site-config" >"$policy_on"
check "with the ontology off, the orchestrator's policy is policy.md and nothing else" cmp -s "$policy_off" "$ROOT/orchestrator/policy.md"
check "... and on, it ends with the ontology's rules" bash -c '
  head -c "$(wc -c <"$2")" "$1" | cmp -s - "$2" && tail -c "$(wc -c <"$3")" "$1" | cmp -s - "$3" && grep -q "^# The ontology$" "$1"' _ \
  "$policy_on" "$policy_off" "$ROOT/orchestrator/knowledge.md"
# Both hooks off in one launch: each flag covers hooks of its own.
hooks_off="$(cd "$REPO_A" && BOSUN_COMPACT_CONTEXT=0 BOSUN_HOOK_SIGNALS=0 "$BOSUN" launch alpha 2>&1)"
check "with events on, repo sessions launch with the prompt hook" contains "$(cd "$REPO_A" && BOSUN_EVENTS_ENABLED=1 "$BOSUN" launch alpha 2>&1)" 'hook\ user-prompt\ --window\ alpha'
check "... and without, they do not" bash -c '! grep -q "user-prompt" <<<"$1"' _ "$alpha_default"
check "BOSUN_COMPACT_CONTEXT=0 leaves the hook out" bash -c 'grep -q "dry-run: would run" <<<"$1" && ! grep -q "compact-context" <<<"$1"' _ "$hooks_off"
out="$(printf '{"hook_event_name":"SessionStart","source":"compact"}' | "$BOSUN" hook compact-context --window alpha)"
check "the compaction hook adds the window's items as SessionStart context" bash -c 'jq -e ".hookSpecificOutput.hookEventName == \"SessionStart\" and (.hookSpecificOutput.additionalContext | test(\"compacted\"))" <<<"$1" >/dev/null' _ "$out"
check "the compaction hook adds nothing when it is off" test -z "$(echo '{}' | BOSUN_COMPACT_CONTEXT=0 "$BOSUN" hook compact-context --window alpha)"
check "the compaction hook never fails the session start" bash -c 'echo "{}" | BOSUN_STATE_DIR=/nonexistent/x "$1" hook compact-context --window alpha' _ "$BOSUN"
check "repo sessions get the async signal hooks" bash -c 'grep -qE "hook.{0,2} signal.{0,2} --window.{0,2} alpha" <<<"$1" && grep -q "StopFailure" <<<"$1" && grep -q "PermissionRequest" <<<"$1" && grep -qE "async.{0,4}true" <<<"$1"' _ "$alpha_default"
check "BOSUN_HOOK_SIGNALS=0 leaves the signal hooks out" bash -c 'grep -q "dry-run: would run" <<<"$1" && ! grep -q "StopFailure" <<<"$1"' _ "$hooks_off"
printf '{"session_id":"s1","hook_event_name":"StopFailure","error":"rate_limit","last_assistant_message":"spend limit"}' | "$BOSUN" hook signal --window alpha
check "the signal hook records the event for the window" bash -c 'jq -e "select(.event == \"StopFailure\" and .kind == \"rate_limit\" and .session == \"s1\")" "$1" >/dev/null' _ "$BOSUN_STATE_DIR/signals/alpha.jsonl"
# Its own log folder: the line it logs is an ERROR, which `bosun status --json` counts for an hour.
check "the signal hook never fails" bash -c 'echo "{broken" | BOSUN_STATE_DIR=/nonexistent/x BOSUN_LOG_DIR="$2" "$1" hook signal --window alpha' _ "$BOSUN" "$WORK/signal-logs"
check "... and says why in the hook log, as a stamped ERROR line that the watchdog's log-errors check alerts" \
  grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} \[hook\] ERROR: signals: could not record' "$WORK/signal-logs/hook-$(date +%F).log"
exits_0_saying_nothing() {
  local out status
  out="$("$@" 2>/dev/null </dev/null)"
  status=$?
  [ "$status" -eq 0 ] && [ -z "$out" ]
}
check "an agent's own hook with a wrong command line exits 0 with nothing on stdout" exits_0_saying_nothing "$BOSUN" hook agent --harness claude --event Ping
check "... says why on stderr" contains "$("$BOSUN" hook agent --harness claude --event Ping 2>&1 >/dev/null </dev/null)" "claude has no hooks of its own"
check "... and in the hook log, as an ERROR line for the watchdog" bash -c 'grep -qE "\[hook\] ERROR: bosun hook agent --harness claude --event Ping exited with status 2" "$1"/hook-*.log' _ "$BOSUN_LOG_DIR"
mkdir "$WORK/no-python"
printf '#!/bin/sh\nexit 2\n' >"$WORK/no-python/python3"
chmod +x "$WORK/no-python/python3"
check "... and when Python cannot run it at all" exits_0_saying_nothing env PATH="$WORK/no-python:$PATH" "$BOSUN" hook agent --harness claude --event Ping
mkdir -p "$WORK/broken-cfg" "$WORK/broken-logs"
printf 'BOSUN_DRAIN_PROMPT=before\nif then fi\n' >"$WORK/broken-cfg/bosun.env"
check "an agent's own hook exits 0 with nothing on stdout even when bosun.env does not parse" exits_0_saying_nothing env BOSUN_CONFIG_DIR="$WORK/broken-cfg" BOSUN_LOG_DIR="$WORK/broken-logs" "$BOSUN" hook agent --harness agy --event Stop
check "... and says in the hook log that bosun.env did not parse" bash -c 'grep -qE "\[hook\] ERROR: bosun.env does not parse, so this hook ran on the defaults" "$1"/hook-*.log' _ "$WORK/broken-logs"
check "any other hook still refuses a bosun.env that does not parse" fails_with "bosun.env does not parse" env BOSUN_CONFIG_DIR="$WORK/broken-cfg" BOSUN_LOG_DIR="$WORK/broken-logs" "$BOSUN" hook signal --window alpha
printf 'BOSUN_DRAIN_PROMPT="$NOT_SET_UNDER_LAUNCHD"\n' >"$WORK/broken-cfg/bosun.env"
check "an agent's own hook exits 0 with nothing on stdout when bosun.env stops before its end when read" exits_0_saying_nothing env BOSUN_CONFIG_DIR="$WORK/broken-cfg" BOSUN_LOG_DIR="$WORK/broken-logs" "$BOSUN" hook agent --harness agy --event Stop
check "... and says so in the hook log" bash -c 'grep -qE "\[hook\] ERROR: bosun.env stops before its end when read, so this hook ran on the defaults: .*NOT_SET_UNDER_LAUNCHD" "$1"/hook-*.log' _ "$WORK/broken-logs"
check "any other command refuses it, naming the variable" fails_with "bosun.env stops before its end when read, so none of it was read:" env BOSUN_CONFIG_DIR="$WORK/broken-cfg" "$BOSUN" status
check "the signal hook records nothing when it is off" bash -c 'echo "{\"session_id\":\"s2\",\"hook_event_name\":\"Stop\"}" | BOSUN_HOOK_SIGNALS=0 "$1" hook signal --window alpha; ! grep -q s2 "$2"' _ "$BOSUN" "$BOSUN_STATE_DIR/signals/alpha.jsonl"
out="$(cd "$REPO_B" && "$BOSUN" launch beta 2>&1)"
check "refuses to resume a live session" contains "$out" "already running as pid $$"

section config-loading "config loading" covers 'libexec/bosun-status,examples/generic/bosun.env'
CFG="$WORK/cfg-test"
mkdir -p "$CFG"
cat >"$WORK/config-child.sh" <<'SH'
source "$1/lib/common.sh"; load_config; printf '%s' "${!2}"
SH
check "a child command re-reads bosun.env after its parent loaded it" env BOSUN_CONFIG_DIR="$CFG" bash -c '
  source "$1/lib/common.sh"; load_config
  echo "BOSUN_DRAIN_PROMPT=new" >"$BOSUN_CONFIG_DIR/bosun.env"
  [ "$(bash "$2" "$1" BOSUN_DRAIN_PROMPT)" = new ]' _ "$ROOT" "$WORK/config-child.sh"
check "environment overrides still beat the file" test "$(BOSUN_CONFIG_DIR="$CFG" BOSUN_DRAIN_PROMPT=from-env bash "$WORK/config-child.sh" "$ROOT" BOSUN_DRAIN_PROMPT)" = from-env
multi_line=$'line one\nline two'
check "multi-line overrides survive" test "$(BOSUN_CONFIG_DIR="$CFG" BOSUN_DRAIN_PROMPT="$multi_line" bash "$WORK/config-child.sh" "$ROOT" BOSUN_DRAIN_PROMPT)" = "$multi_line"
check "load_config exports only environment overrides" env BOSUN_CONFIG_DIR="$CFG" bash -c 'source "$1/lib/common.sh"; load_config; ! env | grep -q "^BOSUN_DRAIN_PROMPT="' _ "$ROOT"
printf 'BOSUN_DRAIN_PROMPT=before\nif then fi\nBOSUN_START_TIME=11:11\n' >"$CFG/bosun.env"
check "a bosun.env that does not parse is refused in bash's own words, none of it read" fails_with "bosun.env does not parse, so none of it was read: $CFG/bosun.env: line 2: syntax error" env BOSUN_CONFIG_DIR="$CFG" "$BOSUN" status
check "... while --help still answers" env BOSUN_CONFIG_DIR="$CFG" "$BOSUN" status --help
rm -f "$CFG/bosun.env"

section common-functions "lib/common.sh functions" covers 'libexec/bosun-launch'
fn() { bash -c 'source "$1/lib/common.sh"; shift; "$@"' _ "$ROOT" "$@"; }
# shift_time's cases from one shell, each its own check.
read -r shifted_on shifted_past shifted_back shifted_zeros <<<"$(bash -c 'source "$1/lib/common.sh"
  echo "$(shift_time 08:00 1) $(shift_time 23:59 2) $(shift_time 00:00 -1) $(shift_time 08:09 1)"' _ "$ROOT")"
check "shift_time adds minutes" test "$shifted_on" = 08:01
log_dir="$WORK/log-checks"
mkdir -p "$log_dir"
log_day="$(date +%F)"
BOSUN_LOG_DIR="$log_dir" BOSUN_COMMAND=probe fn log "hello file" >/dev/null
check "a log line also goes to the day's file of the command that logs it" grep -q "\[probe\] hello file" "$log_dir/probe-$log_day.log"
BOSUN_LOG_DIR="$log_dir" BOSUN_COMMAND=own LOG_TO_FILE=0 fn log "screen only" >/dev/null
check "... but not for a command that keeps its own file (LOG_TO_FILE=0)" test ! -e "$log_dir/own-$log_day.log"
# <command>|<exit status>|<stderr> of a common.sh function that ends the script, its stdout dropped
ends_with() { # <command> <function> <args...>
  local errors status
  errors="$(BOSUN_LOG_DIR="$log_dir" BOSUN_COMMAND="$1" fn "${@:2}" 2>&1 >/dev/null)"
  status=$?
  printf '%s|%s' "$status" "${errors#* "[$1]" }"
}
check "die exits 1 and logs its ERROR line to the file too, for the watchdog" bash -c '
  [ "$1" = "1|ERROR: it broke" ] && grep -q "\[dies\] ERROR: it broke" "$2"' _ "$(ends_with dies die "it broke")" "$log_dir/dies-$log_day.log"
check "refuse exits 1 with the same line on stderr, and none in the file" bash -c '
  [ "$1" = "1|ERROR: no such thing" ] && [ ! -e "$2" ]' _ "$(ends_with refuses refuse "no such thing")" "$log_dir/refuses-$log_day.log"
check "die_usage exits 2 pointing at --help on stderr, and none in the file" bash -c '
  [ "$1" = "2|ERROR: unknown argument (see --help)" ] && [ ! -e "$2" ]' _ "$(ends_with mistyped die_usage "unknown argument")" "$log_dir/mistyped-$log_day.log"
printf 'connectors: probe not found, so this window starts without it\nplugins: the real reason\n\n' >"$WORK/resolver.err"
check "launch_refused says the window does not start, with the module above, exits 4, and keeps the reason the resolver noted" bash -c '
  out="$(LAUNCH_ERROR_FILE="$3" BOSUN_LOG_DIR="$4" bash -c "source \"\$1/lib/common.sh\"; note_reason \"\$2\"; launch_refused w \"it was refused\" lib/x.py" _ "$1" "$2" 2>&1)"
  [ $? = 4 ] && [[ "$out" == *"ERROR: '"'"'w'"'"' does not start: it was refused (lib/x.py, above)" ]] && [ "$(cat "$3")" = "plugins: the real reason" ]' \
  _ "$ROOT" "$WORK/resolver.err" "$WORK/refused.error" "$log_dir"
rm -f "$WORK/refused.error"
check "... and without one, records its own reason, with no window and no \"above\"" bash -c '
  LAUNCH_ERROR_FILE="$2" BOSUN_LOG_DIR="$3" bash -c "source \"\$1/lib/common.sh\"; launch_refused w \"it was refused\" lib/x.py" _ "$1" 2>/dev/null
  [ "$(cat "$2")" = "it was refused (lib/x.py)" ]' _ "$ROOT" "$WORK/refused.error" "$log_dir"
for n in 1 2 3 4 5; do
  BOSUN_LOG_FILE_MAX_BYTES=80 BOSUN_LOG_DIR="$log_dir" BOSUN_COMMAND=capped fn log "line $n of five, long enough to pass the cap" >/dev/null
done
check "a day's file stops at its cap, with one line that says so" bash -c '
  [ "$(grep -c "of five" "$1")" -lt 5 ] && [ "$(grep -c "this log is capped" "$1")" = 1 ]' _ "$log_dir/capped-$log_day.log"
check "a log line still reaches the screen without a log folder" test "$(BOSUN_LOG_DIR="$WORK/no-such-folder" fn log "on screen" | grep -c "on screen")" = 1
check "shift_time wraps past midnight" test "$shifted_past" = 00:01
check "shift_time goes back past midnight" test "$shifted_back" = 23:59
check "shift_time reads leading zeros as decimal" test "$shifted_zeros" = 08:10
check "seconds_until a time already past today is 0" test "$(fn seconds_until 00:00)" = 0
check "the awake window's end at or before the start is the next day" bash -c '[ "$1" -gt 0 ] && [ "$1" -le 86400 ]' _ "$(BOSUN_START_TIME="$(date +%H:%M)" BOSUN_AWAKE_UNTIL="$(date -v-1M +%H:%M)" fn seconds_until_awake_end)"
soon_end="$(date -v+2M +%H:%M)"
if [[ "$soon_end" > "$(date +%H:%M)" ]]; then # not across midnight
  # Later today, not tomorrow: up to two minutes, and less when the minute turns over before fn reads the clock.
  check "... or later today while yesterday's window still runs" bash -c '[ "$1" -gt 0 ] && [ "$1" -le 120 ]' _ "$(BOSUN_START_TIME=23:59 BOSUN_AWAKE_UNTIL="$soon_end" fn seconds_until_awake_end)"
fi
soon="$(date -v+2M +%H:%M)"
if [[ "$soon" > "$(date +%H:%M)" ]]; then # not across midnight
  check "seconds_until a time two minutes ahead" bash -c '[ "$1" -gt 60 ] && [ "$1" -le 120 ]' _ "$(fn seconds_until "$soon")"
fi
check "launchd: every day is one entry without a weekday" bash -c '[ "$(grep -c "<dict>" <<<"$1")" = 1 ] && ! grep -q Weekday <<<"$1"' _ "$(fn launchd_calendar_xml 08:00 MTWRFSU)"
check "launchd: weekdays are Monday 1 to Friday 5" test "$(fn launchd_calendar_xml 08:05 MTWRF | grep -oE 'Weekday</key><integer>[0-9]' | grep -oE '[0-9]$' | tr -d '\n')" = 12345
check "launchd: Sunday is 0 and Saturday 6" test "$(fn launchd_calendar_xml 08:00 SU | grep -oE 'Weekday</key><integer>[0-9]' | grep -oE '[0-9]$' | tr -d '\n')" = 60
slug_dir="$WORK/slug-repo" # one repository, its origin set to each form in turn
git init -q "$slug_dir" && git -C "$slug_dir" remote add origin placeholder
for url in git@github.com:acme/api.git https://github.com/acme/api.git https://github.com/acme/api ssh://git@github.com/acme/api.git; do
  git -C "$slug_dir" remote set-url origin "$url"
  check "repo_slug_of_dir reads $url" test "$(fn repo_slug_of_dir "$slug_dir")" = acme/api
done
check "minutes since the latest client keystroke" test "$(bash -c 'source "$1/lib/common.sh"; tmux_cmd() { echo $(( $(date +%s) - 600 )); echo $(( $(date +%s) - 125 )); }; minutes_since_client_input' _ "$ROOT")" = 2
check "no attached client means no answer" bash -c 'source "$1/lib/common.sh"; tmux_cmd() { :; }; ! minutes_since_client_input' _ "$ROOT"
check "only idle, busy and shell sessions take input" bash -c 'source "$1/lib/common.sh"; agent_takes_input idle && agent_takes_input busy && agent_takes_input shell && ! agent_takes_input waiting && ! agent_takes_input ""' _ "$ROOT"
# (A command that runs past its timeout ends killed, its status 143 (TERM); one left to run would end 0. The sleep's
# length is only how long a broken timeout would hang the run.)
check "run_with_timeout stops a slow command" bash -c 'source "$1/lib/common.sh"; run_with_timeout 1 sleep 30; [ $? -eq 143 ]' _ "$ROOT"
check "run_with_timeout passes a fast command's status" bash -c 'source "$1/lib/common.sh"; run_with_timeout 5 true && ! run_with_timeout 5 false' _ "$ROOT"
# The run's own end (stop_background) stops only what is still its own: a command a check left running in the
# background, never a pid waited for (bg_done drops it) nor one whose process this run did not start (here a loop
# whose parent ended at once, which writes down the signals it gets: the test's USR1, sent after, comes first if no
# TERM came). Each ends on its own after 30 s of polls, exit 7, should nothing stop it.
bash -c 'for _ in $(seq 1 300); do sleep 0.1; done; exit 7' &
bg_own=$!
true &
bg_waited=$!
bg_started "$bg_waited"
bg_done "$bg_waited"
# The other process writes down every TERM and USR1 it gets and goes on (USR1, the test's own, ends it once both
# traps have run), so neither the order the shell runs pending traps in nor a TERM arriving first can hide one; it
# says it is ready once its traps are set, so the test signals it only then.
rm -f "$WORK/bg-other.signals" "$WORK/bg-other.ready"
bg_other="$(sh -c '(trap "echo TERM >>\"\$1\"" TERM; trap "echo USR1 >>\"\$1\"; got=1" USR1; got=""
  : >"$2"; for _ in $(seq 1 300); do [ -z "$got" ] || exit 0; sleep 0.1; done; exit 7) >/dev/null 2>&1 & echo $!' \
  _ "$WORK/bg-other.signals" "$WORK/bg-other.ready")"
wait_for 30 test -e "$WORK/bg-other.ready"
stops_only_its_own() { # <own pid> <other pid>: each is told for what it is, then stop_background goes over both
  own_child "$1" && ! own_child "$2" || return 1
  BG_PIDS=" $2 $1"
  stop_background
}
check "bg_done drops the pid it waited for" bash -c 'case "$1 " in *" $2 "*) exit 1 ;; esac' _ "$BG_PIDS" "$bg_waited"
check "the run's end tells a background command of its own from a pid that names another's process" \
  stops_only_its_own "$bg_own" "$bg_other"
wait "$bg_own" 2>/dev/null # (bash would say "Terminated")
check "... stops its own (TERM: 143), which would have ended 7 on its own" test $? -eq 143
kill -USR1 "$bg_other"
wait_for 30 grep -qx USR1 "$WORK/bg-other.signals"
check "... and sends nothing to the other: it got only the test's signal, sent after" \
  bash -c '! grep -qx TERM "$1"' _ "$WORK/bg-other.signals"
fnc() { bash -c 'source "$1/lib/common.sh"; load_config; shift; "$@"' _ "$ROOT" "$@"; } # with the config's defaults
check "harness_bin names the command the config gives each agent" test "$(fnc harness_bin codex)" = codex
check "... and an override of it" test "$(BOSUN_CODEX_BIN=/opt/acme/codex fnc harness_bin codex)" = /opt/acme/codex
check "harness_bin names agy's command, and an override of it" test "$(fnc harness_bin agy) $(BOSUN_AGY_BIN=/opt/acme/agy fnc harness_bin agy)" = "agy /opt/acme/agy"
check "harness_default_model: agy has none unless configured, and BOSUN_AGY_MODEL is its" test "$(fnc harness_default_model agy) $(BOSUN_AGY_MODEL=gemini-acme fnc harness_default_model agy)" = "- gemini-acme"
check "harness_default_model: Codex has none unless configured" test "$(fnc harness_default_model codex)" = -
check "... or the one in BOSUN_CODEX_MODEL" test "$(BOSUN_CODEX_MODEL=gpt-acme fnc harness_default_model codex)" = gpt-acme
check "... and Claude Code's is BOSUN_MODEL's" test "$(BOSUN_MODEL=acme-model fnc harness_default_model claude)" = acme-model
check "... or - when that is empty" test "$(BOSUN_MODEL="" fnc harness_default_model claude)" = -
check "harness_bin of an agent with no profile exits 1 and prints nothing" bash -c 'source "$1/lib/common.sh"; load_config; out="$(harness_bin acme-agent 2>&1)" && exit 1; [ -z "$out" ]' _ "$ROOT"
check "... as does harness_default_model" bash -c 'source "$1/lib/common.sh"; load_config; out="$(harness_default_model acme-agent 2>&1)" && exit 1; [ -z "$out" ]' _ "$ROOT"
check "known_harnesses lists the agents in the order of AGENTS" test "$(fn known_harnesses)" = "claude codex agy"
check "launchd_paths gives a line per tool, - for one launchd's PATH does not find, in the middle and at the end" \
  test "$(fn launchd_paths jq acme-missing-tool tmux acme-missing-last | sed 's#^/.*/##' | paste -sd' ' -)" = "jq - tmux -"
check "profile_field reads a column by its name" test "$(fn profile_field codex flags_key)" = BOSUN_CODEX_FLAGS
check "find_window finds an early match in a long listing, exit 0 under pipefail" bash -c 'set -o pipefail; source "$1/lib/common.sh"; tmux_cmd() { printf "w1\t42 @1\n"; awk "BEGIN { for (i = 0; i < 300000; i++) print \"x\t1 @2\" }"; }; out="$(find_window w1)"; rc=$?; [ "$out" = "42 @1" ] && [ "$rc" = 0 ]' _ "$ROOT"
check "window_harness finds an early match in a long window list, as it does under pipefail" bash -c 'set -o pipefail; source "$1/lib/common.sh"; load_config; window_specs() { printf "w1\t\t\t\t\tcodex\n"; awk "BEGIN { for (i = 0; i < 300000; i++) print \"x\" }"; }; out="$(window_harness w1)"; rc=$?; [ "$out" = codex ] && [ "$rc" = 0 ]' _ "$ROOT"
# The profiles are a table the shell reads (lib/agent_profiles.tsv), so what can fail is the table: missing or empty
# stops the command, saying so. BOSUN_ROOT is set after common.sh, so only the table's folder moves.
mkdir -p "$WORK/no-profiles/lib" "$WORK/empty-profiles/lib"
: >"$WORK/empty-profiles/lib/agent_profiles.tsv"
for table_root in no-profiles empty-profiles; do
  check "profiles that cannot be read stop the command, saying so ($table_root)" fails_with "could not read the agents' profiles: $WORK/$table_root/lib/agent_profiles.tsv is missing" bash -c 'source "$1/lib/common.sh"; BOSUN_ROOT="$2"; harness_bin claude' _ "$ROOT" "$WORK/$table_root"
done
mkdir -p "$WORK/broken-python"
printf '#!/bin/sh\necho "python is broken" >&2\nexit 1\n' >"$WORK/broken-python/python3"
chmod +x "$WORK/broken-python/python3"
check "reading the profiles starts no python3: a broken one leaves them alone" test "$(bash -c 'source "$1/lib/common.sh"; PATH="$2:$PATH"; known_harnesses' _ "$ROOT" "$WORK/broken-python" 2>&1)" = "claude codex agy"
for shell_bin in /bin/bash bash; do
  check "... and agent_bin_env works under $shell_bin" test "$(BOSUN_CODEX_BIN=acme-codex "$shell_bin" -c 'source "$1/lib/common.sh"; load_config; PATH="$2:$PATH"; agent_bin_env; printf "%s\n" "${agent_bin_assignments[@]}"' _ "$ROOT" "$WORK/broken-python" 2>/dev/null | tr '\n' ' ')" = "BOSUN_CLAUDE_BIN=claude BOSUN_CODEX_BIN=acme-codex BOSUN_AGY_BIN=agy "
done
cat >"$WORK/specs-agents.yml" <<YML
sessions:
  - name: cx-window
    dir: $REPO_A
    harness: codex
  - name: cl-window
    dir: $REPO_A
    harness: claude
  - name: cl-pinned
    dir: $REPO_A
    harness: claude
    model: acme-pinned
YML
agent_specs() { BOSUN_SESSIONS_FILE="$WORK/specs-agents.yml" fnc window_specs; }
spec_model() { awk -F'\t' -v name="$1" '$1 == name { print $4 }'; } # <window>: its model, from window_specs on stdin
check "window_specs gives a Codex window BOSUN_CODEX_MODEL" test "$(BOSUN_CODEX_MODEL=gpt-acme agent_specs | spec_model cx-window)" = gpt-acme
check "... or - when that is empty" test "$(BOSUN_CODEX_MODEL="" agent_specs | spec_model cx-window)" = -
check "... a Claude Code window BOSUN_MODEL" test "$(BOSUN_MODEL=acme-model agent_specs | spec_model cl-window)" = acme-model
check "... and a window's own model beats the default" test "$(BOSUN_MODEL=acme-model agent_specs | spec_model cl-pinned)" = acme-pinned
cat >"$WORK/ghost-sessions.yml" <<YML
sessions:
  - name: ghost-agent
    dir: $REPO_A
    harness: acme-agent
YML
check "a window's model reads right through a tab-separated read, in bash 3.2 too" test "$(BOSUN_SESSIONS_FILE="$WORK/specs-agents.yml" BOSUN_MODEL=acme-model /bin/bash -c 'source "$1/lib/common.sh"; load_config; need_profiles; IFS=$'"'"'\t'"'"' read -r model effort <<<"$(window_model_effort cl-window)"; echo "$model"' _ "$ROOT")" = acme-model
check "bosun launch refuses a window whose agent has no profile" fails_with "no agent 'acme-agent': bosun knows claude, codex" bash -c 'cd "$1" && BOSUN_SESSIONS_FILE="$2" BOSUN_LAUNCH_DRY_RUN=1 "$3" launch ghost-agent </dev/null' _ "$REPO_A" "$WORK/ghost-sessions.yml" "$BOSUN"

section example-configs "example configs" covers 'examples/**'
check "the generic example lists every setting with its default value" bash -c '
  dump() { for v in $(compgen -v BOSUN_ | grep -vx "BOSUN_ROOT\|BOSUN_INSTALL\|BOSUN_CMD\|BOSUN_PASSTHROUGH_VARS\|BOSUN_CONFIG_DIR"); do printf "%s=%s\n" "$v" "${!v}"; done; }
  defaults="$(env -i HOME="$HOME" bash -c "source \"\$1/lib/common.sh\"; set_defaults; $(declare -f dump); dump" _ "$1")"
  example="$(env -i HOME="$HOME" bash -c "BOSUN_CONFIG_DIR=\$HOME/.config/bosun; source \"\$1/examples/generic/bosun.env\"; $(declare -f dump); dump" _ "$1")"
  [ "$defaults" = "$example" ] || { diff <(echo "$defaults") <(echo "$example"); exit 1; }' _ "$ROOT"
check "the example configs only set known settings" bash -c '
  known="$(grep -oE "^  BOSUN_[A-Z_]+=" "$1/lib/common.sh" | tr -d " =")"
  for f in "$1"/examples/*/bosun.env; do
    for key in $(grep -oE "^BOSUN_[A-Z_]+=" "$f" | tr -d =); do grep -qx "$key" <<<"$known" || { echo "$f: $key"; exit 1; }; done
  done' _ "$ROOT"

section templates "templates" covers 'templates/**,libexec/bosun-learn,libexec/bosun-watchdog'
plist="$(bash -c "source '$ROOT/lib/common.sh'; load_config; render_launchd_plist com.example.test 08:00 MTWRF '' BOSUN_TRIGGER=launchd")"
check "launchd plist is valid" plutil -lint - <<<"$plist"
check "launchd plist has weekday schedule" contains "$plist" "<key>Weekday</key><integer>5</integer>"
plist="$(bash -c "source '$ROOT/lib/common.sh'; load_config; render_watchdog_plist")"
check "the watchdog plist is valid" plutil -lint - <<<"$plist"
check "... runs the watchdog every 5 minutes under its own label" bash -c 'grep -q "<string>com.bosun.morning.watchdog</string>" <<<"$1" && grep -q "<integer>300</integer>" <<<"$1" && grep -q "watchdog --once" <<<"$1"' _ "$plist"

plist="$(bash -c "source '$ROOT/lib/common.sh'; load_config; render_learn_plist")"
check "the learning agent's plist is valid" plutil -lint - <<<"$plist"
check "... runs learn --scheduled daily at BOSUN_LEARN_AT under its own label" bash -c 'grep -q "<string>com.bosun.morning.learn</string>" <<<"$1" && grep -q "learn --scheduled" <<<"$1" && grep -q "<key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer>" <<<"$1"' _ "$plist"

section two-paths "two paths" covers 'libexec/bosun-launch,lib/install_paths.py'
V1="$(version_folder v1)"
ln -s bosun-versions/v1 "$WORK/share/bosun" # the install link, as an update leaves it
LINK="$(cd "$WORK/share" && pwd -P)/bosun"
inst() { bash -c 'source "$1/lib/common.sh"; printf "%s %s" "$BOSUN_INSTALL" "$BOSUN_CMD"' _ "$1"; }
check "a version folder writes out the link beside its versions folder" test "$(inst "$V1")" = "$LINK $LINK/bin/bosun"
py_install() { python3 -c 'import sys; sys.path.insert(0, sys.argv[1] + "/lib"); import install_paths, pathlib; print(install_paths.install_path(pathlib.Path(sys.argv[2])))' "$ROOT" "$1"; }
check "... and Python says the same" test "$(py_install "$V1")" = "$LINK"
check "a checkout writes out itself" test "$(inst "$ROOT")" = "$ROOT $ROOT/bin/bosun"
check "bash and Python agree on a normal version folder" test "$(inst "$V1" | cut -d' ' -f1)" = "$(py_install "$V1")"
ODD="$WORK/odd/-versions/x"
mkdir -p "$ODD"
cp -R "$V1/lib" "$V1/bin" "$ODD/"
check "... and on a parent named only -versions, which is not a versions folder" test "$(inst "$ODD" | cut -d' ' -f1)" = "$ODD" -a "$(py_install "$ODD")" = "$ODD"
PR="$WORK/src/bosun-versions/pr-123"
mkdir -p "$PR"
cp -R "$V1/lib" "$V1/bin" "$PR/"
check "... and on a checkout in a -versions folder without bosun.git, which writes out itself" test "$(inst "$PR")" = "$PR $PR/bin/bosun" -a "$(py_install "$PR")" = "$PR"
check "a command run through the link stays on its own version" test "$(bash -c 'source "$(dirname "$(readlink -f "$1/bin/bosun")")/../lib/common.sh"; printf "%s %s" "$BOSUN_ROOT" "$BOSUN_INSTALL"' _ "$LINK")" = "$V1 $LINK"
out="$(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A" "$V1/bin/bosun" launch orch 2>&1)"
check "a version's orchestrator launch names the link" contains "$out" "$LINK/orchestrator/plugin"
check "... never its own folder" not_contains "$out" "$V1"
check "... the versions folder only in the rule that lets it read there, which every version names alike" bash -c '
  grep -q "bosun-versions/" <<<"$1" && ! grep -qE "bosun-versions/[^*\\]" <<<"$1"' _ "$out"
out="$(cd "$REPO_A" && "$V1/bin/bosun" launch alpha 2>&1)"
check "a version's repo window launch names the link in its hooks" contains "$out" "$LINK/bin/bosun"
check "... never its own folder" not_contains "$out" "bosun-versions/"
orch_fingerprint() { (cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A" "$V1/bin/bosun" launch --fingerprint orch); }
fingerprint_before="$(orch_fingerprint)"
echo "A rule added to the policy." >>"$V1/orchestrator/policy.md"
check "the orchestrator's policy text is no launch input (it is re-read, not restarted for)" bash -c '[ -n "$1" ] && [ "$1" = "$2" ]' _ "$fingerprint_before" "$(orch_fingerprint)"
echo "A step added to the chores." >>"$V1/orchestrator/plugin/commands/chores.md"
check "... its plugin's chores.md is, read through the link" bash -c '[ "$(jq -r .files <<<"$1")" != "$(jq -r .files <<<"$2")" ]' _ "$(orch_fingerprint)" "$fingerprint_before"
# Before an update switches the link, the next version's fingerprint reads that version's own files.
(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A" "$V1/bin/bosun" launch orch >/dev/null 2>&1)
cp -R "$V1" "$WORK/share/bosun-versions/v2"
V2="$(cd "$WORK/share/bosun-versions/v2" && pwd -P)"
v2_fingerprint() { (cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A" "$V2/bin/bosun" launch --fingerprint orch | jq -r .files); }
recorded_files="$(jq -r .fingerprint.files "$BOSUN_STATE_DIR/launches/smoke/orch.json")"
check "the next version, the same as the running one, fingerprints as its launch recorded" test "$(v2_fingerprint)" = "$recorded_files"
echo "A step the next version adds to the chores." >>"$V2/orchestrator/plugin/commands/chores.md"
check "... and with a changed chores.md, it reads its own before the switch (the link still at v1)" bash -c '[ "$1" != "$2" ] && [ "$(readlink "$3")" = bosun-versions/v1 ]' _ "$(v2_fingerprint)" "$recorded_files" "$WORK/share/bosun"
plists="$(bash -c 'source "$1/lib/common.sh"; load_config; render_launchd_plist com.example.test 08:00 MTWRF ""; render_watchdog_plist; render_learn_plist' _ "$V1")"
check "a version's plists run the link" contains "$plists" "$LINK/"
check "... never its own folder" not_contains "$plists" "bosun-versions/"
check "a version's window command line names the link" contains "$(bash -c 'source "$1/lib/common.sh"; load_config; build_launch_command alpha; printf "%s\\n" "${launch_cmd[@]}"' _ "$V1")" "$LINK/bin/bosun"
check "a window's shell says no to oh-my-zsh's update question, which would hold its launch" contains "$(bash -c 'source "$1/lib/common.sh"; load_config; build_launch_command alpha; printf "%s\\n" "${launch_cmd[@]}"' _ "$ROOT")" "DISABLE_AUTO_UPDATE=true"

section window-list "window list" covers 'libexec/bosun-checkin,examples/generic/sessions.yml'
specs() { bash -c 'source "$1/lib/common.sh"; load_config; window_specs' _ "$ROOT"; }
check "one window per session, without orchestrator or checkin when disabled" test "$(specs | cut -f1,3 | tr '\t\n' ': ')" = "alpha:session beta:session gamma:session "
out="$(BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A" specs | cut -f1,3 | tr '\t\n' ': ')"
check "orchestrator first, checkin last" test "$out" = "orch:orchestrator alpha:session beta:session gamma:session checkin:checkin "
check "no checkin window when check-ins are off" bash -c '! BOSUN_ORCHESTRATOR=orch BOSUN_CHECKIN_MINUTES=0 bash -c "source \"\$1/lib/common.sh\"; load_config; window_specs" _ "$1" | grep -q checkin' _ "$ROOT"
check "a checkin window for events alone when asked" bash -c 'BOSUN_ORCHESTRATOR=orch BOSUN_CHECKIN_MINUTES=0 BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_WITHOUT_CHECKINS=1 bash -c "source \"\$1/lib/common.sh\"; load_config; window_specs" _ "$1" | grep -q "^checkin"' _ "$ROOT"
check "events alone need events on" bash -c '! BOSUN_ORCHESTRATOR=orch BOSUN_CHECKIN_MINUTES=0 BOSUN_EVENTS_WITHOUT_CHECKINS=1 bash -c "source \"\$1/lib/common.sh\"; load_config; window_specs" _ "$1" | grep -q checkin' _ "$ROOT"

# bosun-ledger passes its settings to lib/ledger.py, whose rules (idempotent writes, kinds, evidence, proposals,
# one-line fields, a corrupt file) tests/unit/test_ledger*.py hold; here, the wrapper, the files and real processes.
section ledger "ledger" covers 'libexec/bosun-ledger,libexec/bosun-resolve'
check "upsert creates in the initial state" contains "$(L upsert 'review:o/r#1' --kind review-request --title 'Fix X' --url https://github.com/o/r/pull/1)" "created: review:o/r#1 [review-request] state=requested"
check "invalid transition is rejected" fails_with "is not allowed" "$BOSUN" ledger transition "review:o/r#1" rereview-requested --note x --evidence y
check "valid transition" contains "$(L transition 'review:o/r#1' reviewed --note 'reviewed on GitHub' --field reviewed_sha=abc)" "requested -> reviewed"
check "re-review with evidence" contains "$(L transition 'review:o/r#1' rereview-requested --note again --evidence 'GitHub re-request after review')" "reviewed -> rereview-requested"
check "unseen source" bash -c '! "$1" ledger seen https://slack/p1' _ "$BOSUN"
L record-source https://slack/p1 --class pr-review --item 'review:o/r#1' >/dev/null
check "seen source is remembered" contains "$(L seen https://slack/p1)" '"classification": "pr-review"'
check "propose records a pending action" contains "$(L propose 'review:o/r#1' 'Post review')" "proposal pending"
check "pending proposals are listed" contains "$(L list --proposals)" "PENDING: Post review"
check "ledger.md shows proposals" contains "$(cat "$BOSUN_STATE_DIR/ledger.md")" "Post review"
L resolve 'review:o/r#1' 'done' --note posted >/dev/null
L upsert 'mypr:o/r#2' --kind my-pr >/dev/null
L transition 'mypr:o/r#2' approved --note ok >/dev/null
L transition 'mypr:o/r#2' merged --note merged >/dev/null
check "list --open hides finished items" bash -c '! grep -q "mypr:o/r#2" <<<"$1"' _ "$(L list --open)"
check "first mark-run reports never" contains "$(L mark-run check-in)" "previous check-in run: never"
check "next mark-run reports the previous time" bash -c '! grep -q never <<<"$1"' _ "$(L mark-run check-in)"
check "a morning prints where its Slack scan starts, from the configured lookback" contains "$(BOSUN_SLACK_LOOKBACK_HOURS=2 L mark-run morning)" "(the last 2h (the last scan was more recent))"
for i in $(seq 1 20); do L upsert "slack:C1/$i" --kind mention >/dev/null & done
wait
check "concurrent writers do not lose items" test "$(L list --kind mention | grep -c '^slack:C1/')" = 20
# Mixed writes at once, two of each kind, through the real entry point (test_ledger_protocol.py's
# ConcurrentWritersTest races 40 through lib/ledger.py itself): slack:C1/3 surfaced and slack:C1/6 handed to w6 are
# what later checks (and the events section) use.
for i in $(seq 1 8); do
  case $((i % 4)) in
    0) L note "slack:C1/1" "note $i" >/dev/null & ;;
    1) L report "slack:C1/$i" --summary "report $i" >/dev/null & ;;
    2) L record-delegation "slack:C1/$i" --window "w$i" --task investigate >/dev/null & ;;
    3) L transition "slack:C1/$i" surfaced --note "surfaced $i" >/dev/null & ;;
  esac
done
wait
check "concurrent mixed writers lose no change" bash -c '
  [ "$("$1" ledger show slack:C1/1 | grep -cE "\"note (4|8)\"")" = 2 ] &&
  [ "$("$1" ledger reports | grep -c "^slack:C1/")" = 2 ] &&
  [ "$("$1" ledger list | grep -c " @w")" = 2 ] &&
  [ "$("$1" ledger list | grep -c "\[mention: surfaced\]")" = 2 ]' _ "$BOSUN"
check "report --state makes a validated transition" contains "$(L report 'slack:C1/3' --summary 'answered' --result notes/findings.md --state 'done')" "surfaced -> done"
check "delegation shows the recorded window" contains "$(L delegation 'slack:C1/6')" '"window": "w6"'
check "delegation refuses an item never delegated" fails_with "never handed to a repo session" "$BOSUN" ledger delegation 'slack:C1/7'
check "every write keeps the previous version" test -s "$BOSUN_STATE_DIR/ledger.json.bak"
check "prune drops finished items" contains "$(L prune --days 0)" "pruned 2 item(s)"
check "every write went through the journal" test -d "$BOSUN_STATE_DIR/journal/ledger"
check "... and ledger.json is its rebuild, after 20 writers at once too" journal_rebuilds "$BOSUN_STATE_DIR"
check "ledger validate --journal agrees: the ledger is valid and its journal rebuilds it" L validate --journal

# Outreach's rules (ids, channels, message times, evidence, the parent guard, shared threads, fyi) are
# tests/unit/test_outreach.py's; here, the minted id, seen's exit code and delegate --update across commands.
section outreach "outreach (waiting on other people)" covers 'libexec/bosun-delegate,libexec/bosun-ledger'
L upsert 'slack:C5/1' --kind mention --title 'template question' >/dev/null
L record-delegation 'slack:C5/1' --window exporter --task new-work >/dev/null
out="$(L reach-out 'slack:C5/1' --to 'Dana (product)' --purpose decision --subject 'Keep the template unchanged?' --option a --option b)"
OUT1="${out%%$'\n'*}"
check "reach-out prints the new id first, minted" bash -c '[[ $1 =~ ^out:slack:C5/1/$2$ ]]' _ "$OUT1" "$MINTED"
check "proposing the message moves it to proposed" contains "$(L propose "$OUT1" 'Slack DM to Dana: ...')" "draft -> proposed"
check "sent records the message ref, at the Slack message's own time" contains "$(L sent "$OUT1" --ref 'https://acme.slack.com/archives/C0000000001/p1700000100000100')" "proposed -> sent; at 2023-11-14T22:15:00+00:00 (slack ref)"
check "seen names the outreach for a reply in its thread" bash -c '
  out="$("$1" ledger seen "https://acme.slack.com/archives/C0000000001/p1700000200000200?thread_ts=1700000100.000100&cid=C0000000001")"
  [ $? = 1 ] && grep -qF "in the conversation of $2" <<<"$out"' _ "$BOSUN" "$OUT1"
check "replied says which window holds the parent" contains "$(L replied "$OUT1" --evidence 'user: Dana said b' --summary 'b')" "window exporter holds slack:C5/1"
out="$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" delegate --update "$OUT1" 2>&1)"
check "delegate --update <out-id> hands the reply to the parent's window" contains "$out" "would deliver to smoke:exporter: Update to ledger item slack:C5/1"
check "... and marks the outreach applied" contains "$out" "replied -> applied"
check "then the parent may finish" contains "$(L transition 'slack:C5/1' 'done' --note answered)" "new -> done"
L upsert 'slack:C5/2' --kind mention --title 'footer question' >/dev/null
out="$(L reach-out 'slack:C5/2' --to 'Dana (product)' --purpose decision --subject 'Keep the footer?')"
FOOTER="${out%%$'\n'*}"
out="$(L reach-out 'slack:C5/2' --to dana --subject 'And the header?')"
HEADER="${out%%$'\n'*}"
check "reach-out warns about open outreach to the same person" contains "$out" "warning: slack:C5/2 already has open outreach to the same person: $FOOTER (decision, draft: Keep the footer?)"
check "sent records one message on several items" contains "$(L sent "$FOOTER" "$HEADER" --ref 'https://acme.slack.com/archives/C0000000001/p1700000300000300')" "ok: $HEADER draft -> sent"

# The rules of tasks, links, tickets and folds are tests/unit/test_lineage.py's and test_tickets.py's; here, minted
# ids, delegate across commands and BOSUN_TICKET_STATUS_MAP through the wrapper.
section tasks-links-tickets "tasks, links and tickets" needs outreach covers 'libexec/bosun-delegate,libexec/bosun-ledger'
out="$(L spawn "$OUT1" --title 'Fix the PDF renderer' --ask 'apply the fix there' --via 'user: go ahead')"
TASK="${out%%$'\n'*}"
check "spawn prints the new task's id first, minted on the UTC day" bash -c '[[ $1 =~ ^task:$2/$3$ ]]' _ "$TASK" "$(date -u +%F)" "$MINTED"
check "a task is not handed over before I accept it" fails_with "only an accepted task is handed over" "$BOSUN" delegate new-work --id "$TASK" --ask x --window exporter
L transition "$TASK" accepted --note 'I asked' --evidence 'user: go ahead' >/dev/null
check "delegate moves an accepted task to delegated" contains "$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" delegate new-work --id "$TASK" --ask 'fix the PDF renderer' --window exporter 2>&1)" "accepted -> delegated"
L report "$TASK" --summary 'draft PR open' --result 'https://github.com/acme/pdf-renderer/pull/7' >/dev/null
check "reports name the commands that link the new PR" contains "$(L reports)" "bosun ledger link '$TASK' produces 'mypr:acme/pdf-renderer#7'"
L upsert 'mypr:acme/pdf-renderer#7' --kind my-pr --title 'PDF renderer fix' >/dev/null
check "link records produces" contains "$(L link "$TASK" produces 'mypr:acme/pdf-renderer#7')" "ok: $TASK produces mypr:acme/pdf-renderer#7"
check "tracks creates the ticket" contains "$(L link 'mypr:acme/pdf-renderer#7' tracks 'jira:EXP-123')" "created jira:EXP-123 [ticket: unsynced]"
check "ticket-sync maps the status and names the mismatch" contains "$(L ticket-sync 'jira:EXP-123' --status 'To Do')" "propose moving it to in-review"
check "the site's status map applies" contains "$(BOSUN_TICKET_STATUS_MAP='jira/EXP:On Hold=cancelled' L ticket-sync 'jira:EXP-123' --status 'On Hold')" "todo -> cancelled ('On Hold')"
out="$(L spawn "$OUT1" --title 'Export credit notes' --ask 'add them')"
FOLDED="${out%%$'\n'*}"
check "spawn warns about a task that already comes from the same source" contains "$out" "warning: a task already comes from the same source or thread: $TASK (delegated @exporter"
check "fold closes the task and prints the update for the target's window" contains "$(L fold "$FOLDED" --into "$TASK" --note 'same PR')" "next: $TASK is with window exporter; carry the work there: bosun delegate --update '$TASK' --details"

# The hierarchy, one workstream per item and the summaries' text are tests/unit/test_workstreams.py's.
section workstreams "workstreams" covers 'libexec/bosun-ledger,examples/generic/workstreams.json'
check "ws add creates a workstream with an alias" contains "$(L ws add widget-gen --title 'Automated widget generation' --epic WID-100 --alias widgets)" "created: ws:widget-gen [workstream: active]"
L upsert 'mypr:acme/widgets#12' --kind my-pr --title 'Widget template cache' >/dev/null
check "link part_of takes an alias" contains "$(L link 'mypr:acme/widgets#12' part_of widgets)" "ok: mypr:acme/widgets#12 part_of ws:widget-gen"
out="$(L reach-out 'mypr:acme/widgets#12' --to Dana --purpose decision --subject 'Cache TTL?')"
check "show says the outreach inherits it" contains "$(L show "${out%%$'\n'*}")" '"how": "inherited from mypr:acme/widgets#12 (explicit)"'
L upsert 'mypr:acme/widgets#13' --kind my-pr --title 'Preview pane' >/dev/null
L link 'mypr:acme/widgets#13' tracks 'jira:WID-101' >/dev/null
L ticket-sync 'jira:WID-101' --status 'In Progress' --parent WID-100 >/dev/null
check "a tracked ticket's epic assigns its work" contains "$(L show 'mypr:acme/widgets#13')" '"how": "epic: tracks jira:WID-101 -> jira:WID-100 (the epic of ws:widget-gen)"'
printf '{"rules": [{"ws": "widgets", "repo": "acme/widget*", "title": "sizes"}]}\n' >"$BOSUN_CONFIG_DIR/workstreams.json"
L upsert 'mypr:acme/widgets#14' --kind my-pr --title 'Widget sizes' >/dev/null
check "assign --dry-run names what it would tag and why" contains "$(L assign --dry-run)" "would assign: mypr:acme/widgets#14 -> ws:widget-gen (rule: rule 1 (repo acme/widget*; title sizes))"
check "assign --apply records the links" contains "$(L assign --apply)" "2 link(s) recorded"
check "list --ws lists its items, inherited ones too" bash -c '[ "$("$1" ledger list --ws widgets | grep -c widgets)" = 4 ]' _ "$BOSUN"
check "summary drafts the stand-up" bash -c 'out="$("$1" ledger summary --ws widgets --since 2000-01-01)"; grep -q "^In review (3)" <<<"$out" && grep -q "Cache TTL?: to send" <<<"$out"' _ "$BOSUN"
check "lint --json is JSON" bash -c '"$1" ledger lint --json | python3 -c "import json, sys; json.load(sys.stdin)"' _ "$BOSUN"
check "lint --strict fails on a finding" fails_with "warn: done-with-open-items: ws:widget-gen" bash -c '"$1" ledger ws set-state widgets done --note x >/dev/null && "$1" ledger lint --strict' _ "$BOSUN"
L ws set-state widgets active --note reopened >/dev/null
check "ws list counts by how items were assigned" contains "$(L ws list)" "ws:widget-gen  [active] Automated widget generation | 4 item(s) (explicit 3, inherited 1), 4 open"
rm "$BOSUN_CONFIG_DIR/workstreams.json"

section repo-window-prompts "what I say in a repo window, and what a window works on" needs tasks-links-tickets covers 'libexec/bosun-launch,libexec/bosun-ledger,libexec/bosun-hook'
said_payload='{"hook_event_name":"UserPromptSubmit","session_id":"s-1","prompt":"go with option b for the suites"}'
check "the prompt hook does nothing while events are off" bash -c '"$1" hook user-prompt --window exporter <<<"$2" && [ "$("$1" ledger said)" = "(nothing I said in a repo window matches)" ]' _ "$BOSUN" "$said_payload"
check "the prompt hook records what I type on the item the window holds" bash -c '
  BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"$2" || exit 1
  for _ in $(seq 1 50); do "$1" ledger said "$3" | grep -q "go with option b" && exit 0; sleep 0.1; done; exit 1' _ "$BOSUN" "$said_payload" "$TASK"
# In the background: with the ledger's lock held (as a write in progress holds it), the hook returns all the same,
# and its write lands once the lock is free. (The 30 s are only hang protection, against a hook that waits.)
hold_lock "$BOSUN_STATE_DIR/ledger.lock" ledger-for-the-hook
said_later='{"hook_event_name":"UserPromptSubmit","session_id":"s-1","prompt":"and keep the suites green"}'
check "... in the background: the hook returns while the ledger's lock is held, which its write waits for" python3 -I -c '
import subprocess, sys
sys.exit(subprocess.run(sys.argv[2:], input=sys.argv[1], text=True, timeout=30).returncode)' "$said_later" env BOSUN_EVENTS_ENABLED=1 "$BOSUN" hook user-prompt --window exporter
release_lock ledger-for-the-hook
check "... and its write lands once the lock is free" wait_for 30 bash -c '"$1" ledger said "$2" | grep -q "keep the suites green"' _ "$BOSUN" "$TASK"
check "... and never fails the prompt, even on garbage" bash -c 'BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"{nope"' _ "$BOSUN"
check "... it logs the error instead" wait_for 30 bash -c 'grep -q "ERROR: unreadable hook input" "$1"/hook-*.log' _ "$BOSUN_LOG_DIR"
check "a prompt bosun passes at launch is not taken for mine" bash -c '
  prompt="Bosun started this fresh conversation for this window; reply with a one-line status"
  (cd "$3" && BOSUN_EVENTS_ENABLED=1 "$1" launch exporter "$prompt" >/dev/null 2>&1) || exit 1
  payload="$(jq -nc --arg p "$prompt" "{hook_event_name: \"UserPromptSubmit\", session_id: \"s-2\", prompt: \$p}")"
  BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"$payload" || exit 1
  BOSUN_EVENTS_ENABLED=1 "$1" hook user-prompt --window exporter <<<"${payload/Bosun started this fresh conversation/control typed by me}" || exit 1
  for _ in $(seq 1 50); do "$1" ledger said | grep -q "control typed by me" && break; sleep 0.1; done
  # the first hook done too: it says in its log that it skipped the prompt bosun passed (no fixed wait for nothing)
  for _ in $(seq 1 50); do grep -q "skipped in exporter: not typed by me (.Bosun started this fresh" "$2"/hook-*.log 2>/dev/null && break; sleep 0.1; done
  grep -q "skipped in exporter: not typed by me (.Bosun started this fresh" "$2"/hook-*.log &&
    "$1" ledger said | grep -q "control typed by me" && ! "$1" ledger said | grep -q "fresh conversation"' _ "$BOSUN" "$BOSUN_LOG_DIR" "$REPO_A"
rm -f "$BOSUN_LOG_DIR"/hook-*.log # the ERROR line above is expected, not one for the watchdog
check "reports --since shows what I said with the reports" contains "$(L reports --since 2000-01-01T00:00:00Z)" "$TASK  [task: delegated] user said"
check "working marks the window's active item" contains "$(L working "$TASK")" "ok: exporter is working on $TASK"
check "... shown in list and delegation" bash -c '"$1" ledger list | grep -q "@exporter (reported, working)" && "$1" ledger delegation "$2" | grep -q "\"working\": true"' _ "$BOSUN" "$TASK"
check "idle takes it out" bash -c '"$1" ledger idle "$2" >/dev/null && "$1" ledger delegation "$2" | grep -q "\"working\": false"' _ "$BOSUN" "$TASK"
check "ledger.json is still the journal's rebuild after outreach, tasks, workstreams and the hook" journal_rebuilds "$BOSUN_STATE_DIR"

section repo-mask "repo mask" covers 'libexec/bosun-ledger'
mask_check() { # <mask> <repo>
  bash -c 'source "$1/lib/common.sh"; BOSUN_REPO_MASK="$2"; repo_in_mask "$3"' _ "$ROOT" "$1" "$2"
}
# repo_in_mask calls lib/repo_mask.py, whose rules tests/unit/test_repo_mask.py holds: one case shows the call.
check "owner glob matches its repos" mask_check "acme/*" acme/api
check "repo globs narrow the match" bash -c '! "$@"' _ mask_check "acme/web-*" acme/api
check "ledger rejects items outside the mask" fails_with "outside BOSUN_REPO_MASK" env BOSUN_REPO_MASK="acme/*" "$BOSUN" ledger upsert "mypr:personal/x#1" --kind my-pr
check "ledger accepts items inside the mask" contains "$(BOSUN_REPO_MASK="acme/*" L upsert 'mypr:acme/api#1' --kind my-pr)" "created"
check "mask does not apply to non-repo items" contains "$(BOSUN_REPO_MASK="acme/*" L upsert 'slack:C9/1' --kind mention)" "created"

section my-prs "my-prs (fake gh)" covers 'libexec/bosun-my-prs'
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\nexit 0\n' >"$HOME/.local/bin/osascript" # no real notifications from tests
chmod +x "$HOME/.local/bin/osascript"
mkdir -p "$WORK/osascript-argv"
printf '#!/bin/sh\nfor a in "$@"; do printf "%%s\\0" "$a"; done >"%s"\n' "$WORK/osascript.argv" >"$WORK/osascript-argv/osascript"
chmod +x "$WORK/osascript-argv/osascript"
# shellcheck disable=SC1003 # the backslash before the closing quote is part of the message, on purpose
hostile='a \" & (do shell script "touch PWNED") -- "b" \'
/bin/bash -c 'source "$1/lib/common.sh"; PATH="$3:$PATH"; notify "t \" x" "$2"' _ "$ROOT" "$hostile" "$WORK/osascript-argv"
check "notify hands osascript the title and message as arguments, unchanged, never as script text" test \
  "$(tr '\0' '\n' <"$WORK/osascript.argv")" = "$(printf '%s\n' -e 'on run argv' -e 'display notification (item 2 of argv) with title (item 1 of argv)' -e 'end run' 't " x' "$hostile")"
cat >"$HOME/.local/bin/gh" <<FAKE
#!/bin/bash
printf '%s\n' "\$@" >"$WORK/fake-gh.args"
echo '{"url":"https://github.com/acme/api/pull/1","repo":"acme/api","number":1,"title":"a"}'
echo '{"url":"https://github.com/personal/x/pull/2","repo":"personal/x","number":2,"title":"b"}'
FAKE
chmod +x "$HOME/.local/bin/gh"
out="$(BOSUN_REPO_MASK="acme/*" "$BOSUN" my-prs)"
check "my-prs applies the repo mask" test "$(jq -r .repo <<<"$out")" = "acme/api"
check "my-prs succeeds when the last PR is masked out" env BOSUN_REPO_MASK="acme/*" "$BOSUN" my-prs
check "my-prs skips drafts by default" grep -qx -- "--draft=false" "$WORK/fake-gh.args"
check "default window filters by activity" grep -q -- "^--updated=>=20" "$WORK/fake-gh.args"
BOSUN_REPO_MASK="" "$BOSUN" my-prs --since 7d --by created --include-drafts >/dev/null
check "--by created filters by creation time" grep -q -- "^--created=>=$(date -u -v-7d +%Y-%m-%d)" "$WORK/fake-gh.args"
check "--include-drafts drops the draft filter" bash -c '! grep -q -- "--draft" "$1"' _ "$WORK/fake-gh.args"
"$BOSUN" my-prs --since 2026-05-01 >/dev/null
check "--since accepts a date" grep -qx -- "--updated=>=2026-05-01" "$WORK/fake-gh.args"
check "--since rejects nonsense" fails_with "bad window" "$BOSUN" my-prs --since soon
rm "$HOME/.local/bin/gh"

section context "context (fake gh)" needs repo-mask covers 'libexec/bosun-context'
printf '#!/bin/sh\necho octo-me\n' >"$HOME/.local/bin/gh"
chmod +x "$HOME/.local/bin/gh"
for slug in acme/api personal/x; do
  git init -q "$WORK/context-repos/${slug#*/}" && git -C "$WORK/context-repos/${slug#*/}" remote add origin "git@github.com:$slug.git"
done
out="$(BOSUN_REPOS_DIR="$WORK/context-repos" BOSUN_REPO_MASK="acme/*" "$BOSUN" context 2>&1)"
check "context shows the chores config and my GitHub login" bash -c 'grep -qx -- "- max_nudges: 2 (outreach other than review)" <<<"$1" && grep -q -- "^- max_review_nudges: 0 " <<<"$1" && grep -qx -- "- github_login: octo-me" <<<"$1"' _ "$out"
check "context maps only the repos within the mask" bash -c 'grep -q "^- acme/api -> .*/context-repos/api$" <<<"$1" && ! grep -q "personal/x" <<<"$1"' _ "$out"
check "context says when the session is not running" contains "$out" "(session not running)"
check "context lists the open ledger items" contains "$out" "slack:C9/1"
rm "$HOME/.local/bin/gh"

section orchestrator "orchestrator" covers 'libexec/bosun-delegate,libexec/bosun-guard,libexec/bosun-launch,libexec/bosun-send,libexec/bosun-update'
# The orchestrator's launch with the defaults (the ontology and approvals off), made once and read by every check of
# what it holds.
orch_default="$(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch "$BOSUN" launch orch 2>&1)"
out="$orch_default"
check "orchestrator starts a dated conversation" contains "$out" "--name orch-$(date +%F) --remote-control orch"
check "orchestrator runs in auto mode, with its model and effort before the long policy" contains "$out" "--permission-mode auto --model opus --settings"
check "its settings keep the permission rules and guard, plus the effort" bash -c 'grep -q "PreToolUse" <<<"$1" && grep -q "effortLevel" <<<"$1"' _ "$out"
check "orchestrator loads the plugin" contains "$out" "--plugin-dir $ROOT/orchestrator/plugin"
# An update's prompt asks the orchestrator to re-read files of the install (policy.md), and Claude Code asks
# before a read outside its working directories, which would stop the update's wait at a prompt. So it may
# read the install, and only read it: rules added to a settings file's own lists, never in their place.
orchestrator_settings() { # <settings file>: the --settings JSON the orchestrator would be launched with
  BOSUN_ORCHESTRATOR=orch bash -c 'source "$1/lib/common.sh"
    load_config
    build_model_effort_args opus high "$2" "$(orchestrator_extra_settings)"
    for ((i = 0; i < ${#model_effort_args[@]}; i++)); do
      [ "${model_effort_args[$i]}" = --settings ] && printf "%s\n" "${model_effort_args[$((i + 1))]}"
    done' _ "$ROOT" "$1"
}
reads_install_only() { # <settings JSON> <site allow rule> <site deny rule>: as the description says
  jq -e --arg read "Read(/$ROOT/**)" --arg allow "$2" --arg deny "$3" '
    ([.permissions.allow[] | select(. == $read)] | length == 1) and any(.permissions.allow[]; . == $allow)
    and any(.permissions.deny[]; . == $deny) and (.permissions | has("additionalDirectories") | not)' \
    <<<"$1" >/dev/null || {
    echo "$1" | jq -c .permissions
    return 1
  }
}
check "... and may read the install, whose files an update asks it to re-read, and nothing more" \
  reads_install_only "$(orchestrator_settings "$ROOT/orchestrator/settings.json")" "Bash(bosun update note:*)" "Edit"
check "... and its launch settings run this checkout's bosun guard as a PreToolUse hook" runs_this_guard "$(orchestrator_settings "$ROOT/orchestrator/settings.json")"
check "... which orchestrator/settings.json no longer carries" jq -e 'has("hooks") | not' "$ROOT/orchestrator/settings.json"
check "... and asks for Chrome's scripting and form filling by Claude Code's own rules" jq -e '.permissions.ask | index("mcp__claude-in-chrome__javascript_tool") and index("mcp__claude-in-chrome__form_input")' "$ROOT/orchestrator/settings.json"
check "... its stock settings' denies of Edit, Write and NotebookEdit kept" bash -c '
  [ "$(jq -c "[.permissions.deny[] | select(test(\"^(Edit|Write|NotebookEdit)$\"))] | sort" <<<"$1")" = "[\"Edit\",\"NotebookEdit\",\"Write\"]" ]' \
  _ "$(orchestrator_settings "$ROOT/orchestrator/settings.json")"
jq '.permissions.allow += ["Bash(acme-tool:*)"] | .permissions.deny += ["Bash(rm -rf:*)"]' "$ROOT/orchestrator/settings.json" >"$WORK/site-settings.json"
check "... a site's settings keep their own allow and deny rules, the read rule added once" \
  reads_install_only "$(orchestrator_settings "$WORK/site-settings.json")" "Bash(acme-tool:*)" "Bash(rm -rf:*)"
check "orchestrator launches with its auto-compact window and the compaction hook" bash -c 'grep -q -- "--autocompact 300000" <<<"$1" && grep -qE "compact-context.{0,2} --orchestrator" <<<"$1"' _ "$out"
check "orchestrator gets the async signal hooks next to the guard" bash -c 'grep -qE "hook.{0,2} signal.{0,2} --window.{0,2} orch" <<<"$1" && grep -q "PermissionRequest" <<<"$1"' _ "$out"
check "orchestrator starts with the morning chores" contains "$out" "/bosun:chores\ morning"
out="$(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_MORNING_CHORES=0 "$BOSUN" launch orch 2>&1)"
check "with morning chores off, today's conversation starts idle" bash -c '! grep -qF "/bosun:chores\ morning" <<<"$1" && grep -q -- "--name orch-" <<<"$1"' _ "$out"
check "orchestrator never skips permissions" bash -c '! grep -q dangerously <<<"$1"' _ "$out"
# FAKE_TODAY moves `date +%F`, so the orchestrator's conversation name, to another day.
mkdir -p "$HOME/.local/bin"
cat >"$HOME/.local/bin/date" <<'SH'
#!/bin/sh
if [ -n "${FAKE_TODAY:-}" ] && [ "$1" = "+%F" ]; then echo "$FAKE_TODAY"; exit 0; fi
exec /bin/date "$@"
SH
chmod +x "$HOME/.local/bin/date"
orch_fingerprint_today() { (cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A" "$BOSUN" launch --fingerprint orch); }
check "the orchestrator's next day is a conversation of its own" contains "$(cd "$REPO_A" && FAKE_TODAY=2026-01-02 BOSUN_ORCHESTRATOR=orch "$BOSUN" launch orch 2>&1)" "--name orch-2026-01-02 "
check "... and no launch input: its fingerprint stays the same across the date change" bash -c '[ -n "$1" ] && [ "$1" = "$2" ]' _ "$(orch_fingerprint_today)" "$(FAKE_TODAY=2026-01-02 orch_fingerprint_today)"
rm -f "$HOME/.local/bin/date"
# A site's policy.md and the ontology add to the policy each in its own way: one launch with both.
echo "Site rule: be brief." >"$BOSUN_CONFIG_DIR/policy.md"
orch_on="$(cd "$REPO_A" && BOSUN_ORCHESTRATOR=orch BOSUN_KNOWLEDGE_ENABLED=1 "$BOSUN" launch orch 2>&1)"
rm "$BOSUN_CONFIG_DIR/policy.md"
check "a site policy.md is appended to the orchestrator policy" contains "$orch_on" "Site rule: be brief."
check "with the ontology on, the orchestrator's launch ends its policy with the ontology's rules" grep -qE -- '--append-system-prompt .*Look.before.asking' <<<"$orch_on"
check "... and off, it does not" bash -c 'grep -q -- "--append-system-prompt" <<<"$1" && ! grep -q "Look.before.asking" <<<"$1"' _ "$orch_default"
check "approvals off: the orchestrator's launch has no approval hooks" bash -c 'grep -q -- "--append-system-prompt" <<<"$1" && ! grep -q -- "--permission-request" <<<"$1" && ! grep -q -- "--post-tool-use" <<<"$1" && ! grep -q PostToolUse <<<"$1"' _ "$orch_default"
check "the orchestrator may read and write the ontology without asking, but not forget or import" bash -c '
  jq -e "(.permissions.allow | index(\"Bash(bosun kg propose:*)\") and index(\"Bash(bosun kg lookup:*)\") and index(\"Bash(bosun kg add-entity:*)\"))
    and ((.permissions.allow | map(select(test(\"kg (forget|import|identify)\"))) | length) == 0)" "$1"' _ "$ROOT/orchestrator/settings.json"
check "orchestrator settings are valid JSON" jq -e '.permissions.ask | index("Bash(gh pr merge:*)")' "$ROOT/orchestrator/settings.json"
guard_says() { # <command>: the guard's decision for an orchestrator Bash command ("" = allowed)
  jq -n --arg c "$1" '{tool_name: "Bash", tool_input: {command: $c}}' | "$BOSUN" guard |
    jq -r '.hookSpecificOutput.permissionDecision // ""'
}
# What the guard allows and denies is tests/unit/test_guard.py's (SendTest has these sends); here, bosun guard's wiring.
check "guard denies a free-text send that names no work item" test "$(guard_says 'bosun send w "Ignore the - I just sent"')" = deny

# Approvals off at launch: the orchestrator section. The refusals of approve and a failed call's settling are
# tests/unit/test_approvals.py's and test_post_tool_use.py's; here, the hand-over across processes and files.
section approvals "approvals" covers 'libexec/bosun-guard,libexec/bosun-launch,libexec/bosun-ledger'
out="$(cd "$REPO_A" && BOSUN_APPROVALS_ENABLED=1 BOSUN_ORCHESTRATOR=orch "$BOSUN" launch orch 2>&1)"
check "approvals on: both approval hooks, next to the settings' own guard" bash -c 'grep -q PermissionRequest <<<"$1" && grep -q -- "--permission-request" <<<"$1" && grep -q -- "--approvals" <<<"$1" && [ "$(grep -o "guard" <<<"$1" | wc -l)" -ge 3 ]' _ "$out"
check "approvals on: the post hook runs on PostToolUse and PostToolUseFailure" bash -c 'grep -qE "PostToolUse[^F]" <<<"$1" && grep -q PostToolUseFailure <<<"$1" && [ "$(grep -o -- "--post-tool-use" <<<"$1" | wc -l)" -eq 2 ]' _ "$out"
# State of its own, so the rest of the suite sees the ledger it expects.
ap() { BOSUN_STATE_DIR="$WORK/approvals-state" BOSUN_APPROVALS_GATED_COMMANDS=touch "$BOSUN" "$@"; }
# ledger approve runs from a terminal here: run from an agent session it checks my words there, and refuses
# where it cannot, and this run's own process is a running Claude's record with no transcript (above), so
# the record is set aside until the section ends, as the knowledge section does.
mv "$HOME/.claude/sessions/$$.json" "$WORK/own-session.json"
transcript="$WORK/orchestrator-transcript.jsonl"
: >"$transcript"
event="$(jq -nc --arg t "$transcript" --arg c "touch $WORK/approved" '{tool_name: "Bash", tool_input: {command: $c}, transcript_path: $t}')"
check "approval hooks decide nothing with approvals off" test -z "$(ap guard --approvals <<<"$event")$(ap guard --permission-request <<<"$event")"
export BOSUN_APPROVALS_ENABLED=1
check "an unproposed outward call is denied, saying how to propose it" contains "$(ap guard --approvals <<<"$event")" "bosun ledger approve '<id>' --quote"
check "the plain guard is unchanged with approvals on" test -z "$(ap guard <<<"$event")"
ap ledger upsert 'mypr:acme/api#7' --kind my-pr >/dev/null
ap ledger propose 'mypr:acme/api#7' --run "touch $WORK/approved" >/dev/null
printf '{"type":"user","timestamp":"%s","origin":{"kind":"human"},"message":{"role":"user","content":"1 and 3 yes"}}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$transcript"
ap ledger approve 'mypr:acme/api#7' --quote '1 and 3 yes' >/dev/null
check "an approved call whose quote I typed passes PreToolUse" test -z "$(ap guard --approvals <<<"$event")"
check "... and PermissionRequest allows it" test "$(ap guard --permission-request <<<"$event" | jq -r .hookSpecificOutput.decision.behavior)" = allow
check "... only once" test -z "$(ap guard --permission-request <<<"$event")"
check "ledger approvals lists it" contains "$(ap ledger approvals)" "approval-used (1 and 3 yes) Bash"
check "... and it is carried out, closing, until its result" contains "$(ap ledger list --proposals)" "PENDING (carried out, closing)"
post="$(jq -c '. + {hook_event_name: "PostToolUse", tool_use_id: "toolu_1", tool_response: {stdout: "", interrupted: false}}' <<<"$event")"
check "the post hook prints nothing" test -z "$(ap guard --post-tool-use <<<"$post")"
check "... and its success closed the proposal" test "$(ap ledger show 'mypr:acme/api#7' | jq -r '.proposal')" = null
check "... as carried out by the approved call" contains "$(ap ledger show 'mypr:acme/api#7' | jq -r '.history[-1].note')" "carried out by the approved call (Bash)"
unset BOSUN_APPROVALS_ENABLED
check "with approvals off the post hook does nothing" test -z "$(ap guard --post-tool-use <<<"$post")"
mv "$WORK/own-session.json" "$HOME/.claude/sessions/$$.json"

section connectors "connectors (fake claude mcp list)" covers 'libexec/bosun-doctor,libexec/bosun-launch,lib/connectors*.py,lib/claude_state.py,lib/codex_state.py'
# `claude mcp list` prints $WORK/mcp-list.out, or fails while there is none; each call is recorded.
cat >"$WORK/mcp-claude" <<FAKE
#!/bin/bash
[ "\$1 \$2" = "mcp list" ] || exit 0
echo "\$PWD" >>"$WORK/mcp-claude.calls"
[ "\${ENABLE_CLAUDEAI_MCP_SERVERS:-}" = true ] && [ -f "$WORK/mcp-list.out" ] || exit 1
cat "$WORK/mcp-list.out"
FAKE
chmod +x "$WORK/mcp-claude"
printf 'Checking MCP server health...\n\nclaude.ai Acme Chat: https://chat.example.com/mcp - ✔ Connected\n' >"$WORK/mcp-list.out"
# allows <launch output> <jq test of its allowedMcpServers, as $a>: the test holds, or say what it got.
allows() {
  local settings
  settings="$(launch_settings "$1")" || return 1
  jq -e "(.allowedMcpServers // \"no key\") as \$a | $2" <<<"$settings" >/dev/null || {
    echo "allowedMcpServers: $(jq -c '.allowedMcpServers // "no key"' <<<"$settings")"
    return 1
  }
}
CN=(env BOSUN_CLAUDE_BIN="$WORK/mcp-claude" BOSUN_ALERT_SLACK_USER="")
ORCH=("${CN[@]}" BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A")
# grants <launch output> <jq test of its additionalDirectories, as $d>: the test holds, or say what it got.
grants() {
  local settings
  settings="$(launch_settings "$1")" || return 1
  jq -e "(.permissions.additionalDirectories // \"no key\") as \$d | $2" <<<"$settings" >/dev/null || {
    echo "additionalDirectories: $(jq -c '.permissions.additionalDirectories // "no key"' <<<"$settings")"
    return 1
  }
}
# A git repository with a folder in it and a linked worktree in its worktrees folder.
GRANT_REPO="$WORK/grants/exporter"
git init -q "$GRANT_REPO"
GRANT_REPO="$(cd "$GRANT_REPO" && pwd -P)"
mkdir "$GRANT_REPO/src"
git -C "$GRANT_REPO" -c user.name=smoke -c user.email=smoke@example.com -c commit.gpgsign=false commit -q --allow-empty -m init
git -C "$GRANT_REPO" worktree add -q "$GRANT_REPO.worktrees/fix-qty" -b fix-qty
# The grant is the folder worktrees_folder (lib/common.sh) names, or no key when it names none: two launches show
# that, and the folder each other place names is the function's, asked there.
check "a repo window's settings grant it its repository's worktrees folder, and nothing more (exporter)" \
  grants "$(cd "$GRANT_REPO" && "$BOSUN" launch alpha 2>&1)" "\$d == [\"$GRANT_REPO.worktrees\"]"
mkdir -p "$HOME/directly-in-home"
check "... and none for a window outside a git repository" grants "$(cd "$HOME/directly-in-home" && "$BOSUN" launch alpha 2>&1)" '$d == "no key"'
worktrees_folder_in() { (cd "$1" && bash -c 'source "$1/lib/common.sh" && worktrees_folder' _ "$ROOT"); }
for dir in "$GRANT_REPO/src" "$GRANT_REPO.worktrees/fix-qty"; do
  check "... the same folder from a folder in it or a linked worktree (${dir#"$(dirname "$GRANT_REPO")/"})" \
    test "$(worktrees_folder_in "$dir")" = "$GRANT_REPO.worktrees"
done
check "... or none in a folder directly in /" test -z "$(worktrees_folder_in /usr)"
git init -q "$WORK/grants/a-home"
check "... or for a repository that is the home folder (which elsewhere gets one)" bash -c '
  cd "$2" && [ -z "$(HOME="$PWD" bash -c "source \"\$1/lib/common.sh\" && worktrees_folder" _ "$1")" ] &&
    [ "$(bash -c "source \"\$1/lib/common.sh\" && worktrees_folder" _ "$1")" = "$(pwd -P).worktrees" ]' _ "$ROOT" "$WORK/grants/a-home"
check "a repo window reaches no connector by default" allows "$(cd "$REPO_A" && "${CN[@]}" "$BOSUN" launch alpha 2>&1)" '$a == []'
check "... and every one with BOSUN_CONNECTORS=all" allows "$(cd "$REPO_A" && "${CN[@]}" BOSUN_CONNECTORS=all "$BOSUN" launch alpha 2>&1)" '$a == "no key"'
check "... and looked up none of them" test ! -e "$WORK/mcp-claude.calls"
out="$(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat" "$BOSUN" launch orch 2>&1)"
check "the orchestrator reaches a claude.ai connector by the URL claude mcp list shows" allows "$out" '$a == [{"serverUrl": "https://chat.example.com/mcp"}]'
check "... looked up once per launch, in the window's folder" test "$(cat "$WORK/mcp-claude.calls")" = "$REPO_A"
check "... and kept for when the lookup fails" jq -e '."claude.ai Acme Chat" == "https://chat.example.com/mcp"' "$BOSUN_STATE_DIR/connectors.json"
check "... and nothing was missing, so no alert" not_contains "$out" "started without connector"
check "the orchestrator gets no worktrees folder" grants "$out" '$d == "no key"'
rm "$WORK/mcp-list.out"
out="$(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat" "$BOSUN" launch orch 2>&1)"
check "a failed lookup uses the URL kept" allows "$out" '$a == [{"serverUrl": "https://chat.example.com/mcp"}]'
check "... and says so" contains "$out" "claude mcp list failed"
printf 'claude.ai Acme Chat: https://chat.example.com/mcp - ✔ Connected\n' >"$WORK/mcp-list.out"
out="$(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat, claude.ai Acme Gone" "$BOSUN" launch orch 2>&1)"
check "a name not found is left out" allows "$out" '$a == [{"serverUrl": "https://chat.example.com/mcp"}]'
check "... logged" contains "$out" "connectors: claude.ai Acme Gone not found, so this window starts without it"
LAUNCH_LOG="$BOSUN_LOG_DIR/launch-$(date +%F).log"
check "... in the launch log too, as is a failed lookup" bash -c '
  grep -qF "orch: connectors: claude.ai Acme Gone not found, so this window starts without it" "$1" && grep -qF "orch: connectors: claude mcp list failed" "$1"' _ "$LAUNCH_LOG"
check "... and the orchestrator's launch alerts me, the alert's output in the launch log" wait_for 30 grep -qF \
  "alert (notification only; BOSUN_ALERT_SLACK_USER is empty): orch: started without connector(s) it may use: claude.ai Acme Gone" "$LAUNCH_LOG"
check "... never on the window's screen" bash -c 'grep -q "alerting you that it started without connector(s) it may use: claude.ai Acme Gone" <<<"$1" && ! grep -q "notification only" <<<"$1"' _ "$out"
out="$(cd "$REPO_A" && "${CN[@]}" BOSUN_CONNECTORS="claude.ai Acme Gone" "$BOSUN" launch alpha 2>&1)"
check "a repo window's missing name is logged, without an alert" bash -c 'grep -q "claude.ai Acme Gone not found" <<<"$1" && ! grep -q "started without connector" <<<"$1"' _ "$out"
: >"$WORK/mcp-claude.calls"
out="$(cd / && "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat, claude.ai Acme Gone" "$BOSUN" launch --fingerprint orch 2>/dev/null)"
check "launch --fingerprint looks nothing up" bash -c '[ ! -s "$1" ] && jq -e .args <<<"$2" >/dev/null' _ "$WORK/mcp-claude.calls" "$out"
check "... and agrees with the launch, from what was kept" bash -c '[ "$(jq -cS .fingerprint "$1")" = "$(jq -cS . <<<"$2")" ]' _ "$BOSUN_STATE_DIR/launches/smoke/orch.json" "$out"
# A connector once kept that a later lookup no longer shows: the launch leaves it out, so must the fingerprint.
printf 'claude.ai Acme Chat: https://chat.example.com/mcp - ✔ Connected\nclaude.ai Acme Old: https://old.example.com/mcp - ✔ Connected\n' >"$WORK/mcp-list.out"
(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat, claude.ai Acme Old" "$BOSUN" launch orch >/dev/null 2>&1)
printf 'claude.ai Acme Chat: https://chat.example.com/mcp - ✔ Connected\n' >"$WORK/mcp-list.out"
out="$(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat, claude.ai Acme Old" "$BOSUN" launch orch 2>&1)"
check "a kept connector the lookup no longer shows is left out, and dropped from what was kept" bash -c '
  jq -e "has(\"claude.ai Acme Old\") | not" "$2" >/dev/null && grep -q "claude.ai Acme Old not found" <<<"$1"' _ "$out" "$BOSUN_STATE_DIR/connectors.json"
check "... so the fingerprint agrees with that launch" bash -c '[ "$(jq -cS .fingerprint "$1")" = "$(cd / && "${@:2}" launch --fingerprint orch 2>/dev/null | jq -cS .)" ]' \
  _ "$BOSUN_STATE_DIR/launches/smoke/orch.json" "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat, claude.ai Acme Old" "$BOSUN"
# A plugin's server only the listing shows resolves to its command there: test_connectors_claude.py, on the same listing.
# Fail closed in the shell too: a resolver answer that is not one JSON object, settings built without the
# window's limit, or settings asked for before the lookup ran, each stop the launch.
check "a resolver answer that is not one JSON object stops the launch" bash -c '
  source "$1/lib/common.sh"; load_config
  for answer in "not json" "{}{}" "[]" "{}" "{\"allowedMcpServers\": {}}"; do
    python3() { printf "%s\n" "$answer"; }
    ! resolve_connectors alpha acme-local "$PWD" 0 >/dev/null 2>&1 || { echo "took: $answer"; exit 1; }
  done' _ "$ROOT"
check "... but {} (no limit) for the list all" bash -c '
  source "$1/lib/common.sh"; load_config
  python3() { echo "{}"; }
  [ "$(resolve_connectors alpha " all " "$PWD" 0)" = "{}" ]' _ "$ROOT"
check "settings that lost the window's limit are refused" fails_with "lost its connector limit" bash -c '
  source "$1/lib/common.sh"; load_config
  model_effort_args=(--model opus --settings "{\"effortLevel\":\"medium\"}")
  require_connector_limit alpha "{\"allowedMcpServers\":[]}"' _ "$ROOT"
check "... as is connector JSON that cannot be read, never taken for no limit" fails_with "cannot be read; not starting it with every server" bash -c '
  source "$1/lib/common.sh"; load_config
  model_effort_args=(--model opus)
  require_connector_limit alpha "not json"' _ "$ROOT"
check "... and settings asked for before the lookup ran" fails_with "were not resolved before" bash -c '
  source "$1/lib/common.sh"; load_config; connector_settings alpha' _ "$ROOT"
check "a list naming all and more is refused" fails_with "stands alone" "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="all, claude.ai Acme Chat" "$BOSUN" launch orch
cat >"$WORK/connectors-sessions.yml" <<YAML
sessions:
  - name: alpha
    dir: $REPO_A
    connectors: acme-local, https://mcp.example.com/*
  - name: beta
    dir: $REPO_A
    connectors: [acme-local, 'https://mcp.example.com/a"b\$(touch $WORK/pwned)*']
  - name: gamma
    dir: $REPO_A
    connectors: []
YAML
SN=("${CN[@]}" BOSUN_SESSIONS_FILE="$WORK/connectors-sessions.yml" BOSUN_CONNECTORS=acme-default)
TRICKY="https://mcp.example.com/a\"b\$(touch $WORK/pwned)*"
check "connectors: in sessions.yml as a string" allows "$(cd "$REPO_A" && "${SN[@]}" "$BOSUN" launch alpha 2>&1)" '$a == [{"serverName": "acme-local"}, {"serverUrl": "https://mcp.example.com/*"}]'
check "... as a list, a name with \" and \$( reaching the JSON as data" allows "$(cd "$REPO_A" && "${SN[@]}" "$BOSUN" launch beta 2>&1)" "\$a == [{\"serverName\": \"acme-local\"}, {\"serverUrl\": $(jq -n --arg t "$TRICKY" '$t')}]"
check "... never run" test ! -e "$WORK/pwned"
cat >"$WORK/connectors-odd-sessions.yml" <<YAML
sessions:
  - name: delta
    dir: $REPO_A
    connectors: ["claude.ai Acme, Inc"]
  - name: epsilon
    dir: $REPO_A
    connectors: no
YAML
ODD=("${CN[@]}" BOSUN_SESSIONS_FILE="$WORK/connectors-odd-sessions.yml")
check "... a list entry holding a comma is refused, never split into two names" fails_with \
  "the connectors of delta must be a comma-separated string or a list of names, none holding a comma" bash -c 'cd "$1" && "${@:2}" launch delta' _ "$REPO_A" "${ODD[@]}" "$BOSUN"
check "... as is a value that is no string, such as connectors: no (YAML's false)" fails_with \
  "the connectors of epsilon must be a comma-separated string or a list of names" bash -c 'cd "$1" && "${@:2}" launch epsilon' _ "$REPO_A" "${ODD[@]}" "$BOSUN"
check "... and doctor says why" contains "$("${ODD[@]}" "$BOSUN" doctor 2>&1)" "FAIL  epsilon: could not read its connectors: $WORK/connectors-odd-sessions.yml: the connectors of epsilon must be"
check "... as [] (none), over BOSUN_CONNECTORS" allows "$(cd "$REPO_A" && "${SN[@]}" "$BOSUN" launch gamma 2>&1)" '$a == []'
check "... and BOSUN_CONNECTORS for a window without the key" allows "$(cd "$REPO_A" && "${SN[@]}" BOSUN_SESSIONS_FILE="$BOSUN_CONFIG_DIR/sessions.yml" "$BOSUN" launch alpha 2>&1)" '$a == [{"serverName": "acme-default"}]'
out="$(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="$TRICKY" "$BOSUN" launch orch 2>&1)"
check "the same name in BOSUN_ORCHESTRATOR_CONNECTORS" allows "$out" "\$a == [{\"serverUrl\": $(jq -n --arg t "$TRICKY" '$t')}]"
check "... never run" test ! -e "$WORK/pwned"
out="$(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme \"Mail\" \$(touch $WORK/pwned)" "$BOSUN" launch orch 2>&1)"
check "... and in an alert" wait_for 30 bash -c 'grep -qF "notification only; BOSUN_ALERT_SLACK_USER is empty): orch: started without connector(s) it may use: claude.ai Acme \"Mail\" \$(touch $2/pwned)" "$1" && [ ! -e "$2/pwned" ]' _ "$LAUNCH_LOG" "$WORK"
jq '. + {allowedMcpServers: [{serverName: "site-wide"}]}' "$ROOT/orchestrator/settings.json" >"$WORK/site-mcp-settings.json"
check "a site settings file's own allowedMcpServers is replaced by the window's list" \
  allows "$(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_SETTINGS="$WORK/site-mcp-settings.json" BOSUN_ORCHESTRATOR_CONNECTORS=acme-local "$BOSUN" launch orch 2>&1)" '$a == [{"serverName": "acme-local"}]'
check "... and left as it is with all" \
  allows "$(cd "$REPO_A" && "${ORCH[@]}" BOSUN_ORCHESTRATOR_SETTINGS="$WORK/site-mcp-settings.json" BOSUN_ORCHESTRATOR_CONNECTORS=all "$BOSUN" launch orch 2>&1)" '$a == [{"serverName": "site-wide"}]'
check "doctor says which applies" contains "$("${ORCH[@]}" BOSUN_ORCHESTRATOR_SETTINGS="$WORK/site-mcp-settings.json" BOSUN_ORCHESTRATOR_CONNECTORS=acme-local "$BOSUN" doctor 2>&1)" \
  "ok    orch: allowed by name: acme-local; its settings file ($WORK/site-mcp-settings.json) holds an allowedMcpServers, which this list replaces"
# Allow lists in other settings scopes add to a window's (Claude Code), so a launch with a list refuses one
# that allows more than the list names (that it starts once the list names it, or with all, and doctor's line for
# the project's scope, are test_connectors_claude.py's; doctor's line for my user scope is below).
mkdir -p "$REPO_A/.claude"
echo '{"allowedMcpServers": [{"serverName": "acme-repo"}]}' >"$REPO_A/.claude/settings.json"
check "a repo whose own settings allow a server its window's list does not name does not start" fails_with \
  "connectors: $REPO_A/.claude/settings.json allows [{\"serverName\": \"acme-repo\"}], beyond this window's list, and Claude Code adds it to it: name it in the window's list, use all, or remove it from that file" \
  bash -c 'cd "$1" && "${@:2}" launch alpha' _ "$REPO_A" "${CN[@]}" "$BOSUN"
rm -rf "$REPO_A/.claude/settings.json"
# bosun doctor: a line per window, resolved from what earlier starts kept (claude.ai Acme Chat), looking
# nothing up; and a warning when my own Claude Code settings hold an allow list, which widens every window's.
: >"$WORK/mcp-claude.calls"
out="$("${SN[@]}" BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A" BOSUN_ORCHESTRATOR_CONNECTORS=all "$BOSUN" doctor 2>&1)"
check "doctor says what each window reaches: a few" contains "$out" "ok    alpha: allowed by name: acme-local; allowed by pattern: https://mcp.example.com/*"
check "... none" contains "$out" "ok    gamma: none (no MCP server or connector)"
# The suite sets BOSUN_PLAIN_FSYNC for the commands it runs (it is for tests only): doctor says so while it is set, and
# not otherwise.
PLAIN_FSYNC_LINE="warn  BOSUN_PLAIN_FSYNC is set: writes are not flushed to the drive; it is for the test suite only"
check "doctor warns while BOSUN_PLAIN_FSYNC is set, as the suite sets it" contains "$out" "$PLAIN_FSYNC_LINE"
check "... and warns while the orchestrator keeps every connector" contains "$out" \
  "warn  orch: all (every MCP server and connector): name the ones it needs in BOSUN_ORCHESTRATOR_CONNECTORS and drop all"
out="$("${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat, claude.ai Acme Gone" "$BOSUN" doctor 2>&1)"
check "... names what it cannot find" contains "$out" "warn  orch: reaches claude.ai Acme Chat; not among the servers earlier starts looked up: claude.ai Acme Gone"
check "... and looks nothing up" test ! -s "$WORK/mcp-claude.calls"
# An orchestrator whose bosun.env does not set its list (none there, or no bosun.env) had every connector
# before the key: doctor says it now has none, until the key is set, to nothing too.
# (A bosun.env without the key reads the same: test_migrations.py.) The same run, without BOSUN_PLAIN_FSYNC, shows
# doctor quiet about it.
UNSET_LINE="warn  BOSUN_ORCHESTRATOR_CONNECTORS is not set in bosun.env: before this version the orchestrator had every connector, and now it starts with none"
out="$(env -u BOSUN_PLAIN_FSYNC "${ORCH[@]}" "$BOSUN" doctor 2>&1)"
check "doctor warns while there is no bosun.env to set the orchestrator's list" contains "$out" "$UNSET_LINE"
check "... and is quiet about BOSUN_PLAIN_FSYNC when it is not set" bash -c 'grep -qF "$2" <<<"$1" && ! grep -qF "BOSUN_PLAIN_FSYNC" <<<"$1"' _ "$out" "$UNSET_LINE"
echo 'BOSUN_ORCHESTRATOR_CONNECTORS=""' >"$BOSUN_CONFIG_DIR/bosun.env"
# The window list is read once, its stdout only: what it says on stderr while it works (here the orchestrator's
# check-in loop reading a word where the minutes go) is no window of the connectors' or the models' lines. The same
# run (its stdout) shows the orchestrator's list set to nothing.
out="$("${ORCH[@]}" BOSUN_CHECKIN_MINUTES=abc "$BOSUN" doctor 2>/dev/null)"
check "... but not once it is set, to nothing too" bash -c '! grep -qF "$2" <<<"$1" && grep -qF "ok    orch: none (no MCP server or connector)" <<<"$1"' \
  _ "$out" "$UNSET_LINE"
check "doctor's window list is the list's stdout: its stderr is no window" bash -c '
  grep -q "^Connectors" <<<"$1" && ! sed -n "/^Connectors/,/^Schedule/p" <<<"$1" | grep -q "integer expression expected"' _ "$out"
printf 'BOSUN_CHECKIN_MINS=30\nBOSUN_CHECKIN_MINUTES=abc\n' >"$BOSUN_CONFIG_DIR/bosun.env"
out="$("$BOSUN" doctor 2>&1)"
out_status=$?
check "doctor names a key bosun.env sets that bosun does not read, and the one it most likely meant" contains "$out" \
  "warn  bosun.env sets BOSUN_CHECKIN_MINS: bosun does not read it: did you mean BOSUN_CHECKIN_MINUTES?"
check "... and a word where a number goes" contains "$out" "warn  BOSUN_CHECKIN_MINUTES is 'abc', not a whole number"
# doctor's checks run side by side, each into files of its own in a temporary folder; without one, they run one after
# another, and the report is the same, line for line (times of day aside), with the same exit status.
check "doctor without a temporary folder runs its checks one after another and says the same" bash -c '
  untimed() { sed -E "s/[0-9]{2}:[0-9]{2}:[0-9]{2}//"; }
  said="$(TMPDIR=/nonexistent "$2" doctor 2>&1)"; status=$?
  [ "$status" = "$3" ] && [ "$(untimed <<<"$said")" = "$(untimed <<<"$1")" ] || { diff <(untimed <<<"$1") <(untimed <<<"$said"); exit 1; }' \
  _ "$out" "$BOSUN" "$out_status"
# Its tools as launchd's PATH finds them: claude, the last of the list, not found is a failure of its own (a line per
# tool, however many at the end are missing; launchd_paths in common-functions for one in the middle).
out_missing="$(BOSUN_CLAUDE_BIN=acme-missing-claude "$BOSUN" doctor 2>&1)"
missing_status=$?
check "doctor fails a claude that launchd's PATH does not find, the last tool of its list, and exits 1" bash -c '
  grep -qxF "  FAIL  acme-missing-claude not found on launchd PATH" <<<"$1" && [ "$2" = 1 ]' _ "$out_missing" "$missing_status"
# The windows' agents asked at once (lib/agent_state.py windows): an answer that fails as a whole (no python3, say)
# is one failure saying so, not a trust failure and a new session for every window. A python3 that fails only for
# that, for one doctor run.
printf '#!/bin/sh\ncase "$1 $2" in */agent_state.py\\ windows) echo "acme: agent_state could not start" >&2; exit 1 ;; esac\nexec "%s" "$@"\n' \
  "$(command -v python3)" >"$HOME/.local/bin/python3"
chmod +x "$HOME/.local/bin/python3"
out_unasked="$("$BOSUN" doctor 2>&1)"
rm -f "$HOME/.local/bin/python3"
check "doctor says once that the windows' agents could not be asked, and why, and nothing of each window's trust" bash -c '
  [ "$(grep -c "FAIL  could not ask the windows.* agents whether they trust their folders" <<<"$1")" = 1 ] &&
    grep -q "(lib/agent_state.py windows): acme: agent_state could not start$" <<<"$1" &&
    ! grep -qE "does not trust|no session with this exact name" <<<"$1"' _ "$out_unasked"
# ... and one that answers too few lines and says nothing on stderr is told by what it did.
printf '#!/bin/sh\ncase "$1 $2" in */agent_state.py\\ windows) exit 0 ;; esac\nexec "%s" "$@"\n' "$(command -v python3)" >"$HOME/.local/bin/python3"
chmod +x "$HOME/.local/bin/python3"
out_unasked="$("$BOSUN" doctor 2>&1)"
rm -f "$HOME/.local/bin/python3"
check "... with what it did for a reason when it said nothing" contains "$out_unasked" \
  "(lib/agent_state.py windows): it exited 0 with 0 line(s) for 3 window(s)"
# doctor cut short (a TERM here; Ctrl-C ends it the same way) stops what its checks run, by pid: here a claude that
# hangs on `auth status`, which preflight and the login check ask (their own timeout is 30 s).
cat >"$WORK/hang-claude" <<FAKE
#!/bin/bash
case "\$1" in
  --version) echo "2.0.0 (Claude Code)" ;;
  auth) echo \$\$ >>"$WORK/hang-claude.pids"; exec sleep 60 ;;
esac
FAKE
chmod +x "$WORK/hang-claude"
rm -f "$WORK/hang-claude.pids"
BOSUN_CLAUDE_BIN="$WORK/hang-claude" "$BOSUN" doctor >/dev/null 2>&1 &
hung_doctor=$!
# Both askers hang (preflight's and the login check's), so neither is about to start its claude while doctor stops it.
wait_for 30 bash -c '[ "$(wc -l <"$1")" -ge 2 ]' _ "$WORK/hang-claude.pids"
# Its checks (its own children: a shell each), then a second TERM while it stops them, as an impatient Ctrl-C does:
# it is ignored, so no check is left frozen.
ps -Ao pid=,ppid= | awk -v doctor="$hung_doctor" '$2 == doctor { print $1 }' >>"$WORK/hang-claude.pids"
kill -TERM "$hung_doctor"
sleep 0.1
kill -TERM "$hung_doctor" 2>/dev/null
wait "$hung_doctor" 2>/dev/null
# None of those runs once doctor is gone (30 s of looks, only hang protection); if one does, it is named.
check "doctor cut short, twice, leaves nothing its checks started running, nor a check of its own" bash -c '
  left() { while read -r pid; do kill -0 "$pid" 2>/dev/null && ps -o pid=,ppid=,stat=,command= -p "$pid"; done <"$1"; }
  for _ in $(seq 1 150); do [ -z "$(left "$1")" ] && exit 0; sleep 0.2; done
  left "$1"; exit 1' _ "$WORK/hang-claude.pids"
while read -r hung; do kill "$hung" 2>/dev/null && kill -CONT "$hung" 2>/dev/null; done <"$WORK/hang-claude.pids"
rm -f "$BOSUN_CONFIG_DIR/bosun.env"
mkdir -p "$HOME/.claude"
echo '{"allowedMcpServers": [{"serverName": "acme-local"}]}' >"$HOME/.claude/settings.json"
out="$("${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat" "$BOSUN" doctor 2>&1)"
check "doctor says a window that my own Claude Code settings would widen does not start" contains "$out" \
  "warn  orch: reaches claude.ai Acme Chat; does not start: $HOME/.claude/settings.json allows [{\"serverName\": \"acme-local\"}]"
check "... and so does its launch" fails_with "$HOME/.claude/settings.json allows" \
  bash -c 'cd "$1" && "${@:2}" launch orch' _ "$REPO_A" "${ORCH[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat" "$BOSUN"
rm -f "$HOME/.claude/settings.json"
rm -f "$WORK/mcp-claude.calls" "$BOSUN_STATE_DIR/connectors.json" "$LAUNCH_LOG"

# Which plugin each list keeps (by bare name or id), the scopes merged and doctor's every text are
# tests/unit/test_plugins.py's and test_plugins_claude.py's; here, the launch's settings and doctor's wiring.
section plugins "plugins of mine a window loads (fixture Claude Code settings)" covers 'libexec/bosun-doctor,libexec/bosun-launch,lib/plugins*.py,lib/claude_state.py,lib/connectors*.py'
# My settings enable two plugins and switch a third off; one of them is installed with an MCP server of its own.
mkdir -p "$HOME/.claude/plugins" "$WORK/plugins/acme-helper"
echo '{"enabledPlugins": {"probe@local": true, "acme-helper@acme-market": true, "acme-off@acme-market": false}}' >"$HOME/.claude/settings.json"
printf '{"plugins": {"acme-helper@acme-market": [{"scope": "user", "installPath": "%s"}]}}\n' "$WORK/plugins/acme-helper" >"$HOME/.claude/plugins/installed_plugins.json"
echo '{"mcpServers": {"helper": {"command": "/bin/echo", "args": ["helper"]}}}' >"$WORK/plugins/acme-helper/.mcp.json"
PL=(env BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A")
# switches <launch output> <jq test of its enabledPlugins, as $p>: the test holds, or say what it got.
switches() {
  local settings
  settings="$(launch_settings "$1")" || return 1
  jq -e "(.enabledPlugins // \"no key\") as \$p | $2" <<<"$settings" >/dev/null || {
    echo "enabledPlugins: $(jq -c '.enabledPlugins // "no key"' <<<"$settings")"
    return 1
  }
}
out="$(cd "$REPO_A" && "${PL[@]}" "$BOSUN" launch orch 2>&1)"
check "the orchestrator's launch switches off each plugin of mine my settings enable" \
  switches "$out" '$p == {"acme-helper@acme-market": false, "probe@local": false}'
check "... but bosun's own plugin loads" contains "$out" "--plugin-dir $ROOT/orchestrator/plugin"
out="$(cd "$REPO_A" && "${PL[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="plugin:acme-helper:helper" "$BOSUN" launch orch 2>&1)"
check "... and one whose server its connectors name" switches "$out" '$p == {"probe@local": false}'
out="$(cd "$REPO_A" && "${PL[@]}" BOSUN_ORCHESTRATOR_PLUGINS="acme-gone" "$BOSUN" launch orch 2>&1)"
check "a name no plugin has is left out and logged, never fatal" bash -c '
  grep -qF "orch: plugins: acme-gone not found, so this window starts without it" <<<"$1" && grep -q "dry-run: would run" <<<"$1"' _ "$out"
check "the orchestrator with all gets no plugin settings" switches "$(cd "$REPO_A" && "${PL[@]}" BOSUN_ORCHESTRATOR_PLUGINS=all "$BOSUN" launch orch 2>&1)" '$p == "no key"'
check "a repo window keeps every plugin by default" switches "$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)" '$p == "no key"'
cat >"$WORK/plugins-sessions.yml" <<YAML
sessions:
  - name: alpha
    dir: $REPO_A
    plugins: []
  - name: beta
    dir: $REPO_A
    plugins: [acme-helper@acme-market]
  - name: epsilon
    dir: $REPO_A
    plugins: no
YAML
PS=(env BOSUN_SESSIONS_FILE="$WORK/plugins-sessions.yml")
check "plugins: [] in sessions.yml switches them off" \
  switches "$(cd "$REPO_A" && "${PS[@]}" "$BOSUN" launch alpha 2>&1)" '$p == {"acme-helper@acme-market": false, "probe@local": false}'
check "... and a value that is no list is refused, as for connectors" fails_with \
  "the plugins of epsilon must be a comma-separated string or a list of names" bash -c 'cd "$1" && "${@:2}" launch epsilon' _ "$REPO_A" "${PS[@]}" "$BOSUN"
# A site settings file that enables a plugin: its own enabledPlugins merges under the launch's, so the launch
# leaves that plugin on instead of overriding the file.
jq '. + {enabledPlugins: {"acme-site@acme-market": true}}' "$ROOT/orchestrator/settings.json" >"$WORK/site-plugin-settings.json"
out="$(cd "$REPO_A" && "${PL[@]}" BOSUN_ORCHESTRATOR_SETTINGS="$WORK/site-plugin-settings.json" "$BOSUN" launch orch 2>&1)"
check "a plugin the orchestrator's settings file enables stays on, as the file says" \
  switches "$out" '$p == {"acme-helper@acme-market": false, "acme-site@acme-market": true, "probe@local": false}'
# A settings scope that does not parse: said, and the installed plugin still switched off; an installed_plugins.json
# that does not parse: the launch refuses, as nothing can tell which plugins to switch off.
mkdir -p "$REPO_A/.claude"
echo '{not json' >"$REPO_A/.claude/settings.json"
out="$(cd "$REPO_A" && "${PS[@]}" "$BOSUN" launch alpha 2>&1)"
check "a settings file that does not parse is logged, and the window still starts with its plugins off" bash -c '
  grep -qF "alpha: plugins: $2/.claude/settings.json does not parse, so what it says of plugins is not read" <<<"$1"' _ "$out" "$REPO_A"
check "... the installed ones still off" switches "$out" '$p == {"acme-helper@acme-market": false, "probe@local": false}'
rm -f "$REPO_A/.claude/settings.json"
cp "$HOME/.claude/plugins/installed_plugins.json" "$WORK/installed_plugins.kept"
echo '[not an object]' >"$HOME/.claude/plugins/installed_plugins.json"
check "an installed_plugins.json that does not parse keeps a window with a list from starting" fails_with \
  "plugins: $HOME/.claude/plugins/installed_plugins.json does not parse, so bosun cannot tell which plugins to switch off" \
  bash -c 'cd "$1" && "${@:2}" launch alpha' _ "$REPO_A" "${PS[@]}" "$BOSUN"
check "... and its fingerprint is refused, the reason last on stderr (what an update says of it)" bash -c '
  out="$("${@:2}" launch --fingerprint alpha 2>&1 >/dev/null)"
  [ $? = 4 ] && [ "$(tail -n 1 <<<"$out")" = "plugins: $1 does not parse, so bosun cannot tell which plugins to switch off" ]' \
  _ "$HOME/.claude/plugins/installed_plugins.json" "${PS[@]}" "$BOSUN"
# The same doctor run shows epsilon's lines, whose plugins: is no list (its connectors line comes first, read
# without the plugins file).
out="$("${PS[@]}" "$BOSUN" doctor 2>&1)"
check "... and doctor says it does not start" contains "$out" \
  "warn  alpha: does not start: $HOME/.claude/plugins/installed_plugins.json does not parse"
check "doctor says what it reaches before a plugins: it cannot read" bash -c '
  grep -q "  ok    epsilon: none (no MCP server or connector)" <<<"$1" && grep -q "FAIL  epsilon: could not read its plugins: .*the plugins of epsilon must be" <<<"$1"' \
  _ "$out"
check "... but a window with all starts" switches "$(cd "$REPO_A" && "$BOSUN" launch alpha 2>&1)" '$p == "no key"'
mv "$WORK/installed_plugins.kept" "$HOME/.claude/plugins/installed_plugins.json"
# One run for what keeps a plugin on: a server the connectors name, and the orchestrator's settings file.
out="$("${PL[@]}" BOSUN_ORCHESTRATOR_CONNECTORS="plugin:acme-helper:helper" BOSUN_ORCHESTRATOR_SETTINGS="$WORK/site-plugin-settings.json" "$BOSUN" doctor 2>&1)"
check "doctor says what plugins each window loads: the orchestrator's, and why it keeps one (connectors, its settings file)" contains "$out" \
  "ok    orch: plugins: none of yours (1 switched off); keeps acme-helper@acme-market for its server in connectors; kept: the orchestrator's settings file enables acme-site@acme-market"
check "... and a repo window's" contains "$out" "ok    alpha: plugins: all of yours"
# One for two warnings, the orchestrator's and a repo window's.
out="$("${PL[@]}" BOSUN_ORCHESTRATOR_PLUGINS=all BOSUN_PLUGINS=acme-gone "$BOSUN" doctor 2>&1)"
check "... and warns of an orchestrator kept at all, saying how to narrow it" contains "$out" \
  "warn  orch: plugins: all of yours. A plugin's hook or skill can stop its unattended runs at a question; name the ones it needs in BOSUN_ORCHESTRATOR_PLUGINS, or set it empty for none"
check "... and warns of a name not found" contains "$out" \
  "warn  alpha: plugins: none of yours (2 switched off); plugins not found, so it starts without: acme-gone"
(cd "$REPO_A" && "${PL[@]}" "$BOSUN" launch orch >/dev/null 2>&1)
check "the fingerprint agrees with the launch" bash -c '
  [ "$(jq -cS .fingerprint "$1")" = "$(cd / && "${@:2}" launch --fingerprint orch 2>/dev/null | jq -cS .)" ]' \
  _ "$BOSUN_STATE_DIR/launches/smoke/orch.json" "${PL[@]}" "$BOSUN"
check "... and holds the plugin settings: a change of the orchestrator's list changes it (an update restarts it)" bash -c '
  [ "$(jq -cS .fingerprint "$1")" != "$(cd / && "${@:2}" launch --fingerprint orch 2>/dev/null | jq -cS .)" ]' \
  _ "$BOSUN_STATE_DIR/launches/smoke/orch.json" "${PL[@]}" BOSUN_ORCHESTRATOR_PLUGINS=all "$BOSUN"
# Fail closed in the shell: an answer not of the shape a list gets, or settings that lost a `false`, stop the launch.
check "a plugin resolver answer not of its shape stops the launch" bash -c '
  source "$1/lib/common.sh"; load_config
  for answer in "not json" "{}{}" "[]" "{}" "{\"enabledPlugins\": []}" "{\"enabledPlugins\": {\"x@y\": true}}"; do
    python3() { printf "%s\n" "$answer"; }
    ! resolve_plugins alpha "" "" "$PWD" >/dev/null 2>&1 || { echo "took: $answer"; exit 1; }
  done
  python3() { echo "{\"enabledPlugins\": {}}"; }
  ! resolve_plugins alpha all "" "$PWD" >/dev/null 2>&1 || { echo "took a table for all"; exit 1; }' _ "$ROOT"
check "settings that lost a plugin's false are refused" fails_with "lost its plugin limit" bash -c '
  source "$1/lib/common.sh"; load_config
  model_effort_args=(--model opus --settings "{\"enabledPlugins\":{\"probe@local\":true}}")
  require_plugin_limit alpha "{\"enabledPlugins\":{\"probe@local\":false}}"' _ "$ROOT"
check "... and settings asked for before the plugins were resolved" fails_with "were not resolved before" bash -c '
  source "$1/lib/common.sh"; load_config; plugin_settings alpha' _ "$ROOT"
rm -rf "$HOME/.claude/settings.json" "$HOME/.claude/plugins" "$WORK/plugins" "$WORK/site-plugin-settings.json"

# The reads (the context pack, search, a reviewer's preference, lint, forget --dry-run) are tests/unit/test_knowledge.py's,
# through the same command line; here, the writes: git, the journal, view.json, hand edits and real process records.
section knowledge "knowledge (kg, fixture acme knowledge base)" covers 'libexec/bosun-kg,libexec/bosun-context,tests/fixtures/knowledge/**'
# kg runs from a terminal here: kg asks the agent session that ran it for my words, and this run's own process
# is a running Claude's record (above), so the record is set aside until the session checks at the end.
mv "$HOME/.claude/sessions/$$.json" "$WORK/own-session.json"
out="$(kg import "$KG_FIXTURE" 2>&1)"
check "import is a dry run first: counts and refusals, nothing written" bash -c 'grep -q "confirmed automatically: 0; waiting for review: 20" <<<"$1" && grep -q "refused: 2 people whitelist" <<<"$1" && [ ! -e "$2" ]' _ "$out" "$KG_DIR"
check "import --apply --auto-confirm writes the knowledge base" contains "$(kg import "$KG_FIXTURE" --apply --auto-confirm 2>&1)" "confirmed automatically: 6; waiting for review: 14"
check "stats counts it" contains "$(kg stats)" "15 entities, 20 facts (14 proposed, 6 confirmed"
if command -v git >/dev/null; then
  check "every change is a commit in the knowledge directory's own history" test "$(env -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE git -C "$KG_DIR" log --format=%s | head -1)" = "kg import acme-import.jsonl"
fi
check "a person is refused anything outside the whitelist" fails_with "people whitelist" kg propose person/lee mood --value calm --source ledger:x
check "every write goes through the journal first, and view.json says how far the files reflect it" bash -c '
  journal="$1/journal" && [ -s "$journal/local.jsonl" ] && ls "$journal" | grep -Eq "^[0-9a-f]{32}\.jsonl$" &&
    [ "$(jq -r .journal.local "$1/view.json")" = "$(wc -l <"$journal/local.jsonl" | tr -d " ")" ] &&
    ! grep -qi dana "$journal"/[0-9a-f]*.jsonl' _ "$KG_DIR"
check "lookup resolves a strong alias and shows sources, by minted fact ids" bash -c '
  grep -Eq "^  f-[0-9a-hjkmnp-tv-z]{6} Release process has_step Merge to main \(step 1\) \[ledger:mypr:acme/invoice-exporter#102" <<<"$1"' _ "$(kg lookup releases)"
out="$(kg batch --cap 2)"
check "the batch lists capped groups, rework-preventing first" bash -c 'grep -q "^1\. Process: other facts: 5 facts \[prevents rework\]" <<<"$1" && grep -q "(1 more group waits for a later batch)" <<<"$1"' _ "$out"
check "a one-line decision answers it" contains "$(kg batch apply 'confirm all except 1a')" "8 confirmed, 0 rejected, 1 left proposed"
check "the batch's last group, the people's facts, is confirmed" contains "$(kg confirm axis:people)" "confirmed"
transcript="$WORK/kg-transcript.jsonl"
printf '{"type":"user","timestamp":"%s","origin":{"kind":"human"},"message":{"role":"user","content":"note: the pdf renderer repo is archived"}}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$transcript"
check "my own words confirm a fact when the quote is in the transcript" contains "$(kg propose repo/pdf-renderer status --value archived --from-user --quote 'the pdf renderer repo is archived' --transcript "$transcript")" "confirmed (your own words"
check "kg validate: the files are the journal's rebuild" test "$(kg validate)" = "knowledge base is valid"
jq -c 'if .id == "repo/invoice-exporter" then .summary = "exports invoices nightly" else . end' "$KG_DIR/entities.jsonl" >"$WORK/kg-edited.jsonl"
mv "$WORK/kg-edited.jsonl" "$KG_DIR/entities.jsonl"
check "... and says a hand edit is not in the journal yet, as one finding" bash -c '[ "$(jq -r "[.[].kind] | join(\" \")" <<<"$1")" = edited ]' _ "$(kg validate --json)"
check "the next write adopts the hand edit" contains "$(kg add-entity repo/pdf-renderer 2>&1)" "adopted 1 change(s) into the journal (transaction 'adopt')"
check "... and kg validate is clean again, the edit kept" bash -c '[ "$(BOSUN_CONFIG_DIR="$2" "$1" kg validate)" = "knowledge base is valid" ] && grep -q "exports invoices nightly" "$3"' _ "$BOSUN" "$WORK/kg-config" "$KG_DIR/entities.jsonl"
check "kg usage errors exit 2" bash -c '
  for args in "" "bogus" "propose x"; do
    # shellcheck disable=SC2086  # split the argument list on purpose
    "$1" kg $args >/dev/null 2>&1
    [ $? -eq 2 ] || { echo "kg $args did not exit 2"; exit 1; }
  done' _ "$BOSUN"
# From a Claude session (this run's process, with a session of its own for now): a quote of my words is
# checked in that session's own transcript, a review needs one, and another transcript is refused.
kg_session="44444444-0000-0000-0000-000000000001"
kg_own="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import claude_state; print(claude_state.transcript_path(sys.argv[2], sys.argv[3]))' "$ROOT/lib" "$WORK" "$kg_session")"
mkdir -p "$(dirname "$kg_own")"
printf '{"type":"user","timestamp":"%s","origin":{"kind":"human"},"message":{"role":"user","content":"the pdf renderer renders slowly"}}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$kg_own"
printf '{"pid":%d,"sessionId":"%s","cwd":"%s"}' $$ "$kg_session" "$WORK" >"$HOME/.claude/sessions/$$.json"
check "from a Claude session, my words are checked in its own transcript" contains "$(kg propose repo/pdf-renderer caveat --value 'renders slowly' --from-user --quote 'the pdf renderer renders slowly')" "confirmed (your own words"
check "... another transcript is refused there" fails_with "not this session's transcript" kg propose repo/pdf-renderer caveat --value x --from-user --quote 'the pdf renderer renders slowly' --transcript "$transcript"
check "... and a review there needs my words" fails_with "from a session, confirm with --from-user --quote" kg confirm repo/pdf-renderer
# What bosun pasted into the window (a `bosun send`, events off) is in its transcript as a prompt, but not mine.
kg_pasted="note for later: the exporter rounds per line"
printf '{"type":"user","timestamp":"%s","origin":{"kind":"human"},"message":{"role":"user","content":"%s"}}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$kg_pasted" >>"$kg_own"
BOSUN_EVENTS_ENABLED=0 bash -c 'source "$1/lib/common.sh" && note_bosun_prompt kg-window "$2"' _ "$ROOT" "$kg_pasted"
check "... and text bosun pasted into the window is not my words, with events off too" contains "$(BOSUN_CALLER_WINDOW=kg-window kg propose repo/pdf-renderer caveat --value 'rounds per line' --from-user --quote 'the exporter rounds per line')" "stays proposed"
rm -f "$kg_own"
mv "$WORK/own-session.json" "$HOME/.claude/sessions/$$.json"

always_run
# From here on `claude` is this fake (refresh asks it for the installed version), never a real
# Claude Code on this machine.
printf '#!/bin/sh\necho "2.0.0 (Claude Code)"\n' >"$WORK/fake-claude"
chmod +x "$WORK/fake-claude"
export BOSUN_CLAUDE_BIN="$WORK/fake-claude"

section tmux-session "tmux session (isolated socket, dry-run windows)" covers 'libexec/bosun-attach,libexec/bosun-launch,libexec/bosun-start,libexec/bosun-stop,libexec/bosun-send,libexec/bosun-refresh,libexec/bosun-ledger,lib/run_locked.py,libexec/bosun-watchdog,libexec/bosun-sessions,libexec/bosun-setup'
no_session
"$BOSUN" start >/dev/null 2>&1
wait_for 30 has_windows "alpha beta gamma "
windows="$(tmux -L "$SOCKET" list-windows -t "=smoke" -F '#{window_name}' 2>/dev/null | tr '\n' ' ')"
check "start creates one window per session" test "$windows" = "alpha beta gamma "
tmux -L "$SOCKET" new-window -d -t "=smoke" -n at-a-screen "bash -c '(exec -a fake-claude sleep 60); true'"
check "the watchdog sees a Claude that has no session yet" wait_for 30 bash -c '"$1" watchdog --facts | jq -e ".windows[] | select(.name == \"at-a-screen\") | .starting_pid > 0 and .agent_pid == null" >/dev/null' _ "$BOSUN"
tmux -L "$SOCKET" kill-window -t "=smoke:at-a-screen"
check "windows are found without a UTF-8 locale too" bash -c 'LC_ALL=C "$1" sessions --json | jq -e "[.[] | select(.name == \"alpha\")] | .[0].window == true" >/dev/null' _ "$BOSUN"
# -S -200: the launch line (its --settings JSON) wraps over more rows than the window shows.
check "window runs the launcher" wait_for 30 bash -c 'tmux -L "$1" capture-pane -p -S -200 -t "=smoke:0" | grep -q "dry-run: would run"' _ "$SOCKET"
tmux -L "$SOCKET" kill-window -t "=smoke:2"
out="$("$BOSUN" start 2>&1)"
check "second start refreshes instead of duplicating" contains "$out" "already exists; mode: refresh"
check "refresh recreates missing windows" contains "$out" "gamma: create the missing window"
printf 'sessions:\n  - name: alpha\n    dir: %s\n  - name: ghost\n    dir: %s\n' "$REPO_A" "$WORK/repos/moved-away" >"$WORK/ghost-sessions.yml"
check "refresh skips a window whose folder is gone, as start does, never opening it elsewhere" contains \
  "$(BOSUN_SESSIONS_FILE="$WORK/ghost-sessions.yml" "$BOSUN" refresh --dry-run 2>&1)" "ghost: directory $WORK/repos/moved-away does not exist; skipping the window"
hold_lock "$BOSUN_STATE_DIR/windows.lock" tmux-windows
check "a start while another sets up the windows waits, then gives up saying why" fails_with "another bosun start or refresh is setting up the windows" env BOSUN_WINDOWS_LOCK_WAIT_SECONDS=0 "$BOSUN" start
check "... and so does a refresh" fails_with "another bosun start or refresh is setting up the windows" env BOSUN_WINDOWS_LOCK_WAIT_SECONDS=0 "$BOSUN" refresh
check "... but not a dry run, which changes nothing" "$BOSUN" refresh --dry-run
check "... exiting 75 (EX_TEMPFAIL: try again later)" bash -c 'BOSUN_WINDOWS_LOCK_WAIT_SECONDS=0 "$1" start >/dev/null 2>&1; [ $? -eq 75 ]' _ "$BOSUN"
release_lock tmux-windows
lock_free() { ! lock_taken "$1"; } # <lock file>
check "the windows lock is free once the holder is gone, though the tmux server bosun start left runs on" lock_free "$BOSUN_STATE_DIR/windows.lock"
check "a start under a parent that holds the windows lock goes on, and its refresh too (inherited, not waited for)" \
  python3 "$ROOT/lib/run_locked.py" "$BOSUN_STATE_DIR/windows.lock" 0 busy -- env BOSUN_WINDOWS_LOCK_WAIT_SECONDS=0 "$BOSUN" start
check "start --restart from inside bosun's tmux refuses: it would stop itself" fails_with "it would stop itself before starting again" env TMUX="/tmp/tmux-0/$SOCKET,1,0" "$BOSUN" start --restart
check "a window whose agent is not installed says so, rather than becoming a plain shell" fails_with "its agent, $WORK/no-such-claude, is not installed for this window's shell" \
  bash -c 'cd "$1" && BOSUN_LAUNCH_DRY_RUN=0 BOSUN_CLAUDE_BIN="$2" "$3" launch alpha </dev/null' _ "$REPO_A" "$WORK/no-such-claude" "$BOSUN"
check "... and leaves that in its window's error file, for an update's restart to say why it did not start" \
  grep -qx "its agent, $WORK/no-such-claude, is not installed for this window's shell (not on its PATH)" "$BOSUN_STATE_DIR/launches/smoke/alpha.error"
rm -f "$BOSUN_STATE_DIR/launches/smoke/alpha.error"
check "send refuses windows without Claude" fails_with "is not running an agent" "$BOSUN" send alpha hello
check "send refuses unknown windows" fails_with "no window 'no-such-window'" "$BOSUN" send no-such-window hello
tmux -L "$SOCKET" select-window -t "=smoke:alpha"
check "attach refuses a window the session does not have, naming those it has" fails_with "no window 'no-such-window' in session 'smoke'; its windows: alpha beta gamma" "$BOSUN" attach no-such-window
check "... and selects no other window" test "$(tmux -L "$SOCKET" display-message -p -t "=smoke:" '#{window_name}')" = alpha
check "attach takes one window at most" fails_with "one window at most" "$BOSUN" attach alpha beta
check "attach from inside tmux refuses, saying what to run from a plain terminal" fails_with "from a plain terminal run: bosun attach beta" env TMUX="/tmp/tmux-0/$SOCKET,1,0" "$BOSUN" attach beta
check "attach's refusal of a name is on stderr, and never in the logs for the watchdog to alert on" refused_quietly "no window 'no-such-window' in session 'smoke'; its windows" "$BOSUN" attach no-such-window
check "... nor is its refusal inside tmux" refused_quietly "already inside tmux" env TMUX="/tmp/tmux-0/$SOCKET,1,0" "$BOSUN" attach beta
check "... nor its usage error" refused_quietly "one window at most, got 2: alpha beta (see --help)" "$BOSUN" attach alpha beta
check "... nor another command's usage error" refused_quietly "unknown argument '--dry-run' (see --help)" "$BOSUN" stop --dry-run
# In a terminal of its own (a pty from Python's pty module), so that tmux attaches for real; the pid is this run's own
TERM=xterm python3 -c 'import pty, sys; pty.spawn(sys.argv[1:])' "$BOSUN" attach beta </dev/null >/dev/null 2>&1 &
attach_pid=$!
check "attach <window> attaches a client on that window" wait_for 30 bash -c '[ "$(tmux -L "$1" list-clients -t =smoke -F "#{window_name}")" = beta ]' _ "$SOCKET"
tmux -L "$SOCKET" detach-client -s =smoke 2>/dev/null
check "... and once it detaches, the session runs on with no client" wait_for 30 bash -c '[ -z "$(tmux -L "$1" list-clients -t =smoke)" ] && tmux -L "$1" has-session -t =smoke' _ "$SOCKET"
kill "$attach_pid" 2>/dev/null
wait "$attach_pid" 2>/dev/null
check "stop rejects unknown arguments and keeps the session" bash -c '! "$1" stop --dry-run 2>/dev/null && tmux -L "$2" has-session -t =smoke' _ "$BOSUN" "$SOCKET"
check "setup rejects unknown arguments" bash -c '! "$1" setup --dryrun 2>/dev/null' _ "$BOSUN"
"$BOSUN" stop >/dev/null 2>&1
check "stop kills the session" bash -c "! tmux -L '$SOCKET' has-session -t '=smoke'"
check "attach with no session says to start it, on stderr and not in the logs" refused_quietly "no tmux session 'smoke'; start it with: bosun start" "$BOSUN" attach

section refresh "refresh (fake Claude processes and status records)" covers 'libexec/bosun-refresh,libexec/bosun-send,libexec/bosun-start,libexec/bosun-ledger,lib/claude_state.py'
no_session
"$BOSUN" start >/dev/null 2>&1
wait_for 30 has_windows "alpha beta gamma "
alpha_pid="$(fake_claude alpha idle 1.0.0 3600)"
fake_claude beta busy 1.0.0 3600 >/dev/null
gamma_pid="$(fake_claude gamma idle 2.0.0 3600)"
out="$("$BOSUN" refresh --dry-run 2>&1)"
check "restarts idle outdated sessions" contains "$out" "alpha: would restart (v1.0.0 -> v2.0.0"
check "leaves busy sessions alone" contains "$out" "beta: busy (v1.0.0); leaving alone"
check "leaves an idle up-to-date session with nothing in flight idle, without a prompt" contains "$out" "gamma: left idle: nothing in flight (idle, v2.0.0)"
L upsert 'slack:C77/1' --kind mention >/dev/null
L record-delegation 'slack:C77/1' --window gamma --task investigate >/dev/null
check "a window holding a hand-over without a report is in flight" test "$(L in-flight gamma)" = "slack:C77/1"
check "... so it gets the drain prompt" contains "$("$BOSUN" refresh --dry-run gamma 2>&1)" "gamma: would send the drain prompt (idle, v2.0.0)"
L report 'slack:C77/1' --summary 'looked' >/dev/null
check "... and once it reported, nothing is in flight" test -z "$(L in-flight gamma)"
out="$("$BOSUN" refresh --dry-run --drain-only alpha 2>&1)"
check "--drain-only never restarts" bash -c 'grep -q "alpha: left idle: nothing in flight" <<<"$1" && ! grep -q "alpha: would restart" <<<"$1"' _ "$out"
# A conversation past BOSUN_FRESH_CONTEXT_TOKENS (its latest turn read 300k tokens).
big_transcript="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import claude_state; print(claude_state.transcript_path(sys.argv[2], "s-gamma"))' "$ROOT/lib" "$WORK")"
mkdir -p "$(dirname "$big_transcript")"
echo '{"type":"assistant","message":{"usage":{"input_tokens":1000,"cache_read_input_tokens":290000,"cache_creation_input_tokens":9000,"output_tokens":50}}}' >"$big_transcript"
gamma_record() { # <status> <seconds in it>
  printf '{"pid":%d,"status":"%s","version":"2.0.0","statusUpdatedAt":%d,"name":"gamma","cwd":"%s","sessionId":"s-gamma"}' \
    "$gamma_pid" "$1" $((($(date +%s) - $2) * 1000)) "$WORK" >"$HOME/.claude/sessions/$gamma_pid.json"
}
gamma_record idle 3600
check "claude_state reads a conversation's size from its latest turn" test "$(python3 "$ROOT/lib/claude_state.py" context "$gamma_pid")" = 300000
check "a dry run names a big idle window with nothing in flight, also with the flag off" contains "$("$BOSUN" refresh --dry-run gamma 2>&1)" \
  "gamma: would start a fresh conversation with BOSUN_FRESH_CONVERSATIONS=1 (context 300k > 200k tokens, nothing in flight)"
check "... with the flag on it would start one" contains "$(BOSUN_FRESH_CONVERSATIONS=1 "$BOSUN" refresh --dry-run gamma 2>&1)" \
  "gamma: would start a fresh conversation (context 300k > 200k tokens"
L upsert 'slack:C77/2' --kind mention >/dev/null
L record-delegation 'slack:C77/2' --window gamma --task investigate >/dev/null
check "... but not while it holds a hand-over without a report" bash -c '! grep -q "fresh conversation" <<<"$1"' _ "$(BOSUN_FRESH_CONVERSATIONS=1 "$BOSUN" refresh --dry-run gamma 2>&1)"
L report 'slack:C77/2' --summary 'looked' >/dev/null
gamma_record busy 3600
check "... nor while it is busy" bash -c '! grep -q "fresh conversation" <<<"$1"' _ "$(BOSUN_FRESH_CONVERSATIONS=1 "$BOSUN" refresh --dry-run gamma 2>&1)"
gamma_record idle 3600
check "... nor under a higher threshold" bash -c '! grep -q "fresh conversation" <<<"$1"' _ "$(BOSUN_FRESH_CONVERSATIONS=1 BOSUN_FRESH_CONTEXT_TOKENS=400000 "$BOSUN" refresh --dry-run gamma 2>&1)"
mkdir -p "$BOSUN_STATE_DIR/conversations"
echo "gamma-$(date +%F)-1" >"$BOSUN_STATE_DIR/conversations/gamma"
check "a fresh conversation's name is what the window resumes, and the next one counts up" bash -c '
  source "$1/lib/common.sh"; load_config
  [ "$(expected_session_name gamma)" = "gamma-$(date +%F)-1" ] && [ "$(next_conversation_name gamma)" = "gamma-$(date +%F)-2" ]' _ "$ROOT"
rm -f "$BOSUN_STATE_DIR/conversations/gamma"
out="$("$BOSUN" refresh --dry-run --restart-idle gamma 2>&1)"
check "--restart-idle restarts up-to-date idle sessions" contains "$out" "gamma: would restart (--restart-idle"
check "window arguments limit the refresh" bash -c '! grep -q alpha <<<"$1"' _ "$out"
printf '{"pid":%d,"status":"shell","version":"1.0.0","statusUpdatedAt":%d}' "$gamma_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$gamma_pid.json"
out="$("$BOSUN" refresh --dry-run gamma 2>&1)"
check "never restarts while a background shell runs" contains "$out" "restart deferred (status shell"
printf '{"pid":%d,"status":"idle","version":"1.0.0","statusUpdatedAt":%d}' "$gamma_pid" $((($(date +%s) - 5) * 1000)) >"$HOME/.claude/sessions/$gamma_pid.json"
out="$("$BOSUN" refresh --dry-run gamma 2>&1)"
check "defers restart when only briefly idle" contains "$out" "restart deferred (status idle"
alpha_pane_pid="$(tmux -L "$SOCKET" display -p -t "=smoke:alpha" '#{pane_pid}')"
"$BOSUN" refresh alpha >/dev/null 2>&1
check "restart stops the old process" wait_for 30 bash -c "! kill -0 $alpha_pid 2>/dev/null"
check "restart relaunches in the same window, in a new process" wait_for 30 bash -c '
  [ "$(tmux -L "$1" display -p -t "=smoke:alpha" "#{pane_pid}")" != "$2" ] &&
  tmux -L "$1" capture-pane -p -S -200 -t "=smoke:alpha" | grep -q "resuming '"'"'alpha'"'"'"' _ "$SOCKET" "$alpha_pane_pid"
check "... without the drain prompt, since nothing is in flight there" wait_for 30 bash -c '
  tmux -L "$1" capture-pane -p -S -200 -t "=smoke:alpha" | grep -q "idle: no prompt"' _ "$SOCKET"
check "--unless-active is a no-op without attached clients" "$BOSUN" refresh --dry-run --unless-active alpha
beta_window="$(tmux -L "$SOCKET" list-windows -t =smoke -F '#{window_name} #{window_id}' | awk '$1 == "beta" { print $2 }')"
beta_pane="$(tmux -L "$SOCKET" display -p -t "$beta_window" '#{pane_pid}')"
pkill -P "$beta_pane" sleep
wait_for 30 bash -c '! pgrep -P "$1" sleep' _ "$beta_pane"
cat >"$WORK/beta-claude.sh" <<'SH'
exec -a 'claude --model sonnet --settings {"effortLevel":"low"}' sleep 600
SH
tmux -L "$SOCKET" send-keys -t "$beta_window" "bash $WORK/beta-claude.sh" Enter
wait_for 30 pgrep -P "$beta_pane" # exec -a renames it, so match the pane's child, not "sleep"
beta_pid="$(pgrep -P "$beta_pane" | head -1)"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d}' "$beta_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$beta_pid.json"
check "send --list shows the model and effort a session was launched with" bash -c 'grep -E "^beta " <<<"$1" | grep -q "sonnet/low"' _ "$("$BOSUN" send --list)"
check "refresh restarts an idle session whose model/effort no longer match the config" contains "$("$BOSUN" refresh --dry-run beta 2>&1)" "beta: would restart (model/effort sonnet/low -> opus/medium"
check "... but not a Claude it did not launch" bash -c '! grep -q "model/effort" <<<"$1"' _ "$("$BOSUN" refresh --dry-run alpha 2>&1)"
cat >"$WORK/old-claude.sh" <<'SH'
exec -a 'claude --dangerously-skip-permissions --name beta --remote-control beta' sleep 600
SH
kill "$beta_pid"
wait_for 30 bash -c '! pgrep -P "$1" >/dev/null' _ "$beta_pane"
tmux -L "$SOCKET" send-keys -t "$beta_window" "bash $WORK/old-claude.sh" Enter
wait_for 30 pgrep -P "$beta_pane"
beta_pid="$(pgrep -P "$beta_pane" | head -1)"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d}' "$beta_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$beta_pid.json"
check "an bosun session from before model/effort settings is moved onto them" contains "$("$BOSUN" refresh --dry-run beta 2>&1)" "beta: would restart (model/effort -/- -> opus/medium"
printf '{"pid":%d,"status":"waiting","version":"2.0.0","statusUpdatedAt":%d}' "$gamma_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$gamma_pid.json"
check "refresh leaves a session waiting for you alone" contains "$("$BOSUN" refresh --dry-run gamma 2>&1)" "gamma: waiting (a permission prompt or question waits for you); leaving alone"
check "send refuses a session waiting for you" contains "$("$BOSUN" send gamma hello 2>&1)" "not pasting into it"

section console-sessions "console sessions and health (these fake Claude windows; it never starts Claude)" needs refresh covers 'libexec/bosun-console,libexec/bosun-watchdog,libexec/bosun-sessions,lib/console_*.py'
printf '#!/bin/sh\ntouch "%s"\nexit 1\n' "$WORK/claude-was-started" >"$WORK/no-agent"
chmod +x "$WORK/no-agent"
touch "$WORK/before-console"
gamma_screen="$(tmux -L "$SOCKET" capture-pane -p -t "=smoke:gamma")"
BOSUN_CLAUDE_BIN="$WORK/no-agent" "$BOSUN" console --port 0 >"$WORK/live-console.out" 2>"$WORK/live-console.err" &
CONSOLE_PID=$!
check "the console starts on the live session's state" console_started "$WORK/live-console.out" "$WORK/live-console.err"
LIVE_URL="$(grep -o 'http://[^ ]*' "$WORK/live-console.out")"
LIVE_BASE="${LIVE_URL%%/\?*}"
LIVE_COOKIE="bosun_console_${LIVE_BASE##*:}=${LIVE_URL##*t=}"
check "/api/sessions has a card per window, with its Claude's status, model and attach command" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/sessions" | jq -e --arg socket "$3" "
    ([.windows[] | select(.kind == \"session\") | .name] == [\"alpha\", \"beta\", \"gamma\"]) and
    (.windows[] | select(.name == \"gamma\") | .state == \"waiting\" and .model == \"sonnet\" and .agent.seconds >= 3600
      and .attach == \"tmux -L \(\$socket) attach -t smoke:gamma\") and
    (.counts.waiting == 1)"' _ "$LIVE_BASE" "$LIVE_COOKIE" "$SOCKET"
check "/api/health has every watchdog check, and the flags as booleans" bash -c '
  names="$("$3" watchdog --list | cut -d" " -f1 | jq -R . | jq -sc .)" &&
    curl -sf -H "Cookie: $2" "$1/api/health" | jq -e --argjson names "$names" "
      ([.watchdog.checks[].name] == \$names) and ([.flags[] | type] | unique == [\"boolean\"]) and
      (.events | has(\"deliveries_today\") and has(\"max_per_day\") and has(\"pending\")) and
      (.slack_watch | has(\"spend_today\") and has(\"cap\")) and (.login.state | type == \"string\")"' \
  _ "$LIVE_BASE" "$LIVE_COOKIE" "$BOSUN"
check "/api/needs-you lists the window waiting at a prompt" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/needs-you" | jq -e "[.windows[].name] == [\"gamma\"]"' _ "$LIVE_BASE" "$LIVE_COOKIE"
check "/api/sessions and /api/health need the cookie: 401" bash -c '
  for path in /api/sessions /api/health; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" "$1$path")" = 401 ] || exit 1
  done' _ "$LIVE_BASE"
# The console's own files (console/token, written at start, and console/running.lock, held while it
# serves) are its only ones there.
check "... and reading them started no Claude, wrote no state and left the panes alone" bash -c '
  [ ! -e "$1/claude-was-started" ] && [ -z "$(find "$2" -type f -newer "$1/before-console" ! -path "$2/console/token" ! -path "$2/console/running.lock")" ] &&
    [ "$(tmux -L "$3" capture-pane -p -t "=smoke:gamma")" = "$4" ]' _ "$WORK" "$BOSUN_STATE_DIR" "$SOCKET" "$gamma_screen"
kill "$CONSOLE_PID" 2>/dev/null
check "... and it stops" wait_for 30 bash -c '! kill -0 "$1" 2>/dev/null' _ "$CONSOLE_PID"
CONSOLE_PID=""

# A real restart in a window: the manifest folder, a split window, and what an update asks of each window (refresh
# --update-restart, which bosun update runs), on refresh's fake Claude windows. It ends their session.
section update-restart "refresh in a window and --update-restart (refresh's fake Claude windows)" needs refresh covers 'libexec/bosun-refresh,libexec/bosun-launch,libexec/bosun-stop,lib/launch_record.py,lib/claude_state.py,lib/pane_input.py,lib/codex_state.py'
tmux -L "$SOCKET" send-keys -t "=smoke:alpha" "cd /" Enter
wait_for 30 bash -c '[ "$(tmux -L "$1" display -p -t =smoke:alpha "#{pane_current_path}")" = / ]' _ "$SOCKET"
"$BOSUN" refresh alpha >/dev/null 2>&1
check "relaunch starts in the manifest directory" wait_for 30 bash -c '[ "$(tmux -L "$1" display -p -t "=smoke:alpha" "#{pane_current_path}")" = "$2" ]' _ "$SOCKET" "$REPO_A"
tmux -L "$SOCKET" split-window -d -t "=smoke:alpha" "sleep 600"
check "refresh refuses to relaunch into a split window and fails" bash -c '! "$1" refresh alpha >"$2" 2>&1 && grep -q "split into several panes" "$2"' _ "$BOSUN" "$WORK/split.out"
check "... and the other pane keeps running" bash -c 'tmux -L "$1" list-panes -t =smoke:alpha -F "#{pane_current_command}" | grep -qx sleep' _ "$SOCKET"
check "refresh fails without a manifest" bash -c '! BOSUN_SESSIONS_FILE=/nonexistent "$1" refresh 2>/dev/null' _ "$BOSUN"
update_restart() { "$BOSUN" refresh --update-restart "$WORK/update-prompt" "$@"; }
check "refresh --update-restart refuses a window not in the window list" fails_with "no window 'nope' in the window list" update_restart nope
# A window opened by hand in bosun's session (prefix-c), outside the window list, with no launch recorded.
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n by-hand "sleep 600"
by_hand_pane="$(tmux -L "$SOCKET" display -p -t "=smoke:by-hand" '#{pane_pid}')"
check "... and one open outside it with no launch recorded, as bosun did not start it" fails_with "bosun did not start it, so an update leaves it as it is" update_restart by-hand
check "... leaving its pane as it is" bash -c '[ "$(tmux -L "$1" display -p -t "=smoke:by-hand" "#{pane_pid}")" = "$2" ] && kill -0 "$2"' _ "$SOCKET" "$by_hand_pane"
tmux -L "$SOCKET" kill-window -t "=smoke:by-hand"
check "... leaves a window waiting for you running, and exits 3" bash -c '"$1" refresh --update-restart "$2" gamma >/dev/null 2>&1; [ $? -eq 3 ] && kill -0 "$3"' _ "$BOSUN" "$WORK/update-prompt" "$gamma_pid"
gamma_record busy 3600
check "... and a busy one" bash -c '"$1" refresh --update-restart "$2" gamma >/dev/null 2>&1; [ $? -eq 3 ] && kill -0 "$3"' _ "$BOSUN" "$WORK/update-prompt" "$gamma_pid"
gamma_record idle 5
check "... restarts an idle one at once, however briefly idle" update_restart gamma
check "... stopping the old process" wait_for 30 bash -c '! kill -0 "$1" 2>/dev/null' _ "$gamma_pid"
check "... and starting it with the update's prompt instead of the drain prompt" wait_for 30 bash -c '
  screen="$(tmux -L "$1" capture-pane -p -J -S -200 -t "=smoke:gamma")" &&
    grep -q "dry-run: would run: .* -- bosun-was-updated:check-in" <<<"$screen" && ! grep -q "Bosun\\\\ resumed" <<<"$screen"' _ "$SOCKET"
gamma_pid="$(fake_claude gamma shell 2.0.0 5)"
check "... and one in shell (only background work runs)" update_restart gamma
check "... stopping it too" wait_for 30 bash -c '! kill -0 "$1" 2>/dev/null' _ "$gamma_pid"
pane_of() { tmux -L "$SOCKET" display -p -t "=smoke:$1" '#{pane_pid}'; }
# <window> [old pane pid]: a launch with the update's prompt ran in the window (in a new pane process;
# respawn-pane starts the pane's screen and history afresh).
launched_with_update_prompt() {
  [ "$(pane_of "$1")" != "${2:-}" ] &&
    tmux -L "$SOCKET" capture-pane -p -J -S -500 -t "=smoke:$1" | grep -qF -e '-- bosun-was-updated:check-in'
}
wait_for 30 launched_with_update_prompt gamma
gamma_pane="$(pane_of gamma)"
# gamma has a conversation of its own to resume, so its relaunch is a resume, with the update's prompt.
make_session "$REPO_B" 22222222-0000-0000-0000-000000000002 gamma 202601020000
check "... relaunches one whose agent is not running, with the update's prompt" update_restart gamma
check "... (it runs launch again)" wait_for 30 launched_with_update_prompt gamma "$gamma_pane"
tmux -L "$SOCKET" kill-window -t "=smoke:gamma"
check "... and creates a missing one, with that prompt" contains "$(update_restart gamma 2>&1)" "gamma: create the missing window"
check "... (its launch has it)" wait_for 30 launched_with_update_prompt gamma
wait_for 30 bash -c '[ -z "$(pgrep -P "$(tmux -L "$1" display -p -t =smoke:gamma "#{pane_pid}")")" ]' _ "$SOCKET"
gamma_pane="$(pane_of gamma)"
echo "gamma-$(date +%F)-1" >"$BOSUN_STATE_DIR/conversations/gamma"
check "... and with a fresh conversation pending, one whose agent is not running starts that one, not with the update's prompt, and exits 4" bash -c '
  out="$("$1" refresh --update-restart "$2" gamma 2>&1)"
  [ $? -eq 4 ] && grep -qF "gamma: its launch starts a new conversation (gamma-$(date +%F)-1)" <<<"$out"' _ "$BOSUN" "$WORK/update-prompt"
check "... (its launch has the fresh conversation's prompt)" wait_for 30 bash -c '
  [ "$(tmux -L "$1" display -p -t =smoke:gamma "#{pane_pid}")" != "$2" ] && screen="$(tmux -L "$1" capture-pane -p -J -S -500 -t =smoke:gamma)" &&
    grep -qF -e "--name gamma-$3-1 " <<<"$screen" && grep -q "fresh.*conversation" <<<"$screen" && ! grep -q bosun-was-updated <<<"$screen"' \
  _ "$SOCKET" "$gamma_pane" "$(date +%F)"
rm -f "$BOSUN_STATE_DIR/conversations/gamma"
cat >"$WORK/typing-claude.sh" <<'SH'
printf '%s\n\342\235\257 half a prompt\n%s\n' "────────────────────" "────────────────────"
exec sleep 600
SH
wait_for 30 bash -c '[ -z "$(pgrep -P "$(tmux -L "$1" display -p -t =smoke:gamma "#{pane_pid}")")" ]' _ "$SOCKET"
tmux -L "$SOCKET" send-keys -t "=smoke:gamma" "bash $WORK/typing-claude.sh" Enter
wait_for 30 pgrep -P "$(tmux -L "$SOCKET" display -p -t =smoke:gamma '#{pane_pid}')" -x sleep
gamma_pid="$(pgrep -P "$(tmux -L "$SOCKET" display -p -t =smoke:gamma '#{pane_pid}')" -x sleep)"
gamma_record idle 3600
check "... and leaves one whose input box holds unsent text, as send would wait (exit 3)" bash -c '
  out="$("$1" refresh --update-restart "$2" gamma 2>&1)"
  [ $? -eq 3 ] && grep -q "its input line holds unsent text (half a prompt)" <<<"$out" && kill -0 "$3"' _ "$BOSUN" "$WORK/update-prompt" "$gamma_pid"
# An update keeps a window on the agent it was launched on; the configured one takes over at the next
# restart that is not for an update.
pane_is_idle() { [ -z "$(pgrep -P "$(pane_of "$1")")" ]; } # <window>: its pane's shell runs nothing
kill "$gamma_pid"
wait_for 30 pane_is_idle gamma
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$WORK/sessions.yml.on-claude"
echo "    harness: codex" >>"$BOSUN_CONFIG_DIR/sessions.yml" # gamma's entry is the last
check "a window launched on Claude, its entry now on Codex, fingerprints the same (its agent is no change)" bash -c '
  [ "$(cd / && "$1" launch --fingerprint gamma | jq -cS .)" = "$(jq -cS .fingerprint "$2/launches/smoke/gamma.json")" ]' _ "$BOSUN" "$BOSUN_STATE_DIR"
gamma_pane="$(pane_of gamma)"
check "... and --update-restart starts it again" update_restart gamma
check "... with the update's prompt" wait_for 30 launched_with_update_prompt gamma "$gamma_pane"
check "... on Claude, as it was launched" bash -c '
  screen="$(tmux -L "$1" capture-pane -p -J -S -500 -t "=smoke:gamma")"
  grep -q -- "--remote-control gamma" <<<"$screen" && ! grep -q turn-end <<<"$screen"' _ "$SOCKET"
printf '#!/bin/sh\ncase "$*" in *"mcp list --json") echo "[]" && exit 0 ;; esac\necho "codex-cli 0.50.0"\n' >"$WORK/fake-codex"
chmod +x "$WORK/fake-codex"
wait_for 30 pane_is_idle gamma
gamma_pane="$(pane_of gamma)"
# The window's own launch (in a tmux pane, which does not inherit this shell's variables) reads the fake from the config.
echo "BOSUN_CODEX_BIN=\"$WORK/fake-codex\"" >"$BOSUN_CONFIG_DIR/bosun.env"
check "a plain refresh, not for an update" "$BOSUN" refresh gamma
check "... starts it on the agent configured now (Codex)" wait_for 30 bash -c '
  [ "$(tmux -L "$1" display -p -t =smoke:gamma "#{pane_pid}")" != "$2" ] &&
    tmux -L "$1" capture-pane -p -J -S -500 -t "=smoke:gamma" | grep -q turn-end' _ "$SOCKET" "$gamma_pane"
rm -f "$BOSUN_CONFIG_DIR/bosun.env"
cp "$WORK/sessions.yml.on-claude" "$BOSUN_CONFIG_DIR/sessions.yml"
"$BOSUN" stop >/dev/null 2>&1

section restart-shells "restarts leave no shells behind (zsh with a prompt helper in its startup file)" covers 'libexec/bosun-refresh,libexec/bosun-start,libexec/bosun-stop'
no_session
# Like a prompt theme's git-status daemon: interactive startup forks a helper off (detached, so
# launchd adopts it at once), and only the shell's first prompt lets it exit. A pane's launch shell
# (zsh -lic) never shows a prompt. The cap ends a leaked helper after 5 minutes.
HELPER_PIDS="$WORK/helper-pids"
cat >"$HOME/.zshrc" <<'ZSHRC'
if [[ -o interactive ]]; then
  zmodload zsh/system zsh/zselect zsh/datetime
  _helper_marker="$HOME/helper.$$.$EPOCHREALTIME"
  : >"$_helper_marker"
  ( {
    print $sysparams[pid] >>"$HELPER_PIDS"
    repeat 1500; do [[ -e $_helper_marker ]] || exit 0; zselect -t 20; done
  } &! )
  _helper_prompt() { rm -f "$_helper_marker"; precmd_functions=(${precmd_functions:#_helper_prompt}); }
  precmd_functions+=(_helper_prompt)
fi
ZSHRC
saved_shell="$SHELL" saved_zdotdir="${ZDOTDIR-}"
export SHELL=/bin/zsh ZDOTDIR="$HOME" HELPER_PIDS
"$BOSUN" start >/dev/null 2>&1
wait_for 30 has_windows "alpha beta gamma "
launched_in() { tmux -L "$SOCKET" capture-pane -p -S -200 -t "=smoke:$1" | grep -q "dry-run: would run"; }
# One restart: relaunch_in_window's sweep of what the old pane's shell left is the same each time.
wait_for 30 launched_in alpha
helper_pane="$(tmux -L "$SOCKET" display -p -t "=smoke:alpha" '#{pane_pid}')"
fake_claude alpha idle 1.0.0 3600 >/dev/null
"$BOSUN" refresh alpha >/dev/null 2>&1
wait_for 30 bash -c '[ "$(tmux -L "$1" display -p -t "=smoke:alpha" "#{pane_pid}")" != "$2" ]' _ "$SOCKET" "$helper_pane"
wait_for 30 launched_in alpha
"$BOSUN" stop >/dev/null 2>&1
helpers_left() { # the helpers still running, as "pid ppid"
  local pid
  [ -f "$HELPER_PIDS" ] || return 0
  while read -r pid; do ps -o pid=,ppid= -p "$pid"; done <"$HELPER_PIDS"
}
check "the shells' startup files started helpers (one per launch shell at least: three windows and a restart)" bash -c '[ "$(wc -l <"$1")" -ge 4 ]' _ "$HELPER_PIDS"
no_helpers_left() {
  local left
  left="$(helpers_left)"
  [ -z "$left" ] || {
    echo "still running (pid ppid): $left"
    return 1
  }
}
wait_for 30 no_helpers_left
check "after start, a restart and stop, none is left behind" no_helpers_left
helpers_left | awk '{ print $1 }' | xargs kill 2>/dev/null
export SHELL="$saved_shell"
if [ -n "$saved_zdotdir" ]; then export ZDOTDIR="$saved_zdotdir"; else unset ZDOTDIR; fi
rm -f "$HOME/.zshrc" "$HOME"/helper.*

section orphans "orphans (fake processes adopted by launchd)" covers 'libexec/bosun-launch,libexec/bosun-orphans'
FAKE_PIDS="$WORK/fake-orphan-pids"
fake_process() { # <command line> [child]: a process launchd adopts that shows <command line>; with "child", it has one
  (FAKE_PIDS="$FAKE_PIDS" perl -e 'open(my $f, ">>", $ENV{FAKE_PIDS}) or die; print $f "$$\n"; close $f;
    $0 = $ARGV[0]; if ($ARGV[1]) { if (!fork) { sleep 60; exit } } sleep 60' "$1" "${2:-}" &)
}
wrapper="$SHELL -lic $(printf '%q' "$BOSUN")"
fake_process "$wrapper launch smoke-orphan ; exec $SHELL -l"
fake_process "$wrapper launch smoke-busy ; exec $SHELL -l" child
fake_process "tmux -L $SOCKET new-session -d -s smoke-tmux -- $wrapper launch smoke-tmux"
fake_process "$SHELL -lic /Users/me/elsewhere/bin/bosun launch smoke-elsewhere ; exec $SHELL -l"
wait_for 30 bash -c '[ "$(wc -l <"$1")" -ge 4 ]' _ "$FAKE_PIDS"
out="$("$BOSUN" orphans 2>&1)"
check "orphans lists a launch shell's leftover: launchd's, this checkout's command line, no children" bash -c '
  grep -q "1 shell(s) left behind" <<<"$1" && grep -q "launch smoke-orphan" <<<"$1"' _ "$out"
check "... never one with children, the tmux server or another checkout's" bash -c '! grep -qE "smoke-(busy|tmux|elsewhere)" <<<"$1"' _ "$out"
orphan_pid="$(grep "launch smoke-orphan" <<<"$out" | awk '{ print $1 }')"
check "... and stops nothing without --apply" kill -0 "$orphan_pid"
check "--apply stops it" contains "$("$BOSUN" orphans --apply 2>&1)" "stopped 1 shell(s)"
check "... only it" bash -c 'wait_gone() { for _ in $(seq 1 25); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.2; done; return 1; }
  wait_gone "$1" || exit 1
  for pid in $(cat "$2"); do [ "$pid" = "$1" ] && continue; ps -o command= -p "$pid" | grep -q "launch smoke-orphan" && exit 1; done
  [ "$(ps -o pid= -p "$(paste -sd, "$2")" | wc -l)" -ge 3 ]' _ "$orphan_pid" "$FAKE_PIDS"
check "orphans takes no other argument" fails_with "unknown argument" "$BOSUN" orphans --bogus
while read -r pid; do
  pkill -P "$pid" 2>/dev/null
  kill "$pid" 2>/dev/null
done <"$FAKE_PIDS"

# From here on the session has an orchestrator window.
always_run
export BOSUN_ORCHESTRATOR=orch BOSUN_ORCHESTRATOR_DIR="$REPO_A"
section orchestrator-refresh "orchestrator refresh and check-ins" covers 'libexec/bosun-checkin,libexec/bosun-doctor,libexec/bosun-learn,libexec/bosun-ledger,libexec/bosun-refresh,libexec/bosun-send,libexec/bosun-start,libexec/bosun-update,libexec/bosun-watchdog,lib/update_state.py'
no_session
"$BOSUN" start >/dev/null 2>&1
check "start opens the orchestrator first and checkin last" wait_for 30 has_windows "orch alpha beta gamma checkin "
runs_checkin_loop() { # the checkin window of this test session runs `bosun checkin --loop`
  local pane_pid pid
  pane_pid="$(tmux -L "$SOCKET" list-panes -t "=smoke:checkin" -F '#{pane_pid}' 2>/dev/null)" || return 1
  for pid in $(bash -c 'source "$1/lib/common.sh"; descendants "$2"' _ "$ROOT" "$pane_pid"); do
    ps -o command= -p "$pid" | grep -q "bosun-checkin --loop" && return 0
  done
  return 1
}
check "the checkin window runs the check-in loop" wait_for 30 runs_checkin_loop
tmux -L "$SOCKET" kill-window -t "=smoke:checkin"
check "refresh recreates a missing checkin window" contains "$("$BOSUN" refresh 2>&1)" "checkin: create the missing window"
check "... and it runs the check-in loop again" wait_for 30 runs_checkin_loop
new_checkin_loop() { # <old pid>: the checkin window runs a loop, and not that one
  local now
  now="$(bash -c 'source "$1/lib/common.sh"; checkin_loop_pid "$(tmux -L "$2" list-panes -t "=smoke:checkin" -F "#{pane_pid}")"' _ "$ROOT" "$SOCKET")"
  [ -n "$now" ] && [ "$now" != "$1" ]
}
loop_before="$(bash -c 'source "$1/lib/common.sh"; checkin_loop_pid "$(tmux -L "$2" list-panes -t "=smoke:checkin" -F "#{pane_pid}")"' _ "$ROOT" "$SOCKET")"
check "refresh --checkin-loop restarts the check-in loop" contains "$("$BOSUN" refresh --checkin-loop 2>&1)" "checkin: restart the check-in loop"
check "... stopping the old one" wait_for 30 bash -c '[ -n "$1" ] && ! kill -0 "$1" 2>/dev/null' _ "$loop_before"
check "... and running a new one in the same window" wait_for 30 new_checkin_loop "$loop_before"
check "... and says so when there is no checkin window to restart" fails_with "no checkin window" env BOSUN_CHECKIN_MINUTES=0 "$BOSUN" refresh --checkin-loop
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n loop-itself -- perl -e '$0 = "/bin/bash /x/libexec/bosun-checkin --loop"; sleep 60'
loop_pane="$(tmux -L "$SOCKET" display -p -t "=smoke:loop-itself" '#{pane_pid}')"
check "a check-in loop that is the pane's own process is found too (the watchdog looks for it)" bash -c '
  [ "$(source "$1/lib/common.sh"; checkin_loop_pid "$2")" = "$2" ]' _ "$ROOT" "$loop_pane"
tmux -L "$SOCKET" kill-window -t "=smoke:loop-itself"
orch_pid="$(fake_claude orch idle 2.0.0 3600 orch-2000-01-01)"
out="$("$BOSUN" refresh --dry-run orch 2>&1)"
check "orchestrator restarts on a new day" contains "$out" "orch: would restart (new day: orch-2000-01-01 -> orch-$(date +%F)"
check "... even with --drain-only" contains "$("$BOSUN" refresh --dry-run --drain-only orch 2>&1)" "orch: would restart (new day"
check "checkin starts today's conversation instead of checking in on yesterday's" contains "$("$BOSUN" checkin --dry-run 2>&1)" "would start today's conversation"
launched_with_morning_chores() { # <old pane pid>: the orchestrator's window runs a new launch, of today's morning chores
  local screen
  [ "$(tmux -L "$SOCKET" display -p -t "=smoke:orch" '#{pane_pid}')" != "$1" ] || return 1
  screen="$(tmux -L "$SOCKET" capture-pane -p -J -S -500 -t "=smoke:orch")"
  grep -qF -e "--name orch-$(date +%F) " <<<"$screen" && grep -qF -e '-- /bosun:chores\ morning' <<<"$screen"
}
orch_pane="$(tmux -L "$SOCKET" display -p -t "=smoke:orch" '#{pane_pid}')"
check "refresh --update-restart restarts an orchestrator still on yesterday's conversation as a new day does, and exits 4" bash -c '
  out="$("$1" refresh --update-restart "$2" orch 2>&1)"
  [ $? -eq 4 ] && grep -qF "orch: restart for the update (idle), into orch-$(date +%F), as its next conversation starts" <<<"$out"' _ "$BOSUN" "$WORK/update-prompt"
check "... starting today's with the morning chores" wait_for 30 launched_with_morning_chores "$orch_pane"
check "... not with the update's prompt" bash -c '! tmux -L "$1" capture-pane -p -J -S -500 -t "=smoke:orch" | grep -q bosun-was-updated' _ "$SOCKET"
wait_for 30 bash -c '[ -z "$(pgrep -P "$(tmux -L "$1" display -p -t =smoke:orch "#{pane_pid}")")" ]' _ "$SOCKET"
orch_pane="$(tmux -L "$SOCKET" display -p -t "=smoke:orch" '#{pane_pid}')"
check "... and one whose agent exited, today's conversation not there yet, is relaunched as a new day starts, and exits 4" bash -c '
  out="$("$1" refresh --update-restart "$2" orch 2>&1)"
  [ $? -eq 4 ] && grep -qF "orch: its launch starts a new conversation (orch-$(date +%F))" <<<"$out"' _ "$BOSUN" "$WORK/update-prompt"
check "... with the morning chores" wait_for 30 launched_with_morning_chores "$orch_pane"
check "... never the update's prompt in a blank conversation" bash -c '! tmux -L "$1" capture-pane -p -J -S -500 -t "=smoke:orch" | grep -q bosun-was-updated' _ "$SOCKET"
wait_for 30 bash -c '[ -z "$(pgrep -P "$(tmux -L "$1" display -p -t =smoke:orch "#{pane_pid}")")" ]' _ "$SOCKET"
orch_pid="$(fake_claude orch idle 2.0.0 3600 orch-2000-01-01)"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-2000-01-01"}' "$orch_pid" $((($(date +%s) - 30) * 1000)) >"$HOME/.claude/sessions/$orch_pid.json"
out="$("$BOSUN" refresh --dry-run orch 2>&1)"
check "a deferred new-day restart sends no drain prompt into yesterday's conversation" bash -c 'grep -q "still on yesterday" <<<"$1" && ! grep -q "drain prompt" <<<"$1"' _ "$out"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-%s"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) "$(date +%F)" >"$HOME/.claude/sessions/$orch_pid.json"
check "checkin nudges an idle orchestrator" contains "$("$BOSUN" checkin --dry-run 2>&1)" "would send '/bosun:chores check-in'"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch"}' "$orch_pid" $((($(date +%s) - 30) * 1000)) >"$HOME/.claude/sessions/$orch_pid.json"
check "checkin waits while you may be talking to it" bash -c 'grep -qE "skip: orchestrator is idle \(for [0-9]+s\)" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
printf '{"pid":%d,"status":"busy","version":"2.0.0","statusUpdatedAt":%d,"name":"orch"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$orch_pid.json"

# An update's record: its hold pauses the background work; a hold past its hour is a finding.
update_record() { # <step> <hold_until>
  printf '{"id":"u1","from_root":"/a","to_root":"/b","from_commit":"aaa","to_commit":"bbb","step":"%s","started_at":"2026-11-05T09:00:00","hold_until":%d}' "$1" "$2" >"$BOSUN_STATE_DIR/update.json"
}
update_record holding $(($(date +%s) + 600))
check "a hold left by an update that died is no hold" bash -c '! "$1" checkin --dry-run 2>&1 | grep -q "update in progress"' _ "$BOSUN"
check "... and is the update-stuck finding at once" contains "$("$BOSUN" watchdog --dry-run --only update-stuck 2>&1)" "left its hold"
# A running update: a process holding update.lock exclusively (the flock update_state.one_update takes).
hold_lock "$BOSUN_STATE_DIR/update.lock" orch-update
check "checkin skips its tick while an update holds" contains "$("$BOSUN" checkin --dry-run 2>&1)" "update in progress: skipping this tick"
check "the watchdog skips a run while an update holds" contains "$("$BOSUN" watchdog 2>&1)" "update in progress"
check "learning skips a run while an update holds" contains "$("$BOSUN" learn 2>&1)" "update in progress"
# A live hold within its hour is no finding (test_update_state.py); the watchdog's update-stuck wiring is above.
update_record holding $(($(date +%s) - 60))
check "doctor fails on a hold past its hour" contains "$("$BOSUN" doctor 2>&1)" "FAIL  update: an update's hold"
release_lock orch-update
# What the last update left to you, from its record: a change to make in your own files, and the
# config keys it added that bosun.env does not set.
jq -n '{id: "u1", from_root: "/a", to_root: "/b", from_commit: "aaa", to_commit: "bbb", step: "done",
  started_at: "2026-11-05T09:00:00+00:00", hold_until: 0, user_changes: ["sessions.yml: rename `model:` to `agent_model:`"],
  plan: {keys_added: ["BOSUN_WAKE_DELAY_MINUTES", "BOSUN_DRAIN_PROMPT"]}}' >"$BOSUN_STATE_DIR/update.json"
echo 'BOSUN_DRAIN_PROMPT="acme: carry on"' >"$BOSUN_CONFIG_DIR/bosun.env"
out="$("$BOSUN" doctor 2>&1)"
check "doctor lists what the last update left to you: a change to make by hand in your own files" contains "$out" "warn  change by hand: sessions.yml: rename \`model:\` to \`agent_model:\`"
check "... and each new key bosun.env does not set, with the value it has now" contains "$out" "ok    new key, not set in bosun.env: BOSUN_WAKE_DELAY_MINUTES (now '1')"
check "... but not one it sets" not_contains "$out" "bosun.env: BOSUN_DRAIN_PROMPT"
rm -f "$BOSUN_STATE_DIR/update.json" "$BOSUN_CONFIG_DIR/bosun.env"
orch_status() { # <status> <seconds in it> [name]
  printf '{"pid":%d,"status":"%s","version":"2.0.0","statusUpdatedAt":%d,"name":"%s"}' "$orch_pid" "$1" $((($(date +%s) - $2) * 1000)) "${3:-orch-$(date +%F)}" >"$HOME/.claude/sessions/$orch_pid.json"
}
orch_status shell 1440
check "a background command alone (shell) does not hold the check-in back" contains "$("$BOSUN" checkin --dry-run 2>&1)" "would send '/bosun:chores check-in'"
orch_status shell 30
check "... but its turn must have ended 5 minutes ago" bash -c 'grep -qE "skip: orchestrator is shell \(for [0-9]+s\)" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
orch_status shell 1440 orch-2000-01-01
check "... and yesterday's conversation is not restarted under it" bash -c 'grep -q "background command runs (shell); today.s starts once it is idle" <<<"$1" && ! grep -q "would start today" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
orch_status waiting 3600
check "checkin still skips an orchestrator at a prompt, and exits 3 (deferred)" bash -c '
  out="$("$1" checkin --dry-run 2>&1)"
  [ $? -eq 3 ] && grep -qF "skip: orchestrator is waiting" <<<"$out"' _ "$BOSUN"
orch_status idle 3600
gate_state="$WORK/gate-state" # its own state: no events queued by earlier checks
BOSUN_STATE_DIR="$gate_state" L mark-run check-in >/dev/null
mkdir -p "$gate_state/slack-watch"
quiet_run_at="$(python3 -c 'from datetime import UTC, datetime, timedelta; print((datetime.now(UTC) + timedelta(seconds=5)).isoformat())')"
printf '{"failures":0,"runs":[{"started":"%s","found":0}]}' "$quiet_run_at" >"$gate_state/slack-watch/state.json"
gated() { BOSUN_STATE_DIR="$gate_state" BOSUN_EVENTS_ENABLED=1 BOSUN_SLACK_WATCH_ENABLED=1 "$BOSUN" checkin --dry-run 2>&1; }
check "a check-in with nothing new since the last one is skipped" bash -c 'grep -q "skip: nothing new since" <<<"$1" && ! grep -q "would send" <<<"$1"' _ "$(gated)"
check "... unless BOSUN_CHECKIN_MAX_QUIET_HOURS=0 turns the gate off" contains "$(BOSUN_CHECKIN_MAX_QUIET_HOURS=0 gated)" "would send '/bosun:chores check-in'"
# (What makes a check-in news, a failing Slack watcher among it, is test_checkin_gate.py's.)
orch_status busy 3600
check "checkin skips a busy orchestrator: a check-in held back by timing exits 3 (deferred)" bash -c '
  out="$("$1" checkin --dry-run 2>&1)"
  [ $? -eq 3 ] && grep -qF "skip: orchestrator is busy" <<<"$out"' _ "$BOSUN"
BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_INTERVAL_SECONDS=1 BOSUN_CHECKIN_EVERY_SECONDS=1 \
  "$BOSUN" checkin --loop --dry-run >"$WORK/deferred-loop.out" 2>&1 &
deferred_loop=$!
check "the loop keeps a deferred check-in due" wait_for 30 grep -q "the check-in stays due; retrying every tick" "$WORK/deferred-loop.out"
check "... and retries on its next ticks while the orchestrator is busy (each tick's events line)" wait_for 30 bash -c '
  [ "$(sed -n "/the check-in stays due; retrying every tick/,\$p" "$1" | grep -c "\[events\] dry-run: ")" -ge 3 ]' _ "$WORK/deferred-loop.out"
orch_status idle 3600
check "... and sends it on a later tick, once the orchestrator is free" wait_for 30 grep -q "would send '/bosun:chores check-in'" "$WORK/deferred-loop.out"
pkill -P "$deferred_loop" 2>/dev/null
kill "$deferred_loop" 2>/dev/null
wait "$deferred_loop" 2>/dev/null
check "... logging the skip once, not on every retry" test "$(grep -c 'skip: orchestrator is busy' "$WORK/deferred-loop.out")" = 1
check "... while events keep flowing during the wait" grep -q "\[events\] dry-run: " "$WORK/deferred-loop.out"
orch_status busy 3600 orch

# Which errors are a login's, a usage limit's or no credit's, and the watchdog letting go once another window gets a
# reply, are tests/unit/test_login.py's and test_watchdog.py's, over transcripts as here; this is the gate end to end.
section expired-login "expired Claude login, and no credit (fake claude, fictional transcripts)" needs orchestrator-refresh covers 'libexec/bosun-checkin,libexec/bosun-doctor,libexec/bosun-status,libexec/bosun-watchdog,lib/login.py,lib/signals.py'
ORCH_TRANSCRIPT="$(project_dir "$REPO_A")/0000000a-0000-0000-0000-000000000000.jsonl"
mkdir -p "$(dirname "$ORCH_TRANSCRIPT")"
login_reply() { # <seconds ago> [error fields]: a reply in the orchestrator's transcript
  local at
  at="$(date -u -r $(($(date +%s) - $1)) +%Y-%m-%dT%H:%M:%S.000Z)"
  if [ -n "${2:-}" ]; then
    printf '{"type":"assistant","timestamp":"%s","isApiErrorMessage":true,%s,"message":{"model":"<synthetic>","content":[{"type":"text","text":"Login expired"}]}}\n' "$at" "$2"
  else
    printf '{"type":"assistant","timestamp":"%s","message":{"content":[{"type":"text","text":"Checked in."}]}}\n' "$at"
  fi >>"$ORCH_TRANSCRIPT"
}
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-%s","cwd":"%s","sessionId":"0000000a-0000-0000-0000-000000000000"}' \
  "$orch_pid" $((($(date +%s) - 3600) * 1000)) "$(date +%F)" "$REPO_A" >"$HOME/.claude/sessions/$orch_pid.json"
login_reply 7200
login_reply 3700 '"error":"authentication_failed","apiErrorStatus":401'
login_reply 3600 '"error":"authentication_failed"'
LC() { BOSUN_CLAUDE_BIN="$WORK/login-claude" BOSUN_LOGIN_REPLY_WAIT_SECONDS=1 "$BOSUN" "$@" 2>&1; }
out="$(FAKE_PROBE=auth LC checkin)"
check "a check-in to an orchestrator that gets only login errors is not sent" bash -c 'grep -q "orch needs /login: every reply since" <<<"$1" && grep -q "skip: orch needs /login" <<<"$1" && ! grep -q delivered <<<"$1"' _ "$out"
check "... after one login check request, which failed" test "$(probes)" = 1
out="$(FAKE_PROBE=auth LC checkin)"
check "the next check-in only skips, quietly" bash -c 'grep -q "skip: orch needs /login" <<<"$1" && ! grep -q "needs /login:" <<<"$1"' _ "$out"
check "... and makes no new login check request yet" test "$(probes)" = 1
check "status shows the window that needs a login" bash -c 'grep -q "Claude login" <<<"$1" && grep -E "^  orch +needs-login" <<<"$1"' _ "$(LC status)"
check "the watchdog's status shows it" bash -c 'grep "FAIL  login-expired" <<<"$1" | grep -q "every prompt in orch (since"' _ "$(LC watchdog --status --only login-expired)"
# A reply in another window after every window's last login error lets the gate go too: the login is the Mac's.
other_pid="$(fake_claude alpha idle 2.0.0 60)"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"alpha","cwd":"%s","sessionId":"0000000b-0000-0000-0000-000000000000"}' \
  "$other_pid" $((($(date +%s) - 60) * 1000)) "$REPO_A" >"$HOME/.claude/sessions/$other_pid.json"
OTHER_TRANSCRIPT="$(project_dir "$REPO_A")/0000000b-0000-0000-0000-000000000000.jsonl"
printf '{"type":"assistant","timestamp":"%s","message":{"model":"claude-model","content":[{"type":"text","text":"Done."}]}}\n' \
  "$(date -u -r $(($(date +%s) - 120)) +%Y-%m-%dT%H:%M:%S.000Z)" >"$OTHER_TRANSCRIPT"
check "a reply in another window lets the gate go, as the watchdog reads it" bash -c 'grep -q "orch works again (alpha got a reply at .*, after its last login error)" <<<"$1" && grep -q "would send" <<<"$1"' _ "$(BOSUN_CHECKIN_MAX_QUIET_HOURS=0 LC checkin --dry-run)"
kill "$other_pid" 2>/dev/null
rm -f "$OTHER_TRANSCRIPT" "$HOME/.claude/sessions/$other_pid.json"
# login.py failing (here its lock is a folder) gives no verdict, which never counts as working.
rm -f "$BOSUN_STATE_DIR/login.lock"
mkdir "$BOSUN_STATE_DIR/login.lock"
out="$(BOSUN_CHECKIN_MAX_QUIET_HOURS=0 LC checkin)"
check "no verdict from login.py keeps a window held back for its login" bash -c 'grep -q "orch: no verdict from login.py; it stays held back for its login" <<<"$1" && grep -q "skip: orch needs /login" <<<"$1" && ! grep -q delivered <<<"$1"' _ "$out"
rmdir "$BOSUN_STATE_DIR/login.lock"
login_reply 60
check "a normal reply resumes check-ins" bash -c 'grep -q "orch works again" <<<"$1" && grep -q "would send" <<<"$1"' _ "$(LC checkin --dry-run)"
check "the dry run left the record alone" contains "$(cat "$BOSUN_STATE_DIR/login.json")" '"orch"'
LC checkin >/dev/null
check "a real check-in clears it" bash -c '! grep -q "\"orch\"" "$1"' _ "$BOSUN_STATE_DIR/login.json"
login_reply 30 '"error":"rate_limit","apiError":"usage_limit_reached","apiErrorStatus":429'
out="$(LC watchdog --status --only spend-limit)"
check "a usage-limit (spend limit) stop shows in the watchdog's status" bash -c 'grep "FAIL  spend-limit" <<<"$1" | grep -q "orch: it stopped on a usage limit (spend limit)"' _ "$out"
probes_before="$(probes)"
out="$(LC watchdog --dry-run --only spend-limit)"
check "the dry run names it and makes no login check request" bash -c 'grep -q "dry-run: spend-limit: .*orch: it stopped on a usage limit" <<<"$1" && [ "$2" = "$3" ]' _ "$out" "$probes_before" "$(probes)"
# An API key whose organization has no credit that covers Claude Code: every reply is "Credit balance too low".
# First a login error that holds check-ins back; then the /login with such a key.
login_reply 25 '"error":"authentication_failed","apiErrorStatus":401'
check "a login error holds check-ins back again" contains "$(BOSUN_CHECKIN_MAX_QUIET_HOURS=0 FAKE_PROBE=auth LC checkin)" "skip: orch needs /login"
login_reply 20 '"error":"billing_error","apiErrorStatus":400'
login_reply 10 '"error":"billing_error","apiErrorStatus":400'
check "a window whose every reply is 'Credit balance too low' shows in the watchdog's status" bash -c 'grep "FAIL  no-credit" <<<"$1" | grep -q "orch: every prompt gets .Credit balance too low. since"' _ "$(LC watchdog --status --only no-credit)"
check "status shows that the window has no credit" bash -c 'grep -qE "^  orch +no-credit +every reply since .+ is .credit balance too low. \(billing_error/400\)" <<<"$1"' _ "$(LC status)"
check "doctor fails the window, saying what to do" bash -c 'grep -qF "FAIL  window orch no-credit every reply since" <<<"$1" && grep -qF "then tell it to carry on (bosun send orch ...); or use your subscription: remove the API key (ANTHROPIC_API_KEY in your shell profile, or apiKeyHelper in ~/.claude/settings.json), restart the window (bosun refresh orch), then /login in it" <<<"$1"' _ "$(LC doctor)"
check "its login works, so check-ins go to it again, as on a usage limit" bash -c 'grep -q "orch.s login works again, but it has no credit (every reply since" <<<"$1" && grep -q "would send" <<<"$1"' _ "$(BOSUN_CHECKIN_MAX_QUIET_HOURS=0 LC checkin --dry-run)"
# Back to the busy orchestrator the events tests expect, and no state from the real check-ins.
rm -f "$ORCH_TRANSCRIPT" "$BOSUN_STATE_DIR/login.json" "$BOSUN_STATE_DIR/watchdog.json"
printf '{"pid":%d,"status":"busy","version":"2.0.0","statusUpdatedAt":%d,"name":"orch"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) >"$HOME/.claude/sessions/$orch_pid.json"

section events "events to the orchestrator (fake Claude, send dry-run)" needs ledger,repo-window-prompts,orchestrator-refresh covers 'libexec/bosun-events,libexec/bosun-ledger'
EV() { BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_DEBOUNCE_SECONDS=0 BOSUN_EVENTS_MIN_IDLE_SECONDS=60 BOSUN_SEND_DRY_RUN=1 "$BOSUN" events "$@" 2>&1; }
check "events are off by default" contains "$("$BOSUN" events 2>&1)" "events are off (BOSUN_EVENTS_ENABLED=0)"
check "the earlier user-said events are pending" contains "$("$BOSUN" events --list)" "user-said exporter"
check "nothing goes to a busy orchestrator" contains "$(EV)" "skip: the orchestrator is busy"
# (One in shell, its turn over, counts as idle: test_events.py.)
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-%s"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) "$(date +%F)" >"$HOME/.claude/sessions/$orch_pid.json"
check "a dry run shows the one batched summary" contains "$(EV --dry-run)" "dry-run: would deliver:"
delivery_day=$(date +%F) # a delivery just before midnight is yesterday's by the time its status is read
check "an idle orchestrator gets the summary" contains "$(EV)" "delivered "
check "... once" bash -c '[ "$("$1" events --list)" = "(no events)" ] && [ -z "$(BOSUN_EVENTS_ENABLED=1 "$1" events 2>&1)" ]' _ "$BOSUN"
L report 'slack:C1/6' --summary 'found the cause' >/dev/null
check "with events off the ledger queues nothing" bash -c '[ "$("$1" events --list)" = "(no events)" ]' _ "$BOSUN"
BOSUN_EVENTS_ENABLED=1 L report 'slack:C1/6' --summary 'found the cause, again' >/dev/null
check "a check-in covers what is pending" contains "$("$BOSUN" events --covered)" "1 pending event(s) covered by the check-in"
events_status=$("$BOSUN" events --status)
deliveries_line="deliveries today: 1"
[ "$(date +%F)" = "$delivery_day" ] || deliveries_line="deliveries today: " # the run crossed midnight
check "status shows the dispatcher's last run" contains "$events_status" "$deliveries_line"

section slack-watcher "slack watcher (fake claude with fixture JSON)" needs orchestrator-refresh covers 'libexec/bosun-checkin,libexec/bosun-events,libexec/bosun-slack-watch,libexec/bosun-watchdog'
# The orchestrator is idle, as the section before leaves it (a dispatcher run delivers to an idle one).
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"orch-%s"}' "$orch_pid" $((($(date +%s) - 3600) * 1000)) "$(date +%F)" >"$HOME/.claude/sessions/$orch_pid.json"
# The fake prints what a real call streams: the init message with the tools it could see, then the
# result with the structured output. SLACK_FAKE=write adds a write tool, SLACK_FAKE=garbage prints
# no result. Its arguments go to slack-claude.args.
cat >"$WORK/slack-claude" <<FAKE
#!/bin/bash
printf '%s\n' "\$@" >"$WORK/slack-claude.args"
cat >/dev/null
p=mcp__plugin_slack_slack__
tools="\"\${p}slack_search_public_and_private\",\"\${p}slack_read_thread\""
[ "\${SLACK_FAKE:-}" = write ] && tools="\$tools,\"\${p}slack_send_message\""
[ "\${SLACK_FAKE:-}" = garbage ] && { echo "not json"; exit 1; }
sec=$(($(date +%s) - 120))
link="https://acme.slack.com/archives/C0000000001/p\${sec}000100"
echo "{\"type\":\"system\",\"subtype\":\"init\",\"tools\":[\$tools]}"
echo "{\"type\":\"result\",\"is_error\":false,\"total_cost_usd\":0.03,\"permission_denials\":[],\"structured_output\":{\"truncated\":false,\"messages\":[{\"permalink\":\"\$link\",\"channel\":\"C0000000001\",\"ts\":\"\$sec.000100\",\"thread_ts\":\"\",\"author\":\"Dana\",\"kind\":\"mention\",\"excerpt\":\"can you check the exporter header?\"},{\"permalink\":\"https://evil.example.com/x\",\"channel\":\"C1\",\"ts\":\"1.0\",\"thread_ts\":\"\",\"author\":\"x\",\"kind\":\"dm\",\"excerpt\":\"x\"}]}}"
FAKE
chmod +x "$WORK/slack-claude"
echo '{"mcpServers": {"plugin:slack:slack": {"type": "http", "url": "https://mcp.example.com/mcp"}}}' >"$WORK/slack-mcp.json"
SW() { BOSUN_CLAUDE_BIN="$WORK/slack-claude" BOSUN_SLACK_WATCH_MCP_CONFIG="$WORK/slack-mcp.json" "$BOSUN" slack-watch "$@" 2>&1; }
SWE() { BOSUN_EVENTS_ENABLED=1 SW "$@"; }
"$BOSUN" events --covered >/dev/null
check "a scheduled watch does nothing while it is off" bash -c '[ -z "$1" ] && [ ! -e "$2/slack-watch/state.json" ]' _ "$(SW --scheduled)" "$BOSUN_STATE_DIR"
check "a watch needs events on" contains "$(SW)" "events are off (BOSUN_EVENTS_ENABLED=0)"
cp "$BOSUN_STATE_DIR/slack-watch/state.json" "$WORK/slack-state-before.json"
out="$(SWE --dry-run)"
check "a dry run shows what it would queue, and the bad entry rejected" bash -c 'grep -q "would queue: mention https://acme.slack.com/archives/C0000000001/p" <<<"$1" && grep -q "1 rejected (not a Slack permalink)" <<<"$1"' _ "$out"
check "... queues nothing and writes no state" bash -c '[ "$("$1" events --list)" = "(no events)" ] && cmp -s "$2/slack-watch/state.json" "$3"' _ "$BOSUN" "$BOSUN_STATE_DIR" "$WORK/slack-state-before.json"
check "the call gets only Slack's read tools, no built-in tools and no settings" bash -c '
  args="$(tr "\n" " " <"$1")"
  for want in "--strict-mcp-config" "--setting-sources  " "--tools  --allowedTools" "--permission-mode dontAsk" "--permission-prompts none" "--disallowedTools mcp__plugin_slack_slack__slack_send_message"; do
    grep -qF -- "$want" <<<"$args" || { echo "missing: $want"; exit 1; }
  done
  ! grep -qE "Bash|--dangerously" <<<"$args"' _ "$WORK/slack-claude.args"
# My plugins: one my settings enable, and the Slack plugin installed with its server. The call loads none of them
# (it reads no settings file, and bosun does not rely on that), but the Slack plugin when the server is its own.
mkdir -p "$HOME/.claude/plugins" "$WORK/slack-plugin"
echo '{"enabledPlugins": {"probe@local": true}}' >"$HOME/.claude/settings.json"
printf '{"plugins": {"slack@acme-market": [{"scope": "user", "installPath": "%s"}]}}\n' "$WORK/slack-plugin" >"$HOME/.claude/plugins/installed_plugins.json"
echo '{"mcpServers": {"slack": {"type": "http", "url": "https://mcp.example.com/mcp"}}}' >"$WORK/slack-plugin/.mcp.json"
call_plugins() { grep -m1 -A1 -x -- --settings "$WORK/slack-claude.args" | tail -1 | jq -c .enabledPlugins; }
SWE --dry-run >/dev/null
check "the call switches off each plugin of mine, the Slack plugin too when a site's file holds the server" \
  test "$(call_plugins)" = '{"probe@local":false,"slack@acme-market":false}'
# (The Slack plugin kept when the server is its own, and an installed_plugins.json that does not parse: test_slack_watch.py.)
rm -rf "$HOME/.claude/settings.json" "$HOME/.claude/plugins" "$WORK/slack-plugin"
check "a watch queues one slack event per new message" contains "$(SWE)" "1 message(s) (1 mention, 0 dm, 0 thread-reply), 1 new"
check "... which the event list shows with its permalink" contains "$("$BOSUN" events --list)" "slack     slack-watch    https://acme.slack.com/archives/C0000000001/p"
check "... and the dispatcher frames as untrusted, for chores step 1" contains "$(BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_FAST_DEBOUNCE_SECONDS=0 BOSUN_SEND_DRY_RUN=1 "$BOSUN" events --dry-run 2>&1)" "untrusted excerpt: \"can you check the exporter header?\""
check "the next watch does not queue it again" contains "$(SWE)" "1 already queued"
# (When a scheduled watch runs, in your hours and outside them: test_slack_watch.py.)
check "a call that could see a write tool is refused" bash -c 'out="$(SLACK_FAKE=write "$@")"; [ $? -eq 1 ] && grep -q "ERROR: the Slack watch failed (1 in a row): tool restriction not in force" <<<"$out"' _ env BOSUN_EVENTS_ENABLED=1 BOSUN_CLAUDE_BIN="$WORK/slack-claude" BOSUN_SLACK_WATCH_MCP_CONFIG="$WORK/slack-mcp.json" "$BOSUN" slack-watch
check "output that is not the JSON is a failure" contains "$(SLACK_FAKE=garbage SWE)" "failed (2 in a row)"
sw_status="$(SW --status)"
check "status shows the failures and today's spend, a refused call's included" bash -c 'grep -q "failures in a row: 2" <<<"$1" && grep -q "spent today \$0.09" <<<"$1"' _ "$sw_status"
check "... and both intervals" contains "$sw_status" "every 15 min in your hours and every 120 min outside them"
check "the watchdog checks the watcher once it is enabled" contains "$(BOSUN_EVENTS_ENABLED=1 BOSUN_SLACK_WATCH_ENABLED=1 "$BOSUN" watchdog --status 2>&1)" "slack-watch      "
check "the check-in loop runs the watcher with events on" contains "$(BOSUN_EVENTS_ENABLED=1 "$BOSUN" checkin --dry-run 2>&1)" "dry-run: would run bosun slack-watch --scheduled"
"$BOSUN" events --covered >/dev/null

section alerts "alerts" needs orchestrator-refresh covers 'libexec/bosun-alert,libexec/bosun-checkin,libexec/bosun-send,libexec/bosun-watchdog,libexec/bosun-ledger'
echo '{"result":"https://x.slack.com/archives/D1/p1","permission_denials":[],"total_cost_usd":0.02}' >"$WORK/alert-sent.json"
cat >"$WORK/alert-claude" <<FAKE
#!/bin/bash
[ "\$1" = auth ] && { echo '{"loggedIn": true, "authMethod": "claude.ai"}'; exit 0; } # the watchdog's login check
printf '%s\n' "\$@" >"$WORK/alert-claude.args"
if [ -n "\${ALERT_FAKE_REPLY:-}" ]; then echo "\$ALERT_FAKE_REPLY"; else cat "$WORK/alert-sent.json"; fi
FAKE
chmod +x "$WORK/alert-claude"
export BOSUN_ALERT_SLACK_USER=U0TEST
# The watchdog alerts only inside the awake window: pin its clock to noon today, every day scheduled,
# and give the fake records times relative to that.
NOON="$(date -j -f '%Y-%m-%d %H:%M:%S' "$(date +%F) 12:00:00" +%s)"
export BOSUN_WATCHDOG_NOW="$NOON" BOSUN_DAYS=MTWRFSU
claude_record() { # <pid> <status> <seconds in status at noon> [name]
  printf '{"pid":%d,"status":"%s","version":"2.0.0","statusUpdatedAt":%d,"name":"%s"}' \
    "$1" "$2" $(((NOON - $3) * 1000)) "${4:-orch}" >"$HOME/.claude/sessions/$1.json"
}
# (A wait under the threshold alerts nothing: test_watchdog.py's test_a_short_wait_does_not_alert.)
claude_record "$orch_pid" waiting 3600
check "a long wait at a prompt alerts on Slack" contains "$("$BOSUN" checkin --dry-run 2>&1)" "would notify and DM you on Slack: [bosun] The orch session has waited 60 min"
check "the check-in loop's dry run changes no watchdog state" test ! -e "$BOSUN_STATE_DIR/watchdog.json"
out="$(BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" checkin 2>&1)"
check "... and sends it" contains "$out" "alert sent as a Slack DM"
check "the DM can never wait for an approval" bash -c 'grep -qx dontAsk "$1" && grep -qx none "$1" && grep -qx ToolSearch "$1" && ! grep -q -- "--dangerously" "$1"' _ "$WORK/alert-claude.args"
check "... once per prompt" bash -c '! grep -q alert <<<"$1"' _ "$(BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" checkin 2>&1)"
check "... and the loop's old marker agrees, for a loop still running the old code" test "$(cat "$BOSUN_STATE_DIR/alerted-orch")" = "$orch_pid:$(((NOON - 3600) * 1000))"
mkdir -p "$HOME/Library/LaunchAgents"
touch "$HOME/Library/LaunchAgents/com.bosun.morning.watchdog.plist"
claude_record "$orch_pid" waiting 7200
check "once the watchdog agent is installed, the loop leaves alerts to it" bash -c '! grep -q "watchdog\|would notify" <<<"$1"' _ "$("$BOSUN" checkin --dry-run 2>&1)"
rm "$HOME/Library/LaunchAgents/com.bosun.morning.watchdog.plist"
check "a refused DM is reported" fails_with "not delivered on Slack" env BOSUN_CLAUDE_BIN="$WORK/alert-claude" ALERT_FAKE_REPLY='{"result":"blocked","permission_denials":[{"tool_name":"x"}]}' "$BOSUN" alert hi
# My plugins: one my settings enable, and the Slack plugin installed. The DM loads none of them but the Slack plugin,
# whose server it may send with; with an installed_plugins.json that does not parse, it still tries, and says why.
mkdir -p "$HOME/.claude/plugins"
echo '{"enabledPlugins": {"probe@local": true, "slack@acme-market": true}}' >"$HOME/.claude/settings.json"
echo '{"plugins": {"slack@acme-market": [{"scope": "user"}], "acme-helper@acme-market": [{"scope": "user"}]}}' >"$HOME/.claude/plugins/installed_plugins.json"
BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" alert "plugins check" >/dev/null 2>&1
check "the DM switches off each plugin of mine but the Slack plugin, in one --settings" bash -c '
  [ "$(grep -cx -- --settings "$1")" = 1 ] &&
    [ "$(grep -m1 -A1 -x -- --settings "$1" | tail -1 | jq -c .)" = "{\"enabledPlugins\":{\"acme-helper@acme-market\":false,\"probe@local\":false}}" ]' \
  _ "$WORK/alert-claude.args"
echo '[not an object]' >"$HOME/.claude/plugins/installed_plugins.json"
out="$(BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" alert "plugins check" 2>&1)"
check "with an installed_plugins.json that does not parse, the DM is still sent" contains "$out" "alert sent as a Slack DM"
check "... logging what the plugins step said, and which step failed" bash -c '
  grep -qF "[alert] plugins: $2 does not parse, so bosun cannot tell which plugins to switch off" <<<"$1" &&
    grep -qF "[alert] warning: the Slack DM loads your plugins: lib/plugins_claude.py exited 2 (its lines above)" <<<"$1"' \
  _ "$out" "$HOME/.claude/plugins/installed_plugins.json"
check "... and without a --settings" bash -c '! grep -qx -- --settings "$1"' _ "$WORK/alert-claude.args"
# Settings that are not what the step should print (a plugin left on): said so, every line of the step logged, sent.
# A python3 stand-in answers for lib/plugins_claude.py only, in the fixture HOME's ~/.local/bin, which bosun puts
# first on its PATH; it goes again right after the one run.
real_python="$(command -v python3)"
cat >"$HOME/.local/bin/python3" <<FAKE
#!/bin/bash
case "\$1" in
  */plugins_claude.py) echo '{"enabledPlugins": {"x@y": true}}' && echo "plugins: a line of the step" >&2 ;;
  *) exec "$real_python" "\$@" ;;
esac
FAKE
chmod +x "$HOME/.local/bin/python3"
out="$(BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" alert "plugins check" 2>&1)"
rm -f "$HOME/.local/bin/python3"
check "settings that are not valid are said, with the step's lines, and the DM still sent" bash -c '
  grep -qF "[alert] plugins: a line of the step" <<<"$1" &&
    grep -qF "[alert] warning: the Slack DM loads your plugins: lib/plugins_claude.py printed settings that are not valid" <<<"$1" &&
    grep -qF "alert sent as a Slack DM" <<<"$1" && ! grep -qx -- --settings "$2" || { echo "$1"; exit 1; }' _ "$out" "$WORK/alert-claude.args"
rm -rf "$HOME/.claude/settings.json" "$HOME/.claude/plugins"
check "a bad member id is refused" fails_with "must be a Slack member id" env BOSUN_ALERT_SLACK_USER='U1"; x' "$BOSUN" alert hi
check "without a member id, only a notification" contains "$(BOSUN_ALERT_SLACK_USER='' "$BOSUN" alert hi 2>&1)" "notification only"
check "send dry-run only logs" contains "$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" send orch hello 2>&1)" "dry-run: would deliver to smoke:orch: hello"
check "send --new dry-run opens nothing" bash -c 'BOSUN_SEND_DRY_RUN=1 "$1" send --new extra "$2" hi >/dev/null && ! tmux -L "$3" list-windows -t =smoke -F "#{window_name}" | grep -qx extra' _ "$BOSUN" "$REPO_A" "$SOCKET"

section watchdog "watchdog (pinned clock, fake alert Claude)" needs alerts covers 'libexec/bosun-status,libexec/bosun-stop,libexec/bosun-watchdog'
# A label with no launchd job, so `launchctl print` (read-only) finds nothing.
export BOSUN_LAUNCHD_LABEL="com.bosun.smoke-watchdog"
TODAY="$(date +%F)"
printf '%s [morning] === morning run (trigger=launchd, session=smoke, dry_run=0) ===\n%s [morning] morning run complete\n' \
  "$TODAY 08:01:00" "$TODAY 08:01:40" >"$BOSUN_LOG_DIR/morning-$TODAY.log"
WD() { BOSUN_CLAUDE_BIN="$WORK/alert-claude" "$BOSUN" watchdog "$@" 2>&1; }
at_minute() { echo $((NOON + $1 * 60)); } # the pinned clock, <n> minutes after noon
# (Its usage names every check of CHECKS: tests/unit/test_watchdog_usage.py.)
check "an unknown check is a usage error" bash -c '"$1" watchdog --only nope >/dev/null 2>&1; [ $? -eq 2 ]' _ "$BOSUN"
claude_record "$orch_pid" waiting 3600 # the prompt the check-in loop alerted about above
out="$(WD --status)"
check "status sees the session and its check-in loop" contains "$out" "checkin-missing  the check-in loop is running"
check "status shows a prompt it already alerted about" bash -c 'grep "WARN  waiting" <<<"$1" | grep -q "already alerted"' _ "$out"
check "status on a fresh install: the first run only baselines the logs" contains "$out" "first scan:"
cp "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before.json"
claude_record "$orch_pid" waiting 3000
check "a new prompt: the dry run would alert" contains "$(WD --dry-run)" "would notify and DM you on Slack: [bosun] The orch session has waited 50 min"
check "... and changes no state" cmp -s "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before.json"
check "a run alerts" contains "$(WD)" "alert sent as a Slack DM"
check "... once" test -z "$(BOSUN_WATCHDOG_NOW="$(at_minute 5)" WD)"
check "the first full run baselined the logs and recorded itself" bash -c 'jq -e ".logs != null and .last_run != null" "$1"' _ "$BOSUN_STATE_DIR/watchdog.json"
echo "$TODAY 12:07:00 [checkin] ERROR: something broke" >>"$BOSUN_LOG_DIR/checkin-$TODAY.log"
check "a new ERROR line in a log alerts" contains "$(BOSUN_WATCHDOG_NOW="$(at_minute 10)" WD)" "alert sent as a Slack DM (\$0.02): 1 new ERROR line(s) in the bosun logs: checkin-$TODAY.log: [checkin] ERROR: something broke"
claude_record "$orch_pid" shell 14400 # a background command since 08:00
cp "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before.json"
check "a background command keeping a window in shell for 4 awake hours warns" contains "$(WD --dry-run --only long-shell)" "long-shell: alerts now: A background command has kept orch in shell for 4h"
check "... the dry run records no episode" cmp -s "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before.json"
check "... and status shows it" bash -c 'grep "WARN  long-shell" <<<"$1" | grep -q "check it is still wanted"' _ "$(WD --status --only long-shell)"
claude_record "$orch_pid" waiting 3000 # back to the prompt alerted about above
"$BOSUN" stop >/dev/null 2>&1
check "stop records that you stopped the session" test -f "$BOSUN_STATE_DIR/stopped-smoke"
check "a session you stopped is not missing" contains "$(BOSUN_WATCHDOG_NOW="$(at_minute 15)" WD --status)" "session-missing  you stopped it at"
check "... and nothing alerts" test -z "$(BOSUN_WATCHDOG_NOW="$(at_minute 20)" WD)"
rm "$BOSUN_STATE_DIR/stopped-smoke"
check "a session gone without a stop is confirmed on the next run" contains "$(BOSUN_WATCHDOG_NOW="$(at_minute 25)" WD --dry-run)" "session-missing: alerts if still so on the next run"
BOSUN_WATCHDOG_NOW="$(at_minute 25)" WD >/dev/null
check "... then alerts" contains "$(BOSUN_WATCHDOG_NOW="$(at_minute 30)" WD)" "tmux session 'smoke' is not running"
# (Once, and nothing sent outside the awake window: test_watchdog.py.)
check "status reports the watchdog" contains "$("$BOSUN" status 2>&1)" "Watchdog (com.bosun.smoke-watchdog.watchdog)"
unset BOSUN_WATCHDOG_NOW BOSUN_DAYS BOSUN_ALERT_SLACK_USER BOSUN_LAUNCHD_LABEL

section sessions-learn "sessions and learn (fake analysis Claude)" covers 'libexec/bosun-learn,libexec/bosun-ledger,libexec/bosun-sessions,libexec/bosun-watchdog'
cat >"$WORK/learn-claude" <<EOF
#!/bin/bash
printf '%s\n' "\$@" >"$WORK/learn-claude.args"
cat >/dev/null
echo '{"total_cost_usd": 0.05, "structured_output": {"summary": "quiet", "findings": [{"key": "smoke-rule", "category": "prompt-gap", "severity": "low", "title": "Smoke rule", "summary": "s", "evidence": [{"source": "session", "ref": "x", "quote": "Bosun activity digest"}], "proposal": {"target": "policy", "text": "Smoke rule text.", "rationale": "r"}}]}}'
EOF
chmod +x "$WORK/learn-claude"
LEARN() { BOSUN_CLAUDE_BIN="$WORK/learn-claude" "$BOSUN" learn "$@" 2>&1; }
check "sessions --json lists the configured windows, running or not" bash -c '"$1" sessions --json | jq -e "[.[] | select(.kind == \"session\") | .name] == [\"alpha\", \"beta\", \"gamma\"]"' _ "$BOSUN"
check "sessions prints a table" contains "$("$BOSUN" sessions)" "gamma"
out="$(BOSUN_CLAUDE_BIN=false "$BOSUN" learn --collect-only --out "$WORK/learn-digest" 2>&1)"
check "learn --collect-only writes a digest and calls no Claude" contains "$out" "digest of"
check "... and records no run" test ! -e "$BOSUN_STATE_DIR/learn/state.json"
check "... and says what a run would cost" contains "$out" "a run would cost about \$"
check "a run estimated over the budget never calls Claude" contains "$(BOSUN_LEARN_MAX_BUDGET_USD=0.01 BOSUN_CLAUDE_BIN=false "$BOSUN" learn --dry-run --since 2h --out "$WORK/learn-digest" 2>&1)" "not calling Claude"
BOSUN_LEARN_MAX_DIGEST_KB=1 BOSUN_CLAUDE_BIN=false "$BOSUN" learn --collect-only --out "$WORK/learn-capped" >/dev/null 2>&1
check "a digest over its cap says at the top what it left out" grep -q "^## Trimmed to fit the digest cap (1 KB" "$WORK"/learn-capped/*/digest.md
out="$(LEARN --since 2h)"
LEARNED="$(grep -Eo "learn:$(date -u +%F)/$MINTED" <<<"$out" | head -1)"
check "a run proposes its change in the ledger, under an id from the finding and the run" contains "$out" "new proposals: ${LEARNED:-none}"
check "... with the change on the item" contains "$("$BOSUN" ledger show "$LEARNED")" '"text": "Smoke rule text."'
check "... as a pending proposal" contains "$("$BOSUN" ledger list --proposals)" "$LEARNED"
learn_status="$(LEARN --status)"
check "status lists it as waiting for you" contains "$learn_status" "$LEARNED: Smoke rule"
check "... and so do learn status and learn list" bash -c '[ "$("$1" learn status 2>&1)" = "$2" ] && [ "$("$1" learn list 2>&1)" = "$2" ]' _ "$BOSUN" "$learn_status"
check "apply appends the rule to the site policy" bash -c '"$1" learn apply "$2" >/dev/null 2>&1 && grep -q "Smoke rule text." "$3/policy.md"' _ "$BOSUN" "$LEARNED" "$BOSUN_CONFIG_DIR"
check "... and closes the item" contains "$("$BOSUN" ledger show "$LEARNED")" '"state": "applied"'
check "a second run does not propose it again" contains "$(LEARN --since 2h)" "new proposals: none"
check "the watchdog checks the learning run once it is enabled" contains "$(BOSUN_LEARN_ENABLED=1 "$BOSUN" watchdog --status 2>&1)" "learn            "
# My plugins: one my settings enable, and one installed that no scope names. The analysis loads none of them.
mkdir -p "$HOME/.claude/plugins"
echo '{"enabledPlugins": {"probe@local": true}}' >"$HOME/.claude/settings.json"
echo '{"plugins": {"acme-helper@acme-market": [{"scope": "user"}]}}' >"$HOME/.claude/plugins/installed_plugins.json"
LEARN --dry-run --since 2h --out "$WORK/learn-plugins" >/dev/null
check "the analysis switches off each plugin of mine, in its one --settings beside its effort" bash -c '
  [ "$(grep -cx -- --settings "$1")" = 1 ] && grep -m1 -A1 -x -- --settings "$1" | tail -1 |
    jq -e ".enabledPlugins == {\"acme-helper@acme-market\": false, \"probe@local\": false} and (.effortLevel | type) == \"string\"" >/dev/null' \
  _ "$WORK/learn-claude.args"
echo '[not an object]' >"$HOME/.claude/plugins/installed_plugins.json"
check "an installed_plugins.json that does not parse fails the run, saying which file and why" fails_with \
  "learn: the analysis was not started: $HOME/.claude/plugins/installed_plugins.json does not parse, so bosun cannot tell which plugins to switch off" \
  env BOSUN_CLAUDE_BIN="$WORK/learn-claude" "$BOSUN" learn --dry-run --since 2h --out "$WORK/learn-plugins"
rm -rf "$HOME/.claude/settings.json" "$HOME/.claude/plugins" "$WORK/learn-plugins"
rm -f "$BOSUN_CONFIG_DIR/policy.md"

section folder-trust "folder trust" covers 'libexec/bosun-trust'
TRUST_REPO="$WORK/repos/trust-me"
git init -q "$TRUST_REPO" && git -C "$TRUST_REPO" remote add origin git@github.com:acme/api.git
TRUST_REPO="$(cd "$TRUST_REPO" && pwd -P)"
check "--check fails for an untrusted folder" bash -c '! "$1" trust --check "$2"' _ "$BOSUN" "$TRUST_REPO"
check "refuses a repo outside the mask" fails_with "outside the repo mask" env BOSUN_REPO_MASK="other/*" "$BOSUN" trust "$TRUST_REPO"
check "refuses a folder that is not a git repo" fails_with "not a git repository" "$BOSUN" trust "$WORK"
check "trusts a repo inside the mask" env BOSUN_REPO_MASK="acme/*" "$BOSUN" trust "$TRUST_REPO"
check "records it the way Claude does" test "$(jq -r --arg d "$TRUST_REPO" '.projects[$d].hasTrustDialogAccepted' "$HOME/.claude.json")" = true
check "--check passes once trusted" "$BOSUN" trust --check "$TRUST_REPO"
check "--revoke removes it" bash -c '"$1" trust --revoke "$2" && ! "$1" trust --check "$2"' _ "$BOSUN" "$TRUST_REPO"
check "--any trusts any folder" "$BOSUN" trust --any "$REPO_B"

section send-new-start "send --new and --start (fake Claude that registers itself)" needs folder-trust covers 'lib/plugins_claude.py,libexec/bosun-delegate,libexec/bosun-ledger,libexec/bosun-refresh,libexec/bosun-send,libexec/bosun-start,libexec/bosun-stop,libexec/bosun-trust,libexec/bosun-launch'
no_session
cat >"$WORK/live-claude" <<FAKE
#!/bin/bash
if [ "\$1" = "--version" ]; then echo "2.0.0 (Claude Code)"; exit 0; fi
printf '%s\n' "\${@:1:\$#-1}" >"$WORK/live-claude.\$\$.args"
printf '%s' "\${!#}" >"$WORK/live-claude.\$\$.prompt"
env | grep '^BOSUN_' | cut -d= -f1 >"$WORK/live-claude.\$\$.env"
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d}' \$\$ "\$(date +%s)000" >"\$HOME/.claude/sessions/\$\$.json"
exec sleep 600
FAKE
chmod +x "$WORK/live-claude"
BOSUN_CLAUDE_BIN="$WORK/live-claude" "$BOSUN" start >/dev/null 2>&1
wait_for 30 has_windows "orch alpha beta gamma checkin "
# (A start ends as soon as the agent registers, or its launch ends: the timeout is only hang protection.)
live() { BOSUN_LAUNCH_DRY_RUN=0 BOSUN_START_TIMEOUT=120 "$BOSUN" send "$@"; }
# claude_in <window>: prints the pid of the fake Claude running in the window: the first of the pane's descendants
# with a running-session record, which the fakes write for themselves ($HOME/.claude/sessions/<pid>.json). Read by
# the shell, with no Python started (agent_pid_under asks lib/agent_state.py), since wait_for loops call it.
claude_in() {
  local pane_pid
  pane_pid="$(tmux -L "$SOCKET" list-windows -t "=smoke" -F '#{window_name} #{pane_pid}' | awk -v w="$1" '$1 == w { print $2 }')"
  bash -c 'source "$1/lib/common.sh"
    for pid in $(descendants "$2"); do [ -f "$HOME/.claude/sessions/$pid.json" ] && echo "$pid" && exit 0; done
    exit 1' _ "$ROOT" "$pane_pid"
}
# Longer than the 1024-byte terminal line limit, multi-line, and full of shell metacharacters.
long_prompt="$(
  printf 'Ledger id: review:o/r#7\nDo not post. It'"'"'s "quoted" $HOME `date` \\ %%s\n'
  head -c 3000 /dev/zero | tr '\0' x
  printf '\nlast line'
)"
out="$(BOSUN_TRUST_MASKED_REPOS=0 live --new delta "$REPO_A" "$long_prompt" 2>&1)"
check "--new stops at once in an untrusted folder" contains "$out" "does not trust $REPO_A"
check "... without opening a window" bash -c '! tmux -L "$1" list-windows -t =smoke -F "#{window_name}" | grep -qx delta' _ "$SOCKET"
"$BOSUN" trust --any "$REPO_A" >/dev/null
out="$(live --new delta "$REPO_A" "$long_prompt" 2>&1)"
check "--new reports Claude running" contains "$out" "Claude is running in smoke:delta"
delta_pid="$(claude_in delta)"
check "--new starts Claude in the new window" test -n "$delta_pid"
check "--new delivers a long prompt intact" bash -c '[ "$(cat "$1")" = "$2" ]' _ "$WORK/live-claude.$delta_pid.prompt" "$long_prompt"
check "--new names the session after the window" grep -qx delta "$WORK/live-claude.$delta_pid.args"
check "Claude does not inherit the config" bash -c '! grep -qx BOSUN_DRAIN_PROMPT "$1"' _ "$WORK/live-claude.$delta_pid.env"
check "... but keeps real environment overrides" grep -qx BOSUN_CONFIG_DIR "$WORK/live-claude.$delta_pid.env"
L upsert 'slack:C8/1' --kind mention >/dev/null
out="$(live --new --model haiku --effort low zeta "$REPO_A" "start low" 2>&1)"
check "send --new --model/--effort start the window with them" bash -c 'grep -qx -- "--model" "$1" && grep -qx haiku "$1" && grep -q "\"effortLevel\":\"low\"" "$1"' _ "$WORK/live-claude.$(claude_in zeta).args"
check "... and its launch records them, with its directory (it is in no window list)" bash -c '
  jq -e --arg dir "$2" ".dir == \$dir and .model_override == \"haiku\" and .effort_override == \"low\"" "$1/launches/smoke/zeta.json"' _ "$BOSUN_STATE_DIR" "$REPO_A"
check "... so it fingerprints the same while nothing changed, from any directory" bash -c '
  [ "$(cd / && "$1" launch --fingerprint zeta | jq -cS .)" = "$(jq -cS .fingerprint "$2/launches/smoke/zeta.json")" ]' _ "$BOSUN" "$BOSUN_STATE_DIR"
zeta_pid="$(claude_in zeta)"
another_claude_in() { # <window> <old pid>: a fake Claude other than that one runs in the window
  local pid
  pid="$(claude_in "$1")" && [ -n "$pid" ] && [ "$pid" != "$2" ]
}
check "... and an update's restart of it" env BOSUN_LAUNCH_DRY_RUN=0 BOSUN_AGENT_SETTLE_SECONDS=0.5 "$BOSUN" refresh --update-restart "$WORK/update-prompt" zeta
check "... starts it again" wait_for 30 another_claude_in zeta "$zeta_pid"
check "... keeping its --model and --effort, with the update's prompt" bash -c '
  grep -qx haiku "$1.args" && grep -q "\"effortLevel\":\"low\"" "$1.args" && [ "$(cat "$1.prompt")" = bosun-was-updated:check-in ]' _ "$WORK/live-claude.$(claude_in zeta)"
# The default agent now Codex, and zeta's agent exited: one restart for the update shows both, the conversation
# resumed and the agent the window was launched on kept.
echo "BOSUN_HARNESS=codex" >"$BOSUN_CONFIG_DIR/bosun.env"
check "a send --new window launched on Claude, the default agent now Codex, fingerprints the same" bash -c '
  [ "$(cd / && "$1" launch --fingerprint zeta | jq -cS .)" = "$(jq -cS .fingerprint "$2/launches/smoke/zeta.json")" ]' _ "$BOSUN" "$BOSUN_STATE_DIR"
zeta_pid="$(claude_in zeta)"
kill "$zeta_pid"
wait_for 30 bash -c '! pgrep -P "$(tmux -L "$1" display -p -t =smoke:zeta "#{pane_pid}")" >/dev/null' _ "$SOCKET"
# A real Claude leaves its conversation to resume; this fake writes none, so zeta is given one.
make_session "$REPO_A" 44444444-0000-0000-0000-000000000001 zeta 202601040000
check "... and once its agent has exited, an update's restart" env BOSUN_LAUNCH_DRY_RUN=0 BOSUN_AGENT_SETTLE_SECONDS=0.5 "$BOSUN" refresh --update-restart "$WORK/update-prompt" zeta
check "... starts it again" wait_for 30 another_claude_in zeta "$zeta_pid"
check "... keeping them too, resuming its conversation with the update's prompt" bash -c '
  grep -qx haiku "$1.args" && grep -q "\"effortLevel\":\"low\"" "$1.args" && grep -qx 44444444-0000-0000-0000-000000000001 "$1.args" &&
    [ "$(cat "$1.prompt")" = bosun-was-updated:check-in ]' _ "$WORK/live-claude.$(claude_in zeta)"
check "... on Claude, as it was launched (its arguments are Claude's)" bash -c 'grep -qx -- --remote-control "$1.args"' _ "$WORK/live-claude.$(claude_in zeta)"
rm -f "$BOSUN_CONFIG_DIR/bosun.env"
# An update's restart whose launch fails (here: an effort no agent takes, which launch refuses) did not start:
# refresh says so with exit 5, the launch's own error line last on stderr, so the update stops waiting for it.
echo 'BOSUN_EFFORT="extreme"' >"$BOSUN_CONFIG_DIR/bosun.env"
env BOSUN_LAUNCH_DRY_RUN=0 "$BOSUN" refresh --update-restart "$WORK/update-prompt" alpha >/dev/null 2>"$WORK/not-started.err"
rc=$?
rm -f "$BOSUN_CONFIG_DIR/bosun.env"
check "an update's restart whose launch fails did not start: refresh exits 5, with the launch's own error last on stderr" bash -c '
  [ "$1" = 5 ] && tail -1 "$2" | grep -q "^effort must be one of: .*(got .extreme.)$"' _ "$rc" "$WORK/not-started.err"
check "... read from the launch's error file, which it removes" test ! -e "$BOSUN_STATE_DIR/launches/smoke/alpha.error"
# One refused by its resolver says the resolver's own reason, naming the file, not the launch's line that follows it.
mkdir -p "$HOME/.claude/plugins"
installed_plugins="$HOME/.claude/plugins/installed_plugins.json"
[ ! -e "$installed_plugins" ] || mv "$installed_plugins" "$WORK/installed_plugins.before-restart"
echo '[not an object]' >"$installed_plugins"
echo 'BOSUN_PLUGINS=""' >"$BOSUN_CONFIG_DIR/bosun.env"
env BOSUN_LAUNCH_DRY_RUN=0 "$BOSUN" refresh --update-restart "$WORK/update-prompt" alpha >/dev/null 2>"$WORK/not-started.err"
rc=$?
rm -f "$BOSUN_CONFIG_DIR/bosun.env" "$installed_plugins"
[ ! -e "$WORK/installed_plugins.before-restart" ] || mv "$WORK/installed_plugins.before-restart" "$installed_plugins"
check "... and one whose plugins cannot be resolved: exit 5, the resolver's reason last, naming the file" bash -c '
  [ "$1" = 5 ] && tail -1 "$2" | grep -qxF "plugins: $3 does not parse, so bosun cannot tell which plugins to switch off"' \
  _ "$rc" "$WORK/not-started.err" "$installed_plugins"
check "... from the launch's error file, which it removes" test ! -e "$BOSUN_STATE_DIR/launches/smoke/alpha.error"
check "delegate --window delivers through send" contains "$("$BOSUN" delegate investigate --id slack:C8/1 --ask 'look into it' --window delta 2>&1)" "delivered to smoke:delta"
check "the prompt file is removed once read" bash -c '[ -z "$(ls -A "$1")" ]' _ "$BOSUN_STATE_DIR/prompts"
check "--new refuses an existing window" fails_with "already exists" live --new delta "$REPO_A" again
check "--start refuses a window already running Claude" fails_with "already runs Claude" live --start delta again
out="$(live --start alpha "resume with this" 2>&1)"
check "--start starts Claude in an idle window" contains "$out" "Claude is running in smoke:alpha"
alpha_pid="$(claude_in alpha)"
check "--start resumes the window's named session" grep -qx 11111111-0000-0000-0000-000000000002 "$WORK/live-claude.$alpha_pid.args"
check "--start delivers the prompt" test "$(cat "$WORK/live-claude.$alpha_pid.prompt")" = "resume with this"
out="$(live --start beta "x" 2>&1)"
check "--start fails when Claude does not start" test $? -ne 0
check "a failed start shows the window's output" contains "$out" "already running as pid $$"
# The early end's own line (the timeout says "within ...s"), not how long it took.
check "... as soon as its launch has ended, saying so, not at BOSUN_START_TIMEOUT" bash -c '
  grep -q "but its launch ended without starting a Claude session" <<<"$1" && ! grep -q "no Claude session started there within" <<<"$1"' _ "$out"
out="$(live --new epsilon "$TRUST_REPO" hi 2>&1)"
check "--new trusts a repo inside the mask before starting" contains "$out" "trusted: $TRUST_REPO"
check "... and Claude starts there" contains "$out" "Claude is running in smoke:epsilon"
tmux -L "$SOCKET" send-keys -t "=smoke:gamma" "sleep 600" Enter
pane_has_children() { [ -n "$(pgrep -P "$(tmux -L "$SOCKET" display -p -t "=smoke:$1" '#{pane_pid}')")" ]; }
wait_for 30 pane_has_children gamma
check "--start never replaces a running command" fails_with "running another command" env BOSUN_RELAUNCH_SETTLE_SECONDS=1 "$BOSUN" send --start gamma x
tmux -L "$SOCKET" send-keys -t "=smoke:gamma" C-c
wait_for 30 bash -c '! pgrep -P "$(tmux -L "$1" display -p -t =smoke:gamma "#{pane_pid}")" >/dev/null' _ "$SOCKET"
# A shell busy until the test lets it go (a file it waits on), not for a fixed time: --start waits, and starts once
# it is free. The busy command is one process (a shell looping in a pane would have no child between its sleeps,
# which --start would take for free), and it writes a file of its own when it ends: a replace while it was busy
# would have stopped it first, so the file shows that --start replaced nothing until it was done. (The settle
# time, 120 s here, is only hang protection.)
touch "$WORK/gamma-busy"
rm -f "$WORK/gamma-settled"
tmux -L "$SOCKET" send-keys -t "=smoke:gamma" \
  "bash -c \"while [ -e '$WORK/gamma-busy' ]; do sleep 0.1; done; : >'$WORK/gamma-settled'\"" Enter
wait_for 30 pane_has_children gamma
BOSUN_RELAUNCH_SETTLE_SECONDS=120 live --start gamma 'after the shell settled' >"$WORK/gamma-start.out" 2>&1 &
gamma_start=$!
bg_started "$gamma_start"
check "--start waits for a briefly busy shell, saying so" \
  wait_for 30 grep -q "gamma: the pane runs a command; waiting up to 120s for it to end" "$WORK/gamma-start.out"
rm -f "$WORK/gamma-busy"
bg_done "$gamma_start"
check "... and starts once it settled" contains "$(cat "$WORK/gamma-start.out")" "Claude is running in smoke:gamma"
check "... having replaced nothing while it was busy: the busy command ran to its end" test -e "$WORK/gamma-settled"
kill "$alpha_pid"
wait_for 30 bash -c '! pgrep -P "$(tmux -L "$1" display -p -t =smoke:alpha "#{pane_pid}")" >/dev/null' _ "$SOCKET"
drain_prompt="$(bash -c 'source "$1/lib/common.sh"; load_config; printf %s "$BOSUN_DRAIN_PROMPT"' _ "$ROOT")"
tmux -L "$SOCKET" send-keys -t "=smoke:alpha" "cd /" Enter # the pane's shell moved elsewhere
wait_for 30 bash -c '[ "$(tmux -L "$1" display -p -t =smoke:alpha "#{pane_current_path}")" = / ]' _ "$SOCKET"
out="$(live --start alpha 2>&1)"
check "--start without a message resumes with the drain prompt" test "$(cat "$WORK/live-claude.$(claude_in alpha).prompt")" = "$drain_prompt"
check "... in the window's folder from the window list, not where its pane's shell was" \
  test "$(jq -r .dir "$BOSUN_STATE_DIR/launches/smoke/alpha.json")" = "$REPO_A"
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n extra -c "$REPO_B" "exec /bin/sh" # a window outside the list
printf 'sessions: [\n' >"$WORK/broken-sessions.yml"
start_extra() { env BOSUN_SESSIONS_FILE="$1" BOSUN_LAUNCH_DRY_RUN=0 BOSUN_START_TIMEOUT=30 "$BOSUN" send --start extra; }
check "--start of a window outside the list stops at a sessions.yml that cannot be read" \
  fails_with "could not read $WORK/broken-sessions.yml" start_extra "$WORK/broken-sessions.yml"
check "... and with no sessions.yml at all starts it in its pane's folder" \
  contains "$(start_extra "$WORK/no-sessions.yml" 2>&1)" "Claude is running in smoke:extra"
check "... (its launch ran there)" test "$(jq -r .dir "$BOSUN_STATE_DIR/launches/smoke/extra.json")" = "$REPO_B"
tmux -L "$SOCKET" kill-window -t "=smoke:extra"
cat >"$WORK/echo-claude" <<FAKE
#!/bin/bash
printf '{"pid":%d,"status":"idle"}' \$\$ >"\$HOME/.claude/sessions/\$\$.json"
exec cat >"$WORK/received.txt"
FAKE
chmod +x "$WORK/echo-claude"
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n echo "$WORK/echo-claude; sleep 1"
wait_for 30 claude_in echo
cat >"$WORK/bg-claude" <<FAKE
#!/bin/bash
printf '{"pid":%d,"status":"shell"}' \$\$ >"\$HOME/.claude/sessions/\$\$.json"
exec sleep 600
FAKE
chmod +x "$WORK/bg-claude"
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n bgcmd "$WORK/bg-claude; sleep 1"
wait_for 30 claude_in bgcmd
check "send --list shows a Claude with a background command as idle-bg, not as a shell" bash -c 'grep -E "^bgcmd " <<<"$1" | grep -q "claude:idle-bg"' _ "$("$BOSUN" send --list)"
tmux -L "$SOCKET" kill-window -t "=smoke:bgcmd"
# A fake Claude screen: an input box (rule, prompt line, rule) holding $1; with $2=reject, it
# answers whatever arrives the way Claude answers an unknown slash command.
cat >"$WORK/box-claude" <<FAKE
#!/bin/bash
printf '{"pid":%d,"status":"idle"}' \$\$ >"\$HOME/.claude/sessions/\$\$.json"
rule="\$(printf '%.0s─' {1..40})"
printf '%s\n❯ %s\n%s\n' "\$rule" "\$1" "\$rule"
read -r line
[ "\${2:-}" = reject ] && echo "Unknown command: \$line"
exec sleep 600
FAKE
chmod +x "$WORK/box-claude"
box_window() { # <name> <box text> [reject]
  tmux -L "$SOCKET" new-window -d -t "=smoke:" -n "$1" "$WORK/box-claude '$2' ${3:-}; sleep 1"
  wait_for 30 claude_in "$1"
}
box_window halftyped "half typed"
# (No wait here: send_waiting below shows the wait, and what ends it.)
check "send refuses unsent text in the input line once its wait is over" fails_with "input line holds unsent text (half typed)" \
  env BOSUN_SEND_WAIT_SECONDS=0 "$BOSUN" send --no-item halftyped hello
box_window emptybox ""
check "send pastes into an empty input line" contains "$("$BOSUN" send --no-item emptybox hello 2>&1)" "delivered to smoke:emptybox"
box_window clearing "" reject
# (send stops looking once the reply is there: the confirm time is only hang protection.)
check "send fails when the paste ran as an unknown command" fails_with "ran the paste as an unknown command" \
  env BOSUN_SEND_CONFIRM_SECONDS=60 "$BOSUN" send --no-item clearing "/clearLedger item x"
# A fake Claude at an input box holding unsent text, so send waits; once $WORK/flip.$1 exists, with $1=waiting
# its status turns to waiting (a permission prompt opened meanwhile), with $1=clear its box empties and it takes
# the next line, with $1=exit its box empties and it exits, its record left behind still saying idle (as a
# Claude that crashed leaves it).
cat >"$WORK/flip-claude" <<FAKE
#!/bin/bash
record() {
  printf '{"pid":%d,"status":"%s"}' \$\$ "\$1" >"\$HOME/.claude/sessions/\$\$.tmp"
  mv "\$HOME/.claude/sessions/\$\$.tmp" "\$HOME/.claude/sessions/\$\$.json"
}
box() { rule="\$(printf '%.0s─' {1..40})"; printf '%s\n❯ %s\n%s\n' "\$rule" "\$1" "\$rule"; }
record idle
box "half typed"
until [ -e "$WORK/flip.\$1" ]; do sleep 0.2; done
if [ "\$1" = waiting ]; then record waiting; exec sleep 600; fi
clear
box ""
[ "\$1" = exit ] && exit 0
read -r line
printf '%s\n' "\$line" >"$WORK/flip.received"
exec sleep 600
FAKE
chmod +x "$WORK/flip-claude"
send_waiting() { # <window> <out file>: a send that waits, in the background, once it says it waits
  env BOSUN_SEND_WAIT_SECONDS=30 BOSUN_SEND_RETRY_SECONDS=0.2 "$BOSUN" send --no-item "$1" hello >"$2" 2>&1 &
  waiting_send=$!
  wait_for 30 grep -q "waiting to paste into smoke:$1" "$2"
}
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n flipping "$WORK/flip-claude waiting; sleep 1"
wait_for 30 claude_in flipping
send_waiting flipping "$WORK/flipping.out"
touch "$WORK/flip.waiting"
wait "$waiting_send"
waiting_rc=$?
check "send refuses a window that began waiting at a prompt while send waited" bash -c '
  [ "$1" -ne 0 ] && grep -q "is waiting (a permission prompt or question waits for you there); not pasting" "$2"' _ "$waiting_rc" "$WORK/flipping.out"
check "... and pasted nothing there" bash -c '! tmux -L "$1" capture-pane -p -t =smoke:flipping | grep -q hello' _ "$SOCKET"
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n paned "$WORK/flip-claude clear; sleep 1"
wait_for 30 claude_in paned
send_waiting paned "$WORK/paned.out"
: >"$WORK/paned.shell"
tmux -L "$SOCKET" split-window -t "=smoke:paned" "cat >'$WORK/paned.shell'" # the new pane is the active one
touch "$WORK/flip.clear"
wait "$waiting_send"
waiting_rc=$?
check "send that waited delivers into the pane whose agent it checked" test "$waiting_rc" -eq 0
check "... (its agent got the message)" wait_for 30 grep -qx hello "$WORK/flip.received"
check "... never into the pane you moved to meanwhile" test ! -s "$WORK/paned.shell"
: >"$WORK/exiting.shell"
tmux -L "$SOCKET" new-window -d -t "=smoke:" -n exiting "$WORK/flip-claude exit; exec cat >'$WORK/exiting.shell'"
wait_for 30 claude_in exiting
send_waiting exiting "$WORK/exiting.out"
touch "$WORK/flip.exit" # its agent exits and leaves a shell (here a cat) in the pane
wait "$waiting_send"
waiting_rc=$?
check "send refuses a window whose agent exited while send waited, though its record still says idle" bash -c '
  [ "$1" -ne 0 ] && grep -q "no longer runs the agent it had" "$2"' _ "$waiting_rc" "$WORK/exiting.out"
check "... and the shell left in its pane ran nothing" test ! -s "$WORK/exiting.shell"
for w in halftyped emptybox clearing flipping paned exiting; do tmux -L "$SOCKET" kill-window -t "=smoke:$w"; done
message="$(printf 'first line\nsecond line')"
"$BOSUN" send echo "$message" >/dev/null 2>&1
check "send delivers the whole multi-line message" wait_for 30 bash -c '[ "$(cat "$1")" = "$2" ]' _ "$WORK/received.txt" "$message"
check "send leaves no paste buffer behind" bash -c '! tmux -L "$1" list-buffers -F "#{buffer_name}" 2>/dev/null | grep -q bosun-send' _ "$SOCKET"
"$BOSUN" send --no-item echo - >/dev/null 2>&1 <<'EOF'
from stdin, not a dash
EOF
check "send - delivers stdin, never a literal '-'" wait_for 30 bash -c 'grep -qx "from stdin, not a dash" "$1" && ! grep -qx -- "-" "$1"' _ "$WORK/received.txt"
check "send - refuses an empty stdin" fails_with "message on stdin is empty" bash -c '"$1" send echo - </dev/null' _ "$BOSUN"
check "send --id refuses an unknown item" fails_with "no ledger item slack:C8/404" "$BOSUN" send --id slack:C8/404 echo hi
L upsert 'slack:C8/2' --kind mention >/dev/null
"$BOSUN" send --id slack:C8/2 echo "is it done" >/dev/null 2>&1
check "send --id names the item in the message" wait_for 30 grep -q "Ledger item slack:C8/2: is it done" "$WORK/received.txt"
check "... and notes the delivery on the item" contains "$(L show slack:C8/2)" "message sent to echo"
"$BOSUN" delegate investigate --id slack:C8/2 --ask 'look into it' --window echo >/dev/null 2>&1
check "delegate records the window on the item" contains "$(L delegation slack:C8/2)" '"window": "echo"'
"$BOSUN" delegate --update slack:C8/2 --details - >/dev/null 2>&1 <<'EOF'
Also check the second case.
EOF
check "delegate --update reaches the item's window, framed with its id" wait_for 30 grep -q "Update to ledger item slack:C8/2" "$WORK/received.txt"
check "... and is kept in the item's history" contains "$(L show slack:C8/2)" "Also check the second case."
L upsert 'slack:C8/3' --kind mention >/dev/null
L record-delegation 'slack:C8/3' --window gone --task investigate >/dev/null
check "delegate --update fails when the item's window is gone" fails_with "no window 'gone'" "$BOSUN" delegate --update slack:C8/3 --details 'more'
# delegate's rules (what it records of an update not delivered, a second hand-over, --again, the refusals of a
# move, a move away from a window that is gone) are tests/unit/test_delegate.py's and test_ledger_protocol.py's,
# with send's answers given; here, real sends: a hand-over, an update, a move between two windows.
L upsert 'slack:C8/5' --kind mention >/dev/null
"$BOSUN" delegate investigate --id slack:C8/5 --ask 'look' --link https://x/5 --window echo >/dev/null 2>&1
check "--update --window moves the item and tells the old window" contains "$("$BOSUN" delegate --update slack:C8/5 --window delta --details 'echo is stuck' 2>&1)" "moved from echo to delta (the old window was told to stop)"
check "... the old window gets the stop notice by id" wait_for 30 grep -q "Ledger item slack:C8/5 has moved to window delta. Stop working on it here" "$WORK/received.txt"
check "... and the ledger has the new window and the move" bash -c '
  "$1" ledger delegation slack:C8/5 | grep -q "\"window\": \"delta\".*\"moved_from\": \"echo\"\|\"moved_from\": \"echo\".*\"window\": \"delta\"" &&
  "$1" ledger show slack:C8/5 | grep -q "\"event\": \"moved\""' _ "$BOSUN"
check "--start dry-run only logs" contains "$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" send --start gamma hi 2>&1)" "dry-run: would start Claude in smoke:gamma"
"$BOSUN" stop >/dev/null 2>&1

section checkin-loop-timing "check-in loop timing" covers 'libexec/bosun-checkin,libexec/bosun-chores'
# loop_ticks <ticks> <out> [VAR=value...]: runs `bosun checkin --loop` with those settings until its dispatcher has
# ticked <ticks> times, then stops it, and prints `<ticks seen> <1 if it was still running>`. A tick is a new
# last_tick in the events state; the one there before is set to a time long past first, so the first tick shows
# whatever the clock (last_tick has whole seconds, and ticks a second apart differ). The 120 looks are only hang
# protection.
loop_ticks() {
  local want="$1" out="$2" state="$BOSUN_STATE_DIR/events/state.json" last ticks=0 alive=0 pid now
  shift 2
  mkdir -p "$BOSUN_STATE_DIR/events"
  python3 -I -c 'import json, sys
from pathlib import Path
path = Path(sys.argv[1])
state = json.loads(path.read_text()) if path.exists() else {}
path.write_text(json.dumps({**state, "last_tick": "2000-01-01T00:00:00+00:00"}))' "$state"
  last="$(grep -o '"last_tick": *"[^"]*"' "$state")"
  env "$@" "$BOSUN" checkin --loop >"$out" 2>&1 &
  pid=$!
  for _ in $(seq 1 120); do
    now="$(grep -o '"last_tick": *"[^"]*"' "$state" 2>/dev/null)"
    if [ -n "$now" ] && [ "$now" != "$last" ]; then
      ticks=$((ticks + 1))
      last="$now"
    fi
    [ "$ticks" -ge "$want" ] && break
    sleep 0.25
  done
  kill -0 "$pid" 2>/dev/null && alive=1
  kill "$pid" 2>/dev/null
  pkill -P "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  echo "$ticks $alive"
}
# Its dispatcher ticks every second (as it starts, then each interval), the check-in every minute: two ticks seen and
# no check-in line yet is a loop that waits before its first check-in.
check "the loop waits one interval before the first check-in: two of its ticks, and no check-in" bash -c '
  [ "$1" -ge 2 ] && ! grep -q "\[checkin\]" "$2" || { echo "ticks: $1"; cat "$2"; exit 1; }' \
  _ "$(loop_ticks 2 "$WORK/checkin-loop.out" BOSUN_CHECKIN_MINUTES=1 BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_INTERVAL_SECONDS=1 | cut -d" " -f1)" "$WORK/checkin-loop.out"
# Its interval is 10 minutes here: a tick at all, within the looks, is the one as it starts.
check "with events on, the dispatcher runs as the loop starts, not an interval later" test \
  "$(loop_ticks 1 "$WORK/events-start.out" BOSUN_CHECKIN_MINUTES=60 BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_INTERVAL_SECONDS=600 | cut -d" " -f1)" -ge 1
check "one check-in refuses while check-ins are off" fails_with "check-ins are disabled" env BOSUN_CHECKIN_MINUTES=0 "$BOSUN" checkin
check "the loop refuses with check-ins off unless events run without them" fails_with "BOSUN_EVENTS_WITHOUT_CHECKINS=1 keeps the loop" env BOSUN_CHECKIN_MINUTES=0 BOSUN_EVENTS_ENABLED=1 "$BOSUN" checkin --loop
# It keeps running: the dispatcher ticks as it starts and again an interval later.
check "with events alone the loop keeps running and sends no check-in" bash -c '
  [ "$1" = "2 1" ] && ! grep -q "chores check-in" "$2" || { echo "ticks, running: $1"; cat "$2"; exit 1; }' \
  _ "$(loop_ticks 2 "$WORK/events-loop.out" BOSUN_CHECKIN_MINUTES=0 BOSUN_EVENTS_ENABLED=1 BOSUN_EVENTS_WITHOUT_CHECKINS=1 BOSUN_EVENTS_INTERVAL_SECONDS=1)" "$WORK/events-loop.out"

section delegate "delegate (task types)" covers 'libexec/bosun-delegate,libexec/bosun-ledger,libexec/bosun-kg'
L upsert 'review:acme/api#5' --kind review-request --title 'Add X' >/dev/null
L upsert 'slack:C7/1' --kind mention >/dev/null
check "--list shows every task type" bash -c 'for t in review fix-my-pr new-work investigate; do grep -q "^$t: " <<<"$1" || exit 1; done' _ "$("$BOSUN" delegate --list)"
task="$("$BOSUN" delegate review --id 'review:acme/api#5' --link https://github.com/acme/api/pull/5 --requester Priya --ask "review the PR" --details "my main point: add a backfill" --print)"
check "a review for a running window runs in a subagent at medium effort" contains "$(head -1 <<<"$task")" 'one Agent tool call with effort "medium"'
check "the task has the facts" contains "$task" "Ask: review the PR"
check "the task carries my details" contains "$task" "Details: my main point: add a backfill"
check "the task has its type's rules" contains "$task" "submit it as APPROVE"
check "the task ends with the shared rules" contains "$(tail -4 <<<"$task")" "AWS:"
task="$("$BOSUN" delegate investigate,new-work --id 'slack:C7/1' --ask "triage and fix" --print)"
check "types combine in order" bash -c 'grep -n "^Investigate" <<<"$1" | cut -d: -f1 | { read a; [ "$a" -lt "$(grep -n "^New work" <<<"$1" | cut -d: -f1)" ]; }' _ "$task"
# (A type takes ids of its kinds, in the ledger: test_delegate.py.)
check "unknown types are rejected" fails_with "unknown task type" "$BOSUN" delegate nope --id slack:C7/1 --ask x --print
check "the ask must be one line" bash -c '! "$1" delegate investigate --id slack:C7/1 --ask "$(printf "a\nb")" --print 2>/dev/null' _ "$BOSUN"
check "a target is required" fails_with "say where" "$BOSUN" delegate investigate --id slack:C7/1 --ask x
check "--new goes through send" contains "$(BOSUN_SEND_DRY_RUN=1 "$BOSUN" delegate investigate --id slack:C7/1 --ask x --new probe "$REPO_A" 2>&1)" "dry-run: would open window smoke:probe"
# That every ledger step in a task file is a valid transition, and that docs/orchestrator.md's task table is the task
# files' rows: tests/unit/test_delegate.py and test_task_table.py.
# What the ontology says about the work: its own knowledge base (the fixture's six facts that confirm
# themselves are enough), read by delegate only while BOSUN_KNOWLEDGE_ENABLED is 1.
dkg() { BOSUN_CONFIG_DIR="$WORK/kg-delegate" "$BOSUN" "$@"; }
dkg kg import "$KG_FIXTURE" --apply --auto-confirm >/dev/null 2>&1
L upsert 'review:acme/invoice-exporter#5' --kind review-request --title 'Round tax per line' >/dev/null
pack_opening="What bosun knows about this work (its ontology; cite a fact's [id] when you rely on it, and tell me if one is wrong):"
brief="$(BOSUN_KNOWLEDGE_ENABLED=1 dkg delegate review --id 'review:acme/invoice-exporter#5' --ask "review it" --print 2>&1)"
check "with the key on, a brief carries the pack for its repo, named as the ledger names it (owner/repo)" contains "$brief" "$pack_opening"
check "... and logs the pack's size in the delegate log" bash -c 'grep -Eq "\[delegate\] pack for .*: [0-9]+ facts, ~[0-9]+ tokens" "$1"/delegate-*.log' _ "$BOSUN_LOG_DIR"
check "... with its confirmed facts and their ids" bash -c 'grep -Eq "^- invoice exporter depends on pdf renderer \[f-[0-9a-z]{6}; " <<<"$1"' _ "$brief"
check "... after the task's rules and before the shared ones" bash -c '
  [ "$(grep -n "submit it as APPROVE" <<<"$1" | head -1 | cut -d: -f1)" -lt "$(grep -nF "$2" <<<"$1" | cut -d: -f1)" ] &&
    [ "$(grep -nF "$2" <<<"$1" | cut -d: -f1)" -lt "$(grep -n "^In every task:" <<<"$1" | cut -d: -f1)" ]' _ "$brief" "$pack_opening"
check "... and no unconfirmed fact" bash -c '! grep -q "tax rounding needs a test" <<<"$1"' _ "$brief"
brief="$(BOSUN_KNOWLEDGE_ENABLED=0 dkg delegate review --id 'review:acme/invoice-exporter#5' --ask "review it" --print 2>&1)"
check "with the key off, it does not" bash -c '! grep -qF "What bosun knows about this work" <<<"$1"' _ "$brief"
# (With the key on and nothing known about the repo, no section: test_delegate.py.)

section chores-dry-run "chores dry run (fake claude)" covers 'libexec/bosun-chores,libexec/bosun-ledger'
cat >"$WORK/fake-claude" <<FAKE
#!/bin/bash
if [ "\$1" = "--version" ]; then echo "2.0.0 (Claude Code)"; exit 0; fi
printf '%s\n' "\$@" >"$WORK/fake-claude.args"
env | grep -E '^(BOSUN_|PATH=|ENABLE_CLAUDEAI_MCP_SERVERS=)' >"$WORK/fake-claude.env"
"$BOSUN" ledger upsert 'review:o/r#9' --kind review-request --title 'From dry run' >/dev/null
echo '{"result":"fake plan","total_cost_usd":0.01,"num_turns":1,"permission_denials":[{"tool_name":"Bash","tool_input":{"command":"gh pr merge x"}}]}'
FAKE
# The three rehearsals below start in one second, by a stand-in date that gives a folder's stamp as 2026-01-05
# 09:00:00 (and anything else as date does), so they number their folders -01, -02 and -03.
printf '#!/bin/sh\n[ "$1" = "+%%Y%%m%%d-%%H%%M%%S" ] && { echo 20260105-090000; exit 0; }\nexec /bin/date "$@"\n' >"$HOME/.local/bin/date"
chmod +x "$HOME/.local/bin/date"
out="$("$BOSUN" chores morning --dry-run 2>&1)"
check "dry run prints the plan" contains "$out" "fake plan"
check "dry run reports scratch ledger changes" contains "$out" "+ review:o/r#9"
check "dry run reports blocked tool calls" contains "$out" "- Bash: gh pr merge x"
check "dry run uses a scratch state dir" grep -q "^BOSUN_STATE_DIR=$BOSUN_STATE_DIR/dry-runs/" "$WORK/fake-claude.env"
scratch_of_run() { sed -n 's/^BOSUN_STATE_DIR=//p' "$WORK/fake-claude.env"; } # the last rehearsal's scratch state
first_rehearsal="$(scratch_of_run)"
check "dry run turns on send dry-run" grep -qx "BOSUN_SEND_DRY_RUN=1" "$WORK/fake-claude.env"
check "dry run puts this checkout's bosun first on PATH" grep -q "^PATH=$ROOT/bin:" "$WORK/fake-claude.env"
check "dry run loads orchestrator settings and plugin" bash -c 'grep -qx -- --settings "$1" && grep -qx -- --plugin-dir "$1"' _ "$WORK/fake-claude.args"
check "dry run uses the orchestrator's model and effort" bash -c 'grep -qx opus "$1" && grep -q "\"effortLevel\":\"medium\"" "$1" && grep -q PreToolUse "$1"' _ "$WORK/fake-claude.args"
check "dry run invokes the morning chores" grep -qx "/bosun:chores morning" "$WORK/fake-claude.args"
check "dry run runs this checkout's guard, as the orchestrator's launch does" runs_this_guard "$(grep -A1 -x -- --settings "$WORK/fake-claude.args" | tail -1)"
check "dry run denies the settings' ask list by name" bash -c 'grep -qx -- --disallowedTools "$1" && grep -qx "mcp__plugin_slack_slack__slack_send_message" "$1" && grep -qx "Bash(git push:\*)" "$1"' _ "$WORK/fake-claude.args"
check "dry run leaves the real ledger alone" bash -c '! "$1" ledger show "review:o/r#9" >/dev/null 2>&1' _ "$BOSUN"
check "dry run limits the connectors as the orchestrator's launch does" bash -c '
  [ "$(grep -A1 -x -- --settings "$1" | tail -1 | jq -c .allowedMcpServers)" = "[]" ] && ! grep -q ENABLE_CLAUDEAI "$2"' _ "$WORK/fake-claude.args" "$WORK/fake-claude.env"
check "... and holds it to its plugins as its launch does (none of mine)" bash -c '
  [ "$(grep -A1 -x -- --settings "$1" | tail -1 | jq -c .enabledPlugins)" = "{}" ]' _ "$WORK/fake-claude.args"
env -u ENABLE_CLAUDEAI_MCP_SERVERS BOSUN_ORCHESTRATOR_CONNECTORS=acme-local "$BOSUN" chores morning --dry-run >/dev/null 2>&1
second_rehearsal="$(scratch_of_run)"
check "... with the orchestrator's list, and claude.ai's connectors on when it names any" bash -c '
  settings="$(grep -A1 -x -- --settings "$1" | tail -1)"
  [ "$(jq -c .allowedMcpServers <<<"$settings")" = "[{\"serverName\":\"acme-local\"}]" ] && grep -q PreToolUse <<<"$settings" &&
    grep -qx ENABLE_CLAUDEAI_MCP_SERVERS=true "$2"' _ "$WORK/fake-claude.args" "$WORK/fake-claude.env"
out="$("$BOSUN" chores morning --dry-run --continue 2>&1)"
rm -f "$HOME/.local/bin/date"
# Each has a folder of its own, numbered within its second, and --continue seeds from the one just before.
check "each rehearsal has a folder of its own, numbered within the second they all start in" bash -c '
  [ "$1 $2 $3" = "$4-01 $4-02 $4-03" ]' _ "$first_rehearsal" "$second_rehearsal" "$(scratch_of_run)" "$BOSUN_STATE_DIR/dry-runs/20260105-090000"
check "--continue starts from the previous rehearsal" contains "$out" "seeded from $second_rehearsal/ledger.json"
check "--continue sees earlier items as existing" contains "$out" "(0 change(s)"

section chores-options "chores options" covers 'libexec/bosun-chores'
check "--fresh and --continue conflict" fails_with "either --fresh or --continue" "$BOSUN" chores morning --dry-run --fresh --continue
check "--fresh needs --dry-run" fails_with "only apply to --dry-run" "$BOSUN" chores morning --fresh

section pr-threads "pr-threads (fake gh)" covers 'libexec/bosun-pr-threads'
cat >"$WORK/threads.json" <<'JSON'
{"data": {"repository": {"pullRequest": {"url": "https://github.com/acme/api/pull/5", "author": {"login": "me"},
  "reviews": {"nodes": [{"id": "R1", "url": "https://x/r1", "author": {"login": "rev"}, "state": "CHANGES_REQUESTED", "submittedAt": "2026-01-01T00:00:00Z", "body": ""}]},
  "reviewThreads": {"pageInfo": {"hasNextPage": false, "endCursor": null}, "nodes": [
  {"isResolved": false, "isOutdated": false, "path": "a.py", "line": 3,
   "first": {"totalCount": 1, "nodes": [{"author": {"login": "rev"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/1", "body": "please   fix\nthis", "pullRequestReview": {"id": "R1"}}]},
   "last": {"nodes": [{"author": {"login": "rev"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/1"}]}},
  {"isResolved": false, "isOutdated": false, "path": "c.py", "line": 7,
   "first": {"totalCount": 2, "nodes": [{"author": {"login": "rev"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/3", "body": "rename", "pullRequestReview": {"id": "R1"}}]},
   "last": {"nodes": [{"author": {"login": "me"}, "createdAt": "2026-01-02T00:00:00Z", "url": "https://x/4"}]}},
  {"isResolved": true, "isOutdated": false, "path": "b.py", "line": 9,
   "first": {"totalCount": 1, "nodes": [{"author": {"login": "old"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/2", "body": "done"}]},
   "last": {"nodes": [{"author": {"login": "old"}, "createdAt": "2026-01-01T00:00:00Z", "url": "https://x/2"}]}}
]}}}}}
JSON
cat >"$HOME/.local/bin/gh" <<FAKE
#!/bin/bash
cat "$WORK/threads.json"
FAKE
chmod +x "$HOME/.local/bin/gh"
out="$("$BOSUN" pr-threads https://github.com/acme/api/pull/5 2>&1)"
check "pr-threads counts unresolved threads" contains "$out" "unresolved threads: 2 (awaiting the author, me: 1)"
check "pr-threads shows who, where and when" contains "$out" "- rev on a.py:3 2026-01-01T00:00:00Z https://x/1"
check "pr-threads folds the comment onto one line" contains "$out" "please fix this"
check "pr-threads shows replies and the last commenter" contains "$out" "round 1; 1 reply, last: me 2026-01-02T00:00:00Z (answered by the author)"
check "pr-threads counts threads per review round" contains "$out" "1. CHANGES_REQUESTED by rev 2026-01-01T00:00:00Z: 2 threads, 1 awaiting the author"
check "pr-threads skips resolved threads" bash -c '! grep -q "old on b.py" <<<"$1"' _ "$out"
out="$("$BOSUN" pr-threads https://github.com/acme/api/pull/5 --awaiting-author 2>&1)"
check "pr-threads --awaiting-author exits 3 while threads await the author" test "$?" = 3
check "pr-threads --awaiting-author lists only those" bash -c 'grep -q "a.py:3" <<<"$1" && ! grep -q "c.py:7" <<<"$1"' _ "$out"
check "pr-threads --json parses" bash -c '"$1" pr-threads https://github.com/acme/api/pull/5 --json | jq -e ".awaiting_author == 1" >/dev/null' _ "$BOSUN"
check "pr-threads rejects other URLs" fails_with "not a GitHub PR URL" "$BOSUN" pr-threads https://example.com/x

section review-watch "review-watch (fake gh)" needs pr-threads covers 'libexec/bosun-delegate,libexec/bosun-ledger,libexec/bosun-review-watch'
RW_ID='mypr:acme/api#5'
"$BOSUN" ledger upsert "$RW_ID" --kind my-pr --title "api fix" --url https://github.com/acme/api/pull/5 >/dev/null
"$BOSUN" ledger transition "$RW_ID" changes-requested --note "round 1" >/dev/null
"$BOSUN" ledger transition "$RW_ID" addressing --note "fixing" >/dev/null
"$BOSUN" ledger record-delegation "$RW_ID" --window api --task fix-my-pr >/dev/null
out="$("$BOSUN" review-watch --id "$RW_ID" 2>&1)"
check "review-watch ignores a review from before the hand-over" contains "$out" "no new reviews on 1 PR(s)"
# a second round after the hand-over
jq '.data.repository.pullRequest.reviews.nodes[0].submittedAt = "2999-01-01T00:00:00Z"' "$WORK/threads.json" >"$WORK/threads2.json" &&
  mv "$WORK/threads2.json" "$WORK/threads.json"
out="$("$BOSUN" review-watch --id "$RW_ID" --dry-run 2>&1)"
check "review-watch --dry-run shows the new review" contains "$out" "$RW_ID [addressing] (held by api): new review CHANGES_REQUESTED by rev"
check "... and records nothing" bash -c '! "$1" ledger show "$2" | grep -q review-arrived' _ "$BOSUN" "$RW_ID"
out="$("$BOSUN" review-watch --id "$RW_ID" 2>&1)"
check "review-watch names the next step" contains "$out" "bosun delegate --update '$RW_ID'"
check "review-watch records it on the item, with no state change" bash -c '"$1" ledger show "$2" | jq -e "(.history | map(select(.event == \"review-arrived\")) | length) == 1 and .state == \"addressing\"" >/dev/null' _ "$BOSUN" "$RW_ID"
"$BOSUN" review-watch --id "$RW_ID" >/dev/null 2>&1
check "review-watch records a review once" bash -c '"$1" ledger show "$2" | jq -e "(.history | map(select(.event == \"review-arrived\")) | length) == 1" >/dev/null' _ "$BOSUN" "$RW_ID"
check "review-watch --quiet prints nothing once it is recorded" test -z "$("$BOSUN" review-watch --id "$RW_ID" --quiet 2>&1)"
rm "$HOME/.local/bin/gh"

section impact "impact (fake gh)" covers 'libexec/bosun-impact'
cat >"$HOME/.local/bin/gh" <<'FAKE'
#!/bin/sh
case "$1 $2" in
  "api user") echo octo-me ;;
  "pr list") echo '[{"number":5,"title":"api fix","url":"https://github.com/acme/api/pull/5","createdAt":"2026-01-05T09:00:00Z","mergedAt":"2026-01-06T09:00:00Z","additions":3,"deletions":1}]' ;;
  "search prs") echo '[]' ;;
  *) echo "unexpected gh $*" >&2; exit 1 ;;
esac
FAKE
chmod +x "$HOME/.local/bin/gh"
check "impact --help prints its usage" contains "$("$BOSUN" impact --help 2>&1)" "Usage: bosun impact collect"
out="$(BOSUN_IMPACT_REPOS=acme/api "$BOSUN" impact collect --since 2025-12-01 --dry-run 2>&1)"
check "impact collect --dry-run counts what it found" contains "$out" "dry-run: nothing written; impact: 1 merged PRs, 0 reviewed PRs in 1 repos since 2025-12-01"
check "... and writes nothing" test ! -e "$BOSUN_STATE_DIR/impact/snapshot.json"
out="$(BOSUN_IMPACT_REPOS=acme/api BOSUN_IMPACT_OVERHEAD_FIXED_AT=2025-12-15 "$BOSUN" impact collect --since 2025-12-01 2>&1)"
check "impact collect writes the snapshot" bash -c 'jq -e ".prs[0].url == \"https://github.com/acme/api/pull/5\" and (.repos | index(\"acme/api\"))" "$1" >/dev/null' _ "$BOSUN_STATE_DIR/impact/snapshot.json"
check "... with the guardrails and the overhead fix date" bash -c 'jq -e "(.guardrails | type == \"array\") and .settings.overhead_fixed_at == \"2025-12-15\"" "$1" >/dev/null' _ "$BOSUN_STATE_DIR/impact/snapshot.json"
check "impact collect refuses a bad --since" fails_with "--since takes a date" "$BOSUN" impact collect --since yesterday
rm "$HOME/.local/bin/gh"

section setup-steps "setup steps (fake aws)" covers 'libexec/bosun-setup,libexec/bosun-alert,libexec/bosun-send,lib/setup-helpers.sh,examples/generic/setup.sh'
SETUP_CFG="$WORK/setup-config"
mkdir -p "$SETUP_CFG"
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$SETUP_CFG/"
cat >"$HOME/.local/bin/aws" <<FAKE
#!/bin/bash
echo "\$*" >>"$WORK/aws.log"
case "\$*" in
  "sts get-caller-identity --profile good") exit 0 ;;
  "sts get-caller-identity --profile stale") [ -f "$WORK/aws.logged-in" ] ;;
  "sso login --profile stale") touch "$WORK/aws.logged-in" ;;
  "configure get sso_session --profile cached") echo acme ;;
  "sts get-caller-identity --profile cached") [ -f "\$HOME/.aws/cli/cache/role.json" ] || [ -f "$WORK/aws.cached-login" ] ;;
  "sso login --sso-session acme") touch "$WORK/aws.cached-login" ;;
  "configure get sso_session --profile books-"*) echo corp ;;
  "sts get-caller-identity --profile books-"*) [ -f "$WORK/aws.corp-login" ] ;;
  "sso login --sso-session corp")
    echo "open https://oidc.example.com/authorize?state=secret-state&code_challenge=secret"
    # FAKE_SSO_WAIT=alert: a login no one approves yet, until setup has said it alerts you: a line of its own log past
    # the FAKE_SSO_ALERTS_BEFORE it held before (no clock; the 30 s are only hang protection)
    if [ "\${FAKE_SSO_WAIT:-}" = alert ]; then
      for _ in \$(seq 1 300); do
        [ "\$(cat "\$BOSUN_LOG_DIR"/setup-*.log 2>/dev/null | grep -c "alerting you")" -gt "\${FAKE_SSO_ALERTS_BEFORE:-0}" ] && break
        sleep 0.1
      done
    fi
    [ -n "\${FAKE_SSO_APPROVE:-}" ] && touch "$WORK/aws.corp-login"
    exit 0
    ;;
  *) exit 255 ;;
esac
FAKE
chmod +x "$HOME/.local/bin/aws"
cat >"$SETUP_CFG/setup.sh" <<'SH'
setup_steps() {
  ensure_aws_sso good
  ensure_aws_sso stale
  ensure_aws_sso broken
  ensure_aws_sso cached
}
SH
# "cached": its SSO session token expired, but 12-hour role credentials are still cached.
SSO_TOKEN="$HOME/.aws/sso/cache/$(printf '%s' acme | shasum -a 1 | cut -d' ' -f1).json"
mkdir -p "$HOME/.aws/sso/cache" "$HOME/.aws/cli/cache"
echo '{"expiresAt": "2000-01-01T00:00:00Z"}' >"$SSO_TOKEN"
echo '{"ProviderType": "sso", "Credentials": {}}' >"$HOME/.aws/cli/cache/role.json"
out="$(BOSUN_CONFIG_DIR="$SETUP_CFG" "$BOSUN" setup --dry-run 2>&1)"
check "setup --dry-run keeps cached role credentials" test -f "$HOME/.aws/cli/cache/role.json"
check "setup --dry-run only checks" bash -c 'grep -q "would run: aws sso login --profile stale" <<<"$1" && ! grep -q "sso login" "$2"' _ "$out" "$WORK/aws.log"
# (The login timeout is only hang protection here: every login of the stand-in ends at once.)
out="$(BOSUN_CONFIG_DIR="$SETUP_CFG" BOSUN_LOGIN_TIMEOUT=60 "$BOSUN" setup 2>&1)"
code=$?
check "setup counts failed steps and exits 1" bash -c '[ "$1" = 1 ] && grep -q "setup finished with 1 failed step(s)" <<<"$2"' _ "$code" "$out"
check "a valid profile needs no login" contains "$out" "aws profile good is valid, no login needed"
check "an expired profile logs in" contains "$out" "aws profile stale logged in"
check "a login that does not help is a failure" contains "$out" "aws profile broken still invalid after sso login"
check "an expired SSO session is not hidden by cached role credentials" bash -c 'grep -q "aws profile cached logged in" <<<"$1" && [ ! -f "$2" ]' _ "$out" "$HOME/.aws/cli/cache/role.json"
# A step that would run a minute, under a timeout of 1 s (the minute is only how long a broken timeout would hang).
STEPS_CFG="$WORK/steps-config"
mkdir -p "$STEPS_CFG"
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$STEPS_CFG/"
printf 'setup_steps() {\n  run_step "slow step" sleep 60\n  run_step "quick step" true\n}\n' >"$STEPS_CFG/setup.sh"
out="$(BOSUN_CONFIG_DIR="$STEPS_CFG" BOSUN_LOGIN_TIMEOUT=1 "$BOSUN" setup 2>&1)"
check "a step that runs too long is stopped" contains "$out" "FAIL: slow step"
check "the other steps still run" contains "$out" "ok: quick step"
# The cached profile again, alone, its SSO session live now.
CACHED_CFG="$WORK/cached-config"
mkdir -p "$CACHED_CFG"
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$CACHED_CFG/"
echo 'setup_steps() { ensure_aws_sso cached; }' >"$CACHED_CFG/setup.sh"
echo '{"expiresAt": "2999-01-01T00:00:00Z"}' >"$SSO_TOKEN"
echo '{"ProviderType": "sso", "Credentials": {}}' >"$HOME/.aws/cli/cache/role.json"
rm -f "$WORK/aws.cached-login"
out="$(BOSUN_CONFIG_DIR="$CACHED_CFG" BOSUN_LOGIN_TIMEOUT=60 "$BOSUN" setup 2>&1)"
check "a live SSO session keeps its cached role credentials" bash -c 'grep -q "aws profile cached is valid, no login needed" <<<"$1" && [ -f "$2" ]' _ "$out" "$HOME/.aws/cli/cache/role.json"
rm -f "$SSO_TOKEN" "$HOME/.aws/cli/cache/role.json"
SSO_CFG="$WORK/sso-config"
mkdir -p "$SSO_CFG"
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$SSO_CFG/"
printf 'setup_steps() {\n  ensure_aws_sso books-dev\n  ensure_aws_sso books-prod\n}\n' >"$SSO_CFG/setup.sh"
SSO() { BOSUN_CONFIG_DIR="$SSO_CFG" BOSUN_LOGIN_TIMEOUT=60 "$BOSUN" setup "$@" 2>&1; } # (hang protection)
logins() { grep -c "sso login --sso-session corp" "$WORK/aws.log"; }
: >"$WORK/aws.log"
out="$(SSO --dry-run)"
check "setup --dry-run plans one login per SSO session" bash -c 'grep -q "would run: aws sso login --sso-session corp" <<<"$1" && grep -q "books-prod is not valid; the login for SSO session corp above would cover it" <<<"$1"' _ "$out"
out="$(FAKE_SSO_APPROVE=1 SSO)"
check "one login covers every profile of its SSO session" bash -c 'grep -q "aws profile books-dev logged in" <<<"$1" && grep -q "aws profile books-prod is valid, no login needed" <<<"$1" && [ "$2" = 1 ]' _ "$out" "$(logins)"
check "... and the login's OAuth parameters stay out of the log" bash -c 'grep -q "authorize?<masked>" <<<"$1" && ! grep -q secret <<<"$1"' _ "$out"
rm -f "$WORK/aws.corp-login"
: >"$WORK/aws.log"
out="$(SSO)"
check "a session whose login failed is not logged in again for its next profile" bash -c '[ "$2" = 1 ] && grep -q "FAIL: aws profile books-prod: its SSO session corp still needs a login (see above); run: aws sso login --sso-session corp" <<<"$1"' _ "$out" "$(logins)"
check "... the failure says what to run" contains "$out" "no browser approval within 60s. Run: aws sso login --sso-session corp"
: >"$WORK/aws.log"
sso_alerts="$(cat "$BOSUN_LOG_DIR"/setup-*.log 2>/dev/null | grep -c "alerting you")"
out="$(FAKE_SSO_WAIT=alert FAKE_SSO_ALERTS_BEFORE="$sso_alerts" BOSUN_AWS_SSO_ALERT_AFTER=1 SSO)"
check "a login still waiting for its browser approval alerts you" bash -c 'grep -q "still waits for a browser approval after 1s; alerting you" <<<"$1" && grep -q "alert (notification only; BOSUN_ALERT_SLACK_USER is empty): AWS SSO login for SSO session corp waits for your approval" <<<"$1"' _ "$out"
out="$(FAKE_SSO_APPROVE=1 BOSUN_AWS_SSO_ALERT_AFTER=60 SSO)"
check "... but a login the browser approves at once does not" bash -c '! grep -q "alerting you" <<<"$1"' _ "$out"
rm -f "$WORK/aws.corp-login"
LOGIN_CFG="$WORK/login-config"
mkdir -p "$LOGIN_CFG"
cp "$BOSUN_CONFIG_DIR/sessions.yml" "$LOGIN_CFG/"
echo 'setup_steps() { ensure_claude_login; }' >"$LOGIN_CFG/setup.sh"
LS() { BOSUN_CONFIG_DIR="$LOGIN_CFG" BOSUN_CLAUDE_BIN="$WORK/login-claude" "$BOSUN" setup "$@" 2>&1; }
rm -f "$WORK/login-probes.log"
check "setup --dry-run makes no request" bash -c 'grep -q "dry-run: would make one login check request" <<<"$1" && grep -q "ok: Claude Code login works" <<<"$1" && [ ! -e "$2" ]' _ "$(LS --dry-run)" "$WORK/login-probes.log"
check "ensure_claude_login passes when the login check request works" bash -c 'grep -q "ok: Claude Code login works: logged in (claude.ai)" <<<"$1"' _ "$(LS)"
check "... after one request" test "$(probes)" = 1
# My plugins: one my settings enable, and one installed that no scope names. The request loads none of them, though
# it reads no settings file; with an installed_plugins.json that does not parse it makes none, and says why.
mkdir -p "$HOME/.claude/plugins"
echo '{"enabledPlugins": {"probe@local": true}}' >"$HOME/.claude/settings.json"
echo '{"plugins": {"acme-helper@acme-market": [{"scope": "user"}]}}' >"$HOME/.claude/plugins/installed_plugins.json"
LS >/dev/null
check "the request switches off each plugin of mine, in one --settings, with no settings files" bash -c '
  [ "$(grep -cx -- --settings "$1")" = 1 ] && grep -qx -- --setting-sources "$1" &&
    [ "$(grep -m1 -A1 -x -- --settings "$1" | tail -1 | jq -c .)" = "{\"enabledPlugins\":{\"acme-helper@acme-market\":false,\"probe@local\":false}}" ]' \
  _ "$WORK/login-probe.args"
echo '[not an object]' >"$HOME/.claude/plugins/installed_plugins.json"
out="$(LS)"
check "with an installed_plugins.json that does not parse, no request, and the step says why" bash -c '
  grep -qF "warning: could not confirm the Claude Code login: the login check could not tell: unknown ($2 does not parse, so bosun cannot tell which plugins to switch off)" <<<"$1" &&
    [ "$3" = 2 ]' _ "$out" "$HOME/.claude/plugins/installed_plugins.json" "$(probes)"
rm -rf "$HOME/.claude/settings.json" "$HOME/.claude/plugins"
out="$(FAKE_PROBE=auth LS)"
check "a revoked login fails the step loudly, for the morning-failed alert" bash -c 'grep -q "FAIL: Claude Code login: the login check at .* got a login error (authentication_failed/401); run /login" <<<"$1" && grep -q "setup finished with 1 failed step" <<<"$1"' _ "$out"
out="$(FAKE_AUTH=out LS)"
check "logged out fails without a request" bash -c 'grep -q "FAIL: Claude Code login: claude auth status: not logged in" <<<"$1" && [ "$2" = 3 ]' _ "$out" "$(probes)"

section morning-dry-run "morning (dry run)" needs setup-steps covers 'libexec/bosun-morning'
out="$(BOSUN_CONFIG_DIR="$SETUP_CFG" BOSUN_NETWORK_PROBE_URL=file:///dev/null "$BOSUN" morning --dry-run --awake-minutes 5 2>&1)"
check "morning --dry-run would hold the keep-awake" contains "$out" "dry-run: would hold keep-awake for 5 minutes"
check "... but starts no caffeinate" test ! -f "$BOSUN_STATE_DIR/keep-awake-smoke.pid"
check "it waits for the network" contains "$out" "network is up"
# With no network it probes every 5 seconds, but sleeps no longer than what is left of its timeout. A stand-in that
# returns at once (no clock to race) writes down what the morning's own process asks of sleep, which in a dry run is
# only its network wait: each ask what is left of its 3 s timeout, 1 to 3 s, where a sleep to its next probe would
# ask 5. Every other sleep gets the real one: one asked by what the morning starts (the setup), by a subshell of it
# (a timeout's timer), or by any process outside this run (SMOKE_SLEEP_ASKED unset). The stand-in is in
# ~/.local/bin, where bosun looks first, for this one run.
cat >"$HOME/.local/bin/sleep" <<'SLEEP'
#!/bin/sh
morning() { case "$(ps -o command= -p "$1")" in */libexec/bosun-morning*) return 0 ;; esac; return 1; }
if [ -n "${SMOKE_SLEEP_ASKED:-}" ] && morning "$PPID" && ! morning "$(ps -o ppid= -p "$PPID" | tr -d ' ')"; then
  echo "$*" >>"$SMOKE_SLEEP_ASKED"
  exit 0
fi
exec /bin/sleep "$@"
SLEEP
chmod +x "$HOME/.local/bin/sleep"
rm -f "$WORK/sleep-asked.log"
out_down="$(SMOKE_SLEEP_ASKED="$WORK/sleep-asked.log" BOSUN_CONFIG_DIR="$SETUP_CFG" BOSUN_NETWORK_PROBE_URL=file:///nonexistent-probe BOSUN_NETWORK_TIMEOUT=3 "$BOSUN" morning --dry-run --awake-minutes 5 2>&1)"
rm -f "$HOME/.local/bin/sleep"
check "... and gives up once its timeout is over, each wait what was left of it, never its next 5-second probe" bash -c '
  grep -q "network still down after 3s; continuing" <<<"$1" && [ -s "$2" ] && ! grep -qvx "[123]" "$2" ||
    { echo "asked of sleep: $(sort "$2" 2>/dev/null | uniq -c | tr "\n" " ")"; grep -i network <<<"$1"; exit 1; }' _ "$out_down" "$WORK/sleep-asked.log"
check "it previews the setup steps" contains "$out" "aws profile good is valid, no login needed"
check "it lists the windows it would start, with model and effort" contains "$out" "alpha (session, opus/medium) -> $REPO_A"
check "it names no agent update when no agent in use has update_args" bash -c '! grep -qE "updated |could not update|would run .*update" <<<"$1"' _ "$out"

section morning-agents-update "agents: the morning's update" covers 'libexec/bosun-morning,libexec/bosun-update'
# An agent whose profile has update_args (none of the real ones does yet): the profiles' text is a shell
# variable, so a stub line stands in for the real table, and its command is a script that records its arguments.
printf '#!/bin/sh\necho "$@" >"%s/stub-agent.args"\n' "$WORK" >"$WORK/stub-agent-ok"
printf '#!/bin/sh\necho first line\necho network unreachable\nexit 1\n' >"$WORK/stub-agent-bad"
chmod +x "$WORK/stub-agent-ok" "$WORK/stub-agent-bad"
out="$(bash -c 'source "$1/lib/common.sh"; load_config; agent_setup claude' _ "$ROOT" 2>&1)"
check "an agent with no setup has nothing to set up, silently, under its lock" bash -c '[ -z "$1" ] && [ -f "$2/agent-setup.lock" ]' _ "$out" "$BOSUN_STATE_DIR"
update_stub() { # <command> [--dry-run]: update_agents with a stub agent in use; its problem words and its log
  bash -c '
    source "$1/lib/common.sh"; load_config
    agent_display_name() { echo Stub; }
    AGENT_PROFILES="$(printf "stub\tSTUB_BIN\tx\ty\tno\tno\tno\tno\tidle,busy\tself-update --now")"
    BOSUN_HARNESS=stub BOSUN_ORCHESTRATOR="" BOSUN_SESSIONS_FILE=/nonexistent STUB_BIN="$2"
    update_agents "${@:3}"' _ "$ROOT" "$@" 2>&1
}
out="$(update_stub "$WORK/stub-agent-ok")"
check "an agent whose profile has update_args is updated with them" bash -c '[ "$(cat "$1/stub-agent.args")" = "self-update --now" ]' _ "$WORK"
check "... and the update is logged, with no problem" bash -c 'grep -q "updated Stub" <<<"$1" && ! grep -q "update-stub" <<<"$1"' _ "$out"
rm -f "$WORK/stub-agent.args"
out="$(update_stub "$WORK/stub-agent-ok" --dry-run)"
check "a dry run says what it would run and runs nothing" bash -c 'grep -q "dry-run: would run $2 self-update --now" <<<"$1" && [ ! -e "$3/stub-agent.args" ]' _ "$out" "$WORK/stub-agent-ok" "$WORK"
out="$(update_stub "$WORK/stub-agent-bad")"
check "a failed update logs its last line and is a problem word" bash -c 'grep -q "could not update Stub: network unreachable" <<<"$1" && grep -qx "update-stub" <<<"$1"' _ "$out"

section start-modes "start modes" covers 'libexec/bosun-start,libexec/bosun-stop,libexec/bosun-ledger'
no_session
"$BOSUN" start >/dev/null 2>&1
wait_for 30 has_windows "orch alpha beta gamma checkin "
check "drain mode refreshes without restarts" contains "$(BOSUN_ON_EXISTING=drain "$BOSUN" start 2>&1)" "mode: drain"
first_pane="$(tmux -L "$SOCKET" display -p -t "=smoke:alpha" '#{pane_pid}')"
out="$("$BOSUN" start --restart 2>&1)"
check "--restart starts the session afresh" bash -c 'grep -q "restarting existing session" <<<"$1" && [ "$(tmux -L "$2" display -p -t =smoke:alpha "#{pane_pid}")" != "$3" ]' _ "$out" "$SOCKET" "$first_pane"
check "an unknown mode is an error" fails_with "must be refresh, drain or restart" env BOSUN_ON_EXISTING=bogus "$BOSUN" start
"$BOSUN" stop >/dev/null 2>&1

section stopped-until-started "stopped until started (what bosun quit leaves: quit.json)" covers 'libexec/bosun-learn,libexec/bosun-morning,libexec/bosun-quit,libexec/bosun-start,libexec/bosun-status,libexec/bosun-stop,libexec/bosun-watchdog,lib/quit_state.py,lib/watchdog.py'
no_session
mkdir -p "$WORK/fake-caffeinate"
printf '#!/bin/sh\nexit 0\n' >"$WORK/fake-caffeinate/caffeinate" # a real one would keep this Mac awake
chmod +x "$WORK/fake-caffeinate/caffeinate"
ensure_watchdog_state
write_quit paused
cp "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before-quit.json"
out="$("$BOSUN" watchdog 2>&1)"
check "the watchdog skips its run while bosun is stopped, saying since when" bash -c 'grep -qE "^bosun is stopped \(bosun quit at [0-9]{2}:[0-9]{2}\): skipping this run$" <<<"$1"' _ "$out"
check "... also the check-in loop's partial run" contains "$("$BOSUN" watchdog --only waiting 2>&1)" "bosun is stopped (bosun quit at"
check "... changing no state" cmp -s "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before-quit.json"
check "... but its read-only modes still answer" not_contains "$("$BOSUN" watchdog --status 2>&1)" "skipping this run"
out="$(BOSUN_LEARN_ENABLED=1 "$BOSUN" learn --scheduled 2>&1)"
check "the scheduled learning run skips while bosun is stopped" bash -c 'grep -qE "^bosun is stopped \(bosun quit at [0-9]{2}:[0-9]{2}\): skipping this learning run$" <<<"$1"' _ "$out"
check "... but a learn you type yourself still runs" contains "$(BOSUN_CLAUDE_BIN=false "$BOSUN" learn --collect-only --out "$WORK/learn-digest" 2>&1)" "digest of"
out="$("$BOSUN" morning --skip-setup 2>&1)"
check "a paused morning logs why and starts nothing" bash -c 'grep -qE "the morning start is paused \(bosun quit at [0-9]{2}:[0-9]{2}\); start bosun to resume" <<<"$1" && ! grep -qE "network is up|holding keep-awake|starting tmux session" <<<"$1"' _ "$out"
check "... it exits 0" "$BOSUN" morning --skip-setup
check "... and ends its log with a line the watchdog reads as the run's end, skipped: once bosun starts, not a run still going or failed" python3 -I -c '
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import watchdog
run = watchdog.last_real_run(Path(sys.argv[2]))
sys.exit(not (run is not None and run.outcome == "skipped"))' "$ROOT/lib" "$BOSUN_LOG_DIR/morning-$(date +%F).log"
check "... with no session, no keep-awake and the file still there" bash -c '! tmux -L "$1" has-session -t =smoke && [ ! -e "$2/keep-awake-smoke.pid" ] && [ -f "$2/quit.json" ]' _ "$SOCKET" "$BOSUN_STATE_DIR"
check "... also as a dry run" contains "$("$BOSUN" morning --dry-run 2>&1)" "the morning start is paused"
write_quit running
check "a quit that did not pause the morning does not stop a dry run, or clear the file for one" bash -c 'out="$("$1" morning --dry-run --skip-setup 2>&1)" && ! grep -q "is paused" <<<"$out" && [ -f "$2/quit.json" ]' _ "$BOSUN" "$BOSUN_STATE_DIR"
check "... a morning starts as usual" bash -c 'PATH="$3:$PATH" BOSUN_NETWORK_PROBE_URL=file:///dev/null "$1" morning --skip-setup --awake-minutes 1 >/dev/null 2>&1; tmux -L "$2" has-session -t =smoke' _ "$BOSUN" "$SOCKET" "$WORK/fake-caffeinate"
check "... and clears the file" test ! -e "$BOSUN_STATE_DIR/quit.json"
keep_awake_record_ok() { # the morning's keep-awake-<session>.json: an ISO time and an integer pid
  python3 -I -c 'import datetime, json, sys; d = json.load(open(sys.argv[1])); datetime.datetime.fromisoformat(d["until"]); sys.exit(not isinstance(d["pid"], int))' "$1"
}
check "the morning records when its keep-awake ends and whose it is (for bosun status --json)" keep_awake_record_ok "$BOSUN_STATE_DIR/keep-awake-smoke.json"
"$BOSUN" stop >/dev/null 2>&1
check "bosun stop removes that record with the pid file" test ! -e "$BOSUN_STATE_DIR/keep-awake-smoke.json"
rm -f "$BOSUN_STATE_DIR/keep-awake-smoke.pid"
write_quit paused
"$BOSUN" start >/dev/null 2>&1
check "bosun start clears it, paused or not" test ! -e "$BOSUN_STATE_DIR/quit.json"
check "... and stamps when it made the session (the watchdog's gate on a day no morning run is owed)" grep -qE "^$(date +%F) [0-9:]{8}$" "$BOSUN_STATE_DIR/started-smoke"
check "... and the watchdog runs again" not_contains "$("$BOSUN" watchdog --dry-run 2>&1)" "bosun is stopped"
check "... and with BOSUN_CONSOLE_ALWAYS off it leaves no marker for the console job" test ! -e "$BOSUN_STATE_DIR/console/wanted"
"$BOSUN" stop >/dev/null 2>&1
BOSUN_CONSOLE_ALWAYS=1 "$BOSUN" start >/dev/null 2>&1
check "with BOSUN_CONSOLE_ALWAYS=1 bosun start makes console/wanted, which the console job's KeepAlive watches" test -f "$BOSUN_STATE_DIR/console/wanted"
"$BOSUN" start >/dev/null 2>&1
check "a start with it off again takes the marker away" test ! -e "$BOSUN_STATE_DIR/console/wanted"
"$BOSUN" stop >/dev/null 2>&1

section status-json "status --json (the menu bar app's one source of how bosun is)" needs stopped-until-started covers 'libexec/bosun-status,libexec/bosun-start,libexec/bosun-stop,lib/menu_status*.py,libexec/bosun-watchdog'
"$BOSUN" stop >/dev/null 2>&1 # what a stop made today leaves (the section before ends with it too)
# said_has <JSON> <python condition over d, that object>: for an object one run printed, read by several checks.
said_has() { python3 -I -c 'import json, sys; d = json.loads(sys.argv[1]); sys.exit(not eval(sys.argv[2]))' "$1" "$2"; }
status_out="$("$BOSUN" status --json </dev/null)"
status_rc=$?
check "it prints one object with contract 1 and the documented fields, and exits 0" said_has "$status_out" 'd["contract"] == 1 and sorted(d) == sorted("contract level sentence detail windows running starting waiting keep_awake_until stopped schedule update console problems attention logs".split())'
check "... exit 0" test "$status_rc" -eq 0
check "... its console is not running and the address, which carries the token, is never in it" said_has "$status_out" 'd["console"] == {"running": False, "url": None} and "?t=" not in json.dumps(d)'
away_from_midnight() {
  local now=$((10#$(date +%H%M)))
  [ "$now" -gt 30 ] && [ "$now" -lt 2359 ]
}
check "with no window open, outside the awake hours, it does not say bosun is running: it is resting, not stopped, and says the hours" awake_outside status_has 'd["windows"] == 0 and d["sentence"] != "bosun is running" and d["sentence"].startswith("Resting: outside your hours") and d["schedule"]["in_hours"] is False and d["starting"] is False and d["stopped"] is None and d["update"] == {"state": "none", "waiting_for": [], "last": None}'
if away_from_midnight; then
  check "... and inside them, a stop made today is stopped, the morning not paused, and nothing is wrong" awake_all_day status_has 'd["windows"] == 0 and d["level"] == "stopped" and d["sentence"].startswith("bosun is stopped (bosun stop at ") and d["stopped"]["morning_paused"] is False and d["problems"] == []'
fi
write_quit paused
check "after a quit it says stopped, since when, and that the morning is paused" status_has 'd["level"] == "stopped" and d["sentence"] == "bosun is stopped" and d["stopped"]["morning_paused"] is True and d["stopped"]["since"][:2] == "20" and d["problems"] == []'
# (A quit that did not pause the morning, and a quit record that cannot be read: test_menu_status.py and
# test_quit_state.py.)
rm -f "$BOSUN_STATE_DIR/quit.json"
"$BOSUN" start >/dev/null 2>&1
wait_for 30 has_windows "orch alpha beta gamma checkin "
# One run, timed, for what status says with the windows open.
timed="$(python3 -I -c 'import subprocess, sys, time; t = time.monotonic(); r = subprocess.run(sys.argv[1:], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True); print(round(time.monotonic() - t, 1)); print(r.stdout, end="")' "$BOSUN" status --json)"
status_seconds="${timed%%$'\n'*}"
status_out="${timed#*$'\n'}"
check "with the windows open it counts them, and none of the fake Claudes waits" said_has "$status_out" 'd["windows"] == 5 and d["waiting"] == [] and d["level"] != "stopped"'
# (How long it took is a note, not a check: the suite asserts no durations; the profiled runs watch the time.)
echo "  note  bosun status --json took ${status_seconds}s with five windows (the app polls it)"
check "... a level of ok is one with no problems, anything else lists them" said_has "$status_out" '(d["level"] == "ok") == (d["problems"] == []) and all(isinstance(p, str) for p in d["problems"])'
check "... and no window needs you" said_has "$status_out" 'd["attention"] == []'
# A Claude in beta at a prompt (a stand-in: a sleep this run started, with a session record); its pid is killed after
beta_waits="$(fake_claude beta waiting 1.0.0 60)"
check "a window waiting at a prompt needs you, before the watchdog's waiting check names it" status_has 'd["waiting"] == ["beta"] and d["attention"] == [{"window": "beta", "problem": "The beta window is waiting for you at a permission prompt or question."}]'
kill "$beta_waits"
rm -f "$HOME/.claude/sessions/$beta_waits.json"
beta_waits="$(fake_claude beta waiting 1.0.0 7200)"
check "... and once the check names it, its finding is the problem that names the window, with bosun attach beta" status_has '[a["window"] for a in d["attention"]] == ["beta"] and "bosun attach beta" in d["attention"][0]["problem"] and d["attention"][0]["problem"] in d["problems"]'
kill "$beta_waits"
rm -f "$HOME/.claude/sessions/$beta_waits.json"
# (An empty watchdog.json is a problem in the one object: test_menu_status.py, through the live reader.)
ensure_watchdog_state
"$BOSUN" stop >/dev/null 2>&1
mkdir -p "$WORK/status-cfg"
printf 'if then fi\n' >"$WORK/status-cfg/bosun.env"
check "a bosun.env that does not parse still gets the object: an error, with the reason" env BOSUN_CONFIG_DIR="$WORK/status-cfg" bash -c '"$1" status --json | python3 -I -c "import json, sys; d = json.load(sys.stdin); sys.exit(not (d[\"contract\"] == 1 and d[\"level\"] == \"error\" and \"does not parse\" in d[\"problems\"][0]))"' _ "$BOSUN"
mkdir -p "$WORK/no-jq"
printf '#!/bin/sh\nexit 1\n' >"$WORK/no-jq/jq"
chmod +x "$WORK/no-jq/jq"
# (bosun puts ~/.local/bin ahead of the PATH it is given, so the jq that fails goes there for this check.)
cp "$WORK/no-jq/jq" "$HOME/.local/bin/jq"
check "a part that cannot be read is a problem and at least a warning, not a failure, outside the awake hours" awake_outside bash -c '"$1" status --json | python3 -I -c "import json, sys; d = json.load(sys.stdin); sys.exit(not (d[\"contract\"] == 1 and d[\"level\"] in (\"warning\", \"error\") and d[\"problems\"]))"' _ "$BOSUN"
rm -f "$HOME/.local/bin/jq"
if away_from_midnight; then
  # Stopped with a part unread: status cannot vouch for "stopped", so it is a warning that still carries the stop.
  # (The watchdog's saved state, since a jq that fails stops the checks that tell it was a stop at all.)
  cp -p "$BOSUN_STATE_DIR/watchdog.json" "$WORK/watchdog-before-stopped.json"
  : >"$BOSUN_STATE_DIR/watchdog.json"
  check "... and inside them, where a stop made today has stopped bosun: a warning that says which part, and is still stopped" awake_all_day status_has 'd["level"] == "warning" and d["sentence"].endswith(", with a warning") and d["stopped"] is not None and any("could not read" in p for p in d["problems"])'
  cp -p "$WORK/watchdog-before-stopped.json" "$BOSUN_STATE_DIR/watchdog.json"
fi

section quit "quit (bosun quit: the plan, the wait, the order of the stop, and what one start or morning undoes)" covers 'libexec/bosun-morning,libexec/bosun-quit,libexec/bosun-start,libexec/bosun-status,libexec/bosun-stop,libexec/bosun-watchdog,lib/quit*.py'
no_session
# A fake launchctl that says every job is loaded and writes down what it was told (in ~/.local/bin, which
# common.sh puts first on the PATH; the install section below writes its own over it), and a stand-in
# caffeinate that is only a sleep (named so bosun stop recognises it) and does not keep this Mac awake.
QUIT_BIN="$WORK/quit-bin"
QUIT_LAUNCHCTL_LOG="$WORK/quit-launchctl.log"
mkdir -p "$QUIT_BIN" "$HOME/.local/bin"
printf '#!/bin/sh\necho "launchctl $*" >>"%s"\nexit 0\n' "$QUIT_LAUNCHCTL_LOG" >"$HOME/.local/bin/launchctl"
chmod +x "$HOME/.local/bin/launchctl"
ln -s /bin/sleep "$QUIT_BIN/caffeinate" # not a copy: macOS kills a copied system binary
: >"$QUIT_LAUNCHCTL_LOG"
q() { "$BOSUN" quit "$@" </dev/null; }
quit_exits() { # <status> <bosun quit args...>: it exits with <status>; its output is in quit.out and quit.err
  local want="$1" rc
  shift
  q "$@" >"$WORK/quit.out" 2>"$WORK/quit.err"
  rc=$?
  [ "$rc" -eq "$want" ] || {
    echo "exit $rc, wanted $want"
    cat "$WORK/quit.out" "$WORK/quit.err"
    return 1
  }
}
quit_has() { # <python condition over d, the object bosun quit printed to quit.out>
  python3 -I -c 'import json, sys; d = json.load(open(sys.argv[1])); sys.exit(not eval(sys.argv[2]))' "$WORK/quit.out" "$1"
}
plan_has() { # <python condition over d, the object bosun quit --plan --json prints>
  q --plan --json | python3 -I -c 'import json, re, sys; d = json.load(sys.stdin); sys.exit(not eval(sys.argv[1]))' "$1"
}
console_told_to_stop() { # how many times launchd was told to stop the console job
  grep -Ec "^launchctl kill TERM gui/$(id -u)/[^ ]+\.console\$" "$QUIT_LAUNCHCTL_LOG"
}
caffeinate_runs() { ps -p "$1" -o comm= 2>/dev/null | grep -q caffeinate; } # <pid>
start_keep_awake() {                                                        # sets quit_caffeinate to the pid of a stand-in caffeinate, and records it as the morning does
  "$QUIT_BIN/caffeinate" 600 &
  quit_caffeinate=$!
  echo "$quit_caffeinate" >"$BOSUN_STATE_DIR/keep-awake-smoke.pid"
  python3 -I -c 'import datetime, json, sys; print(json.dumps({"until": (datetime.datetime.now().astimezone() + datetime.timedelta(hours=1)).isoformat(timespec="seconds"), "pid": int(sys.argv[1])}))' \
    "$quit_caffeinate" >"$BOSUN_STATE_DIR/keep-awake-smoke.json"
}
agent_status() { # <pid> <status>: what the fake Claude of that pid says it is doing
  printf '{"pid":%d,"status":"%s","version":"2.0.0","statusUpdatedAt":%d,"name":"w"}' "$1" "$2" $((($(date +%s) - 60) * 1000)) >"$HOME/.claude/sessions/$1.json"
}
quit_windows_up() { # a fresh session (the console's marker made) with alpha busy and beta waiting at a prompt
  BOSUN_CONSOLE_ALWAYS=1 "$BOSUN" start >/dev/null 2>&1
  wait_for 30 has_windows "orch alpha beta gamma checkin " || return 1
  quit_alpha="$(fake_claude alpha busy 2.0.0 60)" || return 1
  fake_claude beta waiting 2.0.0 60 >/dev/null || return 1
}
quit_windows_up
start_keep_awake
check "bosun start with BOSUN_CONSOLE_ALWAYS=1 made the console's marker" test -f "$BOSUN_STATE_DIR/console/wanted"

check "--plan --json: the open windows, the busy one, the waiting one and when the keep-awake ends" plan_has \
  'd["contract"] == 1 and d["busy"] == ["alpha"] and d["waiting"] == ["beta"] and d["refused"] is None and set(d["windows"]) >= {"orch", "alpha", "beta", "gamma", "checkin"} and re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", d["keep_awake_until"])'
check "--plan in text names them too" bash -c 'out="$("$1" quit --plan </dev/null)" && grep -q "busy: alpha" <<<"$out" && grep -q "waiting for you: beta" <<<"$out"' _ "$BOSUN"
check "... and changes nothing" bash -c 'tmux -L "$1" has-session -t =smoke && [ ! -e "$2/quit.json" ] && [ -f "$2/console/wanted" ]' _ "$SOCKET" "$BOSUN_STATE_DIR"

check "--let-finish=0 with an agent busy exits 3 ..." quit_exits 3 --let-finish=0
check "... naming the busy window, and not the one that waits for you" bash -c 'grep -q "Still busy after 0 minutes: alpha$" "$1" && ! grep -q beta "$1"' _ "$WORK/quit.out"
check "... and changed nothing: the session, quit.json, the console's marker and job, the keep-awake" bash -c '
  tmux -L "$1" has-session -t =smoke && [ ! -e "$2/quit.json" ] && [ -f "$2/console/wanted" ] && [ ! -s "$3" ] &&
  [ -f "$2/keep-awake-smoke.pid" ] && [ -f "$2/keep-awake-smoke.json" ]' _ "$SOCKET" "$BOSUN_STATE_DIR" "$QUIT_LAUNCHCTL_LOG"
check "... as JSON it exits 3 too" quit_exits 3 --let-finish=0 --json --pause-morning --sleep
check "... with an object that says the wait ran out, and who is busy" quit_has 'd["contract"] == 1 and d["result"] == "busy" and d["busy"] == ["alpha"]'

# Usage errors, each exiting 2.
check "no mode is a usage error (exit 2)" fails_with_status 2 "say what to do" q
check "--let-finish needs whole minutes (exit 2)" fails_with_status 2 "whole number of minutes" q --let-finish=soon
check "--let-finish and --stop-now are alternatives (exit 2)" fails_with_status 2 "alternatives" q --let-finish --stop-now
check "--plan takes neither --sleep nor --pause-morning (exit 2)" fails_with_status 2 "go with --let-finish or --stop-now" q --plan --sleep
check "an unknown argument is a usage error (exit 2)" fails_with_status 2 "unknown argument '--now'" q --now
check "a bad BOSUN_QUIT_WAIT_MINUTES is an error that names it" fails_with "BOSUN_QUIT_WAIT_MINUTES must be a whole number of minutes" env BOSUN_QUIT_WAIT_MINUTES=lots "$BOSUN" quit --let-finish
check "... with --json it is an object, exit 1, with the plan's keys empty" env BOSUN_QUIT_WAIT_MINUTES=lots bash -c '"$1" quit --let-finish --json </dev/null >"$2"; [ $? -eq 1 ]' _ "$BOSUN" "$WORK/quit.out"
check "... that says why" quit_has 'd["contract"] == 1 and d["result"] == "failed" and "BOSUN_QUIT_WAIT_MINUTES" in d["error"] and d["windows"] == [] and d["busy"] == [] and d["refused"] is None'
check "... and carries the configured lock wait, and 0 minutes for the minutes that are not a number" env BOSUN_QUIT_WAIT_MINUTES=lots BOSUN_WINDOWS_LOCK_WAIT_SECONDS=45 bash -c '"$1" quit --let-finish --json </dev/null >"$2"; true' _ "$BOSUN" "$WORK/quit.out"
check "... in the object" quit_has 'd["wait_minutes"] == 0 and d["lock_wait_seconds"] == 45 and d["steps_seconds"] > 120'
check "--let-finish from inside bosun's own tmux is refused: it would wait on its own window" fails_with "run it from another terminal" env TMUX="/tmp/tmux-0/$SOCKET,1,0" "$BOSUN" quit --let-finish
check "... as an object too with --json" bash -c 'TMUX="/tmp/tmux-0/$2,1,0" "$1" quit --let-finish --json </dev/null >"$3"; [ $? -eq 1 ]' _ "$BOSUN" "$SOCKET" "$WORK/quit.out"
check "... that says why" quit_has 'd["result"] == "failed" and "another terminal" in d["error"]'

hold_lock "$BOSUN_STATE_DIR/update.lock" quit-update
check "a quit is refused while an update runs: exit 1 ..." quit_exits 1 --stop-now
check "... with one line saying an update runs, and nothing stopped" bash -c 'grep -q "an update is running" "$1" && [ "$(wc -l <"$1")" -eq 1 ] && tmux -L "$2" has-session -t =smoke && [ ! -e "$3/quit.json" ]' _ "$WORK/quit.err" "$SOCKET" "$BOSUN_STATE_DIR"
check "... in JSON an object that says refused, and why" quit_exits 1 --stop-now --json
check "... with the reason" quit_has 'd["contract"] == 1 and d["result"] == "refused" and "update" in d["refused"]'
release_lock quit-update

# A start that is setting up the windows holds the windows lock: a quit does not write anything under it.
hold_lock "$BOSUN_STATE_DIR/windows.lock" quit-gives-up
check "a quit while a start holds the windows lock gives up when the wait is over: exit 1 ..." env BOSUN_WINDOWS_LOCK_WAIT_SECONDS=0 bash -c '"$1" quit --stop-now </dev/null >"$2" 2>"$3"; [ $? -eq 1 ]' _ "$BOSUN" "$WORK/quit.out" "$WORK/quit.err"
check "... with one line, and nothing changed: no quit.json, the session, the marker and the keep-awake" bash -c 'grep -q "setting up the windows" "$1" && [ "$(wc -l <"$1")" -eq 1 ] && [ ! -e "$2/quit.json" ] && tmux -L "$3" has-session -t =smoke && [ -f "$2/console/wanted" ] && [ -f "$2/keep-awake-smoke.pid" ]' _ "$WORK/quit.err" "$BOSUN_STATE_DIR" "$SOCKET"
release_lock quit-gives-up

# A quit record while windows are open is a quit that did not finish (bosun stop failed or was cut short)
write_quit paused
check "bosun status --json calls a quit record with windows still open an error, with how to finish" status_has 'd["level"] == "error" and d["sentence"] == "bosun quit did not finish" and "bosun quit --stop-now" in d["problems"][0] and "bosun start" in d["problems"][0]'
rm -f "$BOSUN_STATE_DIR/quit.json"

# --let-finish in the order its waits come, each moved on by the test once the quit says it waits (no fixed times):
# alpha busy (beta waiting at its prompt, which does not hold the wait), then a start holding the windows lock, which
# the quit takes only for its stop phase. The lock wait (120 s) and the minute for alpha are only hang protection.
hold_lock "$BOSUN_STATE_DIR/windows.lock" quit-let-finish
rm -f "$WORK/quit.out" "$WORK/quit.err"
BOSUN_WINDOWS_LOCK_WAIT_SECONDS=120 "$BOSUN" quit --let-finish=1 --pause-morning </dev/null >"$WORK/quit.out" 2>"$WORK/quit.err" &
quitting=$!
bg_started "$quitting"
check "--let-finish waits (polling every BOSUN_QUIT_POLL_SECONDS) until alpha is done, though beta waits at a prompt, saying so" \
  wait_for 60 grep -q "Waiting for 1 busy window (alpha), up to 1 minute" "$WORK/quit.err"
agent_status "$quit_alpha" idle
check "... then, alpha done, for the start holding the windows lock, saying so" \
  wait_for 60 grep -q "Waiting for a bosun start or refresh to finish setting up the windows, up to 120 s" "$WORK/quit.err"
check "... having stopped nothing while it waits: the session, no quit.json, the console's marker" bash -c '
  kill -0 "$1" && tmux -L "$2" has-session -t =smoke && [ ! -e "$3/quit.json" ] && [ -f "$3/console/wanted" ]' _ "$quitting" "$SOCKET" "$BOSUN_STATE_DIR"
release_lock quit-let-finish
bg_done "$quitting"
check "... and once the lock is free, it quits (exit 0): morning paused, Mac kept awake" test $? -eq 0
check "... it said on stderr who it waited for, once, and that it waited for the lock" bash -c '
  [ "$(wc -l <"$1")" -eq 2 ] && [ "$(grep -c "Waiting for 1 busy window (alpha), up to 1 minute" "$1")" -eq 1 ]' _ "$WORK/quit.err"
check "... it says what it stopped and who was waiting for you" bash -c 'grep -q "^bosun is stopped\.$" "$1" && grep -q "Stopped the windows: .*alpha" "$1" && grep -q "They were waiting for you: beta" "$1" && grep -q "paused until you run: bosun start" "$1"' _ "$WORK/quit.out"
check "... no session is left" bash -c '! tmux -L "$1" has-session -t =smoke' _ "$SOCKET"
check "... quit.json says the morning is paused" bash -c 'python3 -I -c "import json,sys; sys.exit(not json.load(open(sys.argv[1]))[\"morning_paused\"])" "$1/quit.json"' _ "$BOSUN_STATE_DIR"
check "... the console's marker is gone, and its job was told to stop" bash -c '[ ! -e "$1/console/wanted" ] && [ "$2" -eq 1 ]' _ "$BOSUN_STATE_DIR" "$(console_told_to_stop)"
check "... the keep-awake goes on: the caffeinate runs and its files stay" bash -c '[ -f "$1/keep-awake-smoke.pid" ] && [ -f "$1/keep-awake-smoke.json" ]' _ "$BOSUN_STATE_DIR"
check "... the caffeinate still runs" caffeinate_runs "$quit_caffeinate"
check "... and the quit says so" bash -c 'grep -qE "keep-awake kept until [0-9]{2}:[0-9]{2}" "$1"' _ "$WORK/quit.out"
check "bosun status --json says stopped" status_has 'd["level"] == "stopped" and d["stopped"]["morning_paused"] is True'
check "the watchdog skips its run, so it never restarts what was stopped" contains "$("$BOSUN" watchdog 2>&1)" "bosun is stopped"
check "... no session appeared" bash -c '! tmux -L "$1" has-session -t =smoke' _ "$SOCKET"

# Ruling 18: bosun start is the one place that creates the session (the morning, init and the update's
# restart all go through it, and refresh and send need the session to exist), so it is also where a quit ends.
BOSUN_CONSOLE_ALWAYS=1 "$BOSUN" start >/dev/null 2>&1
check "bosun start ends the quit: quit.json is gone, the console's marker is back and the session is up" bash -c '[ ! -e "$1/quit.json" ] && [ -f "$1/console/wanted" ] && tmux -L "$2" has-session -t =smoke' _ "$BOSUN_STATE_DIR" "$SOCKET"
wait_for 30 has_windows "orch alpha beta gamma checkin "
fake_claude alpha busy 2.0.0 60 >/dev/null
check "--stop-now quits with an agent busy, and --sleep lets the Mac sleep" quit_exits 0 --stop-now --sleep --json
check "... the object says stopped, who was busy, that the morning is not paused and that the keep-awake was released" quit_has 'd["contract"] == 1 and d["result"] == "stopped" and d["busy"] == ["alpha"] and d["morning_paused"] is False and d["keep_awake"] == "released" and d["console"] in ("stopped", "not running")'
check "... no session, quit.json without the morning paused" bash -c '! tmux -L "$1" has-session -t =smoke && python3 -I -c "import json,sys; sys.exit(json.load(open(sys.argv[1]))[\"morning_paused\"])" "$2/quit.json"' _ "$SOCKET" "$BOSUN_STATE_DIR"
check "... the caffeinate is gone, with its files" bash -c '[ ! -e "$1/keep-awake-smoke.pid" ] && [ ! -e "$1/keep-awake-smoke.json" ]' _ "$BOSUN_STATE_DIR"
check "... and the process" wait_for 30 bash -c '! ps -p "$1" -o comm= 2>/dev/null | grep -q caffeinate' _ "$quit_caffeinate"
check "... the console's job was told to stop again" test "$(console_told_to_stop)" = 2
# A notification stand-in that writes down what it was asked to show (~/.local/bin comes first on the PATH);
# the plain `exit 0` one is put back after the morning checks below.
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\n' "$WORK/quit-notify.log" >"$HOME/.local/bin/osascript"
chmod +x "$HOME/.local/bin/osascript"
: >"$WORK/quit-notify.log"
check "a morning that was not paused starts bosun again, through bosun start: the quit is over and the console's marker is back" bash -c '
  PATH="$5:$PATH" BOSUN_CONSOLE_ALWAYS=1 BOSUN_NETWORK_PROBE_URL=file:///dev/null "$1" morning --skip-setup --awake-minutes 1 >/dev/null 2>&1
  [ ! -e "$2/quit.json" ] && [ -f "$2/console/wanted" ] && tmux -L "$3" has-session -t =smoke' _ "$BOSUN" "$BOSUN_STATE_DIR" "$SOCKET" "" "$WORK/fake-caffeinate"
check "... and tells you the sessions are ready (the control for the check below)" grep -q "Sessions ready" "$WORK/quit-notify.log"
"$BOSUN" stop >/dev/null 2>&1
rm -f "$BOSUN_STATE_DIR/quit.json" "$BOSUN_STATE_DIR/console/wanted"
: >"$WORK/quit-notify.log"

# The morning clears an unpaused quit, runs its setup (minutes), then starts: a quit made in between must
# survive that start, even one that did not pause the morning.
printf 'setup_steps() { python3 -I -c "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import quit_state; quit_state.write(Path(sys.argv[2]), True)" "%s" "%s"; }\n' \
  "$ROOT/lib" "$BOSUN_STATE_DIR" >"$WORK/quit-during-setup.sh"
quit_morning_out="$(PATH="$WORK/fake-caffeinate:$PATH" BOSUN_SETUP_FILE="$WORK/quit-during-setup.sh" BOSUN_NETWORK_PROBE_URL=file:///dev/null "$BOSUN" morning --awake-minutes 1 2>&1 </dev/null)"
quit_morning_rc=$?
check "a quit made while the morning sets up survives the morning's start, which logs that and starts nothing" bash -c 'grep -q "bosun was quit at [0-9:]*: not starting it" <<<"$1" && [ -f "$2/quit.json" ] && ! tmux -L "$3" has-session -t =smoke' _ "$quit_morning_out" "$BOSUN_STATE_DIR" "$SOCKET"
check "... the morning then logs that a quit came during it, not that it completed, and exits 0" bash -c 'grep -q "morning run skipped: bosun quit during the run" <<<"$1" && ! grep -q "morning run complete" <<<"$1" && [ "$2" -eq 0 ]' _ "$quit_morning_out" "$quit_morning_rc"
check "... and does not tell you the sessions are ready" bash -c '! grep -q "Sessions ready" "$1"' _ "$WORK/quit-notify.log"
# The same, with a problem of the run's own (no network): its problems line names the quit too, so it is not read as a start that failed.
rm -f "$BOSUN_STATE_DIR/quit.json"
: >"$WORK/quit-notify.log"
quit_problem_out="$(PATH="$WORK/fake-caffeinate:$PATH" BOSUN_SETUP_FILE="$WORK/quit-during-setup.sh" BOSUN_NETWORK_PROBE_URL=file:///nonexistent-probe BOSUN_NETWORK_TIMEOUT=1 "$BOSUN" morning --awake-minutes 1 2>&1 </dev/null)"
quit_problem_rc=$?
check "a quit during a run that also had a problem: the problems line names both, and it exits 1" bash -c 'grep -q "morning run finished with problems: network.*bosun quit during the run, so its start was skipped" <<<"$1" && [ "$2" -eq 1 ] && grep -q "bosun quit during the run" "$3"' _ "$quit_problem_out" "$quit_problem_rc" "$WORK/quit-notify.log"
printf '#!/bin/sh\nexit 0\n' >"$HOME/.local/bin/osascript" # no real notifications from tests
check "bosun start --unless-quit with a quit record starts nothing, clears nothing, exits 0" bash -c '"$1" start --unless-quit >/dev/null 2>&1 && [ -f "$2/quit.json" ] && ! tmux -L "$3" has-session -t =smoke' _ "$BOSUN" "$BOSUN_STATE_DIR" "$SOCKET"
"$BOSUN" start >/dev/null 2>&1
check "... while a plain bosun start, the person's own act, still ends the quit" bash -c '[ ! -e "$1/quit.json" ] && tmux -L "$2" has-session -t =smoke' _ "$BOSUN_STATE_DIR" "$SOCKET"
check "bosun start --unless-quit with no quit record starts as usual" bash -c '"$1" stop >/dev/null 2>&1; "$1" start --unless-quit >/dev/null 2>&1 && tmux -L "$2" has-session -t =smoke' _ "$BOSUN" "$SOCKET"
"$BOSUN" stop >/dev/null 2>&1
rm -f "$BOSUN_STATE_DIR/quit.json" "$BOSUN_STATE_DIR/console/wanted" "$BOSUN_STATE_DIR/keep-awake-smoke.pid" "$BOSUN_STATE_DIR/keep-awake-smoke.json"

# `bosun morning --now`: the morning run at any hour or day (what the app's Start runs, through --background). The awake window is built around the
# current time, so these hold whatever time the suite runs at: a window ending in 20 minutes that began 2 minutes
# ago is inside (an end earlier than the start runs past midnight, which the window rule handles), one that is 10
# to 20 minutes ahead is outside, and with every day but today's and yesterday's scheduled, no window holds now.
section morning-now "morning --now (the morning run at any hour or day)" needs quit covers 'libexec/bosun-morning,libexec/bosun-quit,libexec/bosun-stop,libexec/bosun-ledger,lib/keep_awake.py,lib/login_start.py,lib/awake.py'
today_letter="$(printf MTWRFSU | cut -c "$(date +%u)")"
yesterday_letter="$(printf MTWRFSU | cut -c "$(date -v-1d +%u)")"
unscheduled_days="$(printf MTWRFSU | tr -d "$today_letter$yesterday_letter")"
inside_until="$(date -v+20M +%H:%M)"
awake_inside=(BOSUN_DAYS=MTWRFSU BOSUN_START_TIME="$(date -v-2M +%H:%M)" BOSUN_AWAKE_UNTIL="$inside_until")
awake_outside=(BOSUN_DAYS=MTWRFSU BOSUN_START_TIME="$(date -v+10M +%H:%M)" BOSUN_AWAKE_UNTIL="$(date -v+20M +%H:%M)")
awake_unscheduled=(BOSUN_DAYS="$unscheduled_days" BOSUN_START_TIME=00:00 BOSUN_AWAKE_UNTIL=23:59)
# The morning job's shells carry BOSUN_TRIGGER=launchd: the header must still say now.
now_morning() { # <VAR=value>... <bosun> morning --now ...: with a stand-in caffeinate and no network
  env PATH="$WORK/fake-caffeinate:$PATH" BOSUN_TRIGGER=launchd BOSUN_NETWORK_PROBE_URL=file:///dev/null "$@" </dev/null 2>&1
}
now_clean() {
  "$BOSUN" stop >/dev/null 2>&1
  rm -f "$BOSUN_STATE_DIR/quit.json" "$BOSUN_STATE_DIR/console/wanted" "$BOSUN_STATE_DIR/keep-awake-smoke.pid" "$BOSUN_STATE_DIR/keep-awake-smoke.json"
}
has_session() { tmux -L "$SOCKET" has-session -t =smoke; }
no_keep_awake() { [ ! -e "$BOSUN_STATE_DIR/keep-awake-smoke.pid" ] && [ ! -e "$BOSUN_STATE_DIR/keep-awake-smoke.json" ]; }
keep_awake_ends_at() { # <HH:MM>: the record's end is that minute, and the pid file is there
  [ -f "$BOSUN_STATE_DIR/keep-awake-smoke.pid" ] &&
    python3 -I -c 'import datetime, json, sys; d = json.load(open(sys.argv[1])); sys.exit(datetime.datetime.fromisoformat(d["until"]).strftime("%H:%M") != sys.argv[2])' "$BOSUN_STATE_DIR/keep-awake-smoke.json" "$1"
}

keep_awake_ends_in() { # <minutes>: the record's end is that far ahead, give or take the seconds the run took
  python3 -I -c 'import datetime, json, sys; d = json.load(open(sys.argv[1])); left = (datetime.datetime.fromisoformat(d["until"]) - datetime.datetime.now().astimezone()).total_seconds(); sys.exit(not (int(sys.argv[2]) * 60 - 60 < left <= int(sys.argv[2]) * 60 + 5))' "$BOSUN_STATE_DIR/keep-awake-smoke.json" "$1"
}

write_quit paused
out="$(now_morning "${awake_outside[@]}" "$BOSUN" morning --now --skip-setup)"
now_rc=$?
check "--now outside the awake window starts the session, exits 0 and says so in its header whatever BOSUN_TRIGGER is" bash -c 'grep -q "=== morning run (trigger=now, " <<<"$1" && ! grep -q "trigger=launchd" <<<"$1" && grep -q "morning run complete" <<<"$1" && [ "$2" -eq 0 ] && tmux -L "$3" has-session -t =smoke' _ "$out" "$now_rc" "$SOCKET"
check "... it holds no keep-awake and logs one line saying it is outside the window" bash -c 'grep -c "outside the awake window" <<<"$1" | grep -qx 1 && ! grep -q "holding keep-awake" <<<"$1"' _ "$out"
check "... no caffeinate, no pid file, no record" no_keep_awake
check "... it cleared the quit that paused the morning, instead of stopping for it" bash -c '! grep -q "is paused" <<<"$1" && [ ! -e "$2/quit.json" ]' _ "$out" "$BOSUN_STATE_DIR"
now_clean

out="$(now_morning "${awake_inside[@]}" "$BOSUN" morning --now --skip-setup)"
check "--now inside the awake window holds the keep-awake until BOSUN_AWAKE_UNTIL (the record says so) and starts the session" bash -c 'grep -q "holding keep-awake for " <<<"$1" && ! grep -q "outside the awake window" <<<"$1" && tmux -L "$2" has-session -t =smoke' _ "$out" "$SOCKET"
check "... the record ends at that minute" keep_awake_ends_at "$inside_until"
now_clean

# A window that began yesterday and runs past midnight into now (an hour from now until half an hour from now, the
# next day): the run's log is yesterday's, the workday's, which the watchdog reads (lib/awake.py's morning-log).
now_hm=$((10#$(date +%H%M)))
if [ "$now_hm" -ge 30 ] && [ "$now_hm" -lt 2230 ]; then
  rm -f "$BOSUN_LOG_DIR/morning-$(date -v-1d +%F).log"
  now_morning BOSUN_DAYS=MTWRFSU BOSUN_START_TIME="$(date -v+1H +%H:%M)" BOSUN_AWAKE_UNTIL="$(date -v+30M +%H:%M)" "$BOSUN" morning --now --dry-run >/dev/null
  check "in a window that began yesterday and runs past midnight, the run's log is the workday's, as the watchdog reads it" grep -q "=== morning run (trigger=now, session=smoke, dry_run=1) ===" "$BOSUN_LOG_DIR/morning-$(date -v-1d +%F).log"
fi
check "the morning log's name falls back to today's when lib/awake.py cannot say, with a warning on stderr" bash -c '
  source "$1/lib/common.sh"; load_config
  python3() { return 1; }
  name="$(morning_log_file 2>"$2")" && [ "$name" = "$BOSUN_LOG_DIR/morning-$(date +%F).log" ] && grep -q "WARN: lib/awake.py did not name the morning log" "$2"' _ "$ROOT" "$WORK/morning-log.err"

out="$(now_morning "${awake_unscheduled[@]}" "$BOSUN" morning --now --skip-setup)"
check "--now on a day that is not scheduled holds no keep-awake, and starts the session" bash -c 'grep -q "outside the awake window" <<<"$1" && ! grep -q "holding keep-awake" <<<"$1" && tmux -L "$2" has-session -t =smoke' _ "$out" "$SOCKET"
check "... no pid file, no record" no_keep_awake
now_clean

out="$(now_morning "${awake_outside[@]}" "$BOSUN" morning --now --skip-setup --awake-minutes 3)"
check "--awake-minutes wins over the window with --now, as without it" contains "$out" "holding keep-awake for 3 minutes"
check "... the record ends in about three minutes" keep_awake_ends_in 3
now_clean

write_quit paused
out="$(now_morning "${awake_inside[@]}" "$BOSUN" morning --now --dry-run --skip-setup)"
check "--now --dry-run says what it would do: the trigger, the quit it would end, the keep-awake and until when" bash -c 'grep -q "=== morning run (trigger=now, .*dry_run=1" <<<"$1" && grep -q "dry-run: would end the bosun quit (at " <<<"$1" && grep -q "dry-run: would hold keep-awake for [0-9]* minutes (until '"$inside_until"')" <<<"$1"' _ "$out"
check "... and changes nothing: the quit stays, no session, no keep-awake" bash -c '[ -f "$1/quit.json" ] && ! tmux -L "$2" has-session -t =smoke' _ "$BOSUN_STATE_DIR" "$SOCKET"
check "... no pid file, no record" no_keep_awake
out="$(now_morning "${awake_outside[@]}" "$BOSUN" morning --now --dry-run --skip-setup)"
check "--now --dry-run outside the window says it would hold none" bash -c 'grep -q "outside the awake window" <<<"$1" && ! grep -q "would hold keep-awake" <<<"$1"' _ "$out"
check "... and changes nothing" bash -c '[ -f "$1/quit.json" ] && ! tmux -L "$2" has-session -t =smoke' _ "$BOSUN_STATE_DIR" "$SOCKET"
now_clean

check "--now with settings that are not a window fails in one line, naming the setting" fails_with "BOSUN_DAYS has 'X'" now_morning BOSUN_DAYS=X "$BOSUN" morning --now --skip-setup
check "... starting nothing" bash -c '! tmux -L "$1" has-session -t =smoke' _ "$SOCKET"

# --now clears the quit at its top, but still passes --unless-quit to its start: a quit made while this Start sets up is kept.
out="$(now_morning "${awake_outside[@]}" BOSUN_SETUP_FILE="$WORK/quit-during-setup.sh" "$BOSUN" morning --now)"
now_rc=$?
check "a quit made while --now sets up survives its start, which logs that, and the run says so and exits 0" bash -c 'grep -q "bosun was quit at [0-9:]*: not starting it" <<<"$1" && grep -q "morning run skipped: bosun quit during the run" <<<"$1" && ! grep -q "morning run complete" <<<"$1" && [ "$2" -eq 0 ] && [ -f "$3/quit.json" ] && ! tmux -L "$4" has-session -t =smoke' _ "$out" "$now_rc" "$BOSUN_STATE_DIR" "$SOCKET"
now_clean

# --at-login (lib/login_start.py): the decision comes before the run's header, so a skip leaves none.
morning_log="$BOSUN_LOG_DIR/morning-$(date +%F).log"
run_headers() { grep -c "=== morning run" "$morning_log" 2>/dev/null || true; }
at_login() { # <VAR=value>...: bosun morning --at-login, with a stand-in caffeinate and no network
  now_morning "$@" "$BOSUN" morning --at-login
}
now_clean
headers_before="$(run_headers)"
out="$(at_login "${awake_inside[@]}" BOSUN_START_AT_LOGIN=0)"
now_rc=$?
check "--at-login with BOSUN_START_AT_LOGIN=0 logs why, exits 0 and starts no session" bash -c 'grep -q "not starting: BOSUN_START_AT_LOGIN is 0" <<<"$1" && [ "$2" -eq 0 ] && ! tmux -L "$3" has-session -t =smoke' _ "$out" "$now_rc" "$SOCKET"
check "... a skip writes no run header (the watchdog would read it as a run that never finished)" bash -c '! grep -q "=== morning run" <<<"$1" && [ "$(grep -c "=== morning run" "$2")" -eq "$3" ]' _ "$out" "$morning_log" "$headers_before"
check "... its line is in the day's morning log" grep -q "not starting: BOSUN_START_AT_LOGIN is 0" "$morning_log"

out="$(at_login "${awake_inside[@]}" BOSUN_START_AT_LOGIN=1)"
check "--at-login after bosun stop says you stopped bosun today, and starts nothing" bash -c 'grep -q "not starting: you stopped bosun today at " <<<"$1" && ! grep -q "=== morning run" <<<"$1" && ! tmux -L "$2" has-session -t =smoke' _ "$out" "$SOCKET"
# (Outside your hours, on a day not yours, right after a reload of the jobs: test_login_start.py.)
write_quit paused
out="$(at_login "${awake_inside[@]}" BOSUN_START_AT_LOGIN=1)"
check "--at-login after a quit that paused the morning says so, and leaves the quit" bash -c 'grep -q "not starting: bosun was quit at [0-9:]* with the morning start paused" <<<"$1" && [ -f "$2/quit.json" ]' _ "$out" "$BOSUN_STATE_DIR"
rm -f "$BOSUN_STATE_DIR/quit.json" "$BOSUN_STATE_DIR/stopped-smoke"
out="$(at_login "${awake_inside[@]}" BOSUN_START_AT_LOGIN=1)"
now_rc=$?
check "--at-login in your hours with nothing in the way starts the session, and the header says trigger=login whatever BOSUN_TRIGGER is" bash -c 'grep -q "starting bosun: you logged in during your hours" <<<"$1" && grep -q "=== morning run (trigger=login, " <<<"$1" && ! grep -q "trigger=launchd" <<<"$1" && grep -q "morning run complete" <<<"$1" && [ "$2" -eq 0 ] && tmux -L "$3" has-session -t =smoke' _ "$out" "$now_rc" "$SOCKET"
out="$(at_login "${awake_inside[@]}" BOSUN_START_AT_LOGIN=1)"
check "... a second login with the session running says it is running already" bash -c 'grep -q "not starting: bosun is running already" <<<"$1" && ! grep -q "=== morning run" <<<"$1"' _ "$out"
now_clean
rm -f "$BOSUN_STATE_DIR/stopped-smoke" # the stop above is a stop today, which comes before the running check
cp "$HOME/.local/bin/launchctl" "$WORK/launchctl.plain"
printf '#!/bin/sh\n[ "$1" != print ] || printf "\\tstate = running\\n"\nexit 0\n' >"$HOME/.local/bin/launchctl"
out="$(at_login "${awake_inside[@]}" BOSUN_START_AT_LOGIN=1)"
check "... and with the morning job running (a scheduled run going, no session yet), it says bosun is running and starts nothing" bash -c 'grep -q "not starting: bosun is running already" <<<"$1" && ! grep -q "=== morning run" <<<"$1"' _ "$out"
cp "$WORK/launchctl.plain" "$HOME/.local/bin/launchctl"
now_clean
out="$(at_login "${awake_inside[@]}" BOSUN_START_AT_LOGIN=1 BOSUN_DAYS=X 2>&1)"
now_rc=$?
check "--at-login with a bad setting fails in one line that names it, not just a status" bash -c '[ "$2" -ne 0 ] && grep -q "BOSUN_DAYS" <<<"$1" && ! grep -q "could not be decided (status" <<<"$1"' _ "$out" "$now_rc"
check "--at-login takes no other option" fails_with "--at-login takes no other option" now_morning "$BOSUN" morning --at-login --now
now_clean

stop_keep_awake_leaves_it() { # bosun stop --keep-awake keeps the caffeinate and its files; a plain stop releases them
  start_keep_awake
  "$BOSUN" stop --keep-awake >/dev/null 2>&1
  [ -f "$BOSUN_STATE_DIR/keep-awake-smoke.pid" ] && [ -f "$BOSUN_STATE_DIR/keep-awake-smoke.json" ] && caffeinate_runs "$quit_caffeinate" || return 1
  "$BOSUN" stop >/dev/null 2>&1
  [ ! -e "$BOSUN_STATE_DIR/keep-awake-smoke.pid" ] && [ ! -e "$BOSUN_STATE_DIR/keep-awake-smoke.json" ] || return 1
  wait_for 30 bash -c '! ps -p "$1" -o comm= 2>/dev/null | grep -q caffeinate' _ "$quit_caffeinate"
}
check "bosun stop --keep-awake leaves the caffeinate and its files alone; a plain stop releases them" stop_keep_awake_leaves_it
plain_fake launchctl 113 # the fake above; this section's alone

section settings "settings (bosun settings get|set: bosun.env through init's writer, the jobs and the wake)" covers 'libexec/bosun-settings,lib/settings.py,lib/run_locked.py,libexec/bosun-init,templates/launchd*.plist'
# Its own config, state and launchd label, so what it writes and reloads is its alone; launchd has a job while it
# has its plist, pmset's repeating event is what $SET_SCHED holds, pmset repeat fails while
# $WORK/settings-pmset-fails is there, launchctl bootstrap while $WORK/settings-bootstrap-fails is. sudo is sudoers
# in small: while $WORK/sudo-fails is there every rule needs a password (sudo -v and sudo -n fail), but for pmset
# while $WORK/sudo-allows-pmset is (a NOPASSWD rule for it); $WORK/sudo-lists-only lets sudo -l say yes to any
# command (sudoers' listpw: another rule has NOPASSWD) while running one still needs the password.
SET_CFG="$WORK/settings-config"
SET_STATE="$WORK/settings-state"
SET_LABEL="com.bosun.smoke-settings"
SET_SCHED="$WORK/settings-pmset-sched"
SET_LOG="$WORK/settings-system.log"
SET_PLIST="$HOME/Library/LaunchAgents/$SET_LABEL.plist"
mkdir -p "$SET_CFG" "$SET_STATE" "$HOME/Library/LaunchAgents"
: >"$SET_LOG"
cat >"$HOME/.local/bin/launchctl" <<EOF
#!/bin/bash
echo "launchctl \$*" >>"$SET_LOG"
[ "\$1" != print ] || [ -e "\$HOME/Library/LaunchAgents/\${2##*/}.plist" ] || exit 113
[ "\$1" != bootstrap ] || [ ! -e "$WORK/settings-bootstrap-fails" ] || exit 5
exit 0
EOF
cat >"$HOME/.local/bin/pmset" <<EOF
#!/bin/bash
echo "pmset \$*" >>"$SET_LOG"
if [ "\$1 \$2" = "-g sched" ] && [ -s "$SET_SCHED" ]; then cat "$SET_SCHED"; fi
[ "\$1" != repeat ] || [ ! -e "$WORK/settings-pmset-fails" ] || exit 1
exit 0
EOF
cat >"$HOME/.local/bin/sudo" <<EOF
#!/bin/bash
echo "sudo \$*" >>"$SET_LOG"
[ "\$1" = -n ] && shift
needs_password() { [ -e "$WORK/sudo-fails" ] && ! { [ "\$1" = pmset ] && [ -e "$WORK/sudo-allows-pmset" ]; }; }
case "\$1" in
  -v) [ ! -e "$WORK/sudo-fails" ]; exit ;;
  -l) ! needs_password "\$2" || [ -e "$WORK/sudo-lists-only" ]; exit ;;
esac
if needs_password "\$1"; then echo "sudo: a password is required" >&2; exit 1; fi
exec "\$@"
EOF
chmod +x "$HOME/.local/bin/launchctl" "$HOME/.local/bin/pmset" "$HOME/.local/bin/sudo"
settings_cmd() { # <args>: bosun settings with this section's folders and label, and no terminal to ask in
  BOSUN_CONFIG_DIR="$SET_CFG" BOSUN_STATE_DIR="$SET_STATE" BOSUN_LOG_DIR="$SET_STATE/logs" \
    BOSUN_LAUNCHD_LABEL="$SET_LABEL" "$BOSUN" settings "$@" </dev/null
}
settings_exits() { # <status> <args>: bosun settings exits <status>
  local want="$1" status
  shift
  settings_cmd "$@" >"$WORK/settings.out" 2>"$WORK/settings.err"
  status=$?
  [ "$status" -eq "$want" ] || {
    echo "exit $status not $want: $(cat "$WORK/settings.err")"
    return 1
  }
}
settings_has() { python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); sys.exit(0 if eval(sys.argv[2]) else 1)' "$WORK/settings.out" "$1"; }
pmset_sched() { printf 'Repeating power events:\n  %s\n' "$1" >"$SET_SCHED"; }
printf '# acme: the schedule\nBOSUN_START_TIME="08:00"\n\n# mine\nBOSUN_EVENTS_ENABLED="1"\n' >"$SET_CFG/bosun.env"

check "get --json prints one object, contract 1, with each setting's value, default and meaning" settings_exits 0 get --json
check "... BOSUN_START_TIME as bosun.env sets it, BOSUN_DAYS as the default, and no wake" settings_has 'd["contract"] == 1 and d["readable"] and d["settings"]["BOSUN_START_TIME"]["set"] and d["settings"]["BOSUN_DAYS"] == {**d["settings"]["BOSUN_DAYS"], "value": "MTWRF", "set": False, "valid": True} and d["wake"] == "none"'
check "get, for a person, a line a setting" contains "$(settings_cmd get 2>&1)" "BOSUN_START_TIME=08:00  (set)"

check "set BOSUN_START_TIME=09:00 succeeds" settings_exits 0 set BOSUN_START_TIME=09:00
check "... and says what changed" contains "$(cat "$WORK/settings.out")" "BOSUN_START_TIME: 08:00 → 09:00"
check "... bosun.env says 09:00, its other lines and comments kept" bash -c 'grep -qx "BOSUN_START_TIME=\"09:00\"" "$1" && grep -qx "# acme: the schedule" "$1" && grep -qx "# mine" "$1" && grep -qx "BOSUN_EVENTS_ENABLED=\"1\"" "$1"' _ "$SET_CFG/bosun.env"
check "... a backup holds bosun.env as it was" bash -c 'grep -qx "BOSUN_START_TIME=\"08:00\"" "$1"/bosun.env.pre-init-*' _ "$SET_CFG"
check "... the morning job's plist says 09:00, and launchd loaded it" bash -c 'grep -q "<key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer>" "$1" && grep -q "launchctl bootstrap gui/$(id -u) $1" "$2"' _ "$SET_PLIST" "$SET_LOG"
check "... with no repeating wake, no sudo" bash -c '! grep -q "^sudo" "$1"' _ "$SET_LOG"

settings_cmd set BOSUN_START_TIME=08:00 >/dev/null 2>&1
pmset_sched "wakepoweron at 8:01AM weekdays only"
: >"$SET_LOG"
check "with bosun's wake in pmset, set BOSUN_START_TIME=09:00 succeeds" settings_exits 0 set BOSUN_START_TIME=09:00
check "... asking sudo first about the very pmset command, then moving the wake with it, sudo -n" bash -c 'grep -qx "sudo -n -l pmset repeat wakeorpoweron MTWRF 09:01:00" "$1" && grep -qx "sudo -n pmset repeat wakeorpoweron MTWRF 09:01:00" "$1" && [ "$(grep -n "^sudo -n -l" "$1" | head -1 | cut -d: -f1)" -lt "$(grep -n "^sudo -n pmset" "$1" | cut -d: -f1)" ]' _ "$SET_LOG"

# A step after the write that fails puts bosun.env back from the backup the write made, and the jobs with it.
pmset_sched "wakepoweron at 9:01AM weekdays only"
cp "$SET_CFG/bosun.env" "$WORK/settings-before.env"
put_back_said() { # <what the line says>: the run failed in one ERROR line, the last, saying it; bosun.env as before
  local errors
  errors="$(grep -c 'ERROR: ' "$WORK/settings.err")"
  if [ "$errors" != 1 ] || ! tail -1 "$WORK/settings.err" | grep -qF -- "$1"; then
    echo "ERROR lines: $errors; stderr: $(cat "$WORK/settings.err")"
    return 1
  fi
  cmp -s "$SET_CFG/bosun.env" "$WORK/settings-before.env" || {
    echo "bosun.env changed"
    return 1
  }
}
morning_at_nine() { grep -q "<key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer>" "$SET_PLIST"; }
touch "$WORK/settings-pmset-fails"
check "when pmset repeat fails, set fails (exit 1)" settings_exits 1 set BOSUN_START_TIME=10:00
check "... in one line saying bosun.env and the jobs were put back, and bosun.env is byte for byte as before" put_back_said "pmset did not take the new wake (pmset failed), so bosun.env and the jobs were put back as they were"
check "... and the morning job is back at 09:00" morning_at_nine
rm -f "$WORK/settings-pmset-fails"
touch "$WORK/settings-bootstrap-fails"
: >"$SET_LOG"
check "when launchctl bootstrap fails in the reload, set fails (exit 1)" settings_exits 1 set BOSUN_START_TIME=10:00
check "... in one line naming the reload, and bosun.env is byte for byte as before" put_back_said "could not reload bosun's launchd jobs (launchctl bootstrap failed for"
check "... the morning job's plist is back at 09:00 (the put-back's reload writes it before launchd refuses it again)" morning_at_nine
check "... and the wake was never moved (sudo was asked, pmset never ran)" bash -c '! grep -q "^pmset repeat" "$1"' _ "$SET_LOG"
rm -f "$WORK/settings-bootstrap-fails"
settings_cmd set BOSUN_START_TIME=09:00 >/dev/null 2>&1 # the jobs loaded again
touch "$WORK/sudo-fails"
check "when sudo needs a password for pmset, set is refused" fails_with "moving the Mac's wake needs your password: run this in Terminal" settings_cmd set BOSUN_START_TIME=10:00
check "... and bosun.env is unchanged" cmp -s "$SET_CFG/bosun.env" "$WORK/settings-before.env"
check "... as an object with --json too, exit 1" settings_exits 1 set BOSUN_START_TIME=10:00 --json
check "... that says why" settings_has 'd == {"contract": 1, "error": "moving the Mac'"'"'s wake needs your password: run this in Terminal"}'
touch "$WORK/sudo-lists-only"
: >"$SET_LOG"
check "when sudo -l allows pmset but running it needs a password, set fails (exit 1)" settings_exits 1 set BOSUN_START_TIME=10:00
check "... in one line saying bosun.env and the jobs were put back, and bosun.env is byte for byte as before" put_back_said "pmset did not take the new wake (pmset failed), so bosun.env and the jobs were put back as they were"
check "... having run pmset only through sudo -n, which never asks" bash -c 'grep -qx "sudo -n pmset repeat wakeorpoweron MTWRF 10:01:00" "$1" && ! grep -q "^sudo pmset" "$1"' _ "$SET_LOG"
check "... and the morning job is back at 09:00" morning_at_nine
rm -f "$WORK/sudo-lists-only"
touch "$WORK/sudo-allows-pmset"
: >"$SET_LOG"
check "with a NOPASSWD rule for just pmset, where sudo -v needs a password, set moves the wake" settings_exits 0 set BOSUN_START_TIME=10:00
check "... with sudo -n pmset, after the write" bash -c 'grep -qx "sudo -n pmset repeat wakeorpoweron MTWRF 10:01:00" "$1" && ! grep -q "^sudo -n -v" "$1" && grep -qx "BOSUN_START_TIME=\"10:00\"" "$2"' _ "$SET_LOG" "$SET_CFG/bosun.env"
rm -f "$WORK/sudo-allows-pmset" "$WORK/sudo-fails"
pmset_sched "wakepoweron at 10:01AM weekdays only"
settings_cmd set BOSUN_START_TIME=09:00 >/dev/null 2>&1

pmset_sched "wakepoweron at 6:30AM every day"
: >"$SET_LOG"
check "with another app's wake, set --json changes bosun.env" settings_exits 0 set BOSUN_DAYS=MTWRFS --json
check "... and says the other wake was left as it is, which pmset never touched" bash -c 'python3 -c "import json, sys; d = json.load(open(sys.argv[1])); sys.exit(0 if d[\"changed\"] == {\"BOSUN_DAYS\": [\"MTWRF\", \"MTWRFS\"]} and \"left as it is\" in d[\"notes\"][0] else 1)" "$1" && ! grep -q "pmset repeat" "$2"' _ "$WORK/settings.out" "$SET_LOG"
replaced_wake() { settings_cmd set BOSUN_DAYS=MTWRFSU --replace-wake >/dev/null 2>&1 && grep -qx "sudo -n pmset repeat wakeorpoweron MTWRFSU 09:01:00" "$SET_LOG"; }
check "... and with --replace-wake it is replaced" replaced_wake
rm -f "$SET_SCHED"

hold_lock "$SET_STATE/update.lock" settings-update
check "while bosun update runs, set is refused, with exit 1" fails_with_status 1 "bosun update is running" settings_cmd set BOSUN_UPDATE_CHANNEL=main
check "... and bosun.env unchanged" bash -c '! grep -q BOSUN_UPDATE_CHANNEL "$1"' _ "$SET_CFG/bosun.env"
release_lock settings-update

# bosun init holds its lock while it runs (one_at_a_time init), and writes bosun.env from what it read at its start
hold_lock "$SET_STATE/init.lock" settings-init
check "while bosun init runs, set is refused" fails_with "bosun init or another bosun settings set is running" settings_cmd set BOSUN_UPDATE_CHANNEL=main
check "... with exit 1, and with --json as the refusal object" settings_exits 1 set BOSUN_UPDATE_CHANNEL=main --json
check "... naming the wizard" settings_has 'list(d) == ["contract", "error"] and "bosun init" in d["error"]'
check "... and bosun.env unchanged" bash -c '! grep -q BOSUN_UPDATE_CHANNEL "$1"' _ "$SET_CFG/bosun.env"
release_lock settings-init
# set's own hold (lock_here, the shell holding it on fd 9), in a shell that then waits in its place, keeping fd 9,
# until the test removes its file (5 minutes at most, only so that a run cut short leaves no holder)
: >"$WORK/settings-lock-here.hold"
BOSUN_CONFIG_DIR="$SET_CFG" BOSUN_STATE_DIR="$SET_STATE" BOSUN_LOG_DIR="$SET_STATE/logs" bash -c \
  'source "$1/lib/common.sh"; load_config; lock_here init 0 && echo held &&
    exec bash -c "for _ in \$(seq 1 3000); do [ -e \"\$1\" ] || exit 0; sleep 0.1; done" _ "$2"' _ "$ROOT" "$WORK/settings-lock-here.hold" >"$WORK/settings-lock-here.out" &
set_init_holder=$!
bg_started "$set_init_holder"
wait_for 30 grep -q held "$WORK/settings-lock-here.out"
check "while set holds init's lock, bosun init is refused at once" fails_with "bosun init is already running (in another terminal?), or bosun settings set is" env BOSUN_CONFIG_DIR="$SET_CFG" BOSUN_STATE_DIR="$SET_STATE" BOSUN_LOG_DIR="$SET_STATE/logs" "$BOSUN" init --answers /dev/null
check "... and so is another set" fails_with "bosun init or another bosun settings set is running" settings_cmd set BOSUN_UPDATE_CHANNEL=main
rm -f "$WORK/settings-lock-here.hold"
bg_done "$set_init_holder"

check "a bad value is refused, naming the setting, with exit 2 (quietly: not an alert, below)" fails_with_status 2 "BOSUN_START_TIME" settings_cmd set BOSUN_START_TIME=25:00
# (An unknown key is refused alike, settings.py's checks: test_settings.py; the --json refusal object is above.)
: >"$SET_LOG"
check "set BOSUN_UPDATE_CHANNEL=main succeeds" settings_exits 0 set BOSUN_UPDATE_CHANNEL=main
check "... writes it and reloads no job" bash -c 'grep -qx "BOSUN_UPDATE_CHANNEL=\"main\"" "$2" && ! grep -q "^launchctl" "$1"' _ "$SET_LOG" "$SET_CFG/bosun.env"
# (A new BOSUN_AWAKE_UNTIL moves learning's time with it: test_settings.py, in the text set writes.)
check "a refusal is never logged as an error (the watchdog alerts on those)" bash -c '! grep -rqE "25:00|bosun update is running|needs your password" "$1/logs" 2>/dev/null' _ "$SET_STATE"

# A running keep-awake follows BOSUN_AWAKE_UNTIL (follow_keep_awake). caffeinate's stand-in lives for its -t
# seconds as a process ps names caffeinate (a link to sleep, as the quit section's), keeping nothing awake. The
# window is every day from an hour ago, so that now is in it at any hour.
mkdir -p "$WORK/settings-caffeinate"
ln -sf /bin/sleep "$WORK/settings-caffeinate/caffeinate"
cat >"$HOME/.local/bin/caffeinate" <<EOF
#!/bin/bash
seconds=0
while [ \$# -gt 0 ]; do
  [ "\$1" = -t ] && seconds="\$2"
  shift
done
exec "$WORK/settings-caffeinate/caffeinate" "\$seconds"
EOF
chmod +x "$HOME/.local/bin/caffeinate"
set_keep_awake_pid() { cat "$SET_STATE/keep-awake-$(settings_session).pid" 2>/dev/null; }
settings_session() { BOSUN_CONFIG_DIR="$SET_CFG" bash -c 'source "$1/lib/common.sh"; load_config; echo "$BOSUN_SESSION_NAME"' _ "$ROOT"; }
is_caffeinate() { ps -p "$1" -o comm= 2>/dev/null | grep -q caffeinate; } # <pid>
own_group() { [ "$(ps -o pgid= -p "$1" | tr -d ' ')" = "$1" ]; }          # <pid>: it leads a process group of its own
settings_cmd set BOSUN_DAYS=MTWRFSU BOSUN_START_TIME="$(date -v-60M +%H:%M)" BOSUN_AWAKE_UNTIL="$(date -v+120M +%H:%M)" >/dev/null 2>&1
BOSUN_CONFIG_DIR="$SET_CFG" BOSUN_STATE_DIR="$SET_STATE" BOSUN_LOG_DIR="$SET_STATE/logs" bash -c \
  'source "$1/lib/common.sh"; load_config; hold_keep_awake 600' _ "$ROOT" >/dev/null 2>&1
set_old_caffeinate="$(set_keep_awake_pid)"
check "a keep-awake held (the stand-in caffeinate runs, as the morning starts it)" is_caffeinate "$set_old_caffeinate"
check "... in a process group of its own" own_group "$set_old_caffeinate"
# Run as the menu bar app runs it: in a process group of its own, which is killed whole once it is done (as on a
# timeout); what the command started and left running must not be in it.
python3 -I -c 'import os, signal, subprocess, sys
child = subprocess.Popen(sys.argv[1:], process_group=0, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
status = child.wait()
try:
    os.killpg(child.pid, signal.SIGKILL)
except ProcessLookupError:
    pass
sys.exit(status)' env BOSUN_CONFIG_DIR="$SET_CFG" BOSUN_STATE_DIR="$SET_STATE" BOSUN_LOG_DIR="$SET_STATE/logs" \
  BOSUN_LAUNCHD_LABEL="$SET_LABEL" "$BOSUN" settings set BOSUN_AWAKE_UNTIL="$(date -v+180M +%H:%M)"
set_status=$?
set_new_caffeinate="$(set_keep_awake_pid)"
check "set BOSUN_AWAKE_UNTIL three hours on, with a keep-awake held, succeeds" test "$set_status" = 0
check "... and starts a new caffeinate, recorded in the pid file" bash -c '[ -n "$1" ] && [ "$1" != "$2" ]' _ "$set_new_caffeinate" "$set_old_caffeinate"
check "... which ends where the new until does (about three hours: -t, from its command line)" bash -c 'left="$(ps -o args= -p "$1" | awk "{ print \$NF }")"; [ "$left" -gt 10000 ] && [ "$left" -le 10800 ]' _ "$set_new_caffeinate"
check "... and outlived the command's process group, killed whole after it (the app's timeout)" is_caffeinate "$set_new_caffeinate"
check "... being in a process group of its own" own_group "$set_new_caffeinate"
check "... the old caffeinate ended, by its recorded pid" wait_for 30 bash -c '! ps -p "$1" >/dev/null 2>&1' _ "$set_old_caffeinate"
check "... and the record bosun status reads names the new one" grep -q "\"pid\": $set_new_caffeinate}" "$SET_STATE/keep-awake-$(settings_session).json"
check "set BOSUN_AWAKE_UNTIL to half an hour ago releases the keep-awake: now is outside every window" settings_exits 0 set BOSUN_AWAKE_UNTIL="$(date -v-30M +%H:%M)"
check "... its caffeinate ended" wait_for 30 bash -c '! ps -p "$1" >/dev/null 2>&1' _ "$set_new_caffeinate"
check "... and its files gone" bash -c '[ ! -e "$1/keep-awake-$2.pid" ] && [ ! -e "$1/keep-awake-$2.json" ]' _ "$SET_STATE" "$(settings_session)"
kill "$set_old_caffeinate" "$set_new_caffeinate" 2>/dev/null # by their pids, in case a check above failed
rm -f "$HOME"/Library/LaunchAgents/"$SET_LABEL"*.plist
plain_fake launchctl 113
plain_fake pmset 0
plain_fake sudo 1
plain_fake caffeinate 0

section console "console (local server on a free port, fixture ledger)" covers 'libexec/bosun-console,libexec/bosun-kg,console/**,tests/fixtures/console/**,tests/fixtures/knowledge/**,lib/console_*.py,libexec/bosun-ledger'
CONSOLE_STATE="$WORK/console-state"
mkdir -p "$CONSOLE_STATE"
cp "$ROOT/tests/fixtures/console/acme-ledger.json" "$CONSOLE_STATE/ledger.json"
# A fictional site for the Library (a fake secret in bosun.env, a site task type, files it must
# never list, a memory folder with a symlink out of it) and learning runs (one report outside).
CONSOLE_CONFIG="$WORK/console-config"
CONSOLE_MEMORY="$WORK/console-memory"
mkdir -p "$CONSOLE_CONFIG/tasks" "$CONSOLE_MEMORY" "$CONSOLE_STATE/learn/runs/20261020-183000" "$WORK/elsewhere"
printf 'BOSUN_SESSION_NAME="smoke"\nACME_API_TOKEN="fake-token-value-0000"\nBOSUN_ALERT_URL=https://hooks.example.com/services/T0/B0/X0\n' >"$CONSOLE_CONFIG/bosun.env"
printf -- '---\ntitle: Site review\nkinds: review-request\nmay: comment\nnever: approve\nfollowup: none\n---\nSite rules.\n' >"$CONSOLE_CONFIG/tasks/review.md"
echo "acme" >"$CONSOLE_CONFIG/forbidden-terms.txt"
echo "old" >"$CONSOLE_CONFIG/policy.md.bak-1"
echo "# Deploys" >"$CONSOLE_MEMORY/deploys.md"
echo "outside" >"$WORK/elsewhere/secret.md"
ln -s "$WORK/elsewhere/secret.md" "$CONSOLE_MEMORY/escape.md"
echo "# Learning report" >"$CONSOLE_STATE/learn/runs/20261020-183000/report.md"
echo "# Not ours" >"$WORK/elsewhere/report.md"
printf '{"runs": [{"started": "2026-10-20T18:30:00+00:00", "outcome": "ok", "findings": 0, "run_dir": "%s"}, {"started": "2026-10-19T18:30:00+00:00", "outcome": "ok", "run_dir": "%s"}]}' \
  "$CONSOLE_STATE/learn/runs/20261020-183000" "$WORK/elsewhere" >"$CONSOLE_STATE/learn/state.json"
# A scratch knowledge base for the Knowledge view (the fixture, with its own git history), and a
# restricted identifier the console must never show.
BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" "$BOSUN" kg import "$KG_FIXTURE" --apply --auto-confirm >/dev/null 2>&1
BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" "$BOSUN" kg identify person/dana slack U0000000077 >/dev/null 2>&1
BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" BOSUN_CONSOLE_MEMORY_DIRS="$CONSOLE_MEMORY" \
  "$BOSUN" console --port 0 >"$WORK/console.out" 2>"$WORK/console.err" &
CONSOLE_PID=$! # the cleanup trap stops it if a check below aborts the run
check "it prints its URL with a token" console_started "$WORK/console.out" "$WORK/console.err"
CONSOLE_URL="$(grep -o 'http://[^ ]*' "$WORK/console.out")"
CONSOLE_BASE="${CONSOLE_URL%%/\?*}"
CONSOLE_PORT="${CONSOLE_BASE##*:}"
CONSOLE_COOKIE="bosun_console_$CONSOLE_PORT=${CONSOLE_URL##*t=}"
http_status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
check "the shell page is served with the CSP header" bash -c '
  curl -sfi "$1/" | grep -qi "^content-security-policy: default-src .self.;"' _ "$CONSOLE_BASE"
check "the token becomes an HttpOnly, SameSite=Strict cookie, and the redirect drops it" bash -c '
  out="$(curl -si "$1")"
  grep -q "^HTTP/1.[01] 303" <<<"$out" && grep -qi "^location: /.$" <<<"$out" &&
    grep -qi "^set-cookie: .*; HttpOnly; SameSite=Strict" <<<"$out"' _ "$CONSOLE_URL"
check "/api/version is valid JSON with the item count" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/version" | jq -e ".ledger.items == 19 and .ledger.version == 1"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/items lists every item, and its open ones as ledger list --open does" bash -c '
  open="$(BOSUN_STATE_DIR="$3" "$4" ledger list --open | wc -l | tr -d " ")" &&
    curl -sf -H "Cookie: $2" "$1/api/items?group=all" | jq -e ".count == 19 and .total == 19" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/items?group=active" | jq -e --argjson open "$open" ".count == \$open"' \
  _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$CONSOLE_STATE" "$BOSUN"
check "/api/items filters and searches" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/items?kind=outreach&group=all&person=dana&q=credit+notes" |
    jq -e "[.rows[].id] == [\"out:task:2026-10-20/veembc/q0kge7\"]"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/items/<id> takes the URL-encoded id" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/items/mypr%3Aacme%2Fexporter%23102" |
    jq -e ".id == \"mypr:acme/exporter#102\" and (.links | map(.label) == [\"blocks\"])"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/needs-you lists the proposals, proposed outreach and mismatches" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/needs-you" |
    jq -e "(.proposals | length) == 2 and (.outreach | length) == 1 and (.mismatches | length) == 3"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/sessions and /api/health answer JSON with no session running" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/sessions" | jq -e "[.windows[] | select(.window)] == [] and .error == null" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/health" | jq -e ".watchdog.checks | length > 0"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
touch "$WORK/before-stats"
check "/api/stats has every metric for the period asked, 7 days without one" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/stats?days=30" | jq -e "
    .days == 30 and (.throughput.days | length) == 30 and .periods == [7, 30, 90] and
    (.prs | has(\"median_open_to_merge_seconds\")) and (.reviews | has(\"median_turnaround_seconds\")) and
    (.tasks | has(\"per_window\")) and (.outreach | has(\"nudges_per_reply\")) and
    (.people.recipients | has(\"top\")) and (.bosun.spend | length) == 30 and (.odd_times | type) == \"number\"" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/stats" | jq -e ".days == 7"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/stats refuses another period: 400" bash -c '
  for query in days=8 days=365 "days=7&days=30" days=week; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" -H "Cookie: $2" "$1/api/stats?$query")" = 400 ] || exit 1
  done' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/learn lists the runs, a report key only for the one under the learn folder, and the learn: items" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/learn" | jq -e "
    (.runs | length) == 2 and (.runs[0].report | test(\"^[0-9a-f]{16}\$\")) and .runs[1].report == null and
    ([.items[].id] == [\"learn:2026-10-20/1\"]) and (.items[0].pending == true)" >/dev/null &&
    key="$(curl -sf -H "Cookie: $2" "$1/api/learn" | jq -r ".runs[0].report")" &&
    curl -sf -H "Cookie: $2" "$1/api/learn/report/$key" | jq -e ".body == \"# Learning report\n\""' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/library lists the repo, the site overlay with its effective task type, and memory" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/library" | jq -e "
    [.groups[].entries[]] as \$all | (\$all | map(.name)) as \$names |
    (\$names | index(\"orchestrator/policy.md\")) and (\$names | index(\"bosun.env\")) and
    (\$names | index(\"deploys.md\")) and (\$names | index(\"escape.md\") | not) and
    (\$names | map(select(test(\"forbidden|bak\"))) == []) and
    (\$all | map(select(.name == \"tasks/review.md\"))[0].badge == \"effective\") and
    (\$all | map(select(.name == \"orchestrator/tasks/review.md\"))[0].badge == \"replaced by site\")"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/library/<key> masks the site config: no token, no webhook URL" bash -c '
  key="$(curl -sf -H "Cookie: $2" "$1/api/library" | jq -r "[.groups[].entries[] | select(.name == \"bosun.env\")][0].key")" &&
    body="$(curl -sf -H "Cookie: $2" "$1/api/library/$key")" &&
    jq -e ".redacted == true and (.body | contains(\"BOSUN_SESSION_NAME\"))" <<<"$body" >/dev/null &&
    ! grep -q "fake-token-value\|hooks.example.com" <<<"$body"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "files only by a known key: anything else is 404" bash -c '
  for path in /api/library/0123456789abcdef /api/library/..%2F..%2Fetc%2Fpasswd /api/library/bosun.env /api/learn/report/0123456789abcdef; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" -H "Cookie: $2" "$1$path")" = 404 ] || exit 1
  done' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "... and reading the stats wrote nothing in the state directory" bash -c '[ -z "$(find "$1" -newer "$2")" ]' _ "$CONSOLE_STATE" "$WORK/before-stats"
check "XSS strings come back as text, and a javascript: URL is no link" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/items/mypr%3Aacme%2Fexporter%23103" | jq -e ".title == \"Header fix <script>alert(1)</script>\"" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/items/slack%3AC0000000004%2F1700000004.000004" |
    jq -e ".url == null and .url_text == \"javascript:alert(document.cookie)\" and .anchors == []"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "/api/knowledge: the four axis cards, the counts and today's review batch" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/knowledge" | jq -e "
    (.cards | map(.title)) == [\"People\", \"Process\", \"Product\", \"Systems\"] and
    .totals.entities == 15 and .totals.facts == 20 and .review.groups == 3"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
kg_key() { # <entity id>: its opaque key, from the console's search
  curl -sf -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/knowledge?q=${1#*/}" |
    jq -r --arg id "$1" '[.search.entities[] | select(.id == $id)][0].key'
}
check "an entity page by its opaque key: a process with its ordered flow and its graph" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/knowledge/entity/$3" | jq -e "
    .entity.name == \"Release process\" and ([.flow.steps[].step.name] == [\"Merge to main\", \"Smoke tests\", \"Gradual rollout\"]) and
    (.graph.nodes | length) > 1"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$(kg_key process/release)"
check "a person page says how to delete them, and no knowledge view shows a restricted identifier" bash -c '
  page="$(curl -sf -H "Cookie: $2" "$1/api/knowledge/entity/$3")" &&
    jq -e ".privacy | contains(\"bosun kg forget person/dana\")" <<<"$page" >/dev/null &&
    ! grep -q U0000000077 <<<"$page$(curl -sf -H "Cookie: $2" "$1/api/knowledge?q=dana")$(curl -sf -H "Cookie: $2" "$1/api/knowledge/review")"' \
  _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$(kg_key person/dana)"
check "/api/knowledge/briefing and /constellation: the headline, coverage, a placeholder for rediscovery, every entity" bash -c '
  curl -sf -H "Cookie: $2" "$1/api/knowledge/briefing" | jq -e "
    .totals.facts == 20 and (.coverage | map(.card)) == [\"people\", \"process\", \"product\", \"systems\"] and
    (.captured.new | length) == 30 and .rediscovery.measured == false and (.connected | length) > 0" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/knowledge/constellation" | jq -e "
      (.nodes | length) == 15 and .more == 0 and ([.edges[] | select(.from >= 15 or .to >= 15)] == [])" >/dev/null &&
    for path in briefing constellation; do
      [ "$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Cookie: $2" "$1/api/knowledge/$path")" = 405 ] &&
        [ "$(curl -s -o /dev/null -w "%{http_code}" "$1/api/knowledge/$path")" = 401 ] || exit 1
    done' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
check "entities only by a known key: an id, a traversal or an unknown key is 404" bash -c '
  for path in 0123456789abcdef process/release process%2Frelease ..%2Fentities.jsonl; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" -H "Cookie: $2" "$1/api/knowledge/entity/$path")" = 404 ] || exit 1
  done' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE"
KG_REVIEW="$(curl -sf -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/knowledge/review")"
KG_FACT="$(jq -r '[.groups[].facts[] | select(.decidable)][0].id' <<<"$KG_REVIEW")"
KG_POST=(env BASE="$CONSOLE_BASE" COOKIE="$CONSOLE_COOKIE" CSRF="$(jq -r .csrf <<<"$KG_REVIEW")" OUT="$WORK/kg-post.json"
LOG="$CONSOLE_CONFIG/knowledge/facts.jsonl" KGDIR="$CONSOLE_CONFIG/knowledge" FACT="$KG_FACT"
OTHER="$(jq -r '[.groups[].facts[] | select(.decidable)][1].id' <<<"$KG_REVIEW")"
BODY="{\"action\": \"confirm\", \"facts\": [\"$KG_FACT\"]}")
# post [curl options...] inside these checks: POST the decision $BODY (confirm $FACT), print the status
KG_POST_FN='post() { curl -s -o "$OUT" -w "%{http_code}" -X POST --data "$BODY" "$@" "$BASE/api/knowledge/review"; }'
check "/api/knowledge/review gives the batch, lettered like kg batch, and a CSRF token" bash -c '
  jq -e "(.csrf | length) > 20 and .groups[0].facts[0].ref == \"1a\" and .summary.facts == 14" <<<"$1"' _ "$KG_REVIEW"
check "a GET with a forged decision in its query writes nothing" "${KG_POST[@]}" bash -c '
  before="$(cksum <"$LOG")" && curl -sf -H "Cookie: $COOKIE" "$BASE/api/knowledge/review?action=confirm&facts=$FACT" >/dev/null &&
    [ "$(cksum <"$LOG")" = "$before" ]'
check "a decision without the cookie, the CSRF token, this Origin or JSON is refused, and changes nothing" "${KG_POST[@]}" bash -c "$KG_POST_FN"'
  before="$(cksum <"$LOG")" json="Content-Type: application/json" token="X-Bosun-CSRF: $CSRF"
  [ "$(post -H "Origin: $BASE" -H "$json" -H "$token")" = 401 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "$json")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "$json" -H "X-Bosun-CSRF: guess")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "$json" -H "$token")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: http://evil.example.com" -H "$json" -H "$token")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "Sec-Fetch-Site: cross-site" -H "$json" -H "$token")" = 403 ] &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "Content-Type: text/plain" -H "$token")" = 415 ] &&
    BODY="{\"action\": \"confirm\", \"facts\": [\"f1\"]}" &&
    [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "$json" -H "$token")" = 409 ] &&
    [ "$(cksum <"$LOG")" = "$before" ]'
KG_STAMP="$(curl -sf -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/version" | jq -r .knowledge.stamp)"
check "a confirm round trip: confirmed, recorded as reviewed in the console, one commit" "${KG_POST[@]}" bash -c "$KG_POST_FN"'
  [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "Content-Type: application/json" -H "X-Bosun-CSRF: $CSRF")" = 200 ] &&
    jq -e ".confirmed == 1 and .left_proposed == 0" "$OUT" >/dev/null &&
    tail -1 "$LOG" | jq -e --arg f "$FACT" ".id == \$f and .event == \"confirmed\" and .set.reviewed_by.ref == \"console\"" >/dev/null &&
    curl -sf -H "Cookie: $COOKIE" "$BASE/api/knowledge/review" | jq -e --arg f "$FACT" "[.groups[].facts[] | select(.id == \$f and .decidable)] == []" >/dev/null &&
    [ "$(env -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE git -C "$KGDIR" log -1 --format=%s)" = "kg console review: confirm $FACT" ]'
check "/api/version carries the knowledge base's own stamp, changed by the decision" bash -c '
  [ "$(curl -sf -H "Cookie: $2" "$1/api/version" | jq -r .knowledge.stamp)" != "$3" ]' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$KG_STAMP"
check "defer leaves the fact proposed and writes nothing" "${KG_POST[@]}" bash -c "$KG_POST_FN"'
  before="$(cksum <"$LOG")" BODY="{\"action\": \"defer\", \"facts\": [\"$OTHER\"]}"
  [ "$(post -H "Cookie: $COOKIE" -H "Origin: $BASE" -H "Content-Type: application/json" -H "X-Bosun-CSRF: $CSRF")" = 200 ] &&
    jq -e ".left_proposed == 1" "$OUT" >/dev/null && [ "$(cksum <"$LOG")" = "$before" ]'
check "an unknown item: 404" test "$(http_status -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/items/mypr%3Aacme%2Fexporter%23999")" = 404
check "a query it cannot use: 400" test "$(http_status -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/items?group=mine")" = 400
check "the ledger endpoints need the cookie: 401" bash -c '
  for path in /api/items /api/items/mypr%3Aacme%2Fexporter%23102 /api/needs-you /api/sessions /api/health /api/stats /api/learn /api/library; do
    [ "$(curl -s -o /dev/null -w "%{http_code}" "$1$path")" = 401 ] || exit 1
  done' _ "$CONSOLE_BASE"
check "no token: 401" test "$(http_status "$CONSOLE_BASE/api/version")" = 401
check "a wrong Host: 403" test "$(http_status -H 'Host: evil.example.com' "$CONSOLE_BASE/")" = 403
check "POST: 405" test "$(http_status -X POST -H "Cookie: $CONSOLE_COOKIE" "$CONSOLE_BASE/api/version")" = 405
check "path traversal: 404" test "$(http_status --path-as-is "$CONSOLE_BASE/../lib/ledger.py")" = 404
check "a ledger write shows in /api/version" bash -c '
  BOSUN_STATE_DIR="$3" "$4" ledger upsert "mypr:acme/exporter#105" --kind my-pr --title "Credit notes" >/dev/null &&
    curl -sf -H "Cookie: $2" "$1/api/version" | jq -e ".ledger.items == 20"' _ "$CONSOLE_BASE" "$CONSOLE_COOKIE" "$CONSOLE_STATE" "$BOSUN"
check "the log has the refusals, but no token, query, item id or ledger text" bash -c '
  grep -q "POST /api/version 405" "$1" && grep -q "GET /api/items/<id> 404" "$1" &&
    ! grep -qE "t=|acme|Credit|mine" "$1"' _ "$WORK/console.err"
# (Another state folder: a console already serving from this one is the one a hand run prints the address of.)
check "a port in use is a clear error" fails_with "use --port N, or --port 0 for a free one" \
  env BOSUN_STATE_DIR="$WORK/console-state-other" "$BOSUN" console --port "$CONSOLE_PORT"
check "it refuses a non-loopback address" fails_with "only listens on 127.0.0.1, not 0.0.0.0" \
  env BOSUN_STATE_DIR="$CONSOLE_STATE" "$BOSUN" console --port 0 --host 0.0.0.0
kill "$CONSOLE_PID" 2>/dev/null
check "a TERM stops it cleanly" wait_for 30 grep -q "bosun console: stopped" "$WORK/console.out"
check "... and it exits" wait_for 30 bash -c '! kill -0 "$1" 2>/dev/null' _ "$CONSOLE_PID"
CONSOLE_PID=""
# console_token_of_start [args...]: start the console on the same state, print its URL's token, stop it.
console_token_of_start() {
  local out="$WORK/console-again.out" pid
  BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" \
    "$BOSUN" console --port 0 "$@" >"$out" 2>"$WORK/console-again.err" &
  pid=$!
  console_started "$out" "$WORK/console-again.err" >/dev/null && grep -o '?t=[^ ]*' "$out" | cut -c4-
  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
}
FIRST_TOKEN="${CONSOLE_URL##*\?t=}"
check "--url with no console running says so, and fails" fails_with "the console is not running" \
  env BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" "$BOSUN" console --url
# The console as the job runs it (--job) serves while a hand run asks for its address.
BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" \
  "$BOSUN" console --job --port 0 >"$WORK/console-job.out" 2>"$WORK/console-job.err" &
CONSOLE_PID=$!
console_started "$WORK/console-job.out" "$WORK/console-job.err" >/dev/null
JOB_URL="$(grep -o 'http://[^ ]*' "$WORK/console-job.out")"
console_here() { BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" "$BOSUN" console "$@"; }
check "--url prints the running console's address from the stored token and its port, and only that" test "$(console_here --url 2>&1)" = "$JOB_URL"
check "... with the token the console stored" contains "$(console_here --url)" "/?t=$FIRST_TOKEN"
# BOSUN_TRIGGER=launchd is what the morning job's shells inherit: it does not make a hand run the job.
check "a hand run while it serves prints that address, and exits 0 at once, also under the morning job's variable" bash -c 'source "$1/lib/common.sh"; out="$(run_with_timeout 20 "${@:3}" 2>&1)" && grep -q "bosun console: $2" <<<"$out"' _ "$ROOT" "$JOB_URL" env BOSUN_TRIGGER=launchd BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" "$BOSUN" console --port 0
check "... it does not rotate the token under the running console" fails_with "already running" console_here --new-token
check "... and the console is still the one serving" curl -sf -o /dev/null "${JOB_URL%%/\?*}/"
kill "$CONSOLE_PID" 2>/dev/null
check "... --url fails again once it stops" wait_for 30 bash -c '! env BOSUN_STATE_DIR="$1" BOSUN_CONFIG_DIR="$2" "$3" console --url >/dev/null 2>&1' _ "$CONSOLE_STATE" "$CONSOLE_CONFIG" "$BOSUN"
CONSOLE_PID=""
check "a restart keeps the token, so an open tab stays signed in" test "$(console_token_of_start)" = "$FIRST_TOKEN"
check "... stored 0600 in a 0700 folder" bash -c '
  [ "$(stat -c %a "$1/console/token" 2>/dev/null || stat -f %Lp "$1/console/token")" = 600 ] &&
    [ "$(stat -c %a "$1/console" 2>/dev/null || stat -f %Lp "$1/console")" = 700 ]' _ "$CONSOLE_STATE"
check "--new-token rotates it" bash -c '[ -n "$1" ] && [ "$1" != "$2" ]' _ "$(console_token_of_start --new-token)" "$FIRST_TOKEN"
# The job waits for a console run by hand (its port and running.lock), and serves once that one ends.
BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" \
  "$BOSUN" console --port 0 >"$WORK/console-hand.out" 2>"$WORK/console-hand.err" &
HAND_PID=$!
console_started "$WORK/console-hand.out" "$WORK/console-hand.err" >/dev/null
BOSUN_STATE_DIR="$CONSOLE_STATE" BOSUN_CONFIG_DIR="$CONSOLE_CONFIG" \
  "$BOSUN" console --job --port 0 >"$WORK/console-job2.out" 2>"$WORK/console-job2.err" &
CONSOLE_PID=$!
check "the job started while a console run by hand serves says it waits for it" wait_for 30 grep -q "the job waits for it to end" "$WORK/console-job2.out"
check "... and does not exit for launchd to start it again" kill -0 "$CONSOLE_PID"
check "... nor serve yet" bash -c '! grep -q "^bosun console: http" "$1"' _ "$WORK/console-job2.out"
kill "$HAND_PID" 2>/dev/null
check "... and serves once the hand-run console ends" console_started "$WORK/console-job2.out" "$WORK/console-job2.err"
kill "$CONSOLE_PID" 2>/dev/null
wait "$CONSOLE_PID" 2>/dev/null
CONSOLE_PID=""

always_run
# launchd has a job while it has its plist; pmset's schedule is what a test leaves in a file; sudo only
# reaches these.
cat >"$HOME/.local/bin/launchctl" <<EOF
#!/bin/bash
echo "launchctl \$*" >>"$WORK/system.log"
[ ! -e "$WORK/launchctl-fails" ] || [ "\$1" != bootstrap ] || exit 5
case "\$1" in
print) # loaded where its plist is; running while its label is in the file the suite writes (an inner line says
  # running for every job, as launchd's lists of processes do: only the top-level one is the job's)
  [ -e "\$HOME/Library/LaunchAgents/\${2##*/}.plist" ] || exit 113
  if grep -qx "\${2##*/}" "$WORK/job-running" 2>/dev/null; then printf '\tstate = running\n'; else printf '\tstate = not running\n'; fi
  printf '\tprocess = {\n\t\tstate = running\n\t}\n'
  ;;
kickstart) # takes it, a running job too (without -k it leaves that one alone); fails when the suite says so
  [ ! -e "$WORK/kickstart-fails" ] || { echo "Could not kickstart service: 113: Could not find service" >&2; exit 113; }
  ;;
esac
EOF
cat >"$HOME/.local/bin/pmset" <<EOF
#!/bin/bash
echo "pmset \$*" >>"$WORK/system.log"
if [ "\$1 \$2" = "-g sched" ] && [ -s "$WORK/pmset-sched" ]; then cat "$WORK/pmset-sched"; fi
if [ "\$1 \$2" = "repeat cancel" ]; then rm -f "$WORK/pmset-sched"; fi
exit 0
EOF
cat >"$HOME/.local/bin/sudo" <<EOF
#!/bin/bash
echo "sudo \$*" >>"$WORK/system.log"
[ "\$1" = -v ] && exit 0 # the password asked for ahead: always given here
[ "\$1" = -n ] && shift
exec "\$@"
EOF
chmod +x "$HOME/.local/bin/launchctl" "$HOME/.local/bin/pmset" "$HOME/.local/bin/sudo"
# install.sh refuses to run without Claude Code on PATH: the fake, not one this machine may have.
ln -sf "$WORK/fake-claude" "$HOME/.local/bin/claude"
export BOSUN_LAUNCHD_LABEL="com.bosun.smoke-test"
INSTALL_CFG="$WORK/install-config"
PLIST="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.plist"
# Never let a test touch the real launchd, pmset or sudo: only run if the fakes come first on PATH.
if [ "$(bash -c 'source "$1/lib/common.sh"; command -v launchctl; command -v pmset; command -v sudo' _ "$ROOT" | tr '\n' ' ')" = "$HOME/.local/bin/launchctl $HOME/.local/bin/pmset $HOME/.local/bin/sudo " ]; then
  section install "install and uninstall (fake launchctl and pmset, test label)" needs ledger,knowledge covers 'install.sh,templates/**,examples/**,tests/fixtures/knowledge/**,libexec/bosun-console,libexec/bosun-doctor,libexec/bosun-kg,libexec/bosun-morning,libexec/bosun-ledger,lib/menu_status_live.py'
  touch "$WORK/launchctl-fails"
  check "an install that stops at the launchd jobs fails" fails_with "could not load the launchd jobs" env BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" --example generic
  check "... and leaves ~/.local/bin/bosun unlinked, so the install line takes the install for unfinished and runs init again" test ! -e "$HOME/.local/bin/bosun"
  rm -f "$WORK/launchctl-fails" "$HOME"/Library/LaunchAgents/"$BOSUN_LAUNCHD_LABEL"*.plist
  check "install checks Claude Code as BOSUN_CLAUDE_BIN names it" fails_with "missing: $WORK/no-claude/claude" env BOSUN_CONFIG_DIR="$INSTALL_CFG" BOSUN_CLAUDE_BIN="$WORK/no-claude/claude" "$ROOT/install.sh" --example generic
  BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" --example generic >/dev/null 2>&1
  check "install copies the example config" bash -c '[ -f "$1/bosun.env" ] && [ -f "$1/sessions.yml" ] && [ -f "$1/setup.sh" ]' _ "$INSTALL_CFG"
  check "install links bosun into ~/.local/bin" test "$(readlink "$HOME/.local/bin/bosun")" = "$BOSUN"
  check "install writes a valid launchd job" plutil -lint -s "$PLIST"
  check "install loads it with launchctl" grep -q "launchctl bootstrap gui/$(id -u) $PLIST" "$WORK/system.log"
  check "install never changes pmset without --with-wake" bash -c '! grep -q "pmset repeat" "$1"' _ "$WORK/system.log"
  stamped_now() { # <stamp file>...: each holds a time of the last ten minutes, as reload_job writes it
    python3 -I -c '
import datetime, sys
for path in sys.argv[1:]:
    stamp = datetime.datetime.strptime(open(path).read().strip(), "%Y-%m-%d %H:%M:%S")
    assert abs((datetime.datetime.now() - stamp).total_seconds()) <= 600, path' "$@"
  }
  check "loading the morning job and the watchdog stamps when each was loaded, which the watchdog goes by" stamped_now "$BOSUN_STATE_DIR/loaded-morning" "$BOSUN_STATE_DIR/loaded-watchdog"
  echo "2026-01-05 05:00:00" >"$BOSUN_STATE_DIR/loaded-morning"
  echo "# kept" >>"$INSTALL_CFG/bosun.env"
  BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" >/dev/null 2>&1
  check "... a run that reloads nothing moves no stamp (a re-run on the day of a missed morning keeps its alarm)" grep -qx "2026-01-05 05:00:00" "$BOSUN_STATE_DIR/loaded-morning"
  check "a second install keeps the config" grep -qx "# kept" "$INSTALL_CFG/bosun.env"
  rm -f "$PLIST"
  cp -p "$HOME/.local/bin/sudo" "$WORK/sudo.ok"
  printf '#!/bin/sh\nexit 1\n' >"$HOME/.local/bin/sudo"
  check "... a wake that sudo refuses fails the install" fails_with "pmset failed" env BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" --with-wake
  check "... after the morning job was loaded again, and stamped" stamped_now "$BOSUN_STATE_DIR/loaded-morning"
  mv "$WORK/sudo.ok" "$HOME/.local/bin/sudo"
  # reload_changed_jobs on its own, with the install's config: a reload that keeps the job's time keeps its stamp
  # (the run due today stays owed), one that moves it stamps it, and the labels on stdout are only labels.
  reload_jobs() { env BOSUN_CONFIG_DIR="$INSTALL_CFG" "$@" bash -c 'source "$1/lib/common.sh"; load_config; reload_changed_jobs' _ "$ROOT"; }
  echo "2026-01-05 05:00:00" >"$BOSUN_STATE_DIR/loaded-morning"
  echo "<!-- touched -->" >>"$PLIST"
  out="$(reload_jobs)"
  check "a reload of the morning job that keeps its time keeps its stamp" bash -c 'grep -qx "$1" <<<"$2" && grep -qx "2026-01-05 05:00:00" "$3"' _ "$BOSUN_LAUNCHD_LABEL" "$out" "$BOSUN_STATE_DIR/loaded-morning"
  reload_jobs BOSUN_START_TIME=07:00 >/dev/null
  check "... and one that moves its time stamps it" stamped_now "$BOSUN_STATE_DIR/loaded-morning"
  reload_jobs BOSUN_START_TIME=07:00 >/dev/null # its days alone change next
  echo "2026-01-05 05:00:00" >"$BOSUN_STATE_DIR/loaded-morning"
  reload_jobs BOSUN_START_TIME=07:00 BOSUN_DAYS=MTWRFSU >/dev/null
  check "... and so does one that changes its days (today added after its time owes no run today)" stamped_now "$BOSUN_STATE_DIR/loaded-morning"
  echo "2026-01-05 05:00:00" >"$BOSUN_STATE_DIR/loaded-watchdog"
  echo "<!-- touched -->" >>"$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.watchdog.plist"
  reload_jobs >/dev/null
  check "... and a plist with no time of day it can read (the watchdog's interval) counts as moved: stamped" stamped_now "$BOSUN_STATE_DIR/loaded-watchdog"
  reload_jobs >/dev/null 2>&1 # back at its time
  ro_state="$WORK/read-only-state"
  mkdir -p "$ro_state/logs"
  chmod 555 "$ro_state"
  rm -f "$PLIST" # a fresh load, which stamps
  out="$(reload_jobs BOSUN_STATE_DIR="$ro_state" BOSUN_LOG_DIR="$ro_state/logs" 2>"$WORK/reload.err")"
  chmod 755 "$ro_state"
  check "a stamp that cannot be written is a line on stderr, never a label on stdout (which install.sh and bosun update read)" bash -c '! grep -q "WARN" <<<"$1" && grep -qx "$2" <<<"$1" && grep -q "WARN: could not write $3/loaded-morning" "$4"' _ "$out" "$BOSUN_LAUNCHD_LABEL" "$ro_state" "$WORK/reload.err"
  reload_jobs >/dev/null 2>&1 # back in the suite's own state folder
  WATCHDOG_PLIST="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.watchdog.plist"
  check "install writes the watchdog agent" plutil -lint -s "$WATCHDOG_PLIST"
  check "... and loads it" grep -q "launchctl bootstrap gui/$(id -u) $WATCHDOG_PLIST" "$WORK/system.log"
  LEARN_PLIST="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.learn.plist"
  check "install writes no learning agent by default" test ! -e "$LEARN_PLIST"
  # install.sh ends with bosun doctor (here with learning on): its lines are read from what the install printed.
  doctor_said="$(BOSUN_LEARN_ENABLED=1 BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" 2>&1)"
  check "with BOSUN_LEARN_ENABLED=1 it writes and loads one" bash -c 'plutil -lint -s "$1" && grep -q "launchctl bootstrap gui/$(id -u) $1" "$2"' _ "$LEARN_PLIST" "$WORK/system.log"
  check "doctor checks the learning agent once it is enabled" contains "$doctor_said" "ok    learning agent $BOSUN_LAUNCHD_LABEL.learn loaded"
  check "doctor checks the job that runs --now, which the app's Start kickstarts" contains "$doctor_said" "ok    launchd job $BOSUN_LAUNCHD_LABEL.now loaded"
  check "doctor checks the ledger against its journal" contains "$doctor_said" "ok    the ledger and its journal: ledger is valid"
  # (pmset's 0:01AM for 00:01 is init_plan.wake_command's, which settings.py's wake_owner asks: test_init_plan.py.)
  # Whose the wake is, as bosun settings judges it (settings.py wake-owner), for the settings in force.
  printf 'Repeating power events:\n  wakepoweron at 8:01AM weekdays only\n' >"$WORK/pmset-sched"
  ours_env=(BOSUN_DAYS=MTWRF BOSUN_START_TIME=08:00 BOSUN_WAKE_DELAY_MINUTES=1)
  # (with learning on, as the install above loaded it: the check below removes it, and says why)
  out="$(env "${ours_env[@]}" BOSUN_LEARN_ENABLED=1 BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" 2>&1)"
  check "install says the wake is bosun's already, and prints no sudo line to set it again" bash -c 'grep -q "repeating wake is bosun'"'"'s already" <<<"$1" && ! grep -q "run once:" <<<"$1"' _ "$out"
  check "doctor with BOSUN_WAKE=0 and bosun's own wake still set warns that it is left from before, and how to cancel it" contains "$(env "${ours_env[@]}" BOSUN_WAKE=0 "$BOSUN" doctor 2>&1)" "warn  pmset still has bosun's repeating wake:"
  check "... and with BOSUN_WAKE=1 (the example's) it is bosun's, in the install's own doctor" contains "$out" "ok    pmset repeating wake:"
  # install.sh --cancel-wake <event> (cancel_wake): it cancels only while pmset's repeating events are exactly the
  # event bosun init judged bosun's (pmset cancels them all), whatever schedule bosun.env has by then.
  cancel_with() { # <shell to run first, to stand in for a tool> <event>
    env BOSUN_CONFIG_DIR="$INSTALL_CFG" BOSUN_START_TIME=09:00 bash -c 'source "$1/lib/common.sh"; load_config; eval "$2"; cancel_wake "$3"' _ "$ROOT" "$@"
  }
  cancel() { cancel_with "" "$1"; }
  ours_event="wakepoweron at 8:01AM weekdays only"
  printf 'Repeating power events:\n  %s\n  shutdown at 11:00PM every day\n' "$ours_event" >"$WORK/pmset-sched"
  check "cancelling bosun's wake is refused while pmset has another repeating event, which it would take too" fails_with "pmset has 2 repeating events, and cancelling takes them all" cancel "$ours_event"
  printf 'Repeating power events:\n  wakepoweron at 6:30AM every day\n' >"$WORK/pmset-sched"
  check "... and when pmset's is not the event init judged bosun's (given by hand, or changed since)" fails_with "pmset's (wakepoweron at 6:30AM every day) is not the one bosun init judged bosun's ($ours_event)" cancel "$ours_event"
  check "... neither ran pmset repeat cancel" bash -c '! grep -q "repeat cancel" "$1"' _ "$WORK/system.log"
  printf 'Repeating power events:\n  %s\n' "$ours_event" >"$WORK/pmset-sched"
  pmset_reads="$(grep -c '^pmset -g sched$' "$WORK/system.log")"
  if cancel_with 'sudo() { return 1; }' "$ours_event" >/dev/null 2>&1; then sudo_refused=no; else sudo_refused=yes; fi
  check "... nor looked at pmset when sudo was refused: sudo is asked first, so nothing changes behind its prompt" bash -c '[ "$1" = yes ] && [ "$(grep -c "^pmset -g sched$" "$2")" = "$3" ]' _ "$sudo_refused" "$WORK/system.log" "$pmset_reads"
  out="$(cancel_with 'pmset() { return 1; }' "$ours_event" 2>&1)"
  cancel_rc=$?
  check "... and a schedule pmset cannot give is a warning, no failure, and nothing is cancelled" bash -c '[ "$1" = 0 ] && grep -q "WARN: could not read pmset" <<<"$2" && [ -s "$3" ]' _ "$cancel_rc" "$out" "$WORK/pmset-sched"
  cancel "$ours_event" >/dev/null 2>&1
  check "that event alone is cancelled, though bosun.env's start has moved to 09:00 since (a no that moved it)" grep -q "sudo -n pmset repeat cancel" "$WORK/system.log"
  check "... sudo asked first, then pmset looked at, then the cancel with sudo -n" bash -c 'grep -E "^(sudo -v|pmset -g sched|sudo -n pmset repeat cancel)$" "$1" | tail -3 | paste -sd "|" - | grep -qx "sudo -v|pmset -g sched|sudo -n pmset repeat cancel"' _ "$WORK/system.log"
  check "with no repeating event left there is nothing to cancel, and that is no failure" cancel "$ours_event"
  rm -f "$WORK/pmset-sched"
  # doctor on a state folder and a config of their own (with the suite's knowledge base): the watchdog's line, judged
  # as bosun status judges it (its last full run long past, but a run it skipped, an update's hold or a quit, is a
  # run), and the ledger and the knowledge base against their journals. Clean; then a link to an item that is gone
  # (an old prune) and an edit by hand, which warn and add no failure; then the ledger's journal damaged, with bosun
  # quit; then the knowledge base's journal damaged, which each fail. (Doctor's login check calls the fake claude.)
  dstate="$WORK/doctor-state"
  dconfig="$WORK/doctor-config"
  mkdir -p "$dstate/logs"
  printf '{"last_run": "2026-01-05 05:00:00"}' >"$dstate/watchdog.json"
  echo "update in progress: skipping this watchdog run" >"$dstate/logs/watchdog.launchd.out"
  cp -R "$BOSUN_CONFIG_DIR" "$dconfig"
  cp -R "$KG_DIR" "$dconfig/knowledge"
  dl() { BOSUN_STATE_DIR="$dstate" "$BOSUN" ledger "$@" >/dev/null 2>&1; }
  d_doctor() { BOSUN_STATE_DIR="$dstate" BOSUN_LOG_DIR="$dstate/logs" BOSUN_CONFIG_DIR="$dconfig" "$BOSUN" doctor 2>&1; }
  failed_checks() { grep -o '^[0-9]* check(s) failed' <<<"$1" || echo "none failed"; }
  dl upsert 'mypr:acme/api#1' --kind my-pr
  dl upsert 'review:acme/api#2' --kind review-request
  dl link 'mypr:acme/api#1' relates_to 'review:acme/api#2'
  clean="$(d_doctor)"
  check "doctor counts a run the watchdog skipped as a run, as bosun status does" contains "$clean" "ok    watchdog agent $BOSUN_LAUNCHD_LABEL.watchdog loaded; last ran at "
  check "doctor checks the knowledge base against its journal" contains "$clean" "ok    the knowledge base and its journal: knowledge base is valid"
  jq 'del(.items["review:acme/api#2"])' "$dstate/ledger.json" >"$WORK/pruned.json"
  mv "$WORK/pruned.json" "$dstate/ledger.json"
  dl render # adopts the item gone, as a prune from before prunes kept lineage whole left it
  jq -c 'if .id == "step/merge" then .name = "Merge to trunk" else . end' "$dconfig/knowledge/entities.jsonl" >"$WORK/kg-edited.jsonl"
  mv "$WORK/kg-edited.jsonl" "$dconfig/knowledge/entities.jsonl"
  doctor_said="$(d_doctor)"
  check "doctor warns about a link to an item that is gone" contains "$doctor_said" "warn  the ledger breaks a rule, or holds what the next write takes care of"
  check "... naming it" contains "$doctor_said" "link-target: mypr:acme/api#1 links: "
  check "doctor warns about an edit by hand the next write adopts" contains "$doctor_said" "warn  the knowledge base breaks a rule, or holds what the next write takes care of (every finding: bosun kg validate)"
  check "... naming it" contains "$doctor_said" "edited: journal: the knowledge files were changed outside their journal"
  check "... and neither warning is a failure" test "$(failed_checks "$doctor_said")" = "$(failed_checks "$clean")"
  echo 'nonsense' >>"$dstate/journal/ledger/$(jq -r .replica "$dstate/replica.json").jsonl"
  BOSUN_STATE_DIR="$dstate" write_quit paused
  doctor_said="$(d_doctor)"
  check "a damaged journal fails doctor" contains "$doctor_said" "FAIL  the ledger's journal refuses writes or a rebuild would lose data"
  check "... saying what validate found" contains "$doctor_said" "journal: the journal is damaged: "
  check "... naming the journal once" not_contains "$doctor_said" "journal: journal:"
  check "while bosun is quit, doctor says the watchdog skips its runs, with no warning" contains "$doctor_said" "ok    watchdog agent $BOSUN_LAUNCHD_LABEL.watchdog loaded; it skips its runs while bosun is quit"
  # The knowledge base's journal damaged, with the suite's own state (its ledger valid, so the one damaged journal).
  echo 'nonsense' >>"$dconfig/knowledge/journal/$(jq -r .replica "$BOSUN_STATE_DIR/replica.json").jsonl"
  doctor_said="$(BOSUN_CONFIG_DIR="$dconfig" "$BOSUN" doctor 2>&1)"
  check "a damaged knowledge journal fails doctor" contains "$doctor_said" "FAIL  the knowledge base's journal refuses writes or a rebuild would lose data"
  check "... saying what validate found" contains "$doctor_said" "journal: the journal is damaged: "
  check "... with the reader's warning kept out of the counts' ok line" bash -c '! grep -q "ok    knowledge: .*damaged" <<<"$1"' _ "$doctor_said"
  CONSOLE_PLIST="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.console.plist"
  NOW_PLIST="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.now.plist"
  check "install writes no console job by default" test ! -e "$CONSOLE_PLIST"
  # Each install below ends with bosun doctor, whose console lines are read from what the install printed: bosun
  # stopped (no marker), then the console wanted (the marker, which install.sh never makes) but not serving.
  mkdir -p "$BOSUN_STATE_DIR/console"
  [ ! -e "$BOSUN_STATE_DIR/console/wanted" ] || mv "$BOSUN_STATE_DIR/console/wanted" "$WORK/console-wanted.aside"
  out="$(BOSUN_CONSOLE_ALWAYS=1 BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" 2>&1)"
  check "... and removes the learning agent again once it is disabled" test ! -e "$LEARN_PLIST"
  check "... saying the setting that is off" contains "$out" "removed launchd job $BOSUN_LAUNCHD_LABEL.learn (no longer wanted: BOSUN_LEARN_ENABLED is not 1)"
  check "with BOSUN_CONSOLE_ALWAYS=1 it writes and loads the console job" bash -c 'plutil -lint -s "$1" && grep -q "launchctl bootstrap gui/$(id -u) $1" "$2"' _ "$CONSOLE_PLIST" "$WORK/system.log"
  check "... named by the morning job's label plus .console, running bosun console --job (and no BOSUN_TRIGGER)" python3 -I -c '
import plistlib, sys
job = plistlib.load(open(sys.argv[1], "rb"))
assert job["Label"] == sys.argv[2] + ".console", job["Label"]
assert job["ProgramArguments"][-1].rstrip().endswith("/bin/bosun\" console --job"), job["ProgramArguments"]
assert "BOSUN_TRIGGER" not in job["EnvironmentVariables"], job["EnvironmentVariables"]
' "$CONSOLE_PLIST" "$BOSUN_LAUNCHD_LABEL"
  check "... not started at load: the marker alone keeps it, so a quit stays quit through a reboot" python3 -I -c '
import plistlib, sys
job = plistlib.load(open(sys.argv[1], "rb"))
assert job.get("RunAtLoad") is not True, job.get("RunAtLoad")
assert job["KeepAlive"] == {"PathState": {sys.argv[2] + "/console/wanted": True}}, job["KeepAlive"]
' "$CONSOLE_PLIST" "$BOSUN_STATE_DIR"
  check "doctor, with BOSUN_CONSOLE_ALWAYS=1, says all is well, and that bosun is stopped, with no marker" contains "$out" "ok    launchd job $BOSUN_LAUNCHD_LABEL.console loaded (serves the console while bosun runs: not now, bosun is stopped"
  log_lines="$(wc -l <"$WORK/system.log")"
  : >"$BOSUN_STATE_DIR/console/wanted"
  out="$(BOSUN_CONSOLE_ALWAYS=1 BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" 2>&1)"
  rm -f "$BOSUN_STATE_DIR/console/wanted"
  check "a second install leaves it loaded as it is" bash -c '! tail -n +$(($2 + 1)) "$1" | grep -q "launchctl bootstrap .*console"' _ "$WORK/system.log" "$log_lines"
  check "... and its doctor warns about the console job loaded and wanted while no console serves" contains "$out" "warn  launchd job $BOSUN_LAUNCHD_LABEL.console loaded and wanted, but the console is not serving"
  [ ! -e "$WORK/console-wanted.aside" ] || mv "$WORK/console-wanted.aside" "$BOSUN_STATE_DIR/console/wanted"
  out="$(BOSUN_CONFIG_DIR="$INSTALL_CFG" "$ROOT/install.sh" 2>&1)"
  check "turned off, install unloads and removes it" bash -c 'tail -n +$(($4 + 1)) "$1" | grep -q "launchctl bootout gui/$(id -u)/$2.console" && [ ! -e "$3" ]' _ "$WORK/system.log" "$BOSUN_LAUNCHD_LABEL" "$CONSOLE_PLIST" "$log_lines"
  check "... and its doctor, without BOSUN_CONSOLE_ALWAYS=1, says nothing of the console job" bash -c '
    grep -q "^  ok  " <<<"$1" && ! grep -qE "^  (ok|warn|FAIL) +launchd job $2\.console" <<<"$1"' _ "$out" "$BOSUN_LAUNCHD_LABEL"
  # Both jobs not loaded (the .now job's plist set aside for one doctor run): a warning each, saying to run install.sh.
  mv "$NOW_PLIST" "$WORK/now.plist.aside"
  doctor_said="$(BOSUN_CONSOLE_ALWAYS=1 "$BOSUN" doctor 2>&1)"
  mv "$WORK/now.plist.aside" "$NOW_PLIST"
  check "doctor, with BOSUN_CONSOLE_ALWAYS=1 and no job, warns to run install.sh" contains "$doctor_said" "warn  launchd job $BOSUN_LAUNCHD_LABEL.console not loaded, though BOSUN_CONSOLE_ALWAYS=1"
  check "... and of the job that runs --now not loaded, saying to run install.sh (a warning, as nothing else fails)" contains "$doctor_said" "warn  launchd job $BOSUN_LAUNCHD_LABEL.now not loaded"

  check "install writes and loads the job that runs the morning run now" bash -c 'plutil -lint -s "$1" && grep -q "launchctl bootstrap gui/$(id -u) $1" "$2"' _ "$NOW_PLIST" "$WORK/system.log"
  check "... named by the morning job's label plus .now, running bosun morning --now in a login shell (and no BOSUN_TRIGGER)" python3 -I -c '
import plistlib, sys
job = plistlib.load(open(sys.argv[1], "rb"))
assert job["Label"] == sys.argv[2] + ".now", job["Label"]
assert job["ProgramArguments"][:2] == ["/bin/bash", "-lc"], job["ProgramArguments"]
assert job["ProgramArguments"][-1].startswith("exec \"") and job["ProgramArguments"][-1].endswith("/bin/bosun\" morning --now"), job["ProgramArguments"]
assert "BOSUN_TRIGGER" not in job["EnvironmentVariables"], job["EnvironmentVariables"]
' "$NOW_PLIST" "$BOSUN_LAUNCHD_LABEL"
  check "... started by nothing but a kickstart: no load, schedule or keep-alive; its process group is abandoned, so the tmux server it starts outlives it" python3 -I -c '
import plistlib, sys
job = plistlib.load(open(sys.argv[1], "rb"))
assert job.get("RunAtLoad") is False, job.get("RunAtLoad")
assert not {"KeepAlive", "StartCalendarInterval", "StartInterval", "WatchPaths", "QueueDirectories"} & set(job), sorted(job)
assert job.get("AbandonProcessGroup") is True, job.get("AbandonProcessGroup")
' "$NOW_PLIST"
  check "a second install (two, since the one above) leaves it loaded as it is" bash -c '! tail -n +$(($2 + 1)) "$1" | grep -q "launchctl bootstrap .*\.now"' _ "$WORK/system.log" "$log_lines"

  # The login job (templates/launchd-login.plist): rendered only while BOSUN_START_AT_LOGIN=1; a reload leaves the
  # marker that --at-login tells a reload by.
  login_job() { bash -c 'source "$1/lib/common.sh"; load_config; shift; "$@"' _ "$ROOT" "$@"; }
  LOGIN_LABEL="$BOSUN_LAUNCHD_LABEL.login"
  login_plist="$(BOSUN_START_AT_LOGIN=1 login_job render_job "$LOGIN_LABEL")"
  check "render_job renders the login job with BOSUN_START_AT_LOGIN=1: it runs morning --at-login at load" bash -c 'grep -q "morning --at-login" <<<"$1" && grep -A1 "<key>RunAtLoad</key>" <<<"$1" | grep -q "<true/>" && grep -q "login.launchd.out" <<<"$1" && grep -A1 "AbandonProcessGroup" <<<"$1" | grep -q "<true/>"' _ "$login_plist"
  check "... and renders nothing with BOSUN_START_AT_LOGIN=0" test -z "$(BOSUN_START_AT_LOGIN=0 login_job render_job "$LOGIN_LABEL")"
  check "... its plist is valid for plutil" bash -c 'printf "%s\n" "$1" >"$2" && plutil -lint -s "$2"' _ "$login_plist" "$WORK/login.plist"
  check "install has loaded it (the default is on), named by the morning label plus .login" bash -c 'grep -q "launchctl bootstrap gui/$(id -u) .*\.login\.plist" "$1" && plutil -lint -s "$2"' _ "$WORK/system.log" "$HOME/Library/LaunchAgents/$LOGIN_LABEL.plist"
  check "... and the job list for uninstall names it" contains "$(login_job job_labels)" "$LOGIN_LABEL"
  rm -f "$BOSUN_STATE_DIR/login-reload" "$HOME/Library/LaunchAgents/$LOGIN_LABEL.plist"
  check "reload_changed_jobs with the setting on loads it again and leaves the reload marker" test -n "$(BOSUN_START_AT_LOGIN=1 login_job reload_changed_jobs | grep "\.login$")" -a -s "$BOSUN_STATE_DIR/login-reload"

  # --background: launchd starts the run (the job above), so that nothing it starts is the caller's child.
  section morning-background "morning --now --background (fake launchctl)" needs install covers 'install.sh,templates/launchd-now.plist,libexec/bosun-morning,libexec/bosun-doctor'
  no_session
  morning_headers() { cat "$BOSUN_LOG_DIR"/morning-*.log 2>/dev/null | grep -c "=== morning run" || true; }
  bg_kickstarts() { grep -c "^launchctl kickstart gui/$(id -u)/$BOSUN_LAUNCHD_LABEL.now\$" "$WORK/system.log" || true; }
  bg_headers="$(morning_headers)"
  bg_before="$(bg_kickstarts)"
  out="$("$BOSUN" morning --now --background 2>&1)"
  bg_rc=$?
  check "--background kickstarts the job and exits 0" bash -c '[ "$1" -eq 0 ] && [ "$3" -eq $(($2 + 1)) ]' _ "$bg_rc" "$bg_before" "$(bg_kickstarts)"
  check "... without -k: a run already going is not restarted" bash -c '! grep -q "^launchctl kickstart -k" "$1"' _ "$WORK/system.log"
  check "... says so in a line, and runs nothing itself: no session, no morning log header" bash -c '[ "$(wc -l <<<"$1")" -eq 1 ] && grep -q "started" <<<"$1" && ! tmux -L "$2" has-session -t =smoke && [ "$3" = "$4" ]' _ "$out" "$SOCKET" "$bg_headers" "$(morning_headers)"
  for running_job in "$BOSUN_LAUNCHD_LABEL.now" "$BOSUN_LAUNCHD_LABEL"; do
    echo "$running_job" >"$WORK/job-running"
    bg_before="$(bg_kickstarts)"
    out="$("$BOSUN" morning --now --background 2>&1)"
    bg_rc=$?
    check "with $running_job running, it exits 0, says so in one line and starts nothing (no kickstart)" bash -c '[ "$1" -eq 0 ] && [ "$(wc -l <<<"$2")" -eq 1 ] && grep -q "already going (launchd job $3)" <<<"$2" && [ "$4" = "$5" ]' _ "$bg_rc" "$out" "$running_job" "$bg_before" "$(bg_kickstarts)"
  done
  rm -f "$WORK/job-running"
  touch "$WORK/kickstart-fails"
  out="$("$BOSUN" morning --now --background 2>&1)"
  bg_rc=$?
  check "a kickstart launchd refuses, with nothing running, exits 1, in one line, with launchd's words" bash -c '[ "$1" -eq 1 ] && [ "$(wc -l <<<"$2")" -eq 1 ] && grep -q "launchd did not start" <<<"$2" && grep -q "Could not find service" <<<"$2"' _ "$bg_rc" "$out"
  rm -f "$WORK/kickstart-fails"
  mv "$NOW_PLIST" "$WORK/now.plist"
  bg_before="$(bg_kickstarts)"
  out="$("$BOSUN" morning --now --background 2>&1)"
  bg_rc=$?
  check "with the job not loaded it exits 1, in one line that says to run install.sh again, and kickstarts nothing" bash -c '[ "$1" -eq 1 ] && [ "$(wc -l <<<"$2")" -eq 1 ] && grep -q "not loaded" <<<"$2" && grep -q "run install.sh again" <<<"$2" && [ "$3" = "$4" ]' _ "$bg_rc" "$out" "$bg_before" "$(bg_kickstarts)"
  # (doctor's warning for it: the install section.)
  mv "$WORK/now.plist" "$NOW_PLIST"
  bg_before="$(bg_kickstarts)"
  bg_usage() {
    "$BOSUN" morning "$@" >/dev/null 2>&1
    [ $? -eq 2 ]
  }
  check "--background without --now is a usage error (exit 2)" bg_usage --background
  check "... so is --dry-run with it, which would start nothing to look at" bg_usage --now --background --dry-run
  check "... and --skip-setup, which the job's run cannot be given" bg_usage --now --background --skip-setup
  check "... and --awake-minutes" bg_usage --now --background --awake-minutes 5
  check "... and none of them asked launchctl for anything" test "$bg_before" = "$(bg_kickstarts)"
  check "--help says what --background is, without a config" contains "$(env -i HOME="$HOME" PATH="$PATH" "$BOSUN" morning --help 2>&1)" "--background"

  section app "bosun app (fake curl; the stand-ins for ditto, codesign, spctl, osascript, open, swift and iconutil)" covers 'libexec/bosun-app,libexec/bosun-status,libexec/bosun-update,lib/app_*.py,app/build.sh,app/VERSION,app/publisher.json,app/Resources/**,templates/launchd-app.plist'
  APP_DIR="$HOME/Applications"
  APP="$APP_DIR/bosun.app"
  APP_JOB="$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.app.plist"
  APP_LOG="$HOME/.sandbox/system.log" # what the stand-ins were asked
  APP_ZIP="$WORK/app-release.zip"
  APP_TEAM=ABCDE12345
  APP_CHECKOUT="$WORK/app-checkout" # a copy of this checkout, with a Team ID in app/publisher.json: the repo's own stays empty until its publisher fills it in
  mkdir -p "$HOME/.sandbox"
  for tool in ditto codesign spctl open osascript swift iconutil; do
    install -m 755 "$ROOT/tests/sandbox/macos/$tool" "$HOME/.local/bin/$tool"
  done
  # spctl notes whether the old app is still in place while the new one is assessed, then answers as its stand-in does.
  cat >"$HOME/.local/bin/spctl" <<EOF
#!/bin/bash
[ ! -e "$APP/Contents/old.txt" ] || echo "the old app was in place" >>"$WORK/app-spctl-saw"
exec "$ROOT/tests/sandbox/macos/spctl" "\$@"
EOF
  # curl serves the release zip of the test ($APP_ZIP) and writes down the address; a missing release is its 404, which
  # -f makes exit 22 and -w prints.
  cat >"$HOME/.local/bin/curl" <<EOF
#!/bin/bash
echo "curl \$*" >>"$WORK/app-curl.log"
[ ! -e "$WORK/app-release-missing" ] || {
  printf 404
  exit 22
}
out=""
while [ \$# -gt 0 ]; do
  [ "\$1" != -o ] || out="\$2"
  shift
done
cp "$APP_ZIP" "\$out"
EOF
  chmod +x "$HOME/.local/bin/spctl" "$HOME/.local/bin/curl"
  if [ "$(bash -c 'source "$1/lib/common.sh"; command -v curl ditto codesign spctl osascript open swift iconutil' _ "$ROOT" | tr '\n' ' ')" = "$HOME/.local/bin/curl $HOME/.local/bin/ditto $HOME/.local/bin/codesign $HOME/.local/bin/spctl $HOME/.local/bin/osascript $HOME/.local/bin/open $HOME/.local/bin/swift $HOME/.local/bin/iconutil " ]; then
    copy_tree "$APP_CHECKOUT"
    APP_ID="$(jq -r .bundle_id "$ROOT/app/publisher.json")"
    # bapp: `bosun app` of the copy.
    bapp() { "$APP_CHECKOUT/bin/bosun" app "$@"; }
    set_team() { jq --arg team "$1" '.team_id = $team' "$APP_CHECKOUT/app/publisher.json" >"$WORK/publisher.json" && mv "$WORK/publisher.json" "$APP_CHECKOUT/app/publisher.json"; }
    set_wanted() { echo "$1" >"$APP_CHECKOUT/app/VERSION"; }
    installed() { bapp status --json | jq -r '.installed // "none"'; }
    curls() { wc -l <"$WORK/app-curl.log" 2>/dev/null || echo 0; }
    # Whatever ~/Applications holds, and what is in it, as one number: to compare before and after.
    apps_state() {
      [ -d "$APP_DIR" ] || {
        echo none
        return
      }
      (cd "$APP_DIR" && find . | LC_ALL=C sort && find . -type f -exec cksum {} + | LC_ALL=C sort) | cksum
    }
    # app_release <version> <a Team ID, adhoc or unsigned> <notarized: yes or no> <bundle id> [<extra>]: the zip the
    # release holds ($APP_ZIP): bosun.app, signed as the stand-ins read it, and <extra> beside it (a second app, or
    # a file) when given.
    app_release() {
      local version="$1" signature="$2" notarized="$3" bundle_id="$4" extra="${5:-}" dir
      dir="$(mktemp -d "$WORK/app-release.XXXXXX")"
      mkdir -p "$dir/bosun.app/Contents/MacOS"
      : >"$dir/bosun.app/Contents/MacOS/bosun"
      python3 -I -c 'import plistlib, sys
plistlib.dump({"CFBundleIdentifier": sys.argv[2], "CFBundleShortVersionString": sys.argv[3]}, open(sys.argv[1], "wb"))' "$dir/bosun.app/Contents/Info.plist" "$bundle_id" "$version"
      if [ "$signature" != unsigned ]; then
        mkdir "$dir/bosun.app/Contents/_CodeSignature"
        echo "$APP_ID" >"$dir/bosun.app/Contents/_CodeSignature/identifier" # the code's identifier, which the requirement names
        [ "$signature" = adhoc ] || echo "$signature" >"$dir/bosun.app/Contents/_CodeSignature/team"
        [ "$notarized" = no ] || : >"$dir/bosun.app/Contents/_CodeSignature/notarized"
      fi
      case "$extra" in
        "") ;;
        *.app) cp -R "$dir/bosun.app" "$dir/$extra" ;;
        *) echo x >"$dir/$extra" ;;
      esac
      rm -f "$APP_ZIP"
      python3 -I -c 'import sys, zipfile
from pathlib import Path
with zipfile.ZipFile(sys.argv[1], "w") as bundle:
    for path in sorted(Path(sys.argv[2]).rglob("*")):
        bundle.write(path, path.relative_to(sys.argv[2]))' "$APP_ZIP" "$dir"
      rm -rf "$dir"
    }
    good_release() { app_release "$1" "$APP_TEAM" yes "$APP_ID"; } # <version>
    # app_refused <description> <what it says> <app_release's arguments>: the install is refused, with that
    # reason, and ~/Applications is as it was.
    app_refused() {
      local description="$1" says="$2" before
      shift 2
      app_release "$@"
      before="$(apps_state)"
      check "install refuses $description" fails_with "$says" bapp install
      check "... and leaves ~/Applications as it was" test "$(apps_state)" = "$before"
    }
    app_refusals() { # [two]: only the first two, which reach the codesign and spctl stand-ins each its own way
      app_refused "an app signed by another team" "not signed with the publisher's Developer ID" 0.1.0 ZZZZZ99999 yes "$APP_ID"
      app_refused "one that is not notarized" "not notarized" 0.1.0 "$APP_TEAM" no "$APP_ID"
      [ "${1:-}" != two ] || return 0
      app_refused "one that is not signed at all" "not signed" 0.1.0 unsigned no "$APP_ID"
      app_refused "one signed ad hoc" "not signed with the publisher's Developer ID" 0.1.0 adhoc yes "$APP_ID"
      app_refused "one with another bundle id" "bundle id is ai.other.app" 0.1.0 "$APP_TEAM" yes ai.other.app
      app_refused "a zip with a second app in it" "other.app" 0.1.0 "$APP_TEAM" yes "$APP_ID" other.app
      app_refused "a zip with a file beside the app" "README.txt" 0.1.0 "$APP_TEAM" yes "$APP_ID" README.txt
    }

    # logged <regex>: the stand-ins were asked something that matches, a line of $APP_LOG.
    logged() { grep -qE -- "$1" "$APP_LOG"; }
    # plist_is <Info.plist> <bundle id> <version>: what app/build.sh puts in the bundle's Info.plist.
    plist_is() {
      python3 -I -c 'import plistlib, sys
info = plistlib.load(open(sys.argv[1], "rb"))
ok = info["CFBundleIdentifier"] == sys.argv[2] and info["CFBundleShortVersionString"] == sys.argv[3]
ok = ok and info["CFBundleVersion"] == sys.argv[3] and info["CFBundleExecutable"] == "bosun"
sys.exit(not (ok and info["LSUIElement"] is True and info["LSMinimumSystemVersion"] == "14.0"))' "$@"
    }
    # zip_holds <zip> <name>...: every entry of the archive is inside bosun.app/, and these are in it.
    zip_holds() {
      python3 -I -c 'import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
sys.exit(not (all(n.startswith("bosun.app/") for n in names) and all(f"bosun.app/{n}" in names for n in sys.argv[2:])))' "$@"
    }
    # app_build_checks: `install --build` and app/build.sh itself. swift, iconutil, codesign and ditto are the
    # stand-ins (tests/install_e2e.sh runs the real ones, and the build on a Mac): this shows what build.sh asks of
    # them, what goes in the bundle, how it is signed, and that a failed build leaves nothing. It ends with
    # ~/Applications as it was.
    app_build_checks() {
      local out="$WORK/app-build-out" version built
      local contents="$out/bosun.app/Contents" identity="Developer ID Application: Acme Inc (ABCDE12345)"
      local PATH="$HOME/.local/bin:$PATH" # `bosun` puts the stand-ins first; the direct runs of app/build.sh must too
      version="$(cat "$APP_CHECKOUT/app/VERSION")"
      set_team ""
      : >"$APP_LOG"
      touch "$HOME/.sandbox/swift-fails"
      bapp install --build >"$WORK/app-build-failed.out" 2>"$WORK/app-build-failed.err"
      local failed_rc=$?
      check "--build with a swift build that fails says why, though no team_id is set" bash -c '
        [ "$1" -ne 0 ] && cat "$2" "$3" | grep -qF "app/build.sh failed, the last lines of its output:"' _ "$failed_rc" "$WORK/app-build-failed.out" "$WORK/app-build-failed.err"
      check "... shows the compiler's own words, and the build's last line" bash -c 'cat "$1" "$2" | grep -qF "error: cannot find '"'"'Pretend'"'"' in scope"' _ "$WORK/app-build-failed.out" "$WORK/app-build-failed.err"
      check "... and says when the build starts, on stderr" grep -qF "building bosun.app with app/build.sh" "$WORK/app-build-failed.err"
      check "... and leaves no ~/Applications" test ! -e "$APP_DIR"
      rm -f "$HOME/.sandbox/swift-fails"
      built="$(bapp install --build 2>&1)"
      check "--build installs the app and says it is a development build" contains "$built" "bosun.app $version installed in ~/Applications (a development build"
      check "... the app is app/VERSION's, with nothing else left in ~/Applications" test "$(installed) $(ls -A "$APP_DIR")" = "$version bosun.app"
      check "... it ran swift build for arm64 in release" logged '^swift build -c release --arch arm64( |$)'
      check "... and signed ad hoc, as the bundle id, into a folder of its own, with no hardened runtime or timestamp" logged "^codesign --force --sign - --identifier $APP_ID [^ ]*/built/\.bosun-build\.[^/ ]*/bosun\.app\$"
      check "... its Info.plist has the bundle id, the version and what makes it a menu bar app" plist_is "$APP/Contents/Info.plist" "$APP_ID" "$version"
      check "... its signature is the bundle id, with no team" bash -c '[ "$(cat "$1/Contents/_CodeSignature/identifier")" = "$2" ] && [ ! -e "$1/Contents/_CodeSignature/team" ]' _ "$APP" "$APP_ID"
      check "... and nothing was downloaded or assessed by Gatekeeper" bash -c '! grep -qE "^(curl|spctl)" "$1" && [ ! -e "$2" ]' _ "$APP_LOG" "$WORK/app-curl.log"
      bapp remove >/dev/null 2>&1
      rm -rf "$APP_DIR"

      rm -rf "$out"
      check "app/build.sh with a Developer ID makes the bundle" "$APP_CHECKOUT/app/build.sh" --identity "$identity" --out "$out"
      check "... signing it with the hardened runtime and a timestamp" logged "^codesign --force --sign Developer ID Application: Acme Inc \(ABCDE12345\) --identifier $APP_ID --options runtime --timestamp $out/\.bosun-build\.[^/ ]*/bosun\.app\$"
      check "... as the bundle id, with the identity's team" bash -c '[ "$(cat "$1/_CodeSignature/identifier")" = "$2" ] && [ "$(cat "$1/_CodeSignature/team")" = ABCDE12345 ]' _ "$contents" "$APP_ID"
      check "... which meets the Developer ID requirement of that team and bundle id, as bosun app install asks" codesign --verify --deep --strict -R "=anchor apple generic and identifier \"$APP_ID\" and certificate leaf[subject.OU] = \"ABCDE12345\"" "$out/bosun.app"
      check "... the bundle has the executable, the icon and both menu bar images" bash -c 'for f in MacOS/bosun Resources/AppIcon.icns Resources/MenuBarIcon.png Resources/MenuBarIcon@2x.png; do [ -f "$1/$f" ] || exit 1; done' _ "$contents"
      check "... and an Info.plist with app/VERSION and app/publisher.json's bundle id" plist_is "$contents/Info.plist" "$APP_ID" "$version"
      check "... and is zipped as bosun-app-<version>.zip, the one bosun.app" zip_holds "$out/bosun-app-$version.zip" Contents/MacOS/bosun Contents/Info.plist
      check "... the build and --zip zip the same way (one function, the same flags)" bash -c '[ "$(grep -c "^ditto -c -k --keepParent --norsrc --noextattr --noqtn " "$1")" -ge 1 ]' _ "$APP_LOG"
      check "... and nothing else is left in --out: no staging folder" test "$(find "$out" -mindepth 1 -maxdepth 1 | LC_ALL=C sort | sed 's|.*/||' | tr '\n' ' ')" = "bosun-app-$version.zip bosun.app "
      check "app/build.sh --zip zips a bundle again, as the build does, into --out" bash -c '"$1" --zip "$2/bosun.app" --out "$3" >/dev/null && grep -qE "^ditto -c -k --keepParent --norsrc --noextattr --noqtn $2/bosun\.app $3/\.bosun-build\.[^/]*/bosun-app-$4\.zip$" "$5"' _ "$APP_CHECKOUT/app/build.sh" "$out" "$WORK/app-rezip" "$version" "$APP_LOG"
      check "... which holds the one bosun.app" zip_holds "$WORK/app-rezip/bosun-app-$version.zip" Contents/MacOS/bosun
      check "... and refuses what is not a bosun.app, and a --zip with an --identity" bash -c '! "$1" --zip "$2" >/dev/null 2>&1 && "$1" --zip "$2/bosun.app" --identity - >/dev/null 2>&1; [ $? = 2 ]' _ "$APP_CHECKOUT/app/build.sh" "$out"
      check "... a refused --zip first leaves the zip already in --out as it was, and makes no staging folder" bash -c '! "$1" --zip "$2" --out "$2" >/dev/null 2>&1 && [ -f "$2/bosun-app-$3.zip" ] && [ "$(find "$2" -mindepth 1 -maxdepth 1 | wc -l | tr -d " ")" = 2 ]' _ "$APP_CHECKOUT/app/build.sh" "$out" "$version"
      touch "$HOME/.sandbox/swift-fails"
      check "a failed app/build.sh removes the bundle and the zip of an earlier build, and leaves no unsigned bundle or staging folder" bash -c '
        ! "$1" --out "$2" >/dev/null 2>&1 && [ -z "$(ls -A "$2")" ]' _ "$APP_CHECKOUT/app/build.sh" "$out"
      rm -f "$HOME/.sandbox/swift-fails"
      check "app/build.sh refuses an unknown argument, with usage (exit 2)" bash -c '"$1" --bogus >/dev/null 2>&1; [ $? = 2 ]' _ "$APP_CHECKOUT/app/build.sh"
      check "... and an option with no value (exit 2)" bash -c '"$1" --out >/dev/null 2>&1; [ $? = 2 ]' _ "$APP_CHECKOUT/app/build.sh"
      check "a fresh copy of app/build.sh, run directly with a bogus option, says its usage and exits 2, with no failure of its own" bash -c '
        cp -R "$1/app" "$2/app-fresh" && out="$("$2/app-fresh/build.sh" --bogus 2>&1)"; status=$?; rm -rf "$2/app-fresh"
        [ "$status" = 2 ] && grep -q "^Usage: app/build.sh " <<<"$out" && ! grep -q "failed at line" <<<"$out"' _ "$APP_CHECKOUT" "$WORK"
      check "app/build.sh names the line of a failure it has no message for (an rm that fails), and exits 1" bash -c '
        mkdir -p "$2/stub" && printf "#!/bin/sh\nexit 1\n" >"$2/stub/rm" && chmod +x "$2/stub/rm"
        out="$(PATH="$2/stub:$PATH" "$1" --out "$2/out" 2>&1)"; status=$?; /bin/rm -rf "$2"
        [ "$status" = 1 ] && grep -q "^app/build.sh: failed at line [0-9]*: rm -rf " <<<"$out"' _ "$APP_CHECKOUT/app/build.sh" "$WORK/build-trap"
      jq '.bundle_id = "not a bundle id"' "$APP_CHECKOUT/app/publisher.json" >"$WORK/publisher.json" && cp "$APP_CHECKOUT/app/publisher.json" "$WORK/publisher.good" && mv "$WORK/publisher.json" "$APP_CHECKOUT/app/publisher.json"
      : >"$APP_LOG"
      check "app/build.sh refuses a bundle_id that is not one, before it builds anything" bash -c '! out="$("$1" --out "$2" 2>&1)" && grep -q "is not a bundle identifier" <<<"$out" && ! grep -q "^swift" "$3"' _ "$APP_CHECKOUT/app/build.sh" "$out" "$APP_LOG"
      mv "$WORK/publisher.good" "$APP_CHECKOUT/app/publisher.json"
      rm -rf "$out" "$WORK/app-rezip"
      set_team "$APP_TEAM"
    }

    check "status --json with no app: what this checkout wants, and no Open at Login" bash -c '
      jq -e --arg want "$(cat "$2/app/VERSION")" --arg home "$3" ".contract == 1 and .installed == null and .wanted == \$want
        and .login == false and .path == (\$home + \"/Applications/bosun.app\")" <<<"$("$1" app status --json)"' _ "$BOSUN" "$ROOT" "$HOME"
    check "... and in words" contains "$("$BOSUN" app status)" "bosun.app is not installed"
    # (app/VERSION and app/publisher.json as the design says: test_app_release.py.)
    set_team ""
    check "install refuses while app/publisher.json has no team_id" fails_with "app/publisher.json has no team_id yet" bapp install
    check "update with no app asks nothing of the publisher: not installed, exit 0" contains "$(bapp update 2>&1)" "not installed"
    check "... with no download and no ~/Applications" bash -c '[ ! -e "$1" ] && [ ! -e "$2" ]' _ "$WORK/app-curl.log" "$APP_DIR"
    set_wanted 0.1.0
    echo "  (a development build: app/build.sh with the stand-ins for swift, iconutil, codesign and ditto)"
    app_build_checks
    set_team "$APP_TEAM"

    echo "  (a refused app, with no ~/Applications yet)"
    app_refusals
    check "a refusal makes no ~/Applications" test ! -e "$APP_DIR"
    check "... and no folder of the install is left in the home folder" bash -c '! find "$1" -name ".bosun-app-*" | grep -q .' _ "$HOME"

    good_release 0.1.0
    check "install downloads the release app/VERSION names, from the publisher's repository" bash -c '
      "$1" app install >/dev/null && grep -qF "https://github.com/promptromp/bosun/releases/download/app-v0.1.0/bosun-app-0.1.0.zip" "$2"' _ "$APP_CHECKOUT/bin/bosun" "$WORK/app-curl.log"
    check "... and places the app: the version is the bundle's" test "$(installed)" = 0.1.0
    check "... with nothing else left in ~/Applications" test "$(ls -A "$APP_DIR")" = bosun.app
    check "status says it in words" contains "$(bapp status)" "bosun.app 0.1.0 is installed"
    check "install checked the signature against the publisher's requirement, and notarization, of the unpacked app, before it was placed" bash -c '
      folder="$2/\.bosun-app-[0-9]*-[^/]*/unpacked/bosun\.app"
      grep -qE "^codesign --verify --deep --strict -R =anchor apple generic and identifier \"$3\" and .* leaf\[subject\.OU\] = \"$4\" $folder$" "$1" &&
        grep -qE "^spctl --assess --type execute -vv $folder$" "$1"' _ "$APP_LOG" "$APP_DIR" "$APP_ID" "$APP_TEAM"
    check "... and read nothing from what codesign prints about the app (codesign -dv was never run)" bash -c '! grep -q "^codesign -dv" "$1"' _ "$APP_LOG"

    echo "x" >"$APP/Contents/old.txt"
    # (The old app stays whatever the reason for a refusal, which comes before anything is placed:
    # test_app_install.py; two of them here.)
    echo "  (a refused app, with an old one in place)"
    app_refusals two
    echo "  (what a download may hold, what a killed install leaves, and an app that is not ours)"
    # app_refused_entry <description> <says> <entry> [symlink]: the good release with one more entry, refused.
    app_refused_entry() {
      local description="$1" says="$2" before
      good_release 0.1.0
      python3 -I -c 'import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "a") as bundle:
    info = zipfile.ZipInfo(sys.argv[2])
    if len(sys.argv) > 3:
        info.external_attr = 0o120777 << 16
    bundle.writestr(info, "x")' "$APP_ZIP" "${@:3}"
      before="$(apps_state)"
      check "install refuses $description" fails_with "$says" bapp install
      check "... and leaves ~/Applications as it was, nothing unpacked" test "$(apps_state)" = "$before"
    }
    app_refused_entry "an entry that climbs out of its folder" "climbs out" "../escape.txt"
    app_refused_entry "an entry with an absolute path" "absolute path" "/etc/escape.txt"
    app_refused_entry "a symbolic link in the archive" "symbolic link" "bosun.app/Contents/link" symlink
    check "nothing was unpacked outside: no escape.txt anywhere beside ~/Applications" bash -c '[ ! -e "$1/../escape.txt" ] && [ ! -e /etc/escape.txt ]' _ "$APP_DIR"
    # An install killed between its two renames leaves the old app in its own folder and none at the app's place.
    mkdir -p "$APP_DIR/.bosun-app-999999-killed/previous"
    mv "$APP" "$APP_DIR/.bosun-app-999999-killed/previous/bosun.app"
    app_release 0.1.0 ZZZZZ99999 yes "$APP_ID"
    check "an install after a killed one gives the old app back first, even when it is itself refused" bash -c '
      ! "$1" app install >/dev/null 2>&1 && [ -f "$2/Contents/old.txt" ] && [ ! -e "$3/.bosun-app-999999-killed" ] && [ "$(ls -A "$3")" = bosun.app ]' _ "$APP_CHECKOUT/bin/bosun" "$APP" "$APP_DIR"
    mkdir -p "$APP_DIR/.bosun-app-999999-junk/unpacked" && echo junk >"$APP_DIR/.bosun-app-999999-junk/unpacked/x"
    check "... and sweeps what a killed install left unpacked" bash -c '! "$1" app install >/dev/null 2>&1 && [ ! -e "$2/.bosun-app-999999-junk" ]' _ "$APP_CHECKOUT/bin/bosun" "$APP_DIR"
    # Another app of that name is not bosun's to replace or remove.
    mv "$APP" "$WORK/bosun.app.kept"
    mkdir -p "$APP/Contents"
    python3 -I -c 'import plistlib, sys; plistlib.dump({"CFBundleIdentifier": "ai.other.app", "CFBundleShortVersionString": "9"}, open(sys.argv[1], "wb"))' "$APP/Contents/Info.plist"
    good_release 0.1.0
    before="$(apps_state)"
    check "install leaves another app of that name, and says so" fails_with "not bosun's app" bapp install
    check "remove leaves it too" fails_with "not bosun's app" bapp remove
    check "... both with ~/Applications as it was" test "$(apps_state)" = "$before"
    rm -rf "$APP"
    mv "$WORK/bosun.app.kept" "$APP"
    rm -f "$WORK/app-spctl-saw"
    set_wanted 0.2.0
    good_release 0.2.0
    check "update installs the version app/VERSION names when the installed one differs" bash -c '"$1" app update >/dev/null' _ "$APP_CHECKOUT/bin/bosun"
    check "... the app is the new one" test "$(installed)" = 0.2.0
    check "... the old app was in place until the new one was verified" grep -q "the old app was in place" "$WORK/app-spctl-saw"
    check "... and is gone after, with no folder of the install left in ~/Applications" bash -c '[ ! -e "$1/Contents/old.txt" ] && [ "$(ls -A "$2")" = bosun.app ]' _ "$APP" "$APP_DIR"
    downloads="$(curls)"
    check "update with the same version says so" contains "$(bapp update 2>&1)" "up to date"
    check "... and downloads nothing" test "$(curls)" = "$downloads"

    echo "  (an app that is running)"
    set_wanted 0.3.0
    good_release 0.3.0
    touch "$HOME/.sandbox/app-running"
    : >"$APP_LOG"
    bapp update >/dev/null 2>&1
    check "update asks a running app to quit, by its bundle id, then opens the new one" bash -c '
      quit="$(grep -n "^osascript -e tell application id \"$3\" to quit$" "$1" | head -1 | cut -d: -f1)"
      open="$(grep -n "^open $2$" "$1" | head -1 | cut -d: -f1)"
      [ -n "$quit" ] && [ -n "$open" ] && [ "$quit" -lt "$open" ]' _ "$APP_LOG" "$APP" "$APP_ID"
    check "... which is the new one" test "$(installed)" = 0.3.0
    set_wanted 0.4.0
    good_release 0.4.0
    rm -f "$HOME/.sandbox/app-running"
    : >"$APP_LOG"
    check "an app that is not running is neither asked to quit nor opened" bash -c '"$2" app update >/dev/null && ! grep -qE "to quit|^open " "$1"' _ "$APP_LOG" "$APP_CHECKOUT/bin/bosun"
    set_wanted 0.5.0
    app_release 0.5.0 ZZZZZ99999 yes "$APP_ID"
    touch "$HOME/.sandbox/app-running"
    : >"$APP_LOG"
    before="$(apps_state)"
    check "a refused update leaves the running app running" bash -c '! "$2" app update >/dev/null 2>&1 && [ -e "$HOME/.sandbox/app-running" ] && ! grep -q "to quit" "$1"' _ "$APP_LOG" "$APP_CHECKOUT/bin/bosun"
    check "... and ~/Applications as it was" test "$(apps_state)" = "$before"
    # (An app that will not quit stops the update after its quit polls, the home left as it was:
    # test_app_install.py, with a fake clock; asking it to quit is above.)
    rm -f "$HOME/.sandbox/app-running"

    touch "$WORK/app-release-missing"
    check "a release that is not there is one line saying so" fails_with "no release of bosun.app 0.5.0 at promptromp/bosun" bapp update
    check "... and ~/Applications is as it was" test "$(apps_state)" = "$before"
    rm -f "$WORK/app-release-missing"

    echo "  (Open at Login)"
    check "login on makes the job and loads it" bash -c '"$1" app login on >/dev/null && plutil -lint -s "$2" && grep -q "launchctl bootstrap gui/$(id -u) $2" "$3"' _ "$APP_CHECKOUT/bin/bosun" "$APP_JOB" "$WORK/system.log"
    check "... which opens the app at login, once, and is not kept alive; its process group is abandoned, so quitting the app never takes what it started (an update) with it" python3 -I -c '
import plistlib, sys
job = plistlib.load(open(sys.argv[1], "rb"))
assert job["Label"] == sys.argv[2] + ".app", job["Label"]
assert job["ProgramArguments"] == [sys.argv[3] + "/Applications/bosun.app/Contents/MacOS/bosun", "--at-login"], job["ProgramArguments"]
assert job["RunAtLoad"] is True, job
assert "KeepAlive" not in job, job
assert job.get("AbandonProcessGroup") is True, job.get("AbandonProcessGroup")
' "$APP_JOB" "$BOSUN_LAUNCHD_LABEL" "$HOME"
    check "... and status says Open at Login is on" bash -c 'jq -e ".login == true" <<<"$("$1" app status --json)"' _ "$APP_CHECKOUT/bin/bosun"
    login_log_lines="$(wc -l <"$WORK/system.log")"
    check "login on again, with the job there and loaded, says so" contains "$(bapp login on 2>&1)" "already on"
    check "... and reloads nothing: no bootout, no bootstrap, which would restart an app the job started" bash -c '! tail -n +$(($2 + 1)) "$1" | grep -qE "launchctl (bootout|bootstrap)"' _ "$WORK/system.log" "$login_log_lines"
    reload_check() { bash -c 'source "$1/lib/common.sh"; load_config; shift; reload_changed_jobs "$@"' _ "$APP_CHECKOUT" "$@" 2>&1; }
    check "reload_changed_jobs leaves a current one alone" not_contains "$(reload_check --dry-run)" "$BOSUN_LAUNCHD_LABEL.app"
    echo "<!-- from an older bosun -->" >>"$APP_JOB"
    check "... and keeps a stale one current" contains "$(reload_check --dry-run)" "$BOSUN_LAUNCHD_LABEL.app"
    reload_check >/dev/null
    check "... by writing the job again" not_contains "$(cat "$APP_JOB")" "from an older bosun"
    check "login off unloads it and removes the plist" bash -c '"$1" app login off >/dev/null && [ ! -e "$2" ] && grep -q "launchctl bootout gui/$(id -u)/$3.app" "$4"' _ "$APP_CHECKOUT/bin/bosun" "$APP_JOB" "$BOSUN_LAUNCHD_LABEL" "$WORK/system.log"
    check "... and status says it is off" bash -c 'jq -e ".login == false" <<<"$("$1" app status --json)"' _ "$APP_CHECKOUT/bin/bosun"
    reload_check >/dev/null
    check "reload_changed_jobs never makes one" test ! -e "$APP_JOB"

    echo "  (remove)"
    bapp login on >/dev/null
    touch "$HOME/.sandbox/app-running"
    : >"$APP_LOG"
    echo mine >"$APP_DIR/mine.txt"
    check "remove quits the app, turns Open at Login off and removes the app" bash -c '
      "$1" app remove >/dev/null && [ ! -e "$2" ] && [ ! -e "$3" ] && grep -q "to quit" "$4" && grep -q "launchctl bootout gui/$(id -u)/$5.app" "$6"' _ "$APP_CHECKOUT/bin/bosun" "$APP" "$APP_JOB" "$APP_LOG" "$BOSUN_LAUNCHD_LABEL" "$WORK/system.log"
    check "... and what else is in ~/Applications stays" test -f "$APP_DIR/mine.txt"
    check "remove with no app says so, exit 0" contains "$(bapp remove 2>&1)" "not installed"
    rm -f "$APP_DIR/mine.txt"
    downloads="$(curls)"
    check "update with no app installed says to run install, and downloads nothing" contains "$(bapp update 2>&1)" "run bosun app install"
    check "... exiting 0" bapp update
    check "... still no app, and no download" bash -c '[ ! -e "$1" ] && [ "$(wc -l <"$2")" = "$3" ]' _ "$APP" "$WORK/app-curl.log" "$downloads"
    check "login on with no app is refused" fails_with "bosun.app is not installed: run bosun app install first" bapp login on
    check "... and makes no job" test ! -e "$APP_JOB"

    check "usage errors exit 2" bash -c '
      for args in "" "bogus" "install --bogus" "update now" "remove now" "login" "login maybe" "login on now" "status --bogus"; do
        # shellcheck disable=SC2086  # split the argument list on purpose
        "$1" app $args >/dev/null 2>&1
        [ $? -eq 2 ] || { echo "bosun app $args did not exit 2"; exit 1; }
      done' _ "$APP_CHECKOUT/bin/bosun"

    # What the uninstall below finds: the app, and its job.
    good_release 0.5.0
    set_wanted 0.5.0
    installed_said="$(BOSUN_MENU_APP=0 bapp install 2>&1)"
    check "install says, with BOSUN_MENU_APP not 1, how to have the watchdog watch the app" contains "$installed_said" "set BOSUN_MENU_APP=1 in bosun.env"
    check "... and says nothing of it with BOSUN_MENU_APP=1" not_contains "$(BOSUN_MENU_APP=1 bapp install 2>&1)" "set BOSUN_MENU_APP=1"
    check "... and no bosun app command writes bosun.env" bash -c '[ ! -e "$BOSUN_CONFIG_DIR/bosun.env" ] || ! grep -q BOSUN_MENU_APP "$BOSUN_CONFIG_DIR/bosun.env"'
    bapp login on >/dev/null 2>&1
    check "an app and its Open at Login job are in place for the uninstall that follows" bash -c '[ -d "$1" ] && [ -e "$2" ]' _ "$APP" "$APP_JOB"
  else
    fail_check "the fake curl and the stand-ins for ditto, codesign, spctl, osascript, open, swift and iconutil are not first on PATH; skipped the app tests"
  fi

  section uninstall "uninstall (fake launchctl, pmset and sudo, test label)" needs install,app covers 'uninstall.sh,install.sh,lib/claude_state.py,lib/hook.py,libexec/bosun-uninstall,libexec/bosun-trust,templates/launchd*.plist'
  # Its own session and state: it would otherwise stop and delete what the rest of this run uses.
  un() {
    BOSUN_CONFIG_DIR="$INSTALL_CFG" BOSUN_STATE_DIR="$WORK/un-state" BOSUN_LOG_DIR="$WORK/un-state/logs" \
      BOSUN_SESSION_NAME=un-test BOSUN_START_TIME=09:00 BOSUN_WAKE_DELAY_MINUTES=1 BOSUN_DAYS=MTWRFSU \
      "$ROOT/uninstall.sh" "$@"
  }
  un_with_no_terminal() { un </dev/null; }
  un_in_its_tmux() { TMUX="/tmp/tmux-1/$BOSUN_TMUX_SOCKET,1,0" un --yes; }
  UN_REPO="$WORK/un-repo"
  mkdir -p "$UN_REPO"
  check "uninstall will not start with no terminal to ask on" fails_with "no terminal" un_with_no_terminal
  check "... or from inside the session it would stop" fails_with "another terminal" un_in_its_tmux
  check "... or with an option it does not know" bash -c '"$1" uninstall --bogus >/dev/null 2>&1; [ $? -eq 2 ]' _ "$BOSUN"
  un_plan="$(un --dry-run 2>&1)"
  check "--dry-run says what it would remove" contains "$un_plan" "launchd job $BOSUN_LAUNCHD_LABEL"
  check "... and lists the menu bar app" contains "$un_plan" "the menu bar app, ~/Applications/bosun.app (it is asked to quit first)"
  check "... and its Open at Login job" contains "$un_plan" "launchd job $BOSUN_LAUNCHD_LABEL.app"
  check "... and the job that runs the morning run now" contains "$un_plan" "launchd job $BOSUN_LAUNCHD_LABEL.now"
  check "... and creates nothing, not even its own folders" test ! -e "$WORK/un-state"
  check "... and removes nothing" test -e "$PLIST"
  printf 'Repeating power events:\n  wakepoweron at 7:00AM every day\n' >"$WORK/pmset-sched"
  check "a wake at another time would stay" contains "$(un --dry-run 2>&1)" "may not be bosun's"
  printf 'Repeating power events:\n  wakepoweron at 9:01AM every day\n' >"$WORK/pmset-sched"
  BOSUN_STATE_DIR="$WORK/un-state" "$BOSUN" trust --any "$UN_REPO" >/dev/null
  echo "# mine" >"$WORK/un-state/a-file-of-mine"
  # Two worktrees folders a launch made beside windows' repositories, as it makes them: one empty, one that
  # holds a worktree now.
  UN_WORKTREES="$WORK/un-repos"
  mkdir -p "$UN_WORKTREES"
  for folder in exporter billing; do
    python3 "$ROOT/lib/agent_changes.py" make-folder "$WORK/un-state" "$UN_WORKTREES/$folder.worktrees"
  done
  mkdir "$UN_WORKTREES/billing.worktrees/fix-qty"
  # A window whose agent, stopped, starts a hook in a session of its own that writes a signal two seconds
  # later, as Claude Code starts its async SessionEnd hook, and a helper in its window's session that
  # outlives it, as Claude Code's caffeinate does, until the test removes its file (it ignores TERM, as the
  # window's end would send it); the agent ends a second later. Uninstall must wait for the hook before it
  # removes bosun's files, and not for the helper.
  cat >"$WORK/late-hook.py" <<'LATE'
import os, sys, time
os.setsid()
time.sleep(2)  # timing: the hook's delay, which uninstall waits out
os.makedirs(os.path.join(sys.argv[1], "signals"), exist_ok=True)
with open(os.path.join(sys.argv[1], "signals", "w.jsonl"), "w") as signal:
    signal.write("{}\n")
with open(sys.argv[2], "w") as done:  # when it wrote, for the order below
    done.write(f"{time.time_ns()}\n")
LATE
  cat >"$WORK/late-agent" <<'LATE'
#!/bin/bash
trap 'python3 "$1/late-hook.py" "$2" "$3" & (trap "" HUP TERM; for _ in $(seq 1 3000); do [ -e "$4.hold" ] || exit 0; sleep 0.1; done) &
  echo $! >"$4"; sleep 1; exit 0' HUP TERM
: >"$1/late-ready"
while :; do sleep 1; done
LATE
  chmod +x "$WORK/late-agent"
  rm -f "$WORK/late-ready"
  : >"$WORK/late-helper.hold"
  tmux -L "$SOCKET" new-session -d -s un-test -n w -- "$WORK/late-agent" "$WORK" "$WORK/un-state" "$WORK/late-written" "$WORK/late-helper"
  # tmux returns before the agent has set its trap: a stop that came first would end it with no hook to wait for.
  wait_for 30 test -e "$WORK/late-ready"
  # Each line uninstall says, with when it said it.
  PYTHONUNBUFFERED=1 un --yes 2>&1 |
    python3 -u -c 'import sys, time
for line in sys.stdin:
    print(time.time_ns(), line, end="")' >"$WORK/un-timed.txt"
  UN_OUT="$(sed 's/^[0-9]* //' "$WORK/un-timed.txt")"
  # This state folder holds a file of mine, so uninstall leaves it whole: what shows the wait is the order,
  # the late write before uninstall says the windows are removed, which it says once it stopped waiting
  # (tests/unit/test_uninstall.py: the files go only after it).
  check "uninstall waits for what a window it stopped left running: its late write came before it said the windows were removed" \
    bash -c 'wrote="$(cat "$1")" && said="$(grep -m1 "^[0-9]* removed the running windows" "$2" | cut -d" " -f1)" &&
      [ -n "$said" ] && [ "$wrote" -lt "$said" ]' _ "$WORK/late-written" "$WORK/un-timed.txt"
  check "... but not for a helper it left in its window's session" bash -c '
    kill -0 "$(cat "$2")" && ! grep -q "still running" <<<"$1"' _ "$UN_OUT" "$WORK/late-helper"
  rm -f "$WORK/late-helper.hold"
  check "uninstall takes back the trust it gave" bash -c '! python3 "$1/lib/claude_state.py" trusted "$2"' _ "$ROOT" "$UN_REPO"
  check "... removes the empty worktrees folder a launch made" bash -c '
    [ ! -e "$1/exporter.worktrees" ] && grep -q "removed the empty worktrees folder bosun made, $1/exporter.worktrees" <<<"$2"' _ "$UN_WORKTREES" "$UN_OUT"
  check "... and leaves the one that holds a worktree, saying why" bash -c '
    [ -d "$1/billing.worktrees/fix-qty" ] && grep -A1 "^  $1/billing.worktrees\$" <<<"$2" | grep -q "unfinished work"' _ "$UN_WORKTREES" "$UN_OUT"
  check "... cancels its wake, through sudo" grep -q "sudo .*pmset repeat cancel" "$WORK/system.log"
  check "... says it is done" contains "$UN_OUT" "bosun is uninstalled"
  check "... says where your settings are" contains "$UN_OUT" "your settings: bosun.env"
  check "... leaves a checkout you made" test -x "$ROOT/bin/bosun"
  check "... and says why" contains "$UN_OUT" "not made by the installer"
  check "... leaves a file it does not know, and names it" contains "$UN_OUT" "a-file-of-mine"
  check "a second run finds nothing to do" contains "$(un --yes 2>&1)" "Nothing of bosun is left"
  check "uninstall unloads and removes the job" bash -c 'grep -q "launchctl bootout gui/$(id -u)/$2" "$1" && [ ! -e "$3" ]' _ "$WORK/system.log" "$BOSUN_LAUNCHD_LABEL" "$PLIST"
  check "uninstall unloads and removes the watchdog agent" bash -c 'grep -q "launchctl bootout gui/$(id -u)/$2.watchdog" "$1" && [ ! -e "$3" ]' _ "$WORK/system.log" "$BOSUN_LAUNCHD_LABEL" "$WATCHDOG_PLIST"
  check "uninstall unloads and removes the job that runs the morning run now" bash -c 'grep -q "launchctl bootout gui/$(id -u)/$2.now" "$1" && [ ! -e "$3" ]' _ "$WORK/system.log" "$BOSUN_LAUNCHD_LABEL" "$NOW_PLIST"
  check "uninstall unloads the learning agent" grep -q "launchctl bootout gui/$(id -u)/$BOSUN_LAUNCHD_LABEL.learn" "$WORK/system.log"
  check "uninstall removes the menu bar app, and leaves ~/Applications" bash -c '[ ! -e "$1" ] && [ -d "$2" ]' _ "$APP" "$APP_DIR"
  check "... and unloads and removes its Open at Login job" bash -c 'grep -q "launchctl bootout gui/$(id -u)/$2.app" "$1" && [ ! -e "$3" ]' _ "$WORK/system.log" "$BOSUN_LAUNCHD_LABEL" "$APP_JOB"
  check "uninstall removes the link" test ! -L "$HOME/.local/bin/bosun"
  check "uninstall keeps the config without --purge" test -f "$INSTALL_CFG/bosun.env"
  un_purge() {
    un --yes --purge >/dev/null 2>&1
    [ ! -e "$INSTALL_CFG/bosun.env" ]
  }
  check "--purge takes that too" un_purge
  # The app tests' tools go, and osascript is the plain one again (no real notifications from tests).
  rm -f "$HOME/.local/bin/curl" "$HOME/.local/bin/swift" "$HOME/.local/bin/iconutil"
  for tool in ditto codesign spctl open osascript; do plain_fake "$tool"; done

  section init "init (fake launchctl and pmset, test label)" needs install covers 'install.sh,uninstall.sh,lib/init*.py,lib/claude_state.py,lib/run_locked.py,libexec/bosun-init,templates/launchd*.plist'
  export BOSUN_SKIP_PREFLIGHT=1 # what bosun needs has its own tests, on a made-up machine
  INIT_CFG="$WORK/init-config"
  INIT_REPO="$HOME/Development/init-api"
  git init -q "$INIT_REPO"
  git -C "$INIT_REPO" remote add origin git@github.com:acme/init-api.git
  # No wake in these answers: `sudo pmset` would find the real pmset, whatever PATH says here.
  INIT_ANSWERS="$WORK/init-answers.json"
  printf '{"repos": [{"dir": "%s"}], "repos_dir": "%s", "days": "MWF", "start": "09:00", "until": "18:00", "wake": false}' \
    "$INIT_REPO" "$HOME/Development" >"$INIT_ANSWERS"
  INIT_PLAN="$(BOSUN_CONFIG_DIR="$INIT_CFG" "$BOSUN" init 2>&1)"
  check "init on its own prints the plan" contains "$INIT_PLAN" "Nothing has changed yet"
  check "... and changes nothing" test ! -e "$INIT_CFG"
  check "init --dry-run changes nothing" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c '"$1" init --answers "$2" --dry-run >/dev/null && [ ! -e "$BOSUN_CONFIG_DIR" ]' _ "$BOSUN" "$INIT_ANSWERS"
  # (--print-answers takes --detect's way past the lock and the preflight: test_init.py holds its JSON.)
  check "init refuses bad answers" fails_with "unknown answers" env BOSUN_CONFIG_DIR="$INIT_CFG" "$BOSUN" init --answers <(echo '{"typo": 1}')
  hold_lock "$BOSUN_STATE_DIR/init.lock" init
  check "a second bosun init while one runs is refused at once" fails_with "bosun init is already running" env BOSUN_CONFIG_DIR="$INIT_CFG" "$BOSUN" init --answers "$INIT_ANSWERS"
  check "... while init --detect still answers, in JSON (it reads only, so it takes no lock)" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c '"$1" init --detect | jq -e .home >/dev/null' _ "$BOSUN"
  check "... and changes nothing" test ! -e "$INIT_CFG"
  release_lock init
  BOSUN_CONFIG_DIR="$INIT_CFG" "$BOSUN" init --answers "$INIT_ANSWERS" >"$WORK/init.out" 2>&1
  init_rc=$?
  init_said="$(cat "$WORK/init.out")"
  # This made-up machine fails some of doctor's checks (gh is not logged in, ...): bosun is set up all the same.
  check "init sets bosun up, and exits 3 for the problems doctor found here, which it says last" bash -c '[ "$1" -eq 3 ] && grep -q "bosun is set up\. " <<<"$2" && tail -n 1 <<<"$2" | grep -q "run bosun doctor again" && ! grep -q "install.sh stopped" <<<"$2"' _ "$init_rc" "$init_said"
  check "... with no wake among them: the answers said no to it, which bosun.env records" bash -c '! sed -n "/found problems:/,\$p" <<<"$2" | grep -q "pmset" && grep -qx "BOSUN_WAKE=\"0\"" "$1/bosun.env"' _ "$INIT_CFG" "$init_said"
  check "... install.sh prints no sudo pmset line against it" bash -c 'grep -q "not bosun'"'"'s to set (BOSUN_WAKE=0)" <<<"$1" && ! grep -q "run once:  sudo pmset" <<<"$1"' _ "$init_said"
  check "... and doctor says the wake was declined, in a warning" contains "$init_said" "warn  no repeating pmset wake, and bosun sets none (BOSUN_WAKE=0)"
  check "init writes the config" bash -c 'grep -q "BOSUN_DAYS=\"MWF\"" "$1/bosun.env" && grep -q "name: init-api" "$1/sessions.yml" && [ -f "$1/setup.sh" ]' _ "$INIT_CFG"
  check "what it writes loads as config" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c 'source "$1/lib/common.sh"; load_config; [ "$BOSUN_START_TIME" = 09:00 ] && [ "$BOSUN_EVENTS_ENABLED" = 1 ] && [ "$BOSUN_REPOS_DIR" = "$HOME/Development" ]' _ "$ROOT"
  check "... and as windows" env BOSUN_CONFIG_DIR="$INIT_CFG" bash -c 'source "$1/lib/common.sh"; load_config; window_specs | grep -q "^init-api	$HOME/Development/init-api	session"' _ "$ROOT"
  check "init trusts the folders" python3 "$ROOT/lib/claude_state.py" trusted "$INIT_REPO"
  check "init loads the jobs through install.sh, with valid plists" bash -c 'plutil -lint -s "$1" && plutil -lint -s "$2" && grep -q "launchctl bootstrap gui/$(id -u) $1" "$3"' _ "$PLIST" "$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.learn.plist" "$WORK/system.log"
  check "the learning agent is valid XML, not only what plutil lets through" python3 -c 'import plistlib, sys; plistlib.loads(open(sys.argv[1], "rb").read())' "$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.learn.plist"
  check "... at the time the answers imply" grep -q "<key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer>" "$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.learn.plist"
  BOSUN_CONFIG_DIR="$INIT_CFG" BOSUN_STATE_DIR="$WORK/un-state" BOSUN_LOG_DIR="$WORK/un-state/logs" \
    BOSUN_SESSION_NAME=un-test "$ROOT/uninstall.sh" --yes >/dev/null 2>&1
  unset BOSUN_SKIP_PREFLIGHT

  section codex-launch "a window on Codex (launch's dry run)" covers 'libexec/bosun-launch,libexec/bosun-hook,libexec/bosun-preflight,libexec/bosun-sessions,libexec/bosun-trust,tests/sandbox/codex-stand-in,lib/codex_state.py'
  CX_HOME="$WORK/cx-home"
  CX_REPO="$WORK/cx-repo"
  mkdir -p "$CX_HOME/sessions/2026/10/30" "$CX_REPO"
  git -C "$CX_REPO" init -q
  printf 'sessions:\n  - name: cx\n    dir: %s\n    harness: codex\n  - name: cl\n    dir: %s\n' "$CX_REPO" "$CX_REPO" >"$WORK/cx-sessions.yml"
  # Codex's stand-in lists the servers in $WORK/cx-servers.json (`codex mcp list --json`); its listing
  # fails when CODEX_STAND_IN_MCP_LIST names a file that is not there.
  printf '[{"name":"acme-docs"},{"name":"acme.mail"}]' >"$WORK/cx-servers.json"
  CX() { (cd "$CX_REPO" && BOSUN_CODEX_BIN="$ROOT/tests/sandbox/codex-stand-in" CODEX_STAND_IN_MCP_LIST="${CODEX_STAND_IN_MCP_LIST:-$WORK/cx-servers.json}" CODEX_HOME="$CX_HOME" BOSUN_SESSIONS_FILE="$WORK/cx-sessions.yml" BOSUN_LAUNCH_DRY_RUN=1 BOSUN_EVENTS_ENABLED=0 "$BOSUN" "$@" 2>&1); }
  out="$(CX launch cx)"
  check "a new Codex session starts with what keeps it unattended" contains "$out" "--no-daemon --approve-for-me"
  check "... can reach git and bosun's state" contains "$out" "sandbox_workspace_write.writable_roots"
  check "... and the worktrees folder beside its repository" contains "$out" "\\\"$(cd "$CX_REPO" && pwd -P).worktrees\\\""
  check "... reports when a turn ends, by the agent's own name" contains "$out" "turn-end"
  check "... not by the name only Codex had" not_contains "$out" "codex-turn-end"
  check "... and always has a first prompt" contains "$out" 'nothing\ to\ do\ yet'
  check "... with the model only if one is set" not_contains "$out" "--model"
  # A prompt and the two models, each its own argument: one launch.
  out="$(BOSUN_CODEX_MODEL=gpt-test BOSUN_MODEL=opus CX launch cx '-starts with a dash')"
  check "a prompt it was given goes after --" contains "$out" "-- -starts\\ with\\ a\\ dash"
  check "BOSUN_CODEX_MODEL applies to a Codex window" contains "$out" "--model gpt-test"
  check "... and not BOSUN_MODEL" not_contains "$out" "--model opus"
  check "a window without a harness: stays on Claude Code" contains "$(CX launch cl)" "$WORK/fake-claude"
  out="$(BOSUN_KNOWLEDGE_ENABLED=1 CX launch cx)"
  check "with the ontology on, a Codex window's launch carries none of its rules (its brief carries the pack)" bash -c '
    grep -q -- "--no-daemon" <<<"$1" && ! grep -q -- "--append-system-prompt" <<<"$1" && ! grep -qE "Look.before.asking|The.ontology" <<<"$1"' _ "$out"
  out="$(BOSUN_HARNESS=codex BOSUN_MODEL=opus CX launch not-listed)"
  check "a window sessions.yml does not list runs on BOSUN_HARNESS" contains "$out" "--no-daemon"
  check "... with Codex's default model, not BOSUN_MODEL" not_contains "$out" "--model opus"
  out="$(BOSUN_CODEX_MODEL=gpt-test CX sessions --json)"
  check "sessions says which agent a window is configured for" contains "$(jq -r '.[] | select(.name == "cx") | .harness' <<<"$out")" "codex"
  check "sessions knows its model" contains "$(jq -r '.[] | select(.name == "cx") | .model' <<<"$out")" "gpt-test"
  printf '{"type":"session_meta","payload":{"id":"cx-1","cwd":"%s","source":"cli"}}\n' "$(cd "$CX_REPO" && pwd -P)" >"$CX_HOME/sessions/2026/10/30/rollout-2026-10-30T00-00-00-cx-1.jsonl"
  CX hook turn-end --harness codex --window cx --conversation cx "{\"thread-id\":\"cx-1\",\"cwd\":\"$CX_REPO\"}" >/dev/null
  CX hook codex-turn-end --window cx --conversation cx "{\"thread-id\":\"a-helper\",\"cwd\":\"$CX_REPO\"}" >/dev/null
  check "once a turn has ended it resumes that session" wait_for 30 bash -c 'cd "$1" && BOSUN_CODEX_BIN="$5" CODEX_HOME="$2" BOSUN_SESSIONS_FILE="$3" BOSUN_LAUNCH_DRY_RUN=1 "$4" launch cx 2>&1 | grep -q "resume .* cx-1"' _ "$CX_REPO" "$CX_HOME" "$WORK/cx-sessions.yml" "$BOSUN" "$ROOT/tests/sandbox/codex-stand-in"
  check "bosun trust covers each agent in use" bash -c 'cd "$1" && CODEX_HOME="$2" BOSUN_SESSIONS_FILE="$3" "$4" trust --any "$1" >/dev/null 2>&1 && grep -q "trust_level = \"trusted\"" "$2/config.toml"' _ "$CX_REPO" "$CX_HOME" "$WORK/cx-sessions.yml" "$BOSUN"
  check "... and can take it back" bash -c 'cd "$1" && CODEX_HOME="$2" BOSUN_SESSIONS_FILE="$3" "$4" trust --revoke "$1" >/dev/null 2>&1 && ! grep -q trust_level "$2/config.toml"' _ "$CX_REPO" "$CX_HOME" "$WORK/cx-sessions.yml" "$BOSUN"
  check "preflight checks each agent in use" contains "$(CX preflight)" "Codex"

  section codex-connectors "a window on Codex reaches only the servers its config names" needs codex-launch covers 'libexec/bosun-doctor,libexec/bosun-launch,tests/sandbox/codex-stand-in,lib/connectors_codex.py,lib/codex_state.py'
  printf '  - name: cx-docs\n    dir: %s\n    harness: codex\n    connectors: [acme-docs]\n  - name: cx-all\n    dir: %s\n    harness: codex\n    connectors: all\n' "$CX_REPO" "$CX_REPO" >>"$WORK/cx-sessions.yml"
  # codex_launch_args <launch output>: the arguments its dry-run line hands Codex, on one line.
  codex_launch_args() {
    local line
    local -a args
    line="$(grep '^dry-run: would run:' <<<"$1" | tail -1)"
    [ -n "$line" ] || {
      echo "no dry-run line in: $(tail -3 <<<"$1")"
      return 1
    }
    eval "args=(${line#dry-run: would run:})" # printf %q's own quoting, read back
    printf ' %s' "${args[@]}"
    echo
  }
  out="$(codex_launch_args "$(CX launch cx)")"
  check "with no list, every server is switched off by name, in one inline table, a name that is not a bare TOML key quoted" \
    contains "$out" ' -c mcp_servers={acme-docs={enabled=false},"acme.mail"={enabled=false}} '
  check "... and ChatGPT's connectors off" contains "$out" ' --disable apps'
  check "... and no update screen to stall at" contains "$out" ' -c check_for_update_on_startup=false '
  out="$(codex_launch_args "$(CX launch cx-docs)")"
  check "with connectors: [acme-docs], only the other server is switched off" bash -c '
    grep -qF " -c mcp_servers={\"acme.mail\"={enabled=false}} --disable apps" <<<"$1"' _ "$out"
  out="$(codex_launch_args "$(CX launch cx-all)")"
  check "with connectors: all, nothing is switched off" bash -c '! grep -qF mcp_servers <<<"$1" && ! grep -qF -- --disable <<<"$1"' _ "$out"
  check "... and there is still no update screen" contains "$out" ' -c check_for_update_on_startup=false '
  # A plugin's server: in the listing, but not in the one with --disable plugins.
  printf '[{"name":"acme-docs"},{"name":"acme.mail"},{"name":"acme-kit"}]' >"$WORK/cx-servers-plugins.json"
  CXP() { CODEX_STAND_IN_MCP_LIST="$WORK/cx-servers-plugins.json" CODEX_STAND_IN_MCP_LIST_NO_PLUGINS="$WORK/cx-servers.json" CX "$@"; }
  out="$(codex_launch_args "$(CXP launch cx-docs)")"
  check "a plugin's server the list does not name switches every plugin off, and is not in the table" bash -c '
    grep -qF " -c mcp_servers={\"acme.mail\"={enabled=false}} --disable plugins --disable apps" <<<"$1" && ! grep -qF acme-kit <<<"$1"' _ "$out"
  out="$(codex_launch_args "$(BOSUN_CONNECTORS=acme-kit CXP launch cx)")"
  check "... and a list that names it keeps plugins on" bash -c '
    grep -qF " -c mcp_servers={acme-docs={enabled=false},\"acme.mail\"={enabled=false}} --disable apps" <<<"$1" && ! grep -qF -- "--disable plugins" <<<"$1"' _ "$out"
  # A window whose plugins: [] (its connectors all, its own), whose doctor line comes from the same run as cx's.
  printf '  - name: cx-noplugins\n    dir: %s\n    harness: codex\n    connectors: all\n    plugins: []\n' "$CX_REPO" >>"$WORK/cx-sessions.yml"
  out="$(BOSUN_CONNECTORS=acme-docs CXP doctor)"
  check "... and doctor says every plugin goes" contains "$out" \
    "cx: reaches acme-docs (switches off acme.mail, every plugin (with acme-kit) and ChatGPT's connectors)"
  check "... as it does for a Codex window with plugins: []" contains "$out" \
    "ok    cx-noplugins: plugins: none of yours (Codex switches every plugin off: it cannot keep some and not others)"
  out="$(BOSUN_CONNECTORS="acme-docs, https://mcp.example.com/*" CX doctor)"
  check "doctor says what a Codex window reaches, from its server names" contains "$out" \
    "ok    cx-docs: reaches acme-docs (switches off acme.mail and ChatGPT's connectors)"
  check "... and that a URL means nothing to Codex" contains "$out" \
    "warn  cx: reaches acme-docs (switches off acme.mail and ChatGPT's connectors); mean nothing to Codex: https://mcp.example.com/*"
  out="$(BOSUN_CONNECTORS="acme-docs, acme-gone" CX launch cx)"
  check "a Codex launch says which names of its list it has no server by" contains "$out" "cx: connectors: acme-gone not found, so this window starts without it"
  check "... in the launch log too" grep -qF "cx: connectors: acme-gone not found, so this window starts without it" "$BOSUN_LOG_DIR/launch-$(date +%F).log"
  out="$(CODEX_STAND_IN_MCP_LIST="$WORK/no-such-listing" CX launch cx)"
  listing_rc=$?
  check "a listing that fails keeps the window from starting" bash -c '[ "$1" -ne 0 ] && grep -q "codex mcp list failed" <<<"$2"' _ "$listing_rc" "$out"
  check "... and says why, without starting Codex" bash -c 'grep -q "so bosun cannot tell which servers to switch off" <<<"$1" && ! grep -q "dry-run: would run" <<<"$1"' _ "$out"
  broken_listing_cx() { CODEX_STAND_IN_MCP_LIST="$WORK/no-such-listing" CX "$@"; }
  check "... and so does its fingerprint, which an update reads as the window changed" fails_with "codex mcp list failed" broken_listing_cx launch --fingerprint cx
  out="$(CX launch --fingerprint cx)"
  check "... while a listing that works gives one" bash -c 'jq -e ".args | length > 0" <<<"$1" >/dev/null' _ "$out"
  # codex_args_valid <list> <lines...>: "valid" or "refused", as lib/launch/codex.sh judges what
  # connectors_codex.py would print for <list> and the plugin list $CX_PLUGINS (default all). A crash prints
  # neither, so it is no answer to either check.
  codex_args_valid() {
    bash -c 'set -u; BOSUN_CODEX_BIN=codex; source "$1/lib/common.sh" && source "$1/lib/launch/codex.sh" || exit 3; list="$2"; shift 2; codex_connector_args=("$@")
      if codex_connector_args_valid "$list" "${CX_PLUGINS:-all}"; then echo valid; else echo refused; fi' _ "$ROOT" "$@"
  }
  outputs() { # <want> <command...>: the command prints exactly <want>
    local got
    got="$("${@:2}" 2>&1)" && [ "$got" = "$1" ] || {
      echo "wanted: $1; got: $got"
      return 1
    }
  }
  # The shape only: nothing for all, else an optional -c mcp_servers={...}, an optional --disable plugins,
  # then --disable apps. What the table holds is connectors_codex.py's (its unit tests read it back as TOML).
  check "the shape -c mcp_servers={...}, --disable plugins, --disable apps is taken" outputs valid codex_args_valid "" -c 'mcp_servers={acme-docs={enabled=false},"acme.mail"={enabled=false}}' --disable plugins --disable apps
  check "... and without --disable plugins" outputs valid codex_args_valid "" -c 'mcp_servers={acme-docs={enabled=false}}' --disable apps
  check "... and without the table" outputs valid codex_args_valid "acme-docs" --disable plugins --disable apps
  check "... and --disable apps alone" outputs valid codex_args_valid "acme-docs" --disable apps
  check "... and nothing for all" outputs valid codex_args_valid " all "
  check "... but nothing for any other list is no answer" outputs refused codex_args_valid "acme-docs"
  check "... nor -c cut short" outputs refused codex_args_valid "" -c --disable apps
  check "... nor -c with anything but an mcp_servers table, such as the form Codex splits at every dot" outputs refused codex_args_valid "" -c 'mcp_servers."acme.mail".enabled=false' --disable apps
  check "... nor two tables" outputs refused codex_args_valid "" -c 'mcp_servers={a={enabled=false}}' -c 'mcp_servers={b={enabled=false}}' --disable apps
  check "... nor a missing --disable apps" outputs refused codex_args_valid "" -c 'mcp_servers={acme-docs={enabled=false}}' --disable plugins
  check "... nor the two --disable pairs the other way round" outputs refused codex_args_valid "" --disable apps --disable plugins
  check "... nor more after --disable apps" outputs refused codex_args_valid "" --disable apps -c 'mcp_servers={acme-docs={enabled=false}}'
  check "... nor extra text after it" outputs refused codex_args_valid "" --disable apps extra
  check "... nor an unknown flag" outputs refused codex_args_valid "" --frob --disable apps
  # A plugin list other than all: Codex can only switch every plugin off, so --disable plugins must be there.
  codex_plugins_valid() { (export CX_PLUGINS="$1" && codex_args_valid "${@:2}"); } # <plugin list> <list> <lines...>
  check "with a plugin list, --disable plugins is taken" outputs valid codex_plugins_valid acme-kit "acme-docs" --disable plugins --disable apps
  check "... and is no longer optional" outputs refused codex_plugins_valid acme-kit "acme-docs" --disable apps
  check "... and for the connectors all it is the whole answer" outputs valid codex_plugins_valid " " all --disable plugins
  check "... where nothing is no answer" outputs refused codex_plugins_valid " " all
  check "... nor --disable apps after it" outputs refused codex_plugins_valid " " all --disable plugins --disable apps
  out="$(codex_launch_args "$(CXP launch cx-noplugins)")"
  check "a Codex window with plugins: [] switches every plugin off, and nothing else, for connectors: all" bash -c '
    grep -qF -- " --disable plugins" <<<"$1" && ! grep -qF mcp_servers <<<"$1" && ! grep -qF -- "--disable apps" <<<"$1"' _ "$out"

  section agy-launch "a window on agy (launch, with a stand-in that records what it was started with)" needs codex-launch,setup-steps covers 'libexec/bosun-launch,libexec/bosun-morning,libexec/bosun-preflight,libexec/bosun-sessions,tests/sandbox/agy-stand-in,tests/fixtures/agy/**,agents/agy-plugin/**,lib/agy_*.py,lib/connectors_agy.py,libexec/bosun-doctor'
  AGY_REPO="$WORK/agy-repo"
  mkdir -p "$AGY_REPO"
  git -C "$AGY_REPO" init -q
  # What agy is asked, and what it would run with: it answers `--version`, `plugin list|install|uninstall`
  # as the real one does for bosun's setup, and any other call it records (arguments one per line, and its
  # environment) in the files AGY_STAND_IN_RECORD names.
  cat >"$WORK/fake-agy" <<'FAKE'
#!/bin/sh
case "$1 $2" in
  "--version "*)
    echo 1.5.5
    exit 0
    ;;
  "plugin list")
    echo '[]'
    exit 0
    ;;
  "plugin install")
    mkdir -p "$HOME/.gemini/config/plugins" && rm -rf "$HOME/.gemini/config/plugins/bosun" &&
      cp -R "$3" "$HOME/.gemini/config/plugins/bosun"
    exit $?
    ;;
  "plugin uninstall")
    rm -rf "$HOME/.gemini/config/plugins/bosun"
    exit 0
    ;;
esac
[ -n "$AGY_STAND_IN_RECORD" ] || exit 0
printf '%s\n' "$@" >"$AGY_STAND_IN_RECORD.args"
env >"$AGY_STAND_IN_RECORD.env"
FAKE
  chmod +x "$WORK/fake-agy"
  printf 'sessions:\n  - name: agyw\n    dir: %s\n    harness: agy\n  - name: agy-docs\n    dir: %s\n    harness: agy\n    connectors: [acme-docs]\n' "$AGY_REPO" "$AGY_REPO" >"$WORK/agy-sessions.yml"
  AGY() { (cd "$AGY_REPO" && BOSUN_AGY_BIN="$WORK/fake-agy" BOSUN_SESSIONS_FILE="$WORK/agy-sessions.yml" BOSUN_EVENTS_ENABLED=0 "$BOSUN" "$@" 2>&1); }
  AGYD() { BOSUN_LAUNCH_DRY_RUN=1 AGY "$@"; }
  # (the suite exports BOSUN_LAUNCH_DRY_RUN=1: a real launch of the stand-in says BOSUN_LAUNCH_DRY_RUN=0)
  # agy_flag <flag>: the argument the stand-in got after <flag>, from its last real run
  agy_flag() { awk -v flag="$1" 'found { print; exit } $0 == flag { found = 1 }' "$WORK/agy-run.args"; }
  agy_env() { grep -m1 "^$1=" "$WORK/agy-run.env" | cut -d= -f2-; } # <name>: its value in that run's environment
  # One dry run (with the ontology on, which changes nothing of an agy window's launch) read for what a new
  # window's launch holds.
  out="$(BOSUN_KNOWLEDGE_ENABLED=1 AGYD launch agyw)"
  check "a new agy window's dry run says what it would run" contains "$out" "dry-run: would run: $WORK/fake-agy"
  check "... with its folder trusted by --settings, in auto" contains "$out" 'agentMode'
  check "with the ontology on, an agy window's launch carries none of the ontology's rules" bash -c '
    grep -q "dry-run: would run:" <<<"$1" && ! grep -q -- "--append-system-prompt" <<<"$1" && ! grep -qE "Look.before.asking|The.ontology" <<<"$1"' _ "$out"
  check "... a log file named for its conversation, and Remote Control" bash -c 'grep -qF -- "--log-file $2/agy-agyw.log --remote-control" <<<"$1"' _ "$out" "$BOSUN_LOG_DIR"
  check "... and a first prompt of its own, ready to be answered" contains "$out" "--prompt-interactive=This\\ is\\ bosun\\'s\\ agyw\\ window"
  check "... none of the flags an agy window never gets" bash -c '! grep -qE -- " (--add-dir|--mode|-c|--continue|--dangerously-skip-permissions)( |$)" <<<"$1"' _ "$out"
  check "... and the model only if one is set" not_contains "$out" "--model"
  check "... the effort the window is configured with (BOSUN_EFFORT)" contains "$out" "--effort medium"
  check "the dry run shows the environment it would set: git's config that outranks every repo's" contains "$out" "dry-run: would set:"
  check "... with the hooks off" bash -c 'grep -q "GIT_CONFIG_VALUE_[0-9]*=/dev/null" <<<"$1" && grep -q "GIT_CONFIG_KEY_[0-9]*=core.hooksPath" <<<"$1"' _ "$out"
  check "a dry run records nothing about a start" test ! -e "$BOSUN_STATE_DIR/agy/agyw.json"
  check "... and installs no plugin" test ! -e "$HOME/.gemini/config/plugins/bosun"
  # The effort and the model are arguments of their own (build_agent_args): one launch shows one of each.
  effort_xhigh="$(AGYD launch --effort xhigh --model gemini-3.8-flash-low agyw)"
  effort_max="$(AGYD launch --effort max --model gemini-3.8-pro-medium agyw)"
  effort_medium="$(AGYD launch --effort medium --model gemini-3.8-pro-high agyw)"
  check "an effort above high is passed as high" contains "$effort_xhigh" "--effort high"
  check "... also max" contains "$effort_max" "--effort high"
  check "... and a lower one as it is" contains "$effort_medium" "--effort medium"
  check "a model name loses its effort suffix, which --effort carries" contains "$effort_xhigh" "--model gemini-3.8-flash "
  check "... also -medium and -high" bash -c 'grep -qF -- "--model gemini-3.8-pro " <<<"$1" && grep -qF -- "--model gemini-3.8-pro " <<<"$2"' _ "$effort_max" "$effort_medium"
  out="$(BOSUN_AGY_MODEL=gemini-3.8-flash BOSUN_MODEL=opus AGYD launch agyw)"
  check "BOSUN_AGY_MODEL is an agy window's model" contains "$out" "--model gemini-3.8-flash"
  check "... and BOSUN_MODEL is not" not_contains "$out" "--model opus"
  # No effort, flags of its own and a prompt that starts with a dash, each its own part of the command: one launch.
  out="$(BOSUN_EFFORT="" BOSUN_AGY_FLAGS='--acme-flag' AGYD launch agyw '-starts with a dash')"
  check "... and none when it has none" bash -c 'grep -q "dry-run: would run:" <<<"$1" && ! grep -q -- "--effort" <<<"$1"' _ "$out"
  check "BOSUN_AGY_FLAGS come first" contains "$out" "$WORK/fake-agy --acme-flag --settings"
  check "a prompt that starts with a dash is no flag" contains "$out" '--prompt-interactive=-starts\ with\ a\ dash'
  check "bosun launch --fingerprint covers an agy window" bash -c 'jq -e ".args | length > 0" <<<"$1" >/dev/null' _ "$(AGY launch --fingerprint agyw)"
  check "... and writes no status for it" test ! -e "$BOSUN_STATE_DIR/agy/agyw.json"

  mkdir -p "$BOSUN_STATE_DIR/agy"
  printf '{"status":"idle","statusUpdatedAt":5,"conversationId":"old-run","version":"1.5.4","lastSaid":"hello"}' >"$BOSUN_STATE_DIR/agy/agyw.json"
  rm -f "$WORK/agy-run.args" "$WORK/agy-run.env"
  out="$(BOSUN_LAUNCH_DRY_RUN=0 SSH_CLIENT="192.0.2.1 22 22" SSH_CONNECTION="192.0.2.1 22 192.0.2.2 22" GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=acme AGY_STAND_IN_RECORD="$WORK/agy-run" AGY launch agyw)"
  check "a real launch starts agy" test -s "$WORK/agy-run.args"
  check "... with --settings one JSON object: auto, and the folder as given" bash -c 'jq -e --arg dir "$2" ". == {agentMode: \"auto\", trustedWorkspaces: [\$dir]}" <<<"$1" >/dev/null' _ "$(agy_flag --settings)" "$AGY_REPO"
  check "... the log file for its conversation" test "$(agy_flag --log-file)" = "$BOSUN_LOG_DIR/agy-agyw.log"
  check "... and its prompt last" bash -c 'tail -n 1 "$1" | grep -q "^--prompt-interactive=This is bosun"' _ "$WORK/agy-run.args"
  check "its environment is the marker: the window" test "$(agy_env BOSUN_WINDOW)" = agyw
  check "... the conversation name" test "$(agy_env BOSUN_CONVERSATION)" = agyw
  check "... the install link its hooks run" test "$(agy_env BOSUN_HOOK_CMD)" = "$ROOT/bin/bosun"
  check "... and no resume" test -z "$(agy_env BOSUN_RESUMED)"
  check "... with its own updates off" test "$(agy_env AGY_CLI_DISABLE_AUTO_UPDATE)" = true
  check "... and no ssh variables, which would hide its login" bash -c '! grep -qE "^SSH_(CLIENT|CONNECTION)=" "$1"' _ "$WORK/agy-run.env"
  check "git's config is appended after what the environment held" bash -c '
    env=$1
    [ "$(grep -m1 ^GIT_CONFIG_COUNT= "$env")" = GIT_CONFIG_COUNT=10 ] &&
    grep -qx GIT_CONFIG_KEY_0=user.name "$env" && grep -qx GIT_CONFIG_VALUE_0=acme "$env"' _ "$WORK/agy-run.env"
  check "... once, though a launch builds its arguments twice" bash -c '! grep -q "^GIT_CONFIG_KEY_10=" "$1"' _ "$WORK/agy-run.env"
  for pair in safe.bareRepository=explicit core.fsmonitor=false core.hooksPath=/dev/null core.editor=false sequence.editor=false core.pager=cat submodule.recurse=false fetch.recurseSubmodules=false diff.ignoreSubmodules=all; do
    check "... holds ${pair%%=*}=${pair#*=}" bash -c '
      env=$1; key=${2%%=*}; value=${2#*=}
      n=$(grep -x "GIT_CONFIG_KEY_[0-9]*=$key" "$env" | tail -1 | sed "s/GIT_CONFIG_KEY_\([0-9]*\)=.*/\1/")
      [ -n "$n" ] && [ "$n" -ge 1 ] && grep -qxF "GIT_CONFIG_VALUE_$n=$value" "$env"' _ "$WORK/agy-run.env" "$pair"
  done
  check "a window has no person at an editor or a pager, in git and in gh" bash -c '
    env=$1
    for pair in GIT_EDITOR=false GIT_PAGER=cat GH_EDITOR=false GH_PAGER=cat GH_PROMPT_DISABLED=1; do grep -qx "$pair" "$env" || exit 1; done' _ "$WORK/agy-run.env"
  check "a launch forgets what the last run left in the status file, and records its version and folder" bash -c '
    jq -e --arg dir "$2" ". == {version: \"1.5.5\", lastSaid: \"hello\", folder: \$dir}" "$1" >/dev/null' _ "$BOSUN_STATE_DIR/agy/agyw.json" "$(cd "$AGY_REPO" && pwd)"
  check "the first launch on agy sets up bosun's plugin" test -f "$HOME/.gemini/config/plugins/bosun/plugin.json"

  CONV_DIR="$HOME/.gemini/antigravity-cli/conversations"
  mkdir -p "$CONV_DIR"
  : >"$CONV_DIR/conv-1.db"
  jq -n --arg cwd "$(cd "$AGY_REPO" && pwd -P)" '{agyw: {id: "conv-1", cwd: $cwd}}' >"$BOSUN_STATE_DIR/agy-sessions.json"
  out="$(AGYD launch agyw)"
  check "a resume prints --conversation <id>" bash -c 'grep -qF -- "--conversation conv-1" <<<"$1"' _ "$out"
  check "... and says in its environment which conversation it asked for" contains "$out" "BOSUN_RESUMED=conv-1"
  rm -f "$WORK/agy-run.args" "$WORK/agy-run.env"
  BOSUN_LAUNCH_DRY_RUN=0 AGY_STAND_IN_RECORD="$WORK/agy-run" AGY launch agyw 'carry on' >/dev/null
  check "a real resume names the conversation before the prompt" bash -c '
    args=$1; n=$(grep -nx -- --conversation "$args" | cut -d: -f1)
    [ -n "$n" ] && [ "$(sed -n "$((n + 1))p" "$args")" = conv-1 ] && [ "$(tail -n 1 "$args")" = "--prompt-interactive=carry on" ]' _ "$WORK/agy-run.args"
  check "... tells bosun's hooks which conversation it asked for" test "$(agy_env BOSUN_RESUMED)" = conv-1
  rm -f "$WORK/agy-run.args" "$WORK/agy-run.env"
  BOSUN_LAUNCH_DRY_RUN=0 AGY_STAND_IN_RECORD="$WORK/agy-run" AGY launch --no-prompt agyw >/dev/null
  check "a resume with no prompt has none" bash -c '! grep -q -- "--prompt-interactive" "$1" && grep -qx conv-1 "$1"' _ "$WORK/agy-run.args"
  check "... and the window is on record at once, idle, with the conversation it resumed (agy fires no hook before a prompt)" bash -c '
    jq -e ".conversationId == \"conv-1\" and .status == \"idle\" and .statusUpdatedAt > 5" "$1" >/dev/null' _ "$BOSUN_STATE_DIR/agy/agyw.json"
  rm "$CONV_DIR/conv-1.db"
  check "... and a conversation that agy no longer has is started anew" bash -c '! grep -qF -- "--conversation" <<<"$1"' _ "$(AGYD launch agyw)"

  mkdir -p "$HOME/.gemini/config"
  printf '{"mcpServers":{"acme-docs":{},"acme-mail":{}}}' >"$HOME/.gemini/config/mcp_config.json"
  out="$(AGYD launch agyw || true)"
  check "a server beyond the window's list keeps it from starting" bash -c 'grep -q "acme-docs (global) is not in this window" <<<"$1" && grep -q "agyw. does not start" <<<"$1" && ! grep -q "dry-run: would run" <<<"$1"' _ "$out"
  out="$(AGYD launch agy-docs || true)"
  check "... also with one of the two named" bash -c 'grep -q "acme-mail (global)" <<<"$1" && ! grep -q "dry-run: would run" <<<"$1"' _ "$out"
  check "... but a list naming them all starts it" contains "$(BOSUN_CONNECTORS=all AGYD launch agyw)" "dry-run: would run"
  check "... and a refusal is logged like Codex's" grep -qF "agyw: connectors: acme-mail (global)" "$BOSUN_LOG_DIR/launch-$(date +%F).log"
  rm -f "$HOME/.gemini/config/mcp_config.json"
  mkdir -p "$AGY_REPO/.agents"
  echo '{}' >"$AGY_REPO/.agents/hooks.json"
  AGY_REPO_REAL="$(cd "$AGY_REPO" && pwd -P)"
  out="$(AGYD launch agyw || true)"
  check "a workspace hooks.json keeps an agy window from starting, saying where it is" contains "$out" "'agyw' does not start: $AGY_REPO_REAL/.agents/hooks.json would run outside agy's sandbox when agy starts (bosun does not start agy windows with workspace hooks, plugins or servers)"
  check "... and nothing is started" not_contains "$out" "dry-run: would run"
  rm -rf "$AGY_REPO/.agents"
  check "... and without it the window starts" contains "$(AGYD launch agyw)" "dry-run: would run"
  mkdir -p "$WORK/log dir"
  check "a log folder with a space keeps an agy window from starting" fails_with "'agyw' does not start: bosun's log folder \"$WORK/log dir\" has a space in it, which agy windows cannot use yet" env BOSUN_LOG_DIR="$WORK/log dir" BOSUN_AGY_BIN="$WORK/fake-agy" BOSUN_SESSIONS_FILE="$WORK/agy-sessions.yml" BOSUN_LAUNCH_DRY_RUN=1 BOSUN_EVENTS_ENABLED=0 bash -c 'cd "$1" && "$2" launch agyw' _ "$AGY_REPO" "$BOSUN"
  check "a plugin list keeps an agy window from starting, saying why" fails_with \
    "plugins: agy cannot switch plugins off per window, so its plugin list must be all" \
    env BOSUN_PLUGINS="" BOSUN_AGY_BIN="$WORK/fake-agy" BOSUN_SESSIONS_FILE="$WORK/agy-sessions.yml" BOSUN_LAUNCH_DRY_RUN=1 BOSUN_EVENTS_ENABLED=0 bash -c 'cd "$1" && "$2" launch agyw' _ "$AGY_REPO" "$BOSUN"
  check "... and doctor says it does not start" contains "$(BOSUN_PLUGINS="" BOSUN_SESSIONS_FILE="$WORK/agy-sessions.yml" BOSUN_AGY_BIN="$WORK/fake-agy" "$BOSUN" doctor 2>&1)" \
    "warn  agyw: does not start: agy cannot switch plugins off per window"
  printf '#!/bin/sh\nexit 1\n' >"$WORK/failing-agy"
  chmod +x "$WORK/failing-agy"
  check "a listing that fails keeps it from starting too" fails_with "does not start" env BOSUN_AGY_BIN="$WORK/failing-agy" BOSUN_SESSIONS_FILE="$WORK/agy-sessions.yml" BOSUN_LAUNCH_DRY_RUN=1 BOSUN_EVENTS_ENABLED=0 bash -c 'cd "$1" && "$2" launch agyw' _ "$AGY_REPO" "$BOSUN"
  check "an agy that is not installed is said so, not left to the connectors check" fails_with "its agent, /nonexistent/agy, is not installed for this window's shell" env BOSUN_AGY_BIN=/nonexistent/agy BOSUN_SESSIONS_FILE="$WORK/agy-sessions.yml" BOSUN_LAUNCH_DRY_RUN=0 BOSUN_EVENTS_ENABLED=0 bash -c 'cd "$1" && "$2" launch agyw' _ "$AGY_REPO" "$BOSUN"
  check "... and so is a Codex that is not installed, which is asked for its servers too" fails_with "its agent, /nonexistent/codex, is not installed for this window's shell" env BOSUN_CODEX_BIN=/nonexistent/codex CODEX_HOME="$CX_HOME" BOSUN_SESSIONS_FILE="$WORK/cx-sessions.yml" BOSUN_LAUNCH_DRY_RUN=0 BOSUN_EVENTS_ENABLED=0 bash -c 'cd "$1" && "$2" launch cx' _ "$CX_REPO" "$BOSUN"
  rm -f "$WORK/agy-run.args" "$WORK/agy-run.env"
  BOSUN_LAUNCH_DRY_RUN=0 GIT_CONFIG_COUNT=08 AGY_STAND_IN_RECORD="$WORK/agy-run" AGY launch agyw >/dev/null
  check "a GIT_CONFIG_COUNT that git would refuse (08, read as octal by the shell) counts as none" test "$(agy_env GIT_CONFIG_COUNT)" = 9
  check "a window on an agent that does not exist lists the ones that do" fails_with "no agent 'acme-agent': bosun knows claude, codex, agy" bash -c 'cd "$1" && BOSUN_LAUNCH_DRY_RUN=1 "$2" launch --harness acme-agent agyw' _ "$AGY_REPO" "$BOSUN"
  check "preflight checks agy, which it names" contains "$(BOSUN_SESSIONS_FILE="$WORK/agy-sessions.yml" BOSUN_AGY_BIN="$WORK/fake-agy" "$BOSUN" preflight 2>&1)" "Antigravity"
  out="$(BOSUN_SESSIONS_FILE="$WORK/agy-sessions.yml" BOSUN_AGY_BIN="$WORK/fake-agy" BOSUN_CONFIG_DIR="$SETUP_CFG" BOSUN_NETWORK_PROBE_URL=file:///dev/null "$BOSUN" morning --dry-run --awake-minutes 5 2>&1)"
  check "the morning run would update agy with its own command" contains "$out" "dry-run: would run $WORK/fake-agy update"

  section agy-windows "windows on agy (real windows, with the stand-in: tests/sandbox/agy-stand-in)" needs agy-launch covers 'libexec/bosun-launch,libexec/bosun-send,libexec/bosun-quit,libexec/bosun-sessions,libexec/bosun-start,libexec/bosun-stop,tests/sandbox/agy-stand-in,tests/fixtures/agy/**,agents/agy-plugin/**,lib/agy_*.py'
  AGY_STAND_IN="$ROOT/tests/sandbox/agy-stand-in"
  AL_SOCKET="$SOCKET-agy"
  AL_STATE="$BOSUN_STATE_DIR"
  AL_HOME="$HOME/.gemini/antigravity-cli"
  rm -rf "$HOME/.gemini" "$AL_STATE/agy" "$AL_STATE/agy-sessions.json" # the stand-in installs bosun's plugin itself
  printf 'sessions:\n  - name: agyl\n    dir: %s\n    harness: agy\n' "$AGY_REPO" >"$WORK/agy-live-sessions.yml"
  AL=(env BOSUN_TMUX_SOCKET="$AL_SOCKET" BOSUN_SESSION_NAME=agy-live BOSUN_SESSIONS_FILE="$WORK/agy-live-sessions.yml"
    BOSUN_AGY_BIN="$AGY_STAND_IN" BOSUN_LAUNCH_DRY_RUN=0 BOSUN_EVENTS_ENABLED=0 BOSUN_ORCHESTRATOR="")
  al() { (cd "$AGY_REPO" && "${AL[@]}" "$BOSUN" "$@" 2>&1); }
  al_tmux() { tmux -L "$AL_SOCKET" "$@"; }
  al_window() { al_tmux send-keys -t "=agy-live:=$1" "${@:2}"; }                                                 # <window> <keys...>
  al_status() { al sessions --json | jq -r --arg w "$1" '.[] | select(.name == $w) | .agent.status // "none"'; } # <window>
  al_has() { [ "$(al_status "$1")" = "$2" ]; }                                                                   # <window> <status>
  al_conversation() { jq -r --arg w "$1" '.[$w].id // empty' "$AL_STATE/agy-sessions.json"; }                    # <window>
  al_screen() { al_tmux capture-pane -p -t "=agy-live:=$1"; }                                                    # <window>
  al_named() { [ -n "$(al_conversation "$1")" ]; }                                                               # <window>
  al_conversations() { find "$AL_HOME/conversations" -name '*.db' 2>/dev/null | wc -l | tr -d ' '; }
  check "the stand-in answers --version, as agy does" test "$("$AGY_STAND_IN" --version)" = 1.5.5
  check "... refuses an unknown key in --settings, as agy does" fails_with 'unknown field "hooks"' "$AGY_STAND_IN" --settings '{"hooks":{}}'
  al start >/dev/null
  check "a window starts, and bosun's plugin was installed into agy by its setup" wait_for 90 bash -c 'jq -e ".[] | select(.name == \"bosun\")" <(HOME="$1" "$2" plugin list --json) >/dev/null' _ "$HOME" "$AGY_STAND_IN"
  check "... records the name of its conversation, from agy's own hook" wait_for 90 al_named agyl
  conversation="$(al_conversation agyl)"
  check "... which agy keeps, and holds open" bash -c '[ -f "$1/conversations/$2.db" ] && [ -f "$1/presence/$2.lock" ]' _ "$AL_HOME" "$conversation"
  check "... and reads idle, from the hooks, once its first prompt is answered" wait_for 90 al_has agyl idle
  check "... and the launch recorded where its screen is, in its status file" jq -e '.pane != null and .socket != null' "$AL_STATE/agy/agyl.json"
  check "bosun sessions says it is agy's" bash -c '[ "$(jq -r ".[] | select(.name == \"agyl\") | .agent.harness" <<<"$1")" = agy ]' _ "$(al sessions --json)"
  check "a message is delivered, a turn runs, and the window reads busy" al send --no-item agyl "sleep 60"
  check "... busy, from the hook before the model call" wait_for 60 al_has agyl busy
  al_window agyl Escape
  check "Esc cancels the turn, which fires no hook, and the screen says idle" wait_for 60 al_has agyl idle
  al_window agyl "please ask" Enter
  check "... a window at a dialog reads waiting" wait_for 60 al_has agyl waiting
  # Refused either way; which of its two refusals says so depends on whether its screen shows the dialog yet.
  check "... and bosun send refuses it, so a pasted Enter never answers it" wait_for 30 fails_with "asking something on its screen" al send --no-item agyl "go on"
  al_window agyl Escape
  check "... Esc cancels it, and the window reads idle again" wait_for 60 al_has agyl idle
  al_window agyl "run git status" Enter
  check "a git command through bosun's plugin is approved, with the sandbox lifted, as bosun's hook answers it" wait_for 60 grep -qF "PreToolUse git status: auto_approve, BypassSandbox true" "$BOSUN_LOG_DIR/agy-agyl.log"
  check "... and the turn ends idle" wait_for 60 al_has agyl idle
  al_window agyl "run date" Enter
  check "a command outside its allow-list gets no answer" wait_for 60 grep -q "PreToolUse date: no answer" "$BOSUN_LOG_DIR/agy-agyl.log"
  check "... and the turn ends idle" wait_for 60 al_has agyl idle
  # A person's own agy, in the same folder with bosun's plugin installed: no BOSUN_* in its environment.
  al_tmux new-window -d -n person -c "$AGY_REPO" "env -u BOSUN_HOOK_CMD -u BOSUN_WINDOW -u BOSUN_CONVERSATION '$AGY_STAND_IN' --log-file '$WORK/person-agy.log' '--prompt-interactive=run git status'"
  check "a person's own agy run gets no answer from any hook, not even for git" wait_for 60 grep -q "PreToolUse git status: no answer" "$WORK/person-agy.log"
  check "... and bosun's hooks wrote no status for it" bash -c '[ "$(ls "$1/agy" | tr "\n" " ")" = "agyl.json " ]' _ "$AL_STATE"
  al_tmux kill-window -t "=agy-live:=person"
  conversations_before="$(al_conversations)"
  al stop >/dev/null
  check "stop leaves no agy of this run running" wait_for 60 bash -c '! pgrep -f "$1 -c.*$2/state/logs" >/dev/null' _ "$AGY_STAND_IN" "$WORK"
  al start >/dev/null
  check "a restart resumes the conversation it had" wait_for 90 al_has agyl idle
  # The launch records an idle resumed window at once, before the stand-in has started and logged: wait for
  # its log, then look at what it kept.
  check "... which the stand-in resumed, as its log says" wait_for 60 grep -q "resumed conversation $conversation" "$BOSUN_LOG_DIR/agy-agyl.log"
  check "... the same one, under the same name" test "$(al_conversation agyl)" = "$conversation"
  check "... and made no new one" test "$(al_conversations)" = "$conversations_before"
  al stop >/dev/null
  wait_for 60 bash -c '! pgrep -f "$1 -c.*$2/state/logs" >/dev/null' _ "$AGY_STAND_IN" "$WORK" # its conversation is held while it runs
  # A resume with no prompt: agy is idle and fires no hook, and bosun already knows the window and its conversation.
  # (the launch is the pane shell's child, as bosun starts it, so that the window's agent is found under the pane)
  al_tmux new-session -d -s agy-live -n agyl -c "$AGY_REPO" "$(printf '%q ' "${AL[@]}" "$BOSUN" launch --no-prompt agyl); sleep 600"
  check "a window resumed with no prompt reads idle at once, a session of the conversation it resumed" wait_for 60 al_has agyl idle
  check "... that conversation, before any prompt" bash -c '[ "$(jq -r ".[] | select(.name == \"agyl\") | .agent.sessionId" <<<"$1")" = "$2" ]' _ "$(al sessions --json)" "$conversation"
  al_tmux kill-server 2>/dev/null
  al stop >/dev/null
  check "... and nothing of it is left running" wait_for 60 bash -c '! pgrep -f "$1 -c.*$2/state/logs" >/dev/null' _ "$AGY_STAND_IN" "$WORK"
  rm -f "$AL_HOME/conversations/$conversation.db"
  al start >/dev/null
  al_renamed() { [ -n "$(al_conversation agyl)" ] && [ "$(al_conversation agyl)" != "$1" ]; } # <the old id>
  check "a window whose conversation is gone starts a new one" wait_for 90 al_renamed "$conversation"
  check "... never under the old id: that conversation stays gone" test ! -e "$AL_HOME/conversations/$conversation.db"
  check "... and reads idle" wait_for 90 al_has agyl idle
  # A resume that agy turns into a fresh conversation (an id it does not have), with the marker bosun's launch sets.
  al_tmux new-window -d -n guard -c "$AGY_REPO" "env BOSUN_WINDOW=guard BOSUN_CONVERSATION=guard BOSUN_HOOK_CMD='$BOSUN' BOSUN_RESUMED=ghost-conversation '$AGY_STAND_IN' --log-file '$BOSUN_LOG_DIR/agy-guard.log' --conversation ghost-conversation '--prompt-interactive=hello'"
  check "a resume that agy makes a fresh conversation of says so on its screen" wait_for 60 bash -c 'tmux -L "$1" capture-pane -p -t "=agy-live:=guard" | grep -q "conversation \"ghost-conversation\" not found"' _ "$AL_SOCKET"
  check "... and bosun stops its first model call, and says why" wait_for 60 bash -c 'tmux -L "$1" capture-pane -p -t "=agy-live:=guard" | grep -q "resumed a different conversation"' _ "$AL_SOCKET"
  check "... so it never answered" bash -c '! tmux -L "$1" capture-pane -p -t "=agy-live:=guard" | grep -q "stand-in\] ok"' _ "$AL_SOCKET"
  al stop >/dev/null
  al_tmux kill-server 2>/dev/null

  section get-sh "get.sh (a dry run: it changes nothing)" covers 'get.sh,install.sh,libexec/bosun-init,libexec/bosun-update'
  check "it is plain sh" sh -n "$ROOT/get.sh"
  check "it says how to use it" bash -c 'sh "$1/get.sh" --help | grep -q "Usage: get.sh"' _ "$ROOT"
  check "it refuses what it does not know" fails_with "unknown option" sh "$ROOT/get.sh" --typo
  # In a home of its own, where the install is each of the things get.sh finds there: nothing, a link to a
  # version folder beside bosun.git (set up by bosun init, or not yet), and a checkout from before versions,
  # which it refuses.
  G="$WORK/get"
  G_DIR="$G/share/bosun"
  G_VERSIONS="$G/share/bosun-versions"
  mkdir -p "$G/home"
  stand_ins_in "$G/home" # get.sh runs bosun with this HOME
  fakes_first "$G/home"
  get_dry() { HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/upstream" sh "$ROOT/get.sh" --dry-run 2>&1; }
  g_tree() { (cd "$G" && find . -print | LC_ALL=C sort && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 cat | shasum); }
  says_lines() { # <output> <line...>: each line, whole, in the output
    local out="$1" line
    shift
    for line in "$@"; do grep -qxF -- "$line" <<<"$out" || {
      echo "expected the line: $line"
      echo "in: $out"
      return 1
    }; done
  }
  # An upstream of its own: a first install looks for its newest release, so the repository has to be there.
  g_git() { git -C "$G/upstream" -c user.name=acme -c user.email=dev@example.com -c commit.gpgsign=false "$@"; }
  mkdir -p "$G/upstream"
  git -C "$G/upstream" init -q -b main
  g_git commit -q --allow-empty -m "first"
  g_git tag v2026.11.05
  g_git commit -q --allow-empty -m "second"
  g_git tag -a v2026.11.12 -m "- Second"
  g_git commit -q --allow-empty -m "past the release"
  g_release="$(g_git rev-parse 'v2026.11.12^{commit}' | cut -c1-12)"
  g_head="$(g_git rev-parse HEAD | cut -c1-12)"
  G_NOTHING="$(get_dry)"
  check "with nothing installed, a dry run would clone bosun.git bare beside the install, fetch the newest release into a remote ref, add its folder, link the install to it and start bosun init" says_lines "$G_NOTHING" \
    "Getting bosun v2026.11.12, the newest release, into $G_VERSIONS, with $G_DIR a link to the version in use" \
    "  \$ git -c core.hooksPath=/dev/null clone --quiet --bare --no-tags --single-branch --branch main $G/upstream $G_VERSIONS/bosun.git" \
    "  \$ git --git-dir=$G_VERSIONS/bosun.git config bosun.managed true" \
    "  \$ git --git-dir=$G_VERSIONS/bosun.git config bosun.branch main" \
    "  \$ git -c core.hooksPath=/dev/null --git-dir=$G_VERSIONS/bosun.git fetch --quiet --no-tags origin +refs/tags/v2026.11.12:refs/remotes/origin/release" \
    "  \$ ln -s $G_VERSIONS/$g_release $G_DIR" \
    "Then it would start: $G_DIR/bin/bosun init"
  check "... and does none of it" test ! -e "$G/share"
  check "... and is not the head of main" not_contains "$G_NOTHING" "$g_head"
  check "with BOSUN_BRANCH it is that branch's head, and init is told the install follows it" says_lines \
    "$(HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/upstream" BOSUN_BRANCH=main sh "$ROOT/get.sh" --dry-run 2>&1)" \
    "  \$ git -c core.hooksPath=/dev/null --git-dir=$G_VERSIONS/bosun.git fetch --quiet --no-tags origin +refs/heads/main:refs/remotes/origin/main" \
    "  \$ ln -s $G_VERSIONS/<commit> $G_DIR" \
    "Then it would start: BOSUN_INIT_CHANNEL=main $G_DIR/bin/bosun init"
  check "... also unattended" says_lines \
    "$(HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/upstream" BOSUN_BRANCH=main sh "$ROOT/get.sh" --dry-run --unattended 2>&1)" \
    "Then it would start: BOSUN_INIT_CHANNEL=main BOSUN_INIT_UNATTENDED=1 $G_DIR/bin/bosun init --recommended"
  mkdir -p "$G/no-release"
  git -C "$G/no-release" init -q -b main
  git -C "$G/no-release" -c user.name=acme -c user.email=dev@example.com -c commit.gpgsign=false commit -q --allow-empty -m "first"
  git -C "$G/no-release" tag app-v1.0.0
  G_NO_RELEASE="$(HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/no-release" sh "$ROOT/get.sh" --dry-run 2>&1)"
  check "a repository with no release yet gets the head of main, and says so" says_lines "$G_NO_RELEASE" \
    "There is no release of bosun at $G/no-release yet: getting the head of main" \
    "  \$ git -c core.hooksPath=/dev/null --git-dir=$G_VERSIONS/bosun.git fetch --quiet --no-tags origin +refs/heads/main:refs/remotes/origin/main" \
    "Then it would start: $G_DIR/bin/bosun init"
  check "a repository that cannot be reached is not a repository with no release" fails_with "could not reach $G/nowhere" \
    env HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/nowhere" sh "$ROOT/get.sh" --dry-run
  check "... and nothing was made" test ! -e "$G/share"
  G_VERSION="$G_VERSIONS/0123456789ab"
  mkdir -p "$G_VERSIONS/bosun.git" "$G_VERSION/bin"
  printf '#!/bin/sh\n' >"$G_VERSION/bin/bosun"
  ln -s "$G_VERSION" "$G_DIR"
  G_UNFINISHED="$(get_dry)"
  G_UNFINISHED_LINE="$G_DIR is there, but ~/.local/bin/bosun does not lead into it, as bosun init leaves it: starting bosun init again"
  check "with an install bosun init has not set up yet (it stopped at what bosun needs), it starts bosun init again, as it is" says_lines "$G_UNFINISHED" \
    "$G_UNFINISHED_LINE" "Then it would start: $G_DIR/bin/bosun init"
  check "... getting nothing" not_contains "$G_UNFINISHED" "  \$ git"
  check "... unattended, it would hand that over as a variable, which an older bosun ignores" says_lines \
    "$(HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/upstream" sh "$ROOT/get.sh" --dry-run --unattended 2>&1)" \
    "Then it would start: BOSUN_INIT_UNATTENDED=1 $G_DIR/bin/bosun init --recommended"
  # The version there is older than this get.sh: a bosun that knows no --unattended, which says what it got.
  printf '#!/bin/sh\necho "init got: $*, BOSUN_INIT_UNATTENDED=${BOSUN_INIT_UNATTENDED:-}"\necho "init env: channel=${BOSUN_INIT_CHANNEL:-} config_channel=${BOSUN_UPDATE_CHANNEL:-} rerun=${BOSUN_RERUN:-}"\n' >"$G_VERSION/bin/bosun"
  chmod +x "$G_VERSION/bin/bosun"
  check "... and does: init --recommended, with BOSUN_INIT_UNATTENDED=1" says_lines \
    "$(HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/upstream" sh "$ROOT/get.sh" --unattended </dev/null 2>&1)" \
    "init got: init --recommended, BOSUN_INIT_UNATTENDED=1"
  g_init_env() { # [VAR=value...]: what the stand-in says it was handed
    env -u BOSUN_HARNESS -u BOSUN_BRANCH -u BOSUN_UPDATE_CHANNEL -u BOSUN_INIT_CHANNEL "$@" HOME="$G/home" BOSUN_DIR="$G_DIR" \
      sh "$ROOT/get.sh" --unattended </dev/null 2>&1 | grep "^init env: "
  }
  g_line="curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh"
  check "... a first install that asked for a branch hands init the channel under init's own name, never the config key" says_lines \
    "$(g_init_env BOSUN_BRANCH=feature)" "init env: channel=main config_channel= rerun=$g_line | BOSUN_DIR=$G_DIR BOSUN_BRANCH=feature sh"
  check "... and the line it prints to run again keeps that branch and the folder, as it keeps the agent" says_lines \
    "$(g_init_env BOSUN_BRANCH=feature BOSUN_HARNESS=codex)" "init env: channel=main config_channel= rerun=$g_line | BOSUN_DIR=$G_DIR BOSUN_BRANCH=feature BOSUN_HARNESS=codex sh"
  check "... without a branch, init is told no channel, and the line has none (it keeps the folder)" says_lines \
    "$(g_init_env)" "init env: channel= config_channel= rerun=$g_line | BOSUN_DIR=$G_DIR sh"
  printf '#!/bin/sh\n' >"$G_VERSION/bin/bosun"
  chmod -x "$G_VERSION/bin/bosun" # as it was
  mkdir -p "$G/home/.local/bin"
  ln -s "$G/gone/bin/bosun" "$G/home/.local/bin/bosun"
  check "... and so with a ~/.local/bin/bosun that leads nowhere" says_lines "$(get_dry)" "$G_UNFINISHED_LINE"
  ln -sfn "$G_DIR/bin/bosun" "$G/home/.local/bin/bosun" # what install.sh links, once bosun init set it up
  g_before="$(g_tree)"
  get_dry_plain() { HOME="$G/home" BOSUN_DIR="$G_DIR" env -u BOSUN_REPO -u BOSUN_BRANCH sh "$ROOT/get.sh" --dry-run "$@" 2>&1; }
  G_LINK="$(get_dry_plain)"
  check "with versions side by side, it would hand over to the install's own bosun update, which its channel decides" says_lines "$G_LINK" \
    "Then it would run: $G_DIR/bin/bosun update"
  check "... fetching nothing, cloning nothing, and changing nothing" not_contains "$G_LINK" '  $ '
  check "... also unattended, which goes ahead" says_lines \
    "$(get_dry_plain --unattended)" \
    "Then it would run: $G_DIR/bin/bosun update"
  check "... and its dry run changes nothing" test "$(g_tree)" = "$g_before"
  printf '#!/bin/sh\nif [ -t 0 ]; then echo "update got: $* (stdin is a terminal)"; else echo "update got: $*"; fi\n' >"$G_VERSION/bin/bosun"
  chmod +x "$G_VERSION/bin/bosun"
  check "... and does: bosun update, with --yes when unattended" says_lines \
    "$(HOME="$G/home" BOSUN_DIR="$G_DIR" sh "$ROOT/get.sh" --unattended </dev/null 2>&1 | tail -1)" \
    "update got: update --yes"
  # In a session of its own, which has no controlling terminal whatever ran the suite: /dev/tty cannot be opened.
  g_no_tty() { python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@"; }
  check "... and with no terminal to ask on (none to open), bosun update as it is, which shows its plan: nothing is --yes, and its input is not a terminal" says_lines \
    "$(HOME="$G/home" BOSUN_DIR="$G_DIR" g_no_tty sh "$ROOT/get.sh" </dev/null 2>&1 | tail -1)" \
    "update got: update"
  chmod -x "$G_VERSION/bin/bosun"
  printf '#!/bin/sh\n' >"$G_VERSION/bin/bosun" # as it was
  check "... a BOSUN_REPO or BOSUN_BRANCH given then is kept for the updates after it, as before" says_lines \
    "$(HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/upstream" BOSUN_BRANCH=feature sh "$ROOT/get.sh" --dry-run 2>&1)" \
    "  \$ git --git-dir=$G_VERSIONS/bosun.git remote set-url origin $G/upstream" \
    "  \$ git --git-dir=$G_VERSIONS/bosun.git config bosun.branch feature" \
    "Then it would run: $G_DIR/bin/bosun update"
  out="$(HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="https://0000placeholder-token@example.com/acme/bosun.git" sh "$ROOT/get.sh" --dry-run 2>&1)" # gitleaks:allow
  check "... and never says the token in a BOSUN_REPO's URL" bash -c '
    grep -qxF "  \$ git --git-dir=$2/bosun.git remote set-url origin https://***@example.com/acme/bosun.git" <<<"$1" &&
      ! grep -q 0000placeholder-token <<<"$1"' _ "$out" "$G_VERSIONS"
  # A checkout of bosun at the install path, as an older get.sh made one before versions side by side: set up
  # (~/.local/bin/bosun leads into it) or not, it is refused, saying how to install again, and nothing changes.
  rm "$G_DIR"
  mkdir -p "$G_DIR/bin"
  printf '#!/bin/sh\n' >"$G_DIR/bin/bosun"
  git -C "$G_DIR" init -q
  git -C "$G_DIR" add -A
  git -C "$G_DIR" -c user.name=acme -c user.email=dev@example.com -c commit.gpgsign=false commit -qm "a release"
  echo mine >"$G_DIR/notes.txt"
  g_before="$(g_tree)"
  G_REFUSED="$G_DIR is a checkout of bosun from before versions side by side, which this no longer updates: install bosun again."
  check "with a checkout from before versions, it refuses: that install is installed again, never updated" fails_with "$G_REFUSED" get_dry
  check "... saying how: its own bosun uninstall, else its bosun stop and moving it aside, then the line again" fails_with \
    "Take it off with its own bosun uninstall ($G_DIR/bin/bosun uninstall, which keeps your settings, ledger and ontology); if that keeps the checkout for what is yours in it, or it has no uninstall, run $G_DIR/bin/bosun stop and move the checkout aside. Then run this line again." \
    env HOME="$G/home" BOSUN_DIR="$G_DIR" BOSUN_REPO="$G/upstream" sh "$ROOT/get.sh" --unattended
  check "... changing nothing, in the checkout or beside it" test "$(g_tree)" = "$g_before"
  rm "$G/home/.local/bin/bosun"
  check "... and so one bosun init never set up" fails_with "$G_REFUSED" get_dry
  mv "$G_DIR" "$G/a-checkout"
  ln -s "$G/a-checkout" "$G_DIR"
  check "a link to anything but a version beside bosun.git is refused, not guessed at" fails_with "is a link to $G/a-checkout, which is not a version in $G_VERSIONS" get_dry
  for g_beside in "$G_VERSIONS/bosun.git" "$G_VERSIONS/not-a-version"; do
    mkdir -p "$g_beside"
    ln -sfn "$g_beside" "$G_DIR"
    check "... nor one to a folder beside bosun.git with no bin/bosun ($(basename "$g_beside"))" fails_with "is a link to $g_beside, which is not a version in $G_VERSIONS" get_dry
  done
  rm "$G_DIR"
  mkdir -p "$G_DIR"
  echo mine >"$G_DIR/notes.txt"
  check "... and so is a folder that is not a checkout of bosun" fails_with "$G_DIR is not bosun's" get_dry
  rm -rf "$G_DIR" "$G_VERSIONS"
  mkdir -p "$G_VERSIONS"
  echo mine >"$G_VERSIONS/notes.txt"
  check "... and a versions folder that is someone else's" fails_with "$G_VERSIONS is there, without bosun.git" get_dry
  check "everything in it is in functions, so a download cut short does nothing" bash -c '[ "$(tail -1 "$1/get.sh")" = "main \"\$@\"" ]' _ "$ROOT"

  # The update's origin: a repository of bosun's next versions, built here once for the update sections (update,
  # update-back and update-branch each need this alone). v1 is this working tree, standing for an install from
  # before the connector keys and the update channel: its lib/common.sh has no default line for them (what the plan
  # reads a version's keys from), though it still sets them, to every connector and to main as such an install
  # had, so that v1 runs. Then, from v1: a v2 whose migration fails while writes are paused, off main; v2
  # (tests/sandbox/update_patch.sh: the ledger's format 2, with a migration that sleeps UPDATE_PATCH_SLEEP seconds
  # while writes are paused, a changed line of policy.md, and the connector keys' defaults back, so the update adds
  # them and writes the kept values to bosun.env: migrations.CONFIG_ADDED); v3, a line of the readme (nothing a
  # version reads the data by, nor anything a window was launched with); v4, its launchd templates broken. main is
  # left at v4; a section that follows main moves it (`ug update-ref refs/heads/main <commit>`).
  section update-upstream "the update's origin (v1 to v4, built once for the update sections)" covers 'tests/sandbox/update_patch.sh'
  U="$WORK/u"
  U_UP="$U/upstream"
  mkdir -p "$U"
  copy_tree "$U_UP"
  cp "$U_UP/lib/common.sh" "$U/common.sh.with-connectors"
  python3 - "$U_UP/lib/common.sh" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
for key, default, kept in (
    ("BOSUN_CONNECTORS", '""', "all"),
    ("BOSUN_ORCHESTRATOR_CONNECTORS", '""', "all"),
    ("BOSUN_UPDATE_CHANNEL", '"releases"', "main"),
):
    line = f"  {key}={default}\n"
    if text.count(line) != 1:
        raise SystemExit(f"{path} does not hold {line!r} once")
    text = text.replace(line, f'  : "${{{key}={kept}}}"\n')
path.write_text(text)
PY
  u_next() { # [--failing]: v2 made from v1 in upstream's tree, its connector keys' defaults back
    cp "$U/common.sh.with-connectors" "$U_UP/lib/common.sh"
    "$ROOT/tests/sandbox/update_patch.sh" "$@" "$U_UP"
  }
  ug() { git -C "$U_UP" -c core.hooksPath=/dev/null -c commit.gpgsign=false -c user.name=acme -c user.email=dev@example.com "$@"; }
  ug init -q -b main
  # No housekeeping after upstream's commits: git (2.54 at least) starts a detached repack after a commit of
  # a few hundred objects, and a local clone (here, an install section's) hardlinks upstream's objects while that
  # repack writes its tmp_pack, and dies ("hardlink different from source"). As lib/knowledge_store.py does.
  ug config maintenance.auto false
  ug config gc.auto 0
  ug add -A
  ug commit -qm v1
  U_V1_COMMIT="$(ug rev-parse HEAD)"
  u_next --failing
  ug commit -qam "v2, whose migration fails while writes are paused"
  U_FAILING_COMMIT="$(ug rev-parse HEAD)"
  ug reset -q --hard "$U_V1_COMMIT"
  u_next
  ug commit -qam v2
  U_V2_COMMIT="$(ug rev-parse HEAD)"
  printf '\nv3: a line of the readme.\n' >>"$U_UP/README.md"
  ug commit -qam "v3, a line of the readme"
  U_V3_COMMIT="$(ug rev-parse HEAD)"
  ug rm -q templates/launchd.plist
  printf '<plist><dict><string>__LABEL__</string>\n' >"$U_UP/templates/launchd-watchdog.plist"
  ug commit -qam "v4, its launchd templates broken"
  U_V4_COMMIT="$(ug rev-parse HEAD)"
  check "the origin holds v1, v2, v3 and v4 on main, and the v2 whose migration fails beside it" bash -c '
    [ "$(git -C "$1" rev-list main | tr "\n" " ")" = "$5 $4 $3 $2 " ] && [ "$(git -C "$1" rev-parse "$6^")" = "$2" ]' \
    _ "$U_UP" "$U_V1_COMMIT" "$U_V2_COMMIT" "$U_V3_COMMIT" "$U_V4_COMMIT" "$U_FAILING_COMMIT"
  # A Claude of its own that answers and writes nothing: the suite's $WORK/fake-claude (the chores dry
  # run's) writes a ledger item wherever it runs, and bosun doctor, which the update runs while it plans,
  # asks Claude for its login.
  printf '#!/bin/sh\n[ "$1" = --version ] && echo "2.0.0 (Claude Code)"\nexit 0\n' >"$U/quiet-claude"
  chmod +x "$U/quiet-claude"
  u_ended_well() { [ "$1" = 0 ] || { tail -15 "$2" && return 1; }; }

  section update "bosun update (the origin's next versions, fake launchctl)" needs update-upstream covers 'install.sh,templates/launchd*.plist,docs/updating.md,tests/sandbox/update_patch.sh,tests/sandbox/acme_year.py,lib/update*.py,lib/versions.py,lib/migrations.py,lib/launch_record.py,libexec/bosun-update,libexec/bosun-checkin,libexec/bosun-doctor,libexec/bosun-guard,libexec/bosun-hook,libexec/bosun-kg,libexec/bosun-launch,libexec/bosun-ledger,libexec/bosun-refresh,libexec/bosun-start,libexec/bosun-status,lib/menu_status*.py'
  # An install of its own, with its own state and config, from the origin above: its bosun.git a clone of it at
  # v1, with v1's folder and the link to it; main moved along (failing v2, v2, v3, v4) as the update goes.
  U_LINK="$U/share/bosun"
  U_VERSIONS="$U/share/bosun-versions"
  U_STATE="$U/state"
  U_CONFIG="$U/config"
  mkdir -p "$U_VERSIONS" "$U_STATE" "$U_CONFIG"
  printf '# acme: an install from before connectors\n' >"$U_CONFIG/bosun.env"
  ug update-ref refs/heads/main "$U_V1_COMMIT"
  git clone -q --bare "$U_UP" "$U_VERSIONS/bosun.git"
  git --git-dir="$U_VERSIONS/bosun.git" worktree add -q --detach "$U_VERSIONS/${U_V1_COMMIT:0:12}" "$U_V1_COMMIT"
  ln -s "bosun-versions/${U_V1_COMMIT:0:12}" "$U_LINK"
  U_V1="$(cd "$U_VERSIONS/${U_V1_COMMIT:0:12}" && pwd -P)"
  ug update-ref refs/heads/main "$U_FAILING_COMMIT"
  U_ENV=(env BOSUN_STATE_DIR="$U_STATE" BOSUN_LOG_DIR="$U_STATE/logs" BOSUN_CONFIG_DIR="$U_CONFIG"
    BOSUN_SESSION_NAME=update-test BOSUN_LAUNCHD_LABEL=com.bosun.update-test BOSUN_SKIP_PREFLIGHT=1 BOSUN_KNOWLEDGE_GIT=0
    BOSUN_CLAUDE_BIN="$U/quiet-claude" BOSUN_UPDATE_CHANNEL=main)
  # Upstream's next versions are commits of main, no releases: the update follows the branch here, as an
  # install from before releases does; the release tests below name the channel (uc).
  ub() { "${U_ENV[@]}" "$U_LINK/bin/bosun" "$@"; }
  uc() { "${U_ENV[@]}" BOSUN_UPDATE_CHANNEL="$1" "$U_LINK/bin/bosun" "${@:2}"; }
  ub ledger upsert 'review:acme/api#1' --kind review-request --title "Exporter retries" >/dev/null
  ub ledger upsert 'mypr:acme/api#2' --kind my-pr --title "Nightly export" >/dev/null
  ub kg import "$KG_FIXTURE" --apply --auto-confirm >/dev/null 2>&1
  # Everything in the state and config folders (locks aside: taking one creates its file; logs aside: saying
  # what it did, in its day's log, changes nothing); and the data alone, a line per file (not the update's own
  # record, folder, backups or logs).
  u_listed() { (cd "$U" && find state config -type f ! -name '*.lock' "$@" -print0 | LC_ALL=C sort -z | xargs -0 shasum); }
  u_files() { u_listed ! -path 'state/logs/*' | shasum; }
  u_data_list() { u_listed ! -path 'state/update*' ! -path 'state/backups/*' ! -path 'state/logs/*'; }
  u_data_is() { # <list before>: the data as it was then; if not, which files differ
    local now
    now="$(u_data_list)"
    [ "$now" = "$1" ] || {
      diff <(echo "$1") <(echo "$now")
      return 1
    }
  }
  u_at() { [ "$(cd "$U_LINK" && pwd -P)" = "$1" ]; }
  u_step_is() { [ "$(jq -r .step "$U_STATE/update.json" 2>/dev/null)" = "$1" ]; }

  before="$(u_files)"
  out="$(ub update --dry-run 2>&1)"
  check "update --dry-run fetches the branch and shows the plan: the ledger's migration" contains "$out" "ledger: format 1 -> 2"
  check "... and the one prompt that changed" bash -c 'grep -q "Prompts changed (1):" <<<"$1" && grep -q "orchestrator/policy.md" <<<"$1"' _ "$out"
  check "... and changes nothing in the state or the config" test "$(u_files)" = "$before"
  check "... and that the update writes to bosun.env the value that keeps what this install did" bash -c '
    grep -q "^Written to bosun.env, to keep what this install did:" <<<"$1" && grep -qx "  BOSUN_ORCHESTRATOR_CONNECTORS=\"all\"" <<<"$1"' _ "$out"
  check "... and, as the install had no update channel, BOSUN_UPDATE_CHANNEL=\"main\" with its note: it goes on following main" bash -c '
    grep -qx "  BOSUN_UPDATE_CHANNEL=\"main\"" <<<"$1" && grep -q "bosun update goes on following main, as before; to follow the dated releases instead, set BOSUN_UPDATE_CHANNEL=releases" <<<"$1"' _ "$out"
  check "... nor the link" u_at "$U_V1"
  git --git-dir="$U_VERSIONS/bosun.git" remote set-url origin "https://0000placeholder-token@127.0.0.1:9/acme/bosun.git" # gitleaks:allow
  out="$(ub update --dry-run 2>&1)"
  check "a fetch that fails says so, without the token in the remote's URL" bash -c 'grep -qF "could not fetch main from https://127.0.0.1:9/acme/bosun.git" <<<"$1" && ! grep -q placeholder-token <<<"$1"' _ "$out"
  git --git-dir="$U_VERSIONS/bosun.git" remote set-url origin "$U_UP"

  # Releases: dated tags in upstream (v1's commit is the first release, the failing v2 the next one).
  # `bosun update --check` only looks: no version folder, no state, no hold or lock.
  ug tag -a v2026.01.01 -m "- The first release" "$U_V1_COMMIT"
  before="$(u_files)"
  u_folders="$(find "$U_VERSIONS" -maxdepth 1 | LC_ALL=C sort)"
  json="$(uc releases update --check --json)"
  check "update --check --json names the newest release, which this install is" jq -e --arg c "$U_V1_COMMIT" '
    .contract == 1 and .channel == "releases" and .latest.version == "v2026.01.01" and .latest.commit == $c and
    .installed.version == "v2026.01.01" and .installed.commit == $c and .available == false and
    .ahead == false and .skipped == false and .error == null' <<<"$json"
  check "... in one line without --json" contains "$(uc releases update --check)" "bosun is up to date (v2026.01.01)"
  ug tag -a v2026.02.01 -m "- The next version" "$U_FAILING_COMMIT"
  # (What --check answers of a newer release, a skipped one and the main channel, in words and in JSON, is
  # tests/unit/test_update_lookup.py's, on real git; here, one of each path through the wrapper.)
  uc releases update --check --json >/dev/null
  check "... a check with a newer release changes nothing in the state or the config" test "$(u_files)" = "$before"
  check "... and making no version folder" test "$(find "$U_VERSIONS" -maxdepth 1 | LC_ALL=C sort)" = "$u_folders"
  check "--skip records the release it is given" contains "$(uc releases update --skip v2026.02.01)" "will not offer v2026.02.01"
  check "... in update-skip.json" jq -e '.version == "v2026.02.01" and (.at | length > 0)' "$U_STATE/update-skip.json"
  rm "$U_STATE/update-skip.json"
  check "--check takes no --yes: it changes nothing" fails_with "take no --dry-run or --yes" uc releases update --check --yes
  git --git-dir="$U_VERSIONS/bosun.git" remote set-url origin "https://0000placeholder-token@127.0.0.1:9/acme/bosun.git" # gitleaks:allow
  json="$(uc releases update --check --json)"
  u_rc=$?
  git --git-dir="$U_VERSIONS/bosun.git" remote set-url origin "$U_UP"
  check "an origin that cannot be reached is the check's error, exit 0, and no token in it" bash -c '
    [ "$1" = 0 ] && jq -e ".error == \"could not reach https://127.0.0.1:9/acme/bosun.git\" and .available == false and .latest == null" <<<"$2" >/dev/null &&
      ! grep -q placeholder-token <<<"$2"' _ "$u_rc" "$json"
  git --git-dir="$U_VERSIONS/bosun.git" remote set-url origin "https://0000placeholder-token@127.0.0.1:9/acme/bosun.git" # gitleaks:allow
  json="$(uc releases update --dry-run --json 2>/dev/null)"
  u_rc=$?
  git --git-dir="$U_VERSIONS/bosun.git" remote set-url origin "$U_UP"
  check "update --json, when the wrapper cannot reach the origin, still prints its one object with the error, exit 1, no token in it" bash -c '
    [ "$1" = 1 ] && jq -e ".contract == 1 and .plan == null and .update == null and .summary == null and
      (.error | startswith(\"could not reach https://127.0.0.1:9/acme/bosun.git\"))" <<<"$2" >/dev/null &&
      ! grep -q placeholder-token <<<"$2"' _ "$u_rc" "$json"
  json="$(uc main update --check --json)"
  check "on the main channel the latest is the head of the branch, its notes the subjects since this install" jq -e --arg c "$U_FAILING_COMMIT" '
    .channel == "main" and .available == true and .latest.commit == $c and
    .latest.notes == "- v2, whose migration fails while writes are paused"' <<<"$json"
  check "a channel that is neither is refused, naming the key" fails_with 'BOSUN_UPDATE_CHANNEL is "nightly": it is releases or main' uc nightly update --dry-run
  out="$(uc releases update --dry-run 2>&1)"
  check "update --dry-run on the releases channel plans the newest release: the ledger's migration" contains "$out" "ledger: format 1 -> 2"
  check "... and the install is still v1" u_at "$U_V1"
  # An install past the newest release: the link on the failing v2 (a descendant of the first release, once
  # the next one is gone) for the one dry run, and back.
  ug tag -d v2026.02.01 >/dev/null
  ln -sfn "bosun-versions/${U_FAILING_COMMIT:0:12}" "$U_LINK"
  out="$(uc releases update --dry-run 2>&1)"
  ln -sfn "bosun-versions/${U_V1_COMMIT:0:12}" "$U_LINK"
  check "an install past the newest release says it is ahead of it, and changes nothing" contains "$out" "bosun is ahead of the newest release (v2026.01.01)"
  check "... the link where it was" u_at "$U_V1"
  ug tag -d v2026.01.01 >/dev/null
  check "with no release at all, the releases channel says so, in one line" fails_with "no release at" uc releases update --dry-run
  check "an update refuses to run inside bosun's tmux, whose windows it restarts" fails_with "runs inside one of them" env TMUX="/tmp/tmux-0/$SOCKET,1,0" "${U_ENV[@]}" "$U_LINK/bin/bosun" update --yes
  out="$(ub update </dev/null 2>&1)"
  check "without --yes and no terminal, it shows the plan and stops" bash -c 'grep -q "ledger: format 1 -> 2" <<<"$1" && grep -q "Nothing has changed" <<<"$1"' _ "$out"
  check "... changing nothing" bash -c '[ "$1" = "$2" ]' _ "$(u_files)" "$before"

  data_before="$(u_data_list)"
  json="$(ub update --yes --json 2>"$U/failing.err")"
  rc=$?
  out="$(cat "$U/failing.err")"
  check "a migration that fails while writes are paused fails the update (exit 1), which says it put everything back" bash -c '[ "$1" = 1 ] && grep -q "Everything is as it was" <<<"$2"' _ "$rc" "$out"
  check "... with --json, what it says goes to stderr and stdout is one object: the plan and the record" jq -e '.plan.migrations == [["ledger", 1, 2]] and .update.step == "failed"' <<<"$json"
  check "... the link still at v1" u_at "$U_V1"
  check "... the data as it was" u_data_is "$data_before"
  check "... and the record says failed, with the error" jq -e '.step == "failed" and (.error | contains("failed on purpose"))' "$U_STATE/update.json"

  # Upstream's v2 now migrates well.
  ug update-ref refs/heads/main "$U_V2_COMMIT"
  U_V2="$(cd "$U_VERSIONS" && pwd -P)/${U_V2_COMMIT:0:12}"
  cp -Rp "$U_STATE" "$U/state-v1" # as it was before the update, for laying out below where one was cut short
  cp -Rp "$U_CONFIG" "$U/config-v1"
  : >"$U/transcript.jsonl"
  guard_event="$(jq -nc --arg t "$U/transcript.jsonl" --arg c "touch $U/approved" '{tool_name: "Bash", tool_input: {command: $c}, transcript_path: $t}')"
  u_guard() { "${U_ENV[@]}" BOSUN_APPROVALS_ENABLED=1 BOSUN_APPROVALS_GATED_COMMANDS=touch "$U_LINK/bin/bosun" guard --approvals <<<"$guard_event"; }
  guard_before="$(u_guard 2>&1)"
  "${U_ENV[@]}" UPDATE_PATCH_SLEEP=8 "$U_LINK/bin/bosun" update --yes >"$U/update.out" 2>&1 &
  updating=$!
  check "--yes: it rehearses, holds, then pauses writes for the migration (the patch's sleep)" wait_for 120 u_step_is migrated
  "${U_ENV[@]}" "$U_V1/bin/bosun" ledger upsert 'review:acme/api#3' --kind review-request --title "Written while paused" >"$U/writer.out" 2>&1 &
  writer=$!
  u_guard >"$U/guard.out" 2>&1 &
  guarding=$!
  out="$(ub update --yes 2>&1)"
  check "a second update while one runs is refused, naming when the first started" contains "$out" "an update is running (started $(jq -r .started_at "$U_STATE/update.json")"
  check "a ledger write and an approval hook started on v1 while writes are paused wait" bash -c 'kill -0 "$1" && kill -0 "$2" && [ "$(jq -r .step "$3/update.json")" = migrated ]' _ "$writer" "$guarding" "$U_STATE"
  wait "$updating"
  check "the update ends well" u_ended_well $? "$U/update.out"
  u_backup_now="$(jq -r .backup "$U_STATE/update.json")"
  sed -n '/^bosun updated: /,$p' "$U/update.out" >"$U/summary.txt"
  u_summary_is() { # the summary the update ended with, as the design lays it out
    grep -qE "^bosun updated: ${U_V1_COMMIT:0:7} -> ${U_V2_COMMIT:0:7} \(1 commit\) in [0-9]+(m [0-9]+)?s$" "$1" &&
      grep -qE "^  ledger      format 1 -> 2 \([0-9]+ items migrated\)$" "$1" && grep -qx "  knowledge   format 1, unchanged" "$1" &&
      grep -qx "Prompts changed: 1 (policy.md)" "$1" && grep -qx "Jobs reloaded: morning, watchdog, now, login" "$1" &&
      ! grep -q "^  FAIL: " "$1" && grep -qE "^  [0-9]+ already failing before the update$" "$1" &&
      grep -qE "^  [0-9]+ warnings?: run bosun doctor$" "$1" &&
      grep -qxE "Backup: $2 \([0-9.]+ (bytes|KB|MB|GB)\)" "$1" && [ "$(tail -1 "$1")" = "Going back: bosun update --back (docs/updating.md#going-back)" ] || {
      cat "$1"
      return 1
    }
  }
  check "... and its output ends with the summary: the commit, the ledger's migration, the prompt changed, the jobs reloaded, only counts of what doctor already failed at before the update and of its warnings, the backup" u_summary_is "$U/summary.txt" "$u_backup_now"
  check "... saved as summary.md next to the backup" bash -c '[ "$(sed "1d;\$d" "$1/summary.md")" = "$(cat "$2")" ]' _ "$u_backup_now" "$U/summary.txt"
  wait "$writer"
  writer_rc=$?
  wait "$guarding"
  check "... the link leads to v2" u_at "$U_V2"
  check "... the ledger is in format 2, migrated" test "$(jq -r '"\(.version) \(.update_patch)"' "$U_STATE/ledger.json")" = "2 migrated"
  check "... and passes its check against its journal" test "$(ub ledger validate --journal 2>&1)" = "ledger is valid"
  check "... a backup from v1 to v2 with its manifest" bash -c 'grep -lq "\"to\": \"$2\"" "$1"/backups/*/manifest.json' _ "$U_STATE" "$U_V2_COMMIT"
  check "... the record says done, with how long writes were paused ($(jq .freeze_ms "$U_STATE/update.json") ms, 8 s of them the patch's sleep)" jq -e '.step == "done" and .freeze_ms > 0' "$U_STATE/update.json"
  check "... and what bosun doctor found then (here: no sessions.yml)" jq -e '.findings | any(contains("sessions manifest not found"))' "$U_STATE/update.json"
  check "... it wrote the value that keeps what the install did to bosun.env, keeping a copy as it was" bash -c '
    grep -qx "BOSUN_ORCHESTRATOR_CONNECTORS=\"all\"" "$1/bosun.env" && ! grep -q "^BOSUN_CONNECTORS=" "$1/bosun.env" &&
      grep -qx "BOSUN_UPDATE_CHANNEL=\"main\"" "$1/bosun.env" &&
      [ "$(cat "$1"/bosun.env.pre-update-*)" = "# acme: an install from before connectors" ]' _ "$U_CONFIG"
  check "... and its summary says so, with what each added key means" bash -c '
    grep -q "^  bosun\.env  *BOSUN_ORCHESTRATOR_CONNECTORS=\"all\" added.*BOSUN_UPDATE_CHANNEL=\"main\" added (before: bosun\.env\.pre-update-" "$1" &&
      grep -q "^  BOSUN_ORCHESTRATOR_CONNECTORS  *the orchestrator keeps every connector, as before" "$1" &&
      grep -q "^  BOSUN_CONNECTORS  *repo windows now start without connectors" "$1" &&
      grep -q "^  BOSUN_UPDATE_CHANNEL  *bosun update goes on following main, as before" "$1"' _ "$U/summary.txt"
  check "... it reloaded the launchd jobs, which run the link" bash -c 'grep -qF "$2/bin/bosun" "$1/Library/LaunchAgents/com.bosun.update-test.plist"' _ "$HOME" "$(cd "$U/share" && pwd -P)/bosun"
  check "... and deleted the folder of the version that failed, keeping v1's and v2's" bash -c '[ ! -e "$1/${2:0:12}" ] && [ -d "$3" ] && [ -d "$4" ]' _ "$U_VERSIONS" "$U_FAILING_COMMIT" "$U_V1" "$U_V2"
  check "the write that waited ran again on v2, and landed" bash -c '[ "$1" = 0 ] && [ "$(jq -r ".items[\"review:acme/api#3\"].title" "$2/ledger.json")" = "Written while paused" ]' _ "$writer_rc" "$U_STATE"
  check "... once" test "$(cat "$U_STATE"/journal/ledger/*.jsonl | grep -c 'Written while paused')" = 1
  check "a PreToolUse approval hook while writes are paused decides as without them" bash -c '[ -n "$1" ] && [ "$(cat "$2")" = "$1" ]' _ "$guard_before" "$U/guard.out"

  # An update cut short in its freeze, laid out by hand: the record at the step, the backup from backed-up on,
  # the migrated ledger from migrated on, the link at v2 at switched, and no update running. The next ledger
  # write puts it back. Two steps: marked (no backup yet: it only points back) and switched (the backup, the
  # migrated ledger and the link); recover takes one way for every step with a backup, which
  # tests/unit/test_update_gate.py runs for each (and the kill -9 below cuts one at migrated).
  u_reset() {
    rm -rf "$U_STATE" "$U_CONFIG"
    cp -Rp "$U/state-v1" "$U_STATE"
    cp -Rp "$U/config-v1" "$U_CONFIG"
    rm -f "$U_STATE/update.json"
    ln -sfn "bosun-versions/${U_V1_COMMIT:0:12}" "$U_LINK"
  }
  u_backup() {
    python3 -c 'import datetime, pathlib, sys; sys.path.insert(0, sys.argv[1] + "/lib"); import update_backups
print(update_backups.take(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), sys.argv[4], sys.argv[5], {"ledger": 1, "knowledge": 1}, datetime.datetime.now(datetime.UTC)))' \
      "$U_V1" "$U_STATE" "$U_CONFIG" "$U_V1_COMMIT" "$U_V2_COMMIT"
  }
  u_migrate() {
    UPDATE_PATCH_SLEEP=0 python3 -c 'import pathlib, sys; sys.path.insert(0, sys.argv[1] + "/lib"); import migrations
migrations.run(migrations.Context(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])))' "$U_V2" "$U_STATE" "$U_CONFIG"
  }
  u_record() { # <step> [backup]
    jq -n --arg step "$1" --arg backup "${2:-}" --arg from "$U_V1" --arg to "$U_V2" --arg fc "$U_V1_COMMIT" --arg tc "$U_V2_COMMIT" '
      {id: "laid-out", from_root: $from, to_root: $to, from_commit: $fc, to_commit: $tc, step: $step,
       started_at: "2026-11-07T12:00:00+00:00", hold_until: 0, backup: $backup,
       restorable: (if $backup == "" then [] else ["state/ledger.json", "state/ledger.json.bak", "state/ledger.md",
         "state/journal", "state/replica.json", "config/knowledge"] end)}' >"$U_STATE/update.json"
  }
  u_cut_at() { # <step>: what an update that died at <step> leaves
    local backup=""
    u_reset
    case "$1" in backed-up | migrated | switched | checked) backup="$(u_backup)" ;; esac
    case "$1" in migrated | switched | checked) u_migrate ;; esac
    case "$1" in switched | checked) ln -sfn "bosun-versions/${U_V2_COMMIT:0:12}" "$U_LINK" ;; esac
    u_record "$1" "$backup"
  }
  u_as_before() { # <item written since>: the ledger as before the update but for it, in format 1, and it there
    [ "$(jq -cS --arg id "$1" 'del(.items[$id]) | .items' "$U_STATE/ledger.json")" = "$(jq -cS .items "$U/state-v1/ledger.json")" ] &&
      [ "$(jq .version "$U_STATE/ledger.json")" = 1 ] && jq -e --arg id "$1" '.items[$id]' "$U_STATE/ledger.json" >/dev/null
  }
  n=10
  for step in marked switched; do
    n=$((n + 1))
    u_cut_at "$step"
    out="$(ub ledger upsert "review:acme/api#$n" --kind review-request --title "after a cut at $step" 2>&1)"
    check "an update cut short at $step: the next ledger write says so and puts it back" contains "$out" "was cut short while writes were paused; putting back its backup and ${U_V1_COMMIT:0:7}"
    check "... the link at v1 again" u_at "$U_V1"
    check "... the ledger as it was, in format 1, and the write landed" u_as_before "review:acme/api#$n"
  done

  u_reset
  "${U_ENV[@]}" UPDATE_PATCH_SLEEP=60 "$U_LINK/bin/bosun" update --yes >"$U/killed.out" 2>&1 &
  updating=$!
  check "an update killed (kill -9) while writes are paused..." wait_for 120 u_step_is migrated
  {
    kill -9 "$updating"
    wait "$updating"
  } 2>/dev/null
  out="$(ub ledger upsert 'review:acme/api#20' --kind review-request --title "after a kill" 2>&1)"
  check "... is put back by the next ledger write, which says so" contains "$out" "was cut short while writes were paused"
  check "... the link at v1 again" u_at "$U_V1"
  check "... the ledger as it was, in format 1, and the write landed" u_as_before 'review:acme/api#20'

  # A kg forget cut short before view.json was saved: the next kg write finishes it, so no backup or
  # rehearsal copy may hold the person until then.
  cp -Rp "$U_STATE" "$U/forget-state"
  cp -Rp "$U_CONFIG" "$U/forget-config"
  uf() { "${U_ENV[@]}" BOSUN_STATE_DIR="$U/forget-state" BOSUN_CONFIG_DIR="$U/forget-config" "$U_LINK/bin/bosun" "$@"; }
  cp "$U/forget-config/knowledge/view.json" "$U/view-before-forget.json"
  uf kg forget person/lee >/dev/null 2>&1
  cp "$U/view-before-forget.json" "$U/forget-config/knowledge/view.json"
  out="$(uf update --yes 2>&1)"
  rc=$?
  check "a kg forget cut short stops the update at its plan (exit 1), saying so" bash -c '[ "$1" = 1 ] && grep -q "a kg forget was cut short" <<<"$2" && ! grep -q Rehearsing <<<"$2"' _ "$rc" "$out"

  u_cut_at checked
  json="$(ub update --yes --json 2>"$U/carried.err")"
  out="$(cat "$U/carried.err")"
  check "an update that stopped after its checks passed: the next bosun update carries it on" contains "$out" "carried on to the end"
  check "... says its summary, from a record laid out as an earlier bosun kept it (no plan: no Data), and in --json's object the backup's path whole" bash -c '
    grep -qx "Going back: bosun update --back (docs/updating.md#going-back)" <<<"$1" &&
      jq -e --arg to "$3" --arg backup "$(jq -r .backup "$4/update.json")" ".summary.to_commit == \$to and .summary.backup == \$backup and .summary.data == {} and (\$backup | startswith(\"/\"))" <<<"$2" >/dev/null' \
    _ "$out" "$json" "$U_V2_COMMIT" "$U_STATE"
  check "... then says bosun is up to date" contains "$out" "bosun is up to date (${U_V2_COMMIT:0:7})"
  check "... the record says done, the link at v2" bash -c 'jq -e ".step == \"done\"" "$1/update.json" >/dev/null && [ "$(cd "$2" && pwd -P)" = "$3" ]' _ "$U_STATE" "$U_LINK" "$U_V2"
  # (With nothing new and nothing to carry on, it is up to date, in words and with --json: test_update.py.)
  check "a checkout is updated with git, not bosun update" fails_with "is a checkout: update it with git" "${U_ENV[@]}" "$BOSUN" update --dry-run
  json="$("${U_ENV[@]}" "$BOSUN" update --dry-run --json 2>/dev/null)"
  u_rc=$?
  check "... and with --json the same refusal is the one object, its error the line said, exit 1" bash -c '
    [ "$1" = 1 ] && jq -e ".contract == 1 and .plan == null and .update == null and .summary == null and
      (.error | contains(\"is a checkout: update it with git\"))" <<<"$2" >/dev/null' _ "$u_rc" "$json"

  # A running session keeps the hook commands it was launched with: each of v1's runs on v2.
  mkdir -p "$U/home/.local/bin"
  ln -sfn "$U_LINK/bin/bosun" "$U/home/.local/bin/bosun"
  u_hooks="$("${U_ENV[@]}" BOSUN_APPROVALS_ENABLED=1 BOSUN_EVENTS_ENABLED=1 BOSUN_ORCHESTRATOR=orch bash -c '
    source "$1/lib/common.sh"; load_config; orchestrator_extra_settings; repo_session_settings alpha; cat "$1/orchestrator/settings.json"' _ "$U_V1" |
    jq -r '.hooks // {} | .[][] | .hooks[] | .command' | sort -u)"
  hook_payload='{"session_id":"00000000-0000-0000-0000-000000000000","transcript_path":"/dev/null","cwd":"/","hook_event_name":"Stop","tool_name":"Bash","tool_input":{"command":"ls"},"tool_use_id":"toolu_0","tool_response":{"stdout":""},"prompt":"how are the exports?"}'
  u_run_hooks() {
    local command
    stand_ins_in "$U/home"
    fakes_first "$U/home"
    while IFS= read -r command; do
      "${U_ENV[@]}" HOME="$U/home" BOSUN_APPROVALS_ENABLED=1 BOSUN_EVENTS_ENABLED=1 bash -c "$command" <<<"$hook_payload" >/dev/null 2>"$U/hook.err" ||
        { echo "$command: exit $?: $(tail -2 "$U/hook.err")" && return 1; }
    done <<<"$u_hooks"
  }
  check "each hook command of v1's launch settings runs on v2 and exits 0 ($(grep -c . <<<"$u_hooks") of them)" u_run_hooks
  check "... each through the install link, never v1's own folder" bash -c '[ -n "$1" ] && ! grep -q "bosun-versions/" <<<"$1"' _ "$u_hooks"

  # Bringing the agents along. In a tmux server of their own, on v1: the orchestrator, which only
  # re-reads (v2 changes its policy, nothing it was launched with), and four repo windows whose hook
  # settings v2 changes: an idle one, one where only background work runs (shell), a busy one, and one
  # waiting for your answer at a permission prompt (which the update never touches until answered). No
  # checkin window (check-ins off), so only the ticks below take the loop's turn. Each fake Claude
  # registers with the status in $U/status.<window>, keeps the prompt it was started with in
  # $U/prompt.<window>.<pid> and what is pasted into it in $U/pasted.<window>, and answers bosun's update
  # prompt (as its first prompt or pasted in) with `bosun update note --status`, and a take-stock prompt
  # with `--stock`.
  u_reset
  U_TMUX="$SOCKET-update"
  U_REPO="$U/repos/exporter"
  mkdir -p "$U_REPO/.git"
  printf 'sessions:\n' >"$U_CONFIG/sessions.yml"
  for status in idle shell busy waiting; do
    printf '  - name: u-%s\n    dir: %s\n' "$status" "$U_REPO" >>"$U_CONFIG/sessions.yml"
    echo "$status" >"$U/status.u-$status"
  done
  echo idle >"$U/status.orch"
  u_note_env="env BOSUN_STATE_DIR=$U_STATE BOSUN_LOG_DIR=$U_STATE/logs BOSUN_CONFIG_DIR=$U_CONFIG $U_LINK/bin/bosun"
  cat >"$U/fake-claude" <<FAKE
#!/bin/bash
if [ "\$1" = "--version" ]; then echo "2.0.0 (Claude Code)"; exit 0; fi
window=""
name=""
previous=""
for arg in "\$@"; do
  if [ "\$previous" = "--remote-control" ]; then window="\$arg"; fi
  if [ "\$previous" = "--name" ]; then name="\$arg"; fi
  previous="\$arg"
done
prompt="\${!#}"
printf '%s' "\$prompt" >"$U/prompt.\$window.\$\$"
printf '{"pid":%d,"status":"%s","version":"2.0.0","statusUpdatedAt":%d,"name":"%s"}' \$\$ "\$(cat "$U/status.\$window")" "\$(date +%s)000" "\$name" >"\$HOME/.claude/sessions/\$\$.json"
note() { $u_note_env update note --window "\$window" "\$1" <<<"\$2"; }
case "\$prompt" in *"--window \$window --status"*) note --status "resumed after the update" ;; esac
while IFS= read -r line; do
  printf '%s\n' "\$line" >>"$U/pasted.\$window"
  case "\$line" in
    *"--window \$window --stock"*) note --stock "sleep 600: the wait for CI; when it ends, report CI" ;;
    *"--window \$window --status"*) note --status "re-read, checked in, carrying on" ;;
  esac
done
FAKE
  chmod +x "$U/fake-claude"
  # The update's restarts are real (refresh --update-restart): an agent that lasts half a second has started, as the
  # fakes live until stopped (BOSUN_AGENT_SETTLE_SECONDS, 2 s by default, is for tests).
  U_LIVE=(BOSUN_TMUX_SOCKET="$U_TMUX" BOSUN_LAUNCH_DRY_RUN=0 BOSUN_CLAUDE_BIN="$U/fake-claude" BOSUN_ORCHESTRATOR=orch
    BOSUN_ORCHESTRATOR_DIR="$U_REPO" BOSUN_CHECKIN_MINUTES=0 BOSUN_AGENT_SETTLE_SECONDS=0.5)
  ul() { "${U_ENV[@]}" "${U_LIVE[@]}" "$U_LINK/bin/bosun" "$@"; }
  u_claude() { # <window>: the pid of the fake Claude running in it
    local pane_pid
    pane_pid="$(tmux -L "$U_TMUX" list-windows -t "=update-test" -F '#{window_name} #{pane_pid}' 2>/dev/null | awk -v w="$1" '$1 == w { print $2 }')"
    [ -n "$pane_pid" ] && bash -c 'source "$1/lib/common.sh"; agent_pid_under "$2"' _ "$ROOT" "$pane_pid"
  }
  u_claudes() { for w in orch u-idle u-shell u-busy u-waiting; do u_claude "$w" >/dev/null || return 1; done; }
  u_windows() { jq -c --arg w "$1" '.windows[$w]' "$U_STATE/update.json"; }
  ul start >/dev/null 2>&1
  check "bringing the agents along: five fake Claude windows run on v1" wait_for 30 u_claudes
  orch_pid="$(u_claude orch)"
  idle_pid="$(u_claude u-idle)"
  shell_pid="$(u_claude u-shell)"
  busy_pid="$(u_claude u-busy)"
  waiting_pid="$(u_claude u-waiting)"
  # Two windows bosun did not start, outside the window list with no launch recorded: a plain shell
  # opened in its session (prefix-c), and a Claude started there by hand (no --name), idle.
  cat >"$U/hand-claude" <<HAND
#!/bin/bash
printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":""}' \$\$ "\$(date +%s)000" >"\$HOME/.claude/sessions/\$\$.json"
while IFS= read -r line; do printf '%s\n' "\$line" >>"$U/pasted.u-hand"; done
HAND
  chmod +x "$U/hand-claude"
  tmux -L "$U_TMUX" new-window -d -t "=update-test:" -n u-mine -- /bin/sh
  tmux -L "$U_TMUX" new-window -d -t "=update-test:" -n u-hand -- bash -c '"$1"; exec sleep 600' _ "$U/hand-claude"
  u_pane() { tmux -L "$U_TMUX" display -p -t "=update-test:$1" '#{pane_pid}'; }
  check "... with a plain shell and a Claude started by hand beside them" wait_for 30 u_claude u-hand
  mine_pane="$(u_pane u-mine)"
  hand_pane="$(u_pane u-hand)"
  hand_pid="$(u_claude u-hand)"
  # (Its plan is read from what the update below prints before it goes ahead: the same plan a dry run shows.)
  # Run so that Ctrl-C reaches it (a job a script starts in the background ignores SIGINT).
  python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' \
    "${U_ENV[@]}" "${U_LIVE[@]}" "$U_LINK/bin/bosun" update --yes >"$U/agents.out" 2>&1 &
  updating=$!
  u_pending_is() { [ "$(jq -c .pending "$U_STATE/update.json" 2>/dev/null)" = "$1" ]; }
  u_waits_for_busy() { # the update brought every window along but the busy one and the one at a prompt; if not, where it is
    wait_for 180 u_pending_is '["u-busy","u-waiting"]' || {
      jq -c '{step, pending, windows}' "$U_STATE/update.json"
      tail -5 "$U/agents.out"
      return 1
    }
  }
  check "... the update brings the orchestrator, the idle and the shell window along, and waits for the busy one (and the one at a prompt)" u_waits_for_busy
  check "... its plan: the orchestrator re-reads; v2 changed what the repo windows were launched with, so the idle one restarts, the shell one takes stock first, the busy one is waited for, and the one at a prompt waits for you" bash -c '
    grep -q "orch: re-read" "$1" && grep -q "u-idle: restart$" "$1" && grep -q "u-shell: take stock, then restart" "$1" &&
      grep -q "u-busy: wait until idle" "$1" && grep -q "u-waiting: ask first" "$1"' _ "$U/agents.out"
  check "... and the two windows bosun did not start are left as they are" bash -c '
    grep -q "u-mine: not started by bosun: left as it is$" "$1" && grep -q "u-hand: not started by bosun: left as it is$" "$1"' _ "$U/agents.out"
  check "... the orchestrator kept running, got the update prompt pasted in, naming policy.md, and its answer ended its wait" bash -c '
    jq -e ".windows.orch == [\"orchestrator\", \"re-read\", \"asked to re-read\", \"answered\"]" "$1/update.json" >/dev/null && kill -0 "$2" &&
      grep -q "this session was kept running. Re-read .*/orchestrator/policy.md" "$3/pasted.orch"' _ "$U_STATE" "$orch_pid" "$U"
  check "... the idle one restarted on v2 with the update prompt, and its answer ended its wait" bash -c '
    jq -e ".windows[\"u-idle\"] == [\"repo\", \"restart\", \"restarted\", \"answered\"]" "$1/update.json" >/dev/null &&
      ! kill -0 "$2" 2>/dev/null && grep -q "bosun was updated from .* this session was restarted on the new version" "$3"/prompt.u-idle.* &&
      grep -qx "resumed after the update" "$1/update/notes/u-idle.status"' _ "$U_STATE" "$idle_pid" "$U"
  check "... the shell one listed its background work first, then restarted with that list in its prompt" bash -c '
    jq -e ".windows[\"u-shell\"] == [\"repo\", \"take stock, then restart\", \"asked to take stock\", \"restarted\", \"answered\"]" "$1/update.json" >/dev/null &&
      ! kill -0 "$2" 2>/dev/null && grep -l "this session was restarted" "$3"/prompt.u-shell.* | xargs grep -q "sleep 600: the wait for CI"' _ "$U_STATE" "$shell_pid" "$U"
  check "... the busy one is left running, never restarted" bash -c 'kill -0 "$1" && [ "$(jq -c ".windows[\"u-busy\"]" "$2/update.json")" = "[\"repo\",\"wait until idle\"]" ]' _ "$busy_pid" "$U_STATE"
  check "... and so is the one at a prompt, nothing pasted into it" bash -c '
    kill -0 "$1" && [ "$(jq -c ".windows[\"u-waiting\"]" "$2/update.json")" = "[\"repo\",\"ask first\"]" ] && [ ! -e "$3/pasted.u-waiting" ]' _ "$waiting_pid" "$U_STATE" "$U"
  check "... and the update says it waits for them: a busy window keeps the wait going" wait_for 120 grep -q "Waiting for u-busy, u-waiting" "$U/agents.out"
  kill -INT "$updating"
  wait "$updating"
  rc=$?
  check "Ctrl-C stops the wait (exit 0), the record at agents; with no check-in loop running, it says bosun update advance finishes the windows" bash -c '
    [ "$1" = 0 ] && grep -q "Stopped waiting: no check-in loop runs here: bosun update advance, run by hand, takes a turn at u-busy, u-waiting" "$2" &&
      jq -e ".step == \"agents\" and .pending == [\"u-busy\", \"u-waiting\"] and (.handed_over | length > 0)" "$3/update.json" >/dev/null' _ "$rc" "$U/agents.out" "$U_STATE"
  check "... then the summary, the busy window and the one at a prompt still waited for, the shell one's restart after taking stock" bash -c '
    grep -qE "^  u-busy +nothing yet; still waited for$" "$1" && grep -qE "^  u-waiting +nothing yet; still waited for$" "$1" &&
      grep -qE "^  u-shell +asked to take stock, restarted; resumed after the update$" "$1" &&
      [ "$(tail -1 "$1")" = "Going back: bosun update --back (docs/updating.md#going-back)" ] && [ ! -e "$(jq -r .backup "$2/update.json")/summary.md" ]' _ "$U/agents.out" "$U_STATE"
  check "... with the link at v2" u_at "$U_V2"
  check "the windows bosun did not start were left as they are: their panes, the Claude in one, nothing pasted, no bosun launch" bash -c '
    [ "$1" = "$2" ] && kill -0 "$3" && [ ! -e "$4/pasted.u-hand" ] &&
      ! tmux -L "$5" capture-pane -p -J -S -500 -t "=update-test:u-mine" | grep -q "launch" &&
      jq -e ".windows | has(\"u-mine\") or has(\"u-hand\") | not" "$6/update.json" >/dev/null' \
    _ "$mine_pane $hand_pane" "$(u_pane u-mine) $(u_pane u-hand)" "$hand_pid" "$U" "$U_TMUX" "$U_STATE"
  check "... and the summary says so" bash -c '
    grep -qE "^  u-mine +left as it is \(bosun did not start it\)$" "$1" && grep -qE "^  u-hand +left as it is \(bosun did not start it\)$" "$1"' _ "$U/agents.out"
  uc() { "${U_ENV[@]}" "${U_LIVE[@]}" BOSUN_CHECKIN_MINUTES=60 "$U_LINK/bin/bosun" checkin 2>&1; } # one tick of the loop
  out="$(uc)"
  check "a check-in tick leaves the busy window alone while it is busy" bash -c 'kill -0 "$1" && ! grep -q "u-busy: restarted" <<<"$2"' _ "$busy_pid" "$out"
  echo idle >"$U/status.u-busy"
  printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"u-busy"}' "$busy_pid" "$(date +%s)000" >"$HOME/.claude/sessions/$busy_pid.json"
  out="$(uc)"
  check "... and restarts it once it is idle" contains "$out" "bosun update: u-busy: restarted"
  check "... with the update prompt, which it answers" wait_for 30 test -s "$U_STATE/update/notes/u-busy.status"
  out="$out$(uc)" # the restart's tick may take the answer itself: refresh waits for the agent to start
  check "... and a tick takes its answer; the one at a prompt is still waited for" bash -c '
    grep -q "u-busy: answered: resumed after the update" <<<"$1" && jq -e ".step == \"agents\" and .pending == [\"u-waiting\"]" "$2/update.json" >/dev/null' _ "$out" "$U_STATE"
  check "... and those ticks left it alone while it waits for you" bash -c 'kill -0 "$1" && ! grep -q "u-waiting" <<<"$2"' _ "$waiting_pid" "$out"
  u_status() { "${U_ENV[@]}" "${U_LIVE[@]}" "$U_LINK/bin/bosun" status --json 2>/dev/null; }
  check "the status, its update handed to the check-in loop: no update running, and a line for the window at a prompt" bash -c '
    jq -e ".update.state == \"none\" and (.detail | index(\"u-waiting restarts for the update once you answer it\"))" <<<"$1" >/dev/null' _ "$(u_status)"

  # The next version while the last update still waits for a window: it is not blocked, and takes the window
  # over. v3 changes nothing the windows were launched with, so the windows v2 restarted are left alone; the one
  # at a prompt was launched on v1, so it is still owed its restart.
  ug update-ref refs/heads/main "$U_V3_COMMIT"
  u_last_backup="$(jq -r .backup "$U_STATE/update.json")"
  u_last_id="$(jq -r .id "$U_STATE/update.json")"
  # And the orchestrator's plugins (its default list: none of mine) cannot be resolved on v3: an
  # installed_plugins.json that does not parse. Its launch there would refuse, so the update leaves it running.
  mkdir -p "$HOME/.claude/plugins"
  u_installed="$HOME/.claude/plugins/installed_plugins.json"
  [ ! -e "$u_installed" ] || mv "$u_installed" "$U/installed_plugins.kept"
  echo '[not an object]' >"$u_installed"
  u_refused="plugins: $u_installed does not parse, so bosun cannot tell which plugins to switch off; fix that, then bosun refresh orch"
  ul update --yes >"$U/takeover.out" 2>&1
  rc=$?
  # Its plan, as the update printed it before it went ahead.
  check "a next update while a window is still owed: the plan is not blocked, and lists it as owed from the last update" bash -c '
    ! grep -q "The update is blocked" "$1" && grep -q "^Still owed from the last update, which this one takes over:" "$1" &&
      grep -qx "  u-waiting: a restart" "$1" && grep -qx "  u-waiting: ask first" "$1" && grep -qx "  u-busy: leave" "$1"' _ "$U/takeover.out"
  check "... and the orchestrator, whose launch on v3 would refuse, is left running: the plan says why and what to do" \
    grep -qxF "  orch: left running, as it would not start on the new version: $u_refused" "$U/takeover.out"
  rm -f "$u_installed"
  [ ! -e "$U/installed_plugins.kept" ] || mv "$U/installed_plugins.kept" "$u_installed"
  check "... and so does the summary, the orchestrator still running" bash -c '
    kill -0 "$1" && grep -qE "^  orch +left running, as it would not start on the new version: " "$2" &&
      grep -qF "left running, as it would not start on the new version: $3" "$2"' _ "$orch_pid" "$U/takeover.out" "$u_refused"
  check "... it takes the window over, and its wait ends at once, as the window waits for your answer (exit 0)" bash -c '
    [ "$1" = 0 ] && grep -qx "Stopped waiting: every window still waited for waits for your answer." "$2" &&
      grep -qx "  u-waiting waits for your answer at its prompt; once you answer, bosun update advance, run by hand, restarts it for the update" "$2"' _ "$rc" "$U/takeover.out"
  check "... its record carries the last one and brings the window along, handed over" bash -c '
    jq -e --arg last "$2" ".step == \"agents\" and .pending == [\"u-waiting\"] and .windows[\"u-waiting\"] == [\"repo\", \"ask first\"] and
      .carried.id == \$last and (.handed_over | length > 0)" "$1/update.json" >/dev/null' _ "$U_STATE" "$u_last_id"
  check "... the last update's summary saved next to its backup, the window carried into the next update" bash -c '
    grep -qE "^  u-waiting +carried into the next update$" "$1/summary.md" && grep -qE "^  u-busy +restarted; resumed after the update$" "$1/summary.md" &&
      grep -qE "^  orch +asked to re-read policy.md; re-read, checked in, carrying on$" "$1/summary.md"' _ "$u_last_backup"
  check "... and the new summary says it is still waited for, owed by the last update too" \
    grep -qE "^  u-waiting +nothing yet; still waited for \(owed by the last update too\)$" "$U/takeover.out"
  check "... the window untouched, the link at v3, and the status calls no update running" bash -c '
    kill -0 "$1" && [ "$(cd "$2" && pwd -P)" = "$3" ] && jq -e ".update.state == \"none\"" <<<"$4" >/dev/null' \
    _ "$waiting_pid" "$U_LINK" "$(cd "$U_VERSIONS" && pwd -P)/${U_V3_COMMIT:0:12}" "$(u_status)"
  echo idle >"$U/status.u-waiting"
  printf '{"pid":%d,"status":"idle","version":"2.0.0","statusUpdatedAt":%d,"name":"u-waiting"}' "$waiting_pid" "$(date +%s)000" >"$HOME/.claude/sessions/$waiting_pid.json"
  out="$(uc)"
  check "once you answered it, a tick restarts it for the update" bash -c 'grep -q "bosun update: u-waiting: restarted" <<<"$1" && ! kill -0 "$2" 2>/dev/null' _ "$out" "$waiting_pid"
  check "... with the update prompt, which it answers" wait_for 30 test -s "$U_STATE/update/notes/u-waiting.status"
  out="$out$(uc)"
  check "... and a tick takes its answer: the update is done" bash -c '
    grep -q "u-waiting: answered: resumed after the update" <<<"$1" && jq -e ".step == \"done\" and .pending == []" "$2/update.json" >/dev/null' _ "$out" "$U_STATE"
  check "... and saves its summary next to its backup, saying the window restarted, owed by the last update too" bash -c '
    backup="$(jq -r .backup "$2/update.json")" && grep -qF "bosun update: done; its summary is in $backup/summary.md" <<<"$1" &&
      grep -qE "^  u-waiting +restarted; resumed after the update \(owed by the last update too\)$" "$backup/summary.md"' _ "$out" "$U_STATE"
  check "... after which a tick has nothing to do" bash -c '! grep -q "bosun update" <<<"$1"' _ "$(uc)"
  tmux -L "$U_TMUX" kill-server 2>/dev/null
  rm -f "$U_CONFIG/sessions.yml"

  # A next version whose launchd templates are broken: the morning job's fails to render, the
  # watchdog's renders XML that is not valid. Neither of those is unloaded or removed (the job that runs
  # `morning --now` renders fine, and goes on being reloaded: a job that fails does not stop the others).
  ug update-ref refs/heads/main "$U_V4_COMMIT"
  ub update --dry-run >"$U/broken-plan.out" 2>&1
  check "a next version whose launchd templates are broken: its plan is blocked, naming each job" bash -c '
    grep -q "The update is blocked." "$1" && grep -q "could not render the plist of com.bosun.update-test;" "$1" &&
      grep -q "the rendered plist of com.bosun.update-test.watchdog is invalid" "$1"' _ "$U/broken-plan.out"
  u_jobs="$HOME/Library/LaunchAgents/com.bosun.update-test"
  cp "$u_jobs.plist" "$U/morning.plist"
  cp "$u_jobs.watchdog.plist" "$U/watchdog.plist"
  u_bootouts() { grep -cE "launchctl bootout gui/$(id -u)/com.bosun.update-test(\.watchdog)?\$" "$WORK/system.log"; }
  bootouts="$(u_bootouts)"
  "${U_ENV[@]}" bash -c 'source "$1/lib/common.sh"; load_config; reload_changed_jobs' _ "$U_VERSIONS/${U_V4_COMMIT:0:12}" >/dev/null 2>&1
  rc=$?
  check "... and reloading its jobs fails (exit 1), leaving each installed plist as it was, and loaded" bash -c '
    [ "$1" = 1 ] && cmp -s "$2.plist" "$3/morning.plist" && cmp -s "$2.watchdog.plist" "$3/watchdog.plist" && [ "$4" = "$5" ]' \
    _ "$rc" "$u_jobs" "$U" "$bootouts" "$(u_bootouts)"

  section update-back "bosun update --back: one step back, what it restores and what it keeps (fake launchctl)" needs update-upstream covers 'tests/sandbox/update_patch.sh,lib/update_back*.py,lib/update.py,lib/update_state.py,lib/update_backups.py,lib/update_summary.py,lib/update_lookup.py,lib/migrations.py,libexec/bosun-update,libexec/bosun-ledger,libexec/bosun-kg'
  # An install of its own at the origin's v1 (its ledger at format 1, its common.sh without the
  # connector keys), following upstream's branch b-main: at v2 first (the ledger's format 2, and the kept keys
  # the update writes to bosun.env), then at v3 (a line of the readme: nothing a version reads the data by).
  mkdir -p "$WORK/b/share" "$WORK/b/state" "$WORK/b/config"
  B="$(cd "$WORK/b" && pwd -P)" # as the update names its folders
  B_LINK="$B/share/bosun"
  B_VERSIONS="$B/share/bosun-versions"
  B_STATE="$B/state"
  B_CONFIG="$B/config"
  ug branch -f b-main "$U_V2_COMMIT"
  git -c core.hooksPath=/dev/null clone -q --bare "$U_UP" "$B_VERSIONS/bosun.git"
  git --git-dir="$B_VERSIONS/bosun.git" config bosun.managed true
  git --git-dir="$B_VERSIONS/bosun.git" config bosun.branch b-main
  git --git-dir="$B_VERSIONS/bosun.git" -c core.hooksPath=/dev/null worktree add -q --detach "$B_VERSIONS/${U_V1_COMMIT:0:12}" "$U_V1_COMMIT"
  ln -s "bosun-versions/${U_V1_COMMIT:0:12}" "$B_LINK"
  B_V1="$B_VERSIONS/${U_V1_COMMIT:0:12}"
  B_V2="$B_VERSIONS/${U_V2_COMMIT:0:12}"
  printf '# acme: an install from before connectors\n' >"$B_CONFIG/bosun.env"
  B_ENV=(env BOSUN_STATE_DIR="$B_STATE" BOSUN_LOG_DIR="$B_STATE/logs" BOSUN_CONFIG_DIR="$B_CONFIG"
    BOSUN_SESSION_NAME=back-test BOSUN_LAUNCHD_LABEL=com.bosun.back-test BOSUN_SKIP_PREFLIGHT=1 BOSUN_KNOWLEDGE_GIT=0
    BOSUN_CLAUDE_BIN="$U/quiet-claude" BOSUN_UPDATE_CHANNEL=main)
  bb() { "${B_ENV[@]}" "$B_LINK/bin/bosun" "$@"; }
  b_at() { [ "$(cd "$B_LINK" && pwd -P)" = "$1" ]; }
  b_has() { jq -e --arg id "$1" '.items | has($id)' "$B_STATE/ledger.json" >/dev/null; } # <item>
  bb ledger upsert 'review:acme/api#31' --kind review-request --title "Written on v1" >/dev/null
  bb kg import "$KG_FIXTURE" --apply --auto-confirm >/dev/null 2>&1
  b_env_before="$(cat "$B_CONFIG/bosun.env")"
  check "with no update finished, --back is refused: there is nothing to go back to" fails_with "no update has finished here" bb update --back --dry-run
  check "--back takes no --branch" fails_with "--back and --branch are separate" bb update --back --branch b-main
  check "... nor --check" fails_with "--check and --skip take no --back or --branch" bb update --back --check
  bb update --yes >"$B/forward.out" 2>&1
  rc=$?
  check "an update to v2 first (exit 0): the ledger migrated, the kept keys written to bosun.env" bash -c '
    [ "$1" = 0 ] && [ "$(jq -r .version "$2/ledger.json")" = 2 ] && grep -q "^BOSUN_ORCHESTRATOR_CONNECTORS=\"all\"" "$3/bosun.env"' _ "$rc" "$B_STATE" "$B_CONFIG"
  B_V1="$(cd "$B_V1" && pwd -P)"
  B_V2="$(cd "$B_V2" && pwd -P)"
  b_over_id="$(jq -r .id "$B_STATE/update.json")"
  b_over_backup="$(jq -r .backup "$B_STATE/update.json")"
  bb ledger upsert 'review:acme/api#32' --kind review-request --title "Written on v2, since the update" >/dev/null
  # v1's folder gone (deleted by hand, or pruned): its commit is still in bosun.git.
  git --git-dir="$B_VERSIONS/bosun.git" worktree remove --force "$B_V1"
  out="$(bb update --back --dry-run 2>&1)"
  check "--back --dry-run with v1's folder gone adds no folder, and reads v1 from bosun.git: what it cannot tell yet, said" bash -c '
    [ ! -e "$2" ] && grep -qF "$2 is not there: going back adds it again from bosun.git, at ${3:0:7}" <<<"$1" &&
      grep -q "^Not known in this dry run:" <<<"$1"' _ "$out" "$B_V1" "$U_V1_COMMIT"
  check "--back --dry-run: its plan goes back over that update, restores the ledger (format 2 -> 1) and says what that loses" bash -c '
    grep -q "^Go back ${2:0:7} -> ${3:0:7}, over the update of " <<<"$1" &&
      grep -q "ledger: format 2 -> 1: restored from that update.s backup; loses 1 change written since (the last at .*), to review:acme/api#32" <<<"$1"' _ "$out" "$U_V2_COMMIT" "$U_V1_COMMIT"
  check "... keeps the knowledge base, which both versions read alike" contains "$out" "knowledge: kept, with what was written since (format 1, rules 1 in both)"
  check "... takes back what the update wrote to bosun.env" contains "$out" 'bosun.env: BOSUN_ORCHESTRATOR_CONNECTORS="all" removed'
  check "... and changes nothing" bash -c '[ "$(cd "$1" && pwd -P)" = "$2" ] && jq -e ".kind == \"\" and .step == \"done\"" "$3/update.json" >/dev/null' _ "$B_LINK" "$B_V2" "$B_STATE"
  bb update --back --yes >"$B/back.out" 2>&1
  rc=$?
  check "--back --yes goes back (exit 0): the link at v1" bash -c '[ "$1" = 0 ] && [ "$(cd "$2" && pwd -P)" = "$3" ] || { tail -15 "$4"; exit 1; }' _ "$rc" "$B_LINK" "$B_V1" "$B/back.out"
  check "... v1's folder added again from bosun.git, at its commit, said" bash -c '
    [ "$(git -C "$2" rev-parse HEAD)" = "$3" ] && grep -qF "Added $2 again, the version the last update came from" "$1"' _ "$B/back.out" "$B_V1" "$U_V1_COMMIT"
  check "... the ledger restored from that update's backup: format 1, without what was written since" bash -c '
    [ "$(jq -r .version "$1/ledger.json")" = 1 ] && jq -e ".items | has(\"review:acme/api#31\") and (has(\"review:acme/api#32\") | not)" "$1/ledger.json" >/dev/null' _ "$B_STATE"
  check "... and valid against its journal, as v1 reads it" test "$(bb ledger validate --journal 2>&1)" = "ledger is valid"
  check "... what was written since kept in the backup going back took first" bash -c '
    own="$(jq -r .backup "$1/update.json")" && [ "$own" != "$2" ] && jq -e ".items | has(\"review:acme/api#32\")" "$own/state/ledger.json" >/dev/null' _ "$B_STATE" "$b_over_backup"
  check "... bosun.env as it was before the update, kept first as it was before going back" bash -c '
    [ "$(cat "$1/bosun.env")" = "$2" ] && grep -q "^BOSUN_ORCHESTRATOR_CONNECTORS=\"all\"" "$1"/bosun.env.pre-back-*' _ "$B_CONFIG" "$b_env_before"
  check "... the record: a back, done, over that update, with what it restored" jq -e --arg id "$b_over_id" '
    .kind == "back" and .step == "done" and .previous.id == $id and (.restored | length == 1) and (.config_changed | length == 1)' "$B_STATE/update.json"
  check "... its summary says so, and is saved beside its own backup" bash -c '
    grep -q "^bosun went back: ${2:0:7} -> ${3:0:7} in .*, over the update of " "$1" && [ "$(tail -1 "$1")" = "Going forward again: bosun update" ] &&
      grep -q "^bosun went back" "$(jq -r .backup "$4/update.json")/back-summary.md"' _ "$B/back.out" "$U_V2_COMMIT" "$U_V1_COMMIT" "$B_STATE"
  check "... and the version it left is skipped: --check no longer offers it" jq -e --arg v "${U_V2_COMMIT:0:12}" '.skipped == true and .latest.version == $v' <<<"$(bb update --check --json)"
  check "a second --back is refused: one step back, never further" fails_with "bosun already went back from ${U_V2_COMMIT:0:7} to ${U_V1_COMMIT:0:7}" bb update --back --dry-run
  out="$(bb update --dry-run 2>&1)"
  check "bosun update goes forward again, its plan saying --back left that version" bash -c '
    grep -q "^Update $3 -> $2" <<<"$1" && grep -q "^bosun update --back left this version on " <<<"$1"' _ "$out" "$U_V2_COMMIT" "$U_V1_COMMIT"
  bb update --yes >"$B/forward2.out" 2>&1
  rc=$?
  check "... and does (exit 0), migrating the ledger again" bash -c '[ "$1" = 0 ] && [ "$(jq -r .version "$2/ledger.json")" = 2 ] || { tail -15 "$3"; exit 1; }' _ "$rc" "$B_STATE" "$B/forward2.out"
  mv "$B_STATE/backups" "$B/backups.aside"
  check "with the backup a restore needs gone (a kg forget deletes them all), --back is blocked, saying so" \
    fails_with "the ledger needs the backup that update took (format 2 -> 1), which is gone" bb update --back --dry-run
  mv "$B/backups.aside" "$B_STATE/backups"

  # Over an update that changes nothing a version reads the data by: everything written since is kept.
  ug branch -f b-main "$U_V3_COMMIT"
  bb update --yes >"$B/forward3.out" 2>&1
  rc=$?
  check "an update to v3, which changes nothing a version reads the data by (exit 0)" u_ended_well "$rc" "$B/forward3.out"
  b_over_backup="$(jq -r .backup "$B_STATE/update.json")"
  bb ledger upsert 'review:acme/api#33' --kind review-request --title "Written on v3, since the update" >/dev/null
  bb kg add-entity repo/acme-billing --name "acme billing" >/dev/null 2>&1
  b_backups="$(find "$B_STATE/backups" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
  out="$(bb update --back --dry-run 2>&1)"
  check "--back over it keeps both stores, with what was written since" bash -c '
    grep -q "ledger: kept, with what was written since (format 2, rules 1 in both)" <<<"$1" && grep -q "knowledge: kept" <<<"$1" && ! grep -q "restored" <<<"$1"' _ "$out"
  bb update --back --yes >"$B/back2.out" 2>&1
  rc=$?
  check "--back --yes (exit 0): the link at v2, nothing frozen, no backup taken" bash -c '
    [ "$1" = 0 ] && [ "$(cd "$2" && pwd -P)" = "$3" ] && [ "$(find "$4/backups" -mindepth 1 -maxdepth 1 | wc -l | tr -d " ")" = "$5" ] &&
      jq -e ".kind == \"back\" and .step == \"done\" and .backup == \"\" and .freeze_ms == 0" "$4/update.json" >/dev/null || { tail -15 "$6"; exit 1; }' \
    _ "$rc" "$B_LINK" "$B_V2" "$B_STATE" "$b_backups" "$B/back2.out"
  check "... the ledger write and the knowledge write made since are both there" bash -c '
    jq -e ".items | has(\"review:acme/api#33\")" "$1/ledger.json" >/dev/null && grep -q "repo/acme-billing" "$2/knowledge/entities.jsonl"' _ "$B_STATE" "$B_CONFIG"
  check "... and its summary is saved beside the backup of the update it went back over" grep -q "^bosun went back: ${U_V3_COMMIT:0:7} -> ${U_V2_COMMIT:0:7}" "$b_over_backup/back-summary.md"
  check "the data passes its checks on v2 after it" test "$(bb ledger validate --journal 2>&1)" = "ledger is valid"

  section update-branch "bosun update --branch: following a branch of the origin, and main again (fake launchctl)" needs update-upstream covers 'tests/sandbox/update_patch.sh,libexec/bosun-update,libexec/bosun-settings,lib/update_back*.py,lib/update_facts.py,lib/update_lookup.py,lib/update_check.py,lib/settings.py,libexec/bosun-ledger'
  # An install of its own at v2, on the releases channel (bosun.env says so), its data v2's (the ledger at
  # format 2, rules 1). Upstream's branches: try-x at v3, old-x at v1 (the ledger at format 1, older than the
  # data). (A branch older than the data in a store's rules goes through the same target-check as old-x does:
  # tests/unit/test_update_back.py reads the rules from a version's source, test_update_back_plan.py judges them.)
  mkdir -p "$WORK/r/share" "$WORK/r/state" "$WORK/r/config"
  R="$(cd "$WORK/r" && pwd -P)"
  R_LINK="$R/share/bosun"
  R_VERSIONS="$R/share/bosun-versions"
  R_STATE="$R/state"
  R_CONFIG="$R/config"
  # No release on the origin: the update section tags two and deletes them again, but one it left (a check failed
  # between) would be what this install, on the releases channel, follows.
  for tag in $(ug tag -l); do ug tag -d "$tag" >/dev/null; done
  ug branch -f try-x "$U_V3_COMMIT"
  ug branch -f old-x "$U_V1_COMMIT"
  ug update-ref refs/heads/main "$U_V4_COMMIT" # main's head: v4, its launchd templates broken (the last check)
  git -c core.hooksPath=/dev/null clone -q --bare "$U_UP" "$R_VERSIONS/bosun.git"
  git --git-dir="$R_VERSIONS/bosun.git" config bosun.managed true
  git --git-dir="$R_VERSIONS/bosun.git" -c core.hooksPath=/dev/null worktree add -q --detach "$R_VERSIONS/${U_V2_COMMIT:0:12}" "$U_V2_COMMIT"
  ln -s "bosun-versions/${U_V2_COMMIT:0:12}" "$R_LINK"
  R_V2="$(cd "$R_VERSIONS/${U_V2_COMMIT:0:12}" && pwd -P)"
  printf 'BOSUN_UPDATE_CHANNEL="releases"\n' >"$R_CONFIG/bosun.env"
  R_ENV=(env BOSUN_STATE_DIR="$R_STATE" BOSUN_LOG_DIR="$R_STATE/logs" BOSUN_CONFIG_DIR="$R_CONFIG"
    BOSUN_SESSION_NAME=branch-test BOSUN_LAUNCHD_LABEL=com.bosun.branch-test BOSUN_SKIP_PREFLIGHT=1 BOSUN_KNOWLEDGE_GIT=0
    BOSUN_CLAUDE_BIN="$U/quiet-claude")
  rb() { "${R_ENV[@]}" "$R_LINK/bin/bosun" "$@"; }
  r_branch() { git --git-dir="$R_VERSIONS/bosun.git" config bosun.branch; }
  rb ledger upsert 'review:acme/api#41' --kind review-request --title "Written on v2" >/dev/null
  r_env="$(cat "$R_CONFIG/bosun.env")"
  check "--branch to a branch the origin does not have is refused, and nothing changes" fails_with "has no branch nope: nothing changed" rb update --branch nope
  check "... nothing: no branch followed, bosun.env as it was, no version folder" bash -c '
    ! git --git-dir="$1/bosun.git" config bosun.branch && [ "$(cat "$2/bosun.env")" = "$3" ] && [ "$(find "$1" -mindepth 1 -maxdepth 1 | wc -l | tr -d " ")" = 2 ]' _ "$R_VERSIONS" "$R_CONFIG" "$r_env"
  check "... with --json, the one object with the error" bash -c '
    out="$("$@" update --branch nope --json 2>/dev/null)"; [ $? = 1 ] && jq -e ".contract == 1 and .plan == null and (.error | endswith(\"has no branch nope: nothing changed\"))" <<<"$out" >/dev/null' _ "${R_ENV[@]}" "$R_LINK/bin/bosun"
  check "a name git does not take as a branch's is a usage error" fails_with "is not a branch's name" rb update --branch "a..b"
  check "... and changes nothing: no branch followed, bosun.env as it was" bash -c '
    ! git --git-dir="$1/bosun.git" config bosun.branch && [ "$(cat "$2/bosun.env")" = "$3" ]' _ "$R_VERSIONS" "$R_CONFIG" "$r_env"
  # Another bosun settings set (or bosun init) holding its lock: settings set refuses, and --branch sets nothing.
  hold_lock "$R_STATE/init.lock" branch-init
  check "--branch whose channel cannot be set (bosun settings set refuses) sets nothing at all" bash -c '
    out="$("${@:4}" update --branch try-x </dev/null 2>&1)"; [ $? = 1 ] && grep -q "nothing changed: the install follows what it did" <<<"$out" &&
      ! git --git-dir="$1/bosun.git" config bosun.branch && [ "$(cat "$2/bosun.env")" = "$3" ]' _ "$R_VERSIONS" "$R_CONFIG" "$r_env" "${R_ENV[@]}" "$R_LINK/bin/bosun"
  release_lock branch-init
  # bosun.branch that cannot be set (a config.lock left in bosun.git) once the channel was: it is put back.
  mkdir "$R_VERSIONS/bosun.git/config.lock"
  check "--branch whose bosun.branch cannot be set puts the channel back, and says so" bash -c '
    out="$("${@:4}" update --branch try-x </dev/null 2>&1)"; [ $? = 1 ] &&
      grep -q "could not set bosun.branch in .*; BOSUN_UPDATE_CHANNEL put back to releases: nothing changed" <<<"$out" &&
      grep -q "^BOSUN_UPDATE_CHANNEL=\"\{0,1\}releases\"\{0,1\}$" "$2/bosun.env" && ! git --git-dir="$1/bosun.git" config bosun.branch' \
    _ "$R_VERSIONS" "$R_CONFIG" "$r_env" "${R_ENV[@]}" "$R_LINK/bin/bosun"
  rmdir "$R_VERSIONS/bosun.git/config.lock"
  r_env="$(cat "$R_CONFIG/bosun.env")" # as bosun settings set wrote it back
  out="$(rb update --branch try-x --dry-run 2>&1)"
  check "--branch --dry-run shows the plan to the branch's head, and writes nothing" bash -c '
    grep -q "^Update $2 -> $3" <<<"$1" && ! git --git-dir="$4/bosun.git" config bosun.branch && [ "$(cat "$5/bosun.env")" = "$6" ]' \
    _ "$out" "$U_V2_COMMIT" "$U_V3_COMMIT" "$R_VERSIONS" "$R_CONFIG" "$r_env"
  rb update --branch try-x </dev/null >"$R/declined.out" 2>&1
  rc=$?
  check "--branch said no to (no terminal: the plan alone): the install follows the branch from now on, and says so" bash -c '
    [ "$1" = 0 ] && grep -qx "bosun follows try-x from now on (bosun update --branch main follows main again)" "$2" &&
      grep -q "^bosun now follows try-x; nothing else changed: in a terminal it asks first" "$2" && ! grep -q "Nothing has changed" "$2"' _ "$rc" "$R/declined.out"
  check "... bosun.branch is try-x, the channel main in bosun.env (its copy kept), the version as it was" bash -c '
    [ "$(git --git-dir="$1/bosun.git" config bosun.branch)" = try-x ] && grep -q "^BOSUN_UPDATE_CHANNEL=\"\{0,1\}main\"\{0,1\}" "$2/bosun.env" &&
      ls "$2"/bosun.env.pre-init-* >/dev/null && [ "$(cd "$3" && pwd -P)" = "$4" ]' _ "$R_VERSIONS" "$R_CONFIG" "$R_LINK" "$R_V2"
  rb update --yes >"$R/try.out" 2>&1
  rc=$?
  check "... so bosun update moves to the branch's head (exit 0)" bash -c '[ "$1" = 0 ] && [ "$(cd "$2" && pwd -P)" = "$3" ] || { tail -15 "$4"; exit 1; }' \
    _ "$rc" "$R_LINK" "$(cd "$R_VERSIONS/${U_V3_COMMIT:0:12}" 2>/dev/null && pwd -P)" "$R/try.out"
  check "--branch to a branch whose code is older in a store's format than the data is refused, pointing at --back" bash -c '
    out="$("$@" update --branch old-x 2>&1)"; [ $? = 1 ] && grep -q "has the ledger at format 1, and the data is at format 2" <<<"$out" &&
      grep -q "bosun update --back" <<<"$out"' _ "${R_ENV[@]}" "$R_LINK/bin/bosun"
  check "... nothing changed: still following try-x, bosun.env as it was, the version as it was" bash -c '
    [ "$(git --git-dir="$1/bosun.git" config bosun.branch)" = try-x ] && [ "$(cat "$2/bosun.env")" = "$3" ] && [ "$(cd "$4" && pwd -P)" = "$5" ]' \
    _ "$R_VERSIONS" "$R_CONFIG" "$(cat "$R_CONFIG/bosun.env")" "$R_LINK" "$(cd "$R_VERSIONS/${U_V3_COMMIT:0:12}" && pwd -P)"
  rb update --branch main </dev/null >"$R/main.out" 2>&1
  rc=$?
  check "--branch main follows main again, and says so; main's head (the origin's v4, its launchd templates broken) is blocked at its plan (exit 1)" bash -c '
    [ "$1" = 1 ] && [ "$(git --git-dir="$2/bosun.git" config bosun.branch)" = main ] &&
      grep -qx "bosun follows main from now on (bosun update --branch main follows main again)" "$3" && grep -q "The update is blocked." "$3"' _ "$rc" "$R_VERSIONS" "$R/main.out"
  always_run
else
  fail_check "fake launchctl/pmset are not first on PATH; skipped install tests"
fi
unset BOSUN_LAUNCHD_LABEL

# What this whole run left behind is the best list there is of what bosun writes: uninstall must know it all,
# or the file stays on people's machines after they uninstall. A new file goes in the tables of lib/file_tables.py.
section uninstall-files "uninstall knows every file bosun writes" covers 'lib/uninstall*.py,lib/file_tables.py,lib/agent_changes.py,libexec/bosun-uninstall'
knows_all_of() { # <state dir> <config dir>: names what it does not know
  local unknown
  unknown="$(BOSUN_STATE_DIR="$1" BOSUN_LOG_DIR="$1/logs" BOSUN_CONFIG_DIR="$2" "$BOSUN" uninstall --dry-run 2>&1 |
    grep -B1 "does not know this file" | grep -v "does not know this file\|^--")"
  [ -z "$unknown" ] || {
    echo "$unknown"
    return 1
  }
}
for state in "$WORK/state" "$WORK/approvals-state" "$WORK/worktrees-state"; do
  check "what a run left in $(basename "$state")" knows_all_of "$state" "$WORK/config"
done
check "what it left in the knowledge base's config" knows_all_of "$WORK/state" "$WORK/kg-config"
if [ -d "$WORK/u/state" ]; then
  check "what bosun update left in its state and config" knows_all_of "$WORK/u/state" "$WORK/u/config"
fi

always_run "the repository this ran from"
repo_unchanged() {
  local after
  after="$(repo_state)"
  [ "$after" = "$REPO_STATE_BEFORE" ] && return 0
  echo "!!! THE REAL REPOSITORY CHANGED DURING THE SMOKE RUN !!!" >&2
  echo "before: $REPO_STATE_BEFORE" >&2
  echo "after:  $after" >&2
  return 1
}
check "its HEAD, staged files and core.bare are what they were before the run" repo_unchanged

full_run_end
summary
[ "$failed" -eq 0 ]
