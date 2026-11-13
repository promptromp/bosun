# Setup

Requirements, installation and configuration, and the morning's logins.

## Requirements

- macOS on Apple silicon or Intel, an **admin account** (for the one-time `sudo pmset`)
- Charger connected and **lid open** (or an external display) overnight
- [Homebrew](https://brew.sh), which also brings Apple's command line tools
- `brew install tmux jq python gh` (`python3` must be 3.13 or newer; the macOS system one is 3.9), plus
  `ruby` and `curl` (the macOS system ones are fine)
- [GitHub CLI](https://cli.github.com), logged in: `gh auth login`
- [Claude Code](https://claude.com/claude-code) 2.1.283 or newer, logged in, and opened once by hand:
  run `claude`, go through its first screens, then `/exit`. The orchestrator always runs on it
- Optional, to run repo sessions on it: [Codex](https://developers.openai.com/codex/cli) 0.166.0 or newer
  (`brew install codex`), logged in (`codex login`). See
  [Running repo sessions on Codex](#running-repo-sessions-on-codex)
- Optional, a preview: Google's Antigravity CLI (`agy`) 1.5.5 or newer, signed in. See
  [Running repo sessions on Antigravity](#running-repo-sessions-on-antigravity-a-preview)

You do not have to check these yourself. `bosun preflight` does, changes nothing, and lists everything
missing at once, with what to do about each. `bosun init` and `bosun doctor` start with the same check.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | sh
```

[`get.sh`](../get.sh) puts the newest release of bosun in `~/.local/share/bosun-versions`, a folder for each
version beside the one repository they come from (`bosun.git`), makes `~/.local/share/bosun` a link to the
version in use, and starts `bosun init`. It installs nothing else: if something is missing, it says what, gives you the
lines to run and the pages to go to, and stops. Do those, and run the same line again. It takes a few
minutes the first time, most of it Homebrew's, and once everything is there the questions take about a
minute.

To run the repo sessions on Codex, ask for it after the pipe, so that it checks Codex is ready too:

```bash
curl -fsSL https://raw.githubusercontent.com/promptromp/bosun/main/get.sh | BOSUN_HARNESS=codex sh
```

Run it again to update: it hands over to the `bosun update` of the install you have (see
[Updating](#updating)), which moves it as its own channel says, so it never moves an install off its channel.
After the pipe, `sh -s -- --dry-run` says what it would do and does nothing, and `sh -s -- --unattended`
asks nothing: it takes the recommended setup, or goes ahead with the update. On a first install that
setup has no repo windows and gives the orchestrator no connectors, since nobody chose them, and its last
line says so: run `bosun init` to add them, or name them in a file with `bosun init --answers` (below).
Run again, it keeps what your config says. `BOSUN_DIR`, `BOSUN_REPO` and `BOSUN_BRANCH` override where it
goes and what it gets; on an install that is already there, the defaults are what it came from, and a
`BOSUN_REPO` given then is kept for the updates after it, and so is a `BOSUN_BRANCH` on the `main` channel (an install made with a branch is on it). On a releases install a branch given later is not followed until `BOSUN_UPDATE_CHANNEL="main"` is set.

A new install gets the newest release (a tag `v<YYYY.MM.DD>`, as `bosun update` follows them). A repository that
has no release yet gets the head of `main`, and `get.sh` says so. With `BOSUN_BRANCH` (after the pipe, as
`BOSUN_HARNESS` above) it gets the head of that branch instead, and the install follows the branch, not the
releases: `bosun init` writes `BOSUN_UPDATE_CHANNEL="main"` to `bosun.env` without asking, and `bosun update`
goes on to the head of the branch (the line it prints to run again after what bosun needs is there keeps `BOSUN_BRANCH`,
as it keeps `BOSUN_HARNESS`). The channel is the one setting; `bosun init --answers` takes it as
`update_channel` (`releases` or `main`).

`bosun init` reads what this Mac already shows: the repos you use your agents in, the MCP servers and
connectors `claude mcp list` shows, your usual hours, your logins. In a terminal it asks a few questions,
one key each: Return takes the recommended answer, `r` starts again and `q` leaves with nothing changed. The last
of them (once a signed build of it exists to install) is whether to add the menu bar app: `bosun.app` in
`~/Applications`, opened at login (`bosun app install` adds it later; `bosun update` keeps an installed app
current, and `BOSUN_MENU_APP=1` in `bosun.env` makes the watchdog say when it is gone).
It says why it recommends what it does, and lists every file and command for you to accept before it
touches anything. Then it writes the config, trusts the folders, and runs `install.sh`.

Two of the questions are lists to tick. Which repos get a window: the one you use most is ticked, and the
others it found are listed for you to add. Which connectors the orchestrator may use (asked when
`claude mcp list` shows any): nothing is ticked, because only you know which are work tools; tick the ones
it needs, such as Slack and your tracker. It writes them to `BOSUN_ORCHESTRATOR_CONNECTORS`. Repo windows
get none: `BOSUN_CONNECTORS`, or `connectors:` in a `sessions.yml` entry, gives them some (see
[Connectors](#connectors-what-each-window-may-reach)).

Without a terminal, or with `--dry-run`, it only prints the plan. To use answers from a file instead,
which only needs what it changes:

```bash
bosun init --print-answers > answers.json
$EDITOR answers.json           # repos, orchestrator_connectors, days, start, until, events, ...
bosun init --answers answers.json
bosun init --recommended       # or just take what it recommends
```

Run it again whenever you like: it starts from your config, keeps anything you added to `bosun.env`,
keeps a backup of what it changes, and never changes your `setup.sh` once it exists. How it decides,
and what it never does: [design note](design/init.md).

### If you get stuck

`bosun preflight` tells you where you are, and `bosun doctor` tells you what is wrong once bosun is
installed. The usual ones:

| What you see | What it means | What to do |
|---|---|---|
| `missing  Homebrew` | Nothing to install the tools with | Install it from [brew.sh](https://brew.sh), then do what it prints at the end to add it to your PATH, and open a new terminal |
| A tool you just installed is still `missing` | Your terminal has the old PATH | Open a new terminal |
| `first-run screens not done` | Claude Code stops at its theme and security screens the first time, and a window nobody is watching would wait there for ever | Run `claude`, go through them, then `/exit` |
| `not logged in` under Claude Code | It has no login | Run `claude`, and follow what it asks |
| `not logged in` under Codex | It has no login | Run `codex login`. With an API key: `printenv OPENAI_API_KEY \| codex login --with-api-key` |
| A window is on a screen, and the watchdog reports `stalled-start` | The agent is asking something only you can answer, usually about trusting the folder | `bosun attach`, then read it. Do not just press Return: on several of Claude Code's screens the default is to exit, and on Codex's it says yes |
| `does not run on <your OS> yet`, or `bosun runs on macOS today` | bosun runs on macOS today, and Linux is next | See the [roadmap](../ROADMAP.md) |
| `bosun.env does not parse, so none of it was read` | An edit left a line bash cannot read: an unclosed quote, a stray word. Read half, the file would give a config that is neither yours nor the defaults, so every command stops instead | Fix the line it names, or move the file aside to run on the defaults |
| `bosun.env stops before its end when read, so none of it was read` | A line stops the shell that reads it, usually a variable that is not set where bosun runs (`$SOMETHING` from your shell's startup files, which launchd's jobs do not have), so every command stops instead of running on half of it | Set the value in the file itself, or remove the line |
| `warn  bosun.env sets BOSUN_...: bosun does not read it` in `bosun doctor` | A typo, or a key bosun no longer has; it names the key you most likely meant | Fix the name. A key renamed is changed by `bosun update` |
| `run bosun as yourself, not as root` | You ran it with `sudo`, which would leave root's files in your folders | Run it without `sudo`: it asks for `sudo` itself, once, for the wake schedule |
| `another bosun start or refresh is setting up the windows` | Two runs at once, such as the morning job and one you typed; one sets the windows up while the other waits, up to 3 minutes | Nothing, usually: the first one does the work. Run it again if you need to |
| `... and this runs inside it` (`start --restart`, `update`) | You ran it in one of bosun's own windows, which it would stop or wait on | Run it from another terminal. `bosun stop` works from inside: it stops its own window last |
| `its agent, ..., is not installed for this window's shell` in a window | The window's shell cannot find its agent (Claude Code, Codex or `agy`), so the window stays a plain shell; you also get an alert | Install it, or name its path in `bosun.env` (`BOSUN_CLAUDE_BIN`, `BOSUN_CODEX_BIN`, `BOSUN_AGY_BIN`) |

If none of these fit, open an issue with what `bosun preflight` and `bosun doctor` print:
<https://github.com/promptromp/bosun/issues>.

### By hand

If you prefer your own checkout and to write the config yourself:

```bash
git clone https://github.com/promptromp/bosun.git ~/Development/bosun
cd ~/Development/bosun
./install.sh --example generic        # seeds the config from examples/generic
$EDITOR ~/.config/bosun/{bosun.env,sessions.yml,setup.sh}
./install.sh                          # re-run after editing schedule values
sudo pmset repeat wakeorpoweron MTWRF 08:01:00   # install.sh prints the exact line;
                                                 # ./install.sh --with-wake runs it for you
bosun doctor
```

**Why the wake comes after the job time.** launchd runs a calendar job that fell due during sleep
as soon as the Mac wakes. Waking a minute *after* the job time (`BOSUN_WAKE_DELAY_MINUTES`)
means the job starts within seconds of the wake and takes its keep-awake assertion immediately.
Waking *before* the job would leave a gap in which an unattended Mac can doze off again.

`install.sh` is idempotent and never overwrites your config. It links `bosun` into
`~/.local/bin` and loads the launchd jobs: the morning run, the [watchdog](watchdog.md#watchdog) and, with
`BOSUN_LEARN_ENABLED=1`, the daily [learning](learning.md#learning) run
(`<BOSUN_LAUNCHD_LABEL>.learn`) and, with `BOSUN_CONSOLE_ALWAYS=1`, the [console](console.md#console)
(`<BOSUN_LAUNCHD_LABEL>.console`). One more job, `<BOSUN_LAUNCHD_LABEL>.now`, has no schedule: it runs
`bosun morning --now` when `bosun morning --now --background` (which the menu bar app's Start runs) kickstarts it,
so that launchd, not the caller, is the parent of what the morning run starts.
[`bosun uninstall`](#uninstall) reverses it. A checkout of your own like this one updates with git, not
with `bosun update`.

## Updating

```bash
bosun update             # shows the plan, and asks
bosun update --dry-run   # only shows the plan
bosun update --yes       # goes ahead without asking
```

`bosun update --check` says whether there is a newer release and changes nothing (one line, or `--json`), and
`bosun update --skip <version>` stops it being offered again. It updates bosun to the newest release, or on the `main` channel (`BOSUN_UPDATE_CHANNEL="main"`, which an install
made with `BOSUN_BRANCH` follows) to the head of the branch the install follows, while the agents keep working. It gets the new version into a folder of its own, beside
the one you run, and that version shows its plan: the commits, the migrations your data needs, the
prompts that changed, what each window gets, the launchd jobs to reload, and anything that stops it. In a
terminal it asks first; without one it stops at the plan. Running the install line again does the same,
so it also works from a bosun that has no `bosun update` yet.

Then it rehearses the migrations on a copy of your data, holds bosun's own background work (the check-in
loop, the watchdog, learning, the morning job), and pauses writes for a moment: it backs up your ledger,
knowledge base and settings (`backups/` in the state folder, the last 3 kept), migrates them, switches
`~/.local/share/bosun` to the new version and checks each store. If any of that fails, it puts everything
back as it was and says what failed. Writes that arrive meanwhile wait, and land on the new version.

What it does to the windows that are running:

| The window | What the update does |
|---|---|
| Nothing it was started with changed (most updates) | Leaves it alone: its next `bosun` command runs the new version |
| Only the orchestrator's prompts changed (`policy.md`, the task files) | Asks the orchestrator to re-read them once it is idle |
| Something it was started with changed (its settings, hooks, plugin) | Restarts it on the new version once it is idle, in the same conversation. If only background work runs there, it first asks the window to list that work, and passes the list back after the restart, as a restart ends it |
| Busy, waiting for your answer, or someone typing in it | Waits, and never restarts one that waits for you |
| One bosun did not start (a shell or a Claude you opened in its tmux session, outside `sessions.yml`) | Leaves it as it is, and says so in the plan |

A window opened after the plan was shown, while you answer or during the update, is brought along too:
once the new version runs, the update looks at the windows again. One opened after that runs the new
version from its next `bosun` command on, and keeps what it was started with.

Each window gets a prompt to check for anything it missed and carry on, and answers with its status. The
update waits for every answer and says which windows it is waiting for; Ctrl-C stops that wait, and the
check-in loop brings the rest along once each is idle. A window left alone, or a task already handed to a
repo session, keeps the text it has; the summary lists the tasks, for a `bosun delegate --update`.

It ends with a summary: the commits, the migrations and version bumps, the config it changed and its new
keys (with what each one means, when it says something), anything you have to change by hand in your own files, the prompts that changed, the jobs reloaded,
what came of each window, anything that fails now that did not before, and where the backup is. It is
saved as `summary.md` in the backup's folder, and its last line points to [Going back](#going-back).
`bosun doctor` lists what the last update left to you. An update cut short (Ctrl-C, a crash, a power cut)
is put back, or finished, by the next `bosun update`. How it works: [design note](design/update.md).

**Your config.** A new key needs nothing: its default applies. A key that is renamed or removed is
changed in `bosun.env`, and so is a key whose default would change what your install did: the update
writes it with the value that keeps that, under a comment saying why. Either way it first keeps the file
as it was, as `bosun.env.pre-update-<time>`, and the plan shows the change before you say yes. The first
such key is `BOSUN_ORCHESTRATOR_CONNECTORS="all"`: an install from before
[connectors](#connectors-what-each-window-may-reach) keeps every connector for its orchestrator, and
`bosun doctor` warns until you name the ones it needs and drop `all`. Repo windows start without
connectors from then on, a repo's own `.mcp.json` servers included, which the plan and the summary say:
give one back with `BOSUN_CONNECTORS`, or `connectors:` in its `sessions.yml` entry. Without a `bosun.env`
nothing is written, and the defaults apply: the orchestrator then starts with no connectors, which the
plan, the summary and `bosun doctor` say. A checkout of your own that you update with `git pull` gets no
such change: set `BOSUN_ORCHESTRATOR_CONNECTORS` in `bosun.env` to the connectors the orchestrator needs,
or to `all`, or it starts with none, which `bosun doctor` warns about until the key is set.

Another such key is `BOSUN_UPDATE_CHANNEL="main"`: an install that was updating before releases goes on
following the head of `main`, and the plan and summary say how to follow the dated releases instead
(`BOSUN_UPDATE_CHANNEL="releases"`, which a new install has).

A Claude Code window that a repo's own `.claude/settings.json` (or `settings.local.json`, or your
`~/.claude/settings.json`) would give servers its list does not name does not start on the new version
([Connectors](#connectors-what-each-window-may-reach)). The update restarts it, the launch refuses in its
pane, and the summary says it was restarted and is still waited for; its pane, the day's launch log and
`bosun doctor` say why. Name those servers in its list, or remove them from that file. Until the window
answers, the next `bosun update` names it and how to finish it.

### The first update of an older install

An install from before versions side by side is a checkout in `~/.local/share/bosun`, and the bosun in it
has no `bosun update`. Running the install line again converts it, once. The new version's `bosun update`
shows its plan and asks, then waits for a quiet moment: every window idle with nothing running, and
nobody typing in the session for `BOSUN_ACTIVE_CLIENT_MINUTES`. It says what it waits for, and Ctrl-C
there changes nothing. Then it stops the watchdog, learning and the check-in loop, since an older version
may not hold them for an update, checks again that every window is still quiet, and updates as above. If
a window is still busy two minutes after that stop, it starts that work again and waits for a quiet
moment with everything running, as before. The checkout is swapped for the link in one step and kept as
`~/.local/share/bosun-versions/legacy-<its commit>`, and each of bosun's own windows (the orchestrator,
those in `sessions.yml`) is restarted on the new version; any other is left as it is, as the old version
recorded no launch of it. If anything fails, the old checkout is what runs again, with the jobs and the
loop it stopped started again as they were. That old checkout has no `bosun update`, so after a crash
run the install line again: it starts them, going by what it noted in `update/own-work.json` in the
state folder before it stopped anything, and leaves a check-in loop that runs by then as it is.

It will not convert a checkout with anything of yours in it: changes, a stash, commits not pushed,
another worktree, a file git ignores. Take that out first. The update after it deletes the kept checkout,
unless something of yours is in it by then, or the installer did not make it, which its summary says.

## Going back

There is no command for it yet: a failed update puts everything back by itself, and going back later is by
hand. You need the version the update came from and the backup it took, both named in its summary:

1. Stop what writes: `bosun quit --stop-now` (or `bosun stop`), and unload the watchdog, learning and console jobs
   (`launchctl bootout gui/$(id -u)/com.bosun.morning.watchdog`, and `.learn` and `.console`, with your
   `BOSUN_LAUNCHD_LABEL` if you set one; the last two only if you have them).
2. Find the older version's folder in `~/.local/share/bosun-versions`: an update keeps the one it came
   from until the next update. If it is gone, add it again from its commit, whole: the backup's
   `manifest.json` names it as `from` (the summary's first line shows only 7 characters, which
   `git --git-dir ~/.local/share/bosun-versions/bosun.git rev-parse <those 7>` turns into the whole one):
   `git --git-dir ~/.local/share/bosun-versions/bosun.git worktree add --detach
   ~/.local/share/bosun-versions/<its first 12 characters> <commit>`. After the first update, the version
   before it is the checkout it kept, `legacy-<its first 12 characters>`.
3. Put the backup back: each entry in its `state/` folder over the one in `~/.local/state/bosun`, and
   each in its `config/` folder over the one in `~/.config/bosun`, replacing the newer one whole (its
   `manifest.json` lists every file). Anything written since the update is not in it.
4. Lead the link to the older version (`ln -sfn ~/.local/share/bosun-versions/<folder>
   ~/.local/share/bosun`), run its `~/.local/share/bosun/install.sh` to load its jobs again, and
   `bosun start`. `bosun update` would take you forward again, to the newest release, or on the `main` channel
   to the head of the branch (from the checkout kept from before versions, which has no `bosun update`, the
   install line does).

Going back is only to the version the update came from, and never past a `kg forget`: a deletion of a
person deletes every update backup there is, as restoring one would bring the person back, and says so in
its line. A summary whose backup a forget deleted says that going back past it is no longer possible.

## Uninstall

```bash
bosun uninstall             # says what goes and what stays, then asks
bosun uninstall --dry-run   # only says
```

It changes nothing until you have answered everything, and Enter always takes the safe answer: at
"Are you sure?" it means no, at "Keep your ledger?" it means keep. Ctrl-C at any question leaves everything
as it was.

| What | What happens |
|---|---|
| Your windows | Stopped, so any work in them stops. It says which are running first, and waits up to 15 seconds for what they started to end, so their last hooks finish first; what still runs then it names and leaves running, such as a background command a Codex window started |
| The launchd jobs (morning, watchdog, learning, console, the job that runs `morning --now` for the menu bar app's Start, and the app's Open at Login job) | Unloaded and deleted, before the windows stop, so the watchdog cannot bring them back |
| The menu bar app, `~/Applications/bosun.app` (with `bosun app install`) | Asked to quit if it runs, then deleted, after its job and before the windows stop. The `~/Applications` folder stays |
| The repeating wake | Cancelled with `sudo pmset repeat cancel`, if it is the one bosun set. `pmset` can only cancel every repeating event at once, so another event, or a wake at another time, stays and it says so |
| Folder trust | Taken back for the folders bosun marked, in Claude Code and Codex (`agy` windows pass their folder at each start and mark nothing). A folder you had accepted yourself stays trusted. It goes by bosun's record of what it changed (`agent-changes.json`), kept since `bosun uninstall` was added; trust or notes from an install before that are not on it and stay |
| The plugin bosun installed into `agy`, if an `agy` window ever started | Uninstalled with `agy plugin uninstall`, with what that leaves behind (`plugin_data/bosun/`), by bosun's record of having installed it. Your own `agy` setup is not touched |
| Memory notes from [`bosun learn`](learning.md#learning) | Deleted, with their line in `MEMORY.md`. Your own notes stay |
| `~/.local/bin/bosun` and bosun's working files | Deleted |
| What `get.sh` installed: `~/.local/share/bosun-versions` (its `bosun.git` and every version), the checkout from before versions kept there, and the link `~/.local/share/bosun` | Deleted, last. Each folder is checked as a checkout is, and anything of yours in any of them keeps them all, link included: changes, a stash, commits not pushed, a worktree elsewhere, a file git ignores. Versions you made yourself, without the installer's mark on `bosun.git`, stay |
| A checkout of your own you run bosun from | Stays. One `get.sh` made before versions side by side is deleted, last, unless it has anything of yours in it |
| Your ledger and learning history, your knowledge graph | It asks, and Enter keeps them |
| The backups `bosun update` took (`backups/` in the state folder) | Deleted when either the ledger or the knowledge graph is, as each holds both (every person in it included). Kept only when you keep both |
| `~/.config/bosun`: `bosun.env`, `sessions.yml`, `setup.sh`, `policy.md`, `learn.md`, `tasks/`, `workstreams.json`, the backups `bosun init` made (`*.pre-init-*`), and the copies of `bosun.env` that `bosun update` kept (`bosun.env.pre-update-*`) | Stay, and it says where. `--purge` deletes them too |
| Anything else in those folders | Stays, and it names it. It only deletes what it knows is bosun's, and if you pointed `BOSUN_STATE_DIR` or `BOSUN_LOG_DIR` at a folder that also holds other files, it leaves that folder whole |

`/bosun:chores` and bosun's hooks need no removing: bosun passes them to each window it starts, so neither
Claude Code nor Codex has them installed. (`agy` takes plugins only by installing them: see below.) Your repos, branches and pull requests are not touched, nor are
your conversations.

Install again with the same line, and bosun picks up what you kept. `replica.json`, this Mac's id in
both journals, goes with the ledger: deleting the ledger while keeping the knowledge graph deletes
it too, and the knowledge base's next write carries on under a new id. For scripts, `--yes` asks
nothing and keeps your records; `--delete-ledger`, `--delete-knowledge` and `--purge` say otherwise. `--purge` leaves
`forbidden-terms.txt`, which guards commits to this repo and is not part of running bosun. Run it from a
terminal outside bosun's windows, since it stops them. If something could not be removed it says what to run,
and running it again finishes the job.

If you deleted bosun's folders by hand, the jobs and the wake are still there. Run the install line again,
and then `bosun uninstall`.

A first update of an older install that you declined, or that failed before the switch, leaves
`~/.local/share/bosun-versions` beside the checkout, which the checkout's own `bosun uninstall` does not know
about: it is only what `get.sh` fetched (`bosun.git` and the new version's folder). Delete it by hand once
nothing of yours is in it: `rm -rf ~/.local/share/bosun-versions`.

## Configuration

All configuration lives in `~/.config/bosun/` (override with `BOSUN_CONFIG_DIR`). If you move it or
the state folder (`BOSUN_STATE_DIR`), keep both on a disk that supports hard links, as the Mac's own
disk does: bosun makes `replica.json` and the knowledge base's `secret.key` with one, so that a file
is never half-made or made twice. On a disk without them (exFAT, some network shares) writes stop
and say so.

| File | Purpose |
|---|---|
| `bosun.env` | Schedule, session name, Claude flags, drain prompt, orchestrator and chores settings. Every `BOSUN_*` value can also be overridden from the environment. Commands read the file each time they run, so edits apply at once, also inside running sessions. A file bash cannot parse is not read at all, and commands stop and say which line; `bosun doctor` warns of a key bosun does not read (with the one you most likely meant) and of a word where a number goes. See `examples/generic/bosun.env` for all keys. |
| `sessions.yml` | One entry per repo window: `name` (the session name) and `dir`, optionally `harness` (`claude` or `codex`), `model`, `effort` and `connectors` (see [Connectors](#connectors-what-each-window-may-reach)), and on Claude Code `autocompact`, for that window. The orchestrator is configured in `bosun.env`, not here. |
| `setup.sh` | Defines `setup_steps()`. Helpers: `ensure_claude_login` (see Claude Code login), `ensure_aws_sso <profile>`, `run_step "<desc>" cmd...`. |

State and logs go to `~/.local/state/bosun/`:

- `ledger.json`, its readable view `ledger.md`, and `ledger.json.bak`, the version before the last
  write. Copying that (or any older `ledger.json`) back does not undo a write: the journal brings it
  forward again. A change you make to `ledger.json` by hand is adopted by the next write, into the
  journal, with a line on stderr; one the journal cannot hold, such as removing a history entry, is
  undone by that write, which says so (`ledger.json.bak` keeps the edited file until the next write)
- `journal/ledger/`: every change to the ledger, which every write appends to before it saves
  `ledger.json` (rebuilt from it when a write was cut short): `<replica>.jsonl` for this Mac's
  changes, `local.jsonl` for what never leaves it, such as your own words, and
  `<file>.torn-<time>` for a transaction a write left unfinished, moved aside and kept
  ([Sync and sharing](design/sync.md))
- `replica.json`: this Mac's id in the journals (the ledger's, and the knowledge base's in
  `knowledge/journal/` of the config folder, see [Knowledge](knowledge.md#knowledge)), made once,
  and anew when a journal starts again (moved aside, emptied or removed); moved aside itself, it
  gives the Mac a new id too. Either way, both journals carry on under the new id
- `logs/`: `morning-YYYY-MM-DD.log`, `checkin-YYYY-MM-DD.log`, `learn-YYYY-MM-DD.log`,
  `impact-YYYY-MM-DD.log`, `hook-YYYY-MM-DD.log` (the repo sessions' prompt hook), `launch-YYYY-MM-DD.log`
  (the alerts a window's launch sent, such as connectors it started without), and what launchd
  captures from each job (`launchd.{out,err}`, `watchdog.launchd.{out,err}`, `learn.launchd.{out,err}`)
- `watchdog.json`: the watchdog's de-duplication state
- `login.json`: the last Claude Code login check (see Claude Code login)
- `codex-sessions.json`: which Codex session each window's name stands for
- `connectors.json`: the URL or command line `claude mcp list` last showed for each name a list holds
  that only that listing resolves (a claude.ai connector, a plugin's server the installed plugins do not
  give), for a launch whose lookup fails (see [Connectors](#connectors-what-each-window-may-reach))
- `agent-changes.json`: what bosun changed in the agents' own files (folder trust, memory notes, an agent's
  one-time setup), so that `bosun uninstall` takes back only that
- `agent-setup.lock`: held while an agent's one-time setup is made, so two windows starting together make it once
- `signals/`: what Claude Code's hooks reported per window ([Signals](design/signals.md))
- `conversations/`: a repo window's current conversation, once refresh started a fresh one
- `prompts/`: the first prompt of each window start, passed to its launch as a file
- `launches/<session>/<window>.json`: what each window was last launched with (`bosun launch`):
  bosun's commit, the agent and the model and effort it ran with, its directory and any `--model` /
  `--effort` it was given, and a fingerprint of the agent's arguments and the files they name, so an
  update can tell which windows need a restart and start them again the same way
- `console/token`: the console's access token; `console/running.lock`, held while a console runs, so
  that `bosun update` can say to restart it
- `update.json` and `update.lock`: the last `bosun update`'s record (what it did, step by step) and the
  lock it holds while it runs; `update/`: the windows' notes (`update/notes/`), what a first update
  stopped of bosun's own work (`update/own-work.json`) and, while one runs, its rehearsal's copy
- `backups/<time>-<from>-<to>/`: what `bosun update` backed up before it changed anything, with its
  `manifest.json` and the update's `summary.md`, the last 3 kept (see [Updating](#updating) and
  [Going back](#going-back))
- `impact/`: the Impact snapshot (`bosun impact collect`)
- `alerts/`: the last Slack alert's result, with Slack alerts on
- `events/`: the event queue (`queue.jsonl`) and what was delivered (`state.json`), with events on
- `slack-watch/state.json`: the Slack watcher's last runs, with it on
- `learn/`: the learning runs (`state.json`, and per run its digest and report, kept 14 days)
- `dry-runs/<timestamp>/` for chores rehearsals

### Model and effort

Every Claude Code session bosun starts gets an explicit model and effort, so cost does not depend on
whatever you last picked by hand (Claude Code's `/model` and `/effort` save your choice as the
default for all future sessions, which is how sessions drift to Opus on high effort).

- Repo sessions: `BOSUN_MODEL` / `BOSUN_EFFORT` (default `opus` / `medium`), overridable
  per window in `sessions.yml` (`model:`, `effort:`). On Codex: `BOSUN_CODEX_MODEL` (empty: Codex's own
  default) and `BOSUN_EFFORT`, passed as `-c model_reasoning_effort`.
- Orchestrator: `BOSUN_ORCHESTRATOR_MODEL` / `BOSUN_ORCHESTRATOR_EFFORT` (`opus` /
  `medium`).
- Delegated tasks: each task type sets an effort (reviews and investigations: `medium`; fixes on
  your PRs and new work: the session's own). A window opened for the task starts at it; a session
  that is already running runs the task in a subagent at it, so its own setting is untouched (on Codex,
  which has no such instruction yet, it runs at the session's own settings). The
  orchestrator passes `--effort high` only when you ask for more depth.

On Claude Code the model goes in as `--model`; the effort as a session-level setting (`--settings
'{"effortLevel":...}'`) rather than `--effort`, so `/effort` inside a session can still change
it. Empty values leave Claude Code's own default. `bosun send --list` shows what each window
was launched with, `refresh` restarts idle sessions whose launch settings no longer match the
config, and `bosun doctor` shows the configured values and your saved default.

### Connectors: what each window may reach

A window bosun starts reaches only the MCP servers and connectors its list names; the rest are off for it.
On Claude Code, Chrome is not one of them: it stays on through `--chrome`, as before. On Codex, see below.

| Key | Whose list | Default |
|---|---|---|
| `BOSUN_CONNECTORS` | every repo window | empty: none |
| `connectors:` in a `sessions.yml` entry | that window, instead of `BOSUN_CONNECTORS` | (the key's) |
| `BOSUN_ORCHESTRATOR_CONNECTORS` | the orchestrator, and `bosun chores --dry-run` | empty: none; `bosun init` writes what you tick |

A list is comma-separated, or a YAML list in `sessions.yml` (`[]` is none). Each entry is a server's name
as `claude mcp list` (and `/mcp`) shows it, or a URL pattern; `all` means every one, as before this
setting, and stands alone:

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

At launch bosun turns the list into Claude Code's `allowedMcpServers`, in the window's `--settings`, in
place of any a site settings file names (with `all`, the site file's applies). A server you added yourself
goes by its name, a plugin's by its URL or command, and a claude.ai connector by the URL `claude mcp list`
shows for it. bosun runs that listing (a few seconds) only when the list names a claude.ai connector, or a
plugin's server the installed plugins do not list (a plugin synced from your account, say), once per
launch, and keeps what it found in `connectors.json` (a name it no longer shows is dropped there); if the
listing fails (at a morning wake, before the network is up), the launch uses what was kept. A name it
cannot find is left out, never let in as more: the launch logs a line, on the window's screen and in
`launch-YYYY-MM-DD.log`, and for the orchestrator it also alerts you (`bosun alert`, whose output goes to
that log too). A name you added yourself is matched exactly, and checked only when that listing runs
anyway: it cannot let in more than a server of that name, so a typo allows nothing, and a launch that runs
the listing logs such a name it does not show. The list is read at launch, so a change applies once the
window starts again.

Claude Code adds the `allowedMcpServers` of your own `~/.claude/settings.json` and of the window folder's
`.claude/settings.json` and `.claude/settings.local.json` to the window's list. So a window with a list
(anything but `all`) does not start while one of those files allows a server its list does not name, entry
for entry: the launch says which file and which entries, and what to do: name them in the window's list,
use `all`, or remove them from that file.

On Codex, a window's list names servers as `codex mcp list` shows them. bosun switches off the servers it
does not name: those in a `config.toml` (yours, or a trusted repo's) one by one, and a plugin's only by
switching off every plugin, since Codex can switch a plugin's server off only by its plugin and says
nowhere which plugin a server comes from. So to keep any plugin's server, the list must name every
plugin's server; one that names some but not all of them loses all of them, and every plugin's skills
too. A name Codex has no server by is logged at launch. Servers that `BOSUN_CODEX_FLAGS` adds (a
`--profile`, a `-c mcp_servers...`) are not switched off, as bosun lists the servers without the window's
flags: add servers in `config.toml`, not through flags. Unless the list is `all`, bosun also passes
`--disable apps`, which is documented to switch off ChatGPT's connectors (`codex_apps`; the test VM had no
ChatGPT login to see that with). Codex's other built-in servers
(`codex_worktrees`) stay on, as Chrome does on Claude Code: they are local, and `codex mcp list` does not
show them, so a list cannot name them. bosun also starts Codex without its update screen, which would stop
an unattended start.

`bosun doctor` has a line for each window: what it reaches (a name resolved to a server it knows), what
it only allows by name or by pattern, and each name it cannot find. It looks nothing up (`claude mcp list`
connects to every server), so a claude.ai connector no start has looked up yet shows as not found until
the window's next start does. For a Codex window it asks `codex mcp list --json` for the servers' names,
and says which names mean nothing to Codex (a URL pattern, a claude.ai connector). It warns while the
orchestrator's list is `all`, while `bosun.env` does not set it at all, and for a Claude Code window that
another settings file would widen, which does not start; and it says what an `allowedMcpServers` in the
orchestrator's site settings file comes to.

### How sessions are resumed

`claude --resume <name>` opens a picker when several sessions share a name or prefix. That would
block an unattended start. `bosun launch` instead reads the session transcripts in
`~/.claude/projects/`, picks the **most recently active session whose name is exactly `<name>`**,
and resumes it by UUID. If none exists it starts a new session with that name. If the session is
already open in another terminal (per `~/.claude/sessions/<pid>.json`), it refuses rather than
running two copies on one transcript.

Every repo window on Claude Code gets `--name <name> --remote-control <name>` plus `BOSUN_CLAUDE_FLAGS`.
Codex has no session names, so a [Codex window](#running-repo-sessions-on-codex) resumes from bosun's own
table, `codex-sessions.json`.

### Steady state: the session already exists

After the first day the tmux session normally survives the night, because the Mac only sleeps.
Re-attach any time with `bosun attach`. The morning run never duplicates the session.
It runs `bosun refresh`, which handles each window on its own:

| Window | Action |
|---|---|
| its folder gone (moved or deleted) | skip it, as `bosun start` does, and say so in the log, which the watchdog alerts on: never open it in another folder |
| missing | create it and launch its agent |
| agent not running | relaunch in the manifest directory (resume by UUID, drain prompt) |
| agent `busy` (mid-turn) | leave alone |
| agent `waiting` (a permission prompt or a question to you) | leave alone: a pasted Enter could answer it |
| window split into several panes | leave alone and report it (relaunching could kill the other pane's process) |
| agent `idle` at least `BOSUN_RESTART_MIN_IDLE_SECONDS`, and on a different version than that agent's installed one (usually older), or launched by bosun with a model or effort other than the configured one; for the orchestrator also when it is still on yesterday's conversation | restart: SIGTERM (Claude exits cleanly in about 2s), then relaunch on the installed version with the configured model and effort. Same session UUID, so the conversation is intact (the orchestrator's new-day restart starts today's conversation instead), plus, on Claude Code, a fresh Remote Control connection. |
| anything else (idle and current, idle too briefly, or `shell`: turn over but a background shell still running) | send the drain prompt |

The drain prompt, also the one a relaunch or restart sends, goes only to a window with something in
flight: the orchestrator, a Claude in `shell`, or a window holding a handed-over item whose work is
not done (`bosun ledger in-flight <window>` lists them: awaiting a report, or being worked on).
Any other window is resumed or left idle without a prompt (`left idle: nothing in flight`), since
every prompt re-reads the whole conversation; it costs nothing until it gets work.

With `BOSUN_FRESH_CONVERSATIONS=1` (off by default), refresh, and so the morning run, also
starts a fresh conversation for a repo window that is idle for `BOSUN_RESTART_MIN_IDLE_SECONDS`,
holds nothing in flight, and whose conversation grew past `BOSUN_FRESH_CONTEXT_TOKENS` (200000;
the size its latest turn read, `agent_state.py context <pid>`). The new conversation is named
`<window>-<date>-<n>` (kept in the state dir's `conversations/<window>`, which `launch` resumes from
then on) and starts with a short prompt pointing at its memory notes and `bosun ledger list
--open`. `refresh --dry-run` names the windows that would get one, also with the flag off.

Fresh conversations only start at a refresh. In between, every Claude Code window is launched with
`claude --autocompact BOSUN_AUTOCOMPACT_TOKENS` (300000; the orchestrator:
`BOSUN_ORCHESTRATOR_AUTOCOMPACT_TOKENS`; a sessions.yml entry can set its own `autocompact`;
empty or 0 leaves Claude Code's default, `auto` is accepted): once a conversation passes that size,
Claude Code compacts it by itself, mid-task, into a summary, so no window re-reads 300k-900k tokens
a turn for the rest of the day. On Codex bosun sets no compaction: `BOSUN_AUTOCOMPACT_TOKENS`, `autocompact:`
and `BOSUN_COMPACT_CONTEXT` do nothing there. Claude Code's own idle compaction (200k+ tokens idle about an hour)
still applies. A summary can drop which ledger items the window holds, so with
`BOSUN_COMPACT_CONTEXT=1` launch also installs a SessionStart hook (matcher `compact`,
`bosun hook compact-context`) that adds them back, in at most
`BOSUN_COMPACT_CONTEXT_MAX_CHARS` (1500) characters, from `bosun ledger compact-context`:
for a repo window its items (in flight first) and how to report; for the orchestrator the proposals
waiting for my approval and the open items per window. Both are read at launch, so a running window
picks them up at its next restart. The fresh conversation stays as the backstop: a window still over
`BOSUN_FRESH_CONTEXT_TOKENS` at the morning refresh starts a new one.

Status and version come from Claude Code's own `~/.claude/sessions/<pid>.json`, and on Codex from its
session files. The status is
re-read just before a restart. With `BOSUN_HOOK_SIGNALS=1` (the default) launch also installs
async hooks (`bosun hook signal`) on Notification, PermissionRequest, Stop, StopFailure,
SessionStart and SessionEnd that record Claude Code's own signals per window in
`$BOSUN_STATE_DIR/signals/<window>.jsonl` (`BOSUN_SIGNALS_MAX_KB`, 256): the watchdog's
`waiting` alert and the events' waiting line then say what the window was asked (`Bash: make
deploy`), `long-shell` names what is still running, and `spend-limit` also sees a stop only the
StopFailure hook reported. They add to the session records and transcripts, never replace them;
design: [design/signals.md](design/signals.md). Claude Code installs updates automatically but keeps running the old
version until restarted. `refresh` is how sessions pick up updates without losing work.

The morning run passes `--unless-active`: if someone typed in an attached tmux client within the
last `BOSUN_ACTIVE_CLIENT_MINUTES`, it leaves the windows alone instead of pasting into your
draft. Run `bosun refresh` by hand any time; add `--dry-run` to preview it.

`BOSUN_ON_EXISTING` picks the behaviour: `refresh` (default), `drain` (never restart) or
`restart` (kill the whole session and start fresh).

## Running repo sessions on Codex

[Codex](https://developers.openai.com/codex/cli) can run the repo sessions. The orchestrator stays on Claude
Code, so you need both, and it hands tasks to a Codex window the way it does to any other: the same tasks,
the same reports to the ledger.

```bash
brew install codex
codex login
```

Then choose it: in `bosun init`, which asks once Codex is installed and logged in (and recommends it if you
use it more than Claude Code), or in `bosun.env`:

```bash
BOSUN_HARNESS="codex"                # every repo window
BOSUN_CODEX_MODEL="gpt-6.1-sol"      # optional. Empty: Codex's own default
```

Or one window at a time, in `sessions.yml`:

```yaml
sessions:
  - name: my-service
    dir: ~/Development/my-service
    harness: codex
    model: gpt-6.1-sol               # optional
```

Then run `bosun trust <dir>` for any folder Codex has not seen, and `bosun doctor`.

**What it may do.** Windows start with `BOSUN_CODEX_FLAGS`, by default `--no-daemon --approve-for-me`: Codex's
sandbox, and an automatic reviewer that decides when a command may go past it, with no prompts. bosun also
gives the sandbox the repo's `.git`, its own state folder and the network, or nothing could be committed,
reported or pushed. Keep `--no-daemon`: bosun needs each session in its own process. Each window reaches
only the MCP servers its list names ([Connectors](#connectors-what-each-window-may-reach)), but servers
these flags add (a `--profile`, a `-c mcp_servers...`) stay on: add servers in `config.toml` instead.

**What differs from a Claude Code window**, among them no Remote Control from your phone, a revoked login
that is not reported, and no Impact stats or learning yet:
[what windows do not have yet](design/harnesses.md#what-windows-do-not-have-yet).

## Running repo sessions on Antigravity (a preview)

Google's Antigravity CLI (`agy`) can run repo sessions, as Codex can. The orchestrator stays on Claude Code.
This is a preview. The windows are built and tested against a stand-in, in the smoke suite and in the Docker
sandbox, but the live run on the real `agy` (`tests/sandbox.sh mac live agy`, which needs a person to sign in
to `agy` inside the throwaway VM) has not passed yet. Expect rough edges, and read the risk below first.

Set it up once:

1. Install `agy` with Google's installer, `curl -fsSL https://antigravity.google/cli/install.sh | bash`. It
   puts `agy` in `~/.local/bin` and does not add that folder to your PATH, so do that yourself.
2. Sign in in the desktop session, not over ssh: `agy` reads its login from the Keychain only there.
3. Run `agy` once and go through its first-run screens, then leave it.
4. Choose it for the windows you want: `harness: agy` on a window in `sessions.yml`, or `BOSUN_HARNESS=agy`
   for all of them (or `BOSUN_HARNESS=agy` after the pipe of the install line, as for Codex).
   `bosun preflight` and `bosun doctor` check the version, the login and the first-run screens.

What an `agy` window does:

- It runs in `agy`'s `auto` mode, set for that session only, with the repo folder passed as trusted at each
  start. bosun never saves a setting in `agy`. `BOSUN_AGY_FLAGS`, `BOSUN_AGY_MODEL` and `BOSUN_AGY_BIN` are the
  keys, like Codex's.
- When the first `agy` window starts, bosun installs a small plugin of its own into `agy`. It is recorded, and
  `bosun uninstall` takes it back. In a bosun window the plugin lets exactly allow-listed `git`, `gh` and
  `bosun` commands run past `agy`'s sandbox, so that pushes and `gh` work; every other command is left to
  `agy`. In your own `agy` sessions the plugin does nothing.
- Repo git hooks do not run in an `agy` window: an approved commit would otherwise run the repo's own hook
  code outside the sandbox. Pre-commit-style checks therefore run in CI only.
- Its status comes from `agy`'s hooks and, while a window is busy, from its screen (Esc, and `agy`'s dialogs).
- A window is refused, saying so, when `agy` would start an MCP server that its connectors list does not
  name ([Connectors](#connectors-what-each-window-may-reach)). A repo window with no connectors starts only
  if `agy` has no MCP servers set up at all, the Antigravity app's included. `bosun doctor` names the servers
  that block a window.
- `bosun morning` updates `agy` once before the windows start, and `bosun refresh` restarts an idle window
  that runs an older version.

**What it goes without:** transcript features (Impact stats and learning), autocompact, per-task subagents,
and a hook when a window exits; see [what windows go without](design/harnesses.md#what-windows-do-not-have-yet).

**The risk.** Google has said nothing about another program driving `agy` unattended, and a forum thread
appeals an `agy` account suspended under its terms. Use an account you can afford to lose access to.

## Claude Code login

A Claude Code login can expire or be revoked at any time. Every session then answers every prompt
with an error reply ("Please run /login") and nothing else, so a check-in loop that only pastes
prompts would keep reporting them delivered. bosun reads, best signal first
(`lib/login.py`; the Claude Code formats in `lib/claude_state.py`):

1. **The transcript's error fields.** A failed request is written as a synthetic reply flagged
   `isApiErrorMessage`, with `error` (`authentication_failed`, `rate_limit`, `server_error`, ...),
   often the HTTP status (`apiErrorStatus`: 401, 429, 529) and a finer `apiError` (`no_response`,
   `usage_limit_reached`). These map to auth, rate-limit, overloaded, network or other; only auth
   counts. Exact and free, but known only once a prompt has failed.
2. **`claude auth status --json`** (`loggedIn`, `authMethod`): free, but it reads the stored
   credentials only and makes no request (it answers the same with the network cut off), so a token
   the server revoked still shows `loggedIn: true`. It does catch a missing login (`loggedIn:
   false`, exit 1).
3. **A login check request:** one tiny headless request (`claude -p`, a small model, no tools,
   settings, MCP servers or transcript; about $0.002). `ensure_claude_login` makes one each
   morning; the check-in loop makes one while the orchestrator needs a login or the last check
   failed, at most every 30 minutes, to learn when the login works again (a `/login` itself is not
   written to any transcript). A failure before any request (for example settings that could not
   load without network) gives no structured answer and counts as unknown, never as a login error.
4. **Fallback:** a loose reading of the error text (phrases like "please run /login", "token
   revoked", "401 Unauthorized"), only for an error reply without structured fields, or, when a
   window has no transcript yet, its last lines. Verdicts from it say "pattern fallback".

What happens then:

- **Morning:** add `ensure_claude_login` to `setup_steps()` in your `setup.sh` (first, as in
  `examples/generic/setup.sh`). It cannot log in by itself (`/login` is interactive): it posts a
  notification and fails the step, so the watchdog's `morning-failed` alert names it. Without an
  answer either way (no network) it only warns.
- **Check-ins and events:** when the orchestrator's latest replies are all login errors, the
  check-in loop logs `<window> needs /login: ...` once and sends it no more check-ins or events
  (events stay pending) until it works again: a normal reply (run `/login` in any Claude window,
  then talk to it) or a login check that succeeds. After each delivery it waits up to 15 seconds
  for the reply, so a check-in that just failed is logged as such.
- **Watchdog:** `login-expired` alerts once per outage, for the login and for every Claude window
  (repo sessions too), and alerts again if it comes back after recovering. Like every check it
  alerts only inside the awake window: an outage overnight or on a weekend alerts when the window
  opens, together with the morning run's own failure (see ROADMAP, urgent alerts).
- **`bosun status`** shows the login and each window's state; **`bosun doctor`** fails when
  Claude Code is logged out, the last check failed, or a running window gets only login errors.

## AWS SSO

`ensure_aws_sso <profile>` skips the login when `aws sts get-caller-identity` already works.

The CLI caches each profile's role credentials (in `~/.aws/cli/cache`, typically for 12 hours), and
that cache can outlive the Identity Center session: a check that reuses it passes in the morning,
and the credentials then run out a few hours later. So when a profile's `[sso-session]` access
token has expired, `ensure_aws_sso` first drops the cached SSO role credentials. The check then
passes only if the session can still issue new ones (the CLI refreshes the token on the way), and
otherwise the login happens at the morning run rather than mid-morning. Profiles without an
`sso_session` are checked as before. How long the Identity Center session lasts is set by your
organization; if it does not survive the night, expect a browser approval most mornings.

Profiles that reference a shared `[sso-session <name>]` block get a refresh token. The CLI then
renews them silently for as long as the Identity Center session lasts, and one browser login covers
every profile in that session. Profiles using the legacy per-profile `sso_start_url` /
`sso_region` keys get no refresh token, so they need the browser every time. If a profile keeps
prompting, point it at the shared session:

```ini
[profile my-prod-profile]
sso_session = my-sso        # instead of sso_start_url / sso_region
sso_account_id = ...
sso_role_name = admin
region = us-east-1
```
