# Design note: bosun.app, a menu bar app

bosun runs as scripts, launchd jobs and a tmux server, and you reach it through a terminal. This adds what a Mac
app has: an icon in the menu bar that says at a glance whether bosun is well, its updates, its console, a Quit
that stops it cleanly, and a bosun.app in Applications that starts it again. Status: approved 2026-11-12, with the person's answers of the same day; part 1 (the commands) in progress.
Research: the session report "Shipping bosun as a macOS app" (install channels, the native architecture, how
Ollama, Rogue Amoeba, Docker, OrbStack, Tailscale, Rancher Desktop and Postgres.app behave, and how tools that
ship a CLI and an app keep them in step).

## Why

- **bosun should feel like an app.** Today nothing on screen says bosun is running; you find out it stopped when
  a window goes quiet. Starting it again, updating it and opening its console are commands to remember.
- **Quitting is not supported cleanly.** `bosun stop` ends the windows but leaves the launchd jobs, a keep-awake
  that holds the Mac up until evening, and a morning start that brings everything back tomorrow.
- **It is the step towards distribution.** A signed, notarized app is what a DMG or a Homebrew cask needs later.

## Goals

1. **A menu bar icon** with bosun's knot as a template image, and a menu whose first line is bosun's state: a
   coloured dot and one sentence, with a grey detail line.
   - green "bosun is running" (windows open, how many wait for you, keep-awake until when);
   - yellow "bosun is running, with a warning" (the watchdog has not run, errors in the last hour, a window that
     could not start), with a "Show What's Wrong…" item;
   - red "bosun is not running" (it should be: inside its awake hours and not stopped), with "Start bosun" first;
   - grey "bosun is stopped" (you quit it), with "Start bosun" first.
   The icon shows a small dot for a warning, an error or an update ready.
2. **Updates the way Mac apps do them.** "Check for Updates…" runs bosun's own update check. Its states:
   "Checking for Updates…" (greyed), "Update Available: <version>…" with a badge, "Updating… waiting for N
   windows" (greyed, while `bosun update` waits for a quiet moment), back to "Check for Updates…". An available
   update shows the standard dialog: what is new, Skip This Version, Remind Me Later, Update Now. `bosun update`
   stays the one updater, of bosun and of the app.
3. **Open Console** (⌘O) opens the web console in the default browser, signed in. The console runs all the time,
   as a launchd job like the watchdog, while bosun runs.
4. **Quit bosun…** (⌘Q) stops bosun cleanly (below), after a short dialog.
5. **A bosun.app** in `~/Applications`: Spotlight and Launchpad find it; opening it starts bosun if it is stopped
   and brings the icon back. "Open at Login" in the menu.
6. **Installing asks.** `bosun init` asks whether to add bosun to the menu bar and Applications (recommended
   yes); `bosun update` keeps the app current. **Uninstalling** is "Uninstall bosun…" in the Help submenu, which
   runs `bosun uninstall` after a confirmation.

## Non-goals

- The Mac App Store, the App Sandbox, Sparkle. bosun drives tmux, git and launchd, which a sandboxed app cannot,
  and its updates are its own.
- A DMG download and a Homebrew cask: the next step, once this ships; the notarized app built here is what they
  will carry.
- A Settings window, notifications from the app, and anything the console already does: the menu is a status
  light and four actions.
- Linux and Intel Macs (macOS 27 is Apple silicon only; bosun's minimum macOS stays what it is).

## Anti-goals

- **Never a second source of truth.** The app knows nothing bosun's CLI does not tell it: it asks `bosun … --json`
  and acts through `bosun` commands, and never reads or writes bosun's state or config files.
- **Never stop an agent's work without your say.** Quit asks when an agent is mid-task.
- **Nothing left running after Quit** that you did not choose to keep: no tmux server, no console, no keep-awake,
  no orphaned helper, and no launchd job that brings bosun back before you start it again.
- **No privacy permissions.** The app asks for none (no notifications, no accessibility, no automation), so a
  new build never resets anything you granted.
- **Nothing destroyed.** Quit and Start never touch the ledger, the knowledge base or the config.

## Decided

The person's, 2026-11-12:

- The status light, updates, Open Console, Quit and the app in Applications, as above.
- Quit lets you choose what happens to an agent in the middle of a task; a checkbox, on by default, pauses the
  morning start until you open bosun again.
- bosun.app is signed with the person's Apple Developer ID and notarized, built in CI the way the person's other
  Mac app is (a throwaway keychain, `notarytool` with an App Store Connect API key, stapled); the person puts the
  certificate and key into the repository's Actions secrets.
- `bosun init` offers the app; a person can say no.
- **Signed as the person** with their current Developer ID, team and bundle identifier `ai.predicated.bosun`, for
  now: the person may move to an organization account later, so the publisher must be easy to swap.
- **The publisher lives in one file and the secrets.** `app/publisher.json` holds the bundle identifier, the Team
  ID (so downloads are checked strictly; a written exception to the repo's rule against ids, in CLAUDE.md's
  Publishing section) and the repository whose releases carry the app; the signing identity, certificate and App
  Store Connect key are the repository's Actions secrets. The build reads the bundle identifier from that file (it
  generates `Info.plist`), the release workflow reads the secrets, and `bosun app install` reads the Team ID and the
  release repository from it. Nothing else names them. Swapping the publisher is editing that file, replacing the
  secrets and cutting one app release; the next `bosun update` installs the newly signed app (a new bundle
  identifier makes macOS see a new app, so its login job is re-made under it).
- **bosun gets tagged releases**, which the menu calls versions (below).

Calls made from the research and the builder check, to check while reading:

- **Keep-awake is a real choice at Quit:** "Let this Mac sleep now" (on by default) ends it; unchecked, the keep-awake
  runs to its end and Quit leaves it alone.
- **An agent still busy after the wait** (10 minutes): the app asks again — Keep Waiting (default), Stop It Now,
  Cancel Quit. A window waiting for you (a question or permission prompt on its screen) is not waited on: the dialog
  lists it by name, and Quit stops it.
- **After Quit the launchd jobs stay loaded and do nothing** until Start: the watchdog and learning skip their runs
  while bosun is stopped, the morning job skips while paused. So the jobs never need reloading, and
  `reload_changed_jobs` is unchanged. The console job is the one that would restart a process, so its KeepAlive
  depends on a marker file Start creates and Quit removes (launchd's `KeepAlive` → `PathState`).
- **"Start bosun" does what the morning run does** — the setup refresh, the agents' update, a keep-awake until the
  awake hours end when it is inside them, then start or refresh — on any day, at any hour. It runs `bosun morning
  --now`: that run ends a quit (paused or not) instead of stopping for it, takes "inside the awake hours" from
  `lib/awake.py`'s window (the one rule the watchdog and the status use), holds no keep-awake outside it, and still
  passes `--unless-quit` to its start, so a Quit made while Start is setting up is kept. Opening the app in the red
  state starts bosun too.
- **The console always running is a setting**, `BOSUN_CONSOLE_ALWAYS`, on for installs with the app and off for
  others (no `CONFIG_ADDED` entry: its default, 0, is what an older install did). A `bosun console` by hand while the
  job runs prints (and with `--open` opens) the running one instead of starting a second.
- **"Uninstall bosun…" opens Terminal running `bosun uninstall`**: one implementation with its questions, and the
  administrator password the scheduled wake needs is asked where `sudo` can ask for it.
- **Where the app comes from:** the GitHub releases of the repository `BOSUN_APP_RELEASES` names (a fork or
  mirror), else the one `app/publisher.json` names, else the repository bosun was installed from (its origin). No
  release for `app/VERSION` there: no app, and one line saying so.
  Developers build it on their own Mac with `bosun app install --build` (ad-hoc signed, for developing the app only).
- **Quit is option B of the mockups:** a short dialog, and a second one only when agents are busy (as Ollama asks
  only when work would end).
- **Built in CI, downloaded by bosun.** Building on each Mac would make every rebuild a new app to macOS
  (permissions reset, login items pile up, launch-constraint kills on macOS 27); no shipped product does it.
- **The app versions on its own**, behind a small versioned JSON contract with the CLI, so most bosun updates do
  not touch it.
- **AppKit, not SwiftUI's `MenuBarExtra`:** `NSStatusItem`, `NSMenu` and `NSAlert` give the coloured status line,
  a menu that updates while open, and a dialog with checkboxes; `MenuBarExtra` gives none of those reliably.
- **Trashing the app does not uninstall bosun.** Even Apple's `SMAppService` leaves registered jobs running after
  the app is trashed (cleaned "overnight"); "Uninstall bosun…" is the clean way, as Docker and SoundSource do it.

## The app

- **What it is.** A Swift package in `app/`: a library `BosunMenuCore` (pure: the status model, the menu it
  becomes, the update states, the Quit choices and the command each makes; tested with `swift test`) and an
  executable `BosunMenu` (AppKit only: `NSStatusItem`, `NSMenu`, `NSAlert` with an accessory view, `NSWorkspace`
  to open URLs, `Process` to run `bosun`). `LSUIElement` (no Dock icon), not sandboxed, hardened runtime.
  Bundle `bosun.app`, its icon an `.icns` made from `docs/assets/logo.png` with `iconutil`, the menu bar icon a
  template outline of the knot (black on clear, 18 pt, drawn once as a PDF or PNG pair).
- **How it talks to bosun.** Only through the install link `~/.local/bin/bosun` (which `bosun update` points at the
  current version), with JSON commands defined in the CLI:
  - `bosun status --json`: the menu's whole state, with a `contract` version (below);
  - `bosun update --check --json`, `bosun update` (started detached; its progress is in the status);
  - `bosun console --url`: the running console's address with its token;
  - `bosun quit --plan --json` (what quitting would stop, which agents are mid-task) and `bosun quit <choices>`;
  - `bosun start`;
  - `bosun app login on|off`.
  It refreshes the status when the menu opens and every 30 seconds otherwise; a menu open shows the last state at
  once and updates in place.
- **Version skew.** `bosun status --json` carries a `contract` number. An app that does not know the number says
  "bosun.app needs an update" in its status line and offers "Update bosun…" (which runs `bosun update`); a CLI that
  sees an older app than its checkout expects updates it (below).
- **Opening it.** Opened by you (Finder, Spotlight, Launchpad): if bosun is stopped, it runs `bosun start`, then shows
  the menu bar icon. Opened at login (its launchd job passes `--at-login`): it only shows the icon; bosun's own
  morning job starts bosun at its time. Opened while running: macOS brings up the running copy, which does nothing
  new.
- **Open at Login.** A LaunchAgent bosun renders like its others (`templates/launchd-app.plist`, RunAtLoad, not
  KeepAlive), loaded or unloaded by `bosun app login on|off`; the menu item shows its state. A plain plist, as for
  bosun's other jobs: one place that manages launchd, and uninstall already knows how to take them back.

## The CLI side

- **`bosun status --json`** (new, or the existing `bosun status` with `--json`): the level (`ok`, `warning`,
  `error`, `stopped`), the sentence and detail lines, the windows and how many wait for you, the keep-awake end,
  the update state (none, checking, available with version and notes, running with what it waits for), whether
  bosun is stopped by a Quit and whether the morning start is paused, and the console's state. Its level comes
  from what the console's health view and the watchdog already compute (`lib/console_sessions.py`,
  `lib/watchdog.py`), in one pure function, so the menu, the console and the watchdog never disagree.
  Built as `lib/menu_status.py` (the pure function, `snapshot`) and `lib/menu_status_live.py` (its facts, read-only,
  and the command). The keep-awake end comes from `keep-awake-<session>.json` (`{"until", "pid"}`), which the morning
  writes beside the pid file and `bosun stop` removes; a hold whose process is gone is not shown. A missing session is judged by the watchdog's own gate
  (`watchdog.session_gate_kind`), never by a rule of status's own: an error only when it should be running; a plain
  `bosun stop` today is `stopped` with the morning not paused; outside the awake hours it is resting (until the next
  scheduled start), and when the awake window has begun and the morning run is going or not yet due it is starting
  (never, at the error level: a crashed or hung morning run is named as failed). The yellow level also lists the ERROR
  lines of the last hour after the watchdog has alerted them (the same line is an error until the watchdog's next
  run, then a warning for the hour). A quit record that cannot be read shows as stopped, with the problem saying so and that `bosun start` clears it. A quit record while windows are still open is "bosun quit did not finish", an error (its `bosun stop` failed or
  was cut short, while the watchdog skips its runs), with the problem saying how to finish: `bosun quit --stop-now` again,
  or `bosun start`. Seconds of it during a normal quit, between `quit.json` and the session going, are accepted: the app
  does not poll during its own quit.
- **The console as a job.** `templates/launchd-console.plist` runs `bosun console` (persisted token, its port),
  rendered and loaded by `reload_changed_jobs` only with `BOSUN_CONSOLE_ALWAYS=1`. Its `KeepAlive` is `PathState` on
  `$BOSUN_STATE_DIR/console/wanted` alone and `RunAtLoad` is false: launchd starts a job whose KeepAlive condition holds
  (`man launchd.plist`), so the marker alone starts and keeps it, where a server started at load would run all session
  after a quit and a reboot with the marker gone. `bosun start` makes the marker; quit removes it and stops the process
  (`launchctl kill TERM`). `bosun console --url` prints its address with the token: the port the console recorded in its
  running lock and the stored token.
- **`bosun quit`.** The one command Quit runs (the app collects the choices; the CLI does the work, so a terminal
  can quit bosun the same way). `bosun quit --plan [--json]` changes nothing and says what a quit would meet: the
  open windows, which are busy and which wait for you, when the keep-awake ends, and why a quit is refused (an
  update runs). `bosun quit (--let-finish[=<minutes>] | --stop-now) [--pause-morning] [--sleep] [--json]` does it,
  in this order (built as `lib/quit.py`, the plan and the run over its ports, and `lib/quit_live.py`, the world
  they touch):
  1. **The wait, with `--let-finish`**: until no window is busy, polling every 5 seconds for up to the minutes
     (`BOSUN_QUIT_WAIT_MINUTES`, default 10; 0 checks once). A window waiting for you does not hold it, and one at
     `shell` (only background work) counts as done. It holds no lock and changes nothing; if the minutes run out
     it names the windows still busy and exits 3, so the app can ask again. `--stop-now` has no wait. Quit never
     sends a key to a window, and `--let-finish` inside one of bosun's own windows is refused (it would wait on
     itself).
  2. **The `windows` lock**, the one `bosun start` and `bosun refresh` hold, waiting up to
     `BOSUN_WINDOWS_LOCK_WAIT_SECONDS` for a start that is setting up the windows; if it does not come free (or
     cannot be opened), quit fails with nothing changed. Everything after holds it, and so does what it starts.
  3. **A second look under the lock**, for both modes: the windows and whether an update runs are read again. An
     update that began refuses the quit (exit 1); with `--let-finish`, a window busy again lets the lock go and
     goes back to the wait, within the same deadline (exit 3 when it passes); all with nothing written. The
     windows the quit reports as stopped are those of this look.
  4. **`quit.json`** is written (since when, and whether the morning start is paused), so the watchdog and the
     scheduled learning skip their runs and never restart or alert about what is stopping.
  5. **The console**: the `console/wanted` marker is removed first (so launchd does not start the job again),
     then the job is told to stop; a console still running afterwards (the job's slow to exit, or one started by
     hand, which is not hunted for) is reported as `still running` with its `console_port`.
  6. **`bosun stop`**, with `--keep-awake` unless `--sleep` (the Mac may sleep now: a plain `bosun stop`, which
     ends bosun's keep-awake). `--pause-morning` is the `morning_paused` field of `quit.json`; the scheduled wake
     stays (it needs an administrator to change), so the Mac may wake at its time and sleep again.
  Exit codes: 0 done; 1 refused (an update runs, or began) or failed; 2 usage; 3 the wait ran out. With `--json`
  each result is one object with `contract` 1 and the plan's keys, plus `result` (`stopped`, `busy`, `refused`,
  `failed`) and `error`; when it stopped bosun also `morning_paused`, `keep_awake`, `console` (a fixed set:
  `stopped`, `not running`, `still running`) and `console_port`. If a step after step 4 fails, `quit.json` stays
  (bosun is meant to be stopped) and the quit says so, with the two ways out: run `bosun quit --stop-now` again,
  or `bosun start`. The app then quits itself, unless "Keep bosun in the menu bar" was checked.
- **`bosun start`** clears the stopped mark, loads the console job, and starts the session as today. `bosun start
  --unless-quit` instead logs that bosun was quit and exits 0, starting nothing and clearing nothing, checked under
  the same lock before the clear. The morning run passes it: it clears an unpaused quit when it begins, then runs
  its setup for minutes, and a quit made in between (even one that paused the morning) must not be undone by its
  start; a start you type is the way to end a quit, so it never skips. A morning skipped this way logs "bosun quit
  during the morning run; not starting" and sends no "Sessions ready" notification.
- **`bosun app install|update|remove|login on|off`**: the app's own lifecycle, which `bosun init`, `bosun update` and
  `bosun uninstall` call.

## Building, releasing, installing

- **CI.** Every pull request that touches `app/` builds the app (ad-hoc signed) and runs `swift test` on a macOS
  runner. A release workflow, on a tag `app-v<version>`, builds it, signs it with the Developer ID (throwaway
  keychain), notarizes and staples it, and attaches `bosun-app-<version>.zip` (made with `ditto`) to a GitHub
  release. The secrets: the certificate (.p12) and its password, the signing identity, the team id, and the App
  Store Connect API key (id, issuer, key).
- **What a checkout expects.** `app/VERSION` names the app version this checkout works with. `bosun app install`
  and `update` download that release with `curl`, and accept it only if `codesign --verify --deep --strict` passes,
  its Team ID is the one in `app/publisher.json`, and `spctl --assess` accepts it; then they move it into
  `~/Applications/bosun.app` in one rename, keeping the old one until the new one starts.
- **`bosun init`** asks "Add bosun to the menu bar and Applications?" (an `Answers` field, default yes; skipped when
  no one is at the terminal), sets `BOSUN_MENU_APP=1`, installs the app and turns on Open at Login.
- **`bosun update`** installs the app version the new checkout expects when `BOSUN_MENU_APP=1` and the installed one
  differs, and relaunches it if it was running. An install from before this change gets `BOSUN_MENU_APP=0` (a
  `CONFIG_ADDED` entry) and a line in the update's summary saying how to add the app.
- **Uninstall.** `bosun uninstall` removes `~/Applications/bosun.app` and its login job (rows in
  `lib/uninstall_plan.py`). "Uninstall bosun…" runs it after a confirmation that lists what goes and what stays (its
  plan's own text).
- **The app moved to the Trash.** bosun keeps running. The watchdog notices the app gone while `BOSUN_MENU_APP=1`
  and alerts once: bosun still runs; `bosun uninstall` removes it, `bosun app install` brings the app back.

## Releases

bosun has had no versions: `bosun update` follows a branch, so any commit is "an update". The menu needs versions
people can skip, read about and recognise.

- **A release is a tag `v<YYYY.MM.DD>`** (a second one that day gets `.2`) on `main`, made by a workflow: a weekly train
  that tags `main` when it has new commits and its checks are green, and a manual run at any time. Its notes are the
  titles of the pull requests merged since the last release, kept as the GitHub release's text and in the tag's
  message.
- **`bosun update` follows releases by default** (`BOSUN_UPDATE_CHANNEL=releases`); `main` stays a channel for
  developers. An existing install keeps following `main` (a `CONFIG_ADDED` entry with the kept value `main`) and the
  update's summary says how to switch. A first install (`get.sh`) takes the newest release too, found by a small `sh`
  function that follows the same tag rule (it cannot ask Python, which has not been checked yet; a unit test holds
  the two to the same answers); a repository with no release yet gets the head of `main`, and `get.sh` says so. With
  `BOSUN_BRANCH` it installs that branch's head and the install follows the branch (`BOSUN_UPDATE_CHANNEL=main`).
  Running the install line again hands over to the install's own `bosun update`, so its channel decides.
- **`bosun update --check --json`** looks up the newest release with `git ls-remote`, fetches only that tag's objects (no branch, no
  version folder) and reads its notes from the tag's message: what the app's "Update Available" and its dialog show. The app checks every 6
  hours and when the menu opens, at most once a minute.
- **"Update Now" shows the plan first:** the app runs the update's rehearsal (`bosun update --dry-run --json`), shows
  what it will do (the windows that restart, anything that needs you, a reason it cannot run), and only then runs
  `bosun update --yes`. "Skip This Version" records the release it skips (in bosun's state, through
  `bosun update --skip <version>`), so the next release is offered again.
- **The app's own releases** are tags `app-v<semver>`, separate from bosun's: `app/VERSION` in a checkout names the one
  it works with.

## Testing

- `swift test` for `BosunMenuCore`: each status level to its menu, each update state to its item, each set of Quit
  choices to its `bosun quit` command line, the contract check.
- Unit tests for `bosun status --json` (each level from recorded facts) and `bosun quit` (the plan, each choice),
  smoke checks for quit and start in the hermetic suite (no tmux server, no console job, no keep-awake after a quit;
  everything back after a start; the morning job skipping while paused).
- The Tart VM: `bosun init` with the app, a real download of the notarized release, the app launched, Quit with each
  choice and the processes and jobs left after each, Start, an update that changes the app, uninstall, and the app
  moved to the Trash.

## What stops it shipping

The notarized app launches on a clean macOS 27 VM from a `curl` download with no Gatekeeper prompt; Quit leaves no
bosun process running and no job that does anything until Start (the watchdog, learning and, when paused, the
morning job skip; the console stays down); Start brings back what was running; the app never reads bosun's files.

## Left to the builder

- The exact wording of every line and dialog (the person may refine the copy).
- The menu bar icon's drawing.

## Changes while building (2026-11-13)

Where what is built differs from what this note said before, or says more:

- Quit's order was "mark stopped first, then wait, then stop". It is now the wait first (so a quit that gives up
  changes nothing and leaves the watchdog running), then the `windows` lock and a second look, then the mark, the
  console and `bosun stop`: the mark is still written before anything is stopped, but after the person's say.
- The stopped mark does not mean the session is gone: a quit record with windows open is an error in status, since
  `bosun stop` can fail after the record is written.
- `bosun start --unless-quit` is new, for the morning run, so that a start in flight cannot undo a quit.
- Quit takes the `windows` lock, which the note did not mention; a start that holds it makes the quit wait.
- The helpers a pane left (`lib/orphans.py`) are stopped by `bosun stop` itself, not by quit.
- `bosun app` (built): where the app comes from is looked up in this order, `BOSUN_APP_RELEASES`, then
  `app/publisher.json`'s `releases` (which ships naming this repository), then the install's origin when that is a GitHub
  address, read as `bosun update` reads it, with no credentials ever in the address. The first two are `owner/repo` (a
  GitHub address is taken for its slug), so the origin is only read when nothing else names the releases.
- While `app/publisher.json` has no `team_id`, `install` and `update` refuse with "app/publisher.json has no team_id yet"
  before they look anything up; `--build` is not held up by it. The Team ID is checked to be ten capital letters and
  digits, so a value such as "not set" (what `codesign` prints for an ad hoc signature) can never match.
- The download and the unpacking happen in a hidden folder inside `~/Applications` itself (`.bosun-app-…`), so the rename
  into place never crosses a volume; the old app is renamed into that folder and goes with it, and a failed rename puts it
  back. The folder, and `~/Applications` if the install made it, are removed whatever the outcome (the latter unless the app
  was placed), so a refused or failed install leaves `~/Applications` as it was. A running app is asked to quit, and waited
  for, only after the new app is verified, so a refusal never disturbs it.
- Open at Login is whether the job's plist (`<label>.app`) is in `~/Library/LaunchAgents`. `reload_changed_jobs` keeps a
  present plist current and never makes one; `bosun app login on|off` is the only switch, and `on` is refused while no app is
  installed. Loading the job runs the app at once (RunAtLoad), so the app must be safe to start twice.
- Uninstall's step for the app asks it to quit first, so a running app is not left running from a folder that is gone.
