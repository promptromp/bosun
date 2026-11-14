# shellcheck shell=sh
# The Google Antigravity CLI (agy) profile for lib/preflight.sh. A harness is one file like this, named for its
# BOSUN_HARNESS, and only says how to check this agent. The interface is in docs/design/init.md.
# agy's own files are read through lib/agy_state.py. bosun installs nothing of agy's own.

harness_name() { echo "Antigravity"; }
harness_command() { echo "${BOSUN_AGY_BIN:-agy}"; }

# agy's installer puts the command in ~/.local/bin and does not add that folder to PATH.
harness_install_help() {
  echo "Install Antigravity: curl -fsSL https://antigravity.google/cli/install.sh | bash. It puts agy in ~/.local/bin and does not add that to your PATH; do that, then open a new terminal"
}
harness_upgrade_help() { echo "Update Antigravity: agy update"; }

# The version bosun has been run against: its auto mode, --settings and --conversation as bosun uses them,
# and what lib/agy_state.py reads of it, which is not a public API.
harness_min_version() { echo "1.5.5"; }
harness_version() { "$(harness_command)" --version </dev/null 2>/dev/null | awk 'NR == 1 && $1 ~ /^[0-9]/ { print $1 }'; }

# What else it needs, past being installed. Python is known to be there when this runs. The login is the
# Keychain's item, looked up without reading it, and agy's first-run screens, which an unattended window would
# wait at for ever, are done when its onboarding file says so.
harness_checks() {
  if python3 "$ROOT/lib/agy_state.py" logged-in; then
    check_ok "logged in, first-run screens done"
  elif [ -n "${SSH_CONNECTION:-}${SSH_CLIENT:-}" ]; then
    check_todo "login not seen over ssh" \
      "agy's login is only seen in a desktop session (its Keychain is locked over ssh): run this check in Terminal on the Mac, where agy signs in and shows its first-run screens: run agy, then /exit"
  else
    check_todo "not logged in, or first-run screens not done" \
      "Open Antigravity once, to sign in and go through its first-run screens: run agy, then /exit"
  fi
}
