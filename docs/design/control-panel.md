# Design note: the menu bar app as a control panel, and starting at login

bosun.app ([the menu bar app](menu-bar.md)) shows bosun's state and runs a few actions. This turns it into what the
person who runs bosun reaches for whatever the hour: a control panel that is there from login, apart from bosun's
session (the orchestrator, the windows, the check-ins), which keeps to the schedule. It adds starting bosun at
login, a separate Stop and Quit, and a Settings window. Status: approved 2026-11-14, with the person's answers of
the same day; built ([plan](../plans/control-panel.md)), bosun.app 0.1.3, and run in the Mac VM (`tests/sandbox.sh
mac app`); what changed while building is [at the end](#changes-while-building). (Builder's call in the plan: Open
at Login moves from the menu into Settings → General, beside starting at login, so it lives in one place.)

## Why

On the first installs on a fresh Mac (the Mac VM, 2026-11-14):

- **Open at Login opens only the icon.** bosun itself starts from the morning job at the start time
  (`templates/launchd.plist`). A Mac that starts, or a person who logs in, after that time on a working day gets no
  bosun until the next morning, unless they click Start.
- **The panel and the session are one in the menu.** Quit stops bosun and takes the icon away, unless "Keep bosun
  in the menu bar" is ticked; outside the hours the icon says "resting until 08:00" without saying it is the
  schedule, or when the next start is.
- **Settings are in three places**: `bosun.env` (written only by `bosun init`), the wizard, and the menu's Open at
  Login. Nothing in the app shows the schedule, or the update channel, or changes them.
- **What a start outside the hours does is not one rule**: the check-ins ignore `BOSUN_DAYS` and the hours, while
  the Slack watcher, learning and the watchdog's alerts keep to them (the person asked to settle this after
  testing; this note does).

Comparable apps: Docker Desktop's Settings → General has "Start Docker Desktop when you sign in" (off by default);
OrbStack and Tailscale have a start-at-login item; Apple's guidelines put app-level settings behind a "Settings…"
item (⌘,), in a window titled "<App> Settings", with panes only when there are several.

## The person's answers (2026-11-14)

- Starting at login: yes, a setting, and in the setup wizard.
- A Settings window reached from the menu bar.
- The menu bar icon shows from login whatever the schedule, as a panel to reach the console, the logs, the
  actions and the settings; the status says when it is outside the hours. "There's some independence there, and
  there's some value in being able to see the menu bar even when it's off hours."
- With the recommendations taken: automatic starts follow the schedule; the Settings window starts with General
  and Schedule; starting at login is on by default for a new setup.

## Goals

- **Two layers, named apart.** The app is the panel: from login, any hour, until you quit it. The session is the
  worker: it follows the schedule on its own, and you start or stop it from the panel at any time.
- **Start bosun when you log in, during your hours**: on a scheduled day, between `BOSUN_START_TIME` and
  `BOSUN_AWAKE_UNTIL`, unless bosun runs already, you quit it with the morning paused, or you stopped it today.
- **The schedule in the status**: whether it is your hours, and when the next automatic start is.
- **Stop and Quit apart**: Stop ends the session and leaves the panel; Quit closes the panel, and leaves the
  session running unless you choose otherwise.
- **A Settings window** (General, Schedule) that changes settings through bosun's own command, never by writing
  `bosun.env` itself.
- **One rule for a start outside the hours.**

## Non-goals

- Editing the windows (repos), connectors, agents, models or permissions in the Settings window. The wizard does
  that, starting from your config; Settings opens it in Terminal.
- Notifications from the app; a login item through `SMAppService` (bosun's jobs are its own launchd plists, as the
  menu bar note decided).
- Changing the wake schedule without an administrator: `pmset` needs `sudo`, so a Settings change that moves the
  wake says so and opens Terminal for it.

## Anti-goals

- **An automatic start you did not expect.** A start at login never overrides a Quit that paused the morning, a
  Stop earlier the same day, or the hours. A reload of bosun's jobs (an install, `bosun update`) must never start a
  session that you stopped.
- **The app writing configuration.** `bosun init` is the only writer of `bosun.env`; the app asks bosun.
- **A Settings window that disagrees with `bosun.env`** or with the wizard: it shows what bosun reads, and a value
  it cannot show is shown as such, not as a default.

## Decided

### The schedule rule

- **Automatic starts follow the schedule**: the morning job at its time, and a start at login, only on a
  scheduled day within the hours.
- **A start you make always starts**: Start in the menu, opening bosun.app by hand when it is stopped (as today),
  `bosun start`, `bosun morning --now`.
- **The work follows the session** (the person's choice, 2026-11-14, of two): while the session runs, its
  check-ins, events and the Slack watcher (when on) run, whatever the hour: a session you started on a weekend, and
  the everyday evening, since a morning session runs on past `BOSUN_AWAKE_UNTIL` (only the keep-awake ends). The
  clock decides only the automatic starts, the keep-awake's end, the watchdog's holding of alerts, learning's time,
  and the status's "outside your hours". One rule, no marker of how the session started; the check-ins already work
  this way, and the Slack watcher follows (it stopped at `BOSUN_AWAKE_UNTIL`). The other way, the work following the
  clock (a running session quiet outside the hours), was not taken.
- The status says it by the clock: "Running · outside your hours".

### Start at login

- **Setting**: `BOSUN_START_AT_LOGIN` (`1` or `0`). The wizard's recommendation for a new setup is `1`. For an
  install that predates it the update keeps what it did, `0`, through `CONFIG_ADDED` (`lib/migrations.py`), with a
  note in the update's plan and summary that it is there to turn on.
- **Who starts it**: bosun, not the app, so that it works without bosun.app and launchd is the parent of what it
  starts (the menu bar note's rule). A launchd agent like bosun's others (its own template and label helper, in
  `job_labels`, rendered and reloaded by `lib/common.sh`, in uninstall's tables), which runs at login and does what
  the morning run does for `--now`, behind the checks above.
- **Only at login**: launchd also runs a job at load when an install or `bosun update` reloads it. The job tells a
  login from a reload (by the login session's time, or a marker the reload leaves; the builder chooses) and does
  nothing on a reload. With the "stopped today" and quit checks, a reload during the day never brings back a
  session you stopped.
- **The app at login** (`--at-login`) still only shows the icon, as now: starting is the job's.
- **What it says**: one line in the day's log (`login-<date>.log` or the morning log; the builder chooses): that it
  started, or why not (outside your hours, not a scheduled day, quit with the morning paused, stopped today,
  running already, the setting off).

### The status: the schedule

- `bosun status --json` gains (the contract only grows) the schedule's state: whether now is within the hours, and
  the next automatic start (the morning job's next run, or null when there is none).
- The status line, by situation:

  | Situation | Dot | Line |
  |---|---|---|
  | Your hours, running | green | Running · 3 windows open |
  | Outside your hours, not running | grey | Resting: outside your hours · next start Mon 08:00 |
  | Outside your hours, started by you | green | Running · outside your hours |
  | Your hours, you quit or stopped it today | grey | Stopped · you quit at 18:40 (as now) |
  | Your hours, not running and nobody stopped it (a missed start, a crash) | red | as now: the watchdog's gate (`session_gate_kind`), so the menu, the console and the watchdog agree |
  | A problem | yellow / red | as now |

- In the menu, Start is "Start bosun" in your hours and "Start bosun now" outside them, with the next automatic
  start in its line.

### Stop and Quit

- **Stop bosun…** (when the session runs): today's quit flow for the session (what to do with busy agents, pausing
  the next automatic start, letting the Mac sleep), and the app stays, with the grey stopped status.
- **Quit bosun.app** (always): closes the panel. When the session runs it asks: Leave bosun running (the default),
  Stop bosun and quit, or Cancel. When it does not run, it quits at once.
- The "Keep bosun in the menu bar" box goes, since Stop is that.
- Reopening the app (Finder, Spotlight): as now (it starts bosun only when bosun is stopped and you opened it by
  hand). The panel's Open at Login is what brings it back at login.

### The Settings window

- **"Settings…" with ⌘,** in the menu, a window titled "bosun Settings", one at a time, with two panes:
  - **General**: Open bosun.app at login (today's toggle, `bosun app login on|off`); Start bosun when you log in,
    during your hours (`BOSUN_START_AT_LOGIN`); updates from: releases or main (`BOSUN_UPDATE_CHANNEL`); and
    buttons: Change windows and connectors… (opens Terminal with `bosun init`), Open Config Folder, Show Logs.
  - **Schedule**: the days (`BOSUN_DAYS`), the start time (`BOSUN_START_TIME`) and until when the Mac is kept awake
    (`BOSUN_AWAKE_UNTIL`), with a line that says it back ("bosun starts at 08:00 on weekdays, and lets the Mac sleep
    after 20:00"). A change that moves the wake (the days or the start time, while bosun sets a wake) runs `bosun
    settings set` in Terminal, which asks for an administrator first (`pmset` needs `sudo`) and writes nothing if
    that is cancelled or fails: `bosun.env`, the morning job and the wake stay as they were, together. As `bosun
    init` does, it does not replace another app's repeating wake unless told to (`pmset` keeps one), and says so. A
    change of `BOSUN_AWAKE_UNTIL` moves a running keep-awake's end too. The rest applies at once (bosun's jobs
    reloaded).
- **Through bosun**: `bosun settings get --json` (each settable key: its value, its default, whether `bosun.env`
  sets it, what it means; a `contract` number, as `status --json` has) and `bosun settings set <KEY>=<value>...`,
  which checks each value as the wizard does and writes `bosun.env` through `bosun init`'s writer (a backup, the
  file's order and comments kept), then does what the key needs (reload the jobs, `bosun app login`). A key it
  does not offer is refused. The app is a view over these two commands; the CLI is usable without it.
- Values the window cannot show (a `bosun.env` that cannot be read, a key set to something it does not know) are
  shown as such, with the wizard as the way to fix them.

### The wizard

- Two settings (`Answers` gains `start_at_login` beside the menu app's answer), asked as one question with three
  answers when the app can be installed, since they interact:
  1. Show bosun in the menu bar and start it during your hours (recommended): the app, Open at Login, and
     `BOSUN_START_AT_LOGIN=1`
  2. Show bosun in the menu bar only (it starts at the morning time, or when you click Start): the app and Open at
     Login, as today's yes
  3. No menu bar app
- After answer 3, or when no app can be installed (no release), the wizard asks the start alone: start bosun when
  you log in, during your hours (recommended yes), since it works without the app. A re-run of `bosun init`
  recommends what `bosun.env` and the installed app say.

## Where it goes

- **CLI**: `lib/menu_status.py` (the schedule fields and lines), `lib/awake.py` (the next start), a new
  `lib/settings.py` with `libexec/bosun-settings` (get and set; the table of settable keys), the login job (template,
  label helper, `reload_changed_jobs`, uninstall's tables), the morning run's at-login path, `lib/init_plan.py` and
  `lib/init_wizard.py` (the question), `lib/migrations.py` (`CONFIG_ADDED`), `examples/generic/bosun.env`.
- **App**: `BosunMenuCore` (the menu's states and items, the Stop and Quit flows, the Settings model over `settings
  get/set`), `BosunMenu` (the Settings window), a new app version.
- **Docs**: `docs/setup.md` (the wizard, the settings, the menu), `docs/commands.md` (`bosun settings`),
  `docs/design/menu-bar.md` (a pointer here, and the non-goal it lifts), `docs/design/init.md` (the question).

## Testing

- Unit tests: the schedule fields and lines; `settings get/set` (the table, refusals, the writer, the keys' side
  effects, a `bosun.env` that cannot be read); the login job's decision for each case (in hours, outside, not a
  scheduled day, quit paused, stopped today, running, the setting off, a reload); the wizard's question; the app's
  states, Stop and Quit flows and the Settings model.
- Smoke: `bosun settings` round trips in the hermetic HOME; the login job's plist renders and loads with the fakes.
- The Mac VM (`tests/sandbox.sh mac app`): a restart in hours starts bosun, one outside the hours shows "Resting"
  with the next start, a Stop leaves the panel, Quit leaves bosun running, a Settings change of the start time moves
  the morning job, and a reload of the jobs after a Stop starts nothing.

## Left to the builder

- How the login job tells a login from a reload.
- Where its line is logged.
- The Settings window's layout within Apple's conventions, and whether it is SwiftUI in an AppKit app or AppKit.
- The exact wording of the lines, keeping the table's sense.

## Changes while building

The builder's calls on what the note left open, and the rulings made while building (2026-11-14):

- **The login job tells a login from a reload by a marker**: reloading bosun's jobs (`reload_job` in
  `lib/common.sh`) leaves `login-reload` in the state folder before it loads the login job, and a run that finds
  one younger than two minutes is a reload, and starts nothing; the run removes it, whatever it decides. Its line
  goes in the day's morning log ("starting bosun: you logged in during your hours", or "not starting: <why>"); a
  skip writes no run header, so the watchdog does not count it as a morning run, and a start is the scheduled run
  with `trigger=login`. A login run while the morning or the `.now` job's run is going counts as running already.
- **The grey dot is the app's**: resting keeps the status level `ok` (the contract's levels mean what they meant),
  and the app draws the dot grey when the level is `ok`, the hours say outside and no window is open; a warning or
  an error keeps its colour. `schedule.next_start` is the next start after now, so in your hours it is tomorrow's.
- **Opening bosun.app by hand does not start a resting bosun** (all well or a warning, outside your hours): the
  schedule decides the automatic starts, and Start bosun now is one click; it still starts a stopped bosun, or a
  red one with no window open, as before.
- **The watchdog's stall check of the Slack watcher follows the session**, as the watcher does: it is no longer
  gated on the awake window (its alerts are still held outside it).
- **`settings get` on a `bosun.env` that cannot be read** gives every value as null and not valid (with its
  default beside it), never the default as the value; the Settings window shows those as values it cannot show.
- **A Save that moves bosun's wake** goes to Terminal (`bosun settings set` there asks for the password, `sudo -v`,
  and writes nothing when that fails); the window says to finish it there, and shows what bosun read before.
  Without a terminal to ask in, `settings set` moves the wake only where sudo needs no password (`sudo -n -v`), and
  refuses otherwise. A failed `pmset` or reload puts `bosun.env` back from the backup the writer made.
- **The Settings window is SwiftUI in the AppKit app** (`app/Sources/BosunMenu/SettingsWindow.swift`), its state
  and its Schedule draft in the core (`Settings.swift`, `SettingsSchedule.swift`), so a refused Save, a hand-off to
  Terminal and a reopen all show what bosun read, never an unsaved draft.
- **The Stop flow's words speak of stopping** ("bosun cannot say what stopping would end", "Stopping…", Cancel),
  and of quitting only where the app quits too (Quit bosun.app's "Stop bosun and Quit…").
- **The Mac VM run** sets the clone's time zone so that its local time is about noon, since the restart in the
  hours and the watchdog's held alert depend on the hour; for Resting it moves the hours (a Save of
  `BOSUN_AWAKE_UNTIL` an hour back), not the clock, since launchd keeps the zone it started with until the restart
  and ran the morning job when the clock passed its time in that zone.
