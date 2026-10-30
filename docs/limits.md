# Known limits

See also [ROADMAP.md](../ROADMAP.md) for planned work and open questions.

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
