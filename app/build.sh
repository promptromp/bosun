#!/bin/bash
# Usage: app/build.sh [--identity <name>|-] [--out <dir>]
#
# Builds bosun.app (docs/design/menu-bar.md): `swift build -c release --arch arm64`, the bundle assembled in
# <dir>, signed, verified, and zipped as bosun-app-<version>.zip. `bosun app install --build` runs it with an
# ad hoc identity into a temporary folder; the release workflow with the publisher's Developer ID.
#
#   --identity   what `codesign --sign` takes: a Developer ID Application identity, or - (the default) for an
#                ad hoc signature, which is for developing the app only: macOS accepts it nowhere else. A real
#                identity also turns on the hardened runtime and a secure timestamp, which notarization needs
#   --out        the folder that gets bosun.app and the zip (default: app/dist, which git ignores)
#
# The bundle identifier is app/publisher.json's bundle_id and the version is app/VERSION: nothing else names
# them. The signing identifier is the bundle identifier, since `bosun app install` accepts a build only when it
# satisfies the Developer ID requirement for that identifier.
# Exit status: 0 built, 1 failed (one line saying why), 2 usage.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOURCES="$APP_DIR/Resources"
AD_HOC="-"

die() {
  echo "app/build.sh: $*" >&2
  exit 1
}

die_usage() {
  echo "app/build.sh: $*" >&2
  echo "Usage: app/build.sh [--identity <name>|-] [--out <dir>]" >&2
  exit 2
}

# The bundle identifier of app/publisher.json: Info.plist's text and codesign's identifier, so it must be
# one word of letters, digits, dots and dashes.
bundle_id() {
  local id
  id="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["bundle_id"])' "$APP_DIR/publisher.json")" ||
    die "cannot read bundle_id from app/publisher.json"
  [[ $id =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die "app/publisher.json's bundle_id is not a bundle identifier"
  echo "$id"
}

# The version of app/VERSION, in the form of a release: major.minor.patch.
app_version() {
  local version
  version="$(tr -d '[:space:]' <"$APP_DIR/VERSION")"
  [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "app/VERSION is not major.minor.patch"
  echo "$version"
}

# The release build for Apple silicon; the path of its executable.
build_binary() {
  (cd "$APP_DIR" && swift build -c release --arch arm64 >&2) || die "swift build failed"
  local bin_path
  bin_path="$(cd "$APP_DIR" && swift build -c release --arch arm64 --show-bin-path)" || die "swift build could not say where it built"
  [ -x "$bin_path/BosunMenu" ] || die "swift build left no BosunMenu in $bin_path"
  echo "$bin_path/BosunMenu"
}

# assemble <executable> <bundle> <bundle id> <version>: the bundle's files, not yet signed.
assemble() {
  local executable="$1" bundle="$2" id="$3" version="$4"
  rm -rf "$bundle"
  mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
  cp "$executable" "$bundle/Contents/MacOS/bosun"
  sed -e "s/@BUNDLE_ID@/$id/g" -e "s/@VERSION@/$version/g" "$RESOURCES/Info.plist.in" >"$bundle/Contents/Info.plist"
  plutil -lint -s "$bundle/Contents/Info.plist" || die "the Info.plist it made is not a valid plist"
  iconutil -c icns "$RESOURCES/AppIcon.iconset" -o "$bundle/Contents/Resources/AppIcon.icns" || die "iconutil could not make AppIcon.icns"
  cp "$RESOURCES/MenuBarIcon.png" "$RESOURCES/MenuBarIcon@2x.png" "$bundle/Contents/Resources/"
}

# sign <bundle> <identity> <bundle id>: the whole bundle, its signing identifier the bundle identifier; a real
# identity also gets the hardened runtime and a secure timestamp.
sign() {
  local bundle="$1" identity="$2" id="$3"
  local args=(--force --sign "$identity" --identifier "$id")
  [ "$identity" = "$AD_HOC" ] || args+=(--options runtime --timestamp)
  codesign "${args[@]}" "$bundle" || die "codesign failed"
  codesign --verify --deep --strict "$bundle" || die "the signature does not verify"
}

parse_arguments() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --identity | --out)
        [ $# -ge 2 ] && [ -n "$2" ] || die_usage "$1 needs a value"
        if [ "$1" = --identity ]; then identity="$2"; else out="$2"; fi
        shift 2
        ;;
      *) die_usage "unknown argument: $1" ;;
    esac
  done
}

main() {
  local identity="$AD_HOC" out="$APP_DIR/dist" id version executable bundle zip
  parse_arguments "$@"
  id="$(bundle_id)"
  version="$(app_version)"
  mkdir -p "$out" || die "cannot make $out"
  out="$(cd "$out" && pwd)"
  bundle="$out/bosun.app"
  zip="$out/bosun-app-$version.zip"
  executable="$(build_binary)"
  assemble "$executable" "$bundle" "$id" "$version"
  sign "$bundle" "$identity" "$id"
  rm -f "$zip"
  # Without the extended attributes of this Mac (provenance, quarantine), which ditto would otherwise add to
  # the archive as `._` files beside every file of the bundle.
  ditto -c -k --keepParent --norsrc --noextattr --noqtn "$bundle" "$zip" || die "ditto could not make the zip"
  echo "built $bundle ($id $version, signed $([ "$identity" = "$AD_HOC" ] && echo "ad hoc" || echo "with $identity"))"
  echo "zip $zip"
}

main "$@"
