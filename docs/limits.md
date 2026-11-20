# Known limits

What bosun does not do yet, and what it depends on that could change under it. Read it to decide whether bosun fits
your setup, or when something behaves unexpectedly. Planned work and open questions are in the
[roadmap](../ROADMAP.md).

## Your Mac

- **macOS only, one engineer, one Mac** for now. Linux is next.
- **The Mac must stay logged in** (asleep is fine; logged out is not), and a laptop needs its power adapter and the
  lid open, or an external display, when the wake comes and through your hours. See
  [A typical setup](setup.md#a-typical-setup).
- **Whether Remote Control reconnects after a long sleep** without a restart is not verified. Windows that `refresh`
  restarts get a fresh connection.

## The agents

- **Claude Code's internals.** bosun relies on parts of Claude Code that are not a public API, all read and written
  in `lib/claude_state.py`: transcript `custom-title` entries in `~/.claude/projects/` (name resolution),
  `~/.claude/sessions/<pid>.json` (`status`, `version`, `statusUpdatedAt`), folder trust in `~/.claude.json`, and
  the error fields of failed requests and `claude auth status --json` (login detection, verified on 2.1.304). It was
  last run from a clean install on Claude Code 2.1.308, and asks for 2.1.283 or newer. If a Claude Code update
  changes these, `tests/smoke.sh` still passes (it uses fixtures), so check with `bosun refresh --dry-run` and `bosun
  doctor` after major updates, and with `tests/sandbox.sh mac live claude` if you can.
- **Codex's internals,** likewise, all in `lib/codex_state.py`: the session files in `~/.codex/sessions/`,
  `[projects]` in `~/.codex/config.toml`, the JSON of its `notify=` program and the screens `lib/pane_input.py`
  reads. Verified on Codex 0.166.0. After a Codex update, check with `bosun doctor`, and with `tests/sandbox.sh mac
  live codex` if you can.
- **Codex and Antigravity windows** do not have everything a Claude Code window has, and the orchestrator needs
  Claude Code: see [the list](design/harnesses.md#what-windows-do-not-have-yet).
- **Antigravity (`agy`) windows are a preview.** They are tested against a stand-in, in the smoke and Docker suites,
  but the live run on the real `agy` (`tests/sandbox.sh mac live agy`, which needs a person to sign in inside the
  throwaway VM) has not passed yet. They rely on `agy` internals that are not a public API, all in
  `lib/agy_state.py`: its hooks' JSON, its conversation files and `agy-sessions.json`'s source, the Keychain item of
  its login, `~/.gemini/config/` and its screens. Verified on `agy` 1.5.5 (the VM), and its sessions on 1.5.13. Google has said nothing about another
  program driving `agy` unattended, and an `agy` account has been reported suspended under its terms.
- **What an `agy` window cannot do.** It runs repo git hooks nowhere: an approved commit would otherwise run the
  repo's own hook code outside `agy`'s sandbox, so checks that a pre-commit hook makes run in CI only. Its login
  reaches only a desktop session, never ssh. A window starts only if `agy` has no MCP server its connectors list does
  not name, the Antigravity app's included. It does not start when an `.agents/` in its folder or any folder above
  it up to your home folder holds a `plugins` folder or a file named like `*hooks*.json`, `plugin.json`,
  `plugins.json` or `mcp_config.json` (rules, skills and workflows are fine), whose hooks or servers `agy` would
  run outside its sandbox, nor with a log folder (`BOSUN_LOG_DIR`) that has a space in its path. That is checked
  when a window starts, not while it runs. bosun's plugin lets past `agy`'s sandbox only an allowlist of exact
  `git`, `gh` and `bosun` command shapes, what the task files let a repo session do, since `agy` 1.5.13 takes such
  an approval as final (its own reviewer no longer looks): a push only of the branch checked out, never the
  default branch, and only when the remote's default branch is known (`origin/HEAD`, which a clone sets); a PR
  only as a draft; a review that approves or comments; reads. Anything else is left to `agy`.

## Starting work

- **Every delegated task starts work in a repo window.** `bosun send --new` and `--start` each start an agent's
  session, so the orchestrator proposes them for approval rather than running them itself.
- **A session you have open in your own terminal** cannot be started in tmux at the same time: `--start` then fails
  with "already running", and the orchestrator tells you rather than opening another window.

## Logins and credentials

- **An expired AWS Identity Center session needs your browser.** `ensure_aws_sso` then runs `aws sso login`, which
  opens the browser, and posts a macOS notification. It logs in once per `[sso-session]` (`aws sso login
  --sso-session <name>`), which covers every profile of that session; the session's other profiles only check their
  credentials, and if the login failed they fail at once instead of opening the browser again. If the browser is
  still signed in to the AWS access portal, the login can complete on its own (on the first real run it finished in
  20 seconds with nobody at the Mac). Otherwise it needs a click: a login still waiting after
  `BOSUN_AWS_SSO_ALERT_AFTER` seconds (60) alerts you (`bosun alert`: a notification and, with
  `BOSUN_ALERT_SLACK_USER`, a Slack DM) with the time it gives up, so you can approve it at the Mac. The step times
  out after `BOSUN_LOGIN_TIMEOUT` seconds, its failure names the command to run later, and the rest of the morning
  run goes on. The login's authorization address is masked in the log.
- **Static AWS credential variables** (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`), for example
  from a local cloud emulator or test credentials, override SSO profiles: tools and SDKs that read the default
  credentials then fail as if the SSO session had expired, while `aws --profile X` still works. tmux windows inherit
  the environment of whatever started the tmux server, which is why bosun runs on its own server
  (`BOSUN_TMUX_SOCKET`, default `bosun`). `bosun doctor` warns when the session's tmux environment has them, and
  delegated tasks include the workaround.

## Cost

- A morning chores rehearsal took about 4.5 minutes and $1.60 (30 turns). Check-ins only process what is new since
  the last run. `bosun chores --dry-run` prints the cost of a run.

## Updating

- **There is no command to go back:** a failed update puts everything back by itself, and going back later is by
  hand, to the version the update came from, and never past a `kg forget`
  ([Going back](updating.md#going-back)).
- It updates only to the newest release, or on the `main` channel the head of the branch the install follows.
- Its unit and smoke tests run on stand-ins (fake windows and launchd); the real runs are in the macOS VM
  (`tests/sandbox.sh mac work` and `live`: a working day updated mid-afternoon, the freeze on a year of data, the
  real Claude Code and Codex answering its prompts).
