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
  awake hours end when it is inside them, then start or refresh — on any day, at any hour. Opening the app in the red
  state starts bosun too.
- **The console always running is a setting**, `BOSUN_CONSOLE_ALWAYS`, on for installs with the app and off for
  others (a `CONFIG_ADDED` entry keeps it off on existing installs). A `bosun console` by hand while the job runs opens
  the running one instead of starting a second.
- **"Uninstall bosun…" opens Terminal running `bosun uninstall`**: one implementation with its questions, and the
  administrator password the scheduled wake needs is asked where `sudo` can ask for it.
- **Where the app comes from:** the GitHub releases of the repository bosun was installed from (its origin), or
  `BOSUN_APP_RELEASES` for a fork or mirror. No release for `app/VERSION` there: no app, and one line saying so.
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
- **The console as a job.** `templates/launchd-console.plist` runs `bosun console` (persisted token, its port) with
  KeepAlive while bosun runs; `bosun console --url` prints its address with the token. Stopping bosun boots it out;
  starting bosun loads it.
- **`bosun quit`.** The one command Quit runs (the app collects the choices; the CLI does the work, so a terminal
  can quit bosun the same way):
  1. Marks bosun stopped (a state file: since when, and whether the morning start is paused), so the watchdog and
     the check-in loop do not restart or alert about what is stopping.
  2. Agents mid-task: `--let-finish[=<minutes>]` waits until each window's turn is over (the update's wait,
     `lib/update_windows.py`, up to 10 minutes), then stops them; `--stop-now` stops them at once.
  3. Stops the windows and the tmux server (`bosun stop`), the console job, the helpers the panes left
     (`lib/orphans.py`).
  4. `--sleep` ends bosun's keep-awake.
  5. `--pause-morning` keeps the morning job from starting bosun until `bosun start`; the scheduled wake stays (it
     needs an administrator to change), so the Mac may wake at its time and sleep again.
  6. Prints what it stopped, as JSON with `--json`.
  The app then quits itself, unless "Keep bosun in the menu bar" was checked.
- **`bosun start`** clears the stopped mark, loads the console job, and starts the session as today.
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
  update's summary says how to switch.
- **`bosun update --check --json`** looks up the newest release with `git ls-remote` (no fetch, no folder) and reads
  its notes from the tag's message: what the app's "Update Available" and its dialog show. The app checks every 6
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
- The JSON fields of `bosun status --json` beyond those listed, and the contract's first number.
- Whether `bosun status --json` extends `bosun status` or is a new command.
- The menu bar icon's drawing.
