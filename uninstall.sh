#!/bin/bash
# Usage: ./uninstall.sh [--with-wake] [--purge]
#
# Unload and remove the launchd jobs (morning, watchdog and learn), the ~/.local/bin/bosun link, and
# the tmuxinator project that earlier versions installed.
# Leaves a running tmux session alone (use `bosun stop`).
#   --with-wake  also cancel the repeating pmset wake (sudo; pmset keeps only one, so check
#                `pmset -g sched` first if something else uses it)
#   --purge      also delete ~/.config/bosun and ~/.local/state/bosun
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
BOSUN_COMMAND=uninstall
load_config

WITH_WAKE=0
PURGE=0
for arg in "$@"; do
  case "$arg" in
    --with-wake) WITH_WAKE=1 ;;
    --purge) PURGE=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die_usage "unknown option $arg" ;;
  esac
done

launchctl bootout "gui/$(id -u)/$BOSUN_LAUNCHD_LABEL" 2>/dev/null && log "unloaded $BOSUN_LAUNCHD_LABEL"
rm -f "$HOME/Library/LaunchAgents/$BOSUN_LAUNCHD_LABEL.plist"
launchctl bootout "gui/$(id -u)/$(watchdog_label)" 2>/dev/null && log "unloaded $(watchdog_label)"
rm -f "$(watchdog_plist)"
launchctl bootout "gui/$(id -u)/$(learn_label)" 2>/dev/null && log "unloaded $(learn_label)"
rm -f "$(learn_plist)"

[ "$(readlink "$HOME/.local/bin/bosun")" = "$BOSUN" ] && rm -f "$HOME/.local/bin/bosun" && log "removed ~/.local/bin/bosun"

tmuxinator_file="$HOME/.config/tmuxinator/$BOSUN_SESSION_NAME.yml"
if grep -q "bosun tmuxinator project" "$tmuxinator_file" 2>/dev/null; then
  rm -f "$tmuxinator_file"
  log "removed $tmuxinator_file"
fi

if [ "$WITH_WAKE" = "1" ]; then
  sudo pmset repeat cancel && log "cancelled repeating pmset wake"
else
  log "repeating wake left in place; cancel with: sudo pmset repeat cancel"
fi

if [ "$PURGE" = "1" ]; then
  rm -rf "$BOSUN_CONFIG_DIR" "$BOSUN_STATE_DIR"
  log "deleted $BOSUN_CONFIG_DIR and $BOSUN_STATE_DIR"
fi
