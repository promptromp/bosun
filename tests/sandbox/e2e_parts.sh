# shellcheck shell=bash
# The parts of a Mac VM suite (tests/install_e2e.sh, tests/work_e2e.sh, tests/app_e2e.sh, tests/sandbox/live.sh),
# sourced by tests/sandbox/e2e_lib.sh and by live.sh. A suite's main body is its `part` lines, in the order they
# run, then `parts_end`:
#
#   part <id> "<name>" [needs <id>,...] [last] [paid] covers '<glob>,...'
#
# Each runs the function part_<id> (its dashes as underscores: part_console_job), which groups the suite's steps.
# tests/sandbox/mac.sh chooses which run (tools/vm_select.py: --only, --changed) and passes them in E2E_PARTS, what
# they need included: the VM resolves nothing. An empty E2E_PARTS runs every part, the whole suite. `needs`,
# `last`, `paid` and `covers` are read by tools/vm_select.py and tests/unit/test_vm_selection.py, not here. With
# E2E_EXITFIRST=1 (mac.sh's -x) no part starts after a failed check. A part E2E_PARTS names that the suite has no
# line for is a failed check. It uses the suite's `failed` count, and parts_end its `passed` and $OUT.
# shellcheck disable=SC2154 # passed and failed are the suite's, which sources this

PARTS_NOT_RUN="" # the parts -x left out, for the summary
PARTS_SEEN=","   # every part line this run went past, chosen or not

# part_chosen <id>: this run runs part <id>.
part_chosen() {
  [ -z "${E2E_PARTS:-}" ] && return 0
  case ",$E2E_PARTS," in *",$1,"*) return 0 ;; esac
  return 1
}

# part_named <id>: E2E_PARTS names part <id>: chosen for itself, by --only or --changed, not as the whole suite.
part_named() { [ -n "${E2E_PARTS:-}" ] && part_chosen "$1"; }

part() { # <id> "<name>" [needs <id>,...] [last] [paid] covers '<glob>,...'
  PARTS_SEEN="$PARTS_SEEN$1,"
  part_chosen "$1" || return 0
  if [ "${E2E_EXITFIRST:-0}" = 1 ] && [ "$failed" -gt 0 ]; then
    PARTS_NOT_RUN="${PARTS_NOT_RUN:+$PARTS_NOT_RUN }$1"
    return 0
  fi
  echo "--- part $1: $2"
  "part_${1//-/_}"
}

# parts_names_known: each part E2E_PARTS names had its line in this run, else a failed check that names it: a run that
# names no part of the suite must not pass with nothing checked. (Nothing to say before any part line ran: the
# half of app's restart after it.)
parts_names_known() {
  local id
  [ "$PARTS_SEEN" != "," ] || return 0
  for id in ${E2E_PARTS//,/ }; do
    case "$PARTS_SEEN" in *",$id,"*) continue ;; esac
    failed=$((failed + 1))
    echo "  FAIL  E2E_PARTS names $id, which is no part of this suite"
  done
}

# parts_end: the summary (a partial run says so, and -x what it left out) and the counts; exit status as they say.
parts_end() {
  [ -z "${OUT:-}" ] || rm -rf "$OUT"
  parts_names_known
  echo
  [ -z "${E2E_PARTS:-}" ] || echo "partial run, not the whole suite: ${E2E_PARTS//,/ }"
  [ -z "$PARTS_NOT_RUN" ] || echo "stopped after the first failure (-x), so these did not run: $PARTS_NOT_RUN"
  echo "$passed passed, $failed failed"
  [ "$failed" -eq 0 ]
}
