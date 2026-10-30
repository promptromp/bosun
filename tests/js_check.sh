#!/bin/bash
# The console page's JavaScript (console/app.js), checked with node when this machine has it:
# `node --check` for syntax, then tests/js/app_check.js, which runs the script in a fake DOM and
# renders views from real API models (tests/js/api_models.py). No npm packages. Without node it
# says so and passes, so the hook and the smoke suite work everywhere.
# Usage: tests/js_check.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v node >/dev/null 2>&1; then
  echo "js check: skipped (no node on PATH)"
  exit 0
fi
node --check "$ROOT/console/app.js" || exit 1
models="$(mktemp)"
trap 'rm -f "$models"' EXIT
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tests/js/api_models.py" >"$models" || exit 1
node "$ROOT/tests/js/app_check.js" "$ROOT/console/app.js" "$models"
