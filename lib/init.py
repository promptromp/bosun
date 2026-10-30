#!/usr/bin/env python3
"""`bosun init`: set bosun up from what this Mac shows, or from an answers file.

lib/init_detect.py finds the facts, lib/init_plan.py recommends answers and plans the changes, and
this file prints the plan and carries it out: it writes the config (with backups), trusts the
folders, then runs install.sh, which already knows launchd, pmset and `bosun doctor`. Design:
docs/design/init.md.

Exit status: 0 done (or only printed), 1 blocked or `bosun doctor` found a problem, 2 bad usage or
answers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import claude_state
import init_detect
import init_plan
from init_plan import Answers, AnswersError, DirState, Facts, Plan


ROOT = Path(__file__).resolve().parents[1]
EXIT_BLOCKED = 1
EXIT_USAGE = 2


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bosun init", description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--recommended", action="store_true", help="carry out the recommended plan")
    source.add_argument(
        "--answers", metavar="FILE", help="carry out the plan for these answers (- for stdin)"
    )
    parser.add_argument(
        "--force", action="store_true", help="replace a sessions.yml that lists other windows"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    show = parser.add_mutually_exclusive_group()
    show.add_argument("--detect", action="store_true", help="print what was found, as JSON")
    show.add_argument("--print-answers", action="store_true", help="print the answers, as JSON")
    return parser.parse_args(argv)


def printable(facts: Facts) -> dict[str, object]:
    """The facts, with which config files and settings exist but not what they say: people paste this
    into issues, and bosun.env can hold a webhook."""
    names_only = replace(
        facts, files=dict.fromkeys(sorted(facts.files), ""), env=dict.fromkeys(sorted(facts.env), "")
    )
    return asdict(names_only)


def load_answers(source: str | None, base: Answers) -> Answers:
    if source is None:
        return base
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
        return init_plan.answers_from_json(json.loads(text), base)
    except OSError as error:
        raise AnswersError(f"cannot read {source}: {error.strerror}") from error
    except json.JSONDecodeError as error:
        raise AnswersError(f"{source} is not JSON: {error}") from error


def dir_state(answers: Answers) -> DirState:
    folders = list(dict.fromkeys([*(r.dir for r in answers.repos), answers.repos_dir]))
    missing = [folder for folder in folders if not Path(folder).expanduser().is_dir()]
    untrusted = [
        str(Path(folder).expanduser())
        for folder in folders
        if folder not in missing and not claude_state.trusted(str(Path(folder).expanduser()))
    ]
    return DirState(tuple(missing), tuple(untrusted))


def write_file(path: Path, text: str) -> None:
    """Atomically, so a reader never sees half a config: bosun.env is read by every command."""
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", delete=False) as temp:
        temp.write(text)
    if path.exists():
        shutil.copymode(path, temp.name)
    else:
        os.chmod(temp.name, 0o644)
    os.replace(temp.name, path)


def apply_files(plan: Plan, config_dir: Path, stamp: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    for change in plan.files:
        if change.action not in init_plan.CHANGING:
            continue
        path = config_dir / change.name
        if path.exists():
            backup = path.with_name(f"{change.name}.pre-init-{stamp}")
            shutil.copy2(path, backup)
            print(f"backed up {change.name} as {backup.name}")
        write_file(path, change.text)
        print(f"wrote {path}")


def can_ask_for_sudo() -> bool:
    if sys.stdin.isatty():
        return True
    return init_detect.output_of("sudo", "-n", "true") is not None


def run_steps(plan: Plan) -> int:
    """Trust the folders, then let install.sh do the rest. Its status is `bosun doctor`'s."""
    sys.stdout.flush()  # what was printed so far comes before what these print
    if plan.trust:
        subprocess.run([str(ROOT / "bin" / "bosun"), "trust", "--any", *plan.trust], check=False)
    flags = []
    if plan.wake_command:
        if can_ask_for_sudo():
            flags.append("--with-wake")
        else:
            print(
                f"No terminal to ask for your password, so run this once yourself: {plan.wake_command}",
                flush=True,
            )
    return subprocess.run([str(ROOT / "install.sh"), *flags], check=False).returncode


def apply_hint() -> str:
    command = "bosun init" if shutil.which("bosun") else f"{ROOT / 'bin' / 'bosun'} init"
    return (
        f"\nTo carry it out: {command} --recommended"
        f"\nTo change something: {command} --print-answers > answers.json, edit it, then "
        f"{command} --answers answers.json"
    )


def carry_out(plan: Plan, config_dir: Path) -> int:
    if plan.blockers:
        print("\nNothing was changed: fix what is under Blocked first.", file=sys.stderr)
        return EXIT_BLOCKED
    apply_files(plan, config_dir, datetime.now().strftime("%Y%m%d-%H%M%S"))
    status = run_steps(plan)
    if status != 0:
        print("\n`bosun doctor` found problems, listed above. Fix them and run it again.", file=sys.stderr)
        return EXIT_BLOCKED
    print("\nbosun is set up. `bosun start` brings the windows up now; `bosun console` shows them.")
    return 0


def show_plan(facts: Facts, answers: Answers, args: argparse.Namespace, config_dir: Path) -> int:
    plan = init_plan.build_plan(facts, answers, dir_state(answers), args.force)
    print(init_plan.render_plan(plan, answers, facts, str(config_dir)), end="")
    if args.dry_run:
        return EXIT_BLOCKED if plan.blockers else 0
    if not args.recommended and args.answers is None:
        print(apply_hint())
        return 0
    return carry_out(plan, config_dir)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    config_dir = Path(os.environ.get("BOSUN_CONFIG_DIR") or Path.home() / ".config" / "bosun")
    facts = init_detect.detect(config_dir)
    if args.detect:
        print(json.dumps(printable(facts), indent=2))
        return 0
    try:
        answers = load_answers(args.answers, init_plan.recommend(facts))
    except AnswersError as error:
        print(f"bosun init: {error}", file=sys.stderr)
        return EXIT_USAGE
    problems = init_plan.validate(answers)
    if problems:
        print("\n".join(f"bosun init: {problem}" for problem in problems), file=sys.stderr)
        return EXIT_USAGE
    if args.print_answers:
        print(json.dumps(init_plan.answers_to_json(answers), indent=2))
        return 0
    return show_plan(facts, answers, args, config_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
