# Menu bar app, part 1: releases and the CLI side — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything bosun.app will ask of bosun exists in the CLI and works from a terminal: tagged releases and an
update that follows them, `bosun status --json`, `bosun quit` and a Start that does what the morning does, the
console as a launchd job, and `bosun app` to install, update and remove the app — so part 2 only has to build the app.

**Architecture:** The CLI stays the one source of truth; the app (part 2) is a thin client of a versioned JSON
contract. Pure Python modules decide (the status level, the quit plan, release names and notes), and the shell
commands gather facts and act, as the repo's other features do. Releases are git tags made by a workflow.

**Tech Stack:** Bash 3.2, Python 3.13+ (stdlib, mypy strict), launchd, git, GitHub Actions.

**Spec:** `docs/design/menu-bar.md` (approved 2026-11-12).

## Global Constraints

- The app never reads bosun's files: everything it needs is in `bosun status --json`, `bosun update --check --json`,
  `bosun update --dry-run --json`, `bosun quit --plan --json`, `bosun console --url`; each JSON object carries
  `"contract": 1`.
- Release tags: `v<YYYY.MM.DD>`, a second one the same day `v<YYYY.MM.DD>.2` (then `.3`); app release tags
  `app-v<semver>` are part 2's.
- New config keys and defaults (`lib/common.sh` and `examples/generic/bosun.env`): `BOSUN_UPDATE_CHANNEL="releases"`,
  `BOSUN_MENU_APP="0"`, `BOSUN_CONSOLE_ALWAYS="0"`, `BOSUN_APP_RELEASES=""` (empty: the install's origin), and
  `BOSUN_QUIT_WAIT_MINUTES="10"`. `CONFIG_ADDED` entries keep an existing install as it was: `BOSUN_UPDATE_CHANNEL`
  kept `main`, `BOSUN_MENU_APP` kept `0`, `BOSUN_CONSOLE_ALWAYS` kept `0`.
- The publisher's identity is only in `app/publisher.json` (`{"bundle_id": "<the publisher's reverse-DNS id>", "team_id":
  "<the person's Team ID>", "releases": "<owner>/<repo>"}`) — the team id is filled by the person; until then the
  file holds `"team_id": ""` and `bosun app install` refuses with "app/publisher.json has no team_id yet". CLAUDE.md's
  Publishing section gains the exception for this one public value.
- New state files get `STATE_FILES` rows; every launchd job is rendered by `lib/common.sh` from a template and known
  to `reload_changed_jobs`, `job_labels` and uninstall.
- No change in what a `bosun stop`, `bosun start` or `bosun update` does today for an install that keeps the kept
  values (`main`, no app, no always-on console), except where a step says so.
- The repo's limits: mypy strict, ruff, complexipy <= 15, lizard CCN <= 10 and <= 60 lines, shellcheck -S style,
  shfmt, `tools/shell_complexity.py`; libexec scripts replaced atomically; docs in the same pull request; bash 3.2;
  never an install on the dev Mac (VM and Docker sandboxes only).

## Review Focus

1. A Quit while an update holds or runs: refused with what to do ("an update is running; quit when it ends"), never a
   half-stopped bosun. Pinned in Task 6 (`test_quit_refuses_while_an_update_runs`).
2. `bosun update` on the releases channel when the newest release is older than the installed commit (an install that
   followed `main`): says it is ahead and changes nothing, never goes back. Pinned in Task 2
   (`test_an_install_ahead_of_the_newest_release_stays`).
3. A machine that was asleep through the morning time while paused, then woken: the morning job still skips; Start is
   the only way back. Pinned in Task 3 (smoke: morning while quit-paused).
4. The console job's port already taken by a `bosun console` someone runs by hand: the job does not crash-loop; the
   hand-run one is told the job serves the console. Pinned in Task 4.
5. `bosun app install` given a release signed by another team, or not notarized, or a zip with anything but
   `bosun.app` in it: refused, nothing moved into `~/Applications`. Pinned in Task 8.

---

### Task 1: Releases

**Files:**

- Create: `tools/release.py` (pure: the next tag name, the notes), `.github/workflows/release.yml`
- Test: `tests/unit/test_release.py`

**Interfaces:**

- Produces: `next_tag(existing: Iterable[str], today: date) -> str` (`v2026.11.12`, then `v2026.11.12.2`, …; ignores
  non-matching tags); `notes(subjects: Sequence[str]) -> str` (one `- <subject>` line per squash-merge subject, newest
  first, dropping a trailing ` (#NN)` into the line's end as is); CLI `release.py next-tag` (reads `git tag --list
  'v*'`) and `release.py notes <from>..<to>`.
- Produces: `release.yml`: on `schedule` (weekly, Monday 06:00 UTC) and `workflow_dispatch`; it does nothing when
  `main` has no commit since the newest `v*` tag, or when the required checks of `main`'s head are not all green
  (query the commit's check runs with `gh api`); otherwise it creates an annotated tag (message: the notes) and a
  GitHub release with the same notes. Permissions: `contents: write` only; actions pinned by commit.

- [ ] **Step 1: Tests**: `test_first_tag_of_the_day`, `test_second_tag_the_same_day`, `test_other_tags_are_ignored`,
  `test_notes_list_subjects_newest_first`, `test_no_subjects_no_notes`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** `tools/release.py` and the workflow (actionlint and zizmor pass).
- [ ] **Step 4: Run** the tests and `uv run pre-commit run --all-files </dev/null`.
- [ ] **Step 5: Commit** ("Releases: a dated tag with its notes, weekly when main moved and its checks are green").

### Task 2: `bosun update` follows releases, and checks without changing anything

**Files:**

- Modify: `libexec/bosun-update` (which commit it fetches), `lib/update.py` (`--check`, `--skip`), `lib/common.sh`
  and `examples/generic/bosun.env` (`BOSUN_UPDATE_CHANNEL`), `lib/migrations.py` (`CONFIG_ADDED`),
  `lib/uninstall_plan.py` (`update-skip.json`)
- Create: `lib/update_check.py` (pure: the check's answer from the facts)
- Test: `tests/unit/test_update_check.py`, `tests/smoke.sh` (the update section)

**Interfaces:**

- Produces: `newest_release(tags: Iterable[tuple[str, str]]) -> tuple[str, str] | None` (tag, commit) over
  `git ls-remote --tags origin 'v*'` output (peeled `^{}` commits preferred); `check(installed_commit: str,
  installed_version: str | None, newest: tuple[str, str] | None, skipped: str | None, ahead: bool) -> dict` →
  `{"contract": 1, "channel", "installed": {"version", "commit"}, "latest": {"version", "commit", "notes"} | null,
  "available": bool, "skipped": bool, "ahead": bool}`.
- Produces: `bosun update --check [--json]`: no fetch of a branch, no version folder: `git ls-remote` for the newest
  release; its notes from the tag's message (`git fetch --no-tags origin tag <tag>` into the versions repo's objects,
  then `git tag -l --format='%(contents)' <tag>`); `installed.version` is the release tag pointing at the installed
  commit, if any. On the `main` channel `latest` is the branch head and `notes` the subjects since the installed
  commit. Exit 0 always (problems in the JSON as `"error"`).
- Produces: `bosun update --skip <version>` writes `$BOSUN_STATE_DIR/update-skip.json` `{"version", "at"}`; `--check`
  reports `skipped: true` while `latest.version` equals it.
- Changes: on the `releases` channel the wrapper fetches and installs the newest release's commit instead of the branch
  head; when the installed commit descends from it (`git merge-base --is-ancestor`), it says "bosun is ahead of the
  newest release (<tag>)" and changes nothing.

- [ ] **Step 1: Tests**: `test_newest_release_by_date_then_number`, `test_peeled_commits_win`,
  `test_available_when_newer`, `test_not_available_when_installed`, `test_skipped_version_is_reported`,
  `test_an_install_ahead_of_the_newest_release_stays`, `test_main_channel_lists_subjects_since_installed`; smoke: in the
  update section's local origin, a tag `v2026.01.01` behind `main` → `--check --json` names it; `--skip` then
  `--check` says skipped; `BOSUN_UPDATE_CHANNEL=main` keeps today's behaviour.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement**; replace `libexec/bosun-update` atomically.
- [ ] **Step 4: Run** the unit tests and the smoke suite.
- [ ] **Step 5: Commit** ("Update: it follows releases, and checks for one without changing anything").

### Task 3: Stopped until started

**Files:**

- Create: `lib/quit_state.py` (the file, read and written atomically)
- Modify: `libexec/bosun-start` (clears it), `libexec/bosun-morning` (skips while paused), `lib/watchdog.py` and
  `libexec/bosun-watchdog` (skip the run while stopped), `libexec/bosun-learn` (skips), `lib/uninstall_plan.py`
- Test: `tests/unit/test_quit_state.py`, `tests/unit/test_watchdog.py`, `tests/smoke.sh`

**Interfaces:**

- Produces: `$BOSUN_STATE_DIR/quit.json` `{"since": ISO, "morning_paused": bool}`; `quit_state.read(state_dir) ->
  QuitState | None`, `write(state_dir, morning_paused)`, `clear(state_dir)`; CLI `quit_state.py stopped|paused
  --state-dir D` (exit 0 when so).
- Changes: the watchdog run exits 0 with "bosun is stopped (bosun quit at HH:MM): skipping this run" while stopped;
  learning skips its scheduled run; the morning run logs "the morning start is paused (bosun quit at …); start bosun to
  resume" and exits 0 while paused; `bosun start` (and the morning when not paused) clears the file.

- [ ] **Step 1: Tests**: the file's round trip; the watchdog skipping; smoke: with `quit.json` paused, `bosun morning`
  skips and starts nothing; with it not paused, the morning starts as usual and clears it; `bosun start` clears it.
- [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement** (libexec scripts atomically).
- [ ] **Step 4: Run** the unit tests and the smoke suite.  - [ ] **Step 5: Commit** ("Stopped until started: what a
  quit leaves, which the watchdog, learning and the morning respect").

### Task 4: The console as a job

**Files:**

- Create: `templates/launchd-console.plist`
- Modify: `lib/common.sh` (render, `reload_changed_jobs`, `job_labels`; `BOSUN_CONSOLE_ALWAYS`), `libexec/bosun-console`
  (`--url`; a hand run while the job serves), `libexec/bosun-start` (the wanted marker), `lib/uninstall_plan.py`,
  `examples/generic/bosun.env`, `lib/migrations.py`
- Test: `tests/smoke.sh` (console and install sections)

**Interfaces:**

- Produces: label `<BOSUN_LAUNCHD_LABEL>.console`; the plist runs `bosun console` with `RunAtLoad` true and `KeepAlive`
  `{PathState: {"$BOSUN_STATE_DIR/console/wanted": true}}`, rendered and loaded only when `BOSUN_CONSOLE_ALWAYS=1`
  (otherwise `reload_changed_jobs` boots it out and removes it); `bosun start` creates `console/wanted` when the key
  is 1; Task 6's quit removes it and stops the process (`launchctl kill TERM gui/<uid>/<label>`).
- Produces: `bosun console --url` prints `http://127.0.0.1:<port>/?t=<token>` from the stored token and the port, and
  exits 1 with "the console is not running" when `console_token.running` says so; a hand-run `bosun console` while the
  job serves prints the URL (and opens it with `--open`) instead of failing on the port.

- [ ] **Step 1: Smoke checks** (with the fake launchctl): the console plist rendered and loaded with the key on, not
  with it off, and removed when it is turned off; its `KeepAlive` names the marker; `--url` with a fake running console
  prints the address; a hand run while "running" prints the URL.
- [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run** the smoke suite and
  the console unit tests.  - [ ] **Step 5: Commit** ("Console: a launchd job while bosun runs, and its address on ask").

### Task 5: `bosun status --json`

**Files:**

- Create: `lib/menu_status.py` (pure: the level, the sentence, the detail lines)
- Modify: `libexec/bosun-status` (`--json`), `libexec/bosun-morning` (records the keep-awake's end),
  `lib/uninstall_plan.py` (`keep-awake-*.json`)
- Test: `tests/unit/test_menu_status.py`, `tests/smoke.sh`

**Interfaces:**

- Consumes: `watchdog.status_data` (`lib/watchdog.py:1789`), `console_sessions.waiting_windows`
  (`lib/console_sessions.py:178`), the facts `console_live.Collector` gathers (`bosun sessions --json`, `bosun
  watchdog --facts`), `update_state.running`/`holding`, the quit state (Task 3), the console job (Task 4).
- Produces: `snapshot(facts: StatusFacts, now: datetime) -> dict`: `{"contract": 1, "level": "ok"|"warning"|"error"|
  "stopped", "sentence": str, "detail": [str], "windows": int, "waiting": [str], "keep_awake_until": "HH:MM"|null,
  "stopped": {"since", "morning_paused"}|null, "update": {"state": "none"|"running", "waiting_for": [str]},
  "console": {"running": bool, "url": null}, "problems": [str], "logs": "<BOSUN_LOG_DIR>"}` (the URL is never in it: it carries the token).
  Level: `stopped` when quit (Task 3); `error` when the session is missing inside the awake window and not stopped, or
  any watchdog check of severity error has a finding; `warning` when any warn check has a finding, or an error line in
  the logs within the last hour; else `ok`. `problems` are the findings' messages, which "Show What's Wrong…" lists.
- Produces: `bosun status --json` printing that object; `bosun status` (text) unchanged.
- Produces: the morning writes `$BOSUN_STATE_DIR/keep-awake-<session>.json` `{"until": "<ISO time>", "pid"}` next to
  the pidfile, and `bosun stop` removes both.

- [ ] **Step 1: Tests**, one per level from recorded facts (all well; a warn finding; an error finding; session missing
  in the window; session missing outside it is `ok` with "bosun is resting until 08:00"; quit → `stopped`; an update
  running → `ok` with `update.state = running` and its windows), plus the keep-awake time and `waiting`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement**; the log-error test reuses `watchdog.scanned_logs` and `ERROR_LINE` over the last hour (read
  only, never the watchdog's marks).
- [ ] **Step 4: Run** the tests and the smoke suite (`bosun status --json` on the smoke session parses and has
  `contract: 1`).
- [ ] **Step 5: Commit** ("Status: one JSON object says how bosun is, for the menu bar app and anyone").

### Task 6: `bosun quit`

**Files:**

- Create: `libexec/bosun-quit`, `lib/quit.py` (the plan: pure over the facts; the run)
- Modify: `libexec/bosun-stop` (`--keep-awake`), `bin/bosun` (usage), `docs/commands.md`
- Test: `tests/unit/test_quit.py`, `tests/smoke.sh`

**Interfaces:**

- Consumes: `bosun sessions --json` (each window's agent status), `update_state.running`, `quit_state.write`, Task 4's
  marker and label, the keep-awake files (Task 5).
- Produces: `quit_plan(windows: Sequence[Window], keep_awake_until: str | None, updating: bool) -> dict` →
  `{"contract": 1, "windows": [names], "busy": [names whose status is busy], "waiting": [names whose status is
  waiting], "keep_awake_until", "refused": str | null}` (`refused` while an update runs).
- Produces: `bosun quit --plan --json`; `bosun quit (--let-finish[=<minutes>] | --stop-now) [--pause-morning]
  [--sleep] [--json]`: refuses while an update runs (exit 1, `refused`); with `--let-finish` waits (polling every 5 s,
  up to `BOSUN_QUIT_WAIT_MINUTES` or the given minutes) until no window is `busy` (`waiting` windows do not hold it;
  `shell` counts as done), then — or at once with `--stop-now` — writes `quit.json`, removes `console/wanted` and stops
  the console job, runs `bosun stop --keep-awake` (or `bosun stop` with `--sleep`), and prints what it stopped. When
  the wait runs out it exits 3 and prints the windows still busy, changing nothing, so a caller can ask again.
- Produces: `bosun stop --keep-awake` leaves the caffeinate and its files alone; plain `bosun stop` unchanged.

- [ ] **Step 1: Tests**: the plan for idle, busy, waiting and shell windows; refused while updating;
  `test_quit_refuses_while_an_update_runs`; smoke: a stand-in agent kept busy → `--let-finish=0` exits 3 naming it,
  nothing stopped; `--stop-now --pause-morning --sleep` → no session, `quit.json` paused, no caffeinate, console job
  told to stop; without `--sleep` the caffeinate (the sandbox stand-in) still runs.
- [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement** (libexec scripts atomically).
- [ ] **Step 4: Run** the unit tests and the smoke suite.  - [ ] **Step 5: Commit** ("Quit: bosun stops cleanly, with
  your say over busy agents, the morning start and the keep-awake").

### Task 7: Start like the morning

**Files:**

- Modify: `libexec/bosun-morning` (`--now`), `lib/common.sh` (if a helper moves)
- Test: `tests/smoke.sh`

**Interfaces:**

- Produces: `bosun morning --now`: the morning run at any hour or day — clears `quit.json`, refreshes credentials
  (`bosun setup`), updates agents that update themselves, holds a keep-awake until `BOSUN_AWAKE_UNTIL` only when now is
  inside today's awake window (none outside it), then starts or refreshes the session; its log header says
  `trigger=now`. This is what the app's "Start bosun" runs.

- [ ] **Step 1: Smoke checks**: `--now` outside the awake window starts the session and holds no keep-awake; inside it
  holds one until the end time (the keep-awake JSON says so); it clears a paused `quit.json`.
- [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement** (atomically).  - [ ] **Step 4: Run** the
  smoke suite.  - [ ] **Step 5: Commit** ("Morning: --now does the morning run at any hour, which the app's Start uses").

### Task 8: `bosun app`

**Files:**

- Create: `libexec/bosun-app`, `lib/app_install.py` (verify and place; the release lookup), `app/publisher.json`,
  `app/VERSION` (`0.1.0`), `templates/launchd-app.plist`
- Modify: `lib/common.sh` (render the app's login job), `lib/uninstall_plan.py` and `lib/uninstall_detect.py` (the app
  and its job), `bin/bosun`, `docs/commands.md`, `CLAUDE.md` (Publishing: the team id exception)
- Test: `tests/unit/test_app_install.py`, `tests/smoke.sh`

**Interfaces:**

- Produces: `bosun app install [--build]`, `bosun app update`, `bosun app remove`, `bosun app login on|off`, `bosun app
  status --json` (`{"contract": 1, "installed": version|null, "wanted": app/VERSION, "path", "login": bool}`).
- Produces: `release_asset_url(releases: str, version: str) -> str`
  (`https://github.com/<owner>/<repo>/releases/download/app-v<version>/bosun-app-<version>.zip`); `releases` is
  `BOSUN_APP_RELEASES` or `app/publisher.json`'s, or the install's origin when that is a GitHub URL.
- Produces: `verify(app: Path, team_id: str) -> str | None` (a reason it is refused, or None): `codesign --verify
  --deep --strict` passes, `codesign -dv` names `TeamIdentifier=<team_id>`, `spctl --assess --type execute` accepts it,
  `Info.plist`'s `CFBundleIdentifier` is the publisher's; `install` downloads with `curl -fsSL` into a temp folder,
  unzips with `ditto -x -k`, requires exactly one `bosun.app`, verifies, then renames it into `~/Applications/bosun.app`
  (the old one renamed aside first and removed after); it relaunches the app when it was running.
- Produces: `--build` (developers only): `app/build.sh` from part 2, ad-hoc signed, installed without the team check
  (part 2 added `app/build.sh`, which `--build` now runs).
- Produces: the login job `<label>.app`: `ProgramArguments` `["$HOME/Applications/bosun.app/Contents/MacOS/bosun",
  "--at-login"]`, `RunAtLoad` true, no `KeepAlive`; `login on|off` renders and loads, or boots out and removes it.
- Produces: uninstall removes `~/Applications/bosun.app` and the login job (a step each, with by-hand text).

- [ ] **Step 1: Tests**: `verify` against faked `codesign`/`spctl` output for each refusal (another team, not
  notarized, wrong bundle id, unsigned); a zip with two apps refused; the asset URL; `app status`; smoke: `install` with
  fake `curl`/`ditto`/`codesign`/`spctl` places the app and keeps the old one aside until done; a refused one leaves
  `~/Applications` unchanged; `login on|off` with the fake launchctl; uninstall's plan lists the app and the job.
- [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement** (new macOS tools used — `codesign`, `spctl`,
  `ditto` — get stand-ins in `tests/sandbox/macos/` and a check in `tests/install_e2e.sh`, per CLAUDE.md).
- [ ] **Step 4: Run** the unit tests, the smoke suite and `tests/sandbox.sh init`.
- [ ] **Step 5: Commit** ("App: bosun installs, updates and removes bosun.app, accepting only the publisher's
  notarized build").

### Task 9: Init, update and the watchdog bring the app along

**Files:**

- Modify: `lib/init_plan.py` (`Answers.menu_app`, `env_groups`), `lib/init_wizard.py` (the question), `lib/init.py`
  (`bosun app install` and `login on` after install.sh), `lib/update.py` (after the switch: `bosun app update` when
  `BOSUN_MENU_APP=1`), `lib/watchdog.py` (`CHECKS` gains `app-missing`), `libexec/bosun-watchdog` (usage line),
  `docs/watchdog.md`
- Test: `tests/unit/test_init_plan.py`, `tests/unit/test_init_wizard.py`, `tests/unit/test_update.py`,
  `tests/unit/test_watchdog.py`, `tests/sandbox/fresh_user.sh`

**Interfaces:**

- Produces: `Answers.menu_app: bool` (recommended True on a first run; skipped when unattended); `env_groups` writes
  `BOSUN_MENU_APP` and `BOSUN_CONSOLE_ALWAYS` (both `1` with the app); the wizard asks "Add bosun to the menu bar and
  Applications?" (yes/no, after the wake question); `ALWAYS_ASKED` grows by one.
- Produces: the update, after it switches and before it releases the hold, runs `bosun app update` when
  `BOSUN_MENU_APP=1`; a failure is a line in its summary and stops nothing; the summary of an install without the app
  says once how to add it (`bosun app install`).
- Produces: after the switch the update restarts the console job (`launchctl kickstart -k gui/<uid>/<label>.console`)
  when it is loaded and `console/wanted` exists, so the console serves the new version; `lib/update_plan.py`'s
  console line says so instead of "restart it after the update" (ruling 14).
- Produces: `app-missing` (warn): `BOSUN_MENU_APP=1` and `~/Applications/bosun.app` is gone: "bosun.app is not in
  ~/Applications (moved to the Trash?); bosun still runs. `bosun uninstall` removes bosun; `bosun app install` brings
  the app back."

- [ ] **Step 1: Tests** for each.  - [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the unit suite, the smoke suite and `tests/sandbox.sh init`.  - [ ] **Step 5: Commit** ("Init and
  update bring bosun.app along; the watchdog notices it gone").

### Task 10: The docs, and the Mac VM

**Files:**

- Modify: `docs/commands.md`, `docs/setup.md` (releases, the app, quitting), `docs/watchdog.md`, `docs/design/update.md`
  (channels), `docs/design/console.md` (the job), `docs/design/menu-bar.md` (status), `README.md`, `ROADMAP.md`,
  `CLAUDE.md`
- Modify: `tests/sandbox/mac.sh` and `tests/install_e2e.sh` (or a new `tests/quit_e2e.sh` copied by `copy_checkout`),
  `tests/sandbox/e2e_lib.sh` (the fresh install runs on the default channel)

- [ ] **Step 1: Docs** as listed (grep for `bosun update`, `bosun stop`, `bosun console`, "follows main").
- [ ] **Step 2: A VM run** of the CLI side on a real Mac: `tests/sandbox.sh mac run` extended so the install also
  turns on `BOSUN_CONSOLE_ALWAYS=1`: the console job really runs and serves (from a state folder with no `console/`
  yet: the job loaded, then `bosun start`, and the console answers within seconds; with the marker removed and the
  job loaded again, no console process — ruling 11); `bosun quit --stop-now --pause-morning
  --sleep` leaves no tmux server, no caffeinate, no console process, and the watchdog's next kickstarted run logs that
  it skipped; `launchctl kickstart -k` of the morning job then starts nothing; `bosun morning --now` brings everything
  back; `bosun update --check --json` against the bundle remote with a tag. The fresh install runs on the default
  `releases` channel (no `BOSUN_UPDATE_CHANNEL=main` export for it; the work run keeps `main`, which its conversion
  writes anyway): the bundle remote carries a release tag, `get.sh` installs that release, the patch commit is tagged
  as the next release, `bosun update --yes` moves to it, and the uninstall after it removes `bosun-versions` (nothing
  of yours kept there).
- [ ] **Step 3: Run** `uv run pre-commit run --all-files`, the smoke suite, `tests/sandbox.sh init` and `tests/sandbox.sh
  mac run`. Expected: all pass.
- [ ] **Step 4: Commit** and open the pull request (the CLI side of bosun.app; behaviour for existing installs
  unchanged by the kept values; the VM run's results).

### Task 11: A first install takes the newest release

Runs right after Task 2 (ledger ruling 5); numbered last so the others keep their numbers.

**Files:**

- Modify: `get.sh`, `lib/init_plan.py` and `lib/init.py` (the channel a branch install follows), `docs/setup.md`,
  `docs/design/init.md`, `docs/design/menu-bar.md` (Releases)
- Test: a unit test for `get.sh`'s release pick (with `tests/unit/test_update_check.py`'s tag lists),
  `tests/unit/test_init_plan.py`, `tests/smoke.sh` or `tests/sandbox.sh init` for the hand-over

**Interfaces:**

- Consumes: `lib/update_check.py`'s tag rule (`RELEASE_TAG`, `release_key`, `newest_release` over `git ls-remote --tags`
  output, peeled `^{}` commits preferred).
- Changes:
  - A new install (no `BOSUN_BRANCH`): `get.sh` installs the newest release, found with `git ls-remote --tags <repo>
    'v*'` by a POSIX sh function that follows the same rule. `get.sh` cannot import Python (it runs before preflight,
    and a new Mac's `python3` may be older than bosun needs), so a unit test runs the sh function and
    `update_check.newest_release` on the same ls-remote outputs (including `.0`/`.1` refused, `.10` above `.9`, peeled
    commits, no release) and requires the same answer. A repo with no release yet: the head of `main`, and one line
    saying so.
  - `BOSUN_BRANCH` given on a new install: that branch's head as today, and the install follows that branch: `get.sh`
    hands `BOSUN_UPDATE_CHANNEL=main` to `bosun init` (as it hands `BOSUN_USER_PATH`), which writes it to `bosun.env`
    (an answer of `init`, per CLAUDE.md "An answer bosun init offers").
  - A re-run on a link install hands over to the install's own `bosun update` (`--yes` when unattended), which moves
    to what its channel says; `get.sh` no longer fetches a version for it, so it never moves an install off its
    channel (nor a `main` install back to an older release). The dry run says "Then it would run: <link>/bin/bosun
    update". A checkout from before versions converts as today (main's head; the conversion keeps `main`).

- [ ] **Step 1: Tests**: the twin-rule test; `init`'s plan writes `BOSUN_UPDATE_CHANNEL="main"` when handed it; the
  link re-run's dry run names `bosun update`; a new install's dry run names the release it would install.
- [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement** (`get.sh` stays plain POSIX sh, functions
  with `main` last).
- [ ] **Step 4: Run** the unit tests, the smoke suite and `tests/sandbox.sh init`.  - [ ] **Step 5: Commit** ("A first
  install takes the newest release, and a re-run hands over to bosun update").

### Task 12: Start through bosun's own launchd job

Runs after Task 9 and before Task 10, so the VM run covers it (ledger ruling 35).

**Files:**

- Create: `templates/launchd-now.plist`
- Modify: `lib/common.sh` (render it, `job_labels`, `reload_changed_jobs`), `libexec/bosun-morning` (`--background`),
  `lib/uninstall_plan.py` and `lib/uninstall_detect.py` (the job), `install.sh` if it lists the jobs it loads
- Docs and comments, in the same commit: every place that lists bosun's launchd jobs or says what the app's Start runs
  — `docs/commands.md` (the morning row), `docs/design/menu-bar.md` (the "Start bosun" bullet: it runs `bosun morning
  --now --background`, and why — macOS's responsible process for privacy prompts; the update stays detached, and
  why), `docs/setup.md`, `docs/day.md`, `docs/design/uninstall.md`, `docs/design/update.md`, `docs/design/init.md`,
  `docs/development.md` and `CLAUDE.md` wherever they list the jobs or their templates, `tests/install_e2e.sh`'s
  header, and the comments that call `--now` "the app's Start" (`tests/smoke.sh`); grep `launchd job`,
  `console_label`, `launchd-console.plist`, `morning --now` and `the app's Start` to find them
- Not redundant: `--background` only kickstarts the job; the morning run's logic (the quit record, the keep-awake,
  the update hold) stays in the one `--now` path the job runs; the plist goes through the same rendering as the other
  jobs, its label from one helper beside `console_label`, listed once in `job_labels`
- Test: `tests/smoke.sh` (with the fake `launchctl`), `tests/unit/test_uninstall_plan.py`, `tests/install_e2e.sh` (the
  real job: `bosun morning --now --background` brings the session up, and its tmux server's responsible process is not
  the caller — `launchctl procinfo`)

**Interfaces:**

- Produces: the job `<label>.now`: `ProgramArguments` `["<install link>/bin/bosun", "morning", "--now"]`,
  `RunAtLoad` false, no `KeepAlive`, `AbandonProcessGroup` true (the tmux server it starts outlives it); rendered,
  loaded and listed like the morning job, and removed by uninstall.
- Produces: `bosun morning --now --background`: `launchctl kickstart gui/<uid>/<label>.now` and return at once (exit
  0 when launchd took it; 1 with one line when the job is not loaded, e.g. "run install.sh again"; a run already going
  is not started twice). This is what the app's Start runs, so nothing bosun starts is the app's child (whose
  processes would name bosun.app in macOS's privacy prompts). `bosun update --yes` stays a child the app starts
  detached: an update run as a launchd job could boot itself out when it reloads changed plists.

- [ ] **Step 1: Tests.**  - [ ] **Step 2: Run them to see them fail.**  - [ ] **Step 3: Implement** (atomically).
- [ ] **Step 4: Run** the smoke suite once.  - [ ] **Step 5: Commit** ("Morning: --now --background starts the morning
  run as bosun's own launchd job, which the app's Start uses").
