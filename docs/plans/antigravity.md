# Antigravity windows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A repo window can run on Google's Antigravity CLI (`agy`), unattended in `auto`: started and resumed
under its bosun name, its status known, its folder trusted, its connectors held to its list, and brought along
by the morning run and `bosun update`, while Claude Code and Codex windows behave as before.

**Architecture:** An adapter module per agent, as Part 1 left it. `agy` gets its own `lib/agy_state.py` (its
files, its screen, its hooks), `lib/connectors_agy.py`, `lib/launch/agy.sh` and `lib/harnesses/agy.sh`, and a
plugin bosun installs into `agy` (`agents/agy-plugin/`). Shared code gains three things it asks of a profile: an
agent's own hooks, a one-time setup, and an update command. The uninstall and backup tables take each agent's
state files from its profile.

**Tech Stack:** Bash 3.2 (macOS `/bin/bash`), Python 3.13+ (stdlib, mypy strict), tmux, `agy` 1.5.5.

**Spec:** `docs/design/antigravity.md`, Part 2 (amended after the VM run of 2026-11-11), with the run's
captures and payloads in `tests/fixtures/agy/`.

## Global Constraints

- `agy` minimum version: `1.5.5`. `agy --version` prints the bare version.
- No change in behaviour for Claude Code and Codex windows. Each shared change is checked against their existing
  tests, which pass unchanged except where a step names the test it changes.
- No agent's name in shared code: `tests/unit/test_agent_names.py` passes, and its `ALLOWED` list does not grow.
- bosun writes nothing in `agy`'s own settings (`~/.gemini/antigravity-cli/settings.json`,
  `~/.gemini/config/mcp_config.json`, `~/.gemini/config/config.json`). The one change in `agy`'s files is bosun's
  plugin, through `agy plugin install`, recorded in `lib/agent_changes.py`, and taken back by `bosun uninstall`.
- An `agy` window never gets `--add-dir`, `-c`/`--continue`, `--mode`, or `--dangerously-skip-permissions`.
  `auto` and trust come from `--settings` (`agentMode`, `trustedWorkspaces`). Effort above `high` is passed as
  `high`.
- Every hook of bosun's plugin prints nothing and exits 0 unless `BOSUN_HOOK_CMD` is in its environment. Never
  `{}`: `agy` takes it, an exit other than 0 and a blank decision all as a refusal of the tool.
- The plugin's `PreToolUse` answer, exactly: `{"decision":"auto_approve","overwrite":{"BypassSandbox":true}}`
  (a `reason` may be added). The resume guard's `PreInvocation` answer, exactly:
  `{"injectSteps":[{"errorMessage":{"userMessage":"<why>"}}],"terminationBehavior":"terminate"}`.
- The marker in an `agy` window's environment: `BOSUN_WINDOW` (the window), `BOSUN_CONVERSATION` (the name its
  conversation goes by, which `resolve` looks up), `BOSUN_HOOK_CMD` (what the plugin's hooks run), and, on a
  resume, `BOSUN_RESUMED` (the id it resumed). Its command line carries `--log-file
  $BOSUN_LOG_DIR/agy-<conversation name>.log`.
- Config keys and defaults (in `lib/common.sh` and `examples/generic/bosun.env`): `BOSUN_AGY_BIN="agy"`,
  `BOSUN_AGY_FLAGS=""`, `BOSUN_AGY_MODEL=""`.
- The repo's limits hold: mypy strict, ruff, complexipy <= 15, lizard CCN <= 10 and <= 60 lines, shellcheck -S
  style, shfmt, `tools/shell_complexity.py`; libexec scripts replaced atomically; docs change in this pull request.
- Fixtures are fictional or masked: no e-mail address, account tier, real name or id (forbidden-terms clean).

## Review Focus

1. A person's own `agy` sessions, with bosun's plugin installed: every hook prints nothing, exits 0 and returns
   at once. Pinned in Task 7 (`test_every_hook_is_silent_outside_a_bosun_window`).
2. bosun's hook failing inside a bosun window (an exception, bosun mid-update): it must print nothing and exit
   0, never a refusal that blocks every tool. Pinned in Task 2 (`test_a_failing_agent_hook_prints_nothing`).
3. A resume that `agy` silently turns into a fresh conversation: the first model call is stopped and bosun says
   so. Pinned in Task 7 (`test_pre_invocation_stops_a_resume_that_got_another_conversation`).
4. Two `agy` windows in one repo: each pid maps to its own window's status and conversation. Pinned in Task 5
   (`test_two_windows_in_one_repo_keep_their_own_status`).
5. A busy status whose screen cannot be read (no pane, tmux gone): it stays busy, never idle. Pinned in Task 5
   (`test_a_busy_status_with_no_screen_stays_busy`).

---

### Task 1: The uninstall and backup tables take agents' state files from their profiles

**Files:**

- Modify: `lib/uninstall_plan.py` (`STATE_FILES`: drop the `codex-sessions.json` rows; add each agent's)
- Modify: `lib/update_backups.py` (`IN_FLIGHT`: drop `"codex-sessions.json"`; add each agent's)
- Test: `tests/unit/test_uninstall_plan.py`, `tests/unit/test_update_backups.py`

**Interfaces:**

- Consumes: `agent_state.AGENTS`, `Profile.state_files`.
- Produces: `uninstall_plan.agent_state_rows() -> tuple[tuple[str, str], ...]`: for each name in every agent's
  `state_files`, `(name, WORKING)`, and for a name without `/`, `(f".{name}.*", WORKING)` (what an atomic write
  cut short leaves). `update_backups.IN_FLIGHT` ends with every agent's state files, in `AGENTS` order.

- [ ] **Step 1: Write the tests.** `test_each_agents_state_files_are_rows` (every name in every profile's
  `state_files` is a row, with its dot pattern); `test_the_rows_for_codex_are_what_they_were` (`("codex-sessions.json",
  WORKING)` and `(".codex-sessions.json.*", WORKING)` are in `STATE_FILES`); `test_in_flight_holds_each_agents_files`.
  Check first that `uninstall_plan` and `update_backups` can import `agent_state` without a cycle (nothing
  `agent_state` imports imports either).
- [ ] **Step 2: Run them:** `uv run python -m pytest -q tests/unit/test_uninstall_plan.py tests/unit/test_update_backups.py </dev/null`.
  Expected: the new tests PASS before the change too except `test_each_agents_state_files_are_rows`, which FAILS
  on the missing `agent_state_rows`. They guard the rows through the change.
- [ ] **Step 3: Build both tables from the profiles** as above, with no file name of an agent left in either.
- [ ] **Step 4: Run** the two test files, `tests/unit/test_agent_profiles.py` and `tests/unit/test_agent_names.py`.
  Expected: PASS.
- [ ] **Step 5: Commit** ("Uninstall and backups: each agent's state files come from its profile").

### Task 2: An agent's own hooks

**Files:**

- Modify: `lib/agent_files.py` (`AgentHooks`; `Profile.hooks`)
- Modify: `lib/hook.py` (`agent --harness <h> --event <e>`, the hook's JSON on stdin)
- Modify: `libexec/bosun-hook` (routes `agent` in the foreground, stdin and stdout through)
- Test: `tests/unit/test_hook.py`, `tests/smoke.sh` (the hook section)

**Interfaces:**

- Produces: `agent_files.AgentHooks` (frozen): `events: tuple[str, ...]`, `answer: Callable[[str, Mapping[str,
  Any], Mapping[str, str]], str]` (event, the hook's JSON, the environment) returning what to print, `""` for
  nothing. `Profile.hooks: AgentHooks | None = None`, after `turn_end`.
- Produces: `bosun hook agent --harness <h> --event <e>` (stdin: the JSON). It prints the answer and exits 0. An
  agent bosun does not know: exit 2, `no agent '<h>': bosun knows <ids>`. One with no hooks: exit 2, `<h> has no
  hooks of its own`. An event not in `events`: exit 2, `<h> has no hook <e>`. Any error once the agent and event
  are known (bad JSON, the answer raising): an ERROR line in the hook log, nothing printed, exit 0.

- [ ] **Step 1: Write the tests** in `tests/unit/test_hook.py`, with a stub agent module patched into `AGENTS`
  whose profile carries `AgentHooks(("Ping",), lambda event, payload, env: json.dumps({"seen": payload["n"]}))`:
  `test_an_agent_hook_prints_its_answer` (stdin `{"n": 1}` prints `{"seen": 1}`, exit 0);
  `test_a_failing_agent_hook_prints_nothing` (an answer that raises: stdout empty, exit 0, the log has `ERROR`);
  `test_bad_json_prints_nothing`; `test_an_agent_with_no_hooks_is_refused` (`--harness claude`: exit 2, `claude
  has no hooks of its own`); `test_an_unknown_event_is_refused`.
- [ ] **Step 2: Run them to see them fail:** `uv run python -m pytest -q tests/unit/test_hook.py </dev/null`.
- [ ] **Step 3: Add `AgentHooks`, the `agent` event in `hook.py` and its route in `bosun-hook`.** It never detaches:
  the answer is what `agy` waits for. `bosun-hook` passes stdin through (not `</dev/null`) and exits 0 whatever the
  Python's status, for `agent` only. Its `# Usage:` header gains the line and says why it must never fail.
- [ ] **Step 4: Run** the hook tests and add a smoke check: `bosun hook agent --harness claude --event Ping`
  refused with `claude has no hooks of its own` (`fails_with`).
- [ ] **Step 5: Commit** ("Hooks: an agent's own hooks, answered in the foreground and never failing").

### Task 3: A one-time setup per agent, and the morning's update

**Files:**

- Modify: `lib/agent_files.py` (`AgentSetup`, `SetupError`; `Profile.setup`, `Profile.update_args`)
- Modify: `lib/agent_state.py` (`setup <harness>`; `profiles` gains the column `update_args`)
- Modify: `lib/agent_changes.py` (`SETUPS`, `record_setup`, `forget_setup`)
- Modify: `lib/uninstall_detect.py`, `lib/uninstall_plan.py`, `lib/uninstall.py` (a `SETUP` step per recorded setup)
- Modify: `lib/agents.sh` (`agent_setup <harness>`, `update_agents`), `libexec/bosun-launch`, `libexec/bosun-morning`
- Test: `tests/unit/test_agent_changes.py`, `tests/unit/test_uninstall_plan.py`, `tests/unit/test_uninstall.py`,
  `tests/unit/test_agent_profiles.py`, `tests/smoke.sh`

**Interfaces:**

- Produces: `agent_files.AgentSetup` (frozen): `description: str` (how uninstall names it), `needed: Callable[[],
  bool]`, `apply: Callable[[], None]` (raises `SetupError` saying why), `undo: Callable[[], None]`.
  `Profile.setup: AgentSetup | None = None` and `Profile.update_args: tuple[str, ...] = ()`, after `hooks`.
- Produces: `agent_state.py setup <harness>`: with a setup that is `needed()`, `apply()`, then
  `agent_changes.record_setup(state_dir, harness)`; exit 0. Nothing to do: exit 0, nothing printed. A
  `SetupError`: exit 1, its text on stderr.
- Produces: `agent_changes.record_setup(state_dir, harness)`, `forget_setup(state_dir, harness)`, and `read()`
  returning `SETUPS: list[str]`.
- Produces: `agent_setup <harness>` in `lib/agents.sh`: `agent_state setup` holding
  `$BOSUN_STATE_DIR/agent-setup.lock` (`lib/run_locked.py`, waiting up to 60 s). `bosun launch` runs it after
  `set_agent_args` and before the agent starts (not in a dry run), and a failure stops the launch: `'<window>'
  does not start: <why>`. `agent-setup.lock` gets a `STATE_FILES` row.
- Produces: `update_agents` in `lib/agents.sh`: for each harness in `harnesses_in_use` whose `update_args` is
  not empty, runs `<its command> <update_args>` with `run_with_timeout 300`, logging the outcome; a failure is a
  problem line in the morning's notification and stops nothing. `bosun morning` runs it after its setup step and
  before starting or refreshing the windows; with `--dry-run` it says what it would run.

- [ ] **Step 1: Write the tests**: `test_setups_are_recorded_and_forgotten` (agent_changes);
  `test_a_recorded_setup_is_an_uninstall_step` and its text (uninstall_plan);
  `test_uninstall_undoes_a_setup_and_forgets_it`, and `test_a_setup_whose_undo_fails_stays_recorded` (uninstall,
  with a stub agent); `test_setup_applies_only_when_needed` (agent_state CLI with a stub: `needed` false calls
  nothing; true calls `apply` once and records); `test_a_failed_setup_says_why` (exit 1, the text);
  `test_profiles_print_update_args` (claude and codex: `-`). Smoke: `bosun morning --dry-run` names no agent
  update when no agent in use has `update_args`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** as in Interfaces. No agent has a setup or `update_args` yet, so nothing runs for
  Claude Code or Codex.
- [ ] **Step 4: Run** the unit files above, `tests/unit/test_agent_names.py`, and the smoke suite once.
- [ ] **Step 5: Commit** ("Agents: a one-time setup bosun records and uninstall takes back, and the morning's update").

### Task 4: Reading `agy`'s screen

**Files:**

- Create: `lib/agy_state.py` (its docstring, after `lib/codex_state.py`'s: what bosun relies on in `agy` 1.5.5,
  from the VM run; the screen part only in this task)
- Test: `tests/unit/test_agy_state.py`, against `tests/fixtures/agy/screens/`

**Interfaces:**

- Produces: `agy_typed_text(lines: list[str]) -> str | None`: the text in the box (the line starting with `>`
  between two lines of `─`, and the lines under it down to the second rule), `""` when it is empty, `None` with no
  box. `agy_question(capture: str) -> bool`: a dialog (`↑/↓ Navigate`), the trust screen (`Do you trust the
  contents of this project?`) or the sign-in screen (`You are currently not signed in`).
  `screen_status(capture: str) -> str | None`: `"waiting"` for a dialog; `"shell"` for the idle hint
  `? for shortcuts` over an empty box with `task(s)` in the footer; `"idle"` for the hint over an empty box;
  `None` for anything else (busy, typed text, a queued message). `SCREEN = Screen(agy_typed_text, agy_question,
  never_rejects)`.

- [ ] **Step 1: Write the tests**, one per capture: `idle.ansi` (typed `""`, status `idle`, no question);
  `typed.ansi` (typed `"Remember the word PELICAN for later.\nReply with just the word noted."`, status `None`);
  `busy.ansi` (status `None`); `dialog.ansi` (question, status `waiting`); `trust.ansi` and `sign-in.ansi`
  (question); `queued.ansi` (status `None`: a turn is running); `task.ansi` (status `shell`); `cancelled.txt`
  (status `idle`: a turn cancelled with Esc). Read the `.ansi` files as `tmux capture-pane -e` gives them, and a
  plain capture for `agy_question`, as `pane_input` does.
- [ ] **Step 2: Run them to see them fail:** `uv run python -m pytest -q tests/unit/test_agy_state.py </dev/null`.
- [ ] **Step 3: Implement the readers** with `lib/screen_text.py`'s helpers (`visible`, `is_rule`, `box_lines`
  with the mark `>`). The dialog is checked before anything else: its footer also says `esc to cancel`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** ("agy: its screen, read from what the real one showed").

### Task 5: The `agy` adapter

**Files:**

- Modify: `lib/agy_state.py` (the rest of `Agent`'s functions, its names table and status files, the login
  check, its CLI)
- Test: `tests/unit/test_agy_state.py`

**Interfaces:**

- Consumes: `screen_status` (Task 4).
- Produces: `HARNESS = "agy"`, `BIN_KEY = "BOSUN_AGY_BIN"`, `AGY_HOME = ~/.gemini`, `CLI_HOME = AGY_HOME /
  "antigravity-cli"`, `NAMES_FILE = "agy-sessions.json"`, `STATUS_DIR = "agy"` (in bosun's state folder).
- Produces: the names table, as Codex's: `remember(name, conversation_id, workdir)`, `known_names()`,
  `resolve(workdir, name) -> str | None` (the id only if `CLI_HOME/conversations/<id>.db` is there).
- Produces: the status file `<state>/agy/<conversation name>.json` = `{"status", "statusUpdatedAt" (ms),
  "conversationId", "version"}`: `write_status(name, **fields)` (merges, atomic), `read_status(name)`.
- Produces: `marker(pid) -> Marker | None`, from one `ps eww -o command= -p <pid>`: the conversation name from
  `--log-file .../agy-<name>.log`, and `TMUX`, `TMUX_PANE` from the environment. `record(pid)` builds Claude
  Code's record shape, as `codex_state.build_record` does, from the status file: `None` with no marker or no
  status file with a `conversationId` (a window not yet at its first prompt). While its status is busy, it
  captures the pane (`tmux -S <socket from TMUX> capture-pane -e -p -t <TMUX_PANE>`) and takes `screen_status`'s
  answer when there is one; for `idle` and `shell` it writes it to the status file with the time it saw it. No
  pane, or a capture that fails: busy, as the file says. The record's `version` is the status file's: the
  version the window started on, which `bosun refresh` compares with the installed one. `registered`, `describe`, `running`, `live_pid`
  (a running window whose `conversationId` is the id), `live_status`, `launch_settings` (`--model`,
  `--effort`, and `by_bosun` from the marker) as Codex's do. `conversation_size`: `None`.
  `trusted`: `True` (each launch passes its folder), `trust_recorded`: `True`, `set_trust`: nothing.
  `prompt_history`: nothing (its history file is undocumented).
- Produces: `logged_in() -> bool`: `security find-generic-password -s gemini` exits 0 (never `-w` or `-g`), and
  `CLI_HOME/cache/onboarding.json` holds `"onboardingComplete": true`. `ready()`: its command is on PATH and
  `logged_in()`. CLI: `agy_state.py logged-in | resolve <workdir> <name> | live <id>`, exit 3 for not found.

- [ ] **Step 1: Write the tests** with a scratch `CLI_HOME`, state folder and `ps`/`tmux`/`security` faked
  (patched functions, never the real commands): `test_resolve_needs_the_conversation_on_disk`;
  `test_record_comes_from_the_status_file`; `test_a_window_before_its_first_prompt_has_no_record`;
  `test_two_windows_in_one_repo_keep_their_own_status`; `test_a_busy_window_whose_screen_is_idle_is_idle` (and
  the time written); `test_a_busy_window_at_a_dialog_is_waiting` (not written: the next look reads the screen
  again); `test_a_busy_status_with_no_screen_stays_busy`; `test_launch_settings_read_the_command_line`;
  `test_logged_in_needs_the_keychain_item_and_onboarding` (the `security` call has no `-w`/`-g`);
  `test_trust_is_never_written`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the functions above. Do not register `agy` in `AGENTS` yet (Task 8 does, once its
  launch file, connectors module and preflight profile exist).
- [ ] **Step 4: Run** `tests/unit/test_agy_state.py` and `uv run mypy lib`. Expected: PASS.
- [ ] **Step 5: Commit** ("agy: the adapter, its names, its status from hooks and screen").

### Task 6: Holding an `agy` window to its connectors

**Files:**

- Create: `lib/connectors_agy.py` (`main`, `doctor_line`, as `lib/connectors_codex.py`'s)
- Test: `tests/unit/test_connectors_agy.py`

**Interfaces:**

- Consumes: `connectors.parse`, `connectors.Allowed`, the report helpers.
- Produces: `servers(workdir: str, agent_bin: str) -> list[str]`: every server `agy` would start in `workdir`,
  each once: the enabled ones (`"disabled"` not true) of `~/.gemini/config/mcp_config.json` (`mcpServers`), of
  each `.agents/mcp_config.json` from `workdir` up to its repo's root, and the plugins' (`agy plugin list
  --json`, each plugin's `mcpServers`, named `<plugin>_<server>`). A file that does not parse, or a listing that
  fails or times out (30 s): `ConnectorsError`.
- Produces: `main(argv)`: `--agent-bin <bin> --list=<list>`; exit 0, printing nothing, when the list is `all` or
  names every server; otherwise exit 1, one line per server the list does not name, in the style of Codex's
  refusal lines. `doctor_line(args, allowed)` gives `bosun doctor`'s line for the window.

- [ ] **Step 1: Write the tests** with scratch config files and a fake `agy` (a script printing a fixed `plugin
  list --json`): `test_no_servers_starts`; `test_a_global_server_not_listed_refuses`; `test_a_disabled_server_is_not_counted`;
  `test_a_workspace_server_counts`, found from a folder below the repo's root; `test_a_plugins_server_counts_by_its_full_name`;
  `test_all_allows_anything`; `test_a_listing_that_fails_refuses`; `test_doctor_names_what_blocks`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** `lib/connectors_agy.py`. It writes nothing.
- [ ] **Step 4: Run** the tests and `uv run mypy lib`.
- [ ] **Step 5: Commit** ("agy: a window starts only if it reaches no server beyond its list").

### Task 7: bosun's plugin and its hooks

**Files:**

- Create: `agents/agy-plugin/plugin.json` (`{"name": "bosun", "version": "<PLUGIN_VERSION>"}`),
  `agents/agy-plugin/hooks.json`
- Modify: `lib/agy_state.py` (`HOOKS`, `SETUP`, `PLUGIN_VERSION`)
- Test: `tests/unit/test_agy_state.py`, `tests/unit/test_agy_plugin.py`, against `tests/fixtures/agy/hooks/`

**Interfaces:**

- Consumes: `AgentHooks`, `AgentSetup` (Tasks 2, 3), `write_status`, `remember` (Task 5).
- Produces: `hooks.json` with one named hook `bosun` holding `SessionStart`, `PreInvocation` and `Stop` (flat
  lists) and `PreToolUse` (grouped, `"matcher": "run_command"`), each one command:
  `sh -c '[ -n "${BOSUN_HOOK_CMD:-}" ] || exit 0; exec "$BOSUN_HOOK_CMD" hook agent --harness agy --event <Event>'`.
- Produces: `HOOKS = AgentHooks(("SessionStart", "PreInvocation", "Stop", "PreToolUse"), answer)`, where `answer`:
  - `SessionStart`: `remember(env["BOSUN_CONVERSATION"], payload["conversationId"], <folder>)`, the folder
    being `cwd`, or the first of `workspacePaths` when `cwd` is empty (seen after a trust screen, which a bosun
    launch never shows); with neither, an ERROR line and nothing remembered. Prints nothing.
  - `PreInvocation`: with `BOSUN_RESUMED` set and the payload's `conversationId` not that id, the termination
    answer (Global Constraints) saying the window resumed another conversation, and an ERROR line plus a bosun
    alert; otherwise the status busy (with `conversationId`) and, with events on, the payload's `lastUserInput`
    recorded as said in the window when it is not the last one recorded; prints nothing.
  - `Stop`: idle, or `shell` when `fullyIdle` is false; prints nothing.
  - `PreToolUse`: the approval (Global Constraints) for a `run_command` whose command is `git`, `gh` or `bosun`
    (`lib/guard.py`'s `simple_commands` reads it: every simple command in it must be `git`, `gh`, `bosun` or
    `cd`, and at least one of the first three); nothing for anything else.
- Produces: `SETUP = AgentSetup("bosun's plugin in agy", needed, apply, undo)`: `needed` when
  `~/.gemini/config/plugins/bosun/plugin.json` is missing or holds another version; `apply` runs `agy plugin
  install <BOSUN_INSTALL>/agents/agy-plugin` (a `SetupError` with its output when it fails); `undo` runs `agy
  plugin uninstall bosun`, then removes `~/.gemini/antigravity-cli/plugin_data/bosun/` and
  `~/.gemini/config/import_manifest.json` when it holds no imports.

- [ ] **Step 1: Write the tests**: `test_every_hook_is_silent_outside_a_bosun_window` (each command in
  `hooks.json`, run with `sh` and no `BOSUN_HOOK_CMD`: stdout empty, exit 0);
  `test_session_start_remembers_the_conversation` (`session-start.json`);
  `test_pre_invocation_marks_busy` (`pre-invocation.json`); `test_pre_invocation_stops_a_resume_that_got_another_conversation`
  (`pre-invocation-resumed.json` with `BOSUN_RESUMED` set to another id: the exact termination answer);
  `test_pre_invocation_lets_its_own_resume_through`; `test_stop_is_idle_or_shell` (`stop.json`,
  `stop-not-fully-idle.json`); `test_git_gh_and_bosun_are_approved` (`git push origin feature`, `cd repo && gh pr
  view 5`, `bosun ledger list`); `test_other_commands_get_no_answer` (`uname -s` from `pre-tool-use.json`, `curl
  https://example.com`, `git status; rm -rf x`); `test_the_plugin_validates` (`plugin.json` has `name` and
  `version`; `hooks.json` parses and names only these events); `test_setup_installs_when_missing_or_older` and
  `test_undo_leaves_nothing_of_bosun` (fake `agy` and a scratch `~/.gemini`).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the plugin files and `HOOKS`, `SETUP`. Said-recording reuses `hook.record_prompt`'s
  path (`lib/activity.py`), forked so the hook returns at once.
- [ ] **Step 4: Run** the tests, `uv run mypy lib`.
- [ ] **Step 5: Commit** ("agy: bosun's plugin, its hooks, and the commands it lets past the sandbox").

### Task 8: Starting a window on `agy`

**Files:**

- Create: `lib/launch/agy.sh` (`build_agent_args`, `first_prompt`, `run_new`, `run_resumed`, as
  `lib/launch/codex.sh`'s)
- Create: `lib/harnesses/agy.sh` (the preflight profile)
- Modify: `lib/agy_state.py` (`PROFILE`, complete)
- Modify: `lib/agent_state.py` (`AGENTS` gains `agy`), `lib/common.sh` and `examples/generic/bosun.env` (the
  three keys)
- Modify: the texts that list the agents: `bin/bosun`'s usage, `libexec/bosun-context`, `lib/uninstall_plan.py`
- Test: `tests/unit/test_agent_profiles.py`, `tests/unit/test_agy_state.py`, `tests/smoke.sh`

**Interfaces:**

- Produces: `PROFILE = Profile(bin_key=BIN_KEY, flags_key="BOSUN_AGY_FLAGS", model_key="BOSUN_AGY_MODEL",
  names_sessions=False, statuses=frozenset({"idle", "busy", "shell", "waiting"}), remote_control=True,
  autocompact=False, subagents=False, state_files=(NAMES_FILE, STATUS_DIR), display_name="Antigravity",
  setup_preview=(...), screen=SCREEN, connectors="connectors_agy", setup_sessions=..., setup_note=...,
  hooks=HOOKS, setup=SETUP, update_args=("update",))`. `display_name` is also `harness_name` in
  `lib/harnesses/agy.sh`. The setup texts say: repo sessions run on Antigravity in `auto`, the orchestrator stays
  on Claude Code, and what `agy` windows go without.
- Produces: `build_agent_args <window> <session name> <model> <effort>`: exports `BOSUN_WINDOW`,
  `BOSUN_CONVERSATION=<session name>`, `BOSUN_HOOK_CMD=$BOSUN_CMD` and `AGY_CLI_DISABLE_AUTO_UPDATE=true`;
  unsets `SSH_CLIENT` and `SSH_CONNECTION`; writes the status file's `version` (`agent_installed_version agy`);
  runs the connectors check (`lib/connectors_agy.py`, in the launch's own shell; a refusal or a failed listing
  stops the launch, as on Codex); `agent_args` = `$BOSUN_AGY_FLAGS --settings <{"agentMode":"auto",
  "trustedWorkspaces":[<the folder as given>]}> --log-file $BOSUN_LOG_DIR/agy-<session name>.log
  --remote-control`, plus `--model <base name>` (a trailing `-low`/`-medium`/`-high` taken off) and `--effort`
  (`xhigh` and `max` as `high`) when set.
- Produces: `run_new <prompt>`: `run_agent "${agent_args[@]}" "--prompt-interactive=<prompt>"`. `run_resumed <id>
  [prompt]`: exports `BOSUN_RESUMED=<id>`, then `run_agent "${agent_args[@]}" --conversation <id>` with the prompt
  as `--prompt-interactive=<prompt>` when given. `first_prompt` as Codex's: a new conversation always gets one.
- Produces: `lib/harnesses/agy.sh`: `harness_name` `Antigravity`, `harness_command` `${BOSUN_AGY_BIN:-agy}`,
  `harness_install_help` (the installer line, and that it does not put `~/.local/bin` on PATH),
  `harness_upgrade_help` (`agy update`), `harness_min_version` `1.5.5`, `harness_version` (the bare first line),
  `harness_checks` (`agy_state.py logged-in`; when it fails over ssh, it says the login is only seen in a desktop
  session).

- [ ] **Step 1: Write the tests**: `tests/unit/test_agent_profiles.py` gains agy's values (the TSV line
  `agy\tBOSUN_AGY_BIN\tBOSUN_AGY_FLAGS\tBOSUN_AGY_MODEL\tno\tyes\tno\tno\tidle,busy,shell,waiting\tupdate`); the
  smoke suite gains a section on a fake `agy` (`BOSUN_AGY_BIN`): `bosun launch` with `BOSUN_LAUNCH_DRY_RUN=1`
  prints `--settings` holding `"agentMode":"auto"` and the folder, `--log-file`, `--prompt-interactive=` and no
  `--add-dir`/`--mode`/`-c`; a resume prints `--conversation <id>`; an effort of `xhigh` prints `--effort high`;
  a model `gemini-3.8-flash-low` prints `--model gemini-3.8-flash`; `bosun morning --dry-run` with a window on
  `agy` says it would run `agy update`. Change the unknown-agent wording tests (`no agent 'acme-agent': bosun knows
  claude, codex, agy`) where they list the known ids.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the launch file, the preflight profile, the profile and the config keys; register
  `agy` in `AGENTS`; update the three texts (from `known_harnesses` or the profiles where they list agents).
- [ ] **Step 4: Run** the unit suite, `uv run pre-commit run --all-files`, and the smoke suite.
- [ ] **Step 5: Commit** ("agy: a repo window can run on Antigravity").

### Task 9: The stand-in, and the first day on `agy`

**Files:**

- Create: `tests/sandbox/agy-stand-in` (as `tests/sandbox/codex-stand-in`)
- Modify: `tests/smoke.sh` (a section with real windows on the stand-in), `tests/sandbox/fresh_user.sh`
  (`the_first_day_on_agy`)

**Interfaces:**

- Produces: a stand-in that does what the VM run showed and nothing more: `--version` prints `1.5.5`; `models`,
  `plugin install|uninstall|list --json` (into its own `~/.gemini`); an interactive session in the pane that
  draws the screens of `tests/fixtures/agy/screens/` (idle, busy, a dialog on a command named `ask`), takes
  `--prompt-interactive=`, `--conversation <id>` (an unknown id: a warning and a fresh conversation), `--settings`
  (refusing an unknown key, as `agy` does), writes `conversations/<id>.db` and `presence/<id>.lock`, and runs the
  installed plugins' hooks with the fixtures' payload shapes, honouring their answers (a termination stops the
  turn; the approval runs the command). Esc cancels a turn with no hook.
- Produces: smoke checks on the stand-in: a window starts and records its name; a restart resumes the same
  conversation; a window whose conversation is gone starts a new one, never under the old id silently; status
  goes busy and idle from the hooks; Esc in a busy window reads idle; a dialog reads `waiting` and `bosun send`
  refuses it; a person's own `agy` run (no `BOSUN_HOOK_CMD`) gets no answer from any hook; `bosun uninstall`
  takes the plugin back.
- Produces: `the_first_day_on_agy` in `tests/sandbox/fresh_user.sh`: the same steps as `the_first_day_on_codex`,
  on the stand-in.

- [ ] **Step 1: Write the stand-in and the checks** (the checks fail against nothing).
- [ ] **Step 2: Run** `tests/smoke.sh </dev/null` and `tests/sandbox.sh init </dev/null`. Expected: `N passed, 0
  failed` each.
- [ ] **Step 3: Commit** ("Tests: an agy stand-in that does what the real one showed, and its first day").

### Task 10: The live run on the real `agy`

**Files:**

- Modify: `tests/sandbox/mac.sh` (`live agy`), `tests/sandbox/live.sh` (its `agy` steps)

**Interfaces:**

- Produces: `tests/sandbox.sh mac live agy`: boots the clone with its window (not `--no-graphics`), installs
  `agy` with its own installer, prints what to do (sign in, then quit `agy`), and waits up to 15 minutes for
  the Keychain item `gemini` (looked up without reading it) and `onboardingComplete`; then the Anthropic key goes
  in as for the others (the orchestrator stays on Claude Code). bosun's windows start from its launchd job
  (`launchctl kickstart`), never from the ssh session, because `agy` sees its login only in the desktop session.
  The clone is deleted when it ends, as now.
- Produces: `live.sh` steps for `agy`, proving each line of the design note's Testing list: a window started,
  resumed and refreshed by bosun end to end; Esc read as idle; a reviewer dialog (a push to `main` of a local
  bare remote) read as `waiting`; `--remote-control` starting; the login with `SSH_AUTH_SOCK` set; a `git push`
  of a feature branch and a `gh --version` approved and an `ls` left alone (the hook log); a hook slower than
  `agy`'s timeout; the plugin's install and uninstall leaving nothing else (the file tree before and after); the
  connector refusal with a server set up and with none; `bosun morning`'s `agy update` with automatic updates
  off.

- [ ] **Step 1: Write the steps.**
- [ ] **Step 2: Run** `tests/sandbox.sh mac live agy` (about 20 minutes and a person's sign-in), then
  `tests/sandbox.sh mac live claude` and `tests/sandbox.sh mac live codex`. Expected: `N passed, 0 failed` each;
  claude and codex as before this change (73 and 96).
- [ ] **Step 3: Teach the stand-in** anything the run shows that it does not do, and record it in
  `lib/agy_state.py`'s docstring.
- [ ] **Step 4: Commit** ("Tests: the live run on the real agy").

### Task 11: The docs

**Files:**

- Modify: `docs/design/harnesses.md` (an `agy` column in what windows lack; the facts the run found),
  `docs/setup.md` (running repo sessions on Antigravity: install, sign in once in the desktop, the risk), the
  README's Status section, `docs/limits.md`, `docs/commands.md` (`bosun hook agent`), `ROADMAP.md` (Part 2 done;
  OpenCode and Copilot CLI next), `CLAUDE.md` (the agents bullet: three adapters, the plugin, "A Codex fact
  changes" gains its `agy` twin), `docs/design/antigravity.md` (status), `docs/design/uninstall.md` (the setup
  step)

- [ ] **Step 1: Update the docs**, and grep for "Claude Code or Codex", "claude or codex", "two agents" and
  "both agents" across the README, `docs/`, `ROADMAP.md`, `SECURITY.md`, `CONTRIBUTING.md`, `examples/` and
  `.github/` templates; fix each that now misdescribes.
- [ ] **Step 2: Run** `uv run pre-commit run --all-files </dev/null`. Expected: all hooks pass.
- [ ] **Step 3: Commit** ("Docs: windows on Antigravity") and open the pull request: what an `agy` window does and
  goes without, the shared changes and that Claude Code and Codex are unchanged, and the live runs' counts.
