# shellcheck shell=sh
# The OpenAI Codex profile for lib/preflight.sh. A harness is one file like this, named for its
# BOSUN_HARNESS, and only says how to check this agent. The interface is in docs/design/init.md.
# Codex's own files are read through lib/codex_state.py.

harness_name() { echo "Codex"; }
harness_command() { echo "${BOSUN_CODEX_BIN:-codex}"; }
harness_install_help() { echo "Install Codex: https://developers.openai.com/codex/cli"; }
harness_upgrade_help() { echo "Update Codex: codex update, or brew upgrade codex if Homebrew installed it"; }

# The oldest version bosun has been run against: --no-daemon and --approve-for-me are not in much older
# ones, and what lib/codex_state.py reads is not a public API.
harness_min_version() { echo "0.166.0"; }
harness_version() { "$(harness_command)" --version 2>/dev/null | awk 'NR == 1 { print $2 }'; }

harness_checks() {
  if python3 "$ROOT/lib/codex_state.py" logged-in; then
    check_ok "logged in"
  else
    check_todo "not logged in" "Log in to Codex: codex login"
  fi
}
