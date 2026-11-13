#!/bin/bash
# Usage: tests/sandbox/mac.sh setup | run [--from-nothing] | work [--base <git ref>]
#        | app [--from build|release] | live [claude|codex|agy] | shell | clean
#        (through tests/sandbox.sh mac)
#
# A real, throwaway Mac: macOS in a Tart VM on this machine, for what the Docker sandbox cannot show.
# Each run clones a base image, which takes no time and no disk, and deletes the clone afterwards.
#   setup   pull the image (about 29 GB) and prepare the two base images: bosun-mac-new, a new Mac (no
#           Homebrew, no Apple tools), and bosun-mac-dev, a developer's Mac (the same with Homebrew)
#   run     on a clone of the developer's Mac: this checkout's installer on the default releases (its origin
#           has a release tag), real launchd, the console's job, `bosun quit`, an update to the next release,
#           a real wake schedule and uninstall (tests/install_e2e.sh). --from-nothing: on the new Mac instead
#   work    on a clone of the developer's Mac: a week of real work that installs the release (--base,
#           default the last bosun from before versions and the knowledge base's journal), converts it to
#           this checkout by running get.sh again, updates mid-afternoon to the next version on a year of
#           data, goes back and forward again, and keeps working with the clock moved, writers racing and
#           writes cut short (tests/work_e2e.sh)
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
#           live agy needs you: its login is a browser sign-in, in the desktop session. The clone boots with
#           its window, agy is installed with Google's installer, and you are told to open Terminal there,
#           run ~/.local/bin/agy, sign in with Google (a personal account's free quota) and quit it; the run
#           waits up to 15 minutes for that. The orchestrator stays on Claude Code (the Anthropic key only).
#           Your sign-in stays in the clone, which is deleted at the end; no image is ever saved
#   shell   a clone with a window, to try things as a new user would; deleted when you exit
#   clean   delete the clones and base images (the pulled image stays: `tart prune` removes it)
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
# The release `work` starts from: the last bosun from before versions side by side and the knowledge
# base's journal (#17), so its first update converts its checkout, and its knowledge base starts a journal.
# (A commit id, not a secret.)
RELEASE="95545b0ba6a0e3729e980aa9468876e1228c1874" # pragma: allowlist secret
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

boot() { # <vm> [tart run flags...]: start it and wait for ssh
  local vm="$1" deadline=$((SECONDS + BOOT_SECONDS))
  shift
  tart run "$@" "$vm" >"$WORK/run.log" 2>&1 &
  VM_PID=$!
  tart ip "$vm" --wait "$BOOT_SECONDS" >/dev/null 2>&1 || die "$vm got no IP address"
  until logged_in "$vm"; do
    [ "$SECONDS" -ge "$deadline" ] && die "$vm did not finish starting"
    sleep 2
  done
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
    tests/sandbox/e2e_lib.sh tests/sandbox/update_quit_probe.sh tests/sandbox/acme_week.sh tests/sandbox/claude-stand-in \
    tests/sandbox/gh-stand-in tests/sandbox/drive.py tests/sandbox/live.sh tests/sandbox/live_agy.sh tests/sandbox/live \
    tests/sandbox/mcp_probe_server.py \
    tests/sandbox/acme_year.py tests/fixtures/knowledge/acme-import.jsonl
  to_vm "$CLONE" "$WORK/e2e.tgz" e2e.tgz || die "could not copy the test"
  in_vm "$CLONE" 'mkdir -p e2e && tar -xzf e2e.tgz -C e2e' || die "could not unpack the test"
}

# This checkout as the head of `main` of a repository that has made a release (RUN_RELEASE, an annotated tag with
# notes in its message), a bare repository packed in a tar for the VM (~/origin.git there): what a first install
# takes on the default `releases` channel, and what the next release (tests/install_e2e.sh makes it) is tagged on.
# A repository and not a bundle: `git ls-remote` of a bundle has no peeled line for an annotated tag, as GitHub has. No tags
# come with the fetch: this checkout's history holds the project's real releases, and a newer one would be the install's.
release_origin() { # <tar file>
  local scratch="$WORK/origin.git"
  git init -q --bare "$scratch" &&
    git -C "$scratch" fetch -q --no-tags "$ROOT" "$TREE_BRANCH:refs/heads/main" &&
    git -C "$scratch" -c user.name=sandbox -c user.email=sandbox@example.com tag -a "$RUN_RELEASE" -m "The first release" refs/heads/main &&
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
  [ "${1:-}" = "--from-nothing" ] && base="$NEW_MAC"
  prepare_clone "$base"
  say "booting a clone of $base"
  boot "$CLONE" --no-graphics
  copy_checkout
  copy_release_origin
  # shellcheck disable=SC2016 # expands in the VM
  in_vm "$CLONE" 'BOSUN_REPO="$HOME/origin.git" BOSUN_E2E_RELEASE='"$RUN_RELEASE"' e2e/tests/install_e2e.sh'
}

# The clone restarted, as a Mac is: shutdown -r in the guest, then back at the desktop (the auto-login) with a
# new boot time. Whether tart's VM process ends with the guest's restart or goes on is not assumed: if it ends, the
# clone is started again; either way the new boot is awaited by sysctl kern.boottime.
reboot_clone() {
  local before deadline=$((SECONDS + 2 * BOOT_SECONDS)) now
  before="$(in_vm "$CLONE" 'sysctl -n kern.boottime')" || die "could not read the clone's boot time"
  say "restarting the clone (sudo shutdown -r now in the guest)"
  in_vm "$CLONE" 'sudo shutdown -r now' >/dev/null 2>&1
  sleep 30
  if ! kill -0 "$VM_PID" 2>/dev/null; then
    wait "$VM_PID" 2>/dev/null
    say "tart's VM process ended with the restart: starting the clone again"
    boot "$CLONE" --no-graphics
  fi
  until now="$(in_vm "$CLONE" 'sysctl -n kern.boottime' 2>/dev/null)" && [ "$now" != "$before" ] && logged_in "$CLONE"; do
    [ "$SECONDS" -ge "$deadline" ] && die "the clone did not come back after the restart"
    sleep 3
  done
  say "the clone is back from its restart"
}

# bosun.app, built here with app/build.sh (ad hoc: no Team ID or certificate is needed to build, and the app is
# never started on this Mac, only in the clone) and copied in beside the test, or with --from release left to
# `bosun init`, which downloads the published one. The clone has its desktop session (the auto-login: the
# status item needs one), so launchd's gui domain exists, which boot waits for. tests/app_e2e.sh runs in two
# parts with the restart between them.
app() { # [--from build|release]
  local from="build" built="$WORK/app-built" run_env part status=0
  case "$#:${1:-}" in
    0:) ;;
    2:--from) from="$2" ;;
    *) die "usage: tests/sandbox.sh mac app [--from build|release]" ;;
  esac
  case "$from" in
    build | release) ;;
    *) die "unknown source '$from': build or release" ;;
  esac
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
  run_env='BOSUN_REPO="$HOME/origin.git" BOSUN_E2E_RELEASE='"$RUN_RELEASE BOSUN_E2E_APP_FROM=$from"
  for part in before-reboot after-reboot; do
    in_vm "$CLONE" "$run_env e2e/tests/app_e2e.sh $part" || status=1
    [ "$part" = "before-reboot" ] || break
    in_vm "$CLONE" 'test -e ~/.app-e2e-results' || die "the first part of the test did not finish: no restart"
    reboot_clone
  done
  return "$status"
}

# The release (<ref>) installed by its own get.sh, then this checkout as its upgrade: two bundles of the
# same branch, the second a descendant of the first, as a branch's next head is. This checkout's get.sh,
# run then, converts the release's checkout, fetching the head from its origin (~/repo.bundle).
work() { # [--base <git ref>]
  local base="$RELEASE"
  case "$#:${1:-}" in
    0:) ;;
    2:--base) base="$2" ;;
    *) die "usage: tests/sandbox.sh mac work [--base <git ref>]" ;;
  esac
  git -C "$ROOT" merge-base --is-ancestor "$base" HEAD ||
    die "$base is not an ancestor of this checkout, as a release is of the branch's next head"
  prepare_clone "$DEV_MAC"
  say "booting a clone of $DEV_MAC, for a week of work from $base to this checkout"
  boot "$CLONE" --no-graphics
  bundle_ref "$base" "$WORK/repo-base.bundle" || die "could not bundle $base"
  to_vm "$CLONE" "$WORK/repo-base.bundle" repo-base.bundle || die "could not copy $base"
  # The release's own installer, as a user ran it then: it installs the release as that release did.
  git -C "$ROOT" show "$base:get.sh" >"$WORK/release-get.sh" || die "$base has no get.sh"
  to_vm "$CLONE" "$WORK/release-get.sh" release-get.sh || die "could not copy the release's get.sh"
  copy_checkout repo-tree.bundle
  # shellcheck disable=SC2016 # expands in the VM
  in_vm "$CLONE" 'BOSUN_REPO="$HOME/repo.bundle" BOSUN_BRANCH='"$TREE_BRANCH"' e2e/tests/work_e2e.sh'
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

in_vm_masked() { # <live.sh step> <agent> [model]
  in_vm "$CLONE" "BOSUN_BRANCH=$TREE_BRANCH e2e/tests/sandbox/live.sh $*" </dev/null | sed -l -E -e "$KEY_SHAPE" -e "$TOKEN_SHAPE"
  return "${PIPESTATUS[0]}"
}

live() { # [claude|codex|agy]
  local agent="${1:-claude}"
  case "$agent" in
    claude | codex | agy) ;;
    *) die "unknown agent '$agent': claude, codex or agy" ;;
  esac
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
  in_vm_masked go "$agent" "${BOSUN_SANDBOX_CODEX_MODEL:-}"
}

shell() {
  prepare_clone "$DEV_MAC"
  say "a window opens with a clone of $DEV_MAC. Log in as admin, password admin."
  boot "$CLONE"
  copy_checkout
  in_vm "$CLONE" -t 'cat <<WELCOME

A throwaway Mac. This checkout is ~/repo.bundle. To try the installer as a new user would:
  BOSUN_REPO=~/repo.bundle BOSUN_BRANCH='"$TREE_BRANCH"' sh e2e/get.sh
It is all deleted when you leave this shell.

WELCOME
exec zsh -l'
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
  run)
    shift
    run "$@"
    ;;
  work)
    shift
    work "$@"
    ;;
  app)
    shift
    app "$@"
    ;;
  live)
    shift
    live "$@"
    ;;
  shell) shell ;;
  clean) clean ;;
  -h | --help) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0" ;;
  *) die "unknown '${1:-}' (see --help)" ;;
esac
