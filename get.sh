#!/bin/sh
# Install bosun, or update it:
#
#   curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh
#
# A first install puts the newest release of bosun in ~/.local/share/bosun-versions, a folder for each
# version beside the one repository they come from (bosun.git), makes ~/.local/share/bosun a link to the
# version in use, and starts `bosun init`, which checks what bosun needs and says what to do about
# anything missing, then asks a few questions and shows its plan before it changes anything. It installs
# nothing else, and never uses sudo; `bosun init` asks for it once, for the wake schedule, and says so
# first. A repository with no release yet gets the head of main, and it says so. With BOSUN_BRANCH it
# gets the head of that branch instead, and the install follows the branch: init is told through
# BOSUN_INIT_CHANNEL=main, and the line it prints to run again after what bosun needs is there keeps
# BOSUN_BRANCH, as it keeps BOSUN_HARNESS.
#
# Run it again to update: it hands over to the `bosun update` of the install, which moves it as its own
# channel says (the newest release, or the head of its branch), shows its plan and asks first. An install
# an earlier get.sh made, a checkout in ~/.local/share/bosun, is converted by a first update, at a quiet
# moment, by the update this gets from the head of main: the checkout is kept as bosun-versions/legacy-<its
# commit>, and ~/.local/share/bosun becomes the link. An install `bosun init` has not set up yet (it
# stopped at what bosun needs) gets `bosun init` again instead. `bosun uninstall` takes it all off again.
#
#   curl ... | sh -s -- --dry-run      say what it would do, and do nothing
#   curl ... | sh -s -- --unattended   ask nothing: take the recommended setup (on a first install without
#                                      repo windows or connectors, since nobody chose them), or go ahead
#                                      with the update
#   curl ... | BOSUN_HARNESS=codex sh  run repo sessions on Codex, and check that it is ready
#
# BOSUN_DIR (where it goes), BOSUN_REPO and BOSUN_BRANCH (what it gets) override the defaults. On an
# existing install the defaults are what it came from: bosun.git's origin, and its bosun.branch; for a
# checkout from before versions, its own origin. What it says never shows a URL's user and password.
# The newest release is found by newest_release below, which follows lib/update_check.py's rule for
# one (tests/unit/test_get_sh.py holds the two to the same answers): this runs before Python can be
# relied on, so it cannot ask it.
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
RELEASE=""                 # pick_release: the newest release's tag, on a new install that asked for no branch
RELEASE_COMMIT=""          # ... and its commit
THIS_LINE="curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | ${BOSUN_BRANCH:+BOSUN_BRANCH=$BOSUN_BRANCH }${BOSUN_HARNESS:+BOSUN_HARNESS=$BOSUN_HARNESS }sh"
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
# <arg> as this says it: a URL without its user and password (a token in https://<token>@host/...).
shown() {
  case "$1" in
    *://*) ;;
    *)
      printf '%s' "$1"
      return 0
      ;;
  esac
  url_rest="${1#*://}"
  url_host="${url_rest%%/*}"
  printf '%s://%s%s' "${1%%://*}" "${url_host##*@}" "${url_rest#"$url_host"}"
}
run() {
  printf '  $'
  for run_arg in "$@"; do printf ' %s' "$(shown "$run_arg")"; done
  printf '\n'
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
  # As root, the install would land in root's home folder, owned by root, and its jobs in root's session.
  [ "$(id -u)" != 0 ] || die "run this as yourself, not as root (sudo): bosun asks for sudo itself, once, when it needs it."
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

# Where a first bosun.git is cloned from: BOSUN_REPO, else for a checkout from before versions where it
# came from (its origin, as an older get.sh pulled from it), else the default.
pick_repo() {
  [ -z "${BOSUN_REPO:-}" ] && [ "$KIND" = checkout ] || return 0
  origin="$(git -C "$DIR" config remote.origin.url 2>/dev/null)" || origin=""
  [ -z "$origin" ] || REPO="$origin"
}

# The branch: BOSUN_BRANCH, else the one the install follows (bosun.git's bosun.branch), else main.
pick_branch() {
  [ -n "$BRANCH" ] || [ ! -d "$BARE" ] || BRANCH="$(git --git-dir="$BARE" config bosun.branch 2>/dev/null)" || BRANCH=""
  [ -n "$BRANCH" ] || BRANCH=main
}

# `git ls-remote --tags` output on stdin: the newest release in it, as "<tag> <commit>"; nothing when there
# is none. A release tag is v<YYYY.MM.DD>, and a second one the same day v<YYYY.MM.DD>.2, then .3 (.0 and
# .1 are not release tags); the newest is the latest by date, then by number, and an annotated tag's
# commit is its peeled entry's (<tag>^{}). This is a twin of newest_release in lib/update_check.py, which
# `bosun update` asks, pinned to it by tests/unit/test_get_sh.py: change one, change the other.
newest_release() {
  awk -F '\t' '
    $1 != "" && $2 != "" {
      ref = $2
      sub(/^refs\/tags\//, "", ref)
      if (sub(/\^\{\}$/, "", ref)) peeled[ref] = $1; else plain[ref] = $1
    }
    END {
      for (tag in plain) {
        if (tag !~ /^v[0-9][0-9][0-9][0-9]\.[0-9][0-9]\.[0-9][0-9](\.[0-9]+)?$/) continue
        parts = split(substr(tag, 2), field, ".")
        number = 1
        if (parts == 4) number = field[4] + 0
        if (parts == 4 && number < 2) continue
        key = sprintf("%04d%02d%02d%012d", field[1], field[2], field[3], number)
        if (best == "" || key > bestkey || (key == bestkey && tag > best)) {
          best = tag
          bestkey = key
        }
      }
      if (best != "") print best, (best in peeled ? peeled[best] : plain[best])
    }'
}

# On a new install that asked for no branch: the newest release of REPO (RELEASE, RELEASE_COMMIT). A
# repository with no release yet gets the head of its branch, as a branch would, and it says so; one that
# cannot be reached stops the install here, as it would at the clone.
pick_release() {
  [ "$KIND" = new ] && [ -z "${BOSUN_BRANCH:-}" ] || return 0
  tags="$(git ls-remote --tags "$REPO" 'v*')" || die "could not reach $(shown "$REPO")"
  newest="$(printf '%s\n' "$tags" | newest_release)"
  if [ -z "$newest" ]; then
    say "There is no release of bosun at $(shown "$REPO") yet: getting the head of $BRANCH"
    return 0
  fi
  RELEASE="${newest% *}"
  RELEASE_COMMIT="${newest#* }"
}

# bosun.git, a bare clone of the one branch, marked as the installer's. No tags come with it: a release
# is fetched into a remote ref (add_version), as `bosun update` does, never into refs/tags, which
# `bosun uninstall` would count as work of yours.
make_repo() {
  run mkdir -p "$VERSIONS"
  run git -c core.hooksPath=/dev/null clone --quiet --bare --no-tags --single-branch --branch "$BRANCH" "$REPO" "$BARE"
  run git --git-dir="$BARE" config bosun.managed true     # `bosun uninstall` only deletes versions marked so
  run git --git-dir="$BARE" config bosun.branch "$BRANCH" # what `bosun update` follows on the main channel
}

# bosun.git as it is, but for what BOSUN_REPO or BOSUN_BRANCH asks for instead.
use_repo() {
  [ -z "${BOSUN_REPO:-}" ] || run git --git-dir="$BARE" remote set-url origin "$REPO"
  [ -z "${BOSUN_BRANCH:-}" ] || run git --git-dir="$BARE" config bosun.branch "$BRANCH"
}

# <what> fetched into bosun.git as `bosun update` fetches it (<refspec>, into the remote ref <ref>), and its
# folder beside the others, made if it is not there: NEW. <commit> is what a dry run says it leads to.
add_version() {
  what="$1" refspec="$2" ref="$3" commit="$4"
  run git -c core.hooksPath=/dev/null --git-dir="$BARE" fetch --quiet --no-tags origin "$refspec" ||
    die "could not fetch $what from $(shown "$(git --git-dir="$BARE" config remote.origin.url)")"
  if [ "$DRY_RUN" = 0 ]; then
    commit="$(git --git-dir="$BARE" rev-parse --verify --quiet "$ref^{commit}")" ||
      die "no commit at $what"
  fi
  NEW="$VERSIONS/$(printf '%s' "$commit" | cut -c1-12)"
  [ -d "$NEW" ] || run git -c core.hooksPath=/dev/null --git-dir="$BARE" worktree add --quiet --detach "$NEW" "$commit"
  [ "$DRY_RUN" = 1 ] || [ "$(git -C "$NEW" rev-parse HEAD 2>/dev/null)" = "$commit" ] ||
    die "$NEW is not a checkout of $commit; move it aside and run this again"
}

get_bosun() {
  pick_repo
  pick_branch
  pick_release
  case "$KIND" in
    new)
      if [ -n "$RELEASE" ]; then
        say "Getting bosun $RELEASE, the newest release, into $VERSIONS, with $DIR a link to the version in use"
      else
        say "Getting bosun into $VERSIONS, with $DIR a link to the version in use"
      fi
      ;;
    checkout) say "$DIR is a checkout from before versions: getting the newest bosun beside it, into $VERSIONS" ;;
    link) say "Getting the newest bosun into $VERSIONS, beside the one in use ($FROM)" ;;
  esac
  if [ -d "$BARE" ]; then use_repo; else make_repo; fi
  if [ -n "$RELEASE" ]; then
    add_version "$RELEASE" "+refs/tags/$RELEASE:refs/remotes/origin/release" refs/remotes/origin/release "$RELEASE_COMMIT"
  else
    add_version "$BRANCH" "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" "refs/remotes/origin/$BRANCH" "<commit>"
  fi
  [ "$KIND" != new ] || run ln -s "$NEW" "$DIR"
}

start_init() {
  command="$DIR/bin/bosun"
  # An install that asked for a branch follows it: init is told, and writes it into bosun.env (the others
  # follow the releases, which is its default).
  channel=""
  [ -z "${BOSUN_BRANCH:-}" ] || channel="BOSUN_INIT_CHANNEL=main "
  if [ "$DRY_RUN" = 1 ]; then
    if [ "$UNATTENDED" = 1 ]; then
      say "Then it would start: ${channel}BOSUN_INIT_UNATTENDED=1 $command init --recommended"
    else
      say "Then it would start: ${channel}$command init"
    fi
    return 0
  fi
  BOSUN_USER_PATH="$USER_PATH"
  BOSUN_RERUN="$THIS_LINE"
  export BOSUN_USER_PATH BOSUN_RERUN
  [ -z "$channel" ] || export BOSUN_INIT_CHANNEL=main # init's own variable: never the config key, which would beat bosun.env in every window it starts
  # Unattended is handed over as a variable, as the two above: an older version on disk (an install it
  # never finished) ignores it and takes the recommended setup as it always did, where a flag it does not
  # know would stop it.
  if [ "$UNATTENDED" = 1 ]; then
    BOSUN_INIT_UNATTENDED=1
    export BOSUN_INIT_UNATTENDED
    exec "$command" init --recommended
  fi
  has_terminal && exec "$command" init </dev/tty
  exec "$command" init # no terminal to ask on: it prints the plan
}

# Replaces this with `<command> update <args>`, which shows its plan and asks first: with --yes when
# unattended, and with the terminal when there is one.
exec_update() {
  command="$1"
  shift
  [ "$UNATTENDED" = 0 ] || exec "$command" update "$@" --yes
  has_terminal && exec "$command" update "$@" </dev/tty
  exec "$command" update "$@" # no terminal to ask on: it shows the plan
}

# A re-run on an install: the install's own `bosun update`, which moves it as its channel says (the newest
# release, or the head of its branch). Nothing is fetched here, so this never moves an install off its
# channel.
hand_over() {
  if [ "$DRY_RUN" = 1 ]; then
    say "Then it would run: $DIR/bin/bosun update"
    return 0
  fi
  exec_update "$DIR/bin/bosun"
}

# The new version's `bosun update`, from the version in use, which is a checkout from before versions (or
# one put back after a conversion): it converts it, shows the plan and asks first.
start_update() {
  command="$NEW/bin/bosun"
  if [ "$DRY_RUN" = 1 ]; then
    say "Then it would run: $command update --from $FROM"
    [ -z "$CHECKOUT_SET_ASIDE" ] ||
      say "which converts $CHECKOUT_SET_ASIDE at a quiet moment: it keeps it as $VERSIONS/legacy-<its commit>, and $DIR becomes a link to the new version"
    return 0
  fi
  exec_update "$command" --from "$FROM"
}

# Whether bosun init has set the install at $DIR up: its install.sh links ~/.local/bin/bosun to it, as its
# last step. An install it never finished (stopped at what bosun needs, at a question, at the launchd jobs
# or at sudo for the wake) is not set up yet.
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
      if [ -n "$CHECKOUT_SET_ASIDE" ]; then
        say "It gets the newest version beside the one you run, whose bosun update shows its plan and asks"
        say "first. Nothing changes until you accept it."
      else
        say "It hands over to the bosun you run: its bosun update looks for what its channel says is newest,"
        say "shows its plan and asks first. Nothing changes until you accept it."
      fi
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
      if [ -n "$CHECKOUT_SET_ASIDE" ]; then
        get_bosun
        start_update
      else
        use_repo
        hand_over
      fi
      ;;
  esac
}

main "$@"
