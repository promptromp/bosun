"""lib/update_gate.py: every write checks the store first (docs/design/update.md, "Old code that
writes after the switch" and "Cut short"). Fictional data."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger
import update_backups as ub
import update_gate
import update_state
import versions


LIB = Path(__file__).resolve().parents[2] / "lib"
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
FROM = "aaaaaaa1111111111111111111111111111111111"
TO = "bbbbbbb2222222222222222222222222222222222"
# A stand-in for bin/bosun: what it was run with, in the folder GATE_TEST_OUT names.
STUB = """#!/bin/sh
out="$GATE_TEST_OUT"
printf '%s\\n' "$@" > "$out/args"
cat > "$out/stdin"
printf '%s' "${BOSUN_RERUN:-}" > "$out/rerun"
printf '%s' "$(basename "$(cd "$(dirname "$0")/.." && pwd -P)")" > "$out/version"
printf '%s' "${BOSUN_HOOK_SYNC:-}" > "$out/sync"
: > "$out/done"
exit 3
"""
# Holds update.lock until killed, as a running update does.
HOLD = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import update_state\n"
    "with update_state.one_update(Path(sys.argv[2])):\n"
    "    print('held', flush=True); time.sleep(60)\n"
)


def record(step: str, **fields: Any) -> update_state.UpdateState:
    return update_state.UpdateState(
        id="u1",
        from_root=fields.pop("from_root", "/Users/me/.local/share/bosun-versions/aaaaaaa11111"),
        to_root=fields.pop("to_root", "/Users/me/.local/share/bosun-versions/bbbbbbb22222"),
        from_commit=FROM,
        to_commit=TO,
        step=step,
        started_at="2026-11-05T09:00:00+00:00",
        hold_until=10**12,
        **fields,
    )


@contextlib.contextmanager
def update_running(state_dir: Path) -> Iterator[None]:
    """update.lock held by another process for the block."""
    child = subprocess.Popen(
        [sys.executable, "-c", HOLD, str(LIB), str(state_dir)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        yield
    finally:
        child.kill()
        child.wait()
        if child.stdout:
            child.stdout.close()


def stdin_of(data: bytes) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="surrogateescape", newline="\n")


class World(unittest.TestCase):
    """A state and config folder, and an install: two version folders with a stand-in bin/bosun
    each, and the install link at <which>."""

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.state = tmp / "state"
        self.config = tmp / "config"
        self.state.mkdir()
        self.config.mkdir()
        self.out = tmp / "out"
        self.out.mkdir()
        share = tmp / "share"
        self.install = share / "bosun"
        self.old = share / "bosun-versions" / FROM[:12]
        self.new = share / "bosun-versions" / TO[:12]
        for folder in (self.old, self.new):
            (folder / "bin").mkdir(parents=True)
            (folder / "bin" / "bosun").write_text(STUB)
            (folder / "bin" / "bosun").chmod(0o755)
        versions.point(self.install, self.new)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(update_gate, "ROOT", self.old))  # this runs the old version
        stack.enter_context(mock.patch.object(update_gate, "WAIT_SECONDS", 0.3))
        stack.enter_context(
            mock.patch.dict(
                os.environ, {"GATE_TEST_OUT": str(self.out), "BOSUN_CONFIG_DIR": str(self.config)}
            )
        )
        os.environ.pop(update_gate.RERUN_ENV, None)

    def ran(self) -> dict[str, Any] | None:
        """What the stand-in was run with, or None when nothing ran."""
        if not (self.out / "done").exists():
            return None
        return {
            "args": (self.out / "args").read_text().splitlines(),
            "stdin": (self.out / "stdin").read_bytes(),
            "rerun": (self.out / "rerun").read_text(),
            "version": (self.out / "version").read_text(),
        }

    def guarded(self, run: Any, argv: list[str], stdin: bytes = b"") -> tuple[int, str]:
        err = io.StringIO()
        with mock.patch("sys.stdin", stdin_of(stdin)), contextlib.redirect_stderr(err):
            code = update_gate.guarded("ledger", run, argv)
        return code, err.getvalue()

    def ledger_json(self, data: dict[str, Any]) -> None:
        (self.state / "ledger.json").write_text(json.dumps(data))

    def cut_short(self, step: str = "switched") -> Path:
        """An update cut short at <step>: its backup of the ledger, the ledger changed since, and
        the link at the new version."""
        self.ledger_json({"version": 1, "items": {"review:acme/exporter#1": {"state": "requested"}}})
        backup = ub.take(self.state, self.config, FROM, TO, {"ledger": 1}, NOW)
        self.ledger_json({"version": 2, "items": {}, "migrated": True})
        update_state.write(
            self.state,
            record(
                step,
                from_root=str(self.old),
                to_root=str(self.new),
                backup=str(backup),
                restorable=("state/ledger.json",),
            ),
        )
        return backup


class CheckTest(World):
    def test_a_store_at_its_format_is_written(self) -> None:
        update_gate.check_write("ledger", 1, 1, self.state)  # no refusal

    def test_no_store_yet_is_written(self) -> None:
        update_gate.check_write("ledger", 0, 2, self.state)  # no refusal

    def test_a_newer_store_is_refused(self) -> None:
        with self.assertRaises(update_gate.StoreNewer) as caught:
            update_gate.check_write("ledger", 2, 1, self.state)
        self.assertEqual(
            (caught.exception.store, caught.exception.found, caught.exception.mine), ("ledger", 2, 1)
        )

    def test_an_older_store_is_refused(self) -> None:
        with self.assertRaises(update_gate.StoreOlder) as caught:
            update_gate.check_write("knowledge", 1, 2, self.state)
        self.assertIn("run bosun update to migrate it", str(caught.exception))

    def test_a_freeze_left_by_a_dead_update_is_cut_short(self) -> None:
        update_state.write(self.state, record("migrated"))
        with self.assertRaises(update_gate.UpdateCutShort) as caught:
            update_gate.check_write("ledger", 1, 1, self.state)
        self.assertEqual(caught.exception.state.step, "migrated")

    def test_a_freeze_of_a_running_update_is_busy(self) -> None:
        update_state.write(self.state, record("migrated"))
        with update_running(self.state), self.assertRaises(update_gate.UpdateBusy):
            update_gate.check_write("ledger", 1, 1, self.state)

    def test_a_freeze_comes_before_the_format(self) -> None:
        update_state.write(self.state, record("switched"))
        with self.assertRaises(update_gate.UpdateCutShort):
            update_gate.check_write("ledger", 2, 1, self.state)  # the backup is the truth, not the format

    def test_an_update_outside_its_freeze_holds_no_write(self) -> None:
        for step in ("holding", "checked", "released", "done", "restored", "failed"):
            update_state.write(self.state, record(step))
            update_gate.check_write("ledger", 1, 1, self.state)  # no refusal


class RerunTest(World):
    def test_a_newer_store_reruns_with_the_same_stdin(self) -> None:
        """Review Focus 1: the same arguments and the same bytes, the part read and the rest."""
        data = b'{"tool_name": "Bash"}\r\nsecond line \xc3\xa9 \xff\nand the rest'
        seen: list[str] = []

        def run(argv: list[str]) -> int:
            seen.append(sys.stdin.readline())
            raise update_gate.StoreNewer("ledger", 2, 1)

        code, err = self.guarded(run, ["said", "--window", "api", "-"], data)
        self.assertEqual((code, err), (3, ""))
        self.assertEqual(seen, ['{"tool_name": "Bash"}\r\n'])
        ran = self.ran()
        assert ran is not None
        self.assertEqual(ran["args"], ["ledger", "said", "--window", "api", "-"])
        self.assertEqual(ran["stdin"], data)
        self.assertEqual(ran["rerun"], "1")
        self.assertEqual(ran["version"], TO[:12])  # through the install link, to the version it leads to

    def test_stdin_read_whole_is_given_whole(self) -> None:
        def run(argv: list[str]) -> int:
            json.load(sys.stdin)
            raise update_gate.StoreNewer("ledger", 2, 1)

        self.guarded(run, ["--permission-request"], b'{"a": 1}\n')
        ran = self.ran()
        assert ran is not None
        self.assertEqual(ran["stdin"], b'{"a": 1}\n')

    def test_a_second_refusal_never_loops(self) -> None:
        def run(argv: list[str]) -> int:
            raise update_gate.StoreNewer("ledger", 3, 2)

        with mock.patch.dict(os.environ, {update_gate.RERUN_ENV: "1"}):
            code, err = self.guarded(run, ["upsert"])
        self.assertEqual(code, 1)
        self.assertIsNone(self.ran())
        self.assertIn("format 3", err)
        self.assertIn("too", err)

    def test_an_older_store_is_refused_in_one_line(self) -> None:
        def run(argv: list[str]) -> int:
            raise update_gate.StoreOlder("ledger", 1, 2)

        code, err = self.guarded(run, ["upsert"])
        self.assertEqual(code, 1)
        self.assertIsNone(self.ran())
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("format 1", err)
        self.assertIn("run bosun update to migrate it", err)

    def test_a_command_that_is_not_refused_runs_once(self) -> None:
        def run(argv: list[str]) -> int:
            return len(sys.stdin.read())

        self.assertEqual(self.guarded(run, [], b"four")[0], 4)
        self.assertIsNone(self.ran())

    def test_stdin_is_put_back(self) -> None:
        mine = stdin_of(b"")
        with mock.patch("sys.stdin", mine):
            update_gate.guarded("ledger", lambda argv: 0, [])
            self.assertIs(sys.stdin, mine)


class CutShortTest(World):
    def test_a_cut_freeze_is_put_back_then_run_again(self) -> None:
        self.cut_short("switched")

        def run(argv: list[str]) -> int:
            sys.stdin.read()
            update_gate.check_write("ledger", 2, 1, self.state)
            return 0

        code, err = self.guarded(run, ["upsert", "review:acme/exporter#2"], b"payload")
        self.assertEqual(
            err.strip(),
            "an update from aaaaaaa to bbbbbbb was cut short while writes were paused; "
            "putting back its backup and aaaaaaa",
        )
        self.assertEqual(code, 3)
        self.assertEqual(json.loads((self.state / "ledger.json").read_text())["version"], 1)
        self.assertEqual(versions.current(self.install), self.old)
        ran = self.ran()
        assert ran is not None
        self.assertEqual((ran["version"], ran["stdin"]), (FROM[:12], b"payload"))
        self.assertEqual(ran["args"], ["ledger", "upsert", "review:acme/exporter#2"])

    def test_without_the_config_folder_nothing_is_put_back(self) -> None:
        self.cut_short("migrated")

        def run(argv: list[str]) -> int:
            update_gate.check_write("ledger", 2, 1, self.state)
            return 0

        with mock.patch.dict(os.environ):
            del os.environ["BOSUN_CONFIG_DIR"]
            code, err = self.guarded(run, ["upsert"])
        self.assertEqual(code, 1)
        self.assertIn("BOSUN_CONFIG_DIR", err)
        self.assertIsNone(self.ran())
        self.assertEqual(update_state.read(self.state).step, "migrated")  # type: ignore[union-attr]

    def test_a_busy_freeze_still_busy_after_the_wait_is_refused(self) -> None:
        update_state.write(self.state, record("migrated"))

        def run(argv: list[str]) -> int:
            update_gate.check_write("ledger", 1, 1, self.state)
            return 0

        with update_running(self.state):
            start = time.monotonic()
            code, err = self.guarded(run, ["upsert"])
        self.assertGreaterEqual(time.monotonic() - start, 0.3)
        self.assertEqual(code, 1)
        self.assertIn("pausing writes", err)
        self.assertIsNone(self.ran())

    def test_a_busy_freeze_over_by_then_runs_again(self) -> None:
        update_state.write(self.state, record("migrated"))

        def run(argv: list[str]) -> int:
            raise update_gate.UpdateBusy(record("migrated"), self.state)  # it finished meanwhile

        update_state.write(self.state, record("restored"))
        self.assertEqual(self.guarded(run, ["upsert"])[0], 3)
        self.assertIsNotNone(self.ran())

    def test_a_busy_freeze_whose_update_stopped_is_put_back_then_run_again(self) -> None:
        """R17: the update died while this write waited; its run again would only find the freeze
        cut short and stop, so it is put back here first, and run again once."""
        self.cut_short("migrated")

        def run(argv: list[str]) -> int:
            raise update_gate.UpdateBusy(update_state.read(self.state), self.state)  # type: ignore[arg-type]

        code, err = self.guarded(run, ["upsert"])
        self.assertIn("was cut short while writes were paused; putting back its backup", err)
        self.assertEqual(code, 3)
        self.assertEqual(json.loads((self.state / "ledger.json").read_text())["version"], 1)
        self.assertEqual(versions.current(self.install), self.old)
        self.assertEqual(update_state.read(self.state).step, "restored")  # type: ignore[union-attr]
        ran = self.ran()
        assert ran is not None
        self.assertEqual((ran["version"], ran["rerun"]), (FROM[:12], "1"))


class LedgerCommandTest(World):
    """The real `bosun ledger` main under guarded, on a ledger a newer format reshaped."""

    UPSERT = ("upsert", "mypr:acme/exporter#7", "--kind", "my-pr", "--title", "VAT")

    def setUp(self) -> None:
        super().setUp()
        os.environ["BOSUN_STATE_DIR"] = str(self.state)  # restored by World's patch of os.environ
        self.addCleanup(ledger.configure, self.state)

    def test_a_reshaped_newer_ledger_is_run_again_not_refused_as_malformed(self) -> None:
        self.ledger_json({"version": ledger.LEDGER_FORMAT + 1, "items": []})
        code, err = self.guarded(ledger.main, list(self.UPSERT))
        self.assertEqual((code, err), (3, ""))
        ran = self.ran()
        assert ran is not None
        self.assertEqual((ran["args"], ran["version"]), (["ledger", *self.UPSERT], TO[:12]))

    def test_a_reshaped_ledger_in_a_freeze_cut_short_is_put_back(self) -> None:
        self.cut_short("migrated")
        self.ledger_json({"version": ledger.LEDGER_FORMAT + 1, "items": []})  # what the migration left
        code, err = self.guarded(ledger.main, list(self.UPSERT))
        self.assertIn("was cut short while writes were paused", err)
        self.assertEqual(code, 3)
        restored = json.loads((self.state / "ledger.json").read_text())
        self.assertEqual(
            restored, {"version": 1, "items": {"review:acme/exporter#1": {"state": "requested"}}}
        )
        ran = self.ran()
        assert ran is not None
        self.assertEqual(ran["version"], FROM[:12])


class RecoverTest(World):
    def test_recover_puts_back_the_backup_and_the_version(self) -> None:
        self.cut_short("switched")
        self.assertTrue(update_gate.recover(self.state, self.config))
        ledger = json.loads((self.state / "ledger.json").read_text())
        self.assertEqual(ledger, {"version": 1, "items": {"review:acme/exporter#1": {"state": "requested"}}})
        self.assertEqual(versions.current(self.install), self.old)
        self.assertEqual(update_state.read(self.state).step, "restored")  # type: ignore[union-attr]

    def test_recover_twice_is_once(self) -> None:
        self.cut_short("switched")
        update_gate.recover(self.state, self.config)
        before = (
            (self.state / "ledger.json").read_bytes(),
            os.readlink(self.install),
            (self.state / update_state.STATE_FILE).read_bytes(),
        )
        self.assertFalse(update_gate.recover(self.state, self.config))
        after = (
            (self.state / "ledger.json").read_bytes(),
            os.readlink(self.install),
            (self.state / update_state.STATE_FILE).read_bytes(),
        )
        self.assertEqual(after, before)

    def test_a_freeze_cut_before_its_backup_only_points_back(self) -> None:
        self.ledger_json({"version": 1, "items": {}})
        update_state.write(self.state, record("marked", from_root=str(self.old), to_root=str(self.new)))
        self.assertTrue(update_gate.recover(self.state, self.config))
        self.assertEqual(versions.current(self.install), self.old)
        self.assertEqual(json.loads((self.state / "ledger.json").read_text()), {"version": 1, "items": {}})

    def test_a_checkout_is_never_made_a_link(self) -> None:
        checkout = self.state.parent / "checkout"
        checkout.mkdir()
        update_state.write(self.state, record("marked", from_root=str(checkout)))
        self.assertTrue(update_gate.recover(self.state, self.config))
        self.assertTrue(checkout.is_dir() and not checkout.is_symlink())

    def test_nothing_to_recover(self) -> None:
        self.assertFalse(update_gate.recover(self.state, self.config))
        update_state.write(self.state, record("done"))
        self.assertFalse(update_gate.recover(self.state, self.config))

    def test_recover_waits_for_an_update_then_gives_up(self) -> None:
        self.cut_short("switched")
        with update_running(self.state), self.assertRaises(update_state.UpdateRunning):
            update_gate.recover(self.state, self.config)
        self.assertEqual(versions.current(self.install), self.new)  # nothing done


class EntryPointTest(unittest.TestCase):
    """Each command that writes runs under guarded: run from an old version's folder (a copy of
    lib/) against a newer store, it runs again through the install link, whose stand-in records
    what it got."""

    tmp: Path
    old: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        share = cls.tmp / "share"
        cls.old = share / "bosun-versions" / FROM[:12]
        new = share / "bosun-versions" / TO[:12]
        shutil.copytree(LIB, cls.old / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        (new / "bin").mkdir(parents=True)
        (new / "bin" / "bosun").write_text(STUB)
        (new / "bin" / "bosun").chmod(0o755)
        versions.point(share / "bosun", new)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp(dir=self.tmp))
        self.state = tmp / "state"
        self.config = tmp / "config"
        self.out = tmp / "out"
        for folder in (self.state, self.config, self.out):
            folder.mkdir()
        (self.state / "ledger.json").write_text(json.dumps({"version": 99, "items": {}}))
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp),
            "GATE_TEST_OUT": str(self.out),
            "BOSUN_STATE_DIR": str(self.state),
            "BOSUN_CONFIG_DIR": str(self.config),
            "BOSUN_LOG_DIR": str(tmp / "logs"),
        }

    def run_old(self, script: str, argv: list[str], stdin: bytes = b"", **env: str) -> int:
        done = subprocess.run(
            [sys.executable, str(self.old / "lib" / script), *argv],
            input=stdin,
            env={**self.env, **env},
            capture_output=True,
            check=False,
            timeout=60,
        )
        return done.returncode

    def ran(self, seconds: float = 0.0) -> tuple[list[str], bytes, str]:
        deadline = time.monotonic() + seconds
        while not (self.out / "done").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        return (
            (self.out / "args").read_text().splitlines(),
            (self.out / "stdin").read_bytes(),
            (self.out / "rerun").read_text(),
        )

    def test_bosun_ledger(self) -> None:
        argv = ["upsert", "mypr:acme/exporter#7", "--kind", "my-pr", "--title", "VAT"]
        self.assertEqual(self.run_old("ledger.py", argv), 3)
        self.assertEqual(self.ran(), (["ledger", *argv], b"", "1"))

    def test_bosun_kg(self) -> None:
        (self.config / "knowledge").mkdir()
        (self.config / "knowledge" / "format.json").write_text('{"format": 99}')
        argv = ["add-entity", "repo/invoice-exporter", "--name", "invoice exporter"]
        self.assertEqual(self.run_old("knowledge.py", argv, BOSUN_KNOWLEDGE_GIT="0"), 3)
        self.assertEqual(self.ran(), (["kg", *argv], b"", "1"))

    def test_the_guard_reruns_with_the_same_event(self) -> None:
        event = json.dumps(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "tool_input": {"command": "touch x"},
            }
        ).encode()
        code = self.run_old(
            "guard.py",
            ["--permission-request"],
            event,
            BOSUN_APPROVALS_ENABLED="1",
            BOSUN_APPROVALS_GATED_COMMANDS="touch",
        )
        self.assertEqual(code, 0)  # a hook that never blocks the session
        self.assertEqual(self.ran(), (["guard", "--permission-request"], event, "1"))

    def test_the_hooks_detached_child_reruns_in_the_foreground(self) -> None:
        prompt = json.dumps(
            {"hook_event_name": "UserPromptSubmit", "session_id": "s-1", "prompt": "go with b"}
        )
        argv = ["user-prompt", "--window", "exporter"]
        self.assertEqual(self.run_old("hook.py", argv, prompt.encode(), BOSUN_EVENTS_ENABLED="1"), 0)
        self.assertEqual(self.ran(seconds=20), (["hook", *argv], prompt.encode(), "1"))
        self.assertEqual((self.out / "sync").read_text(), "1")

    def hook_log(self, seconds: float, until: str) -> str:
        deadline = time.monotonic() + seconds
        text = ""
        while until not in text and time.monotonic() < deadline:
            time.sleep(0.05)
            text = "".join(p.read_text() for p in Path(self.env["BOSUN_LOG_DIR"]).glob("hook-*.log"))
        return text

    def test_a_hook_child_that_cannot_write_says_so_in_the_log(self) -> None:
        """Its stderr is the hook log, and giving up is an ERROR line for the watchdog."""
        prompt = json.dumps(
            {"hook_event_name": "UserPromptSubmit", "session_id": "s-1", "prompt": "go with b"}
        )
        argv = ["user-prompt", "--window", "exporter"]
        code = self.run_old("hook.py", argv, prompt.encode(), BOSUN_EVENTS_ENABLED="1", BOSUN_RERUN="1")
        self.assertEqual(code, 0)
        log = self.hook_log(20, "ERROR")
        self.assertIn("bosun hook: the ledger is format 99, newer than the installed bosun's 1 too", log)
        self.assertIn("ERROR: bosun hook user-prompt --window exporter recorded nothing", log)
        self.assertFalse((self.out / "done").exists())

    def test_bosun_review_watch(self) -> None:
        url = "https://github.com/acme/exporter/pull/102"
        (self.state / "ledger.json").unlink()  # made here, as the old version's ledger
        ledger.configure(self.state)
        self.addCleanup(ledger.configure, self.state)
        with mock.patch.object(ledger, "now", return_value="2026-01-05T08:00:00+00:00"):
            for argv in (
                ["upsert", "mypr:acme/exporter#102", "--kind", "my-pr", "--title", "footer", "--url", url],
                ["transition", "mypr:acme/exporter#102", "changes-requested", "--note", "round 1"],
                ["transition", "mypr:acme/exporter#102", "addressing", "--note", "fixing"],
            ):
                args = ledger.build_parser().parse_args(argv)
                args.func(args)
        with mock.patch.object(ledger, "now", return_value="2026-01-05T09:00:00+00:00"):
            args = ledger.build_parser().parse_args(
                ["record-delegation", "mypr:acme/exporter#102", "--window", "exporter", "--task", "fix-my-pr"]
            )
            args.func(args)
        data = json.loads((self.state / "ledger.json").read_text())
        data["version"] = 99
        (self.state / "ledger.json").write_text(json.dumps(data))
        review = {
            "id": "R2",
            "url": f"{url}#pullrequestreview-R2",
            "author": {"login": "dana-acme"},
            "state": "CHANGES_REQUESTED",
            "submittedAt": "2026-01-05T10:00:00Z",
            "body": "",
        }
        pull = {
            "url": url,
            "author": {"login": "me-acme"},
            "reviews": {"nodes": [review]},
            "reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []},
        }
        page = self.out.parent / "page.json"
        page.write_text(json.dumps({"data": {"repository": {"pullRequest": pull}}}))
        fake = self.out.parent / "bin"
        fake.mkdir()
        (fake / "gh").write_text('#!/bin/sh\ncat "$FAKE_GH_PAGE"\n')
        (fake / "gh").chmod(0o755)
        path = f"{fake}{os.pathsep}{os.environ['PATH']}"
        self.assertEqual(self.run_old("review_watch.py", ["--quiet"], PATH=path, FAKE_GH_PAGE=str(page)), 3)
        self.assertEqual(self.ran(), (["review-watch", "--quiet"], b"", "1"))


if __name__ == "__main__":
    unittest.main()
