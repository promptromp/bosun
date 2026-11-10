# Agents that say what they can do: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Each agent module declares what it is and can do, so that no shared code names `claude` or `codex`,
with no change in what Claude Code and Codex windows do.

**Architecture:** A frozen `Profile` per agent module (`lib/claude_state.py`, `lib/codex_state.py`) is the one
source; Python asks `agent_state.AGENTS[h].PROFILE`, and the shell reads every profile through one cached
`agent_state.py profiles` call per command. The per-agent parts of the screen readers, the connectors and the
turn-end hook move into per-agent code that a profile names. A unit test keeps agent names out of shared code.

**Tech Stack:** Python 3.13 (mypy strict, ruff, complexity limits), bash 3.2-compatible shell, Ruby one-liners
in `window_specs`, the repo's unit tests, `tests/smoke.sh`, the sandboxes.

**Spec:** [docs/design/antigravity.md](../design/antigravity.md), "Part 1: agents that say what they can do".
Part 2 (`agy` windows) gets its own plan once a discovery run in the macOS VM has settled what the note leaves
to it.

## Global Constraints

- No change in behaviour for Claude Code and Codex windows; every existing test passes unchanged except where
  a step names the test it moves or renames.

- Config keys keep their names and defaults (`BOSUN_CLAUDE_BIN`, `BOSUN_CLAUDE_FLAGS`, `BOSUN_MODEL`,
  `BOSUN_CODEX_BIN`, `BOSUN_CODEX_FLAGS`, `BOSUN_CODEX_MODEL`); their defaults stay in `lib/common.sh`.

- What only Claude Code has (the orchestrator, its login checks, the headless jobs, the transcripts Impact and
  learning read, `PERMISSION_FLAGS`) stays named: the note allows it.

- `lib/agent_files.py` stays shared by the adapters, and no adapter imports another.
- `/bin/bash` 3.2: no associative arrays, `mapfile` or `${x^^}`; empty arrays guarded under `set -u`.
- Replace `libexec/` scripts atomically (temp file, then `mv`).
- The repo's limits hold: mypy strict, ruff, complexipy <= 15, lizard CCN <= 10 and <= 60 lines,
  `tools/shell_complexity.py`; split a function rather than raise a limit.

- Docs change in the same pull request (CLAUDE.md, `docs/design/harnesses.md`).

## Review Focus

1. A Codex window started before this change still runs `bosun hook codex-turn-end ...` (its `notify=` was
   fixed at launch): it must keep recording names and what was said. Task 7 tests the alias.
2. A window naming an agent bosun does not know (`harness: agy` in `sessions.yml` before Part 2) must be
   refused naming the agents bosun knows, never run on Claude Code as `harness_bin`'s fallback does today.
   Task 2 tests it.
3. The profiles lookup failing (a Python error) must stop a command that needs an agent's binary with a line
   that says so, never leave an empty binary to run. Task 2 tests it with a failing `python3` on PATH.
4. An environment override of a binary (`BOSUN_CODEX_BIN=/opt/acme/codex`) must reach `harness_bin`,
   `agent_pid_starting` and the Python adapters, as today. Task 2 tests it.
5. CI's `/bin/bash` 3.2 runs the smoke suite: the profile cache must work there. Task 2's smoke checks run in
   CI's smoke job.

---

### Task 1: Profiles in the agent modules

**Files:**

- Modify: `lib/agent_files.py` (the `Profile` dataclass)
- Modify: `lib/claude_state.py`, `lib/codex_state.py` (a `PROFILE` beside `HARNESS`)
- Modify: `lib/agent_state.py` (`PROFILE` in `Agent`, `DEFAULT_HARNESS`, the `profiles` command, usage text)
- Test: `tests/unit/test_agent_profiles.py` (new)

**Interfaces:**

- Produces: `agent_files.Profile` (frozen dataclass) with fields `bin_key: str`, `flags_key: str`,
  `model_key: str`, `names_sessions: bool`, `statuses: frozenset[str]`, `remote_control: bool`,
  `autocompact: bool`, `subagents: bool`, `state_files: tuple[str, ...]`, `setup_sessions: str = ""`,
  `setup_note: str = ""`. Tasks 5 and 6 add `screen` and `connectors`, required, so they go before the two
  fields with defaults (a dataclass refuses a field without a default after one with); Task 7 adds
  `turn_end`, with a default, last.

- Produces: `agent_state.DEFAULT_HARNESS: str` (Claude Code's `HARNESS`: the agent a window runs when nothing
  says otherwise) and `agent_state.py profiles`, printing one tab-separated line per agent:
  `harness bin_key flags_key model_key names_sessions remote_control autocompact subagents statuses`, booleans
  as `yes`/`no`, statuses comma-separated in the order `idle,busy,shell,waiting`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_agent_profiles.py`:
  - `test_each_agent_has_a_profile`: for every `h, agent` in `AGENTS`, `agent.PROFILE` is a `Profile`, and
    `{"idle", "busy"} <= agent.PROFILE.statuses <= {"idle", "busy", "shell", "waiting"}`.

  - `test_the_profiles_say_what_each_agent_does`: Claude Code's is
    `("BOSUN_CLAUDE_BIN", "BOSUN_CLAUDE_FLAGS", "BOSUN_MODEL")`, `names_sessions`, all four statuses,
    `remote_control`, `autocompact` and `subagents` true, `state_files == ()`; Codex's is
    `("BOSUN_CODEX_BIN", "BOSUN_CODEX_FLAGS", "BOSUN_CODEX_MODEL")`, `names_sessions` false,
    `statuses == {"idle", "busy"}`, the three capabilities false, `state_files == ("codex-sessions.json",)`,
    and its `setup_sessions`/`setup_note` are `init_plan.OTHER_AGENTS["codex"]`'s two texts (copied in
    Task 4).

  - `test_every_state_file_is_in_the_tables`: each name in any profile's `state_files` is a pattern in
    `uninstall_plan.STATE_FILES` and in `update_backups.IN_FLIGHT`.

  - `test_the_profiles_command`: `main(["profiles"])` prints exactly
    `"claude\tBOSUN_CLAUDE_BIN\tBOSUN_CLAUDE_FLAGS\tBOSUN_MODEL\tyes\tyes\tyes\tyes\tidle,busy,shell,waiting"`
    and `"codex\tBOSUN_CODEX_BIN\tBOSUN_CODEX_FLAGS\tBOSUN_CODEX_MODEL\tno\tno\tno\tno\tidle,busy"`, in
    `AGENTS` order, and exits 0.

  - `test_the_default_agent`: `DEFAULT_HARNESS == claude_state.HARNESS`.

- [ ] **Step 2: Run them to see them fail**
  Run: `uv run python -m pytest -q tests/unit/test_agent_profiles.py </dev/null`
  Expected: FAIL (no `Profile`, no `PROFILE`).

- [ ] **Step 3: Add `Profile` to `lib/agent_files.py`, a `PROFILE` to each agent module with the values above,
  `PROFILE: Profile` to `Agent`, `DEFAULT_HARNESS` and the `profiles` command to `lib/agent_state.py`** (its
  usage text gains `agent_state.py profiles   every agent's profile, a line each`).

- [ ] **Step 4: Run the tests to see them pass, and mypy**
  Run: `uv run python -m pytest -q tests/unit/test_agent_profiles.py </dev/null && uv run mypy lib`
  Expected: PASS, and mypy clean.

- [ ] **Step 5: Commit** (`git add` the five files; message "Agents: a profile each, saying what it is and can
  do", with the Co-Authored-By line).

### Task 2: The shell asks the profiles

**Files:**

- Modify: `lib/agents.sh` (`agent_profiles`, `profile_field`, `known_harnesses`, `agent_bin_env`;
  `harness_bin`, `harness_default_model`, `agent_state`, `run_preflight`, `from_profile`, `agent_pid_starting`)

- Modify: `lib/common.sh` (`window_specs`'s Ruby `default_model`)
- Modify: `libexec/bosun-launch` (refuse an agent with no profile)
- Test: `tests/smoke.sh` (checks in its "lib/common.sh functions" section)

**Interfaces:**

- Consumes: `agent_state.py profiles` (Task 1).
- Produces (shell):
  - `agent_profiles`: the `profiles` lines, read once per process into `AGENT_PROFILES`; on a failure it dies
    with "could not read the agents' profiles (lib/agent_state.py profiles): <its stderr>".

  - `profile_field <harness> <field>`: the field by its column name above; exit 1, printing nothing, for a
    harness with no profile.

  - `known_harnesses`: the harness ids, space-separated, in `AGENTS` order.
  - `agent_bin_env`: sets the array `agent_bin_assignments` to `KEY=value` for every profile's `bin_key`
    (what `agent_state`, `run_preflight` and `from_profile` pass with `env`, since `load_config` exports
    nothing).

  - `harness_bin <h>` and `harness_default_model <h>` keep their names and output; for a harness with no
    profile both print nothing and return 1.

- [ ] **Step 1: Write the failing smoke checks**, in the "lib/common.sh functions" section, using its `fn`
  helper:

  - `harness_bin codex` prints `codex`; with `BOSUN_CODEX_BIN=/opt/acme/codex`, `/opt/acme/codex`.
  - `harness_default_model codex` prints `-`; with `BOSUN_CODEX_MODEL=gpt-acme`, `gpt-acme`;
    `harness_default_model claude` prints `$BOSUN_MODEL`'s value or `-`.

  - `harness_bin agy` exits 1 and prints nothing.
  - `known_harnesses` prints `claude codex`.
  - with a `python3` earlier on PATH that exits 1 (a script in `$WORK/broken-python/`), `harness_bin claude`
    fails with "could not read the agents' profiles".

  - `window_specs` for a `sessions.yml` with a Codex window and no model gives that window
    `$BOSUN_CODEX_MODEL`'s value (`-` when empty), and a Claude Code window `$BOSUN_MODEL`'s.

  - `bosun launch` of a window with `harness: agy` fails with
    "no agent 'agy': bosun knows claude, codex" (dry run, `BOSUN_LAUNCH_DRY_RUN=1`).

- [ ] **Step 2: Run the smoke suite to see them fail**
  Run: `tests/smoke.sh </dev/null 2>&1 | grep -E 'FAIL|passed,'`
  Expected: the new checks FAIL, every other check passes.

- [ ] **Step 3: Implement the shell functions in `lib/agents.sh`**: the cache is a string variable read
  with `awk -F'\t'` (no associative array); `harness_bin` reads `${!key}` for the profile's `bin_key`,
  `harness_default_model` `${!key:--}` for its `model_key`; `agent_pid_starting` builds its list from
  `harness_bin` of every `known_harnesses`; `agent_state`, `run_preflight` and `from_profile` pass
  `env "${agent_bin_assignments[@]}"`.

- [ ] **Step 4: `window_specs`**: pass the Ruby one `harness=model` per known harness (from
  `harness_default_model`) after its fixed arguments, and build `default_model` from them, so the hash names
  no agent.

- [ ] **Step 5: `bosun launch` refuses** a window whose harness has no profile, with
  "no agent '<h>': bosun knows <known_harnesses joined with ', '>", before it reads `lib/launch/<h>.sh`.

- [ ] **Step 6: Run the smoke suite, the unit tests and shellcheck**
  Run: `tests/smoke.sh </dev/null 2>&1 | tail -1 && uv run pre-commit run shellcheck --all-files </dev/null`
  Expected: `N passed, 0 failed`, shellcheck passed.

- [ ] **Step 7: Commit** ("Agents: the shell reads each agent's profile, and no case on its name").

### Task 3: Per-task subagents from the profile

**Files:**

- Modify: `lib/delegate.py` (drop `SUBAGENT_HARNESS`)
- Test: `tests/unit/test_delegate.py`

**Interfaces:**

- Consumes: `agent_state.AGENTS`, `agent_state.DEFAULT_HARNESS`, `Profile.subagents` (Task 1).

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_delegate.py`:
  - `test_a_window_whose_agent_has_subagents_gets_the_preamble`: with `window_harness` patched to return
    `"claude"`, `can_use_subagents` is true; `"codex"`, false; `"agy"` (no profile), false.

  - `test_an_unreadable_session_list_falls_back_to_the_default_agent`: `window_harness` with `bosun sessions
    --json` printing `not json` returns `agent_state.DEFAULT_HARNESS`.

- [ ] **Step 2: Run them to see them fail**
  Run: `uv run python -m pytest -q tests/unit/test_delegate.py </dev/null`
  Expected: FAIL where they name the new behaviour (the unknown agent, the default).

- [ ] **Step 3: `window_harness` falls back to `agent_state.DEFAULT_HARNESS`; `can_use_subagents` is true
  when there is no target, else when the target's agent has a profile whose `subagents` is true.** The
  comment over `subagent_preamble` says it names Claude Code's Agent tool, which only an agent with
  `subagents` has.

- [ ] **Step 4: Run the tests to see them pass**
  Run: `uv run python -m pytest -q tests/unit/test_delegate.py </dev/null`
  Expected: PASS.

- [ ] **Step 5: Commit** ("Delegate: a task's own settings reach a window whose agent has subagents").

### Task 4: The setup wizard asks the agents

**Files:**

- Modify: `lib/init_plan.py` (drop `CLAUDE`, `CODEX`, `OTHER_AGENTS`; `Answers.harness` defaults to
  `agent_state.DEFAULT_HARNESS`)

- Modify: `lib/codex_state.py` (its `setup_sessions` and `setup_note`, moved from `OTHER_AGENTS`)
- Test: `tests/unit/test_init_plan.py`

**Interfaces:**

- Consumes: `agent_state.AGENTS`, `DEFAULT_HARNESS`, `Profile.setup_sessions`, `Profile.setup_note`.

- [ ] **Step 1: Write the tests** in `tests/unit/test_init_plan.py`:
  - `test_any_agent_with_a_profile_is_an_answer`: answers with `harness: "codex"` have no problems; with
    `"agy"`, the problem `"harness: 'agy' must be claude or codex"` (the known ids, sorted, joined with
    `" or "`). It passes before the change too: it guards the wording through it.

  - `test_an_agent_other_than_the_default_says_what_its_windows_run`: with `codex_state.PROFILE` patched
    (`dataclasses.replace`) to `setup_sessions="Acme sessions"` and `setup_note="Acme note."`, the plan for
    `harness: "codex"` shows both.

  - In `tests/unit/test_agent_profiles.py`, Task 1's comparison with `OTHER_AGENTS["codex"]` becomes a check
    that Codex's `setup_note` starts with "Repo sessions run on Codex" and `setup_sessions` is
    "Codex, with its automatic reviewer".

- [ ] **Step 2: Run them to see the second fail**
  Run: `uv run python -m pytest -q tests/unit/test_init_plan.py tests/unit/test_agent_profiles.py </dev/null`
  Expected: the plan test FAILS (`init_plan` still shows `OTHER_AGENTS`' text); the others pass.

- [ ] **Step 3: `init_plan` reads every agent from `agent_state.AGENTS`**: the harness check, the default
  (`DEFAULT_HARNESS`), the most-used agent's fallback, and the windows' text from the chosen agent's profile
  when it is not the default. `REQUIRED_TOOLS` and `PERMISSION_FLAGS` stay (Claude Code's own, for the
  orchestrator).

- [ ] **Step 4: Run the init tests to see them pass**
  Run: `uv run python -m pytest -q tests/unit/test_init_plan.py tests/unit/test_init_wizard.py tests/unit/test_init.py </dev/null`
  Expected: PASS.

- [ ] **Step 5: Commit** ("Init: every agent with a profile is an answer, and says what its windows run").

### Task 5: Each agent reads its own screen

**Files:**

- Create: `lib/screen_text.py` (the helpers both agents' readers use, moved from `lib/pane_input.py`: `SGR`,
  `NBSP`, `visible`, `is_rule`, `box_lines`, `extended_faint`, `faint_after`)

- Modify: `lib/agent_files.py` (`Screen`; `Profile.screen`)
- Modify: `lib/claude_state.py`, `lib/codex_state.py` (their readers and marks, moved from `pane_input`)
- Modify: `lib/pane_input.py` (dispatch over `AGENTS`; `--harness`)
- Modify: `libexec/bosun-send` (passes the window's agent)
- Test: `tests/unit/test_pane_input.py`

**Interfaces:**

- Produces: `agent_files.Screen` (frozen dataclass): `typed: Callable[[list[str]], str | None]`,
  `question: Callable[[str], bool]`, `rejected: Callable[[str], list[str]]`. Claude Code's is
  `Screen(claude_typed_text, never_asks, unknown_command_lines)`, Codex's `Screen(codex_typed_text,
  codex_question, never_rejects)`, where `never_asks` and `never_rejects` are shared defaults in
  `agent_files`.

- Produces: `pane_input.py typed|rejected|question [--harness <h>] < capture`: with `--harness`, that agent's
  reader; without it, every agent's (`typed`: the first box found, in `AGENTS` order; `question`: any agent's;
  `rejected`: the lines any agent's finds). Exit codes unchanged.

- [ ] **Step 1: Move the tests that read Claude Code's or Codex's screen** to call the readers through
  `AGENTS[h].PROFILE.screen`, keeping every capture and assertion; add
  `test_question_with_harness_reads_only_that_agent` (a Codex question screen: `question --harness codex`
  exits 0, `--harness claude` exits as "no question") and
  `test_typed_without_harness_finds_either_box` (both agents' existing captures).

- [ ] **Step 2: Run them to see them fail**
  Run: `uv run python -m pytest -q tests/unit/test_pane_input.py </dev/null`
  Expected: FAIL (no `Screen`).

- [ ] **Step 3: Create `lib/screen_text.py`, move the readers and their marks into the agent modules, add
  `Screen` and `Profile.screen`, and make `pane_input` dispatch over `AGENTS`.** `bosun send` passes
  `--harness "$(agent_session_field "$agent_pid" harness)"` to `question` and `rejected`.

- [ ] **Step 4: Run the screen tests, the agents' tests and the smoke suite's send checks**
  Run: `uv run python -m pytest -q tests/unit/test_pane_input.py tests/unit/test_claude_state.py tests/unit/test_codex_state.py </dev/null && tests/smoke.sh </dev/null 2>&1 | tail -1`
  Expected: PASS, and `N passed, 0 failed`.

- [ ] **Step 5: Commit** ("Screens: each agent reads its own, and send asks the window's agent").

### Task 6: Each agent holds its windows to their connectors

**Files:**

- Create: `lib/connectors_claude.py` (from `lib/connectors.py`: `plugin_entry`, `target_entry`,
  `claude_entry`, `claude_entries`, `needs_lookup_of`, `needs_lookup`, `load_kept`, `save_kept`, `widening`,
  `widening_problem`, `refuse_widening`, `ClaudeFacts`, `REACH_LABELS`, `_reach_label`, `_claude_reach`,
  `claude_report`, `_replaced`, `_site`, `looked_up_targets`, `say_unshown`, `plugins_by_file`,
  `claude_settings`, `write_missing`, `cmd_claude` as `main`, and `doctor_line` from `_claude_line`)

- Create: `lib/connectors_codex.py` (`TOML_*`, `toml_char`, `toml_string`, `toml_key`, `CodexPlan`,
  `codex_plan`, `codex_args`, `CODEX_OFF`, `MEANINGLESS`, `MEANS_NOTHING`, `means_nothing_to_codex`,
  `_codex_off`, `_codex_names`, `codex_report`, `say_codex_unknown`, `cmd_codex` as `main`, `doctor_line`
  from `_codex_line`)

- Modify: `lib/connectors.py` (what both share: `parse`, `Allowed`, `Resolution`, `ConnectorsError`,
  `is_url`, `is_plain`, the report helpers and texts, and `doctor`, which asks the window's agent)

- Modify: `lib/agent_files.py` (`Profile.connectors: str`, the module that holds a window to its list)
- Modify: `lib/claude_state.py`, `lib/codex_state.py` (`connectors="connectors_claude"`, `"connectors_codex"`)
- Modify: `lib/common.sh` (`resolve_connectors` runs `lib/connectors_claude.py`), `lib/launch/codex.sh` (runs
  `lib/connectors_codex.py`)

- Test: `tests/unit/test_connectors.py` (split into `test_connectors_claude.py`, `test_connectors_codex.py`
  and what stays shared), and the `connectors` part of `mypy`'s file list if it names files

**Interfaces:**

- Produces: each per-agent module has `main(argv: list[str]) -> int` (its former subcommand's arguments and
  output, unchanged) and `doctor_line(args: argparse.Namespace, allowed: connectors.Allowed) -> tuple[str,
  str]`. `connectors.py doctor --harness <h>` keeps its command line and output, with `--harness` any id in
  `AGENTS`, importing `AGENTS[h].PROFILE.connectors`.

- Keeps: Claude Code's connector flow in the shell (`window_connectors`, `set_connector_settings`,
  `resolve_connectors`, `alert_missing_connectors`, `require_connector_limit` in `lib/common.sh`): it serves
  the orchestrator's dry run (`bosun chores`) too, and names no agent to choose between.

- [ ] **Step 1: Split the tests** by what they exercise, every assertion kept; the doctor tests call
  `connectors.main(["doctor", "--harness", "claude", ...])` as before; add
  `test_doctor_asks_the_windows_agent_module` (a patched profile naming a stub module whose `doctor_line`
  returns `("ok", "stub")` gives the line `ok\tstub`).

- [ ] **Step 2: Run them to see them fail**
  Run: `uv run python -m pytest -q tests/unit/test_connectors*.py </dev/null`
  Expected: FAIL (no per-agent modules).

- [ ] **Step 3: Move the code as listed, point the callers at the new modules, and update each module's
  usage text** (`connectors.py`'s says what stays and that each agent's module has its own; each new module's
  carries its former subcommand's text).

- [ ] **Step 4: Run the connectors' tests, mypy, the complexity hooks and the smoke suite's connectors and
  Codex sections**
  Run: `uv run python -m pytest -q tests/unit/test_connectors*.py </dev/null && uv run mypy lib && uv run pre-commit run --all-files </dev/null && tests/smoke.sh </dev/null 2>&1 | tail -1`
  Expected: all pass, `N passed, 0 failed`.

- [ ] **Step 5: Commit** ("Connectors: each agent's own module holds its windows to their lists").

### Task 7: The turn-end hook, by agent

**Files:**

- Modify: `lib/agent_files.py` (`TurnReport`, `TurnEnd`; `Profile.turn_end: TurnEnd | None = None`)
- Modify: `lib/codex_state.py` (`turn_report`, from `hook.codex_turn_end`'s Codex part; its `PROFILE.turn_end`)
- Modify: `lib/hook.py` (`turn-end --harness <h>`, `codex-turn-end` as its alias; no `codex_state` import)
- Modify: `libexec/bosun-hook` (dispatches both names), `lib/launch/codex.sh` (`notify` runs
  `bosun hook turn-end --harness codex ...`)

- Test: `tests/unit/test_hook.py`, the smoke suite's Codex section

**Interfaces:**

- Produces: `agent_files.TurnReport` (frozen dataclass: `session_id: str`, `workdir: str`, `prompts: int`,
  `new: tuple[str, ...]`) and `TurnEnd` (frozen dataclass: `report: Callable[[str, Mapping[str, Any]],
  TurnReport | str]`, a `str` being why the report is ignored; `remember: Callable[[str, str, str, int],
  None]`). Codex's `report` is `codex_state.turn_report`, its `remember` `codex_state.remember`.

- Produces: `hook.py turn-end --harness <h> --window <w> --conversation <n> <json>`; `hook.py codex-turn-end
  ...` is the same with `--harness codex`. An agent whose profile has no `turn_end` is refused with
  "<h> reports no turns".

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_hook.py`, keeping every `codex-turn-end` test as
  it is (the alias), and adding `test_turn_end_with_harness_codex_does_what_codex_turn_end_did` (the same
  payload through both names leaves the same `codex-sessions.json` and the same `said` records) and
  `test_an_agent_that_reports_no_turns_is_refused` (`turn-end --harness claude ...` exits 2 with
  "claude reports no turns").

- [ ] **Step 2: Run them to see them fail**
  Run: `uv run python -m pytest -q tests/unit/test_hook.py </dev/null`
  Expected: FAIL (no `turn-end`).

- [ ] **Step 3: Move Codex's part into `codex_state.turn_report`, make `hook.py`'s handler generic over
  `AGENTS[h].PROFILE.turn_end`, dispatch both names in `bosun-hook`, and point Codex's `notify` at
  `turn-end --harness codex`.**

- [ ] **Step 4: Run the hook tests and the smoke suite**
  Run: `uv run python -m pytest -q tests/unit/test_hook.py tests/unit/test_codex_state.py </dev/null && tests/smoke.sh </dev/null 2>&1 | tail -1`
  Expected: PASS, `N passed, 0 failed`; the smoke Codex section still checks a name recorded through the
  stand-in's `notify`.

- [ ] **Step 5: Commit** ("Hook: an agent's turn-end report by its profile, codex-turn-end kept for windows
  started before").

### Task 8: No agent's name in shared code

**Files:**

- Create: `tests/unit/test_agent_names.py`

**Interfaces:**

- Consumes: `agent_state.AGENTS` (the ids to look for).

- [ ] **Step 1: Write the test**:
  - `test_shared_python_names_no_agent`: parses (`ast`) every `lib/*.py` and `libexec/bosun-resolve` but the
    agent modules (`claude_state.py`, `codex_state.py`, `connectors_claude.py`, `connectors_codex.py`) and
    `agent_state.py` (the table itself), and fails on a string constant equal to an id in `AGENTS`, naming
    file and line. `ALLOWED` maps each file that holds what only Claude Code has to the reason, one line each
    (for example `login.py`: its login checks; `watchdog.py`: its `claude_bin` for Claude Code's checks;
    `init_plan.py`: `REQUIRED_TOOLS`, the orchestrator's), and a file listed there that no longer needs it
    fails too, so the list stays true.

  - `test_shared_shell_names_no_agent`: in `lib/*.sh` and `libexec/*` (shell files only; not
    `lib/launch/*.sh` or `lib/harnesses/*.sh`), fails on a `case` arm that is an id (`claude)`, `codex)`) and
    on a test comparing a value with an id (`= claude`, `== "codex"`), outside comments.

- [ ] **Step 2: Run it**
  Run: `uv run python -m pytest -q tests/unit/test_agent_names.py </dev/null`
  Expected: PASS after Tasks 1-7. A failure names a place a task missed: move that one into its agent's module
  or profile (never add it to `ALLOWED` unless it is something only Claude Code has, with the reason).

- [ ] **Step 3: Commit** ("Tests: shared code names no agent").

### Task 9: Docs, and the real agents

**Files:**

- Modify: `docs/design/harnesses.md` ("How it is built": the profile, `agent_state.py profiles`, per-agent
  screens, connectors and turn-end; "Adding another": a `PROFILE`, and what Task 8's test checks), `CLAUDE.md`
  ("Agents" bullet and "An agent to support"), `docs/design/antigravity.md` (Status: Part 1 done, with the
  pull request), `docs/design/connectors.md` (where each agent's connector code is now)

- Modify: `ROADMAP.md` (the other-agents entry: Part 1 done, Part 2 next)

- [ ] **Step 1: Update the docs as listed.** Grep for `connectors.py claude`, `connectors.py codex`,
  `codex-turn-end`, `SUBAGENT_HARNESS`, `OTHER_AGENTS` and `BOX_READERS` across the repo's docs and fix each.

- [ ] **Step 2: Run every check**
  Run: `uv run pre-commit run --all-files </dev/null && tests/smoke.sh </dev/null 2>&1 | tail -1 && tests/sandbox.sh init </dev/null 2>&1 | tail -1`
  Expected: all hooks pass; `N passed, 0 failed` twice.

- [ ] **Step 3: Run the real agents in the macOS VM**, since this touches how their windows start, resume,
  record names and are held to their connectors:
  Run: `tests/sandbox.sh mac live claude` then `tests/sandbox.sh mac live codex` (about 10 minutes and a dollar
  each; the keys enter as the session's security rules say).
  Expected: `N passed, 0 failed` each, as before this change (73 and 96 at #31).

- [ ] **Step 4: Commit the docs** ("Docs: agents that say what they can do") and open the pull request, its
  description saying that behaviour is unchanged and how the runs show it.
