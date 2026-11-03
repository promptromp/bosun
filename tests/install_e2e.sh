#!/bin/bash
# A new machine's install, for real: the piped installer with its real Homebrew, real launchd jobs that
# really fire, the real pmset and plutil, and a clean uninstall. Claude Code is a stand-in, since these
# machines have no login. It changes the machine it runs on, so it only runs on a GitHub macOS runner
# (.github/workflows/install.yml) or in the throwaway Mac of tests/sandbox.sh mac.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
# The guard (it refuses to run anywhere else), the checks and the steps tests/work_e2e.sh shares.
source "$(dirname "$0")/sandbox/e2e_lib.sh" || exit 1

run_again() {
  echo "a re-run"
  local before after
  before="$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  # shellcheck disable=SC2002
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/again.txt" 2>&1
  check "it updates and passes again" test "$?" = 0
  after="$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  check "the same answers change nothing" test "$before" = "$after"
  check "... and back nothing up" bash -c '! ls "$1"/*.pre-init-* >/dev/null 2>&1' _ "$CONFIG"
}

# Through a real terminal, with real launchd and pmset.
uninstall() {
  local bosun="$HOME/.local/bin/bosun" state="$HOME/.local/state/bosun" out="$OUT/uninstall.txt"
  local drive=(python3 "$ROOT/tests/sandbox/drive.py" "$out") settings ledger
  echo "uninstall, through a real terminal"
  "$bosun" start >/dev/null 2>&1
  "$bosun" ledger spawn --title "Keep me" --ask "Stay through an uninstall" --via "user: test" >/dev/null
  "$bosun" kg import "$ROOT/tests/fixtures/knowledge/acme-import.jsonl" --apply --auto-confirm >/dev/null 2>&1
  settings="$(settings_sum)"
  ledger="$(cksum <"$state/ledger.json")"

  check "Enter at are-you-sure is a no" "${drive[@]}" "Are you sure" "<enter>" -- "$bosun" uninstall
  cat "$out"
  check "... which says so" grep -q "Nothing was changed" "$out"
  check "... and it is so" launchctl print "$DOMAIN/$LABEL"
  check "... with the wake still set" bash -c 'pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'
  check "... and the windows up" tmux -L bosun has-session -t "=bosun"

  uninstall_keeping_records "$settings" "$ledger"
  install_again "$settings"
  check "... your ledger" bash -c '"$1" ledger list | grep -q "Keep me"' _ "$bosun"
  check "... the jobs" launchctl print "$DOMAIN/$LABEL"
  check "... the wake" bash -c 'pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'

  echo "a wake that is not bosun's"
  sudo pmset repeat wakeorpoweron MTWRFSU 03:17:00
  check "uninstall leaves it" bash -c '"$1" uninstall --yes --purge >"$2" 2>&1; pmset -g sched | grep -i -A3 "repeating" | grep -q "3:17AM"' _ "$bosun" "$out"
  cat "$out"
  check "... and says so" grep -q "may not be bosun" "$out"
  sudo pmset repeat cancel
  jobs_are_gone
  check "the state folder is gone" test ! -e "$state"
  check "the config folder is gone" test ! -e "$CONFIG"
  check "nothing named bosun is left in this home folder" bash -c '[ -z "$1" ] || { echo "$1"; exit 1; }' _ "$(named_bosun)"
  check "the agent's own config is still there" jq -e '.hasCompletedOnboarding == true' "$HOME/.claude.json"
}

a_new_machine
follow_what_it_says
install
launchd_for_real
run_again
uninstall
rm -rf "$OUT"
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
