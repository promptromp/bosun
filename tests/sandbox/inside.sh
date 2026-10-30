#!/bin/bash
# Runs in the container (tests/sandbox.sh): copy the checkout, which the host mounts read-only and so
# stays untouched, then run what was asked.
set -uo pipefail
[ -e /etc/bosun-sandbox ] || {
  echo "$0 only runs in tests/sandbox.sh's container: it would change this machine" >&2
  exit 1
}

mkdir -p "$HOME/bosun"
tar -C /repo --exclude=./.venv --exclude=./.git --exclude=__pycache__ --exclude=.ruff_cache \
  --exclude=.mypy_cache --exclude=.pytest_cache -cf - . | tar -xf - -C "$HOME/bosun"
git -C "$HOME/bosun" init -q
git -C "$HOME/bosun" add -A
git -C "$HOME/bosun" -c user.name=sandbox -c user.email=sandbox@example.com commit -q -m "the checkout"
cd "$HOME/bosun" || exit 1

run_unit() { uv sync -q && uv run pytest -q tests/unit; }
run_smoke() { uv sync -q && tests/smoke.sh; }

case "${1:-init}" in
  init) tests/sandbox/fresh_user.sh ;;
  unit) run_unit ;;
  smoke) run_smoke ;;
  all)
    status=0
    run_unit || status=1
    run_smoke || status=1
    tests/sandbox/fresh_user.sh || status=1
    exit "$status"
    ;;
  shell)
    tests/sandbox/world.sh
    cat <<'WELCOME'

A new user's machine: nothing of bosun installed, three fictional repos and a month of Claude Code
history. Try `cd ~/bosun && bin/bosun init`, then `bin/bosun init --recommended`. launchctl, pmset,
claude and gh are stand-ins, so nothing here reaches anything real, and it all goes away on exit.

WELCOME
    exec bash -l
    ;;
  *)
    echo "sandbox: unknown '$1'" >&2
    exit 2
    ;;
esac
