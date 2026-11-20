#!/bin/bash
# Usage: tests/sandbox/mac.sh setup | run [--from-nothing] [<parts>] | work [--base <git ref>] [<parts>]
#        | app [--from build|release] [<parts>] | live [claude|codex|agy] [<parts>] [--paid]
#        | --changed <git ref> [--list] | shell | try [--from release|checkout] [--run <script>] [codex] | clean
#        (through tests/sandbox.sh mac)
#   <parts>: --only <part>[,<part>...] | --changed <git ref> [--list], and -x (--exitfirst)
#
# A real, throwaway Mac: macOS in a Tart VM on this machine, for what the Docker sandbox cannot show.
# Each run clones a base image, which takes no time and no disk, and deletes the clone afterwards.
#   setup   pull the image (about 29 GB) and prepare the two base images: bosun-mac-new, a new Mac (no
#           Homebrew, no Apple tools), and bosun-mac-dev, a developer's Mac (the same with Homebrew)
#   run     on a clone of the developer's Mac: this checkout's installer on the default releases (its origin
#           has a release tag), real launchd, the console's job, `bosun quit`, an update to the next release,
#           a real wake schedule and uninstall (tests/install_e2e.sh). --from-nothing: on the new Mac instead
#   work    on a clone of the developer's Mac: a week of real work on this checkout, installed by its
#           get.sh with its versions side by side, an update mid-afternoon to the next version on a year of
#           data, back and forward again, then the clock moved, writers racing, writes cut short and the
#           knowledge files changed outside their journal, one of them by an older bosun's forget (--base,
#           default the last bosun from before the knowledge base's journal) (tests/work_e2e.sh)
#   app     on a clone of the developer's Mac, with its desktop session: bosun.app driven through every state and
#           choice of its menu by its test hooks, and through a reboot (tests/app_e2e.sh). --from build (the
#           default): the app is built here with app/build.sh, ad hoc signed, and copied in; --from release:
#           bosun init installs the published, notarized one, as a person gets it (needs a release, and a
#           Team ID in app/publisher.json). The app is never started on this Mac, only in the clone
#   live    the real agents from nothing, a real task handed over, and an update with them at work: the
#           take-stock and update prompts (tests/sandbox/live.sh). It costs
#           real money, about a dollar. It reads API keys from your Keychain, as BOSUN_SANDBOX_ANTHROPIC_ITEM
#           and BOSUN_SANDBOX_OPENAI_ITEM name them; they never leave the clone, which is deleted.
#           BOSUN_SANDBOX_CODEX_MODEL picks a cheaper model than Codex's default.
#           On Claude Code it includes the ontology end to end (the part `ontology`, after the hand-over);
#           `--only ontology` runs only that, after the install and the real windows.
#           live agy needs you: its login is a browser sign-in, in the desktop session. The clone boots with
#           its window, agy is installed with Google's installer, and you are told to open Terminal there,
#           run ~/.local/bin/agy, sign in with Google (a personal account's free quota) and quit it; the run
#           waits up to 15 minutes for that. The orchestrator stays on Claude Code (the Anthropic key only).
#           Your sign-in stays in the clone, which is deleted at the end; no image is ever saved
#   shell   a clone with a window, to try things as a new user would; a restart in it keeps it, and it is
#           deleted when you leave its shell
#   try     the same, with bosun already set up and running in it, signed in: Claude Code (and Codex with
#           `codex`) installed by the installer's own lines, its first-run screens gone through, your Anthropic
#           key from the Keychain as its apiKeyHelper (as for live; the OpenAI key for codex), bosun installed
#           unattended from the release (--from checkout: this checkout, with the ontology), the acme world's three
#           repos as its windows, the menu bar app (from the release), and started. Its agents use BOSUN_SANDBOX_TRY_MODEL (sonnet): the
#           key is billed per request for as long as bosun runs in it, until you leave the shell. --run <script>:
#           instead of your shell, the script runs in the clone (zsh, its output here, masked), and the clone is
#           deleted when it ends: bosun with the real agents, set up and checked with no one at it
#   clean   delete the clones and base images (the pulled image stays: `tart prune` removes it)
#
# run, work, app and live are in parts (each suite's `part` lines; tools/vm_select.py). By default a suite runs
# whole. --only <part>[,<part>...] runs those parts and what they need; --changed <git ref> the parts the change
# since the merge-base of <ref> and HEAD reaches (committed, staged, unstaged and untracked files), by the parts'
# `covers` and tests/vm_selection.txt, failing closed: a file nothing names runs run, work and app whole. Either
# prints the choice and its estimate first, and the summary says it was a partial run; with --list it prints the
# choice and runs nothing (no clone). Nothing to run for a suite: it says so, exit 0. `--changed <ref>` without a
# suite prints every suite's choice. live is paid: --changed runs its parts only with --paid (a whole run or --only
# is asked for by name). On work, --changed gives the afternoon update a year of data only when the update's freeze
# is in the change (else 30 days; --only always 30), BOSUN_SANDBOX_YEAR_DAYS=<days> another. -x stops at the
# first failed part. live agy runs whole.
# Needs Tart: brew install openai/tools/tart. Guide: docs/development.md.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="ghcr.io/cirruslabs/macos-golden-gate-vanilla:latest"
NEW_MAC="bosun-mac-new"
DEV_MAC="bosun-mac-dev"
CLONE="bosun-mac-run-$$"
TREE_BRANCH="sandbox-mac-tree"
HOMEBREW_INSTALLER="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
BOOT_SECONDS=240
WORK="$(mktemp -d)"
# The older bosun `work` runs beside the install, from a checkout of its own: the last from before the
# knowledge base's journal (#17), whose forget in the same knowledge folder the installed bosun takes in.
# (A commit id, not a secret.)
OLDER_BOSUN="95545b0ba6a0e3729e980aa9468876e1228c1874" # pragma: allowlist secret
# The release `run` makes in its origin (the next one, v2026.11.02, is tests/install_e2e.sh's).
RUN_RELEASE="v2026.11.01"
VM_PID=""
ANTHROPIC_ITEM="${BOSUN_SANDBOX_ANTHROPIC_ITEM:-bosun-sandbox-anthropic}"
OPENAI_ITEM="${BOSUN_SANDBOX_OPENAI_ITEM:-bosun-sandbox-openai}"
KEY_SHAPE='s/sk-(ant|proj|svcacct|admin)-[A-Za-z0-9_*-]+/[KEY]/g'
# Google's token shapes (agy's sign-in), and the query string of any URL, which a pairing link of agy's
# Remote Control or a sign-in redirect would carry: masked in what comes back from the VM, as keys are.
TOKEN_SHAPE='s/(ya29\.|1\/\/|AIza|GOCSPX-)[A-Za-z0-9_.\/-]+/[TOKEN]/g; s/(https?:\/\/[^ ?"<>]+)\?[^ "<>]*/\1?[QUERY]/g'

cleanup() {
  [ -n "$VM_PID" ] && {
    tart stop "$CLONE" >/dev/null 2>&1
    wait "$VM_PID" 2>/dev/null
  }
  tart delete "$CLONE" >/dev/null 2>&1
  git -C "$ROOT" branch -q -D "$TREE_BRANCH" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

say() { printf 'sandbox mac: %s\n' "$*"; }
die() {
  say "$*" >&2
  exit 1
}

# The images' default login, admin/admin (https://tart.run/quick-start), through ssh's own askpass, so
# this needs nothing installed for it.
# shellcheck disable=SC2054 # that comma is inside one option's value
ssh_options=(-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15
  -o ServerAliveInterval=30 -o PreferredAuthentications=password,keyboard-interactive)
in_vm() { # <vm> <command...>
  local vm="$1"
  shift
  # shellcheck disable=SC2029 # the IP expands here, and the command in the VM
  SSH_ASKPASS="$WORK/askpass" SSH_ASKPASS_REQUIRE=force ssh "${ssh_options[@]}" "admin@$(tart ip "$vm")" "$@"
}
to_vm() { # <vm> <local file> <remote path>
  SSH_ASKPASS="$WORK/askpass" SSH_ASKPASS_REQUIRE=force scp "${ssh_options[@]}" "$2" "admin@$(tart ip "$1"):$3"
}

# Without sound (--no-audio): no test needs it, and the VM's sound device waits on the Mac's audio service. Once
# that service had restarted, every boot hung before the guest started: no IP, and nothing in tart's output.
boot() { # <vm> [tart run flags...]: start it and wait for ssh
  local vm="$1" deadline=$((SECONDS + BOOT_SECONDS))
  shift
  tart run --no-audio "$@" "$vm" >"$WORK/run.log" 2>&1 &
  VM_PID=$!
  tart ip "$vm" --wait "$BOOT_SECONDS" >/dev/null 2>&1 || boot_failed "$vm got no IP address"
  until logged_in "$vm"; do
    [ "$SECONDS" -ge "$deadline" ] && boot_failed "$vm did not finish starting"
    sleep 2
  done
}
boot_failed() { # <message>: what tart run printed (the cleanup deletes it), then the message
  [ -s "$WORK/run.log" ] && say "tart run printed:" && tail -20 "$WORK/run.log" >&2
  die "$1"
}

# ssh comes up before the auto-login, and launchd has no gui domain until then.
logged_in() { # <vm>
  # shellcheck disable=SC2016 # expands in the VM
  in_vm "$1" 'launchctl print "gui/$(id -u)"' >/dev/null 2>&1
}

stop() { # <vm>
  in_vm "$1" 'sudo shutdown -h now' >/dev/null 2>&1
  wait "$VM_PID" 2>/dev/null
  VM_PID=""
}

need_tart() {
  command -v tart >/dev/null || die "needs Tart: brew install openai/tools/tart"
}

# ---- which parts a run runs (tools/vm_select.py; see the header) ----

ONLY="" CHANGED="" LIST=0 EXITFIRST=0 PAID=0 # the options every suite takes
SUITE_ARGS=()                                # the rest: the suite's own
# selection_args <arguments...>: the selection's options out of them, the rest in SUITE_ARGS.
selection_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --only | --changed)
        [ -n "${2:-}" ] || die "$1 takes a value (see --help)"
        if [ "$1" = --only ]; then ONLY="$2"; else CHANGED="$2"; fi
        shift
        ;;
      --list) LIST=1 ;;
      -x | --exitfirst) EXITFIRST=1 ;;
      --paid) PAID=1 ;;
      *) SUITE_ARGS+=("$1") ;;
    esac
    shift
  done
}

# chosen_parts <suite>: PARTS, the parts the run runs with what they need (empty: the whole suite), and for work
# YEAR_DAYS, from --only or --changed; it prints the choice. With --list, or when the change reaches none of the
# suite's parts, it ends the run here, before any clone. A --changed that cannot be read runs the whole suite.
PARTS="" YEAR_DAYS=""
chosen_parts() {
  local suite="$1" out last
  if [ -z "$ONLY$CHANGED" ]; then
    [ "$LIST" = 0 ] || die "--list goes with --changed or --only"
    return 0
  fi
  [ -z "$ONLY" ] || [ -z "$CHANGED" ] || die "--only and --changed do not go together"
  if [ -n "$ONLY" ]; then
    out="$(python3 "$ROOT/tools/vm_select.py" --root "$ROOT" --suite "$suite" --only "$ONLY" 2>&1)" || die "$out"
  else
    out="$(python3 "$ROOT/tools/vm_select.py" --root "$ROOT" --suite "$suite" --changed "$CHANGED" 2>&1)"
  fi
  last="${out##*$'\n'}"
  printf '%s\n' "${out%$'\n'*}"
  case "$last" in
    "=> none")
      say "the change reaches no part of $suite: nothing to run"
      exit 0
      ;;
    "=> all"*) PARTS="" ;;
    "=> only "?*)
      PARTS="${last#=> only }"
      PARTS="${PARTS%% *}"
      ;;
    *) say "tools/vm_select.py gave no answer: the whole of $suite runs" ;;
  esac
  case "$last" in *" year="*) YEAR_DAYS="${last##* year=}" ;; esac
  [ "$LIST" = 0 ] || exit 0
}
# part_runs <part>: the run runs it (every part does when PARTS is empty: the whole suite).
part_runs() {
  [ -z "$PARTS" ] && return 0
  case ",$PARTS," in *",$1,"*) return 0 ;; esac
  return 1
}
# What goes before a suite's script in the VM: its parts (tests/sandbox/e2e_parts.sh), -x, and work's year.
vm_env() {
  local year="${BOSUN_SANDBOX_YEAR_DAYS:-$YEAR_DAYS}"
  case "$year" in *[!0-9]*) die "BOSUN_SANDBOX_YEAR_DAYS is a number of days, not '$year'" ;; esac
  printf 'E2E_PARTS=%s E2E_EXITFIRST=%s%s' "$PARTS" "$EXITFIRST" "${year:+ E2E_YEAR_DAYS=$year}"
}

setup() {
  need_tart
  printf '#!/bin/sh\necho admin\n' >"$WORK/askpass"
  chmod +x "$WORK/askpass"
  if ! tart get "$NEW_MAC" >/dev/null 2>&1; then
    say "pulling $IMAGE as $NEW_MAC (about 29 GB, once)"
    tart clone "$IMAGE" "$NEW_MAC" || die "could not pull the image"
    boot "$NEW_MAC" --no-graphics
    in_vm "$NEW_MAC" 'sudo touch /etc/bosun-sandbox' || die "could not mark the image"
    stop "$NEW_MAC"
  fi
  if ! tart get "$DEV_MAC" >/dev/null 2>&1; then
    say "preparing $DEV_MAC: Homebrew and Apple's tools, a few minutes"
    tart clone "$NEW_MAC" "$DEV_MAC"
    boot "$DEV_MAC" --no-graphics
    # shellcheck disable=SC2016 # expands in the VM
    in_vm "$DEV_MAC" "NONINTERACTIVE=1 /bin/bash -c \"\$(curl -fsSL $HOMEBREW_INSTALLER)\" </dev/null" ||
      die "Homebrew did not install"
    stop "$DEV_MAC"
  fi
  say "ready: $NEW_MAC, $DEV_MAC"
}

# This checkout as it is now, also what is not committed, as a git bundle a new Mac can clone. A
# temporary index and branch, so nothing you have staged or checked out moves.
bundle_checkout() { # <file>
  local index="$WORK/index" tree commit
  cp "$(git -C "$ROOT" rev-parse --git-dir)/index" "$index" 2>/dev/null
  GIT_INDEX_FILE="$index" git -C "$ROOT" add -A || return 1
  tree="$(GIT_INDEX_FILE="$index" git -C "$ROOT" write-tree)" || return 1
  commit="$(git -C "$ROOT" -c user.name=sandbox -c user.email=sandbox@example.com commit-tree "$tree" \
    -p HEAD -m "the checkout, for the sandbox")" || return 1
  git -C "$ROOT" branch -f "$TREE_BRANCH" "$commit" || return 1
  git -C "$ROOT" bundle create -q "$1" "$TREE_BRANCH" 2>/dev/null
}

prepare_clone() { # <base image>
  need_tart
  printf '#!/bin/sh\necho admin\n' >"$WORK/askpass"
  chmod +x "$WORK/askpass"
  tart get "$1" >/dev/null 2>&1 || die "no $1 yet: run tests/sandbox.sh mac setup"
  tart clone "$1" "$CLONE" || die "could not clone $1"
}

# <ref> as the branch a new Mac installs from, in a git bundle of its own.
bundle_ref() { # <git ref> <file>
  git -C "$ROOT" branch -q -f --no-track "$TREE_BRANCH" "$1" || return 1
  git -C "$ROOT" bundle create -q "$2" "$TREE_BRANCH" 2>/dev/null
}

copy_checkout() { # [<name of the bundle in the VM>]
  bundle_checkout "$WORK/repo.bundle" || die "could not bundle this checkout"
  to_vm "$CLONE" "$WORK/repo.bundle" "${1:-repo.bundle}" || die "could not copy the checkout"
  COPYFILE_DISABLE=1 tar -C "$ROOT" -czf "$WORK/e2e.tgz" get.sh tests/install_e2e.sh tests/work_e2e.sh tests/app_e2e.sh \
    tests/sandbox/e2e_lib.sh tests/sandbox/e2e_parts.sh tests/sandbox/update_quit_probe.sh tests/sandbox/acme_week.sh tests/sandbox/claude-stand-in \
    tests/sandbox/gh-stand-in tests/sandbox/drive.py tests/sandbox/live.sh tests/sandbox/live_agy.sh tests/sandbox/live \
    tests/sandbox/mcp_probe_server.py \
    tests/sandbox/acme_year.py tests/fixtures/knowledge/acme-import.jsonl
  to_vm "$CLONE" "$WORK/e2e.tgz" e2e.tgz || die "could not copy the test"
  in_vm "$CLONE" 'mkdir -p e2e && tar -xzf e2e.tgz -C e2e' || die "could not unpack the test"
}

# This checkout as the head of `main` of a repository that has made a release (RUN_RELEASE), packed in a tar for the
# VM (~/origin.git there): what a first install takes on the default `releases` channel, and what the next release
# (tests/install_e2e.sh makes it) is tagged on. tests/sandbox/release_origin.sh makes it, as CI's install job does.
release_origin() { # <tar file>
  "$ROOT/tests/sandbox/release_origin.sh" "$ROOT" "$TREE_BRANCH" "$WORK/origin.git" "$RUN_RELEASE" &&
    COPYFILE_DISABLE=1 tar -C "$WORK" -czf "$1" origin.git
}

# The origin the install takes from has a release: no BOSUN_BRANCH, so get.sh takes the newest release and the
# install follows the releases, as a user's does (copy_checkout's bundle, a branch with no release, is for the rest).
copy_release_origin() {
  release_origin "$WORK/origin.tgz" || die "could not make the release origin"
  to_vm "$CLONE" "$WORK/origin.tgz" origin.tgz || die "could not copy the release origin"
  in_vm "$CLONE" 'tar -xzf origin.tgz' || die "could not unpack the release origin"
}

run() { # [--from-nothing]
  local base="$DEV_MAC"
  case "$#:${1:-}" in
    0:) ;;
    1:--from-nothing) base="$NEW_MAC" ;;
    *) die "usage: tests/sandbox.sh mac run [--from-nothing] [<parts>]" ;;
  esac
  chosen_parts run
  prepare_clone "$base"
  say "booting a clone of $base"
  boot "$CLONE" --no-graphics
  copy_checkout
  copy_release_origin
  # shellcheck disable=SC2016 # expands in the VM
  in_vm "$CLONE" "$(vm_env) "'BOSUN_REPO="$HOME/origin.git" BOSUN_E2E_RELEASE='"$RUN_RELEASE"' e2e/tests/install_e2e.sh'
}

# Waits until the clone is back at its desktop (the auto-login), with a boot time other than <before> when one is
# given. Whether tart's VM process ends with a guest's restart or goes on, and when, is not assumed: on every turn of
# the wait, a VM process that has ended is waited for and the clone started again, with <tart run flags>.
await_the_clone() { # <boot time it must differ from, or ""> [tart run flags...]
  local before="$1" deadline=$((SECONDS + 2 * BOOT_SECONDS)) now
  shift
  until now="$(in_vm "$CLONE" 'sysctl -n kern.boottime' 2>/dev/null)" && [ "$now" != "$before" ] && logged_in "$CLONE"; do
    [ "$SECONDS" -ge "$deadline" ] && die "the clone did not come back to its desktop in $((2 * BOOT_SECONDS)) seconds"
    if ! kill -0 "$VM_PID" 2>/dev/null; then
      wait "$VM_PID" 2>/dev/null
      say "tart's VM process ended with the restart: starting the clone again"
      boot "$CLONE" "$@"
    fi
    sleep 3
  done
}

# The clone restarted, as a Mac is: shutdown -r in the guest, then back at the desktop with a new boot time.
reboot_clone() {
  local before
  before="$(in_vm "$CLONE" 'sysctl -n kern.boottime')" || die "could not read the clone's boot time"
  say "restarting the clone (sudo shutdown -r now in the guest)"
  in_vm "$CLONE" 'sudo shutdown -r now' >/dev/null 2>&1
  await_the_clone "$before" --no-graphics
  say "the clone is back from its restart"
}

# bosun.app, built here with app/build.sh (ad hoc: no Team ID or certificate is needed to build, and the app is
# never started on this Mac, only in the clone) and copied in beside the test, or with --from release left to
# `bosun init`, which downloads the published one. The clone has its desktop session (the auto-login: the
# status item needs one), so launchd's gui domain exists, which boot waits for. tests/app_e2e.sh runs in two
# halves with the restart between them, when the run has its part `reboot`.
app() { # [--from build|release]
  local from="build" built="$WORK/app-built" run_env status=0
  case "$#:${1:-}" in
    0:) ;;
    2:--from) from="$2" ;;
    *) die "usage: tests/sandbox.sh mac app [--from build|release] [<parts>]" ;;
  esac
  case "$from" in
    build | release) ;;
    *) die "unknown source '$from': build or release" ;;
  esac
  chosen_parts app
  if [ "$from" = "build" ]; then
    say "building bosun.app with app/build.sh (ad hoc signed; a cold build takes minutes)"
    mkdir -p "$built"
    "$ROOT/app/build.sh" --identity - --out "$built" >"$WORK/app-build.log" 2>&1 ||
      { tail -20 "$WORK/app-build.log" && die "app/build.sh failed"; }
  fi
  prepare_clone "$DEV_MAC"
  say "booting a clone of $DEV_MAC, with its desktop session, to drive bosun.app (from $from)"
  boot "$CLONE" --no-graphics
  copy_checkout
  copy_release_origin
  if [ "$from" = "build" ]; then
    to_vm "$CLONE" "$(ls "$built"/bosun-app-*.zip)" e2e/app.zip || die "could not copy the app"
  fi
  # shellcheck disable=SC2016 # expands in the VM
  run_env="$(vm_env) "'BOSUN_REPO="$HOME/origin.git" BOSUN_E2E_RELEASE='"$RUN_RELEASE BOSUN_E2E_APP_FROM=$from"
  in_vm "$CLONE" "$run_env e2e/tests/app_e2e.sh before-reboot" || status=1
  part_runs reboot || return "$status" # without the restart, the half before ends with the summary
  app_after_the_restart "$run_env" "$status"
}

# The part `reboot`: the clone restarted once the half before has kept its counts, then the half after.
app_after_the_restart() { # <run env> <the half before's status>
  if ! in_vm "$CLONE" 'test -e ~/.app-e2e-results'; then
    [ "$EXITFIRST" = 1 ] && [ "$2" = 1 ] && say "no restart: -x stopped the run before it" && return 1
    die "the first half of the test did not finish: no restart"
  fi
  reboot_clone
  in_vm "$CLONE" "$1 e2e/tests/app_e2e.sh after-reboot" || return 1
  return "$2"
}

# This checkout, installed by its own get.sh from its origin (~/repo.bundle, which the suite makes from
# ~/repo-tree.bundle), and the older bosun (<ref>, an ancestor of this checkout) as a bundle of its own.
work() { # [--base <git ref>]
  local base="$OLDER_BOSUN"
  case "$#:${1:-}" in
    0:) ;;
    2:--base) base="$2" ;;
    *) die "usage: tests/sandbox.sh mac work [--base <git ref>] [<parts>]" ;;
  esac
  chosen_parts work
  git -C "$ROOT" merge-base --is-ancestor "$base" HEAD ||
    die "$base is not an ancestor of this checkout: the older bosun is one of this branch's"
  prepare_clone "$DEV_MAC"
  say "booting a clone of $DEV_MAC, for a week of work on this checkout (the older bosun: $base)"
  boot "$CLONE" --no-graphics
  bundle_ref "$base" "$WORK/repo-base.bundle" || die "could not bundle $base"
  to_vm "$CLONE" "$WORK/repo-base.bundle" repo-base.bundle || die "could not copy $base"
  copy_checkout repo-tree.bundle
  # shellcheck disable=SC2016 # expands in the VM
  in_vm "$CLONE" "$(vm_env) "'BOSUN_REPO="$HOME/repo.bundle" BOSUN_BRANCH='"$TREE_BRANCH"' e2e/tests/work_e2e.sh'
}

need_key() { # <Keychain item>: without -w, this asks whether it is there and not for what it holds
  security find-generic-password -s "$1" >/dev/null 2>&1 ||
    die "no key in your Keychain as $1. How to store one: docs/development.md"
}

# A key only ever goes through a pipe, so it is never in a variable, an argument or a screen, and goes into
# a file only the VM's user can read: what apiKeyHelper reads, or Codex's own login. Anything coming back
# that looks like a key is masked.
keys_into_vm() { # <agent>
  # shellcheck disable=SC2016 # expands in the VM
  security find-generic-password -s "$ANTHROPIC_ITEM" -w | in_vm "$CLONE" 'umask 077; mkdir -p ~/.config/sandbox ~/.claude
    cat >~/.config/sandbox/anthropic-key
    printf "{\"apiKeyHelper\": \"cat %s/.config/sandbox/anthropic-key\"}\n" "$HOME" >~/.claude/settings.json' ||
    die "could not put the Anthropic key in the VM"
  [ "$1" = "codex" ] || return 0
  security find-generic-password -s "$OPENAI_ITEM" -w | in_vm "$CLONE" '/opt/homebrew/bin/codex login --with-api-key >/dev/null 2>&1' ||
    die "could not log Codex in"
}

in_vm_masked() { # <live.sh step> <agent> [model]; VM_ENV: what goes before it (vm_env, for `go`)
  in_vm "$CLONE" "${VM_ENV:-}${VM_ENV:+ }BOSUN_BRANCH=$TREE_BRANCH e2e/tests/sandbox/live.sh $*" </dev/null | sed -l -E -e "$KEY_SHAPE" -e "$TOKEN_SHAPE"
  return "${PIPESTATUS[0]}"
}

live() { # [claude|codex|agy]
  local agent="${1:-claude}"
  case "$#:$agent" in
    [01]:claude | [01]:codex | [01]:agy) ;;
    *) die "usage: tests/sandbox.sh mac live [claude|codex|agy] [<parts>] [--paid]" ;;
  esac
  if [ "$agent" = "agy" ]; then
    # --list too: it would otherwise be ignored here, and a run that was asked only to list would start.
    [ -z "$ONLY$CHANGED" ] && [ "$LIST" = 0 ] || die "live agy runs whole: its steps need a person, and it has no parts"
  else
    chosen_parts live
    [ -z "$CHANGED" ] || [ "$PAID" = 1 ] ||
      { say "live is paid (the real agents' API calls): add --paid to run those parts" && exit 0; }
  fi
  need_key "$ANTHROPIC_ITEM"
  [ "$agent" = "codex" ] && need_key "$OPENAI_ITEM"
  prepare_clone "$DEV_MAC"
  say "booting a clone of $DEV_MAC, for real $agent"
  if [ "$agent" = "agy" ]; then # a person signs in to it in the desktop session, so the VM has its window
    say "a window opens with the clone: you will be asked to sign in to Antigravity in it, in a few minutes"
    boot "$CLONE"
  else
    boot "$CLONE" --no-graphics
  fi
  copy_checkout
  in_vm_masked prepare "$agent" || die "preparing it failed"
  # The keys first: a failure of theirs, or a Keychain prompt on this Mac, never comes after the sign-in.
  keys_into_vm "$agent"
  [ "$agent" = "agy" ] && { in_vm_masked login agy || die "the sign-in to Antigravity did not finish"; }
  VM_ENV="$(vm_env)" in_vm_masked go "$agent" "${BOSUN_SANDBOX_CODEX_MODEL:-}"
}

# A clone with its window, and your shell in it, until you leave it. A restart in the clone (to try Open at Login,
# say) drops the connection, not the clone: the shell comes back once the clone is at its desktop again. Leaving
# the shell (exit, Ctrl-D) writes a mark in the clone, which a dropped connection never does; only then does this
# end, and delete the clone.
# The repository the releases are published in (get.sh's own), from app/publisher.json.
releases_repo() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["releases"])' "$ROOT/app/publisher.json" ||
    die "could not read the releases' repository from app/publisher.json"
}

# A clone of the developer's Mac with its window, logged in as admin, and this checkout in it. --no-graphics: no
# window (nobody is there to see it); the desktop session is there either way.
clone_for_a_person() { # [--no-graphics]
  prepare_clone "$DEV_MAC"
  if [ "${1:-}" = "--no-graphics" ]; then
    say "booting a clone of $DEV_MAC"
  else
    say "a window opens with a clone of $DEV_MAC, its desktop logged in as admin (password admin, for sudo)"
  fi
  boot "$CLONE" "$@"
  copy_checkout
}

shell() {
  local repo
  repo="$(releases_repo)" || exit 1
  clone_for_a_person
  person_shell "$(
    cat <<WELCOME

A throwaway Mac, deleted when you leave this shell (exit). A restart in it keeps it: this shell comes back once
its desktop is back. To try bosun as a person installs it, in Terminal in the window or here:

  the published release:  curl -fsSL https://raw.githubusercontent.com/$repo/main/get.sh | sh
  this checkout instead:  BOSUN_REPO=~/repo.bundle BOSUN_BRANCH=$TREE_BRANCH sh e2e/get.sh

The wizard offers your repos: clone one first (git clone https://github.com/$repo ~/Development/bosun).

WELCOME
  )"
}

# What `shell` and `try` end with: <welcome>, then your shell in the clone until you leave it. A restart in the
# clone (to try Open at Login, say) drops the connection, not the clone: the shell comes back once the clone is at
# its desktop again. Leaving the shell (exit, Ctrl-D) writes a mark in the clone, which a dropped connection never
# does; only then does this end, and the clone is deleted.
person_shell() {                 # <welcome>
  local left=.sandbox-shell-left # in the clone's home, where each ssh command starts
  printf '%s\n' "$1" | in_vm "$CLONE" "cat >.sandbox-welcome" || die "could not greet you in the clone"
  in_vm "$CLONE" -t "rm -f $left; cat .sandbox-welcome; zsh -l; touch $left"
  until in_vm "$CLONE" "test -e $left" 2>/dev/null; do
    say "the connection to the clone ended (a restart?): your shell comes back once its desktop is back. Ctrl-C deletes the clone"
    sleep 5
    await_the_clone ""
    in_vm "$CLONE" -t "rm -f $left; zsh -l; touch $left"
  done
}

# `shell` with bosun set up, running and signed in (see the header). The keys go in as for live: through a pipe,
# into a file only the clone's user can read, and the clone is deleted when you leave.
welcome_text() { # <from> <model>: what `mac try` tells a person (a function: bash 3.2 misparses this heredoc inside $( ))
  local from="$1" model="$2"
  cat <<WELCOME

A throwaway Mac with bosun running in it, from the $from, signed in with your Anthropic key (as its
apiKeyHelper), its agents on $model. The key is billed per request for as long as bosun runs: leaving
this shell (exit) deletes the clone, and \`bosun stop\` stops it sooner. A restart keeps it.

  bosun status          how it is (and the menu bar icon in the window)
  bosun attach          the windows: the orchestrator, and a session for each of the three repos
  bosun console         the web console

The repos (~/Development/exporter, pdf-renderer, ledger-sync) push to a local stand-in of GitHub, so nothing leaves the Mac
but requests to the agents' APIs. Remote Control needs a claude.ai login: it does not work with a key.

WELCOME
}

try() { # [--from release|checkout] [--run <script>] [claude|codex]
  local from=release agent=claude repo model="${BOSUN_SANDBOX_TRY_MODEL:-sonnet}" script=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --from)
        from="${2:-}"
        shift
        ;;
      --run)
        script="${2:-}"
        [ -f "$script" ] || die "--run takes a script file"
        shift
        ;;
      claude | codex) agent="$1" ;;
      *) die "unknown '$1': try [--from release|checkout] [--run <script>] [claude|codex]" ;;
    esac
    shift
  done
  case "$from" in release | checkout) ;; *) die "--from takes release or checkout" ;; esac
  need_key "$ANTHROPIC_ITEM"
  [ "$agent" = "codex" ] && need_key "$OPENAI_ITEM"
  repo="$(releases_repo)" || exit 1
  if [ -n "$script" ]; then clone_for_a_person --no-graphics; else clone_for_a_person; fi
  in_vm_masked prepare "$agent" || die "preparing it failed"
  keys_into_vm "$agent"
  in_vm_masked ready "$agent" "$from" "$model" "https://raw.githubusercontent.com/$repo/main/get.sh" ||
    die "setting bosun up failed"
  if [ -n "$script" ]; then
    say "running $script in the clone"
    # bosun's own line on the path is in ~/.zshrc, which a shell reading a script does not run
    # shellcheck disable=SC2016 # expands in the clone
    {
      printf 'export PATH="$HOME/.local/bin:$PATH"\n'
      cat "$script"
    } |
      in_vm "$CLONE" "zsh -l -s" 2>&1 | sed -l -E -e "$KEY_SHAPE" -e "$TOKEN_SHAPE"
    return "${PIPESTATUS[1]}" # the script's, through ssh
  fi
  person_shell "$(welcome_text "$from" "$model")"
}

clean() {
  need_tart
  tart list --source local --format json 2>/dev/null | python3 -c '
import json, sys
for vm in json.load(sys.stdin):
    if vm["Name"].startswith("bosun-mac-"):
        print(vm["Name"])' | while read -r vm; do
    tart stop "$vm" >/dev/null 2>&1
    tart delete "$vm" && say "deleted $vm"
  done
}

case "${1:-}" in
  setup) setup ;;
  run | work | app | live)
    suite="$1"
    shift
    selection_args "$@"
    "$suite" ${SUITE_ARGS[@]+"${SUITE_ARGS[@]}"}
    ;;
  --changed) # every suite's choice; it runs nothing
    selection_args "$@"
    [ -z "$ONLY" ] && [ -z "${SUITE_ARGS[*]-}" ] || die "usage: tests/sandbox.sh mac --changed <git ref> [--list]"
    python3 "$ROOT/tools/vm_select.py" --root "$ROOT" --changed "$CHANGED"
    ;;
  shell) shell ;;
  try)
    shift
    try "$@"
    ;;
  clean) clean ;;
  -h | --help) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0" ;;
  *) die "unknown '${1:-}' (see --help)" ;;
esac
