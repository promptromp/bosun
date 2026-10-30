# bosun documentation

The [README](../README.md) is the overview. These guides cover how bosun works and how to run it;
the design notes in [design/](design/) explain why it works that way.

| Guide | What it covers |
|---|---|
| [Setup](setup.md) | Requirements, install, configuration, sessions, Claude Code login, AWS SSO |
| [Commands](commands.md) | Every `bosun` command |
| [Orchestrator](orchestrator.md) | Chores, delegated tasks, the ledger, outreach, tasks, tickets, workstreams |
| [Permissions](permissions.md) | What needs your approval, and approvals bound to the exact call |
| [Events](events.md) | Events from repo sessions and the Slack watcher |
| [Knowledge](knowledge.md) | The organizational knowledge graph |
| [Learning](learning.md) | The daily learning run |
| [Watchdog](watchdog.md) | Health checks and alerts |
| [Console](console.md) | The local web console and the Impact view |
| [Development](development.md) | Working on bosun, and testing without waiting for the schedule |
| [Known limits](limits.md) | What bosun does not do yet |

## What happens each day

Every morning bosun:

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
