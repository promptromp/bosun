# Setup

This page gets bosun installed and running on your Mac: what you need, the install, and the first start. It ends
once bosun runs. After that, [How a day runs](day.md) says what bosun does on its own,
[Configuration](configuration.md) how to change it, [The menu bar app](menu-bar-app.md) what its icon offers, and
[Updating and uninstalling](updating.md) how to keep it current or take it off.

## Requirements

- **A Mac** on macOS, Apple silicon or Intel. To let bosun wake the Mac on schedule, your account must be an
  administrator: bosun asks for your password once, for `sudo pmset`.
- **[Homebrew](https://brew.sh)**, which also brings Apple's command line tools.
- **A few tools:** `brew install tmux jq python gh`. bosun needs Python 3.13 or newer (the one macOS ships is
  older). The system's `git`, `ruby` and `curl` are fine.
- **The [GitHub CLI](https://cli.github.com)**, logged in: `gh auth login`.
- **[Claude Code](https://claude.com/claude-code)** 2.1.283 or newer, logged in, and opened once by hand: run
  `claude`, go through its first screens, then `/exit`. The orchestrator always runs on Claude Code.
- **Optional:** [Codex](configuration.md#running-repo-sessions-on-codex) or [Antigravity](configuration.md#running-repo-sessions-on-antigravity-a-preview)
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
| Stays logged in to your account. Asleep is fine | bosun's jobs run in your login session. After a restart or a log-out nothing runs until you log in again; then, by default, bosun starts by itself if it is one of your days and inside your hours ([Starting at login](menu-bar-app.md#starting-at-login)). |
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
| Which connectors may the orchestrator use? | The MCP servers and connectors the orchestrator may reach. Nothing is ticked, because only you know which are work tools: tick the ones it needs, such as Slack and your tracker. Asked only when `claude mcp list` shows some. Repo windows get none ([Connectors](configuration.md#connectors-what-each-window-may-reach)). |
| When should bosun run? | Your days, start time and "awake until" time, recommended from when you usually work. |
| How proactive should it be? | Events, approvals in chat, the evening learning run and the [ontology](knowledge.md#in-the-agents-hands) in the agents' hands, all recommended. With Slack's plugin installed, also the Slack watcher. |
| Which agent should run the repo sessions? | Claude Code, or Codex or Antigravity. Asked only when one of those is installed and logged in. |
| Which model should the sessions use? | `opus` or `sonnet` for the windows (for the orchestrator, when repo windows run on another agent). |
| How should repo sessions handle permissions? | Claude Code's auto mode (recommended) or no permission checks. Asked when repo windows run on Claude Code. See [Permissions](permissions.md). |
| Should the Mac wake by itself? | The repeating `pmset` wake. Recommended, unless another app already set a repeating wake (`pmset` keeps only one). |
| Add bosun to the menu bar, and start it when you log in? | The [menu bar app](menu-bar-app.md), and whether bosun starts when you log in during your hours. Answer "no" and it asks the second part alone. |
| Which AWS SSO profiles should each morning log in to? | The profiles the morning run logs in to ([AWS SSO](configuration.md#aws-sso)). Asked only when `~/.aws/config` has some. |

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
| A window answers "Credit balance too low · Add funds" while `claude -p` still works | Claude Code is signed in with an API key whose organization has only subscriber credits ("Promotional credits"), which do not cover Claude Code | Buy credit for the key's organization, or use a login (see [With an API key](configuration.md#with-an-api-key-instead-of-a-login)) |
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

<details class="note" markdown>
<summary>Installing from a checkout of your own</summary>

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
loads bosun's [background jobs](configuration.md#background-jobs). A checkout of your own updates with `git pull`,
not with `bosun update`. [`bosun uninstall`](updating.md#uninstall) takes bosun off as from an install, and leaves
the checkout itself.

The wake comes a minute after the start time (`BOSUN_WAKE_DELAY_MINUTES`) on purpose: launchd runs a calendar job
that fell due during sleep as soon as the Mac wakes, so the morning job starts within seconds of the wake and takes
its keep-awake at once. Waking *before* the job would leave a gap in which an unattended Mac could doze off again.

</details>

## Moved from this page

Setup used to cover more. Those parts have pages of their own now.

### The menu bar app

Moved to [The menu bar app](menu-bar-app.md).

### Updating

Moved to [Updating and uninstalling](updating.md).

### Uninstall

Moved to [Updating and uninstalling](updating.md#uninstall).

### Configuration

Moved to [Configuration](configuration.md).
