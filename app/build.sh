#!/bin/bash
# Usage: app/build.sh [--identity <name>|-] [--out <dir>]
#        app/build.sh --zip <bosun.app> [--out <dir>]
#
# Builds bosun.app (docs/design/menu-bar.md): `swift build -c release --arch arm64`, the bundle assembled in
# <dir>, signed, verified, and zipped as bosun-app-<version>.zip. `bosun app install --build` runs it with an
# ad hoc identity into a temporary folder; the release workflow with the publisher's Developer ID.
#
#   --identity   what `codesign --sign` takes: a Developer ID Application identity, or - (the default) for an
#                ad hoc signature, which is for developing the app only: macOS accepts it nowhere else. A real
#                identity also turns on the hardened runtime and a secure timestamp, which notarization needs
#   --out        the folder that gets bosun.app and the zip (default: app/dist, which git ignores)
#   --zip        only zip the bundle it names (the release workflow does so again after it staples the
#                notarization ticket), the way the build zips: the same flags, the same name, in --out
#
# The bundle identifier is app/publisher.json's bundle_id and the version is app/VERSION: nothing else names
# them. The signing identifier is the bundle identifier, since `bosun app install` accepts a build only when it
# satisfies the Developer ID requirement for that identifier. A bundle and a zip of an earlier build of the same
# version are removed first, and the new ones are built beside them and moved in only once the bundle is signed
# and verified: a failed build leaves neither, never an unsigned bundle. The compiler's output goes to stderr.
# It builds in this folder (app/.build), which an install made by get.sh keeps in its version's folder.
# Exit status: 0 built, 1 failed (one line saying why), 2 usage.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOURCES="$APP_DIR/Resources"
AD_HOC="-"
STAGING="" # the folder beside the output where a build is made; removed whatever happens

die() {
  echo "app/build.sh: $*" >&2
  exit 1
}

die_usage() {
  echo "app/build.sh: $*" >&2
  echo "Usage: app/build.sh [--identity <name>|-] [--out <dir>]" >&2
  echo "       app/build.sh --zip <bosun.app> [--out <dir>]" >&2
  exit 2
}

cleanup() {
  [ -z "$STAGING" ] || rm -rf "$STAGING"
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
  version="$(tr -d '[:space:]' <"$APP_DIR/VERSION")" || die "cannot read app/VERSION"
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
  mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources" || die "cannot make $bundle"
  cp "$executable" "$bundle/Contents/MacOS/bosun" || die "cannot copy the executable into the bundle"
  sed -e "s/@BUNDLE_ID@/$id/g" -e "s/@VERSION@/$version/g" "$RESOURCES/Info.plist.in" >"$bundle/Contents/Info.plist" ||
    die "cannot make the Info.plist"
  plutil -lint -s "$bundle/Contents/Info.plist" || die "the Info.plist it made is not a valid plist"
  iconutil -c icns "$RESOURCES/AppIcon.iconset" -o "$bundle/Contents/Resources/AppIcon.icns" || die "iconutil could not make AppIcon.icns"
  cp "$RESOURCES/MenuBarIcon.png" "$RESOURCES/MenuBarIcon@2x.png" "$bundle/Contents/Resources/" ||
    die "cannot copy the menu bar images into the bundle"
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

# make_zip <bundle> <zip>: the one place that zips, for the build and for --zip. Without the extended
# attributes of this Mac (provenance, quarantine), which ditto would otherwise add to the archive as `._` files
# beside every file of the bundle.
make_zip() {
  ditto -c -k --keepParent --norsrc --noextattr --noqtn "$1" "$2" || die "ditto could not make the zip"
}

# build <identity> <out> <id> <version>: the signed bundle and its zip, in <out>, once both are made.
build() {
  local identity="$1" out="$2" id="$3" version="$4" executable
  echo "app/build.sh: building bosun.app $version with swift (the first build can take minutes)" >&2
  executable="$(build_binary)"
  assemble "$executable" "$STAGING/bosun.app" "$id" "$version"
  sign "$STAGING/bosun.app" "$identity" "$id"
  make_zip "$STAGING/bosun.app" "$STAGING/bosun-app-$version.zip"
  mv "$STAGING/bosun.app" "$out/bosun.app" || die "cannot move the build into $out"
  mv "$STAGING/bosun-app-$version.zip" "$out/bosun-app-$version.zip" || die "cannot move the zip into $out"
  echo "built $out/bosun.app ($id $version, signed $([ "$identity" = "$AD_HOC" ] && echo "ad hoc" || echo "with $identity"))"
  echo "zip $out/bosun-app-$version.zip"
}

# zip_only <bundle> <out> <version>: <bundle> zipped as the build zips it.
zip_only() {
  local bundle="$1" out="$2" version="$3"
  [ -d "$bundle" ] && [ "$(basename "$bundle")" = bosun.app ] || die "--zip needs a bosun.app folder, not $bundle"
  make_zip "$bundle" "$STAGING/bosun-app-$version.zip"
  mv "$STAGING/bosun-app-$version.zip" "$out/bosun-app-$version.zip" || die "cannot move the zip into $out"
  echo "zip $out/bosun-app-$version.zip"
}

parse_arguments() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --identity | --out | --zip)
        [ $# -ge 2 ] && [ -n "$2" ] || die_usage "$1 needs a value"
        case "$1" in
          --identity) identity="$2" ;;
          --out) out="$2" ;;
          *) zip_bundle="$2" ;;
        esac
        shift 2
        ;;
      *) die_usage "unknown argument: $1" ;;
    esac
  done
  [ -z "$zip_bundle" ] || [ -z "$identity" ] || die_usage "--zip does not sign: --identity is for a build"
}

main() {
  local identity="" out="$APP_DIR/dist" zip_bundle="" id version
  parse_arguments "$@"
  version="$(app_version)"
  mkdir -p "$out" || die "cannot make $out"
  out="$(cd "$out" && pwd)"
  trap cleanup EXIT
  STAGING="$(mktemp -d "$out/.bosun-build.XXXXXX")" || die "cannot make a folder in $out"
  if [ -n "$zip_bundle" ]; then
    rm -f "$out/bosun-app-$version.zip"
    zip_only "$zip_bundle" "$out" "$version"
    return
  fi
  id="$(bundle_id)"
  rm -rf "$out/bosun.app" "$out/bosun-app-$version.zip"
  build "${identity:-$AD_HOC}" "$out" "$id" "$version"
}

main "$@"
