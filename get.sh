#!/bin/sh
# Install bosun, or update it:
#
#   curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh
#
# A first install puts bosun in ~/.local/share/bosun-versions, a folder for each version beside the one
# repository they come from (bosun.git), makes ~/.local/share/bosun a link to the version in use, and
# starts `bosun init`, which checks what bosun needs and says what to do about anything missing, then
# asks a few questions and shows its plan before it changes anything. It installs nothing else, and never
# uses sudo; `bosun init` asks for it once, for the wake schedule, and says so first.
#
# Run it again to update: it gets the head of the branch into a folder of its own, beside the version in
# use, and hands over to that version's `bosun update`, which shows its plan and asks first. An install
# an earlier get.sh made, a checkout in ~/.local/share/bosun, is converted by that first update, at a
# quiet moment: the checkout is kept as bosun-versions/legacy-<its commit>, and ~/.local/share/bosun
# becomes the link. An install `bosun init` has not set up yet (it stopped at what bosun needs) gets
# `bosun init` again instead. `bosun uninstall` takes it all off again.
#
#   curl ... | sh -s -- --dry-run      say what it would do, and do nothing
#   curl ... | sh -s -- --unattended   ask nothing: take the recommended setup, or go ahead with the update
#   curl ... | BOSUN_HARNESS=codex sh  run repo sessions on Codex, and check that it is ready
#
# BOSUN_DIR (where it goes), BOSUN_REPO and BOSUN_BRANCH (what it gets) override the defaults. On an
# existing install the defaults are what it came from: bosun.git's origin, and its bosun.branch.
#
# Plain POSIX sh, because this runs before anything else is known to be there. Everything is in
# functions and main runs last, so a download cut short does nothing. What bosun needs, per OS and per
# agent, is in lib/preflight.sh and its profiles, not here: this file only needs git to get them.

set -eu

REPO="${BOSUN_REPO:-https://github.com/promptromp/bosun.git}"
DIR="${BOSUN_DIR:-$HOME/.local/share/bosun}"
DIR="${DIR%/}"
VERSIONS="$DIR-versions" # as lib/install_paths.py's versions_dir
BARE="$VERSIONS/bosun.git"
BRANCH="${BOSUN_BRANCH:-}" # pick_branch settles it
THIS_LINE="curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | ${BOSUN_HARNESS:+BOSUN_HARNESS=$BOSUN_HARNESS }sh"
ROADMAP="https://github.com/promptromp/bosun/blob/main/ROADMAP.md"
USER_PATH="$PATH"
PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH" # where Homebrew and Claude Code go
export PATH
DRY_RUN=0
UNATTENDED=0
# What is at $DIR: new (nothing), checkout (one from before versions), link (to a version), or
# unfinished (either of them, which bosun init has not set up yet).
KIND=""
FROM="" # the version in use: the checkout, or the folder the link leads to
NEW=""  # the folder of the version this gets
CHECKOUT_SET_ASIDE="the checkout"

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

# No GIT_* variable from whatever runs this (a git hook, another repository's tooling) may send git to
# another repository.
clean_env() { unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR GIT_NAMESPACE; }

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

# The link at $DIR, if it leads to a version: a folder in $VERSIONS, beside bosun.git, with bosun in it.
look_at_link() {
  target="$(readlink "$DIR")"
  case "$target" in
    /*) ;;
    *) target="$(dirname "$DIR")/$target" ;;
  esac
  parent="$(cd "$(dirname "$target")" 2>/dev/null && pwd -P)" || parent=""
  versions="$(cd "$VERSIONS" 2>/dev/null && pwd -P)" || versions=""
  [ -f "$target/bin/bosun" ] && [ -d "$BARE" ] && [ -n "$parent" ] && [ "$parent" = "$versions" ] ||
    die "$DIR is a link to $target, which is not a version in $VERSIONS. Move it, or set BOSUN_DIR."
  KIND="link"
  FROM="$target"
}

# What is at $DIR: nothing, a checkout of bosun from before versions, or a link to a version. Anything
# else is refused, never guessed at, and so is a versions folder that is not bosun's.
look() {
  if [ -L "$DIR" ]; then
    look_at_link
  elif [ -d "$DIR/.git" ] && [ -f "$DIR/bin/bosun" ]; then
    KIND="checkout"
    FROM="$DIR"
  elif [ -e "$DIR" ]; then
    die "$DIR is not bosun's: neither a link to one of its versions nor a checkout of bosun. Move it, or set BOSUN_DIR."
  else
    KIND="new"
  fi
  [ ! -e "$VERSIONS" ] || [ -d "$BARE" ] || [ -z "$(ls -A "$VERSIONS")" ] ||
    die "$VERSIONS is there, without bosun.git, so it is not bosun's. Move it, or set BOSUN_DIR."
  case "$FROM" in
    */legacy-*) CHECKOUT_SET_ASIDE="$FROM, the checkout from before versions," ;; # a conversion put back
    *) [ "$KIND" = checkout ] || CHECKOUT_SET_ASIDE="" ;;
  esac
}

# The branch: BOSUN_BRANCH, else the one the install follows (bosun.git's bosun.branch), else main.
pick_branch() {
  [ -n "$BRANCH" ] || [ ! -d "$BARE" ] || BRANCH="$(git --git-dir="$BARE" config bosun.branch 2>/dev/null)" || BRANCH=""
  [ -n "$BRANCH" ] || BRANCH=main
}

# bosun.git, a bare clone of the one branch, marked as the installer's.
make_repo() {
  run mkdir -p "$VERSIONS"
  run git -c core.hooksPath=/dev/null clone --quiet --bare --single-branch --branch "$BRANCH" "$REPO" "$BARE"
  run git --git-dir="$BARE" config bosun.managed true     # `bosun uninstall` only deletes versions marked so
  run git --git-dir="$BARE" config bosun.branch "$BRANCH" # what `bosun update` follows
}

# bosun.git as it is, but for what BOSUN_REPO or BOSUN_BRANCH asks for instead.
use_repo() {
  [ -z "${BOSUN_REPO:-}" ] || run git --git-dir="$BARE" remote set-url origin "$REPO"
  [ -z "${BOSUN_BRANCH:-}" ] || run git --git-dir="$BARE" config bosun.branch "$BRANCH"
}

# The head of the branch fetched into bosun.git (where `bosun update` fetches it too), and its folder
# beside the others, made if it is not there: NEW.
add_version() {
  run git -c core.hooksPath=/dev/null --git-dir="$BARE" fetch --quiet origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" ||
    die "could not fetch $BRANCH from $(git --git-dir="$BARE" config remote.origin.url)"
  commit="<commit>"
  if [ "$DRY_RUN" = 0 ]; then
    commit="$(git --git-dir="$BARE" rev-parse --verify --quiet "refs/remotes/origin/$BRANCH^{commit}")" ||
      die "no commit at the head of $BRANCH"
  fi
  NEW="$VERSIONS/$(printf '%s' "$commit" | cut -c1-12)"
  [ -d "$NEW" ] || run git -c core.hooksPath=/dev/null --git-dir="$BARE" worktree add --quiet --detach "$NEW" "$commit"
  [ "$DRY_RUN" = 1 ] || [ "$(git -C "$NEW" rev-parse HEAD 2>/dev/null)" = "$commit" ] ||
    die "$NEW is not a checkout of $commit; move it aside and run this again"
}

get_bosun() {
  pick_branch
  case "$KIND" in
    new) say "Getting bosun into $VERSIONS, with $DIR a link to the version in use" ;;
    checkout) say "$DIR is a checkout from before versions: getting the newest bosun beside it, into $VERSIONS" ;;
    link) say "Getting the newest bosun into $VERSIONS, beside the one in use ($FROM)" ;;
  esac
  if [ -d "$BARE" ]; then use_repo; else make_repo; fi
  add_version
  [ "$KIND" != new ] || run ln -s "$NEW" "$DIR"
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

# The new version's `bosun update`, from the version in use: it shows the plan and asks first.
start_update() {
  command="$NEW/bin/bosun"
  if [ "$DRY_RUN" = 1 ]; then
    say "Then it would run: $command update --from $FROM"
    [ -z "$CHECKOUT_SET_ASIDE" ] ||
      say "which converts $CHECKOUT_SET_ASIDE at a quiet moment: it keeps it as $VERSIONS/legacy-<its commit>, and $DIR becomes a link to the new version"
    return 0
  fi
  [ "$UNATTENDED" = 0 ] || exec "$command" update --from "$FROM" --yes
  has_terminal && exec "$command" update --from "$FROM" </dev/tty
  exec "$command" update --from "$FROM" # no terminal to ask on: it shows the plan
}

# Whether bosun init has set the install at $DIR up: its install.sh links ~/.local/bin/bosun to it. An
# install it never finished (stopped at what bosun needs, or at a question) is not set up yet.
set_up() {
  target="$(readlink "$HOME/.local/bin/bosun" 2>/dev/null)" || return 1
  linked="$(cd "$(dirname "$target")" 2>/dev/null && pwd -P)" || return 1
  ours="$(cd "$DIR/bin" 2>/dev/null && pwd -P)" || return 1
  [ "$linked" = "$ours" ]
}

intro() {
  say "bosun: agents write the code; bosun runs the rest of the day, and remembers it."
  case "$KIND" in
    new | unfinished)
      say "First it checks what it needs and tells you what to do about anything missing. Then a few"
      say "questions, and a plan to accept. Nothing changes until you do."
      ;;
    *)
      say "It gets the newest version beside the one you run, whose bosun update shows its plan and asks"
      say "first. Nothing changes until you accept it."
      ;;
  esac
  [ "$DRY_RUN" = 0 ] || say "(a dry run: nothing changes)"
}

main() {
  parse_args "$@"
  clean_env
  check_os
  look
  [ "$KIND" = new ] || set_up || KIND="unfinished"
  intro
  case "$KIND" in
    new)
      get_bosun
      start_init
      ;;
    unfinished)
      # Most likely it never got further than this line, and nothing of it runs yet: bosun init again,
      # from the version there, which `bosun update` takes forward once it is set up. Only `bosun
      # update` switches versions, so this never does, whatever made the link go.
      say "$DIR is there, but ~/.local/bin/bosun does not lead into it, as bosun init leaves it: starting bosun init again"
      start_init
      ;;
    *)
      get_bosun
      start_update
      ;;
  esac
}

main "$@"
