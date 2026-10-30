#!/bin/bash
# Usage: tests/sandbox/mac.sh setup | run [--from-nothing] | shell | clean   (through tests/sandbox.sh mac)
#
# A real, throwaway Mac: macOS in a Tart VM on this machine, for what the Docker sandbox cannot show.
# Each run clones a base image, which takes no time and no disk, and deletes the clone afterwards.
#   setup   pull the image (about 29 GB) and prepare the two base images: bosun-mac-new, a new Mac (no
#           Homebrew, no Apple tools), and bosun-mac-dev, a developer's Mac (the same with Homebrew)
#   run     on a clone of the developer's Mac: this checkout's installer, real launchd, a real wake
#           schedule and uninstall (tests/install_e2e.sh). --from-nothing: on the new Mac instead
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
VM_PID=""

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
  -o PreferredAuthentications=password,keyboard-interactive)
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
  # ssh comes up before the auto-login, and launchd has no gui domain until then.
  # shellcheck disable=SC2016 # expands in the VM
  until in_vm "$vm" 'launchctl print "gui/$(id -u)"' >/dev/null 2>&1; do
    [ "$SECONDS" -ge "$deadline" ] && die "$vm did not finish starting"
    sleep 2
  done
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

copy_checkout() {
  bundle_checkout "$WORK/repo.bundle" || die "could not bundle this checkout"
  to_vm "$CLONE" "$WORK/repo.bundle" repo.bundle || die "could not copy the checkout"
  COPYFILE_DISABLE=1 tar -C "$ROOT" -czf "$WORK/e2e.tgz" get.sh tests/install_e2e.sh tests/sandbox/claude-stand-in \
    tests/sandbox/gh-stand-in
  to_vm "$CLONE" "$WORK/e2e.tgz" e2e.tgz || die "could not copy the test"
  in_vm "$CLONE" 'mkdir -p e2e && tar -xzf e2e.tgz -C e2e' || die "could not unpack the test"
}

run() { # [--from-nothing]
  local base="$DEV_MAC"
  [ "${1:-}" = "--from-nothing" ] && base="$NEW_MAC"
  prepare_clone "$base"
  say "booting a clone of $base"
  boot "$CLONE" --no-graphics
  copy_checkout
  # shellcheck disable=SC2016 # expands in the VM
  in_vm "$CLONE" 'BOSUN_REPO="$HOME/repo.bundle" BOSUN_BRANCH='"$TREE_BRANCH"' e2e/tests/install_e2e.sh'
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
  shell) shell ;;
  clean) clean ;;
  -h | --help) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0" ;;
  *) die "unknown '${1:-}' (see --help)" ;;
esac
