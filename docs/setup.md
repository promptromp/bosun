# Setup

Requirements, installation and configuration, and the morning's logins.

## Requirements

- macOS on Apple silicon or Intel, an **admin account** (for the one-time `sudo pmset`)
- Charger connected and **lid open** (or an external display) overnight
- `brew install tmux jq python` (`python3` must be 3.13 or newer; the macOS system one is 3.9), plus
  `ruby` (the macOS system one is fine)
- For the orchestrator: [GitHub CLI](https://cli.github.com) (`gh`), logged in (`gh auth login`)
- [Claude Code](https://claude.com/claude-code), logged in

## Install

```bash
git clone https://github.com/promptromp/bosun.git ~/Development/bosun
cd ~/Development/bosun
bin/bosun init                 # prints the plan and changes nothing
bin/bosun init --recommended   # carries it out
```

`bosun init` reads what this Mac already shows: the repos you use Claude Code in, your usual hours,
your logins. It recommends a setup from that, says why, and lists every file and command before
it touches anything. Then it writes the config, trusts the folders, and runs `install.sh`.

To change an answer, edit them and pass them back. A file only needs what it changes:

```bash
bin/bosun init --print-answers > answers.json
$EDITOR answers.json           # repos, days, start, until, events, approvals, learning, ...
bin/bosun init --answers answers.json
```

Run it again whenever you like: it starts from your config, keeps anything you added to `bosun.env`,
keeps a backup of what it changes, and never changes your `setup.sh` once it exists. How it decides,
and what it never does: [design note](design/init.md).

### By hand

If you prefer to write the config yourself:

```bash
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

Fresh conversations only start at a refresh. In between, every window is launched with
`claude --autocompact BOSUN_AUTOCOMPACT_TOKENS` (300000; the orchestrator:
`BOSUN_ORCHESTRATOR_AUTOCOMPACT_TOKENS`; a sessions.yml entry can set its own `autocompact`;
empty or 0 leaves Claude Code's default, `auto` is accepted): once a conversation passes that size,
Claude Code compacts it by itself, mid-task, into a summary, so no window re-reads 300k-900k tokens
a turn for the rest of the day. Claude Code's own idle compaction (200k+ tokens idle about an hour)
still applies. A summary can drop which ledger items the window holds, so with
`BOSUN_COMPACT_CONTEXT=1` launch also installs a SessionStart hook (matcher `compact`,
`bosun hook compact-context`) that adds them back, in at most
`BOSUN_COMPACT_CONTEXT_MAX_CHARS` (1500) characters, from `bosun ledger compact-context`:
for a repo window its items (in flight first) and how to report; for the orchestrator the proposals
waiting for my approval and the open items per window. Both are read at launch, so a running window
picks them up at its next restart. The fresh conversation stays as the backstop: a window still over
`BOSUN_FRESH_CONTEXT_TOKENS` at the morning refresh starts a new one.

Status and version come from Claude Code's own `~/.claude/sessions/<pid>.json`. The status is
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
