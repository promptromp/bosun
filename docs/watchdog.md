# Watchdog

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
| `stalled-start` | a window's agent (Claude Code or Codex) has run for 5 minutes without reaching a session, so it is on a first-run, trust or similar screen only you can answer (`bosun attach`). Do not just press Return: on several of Claude Code's screens the default is to exit, and on Codex's it says yes | process (window and pid) |
| `waiting` | any Claude window (orchestrator or repo session) has waited `BOSUN_ALERT_WAITING_MINUTES` (30) at a permission prompt or question, with what it was asked when a hook signal says ([signals](design/signals.md)) | prompt (pid and status time) |
| `log-errors` | new `ERROR`/`FAIL` lines since the last scan in the logs (`*.log`, `*.err`) other than the morning and watchdog ones, so mainly the check-in logs and `launchd.err`, grouped into one alert (the morning log belongs to the morning checks; lines from `alert` and `watchdog` are left out, or they would alert in a loop, and from `learn` and `slack-watch`, which have their own checks) | batch of lines |
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
