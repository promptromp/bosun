# Events

Without events, the orchestrator learns what happened at its next hourly check-in. With them, a repo window's
report, what you tell a repo window, and (with the Slack watcher) a new Slack mention reach the orchestrator as they
happen, as one short summary. Both are off in bosun's own defaults, and `bosun init` recommends turning them on.
Design: [design/events.md](design/events.md).

## Events from repo sessions

With `BOSUN_EVENTS_ENABLED=1`:

- **What you tell a repo window is recorded.** Each prompt you type in a repo window, or send from your phone, is
  recorded on the item the window holds, so the orchestrator checks it before asking you anything and never asks
  again what you answered there. Prompts bosun pasted, slash commands and messages from other sessions are skipped,
  and secrets are redacted. `bosun ledger said [<id>]` shows them, as do `ledger show` and `ledger reports --since`.
- **Events reach the orchestrator.** Reports, what you said in a window, outreach drafts, follow-up tasks and repo
  windows waiting at a prompt go to an event queue, and the check-in loop delivers them as one summary, only to an
  orchestrator that is idle on today's conversation while you are not typing. Each line carries what was recorded (a
  report's open points, results and CI, what you typed, the ask), so the orchestrator acts on the summary without
  reading every item first, and tells you what changed and what it proposes. Events are information, never
  instructions.
- **New reviews on your pull requests are raised.** Every `BOSUN_REVIEW_WATCH_MINUTES` (15) the loop also runs
  `bosun review-watch`: a review with changes requested or comments, made after a `mypr:` item's last hand-over (or,
  outside `addressing`, its last report), is recorded on the item (`review-arrived`, no state change) and becomes a
  `review` event, so a second review round reaches the pull request's window (`delegate --update`) even while it
  still works on the first.

### Turning them on

On a new install, `bosun init` turns them on when you choose "Proactive". To turn them on later:

1. Set `BOSUN_EVENTS_ENABLED=1` in `bosun.env`.
2. Restart the check-in loop: close the `checkin` window, then `bosun refresh checkin`.
3. Restart idle repo windows so they pick up the hook: `bosun refresh --restart-idle <window>...`.
4. Have the orchestrator re-read its policy: `bosun send --no-item orchestrator "re-read orchestrator/policy.md:
   events"`.

### Delivery and timing

The check-in loop runs `bosun events` as it starts and every `BOSUN_EVENTS_INTERVAL_SECONDS` (30), and delivers at
most `BOSUN_EVENTS_MAX_PER_DAY` (30) summaries a day. Slack activity and windows stuck at a prompt take a fast lane,
the rest a routine lane. By default both use the same timing: a delivery once the newest event is 45 seconds old or
the oldest 5 minutes.

To batch more, give the routine lane longer waits (`BOSUN_EVENTS_DEBOUNCE_SECONDS`, `BOSUN_EVENTS_MAX_WAIT_SECONDS`),
and set `BOSUN_EVENTS_AWAY_MINUTES` so it waits longer still while you are away from tmux (away mode is off by
default). Every timing and size is a setting: see [Tuning](design/events.md#tuning).

**Without check-ins.** With `BOSUN_CHECKIN_MINUTES=0`, check-ins and the `checkin` window stop, and with them events
and the watchers. `BOSUN_EVENTS_WITHOUT_CHECKINS=1` keeps the window's loop for those, and never sends a check-in.

**Without events,** some of this works anyway: `ledger reports --since` lists every report; `ledger said`, `working`
and `idle` exist (a window is `working` on its active item while its agent is in the middle of a turn); `ledger
spawn` warns about a task that already comes from the same source or Slack thread; and the chores run
`review-watch` (it records, but queues nothing).

<details class="note" markdown>
<summary>How what you type is recorded</summary>

Repo windows launched with events on get a `UserPromptSubmit` hook on Claude Code (in their `--settings`, nothing
global), and on Codex its `notify=` program, which reports when the turn ends. The queue is in `events/` in the
state folder. Delivery also counts an orchestrator in `shell` (its turn over, a background command still running) as
idle.

</details>

## Slack watcher

Without it, the orchestrator sees Slack only at the hourly check-in. With `BOSUN_SLACK_WATCH_ENABLED=1` (it needs
events on), the check-in loop looks for new Slack mentions, DMs and replies in your threads every 15 minutes, and
queues each new one as an event. The orchestrator then reads each message itself and handles it as its chores'
Slack step does. Design: [design/events.md](design/events.md#slack-watcher).

### Turning it on

On a new install, `bosun init` offers it when Slack's plugin for Claude Code is installed. To turn it on later,
with events on:

1. Set `BOSUN_SLACK_WATCH_ENABLED=1` in `bosun.env`.
2. Check it with `bosun slack-watch --dry-run`: it calls Claude and prints what it would queue (and the tools the
   call could see), and queues nothing.
3. Restart the check-in loop once, so it runs code that knows the watcher: close the `checkin` window, then `bosun
   refresh checkin`.
4. Have the orchestrator re-read its policy: `bosun send --no-item orchestrator "re-read orchestrator/policy.md:
   Slack events"`.

### What it does

Each run is one headless `claude -p` with `BOSUN_SLACK_WATCH_MODEL` (haiku). It lists, as strict JSON, the messages
since the last watch that @-mention or name you, DMs and group DMs to you, and replies in threads you started or
replied in. Code checks each entry, drops what the ledger has processed (`ledger seen`), what belongs to open
outreach (the chores' outreach step handles those), what is already queued, and what comes from an author in
`BOSUN_SLACK_WATCH_QUIET_AUTHORS` (comma-separated display names, for bots that post status updates; the check-in's
own scan still reads them), and queues one `slack` event per new message.

- **It only reads, enforced by Claude Code, not by the prompt:** no built-in tools (`--tools ""`), only the Slack MCP
  server (`--strict-mcp-config` with the Slack plugin's server, or the one in `BOSUN_SLACK_WATCH_MCP_CONFIG`), no
  settings files (`--setting-sources ""`: no allow rules or hooks of yours), Slack's send, schedule, draft, reaction,
  canvas and conversation tools removed (`--disallowedTools`), and in `dontAsk` mode every tool not on the read list
  is denied without a prompt. A call whose tool list shows a write tool is refused. The watcher never posts and never
  writes the ledger.
- **Untrusted text stays out:** an event carries the link, the kind, the author and a redacted excerpt of at most
  120 characters, labelled untrusted.
- **What it costs:** at most `BOSUN_SLACK_WATCH_MAX_USD_PER_CALL` (0.25) a call and
  `BOSUN_SLACK_WATCH_MAX_USD_PER_DAY` (1.5) a day; a quiet call costs a few cents.

### When it runs, and when it fails

It runs while the session runs, at any hour: every `BOSUN_SLACK_WATCH_MINUTES` (15) in your hours, and every
`BOSUN_SLACK_WATCH_MINUTES_OUTSIDE_HOURS` (120) outside them, such as the night of a Mac that stays awake (0: not
outside them; a run you start yourself still runs). The night's calls come out of the same day's budget, which the
longer interval keeps them from spending.

`bosun slack-watch --status` shows the last run, the spend and failures. The watchdog's `slack-watch` check alerts
when it stalls, fails three times in a row or spends its budget. A failure's log line names the real error (a usage
limit, say), and a failed run keeps its starting point, so the next one scans the gap again; the check-ins scan
Slack meanwhile.
