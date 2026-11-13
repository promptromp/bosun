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
   [Steady state](setup.md#steady-state-the-session-already-exists).
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
  morning starts from where they are.

## Stopping before the evening, and starting again

- **`bosun quit`** stops bosun until you start it: `bosun quit --let-finish` waits until no window is busy
  (exit 3 if they still are, so you can wait longer or stop them), `--stop-now` quits whatever they are doing,
  and `--plan` says what a quit would meet and changes nothing. A quit ends the windows and the console's job,
  and with `--sleep` the keep-awake too (without it the keep-awake the morning started goes on until its time),
  and records itself in `quit.json`: the watchdog and the scheduled learning run skip while it is there, so nothing
  restarts what you stopped. Tomorrow's morning run starts bosun again, unless you quit with `--pause-morning`.
  `bosun stop` only ends the windows, as before.
- **`bosun start`** brings the windows back and ends a quit, and so does **`bosun morning --now`**, the morning
  run at any hour (`--background` has launchd start it: what the menu bar app's Start runs). `bosun status
  --json` says in one object how bosun is, including whether it is stopped. See [Commands](commands.md) for each
  one's options.
