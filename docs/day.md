# How a day runs

What bosun does on its own each day, and when: the morning start, the check-ins through the day, the evening,
and how to stop it and start it again. The times below are the defaults; you change them in the menu bar app's
[Settings](menu-bar-app.md#settings), with `bosun settings set`, or in [Configuration](configuration.md). The
[README](../README.md#a-day-with-bosun) has the short version.

## A day at a glance

| When | What happens | Setting |
|---|---|---|
| 08:00, on your days | The Mac wakes, the morning run checks your logins, the windows start or are refreshed, and the orchestrator runs its morning chores | `BOSUN_START_TIME`, `BOSUN_DAYS` |
| When you log in during your hours | bosun starts, if it is not running and you did not stop it today | `BOSUN_START_AT_LOGIN` |
| Every hour | A check-in: the orchestrator looks at what is new, when something is | `BOSUN_CHECKIN_MINUTES` |
| As things happen | Reports from repo windows, and new Slack mentions, reach the orchestrator (with events on) | `BOSUN_EVENTS_ENABLED` |
| Every 5 minutes | The [watchdog](watchdog.md) checks that everything is alive | |
| 18:30 | The [learning](learning.md) run reviews the day (with learning on) | `BOSUN_LEARN_AT` |
| 20:00 | bosun stops keeping the Mac awake, and the Mac may sleep | `BOSUN_AWAKE_UNTIL` |

## The morning

Every morning on your days, bosun:

1. **Wakes the Mac** with a scheduled wake (`pmset`), and **keeps it awake** until your "awake until" time
   (`caffeinate`).
2. **Checks your logins**: Claude Code's, and any AWS SSO profiles, which it logs in again where it can. A login
   only you can fix is an alert.
3. **Starts the windows** in a tmux session of its own: the orchestrator, one window per repo, and the `checkin`
   window. Each window resumes its named session where it stopped, on Claude Code with **Remote Control** so you
   can drive it from your phone. A window that had work in flight gets a prompt to finish what sleep or a usage
   limit cut off.
4. **Runs the orchestrator's morning chores** in a fresh conversation: it reads Slack and your pull requests,
   records what it finds in the [ledger](ledger.md), hands review and fix tasks to the repo windows, and asks you
   about anything that would go out under your name.

On later days the tmux session is usually still there, since the Mac only slept. The morning run then refreshes
it window by window instead: it restarts idle windows whose agent was updated or whose model or effort changed,
and leaves busy ones alone ([Steady state](#steady-state-the-session-already-exists) has the rules).

If bosun is not running when you log in (after a restart, say), by default it starts then, on one of your days and
inside your hours, unless you stopped it earlier that day ([Starting at login](menu-bar-app.md#starting-at-login)). With
the [menu bar app](menu-bar-app.md), its icon says all along whether bosun is well, and Start bosun runs the
morning at any hour.

<details class="note" markdown>
<summary>The morning, step by step</summary>

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

The orchestrator hands review tasks and fixes on your pull requests to the repo windows on its own, each with the
rules for what that kind of task may do (post a review, push to your branch). Anything else other people would
see waits for your approval, which you can give from your phone. The ledger's stable ids and state machines make
this safe to run again across runs and days. Repo windows may run on Claude Code, Codex or Antigravity (a
preview); see [Configuration](configuration.md#running-repo-sessions-on-codex).

</details>

## During the day

- **Check-ins.** The `checkin` window asks the orchestrator for `/bosun:chores check-in` every 60 minutes
  (`BOSUN_CHECKIN_MINUTES`), when it is idle and something is new since the last one, or after 4 quiet hours
  (`BOSUN_CHECKIN_MAX_QUIET_HOURS`). See [Orchestrator](orchestrator.md).
- **Events.** With `BOSUN_EVENTS_ENABLED=1`, repo windows' reports and, with the Slack watcher on, new mentions
  and replies reach the orchestrator as they happen instead of at the next check-in. See [Events](events.md).
- **The watchdog** checks every 5 minutes that the windows, the check-in loop and the background jobs are alive,
  and alerts you when something is down or a window has waited at a question too long. See
  [Watchdog](watchdog.md).
- **Approvals.** Whatever would go out under your name waits for your one-line approval. See
  [Permissions](permissions.md).

## The evening

- **18:30, learning** (with `BOSUN_LEARN_ENABLED=1`; `BOSUN_LEARN_AT`): a headless Claude reads the day's
  sessions, the ledger and the logs, and proposes fixes for your approval. See [Learning](learning.md).
- **20:00** (`BOSUN_AWAKE_UNTIL`): bosun stops keeping the Mac awake. The windows stay in tmux, and the next
  morning starts from where they are.

The work follows the session, not the clock. While bosun runs (the Mac awake, or a session you started at the
weekend), the check-ins, events and the Slack watcher go on, the watcher at a slower pace outside your hours. The
clock only decides the automatic starts, the keep-awake's end, when the watchdog holds its alerts, and learning's
time.

## Stopping and starting again

| You want to | In a terminal | In the menu bar app |
|---|---|---|
| Stop bosun until you start it again | `bosun quit --let-finish` (wait for busy windows) or `bosun quit --stop-now` | Stop bosun... |
| ... and keep tomorrow's morning start off too | add `--pause-morning` | the "Pause the morning start" box |
| ... and let the Mac sleep now | add `--sleep` | the "Let this Mac sleep now" box |
| Stop for the rest of today | `bosun stop` | |
| Start bosun now, at any hour | `bosun start`, or `bosun morning --now` for the whole morning run | Start bosun |
| See how bosun is | `bosun status` | the icon |

- **`bosun quit`** ends the windows and the console's background job, and records the quit (`quit.json`), so the
  watchdog and the scheduled learning run skip and nothing restarts what you stopped. Tomorrow's morning run starts
  bosun again, unless you quit with `--pause-morning`. `--let-finish` waits until no window is busy, and exits 3 if
  they still are when its minutes run out (`BOSUN_QUIT_WAIT_MINUTES`, 10), so you can wait longer or stop them.
  `bosun quit --plan` says what a quit would meet, and changes nothing.
- **`bosun stop`** ends only the windows and the keep-awake, for the rest of the day: the watchdog expects no
  session, and logging in does not start bosun again, but tomorrow's morning run does.
- **`bosun start`** brings the windows back and ends a quit, and so does `bosun morning --now`.

The menu bar app's Quit bosun.app only closes the icon and leaves bosun running, unless you choose to stop it too
([Stop, Quit and Start](menu-bar-app.md#stop-quit-and-start)). [Commands](commands.md) has every option.

## How sessions are resumed

`claude --resume <name>` opens a picker when several sessions share a name or prefix, which would stop an
unattended start. So `bosun launch` reads the session transcripts in `~/.claude/projects/`, picks the most
recently active session whose name is exactly `<name>`, and resumes it by its id. If there is none, it starts a
new session with that name. If the session is already open in another terminal (per
`~/.claude/sessions/<pid>.json`), it refuses rather than run two copies on one transcript.

Every repo window on Claude Code gets `--name <name> --remote-control <name>`, plus `BOSUN_CLAUDE_FLAGS`. Codex
has no session names, so a [Codex window](configuration.md#running-repo-sessions-on-codex) resumes from bosun's
own table, `codex-sessions.json`.

## Steady state: the session already exists

After the first day the tmux session normally survives the night, since the Mac only sleeps. Attach to it any time
with `bosun attach`. The morning run never makes a second session: it runs `bosun refresh`, which handles each
window on its own.

| Window | What refresh does |
|---|---|
| Its folder is gone (moved or deleted) | Skips it, as `bosun start` does, and says so in the log, which the watchdog alerts on. It never opens the window in another folder |
| Missing | Creates it and starts its agent |
| Agent not running | Starts it again in the window's folder, resuming its session |
| Agent `busy` (in the middle of a turn) | Leaves it alone |
| Agent `waiting` (at a permission prompt or a question to you) | Leaves it alone: a pasted Enter could answer it |
| Window split into several panes | Leaves it alone and reports it, since restarting could kill the other pane's process |
| Agent idle for at least `BOSUN_RESTART_MIN_IDLE_SECONDS` (120), and on an older version than the one installed, or started with another model or effort than the configured one; for the orchestrator also when it is still on yesterday's conversation | Restarts it: SIGTERM (Claude exits cleanly in about 2 seconds), then starts it on the installed version with the configured model and effort, in the same conversation (the orchestrator's new-day restart starts today's instead), and on Claude Code with a fresh Remote Control connection |
| Anything else (idle and current, idle too briefly, or `shell`: its turn over but a background command still running) | Sends the drain prompt, if the window has something in flight (below) |

The drain prompt, which a restart also sends, goes only to a window with something in flight: the orchestrator, a
Claude in `shell`, or a window holding a handed-over item whose work is not done (`bosun ledger in-flight
<window>` lists them). Any other window is resumed or left idle without a prompt ("left idle: nothing in
flight"), since every prompt re-reads the whole conversation.

Claude Code updates itself, but a running session keeps the old version until it restarts; `refresh` is how the
windows pick up updates without losing work. The morning run passes `--unless-active`: if you typed in an
attached tmux client within `BOSUN_ACTIVE_CLIENT_MINUTES`, it leaves the windows alone rather than paste into your
draft. Run `bosun refresh` yourself any time, with `--dry-run` to see what it would do.

`BOSUN_ON_EXISTING` picks what a start does to an existing session: `refresh` (the default), `drain` (never
restart) or `restart` (end the whole session and start it again).

<details class="note" markdown>
<summary>Long conversations: fresh conversations and compaction</summary>

Every prompt makes a window re-read its whole conversation, so bosun keeps them from growing without bound:

- **Compaction.** Every Claude Code window starts with `claude --autocompact BOSUN_AUTOCOMPACT_TOKENS` (300000;
  the orchestrator's is `BOSUN_ORCHESTRATOR_AUTOCOMPACT_TOKENS`; a `sessions.yml` entry can set its own
  `autocompact`; empty or 0 leaves Claude Code's default, and `auto` is accepted). Once a conversation passes that
  size, Claude Code compacts it into a summary by itself, in the middle of a task if need be, so no window re-reads
  300k to 900k tokens a turn for the rest of the day. Claude Code's own idle compaction (200k tokens or more, idle
  about an hour) still applies.
- **What compaction keeps.** A summary can drop which ledger items the window holds, so with
  `BOSUN_COMPACT_CONTEXT=1` (the default) each launch adds a SessionStart hook (matcher `compact`,
  `bosun hook compact-context`) that adds them back, in at most `BOSUN_COMPACT_CONTEXT_MAX_CHARS` (1500)
  characters, from `bosun ledger compact-context`: for a repo window its items (in flight first) and how to report;
  for the orchestrator the proposals waiting for your approval and the open items per window.
- **Fresh conversations.** With `BOSUN_FRESH_CONVERSATIONS=1` (off by default), refresh, and so the morning run,
  also starts a fresh conversation for a repo window that is idle for `BOSUN_RESTART_MIN_IDLE_SECONDS`, holds
  nothing in flight, and whose conversation grew past `BOSUN_FRESH_CONTEXT_TOKENS` (200000, the size its latest
  turn read). The new conversation is named `<window>-<date>-<n>` (kept in `conversations/<window>` in the state
  folder, which later launches resume) and starts with a short prompt pointing at its memory notes and `bosun
  ledger list --open`. `refresh --dry-run` names the windows that would get one, even with the setting off. It is
  the backstop to compaction: a window still over `BOSUN_FRESH_CONTEXT_TOKENS` at the morning refresh starts a new
  one.

These settings are read at launch, so a running window picks them up at its next restart. On Codex bosun sets no
compaction: `BOSUN_AUTOCOMPACT_TOKENS`, `autocompact:` and `BOSUN_COMPACT_CONTEXT` do nothing there.

</details>

<details class="note" markdown>
<summary>How bosun knows what a window is doing</summary>

A window's status and version come from Claude Code's own `~/.claude/sessions/<pid>.json`, and on Codex from its
session files; the status is read again just before a restart. With `BOSUN_HOOK_SIGNALS=1` (the default) each
launch also adds async hooks (`bosun hook signal`) on Notification, PermissionRequest, Stop, StopFailure,
SessionStart and SessionEnd, which record Claude Code's own signals per window in `signals/<window>.jsonl` in the
state folder (at most `BOSUN_SIGNALS_MAX_KB`, 256). With them the watchdog's `waiting` alert and the events'
waiting line say what the window was asked (`Bash: make deploy`), `long-shell` names what is still running, and
`spend-limit` also sees a stop only the StopFailure hook reported. They add to the session records and
transcripts, and never replace them. Design: [design/signals.md](design/signals.md).

</details>
