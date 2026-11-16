# How a day runs

[A day with bosun](../README.md#a-day-with-bosun) is the short version. This is what runs, and when.
The times are the defaults; [Setup](setup.md#configuration) says how to change them.

## Morning

Every morning bosun:

1. **Wakes the Mac** on a schedule (`pmset`) and **keeps it awake** until evening (`caffeinate`).
2. **Refreshes credentials**, for example AWS SSO, skipping any that are still valid.
3. **Starts a tmux session** with one window per agent session (Claude Code, or Codex if you chose it).
   Each window resumes a named session, on Claude Code with **Remote Control** enabled so you can drive
   it from your phone, and sends a **drain prompt** that finishes any work cut off by sleep or usage limits.
   On later days the session is usually still there. It is then **refreshed** window by window:
   idle sessions on an outdated agent, or with a changed model or effort, are restarted, and the
   drain prompt goes only to windows with work in flight. Busy ones are left alone. See
   [Steady state](#steady-state-the-session-already-exists).
4. Runs an **orchestrator** session that does the daily chores: Slack mentions (PR and tech-design
   review requests, other asks) and your own open PRs (reviewed? comments to address? needs a
   nudge? ready to merge?). It hands tasks to the repo sessions on its own, each with rules for what
   that kind of task may do (post a PR review, push a draft PR). Anything else other people would
   see waits for your approval, which you can give from your phone. A **ledger**
   with stable ids and state machines makes this idempotent across runs and days.

If bosun is not running when you log in (after a restart, say), it starts then, on one of
your days and inside your hours, unless you stopped it earlier that day (`BOSUN_START_AT_LOGIN`, on for new installs; see
[Starting at login](setup.md#starting-at-login)). With the [menu bar app](setup.md#the-menu-bar-app), its icon
says all along whether bosun is well, and Start bosun runs the morning at any hour.

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

## During the day

- **Check-ins.** The `checkin` window asks the orchestrator for `/bosun:chores check-in` every 60 minutes
  (`BOSUN_CHECKIN_MINUTES`), when it is idle and something is new since the last one, or after 4 quiet
  hours (`BOSUN_CHECKIN_MAX_QUIET_HOURS`). See [Orchestrator](orchestrator.md).
- **Events.** With `BOSUN_EVENTS_ENABLED=1`, repo sessions' reports and, with the Slack watcher on, new
  mentions and replies reach the orchestrator as they happen instead of at the next check-in. See
  [Events](events.md).
- **The watchdog** checks every 5 minutes that the windows, the check-in loop and the launchd jobs are
  alive, and alerts you when something is down or a window has waited at a question too long. See
  [Watchdog](watchdog.md).
- **Approvals.** Whatever would go out under your name waits for your one-line approval. See
  [Permissions](permissions.md).

## Evening

- **18:30, learning** (with `BOSUN_LEARN_ENABLED=1`, `BOSUN_LEARN_AT`): a headless Claude reads the day's
  sessions, the ledger and the logs and proposes fixes for your approval. See [Learning](learning.md).
- **20:00** (`BOSUN_AWAKE_UNTIL`): bosun stops keeping the Mac awake. The sessions stay in tmux, and the next
  morning starts from where they are. The work follows the session, not the clock: while it runs (the Mac
  awake, or a session you started at the weekend), the check-ins, events and the Slack watcher go on, the
  watcher at a slower pace outside your hours. The clock only decides the automatic starts, the keep-awake's
  end, when the watchdog holds its alerts, and learning's time.

## Stopping before the evening, and starting again

- **`bosun quit`** stops bosun until you start it: `bosun quit --let-finish` waits until no window is busy
  (exit 3 if they still are, so you can wait longer or stop them), `--stop-now` quits whatever they are doing,
  and `--plan` says what a quit would meet and changes nothing. A quit ends the windows and the console's job,
  and with `--sleep` the keep-awake too (without it the keep-awake the morning started goes on until its time),
  and records itself in `quit.json`: the watchdog and the scheduled learning run skip while it is there, so nothing
  restarts what you stopped. Tomorrow's morning run starts bosun again, unless you quit with `--pause-morning`.
  `bosun stop` only ends the windows and the keep-awake, for the rest of the day: the watchdog expects no
  session and the start at login does not start it again, but tomorrow's morning run does. In the menu bar app,
  **Stop bosun...** is the quit, with boxes for pausing the morning start and letting the Mac sleep; **Quit
  bosun.app** only closes the icon and leaves bosun running, unless you choose to stop it too (see
  [Stop, Quit and Start](setup.md#stop-quit-and-start)).
- **`bosun start`** brings the windows back and ends a quit, and so does **`bosun morning --now`**, the morning
  run at any hour (`--background` has launchd start it: what the menu bar app's Start runs). `bosun status
  --json` says in one object how bosun is, including whether it is stopped. See [Commands](commands.md) for each
  one's options.

## How sessions are resumed

`claude --resume <name>` opens a picker when several sessions share a name or prefix. That would
block an unattended start. `bosun launch` instead reads the session transcripts in
`~/.claude/projects/`, picks the **most recently active session whose name is exactly `<name>`**,
and resumes it by UUID. If none exists it starts a new session with that name. If the session is
already open in another terminal (per `~/.claude/sessions/<pid>.json`), it refuses rather than
running two copies on one transcript.

Every repo window on Claude Code gets `--name <name> --remote-control <name>` plus `BOSUN_CLAUDE_FLAGS`.
Codex has no session names, so a [Codex window](setup.md#running-repo-sessions-on-codex) resumes from bosun's own
table, `codex-sessions.json`.

## Steady state: the session already exists

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
