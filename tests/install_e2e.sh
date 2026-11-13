#!/bin/bash
# A new machine's install, for real: the piped installer with its real Homebrew, real launchd jobs that
# really fire (the morning job, and the one the menu bar app's Start runs through `bosun morning --now
# --background`, whose tmux server must not be its caller's child: `launchctl procinfo`), the console's job
# (BOSUN_CONSOLE_ALWAYS=1: started by `bosun start`'s marker, stopped by `bosun quit`), `bosun quit` itself with
# what the watchdog and the morning job do after it, `bosun update` from a release to the next one (on the
# default `releases` channel, with an origin that carries release tags), the real pmset and plutil, and a clean
# uninstall. Claude Code is a stand-in, since these machines have no login. It changes the machine it runs on, so it only runs on a GitHub macOS runner
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

# What app/build.sh asks of the tools, for real, on the ad hoc bundle <app> in the folder <work>. app/ is the
# install's (get.sh made it: $DIR is the link to the version in use, the whole tree). The test's own tarball
# (tests/sandbox/mac.sh copy_checkout) holds no app/.
app_build_tools() {
  local work="$1" app="$2"
  check "the install holds the app's source and its pictures" bash -c '[ -x "$1/app/build.sh" ] && [ -f "$1/app/Resources/Info.plist.in" ] && [ -d "$1/app/Resources/AppIcon.iconset" ]' _ "$DIR"
  check "iconutil makes an icns of the app's committed icon set" bash -c 'iconutil -c icns "$1/app/Resources/AppIcon.iconset" -o "$2/AppIcon.icns" && [ "$(head -c 4 "$2/AppIcon.icns")" = icns ]' _ "$DIR" "$work"
  check "plutil -lint accepts the Info.plist app/build.sh makes from its template" bash -c 'sed -e "s/@BUNDLE_ID@/ai.example.bosun/g" -e "s/@VERSION@/0.0.1/g" "$1/app/Resources/Info.plist.in" >"$2/Info.plist" && plutil -lint -s "$2/Info.plist"' _ "$DIR" "$work"
  check "codesign --identifier makes the signing identifier, which the Developer ID requirement names (not the Info.plist's)" bash -c '
    cp -R "$1" "$2/named.app" && codesign --force --sign - --identifier ai.example.named "$2/named.app" &&
      codesign --verify --deep --strict -R "=identifier \"ai.example.named\"" "$2/named.app" &&
      ! codesign --verify --deep --strict -R "=identifier \"ai.example.bosun\"" "$2/named.app" 2>/dev/null' _ "$app" "$work"
  check "ditto --norsrc --noextattr --noqtn writes no ._ file for an attribute of this Mac" bash -c '
    xattr -w com.example.test 1 "$1/Contents/Info.plist" && ditto -c -k --keepParent --norsrc --noextattr --noqtn "$1" "$2/plain.zip" &&
      ! unzip -l "$2/plain.zip" | grep -qF "._"' _ "$app" "$work"
  app_build_for_real "$work"
}

# app/build.sh run for real on a copy of the install's app/ in <work> (the build folder stays out of the install).
app_build_for_real() {
  local work="$1"
  # The first run of the copied script is by /bin/bash, not as a program: run directly, right after this Mac's
  # assessments were turned on, the first run of a script just copied exited 1 with nothing said (every later
  # run, by bash or directly, built the app). Why is not known; `bosun app install --build` runs it directly.
  check "app/build.sh builds the app for real, ad hoc, for arm64" bash -c '
    cp -R "$1/app" "$2/app-copy" && rm -rf "$2/app-copy/.build" "$2/app-copy/dist" &&
      /bin/bash "$2/app-copy/build.sh" --identity - --out "$2/built" >"$2/build.log" 2>&1 || { echo "app/build.sh exited $?"; tail -15 "$2/build.log"; exit 1; }
    [ "$(lipo -archs "$2/built/bosun.app/Contents/MacOS/bosun")" = arm64 ]' _ "$DIR" "$work"
  check "... the bundle verifies, is signed as the bundle id of app/publisher.json and is not notarized" bash -c '
    id="$(jq -r .bundle_id "$1/app/publisher.json")" && codesign --verify --deep --strict -R "=identifier \"$id\"" "$2/built/bosun.app" &&
      plutil -lint -s "$2/built/bosun.app/Contents/Info.plist" &&
      ! spctl --assess --type execute -vv "$2/built/bosun.app" 2>&1 | grep -qx "source=Notarized Developer ID"' _ "$DIR" "$work"
  check "... and its zip unpacks with ditto into one signed bosun.app" bash -c '
    mkdir "$2/from-build" && ditto -x -k "$2"/built/bosun-app-*.zip "$2/from-build" && [ "$(ls "$2/from-build")" = bosun.app ] &&
      codesign --verify --deep --strict "$2/from-build/bosun.app"' _ "$DIR" "$work"
  check "app/build.sh --zip zips it again, with no ._ file, into one signed bosun.app" bash -c '
    xattr -w com.example.test 1 "$2/built/bosun.app/Contents/Info.plist" &&
      "$2/app-copy/build.sh" --zip "$2/built/bosun.app" --out "$2/rezip" >/dev/null &&
      ! unzip -l "$2"/rezip/bosun-app-*.zip | grep -qF "._" &&
      mkdir "$2/from-rezip" && ditto -x -k "$2"/rezip/bosun-app-*.zip "$2/from-rezip" &&
      codesign --verify --deep --strict "$2/from-rezip/bosun.app"' _ "$DIR" "$work"
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
  # A Tart VM has Gatekeeper's assessments off ("override=security disabled": spctl accepts anything); a runner and a
  # user's Mac have them on, which is what this checks against.
  spctl --status | grep -q "assessments enabled" || { sudo spctl --global-enable && echo "  note  assessments were disabled on this Mac: enabled"; }
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

# ---- the console's job, `bosun quit` and the update to the next release (docs/design/menu-bar.md) ----

# <seconds>: the console's process is gone, and stays so a while (launchd would have restarted it by then)
console_stays_stopped() {
  local deadline=$((SECONDS + $1))
  while console_process; do
    [ "$SECONDS" -ge "$deadline" ] && return 1
    sleep 1
  done
  sleep 8
  ! console_process && [ "$(job_state "$LABEL.console")" != running ]
}

# BOSUN_CONSOLE_ALWAYS=1, which bosun.env says and install.sh acts on: launchd's job for the console, loaded
# with the state folder having no console/ yet, runs nothing; `bosun start` makes its marker and it serves; with
# the marker gone it stays stopped, also when it is loaded again (RunAtLoad is off: ruling 11 of the design).
console_job_for_real() {
  local bosun="$HOME/.local/bin/bosun" job="$LABEL.console" marker="$STATE/console/wanted"
  local plist="$HOME/Library/LaunchAgents/$LABEL.console.plist"
  echo "the console's launchd job, with BOSUN_CONSOLE_ALWAYS=1"
  check "before it, the state folder has no console/ and there is no job" bash -c '[ ! -e "$1" ] && ! launchctl print "$2"' _ "$STATE/console" "$DOMAIN/$job"
  sed -i '' 's/^BOSUN_CONSOLE_ALWAYS=.*/BOSUN_CONSOLE_ALWAYS="1"/' "$CONFIG/bosun.env"
  check "bosun.env says BOSUN_CONSOLE_ALWAYS=1" grep -qx 'BOSUN_CONSOLE_ALWAYS="1"' "$CONFIG/bosun.env"
  check "install.sh loads the job" bash -c '"$1/install.sh" >"$2" 2>&1 || { tail -15 "$2"; exit 1; }' _ "$DIR" "$OUT/console-install.txt"
  check "$job is loaded" launchctl print "$DOMAIN/$job"
  check "... its plist is valid" plutil -lint -s "$plist"
  sleep 5
  check "... and runs nothing: not running, no console process, no marker, no console/ folder" bash -c '
    [ "$(launchctl print "$1" | sed -n "s/^[[:space:]]*state = //p" | head -1)" != running ] && ! pgrep -f "lib/console_server\.py" &&
      [ ! -e "$2" ] && [ ! -e "$3" ]' _ "$DOMAIN/$job" "$marker" "$STATE/console"
  check "bosun status --json says the console is not running" status_says '.console.running == false'

  check "bosun start returns, exit 0" "$bosun" start
  check "... its marker is there" test -e "$marker"
  check "... and the console answers within seconds" wait_for 30 console_answers
  check "... as the job's process: $job is running" test "$(job_state "$job")" = running
  check "bosun status --json says it runs, and gives no address" status_says '.console.running == true and .console.url == null'
  check "bosun console --url prints the address, which has a token" bash -c '"$1" console --url | grep -Eq "^http://127\.0\.0\.1:[0-9]+/\?t=[A-Za-z0-9_-]{43,}$"' _ "$bosun"

  rm -f "$marker"
  launchctl kill TERM "$DOMAIN/$job"
  check "with the marker gone and the console stopped, it stays stopped" console_stays_stopped 30
  check "... and the job is still loaded" launchctl print "$DOMAIN/$job"
  launchctl bootout "$DOMAIN/$job"
  check "unloaded, and loaded again (what a reboot or install.sh does) with the marker still gone: no console process" bash -c '
    ! launchctl print "$1" >/dev/null 2>&1 && launchctl bootstrap "$2" "$3" && sleep 8 && launchctl print "$1" >/dev/null &&
      ! pgrep -f "lib/console_server\.py" && [ "$(launchctl print "$1" | sed -n "s/^[[:space:]]*state = //p" | head -1)" != running ]' _ \
    "$DOMAIN/$job" "$DOMAIN" "$plist"
  check "bosun start again makes the marker" bash -c '"$1" start >/dev/null 2>&1 && [ -e "$2" ]' _ "$bosun" "$marker"
  check "... and the console is back" wait_for 30 console_answers
  "$bosun" stop >/dev/null 2>&1
}

# `bosun quit` on a Mac that is up: nothing of bosun is left running, what the watchdog and the morning job do
# about it (nothing), and `bosun morning --now` ending it. Run from here, outside bosun's windows.
quit_for_real() {
  local bosun="$HOME/.local/bin/bosun" paused
  echo "bosun quit --stop-now --pause-morning --sleep, for real"
  check "bosun morning --now --awake-minutes 30 brings it all up, with a keep-awake" "$bosun" morning --now --awake-minutes 30
  check "... the orchestrator's window" wait_for 60 has_window orchestrator
  check "... a caffeinate" pgrep -x caffeinate
  check "... the console" wait_for 30 console_answers
  check "bosun quit --plan --json changes nothing" bash -c '"$1" quit --plan --json | jq -e ".contract == 1" && tmux -L bosun has-session -t "=bosun"' _ "$bosun"
  check "bosun quit --stop-now --pause-morning --sleep ends well, exit 0" "$bosun" quit --stop-now --pause-morning --sleep
  check "... no tmux server" no_tmux_server
  check "... no caffeinate" bash -c '! pgrep -x caffeinate'
  check "... no console process, and its job is not running" bash -c '
    ! pgrep -f "lib/console_server\.py" && [ "$(launchctl print "$1" | sed -n "s/^[[:space:]]*state = //p" | head -1)" != running ]' _ "$DOMAIN/$LABEL.console"
  check "... the console's marker is gone, which keeps its job down" test ! -e "$STATE/console/wanted"
  check "... quit.json says since when, with the morning paused" jq -e '.morning_paused == true and (.since | type == "string")' "$STATE/quit.json"
  check "bosun status --json says stopped, the morning paused, nothing running" \
    status_says '.level == "stopped" and .stopped.morning_paused == true and .console.running == false and (.windows | length == 0) and .keep_awake_until == null'

  : >"$STATE/logs/watchdog.launchd.out"
  launchctl kickstart -k "$DOMAIN/$LABEL.watchdog"
  check "the watchdog's next run, started by launchd, logs that it skipped" wait_for 90 watchdog_skipped
  check "... and started nothing" no_tmux_server
  paused="$(morning_lines "the morning start is paused")"
  launchctl kickstart -k "$DOMAIN/$LABEL"
  check "the morning job, kickstarted, logs that its start is paused" wait_for 90 morning_lines_above "the morning start is paused" "$paused"
  sleep 10
  check "... and starts nothing: no tmux server, no console, no caffeinate" bash -c '
    ! tmux -L bosun info && ! pgrep -f "lib/console_server\.py" && ! pgrep -x caffeinate'
  check "... the quit is still on record" test -e "$STATE/quit.json"

  check "bosun morning --now brings everything back, exit 0" "$bosun" morning --now
  check "... the quit record is gone" test ! -e "$STATE/quit.json"
  check "... the orchestrator's window" wait_for 60 has_window orchestrator
  check "... and the repo's" wait_for 60 has_window acme-api
  check "... the console's marker is made" test -e "$STATE/console/wanted"
  check "... and the console answers" wait_for 30 console_answers
  check "bosun status --json no longer says stopped" status_says '.stopped == null'
  "$bosun" stop >/dev/null 2>&1
}

# The install is on the releases (the default), in the origin tests/sandbox/e2e_lib.sh's a_next_release adds to.
releases_for_real() {
  local bosun="$HOME/.local/bin/bosun" next="v2026.11.02" first="$BOSUN_E2E_RELEASE" first_commit out="$OUT/release-update.txt"
  echo "releases: the install took the newest, and bosun update moves to the next"
  first_commit="$(git --git-dir="$HOME/origin.git" rev-parse "$first^{commit}")"
  check "bosun.env says the install follows the releases" grep -qx 'BOSUN_UPDATE_CHANNEL="releases"' "$CONFIG/bosun.env"
  check "get.sh installed the release $first: the version in use is its commit" test -n "$first_commit" -a "$(version_in_use)" = "$first_commit"
  check "... bosun.git has no tag (a release is fetched into a remote ref)" bash -c '[ -z "$(git --git-dir="$1/bosun.git" tag -l)" ]' _ "$VERSIONS"
  check "bosun update --check --json: releases, $first installed, up to date" bash -c '
    "$1" update --check --json | jq -e --arg v "$2" ".channel == \"releases\" and .installed.version == \$v and .latest.version == \$v and .available == false and .error == null"' _ "$bosun" "$first"
  check "bosun update --check: one line, up to date" bash -c '"$1" update --check | grep -q "up to date"' _ "$bosun"
  "$bosun" ledger spawn --title "Before the update" --ask "Be migrated by it" --via "user: test" >/dev/null
  check "the next release is made: the patch commit, tagged $next, in the origin" a_next_release "$next"
  check "bosun update --check --json sees it, with its notes from the tag's message" bash -c '
    "$1" update --check --json | jq -e --arg v "$2" ".available == true and .latest.version == \$v and (.latest.notes | contains(\"ledger goes to format 2\")) and .skipped == false and .error == null"' _ "$bosun" "$next"
  check "... the check changed nothing: still one version folder, and the same version in use" test "$(version_folders)" = 1 -a "$(version_in_use)" = "$first_commit"
  check "bosun update --skip $next: --check says skipped, and still available" bash -c '
    "$1" update --skip "$2" >/dev/null && "$1" update --check --json | jq -e ".skipped == true and .available == true"' _ "$bosun" "$next"
  check "bosun update --yes moves to it anyway" bash -c '"$1" update --yes >"$2" 2>&1 || { tail -25 "$2"; exit 1; }' _ "$bosun" "$out"
  cat "$out"
  check "... the version in use is the next release's commit, in a folder beside the first" test "$(version_in_use)" = "$(git -C "$OUT/next" rev-parse HEAD)" -a "$(version_folders)" = 2
  check "... and the install path is still a link to the one in use" test -L "$DIR" -a "$(readlink "$HOME/.local/bin/bosun")" = "$DIR/bin/bosun"
  check "... the ledger went to format 2, with the patch's migration" test "$(jq -r '"\(.version) \(.update_patch)"' "$STATE/ledger.json")" = "2 migrated"
  check "bosun update --check --json: $next installed, up to date" bash -c '
    "$1" update --check --json | jq -e --arg v "$2" ".installed.version == \$v and .available == false and .error == null"' _ "$bosun" "$next"
  check "the jobs name the install path (the link), not the version's folder" bash -c '
    grep -qF "$1/bin/bosun" "$2" && ! grep -qF "$3" "$2"' _ "$DIR" "$HOME/Library/LaunchAgents/$LABEL.plist" "$(cd "$DIR" && pwd -P)"
  check "the console job was restarted by the update, and answers" wait_for 60 console_answers
  check "bosun doctor passes on the new version" bash -c '"$1" doctor >"$2" 2>&1 || { tail -20 "$2"; exit 1; }' _ "$bosun" "$OUT/doctor-after-update.txt"
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
  check "the job that runs --now is unloaded and its plist gone too" bash -c '! launchctl print "$1" && [ ! -e "$2" ]' _ "$DOMAIN/$LABEL.now" "$HOME/Library/LaunchAgents/$LABEL.now.plist"
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
console_job_for_real
launchd_for_real
now_job_for_real
quit_for_real
# Only with an origin that has a release in it (tests/sandbox/mac.sh); a runner installs a branch's head.
[ -z "${BOSUN_E2E_RELEASE:-}" ] || releases_for_real
run_again
app_tools
uninstall
rm -rf "$OUT"
echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
