#!/bin/sh
# One of the menu bar app's Swift checks, run from app/ (the pre-commit hooks of .pre-commit-config.yaml,
# on files under app/ only). Without a Swift toolchain it says so in one line and passes: CI's Linux `lint`
# job has none, and its macOS `app` job runs these for real.
#
#   tools/swift_hook.sh format   swift format lint --strict (the toolchain's swift-format, app/.swift-format)
#   tools/swift_hook.sh lint     SwiftLint, through the SwiftLintPlugins command plugin Package.resolved pins
#   tools/swift_hook.sh test     swift test with warnings as errors (incremental: seconds)
#
# The coverage floor is `uv run python tools/swift_coverage.py` after `swift test --enable-code-coverage`.
set -eu

check=${1:-}
if ! command -v swift >/dev/null 2>&1; then
  echo "swift is not installed: skipping the app's $check check (CI's app job runs it)"
  exit 0
fi
cd "$(dirname "$0")/../app"
case $check in
  format) exec swift format lint --strict -r Sources Tests Package.swift ;;
  lint) exec swift package plugin --allow-writing-to-package-directory swiftlint lint --strict ;;
  test) exec swift test -Xswiftc -warnings-as-errors ;;
  *)
    echo "usage: swift_hook.sh format|lint|test" >&2
    exit 2
    ;;
esac
