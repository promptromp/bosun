"""lib/settings.py: what `bosun settings get` shows and what `bosun settings set` writes and needs done
(docs/design/control-panel.md, "The Settings window")."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import init_plan
import settings


DEFAULTS = {
    "BOSUN_DAYS": "MTWRF",
    "BOSUN_START_TIME": "08:00",
    "BOSUN_AWAKE_UNTIL": "20:00",
    "BOSUN_START_AT_LOGIN": "1",
    "BOSUN_UPDATE_CHANNEL": "releases",
    "BOSUN_LEARN_AT": "18:30",
    "BOSUN_WAKE_DELAY_MINUTES": "1",
}
COMMENTED = """\
# acme's bosun: the schedule first
BOSUN_DAYS="MTWRF"
BOSUN_START_TIME="08:00"   # the morning job

# Features
BOSUN_EVENTS_ENABLED="1"
"""


def get(env_text: str | None = "", error: str | None = None, wake: str = "none") -> dict[str, Any]:
    return settings.view(env_text, error, DEFAULTS, wake, "/Users/me/.config/bosun")


class TestView(unittest.TestCase):
    def test_get_defaults_when_unset(self) -> None:
        shown = get("")
        self.assertEqual(shown["contract"], 1)
        self.assertTrue(shown["readable"])
        self.assertIsNone(shown["error"])
        self.assertEqual(shown["config_dir"], "/Users/me/.config/bosun")
        days = shown["settings"]["BOSUN_DAYS"]
        self.assertEqual(
            {k: days[k] for k in ("value", "default", "set", "valid")},
            {"value": "MTWRF", "default": "MTWRF", "set": False, "valid": True},
        )
        self.assertTrue(days["meaning"])
        self.assertEqual(set(shown["settings"]), {s.key for s in settings.SETTINGS})

    def test_get_shows_an_unknown_value_as_is(self) -> None:
        days = get('BOSUN_DAYS="weekdays"\n')["settings"]["BOSUN_DAYS"]
        self.assertEqual((days["value"], days["valid"], days["set"]), ("weekdays", False, True))

    def test_get_shows_a_line_it_cannot_read_as_written(self) -> None:
        days = get("BOSUN_DAYS=MTWRF SU\n")["settings"]["BOSUN_DAYS"]
        self.assertEqual((days["value"], days["valid"], days["set"]), ("MTWRF SU", False, True))

    def test_get_unreadable(self) -> None:
        shown = get(None, "Permission denied")
        self.assertFalse(shown["readable"])
        self.assertEqual(shown["error"], "Permission denied")
        for key, setting in shown["settings"].items():
            self.assertEqual(setting["value"], DEFAULTS[key])
            self.assertFalse(setting["set"])

    def test_get_does_not_read_a_file_the_shell_cannot(self) -> None:
        shown = get('BOSUN_DAYS="SU"\n', "line 3: syntax error")
        self.assertFalse(shown["readable"])
        self.assertEqual(shown["settings"]["BOSUN_DAYS"]["value"], "MTWRF")

    def test_get_says_whose_wake(self) -> None:
        self.assertEqual(get(wake="other")["wake"], "other")


class TestChange(unittest.TestCase):
    def test_set_changes_one_line(self) -> None:
        done = settings.change(COMMENTED, ["BOSUN_START_TIME=09:00"], DEFAULTS)
        # init's writer: the changed line written anew (its own comment goes), every other line as it was
        wanted = COMMENTED.replace('BOSUN_START_TIME="08:00"   # the morning job', 'BOSUN_START_TIME="09:00"')
        self.assertEqual(done.text, wanted)
        self.assertEqual(done.changed, {"BOSUN_START_TIME": ("08:00", "09:00")})
        self.assertLessEqual({settings.JOBS, settings.WAKE}, done.effects)
        self.assertNotIn(settings.KEEP_AWAKE, done.effects)

    def test_set_moves_learn_at(self) -> None:
        done = settings.change(COMMENTED, ["BOSUN_AWAKE_UNTIL=18:00"], DEFAULTS)
        learn = init_plan.learn_at(init_plan.Answers(start="08:00", until="18:00"))
        self.assertEqual(init_plan.parse_env(done.text)["BOSUN_LEARN_AT"], learn)
        self.assertEqual(done.changed["BOSUN_LEARN_AT"], ("18:30", learn))
        self.assertEqual(done.changed["BOSUN_AWAKE_UNTIL"], ("20:00", "18:00"))
        self.assertEqual(done.effects, frozenset({settings.JOBS, settings.KEEP_AWAKE}))

    def test_set_leaves_other_lines(self) -> None:
        text = '# mine\nBOSUN_DAYS="weekdays"\n'
        done = settings.change(text, ["BOSUN_UPDATE_CHANNEL=main"], DEFAULTS)
        self.assertTrue(done.text.startswith(text))
        self.assertEqual(init_plan.parse_env(done.text)["BOSUN_UPDATE_CHANNEL"], "main")
        self.assertEqual(done.changed, {"BOSUN_UPDATE_CHANNEL": ("releases", "main")})
        self.assertEqual(done.effects, frozenset())

    def test_an_added_setting_says_who_added_it(self) -> None:
        done = settings.change("", ["BOSUN_START_AT_LOGIN=0"], DEFAULTS)
        self.assertIn("# Added by `bosun settings`", done.text)
        self.assertEqual(done.effects, frozenset({settings.JOBS}))

    def test_refusals(self) -> None:
        cases = {
            "BOSUN_FOO=1": "BOSUN_FOO",
            "BOSUN_DAYS": "BOSUN_DAYS",
            "BOSUN_DAYS=XYZ": "BOSUN_DAYS",
            "BOSUN_DAYS=": "BOSUN_DAYS",
            "BOSUN_START_TIME=25:00": "BOSUN_START_TIME",
            "BOSUN_START_AT_LOGIN=yes": "BOSUN_START_AT_LOGIN",
            "BOSUN_UPDATE_CHANNEL=beta": "BOSUN_UPDATE_CHANNEL",
        }
        for assignment, key in cases.items():
            with self.subTest(assignment), self.assertRaises(settings.SettingsError) as caught:
                settings.change(COMMENTED, [assignment], DEFAULTS)
            self.assertIn(key, str(caught.exception))
            self.assertNotIn("\n", str(caught.exception))
        with self.assertRaises(settings.SettingsError) as caught:
            settings.change(COMMENTED, ["BOSUN_DAYS=MTWRF", "BOSUN_DAYS=SU"], DEFAULTS)
        self.assertIn("BOSUN_DAYS", str(caught.exception))

    def test_the_schedule_is_checked_whole(self) -> None:
        with self.assertRaises(settings.SettingsError) as caught:
            settings.change(COMMENTED, ["BOSUN_AWAKE_UNTIL=08:00"], DEFAULTS)
        self.assertIn("BOSUN_AWAKE_UNTIL", str(caught.exception))
        with self.assertRaises(settings.SettingsError) as caught:
            settings.change('BOSUN_DAYS="weekdays"\n', ["BOSUN_START_TIME=09:00"], DEFAULTS)
        self.assertIn("BOSUN_DAYS", str(caught.exception))

    def test_nothing_to_set_is_refused(self) -> None:
        with self.assertRaises(settings.SettingsError):
            settings.change(COMMENTED, [], DEFAULTS)

    def test_same_value_changes_nothing(self) -> None:
        for assignment in ("BOSUN_START_TIME=08:00", "BOSUN_UPDATE_CHANNEL=releases"):
            with self.subTest(assignment):
                done = settings.change(COMMENTED, [assignment], DEFAULTS)
                self.assertEqual(done.changed, {})
                self.assertEqual(done.effects, frozenset())
                self.assertEqual(done.text, COMMENTED)


def schedule(event: str) -> str:
    return f"Repeating power events:\n  {event}\nScheduled power events:\n [0]  wake at 11/20/2026 09:00:00\n"


class TestWakeOwner(unittest.TestCase):
    def test_wake_owner(self) -> None:
        ours = schedule(f"wakepoweron at {init_plan.pmset_label('08:01')} weekdays only")
        self.assertEqual(settings.wake_owner("", {}, DEFAULTS), settings.NONE)
        self.assertEqual(settings.wake_owner(ours, {}, DEFAULTS), settings.OURS)
        self.assertEqual(
            settings.wake_owner(schedule("wakepoweron at 6:30AM every day"), {}, DEFAULTS), "other"
        )
        moved = {"BOSUN_START_TIME": "09:00"}
        self.assertEqual(settings.wake_owner(ours, moved, DEFAULTS), settings.OTHER)
        self.assertEqual(
            settings.wake_owner(schedule("wakepoweron at 9:01AM weekdays only"), moved, DEFAULTS), "ours"
        )

    def test_a_start_that_does_not_parse_owns_no_wake(self) -> None:
        ours = schedule("wakepoweron at 8:01AM weekdays only")
        self.assertEqual(settings.wake_owner(ours, {"BOSUN_START_TIME": "soon"}, DEFAULTS), "other")


class TestOutput(unittest.TestCase):
    def test_result_and_failure(self) -> None:
        result = settings.result({"BOSUN_START_TIME": ("08:00", "09:00")}, ["a note"])
        self.assertEqual(
            result, {"contract": 1, "changed": {"BOSUN_START_TIME": ["08:00", "09:00"]}, "notes": ["a note"]}
        )
        self.assertEqual(settings.failure("no"), {"contract": 1, "error": "no"})

    def test_lines(self) -> None:
        self.assertEqual(
            settings.lines({"BOSUN_START_TIME": ("08:00", "09:00")}, ["a note"]),
            ["BOSUN_START_TIME: 08:00 → 09:00", "a note"],
        )
        self.assertEqual(len(settings.lines({}, [])), 1)


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = settings.main(list(argv))
    return status, out.getvalue(), err.getvalue()


class TestCommandLine(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.env = self.dir / "bosun.env"
        self.env.write_text(COMMENTED)
        self.example = self.dir / "example.env"
        self.example.write_text("".join(f'{k}="{v}"\n' for k, v in DEFAULTS.items()))
        self.common = ("--config-dir", str(self.dir), "--example", str(self.example))

    def test_get(self) -> None:
        status, out, _ = run("get", *self.common, "--schedule", "")
        self.assertEqual(status, 0)
        shown = json.loads(out)
        self.assertEqual(shown["settings"]["BOSUN_START_TIME"]["set"], True)
        self.assertEqual(shown["wake"], "none")

    def test_get_as_text(self) -> None:
        status, out, _ = run("get", *self.common, "--schedule", "", "--text")
        self.assertEqual(status, 0)
        self.assertIn("BOSUN_START_TIME=08:00  (set)", out)
        self.assertIn("BOSUN_UPDATE_CHANNEL=releases  (default)", out)
        self.assertIn("repeating wake: none", out)
        self.env.write_text('BOSUN_DAYS="weekdays"\n')
        self.assertIn(
            "BOSUN_DAYS=weekdays  (set, not one bosun can use",
            run("get", *self.common, "--schedule", "", "--text")[1],
        )

    def test_get_of_a_folder_with_no_bosun_env(self) -> None:
        self.env.unlink()
        shown = json.loads(run("get", *self.common, "--schedule", "")[1])
        self.assertTrue(shown["readable"])
        self.assertFalse(shown["settings"]["BOSUN_DAYS"]["set"])

    def test_get_with_the_shells_error(self) -> None:
        shown = json.loads(run("get", *self.common, "--schedule", "", "--env-error", "it stops")[1])
        self.assertEqual((shown["readable"], shown["error"]), (False, "it stops"))

    def test_plan(self) -> None:
        status, out, _ = run("plan", *self.common, "BOSUN_START_TIME=09:00")
        self.assertEqual(status, 0)
        self.assertEqual(sorted(out.split()), ["jobs", "wake"])
        status, out, err = run("plan", *self.common, "BOSUN_START_TIME=9")
        self.assertEqual((status, out), (2, ""))
        self.assertIn("BOSUN_START_TIME", err)
        self.assertEqual(self.env.read_text(), COMMENTED)

    def test_wake_owner(self) -> None:
        status, out, _ = run("wake-owner", *self.common, "--schedule", schedule("wakepoweron at 8:01AM"))
        self.assertEqual((status, out), (0, "ours\n"))

    def test_write(self) -> None:
        status, out, _ = run("write", *self.common, "--stamp", "20261114-090000", "BOSUN_START_TIME=09:00")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out)["changed"], {"BOSUN_START_TIME": ["08:00", "09:00"]})
        self.assertEqual(init_plan.parse_env(self.env.read_text())["BOSUN_START_TIME"], "09:00")
        self.assertEqual((self.dir / "bosun.env.pre-init-20261114-090000").read_text(), COMMENTED)

    def test_write_of_nothing_new_writes_nothing(self) -> None:
        status, out, _ = run("write", *self.common, "--stamp", "s", "BOSUN_START_TIME=08:00")
        self.assertEqual((status, json.loads(out)["changed"]), (0, {}))
        self.assertFalse((self.dir / "bosun.env.pre-init-s").exists())

    def test_say(self) -> None:
        changes = json.dumps(settings.result({"BOSUN_DAYS": ("MTWRF", "MTWRFS")}, []))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            status = settings.main(["say", "--wake-left", "--json"], stdin=io.StringIO(changes))
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out.getvalue())["notes"], [settings.WAKE_LEFT])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            settings.main(["say"], stdin=io.StringIO(changes))
        self.assertEqual(out.getvalue(), "BOSUN_DAYS: MTWRF → MTWRFS\n")

    def test_error(self) -> None:
        self.assertEqual(json.loads(run("error", "no")[1]), {"contract": 1, "error": "no"})

    def test_defaults_are_the_example(self) -> None:
        example = Path(__file__).resolve().parents[2] / "examples" / "generic" / "bosun.env"
        found = settings.defaults(example.read_text())
        self.assertEqual(found["BOSUN_DAYS"], init_plan.DEFAULT_DAYS)
        self.assertEqual(found["BOSUN_UPDATE_CHANNEL"], init_plan.DEFAULT_CHANNEL)


if __name__ == "__main__":
    unittest.main()
