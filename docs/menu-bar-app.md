# The menu bar app

`bosun.app` puts a small icon in the menu bar that says whether bosun is well. Its menu has what you would
otherwise type: start and stop bosun, check for updates, open the console, and change the schedule. It is there
from login, whatever the hour; bosun itself still starts on your schedule. It lives in `~/Applications` and has
no Dock icon. It reads only what bosun's own `--json` commands print, so it never disagrees with them, and it
changes settings only through [`bosun settings`](commands.md#changing-settings).

Design: [design/menu-bar.md](design/menu-bar.md) and [design/control-panel.md](design/control-panel.md).

## Installing it

`bosun init` offers it: "Add bosun to the menu bar, and start it when you log in?", with three answers.

| Answer | What you get |
|---|---|
| Show bosun in the menu bar and start it during your hours (recommended) | The app, opened at login, and bosun started when you log in during your hours |
| Show bosun in the menu bar only | The app, opened at login. bosun starts at your start time, or when you click Start |
| No menu bar app | No app. The wizard then asks "Start bosun when you log in?" alone, since that works without the app |

Either of the first two also runs the console as a background job, which Open Console needs. To add the app
later, or to see what is installed:

```bash
bosun app install     # download the release this version of bosun expects, verify it, place it in ~/Applications
bosun app status      # what is installed, and what this version wants
```

`bosun app install` accepts only the publisher's notarized build: one whose signature meets the publisher's
Developer ID requirement and that Gatekeeper accepts. Otherwise it says why in one line and leaves
`~/Applications` as it was. `bosun update` keeps an installed app current, and never adds one. Set
`BOSUN_MENU_APP=1` in `bosun.env` if you want the watchdog to tell you when the app is gone.

Working on bosun itself? `bosun app install --build` builds the app on your Mac instead: see
[Building the app](development.md#building-the-app).

## The menu

The first line is a coloured dot and one sentence, with grey lines of detail under it:

- **Green** while bosun runs, with how many windows wait for you and until when the Mac is kept awake.
- **Grey** while bosun rests outside your hours ("Resting: outside your hours · next start Mon 08:00"), or after
  you stopped it.
- **Yellow** with a warning, and **red** with an error. "Show What's Wrong..." lists what is wrong.

The items, in order: Start bosun (only while bosun is not running; "Start bosun now" outside your hours), Show
What's Wrong... (yellow and red only), Open Console, Check for Updates..., Settings... (⌘,), Help, Stop bosun...
(while bosun runs) and Quit bosun.app (⌘Q).

- **Show What's Wrong...** also names a window that needs you (at a first-run or trust screen, a question of your
  shell's startup files, or a prompt) and offers "Open <window>": Terminal running `bosun attach <window>`, where
  you answer it. Closing that Terminal window leaves the window running.
- **Check for Updates...** runs `bosun update --check`. When there is an update, the icon gets a badge and the
  item opens what is new, with Update Now, Remind Me Later and Skip This Version. Update Now shows what
  [`bosun update`](updating.md) would do, then runs it.
- **Open Console** opens the [console](console.md), signed in, in your browser. It is greyed while the console
  is not running.
- **Help** has the bosun guide, Run bosun doctor, Show Logs, and Uninstall bosun..., which opens Terminal running
  [`bosun uninstall`](updating.md#uninstall) after asking you to confirm.

<details class="note" markdown>
<summary>Every status the menu can show</summary>

| Dot and sentence | Meaning | What the menu offers |
|---|---|---|
| Green, "bosun is running" | Windows are open, with how many wait for you and until when the Mac is kept awake | Everything but Start, with "Stop bosun..." |
| Green, "bosun is running · outside your hours" | Windows are open outside your hours: you started it then, or a morning session runs on past `BOSUN_AWAKE_UNTIL`. Its check-ins and events run while it runs, whatever the hour; only the keep-awake ends at its time | As green |
| Grey, "Resting: outside your hours · next start Mon 08:00" | Not running, all well, outside your hours (not one of `BOSUN_DAYS`, or outside `BOSUN_START_TIME` to `BOSUN_AWAKE_UNTIL`), with the next automatic start (no "next start" when none is scheduled) | "Start bosun now" first |
| Green, "bosun is starting (the morning run)" | The morning run is setting bosun up and no window is open yet: at your start time, or after Start bosun now outside your hours (yellow, "..., with a warning", if something warns meanwhile) | Everything but Start and "Stop bosun...": the run is the start (opening `bosun.app` starts no second one) |
| Yellow, "bosun is running, with a warning" | The watchdog is not running, a check warns, there were errors in the last hour, or a window could not start | "Show What's Wrong..." lists them; everything else as green |
| Red, "bosun is not running" | It should be (inside your hours, and you did not stop it), or the morning run failed; a quit that did not finish shows as an error too | "Start bosun" first, then "Show What's Wrong..." |
| Red, "bosun is running, with an error" | Windows are open and a check failed. "bosun's windows could not be read" is treated the same: bosun may be running | "Show What's Wrong..." first, and no Start (opening `bosun.app` does not start it again either) |
| Grey, "bosun is stopped" | You stopped it: Stop bosun..., `bosun quit`, or `bosun stop` earlier today ("bosun is stopped (bosun stop at 18:40)", until your hours end) | "Start bosun" first; Open Console is greyed, and Quit bosun.app quits at once, with no dialog |
| Red, "bosun cannot be reached" | `bosun status --json` failed, timed out with no earlier status to show, or its reply could not be read (a timeout after an earlier status shows that status with "(not current)") | Only Help, with Run bosun doctor (Show Logs and Uninstall are greyed), and "Quit bosun.app"; there is no Start |
| Red, "bosun cannot be run" | There is something at `~/.local/bin/bosun` that cannot be run (a broken link, no permission) | Only Help (Run bosun doctor, Show Logs and Uninstall are greyed) and "Quit bosun.app" |
| Grey, "bosun is not installed" | The app found no bosun | Only Help and "Quit bosun.app" |
| Grey, "Checking bosun..." | The first status has not come yet | Only Help and "Quit bosun.app" |
| Yellow, "bosun.app needs an update" | bosun speaks a newer status contract than this app reads | "Update bosun...", which runs `bosun update` (greyed, saying how the update goes, while one is under way), then Help and "Quit bosun.app"; if bosun is already up to date, `bosun app update` in a terminal brings the app it expects |
| Yellow, with bosun's own sentence | A level this app does not know yet (from a newer bosun) | As for a warning: "Show What's Wrong...", and no Start |

A status older than the last good one says "(not current)" after its sentence. The icon shows a small dot for a
warning, an error or an update ready. An ERROR line in the logs makes the menu yellow for the hour after it was
logged. A watchdog that has stopped running makes it yellow too, but only once the Mac has been awake long enough
for it to have run (three of its five-minute intervals), so a morning wake or a restart does not. How the status
is worked out: [design/menu-bar.md](design/menu-bar.md).

</details>

## Stop, Quit and Start

The app is the control panel and bosun's session is the worker, and the menu keeps them apart:

| Item | What it does |
|---|---|
| **Stop bosun...** | Ends bosun's session: the windows, the check-ins and the console. The app stays, grey, with Start. A dialog lists what stops, with two boxes, both on by default: "Pause the morning start until I open bosun again", and, while the Mac is kept awake, "Let this Mac sleep now" (unchecked, the keep-awake runs to its end). It never touches your ledger, ontology or config. In a terminal, the same is `bosun quit`. |
| **Quit bosun.app** (⌘Q) | Closes the app. While bosun runs it asks first: Leave bosun Running (the default: bosun goes on without its icon), Stop bosun and Quit... (the Stop dialog, then the app quits), or Cancel. |
| **Start bosun** | Does what the morning run does, at any hour: it starts bosun now, even outside your hours. Only the automatic starts (the morning job and the start at login) keep to your hours. |

If agents are in the middle of a task when you stop, a second dialog names them: Let Them Finish waits for them
(up to `BOSUN_QUIT_WAIT_MINUTES`, 10 by default), Stop Them Now stops at once, and Cancel changes nothing. A window
waiting for you at a question is not waited on; it is stopped with the rest.

After a stop, bosun's background jobs stay loaded and do nothing until you start again: the watchdog and learning
skip their runs, and so does the morning job if you paused it. Nothing is left running that you did not choose to
keep.

<details class="note" markdown>
<summary>More on stopping and starting</summary>

- **When bosun cannot say what stopping would end** (it could not read its windows, did not answer in time, or
  failed without saying more), the dialog says so instead, with Cancel and Stop Now, which ends every window
  without waiting; from Quit bosun.app it also offers "Quit bosun.app", which only quits the app. A stop that
  bosun refuses (an update is running) only says why.
- **While the stop runs**, a grey line under the status says what it does: "Stopping: waiting for <names> to
  finish..." while agents finish, "Stopping bosun now..." otherwise ("Quitting: ..." when the app quits too). The
  app gives `bosun quit` as long as bosun says it may take, so a stop is never cut short half way.
- **If agents are still busy when the wait ends**, a third dialog, "Still busy", offers Keep Waiting, Stop It Now
  and Cancel (Cancel Quit from Quit bosun.app).
- **Start** has launchd start the run (`bosun morning --now --background`), so that macOS's privacy prompts name
  bosun's own processes and not the app. It is greyed on a status that is "(not current)", and offered after a
  quit that did not finish, where the choice is yours.
- **Opening `bosun.app`** from Finder or Spotlight starts bosun too, but only when it is stopped, or red with no
  window open: never while bosun runs, even with an error, never while it rests outside your hours, and not after
  a quit that did not finish. At login the app only shows its icon.

</details>

## Starting at login

With `BOSUN_START_AT_LOGIN=1` (the default for a new setup), bosun starts when you log in, if:

- today is one of your days, and now is inside your hours (`BOSUN_START_TIME` to `BOSUN_AWAKE_UNTIL`);
- you have not quit it with the morning start paused (that holds on any day, until you start it again), or
  stopped it earlier the same day;
- and it is not running already.

The day's morning log then says "starting bosun: you logged in during your hours", or why it did not start. This
is bosun's own login job, so it works without the app; the app's Open at Login only shows the icon. A reload of
bosun's jobs (an update, `install.sh`) never starts it. With `BOSUN_START_AT_LOGIN=0`, bosun starts at your start
time, or when you start it. An install from before the setting keeps it at `0`.

## Settings

Settings... (⌘,) opens the bosun Settings window. It shows what bosun reads, and changes a setting only through
`bosun settings set`, which writes `bosun.env` as `bosun init` does.

- **General:** Open bosun.app at login, Start bosun when you log in during your hours (`BOSUN_START_AT_LOGIN`),
  Updates from releases or main (`BOSUN_UPDATE_CHANNEL`), and Change windows and connectors..., which opens
  Terminal running `bosun init` for the windows, connectors, agents, models and permissions. Open Config Folder
  and Show Logs are here too.
- **Schedule:** your days (`BOSUN_DAYS`), Start at (`BOSUN_START_TIME`) and Keep the Mac awake until
  (`BOSUN_AWAKE_UNTIL`), said back in a line ("bosun starts at 08:00 on weekdays, and lets the Mac sleep after
  20:00"), and Save. Saving reloads bosun's jobs, so the morning job runs at the new time, and a new "awake until"
  moves the end of a keep-awake that is running.

When the days or the start time change and bosun set the Mac's wake, moving the wake needs an administrator:
Save opens Terminal running `bosun settings set`, which asks for your password first and writes nothing if you
cancel. Another app's repeating wake is left as it is, and the window says so.

<details class="note" markdown>
<summary>More on the Settings window</summary>

- If the app could not tell whether Open at Login is on (`bosun app status` did not answer), it says "unknown",
  with Turn On and Turn Off, either of which makes it so, instead of a switch that would look off.
- A value the window cannot show (a `bosun.env` that cannot be read, a value bosun does not take) is shown as
  such, with the wizard as the way to fix it.
- While Terminal asks for your password, the window shows the schedule bosun read before; when you come back to
  it, it reads `bosun.env` again and shows what is there. Coming back to the window from another app keeps an
  unsaved edit while `bosun.env` is as it was.
- The rules `bosun settings set` checks, and how it decides whose wake it is:
  [Changing settings](commands.md#changing-settings).

</details>

## Removing the app

Moving `bosun.app` to the Trash does not uninstall bosun: bosun keeps running, and with `BOSUN_MENU_APP=1` the
watchdog tells you once that the app is gone (`bosun app install` brings it back). `bosun app remove` quits the
app, turns Open at Login off and deletes it, and leaves bosun running. To take everything off, use Uninstall
bosun... in the Help menu, or [`bosun uninstall`](updating.md#uninstall).
