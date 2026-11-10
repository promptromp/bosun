# Connectors, the gate and setup grants: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Status: done. The two roadmap bugs merged 2026-11-09 (#29), the gate the same day (#30), and the
connectors, setup grants and the final review's fixes that night (#31). What the macOS VM changed is in
the design note, and what comes next in its Later list and `ROADMAP.md`.

**Goal:** every window bosun starts reaches only the connectors its config names; the orchestrator's gate
covers what it missed and comes from the bosun that launched it; an unattended install grants nothing
nobody saw; two roadmap bugs fixed on the way.

**Architecture:** `lib/connectors.py` (pure, plus a small CLI) turns a window's list into Claude Code's
`allowedMcpServers` (in the launch's `--settings`) or Codex's `-c mcp_servers.<name>.enabled=false`
overrides (Task 9's VM run changed these to one `-c mcp_servers={<name>={enabled=false},...}`, since Codex
splits a `-c` key at every dot: see the design note), from facts read through `lib/claude_state.py` and `lib/codex_state.py`. The shell launch path
calls it once per launch. Existing installs keep the orchestrator's reach through a new kind of config
migration. The gate's changes stay in `lib/guard.py` and `orchestrator/settings.json`.

**Tech Stack:** bash 3.2, Python 3.13+, jq, ruby (YAML), Claude Code 2.1.319, Codex 0.167.0.

**Spec:** `docs/design/connectors.md`.

**Ships as three pull requests,** in task order, each with its docs: Tasks 1-2 (the two bugs), Task 3
(the gate), Tasks 4-9 (connectors, setup, the VM). One branch, `connectors`; once Task 2 and Task 3 are
reviewed, their commits go out as their own pull requests (`fixes`, then `gate` on `main` after it), and the
rest rebases onto `main` as each merges.

## Global Constraints

- Never commit real names, ids, emails or tokens, also not in tests or commit messages; placeholders only
  (`acme`, `example.com`, `claude.ai Acme Mail`, `plugin:acme-tracker:tracker`). None of this Mac's real
  connector or server names.
- The user asked: no brittle hard-coded pattern matching or regular expressions where a structured source
  or the agents' own settings will do, and no overfitting. Where text must be parsed (`claude mcp list`),
  one function in `lib/claude_state.py` does it, with a unit test on its text.
- Fail closed: a window starts with less than its list, never more.
- `/bin/bash` 3.2 (no `mapfile`, associative arrays, `${x@Q}`); shellcheck `-S style`, shfmt; ruff,
  mypy `--strict` (also `--platform linux`), complexipy <= 15, lizard (cyclomatic <= 10, <= 60 lines);
  `tools/shell_complexity.py`. Split a function rather than raise a limit.
- Replace `libexec/` scripts atomically (write a temp file, then `mv`).
- A config key's default in `lib/common.sh` and the same value in `examples/generic/bosun.env`.
- A file bosun writes in its state folder: a row in `STATE_FILES` (`lib/uninstall_plan.py`).
- Docs change in the same pull request as the behavior (`README.md`, `docs/`, `ROADMAP.md`, `CLAUDE.md`).
- Run unit tests with `</dev/null`; one module takes seconds, the suite about 6 min, `tests/smoke.sh`
  about 14 min. Never commit while smoke runs. Never `--no-verify`, never bare `git stash`.
- Never run `install.sh`, `get.sh`, `bosun init --recommended`, `bosun uninstall` or `bosun update` on this
  Mac; never read `~/.config/bosun` or `~/.local/state/bosun` (use scratch `HOME`, `BOSUN_CONFIG_DIR`,
  `BOSUN_STATE_DIR`).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A connector name holding shell or JSON characters (`"`, `$(`, `'`, a comma inside a quoted YAML
   string) in `bosun.env` or `sessions.yml`: passed as data end to end, never word-split or evaluated
   (Task 4's `test_names_with_shell_characters_pass_as_data`, Task 5's smoke check).
2. `claude mcp list` hanging (a stdio server that never answers): bounded at 30 s, then what was kept
   (Task 4's `test_lookup_timeout_uses_kept`).
3. `connectors:` in `sessions.yml` as a string, a YAML list, an empty list (none) or absent (the default)
   (Task 5's `window_connectors` smoke checks).
4. The morning start launching several windows at once, each saving `connectors.json`: an atomic replace,
   and a file that is not JSON read as empty, never a crash (Task 4's `test_kept_file_corrupt_is_empty`).
5. A site's settings file (`BOSUN_ORCHESTRATOR_SETTINGS`) that already sets `allowedMcpServers`: bosun's
   list is the one the window gets (merge replaces a list), and doctor says so (Task 5's smoke check,
   Task 7's doctor line).

---

### Task 1: Business days by local date

**Files:** Modify `lib/ledger.py` (`business_days_since`), `lib/checkin_gate.py` (where it computes
`before` and `today`). Test: `tests/unit/test_outreach.py`, `tests/unit/test_checkin_gate.py`. Docs:
`ROADMAP.md` (remove "Business days by local date" from "Outreach follow-ups").

**Interfaces:** Produces `business_days_since(since: str, today: date | None = None, zone: tzinfo | None = None) -> int`: weekdays after `since`'s local date up to and including `today`; `zone` None means the
Mac's zone; `today` None means today in that zone.

- [ ] **Step 1: Write the failing tests** in `test_outreach.py`:
  `test_business_days_count_local_dates_east_of_utc`: `business_days_since("2026-11-08T22:30:00Z",
  date(2026, 11, 10), timezone(timedelta(hours=2)))` == 1 (sent Monday 00:30 local). And
  `test_business_days_count_local_dates_west_of_utc`: `business_days_since("2026-11-10T02:00:00Z",
  date(2026, 11, 10), timezone(timedelta(hours=-5)))` == 1 (sent Monday 21:00 local). The existing
  `test_business_days_skip_weekends` keeps passing with `zone=UTC` added.
- [ ] **Step 2:** Run `uv run python -m pytest tests/unit/test_outreach.py -k business_days </dev/null`; both new ones fail.
- [ ] **Step 3:** Implement: the date of `parse_time(since).astimezone(zone)`; `today` from `datetime.now(zone)`
  converted to that zone. In `checkin_gate.py`, `before` and `today` are local dates too (check where they
  come from; a test there with a fixed zone).
- [ ] **Step 4:** The module tests and `test_checkin_gate.py` pass.
- [ ] **Step 5: Commit** "Outreach: business days are counted by local date".

### Task 2: `--help` before the config

**Files:** Modify `bin/bosun`, `lib/common.sh` (`usage`). Test: `tests/smoke.sh`. Docs: `docs/commands.md`
if it says anything about help; the update design note's Later list (`docs/design/update.md`) and the
pull request #27 list item no longer apply.

**Interfaces:** Produces `usage_of <script>` in `lib/common.sh`: the script's leading comment block (the
awk that `usage` runs today); `usage` becomes `usage_of "$0"`. `bin/bosun <cmd> -h|--help` (exactly two
arguments) prints `usage_of libexec/bosun-<cmd>` and exits 0 without running the command, so nothing reads
the config. Every command is answered this way: those that handed `--help` to Python (`ledger`, `kg`,
`delegate`, `resolve`) keep their full text in the header instead.

- [ ] **Step 1: Write the failing smoke check** "every command's --help reads no config": for each
  `libexec/bosun-*`, `bosun <cmd> --help` output with `BOSUN_CONFIG_DIR` pointing at a folder whose
  `bosun.env` is `exit 7` equals its output with a valid config, and exits 0.
- [ ] **Step 2:** Run only that section (`tests/smoke.sh` has no filter: run the check's command by hand
  in a scratch config) and see it fail for a command that calls `load_config` first.
- [ ] **Step 3:** Implement `usage_of` and the dispatch in `bin/bosun`; reading in `lib/common.sh` there must
  not call `load_config` (check it only defines functions).
- [ ] **Step 4:** The check passes; `uv run pre-commit run --all-files` is clean.
- [ ] **Step 5: Commit** "Commands: --help prints the usage without reading the config".

### Task 3: The orchestrator's gate

**Files:** Modify `lib/guard.py`, `lib/common.sh` (`guard_hook_settings`, `orchestrator_extra_settings`),
`libexec/bosun-chores` (its settings), `orchestrator/settings.json` (drop the PreToolUse hook; add two
`ask` rules). Test: `tests/unit/test_guard.py`, `tests/unit/test_corpus.py` (fixtures in
`tests/fixtures/corpus/` if a decision changes, with why in the commit), `tests/smoke.sh`. Docs:
`docs/permissions.md`, `docs/design/approvals.md` (the tracker families), `docs/orchestrator.md` where it
says a site's settings must carry the guard, the docstring at the top of `lib/guard.py`, `ROADMAP.md`
(remove "The dry run's guard").

**Interfaces:**

- Produces `name_words(tool_name: str) -> list[str]` in `lib/guard.py`: the tool part of
  `mcp__<server>__<tool>` split on `_`, `-` and each lower-to-upper case change, lowercased
  (`editJiraIssue` -> `["edit", "jira", "issue"]`). `mcp_write_verb` and `gated_mcp` use it.
- `WRITE_VERBS` gains exactly: accept, checkout, decline, fork, forward, grant, pay, purchase, push, react,
  reopen, respond, restore, revoke, share, spam, trash, unlabel, unpin, unstar, untrash. No noun-like
  words (request, comment, label, order, pin, mark, resolve), as the design note says.
- Tracker families: a tool is a tracker or wiki change when `name_words` holds `jira` or `confluence` and
  a write verb (replacing `GATED_MCP_FAMILIES` prefixes).
- Chrome: `CHROME_PASSIVE_ACTIONS = frozenset({"screenshot", "scroll", "scroll_to", "zoom", "wait", "hover"})`;
  `decide` asks for `mcp__claude-in-chrome__computer` when `tool_input["action"]` is not in it (a missing
  action included). `orchestrator/settings.json` `ask` gains `mcp__claude-in-chrome__javascript_tool` and
  `mcp__claude-in-chrome__form_input` (Claude Code's own rule, the idiomatic place for a whole tool).
- `guard_hook_settings` (lib/common.sh): `{"hooks": {"PreToolUse": [{"matcher": "Bash|mcp__.*", "hooks":
  [{"type": "command", "command": "<printf %q of $BOSUN_CMD> guard"}]}]}}`, merged in
  `orchestrator_extra_settings` first, and passed as the extra settings of `bosun chores --dry-run`'s
  `build_model_effort_args`.

- [ ] **Step 1: Write the failing unit tests** (`test_guard.py`): `test_camel_case_tool_names_are_split`
  (`mcp__claude_ai_Acme__editJiraIssue` asks with verb "edit"; `getJiraIssue` passes);
  `test_new_verbs_ask` (one tool per new verb, e.g. `mcp__claude_ai_Acme_Mail__forward_message`,
  `mcp__x__share_file`, `mcp__x__push_files`); `test_noun_like_words_do_not_ask`
  (`mcp__x__get_pull_request`, `mcp__x__pull_request_read`, `mcp__x__get_label` pass);
  `test_camel_case_tracker_writes_are_gated` (`gated_mcp("mcp__x__createConfluencePage")` is a confluence
  change); `test_chrome_clicks_and_typing_ask` (`left_click`, `type`, `key`, a missing action, and
  `"some_future_action"` ask; `screenshot` and `scroll` pass).
- [ ] **Step 2:** `uv run python -m pytest tests/unit/test_guard.py </dev/null` fails on them.
- [ ] **Step 3:** Implement in `lib/guard.py`; then `test_corpus.py`: a changed decision means the fixture
  changes, with the reason in the commit.
- [ ] **Step 4: Smoke checks:** the orchestrator's launch `--settings` holds a PreToolUse hook whose command
  is this checkout's `bin/bosun guard` (not `~/.local/bin/bosun`); `chores --dry-run`'s fake `claude` gets
  the same; `orchestrator/settings.json` has no `hooks` key.
- [ ] **Step 5:** Unit tests, `tests/smoke.sh` and pre-commit pass. Commit "Gate: camel-case tool names, more
  write verbs, Chrome's clicks and typing, and the guard from the bosun that launched the window".

### Task 4: `lib/connectors.py` and what it reads

**Files:** Create `lib/connectors.py`, `tests/unit/test_connectors.py`. Modify `lib/claude_state.py`,
`lib/codex_state.py`, `tests/unit/test_claude_state.py`, `tests/unit/test_codex_state.py`,
`lib/uninstall_plan.py` (`("connectors.json", WORKING)` and its `.tmp`), `.pre-commit-config.yaml` /
`pyproject.toml` if a new module must be listed for mypy and the complexity hooks. Docs: `CLAUDE.md`
("Where things live": `lib/connectors.py`).

**Interfaces (produces):**

- `claude_state.plugin_mcp_servers(installed: Path | None = None) -> dict[str, dict[str, Any]]`: every
  installed plugin's servers keyed `plugin:<plugin>:<server>`, `${CLAUDE_PLUGIN_ROOT}` replaced by the
  plugin's `installPath` in `command`, `args` and `url`. `slack_plugin_mcp_config` uses it.
- `claude_state.mcp_server_targets(cwd: Path, timeout: float = 30.0) -> dict[str, str]`: `claude mcp list`
  run in `cwd` with `ENABLE_CLAUDEAI_MCP_SERVERS=true`, each line `<name>: <target> ... - <status>` as
  name -> target (a URL or a command line); raises `McpListError` on a non-zero exit, a timeout, or output
  with no server line. The one place that reads that text.
- `codex_state.mcp_server_names(timeout: float = 30.0) -> list[str]`: the `name` of each entry of `codex mcp
  list --json` (nothing else of it kept: its stdio entries carry environment values); raises
  `CodexListError` likewise.
- In `lib/connectors.py`:
  - `class ConnectorsError(ValueError)`.
  - `@dataclass(frozen=True) class Allowed: names: tuple[str, ...] = (); everything: bool = False`;
    `parse(value: str) -> Allowed`: split on `,`, strip, drop empty; `all` alone is `everything`; `all`
    with other names raises.
  - `@dataclass(frozen=True) class Resolution: entries: tuple[dict[str, Any], ...] | None; missing:
    tuple[str, ...]` (`entries` None: no limit).
  - `claude_entries(allowed: Allowed, plugins: Mapping[str, Mapping[str, Any]], urls: Mapping[str, str]) -> Resolution`
    (pure): per name, in order: a URL (`http://` or `https://`) -> `{"serverUrl": name}`; an installed
    plugin's server -> `serverUrl` from its `url`, or `serverCommand` `[command, *args]`; a name in `urls`
    -> `{"serverUrl": urls[name]}`; a name of letters, digits, `-`, `_` (Claude Code's documented rule for
    `serverName`) -> `{"serverName": name}`; else missing.
  - `needs_lookup(allowed: Allowed, plugins: Mapping[str, Any]) -> bool`: some name is none of a URL, a
    plugin's server or a plain name.
  - `load_kept(state_dir: Path) -> dict[str, str]` (a missing or unreadable file, or one that is not a JSON
    object of strings: `{}`); `save_kept(state_dir: Path, found: Mapping[str, str]) -> None` (merged into
    what was kept, written to `connectors.json.tmp` then renamed).
  - `codex_args(allowed: Allowed, servers: Sequence[str]) -> list[str]`: for each server not allowed,
    `["-c", f"mcp_servers.{toml_key(server)}.enabled=false"]`; then `["--disable", "apps"]` unless
    `everything`. `toml_key(name: str) -> str`: the name bare when it is all letters, digits, `-`, `_`
    (TOML's bare-key rule), else `json.dumps(name)`.
  - CLI `python3 lib/connectors.py claude --list <value> --cwd <dir> --state-dir <dir> [--kept-only]`: prints
    the settings JSON (`{"allowedMcpServers": [...]}`; `{}` for `all`; never empty, R7), one stderr line per missing name
    (`connectors: <name> not found, so this window starts without it`), exit 1 when any is missing, 2 on a
    bad list. Looks up only when `needs_lookup` and not `--kept-only`; a found lookup is saved; a failed one
    uses `load_kept` and says `connectors: claude mcp list failed (<why>); using the addresses kept from
    the last launch`. `python3 lib/connectors.py codex --list <value>`: prints `codex_args`, one per line;
    exit 1 with the reason when the listing fails.

- [ ] **Step 1: Write the failing tests:** `test_empty_list_allows_nothing` (entries `()`),
  `test_all_has_no_allow_list` (entries None), `test_all_with_names_is_refused`,
  `test_plain_name_is_server_name`, `test_plugin_server_by_url_and_by_command` (a command naming
  `${CLAUDE_PLUGIN_ROOT}` comes out with the install path), `test_connector_name_resolves_to_url`,
  `test_url_pattern_passes_through`, `test_unfound_names_reported_missing`,
  `test_names_with_shell_characters_pass_as_data` (`claude.ai Acme "Mail" $(x)` is missing, never
  evaluated; through the CLI too), `test_failed_lookup_uses_kept`, `test_lookup_timeout_uses_kept`,
  `test_failed_lookup_with_nothing_kept_reports_missing`, `test_kept_file_corrupt_is_empty`,
  `test_codex_disables_each_unlisted_server`, `test_codex_quotes_odd_names` (`acme.mail` ->
  `mcp_servers."acme.mail".enabled=false`), `test_codex_all_keeps_apps`; in `test_claude_state.py`
  `test_mcp_list_text_parsed` (a fixture of placeholder servers: a claude.ai one, an HTTP plugin one with
  ` (HTTP)`, a stdio one, a "Needs authentication" one), `test_mcp_list_failure_raises`,
  `test_plugin_servers_expand_the_plugin_root`; in `test_codex_state.py` `test_mcp_names_only`.
- [ ] **Step 2:** They fail (`uv run python -m pytest tests/unit/test_connectors.py tests/unit/test_claude_state.py tests/unit/test_codex_state.py </dev/null`).
- [ ] **Step 3:** Implement. External commands only through `subprocess.run` with argument lists and a
  timeout; the unit tests use fakes, never the real `claude` or `codex`.
- [ ] **Step 4:** They pass; pre-commit clean.
- [ ] **Step 5: Commit** "Connectors: a window's list turned into what Claude Code and Codex take".

### Task 5: Claude Code windows get their list

**Files:** Modify `lib/common.sh` (defaults, `window_connectors`, `connector_settings`), `lib/launch/claude.sh`,
`libexec/bosun-launch` (`print_fingerprint`), `libexec/bosun-chores`, `examples/generic/bosun.env`,
`tests/smoke.sh` (and its fake `claude`). Docs: `docs/setup.md` (the config keys and the `sessions.yml`
row: `connectors`), `docs/permissions.md` (a section on what each window can reach, with Chrome as before),
`docs/orchestrator.md` (the orchestrator's connectors), `README.md` if its quick start names connectors.

**Interfaces:**

- Defaults `BOSUN_CONNECTORS=""` and `BOSUN_ORCHESTRATOR_CONNECTORS=""`, with a comment each in both files.
- `window_connectors <name>`: the orchestrator's key; else the `sessions.yml` entry's `connectors:` (a string
  as written, a list joined with `, `, an empty list empty); else `BOSUN_CONNECTORS`. Like `window_autocompact`.
- `connector_settings <name>`: runs `connectors.py claude` once per process (kept in a variable per window
  name), with `--kept-only` when `CONNECTOR_LOOKUP=0`; its stderr lines go to `log`; when it exits 1 for the
  orchestrator, `bosun alert "<window>: started without connector(s) it may use: <names>"` (not in
  `--kept-only`). Prints the settings JSON (`{}` for `all`); the list goes as `--list=<value>`, so a value
  starting with `-` is no flag. Empty stdout or an exit of 2 or more (a bad list) is `die` (R7).
- `build_agent_args` (claude.sh): the connector settings join the extra settings for both kinds of window,
  through `merge_settings`.
- `print_fingerprint` sets `CONNECTOR_LOOKUP=0`.
- `chores --dry-run`: the extra settings are `guard_hook_settings` and the orchestrator's connector settings,
  merged; it runs with `ENABLE_CLAUDEAI_MCP_SERVERS=true` when the orchestrator's list is not empty.

- [ ] **Step 1: Write the failing smoke checks:** a repo window's launch `--settings` has
  `"allowedMcpServers":[]` by default; with `BOSUN_CONNECTORS=all` it has no such key; with
  `BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Acme Chat"` and the fake `claude mcp list` printing
  `claude.ai Acme Chat: https://chat.example.com/mcp - ✔ Connected`, the orchestrator's has
  `{"serverUrl":"https://chat.example.com/mcp"}` and `connectors.json` keeps it; with the fake failing, the
  kept URL is used; a name that is not found logs its line and the orchestrator's launch calls the fake
  `bosun alert`; `launch --fingerprint` runs no `claude mcp list` (the fake records calls);
  `connectors:` as a string, a list and `[]` in `sessions.yml`; a name with `"` and `$(` reaches the
  JSON unchanged; a site settings file with its own `allowedMcpServers` is replaced by bosun's list.
- [ ] **Step 2:** Run them; they fail.
- [ ] **Step 3:** Implement. `libexec/` edits by atomic replace.
- [ ] **Step 4:** `tests/smoke.sh` passes (about 14 min); pre-commit clean.
- [ ] **Step 5: Commit** "Launch: each Claude Code window gets only the connectors its config names".

### Task 6: Codex windows get their list

**Files:** Modify `lib/launch/codex.sh`, `tests/sandbox/codex-stand-in` (answers `mcp list --json` from a
file the test writes), `tests/smoke.sh`. Docs: `docs/design/harnesses.md` (what a Codex window reaches),
`lib/codex_state.py`'s top note.

**Interfaces:** `build_agent_args` (codex.sh) appends the lines of `connectors.py codex --list
"$(window_connectors "$1")"`; when it exits 1, the launch stops with its reason (`die`), so the window does
not start.

- [ ] **Step 1: Write the failing smoke checks** with the stand-in: a Codex window with servers `acme-docs`
  and `acme.mail` and an empty list gets `-c mcp_servers.acme-docs.enabled=false`, `-c
  mcp_servers."acme.mail".enabled=false` and `--disable apps`; with `connectors: [acme-docs]` only the
  second; with `all` none of them; a failing listing keeps the window from starting, with the reason in
  the log.
- [ ] **Step 2:** They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Smoke passes.
- [ ] **Step 5: Commit** "Launch: Codex windows switch off every server their config does not name".

### Task 7: Existing installs, and seeing it in doctor

**Files:** Modify `lib/migrations.py`, `lib/update.py` (the `write_config` call), `lib/update_summary.py`,
`libexec/bosun-doctor`, `lib/connectors.py` (a `doctor` subcommand), `lib/claude_state.py` (whether
`~/.claude/settings.json` sets `allowedMcpServers`). Test: `tests/unit/test_migrations.py`,
`tests/unit/test_update_summary.py`, `tests/unit/test_connectors.py`, `tests/smoke.sh`. Docs:
`docs/design/update.md` (the config migrations: renames and added keys), `docs/setup.md` (updating),
`docs/watchdog.md` only if doctor's lines are listed there.

**Interfaces:**

- `@dataclass(frozen=True) class AddedKey: key: str; kept: str | None; note: str` and
  `CONFIG_ADDED: tuple[AddedKey, ...] = (AddedKey("BOSUN_ORCHESTRATOR_CONNECTORS", "all", "the orchestrator keeps every connector, as before; name the ones it needs and drop all (bosun doctor lists them)"), AddedKey("BOSUN_CONNECTORS", None, "repo windows now start without connectors, a repo's own .mcp.json servers included; give one back with BOSUN_CONNECTORS or connectors: in sessions.yml"))`.
- `write_config(config_dir: Path, now: datetime, added: Sequence[str] = ()) -> list[str]`: the renames as
  now, then `KEY="<kept>"` appended for each `AddedKey` with a `kept` value whose key is in `added` and
  that `bosun.env` does not assign; one backup for both; a said line per key. `lib/update.py` calls it when
  there are renames or such keys in `plan.keys_added`.
- The summary's config lines and doctor's after-update list show each added key's `note` (the generic "N
  new keys" line counts only keys without one).
- `connectors.py doctor --window <name> --kind <kind> --harness <harness> --list <value> --cwd <dir>
  --state-dir <dir>`: one `ok`/`warn` line per window: its list, what it resolved to (names only), each
  name not found; `warn` for the orchestrator with `all`; a Codex window's URL or `claude.ai` names noted
  as meaning nothing there. `check_connectors` in doctor runs it per window from `window_specs`, and warns
  once when Claude Code's own settings set `allowedMcpServers` (it widens every window's).

- [ ] **Step 1: Write the failing tests:** `test_kept_key_written_when_the_update_adds_it`,
  `test_kept_key_not_written_when_already_set`, `test_kept_key_not_written_when_not_added`,
  `test_one_backup_for_renames_and_added_keys`, `test_bosun_env_link_target_changes` (as for renames);
  `test_summary_shows_added_key_notes`; `test_doctor_lines` (none, a few with one missing, `all` for the
  orchestrator warns, a Codex window with a URL name); a smoke check of doctor's section.
- [ ] **Step 2:** They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Unit and smoke pass (the update's smoke section, with `tests/sandbox/update_patch.sh`,
  sees the key written).
- [ ] **Step 5: Commit** "Update: an older install's orchestrator keeps its connectors; doctor shows each window's".

### Task 8: Setup

**Files:** Modify `lib/init_plan.py`, `lib/init_wizard.py`, `lib/init_detect.py`, `lib/init.py`, `get.sh`.
Test: `tests/unit/test_init_plan.py`, `tests/unit/test_init_wizard.py`, `tests/unit/test_init.py`,
`tests/sandbox/fresh_user.sh`, and the VM scripts that relied on `--unattended` setting up repo windows
(`tests/install_e2e.sh`, `tests/work_e2e.sh`, `tests/sandbox/e2e_lib.sh`, `tests/sandbox/live.sh`: give
them their windows through `--answers` or `sessions.yml`, whichever each already writes). Docs:
`docs/design/init.md` (the answers table, the repos rule, unattended), `docs/setup.md` (install and
`--unattended`), `docs/design/connectors.md` (Setup: no ticking by name).

**Interfaces:**

- `Facts.connectors: tuple[str, ...] = ()`: the server names `claude_state.mcp_server_targets` shows (an
  empty tuple when it fails), read by `init_detect`.
- `Answers.orchestrator_connectors: str = ""`, in `TEXT_ANSWERS`, checked with `connectors.parse` (an error
  is an answers problem); `env_groups` writes `BOSUN_ORCHESTRATOR_CONNECTORS`.
- `recommend`: on a first run `orchestrator_connectors` is `""` (per the user's guidance, nothing is ticked
  by its name); on a re-run, what `bosun.env` says. `recommended_repos` ticks only the first of
  `recommended_uses` (the most used); the wizard lists the rest unticked, as it lists everything found.
- `ask_connectors(ui, facts, answers, top)` after `ask_repos` in `ask_all`: one toggle per name in
  `facts.connectors`, then each name of the answer that is not among them (shown "not found", kept unless
  switched off), and an `all` line when the answer is `all` (switching any server on switches `all` off).
  Its intro says the orchestrator reads Slack and trackers through these, and that repo windows get none
  (`BOSUN_CONNECTORS`, `connectors:` in `sessions.yml`).
- `bosun init --recommended --unattended`: on a first run (no `bosun.env`), the recommendation with
  `repos=()` and `orchestrator_connectors=""`, and a last line `No repo windows or connectors were set up,
  since nobody chose them: run bosun init to add them.`; on a re-run, the same as `--recommended`.
  `get.sh`'s `start_init` runs it for `--unattended`.

- [ ] **Step 1: Write the failing tests:** `test_first_run_ticks_the_most_used_repo_only`,
  `test_first_run_ticks_no_connectors`, `test_rerun_keeps_the_configs_connectors` (a list, and `all`),
  `test_bad_connectors_answer_is_a_problem`, `test_env_groups_write_the_orchestrators_connectors`;
  wizard: `test_connectors_screen_toggles`, `test_all_line_switches_off_when_a_server_is_ticked`,
  `test_unfound_names_are_kept_and_marked`; `test_unattended_first_run_sets_up_no_windows_or_connectors`,
  `test_unattended_rerun_is_recommended`.
- [ ] **Step 2:** They fail.
- [ ] **Step 3:** Implement; then `tests/sandbox.sh init` (Docker, about 1 min) with `fresh_user.sh`'s
  expectations updated.
- [ ] **Step 4:** Unit, smoke and `tests/sandbox.sh init` pass.
- [ ] **Step 5: Commit** "Setup: the orchestrator's connectors are a question; one repo ticked; unattended grants nothing".

### Task 9: Dogfooding in the VM and on this Mac

**Files:** Create `tests/sandbox/mcp_probe_server.py` (a stdio MCP server in plain Python: answers
`initialize`, `tools/list` with one tool named after its argument, `tools/call`; nothing else). Modify
`tests/sandbox/live.sh` (a `connectors` part), `tests/work_e2e.sh` (after the update from the release:
`bosun.env` holds `BOSUN_ORCHESTRATOR_CONNECTORS="all"`, the summary says both notes, doctor warns).
Docs: `docs/design/connectors.md` (the Codex facts confirmed, and "Status" with what was run),
`docs/design/harnesses.md`, `docs/development.md` (the live part and its duration), `ROADMAP.md`
(Installing: what this added; the dogfooding gaps closed), `CLAUDE.md` (a "Common changes" line: a
connector a window should reach is a config value, never code).

- [ ] **Step 1:** In `live.sh`'s new part, with the real Claude Code: `claude mcp add` user servers
  `probe-allowed` and `probe-blocked` (both `mcp_probe_server.py`), a repo with a `.mcp.json` naming
  `probe-project`; `BOSUN_CONNECTORS=probe-allowed`; start the window through bosun; ask it to list its
  tools whose names start with `mcp__`; check the transcript names `probe-allowed`'s tool and neither
  other. Same for Codex with `[mcp_servers.*]` in the VM's `config.toml`, which also confirms the design
  note's "to be confirmed" Codex facts (an unknown name, a project's server, `--disable apps`).
- [ ] **Step 2:** `tests/sandbox.sh mac live claude` and `mac live codex` (about $1 each, ~10 min); then `mac
  work` (~16 min) for the update check.
- [ ] **Step 3: On this Mac** (not committed; a scratch tmux server and a folder Claude Code already
  trusts; nothing installed): one interactive Claude Code window started with the `--settings` that
  `connectors.py claude` prints for `BOSUN_ORCHESTRATOR_CONNECTORS="<one real claude.ai connector>"`;
  ask it to list its `mcp__` tools; only that connector's appear. Then the same with an installed
  plugin's server. Record what was seen (generic wording) in the design note.
- [ ] **Step 4:** Docs as listed; `uv run pre-commit run --all-files` and the forbidden-terms scan clean.
- [ ] **Step 5: Commit** "Connectors: run with the real agents in the VM; the design note records what they showed".
