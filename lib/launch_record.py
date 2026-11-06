"""What each window was launched with (docs/design/update.md, "Bringing the agents along").

`bosun launch` records, in $BOSUN_STATE_DIR/launches/<session>/<window>.json (a tmux session of its
own, such as wake-test's, keeps records of its own), the commit of the bosun it ran from, a
fingerprint of what it handed the agent, and what starts the window the same way again: the agent,
the model and effort it ran with, its directory, and the --model or --effort launch was given (""
for none; a window `send --new` opened keeps them). An update restarts a window on the agent it was
launched on, with that model and effort when the configured agent is another one.

`bosun launch --fingerprint <window>` prints the fingerprint it would hand it now. Where the two
differ, a session runs with launch inputs an update changed (its settings, its hooks, the launch
command, the plugin), and only a restart gives it the new ones. Everything else it uses (a bosun
command, a hook's code) runs from the install link, which an update switches; so the two are compared
after the switch.

The fingerprint has two parts:
- `args`: the agent's arguments, as `build_agent_args` makes them for the conversation name
  `<conversation>` (a new day's or a fresh conversation's name is no launch input), with the text
  after `--append-system-prompt` left out: the orchestrator's policy, which a running session is
  asked to re-read instead;
- `files`: the content of every file under an argument that is a path inside the install (the
  plugin folder, a settings file), read through that path, as the agent reads it. So the plugin's
  `chores.md` is a launch input, and `policy.md`, whose text goes in as an argument, is not.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LAUNCHES_DIR = "launches"
POLICY_FLAG = "--append-system-prompt"
POLICY = "<policy>"  # what stands for the policy's text in the `args` part


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def launch_args(args: Sequence[str]) -> list[str]:
    """<args> with the value after each --append-system-prompt replaced by POLICY."""
    return [POLICY if i > 0 and args[i - 1] == POLICY_FLAG else arg for i, arg in enumerate(args)]


def _in_install(arg: str, install: Path) -> bool:
    path = Path(arg)
    return path.is_absolute() and path.is_relative_to(install)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files_under(path: Path) -> list[list[str]]:
    """[relative path, content digest] for <path> itself (a file) or each file below it (a folder)."""
    if path.is_file():
        return [["", _file_digest(path)]]
    found = sorted(p for p in path.rglob("*") if p.is_file())
    return [[p.relative_to(path).as_posix(), _file_digest(p)] for p in found]


def fingerprint(args: Sequence[str], install: Path) -> dict[str, str]:
    """{"args": <digest>, "files": <digest>} of what an agent launched with <args> starts from, for
    the install at <install> (the path the arguments name). A path that is not there counts as one
    with no files."""
    files = [[arg, *entry] for arg in args if _in_install(arg, install) for entry in _files_under(Path(arg))]
    return {"args": _digest(launch_args(args)), "files": _digest(files)}


@dataclass(frozen=True)
class Launch:
    """What a window was launched with, and what starts it the same way again."""

    commit: str  # bosun's, or "unknown"
    harness: str
    fingerprint: dict[str, str]
    dir: str
    model: str  # what it ran with; "-" for the agent's default
    effort: str
    model_override: str = ""  # the --model launch was given; "" for none
    effort_override: str = ""


def _file_name(name: str) -> str:
    return name.replace("/", "_")


def record_path(state_dir: Path, session: str, window: str) -> Path:
    return state_dir / LAUNCHES_DIR / _file_name(session) / f"{_file_name(window)}.json"


def record(state_dir: Path, session: str, window: str, launch: Launch) -> None:
    """Record <window>'s launch, replacing its last one: written beside, then renamed over it."""
    path = record_path(state_dir, session, window)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"session": session, "window": window, **dataclasses.asdict(launch)}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def recorded(state_dir: Path, session: str, window: str) -> dict[str, Any] | None:
    """<window>'s last launch as recorded; None if it has none, or none that can be read."""
    try:
        data = json.loads(record_path(state_dir, session, window).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="launch_record.py", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    print_it = commands.add_parser("fingerprint", help="print the fingerprint of <args> as JSON")
    write_it = commands.add_parser("record", help="record a window's launch with <args>", allow_abbrev=False)
    show_it = commands.add_parser("show", help="print a window's record as JSON; exit 1 if it has none")
    for sub in (write_it, show_it):
        sub.add_argument("--state-dir", type=Path, required=True)
        sub.add_argument("--session", required=True)
        sub.add_argument("--window", required=True)
    write_it.add_argument("--commit", required=True)
    write_it.add_argument("--harness", required=True)
    write_it.add_argument("--dir", required=True)
    write_it.add_argument("--model", required=True)
    write_it.add_argument("--effort", required=True)
    write_it.add_argument("--model-override", default="")
    write_it.add_argument("--effort-override", default="")
    for sub in (print_it, write_it):
        sub.add_argument("--install", type=Path, required=True)
        sub.add_argument("args", nargs="*", help="the agent's arguments, after --")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "show":
        shown = recorded(args.state_dir, args.session, args.window)
        if shown is None:
            return 1
        print(json.dumps(shown, sort_keys=True))
        return 0
    fp = fingerprint(args.args, args.install)
    if args.command == "fingerprint":
        print(json.dumps(fp, sort_keys=True))
        return 0
    ran_with = {"model": args.model, "effort": args.effort}
    overrides = {"model_override": args.model_override, "effort_override": args.effort_override}
    launch = Launch(args.commit, args.harness, fp, args.dir, **ran_with, **overrides)
    record(args.state_dir, args.session, args.window, launch)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
