# The control panel and starting at login: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** bosun.app becomes a control panel that is there from login whatever the hour. bosun starts itself at
login during your hours. Stop and Quit are separate actions. A Settings window (General, Schedule) changes
settings through a new `bosun settings` command.

**Architecture:**

- **The CLI decides and the app shows.** A login launchd job runs `bosun morning --at-login`, behind a pure
  decision (`lib/login_start.py`).
- **`bosun status --json` gains the schedule.** The contract only grows.
- **`bosun settings get|set` is the only way the app reads or changes `bosun.env`.** It writes through `bosun
  init`'s writer.
- **The app's new behaviour is pure, in `BosunMenuCore`:** the menu, the Stop and Quit flows, and the Settings
  model. The SwiftUI view in `BosunMenu` only renders it.

**Tech stack:**

- Bash 3.2 and Python 3.13: unittest under pytest, and the hermetic smoke suite.
- launchd plists.
- Swift 6: AppKit plus one SwiftUI view through `NSHostingController`.

**Spec:** [docs/design/control-panel.md](../design/control-panel.md), approved 2026-11-14. It extends
[docs/design/menu-bar.md](../design/menu-bar.md).

## Global constraints

- **Contracts only grow:** add fields, never rename or remove them.
  - `status --json` stays contract 1.
  - `settings get|set --json` start at contract 1.
  - Fixtures are made by `uv run python tools/contract_samples.py`, never by hand.
- **Only `bosun init`'s writer writes `bosun.env`:** `init_plan.set_settings` plus `init.write_with_backup`.
  The app never writes config.
- **Follow CLAUDE.md's checklists:**
  - "A launchd job", "A config key", "A command", "An answer `bosun init` offers", "A menu item";
  - "A file bosun writes in its state folder" (`STATE_FILES`);
  - smoke `covers` and `tests/smoke_selection.txt`;
  - `impacted_map` for tests that run bosun's programs.
- **Shell:** macOS `/bin/bash` 3.2, so no `mapfile` or associative arrays. Complexity limits: Python cyclomatic 10,
  cognitive 15, 60 lines; shell 15 branches and 60 lines.
- **App:**
  - `BosunMenuCore` stays pure (no I/O, no AppKit or SwiftUI).
  - The 95% line floor holds for `BosunMenuCore` and `BosunMenuSystem`.
  - SwiftLint and `swift format --strict` pass.
  - `app/VERSION` becomes `0.1.3`.
- **Refusals of what the person typed** use `refuse` (lib/common.sh), from the pull request "Usage errors and
  refusals are not alerts". Rebase on it once merged; until then use `die_usage` for bad values.
- **Testing limits:**
  - Never run `install.sh`, `bosun init`, `get.sh`, `bosun update` or `bosun uninstall` on this Mac; the Mac VM
    runs them.
  - Fictional data only; no real names or ids.
  - Docs change in the same pull request as the code.
- **Copy pinned by this plan.** The design's table is the source, and tests assert these exact strings:
  - `Resting: outside your hours · next start Mon 08:00`, and `Resting: outside your hours` when no start is
    scheduled;
  - `bosun is running · outside your hours`;
  - the menu items `Start bosun`, `Start bosun now`, `Settings…` (⌘,), `Stop bosun…` and `Quit bosun.app` (⌘Q).

## Review focus

These are the inputs most likely to bite that the spec implies but no other test covers. Each has a test in the
task named.

1. **The morning job and a login fire within seconds of each other**, for example a login at 08:00:30 with
   `BOSUN_START_TIME=08:00`. Expected: one session and no restart. The login run skips with "bosun is running
   already" once the session exists (Task 3, `test_running_already`). The window between the two checks is the
   morning run's own existing handling (`BOSUN_ON_EXISTING`).
2. **Hours that end at midnight** (`BOSUN_AWAKE_UNTIL` at or before the start means midnight). Expected: a
   login at 23:30 on a scheduled day starts bosun (Task 3, `test_until_midnight`).
3. **A `bosun.env` line the window cannot show**, for example `BOSUN_DAYS="weekdays"`, or a file it cannot read.
   Expected: `settings get` shows the raw value with `valid: false`, never the default. A `set` of another key
   leaves that line as it is (Task 6, `test_get_shows_an_unknown_value_as_is`,
   `test_set_leaves_other_lines`).
4. **`settings set` while `bosun update` runs.** The update writes `bosun.env` too. Expected: refused in one line,
   exit 1, nothing written (Task 6, the smoke check "refused during an update").
5. **Quit while the status could not be read** (bosun did not answer, or isn't installed). Expected: the app quits
   at once and leaves bosun as it is. It never asks "Leave bosun running?" about a state it doesn't know
   (Task 8, `testQuitWithUnreadableStatusExitsAtOnce`).

---

### Task 1: The Slack watcher follows the session

**Files:**

- Modify: `lib/slack_watch.py` (`schedule_gate`, `Config.awake` and its parsing, the `--status` text at line 858,
  and the module docstring), `libexec/bosun-slack-watch` (its usage header lines 15-20; drop `BOSUN_DAYS
  BOSUN_START_TIME BOSUN_AWAKE_UNTIL` from what it passes if nothing else reads them)
- Modify: `docs/events.md` and `lib/common.sh`'s comment at line 294 ("inside the awake window"), and any doc that
  says the watcher stops at `BOSUN_AWAKE_UNTIL` (`grep -rn "awake window" docs/ | grep -i slack`)
- Test: `tests/unit/test_slack_watch.py`

**Interfaces:** Produces `schedule_gate(config: Config, state: State, now: datetime) -> str | None`, which now
only returns `NOT_DUE` or None. The check-in loop runs the watcher only while the session runs, which is the
rule.

- [ ] **Step 1: Write the failing test** `test_runs_outside_the_awake_window`. The config is days `MTWRF`,
  08:00 to 20:00. At Saturday 23:00, with no last run, `schedule_gate(...)` is `None`. At 23:00, with the last run
  started 1 minute earlier and a 10-minute interval, it is `slack_watch.NOT_DUE`.

- [ ] **Step 2: Run the test and confirm it fails.** `uv run pytest tests/unit/test_slack_watch.py -k
  awake_window </dev/null`: it fails, returning "outside the awake window".

- [ ] **Step 3: Remove the awake-window test from `schedule_gate`.** Also remove `Config.awake` and its
  `START_TIME`/`AWAKE_UNTIL` parsing, unless something else in the module uses them. Delete tests that asserted
  the old gate, and say so in the commit.

- [ ] **Step 4: Run the tests.** `uv run pytest -n auto --impacted </dev/null`, with an expected duration under
  1 min: they pass. Then run `tests/smoke.sh --only slack-watcher` (a few minutes): it passes.

- [ ] **Step 5: Commit** "Slack watcher: runs while the session runs, whatever the hour (control panel)", with
  the docs listed.

### Task 2: The schedule in `bosun status --json`

**Files:**

- Modify: `lib/menu_status.py` (`not_running_sentence`, `running_sentence`, `snapshot`, plus a new `in_hours`)
- Modify: `tools/contract_samples.py`: regenerate `status-resting`, and add `status-running-outside-hours`
- Modify: `app/Tests/BosunMenuCoreTests/Fixtures/*.json`, written by the tool
- Test: `tests/unit/test_menu_status.py`, `tests/unit/test_contract_samples.py`

**Interfaces:**

- Produces `menu_status.in_hours(window: awake.AwakeWindow | None, now: datetime) -> bool | None`, which is None
  when the settings did not load.
- The snapshot gains `"schedule": {"in_hours": bool | None, "next_start": str | None}`. `next_start` is
  `next_start(...)` as `astimezone().isoformat(timespec="minutes")`, or None.
- Task 8 reads `schedule.in_hours`.

- [ ] **Step 1: Write the failing tests.** Use the existing facts helpers in the test file, `MTWRF`, 08:00 to
  20:00:
  - `test_resting_names_the_next_start`: Saturday 2026-11-14 10:00, no window, `GATE_OUTSIDE`. The sentence is
    `"Resting: outside your hours · next start Mon 08:00"`. `snapshot["schedule"]` is
    `{"in_hours": False, "next_start": <2026-11-16 08:00 local, isoformat to the minute>}`.
  - `test_resting_with_no_scheduled_day`: days `""` (the window has none). The sentence is `"Resting: outside
    your hours"`, and `next_start` is None.
  - `test_running_outside_hours`: Saturday 10:00, one window open, level ok. The sentence is
    `"bosun is running · outside your hours"`. With a warning finding it is `"bosun is running · outside your
    hours, with a warning"`.
  - `test_running_in_hours_unchanged`: Monday 10:00, one window. The sentence is `"bosun is running"`, and
    `schedule.in_hours` is True.
  - `test_schedule_unknown_without_settings`: `awake=None`. The schedule is
    `{"in_hours": None, "next_start": None}`.

- [ ] **Step 2: Run the tests and confirm they fail.** `uv run pytest tests/unit/test_menu_status.py -k
  "resting or outside or schedule" </dev/null`.

- [ ] **Step 3: Implement it.** Add `in_hours`. Use `with_level` for both new sentences. Add the `schedule` key to
  `snapshot`, after `"stopped"` (the key order is the samples').

- [ ] **Step 4: Regenerate the fixtures.** Add the `status-running-outside-hours` sample in
  `tools/contract_samples.py` (`snapshot(now=<a Saturday>, ...)`), run `uv run python tools/contract_samples.py`,
  then `uv run pytest -n auto --impacted </dev/null`: they pass.

- [ ] **Step 5: Commit** "status --json: the schedule (in hours, next start) and the resting and outside-hours
  lines".

### Task 3: Starting at login: the setting, the decision and `bosun morning --at-login`

**Files:**

- Create: `lib/login_start.py`, which is pure plus a small CLI
- Modify: `lib/common.sh` (`load_config`: `BOSUN_START_AT_LOGIN="1"`, with a comment), and
  `examples/generic/bosun.env` (the same value and comment)
- Modify: `lib/migrations.py` (`CONFIG_ADDED` entry), `libexec/bosun-morning` (the `--at-login` option, its usage
  lines, and `begin_run`)
- Test: `tests/unit/test_login_start.py`, `tests/unit/test_migrations.py`, plus a smoke check in the
  `morning-now` section (`tests/smoke.sh`), whose `covers` gains `lib/login_start.py`

**Interfaces:**

- Produces this dataclass:
  ```python
  @dataclass(frozen=True)
  class LoginFacts:
      enabled: bool                 # BOSUN_START_AT_LOGIN == "1"
      window: awake.AwakeWindow
      now: datetime                 # local, naive
      quit: quit_state.QuitState | None
      stopped_at: datetime | None   # the stop marker's time (stopped-<session>, "%Y-%m-%d %H:%M:%S")
      running: bool                 # the tmux session exists
      reloaded_at: datetime | None  # the reload marker's time (Task 4)
  def decide(facts: LoginFacts) -> tuple[bool, str]   # (start?, the log line)
  RELOAD_SECONDS = 120
  RELOAD_MARKER = "login-reload"   # in the state folder; Task 4's reload_job writes it ("%Y-%m-%d %H:%M:%S")
  ```
- CLI: `python3 lib/login_start.py decide --state-dir D --session S --days X --start HH:MM --until HH:MM
  --enabled 0|1 --running 0|1`. It prints the line and exits 0 to start, or 3 to skip. It removes the reload
  marker after reading it.
- Reasons, checked in this order (the first that holds wins). The exact text:
  1. `"not starting: BOSUN_START_AT_LOGIN is 0"`
  2. `"not starting: a reload of bosun's jobs, not a login"`. The marker is younger than `RELOAD_SECONDS`.
  3. `"not starting: bosun was quit at HH:MM with the morning start paused"`
  4. `"not starting: not one of your days (MTWRF)"`
  5. `"not starting: outside your hours (08:00-20:00)"`
  6. `"not starting: you stopped bosun today at HH:MM"`
  7. `"not starting: bosun is running already"`
  8. Otherwise: `(True, "starting bosun: you logged in during your hours")`

- [ ] **Step 1: Write the failing tests.** In `tests/unit/test_login_start.py`, one test per reason above, in
  order, each with every earlier reason absent. Add these:
  - `test_until_midnight`: until `00:00`, Monday 23:30 → start;
  - `test_stale_reload_marker_is_a_login`: the marker is 10 minutes old → start;
  - `test_quit_not_paused_starts`: a quit record with `morning_paused=False` → start, as the morning job does;
  - `test_stopped_yesterday_starts`: `stopped_at` was yesterday → start;
  - `test_running_already`.

  In `test_migrations.py`, check that `CONFIG_ADDED` has `BOSUN_START_AT_LOGIN` with kept `"0"` and a note that
  names `BOSUN_START_AT_LOGIN=1`. The existing config-defaults test checks that `common.sh` and the example agree.

- [ ] **Step 2: Run the tests and confirm they fail.** `uv run pytest tests/unit/test_login_start.py
  tests/unit/test_migrations.py </dev/null`.

- [ ] **Step 3: Implement `decide` and the CLI**, reading the quit record with `quit_state.read`.
- [ ] **Step 4: Add the `CONFIG_ADDED` entry and the default.** For `AddedKey("BOSUN_START_AT_LOGIN", "0",
  note, unset)`:
  - the note says this install keeps today's behaviour (bosun starts at the morning time or from Start), and
    that `BOSUN_START_AT_LOGIN=1` makes it start at login during your hours;
  - the unset note says that since this version bosun starts at login during your hours, and `0` keeps the old
    way.

- [ ] **Step 5: Add `--at-login` to `libexec/bosun-morning`.** It takes no other option, so add a `die_usage`
  for the rest. Its run:
  1. Before `begin_run`, run `login_start.py decide` with `--running` from `session_exists`.
  2. Log its line.
  3. Exit 0 on a skip, there: a skip must never write the `=== morning run` header, which the watchdog's morning
     checks (`last_real_run`) would read as a run that never finished.
  4. Otherwise go on as the scheduled run with `BOSUN_TRIGGER=login`: the non-`--now` branch of `begin_run`,
     which ends a quit that did not pause.

  Add a usage line under `--now`.

- [ ] **Step 6: Add the smoke check** to `morning-now`, with a pinned clock as the section's other checks do:
  - `bosun morning --at-login` with `BOSUN_START_AT_LOGIN=0` logs "not starting: BOSUN_START_AT_LOGIN is 0"
    starts no session, and leaves no `=== morning run (trigger=login` line in the morning log;
  - after `bosun stop` it logs "you stopped bosun today";
  - with the setting on, in hours, with no session, it starts the session, and the run's header says
    `trigger=login`.

- [ ] **Step 7: Run the tests.** `uv run pytest -n auto --impacted </dev/null`, then `tests/smoke.sh --only
  morning-now` (a few minutes, with its needs): they pass.

- [ ] **Step 8: Commit** "Start at login: BOSUN_START_AT_LOGIN, the decision, bosun morning --at-login (kept off
  for an older install)".

### Task 4: The login launchd job

**Files:**

- Create: `templates/launchd-login.plist`. Copy `launchd-now.plist`, then change these:
  - `RunAtLoad` is `<true/>`, with a comment that launchd runs it at login, and at a reload, which the run tells
    apart;
  - `ProgramArguments` ends `exec "__BOSUN_ROOT__/bin/bosun" morning --at-login`;
  - the logs are `__LOG_DIR__/login.launchd.out` and `login.launchd.err`;
  - `AbandonProcessGroup` stays true.
- Modify `lib/common.sh`:
  - add `login_label() { echo "$BOSUN_LAUNCHD_LABEL.login"; }`;
  - add it to `job_labels` and to `reload_changed_jobs`' label list;
  - add `render_login_plist`, shaped like `render_now_plist`;
  - add a `render_job` case that renders only when `BOSUN_START_AT_LOGIN = 1`;
  - in `reload_job`, for `$(login_label)`, write `date '+%F %T' >"$(login_reload_marker)"` before it bootstraps;
  - add `login_reload_marker() { echo "$BOSUN_STATE_DIR/login-reload"; }`, the name Task 3's `RELOAD_MARKER`
    reads.
- Modify `lib/uninstall_plan.py`: a `STATE_FILES` row for `login-reload` (WORKING). `login.launchd.*` is covered
  by `*.launchd.out` and `*.launchd.err` already; check this.
- Modify `tests/sandbox/e2e_lib.sh` (`jobs_are_gone` gains `"$LABEL.login"`) and `tests/install_e2e.sh` (a check
  that the login job is loaded after the install and runs `morning --at-login`).
- Modify `docs/setup.md`, where it lists bosun's jobs.
- Test: `tests/unit/test_uninstall_plan.py`, plus the smoke sections that render jobs. Find them with `grep -n
  "render_job\|reload_changed_jobs" tests/smoke.sh`, and add `templates/launchd-login.plist` to their `covers`.

**Interfaces:**

- Consumes `login_start.decide` (Task 3).
- Produces `login_label` and `login_reload_marker`, which Task 6's reloads go through.

- [ ] **Step 1: Write the failing smoke check.** With the fake `launchctl`:
  - `render_job "$(login_label)"` prints a plist containing `morning --at-login` and `<key>RunAtLoad</key>` then
    `<true/>` when `BOSUN_START_AT_LOGIN=1`, and nothing when it is 0;
  - `reload_changed_jobs` with the setting on leaves `$BOSUN_STATE_DIR/login-reload`;
  - `plutil -lint` (or the XML hook) accepts the template.
- [ ] **Step 2: Run the check and confirm it fails.** `tests/smoke.sh --only <that section>`.
- [ ] **Step 3: Implement it.** Do the template, the helpers, the marker and the `STATE_FILES` row.
- [ ] **Step 4: Run the tests.** The section passes, then `uninstall-files` (`tests/smoke.sh --only
  uninstall-files`), then `uv run pytest -n auto --impacted </dev/null`.

- [ ] **Step 5: Commit** "Start at login: its launchd job (rendered only when on), told apart from a reload".

### Task 5: Moving the keep-awake and the wake into shared helpers

**Files:**

- Modify `lib/common.sh`, adding these:
  - `hold_keep_awake <seconds>`: the body of `start_keep_awake` from `caffeinate -u -t 2` onward. It kills the
    old one by its recorded pid only after `ps -p <pid> -o comm=` says caffeinate. It starts a new one with
    `nohup`, then writes the pid file and the record.
  - `release_keep_awake`: the release block from `libexec/bosun-stop` lines 30 on.
  - `record_keep_awake`, moved from `bosun-morning`.
  - `set_wake`: install.sh's lines 82-88, the `wake_time` from `shift_time`, and the `sudo pmset repeat
    wakeorpoweron` with the note about an existing event.
- Modify `libexec/bosun-morning`, `libexec/bosun-stop` and `install.sh` so that each calls the helpers. Behaviour
  and log lines do not change.
- Modify CLAUDE.md's "install.sh, the one place that sets `pmset`" to "`set_wake` in lib/common.sh, which
  install.sh runs".
- Test: the existing smoke sections `morning-now`, `quit`, `stopped-until-started` and the install section must
  pass unchanged.

**Interfaces:** Produces `hold_keep_awake <seconds>`, `release_keep_awake` and `set_wake`, all in lib/common.sh.
Task 6 calls them.

- [ ] **Step 1: Move the code.** Make no logic change.
- [ ] **Step 2: Run the affected sections.** `tests/smoke.sh --only morning-now,quit,stopped-until-started` plus
  the install section (`tests/smoke.sh --changed origin/main` shows which): all pass. shellcheck and shfmt run in
  the commit hook.

- [ ] **Step 3: Commit** "Keep-awake and wake: one helper each in lib/common.sh (no change in behaviour)".

### Task 6: `bosun settings get|set`

**Files:**

- Create: `lib/settings.py`, `libexec/bosun-settings`
- Modify: `bin/bosun` (its usage line), `docs/commands.md` (the table row and a short section),
  `tools/contract_samples.py` (`settings-get.json`, `settings-set.json`, `settings-set-refused.json`), the
  fixtures
- Test: `tests/unit/test_settings.py`, plus a new smoke section `settings`, which covers
  `libexec/bosun-settings,lib/settings.py`

**Interfaces:**

- Produces this, in `lib/settings.py`:
  ```python
  CONTRACT = 1
  JOBS, WAKE, KEEP_AWAKE = "jobs", "wake", "keep-awake"        # what a change needs done
  @dataclass(frozen=True)
  class Setting:
      key: str
      meaning: str                      # one line, shown by the window
      problem: Callable[[str], str | None]   # why <value> is refused, or None
      effects: frozenset[str]
  SETTINGS: tuple[Setting, ...]   # BOSUN_START_AT_LOGIN (0|1; JOBS), BOSUN_UPDATE_CHANNEL (releases|main; none),
                                  # BOSUN_DAYS, BOSUN_START_TIME (JOBS, WAKE), BOSUN_AWAKE_UNTIL (JOBS, KEEP_AWAKE)
  class SettingsError(Exception): ...
  def defaults(example_text: str) -> dict[str, str]       # init_plan.parse_env of examples/generic/bosun.env
  def view(env_text: str | None, error: str | None, defaults: Mapping[str, str], wake: str,
           config_dir: str) -> dict[str, Any]
  def change(env_text: str, assignments: Sequence[str], defaults: Mapping[str, str]) -> Change
  @dataclass(frozen=True)
  class Change:
      text: str                    # the new bosun.env (init_plan.set_settings)
      changed: dict[str, tuple[str, str]]   # key -> (before, after), only real changes
      effects: frozenset[str]
  def wake_owner(schedule: str, env: Mapping[str, str], defaults: Mapping[str, str]) -> str
      # "ours" (pmset's repeating wake is bosun's: init_plan.wake_command would be ""), "none", "other"
  ```
- `view`'s JSON is
  `{"contract": 1, "readable": bool, "error": str|None, "config_dir": str, "wake": "ours"|"none"|"other",
  "settings": {KEY: {"value": str, "default": str, "set": bool, "valid": bool, "meaning": str}}}`.
  - `value` is what `bosun.env` assigns when it sets the key, else the default.
  - `valid` is false when `problem(value)` says why.
- `change` does these:
  - checks the days and times together with `init_plan.schedule_problems`, over `Answers` holding the current
    values with the new ones applied;
  - when the start or the until changes, also sets `BOSUN_LEARN_AT` to `init_plan.learn_at(...)`, as a re-run of
    `bosun init` does, with `JOBS` among the effects;
  - refuses an unknown key, `KEY` without a `=`, or the same key twice, raising `SettingsError` with one line.
- The CLI is `settings.py get|plan|write`:
  - `get` prints the view;
  - `plan KEY=V...` prints the effects one per line, or fails with exit 2 and the reason;
  - `write --stamp S KEY=V...` writes with `init.write_with_backup` and prints the changes as JSON.
- `libexec/bosun-settings`'s usage: `bosun settings get [--json]` | `bosun settings set KEY=VALUE... [--replace-wake] [--json]`.
- The order `set` runs in:
  1. Refuse while an update runs (`update_state.running` or `update_hold_active`): exit 1, "bosun update is
     running; change settings when it ends".
  2. Run `plan`. A bad value is refused with exit 2.
  3. When the effects include `WAKE` and the owner is `ours`, or `other` with `--replace-wake`, run `sudo -v`
     first. With no terminal, refuse: "moving the Mac's wake needs your password: run this in Terminal". If sudo
     fails, exit 1 and write nothing.
  4. `write`.
  5. Run `load_config` again, then `reload_changed_jobs`.
  6. Run `set_wake` when step 3 applied. If it fails, write the backup's text back, reload the jobs again, and
     exit 1.
  7. When the effects include `KEEP_AWAKE` and a keep-awake is held (its pid file names a live caffeinate):
     `hold_keep_awake "$(awake_seconds_left)"`, or `release_keep_awake` when that is outside every window.
  8. Print one line per change: `BOSUN_START_TIME: 08:00 → 09:00`. With `--json`, print `{"contract": 1,
     "changed": {KEY: [before, after]}, "notes": [...]}`, or on failure `{"contract": 1, "error": "..."}`.
     - With owner `other` and no `--replace-wake`, the notes include that the other app's repeating wake was
       left as it is.
- Task 9 consumes the JSON shapes.

- [ ] **Step 1: Write the failing unit tests** in `tests/unit/test_settings.py`, over `view`, `change` and
  `wake_owner`:
  - `test_get_defaults_when_unset`: env `""`. `BOSUN_DAYS` is `{"value": "MTWRF", "default": "MTWRF", "set":
    False, "valid": True}`.
  - `test_get_shows_an_unknown_value_as_is`: `BOSUN_DAYS="weekdays"` gives `value "weekdays"`, `valid False`,
    `set True`.
  - `test_get_unreadable`: `view(None, "Permission denied", ...)` has `readable False` and `error "Permission
    denied"`, and every setting shows its default with `set False`.
  - `test_set_changes_one_line`: a commented `bosun.env` with `BOSUN_START_TIME="08:00"`. Changing it to
    `09:00` keeps every other line and comment in place, `changed` is `{"BOSUN_START_TIME": ("08:00",
    "09:00")}`, and the effects include `JOBS` and `WAKE`.
  - `test_set_moves_learn_at`: changing `BOSUN_AWAKE_UNTIL` to `18:00` also sets `BOSUN_LEARN_AT` to
    `learn_at(...)`.
  - `test_set_leaves_other_lines`: `BOSUN_DAYS="weekdays"` plus setting `BOSUN_UPDATE_CHANNEL=main` leaves the
    days line byte for byte.
  - `test_refusals`: `BOSUN_FOO=1`, `BOSUN_DAYS`, `BOSUN_DAYS=XYZ`, `BOSUN_START_TIME=25:00`,
    `BOSUN_START_AT_LOGIN=yes`, `BOSUN_UPDATE_CHANNEL=beta`, and a key given twice each raise `SettingsError`
    with a one-line message naming the key.
  - `test_same_value_changes_nothing`: `changed == {}` and no effects.
  - `test_wake_owner`: `""` gives `"none"`. pmset showing bosun's wake (`init_plan.pmset_label` of start plus
    delay) gives `"ours"`. Any other repeating wake gives `"other"`.

- [ ] **Step 2: Run the tests and confirm they fail.** `uv run pytest tests/unit/test_settings.py </dev/null`.
- [ ] **Step 3: Implement `lib/settings.py`.**
- [ ] **Step 4: Write the smoke section `settings`.** Use fake `sudo`, `pmset` and `launchctl` in the run's
  folder, adding a fake `sudo` stand-in if there is none. Its checks:
  - `settings get --json` parses, with `contract 1`;
  - `set BOSUN_START_TIME=09:00` changes `bosun.env`, leaves a backup, and makes the morning plist say 09:00;
  - with a fake `pmset` showing bosun's wake, `set` runs `sudo pmset repeat wakeorpoweron MTWRF 09:01:00`;
  - when the fake `sudo -v` fails, `bosun.env` is unchanged;
  - refused during an update: with an update record that says running, `fails_with "bosun update is running"`;
  - a bad value: `fails_with "BOSUN_START_TIME"`, with exit 2.
- [ ] **Step 5: Implement `libexec/bosun-settings`**, plus `bin/bosun`'s usage line and `docs/commands.md`. Then
  run `tests/smoke.sh --only settings` (minutes) and `uv run pytest -n auto --impacted </dev/null`: they pass.

- [ ] **Step 6: Regenerate the contract samples** for `settings get` and `set` with `uv run python
  tools/contract_samples.py`, then commit "bosun settings get|set: the schedule, start at login and the update
  channel, written through init's writer".

### Task 7: The wizard's question

**Files:**

- Modify: `lib/init_plan.py`:
  - `Answers.start_at_login: bool = True`, plus `BOOL_ANSWERS`;
  - `env_groups`: `("BOSUN_START_AT_LOGIN", flag[answers.start_at_login])` beside `BOSUN_MENU_APP`;
  - `recommend`: `start_at_login=on("BOSUN_START_AT_LOGIN", True)`.
- Modify: `lib/init_wizard.py`:
  - `menu_app_options` gets three options when the app can be installed;
  - add `ask_start_at_login`;
  - `ask_all` asks it after answer 3, or when `facts.app_unavailable`;
  - `total_steps` counts it.
- Modify: `docs/design/init.md` (the answers table), `docs/setup.md` (the wizard's questions)
- Test: `tests/unit/test_init_plan.py`, `tests/unit/test_init_wizard.py`

**Interfaces:**

- Options, with the copy pinned:
  - `y`: "Show bosun in the menu bar and start it during your hours" sets `{"menu_app": True,
    "start_at_login": True}`;
  - `m`: "Show bosun in the menu bar only" sets `{"menu_app": True, "start_at_login": False}`, with the preview
    line "It starts at the morning time, or when you click Start.";
  - `n`: "No menu bar app" sets `{"menu_app": False}`.
- The recommended option follows the answers: `y` when both are true, `m` when only the app is, `n` when the app
  isn't.
- `ask_start_at_login` offers `y` "Yes, start it when I log in during my hours" and `n` "No".
- The question's title is "Add bosun to the menu bar, and start it when you log in?".

- [ ] **Step 1: Write the failing tests.**
  - `test_env_has_start_at_login`: `render_env(Answers(start_at_login=False))` contains
    `BOSUN_START_AT_LOGIN="0"`.
  - `test_recommend_reads_it`: env `{"BOSUN_START_AT_LOGIN": "0"}` recommends False, and no env recommends True.
  - `test_answers_json_round_trip` for the new key.
  - Wizard tests, using the scripted `Ui` the file already uses:
    - pressing `m` gives `menu_app True, start_at_login False` and asks no further question;
    - pressing `n` then asks the start question, and `y` gives `start_at_login True`;
    - with `app_unavailable` there is no menu question, but the start question is asked;
    - the step count matches the questions asked.
- [ ] **Step 2: Run the tests and confirm they fail.** `uv run pytest tests/unit/test_init_plan.py
  tests/unit/test_init_wizard.py </dev/null`.

- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run the tests.** `uv run pytest -n auto --impacted </dev/null`, then the Docker walk-through
  `tests/sandbox.sh init` (a few minutes; it drives the wizard through a real terminal): both pass.

- [ ] **Step 5: Commit** "bosun init: the menu bar app and starting at login, one question".

### Task 8: The app: the schedule, the menu, and Stop and Quit apart

**Files:**

- Modify `app/Sources/BosunMenuCore/Contract.swift`:
  - add `Schedule { inHours: Bool?; nextStart: String? }`, and `StatusSnapshot.schedule: Schedule?` (optional, so
    an older bosun still decodes);
  - `offersStart` also covers `.ok` and `.warning` when `running == false && schedule?.inHours == false`.
- Modify `app/Sources/BosunMenuCore/MenuModel.swift`:
  - `ItemID`: remove `openAtLogin` and `quit`; add `settings`, `stopBosun` and `quitApp`;
  - `Title`: `Start bosun now` when `schedule?.inHours == false`;
  - add `Settings…` with key `,`;
  - Open Console is enabled when `status.console.running`;
  - `Stop bosun…` appears when `showsStop`;
  - `Quit bosun.app` has key `q` and is always present.
- Modify `QuitFlow.swift`:
  - `QuitChoices.keepIcon` is renamed `quitsApp` (default false), set by the entry point and never by a box;
  - `QuitStep.done(quitsApp:note:)`;
  - add `public func showsStop(_ s: StatusSnapshot) -> Bool { s.level != .stopped && (s.running != false || s.stopped != nil) }`;
  - add `quitAppAction(for: StatusState) -> QuitAppAction`, which is `.ask` only for a current `.loaded` status
    where `showsStop`, and otherwise `.exitApp`.
- Modify `DialogSpecs.swift`:
  - the boxes drop "Keep bosun in the menu bar";
  - `quitFirstSpec` has title "Stop bosun?" and buttons ["Stop bosun", "Cancel"];
  - `quitUnavailableSpec(_:quitsApp:)` keeps "Quit bosun.app" only when `quitsApp`;
  - add `quitAppSpec`, with title "Quit bosun.app?", the message "bosun keeps running without its menu bar
    icon. Open bosun.app to see it again.", buttons ["Leave bosun Running", "Stop bosun and Quit…", "Cancel"],
    default 0 and cancel 2.
- Modify `AppCoordinator.swift`: `perform` handles `.stopBosun` (the flow with `quitsApp: false`), `.quitApp`
  (`quitAppAction`, then the dialog, then terminate, or the flow with `quitsApp: true`) and `.settings` (Task 9;
  until then a no-op).
- Modify `TestHooks.swift` (the item names), and `app/VERSION` to `0.1.3`.
- Test: `MenuModelTests`, `QuitFlowTests`, `DialogSpecTests`, `CoordinatorQuitTests` and `ContractTests`, which
  decode the new fixtures from Task 2.

**Interfaces:**

- Consumes `schedule` from Task 2.
- Produces `ItemID.settings`, `.stopBosun` and `.quitApp`, plus `QuitChoices.quitsApp`.

- [ ] **Step 1: Write the failing tests.**
  - `testRestingOffersStartNow`: decode `status-resting.json`. The first item is `Start bosun now`, and there is
    no `Stop bosun…`.
  - `testRunningOutsideHours`: `status-running-outside-hours.json` shows `Stop bosun…` and no Start.
  - `testSettingsItemHasCommaKey`.
  - `testOpenConsoleFollowsConsoleRunning`.
  - `testQuitAppAlwaysLast`, with key `q`.
  - `testStopKeepsTheApp`: the flow ends with `.done(quitsApp: false, …)`, and `ui.terminate` is not called.
  - `testQuitAppLeaveRunning`: button 0 terminates without running `quit`.
  - `testQuitAppStopAndQuit`: button 1 runs the Stop flow, then terminates.
  - `testQuitWithUnreadableStatusExitsAtOnce`: for `.unreachable`, `.notInstalled` and a status that is not
    current, the app terminates and shows no dialog.
  - `testQuitCommandHasNoKeepIcon`: `quitCommand` is the same as before.
  - `testOlderBosunWithoutSchedule`: a snapshot JSON without `schedule` decodes, and Start follows the old rule.
- [ ] **Step 2: Run the tests and confirm they fail.** `cd app && swift test --filter
  "MenuModelTests|QuitFlowTests|DialogSpecTests|CoordinatorQuitTests|ContractTests"` (expected under 2 min).

- [ ] **Step 3: Implement it.** Update every existing test that named `.quit`, `.openAtLogin` or `keepIcon`.
- [ ] **Step 4: Run the full app check.** `cd app && swift test --enable-code-coverage -Xswiftc
  -warnings-as-errors && cd .. && uv run python tools/swift_coverage.py`: it passes, and the floor holds.

- [ ] **Step 5: Commit** "bosun.app 0.1.3: Start bosun now outside the hours, Stop bosun… and Quit bosun.app
  apart".

### Task 9: The app's Settings window

**Files:**

- Create: `app/Sources/BosunMenuCore/Settings.swift`, `app/Sources/BosunMenu/SettingsWindow.swift`
- Modify:
  - `Contract.swift` (`SettingsView`, `SettingsSetReply`, decoding Task 6's JSON);
  - `BosunCLI.swift` (`Bosun.settingsGet = ["settings", "get", "--json"]` with a 15 s timeout, and
    `Bosun.settingsSet(_ values: [String: String])`, which runs `["settings", "set"] + sorted "K=V" + ["--json"]`
    with a 30 s timeout);
  - `AppCoordinator.swift` (`openSettings`, `applySettings`);
  - `AppUI` (one method);
  - `AppShell.swift`;
  - `TestHooks.swift` (a `settings` action).
- Test: `app/Tests/BosunMenuCoreTests/SettingsTests.swift`, `CoordinatorSettingsTests.swift`, `TestHookTests`

**Interfaces:**

- Produces these, in the core:
  ```swift
  public struct SettingsState: Equatable, Sendable {
    public let view: SettingsView?        // nil until read, or when it could not be
    public let openAtLogin: Bool
    public let message: String?           // the last failure or note, shown under the form
    public let busy: Bool                 // a set is running: the form is disabled
    public func needsTerminal(_ values: [String: String]) -> Bool   // a WAKE key with view.wake == "ours"
  }
  public enum SettingsEdit: Equatable, Sendable {
    case openAtLogin(Bool)
    case values([String: String])         // one Save of a pane
    case changeWindows                    // Terminal: bosun init
    case openConfigFolder, showLogs
  }
  // AppUI gains:
  func showSettings(_ state: SettingsState, apply: @escaping @MainActor (SettingsEdit) async -> SettingsState)
  ```
- What the coordinator does:
  - `perform(.settings)` reads `settings get` and `app status`, then calls `ui.showSettings`. A second
    `Settings…` brings the same window to the front with fresh values.
  - `applySettings(.values)` does these:
    - when `needsTerminal`, opens Terminal with `bosun settings set K=V...` through `ScriptWriting`, and its
      message says "Finish the change in Terminal: it asks for your password to move the Mac's wake";
    - otherwise runs `settingsSet`, then reads `settings get` again;
    - a refusal or failure is the `message`, and the values come from the fresh read, never the edit.
  - `.openAtLogin` runs `appLogin(on:)` under `Flow.login`, as the old menu item did.
- The window, in `BosunMenu` only:
  - an `NSWindow` titled "bosun Settings", hosting a SwiftUI `TabView` with "General" and "Schedule", each a
    `Form` with `.formStyle(.grouped)`;
  - General: two toggles ("Open bosun.app at login", "Start bosun when you log in, during your hours"), a picker
    "Updates from" (Releases, main), and buttons "Change windows and connectors…", "Open Config Folder" and
    "Show Logs";
  - Schedule: the day toggles M T W T F S S, start and until pickers (`DatePicker` with hour and minute), the
    line that says the schedule back, and a Save button.
  - A setting with `valid: false` shows its raw value and the line "bosun.env says "<value>": fix it with Change
    windows and connectors…", and its control is disabled.
- The test hook: `{"settings": {"BOSUN_START_TIME": "09:00"}}` calls `applySettings(.values(...))`, and
  `{"settings": {"openAtLogin": false}}` calls `.openAtLogin(false)`. The hook's menu JSON gains the settings
  state.

- [ ] **Step 1: Write the failing tests.**
  - `testNeedsTerminalOnlyForOursWake`: `BOSUN_START_TIME` with wake `"ours"` → true; with `"none"` or
    `"other"` → false; `BOSUN_AWAKE_UNTIL` → false.
  - `testApplyRunsSetThenRereads`: the fake CLI sees `settings set BOSUN_UPDATE_CHANNEL=main --json`, then
    `settings get --json`.
  - `testApplyFailureKeepsReadValues`: the set fails with `{"error": "…"}`, the message is that line, and the
    values are the re-read ones.
  - `testWakeMoveOpensTerminal`: the script writer gets `["settings", "set", "BOSUN_START_TIME=09:00"]`, and
    there is no `settings set` through the CLI.
  - `testInvalidValueShownAsIs`.
  - `testSecondOpenRefreshes`.
  - `testHookSettingsAction`.
- [ ] **Step 2: Run the tests and confirm they fail.** `cd app && swift test --filter
  "SettingsTests|CoordinatorSettingsTests|TestHookTests"`.

- [ ] **Step 3: Implement the core, then the window.** The window has no logic beyond binding the state and
  calling `apply`.

- [ ] **Step 4: Run the full app check.** `swift test --enable-code-coverage -Xswiftc -warnings-as-errors`, the
  coverage floor, SwiftLint, and `swift format lint --strict` (the commit hook runs them on a Mac): all pass.
  Then `app/build.sh --identity -` makes a bundle that verifies.

- [ ] **Step 5: Commit** "bosun.app 0.1.3: Settings… (General, Schedule) over bosun settings".

### Task 10: The Mac VM, the docs, and the note's status

**Files:**

- Modify: `tests/app_e2e.sh` (run with `tests/sandbox.sh mac app`). Its steps:
  1. Turn Open at Login off through the settings hook instead of the removed item.
  2. Stop bosun → the panel stays with "Stopped"; then Start.
  3. Quit bosun.app → Leave Running, so the tmux session is still there; reopen the app.
  4. A settings hook moves `BOSUN_START_TIME`, and the morning plist follows. The wake owner here is `none`,
     unless the clone has bosun's wake.
  5. Set the clock outside the hours, and the status says "Resting: outside your hours · next start …".
  6. Restart the clone during hours with `BOSUN_START_AT_LOGIN=1`: bosun is running after the login, and
     the morning log has `starting bosun: you logged in during your hours`.
  7. After `bosun stop`, a `reload_changed_jobs` (an update's reload) starts nothing.
- Modify `tests/install_e2e.sh`: the login job, as in Task 4.
- Modify `docs/setup.md` ("The menu bar app": the panel, Stop and Quit, Settings, starting at login, and the
  schedule in the status), `docs/commands.md` (done in Task 6), `docs/watchdog.md` (only where it says how the
  menu's status reads a missing session), `docs/design/menu-bar.md` (where it describes the menu's items, Quit's
  dialog with its "Keep bosun in the menu bar" box, and Open at Login in the menu: a pointer to the control panel
  note), and `docs/design/control-panel.md` (status: built, with the plan linked, plus a "Changes while
  building" section for any ruling).
- Modify CLAUDE.md's "Where things live":
  - `lib/settings.py` and `libexec/bosun-settings`;
  - `lib/login_start.py` and the login job in the launchd list;
  - `Settings.swift` and `SettingsWindow.swift` in the app's list.
- Modify `tests/smoke_weights.txt`: rerun the weights after a whole smoke run (`tools/smoke_shard.py
  --record`).

- [ ] **Step 1: Update the VM suite**, then run `tests/sandbox.sh mac app --from build`. It takes 40-60 min, so
  check at about 2 hours. Every check passes; note the count.

- [ ] **Step 2: Run the whole smoke suite once.** `tests/smoke.sh` takes 15-25 min; check at 50. Then record the
  weights.

- [ ] **Step 3: Update the docs**, then run `uv run pre-commit run --all-files </dev/null`. The strict docs build
  and the link checks pass.

- [ ] **Step 4: Commit** "Control panel: the Mac VM run, docs, and the design note built".

## After the plan

1. Open the pull request: design, plan and code, with the docs listed.
2. Queue it.
3. Tag `app-v0.1.3` and approve the `release` environment.
4. Run `gh workflow run release.yml`.
5. Update the Mac VM from the release, and walk the person through Settings and a restart.
