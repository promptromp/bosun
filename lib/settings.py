"""What `bosun settings get|set` shows and changes (docs/design/control-panel.md, "The Settings window").

The menu bar app's Settings window is a view over these two commands, and a person at a terminal can use them
without it. The app never writes `bosun.env`: `set` writes it through `bosun init`'s own writer
(`init_plan.set_settings` and `init.write_with_backup`: a backup, every other line and comment kept), after
checking each value as the wizard does. This module decides and writes; `libexec/bosun-settings` orders what a
change needs done on the Mac (sudo, the launchd jobs, pmset, the keep-awake), which `Change.effects` names.

    settings.py get --config-dir D [--example F] [--schedule S] [--env-error E] [--text]
        the view, one JSON object (`contract` 1; it only grows), or with --text as lines for a person
    settings.py plan --config-dir D [--example F] KEY=VALUE...
        what the change needs done, one effect a line; a refused change: exit 2 and one line on stderr
    settings.py wake-owner --config-dir D [--example F] [--schedule S] [--set KEY=VALUE]...
        whose the Mac's repeating wake is: ours, leftover (the one bosun sets, though BOSUN_WAKE=0 says it
        sets none: to cancel, never to move), none or other; --set: a setting in force over what bosun.env
        says (the environment's, as `bosun doctor` and install.sh have it)
    settings.py write --config-dir D [--example F] --stamp S [--wake] KEY=VALUE...
        the change written, and what changed as JSON; --wake: the change sets the Mac's repeating wake, which
        is then bosun's to set (BOSUN_WAKE=1), written with it
    settings.py put-back --config-dir D
        after a step of `set` that failed: bosun.env as `write` (its JSON on stdin) found it: the backup it
        made copied back, or the bosun.env it made removed; neither (it reports no backup) is exit 1
    settings.py say [--json] [--wake-left] [--wake-leftover]
        what `write` printed (on stdin) as the lines a person reads, or as `set --json`'s object; --wake-left:
        with the note that another app's repeating wake was left as it is; --wake-leftover: that bosun's own,
        which BOSUN_WAKE=0 says it does not set, was
    settings.py error MESSAGE
        `set --json`'s object for a failure

<S> is what `pmset -g sched` printed; without --schedule it is asked (read-only). <E> is why the shell could
not load `bosun.env` (lib/common.sh's read_config): bosun then runs on none of it, and refuses to start.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, TextIO

import init
import init_detect
import init_plan
import update_check
from awake import DAY_LETTERS


CONTRACT = 1
JOBS, WAKE, KEEP_AWAKE = "jobs", "wake", "keep-awake"  # what a change needs done
OURS, NONE, OTHER = "ours", "none", "other"  # whose the Mac's repeating wake is
LEFTOVER = "leftover"  # bosun's own, though BOSUN_WAKE=0: shown as other in `get --json`, which the app reads
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generic" / "bosun.env"  # the defaults
DAYS, START, UNTIL, LEARN_AT = "BOSUN_DAYS", "BOSUN_START_TIME", "BOSUN_AWAKE_UNTIL", "BOSUN_LEARN_AT"
SCHEDULE_KEYS = {"days": DAYS, "start": START, "until": UNTIL}  # init_plan.schedule_problems' names for them
WAKE_DELAY = "BOSUN_WAKE_DELAY_MINUTES"
WAKE_OURS = init_plan.WAKE_KEY  # whether the Mac's repeating wake is bosun's to set: 1 once `set` sets it
WRITER = "bosun settings"  # the comment over a setting it adds: "# Added by `bosun settings`"
WAKE_LEFT = (
    "another app's repeating wake was left as it is (pmset keeps one), so the Mac does not wake for bosun's "
    "schedule; --replace-wake replaces it"
)
WAKE_LEFTOVER = (
    "bosun's own repeating wake is still set, though BOSUN_WAKE=0 says bosun sets none, and was left as is: "
    f"{init_plan.CANCEL_WAKE} cancels it ({init_plan.CANCEL_TAKES_ALL}), "
    "--replace-wake makes it bosun's again"
)


class SettingsError(Exception):
    """A change refused, in one line that names the setting."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SettingsError(message)


def _one_of(*allowed: str) -> Callable[[str], str | None]:
    def problem(value: str) -> str | None:
        return None if value in allowed else f"must be {' or '.join(allowed)}"

    return problem


def _days(value: str) -> str | None:
    letters = DAY_LETTERS
    if value and not set(value) - set(letters) and len(set(value)) == len(value):
        return None
    return f"must use each of {letters} at most once (M T W R F S U, Monday first)"


def _time(value: str) -> str | None:
    return None if init_plan.TIME.match(value) else "must be HH:MM, 24-hour"


@dataclass(frozen=True)
class Setting:
    key: str
    meaning: str  # one line, shown by the window
    problem: Callable[[str], str | None]  # why <value> is refused, or None
    effects: frozenset[str]


SETTINGS: tuple[Setting, ...] = (
    Setting(
        "BOSUN_START_AT_LOGIN",
        "Start bosun when you log in, during your hours (1), or only at the start time and from Start (0)",
        _one_of("0", "1"),
        frozenset({JOBS}),
    ),
    Setting(
        init_plan.UPDATE_CHANNEL,
        "What bosun update follows: the dated releases, or the head of main",
        _one_of(*update_check.CHANNELS),
        frozenset(),
    ),
    Setting(
        DAYS,
        "The days bosun starts on, in pmset's letters: MTWRFSU, Monday first",
        _days,
        frozenset({JOBS, WAKE}),
    ),
    Setting(
        START,
        "When bosun starts on those days (HH:MM); the Mac is woken just after it",
        _time,
        frozenset({JOBS, WAKE}),
    ),
    Setting(
        UNTIL,
        "Until when the Mac is kept awake (HH:MM; at or before the start time means past midnight)",
        _time,
        frozenset({JOBS, KEEP_AWAKE}),
    ),
)
BY_KEY = {setting.key: setting for setting in SETTINGS}


@dataclass(frozen=True)
class Change:
    text: str  # the new bosun.env (init_plan.set_settings)
    changed: dict[str, tuple[str, str]]  # key -> (before, after), only real changes
    effects: frozenset[str]


def defaults(example_text: str) -> dict[str, str]:
    """The defaults: what examples/generic/bosun.env assigns (kept equal to lib/common.sh's by a test)."""
    return init_plan.parse_env(example_text)


def assigned(text: str) -> dict[str, str]:
    """What <text> assigns, a later line winning as in the shell; a value that is not one shell word is kept
    as written, so that it is shown as it is (and refused as a value), never taken for the default."""
    found = {}
    for line in text.splitlines():
        match = init_plan.ASSIGNMENT.match(line)
        if match:
            value = init_plan.shell_value(match.group(2))
            found[match.group(1)] = match.group(2).strip() if value is None else value
    return found


def view(
    env_text: str | None, error: str | None, defaults: Mapping[str, str], wake: str, config_dir: str
) -> dict[str, Any]:
    """`bosun settings get --json`. A value it cannot show is shown as such, never as the default: with
    <env_text> None (bosun.env could not be read; <error> says why) every value is null and not valid. Text
    the shell could not load (<error> says why) still shows what it assigns, as written, each with its own
    `valid`."""
    readable = env_text is not None
    found = assigned(env_text) if env_text is not None else {}
    shown = {}
    for setting in SETTINGS:
        default = defaults.get(setting.key, "")
        value = found.get(setting.key, default) if readable else None
        shown[setting.key] = {
            "value": value,
            "default": default,
            "set": setting.key in found,
            "valid": value is not None and setting.problem(value) is None,
            "meaning": setting.meaning,
        }
    return {
        "contract": CONTRACT,
        "readable": readable,
        "error": error,
        "config_dir": config_dir,
        "wake": wake,
        "settings": shown,
    }


def _shown(text: str) -> str:
    return init_plan.CONTROL.sub("?", text)


def _wanted(assignments: Sequence[str]) -> dict[str, str]:
    if not assignments:
        raise SettingsError("nothing to set: give KEY=VALUE")
    wanted: dict[str, str] = {}
    for assignment in assignments:
        key, has_value, value = assignment.partition("=")
        setting = BY_KEY.get(key)
        if setting is None:
            raise SettingsError(f"{_shown(key)}: not a setting this changes; it changes {', '.join(BY_KEY)}")
        if not has_value:
            raise SettingsError(f"{key}: give it a value, as {key}=<value>")
        if key in wanted:
            raise SettingsError(f"{key}: given twice")
        problem = setting.problem(value)
        if problem:
            raise SettingsError(f"{key}: '{_shown(value)}' {problem}")
        wanted[key] = value
    return wanted


def _named(problem: str) -> str:
    """One of init_plan.schedule_problems, naming the setting as bosun.env does."""
    name, _, rest = problem.partition(":")
    return f"{SCHEDULE_KEYS.get(name, name)}:{rest}"


def _schedule_changes(current: Mapping[str, str], wanted: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """The schedule after the change checked whole, as the wizard checks it; with the start or the until
    moved, learning's time moved with them, as a re-run of `bosun init` moves it."""
    new = {**current, **wanted}
    answers = init_plan.Answers(days=new.get(DAYS, ""), start=new.get(START, ""), until=new.get(UNTIL, ""))
    problems = init_plan.schedule_problems(answers)
    if problems:
        raise SettingsError(_named(problems[0]))
    learn, before = init_plan.learn_at(answers), current.get(LEARN_AT, "")
    moved = wanted.keys() & {START, UNTIL}
    return {LEARN_AT: (before, learn)} if moved and learn != before else {}


def _wake_changes(
    current: Mapping[str, str], changed: Mapping[str, tuple[str, str]], wake: bool
) -> dict[str, tuple[str, str]]:
    """BOSUN_WAKE made 1, when the change (<changed>, not nothing) sets the Mac's repeating wake (<wake>) and
    the config (<current>) does not say it is bosun's yet."""
    if wake and changed and current.get(WAKE_OURS, "1") != "1":
        return {WAKE_OURS: (current.get(WAKE_OURS, ""), "1")}
    return {}


def change(
    env_text: str, assignments: Sequence[str], defaults: Mapping[str, str], *, wake: bool = False
) -> Change:
    """<env_text> with <assignments> made (KEY=VALUE each); SettingsError, in one line, for a key it does
    not offer, one without a value or given twice, a value the wizard would refuse, or a schedule that is not
    one. <wake>: the change sets the Mac's repeating wake, which is bosun's from then on (BOSUN_WAKE=1, with
    the change it comes with: on its own it is nothing to write)."""
    wanted = _wanted(assignments)
    current = {**defaults, **assigned(env_text)}
    real = {key: value for key, value in wanted.items() if current.get(key, "") != value}
    changed = {key: (current.get(key, ""), value) for key, value in real.items()}
    if real.keys() & set(SCHEDULE_KEYS.values()):
        changed.update(_schedule_changes(current, real))
    changed.update(_wake_changes(current, changed, wake))
    if not changed:
        return Change(env_text, {}, frozenset())
    effects = frozenset().union(*(BY_KEY[key].effects for key in changed if key in BY_KEY))
    after = {key: value for key, (_, value) in changed.items()}
    return Change(init_plan.set_settings(env_text, after, by=WRITER), changed, effects)


def wake_owner(schedule: str, env: Mapping[str, str], defaults: Mapping[str, str]) -> str:
    """Whose the Mac's repeating wake in <schedule> (`pmset -g sched`) is: OURS when it is the one bosun sets
    for the settings in <env> (init_plan.wake_command would set none), LEFTOVER when it is that one but <env>
    says BOSUN_WAKE=0 (a wake left from before the no: to cancel, not to move), NONE when there is none, else
    OTHER."""
    wake = init_detect.repeating_wake(schedule)
    if not wake:
        return NONE
    settings = {**defaults, **env}
    facts = init_plan.Facts(home="", today="", env=settings, repeating_wake=wake)
    answers = init_plan.Answers(days=settings.get(DAYS, ""), start=settings.get(START, ""))
    try:
        command = init_plan.wake_command(facts, answers)
    except ValueError:  # a start time or a delay that does not parse: bosun sets no wake of its own then
        return OTHER
    if command:
        return OTHER
    return LEFTOVER if settings.get(WAKE_OURS, "1") == "0" else OURS


def result(changed: Mapping[str, tuple[str, str]], notes: Sequence[str]) -> dict[str, Any]:
    """`bosun settings set --json`'s object."""
    return {
        "contract": CONTRACT,
        "changed": {key: [before, after] for key, (before, after) in changed.items()},
        "notes": list(notes),
    }


def failure(error: str) -> dict[str, Any]:
    """`bosun settings set --json`'s object when it refused or failed."""
    return {"contract": CONTRACT, "error": error}


def lines(changed: Mapping[str, tuple[str, str]], notes: Sequence[str]) -> list[str]:
    """What `bosun settings set` says: a line a change, then the notes."""
    said = [f"{key}: {before} → {after}" for key, (before, after) in changed.items()]
    return (said or ["nothing changed: bosun.env says so already"]) + list(notes)


WAKE_WORDS = {
    OURS: "bosun's (bosun moves it with the days and the start time)",
    NONE: "none",
    OTHER: "another app's (pmset keeps one: bosun leaves it, unless set with --replace-wake)",
    LEFTOVER: (
        f"bosun's from before, though BOSUN_WAKE=0 says it sets none ({init_plan.CANCEL_WAKE} ends it; "
        f"{init_plan.CANCEL_TAKES_ALL})"
    ),
}


def describe(shown: Mapping[str, Any], owner: str | None = None) -> list[str]:
    """`bosun settings get` for a person: a line a setting, with what it means, and whose the wake is
    (<owner>, wake_owner's answer, which the view shows as the app reads it)."""
    folder, error = f"{shown['config_dir']}/{init_plan.ENV_FILE}", shown["error"]
    if not shown["readable"]:
        head = [f"{folder} cannot be read ({error}): fix it, or run bosun init"]
    elif error:
        head = [
            f"{folder}: bosun cannot load it ({error}), so it runs on none of it: fix it, or run bosun init"
        ]
    else:
        head = [folder]
    for key, setting in shown["settings"].items():
        if setting["value"] is None:
            head.append(f"{key}: cannot be read  {setting['meaning']}")
            continue
        where = "set" if setting["set"] else "default"
        valid = "" if setting["valid"] else ", not one bosun can use: fix it with bosun init"
        head.append(f"{key}={setting['value']}  ({where}{valid})  {setting['meaning']}")
    wake = owner or shown["wake"]
    head.append(f"the Mac's repeating wake: {WAKE_WORDS.get(wake, wake)}")
    return head


# ---- the command line --------------------------------------------------------------------------------------


def _read_env(config_dir: Path) -> tuple[str | None, str | None]:
    """bosun.env's text ("" when there is none), or None and why it cannot be read."""
    try:
        return (config_dir / init_plan.ENV_FILE).read_text(), None
    except FileNotFoundError:
        return "", None
    except (OSError, UnicodeDecodeError) as exc:
        return None, getattr(exc, "strerror", None) or str(exc)


def _schedule(given: str | None) -> str:
    return given if given is not None else init_detect.output_of("pmset", "-g", "sched") or ""


def _parser() -> _Parser:
    parser = _Parser(prog="settings.py", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("get", "plan", "wake-owner", "write"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--config-dir", type=Path, required=True)
        sub.add_argument("--example", type=Path, default=EXAMPLE)
        if name in ("get", "wake-owner"):
            sub.add_argument("--schedule")
        if name == "wake-owner":
            sub.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
        if name in ("plan", "write"):
            sub.add_argument("assignments", nargs="*")
    commands.choices["get"].add_argument("--env-error", default="")
    commands.choices["get"].add_argument("--text", action="store_true")
    commands.choices["write"].add_argument("--stamp", required=True)
    commands.choices["write"].add_argument("--wake", action="store_true")
    say = commands.add_parser("say", allow_abbrev=False)
    say.add_argument("--json", action="store_true")
    say.add_argument("--wake-left", action="store_true")
    say.add_argument("--wake-leftover", action="store_true")
    commands.add_parser("error", allow_abbrev=False).add_argument("message")
    commands.add_parser("put-back", allow_abbrev=False).add_argument("--config-dir", type=Path, required=True)
    return parser


def _get(args: argparse.Namespace, known: Mapping[str, str], text: str | None, error: str | None) -> int:
    error = error if text is None else args.env_error or None  # why Python, else why the shell, could not
    owner = wake_owner(_schedule(args.schedule), assigned(text) if text is not None else {}, known)
    shown = view(text, error, known, OTHER if owner == LEFTOVER else owner, str(args.config_dir))
    print("\n".join(describe(shown, owner)) if args.text else json.dumps(shown))
    return 0


def _on_file(args: argparse.Namespace) -> int:
    """get, plan, wake-owner and write: the commands that read bosun.env."""
    config_dir: Path = args.config_dir
    known = defaults(args.example.read_text())
    text, error = _read_env(config_dir)
    if args.command == "get":
        return _get(args, known, text, error)
    if text is None:
        print(f"settings.py: bosun.env cannot be read: {error}", file=sys.stderr)
        return 1
    if args.command == "wake-owner":
        in_force = dict(setting.partition("=")[::2] for setting in args.set)
        print(wake_owner(_schedule(args.schedule), {**assigned(text), **in_force}, known))
        return 0
    done = change(text, args.assignments, known, wake=args.command == "write" and args.wake)
    if args.command == "plan":
        print("".join(f"{effect}\n" for effect in sorted(done.effects)), end="")
        return 0
    path, backup, created = config_dir / init_plan.ENV_FILE, None, False
    if done.changed:
        created = not path.exists()
        with contextlib.redirect_stdout(sys.stderr):  # its "backed up" line: stdout is the JSON
            backup = init.write_with_backup(path, done.text, args.stamp, found=not created)
    # what the put-back acts on: the backup this write made, or that the bosun.env is one it made
    written = {**result(done.changed, []), "backup": str(backup) if backup else None, "created": created}
    print(json.dumps(written))
    return 0


class PutBackError(Exception):
    """bosun.env could not be put back as the write found it."""


def put_back(path: Path, written: Mapping[str, Any]) -> None:
    """<path> as the write that printed <written> found it: the backup it made copied back over it (its mode
    with it), or, when the write made the file, the file removed. With neither, nothing is touched."""
    backup = written.get("backup")
    if backup:
        try:
            text = Path(backup).read_text()
        except OSError as exc:
            raise PutBackError(f"its backup {backup} cannot be read: {exc.strerror or exc}") from exc
        init.write_file(path, text)
    elif written.get("created") is True:
        path.unlink(missing_ok=True)
    else:
        raise PutBackError("the write reported no backup, so bosun.env was left as it is")


def _put_back(args: argparse.Namespace, stdin: TextIO) -> int:
    try:
        put_back(args.config_dir / init_plan.ENV_FILE, json.load(stdin))
    except (PutBackError, OSError, ValueError) as exc:
        print(f"could not put bosun.env back: {exc}", file=sys.stderr)
        return 1
    return 0


def _say(args: argparse.Namespace, stdin: TextIO) -> int:
    written = json.load(stdin)
    changed = {key: (pair[0], pair[1]) for key, pair in written["changed"].items()}
    notes = [*written.get("notes", []), *([WAKE_LEFT] if args.wake_left else [])]
    notes += [WAKE_LEFTOVER] if args.wake_leftover else []
    if args.json:
        print(json.dumps(result(changed, notes)))
    else:
        print("\n".join(lines(changed, notes)))
    return 0


def main(argv: Sequence[str], stdin: TextIO | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "say":
            return _say(args, stdin or sys.stdin)
        if args.command == "put-back":
            return _put_back(args, stdin or sys.stdin)
        if args.command == "error":
            print(json.dumps(failure(args.message)))
            return 0
        return _on_file(args)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
