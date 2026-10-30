#!/bin/sh
# Install bosun:
#
#   curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh
#
# It puts bosun in ~/.local/share/bosun and starts `bosun init`, which checks what bosun needs and says
# what to do about anything missing, then asks a few questions and shows its plan before it changes
# anything. It installs nothing else, and never uses sudo; `bosun init` asks for it once, for the wake
# schedule, and says so first. Run it again to update.
#
#   curl ... | sh -s -- --dry-run      say what it would do, and do nothing
#   curl ... | sh -s -- --unattended   ask nothing: take the recommended setup
#
# BOSUN_DIR (where it goes), BOSUN_REPO and BOSUN_BRANCH (what it gets) override the defaults.
#
# Plain POSIX sh, because this runs before anything else is known to be there. Everything is in
# functions and main runs last, so a download cut short does nothing. What bosun needs, per OS and per
# agent, is in lib/preflight.sh and its profiles, not here: this file only needs git to get them.

set -eu

REPO="${BOSUN_REPO:-https://github.com/promptromp/bosun.git}"
DIR="${BOSUN_DIR:-$HOME/.local/share/bosun}"
BRANCH="${BOSUN_BRANCH:-main}"
THIS_LINE="curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh"
ROADMAP="https://github.com/promptromp/bosun/blob/main/ROADMAP.md"
USER_PATH="$PATH"
PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH" # where Homebrew and Claude Code go
export PATH
DRY_RUN=0
UNATTENDED=0

say() { printf '%s\n' "$*"; }
die() {
  printf 'bosun: %s\n' "$*" >&2
  exit 1
}
run() {
  printf '  $ %s\n' "$*"
  [ "$DRY_RUN" = 1 ] || "$@"
}
has_terminal() { [ "$UNATTENDED" = 0 ] && (: </dev/tty) 2>/dev/null; }

parse_args() {
  for arg in "$@"; do
    case "$arg" in
      --dry-run) DRY_RUN=1 ;;
      --unattended) UNATTENDED=1 ;;
      -h | --help)
        say "Usage: get.sh [--dry-run] [--unattended]. See https://github.com/promptromp/bosun/blob/main/docs/setup.md"
        exit 0
        ;;
      *) die "unknown option $arg (try --help)" ;;
    esac
  done
}

# The first layer: what this OS needs to get as far as the profiles in the repo.
check_os() {
  case "$(uname -s)" in
    Darwin)
      xcode-select -p >/dev/null 2>&1 ||
        die "bosun needs Homebrew, which brings the git this needs: https://brew.sh. Then run this line again."
      ;;
    *) die "bosun runs on macOS today, and Linux is next: $ROADMAP" ;;
  esac
}

get_bosun() {
  if [ -d "$DIR/.git" ]; then
    say "Updating $DIR"
    run git -C "$DIR" pull --ff-only --quiet origin "$BRANCH" || say "Could not update it, so using it as it is."
  elif [ -e "$DIR" ]; then
    die "$DIR exists and is not a git checkout. Move it, or set BOSUN_DIR."
  else
    say "Getting bosun into $DIR"
    run mkdir -p "$(dirname "$DIR")"
    run git clone --quiet --branch "$BRANCH" "$REPO" "$DIR"
  fi
}

start_init() {
  command="$DIR/bin/bosun"
  if [ "$DRY_RUN" = 1 ]; then
    say "Then it would start: $command init"
    return 0
  fi
  BOSUN_USER_PATH="$USER_PATH"
  BOSUN_RERUN="$THIS_LINE"
  export BOSUN_USER_PATH BOSUN_RERUN
  [ "$UNATTENDED" = 0 ] || exec "$command" init --recommended
  has_terminal && exec "$command" init </dev/tty
  exec "$command" init # no terminal to ask on: it prints the plan
}

main() {
  parse_args "$@"
  say "bosun: agents write the code; bosun runs the rest of the day, and remembers it."
  say "First it checks what it needs and tells you what to do about anything missing. Then a few"
  say "questions, and a plan to accept. Nothing changes until you do."
  [ "$DRY_RUN" = 0 ] || say "(a dry run: nothing changes)"
  check_os
  get_bosun
  start_init
}

main "$@"
