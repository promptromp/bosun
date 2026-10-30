# shellcheck shell=bash
# Setup steps run by `bosun morning` after the network is up (and by `bosun setup`).
# Available helpers (see lib/setup-helpers.sh):
#   ensure_claude_login               Claude Code is logged in and its login works (one tiny request)
#   ensure_aws_sso <profile>          skip if credentials work, else `aws sso login`
#   run_step "<description>" cmd ...  run any command with the login timeout
# A failed step is logged and reported, and the remaining steps still run.

setup_steps() {
  ensure_claude_login
  ensure_aws_sso my-profile
  # run_step "gcloud auth" gcloud auth login --update-adc
  # Install the latest Claude Code before sessions start, so the morning refresh restarts them onto it:
  # run_step "update Claude Code" "$BOSUN_CLAUDE_BIN" update
}
