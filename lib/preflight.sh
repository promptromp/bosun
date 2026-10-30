#!/bin/sh
# Usage: sh lib/preflight.sh [--quiet]   (or bosun preflight)
#
# What bosun needs before it can be set up, and exactly what to do about anything that is missing. It
# changes nothing and installs nothing: it lists everything at once, so one pass fixes it all, and
# exits early. In three layers, so that another OS or agent is one more file:
#   1. the OS picks a platform profile (lib/platforms/<os>.sh): its package manager, how to install
#      from it, and what to say when something is missing
#   2. a harness profile (lib/harnesses/<harness>.sh): how to check that agent is ready
#   3. this flow, which knows neither
# Plain POSIX sh, because it runs before Python 3.13 is known to be there.
#   --quiet   only print what needs doing
# BOSUN_PLATFORM and BOSUN_HARNESS override what it picks. BOSUN_RERUN is what to run afterwards.
#
# Exit status: 0 all there, 1 something to do, 2 this OS or harness has no profile.

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUCK_URL="https://github.com/promptromp/bosun/blob/main/docs/setup.md#if-you-get-stuck"
ROADMAP_URL="https://github.com/promptromp/bosun/blob/main/ROADMAP.md"
PYTHON_FLOOR="3.13"
TOOLS="git tmux jq ruby curl gh"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
NEWLINE='
'
problems=0
steps=""
packages=""

check_ok() { [ "$QUIET" = 1 ] || printf '  %-10s %s\n' "ok" "$1"; }
report() { # <what happened> <what> [the next step]
  printf '  %-10s %s\n' "$1" "$2"
  problems=$((problems + 1))
  [ -z "${3:-}" ] || steps="$steps$3$NEWLINE"
}
check_todo() { report "to do" "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }
want_package() { packages="$packages $(platform_package "$1")"; }

version_at_least() { # <version> <floor>
  awk -v have="$1" -v need="$2" 'BEGIN {
    count = split(need, floor, "."); split(have, got, ".")
    for (i = 1; i <= count; i++) {
      if (got[i] + 0 > floor[i] + 0) exit 0
      if (got[i] + 0 < floor[i] + 0) exit 1
    }
    exit 0
  }'
}

load_profiles() {
  platform="${BOSUN_PLATFORM:-$(uname -s | tr '[:upper:]' '[:lower:]' | sed 's/^darwin$/macos/')}"
  if [ ! -f "$ROOT/lib/platforms/$platform.sh" ]; then
    printf "bosun does not run on %s yet. It runs on macOS today, and Linux is next: %s\n" "$platform" "$ROADMAP_URL"
    exit 2
  fi
  harness="${BOSUN_HARNESS:-claude}"
  if [ ! -f "$ROOT/lib/harnesses/$harness.sh" ]; then
    printf "bosun has no profile for the harness '%s' yet. It has:" "$harness"
    for profile in "$ROOT"/lib/harnesses/*.sh; do
      printf ' %s' "$(basename "$profile" .sh)"
    done
    printf '\n'
    exit 2
  fi
  # shellcheck disable=SC1090 # chosen just above
  . "$ROOT/lib/platforms/$platform.sh"
  # shellcheck disable=SC1090 # chosen just above
  . "$ROOT/lib/harnesses/$harness.sh"
}

# The two things everything else stands on. Without the first, even asking can open a dialog.
check_foundation() {
  if ! platform_base_ready; then
    report "missing" "$(platform_base_name)" "$(platform_base_help)"
    finish
  fi
  if platform_manager_ready; then
    check_ok "$(platform_manager_name)"
  else
    report "missing" "$(platform_manager_name)" "$(platform_manager_help)"
  fi
}

check_tools() {
  for tool in $TOOLS; do
    if have "$tool"; then
      check_ok "$tool"
    else
      report "missing" "$tool"
      want_package "$tool"
    fi
  done
}

python_ready() {
  have python3 && version_at_least "$(python_version)" "$PYTHON_FLOOR"
}
python_version() { python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }

check_python() {
  if python_ready; then
    check_ok "Python $(python_version)"
  elif have python3; then
    report "outdated" "Python $(python_version), and bosun needs $PYTHON_FLOOR or newer"
    want_package python3
  else
    report "missing" "Python $PYTHON_FLOOR or newer"
    want_package python3
  fi
}

check_logins() {
  have gh || return 0
  if gh auth status >/dev/null 2>&1; then
    check_ok "GitHub CLI logged in"
  else
    check_todo "GitHub CLI not logged in" "Log in to GitHub: gh auth login"
  fi
}

check_harness() {
  [ "$QUIET" = 1 ] || printf '\n%s\n' "$(harness_name)"
  if ! have "$(harness_command)"; then
    report "missing" "$(harness_name)" "$(harness_install_help)"
    return 0
  fi
  version="$(harness_version)"
  if version_at_least "${version:-0}" "$(harness_min_version)"; then
    check_ok "version ${version:-unknown}"
  else
    report "outdated" "version ${version:-unknown}, and bosun needs $(harness_min_version) or newer" "$(harness_upgrade_help)"
  fi
  if python_ready; then
    harness_checks
  else
    [ "$QUIET" = 1 ] || printf '  %-10s %s\n' "later" "its login is checked once Python is in place"
  fi
}

print_steps() {
  printf '\nWhat to do next:\n'
  number=0
  printf '%s' "$steps" | while IFS= read -r step; do
    number=$((number + 1))
    printf '  %d. %s\n' "$number" "$step"
  done
}

finish() {
  if [ "$problems" = 0 ]; then
    [ "$QUIET" = 1 ] || printf '\nEverything bosun needs is here.\n'
    exit 0
  fi
  print_steps
  printf '\nThen run this again: %s\n' "${BOSUN_RERUN:-bosun init}"
  printf 'Stuck? %s\n' "$STUCK_URL"
  exit 1
}

main() {
  load_profiles
  [ "$QUIET" = 1 ] || printf 'What bosun needs, on %s:\n\n' "$(platform_name)"
  check_foundation
  check_tools
  check_python
  # All the packages as one line to paste, before the steps that need them.
  if [ -n "$packages" ]; then
    # shellcheck disable=SC2086 # the package names are meant to split
    steps="$steps$(platform_install_command $packages)$NEWLINE"
  fi
  check_logins
  check_harness
  finish
}

main
