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
# Couldn't check out revision", and a rerun passed. So the one step that reaches that cache, `swift package
# resolve`, runs holding a lock beside the cache (lib/run_locked.py), and the check then finds its dependencies in
# the checkout's own .build: nothing else touches the cache. A warm checkout resolves in about a second. The cache
# stays shared: a per-checkout --cache-path would download the 76 MB SwiftLint binary again for every worktree.
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
# resolve that fails is tried once more, saying so: what failed in the two runs seen was gone on the second try.
resolve() {
  cache="${HOME:?}/Library/Caches/org.swift.swiftpm"
  mkdir -p "$cache"
  locked_resolve() {
    python3 "$here/../lib/run_locked.py" "$cache/bosun-resolve.lock" 900 \
      "another swift package resolve held $cache/bosun-resolve.lock for 15 minutes" -- swift package resolve
  }
  locked_resolve || {
    echo "swift_hook.sh: swift package resolve failed; trying once more" >&2
    locked_resolve
  }
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
