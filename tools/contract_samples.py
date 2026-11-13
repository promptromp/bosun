"""Writes the JSON the menu bar app's Swift tests decode (app/Tests/BosunMenuCoreTests/Fixtures/*.json).

Each fixture is made by part 1's own code, never typed by hand: the status objects by
`menu_status.snapshot` over recorded facts, the update check by `update_check.check`, the update's plan
by `update_plan.make_plan` inside `update.json_object` (the object `bosun update --dry-run --json` prints),
the quit plans and each quit result by `quit.run` over fake ports (the world it would touch replaced, as
tests/unit/test_quit.py does), with the exit status each run returned in `exits.json`, the app's state by
`app_release.status`. A change on the Python side that alters what the app reads fails
tests/unit/test_contract_samples.py until the fixtures are written again:

    uv run python tools/contract_samples.py

Fictional data throughout (acme's invoice exporter). The times are fixed and read in UTC, so the files are
the same wherever they are written.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime, time as clock_time
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import app_release
import awake
import menu_status
import quit as quit_cmd
import quit_state
import update
import update_check
import update_plan
from menu_status import SessionGate, StatusFacts


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "app" / "Tests" / "BosunMenuCoreTests" / "Fixtures"
NOW = datetime(2026, 11, 11, 10, 0, 0)  # a Wednesday, local time, inside the awake window
WORKDAYS = awake.AwakeWindow("MTWRF", clock_time(8, 0), clock_time(20, 0))
LOGS = "/Users/me/.local/state/bosun/logs"
APP_PATH = "/Users/me/Applications/bosun.app"
Sample = dict[str, Any]


@contextlib.contextmanager
def utc() -> Iterator[None]:
    """Local time as UTC while the samples are made: the status's lines carry a clock time."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


# ---- bosun status --json ---------------------------------------------------------------------------------


def window(name: str, status: str | None = "idle", exists: bool = True) -> dict[str, Any]:
    """A window as `bosun sessions --json` lists it."""
    agent = {"pid": 4101, "status": status, "statusUpdatedAt": 1700000000000} if status else None
    return {"name": name, "kind": "session", "window": exists, "agent": agent}


def check(name: str, severity: str, *messages: str) -> dict[str, Any]:
    findings = [{"message": m, "decision": "alerts now"} for m in messages]
    return {"name": name, "severity": severity, "summary": "", "note": "", "findings": findings}


def watchdog_checks(*checks: dict[str, Any]) -> dict[str, Any]:
    """The watchdog's status data: the all-well checks that <checks> do not replace."""
    named = {c["name"] for c in checks}
    quiet = [
        check(name, severity)
        for name, severity in (("session-missing", "error"), ("log-errors", "error"), ("waiting", "warn"))
        if name not in named
    ]
    return {"in_window": True, "checks": [*quiet, *checks]}


OPEN = [window("acme-orchestrator"), window("acme-exporter"), window("acme-web", "waiting")]
GONE = [window(w["name"], exists=False) for w in OPEN]


def snapshot(now: datetime = NOW, **changes: Any) -> Sample:
    base: dict[str, Any] = {
        "windows": OPEN,
        "checks": watchdog_checks(),
        "awake": WORKDAYS,
        "quit": None,
        "keep_awake_until": NOW.replace(hour=20),
        "log_dir": LOGS,
    }
    return menu_status.snapshot(StatusFacts(**{**base, **changes}), now)


def status_samples() -> dict[str, Sample]:
    quit_at = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)
    warning = "The acme-web window has waited at a prompt for 45 min."
    logged = (
        "1 new ERROR line(s) in the bosun logs: check-in.log: [checkin] ERROR: the exporter's tests failed"
    )
    failed = "Today's morning run reported problems: setup.sh exited 1"
    return {
        "status-ok": snapshot(),
        "status-warning": snapshot(checks=watchdog_checks(check("waiting", "warn", warning))),
        "status-error": snapshot(windows=GONE, gate=SessionGate(menu_status.EXPECTED), keep_awake_until=None),
        # bosun runs, its windows open, and an error-severity finding: the app must not start it again
        "status-running-error": snapshot(checks=watchdog_checks(check("log-errors", "error", logged))),
        # the windows could not be read (running is null) and a finding is an error: nor this
        "status-windows-unreadable": snapshot(
            windows=None,
            checks=watchdog_checks(check("morning-failed", "error", failed)),
            errors={"sessions": "sessions --json did not answer: tmux did not answer"},
        ),
        "status-stopped": snapshot(
            windows=GONE, quit=quit_state.QuitState(quit_at, True), keep_awake_until=None
        ),
        "status-quit-unfinished": snapshot(quit=quit_state.QuitState(quit_at, False)),
        "status-resting": snapshot(
            NOW.replace(hour=6), windows=GONE, gate=SessionGate(menu_status.RESTING), keep_awake_until=None
        ),
        "status-update-running": snapshot(
            update_running=True, update_waiting_for=("acme-exporter", "acme-web")
        ),
    }


# ---- bosun update --check --json, --dry-run --json ---------------------------------------------------------

INSTALLED = "1" * 40
NEWEST = "2" * 40
NOTES = "- Hardening: the adversarial cases an inventory found\n- Repo windows on a second agent"


def update_check_samples() -> dict[str, Sample]:
    latest = ("v2026.11.11", NEWEST)
    return {
        "update-check-available": update_check.check(
            INSTALLED, "v2026.11.04", latest, None, False, notes=NOTES
        ),
        "update-check-current": update_check.check(NEWEST, "v2026.11.11", latest, None, False),
        "update-check-skipped": update_check.check(
            INSTALLED, "v2026.11.04", latest, "v2026.11.11", False, notes=NOTES
        ),
        "update-check-failed": update_check.failed(
            "releases", INSTALLED, "could not reach the origin: Could not resolve host"
        ),
        # the install's own commit could not be read: `installed.commit` is null
        "update-check-failed-no-commit": update_check.failed(
            "releases", None, "cannot read the commit of /Users/me/.local/share/bosun/current"
        ),
    }


def version_facts(commit: str, ledger: int, policy: str) -> update_plan.VersionFacts:
    return update_plan.VersionFacts(
        commit,
        {"ledger": ledger},
        {"ledger": 1},
        frozenset({"BOSUN_MENU_APP"}) if ledger > 1 else frozenset(),
        {"orchestrator/policy.md": policy, "orchestrator/tasks/review.md": policy},
    )


def windows_of_every_treatment() -> list[update_plan.WindowFacts]:
    """One window for each way an update treats one (update_plan.Treatment)."""
    facts = update_plan.WindowFacts
    return [
        facts("acme-orchestrator", "orchestrator", "idle", False),  # re-read
        facts("acme-api", "repo", "idle", True),  # restart
        facts("acme-exporter", "repo", "shell", True),  # take stock, then restart
        facts("acme-worker", "repo", "busy", True),  # wait until idle
        facts("acme-web", "repo", "waiting", True),  # ask first
        facts("acme-docs", "repo", "exited", True),  # relaunch
        facts("checkin", "checkin", "idle", False),  # restart the loop
        facts("acme-notes", "repo", "idle", False),  # leave
        facts("scratch", "repo", "idle", False, started_by_bosun=False),  # not started by bosun
    ]


def make_plan(missing: Sequence[str] = ()) -> update_plan.Plan:
    return update_plan.make_plan(
        version_facts(INSTALLED, 1, "a"),
        version_facts(NEWEST, 2, "b"),
        commits=["Hardening: the adversarial cases an inventory found", "Repo windows on a second agent"],
        windows=windows_of_every_treatment(),
        jobs=["watchdog", "console"],
        renames=[],
        user_changes=[("sessions.yml", "add connectors: to the exporter entry")],
        in_flight=[("task:acme/exporter#7", ["review"])],
        missing=list(missing),
        deletion_cut_short=False,
        console=True,
        steps=lambda _store, found, target: [(found, target)],
    )


def dry_run(plan: update_plan.Plan | None) -> Sample:
    """What `bosun update --dry-run --json` prints, made by the function that prints it: `contract`, the plan
    (null when none was made), the update's record and the summary, which a dry run has none of."""
    printed: Sample = json.loads(json.dumps(update.json_object(plan, None, None)))  # as it is on stdout
    return printed


def update_plan_samples() -> dict[str, Any]:
    return {
        "update-plan": dry_run(make_plan()),
        "update-plan-blocked": dry_run(make_plan(["a migration of the knowledge store is missing"])),
        "update-plan-none": dry_run(None),
        # Every treatment bosun has: the app places each one in the dialog, and fails on one it does not know
        "update-treatments": [t.value for t in update_plan.Treatment],
    }


# ---- bosun quit --plan --json, bosun quit ... --json -------------------------------------------------------


class World:
    """The ports of one quit, the world it touches replaced: the windows (the last reading stays), the
    console's answer, a bosun stop that fails, a lock that stays taken."""

    def __init__(self, windows: list[quit_cmd.Window], **kw: Any) -> None:
        self.windows = windows
        self.updating: bool = kw.get("updating", False)
        self.stop_fails: str | None = kw.get("stop_fails")
        self.console: tuple[str, int | None] = kw.get("console", ("stopped", None))
        self.lock_busy: bool = kw.get("lock_busy", False)
        self.unreadable: str | None = kw.get("unreadable")  # why the windows cannot be read
        self.now = 1000.0

    def read_windows(self) -> list[quit_cmd.Window]:
        if self.unreadable:
            raise quit_cmd.QuitError(self.unreadable)
        return self.windows

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        if self.lock_busy:
            raise quit_cmd.QuitError(quit_cmd.LOCK_BUSY)
        yield

    def stop_bosun(self, _keep_awake: bool) -> None:
        if self.stop_fails:
            raise quit_cmd.QuitError(self.stop_fails)

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def ports(self) -> quit_cmd.Ports:
        lock: Callable[[], AbstractContextManager[None]] = self.lock
        return quit_cmd.Ports(
            read_windows=self.read_windows,
            updating=lambda: self.updating,
            keep_awake_until=lambda: "20:00",
            lock=lock,
            write_quit=lambda _paused: None,
            stop_console=lambda: self.console,
            stop_bosun=self.stop_bosun,
            sleep=self.sleep,
            clock=lambda: self.now,
        )


def quit_window(name: str, status: str = "idle") -> quit_cmd.Window:
    return quit_cmd.Window(name, status)


LOCK_WAIT_SECONDS = 180  # BOSUN_WINDOWS_LOCK_WAIT_SECONDS' default
STEPS_SECONDS = 225  # lib/quit_live.py's STEPS_SECONDS (a test holds the two together)
QUIT_WAIT_MINUTES = 10  # BOSUN_QUIT_WAIT_MINUTES' default
QUIT_WINDOWS = [
    quit_window("acme-orchestrator"),
    quit_window("acme-exporter", "busy"),
    quit_window("acme-web", "waiting"),
    quit_window("acme-api"),
]
QUIET_WINDOWS = [quit_window("acme-orchestrator"), quit_window("acme-web", "waiting")]


def quit_run(
    world: World, mode: str, *, minutes: int = QUIT_WAIT_MINUTES, pause: bool = True, sleep: bool = True
) -> tuple[int, Sample]:
    """The exit status of `bosun quit ... --json` for a run over <world>, and the one object it prints."""
    out, err = io.StringIO(), io.StringIO()
    options = quit_cmd.Options(mode, minutes, pause, sleep, True, LOCK_WAIT_SECONDS, STEPS_SECONDS)
    status = quit_cmd.run(options, world.ports(), out, err)
    result: Sample = json.loads(out.getvalue())
    return status, result


def quit_runs() -> dict[str, tuple[int, Sample]]:
    return {
        "quit-plan": quit_run(World(QUIT_WINDOWS), "plan"),
        "quit-plan-refused": quit_run(World(QUIT_WINDOWS, updating=True), "plan"),
        # `quit --plan --json` when the windows cannot be read: exit 1, this on stdout, nothing on stderr
        "quit-plan-failed": quit_run(
            World(QUIET_WINDOWS, unreadable="the windows could not be listed: tmux did not answer"), "plan"
        ),
        "quit-result-stopped": quit_run(World(QUIET_WINDOWS), "finish"),
        "quit-result-stopped-console-running": quit_run(
            World(QUIET_WINDOWS, console=("still running", 8765)), "now", pause=False, sleep=False
        ),
        "quit-result-busy": quit_run(World(QUIT_WINDOWS), "finish", minutes=0),
        "quit-result-refused": quit_run(World(QUIT_WINDOWS, updating=True), "now"),
        "quit-result-failed": quit_run(
            World(QUIET_WINDOWS, stop_fails="bosun stop failed: the tmux server did not answer"), "now"
        ),
        "quit-result-failed-early": quit_run(World(QUIET_WINDOWS, lock_busy=True), "now"),
    }


def quit_samples() -> dict[str, Any]:
    """Each quit's object, and `exits`: the exit status each run returned, by fixture name (the app's tests
    pair a reply with its own status, never one typed beside it)."""
    runs = quit_runs()
    return {
        **{name: sample for name, (_status, sample) in runs.items()},
        "exits": {n: s for n, (s, _) in runs.items()},
    }


# ---- bosun app status --json -------------------------------------------------------------------------------


def app_status(installed: str | None) -> Sample:
    """`app_release.status` over a folder made to look the way <installed> says; its path is the fixed one."""
    with tempfile.TemporaryDirectory() as tmp:
        root, app = Path(tmp) / "bosun", Path(tmp) / "bosun.app"
        (root / "app").mkdir(parents=True)
        (root / "app" / "VERSION").write_text("0.1.0\n")
        login = Path(tmp) / "app-login.plist"
        if installed:
            (app / "Contents").mkdir(parents=True)
            info = {"CFBundleIdentifier": "ai.example.bosun", "CFBundleShortVersionString": installed}
            (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
            login.write_text("")
        state: Sample = {**app_release.status(root, app, login), "path": APP_PATH}
        return state


def app_samples() -> dict[str, Sample]:
    return {"app-status": app_status("0.1.0"), "app-status-not-installed": app_status(None)}


# ---- the files ---------------------------------------------------------------------------------------------


def samples() -> dict[str, Any]:
    """Every fixture, by file name (without .json)."""
    with utc():
        return {
            **status_samples(),
            **update_check_samples(),
            **update_plan_samples(),
            **quit_samples(),
            **app_samples(),
        }


def render(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write(folder: Path) -> None:
    """The fixtures in <folder>, and none that is no longer made."""
    made = samples()
    folder.mkdir(parents=True, exist_ok=True)
    for path in folder.glob("*.json"):
        if path.stem not in made:
            path.unlink()
    for name, value in made.items():
        (folder / f"{name}.json").write_text(render(value))


def main() -> int:
    write(FIXTURES)
    print(f"wrote {len(list(FIXTURES.glob('*.json')))} fixtures to {FIXTURES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
