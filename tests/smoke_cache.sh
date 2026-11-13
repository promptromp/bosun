# shellcheck shell=bash
# What tests/smoke.sh keeps between runs, in tests/.smoke-cache/ (git-ignored, one per checkout), sourced by it
# and by tests/unit/test_smoke_sections.py:
#   sections  one line per section: `<id> pass|fail`, its last result, for --last-failed. A run changes only the
#             sections it ran.
#   passes    one line per tree that a whole run passed: `<tree> <checks passed> <epoch seconds>`, for --pre-push.
# Everything here is best effort: a run never fails, and never passes, because of what it keeps. Whatever is
# unreadable, missing, older than a day, or in doubt means the whole suite runs. Bash 3.2.

PASS_MAX_AGE=86400 # a record of a pass is good for a day

# cache_update <file> <key> [line]: the lines whose first word is <key> dropped, <line> added, in one rename.
# Keeps the last 200 lines. Never fails.
cache_update() {
  local file="$1" tmp
  {
    mkdir -p "$(dirname "$file")" && tmp="$(mktemp "$file.XXXXXX")" || return 0
    {
      [ ! -f "$file" ] || awk -v key="$2" '$1 != key' "$file"
      [ -z "${3:-}" ] || printf '%s\n' "$3"
    } | tail -n 200 >"$tmp"
    mv "$tmp" "$file" || rm -f "$tmp"
  } 2>/dev/null
  return 0
}

# tree_of_checkout <root>: the hash of the tree that is on disk in <root>: the tracked files as they are now,
# and the untracked ones that are not ignored, which is what `git add -A` stages. Taken in a copy of the index,
# so the real one is not touched. Ignored paths (caches, __pycache__, this folder) cannot change it. Whatever
# untracked file could have influenced a run stays in on purpose: a commit that lacks it has another tree, and
# the whole suite runs for it. Prints nothing and fails if git cannot say.
tree_of_checkout() {
  local gitdir index tree
  gitdir="$(git -C "$1" rev-parse --absolute-git-dir 2>/dev/null)" || return 1
  index="$(mktemp "${TMPDIR:-/tmp}/smoke-index.XXXXXX")" || return 1
  cp "$gitdir/index" "$index" 2>/dev/null || rm -f "$index" # no index: git starts one at that path
  tree="$(GIT_INDEX_FILE="$index" git -C "$1" add -A 2>/dev/null && GIT_INDEX_FILE="$index" git -C "$1" write-tree 2>/dev/null)"
  rm -f "$index"
  [ -n "$tree" ] || return 1
  echo "$tree"
}

# status_of_checkout <root>: `git status --porcelain` of it (what is changed or untracked and not ignored).
status_of_checkout() { git -C "$1" status --porcelain 2>/dev/null; }

# changed_paths <status before> <status after>: the paths that show in the second and not in the first, on one
# line; what a run left in the repository.
changed_paths() {
  comm -13 <(printf '%s\n' "$1" | sort) <(printf '%s\n' "$2" | sort) | grep -v '^$' | cut -c4- | tr '\n' ' ' | sed 's/ $//'
}

# is_tree <text>: a full git tree hash (SHA-1 or SHA-256).
is_tree() {
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
  [ "${#1}" -eq 40 ] || [ "${#1}" -eq 64 ]
}

# record_full_run <passes file> <tree at the start> <tree at the end> <failed> <passed> <now>: what a whole run
# (no --only, --last-failed or -x) leaves. A failure forgets the tree's earlier pass, if any (return 1). A pass is
# recorded only if the tree at the end is the tree at the start: the run changed no tracked file and made no
# untracked one that is not ignored (return 2 if it did, or if either tree is unknown: nothing is recorded).
record_full_run() {
  is_tree "$2" || return 2
  if [ "$4" -gt 0 ]; then
    cache_update "$1" "$2"
    return 1
  fi
  [ "$2" = "$3" ] || return 2
  cache_update "$1" "$2" "$2 $5 $6"
}

# passed_recently <passes file> <tree> <now>: prints when <tree> was recorded as passing, if that was less than
# a day ago and not in the future. Anything else, an unreadable file or an odd line, fails.
passed_recently() {
  local tree count when extra
  is_tree "$2" || return 1
  [ -r "$1" ] || return 1
  while read -r tree count when extra; do
    [ "$tree" = "$2" ] && [ -z "$extra" ] || continue
    case "$count$when" in '' | *[!0-9]*) continue ;; esac
    [ -n "$count" ] && [ -n "$when" ] && [ "${#count}" -le 9 ] && [ "${#when}" -le 12 ] || continue
    [ $((10#$count)) -gt 0 ] && [ $((10#$when)) -le "$3" ] && [ $(($3 - 10#$when)) -lt "$PASS_MAX_AGE" ] || continue
    echo "$((10#$when))"
    return 0
  done <"$1" 2>/dev/null
  return 1
}

# clock_text <epoch>: a time for people, on a BSD or a GNU date.
clock_text() { date -r "$1" '+%F %H:%M' 2>/dev/null || date -d "@$1" '+%F %H:%M' 2>/dev/null || echo "$1"; }

# pre_push_skip <root> <passes file> <pre-commit's to-ref> <now>: whether the pre-push hook has nothing to run:
# 0 if that ref's tree passed a whole run here in the last day (it says so), 3 for a branch deletion (nothing to
# test), 1 for everything else: the whole suite runs. BOSUN_SMOKE_NO_CACHE=1 forces 1.
pre_push_skip() {
  local tree when
  case "$3" in
    '') return 1 ;;
    *[!0]*) ;;
    *)
      echo "a branch deletion: nothing to test"
      return 3
      ;;
  esac
  if [ "${BOSUN_SMOKE_NO_CACHE:-}" = 1 ]; then
    echo "BOSUN_SMOKE_NO_CACHE=1: running the whole suite"
    return 1
  fi
  tree="$(git -C "$1" rev-parse --verify -q "$3^{tree}" 2>/dev/null)" || return 1
  when="$(passed_recently "$2" "$tree" "$4")" || return 1
  echo "smoke already passed on this exact tree at $(clock_text "$when"): skipping"
}
