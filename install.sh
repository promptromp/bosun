#!/bin/bash
# Usage: ./install.sh [--example <name>] [--with-wake]
#
# Idempotent. Safe to re-run after editing bosun.env.
#   1. seed ~/.config/bosun from examples/<name> (default: generic); never overwrites
#   2. link `bosun` into ~/.local/bin
#   3. install and load the launchd jobs that run `bosun morning` on schedule and
#      `bosun watchdog` every 5 minutes (and `bosun learn` daily, with
#      BOSUN_LEARN_ENABLED=1), each only where its plist changed or it is not loaded
#   4. print (or with --with-wake, run via sudo) the pmset repeating-wake command
#   5. run `bosun doctor`
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
BOSUN_COMMAND=install

EXAMPLE=generic
WITH_WAKE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --example)
      EXAMPLE="${2:?example name}"
      shift
      ;;
    --with-wake) WITH_WAKE=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die_usage "unknown option $1" ;;
  esac
  shift
done

missing=()
for tool in tmux ruby python3 jq claude; do
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

mkdir -p "$HOME/.local/bin"
ln -sfn "$BOSUN_CMD" "$HOME/.local/bin/bosun"
log "linked ~/.local/bin/bosun"

# Earlier versions installed a tmuxinator project; `bosun start` no longer uses it.
old_tmuxinator_file="$HOME/.config/tmuxinator/$BOSUN_SESSION_NAME.yml"
if grep -q "bosun tmuxinator project" "$old_tmuxinator_file" 2>/dev/null; then
  rm -f "$old_tmuxinator_file"
  log "removed the old tmuxinator project $old_tmuxinator_file"
fi

# The morning job and the watchdog, which runs on its own, outside the tmux session, so it notices when
# that is gone too; learning only when enabled (an earlier one is removed otherwise). Each is written
# and reloaded only where its plist changed or it is not loaded.
reloaded="$(reload_changed_jobs)" || die "could not load the launchd jobs; see above"
for label in $reloaded; do
  if [ -e "$HOME/Library/LaunchAgents/$label.plist" ]; then
    log "loaded launchd job $label"
  else
    log "removed launchd job $label (no longer wanted: BOSUN_LEARN_ENABLED is not 1)"
  fi
done
[ -n "$reloaded" ] || log "the launchd jobs are loaded and up to date"
log "the morning run at $BOSUN_START_TIME on $BOSUN_DAYS; bosun watchdog every $((WATCHDOG_INTERVAL_SECONDS / 60)) minutes"

# Wake just after the job time: launchd runs a calendar job missed during sleep as soon as the Mac
# wakes, so the job starts within seconds of the wake and the Mac has no chance to doze off first.
wake_time="$(shift_time "$BOSUN_START_TIME" "$BOSUN_WAKE_DELAY_MINUTES"):00"
wake_cmd=(sudo pmset repeat wakeorpoweron "$BOSUN_DAYS" "$wake_time")
existing_repeat="$(pmset -g sched | sed -n '/Repeating power events/,/^[A-Z]/p' | grep -iE 'wake|power' || true)"
[ -n "$existing_repeat" ] && log "note: pmset already has a repeating event (pmset keeps only one):$existing_repeat"
if [ "$WITH_WAKE" = "1" ]; then
  log "setting repeating wake: ${wake_cmd[*]}"
  "${wake_cmd[@]}" || die "pmset failed"
else
  log "to wake the Mac on schedule, run once:  ${wake_cmd[*]}"
fi

echo
"$BOSUN" doctor
