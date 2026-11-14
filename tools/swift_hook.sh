#!/bin/sh
# One of the menu bar app's Swift checks, run from app/ (the pre-commit hooks of .pre-commit-config.yaml,
# on files under app/ only). Only on a Mac: the app is AppKit, which no other system can build, and GitHub's
# Linux images do ship a `swift`, so CI's Linux `lint` job must not try. Off a Mac, or on a Mac without a
# Swift toolchain, it says so in one line and passes; CI's macOS `app` job runs these for real.
#
#   tools/swift_hook.sh format   swift format lint --strict (the toolchain's swift-format, app/.swift-format)
#   tools/swift_hook.sh lint     SwiftLint, through the SwiftLintPlugins command plugin Package.resolved pins
#   tools/swift_hook.sh test     swift test with warnings as errors (incremental: seconds)
#
# The coverage floor is `uv run python tools/swift_coverage.py` after `swift test --enable-code-coverage`.
#
# Several checkouts of this repository (git worktrees: parallel agents) run these at the same time, and SwiftPM
# keeps the clones and downloads of its dependencies in one cache for the whole Mac
# (~/Library/Caches/org.swift.swiftpm). Twice, in two worktrees, a resolve failed there with "'swiftlintplugins':
# Couldn't check out revision", and a rerun passed. So `swift package resolve`, the step the failure came from,
# runs holding a lock beside the cache (lib/run_locked.py), and the check then finds its dependencies in the
# checkout's own .build. The lock covers that resolve and nothing else: a build that has to resolve again later
# does not take it. A warm checkout resolves in about a second. The cache stays shared: a per-checkout
# --cache-path would download the 76 MB SwiftLint binary again for every worktree.
set -eu

check=${1:-}
if [ "$(uname -s)" != Darwin ]; then
  echo "not a Mac: skipping the app's $check check (the app is AppKit; CI's macOS app job runs it)"
  exit 0
fi
if ! command -v swift >/dev/null 2>&1; then
  echo "swift is not installed: skipping the app's $check check (CI's app job runs it)"
  exit 0
fi
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../app"

# resolve: SwiftPM's dependencies in this checkout's .build, one resolve at a time on the Mac (see above). A
# resolve is tried once more, saying so, only when its error is a failed checkout (the failure seen; SwiftPM's
# apostrophe there is typographic, so the match is on the words after it). A wait for the lock that ran out
# (run_locked.py's BUSY, 75) or any other error fails at once, with the first error.
# The wait is SWIFT_HOOK_LOCK_WAIT seconds, 900 unless set (to try the fail-at-once path).
BUSY=75
resolve() {
  cache="${HOME:?}/Library/Caches/org.swift.swiftpm"
  mkdir -p "$cache"
  locked_resolve() {
    python3 "$here/../lib/run_locked.py" "$cache/bosun-resolve.lock" "${SWIFT_HOOK_LOCK_WAIT:-900}" \
      "another swift package resolve held $cache/bosun-resolve.lock for 15 minutes" -- swift package resolve
  }
  errors="$(mktemp)"
  status=0
  locked_resolve 2>"$errors" || status=$?
  cat "$errors" >&2
  if [ "$status" -ne 0 ] && [ "$status" -ne "$BUSY" ] && grep -q "check out revision" "$errors"; then
    echo "swift_hook.sh: swift package resolve failed to check a dependency out; trying once more" >&2
    status=0
    locked_resolve || status=$?
  fi
  rm -f "$errors"
  return "$status"
}

case $check in
  format) exec swift format lint --strict -r Sources Tests Package.swift ;;
  lint)
    resolve
    exec swift package plugin --allow-writing-to-package-directory swiftlint lint --strict
    ;;
  test)
    resolve
    exec swift test -Xswiftc -warnings-as-errors
    ;;
  *)
    echo "usage: swift_hook.sh format|lint|test" >&2
    exit 2
    ;;
esac
