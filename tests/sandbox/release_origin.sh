#!/bin/bash
# Usage: tests/sandbox/release_origin.sh <checkout> <ref> <bare repository to make> <release tag>
#
# The origin a first install takes on the default `releases` channel: <ref> of <checkout> as the head of `main` of a
# new bare repository, with one release, <release tag>, an annotated tag with notes in its message.
# tests/install_e2e.sh's `releases` part tags the next release there, and tests/app_e2e.sh's update does too.
# tests/sandbox/mac.sh makes it for the VM (packed in a tar, ~/origin.git there), and CI's install job on its macOS
# runner (.github/workflows/install.yml), each passing BOSUN_REPO=~/origin.git and BOSUN_E2E_RELEASE=<release tag>.
# A repository and not a bundle: `git ls-remote` of a bundle has no peeled line for an annotated tag, as GitHub has.
# No tags come with the fetch: the checkout's history holds the project's real releases, and a newer one would be
# the install's. It needs only git, and changes nothing but the new repository.
set -euo pipefail

[ "$#" = 4 ] || {
  sed -n '2p' "$0" | sed 's/^# //' >&2
  exit 2
}
checkout="$1" ref="$2" origin="$3" tag="$4"
[ ! -e "$origin" ] || {
  echo "release_origin.sh: $origin is there already" >&2
  exit 1
}
git init -q --bare "$origin"
git -C "$origin" fetch -q --no-tags "$checkout" "$ref:refs/heads/main"
git -C "$origin" -c user.name=sandbox -c user.email=sandbox@example.com tag -a "$tag" -m "The first release" refs/heads/main
