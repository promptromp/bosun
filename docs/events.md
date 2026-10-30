# Events from repo sessions

Off by default (`BOSUN_EVENTS_ENABLED=0`); design: [docs/design/events.md](design/events.md).
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
  instructions. Every timing and size is a setting: see [Tuning](design/events.md#tuning).
- **Events without check-ins.** With `BOSUN_CHECKIN_MINUTES=0`, check-ins and the `checkin`
  window stop, and with them events and the watchers. `BOSUN_EVENTS_WITHOUT_CHECKINS=1` keeps
  the window's loop for those and never sends a check-in.
- **New reviews on my PRs are raised.** Every `BOSUN_REVIEW_WATCH_MINUTES` (15) the loop also
  runs `bosun review-watch`: a review with changes requested or comments that came after a
  `mypr:` item's last hand-over (or, outside `addressing`, its last report) is recorded on the item
  (`review-arrived`, no state change) and becomes a `review` event, so a second review round
  reaches the PR's window (`delegate --update`) even while it still works on the first.

Without the flag, the rest of this works too: `ledger reports --since` lists every report,
`ledger said`, `working` and `idle` exist (on [Codex](design/harnesses.md), what you said is recorded when
the turn ends, not when you press Return; a window is `working` on its active item while its agent
is mid-turn), `ledger spawn` warns about a task that already comes from the same source or
Slack thread, and the chores run `review-watch` in step 4 (it records, but queues nothing).

To turn it on: set `BOSUN_EVENTS_ENABLED=1` in `bosun.env`, restart the check-in loop
(close the `checkin` window, then `bosun refresh checkin`), restart idle repo sessions so they
pick up the hook (`bosun refresh --restart-idle <window>...`), and have the orchestrator re-read
the policy (`bosun send --no-item orchestrator "re-read orchestrator/policy.md: events"`).

## Slack watcher

Off by default (`BOSUN_SLACK_WATCH_ENABLED=0`; needs events on); design: [docs/design/events.md](design/events.md#slack-watcher).
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
