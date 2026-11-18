# Commands

Every `bosun` command, grouped by what you use it for, with a line or two on each. `bosun <command> --help` prints a
command's full usage. The longer commands have [details](#details) below the tables, and the menu bar app's
commands print JSON for it, as described there.

## Setting up

| Command | What it does |
|---|---|
| `bosun preflight [--quiet]` | Check what bosun needs on this Mac, for each agent in use (Claude Code, Codex, Antigravity), and say exactly what to do about anything missing. It changes nothing. `bosun init` and `bosun doctor` start with the same check. See [Setup](setup.md#requirements) |
| `bosun init [--recommended [--unattended] \| --answers <file>] [--force] [--dry-run]` / `--detect` / `--print-answers` | Set bosun up from what this Mac shows: the repos you use your agents in, the connectors `claude mcp list` shows, your usual hours, your logins. In a terminal it asks a few questions and shows the plan before it changes anything; otherwise it only prints the plan. See [Setup](setup.md#install) and [details](#bosun-init) |
| `bosun setup [--dry-run]` | Run (or just check) the morning's setup steps from `setup.sh` |
| `bosun trust [--check\|--revoke] [--any] <dir>...` | Mark repo folders inside the repo mask as trusted by each agent in use, so an unattended start does not stop at its trust prompt (Antigravity has nothing to record: each launch passes its folder as trusted for that session) |
| `bosun app install [--build]` / `app update` / `app remove` / `app login on\|off` / `app status [--json]` | The menu bar app, `bosun.app` in `~/Applications`: install the publisher's notarized build, keep it current, remove it, turn Open at Login on or off, say what is installed. See [The menu bar app](menu-bar-app.md) and [details](#bosun-app) |
| `bosun settings get [--json]` / `settings set KEY=VALUE... [--replace-wake] [--json]` | Show or change the settings the menu bar app's Settings window shows: the days, start time, "awake until", starting at login and the update channel. See [Changing settings](#changing-settings) |
| `bosun doctor` | Check everything bosun depends on, from the tools and the login to the ledger and the ontology against their journals, and what each window may reach. See [details](#bosun-doctor) |
| `bosun wake-test [N] [--full]` / `--report` / `--cleanup` | Test a real sleep and wake on this Mac: schedule a wake N minutes ahead (3 by default), put the Mac to sleep, and say afterwards what happened |

## Every day

| Command | What it does |
|---|---|
| `bosun start [--restart] [--unless-active] [--unless-quit]` / `bosun stop [--keep-awake]` | Start the tmux session, or refresh it if it exists; or stop it. `stop` also releases the keep-awake, unless `--keep-awake`. One start or refresh at a time: a second waits for the first, up to 3 minutes. A start also ends a `bosun quit`, so the watchdog, learning and the morning job run again; `--unless-quit` (what the morning run passes) instead logs that bosun was quit and starts nothing, checked under the same lock, so a quit made while the morning was setting up is not undone. `--restart` refuses inside bosun's own windows, which it would stop; `stop` works there, stopping its own window last |
| `bosun quit --plan [--json]` / `quit (--let-finish[=<minutes>] \| --stop-now) [--pause-morning] [--sleep] [--json]` | Stop bosun until `bosun start`, with your say over busy windows, the morning start and the keep-awake. It never touches the ledger, the ontology or the config. See [How a day runs](day.md#stopping-and-starting-again) and [details](#bosun-quit) |
| `bosun morning [--now [--background]] [--dry-run] [--skip-setup] [--awake-minutes N]` / `morning --at-login` | The morning run: keep the Mac awake, run the setup steps, update the agents that need it, then start or refresh the windows. Its launchd job runs it on your days; `--now` runs it at any hour. See [details](#bosun-morning) |
| `bosun attach [<window>]` | Attach to bosun's tmux session, on its own tmux server (`tmux -L <socket> attach -t <session>`). With a window's exact name it selects that window first (a name the session has no window of is refused, with the names it has): where you answer what a window asks (a first-run screen, a question of your shell's startup files, a prompt). Detach with the tmux prefix and `d`; the windows run on. Refused inside tmux. The menu bar app's "Open <window>" runs it in Terminal |
| `bosun status [--json]` | How bosun is: the schedule, recent sleep and wake, the windows and their login, the watchdog's checks, proposals waiting for you, open outreach, today's log. `--json` is the one object the menu bar app polls. See [details](#bosun-status) |
| `bosun sessions [--json]` | Every window, configured or open, with its kind, folder, agent and status, session id and transcript path (`.agent` in `--json`; `.conversation` is the one its next launch takes up) |
| `bosun console [--port N] [--open] [--new-token] [--url]` | The local web console over the ledger, the sessions, bosun's health and the ontology; read-only but for the ontology's review decisions. `--url` prints the running console's address, token included ("the console is not running", exit 1, when none runs); run by hand while one serves, it prints that address instead of failing on the port. With `BOSUN_CONSOLE_ALWAYS=1` a launchd job runs it from `bosun start` until `bosun quit`. See [Console](console.md#console) |
| `bosun refresh [--dry-run] [--drain-only] [--restart-idle] [--unless-active] [window...]` | Per window: restart idle windows on an outdated agent or with a changed model or effort, prompt idle ones with work in flight, start exited ones again. See [Steady state](day.md#steady-state-the-session-already-exists) and [details](#bosun-refresh-and-bosun-launch) |
| `bosun send --list` / `send <window> <msg>` / `send --new [--model M] [--effort E] <window> <dir> <msg>` / `send --start <window> [msg]` | List the windows with their agent, status and version; hand a message to a running window; open a new window; or start the agent in a window that has none. See [details](#bosun-send) |
| `bosun alert [--dry-run] <message>` | Notify you: a macOS notification and, with `BOSUN_ALERT_SLACK_USER` set, a Slack DM to yourself, sent by a headless Claude that can use only Slack's send-message tool and never waits for an approval |

## Work and records

| Command | What it does |
|---|---|
| `bosun delegate <type>[,<type>] --id <ledger id> --ask "<one line>" [--details ...] [--model M] [--effort E] (--print\|--window W\|--new W DIR\|--start W) [--again]` / `--update <ledger id> --details ... [--window W]` / `--list` | Hand a task to a repo window, with the rules for its task type from `orchestrator/tasks/`, and record the window on each item. Refused while the item's last hand-over waits for a report, unless `--again`. With `BOSUN_KNOWLEDGE_ENABLED=1` the task also carries what the ontology says about its repo and area. `--update` sends an addendum to that window, framed with the item's id and kept in its history; with `--window` it moves the item: the new window gets the whole task, the old one is told to stop. `--update <out-id>` hands an outreach reply to the window holding its parent, and marks it applied. See [Orchestrator](orchestrator.md) |
| `bosun chores [morning\|check-in] [--dry-run [--fresh\|--continue]]` | Tell the orchestrator to run its chores now, or rehearse them headless with no side effects |
| `bosun checkin [--loop] [--dry-run]` | Send `/bosun:chores check-in` if the orchestrator is idle and you are not typing (the `checkin` window loops this) |
| `bosun ledger <command>` | The work ledger: list, show, record and move items, reports, outreach, tasks and links, tickets, workstreams, approvals, checks. See [The ledger](ledger.md) and [the subcommands](#bosun-ledger) |
| `bosun kg <command>` | The ontology, what bosun knows about your organization: propose and confirm facts, the daily review, lookups and context packs, import, forget. See [Ontology](knowledge.md#ontology) and [the subcommands](#bosun-kg) |
| `bosun context` | The orchestrator's working context: config, windows (each as `<agent>:<status>`), the `owner/repo` to folder map, open items |
| `bosun my-prs [--since 48h\|7d\|2w\|YYYY-MM-DD] [--by updated\|created] [--include-drafts]` | Your open pull requests within the repo mask and time window, as JSON lines |
| `bosun pr-threads <pr-url> [--awaiting-author] [--json]` | A pull request's review threads (read-only): each unresolved thread with its replies and last commenter, whether it waits for the author (unresolved, the last comment not by the author; bots are shown, marked, and never count, except the reviewer bots in `BOSUN_REVIEWER_BOTS`), the threads per review round, and each reviewer bot's latest verdict. `--awaiting-author` lists only those, and exits 3 while any are listed: the check before saying a review is addressed |
| `bosun review-watch [--dry-run] [--quiet] [--id <mypr id> ...]` | New reviews (changes requested or comments, by people other than the author or by reviewer bots, never other bots) on your pull requests in `addressing`, `awaiting-review` or `approved` since their last hand-over (or report): recorded once on the item as `review-arrived` (no state change) and, with events on, queued as `review` events. It prints each one still to act on, with its next step. The chores run it; with events on, the check-in loop every `BOSUN_REVIEW_WATCH_MINUTES`. See [Events](events.md#events-from-repo-sessions) |
| `bosun events [--once\|--dry-run\|--status\|--list [--all]\|--covered]` | Push events from repo windows (reports, what you typed there, outreach drafts, follow-up tasks, windows waiting at a prompt) to the idle orchestrator as one summary. The check-in loop runs it with `BOSUN_EVENTS_ENABLED=1`. See [Events](events.md) |
| `bosun slack-watch [--once\|--scheduled] [--dry-run]` / `--status` | Look for new Slack mentions, DMs and replies in your threads with a headless Claude that can only read Slack, and queue each new one as an event. The check-in loop runs it with `BOSUN_SLACK_WATCH_ENABLED=1`. See [Slack watcher](events.md#slack-watcher) |
| `bosun learn [--since 24h\|3d\|<ISO time>] [--until <ISO time>] [--dry-run\|--collect-only] [--out <dir>]` / `--scheduled` / `--status` (also `status`, `list`) / `apply <learn-id>` / `done\|reject <learn-id> [--note <text>]` | Look back over the sessions, ledger and logs since the last run, and propose memory notes, rules and setup changes, each a `learn:` item waiting for your approval. `--scheduled` is what its launchd job runs: once a day after `BOSUN_LEARN_AT`, and not while bosun is quit. See [Learning](learning.md#learning) |
| `bosun impact collect [--since YYYY-MM-DD] [--dry-run]` | Collect the console's Impact snapshot: your merged pull requests and reviews (GitHub, read-only) and, per window and day, agent time, your prompts and tokens. Daily from the morning run with `BOSUN_IMPACT_COLLECT=1`. See [Impact](console.md#impact) |

## Health

| Command | What it does |
|---|---|
| `bosun watchdog [--once\|--dry-run\|--status\|--list\|--facts] [--only <check>,...]` | Look for problems that need you and alert you once per problem: a missing or failed morning run, a missing session or check-in loop, an agent stopped at a screen or waiting at a prompt, errors in the logs, stuck ledger items, an expired login, a window whose every reply is "Credit balance too low", and more. Its launchd job runs it every 5 minutes. `--list` prints the checks, `--status` each one's current result; `--facts` prints what it reads of tmux, ps and launchd, as JSON (what the console reads). See [Watchdog](watchdog.md#watchdog) |
| `bosun orphans [--apply]` | List the helpers that earlier window starts left behind: what a pane's launch shell (`<shell> -lic <checkout>/bin/bosun launch ...` or `... checkin --loop ...`) forked off while reading your startup files (a prompt theme's git-status daemon, say), adopted by launchd, with no children and not tmux itself. `--apply` stops them (SIGTERM, after checking the pid again). `launch`, `checkin --loop`, `refresh` and `stop` stop such helpers themselves |
| `bosun resolve <dir> <name>` / `<dir> --list` / `--live <uuid>` | Show how a Claude Code session name resolves to a session (exit 3: no such session, or none live). Codex windows are not covered: bosun's table for those is `codex-sessions.json` |

## Updating and removing

| Command | What it does |
|---|---|
| `bosun update [--dry-run] [--yes] [--json]` / `update --back [...]` / `update --branch <name> [...]` / `update --check [--json]` / `update --skip <version>` / `update note --window <w> --stock\|--status` / `update advance` | Update bosun to the newest release (or, on the `main` channel, the head of its branch) while the agents keep working, without losing a write: it shows its plan and asks, backs up and migrates your data, switches versions, and brings the windows along. It ends with a short summary, saved as `summary.md` in the backup's folder. `--back` goes back to the version the last update came from, keeping what was written since wherever the older version can read it; `--branch` follows a branch of the origin from now on and updates to its head. `--check` says whether there is an update and changes nothing; `--skip` stops a release (or a head of `main`) being offered; `--json` prints one object with the plan, the update's record and the summary (`null` unless an update ended in that run). See [Updating](updating.md) and [details](#bosun-update) |
| `bosun uninstall [--dry-run] [--yes] [--delete-ledger] [--delete-knowledge] [--purge]` | Take bosun off this Mac, the menu bar app and its Open at Login job too. It says what goes and what stays, asks whether you are sure and whether to keep your ledger and ontology, and changes nothing until you have answered. See [Uninstall](updating.md#uninstall) |

## Run by bosun and its agents

You rarely type these: bosun's windows, hooks and jobs run them.

| Command | What it does |
|---|---|
| `bosun launch [--model M] [--effort E] [--harness H] [--no-prompt] <name> [prompt \| --prompt-file <file>]` / `launch --fingerprint <name>` | Start or resume a named session in the current folder, with the window's model and effort, and record what the agent was launched with. See [details](#bosun-refresh-and-bosun-launch) |
| `bosun guard [--approvals\|--permission-request\|--post-tool-use]` | The orchestrator's PreToolUse hook (see [Permissions](permissions.md)); with `BOSUN_APPROVALS_ENABLED=1` also its approval hooks (see [Approvals](permissions.md#approvals-approve-once-in-chat)) |
| `bosun hook user-prompt\|compact-context\|signal\|turn-end\|agent` | What the agents call: Claude Code's hooks; `turn-end --harness <agent>`, what an agent that reports its turns (Codex) runs when one ends (Codex windows started before keep `codex-turn-end`, which works as before); and `agent --harness <agent> --event <event>`, an agent's own hooks (the plugin bosun installs into Antigravity runs it). The hook's JSON comes in on stdin and the answer goes out on stdout, in the foreground whether or not events are on; it always exits 0, since the agent takes any other exit as a refusal of its tool, and an error is a line in the hook log. Among them is what records what you type in a window (see [Events](events.md#events-from-repo-sessions)) |

## Details

### `bosun init`

- `--recommended` carries out the recommended plan without questions; `--answers <file>` uses your answers from a
  file (`--print-answers` writes one to start from); `--detect` prints what it found as JSON.
- `--unattended` (with `--recommended`; `get.sh --unattended` sets `BOSUN_INIT_UNATTENDED=1`, the same) sets up no
  repo windows, connectors or menu bar app on a first run, since nobody chose them.
- The menu bar app is offered (and its `bosun app install` and `login on` run after `install.sh`) only while
  `app/publisher.json` has a valid `team_id`, so that a signed build exists to install; otherwise `bosun.env` says
  `BOSUN_MENU_APP="0"` and the plan says why. A refused install (no network, say) puts `BOSUN_MENU_APP` back to `0`
  and `BOSUN_CONSOLE_ALWAYS` back to what it was, says how to add the app later, and init still succeeds. Answering
  No where the app is installed leaves it there, and says that `bosun app remove` takes it away.
- **Exit status:** 0 done (or only printed); 1 blocked, or `install.sh` stopped at a step (its `ERROR` line said
  again); 2 bad usage or answers; 3 set up, but `bosun doctor` found problems, said again last. `install.sh` exits 3
  for that too, and 1 when a step stopped it.

How it decides: [design/init.md](design/init.md).

### `bosun morning`

The scheduled entry point: it keeps the Mac awake, runs the setup steps, updates the agents that bosun starts with
their own auto-update off (`agy update`, once, before the windows start or refresh; a failure is a problem in the
notification and stops nothing), then starts or refreshes the windows. `--skip-setup` skips the setup steps only.

- **After a `bosun quit`** that paused the morning start, it logs that and exits 0 before doing anything, until
  `bosun start`. Its line, `morning run skipped: ...`, ends the run for the watchdog, as a quit during the run does.
  After a quit that did not pause the morning start, it starts as usual and ends the quit.
- **`--now`** is the same run at any hour or day, as you start it: it ends a `bosun quit`, paused or not, instead of
  stopping for one. It holds the keep-awake until `BOSUN_AWAKE_UNTIL` only while now is inside your hours
  (`BOSUN_DAYS`, from `BOSUN_START_TIME` to `BOSUN_AWAKE_UNTIL`), and none outside them, saying so in one line;
  `--awake-minutes` still wins. Its log header says `trigger=now`, and `--now --dry-run` changes nothing and says
  what it would do, the keep-awake included.
- **`--now --background`** (what the menu bar app's Start runs; with `--now` alone) has launchd run it: it
  kickstarts bosun's own job `<BOSUN_LAUNCHD_LABEL>.now` (`templates/launchd-now.plist`, which runs `bosun morning
  --now` and nothing else) and returns at once, so the run, and the tmux server and agents it starts, are launchd's
  children and not the caller's (a process the app starts keeps the app as the responsible process for macOS's
  privacy prompts, which would then name the app). The run's log is the day's morning log.
- **`--at-login`** (alone; what bosun's login job runs) is the scheduled run behind the checks of
  `lib/login_start.py`, in this order, the first that holds winning: `BOSUN_START_AT_LOGIN` is 0, the run is a
  reload of bosun's jobs (a marker younger than two minutes) and not a login, a `bosun quit` paused the morning
  start, today is not one of `BOSUN_DAYS`, now is outside your hours, you ran `bosun stop` earlier today, or the
  session is running already. It logs one line, that it starts or why not; a skip exits 0 before the run's header is
  written (the watchdog reads a header as a run), and a start is the scheduled run, its header saying
  `trigger=login`. A start you make yourself (`--now`, `bosun start`) never goes through these checks.

<details class="note" markdown>
<summary>Edge cases and exit codes</summary>

- A quit made while the run sets up is kept, as for the scheduled run: its start is skipped and the run says so
  (with other problems in the run, its problems line names the quit too).
- A morning run that launchd started, by either job (the scheduled one or `.now`), is not started again: it says so
  and exits 0. A `bosun morning --now` typed in a terminal is no job and is not seen; the script takes no lock, so a
  scheduled run that starts in the seconds between that check and the kickstart overlaps.
- `--background` exits 0 when launchd took the kickstart or such a run is going; 1, in one line, when the job is not
  loaded (run `install.sh` again, or `bosun init`) or launchd would not start it; 2 for `--background` without
  `--now`, or with `--dry-run`, `--skip-setup` or `--awake-minutes`.

</details>

### `bosun quit`

- **`--plan`** changes nothing: it says which windows are open, busy and waiting for you, when the keep-awake ends,
  and why a quit is refused.
- **`--let-finish[=<minutes>]`** waits until no window is `busy`, checking every 5 seconds for up to `<minutes>`
  (default `BOSUN_QUIT_WAIT_MINUTES`, 10; 0 checks once). A window waiting at a prompt does not hold the wait (it is
  listed and stopped with the rest), one at `shell` has only background work and counts as done, and no key is
  ever sent to a window. If the minutes run out it changes nothing, names the windows still busy and exits 3. A
  line on stderr says who it waits for. Refused inside one of bosun's own windows, where it would wait on itself.
- **`--stop-now`** quits at once.
- **`--sleep`** lets the Mac sleep now (a plain `bosun stop`); without it the morning's keep-awake runs to its time.
- **`--pause-morning`** keeps the morning start off until `bosun start`; without it the next morning run starts
  bosun again.

A quit writes `quit.json` first (from then on the watchdog, scheduled learning and a paused morning skip their runs,
and never restart what is being stopped), takes away the console job's `console/wanted` marker and stops the job
(a console you started by hand is not the job: if one still runs, it says so), then runs `bosun stop
--keep-awake`. Run it from outside bosun's windows.

<details class="note" markdown>
<summary>Locks, failures and its JSON</summary>

- **The lock.** The steps after the wait hold the `windows` lock that `bosun start` takes, waiting up to
  `BOSUN_WINDOWS_LOCK_WAIT_SECONDS` for a start in the middle of setting up the windows, and failing with nothing
  changed if it does not come free. Under it they look at the windows and for an update once more: an update that
  began refuses the quit, and with `--let-finish` a window busy again lets the lock go and goes back to waiting, to
  the same deadline. The wait for busy windows holds no lock.
- **Failures.** While an update runs, a quit is refused (exit 1). A step after `quit.json` failing fails the quit
  (exit 1), saying the record stays and how to finish (`bosun quit --stop-now` again, or `bosun start`); `bosun
  status --json` also calls that an error ("bosun quit did not finish") while windows are open.
- **`--json`** prints one object with `contract` 1: the plan, and for a quit its `result` (`stopped`, `busy`,
  `refused` or `failed`) and its `error` (the reason for `failed`, else `null`, in every result), and when it stopped
  bosun the console's state (`console`: `stopped`, `not running` or `still running`, and `console_port` for one
  still running, else null). The plan's `wait_minutes` (the `--let-finish` minutes of that run, else
  `BOSUN_QUIT_WAIT_MINUTES`), `lock_wait_seconds` (`BOSUN_WINDOWS_LOCK_WAIT_SECONDS`) and `steps_seconds` (the most
  its other steps take) are what a caller times a quit by: a `--let-finish` quit takes at most the minutes plus the
  lock wait plus the steps, a `--stop-now` one the lock wait plus the steps.

</details>

### `bosun refresh` and `bosun launch`

`bosun refresh` handles each window on its own, as [Steady state](day.md#steady-state-the-session-already-exists)
describes. Two modes are for `bosun update`, and neither takes another mode, `--unless-active` included:

- **`refresh --update-restart <prompt-file> <window>`** restarts one window now if its turn is over (idle, or `shell`:
  only background work runs) and nobody is typing there, resuming its conversation with the prompt in the file
  instead of the drain prompt. A busy or waiting window, or one whose input box holds unsent text, is left running
  and refresh exits 3, so the caller can try again. Once it started a launch it waits for the agent (up to
  `BOSUN_START_TIMEOUT`): a launch that ends without one, or whose agent exits at once, makes it exit 5, with the
  launch's own error line last on stderr (`bosun launch` leaves its last error in `launches/<session>/<name>.error`
  in the state folder, cleared as each launch starts).
- **`refresh --checkin-loop`** restarts the check-in loop in the `checkin` window, which otherwise keeps the code it
  started with. It stops the loop at once, even in the middle of a tick, so call it while an update holds bosun's
  background work.

`bosun launch` resumes a named session in the current folder, with the window's model and effort (`--harness`: on
that agent instead of the configured one), and records what the agent was launched with in
`launches/<session>/<name>.json` in the state folder: bosun's commit, the agent, model and effort, the folder, any
`--model` or `--effort` it was given, and a fingerprint of the agent's arguments and the files they name (with the
conversation's name and the orchestrator's policy text left out). A repo window's launch first makes the folder its
worktrees go in, beside its repository (`<repository>.worktrees`;
[Permissions](permissions.md#what-each-window-can-reach)). `--fingerprint` prints the fingerprint a launch would
record now, and launches nothing; one the launch would refuse exits 4 with the reason last on stderr, which an update
shows for a window it leaves running.

<details class="note" markdown>
<summary>How an update's restart picks the conversation and the agent</summary>

- An orchestrator still on yesterday's conversation starts today's with the morning chores instead, as on a new day,
  and a repo window with a fresh conversation pending starts that one. A window started into its next conversation
  gets that one's own prompt, not the update's, so refresh exits 4.
- A window whose agent is not running is started again (a missing one created) with the update's prompt only when
  its launch resumes a conversation, and otherwise starts as a new conversation does.
- Every window starts on the agent its last launch ran, even if `harness:` or `BOSUN_HARNESS` now names another
  (that one takes over at the next `refresh` or `start` not for an update).
- A window outside the window list (opened by `send --new`) keeps the folder and `--model` / `--effort` its launch
  recorded; one with no launch recorded is refused, as bosun did not start it. `launch --fingerprint` works the same
  way for such a window.

</details>

### `bosun send`

- **`send --list`** lists the windows with their agent, status and version (`claude:idle`, `codex:busy`,
  `no-agent`).
- **`send <window> <msg>`** hands work to a running window as a bracketed paste, so multi-line text arrives as one
  prompt. It is refused while Claude waits at a permission prompt or a question, also one that opens while `send`
  waits. It waits while someone typed in
  that window within `BOSUN_SEND_TYPING_MINUTES` or its input line holds unsent text, up to
  `BOSUN_SEND_WAIT_SECONDS`, and fails unless Claude took the prompt, so the caller resends. A message of `-` is read
  from stdin.
- **`send --new`** opens a new window; **`send --start`** starts Claude in a window that has none, in its folder from
  the window list (the pane's own folder for a window outside the list, or without a `sessions.yml`), resuming its
  named session (with no message, it gets the drain prompt). Both run `bosun launch` as the pane's own command, with
  the task in a prompt file, and exit 0 only once Claude is running.
- **`--id <ledger id>`** names the work item (checked, prefixed, noted in its history); the orchestrator must give
  `--id` or `--no-item`.

### `bosun status`

`bosun status` prints, in text, the schedule, recent sleep and wake events, the keep-awake, the windows, the Claude
Code login per window, the watchdog's checks, proposals waiting for your approval, open outreach and today's log.

`bosun status --json` prints one object, `contract` 1, that says how bosun is; the menu bar app polls it. It only
reads, always prints the object and exits 0.

<details class="note" markdown>
<summary>The fields of <code>bosun status --json</code></summary>

- **`level`**: `ok`, `warning`, `error`, or `stopped` after a `bosun quit` (an `error` if windows are still open:
  the quit did not finish) or a `bosun stop` today; with the `sentence` and `detail` lines.
- **`windows`** (the open ones) and **`waiting`** (those waiting for you).
- **`running`**: whether the windows are open: `false` when none is, `null` when they could not be read. With the
  level it tells a bosun to start from one that runs with an error.
- **`starting`**: `true` exactly when the sentence says "bosun is starting (the morning run)": no window open yet,
  the morning run going or about to, inside your hours or after Start bosun now outside them, and no error (which
  names the run as failed instead). The app then shows no Start and, outside your hours, no grey resting dot.
- **`keep_awake_until`**, and the **`schedule`**: `in_hours` (whether now is inside your hours, `null` when the
  settings did not load) and `next_start` (the next automatic start, ISO 8601 to the minute, or `null`).
- **`update`**: `state` (`none` or `running`), the windows it is `waiting_for`, and `last` (the record the last
  update left: its `id`, `started_at` and `step`, which stays after it ended, or null).
- **`console`**.
- **`problems`**: the watchdog's findings (but for its `log-errors` one), ERROR lines logged in the last hour, the
  watchdog itself not running, and whatever could not be read.
- **`attention`**: the windows that need you, each as `{"window", "problem"}`: a finding about one window (such as
  `stalled-start` or `waiting`), in the order of the problems, then a window waiting at a prompt that the `waiting`
  check has not named yet, which no level counts. `bosun attach <window>` opens it.

A missing session is judged by the watchdog's own gate: an error only when it should be running, not while resting,
starting or quit (`bosun watchdog --status` and the console's health judge it the same way). ERROR lines logged in
the last hour are a warning at any hour, read by the status itself: the watchdog's `log-errors` finding is never
counted, since outside your hours the watchdog holds its log offsets and the finding would stay until its next run
inside them. A warning also lists the watchdog itself not running: its launchd job installed and not loaded, or
loaded with no run for three of its five-minute intervals of the Mac being awake, counted from the latest of its
last run, the Mac's last wake and its boot (a time later than now, after the clock moved back, is left out).

</details>

### `bosun doctor`

It checks, in order: the tools (on launchd's PATH), the Claude Code login, the config, names, the schedule, the
watchdog's and learning's jobs, power (on AC, the lid open), the login keychain, shells left behind by earlier window
starts, the setup steps, the ontology and the ledger against their journals, what each window may reach, and what
the last `bosun update` left to you. It judges the watchdog as `bosun status` does: it skips runs while bosun is quit
or an update holds it, and a watchdog job that never ran warns three intervals after it was loaded.

<details class="note" markdown>
<summary>What fails and what only warns</summary>

- **The ontology and the ledger** are checked against their journals (`bosun kg validate` once there is an
  ontology, `bosun ledger validate --journal` once there is a ledger), each finding's line shown. Journal damage, a
  position that does not match or a rebuild that differs fails, since writes are refused or data is at risk. A broken
  rule, a conflict, an update waiting for its fact, an outside edit the next write adopts, or a knowledge file that
  does not parse (which the next write writes again) warns.
- **What each window may reach** (its connectors): what it reaches, what it only allows by name or by pattern, and
  each name not found. It warns while the orchestrator's list is `all` or `bosun.env` does not set it, and for a
  Claude Code window that another settings file would widen, which does not start. See
  [Connectors](configuration.md#connectors-what-each-window-may-reach).
- **Which of your plugins each window loads**: all of yours, or none or the ones it keeps, how many it switches off,
  what it keeps for its connectors. It warns while the orchestrator's list is `all` (a plugin's hook or skill can
  stop its unattended runs at a question: name the ones it needs, or set it empty for none), for a name not found, a
  plugin managed settings force on and a settings file that does not parse (what it says of plugins is not read),
  and says a window with a list does not start while `installed_plugins.json` does not parse. See
  [Plugins](configuration.md#plugins-which-of-yours-each-window-loads).
- **What the last `bosun update` left to you**: what to change by hand in your own files (warns), and the config keys
  it added that `bosun.env` does not set, with the value each has now and what it means.

</details>

### `bosun update`

How to use it: [Updating](updating.md). What each part does:

- **Channels.** On `BOSUN_UPDATE_CHANNEL=releases` (the default) it moves to the newest release, a dated tag
  `v<YYYY.MM.DD>` (a second one the same day `.2`); on `main`, to the head of the branch it tracks (`git config
  bosun.branch` in the versions folder's `bosun.git`, `main` by default; an install from before releases keeps
  following it). An install already past the newest release says so and changes nothing.
- **`--check`** changes nothing (no version folder, no hold, no lock): it looks the newest release up with `git
  ls-remote`, fetches its tag to resolve the commit it points at, reads its notes from the tag's message, and prints
  one line, or with `--json` one object. It exits 0 whatever it finds; a problem, such as an origin that cannot be
  reached, is its `error`.
- **`--skip <version>`** records a release not to be offered again (`update-skip.json` in the state folder), or on
  the `main` channel a head, by the name `--check` gives it (its commit's first 12 characters): `--check` then
  reports it as `skipped`, and the next one as usual. It does not stop an update you run.
- **`--back`** goes back to the version the last finished update came from, one step, never further: a second
  `--back` is refused (`bosun update` goes forward again), and so is one with no finished update, or to a checkout
  from before versions (by hand: [Going back](updating.md#going-back)). The version installed runs it and fetches
  nothing; the older version's folder is added again from `bosun.git` if it is gone (never by a dry run, which reads
  what it can of that version there and says what it cannot tell). It restores that update's
  backup only for what the older version cannot read: a store whose format or rules version differs between the two
  (the changes written since then go, kept in a backup it takes first), and `bosun.env`, whose changes by that update
  are taken back where they still read as it wrote them (the file kept as `bosun.env.pre-back-<time>`). Everything
  else stays, with what was written since; with nothing to restore it only switches back. The plan says which, and
  what restoring loses, before it asks. The windows come along as for an update, its summary says "bosun went
  back", and the version it left is recorded as skipped, so `--check` and the menu bar app no longer offer it.
- **`--branch <name>`** follows the branch `<name>` of the origin from now on (`bosun.branch` in `bosun.git`, and
  `BOSUN_UPDATE_CHANNEL=main` through `bosun settings set` if it is not), then updates to its head. A branch the
  origin does not have is refused before anything changes; `--dry-run` writes nothing; said no to, the install still
  follows the branch. `--branch main` follows `main` again.
- **A target older than the data.** Before it hands over to the new version, the version installed checks that it
  would not read the data in an older format, or under older rules, than the data is in (a branch from before a
  migration, a revert on `main`), and refuses, pointing at `--back`.
- **The plan** lists the commits, the stores' migrations, the prompts that changed, what each window gets, the
  launchd jobs to reload, a console to restart, and what stops it (something the new version needs and this Mac
  lacks, a store it cannot read, a link a backup cannot copy, a `kg forget` cut short). In a terminal it asks first;
  without one, or with `--dry-run`, it stops at the plan; `--yes` goes ahead without asking.
- **The update** rehearses the migrations and the stores' checks on a copy of the data, holds bosun's own background
  work and pauses writes briefly: it backs the data up (`backups/` in the state folder, the last 3 kept), migrates
  it, switches the install link to the new version and checks each store, and on any failure puts everything back
  as it was.
- **After the switch** it makes sure the install leads to the new version, reloads the launchd jobs whose plist
  changed and unloads those the new version has none of, restarts the check-in loop, runs the new version's `bosun
  app update` (never to an app older than the one installed), restarts the console's job when it is loaded and
  wanted, lifts the hold, and keeps what `bosun doctor` finds. Then it brings the windows along, each as the plan says but decided again from what it
  is doing now ([After an update](orchestrator.md#after-an-update-the-windows-brought-along)).
- **The summary** lists the commits and how long it took, each store's migrations and version bumps, the config it
  changed and its new keys, what to change by hand in your own files, how many prompts changed, the jobs reloaded,
  the handed tasks that keep their old text, what came of each window, what fails after the switch that did not
  before, and where the backup is.

<details class="note" markdown>
<summary>The windows' answers, a cut update, the JSON, and old versions</summary>

- **The windows' answers.** Each window gets a prompt to check for anything it missed and carry on, and answers with
  `bosun update note --window <w> --status` (the text on stdin; `--stock` is the list of background work before a
  restart). The update waits for every answer, saying which windows it waits for. Ctrl-C stops the wait and leaves
  them to the check-in loop, whose `bosun update advance` takes a turn at them each tick (nothing while an update
  runs; without a check-in loop, run it by hand); so does the update itself once only windows waiting for your
  answer at a prompt are left, naming each. The next update takes over the windows still pending: its plan lists
  them as still owed from the last update. A window whose restart did not start (its launch refused, or its agent
  exited at once) is not waited for: the summary says why.
- **The app and the console.** `bosun app update` goes by the app being in `~/Applications`, not by
  `BOSUN_MENU_APP`, which is only your wish: an app you added by hand is kept current, and with none it does nothing.
  A failure (a Team ID not filled in yet, no network) is a line in the summary and stops nothing. The console's job
  is restarted (`launchctl kickstart -k`) when it is loaded and its `console/wanted` marker is there, so the console
  serves the new version; a job that is not loaded is never started, and a console you started by hand is left to
  you.
- **The summary** is saved as `summary.md` in the backup's folder once the update is done (by the update, the next
  `bosun update` or the check-in loop, whichever finishes it). What fails after the switch is each `bosun doctor`
  FAIL with its detail that a doctor run before the hold did not show, with a count of those it did and of doctor's
  warnings. A failed update names its backup too.
- **Cut short.** An update cut short is put back by the next write to a store, or by the next `bosun update`, which
  also finishes one whose checks had passed. A dry run never carries on or puts back an update cut short; it says
  one is there.
- **`--json`** prints one object on every path: `contract` 1 (a number that only grows), the plan (`null` when none
  was made, as when it is up to date or refused), the update's record as it stands, the summary (`null` unless an
  update ended in that run), and `error`, `null` there. A failure of the command's own wrapper before any of that (no
  network, no release yet, Python too old, a checkout, a version folder it could not make) prints the same object
  with `plan`, `update` and `summary` `null` and `error` the one line it says, and exits as it does without
  `--json`. With `--check --json` the object is the check's: `contract`, `channel`, `installed` and `latest` as
  `{version, commit}` with `latest`'s `notes`, `available`, `skipped`, `ahead` and `error`. `update.json_object`
  builds it. An origin that lists no peeled line for an annotated tag, such as a git bundle, is read as rightly as
  GitHub.
- **Installs and checkouts.** It needs an install with its versions side by side (`~/.local/share/bosun-versions`,
  which `get.sh` makes); in a checkout of your own, update with git. The install line run again hands over to it,
  and its first run converts a checkout an older `get.sh` made, once
  ([The first update of an older install](updating.md#the-first-update-of-an-older-install)).
- **Old versions.** An update deletes older version folders, but keeps one with a change or a commit of its own in
  it, as it keeps the checkout from before versions while anything of yours is in it, and its summary says so. One a
  running process still uses is left to a later update.

Design: [design/update.md](design/update.md).

</details>

### `bosun app`

bosun acts on the menu bar app only through these commands ([design/menu-bar.md](design/menu-bar.md)):

- **`install`** downloads the release `app/VERSION` names, verifies it, and places it in `~/Applications`. It accepts
  only the publisher's notarized build, and anything else is refused in one line (exit 1), leaving `~/Applications`
  as it was. A running app is asked to quit once the new one is verified, and is opened again.
- **`install --build`** (developers, with Swift installed) builds the app from the source of the bosun it is run from,
  ad hoc signed, and installs it with no team or Gatekeeper check, saying it is a development build. See
  [Building the app](development.md#building-the-app).
- **`update`** installs `app/VERSION` when the installed version differs; with the same version, or no app, it says
  so and downloads nothing (`bosun update` runs it every time).
- **`remove`** quits the app, turns Open at Login off and deletes `~/Applications/bosun.app`; bosun keeps running.
- **`login on|off`** makes or removes the launchd job that opens the app at login (`<BOSUN_LAUNCHD_LABEL>.app`).
- **`status --json`** prints one object, `contract` 1: `installed` (the app's version, or null), `wanted`
  (`app/VERSION`), `path` and `login`.

These commands never write `bosun.env` (`bosun init` is its only writer); `install` ends, when `BOSUN_MENU_APP` is
not 1, with a line on how to have the watchdog watch the app.

<details class="note" markdown>
<summary>How <code>install</code> checks what it downloads</summary>

- **Where from.** The release `app-v<version>` of the repository `BOSUN_APP_RELEASES` names, else
  `app/publisher.json`'s, else the install's origin when that is on GitHub, downloaded with `curl -q` (no
  `~/.curlrc`), https only, also after a redirect; a failure says the HTTP status.
- **The archive** is unpacked in a hidden folder of its own in `~/Applications`, where it must be exactly one
  `bosun.app` and nothing else but macOS's `__MACOSX`. It refuses an archive with an absolute path, a `..` or a
  symbolic link, or one whose entries do not read back as its central directory lists them (a local header naming
  another path, a name twice), before unpacking it, and after unpacking, one with a symbolic link anywhere in it.
- **The signature.** It places the app only once `codesign --verify --deep --strict -R` passes against the
  publisher's Developer ID requirement (Apple's Developer ID chain, the bundle id, and the Team ID of
  `app/publisher.json` as the leaf certificate's organizational unit: checked in the signature's certificates, never
  from what `codesign` prints, which a signer can write into), `spctl --assess --type execute -vv` exits 0 and prints
  `source=Notarized Developer ID`, and its Info.plist's bundle id is the publisher's. It refuses with
  "app/publisher.json has no team_id yet" until the publisher's Team ID is filled in.
- **Placing it.** The app is renamed in, the old one set aside first, deleted once the new one is in, and put back
  if the rename fails or the install is ended by SIGTERM or SIGHUP. A killed install leaves a hidden
  `.bosun-app-<pid>-*` folder in `~/Applications`; the next `install` or `remove` removes those whose install is gone,
  giving the old app back first if a killed install left none, and `bosun uninstall` lists and removes them.
- **Not the publisher's app.** A `~/Applications/bosun.app` that is not the publisher's (another bundle id, or an
  Info.plist it cannot read) is never replaced or removed by `install`, `update`, `remove` or `uninstall`: it is
  left, with the reason.
- **Quitting the app.** It is asked to quit by its bundle id through `osascript` (never found by a process name). An
  answer `osascript` cannot give (as opposed to a clean not-running) refuses the install rather than replace an app
  that may be running, and macOS refusing the quit (Automation is not allowed) says where to allow it, or to quit
  the app by hand.
- **`--build`** runs `app/build.sh --identity - --out <a temporary folder>` and says when the build starts and, if it
  fails, shows the last 20 lines of its output. From an install it builds the installed version's source, not a
  checkout of yours, and SwiftPM's build folder (`app/.build`, hundreds of megabytes) stays there, which `bosun
  update`'s pruning and `bosun uninstall` do not count as your work.
- **`update`** decides before any Team ID check and whatever `BOSUN_MENU_APP` says.
- **`login on`** loads the job (RunAtLoad, not kept alive, `--at-login`), refused while no app is installed; with the
  job there, unchanged and loaded, it does nothing but say it is already on (a reload would restart an app the job
  started, so `bosun init` run again never does). Open at Login is on while that plist is in `~/Library/LaunchAgents`:
  `install.sh` and `bosun update` keep a present one current and never make one.

</details>

### `bosun ledger`

`list`, `show`, `upsert`, `transition`, `note`, `report` and `reports` (repo windows' outcomes, with `--ci` the CI
status of a pushed pull request; `--since` lists every one, with what you said in the window), `delegation` (where an
item was handed over, and whether its window is working on it), `said` (what you typed in repo windows), `working`
and `idle` (which item a window works on), `in-flight` (whether a window holds work), `compact-context` (what a
window gets back after compaction), `record-delegation`, `record-update` and `record-move` (used by `bosun
delegate`), `propose` (with `--send`, `--run` or `--tool`: the exact outward call), `approve` and `approvals` (see
[Approvals](permissions.md#approvals-approve-once-in-chat)), `resolve`, `resolve-carried-out` (one-time: settle
proposals whose approved call ran before the PostToolUse hook, by the transcript), `seen`, `record-source`, `kinds`
(id formats, Slack classes, state machines), `mark-run` (also prints where the Slack scan starts), `prune`, `render`,
`diff`.

- **Outreach** ([Waiting on someone](orchestrator.md#waiting-on-someone-outreach)): `reach-out` (alias `ask`),
  `sent`, `replied`, `outreach`.
- **Tasks and links** ([Tasks and links](orchestrator.md#work-we-take-on-tasks-and-links)): `spawn`, `link`,
  `unlink`, `fold`, `tree`, `migrate-links`.
- **Tickets** ([External tickets](orchestrator.md#external-tickets)): `ticket-sync`, `tickets`.
- **Workstreams** ([Workstreams](orchestrator.md#workstreams-what-an-item-is-for)): `ws add|list|show|set-state|alias`,
  `assign`, `summary`, `lint`, `list --ws`.
- **Checks:** `validate [--journal]`.

### `bosun kg`

- **Writing:** `add-entity`, `propose` (with `--from-user --quote`: your own words, verified in the transcript, which
  inside an agent session is the one that ran `kg`, so `--transcript` may be left out; `confirm`, `reject` and `batch
  apply` need the same from a session), `confirm`, `reject`, `supersede`, `batch` and `batch apply` (the daily
  review), `import` (proposals only, a dry run unless `--apply`), `identify`, `forget`, `expire`.
- **Reading:** `lookup` (with where each of its reviewers, owners and approvers prefers to be asked), `facts`
  (`--as-of`), `search` (by words too), `neighbors`, `context --for` (repeatable: one pack, one
  budget), `lint`, `validate` (the files against the journal they are the rebuild of), `stats`, `schema`.

See [Ontology](knowledge.md#ontology).

## Changing settings

`bosun settings` is the one way the menu bar app, and you at a terminal, change bosun's settings without `bosun
init`. The app never writes `bosun.env`: `set` writes it through `bosun init`'s own writer, with a backup
(`bosun.env.pre-init-<stamp>` beside it) and every other line and comment kept, and only for the keys it offers:
`BOSUN_DAYS`, `BOSUN_START_TIME`, `BOSUN_AWAKE_UNTIL`, `BOSUN_START_AT_LOGIN` (`0` or `1`) and
`BOSUN_UPDATE_CHANNEL` (`releases` or `main`). Anything else (the windows, connectors, agents, models) is `bosun
init`'s.

```sh
bosun settings get
bosun settings set BOSUN_START_TIME=09:00 BOSUN_AWAKE_UNTIL=18:00
bosun settings set BOSUN_UPDATE_CHANNEL=main --json
```

- **`get`** shows each setting's value (what `bosun.env` sets, else the default; none, never the default, when
  `bosun.env` cannot be read), whether it is one bosun can use, and whose the Mac's repeating wake is.
- **The checks**: the days in pmset's letters (`MTWRFSU`, each at most once), the times as `HH:MM`, the days and
  times checked together (an "awake until" equal to the start is refused), `0` or `1`, `releases` or `main`. An
  unknown key, a key without `=` or one given twice is refused, and nothing is written.
- **What follows a change**: a move of the start or the "awake until" moves `BOSUN_LEARN_AT` too, as a re-run of
  `bosun init` does; the days, times and starting at login reload bosun's launchd jobs (the morning job runs at the
  new time); a change of `BOSUN_AWAKE_UNTIL` moves the end of a keep-awake that is held now, or releases it when
  now is outside your hours.
- **The wake**: when the days or the start time change and the Mac's repeating wake is bosun's, `set` asks for your
  password first (`sudo -v`) and moves it with `sudo pmset repeat wakeorpoweron`. When sudo is cancelled or refuses,
  nothing is written. Another app's repeating wake is left as it is, and said, unless `--replace-wake`. A wake `set`
  sets is bosun's to set from then on: `BOSUN_WAKE=1` is written with the change (`bosun doctor` then checks it). With
  `BOSUN_WAKE=0`, bosun's own wake left from before is not moved either (`--json` shows it as `other`), and the note
  says that `sudo pmset repeat cancel` cancels it (and any other repeating event).
- **One at a time**: `set` is refused while `bosun update`, `bosun init` or another `set` runs (exit 1), and the
  wizard is refused while a `set` runs. A value or key it does not take is exit 2.

<details class="note" markdown>
<summary>The wake without a terminal, whose wake it is, the put-back, and the JSON</summary>

- **Without a terminal** to ask in, `set` asks sudoers first whether it lets that very `pmset` command run with no
  password (`sudo -n -l pmset repeat wakeorpoweron <days> <time>`), so a `NOPASSWD` rule for just that command is
  enough, and is refused otherwise; it then runs it with `sudo -n`, which never asks. `sudo -l` needs no password
  as soon as one of your rules has `NOPASSWD` (sudoers' `listpw`), so it can say yes where the command itself still
  needs one: `sudo -n pmset` then fails, and the put-back below undoes the write.
- **Whose wake it is.** pmset keeps one repeating event. The wake is bosun's only when `pmset -g sched` shows exactly
  the one bosun sets: `wakepoweron`, at the start time plus `BOSUN_WAKE_DELAY_MINUTES`, on `BOSUN_DAYS`. pmset names
  every day, weekdays, weekends and a single day, and prints any other days only as "Some days": such a wake at
  bosun's minute counts as bosun's, since its days cannot be read (another app's wake at that very minute would be
  taken for it).
- **The put-back.** If pmset or the reload of the jobs fails, `bosun.env` is put back from the backup that write made
  (or removed, when the write made it) and the jobs reloaded again, so the file, the jobs and the wake stay together;
  it fails in one line that says so.
- **The lock.** `set` holds `bosun init`'s lock from the check of the values to its last step, since the wizard
  writes `bosun.env` from what it read when it started, and would write over the change. The check for a running
  `bosun update` is made under the same lock.
- **`--json`**: `get` prints `{"contract": 1, "readable", "error", "config_dir", "wake": "ours"|"none"|"other",
  "settings": {KEY: {"value", "default", "set", "valid", "meaning"}}}`. A value it cannot show is never shown as the
  default: a `bosun.env` that cannot be read is `readable: false` with its `error`, and every setting's `value` is
  `null` and `valid` false. One the shell cannot load (it does not parse, or stops before its end) is `readable:
  true` with `error` saying why bosun cannot load it (bosun then runs on none of it), and each setting shows what the
  file assigns, as written. `set` prints `{"contract": 1, "changed": {KEY: [before, after]}, "notes": [...]}`
  (`changed` names `BOSUN_LEARN_AT` too when it moved along, and is empty when `bosun.env` said so already), or
  `{"contract": 1, "error": "..."}` when it refused or failed, the reason on stderr too. Exit 0 done, 1 refused or
  failed, 2 a value or key it does not take.

</details>
