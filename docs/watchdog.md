# Watchdog

The watchdog looks for problems that need you and alerts you about each one once: a morning run that did not
happen, a window stuck at a question, a login that expired, errors in the logs. It runs every 5 minutes as its own
launchd job (`<BOSUN_LAUNCHD_LABEL>.watchdog`, which `install.sh` loads), outside the tmux session, so it also notices
when the session itself, or the morning run, is missing.

## Where alerts go

Every alert is a macOS notification. To get a Slack DM to yourself as well, set `BOSUN_ALERT_SLACK_USER` in
`bosun.env` to your Slack member id (in Slack: your profile, More, Copy member ID).

Alerts go out only inside your hours (your days, from `BOSUN_START_TIME` to `BOSUN_AWAKE_UNTIL`). Anything found
outside them is held: a prompt left open overnight alerts when your hours begin.

<details class="note" markdown>
<summary>How the DM is sent, and adding another destination</summary>

Alerts go through `bosun alert`. The DM goes through a one-shot `claude -p` that may use only ToolSearch and Slack's
send-message tool, in `dontAsk` mode with `--permission-prompts none`, so it cannot itself get stuck at a prompt,
and that loads none of your plugins but the Slack plugin (a plugin's hook or skill could stop it before it sends).
With an `installed_plugins.json` that does not parse, nothing can tell which plugins to switch off: the notification
has already gone out, and the DM is still tried with your plugins (its two-minute timeout bounds one that holds it
up), with a warning in the log saying why.
Another destination is one more sink in `lib/watchdog.py` (`SINKS`).

</details>

## The checks

| Check | Fires when | Alerts once per |
|---|---|---|
| `morning-missing` | On one of your days, `BOSUN_WATCHDOG_GRACE_MINUTES` (15) after the morning run was due (start time plus wake delay), today's morning log has no real (not dry) run. Not on a day its job was loaded after the start time, such as install day | day |
| `morning-failed` | Today's morning run reported problems (a setup step such as an AWS SSO login failed, the network, a window that could not start, an `ERROR`), crashed, or is not done after 45 minutes | day |
| `session-missing` | After today's morning run, there is no tmux session, unless you ran `bosun stop` today or quit bosun | day |
| `checkin-missing` | The session runs, but the `checkin` window is gone or not running the check-in loop | day |
| `orchestrator-down` | After the morning run, the orchestrator's window is gone, or no Claude runs in it, for `BOSUN_WATCHDOG_ORCHESTRATOR_DOWN_MINUTES` (15) awake minutes; it restarts it once per outage | outage |
| `stalled-start` | A window has been starting for 5 minutes: its agent is on a first-run, trust or similar screen only you can answer, or its shell is waiting at a question of your startup files | process (window and pid) |
| `waiting` | A Claude window has waited `BOSUN_ALERT_WAITING_MINUTES` (30) at a permission prompt or a question | prompt (pid and status time) |
| `log-errors` | New `ERROR` or `FAIL` lines in bosun's logs since the last look, mainly the check-in logs and `launchd.err`, grouped into one alert | batch of lines |
| `ledger-stuck` | A ledger item has been `delegated` to a repo window for `BOSUN_WATCHDOG_DELEGATED_HOURS` (8) awake hours without a report or a new hand-over | item and delegation |
| `long-shell` | A background command has kept a Claude window in `shell` (its turn over, the command still running) for `BOSUN_LONG_SHELL_HOURS` (4) awake hours: check it is still wanted | episode (pid and since when) |
| `learn` | With learning on: no learning run an hour after `BOSUN_LEARN_AT` on one of your days, or the run failed, crashed or runs past `BOSUN_LEARN_TIMEOUT_MINUTES` (30) | day (missing), run |
| `events` | With events on: the check-in loop stopped delivering events for 10 minutes while it runs, or a delivery to the orchestrator failed | stall, day (failure) |
| `slack-watch` | With the Slack watcher on: no watch started for two of its intervals while the check-in loop runs, three failed runs in a row, or the day's budget spent | stall, failure streak, day (budget) |
| `spend-limit` | A Claude window, or one of its subagents in the last 12 hours, stopped on a usage limit; the alert names the window and the ledger items it holds | stop (window, pid and since when) |
| `context-size` | A Claude window's conversation has re-read `BOSUN_CONTEXT_WARN_TOKENS` (300000) tokens or more a turn for a day | conversation (window and session) |
| `overhead-share` | The turns bosun started itself cost more than `BOSUN_OVERHEAD_WARN_SHARE` (0.5) of the last 7 days, from the Impact snapshot | week |
| `update-stuck` | An update stopped and left its hold with nothing running it, or a running update's hold outlived its hour | update |
| `app-missing` | With `BOSUN_MENU_APP=1`: `bosun.app` is not in `~/Applications` (moved to the Trash, say). bosun still runs, and the alert says so | outage |
| `login-expired` | Claude Code is logged out, the last login check got a login error, or a Claude window's latest replies are all login errors ([Claude Code login](configuration.md#claude-code-login)) | outage |
| `no-credit` | A Claude window's latest replies are all "Credit balance too low": it is signed in with an API key whose organization has no credit that covers Claude Code ([With an API key](configuration.md#with-an-api-key-instead-of-a-login)) | window and since when |

`bosun watchdog --list` prints every check, and `--status` each one's result now.

<details class="note" markdown>
<summary>More on each check</summary>

- **`morning-missing`**: not on a day its launchd job was loaded after the start time (a first install, the start
  time moved to before now, or today added to your days), since launchd runs no time that passed before a load. The
  load is `loaded-morning` in the state folder, which a fresh load of the job, or one that moves its time or its days,
  writes.
- **`morning-failed`**: a crash is launchd's exit code. A run that bosun quit made skip (the morning start paused, or
  a quit while it set up) ends with `morning run skipped`, which is neither a failure nor a missing run.
- **`session-missing`**: also not while bosun is quit with `bosun quit`. On a day no morning run is owed (its job
  loaded after the start time, as on install day), the session is watched from when `bosun start` made it
  (`started-<session>` in the state folder), as after the wizard's "Start the windows now?".
- **`orchestrator-down`**: events, check-ins and reports wait meanwhile; 0 turns it off. With
  `BOSUN_WATCHDOG_ORCHESTRATOR_RESTART=1` (the default) a full run restarts the orchestrator once per outage with
  `bosun refresh --unless-active <orchestrator>`, which never replaces a running command or acts while you type.
- **`stalled-start`**: its agent (Claude Code, Codex or `agy`) never reached a session, so it is on a screen only you
  can answer (`bosun attach <window>` opens it); or its shell never got as far as starting the agent, because your
  shell's startup files wait for an answer (an update question, zsh's insecure directories; bosun turns oh-my-zsh's
  update question off in its windows). Don't just press Return: on several of Claude Code's screens the default is to
  exit, and on Codex's it says yes.
- **`waiting`**: any Claude window, the orchestrator or a repo window, with what it was asked when a hook signal says
  ([signals](design/signals.md)). The check-in loop runs this check itself (`bosun watchdog --only waiting`, the same
  state) until the watchdog's job is installed, then leaves it to the job.
- **`log-errors`**: the logs (`*.log`, `*.err`) other than the morning's and the watchdog's own (the morning log
  belongs to the morning checks; lines from `alert` and `watchdog` are left out, or they would alert in a loop, and
  so are `learn`'s and `slack-watch`'s, which have their own checks). A usage error (a mistyped flag) or a refusal of
  what you typed (`bosun attach` with a window name that is not there) is said on screen and never written to a log,
  so it does not alert. The menu bar app counts these lines itself, for the last hour, not this check's finding.
- **`ledger-stuck`**: nights and weekends do not count (0 turns it off). The clock restarts at each hand-over to the
  window (`delegate`, `delegate --update`, a move) and each report. Items that wait on someone else are left to the
  nudges: open outreach, or a reported task whose open pull requests all wait on their reviewers or their merge
  (`awaiting-review`, `approved`).
- **`long-shell`**: the orchestrator or a repo window. Meanwhile `refresh` does not restart that Claude. Turns in
  between (a check-in, an event) do not end it; any other status does (0 turns it off).
- **`learn`**: with `BOSUN_LEARN_ENABLED=1`. Not on a day its job was loaded after `BOSUN_LEARN_AT`
  (`loaded-learn`). A crash is a run that ended without an outcome.
- **`events`**: with `BOSUN_EVENTS_ENABLED=1`. Not a stall while the loop holds events back because the orchestrator
  needs `/login`, which `login-expired` reports, as long as the watchdog's own reading of the orchestrator agrees: a
  hold past a sign that the login works is a loop running older code.
- **`slack-watch`**: with `BOSUN_SLACK_WATCH_ENABLED=1` and events on, at any hour while the loop runs: the longer of
  the intervals in force at its last start and now (`BOSUN_SLACK_WATCH_MINUTES_OUTSIDE_HOURS` outside your hours; none
  when that is 0 and now is outside them). Its alerts are held outside your hours, as every check's are.
- **`spend-limit`**: an individual spend limit, or a plan's usage limit. While the window is stopped, a full run makes
  the login check request at most every 30 minutes; once that works (the limit was raised or reset), or the window's
  latest reply is a normal one again, the window gets a resume prompt once. A restarted Claude gets the drain prompt
  instead.
- **`context-size`**: every check-in, notification and resume there re-reads it all (`0` turns it off).
- **`overhead-share`**: the turns bosun started itself are check-ins, events summaries, notifications, resumes,
  messages between sessions and a site's scheduled prompts. Skipped without an Impact snapshot, or when it is over 3
  days old (`0` turns it off).
- **`update-stuck`**: a stopped update's hold, or paused writes; a running update's hold pauses the watchdog, learning
  and the check-in loop. It alerts again for the next update.
- **`app-missing`**: the alert says that `bosun uninstall` removes bosun and `bosun app install` brings the app back.
  It alerts again if the app came back and went.
- **`login-expired`**: logged out per `claude auth status`. Not when a login check that worked, or a normal reply in
  any window, came after every window's last login error: the login is the Mac's, so an idle window clears with the
  rest. It alerts at once, but like every check only inside your hours, and again after it recovered.
- **`no-credit`** (`billing_error`): a subscription's promotional credits do not cover Claude Code. The alert says
  since when and what to do: add credit in the Console's billing, then tell the window to carry on (`bosun send
  <window> ...`); or use your subscription: remove the API key (`ANTHROPIC_API_KEY` in your shell profile, or
  `apiKeyHelper` in `~/.claude/settings.json`), restart the window (`bosun refresh <window>`), then `/login` in it.
  Check-ins are not held back, as for a usage limit. The login check request works without credit, so only a normal
  reply ends it: its own, or one in any window after every window's last such reply (the credit is the
  organization's). A normal reply in between starts a new episode, which alerts again.

</details>

**On Codex windows,** some checks cannot fire yet. Codex's session file records only busy or idle, so a question it
waits on reads as busy (`waiting`) and a background command does not show (`long-shell`); `spend-limit`,
`context-size` and `no-credit` read Claude Code's transcripts; and `login-expired` does not see a revoked Codex login.
See [what windows do not have yet](design/harnesses.md#what-windows-do-not-have-yet).

## How it avoids false alarms

- **Not while bosun is quit.** After `bosun quit`, a run that records or alerts says "bosun is stopped (bosun quit at
  HH:MM): skipping this run" and exits, before it looks at anything, until `bosun start` (as it skips while an update
  holds its work).
- **Only in your hours.** Alerts go out on your days (`BOSUN_DAYS`) between `BOSUN_START_TIME` and
  `BOSUN_AWAKE_UNTIL`; an end at or before the start runs past midnight (08:00 to 01:00 ends at 1 am the next day),
  and until then the day is still the one it began on, for the morning run, the session and their alerts. Anything
  found outside them is held, and log lines are not consumed until then. A session that is missing outside
  your hours is no finding: `bosun status` and the menu bar app show "Resting: outside your hours", with a grey dot.
- **Confirmed first.** A missing morning run, session or check-in loop, or an unfinished morning run, must be seen by
  two runs in a row (5 minutes apart): after a wake the morning job may start a few seconds after the watchdog, and a
  window may be recreated just as it looks. A long gap between runs (the Mac slept) starts the observation over.
- **One message per run.** What a run finds goes out as one alert, the most severe first (at most five items, then
  "and N more"). While something is being confirmed, ready alerts wait one run so they go out together; log lines
  that wait are read again on the next run, not consumed.
- **History is not news.** The first run only records where each log ends. It tracks offset and inode per file, so a
  replaced or new file is read from its start.
- **De-duplication.** `watchdog.json` in the state folder keeps the key of every alert sent (for 14 days), what is
  being confirmed, and the log offsets. Keys are recorded before sending, so a failing Slack DM is not retried every
  5 minutes; the notification and the log still have it.

<details class="note" markdown>
<summary>What the other commands say while bosun is quit or resting</summary>

`--dry-run`, `--status`, `--list` and `--facts` still answer while bosun is quit, and they too know of the quit:
`session-missing` and the check-in check say "you quit bosun at HH:MM (bosun quit)" instead of a missing session, as
`bosun status` and the console's health do (the gate the checks share, `session_gate_kind`). Outside your hours the
gate says "outside the awake window", which `bosun status` and the menu bar app show as "Resting: outside your hours ·
next start <day> <time>".

</details>

## Commands

| Command | What it does |
|---|---|
| `bosun watchdog --dry-run` | What a run would alert, changing no state |
| `bosun watchdog --status` | Every check and its result now (also part of `bosun status`) |
| `bosun watchdog --list` | The checks |
| `bosun watchdog --facts` | What it reads of tmux, ps and launchd, as JSON (what the console reads) |
| `bosun watchdog --only <check>,...` | A run of only those checks |

`bosun doctor` shows whether the watchdog's job is loaded and when it last ran.
