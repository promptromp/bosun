# shellcheck shell=sh
# The Claude Code profile for lib/preflight.sh. A harness is one file like this, named for its
# BOSUN_HARNESS, and only says how to check this agent. The interface is in docs/design/init.md.
# Claude Code's own files are read through lib/claude_state.py, like everywhere else in bosun.

harness_name() { echo "Claude Code"; }
harness_command() { echo "${BOSUN_CLAUDE_BIN:-claude}"; }
harness_install_help() { echo "Install Claude Code: https://claude.com/claude-code"; }
harness_upgrade_help() { echo "Update Claude Code: claude update"; }

# The first version where auto mode, which repo sessions run in, is the built-in starting mode.
harness_min_version() { echo "2.1.283"; }
harness_version() { "$(harness_command)" --version </dev/null 2>/dev/null | awk 'NR == 1 && $1 ~ /^[0-9]/ { print $1 }'; }

# What else it needs, past being installed. Python is known to be there when this runs.
harness_checks() {
  if python3 "$ROOT/lib/claude_state.py" logged-in; then
    check_ok "logged in"
  elif platform_keychain_locked; then
    check_todo "not logged in: the login keychain is locked" \
      "Unlock the keychain, where Claude Code keeps its login (an ssh session starts with it locked): security unlock-keychain ~/Library/Keychains/login.keychain-db"
  else
    check_todo "not logged in" "Log in to Claude Code: run claude, and follow what it asks"
  fi
  # An unattended window would wait at these screens for ever, so a person goes through them once.
  if python3 "$ROOT/lib/claude_state.py" onboarded; then
    check_ok "first-run screens done"
  else
    check_todo "first-run screens not done" \
      "Open Claude Code once, to choose a theme and read its security notes: run claude, then /exit"
  fi
  # Windows started with --chrome (bosun's default, BOSUN_CLAUDE_FLAGS and BOSUN_ORCHESTRATOR_FLAGS; unset means
  # the default) stopped at Claude in Chrome's screen once, on the Claude Code of 2026-11-14. The one of
  # 2026-11-15 (2.1.308) shows none, with Chrome or without, and records nothing, so its absence cannot be a step:
  # it is said, and a window that does stop at it is the watchdog's stalled-start, which names it.
  case " ${BOSUN_CLAUDE_FLAGS---chrome} ${BOSUN_ORCHESTRATOR_FLAGS---chrome} " in
    *" --chrome "*)
      if python3 "$ROOT/lib/claude_state.py" chrome-onboarded; then
        check_ok "Claude in Chrome's screen done"
      else
        check_later "Claude in Chrome's screen not seen: if a window stops at it, answer it there once (bosun attach <window>)"
      fi
      ;;
  esac
}
