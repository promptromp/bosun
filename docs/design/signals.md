# Claude Code's own signals (hooks)

`BOSUN_HOOK_SIGNALS=1` (default) adds async hooks to every Claude Code window bosun launches (repo
sessions through `repo_session_settings`, the orchestrator through `orchestrator_extra_settings`).
Each runs `bosun hook signal --window <window>`, which appends one compact line to
`$BOSUN_STATE_DIR/signals/<window>.jsonl` (`lib/signals.py`). The hooks are `async`, so they
never delay the session; the recorder always exits 0 and reports problems to the hook log. Each
file keeps its newest half once it passes `BOSUN_SIGNALS_MAX_KB` (256).

## What is recorded

| Event | kind | detail | extra |
|---|---|---|---|
| PermissionRequest | tool name | what it runs or touches (`command`, `file_path`, `url`, ...) | |
| Notification | `notification_type` | the message | |
| Stop | | | `running`: the background tasks still running (type, command or description) |
| StopFailure | the error type (`rate_limit`, `billing_error`, `authentication_failed`, ...) | the error text | |
| SessionStart | `source` (`startup`, `resume`, `compact`, ...) | | |
| SessionEnd | `reason` | | |

Every line has `at` (UTC), `event` and `session` (Claude's session id); subagent events carry
`agent`. Details are redacted (`lib/redact.py`) and cut to 200 characters. Readers only use lines
of the window's current session (the `sessionId` of its `~/.claude/sessions/<pid>.json` record),
so an earlier Claude's signals never count.

## Who reads them, and what stays primary

Detection stays where it was reliable: the session record's `status` (`waiting`, `shell`, ...) and
the transcript's structured error fields. The signals add what those cannot say:

- `waiting` (watchdog) and the events' waiting line: what the window was asked. The
  PermissionRequest (tool and command) is preferred over the `permission_prompt` notification that
  follows it about 6 s later, whose text is only "Claude needs your permission".
- `long-shell`: which background work keeps the window in `shell` (the last Stop's `running`).
- `spend-limit`: a StopFailure whose error type is `rate_limit` or `billing_error` and whose text
  names a spend or usage limit counts as a stop when the transcript shows none, until a later Stop
  (a turn finished). The transcript's `apiError: usage_limit_reached` stays the primary signal: a
  StopFailure only has the error type and the text, and `rate_limit` also covers short throttling.

## What was measured before choosing (2.1.312, a lab session on its own tmux socket)

- A permission prompt sets the record's `status` to `waiting` at the moment the PermissionRequest
  hook fires; the Notification (`permission_prompt`) follows about 6 s later. Answering it sets
  `idle` again.
- A turn that ends with a background subagent and a shell still running: the record says `shell`,
  `claude agents --json` says `busy`, and the Stop hook lists both tasks (`background_tasks`). Each
  wake from the subagent is one more Stop with a new `prompt_id`; `idle_prompt` arrives about a
  minute after a turn ends, also while background work runs.
- A subagent going idle on its own background work fires SubagentStop, not a Notification;
  `agent_needs_input` / `agent_completed` are for background sessions (`claude agents`).
- StopFailure carries `error` (its type) and `last_assistant_message` (the text); a forced
  `max_output_tokens` failure produced it in a headless run. A spend limit arrives as
  `rate_limit` (HTTP 429), so the text is what tells it from throttling.
- `claude agents --json` (1.2 s a call) lists name, pid, sessionId, cwd and status, but no
  `statusUpdatedAt`, and reports a window in `shell` as `busy`. The session records it reads give
  more, so bosun keeps reading them (through `lib/claude_state.py`).

## What still reads the screen

- `bosun send`: whether the input line is empty and whether the pasted prompt was
  rejected (`lib/pane_input.py`); a paste is a screen action, so its checks stay on the screen.
- The check-in loop's login check falls back to the pane's last lines only when the window has no
  transcript to read (`login_ok`, `claude_state.pane_auth_error`).
- A launch that fails shows its last lines in the error (`bosun send`).
