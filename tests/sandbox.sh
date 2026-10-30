#!/bin/bash
# Usage: tests/sandbox.sh [init | unit | smoke | all | shell]
#        tests/sandbox.sh mac (setup | run [--from-nothing] | shell | clean)
#
# Run bosun on a clean machine that cannot touch this one: a throwaway Linux container with a
# read-only copy of this checkout. Docker on a Mac is Linux, so launchctl, pmset and the like are
# stand-ins (tests/sandbox/macos/): this tests bosun's logic, not launchd or a real wake. For those,
# `mac` uses a real Mac in a Tart VM (tests/sandbox/mac.sh --help). Guide: docs/development.md.
#   init   (default) a new user's first day: `bosun init`, a morning, re-runs, uninstall. No network.
#   unit   the unit tests
#   smoke  the smoke suite
#   all    all three
#   shell  a new user's machine to try things by hand; it goes away on exit
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="bosun-sandbox"
what="${1:-init}"
case "$what" in
  -h | --help)
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
    exit 0
    ;;
  mac)
    shift
    exec "$ROOT/tests/sandbox/mac.sh" "$@"
    ;;
  init | unit | smoke | all | shell) ;;
  *)
    echo "sandbox: unknown '$what' (see --help)" >&2
    exit 2
    ;;
esac

docker info >/dev/null 2>&1 || {
  echo "sandbox: needs Docker, and it is not running" >&2
  exit 1
}
docker build -q -t "$IMAGE" "$ROOT/tests/sandbox" >/dev/null || exit 1

flags=(--rm --volume "$ROOT:/repo:ro")
[ "$what" = "init" ] && flags+=(--network none)
[ "$what" = "shell" ] && flags+=(--interactive --tty)
exec docker run "${flags[@]}" "$IMAGE" /repo/tests/sandbox/inside.sh "$what"
