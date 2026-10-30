# shellcheck shell=bash
# Helpers available inside the user's setup.sh (sourced by `bosun setup`): run_step,
# ensure_aws_sso, ensure_claude_login.
# Each helper logs what it does, honours BOSUN_SETUP_DRY_RUN=1, and returns non-zero on failure
# without aborting the remaining steps.

SETUP_FAILURES=0

_step_failed() {
  SETUP_FAILURES=$((SETUP_FAILURES + 1))
  log "FAIL: $*"
}

# run_step "<description>" <command> [args...]
# Run an arbitrary command with the login timeout applied.
run_step() {
  local description="$1"
  shift
  if [ "$BOSUN_SETUP_DRY_RUN" = "1" ]; then
    log "dry-run: would run [$description]: $*"
    return 0
  fi
  log "running [$description]: $*"
  if run_with_timeout "$BOSUN_LOGIN_TIMEOUT" "$@"; then
    log "ok: $description"
  else
    _step_failed "$description (exit $?)"
    return 1
  fi
}

aws_profile_valid() {
  "$BOSUN_AWS_BIN" sts get-caller-identity --profile "$1" >/dev/null 2>&1
}

# The [sso-session] a profile uses (empty for a profile without one).
aws_sso_session_of() {
  "$BOSUN_AWS_BIN" configure get sso_session --profile "$1" 2>/dev/null
}

# aws_sso_token_expired <sso-session>: true only if the session's access token has expired (or is
# missing), so its cached role credentials may outlive the session.
aws_sso_token_expired() {
  local token_file
  token_file="$HOME/.aws/sso/cache/$(printf '%s' "$1" | shasum -a 1 | cut -d' ' -f1).json"
  ! python3 - "$token_file" 2>/dev/null <<'PY'
import json
import sys
from datetime import datetime, timezone

expires = json.load(open(sys.argv[1]))["expiresAt"].replace("Z", "+00:00")
sys.exit(0 if datetime.fromisoformat(expires) > datetime.now(timezone.utc) else 1)
PY
}

# forget_aws_sso_role_credentials: drop the CLI's cached SSO role credentials. They are only a
# cache: the next call fetches new ones through the SSO session (refreshing its token), which works
# only while the session is alive.
forget_aws_sso_role_credentials() {
  local cached
  for cached in "$HOME"/.aws/cli/cache/*.json; do
    [ -f "$cached" ] && grep -q '"ProviderType": *"sso"' "$cached" && rm -f "$cached"
  done
  return 0
}

# What this setup run did with each SSO session: "<session>=<ok|failed|would-login>" words. One
# login covers every profile of a session, so the next profile of a session that already logged
# in only checks its credentials, and one whose login failed fails at once instead of opening the
# browser again and waiting another BOSUN_LOGIN_TIMEOUT.
AWS_SSO_SESSIONS=""

aws_sso_outcome() { # <session>
  local word
  for word in $AWS_SSO_SESSIONS; do
    [ "${word%=*}" = "$1" ] && echo "${word#*=}" && return 0
  done
  return 0
}

aws_sso_record() { # <session> <outcome>
  [ -n "$1" ] && AWS_SSO_SESSIONS="$AWS_SSO_SESSIONS $1=$2"
  return 0
}

# The authorization URL `aws sso login` prints carries one-time OAuth parameters; keep them out of
# the logs.
mask_sso_urls() {
  sed -E 's#(https://[^ ?]+)\?[^ ]*#\1?<masked>#g'
}

# aws_sso_login <what> <aws sso login arguments...>: run the login (it opens the browser) for at
# most BOSUN_LOGIN_TIMEOUT seconds. A login the browser approves on its own finishes in
# seconds; one still waiting after BOSUN_AWS_SSO_ALERT_AFTER seconds needs a click, so it
# alerts me then (`bosun alert`: a notification and, if configured, a Slack DM), once.
aws_sso_login() {
  local what="$1" pid waited=0
  shift
  run_with_timeout "$BOSUN_LOGIN_TIMEOUT" "$BOSUN_AWS_BIN" sso login "$@" 2>&1 | mask_sso_urls &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" = "$BOSUN_AWS_SSO_ALERT_AFTER" ]; then
      log "the login for $what still waits for a browser approval after ${waited}s; alerting you"
      "$BOSUN" alert "AWS SSO login for $what waits for your approval in the browser on this Mac (until $(date -v+"$((BOSUN_LOGIN_TIMEOUT - waited))"S +%H:%M)). After that, run: aws sso login $*" 2>&1 &
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid"
}

# ensure_aws_sso <profile>
# Skip if the profile's credentials still work (the CLI silently refreshes tokens for profiles
# that use an [sso-session] block). Otherwise log in, once per SSO session (`aws sso login
# --sso-session`, which covers all its profiles), or with --profile for a profile without one.
# Cached role credentials last 12 hours and can outlive the SSO session, so once the session's
# token has expired the check runs without them: it then passes only if the session can still
# issue credentials, instead of passing on a cache that runs out later in the morning.
ensure_aws_sso() {
  local profile="$1" session outcome target what
  session="$(aws_sso_session_of "$profile")"
  outcome="$(aws_sso_outcome "$session")"
  if [ "$outcome" = "failed" ]; then
    _step_failed "aws profile $profile: its SSO session $session still needs a login (see above); run: aws sso login --sso-session $session"
    return 1
  fi
  if [ "$BOSUN_SETUP_DRY_RUN" != "1" ] && [ -n "$session" ] && [ -z "$outcome" ] && aws_sso_token_expired "$session"; then
    forget_aws_sso_role_credentials
  fi
  if aws_profile_valid "$profile"; then
    log "ok: aws profile $profile is valid, no login needed"
    return 0
  fi
  target=(--profile "$profile")
  what="profile $profile"
  if [ -n "$session" ]; then
    target=(--sso-session "$session")
    what="SSO session $session"
  fi
  if [ "$BOSUN_SETUP_DRY_RUN" = "1" ]; then
    if [ "$outcome" = "would-login" ]; then
      log "dry-run: aws profile $profile is not valid; the login for $what above would cover it"
    else
      log "dry-run: aws profile $profile is not valid; would run: aws sso login ${target[*]}"
      aws_sso_record "$session" would-login
    fi
    return 0
  fi
  if [ "$outcome" = "ok" ]; then
    _step_failed "aws profile $profile still invalid, although $what logged in (check the profile's account and role)"
    return 1
  fi
  log "aws profile $profile is not valid; running aws sso login ${target[*]} (browser approval may be needed)"
  notify "bosun" "AWS SSO login needed for $what - approve in the browser"
  aws_sso_login "$what" "${target[@]}"
  if aws_profile_valid "$profile"; then
    log "ok: aws profile $profile logged in"
    aws_sso_record "$session" ok
  else
    aws_sso_record "$session" failed
    _step_failed "aws profile $profile still invalid after sso login: no browser approval within ${BOSUN_LOGIN_TIMEOUT}s. Run: aws sso login ${target[*]}"
    return 1
  fi
}

# ensure_claude_login
# Check that Claude Code is logged in and that the login still works: `claude auth status` (it only
# sees whether credentials are stored), then one tiny real request (a fraction of a cent; see
# lib/login.py), which also catches a token the server revoked. It cannot log in by itself (/login
# is interactive): it notifies you and fails, so the morning-failed alert names it. Without an
# answer either way (no network), it only warns. A dry run skips the request.
ensure_claude_login() {
  local verdict code probe=(--probe)
  if [ "$BOSUN_SETUP_DRY_RUN" = "1" ]; then
    probe=()
    log "dry-run: would make one login check request; checking only claude auth status and the last check"
  fi
  verdict="$(claude_login global ${probe[@]+"${probe[@]}"})"
  code=$?
  case "$code" in
    0) log "ok: Claude Code login works: $verdict" ;;
    1)
      notify "bosun" "Claude Code needs a login - run /login in a Claude window"
      _step_failed "Claude Code login: $verdict; run /login in any Claude window (or claude auth login)"
      return 1
      ;;
    *) log "warning: could not confirm the Claude Code login: $verdict" ;;
  esac
  return 0
}
