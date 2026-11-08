# bosun update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

Status: done. Part A merged 2026-11-06 (#23), Part B 2026-11-07 (#26) and Part C, the macOS VM runs
and the docs, 2026-11-08 (#27). What comes next is in the design note's Later list.

**Goal:** `bosun update` moves a running install to the head of its branch mid-afternoon: it
backs the data up, migrates it, switches versions in one step, checks the result, puts everything
back on failure, brings every agent along to continue its work, and ends with a summary.

**Architecture:** Each version lives in its own git worktree under `~/.local/share/bosun-versions/`,
and `~/.local/share/bosun` becomes a link switched by one rename. A process runs the code of the
version it started in, and writes only the link into anything that outlives it. The update holds
bosun's own background work. Then, under the stores' locks, it backs up, migrates, switches and
validates. Each step is recorded in `update.json`. Every store write checks that file and the store's
format under its lock, so old code re-runs itself on the new version and a crash mid-freeze is put
back by the next writer. Pure modules decide the plan, each window's treatment and the summary; thin
I/O modules carry them out.

**Tech Stack:** bash 3.2 and stdlib-only Python 3.13 (`fcntl`, `ctypes` for the conversion's
atomic swap, `subprocess` for git, tmux and launchd); POSIX sh for `get.sh`; unittest-style tests
run by pytest; the smoke suite; the Tart VM (`tests/sandbox.sh mac work`, `mac live`).

**Spec:** [docs/design/update.md](../design/update.md) (approved 2026-11-05).

## Global Constraints

- No third-party runtime dependency. Shell stays bash 3.2 compatible, and `get.sh` plain POSIX sh
  with everything in functions and `main` last.
- The checks in `CLAUDE.md` pass: ruff (McCabe <= 10), mypy --strict (also `--platform linux`),
  complexipy (<= 15), lizard (cyclomatic <= 10, <= 60 lines), shellcheck, shfmt,
  `tools/shell_complexity.py`, typos, links, `mkdocs build --strict`. Split a function rather than
  raise a limit.
- Fictional data only in tests and fixtures (acme, placeholders); no real names or ids.
- Never run `install.sh`, `bosun init --recommended`, `get.sh` or `bosun uninstall` on this Mac;
  `get.sh` and launchd are tried in the VM.
- Paths: versions `~/.local/share/bosun-versions/<first 12 of the commit>/`, the shared repository
  `~/.local/share/bosun-versions/bosun.git` (bare); backups
  `$BOSUN_STATE_DIR/backups/<YYYYmmdd-HHMMSS>-<from 7>-<to 7>/` with `manifest.json` and
  `summary.md`; the update's record `$BOSUN_STATE_DIR/update.json`, its lock `update.lock`, its
  folder `update/` (notes and the rehearsal copy); the config copy
  `bosun.env.pre-update-<YYYYmmdd-HHMMSS>`.
- The freeze stays under 10 s (the PostToolUse approval hooks' timeout); the VM measures it.
- Kept: the last 3 backups; the current and the previous version folder.
- The hold expires 3600 s after it is set.
- A deletion of a person prints exactly `kg: deleted <n> update backup(s) taken before this; going
  back past it is no longer possible` (`backup` for 1, `backups` otherwise), never naming them.
- Lock order, everywhere: `update.lock`, then the ledger's, then the knowledge base's. Code
  holding a store lock never takes it again on another descriptor (flock is per open file).
- A migration never takes a store lock: the update holds them.
- No `--restore` command, no releases or channels: the head of the tracked branch
  (`git config bosun.branch` in `bosun.git`, default `main`).
- Every new file in the state or config folder gets a row in `STATE_FILES` / `CONFIG_FILES`
  (`lib/uninstall_plan.py`); the smoke suite fails otherwise.
- Every doc a task affects changes in the same task (README, `docs/`, `CLAUDE.md`, usage headers).
- Replace `libexec/` scripts atomically when a session may be running them.

## Review Focus

1. **An approval hook (PreToolUse) arriving during a freeze that bumps the ledger's format:** it
   waits on the lock, then re-runs on the new version with the same stdin and prints the same
   decision; it never writes format-1 data into a format-2 store. Pinned in Task 6
   (`test_a_newer_store_reruns_with_the_same_stdin`) and Task 10 (smoke).
2. **The update killed after the link switched, before the checks:** the next `bosun ledger`
   write puts back the backup and the link to the old version, then writes on the old version;
   nothing written before the kill survives the restore, nothing after it is lost. Pinned in
   Task 10 (`kill -9` at each freeze step).
3. **bosun run from a developer checkout** (the smoke suite, a clone in `~/src`): the install path
   is the checkout itself, and every launch, hook and plist reads as before. Pinned in Task 1.
4. **A `kg forget` while the update is still bringing agents along:** it deletes the backup the
   update just took; the summary written afterwards says the backup is gone instead of naming a
   folder that is not there. Pinned in Task 12 (`test_a_backup_a_forget_deleted_is_said_so`).
5. **A window that turns busy between the plan and its restart:** it is never killed; it moves to
   waiting. Pinned in Task 11 (`test_a_window_busy_again_is_not_restarted`).

## File structure

| File | Responsibility |
|---|---|
| `lib/install_paths.py` (new, pure) | The code path against the install link; the versions folder |
| `lib/update_state.py` (new) | `update.json`: steps, the hold, the marker; one update at a time |
| `lib/update_backups.py` (new) | Take, prune, restore and delete backups; the manifest |
| `lib/versions.py` (new) | Version folders: commit, folder, point the link, the conversion's swap, prune |
| `lib/update_gate.py` (new) | The write check, re-running through the link, recovering a cut-short freeze |
| `lib/migrations.py` (new) | The registry and runner; config key renames |
| `lib/launch_record.py` (new) | What each window was launched with (its fingerprint) |
| `lib/update_plan.py` (new, pure) | The plan from two versions' facts; each window's treatment |
| `lib/update_agents.py` (new) | Prompts, take stock, restarts, waiting; `advance` |
| `lib/update_summary.py` (new, pure) | The summary as text and JSON |
| `lib/update.py` (new) | The CLI: facts, rehearsal, hold, freeze, release, agents, summary; the conversion |
| `libexec/bosun-update` (new) | Fetch, add the new version's folder, hand over to its `lib/update.py` |
| `lib/common.sh`, `lib/launch/*.sh`, `install.sh`, `lib/init.py` | The install path written out; the hold; job reloads |
| `lib/ledger.py`, `lib/knowledge.py`, `lib/knowledge_store.py` | Format versions, the write check, lock-free validation, forget deletes backups |
| `lib/hook.py`, `lib/guard.py`, `lib/console_kb.py`, `lib/console_server.py` | Run under the gate; the console refuses |
| `libexec/bosun-checkin`, `bosun-watchdog`, `bosun-learn`, `bosun-morning`, `bosun-refresh`, `bosun-launch` | Honour the hold; refresh's update restart; launch records |
| `lib/watchdog.py`, `libexec/bosun-doctor` | Report an update stuck or cut short |
| `get.sh`, `lib/uninstall_*.py` | The new layout on install, update and uninstall |
| `tests/unit/test_install_paths.py`, `test_update_state.py`, `test_update_backups.py`, `test_versions.py`, `test_update_gate.py`, `test_migrations.py`, `test_store_formats.py`, `test_launch_record.py`, `test_update_plan.py`, `test_update_agents.py`, `test_update_summary.py` (new) | Their modules |
| `tests/smoke.sh`, `tests/work_e2e.sh`, `tests/sandbox/update_patch.sh` (new), `tests/llm/cases.py` | End to end |

Part A (Tasks 1-7) is groundwork that changes nothing a user sees except the forget's line, and
ships as one pull request. Part B (Tasks 8-13) is `bosun update` and the new layout, a second.
Part C (Tasks 14-15) is the VM and the closing docs, a third.

---

## Part A: groundwork

### Task 1: Two paths: the code that runs, and the link written out

**Files:**

- Create: `lib/install_paths.py`, `tests/unit/test_install_paths.py`
- Modify: `lib/common.sh` (lines 6-7, 392, 476-483, 549, 675-715, the three `render_*_plist`),
  `lib/launch/claude.sh:16`, `lib/launch/codex.sh:17`, `install.sh:58`, `lib/init.py:157`,
  `tests/smoke.sh`, `CLAUDE.md` (Where things live: the two paths)

**Interfaces:**

- Produces:
  - `VERSIONS_SUFFIX = "-versions"`
  - `install_path(root: Path) -> Path`: for a folder whose parent is named `<name>-versions`,
    that parent's sibling `<name>`; otherwise `root` itself
  - `install_bin(root: Path) -> Path` (`install_path(root) / "bin" / "bosun"`)
  - `versions_dir(install: Path) -> Path` (`<install>-versions`)
  - `is_version_folder(root: Path) -> bool`
  - shell: `BOSUN_INSTALL` (the same rule as `install_path`) and `BOSUN_CMD`
    (`$BOSUN_INSTALL/bin/bosun`) beside `BOSUN_ROOT` and `BOSUN`, which stay the running
    version's own folder and command

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_install_paths.py`:
  - `test_a_version_folder_writes_out_the_link`:
    `install_path(Path("/h/.local/share/bosun-versions/0123456789ab")) == Path("/h/.local/share/bosun")`
  - `test_a_checkout_writes_out_itself`: `install_path(Path("/h/src/bosun")) == Path("/h/src/bosun")`
  - `test_versions_dir_sits_beside_the_link`:
    `versions_dir(Path("/h/.local/share/bosun")) == Path("/h/.local/share/bosun-versions")`
  - `test_only_a_folder_inside_versions_is_one`: `is_version_folder` is true for the first path
    above, false for `/h/.local/share/bosun-versions` and `/h/src/bosun`

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_install_paths.py -q`
  Expected: errors importing `install_paths`.

- [ ] **Step 3: Implement `lib/install_paths.py`** and, in `lib/common.sh`, `BOSUN_INSTALL` and
  `BOSUN_CMD` after `BOSUN` (a `case` on `$(dirname "$BOSUN_ROOT")` matching `*-versions`). Then
  switch every place that writes a path into something that outlives the process to them: the
  launch command (`build_launch_command`'s `launch` arrays), the hook commands
  (`orchestrator_extra_settings`, `approvals_hook_settings`, `repo_session_settings`,
  `signal_hook_settings`, `compact_hook_settings`, line 549's quoted command), the
  `BOSUN_ORCHESTRATOR_SETTINGS` default, `--plugin-dir`, Codex's `notify`, `ROOT=` in the three
  plist renderers, the `~/.local/bin/bosun` link in `install.sh`, and the command `lib/init.py`
  prints (`install_bin(ROOT)`). Calls made now (`"$BOSUN" send`, `lib/events.py`'s `BOSUN`)
  keep the running version.

- [ ] **Step 4: Add smoke checks** (a section "Two paths"):
  - from a version-shaped folder (`$WORK/share/bosun-versions/v1`, a copy of the working tree),
    `BOSUN_INSTALL` is
    `$WORK/share/bosun`, and Python's `install_path` says the same (the copy:
    `git -C "$ROOT" ls-files -co --exclude-standard -z | tar -C "$ROOT" --null -T - -cf - | tar -C
    "$dest" -xf -`, kept as the helper `version_folder <name>` for later tasks);
  - a dry-run launch of the orchestrator and of a repo window from there, and the rendered
    plists, contain `$WORK/share/bosun/` and never `bosun-versions/`;
  - from the checkout itself, `BOSUN_INSTALL` equals `BOSUN_ROOT` (Review Focus 3).

- [ ] **Step 5: Run the checks**

  Run: `uv run pytest tests/unit/test_install_paths.py -q && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 6: Commit**

  ```bash
  git add lib/install_paths.py tests/unit/test_install_paths.py lib/common.sh lib/launch/claude.sh \
    lib/launch/codex.sh install.sh lib/init.py tests/smoke.sh CLAUDE.md
  git commit -m "Two paths: a process runs its own version, and writes out the install link"
  ```

### Task 2: A format version per store, pinned

**Files:**

- Create: `tests/unit/test_store_formats.py`
- Modify: `lib/ledger.py` (line 1132), `lib/knowledge_store.py` (`Paths.format`), `lib/knowledge.py`
  (genesis writes `format.json`), `CLAUDE.md` (a new common change, "A data format change"),
  `docs/design/knowledge.md` (the files list)

**Interfaces:**

- Produces:
  - `ledger.LEDGER_FORMAT = 1`; `ledger.store_format(state_dir: Path) -> int` (`ledger.json`'s
    `version`; 0 when there is no `ledger.json`)
  - `knowledge_store.KNOWLEDGE_FORMAT = 1`; `Paths.format` (`root / "format.json"`, holding
    `{"format": n}`); `knowledge_store.store_format(paths: Paths) -> int` (0 with no knowledge
    base, 1 for one without `format.json`: today's); `knowledge_store.write_format(paths: Paths,
    version: int) -> None` (whole and flushed, `journal.replace_durably`)
  - `ledger.format_digest() -> str` and `knowledge_store.format_digest() -> str`: SHA-256 of
    `change_log.canonical` of the tables that say what is on disk:
    - ledger: `{"envelope": ENVELOPE_VERSION, "fields": [[list(r.pattern), r.rule, r.klass] for r
      in ledger_changes.FIELDS], "maps": list(ledger_changes.MAPS)}`;
    - knowledge: `{"envelope": ENVELOPE_VERSION, "ops": sorted(APPLIED + SKIPPED), "files": the
      file names of `Paths`' properties, sorted}`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_store_formats.py`:
  - `test_the_ledger_format_is_pinned_to_its_version`:
    `FORMAT_DIGESTS["ledger"].get(ledger.LEDGER_FORMAT) == ledger.format_digest()`, the docstring
    saying what to do when it fails (bump the format, add a migration and its fixture, record the
    digest: `CLAUDE.md`, "A data format change")
  - `test_the_knowledge_format_is_pinned_to_its_version`: the same for the knowledge base
  - `test_a_new_knowledge_base_records_its_format`: the first write of an empty knowledge base
    leaves `format.json` with `{"format": 1}`, committed in its history
  - `test_a_knowledge_base_from_before_formats_is_format_1`: `store_format` of a knowledge base
    without `format.json` is 1; of no knowledge base, 0
  - `test_the_ledger_writes_its_format`: after any write, `store_format(state_dir) == LEDGER_FORMAT`

  `FORMAT_DIGESTS = {"ledger": {1: "<hex>"}, "knowledge": {1: "<hex>"}}`, the digests computed
  once and recorded with `# pragma: allowlist secret`, as `RULES_DIGESTS` is.

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_store_formats.py -q`
  Expected: `AttributeError` on `LEDGER_FORMAT`.

- [ ] **Step 3: Implement.** `ledger.json` gets `version = LEDGER_FORMAT` on every write (not
  `setdefault`); the knowledge genesis writes `format.json` before the data files.

- [ ] **Step 4: Run the tests to see them pass**

  Run: `uv run pytest tests/unit/test_store_formats.py tests/unit/test_ledger.py tests/unit/test_knowledge.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add tests/unit/test_store_formats.py lib/ledger.py lib/knowledge_store.py lib/knowledge.py \
    CLAUDE.md docs/design/knowledge.md
  git commit -m "Each store records its format version, pinned by a digest test"
  ```

### Task 3: The update's record, and the hold

**Files:**

- Create: `lib/update_state.py`, `tests/unit/test_update_state.py`
- Modify: `lib/common.sh` (`update_hold_active`), `libexec/bosun-checkin`, `libexec/bosun-watchdog`,
  `libexec/bosun-learn`, `libexec/bosun-morning`, `lib/watchdog.py` (`CHECKS`),
  `libexec/bosun-doctor`, `lib/uninstall_plan.py`, `docs/watchdog.md`

**Interfaces:**

- Produces:
  - `STATE_FILE = "update.json"`, `LOCK_FILE = "update.lock"`, `UPDATE_DIR = "update"`,
    `HOLD_SECONDS = 3600`
  - `STEPS = ("planned", "rehearsed", "holding", "marked", "backed-up", "migrated", "switched",
    "checked", "released", "agents", "done", "restored", "failed")`;
    `FREEZE_STEPS = ("marked", "backed-up", "migrated", "switched")` (the backup is the truth)
  - `@dataclass(frozen=True) class UpdateState(id: str, from_root: str, to_root: str,
    from_commit: str, to_commit: str, step: str, started_at: str, hold_until: int, backup: str = "",
    restorable: tuple[str, ...] = (), pending: tuple[str, ...] = (), user_changes: tuple[str, ...]
    = (), freeze_ms: int = 0, error: str = "")` (`pending`: windows still to bring along;
    `user_changes`: what to change in my own files, from the plan)
  - `read(state_dir: Path) -> UpdateState | None`; `write(state_dir: Path, state: UpdateState) ->
    None` (whole and flushed); `advanced(state: UpdateState, step: str, **fields: Any) ->
    UpdateState`
  - `hold_active(state: UpdateState | None, now: float) -> bool` (a step before `released`, and
    `now < hold_until`)
  - `in_freeze(state: UpdateState | None) -> bool`
  - `running(state_dir: Path) -> bool`: `update.lock` is held (a non-blocking shared `flock`
    fails), which a dead process never holds, whatever its pid became
  - `@contextmanager one_update(state_dir: Path) -> Iterator[None]`: holds `update.lock`
    exclusively; raises `UpdateRunning(started_at, from_commit, to_commit)` if another holds it
  - CLI: `update_state.py hold-active --state-dir <dir>` (exit 0 while on, else 1);
    `update_state.py stuck --state-dir <dir>` (prints why, exit 0, when a hold is past
    `hold_until`, or a freeze step is recorded and nothing runs)
  - shell: `update_hold_active` in `lib/common.sh`

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_update_state.py`:
  - `test_a_hold_lasts_until_it_expires`: a state at `holding` with `hold_until=1000` is active at
    `now=999`, not at `1000`
  - `test_no_hold_once_released`: the same state at `released` is not active at `now=0`
  - `test_the_marker_is_the_freeze_steps`: `in_freeze` is true exactly for `FREEZE_STEPS`
  - `test_the_record_is_written_whole`: `write` then `read` round-trips, and a `.tmp` left by a cut
    is ignored by `read`
  - `test_one_update_at_a_time`: inside `one_update`, a second `one_update` raises `UpdateRunning`,
    and `running()` is true; after it, false
  - `test_a_dead_update_is_not_running`: a child process takes `one_update` and is killed with
    SIGKILL; `running()` is false

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_update_state.py -q`
  Expected: errors importing `update_state`.

- [ ] **Step 3: Implement `lib/update_state.py`**, then honour the hold:
  - the check-in loop: at the top of each tick, while `update_hold_active`, log once per hold
    "update in progress: skipping this tick; the next one catches up" and skip the tick (check-in,
    events, Slack watcher, review watch);
  - `bosun watchdog` and `bosun learn`: skip the run while the hold is on, with a log line;
  - `bosun morning`: wait for the hold to lift (poll every 10 s, until `hold_until`), logging once,
    since its next run would be tomorrow;
  - a watchdog check `Check("update-stuck", "error", "an update's hold outlived its hour, or an
    update stopped while writes were paused", check_update_stuck)`, its usage line and its row in
    `docs/watchdog.md`; `bosun doctor` prints the same as a `fail` under "Config";
  - `STATE_FILES`: `("update.json", WORKING)`, `("update.json.tmp", WORKING)`,
    `("update.lock", WORKING)`, `("update", WORKING)`.

- [ ] **Step 4: Add smoke checks:** with an active hold in `update.json`, `bosun checkin --dry-run`
  says it skips; `bosun watchdog --facts` with a hold past its hour reports `update-stuck`.

- [ ] **Step 5: Run the checks**

  Run: `uv run pytest tests/unit/test_update_state.py tests/unit/test_watchdog.py -q && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 6: Commit**

  ```bash
  git add lib/update_state.py tests/unit/test_update_state.py lib/common.sh libexec/bosun-checkin \
    libexec/bosun-watchdog libexec/bosun-learn libexec/bosun-morning lib/watchdog.py \
    libexec/bosun-doctor lib/uninstall_plan.py docs/watchdog.md tests/smoke.sh
  git commit -m "The update's record, and a hold bosun's own background work honours"
  ```

### Task 4: Backups, and a forget that reaches them

**Files:**

- Create: `lib/update_backups.py`, `tests/unit/test_update_backups.py`
- Modify: `lib/knowledge.py` (`forget_in_journal`), `tests/unit/test_knowledge.py`,
  `lib/uninstall_plan.py`, `docs/knowledge.md`, `docs/design/knowledge.md` (the deletion bullet)

**Interfaces:**

- Consumes: `uninstall_plan.STATE_FILES`, `CONFIG_FILES` and their classes
- Produces:
  - `BACKUPS_DIR = "backups"`, `KEEP = 3`, `MANIFEST = "manifest.json"`, `SUMMARY = "summary.md"`
  - `IN_FLIGHT = ("events", "signals", "slack-watch", "login.json", "codex-sessions.json",
    "agent-changes.json")`: the working files also backed up
  - `FROZEN = ("state/ledger.json", "state/ledger.json.bak", "state/ledger.md", "state/journal",
    "state/replica.json", "config/knowledge")`: what the freeze's locks cover, so what a failed
    freeze puts back (an event queued meanwhile is never undone)
  - `backup_set(state_dir: Path, config_dir: Path) -> list[tuple[str, Path]]`: each file or folder
    classed ledger, knowledge or settings, plus `IN_FLIGHT`, as `("state/<name>" | "config/<name>",
    path)`; never `backups`, `logs`, `dry-runs` or `update`
  - `@dataclass(frozen=True) class Manifest(from_commit: str, to_commit: str, taken_at: str,
    formats: Mapping[str, int], files: Mapping[str, str])` (relative path to SHA-256);
    `read_manifest(backup: Path) -> Manifest`
  - `take(state_dir: Path, config_dir: Path, from_commit: str, to_commit: str, formats:
    Mapping[str, int], now: datetime) -> Path`: copies with APFS clones (`cp -c -pR`, falling back
    to `cp -pR --reflink=auto` where `cp` has no `-c`), writes the manifest last
  - `restore(backup: Path, state_dir: Path, config_dir: Path, only: Sequence[str]) -> list[str]`:
    puts back each manifest file under one of the `only` prefixes whose digest differs, checks each
    against the manifest, returns what it put back
  - `prune(state_dir: Path, keep: int = KEEP) -> list[Path]` (the oldest beyond `keep`)
  - `listed(state_dir: Path) -> list[Path]` (oldest first)
  - `delete_all(state_dir: Path) -> int`: every backup, and a rehearsal copy left in
    `update/rehearsal`; returns how many backups

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_update_backups.py`:
  - `test_the_set_is_records_settings_and_work_in_flight`: over a state and config folder holding
    one file of each `STATE_FILES` / `CONFIG_FILES` name, `backup_set` holds `state/ledger.json`,
    `state/journal`, `state/events`, `config/knowledge`, `config/bosun.env`, and not `state/logs`,
    `state/backups` or `state/update`
  - `test_a_backup_lists_every_file_with_its_digest`: `take` then `read_manifest`: `files` names
    each file copied, with its SHA-256, and `formats` as given
  - `test_restore_puts_back_only_what_changed_under_its_prefixes`: after `take`, change
    `ledger.json` and `events/e1.json`; `restore(..., only=["state/ledger.json"])` returns
    `["state/ledger.json"]`, the ledger is as backed up, the event is as changed
  - `test_three_are_kept`: four backups taken a second apart; `prune` deletes the first only
  - `test_delete_all_takes_the_rehearsal_too`: two backups and `update/rehearsal/x`;
    `delete_all` returns 2, and neither folder is left

  In `tests/unit/test_knowledge.py`:

  - `test_a_forget_deletes_the_update_backups`: two backups in the state folder; `kg forget
    person/<p>` leaves none and prints `kg: deleted 2 update backups taken before this; going back
    past it is no longer possible`, without the person's id or name
  - `test_a_forget_with_no_backups_says_nothing_of_them`
  - `test_a_deletion_cut_short_deletes_them_when_finished`: a forget cut after its marker (the
    existing cut-short helper), a backup taken after it, then any write: the backup is gone
  - `test_expire_deletes_them_when_it_deletes_a_person_fact`, and keeps them when it deletes none

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_update_backups.py tests/unit/test_knowledge.py -q -k "backup or forget or expire"`
  Expected: errors importing `update_backups`; the knowledge tests fail on the missing line.

- [ ] **Step 3: Implement `lib/update_backups.py`**, then call `delete_all(state_dir())` as the
  last step of `forget_in_journal` and print the line when it returns more than 0.
  `STATE_FILES`: `("backups", LEDGER)` (they hold your records), with a comment; `CONFIG_FILES`:
  `("bosun.env.pre-update-*", SETTINGS)`. Docs: `docs/knowledge.md`'s deletion paragraph and
  the deletion bullet of `docs/design/knowledge.md` say a deletion deletes the update backups.

- [ ] **Step 4: Run the tests to see them pass**

  Run: `uv run pytest tests/unit/test_update_backups.py tests/unit/test_knowledge.py tests/unit/test_uninstall_plan.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/update_backups.py tests/unit/test_update_backups.py lib/knowledge.py \
    tests/unit/test_knowledge.py lib/uninstall_plan.py docs/knowledge.md docs/design/knowledge.md
  git commit -m "Update backups, and every deletion of a person deletes them"
  ```

### Task 5: Version folders

**Files:**

- Create: `lib/versions.py`, `tests/unit/test_versions.py`

**Interfaces:**

- Produces:
  - `REPO = "bosun.git"`; `LEGACY_PREFIX = "legacy-"`
  - `commit_of(root: Path) -> str` (`git -C root rev-parse HEAD`)
  - `folder_for(versions: Path, commit: str) -> Path` (`versions / commit[:12]`)
  - `current(install: Path) -> Path | None`: the folder the link leads to, or `None` when
    `install` is not a link
  - `point(install: Path, target: Path) -> None`: a new link beside `install`, renamed over it
    (`os.replace`), the parent folder flushed; the path never goes missing
  - `swap_in_link(install: Path, target: Path) -> Path`: the conversion. A link to `target` made
    beside the checkout at `install` is swapped with it in one call (`renamex_np(...,
    RENAME_SWAP)` on macOS, `renameat2(..., RENAME_EXCHANGE)` on Linux, through `ctypes`; a table
    keyed by `sys.platform`); the old checkout, now at the link's temporary name, is renamed to
    `versions / f"{LEGACY_PREFIX}{commit[:12]}"`, which it returns
  - `prune(versions: Path, keep: Collection[Path]) -> list[Path]`: removes every version folder
    and legacy checkout not in `keep` (`git worktree remove --force` for worktrees), skipping one
    a running process has open (`lsof -t +D <folder>` prints a pid)

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_versions.py` (temporary folders,
  real git):
  - `test_point_switches_without_a_gap`: a thread reading `os.readlink(install)` in a loop during
    100 `point` calls between two folders never gets `FileNotFoundError`
  - `test_current_follows_the_link`: after `point(install, a)`, `current(install) == a`; for a
    plain folder, `None`
  - `test_the_conversion_swaps_a_checkout_for_a_link`: a checkout at `install`, a version folder
    `v`: `swap_in_link` returns `versions/legacy-<12>`, which holds the checkout's files, and
    `install` is a link to `v`
  - `test_prune_keeps_what_it_is_told`: three worktrees and a legacy folder; `prune(keep={b, c})`
    removes the other two, and `git worktree list` no longer names them
  - `test_prune_skips_a_folder_in_use`: a child process with its working folder in `a`; `prune`
    leaves `a`

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_versions.py -q`
  Expected: errors importing `versions`.

- [ ] **Step 3: Implement `lib/versions.py`.**

- [ ] **Step 4: Run the tests to see them pass** (CI runs them on Linux too)

  Run: `uv run pytest tests/unit/test_versions.py -q && uv run mypy --platform linux lib/versions.py`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/versions.py tests/unit/test_versions.py
  git commit -m "Version folders, and a link switched in one rename"
  ```

### Task 6: Every write checks the store first

**Files:**

- Create: `lib/update_gate.py`, `tests/unit/test_update_gate.py`
- Modify: `lib/ledger.py` (`for_writing`, `main`), `lib/knowledge.py` (`opened(write=True)`,
  `main`), `lib/hook.py` (`main`), `lib/guard.py` (its entry point), `lib/console_kb.py`,
  `lib/console_server.py` (`write_refusal`), `docs/design/console.md` (the refusal)

**Interfaces:**

- Consumes: `update_state.read`, `in_freeze`, `running`; `update_backups.restore`;
  `versions.point`; `install_paths.install_bin`
- Produces:
  - `class StoreNewer(Exception)(store: str, found: int, mine: int)`
  - `class UpdateCutShort(Exception)(state: UpdateState)`
  - `class UpdateBusy(Exception)`: a freeze recorded while its update still runs
  - `RERUN_ENV = "BOSUN_RERUN"`
  - `check_write(store: str, found: int, mine: int, state_dir: Path) -> None`: called with the
    store's exclusive lock held; raises `UpdateCutShort` when a freeze step is recorded and
    `running()` is false, `UpdateBusy` when it is true, `StoreNewer` when `found > mine`
  - `guarded(command: str, run: Callable[[list[str]], int], argv: list[str]) -> int`: wraps a
    CLI's main. It records what is read of stdin. On `StoreNewer` it re-runs `[install_bin(root),
    command, *argv]` with the recorded stdin and the rest, `RERUN_ENV=1`, and returns its exit
    code; with `RERUN_ENV` already set it says the store is newer than the install too and returns
    1. On `UpdateCutShort` it prints one line ("an update from <from 7> to <to 7> was cut short
    while writes were paused; putting back its backup and <from 7>"), calls `recover`, then
    re-runs the same way.
  - `recover(state_dir: Path, config_dir: Path) -> bool`: takes `update.lock`, then the stores'
    locks in order, reads the record again (another process may have finished it: `False`), restores
    `state.restorable` from `state.backup`, points the link back at `state.from_root`, records
    `restored`; `True`

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_update_gate.py`:
  - `test_a_store_at_its_format_is_written`: `check_write("ledger", 1, 1, d)` returns
  - `test_a_newer_store_is_refused`: `check_write("ledger", 2, 1, d)` raises `StoreNewer`
  - `test_a_freeze_left_by_a_dead_update_is_cut_short`: `update.json` at `migrated`, no lock held:
    `UpdateCutShort`
  - `test_a_freeze_of_a_running_update_is_busy`: the same with `update.lock` held by a child
    process: `UpdateBusy`
  - `test_a_newer_store_reruns_with_the_same_stdin` (Review Focus 1): a `run` that reads stdin
    then raises `StoreNewer`; `guarded` runs a stub `bin/bosun` in a temporary install that writes
    its argv and stdin to a file: the same arguments, the same bytes, `BOSUN_RERUN=1`
  - `test_a_second_refusal_never_loops`: with `BOSUN_RERUN=1`, `guarded` returns 1 and runs
    nothing
  - `test_recover_puts_back_the_backup_and_the_version`: a backup, a changed `ledger.json`, the
    link at `to`, the record at `switched` with `restorable=("state/ledger.json",)`: after
    `recover`, the ledger is as backed up, the link leads to `from`, the step is `restored`
  - `test_recover_twice_is_once`: a second `recover` returns `False` and changes nothing

  In `tests/unit/test_ledger.py` and `test_knowledge.py`: a write with `update.json` at `marked`
  and no update running raises `UpdateCutShort` before anything is journaled; a write into a store
  of format `LEDGER_FORMAT + 1` raises `StoreNewer`, and the journal is unchanged.

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_update_gate.py -q`
  Expected: errors importing `update_gate`.

- [ ] **Step 3: Implement.** `check_write` goes at the start of `for_writing` and of the knowledge
  write in `opened`, once the exclusive lock is held. `update_gate` imports neither store module;
  `recover` imports them inside the function, so `ledger.py` can import `update_gate`. The CLIs
  that write run under `guarded`: `ledger.py` and `knowledge.py` mains, `hook.py`'s (the detached
  child re-runs with `BOSUN_HOOK_SYNC=1`), and the guard's entry point. The console's knowledge
  save turns `StoreNewer` and `UpdateCutShort` into a 409 saying bosun was updated and the console
  needs a restart (`bosun console`).

- [ ] **Step 4: Run the tests to see them pass**

  Run: `uv run pytest tests/unit -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/update_gate.py tests/unit/test_update_gate.py lib/ledger.py lib/knowledge.py \
    lib/hook.py lib/guard.py lib/console_kb.py lib/console_server.py tests/unit/test_ledger.py \
    tests/unit/test_knowledge.py docs/design/console.md
  git commit -m "Every write checks the store: old code re-runs on the new version, a cut freeze is put back"
  ```

### Task 7: The migration registry

**Files:**

- Create: `lib/migrations.py`, `tests/unit/test_migrations.py`
- Modify: `lib/ledger.py` and `lib/knowledge.py` (`validate_unlocked`), `CLAUDE.md` (the "A data
  format change" item names the registry and the fixture folder), `tests/fixtures/migrations/`
  (new, with a `README.md` saying one fixture per migration, in its old format)

**Interfaces:**

- Produces:
  - `@dataclass(frozen=True) class Context(state_dir: Path, config_dir: Path)`: the files to
    migrate in place; the caller holds the locks
  - `@dataclass(frozen=True) class Migration(store: str, from_version: int, to_version: int, run:
    Callable[[Context], int], summary: str)`: `run` returns how many records it changed; `summary`
    takes `{n}`, as in `"{n} items migrated"`
  - `@dataclass(frozen=True) class Applied(store: str, from_version: int, to_version: int, line:
    str)`
  - `REGISTRY: tuple[Migration, ...] = ()`: none yet
  - `FORMATS: Mapping[str, int]` (`{"ledger": ledger.LEDGER_FORMAT, "knowledge":
    knowledge_store.KNOWLEDGE_FORMAT}`) and `store_formats(ctx: Context) -> dict[str, int]`
  - `path(store: str, found: int, target: int, registry: Sequence[Migration] = REGISTRY) ->
    list[Migration]`: raises `MigrationError` naming the missing step, and refuses `found > target`
    ("going back is not supported")
  - `run(ctx: Context, registry: Sequence[Migration] = REGISTRY) -> list[Applied]`: each store
    from its format to `FORMATS`, recording the version after each migration
  - `CONFIG_RENAMES: tuple[tuple[str, str], ...] = ()` (old key, new key; `""` for removed)
  - `migrate_config(text: str, renames: Sequence[tuple[str, str]] = CONFIG_RENAMES) -> tuple[str,
    list[str]]` (pure: the new `bosun.env` text and a line per key changed)
  - `write_config(config_dir: Path, now: datetime) -> list[str]`: applies it, keeping
    `bosun.env.pre-update-<time>` when anything changed
  - `USER_FILE_CHANGES: tuple[tuple[str, str], ...] = ()` (one of my files, what to change in
    it): never applied; the plan and the summary list them, and `bosun doctor` prints them
  - `ledger.validate_unlocked(state_dir: Path) -> list[Finding]` and
    `knowledge.validate_unlocked(paths: Paths) -> list[Finding]`: what `ledger validate --journal`
    and `kg validate` find, over a given folder and without taking a lock; their `cmd_validate`
    become the lock plus these

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_migrations.py`:
  - `test_every_store_has_a_path_to_its_format`: for each store, `path(store, 1, FORMATS[store])`
    succeeds over `REGISTRY`
  - `test_a_gap_names_the_missing_step`: a registry with ledger 1->2 and 3->4: `path("ledger", 1,
    4, reg)` raises naming `2 -> 3`
  - `test_going_back_is_refused`: `path("ledger", 3, 2)` raises
  - `test_a_migration_runs_once`: a test migration (ledger 1->2, adds `"x": 1` to each item, over
    `tests/fixtures/migrations/ledger-1.json`): `run` twice gives the same ledger, one `Applied`
    the first time and none the second
  - `test_a_migration_leaves_the_store_valid`: after it, `ledger.validate_unlocked` finds nothing
  - `test_config_renames_keep_the_rest`: `migrate_config("# note\nOLD=1\nKEEP=2\n", [("OLD",
    "NEW"), ("GONE", "")])` gives `"# note\nNEW=1\nKEEP=2\n"` and one line; with `GONE=3` present,
    that line is removed and said
  - `test_the_config_copy_is_kept_only_when_it_changed`
  - `test_validate_unlocked_runs_under_a_held_lock`: with the ledger's exclusive lock held by this
    process, `validate_unlocked` returns within a second

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_migrations.py -q`
  Expected: errors importing `migrations`.

- [ ] **Step 3: Implement.** `validate_unlocked` reads the folder it is given, not the global
  `STORE`, so the rehearsal can check its copy in the same process. The registry's docstring
  states the rule for journals: a migration never rewrites what was recorded. It reads old
  envelopes as new ones (upcasting, in the store's reader) and rebuilds the files derived from
  the journal.

- [ ] **Step 4: Run the tests to see them pass**

  Run: `uv run pytest tests/unit -q && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/migrations.py tests/unit/test_migrations.py tests/fixtures/migrations lib/ledger.py \
    lib/knowledge.py CLAUDE.md
  git commit -m "One migration registry for the stores' formats and the config's keys"
  ```

---

## Part B: `bosun update`

### Task 8: What each window was launched with

**Files:**

- Create: `lib/launch_record.py`, `tests/unit/test_launch_record.py`
- Modify: `libexec/bosun-launch` (`--fingerprint`, and the record before it hands over to the
  agent), `libexec/bosun-refresh` (`--update-restart`, `--checkin-loop`), `lib/uninstall_plan.py`,
  `docs/commands.md` (the two refresh flags)

**Interfaces:**

- Produces:
  - `LAUNCHES_DIR = "launches"`
  - `fingerprint(args: Sequence[str], install: Path) -> dict[str, str]` (pure but for reading files):
    `{"args": <digest>, "files": <digest>}`. `args` is the agent's arguments from
    `build_agent_args`, with the value after `--append-system-prompt` replaced by `<policy>`.
    `files` covers the content of every file under each argument that is a path inside `install`
    (the plugin folder, the settings file). So `chores.md` counts as a launch input, and
    `policy.md` does not.
  - `record(state_dir: Path, window: str, commit: str, harness: str, fp: Mapping[str, str]) ->
    None`; `recorded(state_dir: Path, window: str) -> dict[str, Any] | None`
  - CLI: `launch_record.py fingerprint --install <dir> -- <args...>` (JSON) and `record --window
    <w> --commit <c> --harness <h> -- <args...>`
  - `bosun launch --fingerprint <window>`: prints the fingerprint of what it would launch, and
    launches nothing
  - `bosun refresh --update-restart <prompt-file> <window>`: restarts the window now if it is idle
    or `shell`, never if busy or waiting, with that prompt instead of the drain prompt
  - `bosun refresh --checkin-loop`: restarts the `checkin` window's loop

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_launch_record.py`:
  - `test_the_policy_text_is_not_a_launch_input`: two argument lists differing only after
    `--append-system-prompt` have the same fingerprint
  - `test_a_changed_plugin_file_is`: the same arguments with `--plugin-dir <install>/orchestrator/plugin`,
    and `chores.md` changed: different `files`
  - `test_a_changed_hook_command_is`: `--settings` JSON with one hook more: different `args`
  - `test_a_record_round_trips`

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_launch_record.py -q`
  Expected: errors importing `launch_record`.

- [ ] **Step 3: Implement**, with `("launches", WORKING)` in `STATE_FILES`. `bosun launch`
  records `git -C "$BOSUN_ROOT" rev-parse HEAD` (or `unknown`) with the fingerprint.

- [ ] **Step 4: Add smoke checks:** a dry-run launch writes `launches/<window>.json`;
  `bosun launch --fingerprint` prints the same fingerprint and starts nothing; `refresh
  --update-restart` restarts an idle fake Claude with the prompt and leaves a busy one.

- [ ] **Step 5: Run the checks**

  Run: `uv run pytest tests/unit/test_launch_record.py -q && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 6: Commit**

  ```bash
  git add lib/launch_record.py tests/unit/test_launch_record.py libexec/bosun-launch \
    libexec/bosun-refresh lib/uninstall_plan.py docs/commands.md tests/smoke.sh
  git commit -m "Launch records what each window was started with; refresh restarts for an update"
  ```

### Task 9: The plan

**Files:**

- Create: `lib/update_plan.py`, `tests/unit/test_update_plan.py`

**Interfaces:**

- Produces:
  - `PROMPTS = ("orchestrator/policy.md", "orchestrator/plugin/commands/chores.md",
    "orchestrator/tasks/*.md", "orchestrator/learn.md", "orchestrator/slack-watch.md")`;
    `REREAD = ("orchestrator/policy.md", "orchestrator/tasks/*.md")`
  - `@dataclass(frozen=True) class VersionFacts(commit: str, formats: Mapping[str, int], rules:
    Mapping[str, int], keys: frozenset[str], prompts: Mapping[str, str])`. `prompts` maps a
    relative path to its digest. For the version running now, `formats` and `rules` are the live
    data's. `keys` are those of `examples/generic/bosun.env`.
  - `@dataclass(frozen=True) class WindowFacts(name: str, kind: str, status: str, launch_changed:
    bool)`. `kind` is `orchestrator`, `repo` or `checkin`; `status` is `idle`, `shell`, `busy`,
    `waiting`, `exited` or `unknown`.
  - `class Treatment(StrEnum)`: `LEAVE`, `REREAD`, `RESTART`, `TAKE_STOCK`, `WAIT`, `ASK_FIRST`,
    `RELAUNCH`, `RESTART_LOOP`
  - `treatment(window: WindowFacts, reread: bool) -> Treatment`:

    | kind | launch changed | status | Treatment |
    |---|---|---|---|
    | checkin | any | any | `RESTART_LOOP` |
    | any | no | any | `REREAD` for the orchestrator when `reread`, else `LEAVE` |
    | any | yes | idle | `RESTART` |
    | any | yes | shell | `TAKE_STOCK` |
    | any | yes | busy | `WAIT` |
    | any | yes | waiting or unknown | `ASK_FIRST` |
    | any | yes | exited | `RELAUNCH` |

  - `@dataclass(frozen=True) class Plan(from_commit: str, to_commit: str, commits: tuple[str,
    ...], migrations: tuple[tuple[str, int, int], ...], rules: tuple[tuple[str, int, int], ...],
    keys_added: tuple[str, ...], renames: tuple[tuple[str, str], ...], prompts: tuple[str, ...],
    windows: tuple[tuple[str, Treatment], ...], jobs: tuple[str, ...], console: bool, handed:
    tuple[str, ...], user_changes: tuple[str, ...], blockers: tuple[str, ...])`. `handed` lists
    the items in flight whose task type's file changed: they keep their old text, and the summary
    names them for `bosun delegate --update`.
  - `make_plan(old: VersionFacts, new: VersionFacts, *, commits: Sequence[str], windows:
    Sequence[WindowFacts], jobs: Sequence[str], renames: Sequence[tuple[str, str]], user_changes:
    Sequence[tuple[str, str]], in_flight: Sequence[tuple[str, str]], missing: Sequence[str],
    deletion_cut_short: bool, console: bool, steps: Callable[[str, int, int], Sequence[tuple[int,
    int]]]) -> Plan`. `in_flight` holds an item and its task type. `steps` is `migrations.path`
    reduced to version pairs, raising `ValueError` with its message; it is passed in, so the plan
    stays pure. Blockers:
    - what preflight says is missing;
    - a store newer than the new version ("going back is not supported");
    - no migration path (the `ValueError`'s message);
    - a deletion cut short ("a kg forget was cut short: the next kg write finishes it, then run
      bosun update again").
  - `render_plan(plan: Plan) -> str`: the lists of the spec's step 1, then "Nothing changes until
    you say yes."

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_update_plan.py`:
  - `test_every_pairing_has_one_treatment`: for each `kind`, each `status`, `launch_changed` and
    `reread` both ways, `treatment` gives the table's value (parametrized over the table, 72
    cases)
  - `test_only_the_orchestrator_rereads`: a repo window with `reread=True` and nothing changed:
    `LEAVE`
  - `test_prompts_changed_are_named_and_counted`: `policy.md` and `tasks/review.md` differ, the
    rest equal: `plan.prompts == ("orchestrator/policy.md", "orchestrator/tasks/review.md")`
  - `test_migrations_between_the_formats`: live `{"ledger": 1}`, new `{"ledger": 2}` and a
    `steps` giving `[(1, 2)]`: `plan.migrations == (("ledger", 1, 2),)`
  - `test_handed_items_of_a_changed_task_type`: `tasks/review.md` changed and
    `in_flight=[("task:1", "review"), ("task:2", "fix")]`: `plan.handed == ("task:1",)`
  - `test_a_newer_store_blocks`: live ledger 3, new 2: one blocker saying going back is not
    supported
  - `test_a_deletion_cut_short_blocks`
  - `test_new_keys_are_listed`: `keys_added` is the new example's keys less the old's, sorted
  - `test_the_render_lists_every_part`: `render_plan` of a full plan names the commits count, each
    migration, each prompt, each window with its treatment, each job and the console

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_update_plan.py -q`
  Expected: errors importing `update_plan`.

- [ ] **Step 3: Implement `lib/update_plan.py`.**

- [ ] **Step 4: Run the tests to see them pass**

  Run: `uv run pytest tests/unit/test_update_plan.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/update_plan.py tests/unit/test_update_plan.py
  git commit -m "The update's plan, and what each window gets"
  ```

### Task 10: Rehearse, hold, freeze, release

**Files:**

- Create: `lib/update.py`, `libexec/bosun-update`
- Modify: `bin/bosun` (usage), `lib/common.sh` (`reload_changed_jobs`, used by `install.sh` too),
  `install.sh`, `lib/knowledge.py` (`deletion_cut_short`), `orchestrator/settings.json`
  (`Bash(bosun update note:*)`), `docs/commands.md`, `tests/smoke.sh`

**Interfaces:**

- Consumes: Tasks 2-9
- Produces:
  - `bosun update [--dry-run] [--yes] [--json]`. The wrapper, in whatever version runs it, fetches
    `bosun.branch` into `bosun.git` and adds the new commit's folder (`git worktree add --detach`).
    Then it runs `<new>/bin/bosun update --from "$BOSUN_ROOT" "$@"`, or, when the commit is the
    current one, carries on an update already recorded, else says it is up to date. Its `# Usage:`
    header documents `note` and `advance` (Task 11) as what agents and the check-in loop run.
  - `lib/update.py`:
    - `live_facts(state_dir: Path, config_dir: Path, old_root: Path) -> VersionFacts`
    - `new_facts(root: Path) -> VersionFacts`
    - `window_facts(root: Path) -> list[WindowFacts]` (`bosun sessions --json`, and each window's
      launch record against the new `bosun launch --fingerprint`)
    - `rehearse(state_dir: Path, config_dir: Path, plan: Plan) -> list[str]` (findings; the copy
      deleted whatever happens)
    - `missing(root: Path) -> list[str]` (what the new version's `bosun preflight` reports
      missing) and `console_running(state_dir: Path) -> bool` (from the console's state folder)
    - `freeze(state: UpdateState, plan: Plan, state_dir: Path, config_dir: Path) -> UpdateState`: takes the ledger's and
      the knowledge base's locks, then steps `marked`, `backed-up` (`take`, `restorable` set to
      `FROZEN`, plus `config/bosun.env` when it renames keys),
      `migrated`, `switched` (`versions.point`), checks (`validate_unlocked`, both stores), and
      `checked`, each recorded before it starts. It records `freeze_ms`, and on any error restores,
      points back and records `failed` with the error.
    - `release(state: UpdateState) -> UpdateState`: `reload_changed_jobs` (new version), lift the
      hold, `bosun refresh --checkin-loop`, `bosun doctor`'s findings kept for the summary, prune
      backups and version folders
    - `resume(state_dir: Path) -> UpdateState | None`: an update recorded and not running:
      before the freeze, lift the hold; inside it, `update_gate.recover`; after `checked`, go on
      from the step after the recorded one
  - `knowledge.deletion_cut_short() -> bool` (a deletion marker among the unfinished changes,
    under the shared lock)
  - shell: `reload_changed_jobs [--dry-run]`: renders each plist, reloads only those that differ
    from the installed file, prints their labels (with `--dry-run`, the plan's `jobs`)

- [ ] **Step 1: Write the smoke tests first** (a section "bosun update", hermetic, launchd
  faked). The setup:
  - a scratch `upstream.git` holds two commits: v1 (the working tree, from `version_folder`) and
    v2 (v1 patched by `tests/sandbox/update_patch.sh`);
  - the patch bumps `LEDGER_FORMAT` to 2 with a registry entry that adds a field and sleeps
    `UPDATE_PATCH_SLEEP` seconds, changes one line of `policy.md`, and records the new digest;
  - `$WORK/share/bosun-versions/bosun.git` is cloned bare from upstream at v1, with a worktree for
    v1 and the link `$WORK/share/bosun` to it.

  So `$WORK/share/bosun/bin/bosun update` runs the wrapper's fetch and worktree too. The checks:

  - `--dry-run` prints the plan (1 migration, `policy.md`) and changes nothing (digest of both
    folders before and after);
  - without `--yes` and no terminal, it prints the plan and stops;
  - `--yes`: the link leads to `v2`, `ledger.json` is format 2, `ledger validate --journal` is
    clean, a backup with a manifest exists, the record says `done`;
  - a ledger write started under `v1` during the freeze (held open by the patch's sleep): it
    waits, then writes through the re-run on `v2` and is in the ledger once;
  - a PreToolUse guard hook with a fixed payload during the freeze prints the same decision as
    without one (Review Focus 1);
  - a failing migration (`update_patch.sh --failing`): everything is as before, the link at `v1`,
    the record `failed` with the error;
  - an update cut short at each of `marked`, `backed-up`, `migrated` and `switched` (Review
    Focus 2). The state each step leaves is laid out by hand: the record at the step, the backup
    from `backed-up` on, the migrated ledger from `migrated` on, the link at v2 at `switched`, and
    no update running. The next `bosun ledger` write prints the cut-short line, the ledger and the
    link are as before, and the write lands. Once more for real: `kill -9` of the update during
    the patch's sleep. At `checked`, the next `bosun update` rolls forward;
  - a second `bosun update` while one runs is refused, naming when it started;
  - a `kg forget` cut short: the update stops at the plan with the blocker;
  - each hook command of `v1`'s launch settings runs against `v2` and exits 0.

- [ ] **Step 2: Run them to see them fail**

  Run: `tests/smoke.sh`
  Expected: the new section fails (`bosun: unknown command 'update'`).

- [ ] **Step 3: Implement.** Shipped code has no test hook: the only pause is the patched
  migration's sleep, which exists only in `update_patch.sh`'s v2. The plan asks first (`--yes`
  skips it), as `init` and `uninstall` do.

- [ ] **Step 4: Run the checks**

  Run: `uv run pytest tests/unit -q && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/update.py libexec/bosun-update bin/bosun lib/common.sh install.sh lib/knowledge.py \
    orchestrator/settings.json docs/commands.md tests/smoke.sh tests/sandbox/update_patch.sh
  git commit -m "bosun update: rehearse, hold, freeze briefly, switch, check, put back on failure"
  ```

### Task 11: Bringing the agents along

**Files:**

- Create: `lib/update_agents.py`, `tests/unit/test_update_agents.py`
- Modify: `lib/update.py`, `libexec/bosun-update` (`note`, `advance`), `libexec/bosun-checkin`
  (runs `bosun update advance` each tick while the record has windows pending),
  `orchestrator/policy.md` (one line: answer bosun's update prompts with `bosun update note`),
  `orchestrator/tasks/_common.md` (the same for repo sessions), `tests/llm/cases.py`,
  `docs/orchestrator.md`

**Interfaces:**

- Consumes: `update_plan.Treatment`, `treatment`; `bosun refresh --update-restart`; `bosun send`
- Produces:
  - `TAKE_STOCK_PROMPT` and `UPDATE_PROMPT` (format strings; first drafts below, their final words
    settled in the VM):
    - take stock: "bosun will restart this session for an update ({from_} -> {to}), which ends
      your background work. List each background task still running and what you would do when
      it finishes, and save the list with `bosun update note --window {window} --stock` (the list
      on stdin). Then stop and wait; start nothing new."
    - update: "bosun was updated from {from_} to {to}; this session was {how}. {reread}Check for
      anything missed just before: {catch_up}. {stock}Then continue what you were doing, and
      send a one-line status with `bosun update note --window {window} --status` (on stdin)."
      `catch_up` for the orchestrator is a check-in, covering the events queued during the update
      and Slack since its last scan. For a repo session it is its items in the ledger and any
      replies.
  - `note(state_dir: Path, window: str, kind: str, text: str) -> None` (`update/notes/<window>.<kind>`)
  - `prompt_for(window: str, kind: str, state: UpdateState, how: str, reread: Sequence[str], stock:
    str) -> str`
  - `advance(state_dir: Path, statuses: Callable[[], Mapping[str, str]], act: Actions) -> list[str]`:
    for each pending window, re-decides its treatment from its status now and acts once:
    re-read, restart, take stock (then restart once the stock note is there and the window is
    idle or `shell`), relaunch. It drops a window from `pending` once its status note is there,
    and returns the windows still waited for. `Actions` is a protocol with `send(window, text)`,
    `restart(window, prompt)` and `relaunch(window, prompt)`, so the tests run without tmux.
  - `bring_along(state: UpdateState, plan: Plan) -> UpdateState`: sets `pending`, calls `advance`
    every 5 s, prints the windows waited for when that list changes; Ctrl-C stops waiting and
    leaves `pending` to the check-in loop

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_update_agents.py`, with a fake
  `Actions` that records calls and a `statuses` the test changes between calls:
  - `test_an_idle_window_is_restarted_with_the_update_prompt`
  - `test_a_shell_window_takes_stock_first`: first `advance` sends the take-stock prompt only;
    after the stock note, the next restarts it with a prompt holding the stock text
  - `test_a_busy_window_waits`: no call while busy; restarted once idle
  - `test_a_window_busy_again_is_not_restarted` (Review Focus 5): planned `RESTART`, busy at
    `advance`: no restart, still pending
  - `test_a_waiting_window_is_never_touched`
  - `test_the_orchestrator_rereads_without_a_restart`: one `send` naming `policy.md`
  - `test_done_when_every_window_answered`: `advance` returns `[]` once each pending window has a
    status note, and `pending` is empty
  - `test_each_window_is_acted_on_once`: three `advance` calls in the same state send one prompt

  In `tests/llm/cases.py`: the take-stock prompt to a repo session with a background `sleep`
  leads it to run `bosun update note --window <w> --stock`; the update prompt to a restarted one
  leads to `bosun update note --window <w> --status`.

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_update_agents.py -q`
  Expected: errors importing `update_agents`.

- [ ] **Step 3: Implement**, then add the smoke checks: an idle, a `shell` and a busy fake Claude
  window, with `v2` changing the repo sessions' hook settings. Each is restarted as the table
  says, the busy one only once it is idle. A fake answering with `bosun update note` ends the wait.
  After Ctrl-C, the check-in loop's `advance` finishes the busy one.

- [ ] **Step 4: Run the checks**

  Run: `uv run pytest tests/unit/test_update_agents.py -q && tests/smoke.sh && uv run python tests/llm/free_text.py --only <the two new case ids>`
  Expected: all pass (the free-text run costs a few cents).

- [ ] **Step 5: Commit**

  ```bash
  git add lib/update_agents.py tests/unit/test_update_agents.py lib/update.py libexec/bosun-update \
    libexec/bosun-checkin orchestrator/policy.md orchestrator/tasks/_common.md tests/llm/cases.py \
    docs/orchestrator.md tests/smoke.sh
  git commit -m "bosun update brings every agent along: take stock, restart when idle, catch up, continue"
  ```

### Task 12: The summary

**Files:**

- Create: `lib/update_summary.py`, `tests/unit/test_update_summary.py`
- Modify: `lib/update.py` (prints it, saves `summary.md`, `--json`), `libexec/bosun-doctor`

**Interfaces:**

- Produces:
  - `@dataclass(frozen=True) class Summary(from_commit: str, to_commit: str, commits: int,
    seconds: int, data: tuple[tuple[str, str], ...], config: tuple[str, ...], prompts: tuple[str,
    ...], jobs: tuple[str, ...], agents: tuple[tuple[str, str], ...], handed: tuple[str, ...],
    user_changes: tuple[str, ...], backup: str, backup_bytes: int, backup_gone: bool, findings:
    tuple[str, ...])`
  - `build(state: UpdateState, plan: Plan, applied: Sequence[Applied], config_lines: Sequence[str],
    jobs: Sequence[str], findings: Sequence[str], backup_exists: bool, backup_bytes: int, seconds:
    int) -> Summary`
  - `render(summary: Summary) -> str`; `as_json(summary: Summary) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_update_summary.py`:
  - `test_the_text_matches_the_design`: the design's example (fictional windows `api`, `web`)
    built from its parts renders exactly the block in `docs/design/update.md`, "The summary"
  - `test_nothing_changed_says_so`: no migrations, no config, no prompts: `Data` reads
    `unchanged` per store, and there are no `Config` or `Prompts changed` lines
  - `test_a_backup_a_forget_deleted_is_said_so` (Review Focus 4): `backup_gone=True` renders
    `Backup: deleted by a kg forget since; going back past it is no longer possible`, and no
    path
  - `test_handed_items_and_my_files_are_named`: `handed=("task:1",)` renders a line naming it
    with `bosun delegate --update task:1`; a user file change renders under `Config` as "change
    by hand"
  - `test_the_json_carries_the_same`: `as_json` has every field, the backup path absolute

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_update_summary.py -q`
  Expected: errors importing `update_summary`.

- [ ] **Step 3: Implement**, then `lib/update.py` prints it at the end, writes `summary.md` into
  the backup folder if it is still there, and with `--json` prints `as_json` instead. `bosun
  doctor` prints the latest record's `user_changes` as `warn` lines.

- [ ] **Step 4: Run the checks**

  Run: `uv run pytest tests/unit/test_update_summary.py -q && tests/smoke.sh`
  Expected: all pass, the smoke update's output ending with the summary.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/update_summary.py tests/unit/test_update_summary.py lib/update.py libexec/bosun-doctor \
    tests/smoke.sh
  git commit -m "The update ends with a summary: data, config, prompts, jobs, agents, the backup"
  ```

### Task 13: The new layout on install, the first update, and uninstall

**Files:**

- Modify: `get.sh` (and its header), `lib/update.py` (`convert`), `lib/uninstall_detect.py`,
  `lib/uninstall_plan.py`, `lib/uninstall.py`, `tests/unit/test_uninstall_plan.py`,
  `tests/unit/test_uninstall.py`, `tests/smoke.sh` (get.sh dry runs), `tests/sandbox/fresh_user.sh`,
  `docs/design/init.md`, `docs/design/uninstall.md`

**Interfaces:**

- Produces:
  - `get.sh`:
    - a first install clones `bosun.git` bare and records `bosun.managed true` and `bosun.branch`
      in it. It adds the commit's folder, makes the link and starts `bosun init` as today.
    - an existing install, a link or a checkout, fetches and adds the new folder, then execs
      `<new>/bin/bosun update --from <the current folder>`.
    - `--dry-run` says which of these it would do, and `--unattended` passes `--yes`.
  - `convert(old_checkout: Path, new_root: Path, args: Namespace) -> int` in `lib/update.py`, for
    `--from` a plain checkout:
    - it refuses with what `uninstall_detect.checkout_work` reports, if anything;
    - it waits until every window is idle with nothing running and nobody has typed for
      `BOSUN_ACTIVE_CLIENT_MINUTES`, printing what it waits for;
    - it stops the watchdog and learning jobs and the check-in loop;
    - then the backup, the migrations and the checks, with `versions.swap_in_link` as the switch;
    - finally the jobs are reloaded on the link, the check-in loop restarted, and every window
      brought along as launch-changed (none has a launch record).
  - uninstall: the link, `bosun-versions/` (the repository's `bosun.managed`), and the legacy
    checkout, each with its work checked as the checkout's is today

- [ ] **Step 1: Write the failing tests:**
  - `tests/unit/test_uninstall_plan.py`:
    - `test_the_new_layout_goes_whole`: facts with a link, a versions folder of two worktrees
      and a legacy checkout plan their removal, last of all.
    - `test_work_in_a_version_folder_keeps_it`.
  - smoke, get.sh dry runs in a temporary HOME:
    - with nothing, it would clone bare and link;
    - with a checkout, it would convert;
    - with a link, it would update.

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_uninstall_plan.py -q && tests/smoke.sh`
  Expected: the new tests fail.

- [ ] **Step 3: Implement**, and make the Docker sandbox's first day (`tests/sandbox.sh init`)
  install the new layout: its checks read the link and the versions folder.

- [ ] **Step 4: Run the checks**

  Run: `uv run pytest tests/unit -q && tests/smoke.sh && tests/sandbox.sh init`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add get.sh lib/update.py lib/uninstall_detect.py lib/uninstall_plan.py lib/uninstall.py \
    tests/unit/test_uninstall_plan.py tests/unit/test_uninstall.py tests/smoke.sh \
    tests/sandbox/fresh_user.sh docs/design/init.md docs/design/uninstall.md
  git commit -m "Versions side by side from the first install; the first update converts at a quiet moment"
  ```

---

## Part C: tried for real

### Task 14: The VM: the conversion, then a mid-afternoon update

**Files:**

- Modify: `tests/work_e2e.sh`, `tests/sandbox/acme_week.sh` (a busy afternoon), `tests/sandbox/e2e_lib.sh`,
  `tests/sandbox/mac.sh` (`live`: the update prompts), `docs/development.md` (the VM scenario)

**Interfaces:**

- Consumes: everything above; `tests/sandbox/update_patch.sh` (Task 10)

- [ ] **Step 1: Extend `tests/work_e2e.sh`.**
  - "the upgrade", as today the piped `get.sh` from the release to this checkout, is now the
    conversion. Check that the link and `bosun-versions/` exist, the checkout is set aside as
    `legacy-<commit>`, the ledger and the knowledge base read as before, and the summary is
    printed.
  - A new day, "a mid-afternoon update", after two more days of work:
    1. commit B is this checkout plus `update_patch.sh`, pushed to the VM's own origin;
    2. while a stand-in session is busy, one waits on a background `sleep` (`shell`), and Slack
       stand-in events arrive during the hold, `bosun update --yes` runs.
  - It checks:
    - no event, ledger or knowledge write or hook record is lost (counts before and after);
    - each window is brought along by the table, the `shell` one's `sleep` started again from
      its stock note;
    - the summary names the migration, `policy.md` and the backup;
    - the backup restored into a scratch state folder passes the previous version's
      `ledger validate --journal`;
    - after a `kg forget` of a fictional person, no backup or rehearsal copy holds their id
      (`grep -r`);
    - each hook command of the base version's launch settings runs against B;
    - the freeze, recorded as `freeze_ms` on the year of data that `acme_week.sh` builds, is
      under 10 000 ms.

- [ ] **Step 2: Extend `mac live claude|codex`:** the take-stock and update prompts to a real
  session with a background task. It lists the task and runs `bosun update note --stock`, then
  after the restart starts it again and sends its status. Record what the real agents did in
  `docs/design/update.md` (the prompt words, and whether a running Claude picks up a changed
  `chores.md`, which decides if it stays a launch input).

- [ ] **Step 3: Run them**

  Run: `tests/sandbox.sh mac work && tests/sandbox.sh mac live claude && tests/sandbox.sh mac live codex`
  Expected: all checks pass. If the freeze is over 10 s, stop and bring the measurement to the
  user: the spec's fallback (post-call approval hooks saving their record for the next write) is
  built only then.

- [ ] **Step 4: Commit**

  ```bash
  git add tests/work_e2e.sh tests/sandbox/acme_week.sh tests/sandbox/e2e_lib.sh tests/sandbox/mac.sh \
    docs/development.md docs/design/update.md
  git commit -m "The VM converts a release, then updates mid-afternoon with nothing lost"
  ```

### Task 15: The docs, end to end

**Files:**

- Modify: `docs/setup.md` (Updating, Going back, a forget deletes the backups), `README.md`,
  `docs/design/update.md` (status: built, with what the VM settled), `CLAUDE.md` (Where things
  live: the update's modules; Rolling a change out: `bosun update`), `ROADMAP.md` if it lists
  updating

- [ ] **Step 1: Write the docs.** `docs/setup.md` gets "Updating" and "Going back", which the
  summary's last line points to. "Going back" is the older version's folder (`bosun-versions/`, or
  `git worktree add` of the commit) with the backup restored by hand, and not past a `kg forget`.

- [ ] **Step 2: Check them**

  Run: `uv run pre-commit run --all-files && git ls-files | xargs uv run python tools/forbidden_terms.py`
  Expected: all pass; every doc the plan's tasks named mentions `bosun update` where it used to say
  "run get.sh again".

- [ ] **Step 3: Commit**

  ```bash
  git add docs/setup.md README.md docs/design/update.md CLAUDE.md ROADMAP.md
  git commit -m "Docs: updating and going back"
  ```
