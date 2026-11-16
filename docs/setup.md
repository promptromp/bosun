# Setup

This page gets bosun running on your Mac: what you need, the install, and the first start. After that it covers
the menu bar app, updating, configuration, the other agents, logins, and taking bosun off again.

To get going, read [Requirements](#requirements) and [Install](#install). The rest is there for when you need it.

## Requirements

- **A Mac** on macOS, Apple silicon or Intel. To let bosun wake the Mac on schedule, your account must be an
  administrator: bosun asks for your password once, for `sudo pmset`.
- **[Homebrew](https://brew.sh)**, which also brings Apple's command line tools.
- **A few tools:** `brew install tmux jq python gh`. bosun needs Python 3.13 or newer (the one macOS ships is
  older). The system's `git`, `ruby` and `curl` are fine.
- **The [GitHub CLI](https://cli.github.com)**, logged in: `gh auth login`.
- **[Claude Code](https://claude.com/claude-code)** 2.1.283 or newer, logged in, and opened once by hand: run
  `claude`, go through its first screens, then `/exit`. The orchestrator always runs on Claude Code.
- **Optional:** [Codex](#running-repo-sessions-on-codex) or [Antigravity](#running-repo-sessions-on-antigravity-a-preview)
  (a preview), to run some or all repo windows on another agent.

You don't have to check these yourself. The installer runs `bosun preflight`, which changes nothing, lists
everything that is missing at once, and says what to do about each. You can run it any time, and `bosun doctor`
starts with the same check.

<details class="note" markdown>
<summary>Your shell, oh-my-zsh and prompt themes</summary>

Each window starts through your login shell, zsh or bash, as a terminal you open does (`<shell> -lic`), so your
PATH and tools load. oh-my-zsh and Powerlevel10k work as they are. In bosun's windows oh-my-zsh's update question
is switched off (`DISABLE_AUTO_UPDATE=true`), since it would wait for a key and the window would never start its
agent; this holds as long as your `.zshrc` picks no update mode with `zstyle`. The helpers a prompt theme starts
are stopped when a window's launch ends, and `bosun orphans` lists any left behind.

</details>

### A typical setup

bosun works on a Mac you leave signed in. At your start time it wakes the Mac (a repeating `pmset` wake, a
minute after the start time), keeps it awake through your hours (`caffeinate`), and lets it sleep after your
"awake until" time. Between those, the Mac sleeps as usual. For that to work:

| The Mac | Why |
|---|---|
| Stays logged in to your account. Asleep is fine | bosun's jobs run in your login session. After a restart or a log-out nothing runs until you log in again; then, by default, bosun starts by itself if it is one of your days and inside your hours ([Starting at login](#starting-at-login)). |
| On a laptop: is on its power adapter when bosun should be awake | Part of the keep-awake (`caffeinate -s`) holds only on power, and bosun counts on power for the scheduled wake. `bosun doctor` warns while the Mac runs on battery. |
| On a laptop: has the lid open, or closed with an external display | With the lid closed and no external display, macOS keeps the Mac asleep whatever bosun asks. It only wakes for moments in the background, and work started then can hang. |
| Keeps your login keychain unlocked while it sleeps | Claude Code keeps its login there, and after an unattended wake nobody is there to unlock it. `bosun doctor` fails if the keychain locks on sleep. |

So a typical setup is a desktop Mac, or a laptop that stays on its power adapter at your desk, with the lid open
or connected to a display. It wakes for the morning, works through your hours, and sleeps in the evening and
overnight.

`bosun doctor` checks the power, the lid and the keychain. To see a real wake on your own Mac, run
`bosun wake-test`: it schedules a wake a few minutes ahead and puts the Mac to sleep, and `bosun wake-test
--report` says afterwards what happened.

## Install

1. **Run the install line** in Terminal:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh
    ```

    It puts the newest release of bosun in `~/.local/share/bosun` and starts `bosun init`. It installs nothing
    else.

2. **Install what it says is missing**, if anything, then run the same line again. It lists everything at once,
   with the lines to run. On a new Mac most of the time goes to Homebrew.

3. **Answer the questions** ([below](#the-questions)). Each takes one key: Return takes the recommended answer,
   `r` starts again, and `q` leaves with nothing changed. bosun recommends its answers from what your Mac already
   shows: the repos you use your agents in, the connectors `claude mcp list` shows, your usual hours, your logins.
   It says why it recommends each. (`NO_COLOR=1` turns its colours off.)

4. **Accept the plan.** It lists every file and command before it touches anything. Then it writes your config
   to `~/.config/bosun/`, trusts your repo folders in your agents, loads bosun's background jobs, and asks for
   your password once, for the wake.

5. **Start bosun.** The last question is "Start the windows now?". Say yes (see [Starting bosun](#starting-bosun)).

How it decides, and what it never does: [design note](design/init.md).

### The questions

| Question | What it sets |
|---|---|
| Which repos should get a window? | One window per repo you tick. The one you use most is ticked, and the others it found are listed to add. |
| Which connectors may the orchestrator use? | The MCP servers and connectors the orchestrator may reach. Nothing is ticked, because only you know which are work tools: tick the ones it needs, such as Slack and your tracker. Asked only when `claude mcp list` shows some. Repo windows get none ([Connectors](#connectors-what-each-window-may-reach)). |
| When should bosun run? | Your days, start time and "awake until" time, recommended from when you usually work. |
| How proactive should it be? | Events, approvals in chat, the evening learning run and the [ontology](knowledge.md#in-the-agents-hands) in the agents' hands, all recommended. With Slack's plugin installed, also the Slack watcher. |
| Which agent should run the repo sessions? | Claude Code, or Codex or Antigravity. Asked only when one of those is installed and logged in. |
| Which model should the sessions use? | `opus` or `sonnet` for the windows (for the orchestrator, when repo windows run on another agent). |
| How should repo sessions handle permissions? | Claude Code's auto mode (recommended) or no permission checks. Asked when repo windows run on Claude Code. See [Permissions](permissions.md). |
| Should the Mac wake by itself? | The repeating `pmset` wake. Recommended, unless another app already set a repeating wake (`pmset` keeps only one). |
| Add bosun to the menu bar, and start it when you log in? | The [menu bar app](#the-menu-bar-app), and whether bosun starts when you log in during your hours. Answer "no" and it asks the second part alone. |
| Which AWS SSO profiles should each morning log in to? | The profiles the morning run logs in to ([AWS SSO](#aws-sso)). Asked only when `~/.aws/config` has some. |

Run `bosun init` again whenever you like. It starts from your config, keeps anything you added to `bosun.env`,
backs up what it changes (`bosun.env.pre-init-<time>`), and never changes your `setup.sh` once it exists.

### Starting bosun

If you said yes to "Start the windows now?", bosun is running: the orchestrator starts its morning chores, and
each repo window starts its session. `bosun attach` opens the windows (detach with the tmux prefix and `d`;
they keep running), and `bosun console` shows what they are doing.

If you said "Not now", or set up with `--recommended`, `--answers` or `--unattended`, nothing runs yet. bosun
starts by itself at your next start time on one of your days, or, if it starts at login, when you next log in
during your hours. To start it now, run `bosun start`, or choose Start bosun in the menu bar app.

From then on bosun runs on its own. [How a day runs](day.md) says what happens when, and how to stop it.

### If you get stuck

`bosun preflight` tells you what is still missing, and once bosun is installed, `bosun doctor` tells you what is
wrong. The usual ones:

| What you see | What it means | What to do |
|---|---|---|
| `missing  Homebrew` | Nothing to install the tools with | Install it from [brew.sh](https://brew.sh), do what it prints at the end to add it to your PATH, and open a new terminal |
| A tool you just installed is still `missing` | Your terminal has the old PATH | Open a new terminal |
| `first-run screens not done` | Claude Code stops at its theme and security screens the first time, and a window nobody watches would wait there for ever | Run `claude`, go through them, then `/exit` |
| `not logged in` under Claude Code | It has no login | Run `claude`, and follow what it asks |
| `not logged in: the login keychain is locked` | Claude Code keeps its login in your login keychain, and a session over ssh starts with it locked, even when the Mac's desktop unlocked it | Run `security unlock-keychain ~/Library/Keychains/login.keychain-db` (your login password), then the same line again |
| `not logged in` under Codex | It has no login | Run `codex login`. With an API key: `printenv OPENAI_API_KEY \| codex login --with-api-key` |
| `later  Claude in Chrome's screen not seen` | bosun starts windows with Chrome on (`--chrome`), and some versions of Claude Code first show what it may do in your browser and wait for Enter. Nothing stops for it now | Nothing now. If a window does stop at that screen, the watchdog names it (`stalled-start`): answer it there once, with `bosun attach <window>` |
| A window is on a screen, and the watchdog reports `stalled-start` | The agent is asking something only you can answer, usually whether to trust the folder | `bosun attach <window>` (the alert names it), then read it. Don't just press Return: on several of Claude Code's screens the default is to exit, and on Codex's it says yes |
| A window answers "Credit balance too low · Add funds" while `claude -p` still works | Claude Code is signed in with an API key whose organization has only subscriber credits ("Promotional credits"), which do not cover Claude Code | Buy credit for the key's organization, or use a login (see [With an API key](#with-an-api-key-instead-of-a-login)) |
| `its agent, ..., is not installed for this window's shell` in a window | The window's shell cannot find its agent (Claude Code, Codex or `agy`), so the window stays a plain shell; you also get an alert | Install it, or name its path in `bosun.env` (`BOSUN_CLAUDE_BIN`, `BOSUN_CODEX_BIN`, `BOSUN_AGY_BIN`) |
| `bosun.env does not parse, so none of it was read` | An edit left a line bash cannot read, such as an unclosed quote. Half a file would be a config that is neither yours nor the defaults, so every command stops instead | Fix the line it names, or move the file aside to run on the defaults |
| `bosun.env stops before its end when read, so none of it was read` | A line stops the shell that reads it, usually a variable that is not set where bosun runs (`$SOMETHING` from your shell's startup files, which launchd's jobs do not have) | Set the value in the file itself, or remove the line |
| `warn  bosun.env sets BOSUN_...: bosun does not read it` in `bosun doctor` | A typo, or a key bosun no longer has; it names the key you most likely meant | Fix the name. A renamed key is changed for you by `bosun update` |
| `run bosun as yourself, not as root` | You ran it with `sudo`, which would leave root's files in your folders | Run it without `sudo`: it asks for `sudo` itself, once, for the wake |
| `another bosun start or refresh is setting up the windows` | Two runs at once, such as the morning job and one you typed. One sets the windows up while the other waits, up to 3 minutes | Nothing, usually: the first one does the work. Run it again if you need to |
| `... and this runs inside it` (`start --restart`, `update`) | You ran it in one of bosun's own windows, which it would stop or wait on | Run it from another terminal. `bosun stop` works from inside: it stops its own window last |
| `does not run on <your OS> yet`, or `bosun runs on macOS today` | bosun runs on macOS today, and Linux is next | See the [roadmap](../ROADMAP.md) |

If none of these fit, open an issue with what `bosun preflight` and `bosun doctor` print:
<https://github.com/promptromp/bosun/issues>.

### Other ways to install

| You want to | Run |
|---|---|
| See what it would do, and change nothing | `curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh \| sh -s -- --dry-run` |
| Run the repo windows on Codex (it checks Codex is ready too) | `curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh \| BOSUN_HARNESS=codex sh` |
| Answer no questions | `curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh \| sh -s -- --unattended` |
| Take the recommended setup, with no questions | `bosun init --recommended` |
| Give your answers in a file | `bosun init --print-answers > answers.json`, edit it, then `bosun init --answers answers.json` |

`--unattended` takes the recommended setup, but on a first install without repo windows, connectors or the menu
bar app, since nobody chose them; its last line says so. It does set bosun to start when you log in during your
hours. Run `bosun init` afterwards to add the rest, or give them in an answers file, which needs only what it
changes (`repos`, `orchestrator_connectors`, `days`, `start`, `until`, `events`, ...). On a Mac where bosun is
already installed, `--unattended` goes ahead with the update without asking. Without a terminal, or with
`--dry-run`, `bosun init` only prints its plan.

<details class="note" markdown>
<summary>Where it installs, and installing from a branch</summary>

[`get.sh`](../get.sh) keeps each version of bosun in a folder of its own in `~/.local/share/bosun-versions`,
beside the one repository they come from (`bosun.git`), and makes `~/.local/share/bosun` a link to the version in
use. A new install gets the newest release (a tag `v<YYYY.MM.DD>`), or the head of `main` while a repository has
no release yet.

`BOSUN_DIR`, `BOSUN_REPO` and `BOSUN_BRANCH`, set after the pipe as `BOSUN_HARNESS` is above, change where it goes
and what it gets. With `BOSUN_BRANCH` it gets the head of that branch, and the install follows the branch rather
than the releases (`bosun init` writes `BOSUN_UPDATE_CHANNEL="main"`). On an install that is already there, the
defaults are what it came from. The line it prints to run again keeps these settings, and never shows a password
or token in the address. How the channels and an existing install are handled:
[design/update.md](design/update.md#channels).

</details>

### Installing from a checkout

If you prefer your own checkout, and to write the config yourself:

```bash
git clone https://github.com/promptromp/bosun.git ~/Development/bosun
cd ~/Development/bosun
./install.sh --example generic        # seeds ~/.config/bosun from examples/generic
$EDITOR ~/.config/bosun/{bosun.env,sessions.yml,setup.sh}
./install.sh                          # run it again after changing the schedule
sudo pmset repeat wakeorpoweron MTWRF 08:01:00   # install.sh prints the exact line;
                                                 # ./install.sh --with-wake runs it for you
bosun doctor
```

`install.sh` can be run again safely and never overwrites your config. It links `bosun` into `~/.local/bin` and
loads bosun's [background jobs](#background-jobs). A checkout of your own updates with `git pull`, not with
`bosun update`. [`bosun uninstall`](#uninstall) takes bosun off as from an install, and leaves the checkout itself.

<details class="note" markdown>
<summary>Why the wake comes a minute after the start time</summary>

launchd runs a calendar job that fell due during sleep as soon as the Mac wakes. Waking a minute *after* the
job's time (`BOSUN_WAKE_DELAY_MINUTES`) means the morning job starts within seconds of the wake and takes its
keep-awake at once. Waking *before* the job would leave a gap in which an unattended Mac could doze off again.

</details>

## The menu bar app

`bosun.app` puts a small icon in the menu bar that says whether bosun is well. Its menu has what you would
otherwise type: start and stop bosun, check for updates, open the console, and change the schedule. It is there
from login, whatever the hour; bosun itself still starts on your schedule. It lives in `~/Applications` and has
no Dock icon. It reads only what bosun's own `--json` commands print, so it never disagrees with them, and it
changes settings only through [`bosun settings`](commands.md#changing-settings).

Design: [design/menu-bar.md](design/menu-bar.md) and [design/control-panel.md](design/control-panel.md).

### Installing it

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

### The menu

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
  [`bosun update`](#updating) would do, then runs it.
- **Open Console** opens the [console](console.md), signed in, in your browser. It is greyed while the console
  is not running.
- **Help** has the bosun guide, Run bosun doctor, Show Logs, and Uninstall bosun..., which opens Terminal running
  [`bosun uninstall`](#uninstall) after asking you to confirm.

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

### Stop, Quit and Start

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

### Starting at login

With `BOSUN_START_AT_LOGIN=1` (the default for a new setup), bosun starts when you log in, if:

- today is one of your days, and now is inside your hours (`BOSUN_START_TIME` to `BOSUN_AWAKE_UNTIL`);
- you have not quit it with the morning start paused (that holds on any day, until you start it again), or
  stopped it earlier the same day;
- and it is not running already.

The day's morning log then says "starting bosun: you logged in during your hours", or why it did not start. This
is bosun's own login job, so it works without the app; the app's Open at Login only shows the icon. A reload of
bosun's jobs (an update, `install.sh`) never starts it. With `BOSUN_START_AT_LOGIN=0`, bosun starts at your start
time, or when you start it. An install from before the setting keeps it at `0`.

### Settings

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

### Removing the app

Moving `bosun.app` to the Trash does not uninstall bosun: bosun keeps running, and with `BOSUN_MENU_APP=1` the
watchdog tells you once that the app is gone (`bosun app install` brings it back). `bosun app remove` quits the
app, turns Open at Login off and deletes it, and leaves bosun running. To take everything off, use Uninstall
bosun... in the Help menu, or [`bosun uninstall`](#uninstall).

## Updating

```bash
bosun update --check     # is there a newer release? Changes nothing
bosun update             # shows the plan, and asks
bosun update --dry-run   # only shows the plan
bosun update --yes       # goes ahead without asking
```

Running the install line again does the same as `bosun update`. The menu bar app's Check for Updates... runs the
same check, and `bosun update --skip <version>` stops a release being offered again.

An update moves bosun to the newest release (or, on the `main` [channel](#update-channels), to the head of its
branch) while your agents keep working:

1. It gets the new version into a folder of its own, beside the one you run.
2. That version shows its plan: the commits, the migrations your data needs, the prompts that changed, what each
   window gets, the background jobs to reload, and anything that stops it. In a terminal it asks first; without
   one, it stops at the plan.
3. It rehearses the migrations on a copy of your data.
4. It holds bosun's own background work (the check-in loop, the watchdog, learning, the morning job) and pauses
   writes for a moment. It backs up your ledger, ontology and settings (`backups/` in the state folder, the last
   3 kept), migrates them, switches `~/.local/share/bosun` to the new version and checks each store. Writes that
   arrive meanwhile wait, and land on the new version.
5. If any of that fails, it puts everything back as it was and says what failed.

How it works: [design note](design/update.md).

### What happens to the windows

| The window | What the update does |
|---|---|
| Nothing it was started with changed (most updates) | Leaves it alone: its next `bosun` command runs the new version |
| Only the orchestrator's prompts changed (`policy.md`, the task files) | Asks the orchestrator to re-read them once it is idle |
| Something it was started with changed (its settings, hooks, plugin) | Restarts it on the new version once it is idle, in the same conversation. If only background work runs there, it first asks the window to list that work, and passes the list back after the restart, as a restart ends it |
| Busy, waiting for your answer, or someone typing in it | Waits, and never restarts one that waits for you |
| One bosun did not start (a shell or a Claude you opened in its tmux session, outside `sessions.yml`) | Leaves it as it is, and says so in the plan |

Each window gets a prompt to check for anything it missed and carry on, and answers with its status. The update
waits for every answer and says which windows it is waiting for. Ctrl-C stops that wait, and the check-in loop
brings the rest along once each is idle. A window opened while you answer, or during the update, is brought
along too; one opened after it runs the new version from its next `bosun` command, and keeps what it was started
with.

Tasks already handed to a repo window keep the text they had. The summary lists them, so you can send a
`bosun delegate --update` where a changed rule matters.

### When it is done

It ends with a summary: the commits, the migrations, the config it changed and its new keys, anything you have
to change by hand in your own files, the prompts that changed, the jobs reloaded, what came of each window,
anything that fails now that did not before, and where the backup is. The summary is saved as `summary.md` in the
backup's folder, and its last line points to [Going back](#going-back). `bosun doctor` lists what the last update
left for you to do.

An update cut short (Ctrl-C, a crash, a power cut) is put back, or finished, by the next `bosun update`.

**Your config.** A new key needs nothing: its default applies. A key that was renamed or removed is changed in
`bosun.env` for you, and so is a key whose new default would change what your install did: the update writes it
with the value that keeps your install as it was, under a comment saying why. Either way it first keeps the file
as `bosun.env.pre-update-<time>`, and the plan shows the change before you say yes.

<details class="note" markdown>
<summary>The keys an update has written so far</summary>

- **`BOSUN_ORCHESTRATOR_CONNECTORS="all"`:** an install from before
  [connectors](#connectors-what-each-window-may-reach) keeps every connector for its orchestrator, and
  `bosun doctor` warns until you name the ones it needs. Repo windows start without connectors from then on, a
  repo's own `.mcp.json` servers included: give one back with `BOSUN_CONNECTORS`, or `connectors:` in its
  `sessions.yml` entry. A checkout you update with `git pull` gets no such change: set the key yourself, or the
  orchestrator starts with none.
- **`BOSUN_UPDATE_CHANNEL="main"`:** an install that was updating before releases existed goes on following the
  head of `main`, and the plan and summary say how to follow the releases instead.
- **`BOSUN_MENU_APP`, `BOSUN_CONSOLE_ALWAYS` and `BOSUN_START_AT_LOGIN`, all `"0"`:** an install from before the
  menu bar app stays as it was: no app, no console job, no start at login.

A Claude Code window that a settings file would give servers its list does not name
([Connectors](#connectors-what-each-window-may-reach)) does not start on the new version. The summary says it is
still waited for, and its pane, the day's launch log and `bosun doctor` say why. Name those servers in its list,
or remove them from that file.

</details>

### Update channels

| `BOSUN_UPDATE_CHANNEL` | Updates to |
|---|---|
| `releases` (the default for a new install) | The newest dated release, a tag `v<YYYY.MM.DD>` |
| `main` | The head of the branch the install follows (`main`, or the `BOSUN_BRANCH` it was installed from) |

The Settings window's "Updates from" sets it, and so does `bosun settings set BOSUN_UPDATE_CHANNEL=main`.

### The first update of an older install

An install from before versions side by side is a checkout in `~/.local/share/bosun`, and its bosun has no
`bosun update`. Running the install line again converts it, once:

1. The new version's `bosun update` shows its plan and asks.
2. It waits for a quiet moment: every window idle with nothing running, and nobody typing in the session for
   `BOSUN_ACTIVE_CLIENT_MINUTES`. It says what it waits for, and Ctrl-C there changes nothing.
3. It swaps the checkout for the link in one step, keeps the checkout as
   `~/.local/share/bosun-versions/legacy-<its commit>`, and restarts each of bosun's own windows (the
   orchestrator, and those in `sessions.yml`) on the new version.

If anything fails, the old checkout runs again, as it was, with the background work it stopped started again;
after a crash, run the install line again, which does that. It will not convert a checkout with anything of yours
in it: changes, a stash, commits not pushed, another worktree, a file git ignores. Take that out first. The update
after it deletes the kept checkout, unless something of yours is in it by then. How the conversion works, step by
step: [design/update.md](design/update.md#getsh-on-an-existing-install).

## Going back

There is no command for it yet. A failed update puts everything back by itself; going back later is by hand. You
need the version the update came from and the backup it took, both named in its summary:

1. **Stop what writes:** `bosun quit --stop-now` (or `bosun stop`), and unload the watchdog, learning and console
   jobs: `launchctl bootout gui/$(id -u)/com.bosun.morning.watchdog`, and the same for `.learn` and `.console` if
   you have them (with your `BOSUN_LAUNCHD_LABEL` if you set one).
2. **Find the older version's folder** in `~/.local/share/bosun-versions`. An update keeps the one it came from
   until the next update. After the first update, the version before it is the checkout it kept,
   `legacy-<its first 12 characters>`. If the folder is gone, add it again from its commit: the backup's
   `manifest.json` names the whole commit as `from` (the summary's first line shows only 7 characters, which
   `git --git-dir ~/.local/share/bosun-versions/bosun.git rev-parse <those 7>` turns into the whole one), then
   run `git --git-dir ~/.local/share/bosun-versions/bosun.git worktree add --detach
   ~/.local/share/bosun-versions/<its first 12 characters> <commit>`.
3. **Put the backup back:** each entry in its `state/` folder over the one in `~/.local/state/bosun`, and each in
   its `config/` folder over the one in `~/.config/bosun`, replacing the newer one whole (its `manifest.json` lists
   every file). Anything written since the update is not in it.
4. **Point the link at the older version** (`ln -sfn ~/.local/share/bosun-versions/<folder>
   ~/.local/share/bosun`), run its `~/.local/share/bosun/install.sh` to load its jobs again, and `bosun start`.

`bosun update` would take you forward again, to the newest release, or on the `main` channel to the head of the
branch (from the checkout kept from before versions, which has no `bosun update`, the install line does).

Going back is only to the version the update came from, and never past a `kg forget`: deleting a person deletes
every update backup there is, since restoring one would bring the person back, and says so. A summary whose backup
a forget deleted says that going back past it is no longer possible.

## Uninstall

```bash
bosun uninstall             # says what goes and what stays, then asks
bosun uninstall --dry-run   # only says
```

Run it from a terminal outside bosun's windows, since it stops them. It changes nothing until you have answered
every question, and Return always takes the safe answer: at "Are you sure?" it means no, and at "Keep your
ledger?" it means keep. Ctrl-C at any question leaves everything as it was.

- **What goes:** your windows (stopped), bosun's background jobs, the menu bar app, the repeating wake bosun set,
  the folder trust bosun gave your agents, the memory notes [learning](learning.md) wrote, the empty worktrees
  folders it made beside your repositories, the `bosun` command and the installed versions.
- **What it asks about:** your ledger and learning history, and your ontology. Return keeps them.
- **What stays:** your config in `~/.config/bosun` (`--purge` deletes it too), your repos, branches and pull
  requests, your conversations, your own notes and agent settings, and anything it does not know is bosun's.

If something could not be removed it says what to run, and running it again finishes the job. Install again with
the same line, and bosun picks up what you kept. How it decides: [design note](design/uninstall.md).

<details class="note" markdown>
<summary>Everything it removes, and what it leaves</summary>

| What | What happens |
|---|---|
| Your windows | Stopped, so any work in them stops. It says which are running first, and waits up to 15 seconds for what they started to end; anything still running then (a background command a Codex window started, say) it names and leaves running |
| The launchd jobs (morning, watchdog, learning, console, `.now`, the login job, and the app's Open at Login job) | Unloaded and deleted, before the windows stop, so the watchdog cannot bring them back |
| The menu bar app, `~/Applications/bosun.app` | Asked to quit if it runs, then deleted. The `~/Applications` folder stays |
| The repeating wake | Cancelled with `sudo pmset repeat cancel`, if it is the one bosun set. `pmset` can only cancel every repeating event at once, so another app's wake stays, and it says so |
| Folder trust | Taken back for the folders bosun marked, in Claude Code and Codex. A folder you had accepted yourself stays trusted. It goes by bosun's record of what it changed (`agent-changes.json`); trust from an install older than `bosun uninstall` is not on it, and stays |
| The plugin bosun installed into `agy` | Uninstalled with `agy plugin uninstall`, with what that leaves behind (`plugin_data/bosun/`). Your own `agy` setup is not touched |
| Memory notes from [`bosun learn`](learning.md#learning) | Deleted, with their line in `MEMORY.md`. Your own notes stay |
| The worktrees folders bosun made beside your repositories (`exporter.worktrees` beside `exporter`) | Removed while empty. One with a worktree in it stays, and it says where, since a worktree may hold work that is not committed or pushed. A folder of that name that was there before bosun stays |
| `~/.local/bin/bosun` and bosun's working files | Deleted |
| What `get.sh` installed: `~/.local/share/bosun-versions` and the link `~/.local/share/bosun` | Deleted, last, unless anything of yours is in them: changes, a stash, commits not pushed, a worktree elsewhere, a file git ignores. Then they all stay, link included |
| A checkout of your own you run bosun from | Stays. One that an older `get.sh` made, from before versions side by side, is deleted last, unless anything of yours is in it |
| Your ledger and learning history, your ontology | It asks, and Return keeps them |
| The backups `bosun update` took | Deleted when either the ledger or the ontology is, as each backup holds both. Kept only when you keep both |
| `~/.config/bosun`: your config, the backups `bosun init` and `bosun update` made of it | Stay, and it says where. `--purge` deletes them too, but leaves `forbidden-terms.txt`, which is for working on bosun, not running it |
| Anything else in those folders | Stays, and it names it. It deletes only what it knows is bosun's, and a folder you pointed `BOSUN_STATE_DIR` or `BOSUN_LOG_DIR` at that also holds other files stays whole |

- `/bosun:chores` and bosun's hooks need no removing: bosun passes them to each window it starts, so neither
  Claude Code nor Codex has them installed.
- `replica.json`, this Mac's id in both journals, goes with the ledger, so keeping the ontology alone gives its
  next write a new id.
- For scripts, `--yes` asks nothing and keeps your records; `--delete-ledger`, `--delete-knowledge` and `--purge`
  say otherwise.
- If you deleted bosun's folders by hand, the jobs and the wake are still there. Run the install line again, then
  `bosun uninstall`.
- A first update of an older install that you declined, or that failed before the switch, leaves
  `~/.local/share/bosun-versions` beside the checkout, which the checkout's own `bosun uninstall` does not know
  about. Delete it by hand once nothing of yours is in it: `rm -rf ~/.local/share/bosun-versions`.

</details>

## Configuration

Your configuration lives in `~/.config/bosun/` (`BOSUN_CONFIG_DIR` moves it). `bosun init` writes it, and you can
edit it by hand:

| File | What it holds |
|---|---|
| `bosun.env` | Settings: the schedule, the agents and their flags, models, connectors, and which parts of bosun are on. Every key and its default is in [`examples/generic/bosun.env`](../examples/generic/bosun.env) (on an install, `~/.local/share/bosun/examples/generic/bosun.env`). |
| `sessions.yml` | One entry per repo window: `name` and `dir`, and optionally `harness` (`claude`, `codex` or `agy`), `model`, `effort` and `connectors` for that window, and on Claude Code `autocompact`. The orchestrator is set up in `bosun.env`, not here. |
| `setup.sh` | What the morning run does before the windows start: `setup_steps()`, calling `ensure_claude_login` ([Claude Code login](#claude-code-login)), `ensure_aws_sso <profile>` ([AWS SSO](#aws-sso)), or `run_step "<description>" <command>...` for anything else. |

bosun reads `bosun.env` each time a command runs, so an edit applies at once, also inside running windows. A
setting you give in the environment (`BOSUN_...=value bosun ...`) wins over the file. `bosun doctor` warns about a
key bosun does not read, with the one you most likely meant, and about a word where a number goes. A file bash
cannot read is not read at all: commands stop and say which line ([If you get stuck](#if-you-get-stuck)).

Your own policy and task rules for the orchestrator also go in this folder: see
[Orchestrator](orchestrator.md#delegated-tasks-and-folder-trust).

### Model and effort

Every window bosun starts gets the model and effort your config names, so what it costs does not depend on what
you last picked by hand. (Claude Code's `/model` and `/effort` save your choice as the default for every future
session, which is how sessions drift to Opus on high effort.)

| Windows | Model | Effort |
|---|---|---|
| Repo windows on Claude Code | `BOSUN_MODEL` (`opus`) | `BOSUN_EFFORT` (`medium`) |
| Repo windows on Codex | `BOSUN_CODEX_MODEL` (empty: Codex's own default) | `BOSUN_EFFORT`, passed as `-c model_reasoning_effort` |
| Repo windows on Antigravity | `BOSUN_AGY_MODEL` (empty: its own default) | `BOSUN_EFFORT`, passed as `--effort` (above `high` as `high`) |
| The orchestrator | `BOSUN_ORCHESTRATOR_MODEL` (`opus`) | `BOSUN_ORCHESTRATOR_EFFORT` (`medium`) |

A window's entry in `sessions.yml` can set its own `model:` and `effort:`. Each delegated task also sets an
effort: reviews and investigations `medium`, fixes on your pull requests and new work the window's own. A window
opened for the task starts at it; a window already running runs the task in a subagent at it, so its own setting
is untouched (Codex, which has no such instruction yet, runs it at the window's own). The orchestrator asks for
`--effort high` only when you ask for more depth.

`bosun send --list` shows what each window was launched with, `bosun refresh` restarts idle windows whose launch
settings no longer match your config, and `bosun doctor` shows the configured values and your saved default.

<details class="note" markdown>
<summary>How they are passed to Claude Code</summary>

The model goes in as `--model`, and the effort as a session-level setting (`--settings '{"effortLevel":...}'`)
rather than `--effort`, so `/effort` inside a session can still change it for that session. Empty values leave
Claude Code's own default.

</details>

### Connectors: what each window may reach

A window bosun starts reaches only the MCP servers and connectors its list names; the rest are off for it.

| Key | Whose list | Default |
|---|---|---|
| `BOSUN_CONNECTORS` | every repo window | empty: none |
| `connectors:` in a `sessions.yml` entry | that window, instead of `BOSUN_CONNECTORS` | (the key's) |
| `BOSUN_ORCHESTRATOR_CONNECTORS` | the orchestrator, and `bosun chores --dry-run` | empty: none; `bosun init` writes what you tick |

A list is comma-separated, or a YAML list in `sessions.yml` (`[]` is none). Each entry is a server's name as
`claude mcp list` (and `/mcp`) shows it, or a URL pattern. `all` means every one, and stands alone:

```sh
BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Slack, plugin:acme-tracker:tracker"
BOSUN_CONNECTORS="acme-local, https://mcp.example.com/*"
```

```yaml
sessions:
  - name: api
    dir: ~/Development/api
    connectors: [acme-local]
```

The list is read when the window starts, so a change applies at its next start. A name bosun cannot find is left
out, never let in as more: the launch logs a line, and for the orchestrator it also alerts you. On Claude Code,
Chrome is not a connector: it stays on through `--chrome`.

`bosun doctor` has a line for each window: what it reaches, what it only allows by name or by pattern, and each
name it cannot find. It warns while the orchestrator's list is `all`, or while `bosun.env` does not set it at all.

**Settings files that would widen a list.** Claude Code adds the `allowedMcpServers` of your own
`~/.claude/settings.json`, and of the window folder's `.claude/settings.json` and `.claude/settings.local.json`, to
the window's list. So a window with a list (anything but `all`) does not start while one of those files allows a
server its list does not name. The launch says which file and which entries: name them in the window's list, use
`all`, or remove them from that file.

<details class="note" markdown>
<summary>How a list is applied on Claude Code</summary>

At launch bosun turns the list into Claude Code's `allowedMcpServers`, in the window's `--settings`, in place of
any a site settings file names (with `all`, the site file's applies). A server you added yourself goes by its
name, a plugin's by its URL or command, and a claude.ai connector by the URL `claude mcp list` shows for it.
bosun runs that listing (a few seconds) only when it has to, keeps what it found in `connectors.json` in the
state folder, and uses that if a later listing fails (at a morning wake, before the network is up). A name you
added yourself is matched exactly, so a typo allows nothing. `bosun doctor` looks nothing up, so a claude.ai
connector no start has looked up yet shows as not found until the window's next start.
More: [design/connectors.md](design/connectors.md#claude-code).

</details>

<details class="note" markdown>
<summary>How a list is applied on Codex and Antigravity</summary>

On Codex, a list names servers as `codex mcp list` shows them, and bosun switches off the servers it does not
name. Codex can switch a plugin's server off only by switching off every plugin, so to keep any plugin's server,
the list must name every plugin's server: a list that names some but not all of them loses all of them, and every
plugin's skills too. Servers that `BOSUN_CODEX_FLAGS` adds (a `--profile`, a `-c mcp_servers...`) are not switched
off, so add servers in `config.toml` instead. Unless the list is `all`, bosun also passes `--disable apps`, which
is documented to switch off ChatGPT's connectors. Codex's own local servers (`codex_worktrees`) stay on, as Chrome
does on Claude Code. For a Codex window, `bosun doctor` says which names in its list mean nothing to Codex (a URL
pattern, a claude.ai connector). More: [design/connectors.md](design/connectors.md#codex).

An Antigravity window is refused instead of limited: see [below](#running-repo-sessions-on-antigravity-a-preview).

</details>

## Running repo sessions on Codex

[Codex](https://developers.openai.com/codex/cli) can run the repo windows. The orchestrator stays on Claude Code,
so you need both, and it hands tasks to a Codex window as it does to any other: the same tasks, the same reports
to the ledger.

1. Install Codex 0.166.0 or newer, and log in:

    ```bash
    brew install codex
    codex login
    ```

2. Choose it. `bosun init` asks once Codex is installed and logged in (and recommends it if you use it more than
   Claude Code). Or in `bosun.env`, for every repo window:

    ```bash
    BOSUN_HARNESS="codex"
    BOSUN_CODEX_MODEL=""       # optional; empty: Codex's own default
    ```

    Or for one window, in `sessions.yml`:

    ```yaml
    sessions:
      - name: my-service
        dir: ~/Development/my-service
        harness: codex
    ```

3. Run `bosun trust <dir>` for any folder Codex has not seen, then `bosun doctor`.

**What it may do.** Windows start with `BOSUN_CODEX_FLAGS`, by default `--no-daemon --approve-for-me`: Codex's
sandbox, with an automatic reviewer that decides when a command may go past it, and no prompts. bosun also gives
the sandbox the repo's `.git`, its own state folder and the network, or nothing could be committed, reported or
pushed, and the folder beside the repo its worktrees go in (`<repo>.worktrees`,
[Permissions](permissions.md#what-each-window-can-reach)). Keep `--no-daemon`: bosun needs each session in its own
process. Each window reaches only the MCP servers its list names ([Connectors](#connectors-what-each-window-may-reach)),
but servers these flags add (a `--profile`, a `-c mcp_servers...`) stay on: add servers in `config.toml` instead.

**What differs from a Claude Code window**, among them no Remote Control from your phone, a revoked login that is
not reported, and no Impact stats or learning yet:
[what windows do not have yet](design/harnesses.md#what-windows-do-not-have-yet).

## Running repo sessions on Antigravity (a preview)

Google's Antigravity CLI (`agy`) can run repo windows, as Codex can. The orchestrator stays on Claude Code. This
is a preview: the windows are built and tested against a stand-in, in the smoke suite and in the Docker sandbox,
but the live run on the real `agy` (`tests/sandbox.sh mac live agy`, which needs a person to sign in to `agy`
inside the throwaway VM) has not passed yet. Expect rough edges, and read [the risk](#the-risk) first.

Set it up once:

1. Install `agy` 1.5.5 or newer with Google's installer, `curl -fsSL https://antigravity.google/cli/install.sh |
   bash`. It puts `agy` in `~/.local/bin` and does not add that folder to your PATH, so do that yourself.
2. Sign in in the desktop session, not over ssh: `agy` reads its login from the Keychain only there.
3. Run `agy` once and go through its first-run screens, then leave it.
4. Choose it for the windows you want: `harness: agy` on a window in `sessions.yml`, or `BOSUN_HARNESS=agy` for all
   of them (or `BOSUN_HARNESS=agy` after the pipe of the install line, as for Codex). `bosun preflight` and
   `bosun doctor` check the version, the login and the first-run screens.

What an `agy` window does:

- It runs in `agy`'s `auto` mode, set for that session only, with the repo folder passed as trusted at each start.
  bosun never saves a setting in `agy`. `BOSUN_AGY_FLAGS`, `BOSUN_AGY_MODEL` and `BOSUN_AGY_BIN` are its keys, like
  Codex's.
- When the first `agy` window starts, bosun installs a small plugin of its own into `agy`. It is recorded, and
  `bosun uninstall` takes it back. In a bosun window the plugin lets exactly allow-listed `git`, `gh` and `bosun`
  commands run past `agy`'s sandbox, so that pushes and `gh` work; every other command is left to `agy`. In your
  own `agy` sessions the plugin does nothing.
- Repo git hooks do not run in an `agy` window: an approved commit would otherwise run the repo's own hook code
  outside the sandbox. Pre-commit-style checks therefore run in CI only.
- Its status comes from `agy`'s hooks and, while a window is busy, from its screen (Esc, and `agy`'s dialogs).
- A window is refused, saying so, when `agy` would start an MCP server that its connectors list does not name
  ([Connectors](#connectors-what-each-window-may-reach)). A repo window with no connectors starts only if `agy` has
  no MCP servers set up at all, the Antigravity app's included. `bosun doctor` names the servers that block a
  window.
- `bosun morning` updates `agy` once before the windows start, and `bosun refresh` restarts an idle window that
  runs an older version.

**What it goes without:** transcript features (Impact stats and learning), autocompact, per-task subagents, and a
hook when a window exits; see [what windows go without](design/harnesses.md#what-windows-do-not-have-yet).

### The risk

Google has said nothing about another program driving `agy` unattended, and a forum thread appeals an `agy`
account suspended under its terms. Use an account you can afford to lose access to.

## Logins

bosun runs unattended, so the logins it uses must keep working without you: Claude Code's, and any AWS SSO
profiles your repos need. The morning run checks them before the windows start.

### Claude Code login

A Claude Code login can expire or be revoked at any time. Every window then answers every prompt with an error
("Please run /login") and nothing else. bosun notices, and tells you:

- **In the morning,** `ensure_claude_login` in your `setup.sh` (which `bosun init` puts first) checks that the login
  works. It cannot log in by itself (`/login` is interactive): it posts a notification and fails the step, so the
  watchdog's `morning-failed` alert names it. Without an answer either way (no network) it only warns.
- **During the day,** when the orchestrator's latest replies are all login errors, the check-in loop logs
  `<window> needs /login` once and sends it no more check-ins or events (events wait) until the login works again.
- **The watchdog's** `login-expired` check alerts once per outage, for the login and for every Claude window, and
  again if it comes back after recovering. Like every check, it alerts only inside your hours.
- **`bosun status`** shows the login and each window's state, and **`bosun doctor`** fails while Claude Code is
  logged out, the last check failed, or a running window gets only login errors.

To fix it, run `/login` in any Claude window, then talk to it, or run `claude` in a terminal. bosun picks up that
the login works again from the next normal reply, or from its next login check.

<details class="note" markdown>
<summary>How bosun tells that a login is gone</summary>

It reads, best signal first (`lib/login.py`; the Claude Code formats are in `lib/claude_state.py`):

1. **The transcript's error fields.** A failed request is written as a synthetic reply flagged
   `isApiErrorMessage`, with `error` (`authentication_failed`, `rate_limit`, `server_error`, ...), often the HTTP
   status (`apiErrorStatus`: 401, 429, 529) and a finer `apiError` (`no_response`, `usage_limit_reached`). These
   map to auth, rate-limit, overloaded, network or other; only auth counts. Exact and free, but known only once a
   prompt has failed.
2. **`claude auth status --json`** (`loggedIn`, `authMethod`): free, but it reads the stored credentials only and
   makes no request (it answers the same with the network cut off), so a token the server revoked still shows
   `loggedIn: true`. It does catch a missing login (`loggedIn: false`, exit 1).
3. **A login check request:** one tiny headless request (`claude -p`, a small model, no tools, settings, MCP
   servers or transcript, but the API key helper below if you use one; about $0.002). `ensure_claude_login` makes
   one each morning; the check-in loop makes one while the orchestrator needs a login or the last check failed, at
   most every 30 minutes, to learn when the login works again (a `/login` itself is not written to any
   transcript). A failure before any request (for example settings that could not load without network) gives no
   structured answer and counts as unknown, never as a login error.
4. **Fallback:** a loose reading of the error text (phrases like "please run /login", "token revoked", "401
   Unauthorized"), only for an error reply without structured fields, or, when a window has no transcript yet, its
   last lines. Verdicts from it say "pattern fallback".

After each check-in or event delivery, the loop waits up to 15 seconds for the reply, so a delivery that just
failed is logged as such. An outage overnight or on a weekend alerts when your hours begin, together with the
morning run's own failure.

</details>

### With an API key instead of a login

Claude Code can sign in with an Anthropic API key, billed per request on your Console account, instead of a
claude.ai login. For bosun, give it the key through the `apiKeyHelper` setting in `~/.claude/settings.json`: a
command that prints the key, which every Claude bosun starts reads, in a window, under launchd or headless. From
the Keychain, so the key is in no file, the command is `security find-generic-password -s anthropic-api-key -w`,
after storing the key once with `security add-generic-password -U -a "$USER" -s anthropic-api-key -w` (it asks
for it).

`claude auth status` then says `api_key_helper`, and bosun's login check and the Slack watcher, which load no
settings files of yours, still get that one setting. Not `ANTHROPIC_API_KEY`: an interactive Claude asks once
whether to use a key from the environment, which a window bosun starts would wait at, and launchd's jobs do not
have your shell's environment. With a key, Claude Code has no Remote Control (it needs a claude.ai login), and the
models bosun uses (`BOSUN_MODEL`, `BOSUN_ORCHESTRATOR_MODEL`, `BOSUN_LEARN_MODEL`) cost per request: smaller ones
cost less.

The key's organization needs purchased credit. The API credits a subscription adds to the Console (the
"Promotional credits" of a Max, Ultra or Team plan) cover the Claude API and the Agent SDK but not Claude
Code, so the windows reply "Credit balance too low · Add funds" while `claude -p` and the API still work. Add
funds to the organization, or sign in with a login instead
([which credits cover what](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)).

### AWS SSO

`ensure_aws_sso <profile>` in your `setup.sh` logs a profile in each morning, so the windows' AWS tools work all
day. It skips the login when the profile's credentials work and its SSO session can still issue new ones. When it
needs a login, it runs `aws sso login`, which opens your browser; if no approval comes, it alerts you and the rest
of the morning goes on ([Known limits](limits.md) has the details).

Point your profiles at a shared `[sso-session]` block: the CLI then renews them silently for as long as the
Identity Center session lasts, and one browser login covers every profile in that session. Profiles that use the
older per-profile `sso_start_url` and `sso_region` keys get no refresh token, so they need the browser every time:

```ini
[profile my-prod-profile]
sso_session = my-sso        # instead of sso_start_url / sso_region
sso_account_id = ...
sso_role_name = admin
region = us-east-1
```

How long the Identity Center session lasts is set by your organization; if it does not survive the night, expect
a browser approval most mornings.

<details class="note" markdown>
<summary>Why it checks the SSO session, not only the credentials</summary>

The AWS CLI caches each profile's role credentials (in `~/.aws/cli/cache`, typically for 12 hours), and that
cache can outlive the Identity Center session: a check that reuses it passes in the morning, and the credentials
then run out a few hours later. So when a profile's `[sso-session]` access token has expired, `ensure_aws_sso`
first drops the cached SSO role credentials. The check then passes only if the session can still issue new ones
(the CLI refreshes the token on the way), and otherwise the login happens at the morning run rather than
mid-morning. Profiles without an `sso_session` are checked as before.

</details>

## Reference

### Where bosun keeps things

| Folder | What is in it |
|---|---|
| `~/.config/bosun/` (`BOSUN_CONFIG_DIR`) | Your [configuration](#configuration), and the [ontology](knowledge.md) in `knowledge/` |
| `~/.local/state/bosun/` (`BOSUN_STATE_DIR`) | The ledger, logs and bosun's working files (below) |
| `~/.local/state/bosun/logs/` (`BOSUN_LOG_DIR`) | A file per command and day |
| `~/.local/share/bosun` and `~/.local/share/bosun-versions` | The installed versions, and the link to the one in use |

If you move the config or state folder, keep both on a disk that supports hard links, as the Mac's own disk does:
bosun makes `replica.json` and the ontology's `secret.key` with one, so that a file is never half-made or made
twice. On a disk without them (exFAT, some network shares), writes stop and say so.

<details class="note" markdown>
<summary>Every file in the state folder</summary>

| File or folder | What it is |
|---|---|
| `ledger.json`, `ledger.md`, `ledger.json.bak` | The [ledger](ledger.md), its readable view, and the version before the last write. Copying an older `ledger.json` back does not undo a write: the journal brings it forward again ([How it is kept](ledger.md#how-it-is-kept)) |
| `journal/ledger/` | Every change to the ledger, which `ledger.json` is rebuilt from: `<replica>.jsonl` for this Mac's changes, `local.jsonl` for what never leaves it, such as your own words ([Sync and sharing](design/sync.md)) |
| `replica.json` | This Mac's id in the ledger's and the ontology's journals |
| `logs/` | A file per command and day, `<command>-YYYY-MM-DD.log`, with what it said on its screen (`morning-…` has the whole morning run, `launch-…` each window's start), and what launchd captured from each job (`launchd.{out,err}`, `watchdog.launchd.{out,err}`, ...). A day's file stops at 2 MB; each morning run deletes files older than 30 days and cuts launchd's files past 1 MB to their last 256 KB |
| `watchdog.json` | The watchdog's memory of what it already alerted |
| `quit.json`, `stopped-<session>`, `keep-awake-<session>.pid` and `.json`, `login-reload`, `console/wanted`, `update-skip.json` | What starting and stopping leave: a `bosun quit`, a `bosun stop` today, the keep-awake and until when, a reload of the jobs, the console job's switch, and a release `bosun update --skip` passed over |
| `login.json` | The last Claude Code login check |
| `codex-sessions.json` | Which Codex session each window's name stands for |
| `connectors.json` | What `claude mcp list` last showed for the connectors a list names, for a launch whose lookup fails |
| `agent-changes.json` (and its locks) | What bosun changed in the agents' own files and the worktrees folders it made, so that `bosun uninstall` takes back only that |
| `signals/` | What Claude Code's hooks reported per window ([Signals](design/signals.md)) |
| `conversations/`, `prompts/`, `launches/` | A repo window's current conversation after a fresh start, each start's first prompt, and what each window was last launched with (so an update can tell which need a restart) |
| `console/token`, `console/running.lock` | The console's access token, and a lock held while a console runs |
| `update.json`, `update.lock`, `update/` | The last `bosun update`'s record, its lock, and the windows' notes to it |
| `backups/<time>-<from>-<to>/` | What `bosun update` backed up before it changed anything, with its `manifest.json` and `summary.md`, the last 3 kept |
| `impact/`, `alerts/`, `events/`, `slack-watch/`, `learn/` | The Impact snapshot, the last Slack alert, the event queue, the Slack watcher's runs, and the learning runs (kept 14 days), each with its feature on |
| `dry-runs/<timestamp>/` | Chores rehearsals |

</details>

### Background jobs

bosun's background work runs as launchd jobs in your login session, named after `BOSUN_LAUNCHD_LABEL`
(`com.bosun.morning` by default). `install.sh`, `bosun init` and `bosun update` load them, and
[`bosun uninstall`](#uninstall) removes them.

| Job | What it runs, and when |
|---|---|
| `<label>` | `bosun morning`, at your start time on your days |
| `<label>.watchdog` | The [watchdog](watchdog.md), every 5 minutes |
| `<label>.learn` | The daily [learning](learning.md) run, with `BOSUN_LEARN_ENABLED=1` |
| `<label>.console` | The [console](console.md), with `BOSUN_CONSOLE_ALWAYS=1`, from `bosun start` until `bosun quit` |
| `<label>.login` | `bosun morning --at-login` when you log in ([Starting at login](#starting-at-login)), with `BOSUN_START_AT_LOGIN=1` |
| `<label>.now` | No schedule: `bosun morning --now --background` (the menu bar app's Start) starts it, so that launchd, not the caller, is the parent of what the morning run starts |
| `<label>.app` | Opens the menu bar app at login, while Open at Login is on (`bosun app login on`) |

Reloading the jobs (an update, `install.sh`) makes launchd run the login job too; a marker the reload leaves
(`login-reload` in the state folder) tells that run it is not a login.
