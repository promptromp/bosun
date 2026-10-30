# bosun

Turn a macOS laptop into an always-ready agentic coding workstation. Every morning it:

1. **Wakes the Mac** on a schedule (`pmset`) and **keeps it awake** until evening (`caffeinate`).
2. **Refreshes credentials**, for example AWS SSO, skipping any that are still valid.
3. **Starts a tmux session** with one window per Claude Code session. Each window
   resumes a named session with **Remote Control** enabled, so you can drive it from your phone,
   and sends a **drain prompt** that finishes any work cut off by sleep or usage limits.
   On later days the session is usually still there. It is then **refreshed** window by window:
   idle sessions on an outdated Claude Code are restarted on the new version, and other idle
   sessions get the drain prompt. Busy ones are left alone.
4. Runs an **orchestrator** session that does the daily chores: Slack mentions (PR and tech-design
   review requests, other asks) and your own open PRs (reviewed? comments to address? needs a
   nudge? ready to merge?). It hands tasks to the repo sessions on its own, each with rules for what
   that kind of task may do (post a PR review, push a draft PR). Anything else other people would
   see waits for your approval, which you can give from your phone. A **ledger**
   with stable ids and state machines makes this idempotent across runs and days.

```
pmset wake 08:01 ─> launchd runs the 08:00 job it missed during sleep ──> bosun morning
                   ├─ caffeinate -i -s until 20:00
                   ├─ wait for network
                   ├─ setup.sh: ensure_claude_login, ensure_aws_sso my-dev-profile, ...
                   └─ bosun start ──> tmux -L bosun "bosun"  (exists? -> bosun refresh)
                                           ├─ orchestrator  fresh daily conversation: /bosun:chores morning
                                           │                  ├─ reads Slack, GitHub, Confluence; updates the ledger
                                           │                  ├─ bosun delegate review|fix-my-pr ... --window <repo window>
                                           │                  └─ proposes Slack posts, merges, pushes... -> you approve
                                           ├─ api           claude --resume <uuid> --remote-control api "<drain>"
                                           ├─ web           claude --resume <uuid> --remote-control web "<drain>"
                                           └─ checkin       every 60 min, when the orchestrator is idle:
                                                              /bosun:chores check-in
```

## Requirements

- macOS on Apple silicon or Intel, an **admin account** (for the one-time `sudo pmset`)
- Charger connected and **lid open** (or an external display) overnight
- `brew install tmux jq python` (`python3` must be 3.13 or newer; the macOS system one is 3.9), plus
  `ruby` (the macOS system one is fine)
- For the orchestrator: [GitHub CLI](https://cli.github.com) (`gh`), logged in (`gh auth login`)
- [Claude Code](https://claude.com/claude-code), logged in

## Install

```bash
git clone <this repo> ~/Development/bosun
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
`~/.local/bin` and loads the launchd jobs: the morning run, the [watchdog](#watchdog) and, with
`BOSUN_LEARN_ENABLED=1`, the daily [learning](#learning) run
(`<BOSUN_LAUNCHD_LABEL>.learn`).
`./uninstall.sh [--with-wake] [--purge]` reverses it.

## Configuration

All configuration lives in `~/.config/bosun/` (override with `BOSUN_CONFIG_DIR`).

| File | Purpose |
|---|---|
| `bosun.env` | Schedule, session name, Claude flags, drain prompt, orchestrator and chores settings. Every `BOSUN_*` value can also be overridden from the environment. Commands read the file each time they run, so edits apply at once, also inside running sessions. See `examples/generic/bosun.env` for all keys. |
| `sessions.yml` | One entry per repo window: `name` (the Claude session name) and `dir`, optionally `model` and `effort` for that window. The orchestrator is configured in `bosun.env`, not here. |
| `setup.sh` | Defines `setup_steps()`. Helpers: `ensure_claude_login` (see Claude Code login), `ensure_aws_sso <profile>`, `run_step "<desc>" cmd...`. |

State and logs go to `~/.local/state/bosun/`:
- `ledger.json` and its readable view `ledger.md`
- `logs/`: `morning-YYYY-MM-DD.log`, `checkin-YYYY-MM-DD.log`, `learn-YYYY-MM-DD.log`,
  `hook-YYYY-MM-DD.log` (the repo sessions' prompt hook), and what launchd captures from each job
  (`launchd.{out,err}`, `watchdog.launchd.{out,err}`, `learn.launchd.{out,err}`)
- `watchdog.json`: the watchdog's de-duplication state
- `login.json`: the last Claude Code login check (see Claude Code login)
- `events/`: the event queue (`queue.jsonl`) and what was delivered (`state.json`), with events on
- `slack-watch/state.json`: the Slack watcher's last runs, with it on
- `learn/`: the learning runs (`state.json`, and per run its digest and report, kept 14 days)
- `dry-runs/<timestamp>/` for chores rehearsals

### Model and effort

Every session bosun starts gets an explicit model and effort, so cost does not depend on
whatever you last picked by hand (Claude Code's `/model` and `/effort` save your choice as the
default for all future sessions, which is how sessions drift to Opus on high effort).

- Repo sessions: `BOSUN_MODEL` / `BOSUN_EFFORT` (default `opus` / `medium`), overridable
  per window in `sessions.yml` (`model:`, `effort:`).
- Orchestrator: `BOSUN_ORCHESTRATOR_MODEL` / `BOSUN_ORCHESTRATOR_EFFORT` (`opus` /
  `medium`).
- Delegated tasks: each task type sets an effort (reviews and investigations: `medium`; fixes on
  your PRs and new work: the session's own). A window opened for the task starts at it; a session
  that is already running runs the task in a subagent at it, so its own setting is untouched. The
  orchestrator passes `--effort high` only when you ask for more depth.

The model goes in as `--model`; the effort as a session-level setting (`--settings
'{"effortLevel":...}'`) rather than `--effort`, so `/effort` inside a session can still change
it. Empty values leave Claude Code's own default. `bosun send --list` shows what each window
was launched with, `refresh` restarts idle sessions whose launch settings no longer match the
config, and `bosun doctor` shows the configured values and your saved default.

### How sessions are resumed

`claude --resume <name>` opens a picker when several sessions share a name or prefix. That would
block an unattended start. `bosun launch` instead reads the session transcripts in
`~/.claude/projects/`, picks the **most recently active session whose name is exactly `<name>`**,
and resumes it by UUID. If none exists it starts a new session with that name. If the session is
already open in another terminal (per `~/.claude/sessions/<pid>.json`), it refuses rather than
running two copies on one transcript.

Every window gets `--name <name> --remote-control <name>` plus `BOSUN_CLAUDE_FLAGS`.

### Steady state: the session already exists

After the first day the tmux session normally survives the night, because the Mac only sleeps.
Re-attach any time with `bosun attach`. The morning run never duplicates the session.
It runs `bosun refresh`, which handles each window on its own:

| Window | Action |
|---|---|
| missing | create it and launch Claude |
| Claude not running | relaunch in the manifest directory (resume by UUID, drain prompt) |
| Claude `busy` (mid-turn) | leave alone |
| Claude `waiting` (a permission prompt or a question to you) | leave alone: a pasted Enter could answer it |
| window split into several panes | leave alone and report it (relaunching could kill the other pane's process) |
| Claude `idle`, on a different version than the installed Claude Code (usually older), idle at least `BOSUN_RESTART_MIN_IDLE_SECONDS` | restart: SIGTERM (Claude exits cleanly in about 2s), then relaunch on the new version. Same session UUID, so the conversation is intact, plus a fresh Remote Control connection. |
| anything else (idle and current, idle too briefly, or `shell`: turn over but a background shell still running) | send the drain prompt |

The drain prompt, also the one a relaunch or restart sends, goes only to a window with something in
flight: the orchestrator, a Claude in `shell`, or a window holding a handed-over item whose work is
not done (`bosun ledger in-flight <window>` lists them: awaiting a report, or being worked on).
Any other window is resumed or left idle without a prompt (`left idle: nothing in flight`), since
every prompt re-reads the whole conversation; it costs nothing until it gets work.

With `BOSUN_FRESH_CONVERSATIONS=1` (off by default), refresh, and so the morning run, also
starts a fresh conversation for a repo window that is idle for `BOSUN_RESTART_MIN_IDLE_SECONDS`,
holds nothing in flight, and whose conversation grew past `BOSUN_FRESH_CONTEXT_TOKENS` (200000;
the size its latest turn read, `claude_state.py context <pid>`). The new conversation is named
`<window>-<date>-<n>` (kept in the state dir's `conversations/<window>`, which `launch` resumes from
then on) and starts with a short prompt pointing at its memory notes and `bosun ledger list
--open`. `refresh --dry-run` names the windows that would get one, also with the flag off.

Status and version come from Claude Code's own `~/.claude/sessions/<pid>.json`. The status is
re-read just before a restart. Claude Code installs updates automatically but keeps running the old
version until restarted. `refresh` is how sessions pick up updates without losing work.

The morning run passes `--unless-active`: if someone typed in an attached tmux client within the
last `BOSUN_ACTIVE_CLIENT_MINUTES`, it leaves the windows alone instead of pasting into your
draft. Run `bosun refresh` by hand any time; add `--dry-run` to preview it.

`BOSUN_ON_EXISTING` picks the behaviour: `refresh` (default), `drain` (never restart) or
`restart` (kill the whole session and start fresh).

## Commands

| Command | What it does |
|---|---|
| `bosun morning [--dry-run] [--skip-setup] [--awake-minutes N]` | The scheduled entry point |
| `bosun setup [--dry-run]` | Run (or just check) the setup steps |
| `bosun alert [--dry-run] <message>` | Notify you: a macOS notification and, with `BOSUN_ALERT_SLACK_USER` set, a Slack DM to yourself sent by a headless Claude that can use only Slack's send-message tool and never waits for an approval |
| `bosun attach` | Attach to the session on its own tmux server (`tmux -L <socket> attach -t <session>`) |
| `bosun start [--restart] [--unless-active]` / `bosun stop` | Start the tmux session (or refresh it if it exists), or stop it; stop also releases the keep-awake |
| `bosun refresh [--dry-run] [--drain-only] [--restart-idle] [--unless-active] [window...]` | Per window: restart idle outdated sessions, drain idle ones, relaunch exited ones (see Steady state) |
| `bosun launch [--model M] [--effort E] <name> [prompt \| --prompt-file <file>]` | Resume a named session in the current directory, with the window's model and effort |
| `bosun send --list` / `send <window> <msg>` / `send --new [--model M] [--effort E] <window> <dir> <msg>` / `send --start <window> [msg]` | List windows with Claude status and version; hand work to a running session (bracketed paste, so multi-line text arrives as one prompt; refused while Claude waits at a permission prompt or question; it waits while someone typed in that window within `BOSUN_SEND_TYPING_MINUTES` or its input line holds unsent text, up to `BOSUN_SEND_WAIT_SECONDS`, and fails unless Claude took the prompt, so the caller resends), open a new window, or start Claude in a window that has none (resuming its named session; without a message it gets the drain prompt). A message of `-` is read from stdin. `--id <ledger id>` names the work item (checked, prefixed, noted in its history); the orchestrator must give `--id` or `--no-item`. `--new` and `--start` run `bosun launch` as the pane's own command, with the task in a prompt file, and exit 0 only once Claude is running. |
| `bosun delegate <type>[,<type>] --id <ledger id> --ask "<one line>" [--details ...] [--model M] [--effort E] (--print\|--window W\|--new W DIR\|--start W) [--again]` / `--update <ledger id> --details ... [--window W]` / `--list` | Hand a task to a repo session, with the rules for its task type from `orchestrator/tasks/`, and record the window on each item; refused while the item's last hand-over awaits a report, unless `--again`. `--update` sends an addendum to that window, framed with the item's id and kept in its history; with `--window` it moves the item: the new window gets the whole task, the old one is told to stop. `--update <out-id>` hands an outreach reply to the window holding its parent and marks it applied |
| `bosun chores [morning\|check-in] [--dry-run [--fresh\|--continue]]` | Tell the orchestrator to run its chores now, or rehearse them headless with no side effects |
| `bosun checkin [--loop] [--dry-run]` | Send `/bosun:chores check-in` if the orchestrator is idle and you are not typing (the `checkin` window loops this) |
| `bosun ledger <command>` | Work-item ledger: `list`, `show`, `upsert`, `transition`, `note`, `report` and `reports` (repo sessions' outcomes, with `--ci` the CI status of a pushed PR; `--since` lists every one, with what I said in the window), `delegation` (where an item was handed over, and whether its window is working on it), `said` (what I typed in repo windows), `working` and `idle` (which item a window works on), `record-delegation`, `record-update` and `record-move` (used by `bosun delegate`), `propose` (with `--send`, `--run` or `--tool`: the exact outward call), `approve` and `approvals` (see [Approvals](#approvals-approve-once-in-chat)), `resolve`, `resolve-carried-out` (one-time: settle proposals whose approved call ran before the PostToolUse hook, by the transcript), `seen`, `record-source`, `kinds` (id formats, Slack classes, state machines), `mark-run` (also prints where the Slack scan starts), `prune`, `render`, `diff`; outreach (see [Waiting on someone](#waiting-on-someone-outreach)): `reach-out` (alias `ask`), `sent`, `replied`, `outreach`; tasks and links (see [Tasks and links](#work-we-take-on-tasks-and-links)): `spawn`, `link`, `unlink`, `fold`, `tree`, `migrate-links`; tickets (see [External tickets](#external-tickets)): `ticket-sync`, `tickets`; workstreams (see [Workstreams](#workstreams-what-an-item-is-for)): `ws add|list|show|set-state|alias`, `assign`, `summary`, `lint`, `list --ws` |
| `bosun guard [--approvals\|--permission-request\|--post-tool-use]` | PreToolUse hook for the orchestrator (see Permissions); with `BOSUN_APPROVALS_ENABLED=1` also its approval hooks (see [Approvals](#approvals-approve-once-in-chat)) |
| `bosun hook user-prompt --window W` | UserPromptSubmit hook of repo sessions, installed with `BOSUN_EVENTS_ENABLED=1`: records what I type in the window (see [Events](#events-from-repo-sessions)) |
| `bosun events [--once\|--dry-run\|--status\|--list [--all]\|--covered]` | Push events from repo sessions (reports, what I typed there, outreach drafts, follow-up tasks, windows waiting at a prompt) to the idle orchestrator as one summary; the check-in loop runs it with `BOSUN_EVENTS_ENABLED=1` |
| `bosun slack-watch [--once\|--scheduled] [--dry-run]` / `--status` | Look for new Slack mentions, DMs and replies in your threads with a headless Claude that can only read Slack, and queue each new one as an event for the orchestrator; the check-in loop runs it with `BOSUN_SLACK_WATCH_ENABLED=1` (see [Slack watcher](#slack-watcher)) |
| `bosun context` | The orchestrator's working context: config, windows, `owner/repo` -> directory map, open items |
| `bosun pr-threads <pr-url> [--awaiting-author] [--json]` | Review threads on a PR (read-only GraphQL): each unresolved thread with its replies and last commenter, whether it awaits the PR author (unresolved, last comment not by the author; bots are shown, marked, and never count, except reviewer bots: `BOSUN_REVIEWER_BOTS`, see below), the threads per review round, and each reviewer bot's latest verdict. `--awaiting-author`: only those, exit 3 while any are listed (the check before saying a review is addressed) |
| `bosun review-watch [--dry-run] [--quiet] [--id <mypr id> ...]` | New reviews (changes requested or comments, by people other than the author or by reviewer bots, never other bots) on my PRs in `addressing`, `awaiting-review` or `approved` since their last hand-over (or report): recorded once on the item as `review-arrived` (no state change) and, with events on, queued as `review` events; prints each one still to act on with its next step. The chores run it in step 4; with events on, the check-in loop every `BOSUN_REVIEW_WATCH_MINUTES` (see [Events](#events-from-repo-sessions)) |
| `bosun my-prs [--since 48h\|7d\|2w\|YYYY-MM-DD] [--by updated\|created] [--include-drafts]` | My open PRs within the repo mask and time window, as JSON lines |
| `bosun resolve <dir> <name>` / `<dir> --list` / `--live <uuid>` | Inspect session-name resolution (exit 3: no such session, or none live) |
| `bosun trust [--check\|--revoke] [--any] <dir>...` | Mark repo folders inside the repo mask as trusted by Claude Code, so unattended starts do not stall at its trust prompt |
| `bosun doctor` | Check tools (on launchd's PATH), the Claude Code login, config, names, schedule, the watchdog and learning agents, power, keychain, shells left behind by earlier window starts, setup |
| `bosun orphans [--apply]` | List the shells earlier window starts left behind: helpers a pane's launch shell (`<shell> -lic <this checkout>/bin/bosun launch ...`) forked off while reading your startup files (a prompt theme's git-status daemon), adopted by launchd, with no children and not tmux itself. `--apply` stops them (SIGTERM, after checking the pid again). `launch`, `refresh` and `stop` stop such helpers themselves |
| `bosun watchdog [--once\|--dry-run\|--status\|--list\|--facts] [--only <check>,...]` | Alert you once per problem: no or failed morning run, missing session or check-in loop, a Claude waiting at a prompt, new ERROR lines in the logs, ledger items stuck in `delegated`, a background command keeping a Claude in `shell` for hours, an expired Claude Code login, and with `BOSUN_LEARN_ENABLED=1` a missing, failed or stalled learning run (see Watchdog). Its own launchd agent runs it every 5 minutes; `--facts` prints what it reads of tmux, ps and launchd as JSON (what the console reads) |
| `bosun sessions [--json]` | Every window (configured or open) with its kind, folder, Claude status, session id and transcript path |
| `bosun learn [--since 24h\|3d\|<ISO time>] [--until <ISO time>] [--dry-run\|--collect-only] [--out <dir>]` / `--scheduled` / `--status` (also `status`, `list`) / `apply <learn-id>` / `done\|reject <learn-id> [--note <text>]` | Look back over the sessions, ledger and logs since the last run and propose memory notes, rules and setup changes, each a `learn:` ledger item waiting for your approval (see [Learning](#learning)). `--scheduled` is what its launchd agent runs: once a day, after `BOSUN_LEARN_AT` |
| `bosun kg <command>` | Organizational knowledge (see [Knowledge](#knowledge)): `add-entity`, `propose` (with `--from-user --quote`: my own words, verified in the transcript), `confirm`, `reject`, `supersede`, `batch` and `batch apply` (the daily review), `import` (proposals only, a dry run unless `--apply`), `identify`, `forget`, `expire`; reading: `lookup`, `facts` (`--as-of`), `search`, `neighbors`, `context --for`, `lint`, `stats`, `schema` |
| `bosun console [--port N] [--open]` | A local web console over the ledger, the sessions, bosun's health and the knowledge base; read-only except for knowledge review decisions (see [Console](#console)) |
| `bosun impact collect [--since YYYY-MM-DD] [--dry-run]` | Collect the console's Impact snapshot: my merged PRs and my reviews of other people's PRs (GitHub, read-only) and, per bosun window and day, agent busy time, the prompts I typed and tokens; daily from the morning run with `BOSUN_IMPACT_COLLECT=1` (see [Impact](#impact)) |
| `bosun status` | Schedule, recent sleep/wake events, assertions, windows, the Claude Code login per window, the watchdog's checks, proposals awaiting approval, open outreach, today's log |
| `bosun wake-test [N] [--full]` / `--report` / `--cleanup` | Real sleep/wake end-to-end test |

## Development

`uv sync && uv run pre-commit install` once after cloning. Commits then run ruff, mypy --strict,
complexity limits (complexipy, lizard, and a shell checker in `tools/`), shellcheck, shfmt, a
secrets scan (detect-secrets) and the unit tests; pushes also run the smoke suite. A
forbidden-terms check (`tools/forbidden_terms.py`) refuses staged files and commit messages that
contain a term from `~/.config/bosun/forbidden-terms.txt` (one case-insensitive regex
per line, `#` comments), a list you keep outside the repo of names that must never be committed:
your organization, products, repos, colleagues, internal hosts and ids. Without the file it
passes. `uv run pre-commit run --all-files` checks everything by hand. See `CLAUDE.md` for the
limits and conventions.

On GitHub, every pull request to `main` runs the same checks (`.github/workflows/ci.yml`), the
smoke suite on macOS and a gitleaks scan of the new commits, and `main` only takes pull requests
that pass them. For the forbidden-terms check to run there too, store your list as the
`FORBIDDEN_TERMS` Actions secret with `gh secret set FORBIDDEN_TERMS <
~/.config/bosun/forbidden-terms.txt`; the logs never print what it matched.

## Testing without waiting for the schedule

Work up the levels; each one exercises more of the real chain.

1. **Unit and integration:** `tests/smoke.sh`. It runs the Python unit tests in `tests/unit/`
   (ledger and outreach, delegate, Claude Code state, repo mask, permission guard, the watchdog's
   checks with an injected clock, learning's digest, ledger checks and proposals, and replays of
   real orchestrator and repo-session interactions, anonymized and moved to a fictional domain,
   from `tests/fixtures/corpus/`) and then end-to-end checks,
   including setup with a fake `aws`, the morning dry run, and install/uninstall with fake
   `launchctl` and `pmset` under a test label. Hermetic: temp HOME, fixture sessions, fake Claude
   processes with fake status records for the refresh, orchestrator and check-in rules, ledger
   state machines and concurrency, a fake `claude` that registers itself for `send --new` and
   `--start` (long, multi-line prompts arrive intact; failures are reported), and a fake `claude`
   for the chores dry run, and one for the learning analysis. Isolated tmux socket; no Claude
   calls, no launchd or pmset changes.
2. **Config sanity:** `bosun doctor`, then `bosun morning --dry-run`. Once the session
   exists, the dry run also prints the refresh action for each window. `bosun watchdog
   --status` and `--dry-run` show what the watchdog sees and would alert, without sending.
3. **Session start, now:** `bosun start`, then `bosun attach`. Preview the
   steady-state path with `bosun refresh --dry-run`, then run it for real.
4. **Through launchd, now:** `launchctl kickstart gui/$(id -u)/com.bosun.morning`, then
   `bosun status`.
5. **Real sleep and wake:** `bosun wake-test 3` loads a temporary launchd job due in 2 minutes,
   schedules a one-off wake in 3 minutes (the same order as production) and puts the Mac to sleep,
   retrying if a touch or notification cancels the sleep. Scheduling the wake needs
   admin rights: `sudo` in a terminal, or the macOS password dialog when there is no terminal
   (e.g. Claude Code's `!` prefix). The wake is scheduled first, so a cancelled prompt changes
   nothing. **Keep your hands off the keyboard and trackpad during the 20-second countdown**: any
   touch as the Mac goes to sleep cancels the sleep. The test detects this and tells you. Afterwards run
   `bosun wake-test --report` to see the Wake/Sleep events, the keep-awake assertion, the
   morning log and the test session's windows, then `--cleanup`. By default the test windows only
   print the `claude` command they would run. `--full` also runs setup and launches Claude for real.
6. **Orchestrator chores, no side effects:** `bosun chores morning --dry-run` (or `check-in`).
   It runs the real procedure headless against real Slack, GitHub and Confluence reads, and
   prints the plan, the ledger changes and any blocked tool calls. The safeguards: a scratch
   copy of the ledger (`--fresh` starts empty), `bosun send` only logs, and outward-facing
   tools are denied (by headless mode and by name). It uses the checkout you run it from, also
   for the `bosun` calls Claude makes, so it rehearses a branch before you install it. To
   check idempotency, run it again with `--continue`, which starts from the previous rehearsal's
   ledger. Nothing already processed should be processed again.
7. **What I say, translated:** `uv run python tests/llm/free_text.py [--runs 2] [--only O1,R4]`
   runs the real, headless Claude (the orchestrator with its policy, settings, model and effort;
   repo sessions with their real task text) on things I say in my own words ("ask Dana whether
   ...", "ping Priya again", "Marco answered, go ahead with b", and things that must not become
   outreach) and checks the resulting ledger. It runs with no MCP servers (`--with-mcp` loads
   yours, read tools only). Each run has a scratch world: scratch HOME, ledger
   and tmux server, sends only logged, outward tools denied. It costs money (about $0.3 per case
   and run, for each case in `tests/llm/cases.py`), so neither the smoke suite nor the hooks run
   it; run it after changing `policy.md`, `chores.md` or the task files.
8. **What learning finds:** `uv run python tests/llm/learn_eval.py [--runs 2]` builds a
   fictional world (acme's invoice exporter, three days) with planted problems (an overnight
   permission prompt, a repeated guard denial, a duplicate hand-over, a correction made twice, a
   missing report, an unanswered question with no nudge, an idle delegated window, drafts never
   proposed, duplicate and mis-scoped outreach) and checks that `bosun learn`'s analysis finds
   them (recall), invents nothing (precision) and proposes well-formed changes. Real Claude, no MCP
   servers; about $0.35 per run with opus. Run it after changing `orchestrator/learn.md`.
9. **Orchestrator for real:** `bosun start`, attach, and watch the `orchestrator` window run
   `/bosun:chores morning`. Nothing outward-facing happens until you approve it there or from
   your phone. `bosun chores check-in` triggers a check-in on demand.

## Orchestrator

The `orchestrator` window runs an interactive Claude session with Remote Control, so you can
answer it from your phone. Unlike the repo windows it:

- **starts a fresh conversation every day** (`orchestrator-YYYY-MM-DD`). `refresh` restarts it once
  it is idle the next morning. Continuity lives in the ledger, not the chat history.
- runs in its **own permission setup**: `BOSUN_ORCHESTRATOR_FLAGS` (default
  `--chrome --permission-mode auto`) plus `orchestrator/settings.json`, never
  `--dangerously-skip-permissions`.
- loads `orchestrator/policy.md` as an appended system prompt and the `orchestrator/plugin`
  commands. The main one is `/bosun:chores morning|check-in`.
- must run in a folder Claude Code **already trusts** (`BOSUN_ORCHESTRATOR_DIR`, default your
  repos dir). A trust prompt would block an unattended start; `bosun doctor` checks this.

### Delegated tasks and folder trust

The orchestrator hands work to repo sessions with `bosun delegate <type> --id ... --ask ...`.
The task text is built from `orchestrator/tasks/`: one file per task type with what the repo
session may and may never do and what the orchestrator does afterwards, plus `_common.md` with
the rules every task shares. The orchestrator only supplies the facts (ledger ids, link,
requester, a one-line ask, and your instructions in `--details`), so the rules cannot drift.
`bosun delegate --list` shows the types:

| Task | The repo session may | It never |
|---|---|---|
| Address review comments on my PR | push new commits to the PR branch; reply in the review threads it addressed (every thread awaiting the author in a fresh `bosun pr-threads --awaiting-author`, any round) | resolve threads, re-request review, merge, post in Slack, reuse an earlier draft or file for a "review addressed" message |
| Investigate or answer a question | write findings to a local markdown file | post anything |
| New work for me | work in a git worktree, push a new branch, open a draft PR | request reviewers, mark the PR ready, merge, post in Slack |
| Review someone else's PR | post one review with inline comments; APPROVE if nothing blocks merging, otherwise COMMENT; skip a PR that is merged, closed or already approved by someone else, unless the ask names something specific | request changes, push to the PR branch, merge, post in Slack |

Asking for review stays with you: the orchestrator proposes marking the PR ready (a draft does not
notify code owners) and the Slack message, with the exact text. No task merges, force-pushes,
pushes to the default branch or posts in Slack, Jira or Confluence. The orchestrator delegates
`review` and `fix-my-pr` tasks on its own; other work (for example a Slack ask for a code change)
only once you ask or approve, and so does handing an item over again before its window reported,
or moving it to another window. Each session reports with `bosun ledger report` (see Ledger).
These rules live in the task text: a repo session running with
`--dangerously-skip-permissions` follows them but is not technically prevented from doing more.
To change a rule, edit the task file; `tests/smoke.sh` checks that the ledger steps are valid and
that this table matches.

**Site overlays.** To adapt the orchestrator to your team without forking, put files in the config
directory: `policy.md` is appended to the orchestrator policy; `tasks/<type>.md` adds a task type
(same format as `orchestrator/tasks/`; a file named like a built-in type replaces it); and
`tasks/_common.md` adds rules to every task (the built-in shared rules always stay).
`bosun doctor` lists the overlays in use.

Claude Code shows a folder trust prompt the first time it opens a repository (trusting a parent
folder does not cover it) and has no setting or flag to skip it; only headless `-p` runs skip it.
An unattended start would stall there. `bosun trust <dir>` records the same entry Claude
writes when you accept the prompt (`hasTrustDialogAccepted` in `~/.claude.json`, an internal
format) for git repos inside the repo mask, and `bosun send --new|--start` (and so
`delegate --new|--start`) call it for their folder (`BOSUN_TRUST_MASKED_REPOS=1`). Outside the mask they fail at once with the command to
run. `bosun doctor` checks every session folder.

### Chores

`orchestrator/plugin/commands/chores.md` holds the whole procedure:

0. **Orient:** record the run (`ledger mark-run`, which also prints where the Slack scan starts),
   read `bosun context`, and the reports repo sessions filed since the scan window began.
1. **Slack:** mentions and DMs in the scan window: since the last run for check-ins; in the
   morning the last `BOSUN_SLACK_LOOKBACK_HOURS` (24), or from the last scan if that was
   longer ago (a failed morning, days away), but at most `BOSUN_SLACK_MAX_LOOKBACK_HOURS` (96)
   back. Each message is checked against the ledger (`seen`) and classified as `pr-review`,
   `td-review`, `my-pr`, `mention` or `fyi`.
2. **Review requests:** derive state from GitHub. If you already reviewed, mark it `reviewed`.
   Otherwise hand it to the repo window, which posts the review with inline comments and approves
   when nothing blocks merging, or propose opening a window for it.
3. **Tech design reviews:** read the Confluence page, draft comments, and propose posting them.
4. **Your open PRs** (found with `bosun my-prs`; see the repo mask and window below), checked through `gh` and
   `bosun pr-threads`:
   - changes requested: the repo session pushes fixes and answers every thread that awaits you
     (`pr-threads --awaiting-author`, any round); once that lists nothing, the orchestrator
     proposes re-requesting review, with the thread counts per round
   - approved and green: propose a squash-merge
   - waiting too long: propose a nudge in the original review-request thread
5. **Outreach** (what we wait on from other people): propose the message for new drafts, check
   sent ones for a reply on their channel, hand replies to the window holding the work, propose
   nudges. See [Waiting on someone](#waiting-on-someone-outreach).
6. **Mentions:** new asks are surfaced under FYI with a one-line summary, with a draft reply
   proposed when one fits. A mention that asks us to do something becomes a proposed task spawned
   from it, with its `delegate new-work` / `investigate` command; nothing is delegated before you
   say so.
7. **Tasks and tickets:** proposed tasks wait for your yes (see
   [Tasks and links](#work-we-take-on-tasks-and-links)); accepted ones are handed over, delegated
   ones followed, and their PRs linked. Tickets of open work are read with the tracker's read
   tools and recorded; where a ticket and the work disagree, the exact transition is proposed
   (see [External tickets](#external-tickets)).
8. **Report:** group the reply as *Needs your approval* (numbered, with exact text; also earlier
   proposals you have not answered and `learn:` proposals, see [Learning](#learning)), *Handled
   automatically* and *FYI*. You answer "1 and 3 yes, 2 skip". On a Monday morning it also offers
   a stand-up draft per active workstream (see [Workstreams](#workstreams-what-an-item-is-for)).

**Scope.** `BOSUN_REPO_MASK` limits the orchestrator to matching repositories. It is a
space-separated list of `owner/repo` glob patterns, for example `my-org/*` or
`my-org other-org/one-repo` (a bare owner means all its repos; empty means everything). It is
enforced in code, not just the prompt:
- `bosun my-prs` filters by it
- the ledger refuses `review:` and `mypr:` items outside it
- `bosun context` only maps matching repos

**PR window.** `bosun my-prs` discovers your open PRs with activity within
`BOSUN_MY_PR_WINDOW` (default `48h`). Pass `--since 7d`, `--since 2w` or `--since 2026-09-01`
for a wider sweep, and `--by created` to filter by creation time instead of activity. You can also
just ask the orchestrator ("check my PRs from the last week"). PRs already in the ledger stay
tracked until merged or closed, whatever the window.

Each day's new orchestrator conversation starts with `/bosun:chores morning`; with
`BOSUN_ORCHESTRATOR_MORNING_CHORES=0` it starts idle instead, and the chores run only when you
or a check-in ask for them.

The `checkin` window sends `/bosun:chores check-in` every `BOSUN_CHECKIN_MINUTES`. It only
does so when the orchestrator has been idle for 5 minutes and you have not typed in tmux recently,
so it never interrupts a conversation. `shell` counts as idle (the turn is over and only a
background command still runs, such as a wait for a review to settle): the check-in goes in ahead
of that command's completion notice, and the 5 minutes count from the end of the turn. The first
check-in comes one interval after the window starts, since the orchestrator begins the day with
the morning chores. A check-in held back only by timing (the orchestrator busy or idle too briefly,
or you typing) stays due: the loop retries it every tick (`BOSUN_EVENTS_INTERVAL_SECONDS`, or
every few minutes with events off) until it goes out, logging the skip once, so a busy morning
never pushes it a whole interval later. Events keep flowing while it waits.
A check-in also goes out only when something is new since the last one (`lib/checkin_gate.py`, no
Claude call): events not yet delivered, Slack messages the watcher found (or a watcher that is off,
failing or stalled, since the check-in then scans Slack itself), a repo session's report, an outreach
whose wait crossed a business day or whose `expect_by` passed, or a new watchdog alert. Otherwise it
logs `skip: nothing new since HH:MM`, at most `BOSUN_CHECKIN_MAX_QUIET_HOURS` (4; 0 turns the
gate off) in a row, so the chores still run. Every check-in makes the orchestrator re-read its
whole conversation, which is why quiet hours skip it.

A permission prompt or question left open in the orchestrator pauses the check-ins until you answer
it. The [watchdog](#watchdog) alerts you when it, or any other Claude window, has waited
`BOSUN_ALERT_WAITING_MINUTES` (30 by default).

### Ledger: idempotency and state

`bosun ledger` (`lib/ledger.py`) is the only way the orchestrator, and the repo sessions it
delegates to, record state. It keeps:

- **items** with stable ids: `review:<owner>/<repo>#<n>`, `td:<page-id>`, `mypr:<owner>/<repo>#<n>`,
  `slack:<channel>/<ts>`, outreach, `out:<parent-id>/<n>` (see below), learning proposals,
  `learn:<YYYY-MM-DD>/<n>` (see [Learning](#learning)), tasks, `task:<YYYY-MM-DD>/<n>`, and
  external tickets, `jira:<KEY>-<n>` or `ghissue:<owner>/<repo>#<n>` (see
  [Tasks and links](#work-we-take-on-tasks-and-links)), and workstreams, `ws:<slug>` (see
  [Workstreams](#workstreams-what-an-item-is-for)). Each kind has a state machine
  (`bosun ledger kinds`); invalid transitions are rejected.
- **links** between items (`spawned_from`, `produces`, `blocked_by`, `duplicate_of`, `folded_into`,
  `tracks`, `part_of`, `relates_to`), stored once and read both ways.
- **sources**: every processed Slack message (by permalink). A message still in tomorrow's scan
  window is recognised and skipped.
- **proposals**: actions awaiting your approval, which survive the daily restart.
- **runs**: when each chores mode last ran, which sets the check-in scan window.
- **delegations and reports**: `bosun delegate` records which window works on an item (and
  the task's header, so the task can be sent again), and `delegate --update` sends that window
  addenda framed with the item's id. Repo sessions record their outcome with
  `bosun ledger report <id> --summary ... --result <URL or path> [--open ...] [--state ...]`,
  which the orchestrator reads with `ledger reports`, so no step depends on chat history or on a
  window's screen. A hand-over ends with a report, or when the item reaches a state that shows
  the work done (`work_done` in `ledger kinds`: `reviewed`, `commented`, `awaiting-review`,
  `approved`, or a terminal state). Until then `ledger list` shows `@<window> (awaiting report)`,
  `delegate` refuses the item without `--again`, and `delegate --update <id> --window <window>`
  can move it: the new window gets the whole task, the old one a stop notice by id (if it cannot
  take it, the move is still made and the history says so). A report ends the hand-over, not the
  work: `ledger list` shows `(working)` while the window's Claude is mid-turn on the item (its
  active item: the latest hand-over, report, `ledger working` or something I said there), and
  `ledger delegation` has `"working"`; a window is free only when none of its items is working.
- **what I said in repo windows** (`said`), with [events](#events-from-repo-sessions) on: each
  prompt I type there, on the item it is about, read with `ledger said [<id>]`.

The re-review rule is enforced. `reviewed` -> `rereview-requested` (and `commented` ->
`rereview-requested` for tech designs) requires `--evidence` of a new, explicit request after your
last review: GitHub re-requesting you, or a new Slack message asking again. Nudges follow the
chores procedure (the prompt, not code): only for your non-draft PR in `awaiting-review`, never
once it is `approved`.
Writes take a file lock and replace `ledger.json` atomically, keeping the previous version as
`ledger.json.bak`; `ledger.md` is regenerated after each one. Each item keeps its last 200
history entries.

### Waiting on someone: outreach

Work often stops on someone else: a product decision, another team's contract, a vendor's file
format, a reporter's environment. Such an ask is an **outreach** item, so it does not live only in
chat, a PR description or a Slack reply:

| Field | |
|---|---|
| id | `out:<parent-id>/<n>` for the work item it blocks, or `out:<YYYY-MM-DD>/<n>` when it belongs to none (no item id looks like a date: each starts with its kind's prefix) |
| `channel` | `slack`, `email`, `jira`, `github`, `confluence` or `other` (one table in `lib/ledger.py`) |
| `purpose` | `question`, `decision`, `clarification`, `approval`, `review`, `info-ack` (they confirm they saw it) or `fyi` (one-way: no reply expected, so it is done once sent) |
| `to`, `subject`, `options`, `details`, `expect_by` | whom, what (one line), the choices, context, and an optional date |
| `messages` | every message sent for it, the first and each follow-up: ref (permalink, comment URL or message id), channel, and when it was sent (`--at`, else a Slack permalink's own time, else when recorded) |
| `answer`, `reply_ref` | the reply in one line, and the reference to it (the evidence) |

`draft` (recorded) -> `proposed` (the outgoing message awaits my approval: `ledger propose` moves
it there, rejecting the proposal moves it back) -> `sent` (`ledger sent --ref`; later calls add
follow-ups, counted as nudges; several ids record one message on each, e.g. one nudge for several
questions; an `fyi` item goes straight on to `applied`) -> `replied` (`ledger replied --evidence --summary`) -> `applied`
(`bosun delegate --update <out-id>` hands the reply to the window holding the parent, or it was
surfaced to me). `no-reply` gives up after the last nudge; `dropped` is the way out from any state
(`ledger fold <out-id> --into <other-id>` when its questions go into another message,
`--note 'duplicate of <other-id>'` when it asks exactly what another asks).

- **Parents show it and stay open.** `ledger list` shows `waiting on out:.../1 (slack to Dana, 2d)`
  on the parent, and the ledger refuses to finish a parent (any terminal state, also through
  `report --state`) while its outreach is open, unless `--force-open-outreach` (the note says why).
  A PR that merged or closed is a fact, not a choice, so those states go through and the note
  names the outreach still open. Its open review requests (purpose `review`) are dropped with it,
  since nobody owes that review any more; other outreach (a question) stays open.
- **Replies are matched, not re-classified.** `ledger seen <permalink>` names the open outreach
  whose Slack thread (or DM) a new message is in (all of them, latest message first, when several
  share it, so the orchestrator picks the one it answers), so the reply is recorded on it
  (`--class outreach`) instead of becoming a new mention. Other channels: the chores read the
  ticket, PR or page comments, or ask me (email); see step 5 of the chores.
- **One conversation, one item.** `ledger reach-out` warns (it does not refuse) when the parent
  already has open outreach to the same person, so an addition goes out as a follow-up on that
  item instead of a second one waiting in the same thread. `ledger upsert <out-id> --field
  subject=... --field options='a | b'` keeps the record in line with the message actually sent.
- **Nudges are proposals.** After `nudge_after_business_days` without a reply, and below
  `max_nudges`, the chores propose a follow-up in the same conversation; nothing is sent on its own.
  A PR waiting on its reviewers has no fixed count (`max_review_nudges`, 0 = no cap): it is nudged
  each business day while the next move is the reviewers', after a fresh check of the PR, its
  threads, the conversation and the ledger, at most once per reviewer per day. After
  `review_escalate_after_nudges` unanswered nudges the chores also propose another reviewer.
  The watchdog does not alert on outreach.
- **Times are when it happened, not when it was recorded.** The wait counts from the latest
  message's time. `ledger sent` and `ledger replied` take it from `--at` (ISO 8601 with an offset,
  or local `YYYY-MM-DD HH:MM`; never in the future), else from a Slack permalink's own ts, else
  now; `sent --at` with a ref already recorded corrects that message's time. The history entry
  keeps the recording time and says where the other came from (`happened_at`, `time_from`).
- **Free text.** You never type these commands. Tell the orchestrator "ask Dana whether we can drop
  the shared password", "email the vendor to clarify the format and wait for them", "ping Priya
  again", "Marco answered in the thread, go ahead with b"; the policy maps each to the steps above.
  Repo sessions record outreach for decisions they cannot make (`orchestrator/tasks/_common.md`)
  and never send it; the orchestrator proposes the message. Questions for you are not outreach:
  repo sessions ask you directly when you are at the window, or put the decision in their report.

### Work we take on: tasks and links

Work we decide to do that has no artifact of its own yet is a **task**: what a colleague asks of
us in a reply or a mention ("yes please, fix the other callers too", "could you also ..."), what
you tell the orchestrator to do, a follow-up a repo session finds. It is spawned from the item it
came from, so its origin is a link rather than a note, and it is handed over under its own id.
Design and phasing: [docs/design/lineage.md](docs/design/lineage.md).

| State | |
|---|---|
| `proposed` | `ledger spawn [<from-id>] --title --ask [--via <permalink or 'user: ...'>] [--requester] [--repo] [--blocks]` records it; an exact repeat is unchanged, a similar open task from the same source is a warning |
| `accepted` | only on your word: `--evidence 'user: <what you said>'` is required, so a colleague's message never accepts work; it closes the task's pending proposal |
| `delegated` | `bosun delegate new-work --id <task id>` (it refuses a task you did not accept, and an outreach id as the work item) |
| `done` | what it produced landed (its PR merged; `ledger tree` says so), or its report answered the ask |
| `declined`, `dropped` | you said no; or it went away: `ledger fold <id> --into <other-id>` when its work goes on as part of another item, `--note 'duplicate of <id>'` when it was the same item twice (both record the link) |

**Links** are stored once, on the item they start from, and read both ways (`ledger show` lists
them under `derived`, with the lineage tree and hints): `spawned_from` (provenance, with `via`:
the message that asked, or `user: ...`), `produces` (a task's PR), `blocked_by` (the item is not
finished while the other is open, like an outreach parent), `duplicate_of` (the same item twice),
`folded_into` (its work goes on as part of the other, e.g. in that task's PR; read back as
`absorbs`), `tracks` (an external ticket), `part_of` (the item's one workstream, see
[Workstreams](#workstreams-what-an-item-is-for)) and `relates_to`. `ledger link` refuses unknown ids,
self-links and cycles; `ledger tree <id>` shows where an item came from and what came of it, with
states; `ledger list` shows `from <id>`, `-> <id>`, `tracks <id>`, `folded into <id>` and
`absorbs <id>`. An outreach's `parent` stays a field and reads as
a computed `blocked_by`. `ledger reports` prints the commands that record a PR a report names and
link it to the work. `ledger migrate-links [--dry-run]` moves ad hoc provenance into links once
(an `origin` field naming an item, "merged into <id>" and "duplicate of <id>" drop notes, and a
`duplicate_of` recorded from a "merged into" note, which becomes `folded_into`); it is idempotent.

`ledger fold <id> --into <other-id> [--note ...]` records that an item's work went into another
(a small task done in another task's PR, an outreach draft said as a follow-up on an existing
conversation): it links `folded_into`, withdraws the item's pending proposal (it was neither
carried out on the item nor rejected), and closes the item in its kind's way out (`dropped`;
`dismissed` for kinds without it) with the note `folded into <other-id>: ...`. It refuses an item
with open outreach or blockers as `transition` does (`--force-open-outreach`), and a finished
target unless `--force-closed-target` (the work already landed with it). If the target is with a
repo window it prints, and does not run, the `bosun delegate --update` that carries the folded
item's title and ask there. A drop note "merged into <id>" or "folded into <id>" records
`folded_into` too. `prune` keeps a folded item while its target is open.

Tell the orchestrator "go ahead and do it" about an outreach reply or a mention and it spawns the
task with your words as `via`, accepts it and delegates it; a colleague's request ends up as a
proposed task under *Needs your approval*. Repo sessions spawn follow-ups they find and never
start them.

### External tickets

A Jira issue or GitHub issue that tracks our work is a **ticket** item (`jira:EXP-123`,
`ghissue:acme/exporter#7`), a read-only mirror: tell the orchestrator "this PR is for EXP-123" and
it runs `ledger link <pr> tracks jira:EXP-123`, which creates the ticket. The orchestrator reads
it with the tracker's read tools and records what it shows with `ledger ticket-sync <id> --status
'<status>' [--category ...] [--title ...] [--url ...] [--assignee ...]`; bosun never
transitions a ticket itself. Its state is a category (`todo`, `in-progress`, `in-review`, `done`,
`cancelled`; `unsynced` before the first read), from your overrides, the built-in status names or
`--category` (ours, or the tracker's own, such as Jira's statusCategory). Status names your
tracker uses that the built-in names miss go in `bosun.env`:

    BOSUN_TICKET_STATUS_MAP="jira:Ready for Review=in-review; jira/EXP:On Hold=todo"
    BOSUN_TICKET_STALE_HOURS="24"   # a ticket of open work read longer ago is flagged
    BOSUN_TICKET_OWNED_PROJECTS="EXP acme/exporter"   # empty: every project is yours

`ledger tickets [--all] [--mismatch]` lists the tickets of open work, how to read each, and where
a ticket and the work disagree: closed while the work is open, still open after the PR merged,
`todo` while the PR is in review, never or long ago synced. Each is a proposal under *Needs your
approval* with the exact transition, because a tracker write tool asks for permission and would
stall an unattended check-in. A ticket outside `BOSUN_TICKET_OWNED_PROJECTS` (another team's,
such as an infrastructure request in their intake state) is theirs to move: its mismatches print as
`fyi:` lines, never as proposals, and the console does not count them under *Needs you*.

### Workstreams: what an item is for

A **workstream** (`ws:<slug>`) is a time-bound effort you drive, such as "Widget search". It
groups ledger items for a weekly stand-up, stats and, later, the knowledge graph. It is not a Jira
epic (it may name one) and not a product area (it is part of one). Design, research and examples:
[docs/design/workstreams.md](docs/design/workstreams.md).

- `ledger ws add <slug> --title ... [--epic WID-100] [--channel <Slack channel id>] [--repo ...]
  [--started/--ended/--target <date>] [--area <knowledge entity>] [--parent <ws>] [--alias ...]`
  creates one or updates its fields. `ws list`, `ws show`, `ws set-state <ws> active|paused|done
  --note ...` and `ws alias` cover the rest. Any `<ws>` argument takes the id, the slug or an
  alias. A workstream can sit under one other, two levels at most.
- `ledger link <id> part_of <ws>` puts an item in its one workstream; `--move` moves it. Tag the
  root: what was spawned from it, what it produced, its outreach and what was folded into it
  inherit the workstream. Nothing is copied.
- Each item resolves in this order: its own `part_of`, then inherited, then a tracked ticket whose
  epic is the workstream's (`ticket-sync ... --parent <key>` records a ticket's epic or parent),
  then your rules in `$BOSUN_CONFIG_DIR/workstreams.json` (repo globs, title words, origin
  Slack channel, kinds; see `examples/generic/workstreams.json`) and the workstream's own channel,
  then unassigned. `ledger show <id>` says which, and how.
- `ledger assign [--dry-run|--apply] [--ws W]` turns epic and rule matches into explicit links on
  the topmost items, printing each change and why. Ambiguous cases are printed as proposals and
  never applied.
- `ledger summary --ws <ws> [--since 2026-10-19] [--markdown]` drafts a stand-up from the ledger:
  shipped, in review (and waiting on whom), waiting on others, decisions, blocked, next. `--all`
  does it per workstream, then the unassigned. The default since is this Monday.
- `ledger list --ws <ws|unassigned>` filters; `ledger lint` flags evidence that disagrees, a child
  tagged differently from its parent, redundant tags and a growing unassigned count.

### Permissions: what needs your approval

| Tier | Examples | How it is enforced |
|---|---|---|
| Do without asking | read Slack/GitHub/Confluence, update the ledger, `bosun delegate --window` (and `--update`) to a repo window already running Claude; that session then acts within its task type's rules (see Delegated tasks) | auto mode, plus an explicit allow list |
| Propose, then wait | the orchestrator's own Slack posts and reactions, `gh pr merge/review/comment/edit/close/ready/update-branch`, `gh api`, `git push`, Confluence/Jira comments, and any other MCP tool whose name has a write verb (send, create, update, delete, transition, execute, ...) | the policy, `ask` rules in `orchestrator/settings.json`, and `bosun guard`, a PreToolUse hook that asks before every MCP write tool, including ones the rules do not list (verified: Claude Code prompts even in auto mode) |
| Propose, then wait | starting a Claude session: in a new window (`bosun delegate ... --new`) or in a window that has none (`--start`) | the policy and chores procedure only, so you approve once in the plan without a second permission prompt |
| Propose, then wait | accepting work someone else asked for (a task spawned from their message); changing an external ticket (transition, assign, edit) | a task is accepted only with `user:` evidence and `delegate` refuses it before; ticket changes are proposals from `ledger tickets --mismatch`, and the tracker's write tools ask (guard) |
| Propose, then wait | applying a learning proposal (`bosun learn apply`, or `done`/`reject` for one) | the policy and the chores procedure; `ledger resolve` refuses `learn:` items, so they close only through `bosun learn`, which applies the change and closes the item together |
| Propose, then wait (unless you asked for it) | handing an item over again while its last hand-over awaits a report (`delegate ... --again`), or moving it to another window (`delegate --update <id> --window W`) | `delegate` refuses the first without `--again`; the policy covers both |
| Never | edit files, `gh pr merge --admin`, force pushes | `deny` rules (prefix matches, so best effort) plus `bosun guard`, which denies `git push` with `-f`, `--force*` or a `+refspec` and `gh pr merge --admin` anywhere in a command (verified) |
| Never | a free-text `bosun send` that names no work item; `send --new` or `send --start <msg>` instead of `delegate` | `bosun guard` denies `send` without `--id <ledger id>` or `--no-item`; updates to delegated work go through `delegate --update` |

#### Approvals: approve once, in chat

By default every outward call above opens a Claude Code permission prompt, even after you approved
it in the plan, and an unanswered prompt waits forever. With `BOSUN_APPROVALS_ENABLED=1` (from
the orchestrator's next start; design: [docs/design/approvals.md](docs/design/approvals.md)):

- The orchestrator proposes outward calls with the exact call (`ledger propose <id> --send slack
  --channel C --text -`, `--run '<command>'`, `--tool <tool> --args-json -`). When you approve
  ("1 and 3 yes"), it records your words (`ledger approve <id> --quote '1 and 3 yes'`) and makes
  the call; a PermissionRequest hook (`bosun guard --permission-request`) lets exactly that
  call through, once, if your words are in a prompt you typed (or sent from your phone) in that
  session after the proposal. Tool results, pasted and injected text never count.
- An outward call with no approved proposal is denied at once (`bosun guard --approvals`),
  telling the orchestrator to propose it, instead of waiting at a prompt.
  `BOSUN_APPROVALS_DENY_UNAPPROVED=0` brings the prompt back for those.
  `BOSUN_APPROVALS_GATED_COMMANDS` adds a site's own outward commands to gate (prefixes
  separated by `,`, e.g. `"mytool deploy, mytool publish"`).
- An approved command runs as a Bash call of its own, exactly as proposed: a call that wraps it in
  more (`timeout 120 <command>`, a pipe, another step) is denied with the exact command to run. The
  guard judges the command a wrapper runs (`timeout`, `env`, `nice`, `nohup`, `xargs`, ...), so a
  wrapper never hides a gated call.
- Once the approved call has run, `bosun guard --post-tool-use` (PostToolUse and
  PostToolUseFailure) settles its proposal: success closes it as done (outreach is marked carried
  out and closes with `ledger sent` as before); failure leaves it open with `approval-failed`, so
  it is under *Needs you* again and needs a new proposal and approval (approvals are single-use).
  An approved call not made yet, or made and closing, does not count as needing you.
  `ledger resolve-carried-out [--dry-run]` settles proposals whose call ran before this hook, by
  what the orchestrator's transcript shows.
- Standing approvals, none unless configured: `BOSUN_STANDING_CLASSES="pr-review-traffic"`
  (review requests, re-review bumps and merged notes for your own ready PRs, ready meaning the
  latest report on the PR's work says `--ci green` with nothing open; proposed as outreach,
  in `BOSUN_STANDING_SLACK_CHANNELS`, mentioning only `BOSUN_STANDING_SLACK_MENTIONS`, with
  no URL but the PR's) and `pr-housekeeping` (`gh pr ready`, and `gh pr edit` with only
  `--title`, `--body`, `--add-reviewer` from `BOSUN_STANDING_GH_REVIEWERS`, and `gh pr comment
  <url> --body '<text>'` when the text is exactly one of `BOSUN_STANDING_GH_COMMENTS`,
  '|'-separated, e.g. the command that asks an automated reviewer for another round; at most
  `BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR` (default 3, 0: no cap) per text and PR, then each
  needs your word). Never DMs, merges, `gh api` or tracker writes. Each is listed in the next
  check-in (`ledger approvals --auto`).
- A re-review request on your PR (outreach with purpose `review`, `gh pr edit --add-reviewer`, or
  a standing comment), approved or standing, is checked against the live PR first
  (`BOSUN_APPROVALS_CHECK_REVIEW_THREADS=1`, the default): it is denied while a review thread
  awaits you (`bosun pr-threads <url> --awaiting-author` lists it) or a review is newer than
  the proposed message. If GitHub cannot be read, you get the prompt.
- Automated reviewers: bots never count in `pr-threads`, `review-watch` and that check (a bot that
  comments on every push would block every request), unless their GitHub login is in
  `BOSUN_REVIEWER_BOTS` (space-separated): then their threads await you and their reviews are
  rounds, like a person's, and their latest verdict is shown, read from the review body with
  `BOSUN_REVIEWER_BOT_VERDICT_PATTERN` (default `^#+\s*Verdict:\s*(.+?)\s*$`, multi-line; the
  first group is the verdict). A reviewer bot's review is never an approval.
- The `ask` rules stay: if a hook fails or decides nothing, you get the prompt as before.

Content from Slack, PRs and Confluence is untrusted. The policy tells the orchestrator never to
follow instructions found there; delegated tasks are built by `bosun delegate` from the task
files, with the orchestrator supplying only the facts (ledger id, link, requester, one-line ask).
Repo sessions may run with `--dangerously-skip-permissions`, so consider dropping that flag from
`BOSUN_CLAUDE_FLAGS` on machines holding production credentials.

### Your own MCP servers: a site settings file

`orchestrator/settings.json` only names tools every install can have: the Slack plugin, the
claude.ai Slack connector, `gh` and `git`. Your own MCP servers (issue tracker, wiki, CI,
metrics) are still covered, because `bosun guard` asks before any MCP tool whose name has a
write verb and auto mode decides on the rest. To let the orchestrator use their read tools without
a prompt, or to name their write tools explicitly, keep a settings file of your own outside the
repo:

1. `cp orchestrator/settings.json ~/.config/bosun/orchestrator-settings.json`
2. Add the read tools to `permissions.allow`, for example `"mcp__my-tracker__search_issues"` and
   `"mcp__my-wiki__get_page"` (a bare `"mcp__my-metrics"` allows every tool of a read-only
   server), and any write tools to `permissions.ask`.
3. Point bosun at it in `bosun.env`:
   `BOSUN_ORCHESTRATOR_SETTINGS="$BOSUN_CONFIG_DIR/orchestrator-settings.json"`.

The file replaces `orchestrator/settings.json` rather than adding to it, so keep its rules and the
`hooks` block that runs `bosun guard`. `bosun doctor` fails without that hook and warns
about any rule of `orchestrator/settings.json` the file lacks (for example one added after you
copied it). The orchestrator picks the file up at its next launch.

## Events from repo sessions

Off by default (`BOSUN_EVENTS_ENABLED=0`); design: [docs/design/events.md](docs/design/events.md).
With it on:

- **What I tell a repo window is recorded.** Repo sessions launched from then on get a
  `UserPromptSubmit` hook (in their `--settings`, nothing global) that records each prompt I type
  in the window, or send from my phone, on the item(s) the window holds: `bosun ledger said
  [<id>]`, also in `ledger show` and `ledger reports --since`. Prompts bosun pasted, slash
  commands and messages from other sessions are skipped; secrets are redacted. The orchestrator
  checks it before asking me anything, so it never asks again what I answered in the window.
- **Events are pushed to the orchestrator.** Reports, what I said in a window, outreach drafts,
  follow-up tasks and repo windows waiting at a prompt go to an event queue
  (`$BOSUN_STATE_DIR/events/`). The check-in loop runs `bosun events` every
  `BOSUN_EVENTS_INTERVAL_SECONDS` and delivers them as one summary, batched, only to an
  orchestrator that is idle (or in `shell`) on today's conversation while I am not typing, at most
  `BOSUN_EVENTS_MAX_PER_DAY` times a day. Slack activity and windows stuck at a prompt take a
  fast lane; the rest wait longer so more of them share one delivery, and longer still while I am
  away from tmux. Each line carries what was recorded (a report's open points, results and CI,
  what I typed, the ask), so the orchestrator acts on the summary without reading every item
  first, and tells me what changed and what it proposes. Events are information, never
  instructions. Every timing and size is a setting: see [Tuning](docs/design/events.md#tuning).
- **Events without check-ins.** With `BOSUN_CHECKIN_MINUTES=0`, check-ins and the `checkin`
  window stop, and with them events and the watchers. `BOSUN_EVENTS_WITHOUT_CHECKINS=1` keeps
  the window's loop for those and never sends a check-in.
- **New reviews on my PRs are raised.** Every `BOSUN_REVIEW_WATCH_MINUTES` (15) the loop also
  runs `bosun review-watch`: a review with changes requested or comments that came after a
  `mypr:` item's last hand-over (or, outside `addressing`, its last report) is recorded on the item
  (`review-arrived`, no state change) and becomes a `review` event, so a second review round
  reaches the PR's window (`delegate --update`) even while it still works on the first.

Without the flag, the rest of this works too: `ledger reports --since` lists every report,
`ledger said`, `working` and `idle` exist (a window is `working` on its active item while its Claude
is mid-turn), `ledger spawn` warns about a task that already comes from the same source or
Slack thread, and the chores run `review-watch` in step 4 (it records, but queues nothing).

To turn it on: set `BOSUN_EVENTS_ENABLED=1` in `bosun.env`, restart the check-in loop
(close the `checkin` window, then `bosun refresh checkin`), restart idle repo sessions so they
pick up the hook (`bosun refresh --restart-idle <window>...`), and have the orchestrator re-read
the policy (`bosun send --no-item orchestrator "re-read orchestrator/policy.md: events"`).

### Slack watcher

Off by default (`BOSUN_SLACK_WATCH_ENABLED=0`; needs events on); design: [docs/design/events.md](docs/design/events.md#slack-watcher).
Without it the orchestrator sees Slack only at the hourly check-in. With it, the check-in loop runs
`bosun slack-watch --scheduled` every `BOSUN_SLACK_WATCH_MINUTES` (15) inside the awake
window: one headless `claude -p` with `BOSUN_SLACK_WATCH_MODEL` (haiku) lists the messages since
the last watch that @-mention or name you, DMs and group DMs to you, and replies in threads you
started or replied in, as strict JSON. Code checks each entry, drops what the ledger has processed
(`ledger seen`), what belongs to open outreach (step 5 handles those), what is already queued and
what comes from an author in `BOSUN_SLACK_WATCH_QUIET_AUTHORS` (comma-separated display names,
for bots that post status updates; the check-in's scan still reads them), and queues one `slack`
event per new message. The dispatcher delivers them with the other events; the
orchestrator runs chores step 1 for exactly those permalinks, reading each message itself.

- **Read-only, enforced by Claude Code, not the prompt:** no built-in tools (`--tools ""`), only the
  Slack MCP server (`--strict-mcp-config` with the Slack plugin's server, or the one in
  `BOSUN_SLACK_WATCH_MCP_CONFIG`), no settings files (`--setting-sources ""`: no allow rules or
  hooks of yours), Slack's send, schedule, draft, reaction, canvas and conversation tools removed
  (`--disallowedTools`), and in `dontAsk` mode every tool not on the read list is denied without a
  prompt. A call whose tool list shows a write tool is refused. The watcher never posts and never
  writes the ledger.
- **Untrusted text stays out:** an event carries the permalink, the kind, the author and a redacted
  excerpt of at most 120 characters, labelled untrusted.
- **Cost:** at most `BOSUN_SLACK_WATCH_MAX_USD_PER_CALL` (0.25) a call and
  `BOSUN_SLACK_WATCH_MAX_USD_PER_DAY` (1.5) a day; a quiet call costs a few cents.
- `--dry-run` calls Claude and prints what it would queue (and the tools the call could see); it
  queues nothing and writes no state. `--status` shows the last run, the spend and failures; the
  watchdog's `slack-watch` check alerts when it stalls, fails three times in a row or spends its
  budget. A failure's log line names the real error (for example a usage limit), and a failed run
  keeps its starting point, so the next one rescans the gap (the check-ins scan Slack meanwhile).

To turn it on (events must be on): set `BOSUN_SLACK_WATCH_ENABLED=1` in `bosun.env`,
check it with `bosun slack-watch --dry-run`, restart the check-in loop once so it runs code that
knows the watcher (close the `checkin` window, then `bosun refresh checkin`), and have the
orchestrator re-read the policy (`bosun send --no-item orchestrator "re-read
orchestrator/policy.md: Slack events"`).

## Watchdog

`bosun watchdog` looks for problems that need you and alerts you about each one once. Its own
launchd agent (`<BOSUN_LAUNCHD_LABEL>.watchdog`, installed by `install.sh`) runs it every 5
minutes, outside the tmux session, so it also notices when the session itself, or the morning run,
is missing.

| Check | Fires when | Alerts once per |
|---|---|---|
| `morning-missing` | a scheduled day, `BOSUN_WATCHDOG_GRACE_MINUTES` (15) after the run was due (start time + wake delay), and today's morning log has no real (non-dry) run | day |
| `morning-failed` | today's run reported problems (a setup step such as an AWS SSO login failed, the network, a window that could not start, an `ERROR`), crashed (launchd's exit code), or is not done after 45 minutes | day |
| `session-missing` | after today's morning run, no tmux session, unless you ran `bosun stop` today | day |
| `checkin-missing` | the session runs, but the `checkin` window is gone or not running the check-in loop | day |
| `orchestrator-down` | after the morning run, the orchestrator's window is gone or no Claude runs in it for `BOSUN_WATCHDOG_ORCHESTRATOR_DOWN_MINUTES` (15) awake minutes (0 turns it off): events, check-ins and reports wait meanwhile. With `BOSUN_WATCHDOG_ORCHESTRATOR_RESTART=1` (the default) a full run relaunches it once per outage with `bosun refresh --unless-active <orchestrator>`, which never replaces a running command or acts while you type | outage |
| `waiting` | any Claude window (orchestrator or repo session) has waited `BOSUN_ALERT_WAITING_MINUTES` (30) at a permission prompt or question | prompt (pid and status time) |
| `log-errors` | new `ERROR`/`FAIL` lines since the last scan in the logs (`*.log`, `*.err`) other than the morning and watchdog ones, so mainly the check-in logs and `launchd.err`, grouped into one alert (the morning log belongs to the morning checks; lines from `alert` and `watchdog` are left out, or they would alert in a loop, and from `learn`, which has its own check) | batch of lines |
| `ledger-stuck` | a ledger item has been `delegated` to a repo session for `BOSUN_WATCHDOG_DELEGATED_HOURS` (8) awake hours; nights and weekends do not count (0 turns it off). The clock restarts at each hand-over to the window (`delegate`, `delegate --update`, a move) and each report. Items that wait on someone else are left to the nudges: open outreach, or a reported task whose open PRs all wait on their reviewers or their merge (`awaiting-review`, `approved`) | item and delegation |
| `long-shell` | a background command has kept a Claude window (orchestrator or repo session) in `shell` (its turn over, the command still running) for `BOSUN_LONG_SHELL_HOURS` (4) awake hours: check it is still wanted; meanwhile `refresh` does not restart that Claude. Turns in between (a check-in, an event) do not end it; any other status does (0 turns it off) | episode (pid and since when) |
| `learn` | with `BOSUN_LEARN_ENABLED=1`: no learning run an hour after `BOSUN_LEARN_AT` on a scheduled day, or the run failed, crashed (ended without an outcome) or runs past `BOSUN_LEARN_TIMEOUT_MINUTES` (30) | day (missing), run |
| `events` | with `BOSUN_EVENTS_ENABLED=1`: the check-in loop stopped dispatching events (for 10 minutes, while it runs), or a delivery to the orchestrator failed | stall, day (failure) |
| `slack-watch` | with `BOSUN_SLACK_WATCH_ENABLED=1` and events on: no Slack watch started for two intervals inside the awake window (while the check-in loop runs), three failed runs in a row, or the daily budget spent | stall, failure streak, day (budget) |
| `spend-limit` | a Claude window, or one of its subagents in the last 12 hours, stopped on a usage limit (an individual spend limit, or a plan's usage limit): names the window and the ledger items it holds. While it is stopped, a full run makes the login check request at most every 30 minutes; once that works (the limit was raised or reset), or the window's latest reply is a normal one again, the window gets a resume prompt once. A restarted Claude gets the drain prompt instead | stop (window, pid and since when) |
| `context-size` | a Claude window's conversation has re-read `BOSUN_CONTEXT_WARN_TOKENS` (300000) tokens or more a turn for a day: every check-in, notification and resume there re-reads it all (`0` turns it off) | conversation (window and session) |
| `overhead-share` | the turns bosun started itself (check-ins, events summaries, notifications, resumes, messages between sessions, a site's scheduled prompts) cost more than `BOSUN_OVERHEAD_WARN_SHARE` (0.5) of the last 7 days, from the Impact snapshot (skipped without one, or when it is over 3 days old; `0` turns it off) | week |
| `login-expired` | Claude Code is logged out (`claude auth status`), the last login check request got a login error, or any Claude window's latest replies are all login errors (see Claude Code login); alerts at once, but like every check only inside the awake window | outage (alerts again after it recovered) |

How it avoids false alarms:

- **Only in the awake window:** alerts go out on scheduled days (`BOSUN_DAYS`) between
  `BOSUN_START_TIME` and `BOSUN_AWAKE_UNTIL` (an end at or before the start runs past
  midnight: 08:00 to 01:00 ends at 1 am the next day). Anything found outside it is held: a prompt
  left open overnight alerts when the window opens, and log lines are not consumed until then.
- **Confirmed first:** a missing morning run, session or check-in loop, or an unfinished morning
  run, must be seen by two runs in a row (5 minutes apart). After a wake the morning job may start a
  few seconds after the watchdog, and a window may be recreated just as it looks; a long gap
  between runs (the Mac slept) starts the observation over.
- **One message per run:** what a run finds goes out as one alert, the most severe first (at most
  five items, then "and N more"). While something is being confirmed, ready alerts wait one run
  so they go out together.
- **History is not news:** the first run only records where each log ends; it tracks offset and
  inode per file, so a replaced or new file is read from its start.
- **De-duplication** in `$BOSUN_STATE_DIR/watchdog.json`: the key of every alert sent (kept
  14 days), what is being confirmed, and the log offsets. Keys are recorded before sending, so a
  failing Slack DM is not retried every 5 minutes; the notification and the log still have it.

`--dry-run` prints what a run would alert and changes no state; `--status` lists every check and
its current result (also part of `bosun status`); `--list` prints the checks. Alerts go
through `bosun alert` (macOS notification, plus a Slack DM to yourself if
`BOSUN_ALERT_SLACK_USER` holds your Slack member id: Slack profile > More > Copy member ID).
The DM goes through a one-shot `claude -p` that may use only ToolSearch and Slack's send-message
tool, in `dontAsk` mode with `--permission-prompts none`, so it cannot itself get stuck at a
prompt. Another destination is one more sink in `lib/watchdog.py` (`SINKS`).

The check-in loop runs the `waiting` check itself (`bosun watchdog --only waiting`, same state)
until the watchdog agent is installed, then leaves it to the agent. `bosun doctor` shows
whether the agent is loaded and when it last ran.

## Learning

`bosun learn` looks back over recent work and proposes what should change, so the same
mishaps do not happen twice. Design and phasing: [docs/design/learning.md](docs/design/learning.md).

1. **Digest** (no Claude, free): for every bosun window (`bosun sessions`), including the
   orchestrator's daily conversations, it reads the window's Claude Code transcripts in the time
   window and keeps what tells a story: who sent each prompt (you or bosun, and whether it
   arrived while the session was busy), permission prompts and how long they waited, refused and
   failed tool calls, guard denials, questions to you, the `bosun` commands run and the end of
   each turn. Then the ledger's history, the bosun logs (repeated lines collapsed), the
   watchdog's alerts, deterministic ledger checks (overlapping outreach, `info-ack` outreach still
   waiting for a confirmation, which maybe should be `fyi`, sensitive subjects addressed to a
   channel, an item handed to two windows; and lineage: a PR a report names with no item or link,
   outreach closed after a delegate proposal with no task, tasks proposed for days, tickets out of
   step with their work), the instructions and memory in force, and what
   earlier runs found. Tokens, keys, passwords and email addresses are redacted; other sessions in
   the same folders are not read, and `BOSUN_LEARN_EXCLUDE_SESSIONS` lists session ids it must
   never read.
2. **Analysis**: one headless Claude with **no tools and no MCP servers**, its prompt from
   `orchestrator/learn.md` (plus `$BOSUN_CONFIG_DIR/learn.md`), returns findings (stall,
   mishap, duplicate, repeated correction, gap, prompt gap, setup, outreach, memory, ledger
   hygiene), each with evidence quoted from the digest. Findings whose quotes are not in the
   digest are dropped.
3. **Proposals**, never applied on their own: each new finding with a change (at most
   `BOSUN_LEARN_MAX_PROPOSALS` a run) becomes a `learn:<YYYY-MM-DD>/<n>` ledger item whose
   pending proposal is the exact change, so the orchestrator lists it under *Needs your approval*.
   On your yes it runs `bosun learn apply <id>`: a memory note for a window's folder, or a rule
   appended to `$BOSUN_CONFIG_DIR/policy.md` or `tasks/_common.md` (the previous file is kept
   in `learn/backups/`). Changes to this repo, `sessions.yml` or outreach you make yourself, then
   `learn done <id>`; `learn reject <id>` declines one for good. Findings are remembered by key, so a
   later run counts a known problem again instead of proposing it twice.

It runs by hand any time (`--dry-run` writes nothing but its report; `--collect-only` only the
digest). With `BOSUN_LEARN_ENABLED=1`, `install.sh` loads a launchd agent that runs it daily at
`BOSUN_LEARN_AT` on `BOSUN_DAYS` (window: since the last run, at most `BOSUN_LEARN_HOURS`),
and the watchdog's `learn` check alerts when that run is missing, failed, crashed or runs past
`BOSUN_LEARN_TIMEOUT_MINUTES`. Model, effort and a per-run budget: `BOSUN_LEARN_MODEL`,
`BOSUN_LEARN_EFFORT`, `BOSUN_LEARN_MAX_BUDGET_USD` (a run estimated over it is not
started). The digest is capped at `BOSUN_LEARN_MAX_DIGEST_KB` (400); over it, the least telling
parts are left out, and the digest says which.

## Knowledge

`bosun kg` keeps what bosun should know about your organization: people (work facts
only), processes and the product, as atomic facts with sources, so a fact stated once is not lost
when the conversation ends (design: [docs/design/knowledge.md](docs/design/knowledge.md)). It
lives in `$BOSUN_CONFIG_DIR/knowledge/`, never in this repo, with its own git history (one
commit per change; `BOSUN_KNOWLEDGE_GIT=0` turns it off, and it is skipped if git is missing).

```sh
bosun kg add-entity process/release --name "Release process" --alias releases
bosun kg add-entity gate/canary-checks --name "Canary checks"
bosun kg propose releases has_step canary-checks --order 3 --source ledger:mypr:acme/api#7 --source report:task:2026-10-01/1
bosun kg propose releases rule --value "a deploy rolls out to production gradually once the canary checks pass" \
  --from-user --quote "it rolls out gradually once the canary checks pass" --transcript <session.jsonl>
bosun kg batch                          # the day's proposals, grouped and numbered
bosun kg batch apply 'confirm all except 2b'
bosun kg context --for releases         # a small cited pack of confirmed facts
```

- **Entities** are `<type>/<slug>` on three axes: people (`person`, `team`), process (`process`,
  `step`, `gate`, `channel`, `system`) and product (`repo`, `service`, `environment`, `feature`,
  `decision`, `principle`, `lesson`). A name or strong alias works anywhere an id does; weak
  aliases are related terms for search. Near-identical names are reported as merge suggestions.
- **Facts** use a closed set of predicates (`bosun kg schema`); a site adds its own in
  `knowledge/schema.json`. About a person only work facts are kept (role, team, title, timezone,
  stated working hours, preferred channels, what they review, own or approve, stated work
  preferences); anything else is refused, as is anything inferred about a person.
- **Confirmation without the spam:** a fact is confirmed at once when the quote of your words is
  in a prompt you typed in that session's transcript (tool results and pasted text never count),
  when two trusted sources of different kinds back a low-risk fact that names no person, or when a
  profile source gives a title or timezone. Slack, PR, ticket and document text never confirms
  anything. The rest waits for `kg batch`: one list, grouped by entity and axis, rework-preventing
  groups first, at most 10 groups, 8 facts a group and 40 facts a day
  (`BOSUN_KNOWLEDGE_BATCH_CAP`, `_GROUP_FACTS`, `_BATCH_FACTS`), answered in one line;
  "confirm all except 2b" leaves 2b proposed, only "reject 2b" rejects it.
  Unreviewed proposals expire after 30 days unless something observes them again.
- **Two timelines:** facts are never edited or deleted when they change: `kg supersede` closes
  one (`valid_to`, `superseded_by`), and `kg facts <entity> --as-of <date>` shows what held then.
- **Maintenance:** `kg lint` reports contradictions, stale facts (`BOSUN_KNOWLEDGE_STALE_DAYS`),
  orphans, alias collisions and expired proposals; person facts not re-observed for 12 months
  are deleted by `kg expire`, and `kg forget person/<slug>` deletes a person on request (with the
  git history, which restarts).
- **Seeding:** `kg import <file>` reads entities and facts as JSON lines, records every fact as a
  proposal (`--auto-confirm` applies the rules above), is a dry run unless `--apply`, and says how
  many daily batches the review takes; `tools/survey_to_kg.py` converts a survey's markdown tables, with a mapping file kept
  next to the survey, into such a file. `bosun doctor` shows the counts.

## Console

`bosun console` serves a local web page over the ledger, the sessions, bosun's own health
and the knowledge base, read-only except for knowledge review decisions (design: [docs/design/console.md](docs/design/console.md)). It runs in the
foreground until Ctrl-C and prints a URL such as `http://127.0.0.1:8765/?t=<token>`; open it (or
pass `--open`). The top bar shows how many things need you on every view; the page follows the system's
light or dark mode and works down to a narrow window.

- **Ledger** (`#/ledger`): every item with its kind, state, window, what it waits on and when it
  changed, filtered by kind, group, window, person (requester or outreach recipient) and update
  time, searched over ids, titles, fields, notes and reports, and sorted by update, creation or
  kind. The filters live in the URL, so a view can be bookmarked. Groups: *needs me* (a pending
  proposal, outreach whose message awaits approval, a proposed task, a ticket that disagrees
  with our work), *waiting on someone* (my review or PR with the other side, outreach sent, open
  outreach or blockers), *open* and *closed*; the default shows every open item, as `ledger list
  --open` does.
- **Needs you**, above the list: windows waiting at a prompt, each pending proposal with its
  exact action, proposed outreach, and the items a ticket mismatch asks you to decide on (one
  entry per item, as the *needs me* group counts them, with the tickets behind it).
- **Item page** (`#/item/<id>`): fields, delegation, latest report, outreach (to whom, what went
  out, nudges, reply, how long it waited), the pending proposal, what you said about it in a repo
  window, the lineage tree and links (duplicates, folded into, blocked by, tracks, and their
  inverses) that go to other items, every web link in the item labelled GitHub, Jira,
  Confluence, Slack or Web, and the history. Only `http(s)` links are clickable; they open in a
  new tab.
- **Sessions** (`#/sessions`): a card per window (`bosun sessions --json`): its kind, folder,
  model and effort, its Claude's status (idle, busy, shell, waiting) and for how long, the items it
  holds, its active item and whether it works on it now, its last report, its login verdict, and
  the `tmux attach` command with a copy button. Below, **Health**: the watchdog's checks (as
  `watchdog --status` shows them; nothing is alerted or recorded), today's morning run and the
  last chores runs, the events dispatcher (deliveries today against `BOSUN_EVENTS_MAX_PER_DAY`,
  events pending), the Slack watcher (last run, spend today against its daily cap, failures), the
  login (the last login check, windows answering only with login errors) and the feature flags
  (events, Slack watcher, learning: on or off). It never starts Claude, so whether the stored
  login works right now is left to `bosun status`. The view reloads every 15 s while visible.
- **Stats** (`#/stats?days=7|30|90`): from the ledger's history, items opened and closed per day
  by kind, my PRs merged and the median time to merge, review rounds done and their turnaround,
  tasks spawned, accepted and done, hand-overs per window and the median time to the report,
  outreach sent, replied, unanswered and waiting with the median reply time and nudges per reply,
  the people reached out to and worked for (top 10; names stay local), and bosun itself: the
  last chores runs, event deliveries, Slack-watch and learning runs and their spend per day. Each
  metric is defined in the design note. Charts are inline SVG, each with a text summary and its
  numbers in a data table.
- **Improvements** (`#/learning`): learning's runs, live and dry (when, outcome, findings, proposals,
  cost) with a link to each run's report, rendered in the page; every `learn:` proposal with its
  finding, evidence, reason, the exact change and where it goes, and when and how it was applied
  or rejected; and the findings learning remembers, with how often each was seen.
- **Knowledge Graph** (`#/knowledge`): the knowledge base (`bosun kg`) in three tabs. The
  **Briefing** (the home): facts captured per day, the confirmed share, coverage per card, what
  changed this week (before -> after) and the new facts, today's review as a card stack (Y/N/S
  keys or drag, Z undoes), and the most connected entities. The **Constellation**
  (`?view=constellation`): the whole knowledge base as a force-laid graph with hover, a facts
  panel, search, card chips and an "as of" slider (at most 400 entities). **Raw data**
  (`?view=raw`): four cards, People, Process, Product and Systems, with entity counts, confirmed
  and proposed facts and what changed last, and a search over names, aliases and fact text with a
  type filter. A disabled Metro tab marks the map still on the roadmap.
  An entity's page (`#/knowledge/<key>`) shows its current facts by predicate with who stands
  behind each (user, stakeholder, observed, inferred), its status, dates and sources (web links
  and ledger items link; a memory note links to the Config view when
  `BOSUN_CONSOLE_MEMORY_DIRS` lists its folder; a session shows its name; anything else is
  text), the facts that no longer hold (greyed, with what
  replaced them), the facts that point to it, related ledger items (a person as requester or
  outreach recipient, a repo by its items), a process's steps as an ordered flow (actor, gates,
  entry points, systems), and a small graph of what lies within two links. A person's page shows
  only work facts and how to have them deleted (`bosun kg forget`); restricted identifiers are
  never shown. The review (`#/knowledge/review`) is the batch `kg batch` uses, lettered the same
  way: confirm or reject a group or a fact, skip a fact, or confirm a group except the facts you
  tick. Decisions are recorded as yours, from the console, with the time, through the same code
  as `kg batch apply`; once every fact of the batch `kg batch` printed is decided, it is marked
  answered there too. The view reloads when the knowledge base changes (the review only while
  nothing is ticked). Systems groups the system, service and environment types. Without a
  knowledge base the view says how to start one.
- **Config** (`#/library`): the files that steer bosun, read-only: the repo's policy, chores,
  task types and prompts, and your site overlay from `$BOSUN_CONFIG_DIR` (policy, task types,
  `sessions.yml`, the orchestrator settings, `bosun.env`). Each task type says whether it is
  the one `bosun delegate` uses or a site file replaces it. Config values whose names contain
  TOKEN, SECRET, KEY or PASSWORD, and webhook URLs, are masked; site files are also redacted like
  learning's digest. Markdown is rendered (its front matter as a table). Claude Code memory notes
  appear only if you list their folders in `BOSUN_CONSOLE_MEMORY_DIRS` (colon-separated).
- The open view refreshes when the ledger changes (checked every 15 s).

- It listens on `127.0.0.1` only, on `BOSUN_CONSOLE_PORT` (8765) or `--port N`; `--port 0`
  picks a free port. A port in use is an error, not a silent fallback.
- The token is new on every start. The first visit swaps it for an HttpOnly, SameSite=Strict
  cookie and drops it from the address bar; the API answers 401 without the cookie, so other
  local processes and pages cannot read the ledger by finding the port. Restarting the console
  signs the browser out: open the new URL.
- Read-only: GET and HEAD only, a strict Content-Security-Policy, and only its own files are
  served. The one exception is a knowledge review decision (`POST /api/knowledge/review`): local
  data, never outward-facing, and only with the cookie, this page's Origin, a JSON body and a
  per-start CSRF token that only the review page can read. Config files, learning reports and
  knowledge entities only by an opaque key from a list it builds itself
  (no path from a request ever reaches the filesystem). It reads `ledger.json` as a snapshot (never its lock), re-read when the file changes.
  It reads the windows the way `bosun sessions --json` and the watchdog do (list them, read
  Claude Code's session records), at most every 10 s; it never sends keys to a pane, never reads
  a pane's text and never starts Claude.
- It logs only refused requests (to stderr), never ledger content or item ids.

### Impact

The console's **Impact** view (`#/impact`) asks what bosun changed, not what it did (design:
[docs/design/impact.md](docs/design/impact.md)). It compares weeks since the start
(`BOSUN_IMPACT_START`, else the ledger's first item) with my own baseline before it
(`BOSUN_IMPACT_BASELINE_FROM`, else 13 weeks earlier), shown as a band: PRs merged per week and
time to merge, reviews given per week and time to my first review, agent busy hours against my
touch time (leverage, week by week and before the start against since where transcripts from before
exist), what learning has produced (runs, findings that came back, proposals, the knowledge base's
growth), cost per week and per merged PR, and where agent time went per window, with the PRs behind
each week as links. Estimates (my touch time: `BOSUN_IMPACT_PROMPT_MINUTES` per
prompt I typed; cost at list prices) carry an "estimate" badge and a range, every number says how
many weeks it rests on, and fewer than four weeks since the start is marked early data. Other people
are never named. The view reads the snapshot `bosun impact collect` writes to
`$BOSUN_STATE_DIR/impact/` (GitHub reads only, through `gh`, over `BOSUN_IMPACT_REPOS` or the
ledger's repos; transcripts through the same reader as the rest of bosun, cached per file); run it
by hand, or daily from the morning run with `BOSUN_IMPACT_COLLECT=1`.

Its **Overhead** panel shows what bosun itself costs. Every turn's tokens count against what
started it: my requests, tasks handed to a window and subagents are work; check-ins, events
summaries, background-task notifications, resume prompts, messages between sessions, prompts Claude
Code sends on a schedule (a session's own cron or wake-up) and the prompt openings in
`BOSUN_IMPACT_OVERHEAD_PREFIXES` (a site's own scheduled prompts, `|`-separated) are overhead. The panel shows the overhead share per week, the cost per check-in, notification and
resume, and the context each window's turns re-read (a long conversation makes every turn there
expensive). Guardrails beside it (tasks reported and done, hand-over to report time, approval
failures, outreach answered, events delivered and waiting, check-ins sent and skipped) show the
work did not get worse when the cost fell; they, the context per window and cost per turn are on
**Bosun internals** (`#/impact/internals`, linked from the page). `BOSUN_IMPACT_OVERHEAD_FIXED_AT` (a date) adds a
before/after table there for the week on each side. The watchdog's `context-size` and `overhead-share`
checks warn when either grows.

Its **Cost per task** panel shows what each ledger item cost, its subagents included: by outcome
(done, merged, dropped, open), task type and kind, and the most expensive items next to the PR they
produced. A turn counts for the item its hand-over names, else the item the window reported on
next, else the window's active item, as long as that window's last ledger event is within
`BOSUN_IMPACT_ATTRIBUTION_HOURS` (8); the rest stays unattributed per window (the
orchestrator's coordination, work outside the ledger). `BOSUN_IMPACT_TASKS_TOP` (15) sets how
many items it lists. A model missing from the price table is priced as Opus and named on the panel.

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

## Known limits

See also [ROADMAP.md](ROADMAP.md) for planned work and open questions.

- Relies on Claude Code internals that are not a public API, all read and written in
  `lib/claude_state.py`: transcript `custom-title` entries in `~/.claude/projects/` (name
  resolution), `~/.claude/sessions/<pid>.json` (`status`, `version`, `statusUpdatedAt`), folder
  trust in `~/.claude.json`, and the error fields of failed requests and `claude auth status
  --json` (login detection, verified on 2.1.304). Verified on Claude Code 2.1.296 to 2.1.300. If a Claude Code update changes
  them, `tests/smoke.sh` still passes (it uses fixtures), so check with
  `bosun refresh --dry-run` and `bosun doctor` after major updates.
- Whether Remote Control reconnects after a long sleep without a restart is not verified. Sessions
  that `refresh` restarts get a fresh connection.
- Static AWS credential variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_SESSION_TOKEN`), for example from a local cloud emulator or test credentials, override
  SSO profiles: tools and SDKs that read the default credentials then fail as if the SSO session
  had expired, while `aws --profile X` still works. tmux windows inherit the environment of
  whatever started the tmux server, which is why bosun runs on its own server
  (`BOSUN_TMUX_SOCKET`, default `bosun`). `bosun doctor` warns when the session's tmux
  environment has them, and delegated tasks include the workaround.
- The Mac must be logged in (asleep is fine; logged out is not), on AC and with the lid open.
- When the Identity Center session itself expires, `ensure_aws_sso` runs `aws sso login`, which
  opens the browser, and posts a macOS notification. It logs in once per `[sso-session]` (`aws sso
  login --sso-session <name>`), which covers every profile of that session; the session's other
  profiles only check their credentials, and if the login failed they fail at once instead of
  opening the browser again. If the browser is still signed in to the AWS access portal, the login
  can complete on its own: on the first real run it finished in 20 seconds with nobody at the Mac.
  Otherwise it needs a click: a login still waiting after `BOSUN_AWS_SSO_ALERT_AFTER` seconds
  (60) alerts you (`bosun alert`: a notification and, with `BOSUN_ALERT_SLACK_USER`, a Slack
  DM) with the time it gives up, so you can approve it at the Mac. The step times out after
  `BOSUN_LOGIN_TIMEOUT` seconds, its failure names the command to run later, and the rest of
  the morning run continues. The login's authorization URL is masked in the log.
- Orchestrator cost: a morning chores rehearsal took about 4.5 minutes and $1.60 (30 turns).
  Check-ins only process what is new since the last run. `bosun chores --dry-run` prints the
  cost of a run.
- Every delegated task starts work in a repo window. `bosun send --new` and `--start` each
  start a Claude session, so the orchestrator proposes them for approval rather than running them
  itself. A session you have open in your own terminal cannot be started in tmux at the same time;
  `--start` then fails with "already running", and the orchestrator tells you rather than opening
  another window.
