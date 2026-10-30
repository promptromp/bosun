#!/bin/sh
# Install bosun:
#
#   curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh
#
# It checks what bosun needs, offers to install what is missing with Homebrew (and Homebrew itself),
# puts bosun in ~/.local/share/bosun, and starts `bosun init`, which asks a few questions and shows its
# plan before it changes anything. Run it again to update. This script only uses sudo through
# Homebrew's own installer, if it needs that; `bosun init` asks for it once, for the wake schedule,
# and says so first.
#
#   curl ... | sh -s -- --dry-run      say what it would do, and do nothing
#   curl ... | sh -s -- --unattended   ask nothing: install what is missing, take the recommended setup
#
# BOSUN_DIR (where it goes), BOSUN_REPO and BOSUN_BRANCH (what it gets) override the defaults.
#
# Plain POSIX sh, because this runs before anything else is installed. Everything is in functions and
# main runs last, so a download cut short does nothing.

set -eu

REPO="${BOSUN_REPO:-https://github.com/promptromp/bosun.git}"
DIR="${BOSUN_DIR:-$HOME/.local/share/bosun}"
BRANCH="${BOSUN_BRANCH:-main}"
HOMEBREW_INSTALLER="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
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
have() { command -v "$1" >/dev/null 2>&1; }
has_terminal() { [ "$UNATTENDED" = 0 ] && (: </dev/tty) 2>/dev/null; }

ask() { # <question>: yes on Return and with --unattended, no without a terminal to ask on
  [ "$UNATTENDED" = 1 ] && return 0
  has_terminal || return 1
  printf '%s [Y/n] ' "$1" >/dev/tty
  read -r reply </dev/tty || return 1
  case "$reply" in
    "" | y | Y | yes) return 0 ;;
    *) return 1 ;;
  esac
}

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

check_machine() {
  [ "$(uname -s)" = Darwin ] || die "bosun runs on macOS: it schedules with launchd and pmset."
}

# Without Apple's command line tools, the first `git` or `python3` opens a dialog instead of answering.
has_apple_tools() { xcode-select -p >/dev/null 2>&1; }

install_homebrew() {
  say "Homebrew is how bosun gets what it needs, and it is not here. It brings Apple's command line tools too."
  if [ "$DRY_RUN" = 1 ]; then
    say "  It would offer to run Homebrew's installer, from $HOMEBREW_INSTALLER"
    return 0
  fi
  ask "Install Homebrew now? Its installer explains what it does, and asks for your password." ||
    die "Install it from https://brew.sh, then run this line again."
  installer="$(curl -fsSL "$HOMEBREW_INSTALLER")" || die "Could not download Homebrew's installer."
  if [ "$UNATTENDED" = 1 ]; then
    NONINTERACTIVE=1 /bin/bash -c "$installer" </dev/null
  else
    /bin/bash -c "$installer" </dev/tty
  fi
  have brew || die "Homebrew did not end up on the PATH. See https://brew.sh, then run this line again."
}

missing_packages() {
  missing=""
  have tmux || missing="$missing tmux"
  have jq || missing="$missing jq"
  have gh || missing="$missing gh"
  have ruby || missing="$missing ruby"
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))' 2>/dev/null || missing="$missing python"
  printf '%s' "${missing# }"
}

install_missing() {
  packages="$(missing_packages)"
  cask=""
  have claude || cask="claude-code"
  if [ -z "$packages$cask" ]; then
    say "Everything bosun needs is here."
    return 0
  fi
  wanted="$packages${cask:+ $cask}"
  say "Missing: ${wanted# }"
  have brew || install_homebrew
  if [ "$DRY_RUN" = 0 ] && ! ask "Install them with Homebrew?"; then
    die "Install them yourself, then run this line again."
  fi
  # shellcheck disable=SC2086 # the package names are meant to split
  [ -z "$packages" ] || run brew install $packages
  [ -z "$cask" ] || run brew install --cask "$cask"
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
  export BOSUN_USER_PATH
  [ "$UNATTENDED" = 0 ] || exec "$command" init --recommended
  has_terminal && exec "$command" init </dev/tty
  say "No terminal to ask questions on. To finish, run: $command init"
}

main() {
  parse_args "$@"
  say "bosun: agents write the code; bosun runs the rest of the day, and remembers it."
  [ "$DRY_RUN" = 0 ] || say "(a dry run: nothing changes)"
  check_machine
  if ! has_apple_tools; then
    have brew && die "bosun needs Apple's command line tools. Run: xcode-select --install, then this line again."
    install_homebrew
  fi
  if has_apple_tools; then
    install_missing
  else # only in a dry run, which must not set off the dialog
    say "Then it would install what is missing."
  fi
  get_bosun
  start_init
}

main "$@"
