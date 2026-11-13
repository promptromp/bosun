#!/bin/bash
# Usage: tests/sandbox/update_quit_probe.sh <copy of bosun>
#
# Makes "the next version" of bosun ask bosun.app to quit as its update starts, with the call `bosun app update`
# and `bosun app remove` make (lib/app_install.py quit: osascript's `tell application id "<bundle id>" to quit`),
# for tests/app_e2e.sh: what macOS says when that comes from an update the app itself started (`bosun update
# --yes`, a child of the app), which an ad hoc app cannot show with the real `bosun app update` (it refuses a
# build that is not the publisher's before it quits anything). Only the copy changes; commit it to have the new
# version. What the call said goes to ~/.sandbox/quit-probe.log, only for `update --yes` and while ~/.sandbox/quit-probe exists.
set -euo pipefail

TREE="${1:-}"
[ -f "$TREE/libexec/bosun-update" ] || {
  echo "update_quit_probe.sh: ${TREE:-no folder} is not a copy of bosun" >&2
  exit 2
}

# shellcheck disable=SC2016 # the line is for the other script: its $ expand there
probe='case " $* " in *" --yes "*) [ ! -e "$HOME/.sandbox/quit-probe" ] || { echo "probe $(date "+%F %T") parent $PPID"; python3 "$BOSUN_ROOT/lib/app_install.py" quit; echo "probe exit $?"; } >>"$HOME/.sandbox/quit-probe.log" 2>&1 ;; esac'
tmp="$(mktemp)"
awk -v line="$probe" '{ print } /^load_config$/ && !done { print line; done = 1 }' "$TREE/libexec/bosun-update" >"$tmp"
grep -qF 'probe exit' "$tmp" || {
  echo "update_quit_probe.sh: libexec/bosun-update has no load_config line to follow" >&2
  rm -f "$tmp"
  exit 1
}
cat "$tmp" >"$TREE/libexec/bosun-update"
rm -f "$tmp"
