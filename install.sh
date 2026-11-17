#!/bin/bash
# Usage: ./install.sh [--example <name>] [--with-wake | --cancel-wake]
#
# Idempotent. Safe to re-run after editing bosun.env.
#   1. seed ~/.config/bosun from examples/<name> (default: generic); never overwrites
#   2. install and load bosun's launchd jobs, each only where its plist changed or it is not loaded: the morning
#      job that runs `bosun morning` on schedule; the one that runs `bosun morning --now`, started only by a
#      kickstart (which the menu bar app's Start makes through `--background`); `bosun watchdog` every 5 minutes;
#      `bosun morning --at-login` at login, with BOSUN_START_AT_LOGIN=1; `bosun learn` daily, with
#      BOSUN_LEARN_ENABLED=1; and the console, with BOSUN_CONSOLE_ALWAYS=1. One whose setting is off is removed
#   3. print (or with --with-wake, run via sudo) the pmset repeating-wake command, unless the wake is bosun's
#      already; with BOSUN_WAKE=0 (the wake is not bosun's to set) neither, unless --with-wake. --cancel-wake
#      cancels bosun's own (sudo pmset repeat cancel: bosun init passes it after a no, when bosun's is the only
#      repeating event); a sudo that is refused is a warning, never a failed install
#   4. link `bosun` into ~/.local/bin
#   5. run `bosun doctor`
# A job it loads fresh, or whose time it moves, leaves the time it was loaded ($BOSUN_STATE_DIR/loaded-<morning|learn|
# watchdog>, reload_job in lib/common.sh): the watchdog owes no run that was due before then, which launchd would
# not run. --cancel-wake looks again first: it refuses, saying why, unless pmset has exactly one repeating event,
# bosun's own left from before (BOSUN_WAKE=0).
# Exit status: 0 done; 1 a step failed (its ERROR line says which), and the steps after it were not made; 2 bad
# usage; 3 every step was made, and `bosun doctor` found problems (its FAIL lines).
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
BOSUN_COMMAND=install
DOCTOR_FOUND_PROBLEMS=3 # lib/init.py goes on to the app and the start on it, and lists doctor's problems last

EXAMPLE=generic
WITH_WAKE=0
CANCEL_WAKE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --example)
      EXAMPLE="${2:?example name}"
      shift
      ;;
    --with-wake) WITH_WAKE=1 ;;
    --cancel-wake) CANCEL_WAKE=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die_usage "unknown option $1" ;;
  esac
  shift
done

# The settings, read (creating nothing) for what to check: Claude Code is BOSUN_CLAUDE_BIN, as preflight and
# doctor check it. A bosun.env that cannot be read is load_config's to refuse, below.
read_config
missing=()
for tool in tmux ruby python3 jq "$BOSUN_CLAUDE_BIN"; do
  command -v "$tool" >/dev/null || missing+=("$tool")
done
if [ ${#missing[@]} -gt 0 ]; then
  die "missing: ${missing[*]}. Install with: brew install tmux jq; Claude Code from https://claude.com/claude-code"
fi

config_dir="${BOSUN_CONFIG_DIR:-$HOME/.config/bosun}"
example_dir="$BOSUN_ROOT/examples/$EXAMPLE"
[ -d "$example_dir" ] || die "no example '$EXAMPLE' in $BOSUN_ROOT/examples"
mkdir -p "$config_dir"
for file in bosun.env sessions.yml setup.sh; do
  if [ -f "$config_dir/$file" ]; then
    log "keeping existing $config_dir/$file"
  else
    cp "$example_dir/$file" "$config_dir/$file"
    log "created $config_dir/$file from examples/$EXAMPLE (edit it)"
  fi
done

load_config

# Earlier versions installed a tmuxinator project; `bosun start` no longer uses it.
old_tmuxinator_file="$HOME/.config/tmuxinator/$BOSUN_SESSION_NAME.yml"
if grep -q "bosun tmuxinator project" "$old_tmuxinator_file" 2>/dev/null; then
  rm -f "$old_tmuxinator_file"
  log "removed the old tmuxinator project $old_tmuxinator_file"
fi

# The morning job and the watchdog, which runs on its own, outside the tmux session, so it notices when
# that is gone too, and the job that runs `morning --now` for `morning --now --background`; the login job,
# learning and the console only when their setting is on (an earlier one is removed otherwise). Each is written
# and reloaded only where its plist changed or it is not loaded.
reloaded="$(reload_changed_jobs)" || die "could not load the launchd jobs; see above"
for label in $reloaded; do
  if [ -e "$HOME/Library/LaunchAgents/$label.plist" ]; then
    log "loaded launchd job $label"
  else
    setting="$(job_setting "$label")"
    why="its setting is off"
    [ -z "$setting" ] || why="$setting is not 1"
    log "removed launchd job $label (no longer wanted: $why)"
  fi
done
[ -n "$reloaded" ] || log "the launchd jobs are loaded and up to date"
log "the morning run at $BOSUN_START_TIME on $BOSUN_DAYS; bosun watchdog every $((WATCHDOG_INTERVAL_SECONDS / 60)) minutes"

# Wake just after the job time (set_wake, lib/common.sh), when the wake is bosun's to set.
if [ "$WITH_WAKE" = "1" ]; then
  set_wake
elif [ "$CANCEL_WAKE" = "1" ]; then
  cancel_wake || log "WARN: bosun's repeating wake was not cancelled (bosun doctor says so); run: sudo pmset repeat cancel"
elif [ "$BOSUN_WAKE" != "1" ]; then
  log "the Mac's repeating wake is not bosun's to set (BOSUN_WAKE=0): pmset is left as it is"
elif [ "$(wake_owner "")" = ours ]; then # "": pmset is asked
  log "the Mac's repeating wake is bosun's already, for $BOSUN_DAYS at $BOSUN_START_TIME"
else
  set_wake print
fi

# Last: get.sh takes this link for an install that is set up, and runs `bosun update` on it; made any
# earlier, an install cut short after it (at the jobs, or at sudo for the wake) would never be finished.
mkdir -p "$HOME/.local/bin"
ln -sfn "$BOSUN_CMD" "$HOME/.local/bin/bosun"
log "linked ~/.local/bin/bosun"

echo
"$BOSUN" doctor || exit "$DOCTOR_FOUND_PROBLEMS"
