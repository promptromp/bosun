#!/bin/bash
# A new machine's install, for real: the piped installer with its real Homebrew, real launchd jobs that
# really fire, the real pmset and plutil, and a clean uninstall. Claude Code is a stand-in, since these
# machines have no login. It changes the machine it runs on, so it only runs on a GitHub macOS runner
# (.github/workflows/install.yml) or in the throwaway Mac of tests/sandbox.sh mac.
# Checks pass shell snippets to `bash -c '...' _ args`; their $ must not expand here.
# shellcheck disable=SC2016
set -uo pipefail
# The guard (it refuses to run anywhere else), the checks and the steps tests/work_e2e.sh shares.
source "$(dirname "$0")/sandbox/e2e_lib.sh" || exit 1

backups() { find "$CONFIG" -maxdepth 1 -name '*.pre-init-*' | wc -l | tr -d ' '; }

# A first install, unattended: nobody chose a window or a connector, so there are none, and it says so.
nothing_chosen() {
  check "on a first install, no repo window, since nobody chose one" grep -qx "sessions: \[\]" "$CONFIG/sessions.yml"
  check "... nor connectors for the orchestrator" grep -qx 'BOSUN_ORCHESTRATOR_CONNECTORS=""' "$CONFIG/bosun.env"
  check "... and it says how to add them" grep -q "No repo windows or connectors were set up" "$OUT/install.txt"
}

run_again() {
  echo "a re-run"
  local before after kept
  before="$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  kept="$(backups)" # sessions.yml's, from when the windows were named
  # shellcheck disable=SC2002
  cat "$ROOT/get.sh" | sh -s -- --unattended >"$OUT/again.txt" 2>&1
  check "it updates and passes again" test "$?" = 0
  after="$(cat "$CONFIG"/bosun.env "$CONFIG"/sessions.yml "$CONFIG"/setup.sh | cksum)"
  check "the same answers change nothing" test "$before" = "$after"
  check "... and back nothing up" test "$kept" = "$(backups)"
}

# What app/build.sh asks of the tools, for real, on the ad hoc bundle <app> in the folder <work>.
app_build_tools() {
  local work="$1" app="$2"
  check "iconutil makes an icns of the app's committed icon set" bash -c 'iconutil -c icns "$1/app/Resources/AppIcon.iconset" -o "$2/AppIcon.icns" && [ "$(head -c 4 "$2/AppIcon.icns")" = icns ]' _ "$ROOT" "$work"
  check "plutil -lint accepts the Info.plist app/build.sh makes from its template" bash -c 'sed -e "s/@BUNDLE_ID@/ai.example.bosun/g" -e "s/@VERSION@/0.0.1/g" "$1/app/Resources/Info.plist.in" >"$2/Info.plist" && plutil -lint -s "$2/Info.plist"' _ "$ROOT" "$work"
  check "codesign --identifier makes the signing identifier, which the Developer ID requirement names (not the Info.plist's)" bash -c '
    cp -R "$1" "$2/named.app" && codesign --force --sign - --identifier ai.example.named "$2/named.app" &&
      codesign --verify --deep --strict -R "=identifier \"ai.example.named\"" "$2/named.app" &&
      ! codesign --verify --deep --strict -R "=identifier \"ai.example.bosun\"" "$2/named.app" 2>/dev/null' _ "$app" "$work"
  check "ditto --norsrc --noextattr --noqtn writes no ._ file for an attribute of this Mac" bash -c '
    xattr -w com.example.test 1 "$1/Contents/Info.plist" && ditto -c -k --keepParent --norsrc --noextattr --noqtn "$1" "$2/plain.zip" &&
      ! unzip -l "$2/plain.zip" | grep -qF "._"' _ "$app" "$work"
  check "app/build.sh builds the app for real, ad hoc, for arm64" bash -c '
    cp -R "$1/app" "$2/app-copy" && rm -rf "$2/app-copy/.build" "$2/app-copy/dist" &&
      "$2/app-copy/build.sh" --identity - --out "$2/built" >"$2/build.log" 2>&1 || { tail -5 "$2/build.log"; exit 1; }
    [ "$(lipo -archs "$2/built/bosun.app/Contents/MacOS/bosun")" = arm64 ]' _ "$ROOT" "$work"
  check "... the bundle verifies, is signed as the bundle id of app/publisher.json and is not notarized" bash -c '
    id="$(jq -r .bundle_id "$1/app/publisher.json")" && codesign --verify --deep --strict -R "=identifier \"$id\"" "$2/built/bosun.app" &&
      plutil -lint -s "$2/built/bosun.app/Contents/Info.plist" &&
      ! spctl --assess --type execute -vv "$2/built/bosun.app" 2>&1 | grep -qx "source=Notarized Developer ID"' _ "$ROOT" "$work"
  check "... and its zip unpacks with ditto into one signed bosun.app" bash -c '
    mkdir "$2/from-build" && ditto -x -k "$2"/built/bosun-app-*.zip "$2/from-build" && [ "$(ls "$2/from-build")" = bosun.app ] &&
      codesign --verify --deep --strict "$2/from-build/bosun.app"' _ "$ROOT" "$work"
}

# The tools `bosun app` runs (docs/design/menu-bar.md), for real, on a bundle made here. No Developer ID is on
# this Mac, so it is signed ad hoc: the two things macOS says of such an app (no team, not notarized) are the
# refusals bosun's checks rest on. The notarized case is the real release, which needs the publisher's Team ID.
app_tools() {
  local work app bosun="$HOME/.local/bin/bosun"
  work="$(mktemp -d)"
  app="$work/bosun.app"
  echo "the tools bosun app runs, for real"
  mkdir -p "$app/Contents/MacOS"
  printf '#!/bin/sh\nexit 0\n' >"$app/Contents/MacOS/bosun"
  chmod +x "$app/Contents/MacOS/bosun"
  cat >"$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>bosun</string>
  <key>CFBundleIdentifier</key><string>ai.example.bosun</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.0.1</string>
</dict></plist>
PLIST
  check "codesign signs a bundle ad hoc" codesign --force --sign - "$app"
  check "codesign --verify --deep --strict passes on it" codesign --verify --deep --strict "$app"
  # The Developer ID requirement bosun builds (lib/app_release.py requirement), for a made-up Team ID: text
  # introduced by = (`-R =<text>`; without the =, codesign takes it for a file name).
  local requirement='anchor apple generic and identifier "ai.example.bosun" and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ and certificate leaf[subject.OU] = "ABCDE12345"' # pragma: allowlist secret gitleaks:allow
  check "codesign accepts a requirement an ad hoc bundle meets, with -R and the = (exit 0)" codesign --verify --deep --strict -R '=identifier "ai.example.bosun"' "$app"
  check "... and fails one it does not meet with exit 3" bash -c 'codesign --verify --deep --strict -R "=identifier \"other\"" "$1" 2>/dev/null; [ $? = 3 ]' _ "$app"
  check "the Developer ID requirement refuses an ad hoc bundle, with exit 3" bash -c 'codesign --verify --deep --strict -R "=$1" "$2" 2>/dev/null; [ $? = 3 ]' _ "$requirement" "$app"
  check "... and a requirement codesign cannot read fails too, rather than passing" bash -c '! codesign --verify --deep --strict -R "=anchor apple generic and (" "$1" 2>/dev/null' _ "$app"
  # A signer can put a line that looks like a Team ID in the identifier (codesign takes a newline in it) ahead of
  # the real one in what `codesign -dv` prints: the requirement does not read that text.
  mkdir -p "$work/forged"
  cp -R "$app" "$work/forged/bosun.app"
  codesign --force --sign - -i $'foo\nTeamIdentifier=ABCDE12345' "$work/forged/bosun.app" >/dev/null 2>&1
  check "codesign -dv of a bundle with a forged identifier does show TeamIdentifier=ABCDE12345 first" bash -c 'codesign -dv "$1" 2>&1 | grep -qx "TeamIdentifier=ABCDE12345"' _ "$work/forged/bosun.app"
  check "... but the Developer ID requirement still refuses it, with exit 3" bash -c 'codesign --verify --deep --strict -R "=$1" "$2" 2>/dev/null; [ $? = 3 ]' _ "$requirement" "$work/forged/bosun.app"
  check "spctl --assess --type execute -vv rejects it, as it would an app that is not notarized (no Notarized Developer ID line)" bash -c 'out="$(spctl --assess --type execute -vv "$1" 2>&1)"; [ $? -ne 0 ] && ! grep -qx "source=Notarized Developer ID" <<<"$out"' _ "$app"
  check "ditto -x -k unpacks a zip made with ditto -c -k --keepParent into one bosun.app" bash -c '
    ditto -c -k --keepParent "$1" "$2/release.zip" && mkdir "$2/unpacked" && ditto -x -k "$2/release.zip" "$2/unpacked" &&
      [ "$(ls "$2/unpacked")" = bosun.app ]' _ "$app" "$work"
  check "... and what it unpacks is still a signed bundle" codesign --verify --deep --strict "$work/unpacked/bosun.app"
  app_build_tools "$work" "$app"
  check "osascript does not say an app that is not there is running, asked by its bundle id" bash -c '[ "$(osascript -e "application id \"ai.example.bosun\" is running" 2>/dev/null)" != true ]'
  check "... and says it of one that is: Finder" bash -c '[ "$(osascript -e "application id \"com.apple.finder\" is running")" = true ]'
  check "open is there" command -v open
  check "bosun app status --json answers on this Mac, with no app" bash -c 'jq -e ".contract == 1 and .installed == null and .login == false" <<<"$("$1" app status --json)"' _ "$bosun"
  check "bosun app login on is refused while no app is installed" bash -c '! out="$("$1" app login on 2>&1)" && grep -q "bosun.app is not installed" <<<"$out"' _ "$bosun"
  rm -rf "$work"
}

# Through a real terminal, with real launchd and pmset.
uninstall() {
  local bosun="$HOME/.local/bin/bosun" state="$HOME/.local/state/bosun" out="$OUT/uninstall.txt"
  local drive=(python3 "$ROOT/tests/sandbox/drive.py" "$out") settings ledger
  echo "uninstall, through a real terminal"
  "$bosun" start >/dev/null 2>&1
  "$bosun" ledger spawn --title "Keep me" --ask "Stay through an uninstall" --via "user: test" >/dev/null
  "$bosun" kg import "$ROOT/tests/fixtures/knowledge/acme-import.jsonl" --apply --auto-confirm >/dev/null 2>&1
  settings="$(settings_sum)"
  ledger="$(cksum <"$state/ledger.json")"

  check "Enter at are-you-sure is a no" "${drive[@]}" "Are you sure" "<enter>" -- "$bosun" uninstall
  cat "$out"
  check "... which says so" grep -q "Nothing was changed" "$out"
  check "... and it is so" launchctl print "$DOMAIN/$LABEL"
  check "... with the wake still set" bash -c 'pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'
  check "... and the windows up" tmux -L bosun has-session -t "=bosun"

  uninstall_keeping_records "$settings" "$ledger"
  install_again "$settings"
  check "... your ledger" bash -c '"$1" ledger list | grep -q "Keep me"' _ "$bosun"
  check "... the jobs" launchctl print "$DOMAIN/$LABEL"
  check "... the wake" bash -c 'pmset -g sched | grep -i -A3 "repeating" | grep -iq wake'

  echo "a wake that is not bosun's"
  sudo pmset repeat wakeorpoweron MTWRFSU 03:17:00
  check "uninstall leaves it" bash -c '"$1" uninstall --yes --purge >"$2" 2>&1; pmset -g sched | grep -i -A3 "repeating" | grep -q "3:17AM"' _ "$bosun" "$out"
  cat "$out"
  check "... and says so" grep -q "may not be bosun" "$out"
  sudo pmset repeat cancel
  jobs_are_gone
  check "the state folder is gone" test ! -e "$state"
  check "the config folder is gone" test ! -e "$CONFIG"
  check "nothing named bosun is left in this home folder" bash -c '[ -z "$1" ] || { echo "$1"; exit 1; }' _ "$(named_bosun)"
  check "the agent's own config is still there" jq -e '.hasCompletedOnboarding == true' "$HOME/.claude.json"
}

a_new_machine
follow_what_it_says
install
check "... versions side by side, the install path a link to the version in use" side_by_side
nothing_chosen
windows_by_answers acme-api
launchd_for_real
run_again
app_tools
uninstall
rm -rf "$OUT"
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
