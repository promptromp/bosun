# shellcheck shell=bash
# What the scenarios on a real Mac share (tests/install_e2e.sh, tests/work_e2e.sh): the checks, a new
# machine, the piped installer, launchd for real, and an uninstall that keeps your records. Sourced first
# thing: it changes the machine it runs on, so it refuses to go on anywhere but a GitHub macOS runner or
# the throwaway Mac of tests/sandbox.sh mac.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
{ [ "${GITHUB_ACTIONS:-}" = "true" ] || [ -e /etc/bosun-sandbox ]; } && [ "$(uname -s)" = "Darwin" ] || {
  echo "$0 only runs on a GitHub macOS runner or in tests/sandbox.sh mac: it would change this machine" >&2
  exit 1
}
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
# These tests update to the next commit of a branch, which has no release: follow the branch
# (the default follows the releases, docs/design/menu-bar.md).
export BOSUN_UPDATE_CHANNEL=main

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UID_NOW="$(id -u)"
DOMAIN="gui/$UID_NOW"
LABEL="com.bosun.morning"
DIR="$HOME/.local/share/bosun" # the install path: a link to the version in use
VERSIONS="$DIR-versions"       # each version's folder, beside bosun.git, and a checkout from before them
CONFIG="$HOME/.config/bosun"
OUT="$(mktemp -d)"
# The installer a first install pipes: this checkout's, or (tests/work_e2e.sh) the release's own, as the
# one a user ran then.
GET_SH="$ROOT/get.sh"
passed=0
failed=0

check() { # <description> <command...>: on failure, show the end of its output
  local description="$1" output
  shift
  if output="$("$@" 2>&1)"; then
    passed=$((passed + 1))
    echo "  pass  $description"
  else
    failed=$((failed + 1))
    echo "  FAIL  $description"
    [ -n "$output" ] && tail -20 <<<"$output" | sed 's/^/          /'
  fi
}
wait_for() { # <seconds> <command...>
  local deadline=$((SECONDS + $1))
  shift
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 2
  done
}
windows() { tmux -L bosun list-windows -t "=bosun" -F '#{window_name}' 2>/dev/null | sort | tr '\n' ' '; }
has_window() { windows | grep -q "$1 "; }
morning_ran() { grep -q "morning run complete\|morning run finished" "$HOME"/.local/state/bosun/logs/morning-*.log; }

a_repo_by_hand() { # <name> <origin url>: on a new Mac there is no git until the installer has run
  local repo="$HOME/Development/$1/.git"
  mkdir -p "$repo/objects" "$repo/refs"
  echo "ref: refs/heads/main" >"$repo/HEAD"
  printf '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = %s\n' "$2" >"$repo/config"
}

a_new_machine() {
  echo "a new machine"
  mkdir -p "$HOME/.local/bin"
  cp "$ROOT/tests/sandbox/claude-stand-in" "$HOME/.local/bin/claude"
  # A person went through its first-run screens, and accepted the folder trust for the repos' folder.
  jq -n --arg dir "$HOME/Development" '{hasCompletedOnboarding: true, projects: {($dir): {hasTrustDialogAccepted: true}}}' >"$HOME/.claude.json"
  # With GH_TOKEN the real gh counts as logged in; without one, it needs its stand-in too.
  [ -n "${GH_TOKEN:-}" ] || cp "$ROOT/tests/sandbox/gh-stand-in" "$HOME/.local/bin/gh"
  a_repo_by_hand acme-api https://github.com/acme/api.git
  if [ -z "${BOSUN_REPO:-}" ]; then # on a runner: this checkout, under a name it can clone
    git -C "$ROOT" checkout -q -B install-test
    export BOSUN_REPO="$ROOT" BOSUN_BRANCH="install-test"
  fi
  check "nothing of bosun is here" bash -c '[ ! -e "$1" ] && [ ! -e "$2" ] && [ ! -e "$3" ]' _ "$DIR" "$VERSIONS" "$CONFIG"
  command -v tmux >/dev/null && echo "  note  tmux is already on this runner"
}

# What someone on a new Mac is told, followed to the letter. get.sh installs nothing itself.
follow_what_it_says() {
  echo "what bosun needs: it says what to do, and stops"
  check "a dry run changes nothing" bash -c 'sh "$1" --dry-run; [ ! -e "$2" ]' _ "$GET_SH" "$DIR"
  if ! command -v brew >/dev/null; then
    # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
    cat "$GET_SH" | sh >"$OUT/first.txt" 2>&1
    check "on a new Mac it stops and points at Homebrew" grep -q "https://brew.sh" "$OUT/first.txt"
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/null >/dev/null
  fi
  # shellcheck disable=SC2002
  cat "$GET_SH" | sh >"$OUT/second.txt" 2>&1
  cat "$OUT/second.txt"
  local line
  line="$(grep -oE "brew install [a-z0-9 ]+" "$OUT/second.txt" | head -1)"
  check "it says what to install" test -n "$line"
  # shellcheck disable=SC2086 # the words of the line it printed
  check "that line works" $line
}

install() {
  echo "the piped installer, unattended"
  # shellcheck disable=SC2002
  cat "$GET_SH" | sh -s -- --unattended >"$OUT/install.txt" 2>&1
  local installed=$?
  cat "$OUT/install.txt"
  check "it finishes with bosun doctor passing" test "$installed" = 0
  check "Homebrew's tmux is here" command -v tmux
  check "python3 is new enough" python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))'
  check "repo sessions are in auto mode" grep -q -- "--permission-mode auto" "$CONFIG/bosun.env"
  check "bosun is linked" test -L "$HOME/.local/bin/bosun"
}

# The windows a script wants, as it names them: `get.sh --unattended` sets up none on a first install,
# since nobody chose them. (A release from before that set them up already, in an order of its own, so
# --force: then sessions.yml may be rewritten, with a backup.)
windows_by_answers() { # <repo folder name in ~/Development>...
  local answers="$OUT/windows.json" name
  jq -n '{repos: [$ARGS.positional[] | {dir: ("~/Development/" + .)}]}' --args "$@" >"$answers"
  check "the windows named with bosun init --answers: $*" bash -c '"$1" init --force --answers "$2" >"$3" 2>&1 || { tail -15 "$3"; exit 1; }' \
    _ "$HOME/.local/bin/bosun" "$answers" "$OUT/windows.txt"
  for name in "$@"; do
    check "... $name in sessions.yml" grep -q "name: $name" "$CONFIG/sessions.yml"
  done
}

# <folder> is a version: a folder of bosun-versions, a worktree of its bosun.git at the commit it is
# named for.
a_version() {
  [ "$(dirname "$1")" = "$(cd "$VERSIONS" && pwd -P)" ] && [ -f "$1/.git" ] &&
    [[ "$(git -C "$1" rev-parse HEAD)" == "$(basename "$1")"* ]]
}
# What a first install of this checkout's get.sh makes: the versions side by side, the install path a
# link to the one in use, and ~/.local/bin/bosun through it.
side_by_side() {
  local version
  version="$(cd "$DIR" 2>/dev/null && pwd -P)" || return 1
  [ -L "$DIR" ] && [ -d "$VERSIONS/bosun.git" ] && a_version "$version" &&
    [ "$(readlink "$HOME/.local/bin/bosun")" = "$DIR/bin/bosun" ] || {
    ls -la "$DIR" "$VERSIONS" "$HOME/.local/bin/bosun" 2>&1
    return 1
  }
}

morning_runs() { cat "$HOME"/.local/state/bosun/logs/morning-*.log 2>/dev/null | grep -c "morning run complete"; }

# This shell's pid and each of its parents': whatever started the check, which macOS must not name as the
# responsible process of what bosun starts.
caller_pids() {
  local pid=$$
  while [ "${pid:-0}" -gt 1 ]; do
    echo "$pid"
    pid="$(ps -o ppid= -p "$pid" | tr -d ' ')"
  done
}

# <pid>'s responsible process, from `launchctl procinfo` (sudo as a second try: it may refuse another
# process). It fails, saying what it got, when that has no "responsible pid = <number>" line, so that a
# macOS that prints it otherwise is a failure here and not a pass.
responsible_pid() {
  local info pid
  info="$(launchctl procinfo "$1" 2>&1)" || info="$(sudo -n launchctl procinfo "$1" 2>&1)" || {
    echo "launchctl procinfo $1 failed: $info"
    return 1
  }
  pid="$(sed -n 's/^[[:space:]]*responsible pid = \([0-9][0-9]*\).*/\1/p' <<<"$info" | head -1)"
  [ -n "$pid" ] || {
    echo "no 'responsible pid = <number>' line in launchctl procinfo $1: its format is not the one this check reads; it said:"
    echo "$info"
    return 1
  }
  echo "$pid"
}

# The tmux server's responsible process is not one of the caller's (it is launchd's job's), which is what
# `bosun morning --now --background` is for: a process the app starts keeps the app as its responsible
# process, and with it macOS's privacy prompts.
server_not_the_callers_child() {
  local server responsible
  server="$(tmux -L bosun display-message -p '#{pid}')" || return 1
  responsible="$(responsible_pid "$server")" || {
    echo "$responsible"
    return 1
  }
  if caller_pids | grep -qx "$responsible"; then
    echo "tmux server $server has responsible pid $responsible, which is this check or one of its parents: $(caller_pids | tr '\n' ' ')"
    return 1
  fi
  echo "tmux server $server, responsible pid $responsible (not one of: $(caller_pids | tr '\n' ' '))"
}

more_runs_than() { [ "$(morning_runs)" -gt "$1" ]; } # <count>
now_runs() { cat "$HOME"/.local/state/bosun/logs/morning-*.log 2>/dev/null | grep -c "=== morning run (trigger=now, "; }
now_runs_settle_at() { # <count>: and stay there a while, as a second run would have started by then
  sleep 5
  [ "$(now_runs)" = "$1" ]
}

# `bosun morning --now --background`, from a stopped bosun: launchd's job runs the morning run now.
now_job_for_real() {
  local bosun="$HOME/.local/bin/bosun" done_before now_before
  "$bosun" stop >/dev/null 2>&1
  done_before="$(morning_runs)"
  now_before="$(now_runs)"
  check "bosun morning --now --background returns, exit 0" "$bosun" morning --now --background
  check "... and a second one straight after, while the run is going, is no error" "$bosun" morning --now --background
  check "... launchd runs the job: its morning run ends well" wait_for 180 more_runs_than "$done_before"
  check "... once, whatever the second one asked: launchd does not start a run that is going (no -k)" now_runs_settle_at "$((now_before + 1))"
  check "... which brings up the orchestrator's window" wait_for 60 has_window orchestrator
  check "... and the tmux server it started is not a child of the caller" server_not_the_callers_child
}

launchd_for_real() {
  echo "launchd and pmset, for real"
  for job in "$LABEL" "$LABEL.watchdog" "$LABEL.learn" "$LABEL.now"; do
    check "$job is loaded" launchctl print "$DOMAIN/$job"
    check "$job's plist is valid" plutil -lint -s "$HOME/Library/LaunchAgents/$job.plist"
  done
  check "the repeating wake is set" bash -c 'pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'
  launchctl kickstart -k "$DOMAIN/$LABEL"
  check "launchd really runs the morning job" wait_for 180 morning_ran
  check "... which brings up the repo's window" wait_for 60 has_window acme-api
  check "... and the orchestrator's" wait_for 60 has_window orchestrator
  check "... and ended well" grep -q "morning run complete" "$HOME"/.local/state/bosun/logs/morning-*.log
  tail -15 "$HOME"/.local/state/bosun/logs/morning-*.log
  launchctl print "$DOMAIN/$LABEL" | grep -E '^\s+(state|last exit code|runs) ' || true
  now_job_for_real
  "$HOME/.local/bin/bosun" stop >/dev/null 2>&1
}

jobs_are_gone() {
  for job in "$LABEL" "$LABEL.watchdog" "$LABEL.learn" "$LABEL.now"; do
    check "$job is unloaded" bash -c '! launchctl print "$1"' _ "$DOMAIN/$job"
    check "... launchd cannot run it" bash -c '! launchctl kickstart "$1"' _ "$DOMAIN/$job"
    check "... and its plist is gone" test ! -e "$HOME/Library/LaunchAgents/$job.plist"
  done
}
settings_sum() { cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum; }
# <settings_sum before>: your settings as they were, or with only the keys a newer `bosun init` adds to
# bosun.env (an install from an older release has none of them: an update never writes a new key, its
# default applies), the file as it was kept beside it as a .pre-init-* copy: lines only added, each a
# BOSUN_* key, a comment or blank. Else what changed.
settings_as() {
  local copy kept="" stray
  [ "$1" = "$(settings_sum)" ] && return 0
  for copy in "$CONFIG"/bosun.env.pre-init-*; do [ -e "$copy" ] && kept="$copy"; done
  if [ -n "$kept" ] && [ "$1" = "$(cat "$kept" "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)" ]; then
    # The lines that differ and are not an added key, comment or blank, collected first: under pipefail,
    # a test of the pipeline's status would be diff's (1 whenever the files differ).
    stray="$(diff "$kept" "$CONFIG/bosun.env" | grep '^[<>]' | grep -vE '^>( (BOSUN_[A-Z0-9_]+=.*|#.*))?[[:space:]]*$')"
    if [ -z "$stray" ]; then
      diff "$kept" "$CONFIG/bosun.env" | sed -n 's/^> \(BOSUN_[A-Z_]*=.*\)/  note  added by the newer init: \1/p' >&2
      return 0
    fi
  fi
  [ -n "$kept" ] && diff "$kept" "$CONFIG/bosun.env"
  return 1
}
trusted() { jq -e --arg dir "$1" '.projects[$dir].hasTrustDialogAccepted == true' "$HOME/.claude.json"; }
# Anything of bosun's anywhere in this home folder, by name. Not the agents' own folders, nor this test's.
named_bosun() {
  find "$HOME" \( -path "$ROOT" -o -path "$HOME/.claude" -o -path "$HOME/Library/Caches" -o -path "$HOME/repo.bundle" \
    -o -path "$HOME/work" -o -path "$HOME/runners" -o -path "$HOME/hostedtoolcache" \) -prune -o -iname '*bosun*' -print 2>/dev/null
}

# Through a real terminal, with real launchd and pmset: yes, then Enter at each question, so your records
# stay. <settings> and <ledger> are what settings_sum and cksum said of them before.
uninstall_keeping_records() { # <settings> <ledger>
  local bosun="$HOME/.local/bin/bosun" state="$HOME/.local/state/bosun" out="$OUT/uninstall.txt"
  check "yes, then Enter to keep your records" python3 "$ROOT/tests/sandbox/drive.py" "$out" \
    "Are you sure" "y<enter>" \
    "Keep your ledger" "<enter>" \
    "Keep your knowledge graph" "<enter>" \
    "bosun is uninstalled" "" \
    -- "$bosun" uninstall
  cat "$out"
  jobs_are_gone
  check "the wake is gone" bash -c '! pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'
  check "the windows have stopped" bash -c '! tmux -L bosun has-session -t "=bosun"'
  check "what get.sh installed is gone: the link, and bosun-versions with every version" \
    bash -c '[ ! -e "$1" ] && [ ! -L "$1" ] && [ ! -e "$2" ]' _ "$DIR" "$VERSIONS"
  check "the link is gone" test ! -e "$bosun"
  check "your settings are as you left them" test "$1" = "$(settings_sum)"
  check "your ledger is as you left it" test "$2" = "$(cksum <"$state/ledger.json")"
  check "your knowledge graph is there" test -s "$CONFIG/knowledge/facts.jsonl"
  check "the trust it gave a repo is gone" bash -c '! jq -e --arg dir "$1" ".projects[\$dir].hasTrustDialogAccepted" "$HOME/.claude.json"' _ "$HOME/Development/acme-api"
  check "the trust you gave stays" trusted "$HOME/Development"
}

install_again() { # <settings>: the piped installer once more, on what uninstall kept
  # shellcheck disable=SC2002 # the pipe is the point: this is how people run it
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/back.txt" 2>&1
  check "installing again passes" test "$?" = 0
  check "... versions side by side, the install path a link" side_by_side
  check "... with your settings" settings_as "$1"
}
