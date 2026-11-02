#!/usr/bin/env python3
"""`bosun uninstall`: take bosun off this machine, having said what goes and what stays.

lib/uninstall_detect.py finds what is here and lib/uninstall_plan.py decides what to do about it. This file
asks and carries it out: nothing changes until every question is answered, a failed step does not stop the
others, and at the end it looks again and reports what it finds, not what it did. Design:
docs/design/uninstall.md.

Exit status: 0 done, or nothing was changed; 1 something could not be removed; 2 bad usage, or refused.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import agent_changes
import claude_state
import uninstall_detect
import uninstall_macos
import uninstall_plan as up
from agent_state import AGENTS
from uninstall_plan import Choices, Facts, Plan, Step


ROOT = Path(__file__).resolve().parents[1]
EXIT_LEFT = 1
EXIT_REFUSED = 2
COMMAND_TIMEOUT_SECONDS = 120


class StepFailed(Exception):
    pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bosun uninstall", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show what it would do, and do nothing")
    parser.add_argument("--yes", action="store_true", help="ask nothing; your records stay unless told")
    parser.add_argument("--delete-ledger", action="store_true", help="delete the ledger without asking")
    parser.add_argument(
        "--delete-knowledge", action="store_true", help="delete the knowledge graph without asking"
    )
    parser.add_argument("--purge", action="store_true", help="delete your settings and records too")
    return parser.parse_args(argv)


def inside_bosun(env: Mapping[str, str]) -> bool:
    """Whether this runs in a pane of bosun's tmux server, which it is about to stop. That also keeps an
    agent in one of bosun's windows from running it. With no socket of its own, bosun shares your default
    server, so any tmux there counts."""
    server = env.get("TMUX", "").split(",")[0]
    if not server:
        return False
    return Path(server).name == (env.get("BOSUN_TMUX_SOCKET") or "default")


def refusal(args: argparse.Namespace, env: Mapping[str, str]) -> str:
    if inside_bosun(env) and not args.dry_run:
        return "this runs inside bosun's tmux session, which it would stop. Run it from another terminal."
    other = uninstall_detect.other_install(ROOT)
    if other:
        return f"~/.local/bin/bosun runs another checkout. Uninstall with that one: {other} uninstall"
    if not (args.yes or args.dry_run or sys.stdin.isatty()):
        return "no terminal to ask on. --yes uninstalls without asking, and --dry-run shows what it would do."
    return ""


def confirm(question: str, *, default: bool) -> bool:
    """Enter takes <default>. Ctrl-C and the end of the input go up to main, where nothing has changed."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{question} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def from_flags(args: argparse.Namespace) -> Choices:
    return Choices(
        keep_ledger=not (args.delete_ledger or args.purge),
        keep_knowledge=not (args.delete_knowledge or args.purge),
        purge=args.purge,
    )


def ask(args: argparse.Namespace, facts: Facts) -> Choices | None:
    """Your answers, or None if you are not sure. All of it comes before any change."""
    choices = from_flags(args)
    if args.yes:
        return choices
    if not confirm("Are you sure you want to uninstall bosun?", default=False):
        return None
    keep = {question.key: confirm(question.text, default=True) for question in up.questions(facts, choices)}
    return Choices(
        keep_ledger=keep.get(up.LEDGER, choices.keep_ledger),
        keep_knowledge=keep.get(up.KNOWLEDGE, choices.keep_knowledge),
        purge=choices.purge,
    )


def remove_path(path: Path) -> None:
    """Whatever it is. A link goes, and never what it points at."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def stop_windows(_step: Step) -> None:
    command = [str(ROOT / "bin" / "bosun"), "stop"]
    done = subprocess.run(command, check=False, capture_output=True, timeout=COMMAND_TIMEOUT_SECONDS)
    if done.returncode != 0:
        raise StepFailed("bosun stop failed")


class Carrying:
    """One run of the steps: what they need, and what failed."""

    def __init__(self, facts: Facts, *, can_ask: bool) -> None:
        self.facts = facts
        self.can_ask = can_ask
        self.state_dir = Path(facts.state_dir)
        self.jobs = {job.label: job for job in facts.jobs}
        self.actions: dict[str, Callable[[Step], None]] = {
            up.WAKE: self.cancel_wake,
            up.JOB: self.remove_job,
            up.STOP: stop_windows,
            up.TRUST: self.revoke_trust,
            up.NOTE: self.remove_note,
            up.LINK: lambda step: remove_path(Path(step.target)),
            up.FILE: lambda step: remove_path(Path(step.target)),
            up.CHECKOUT: lambda step: shutil.rmtree(step.target),
        }

    def cancel_wake(self, _step: Step) -> None:
        sys.stdout.flush()  # sudo may ask for your password next
        if not uninstall_macos.cancel_wake(can_ask=self.can_ask):
            raise StepFailed(f"{up.CANCEL_WAKE} failed")

    def remove_job(self, step: Step) -> None:
        uninstall_macos.remove_job(self.jobs[step.target])

    def revoke_trust(self, step: Step) -> None:
        """Forgotten only once the agent says it is gone: the record is what a second run goes by."""
        agent = AGENTS[step.harness]
        agent.set_trust(step.target, False)
        if agent.trust_recorded(step.target) is not False:
            raise StepFailed(f"{step.harness} still trusts it")
        agent_changes.forget_trust(self.state_dir, step.harness, step.target)

    def remove_note(self, step: Step) -> None:
        claude_state.remove_memory_note(Path(step.target))
        agent_changes.forget_note(self.state_dir, step.target)

    def run(self, steps: tuple[Step, ...]) -> None:
        for step in steps:
            try:
                self.actions[step.kind](step)
            except Exception as error:  # noqa: BLE001 (one step's trouble must not stop the others)
                print(f"could not remove {up.describe(step, self.facts.home)}: {error}")
            else:
                print(f"removed {up.describe(step, self.facts.home)}")


def remove_empty_folders(facts: Facts) -> None:
    """The folders themselves, if nothing stayed in them. Their parents may hold other tools' files."""
    for folder in (facts.log_dir, facts.state_dir, facts.config_dir):
        if up.safe_folder(folder, facts):
            try:
                os.rmdir(folder)
            except OSError:
                continue


def remove_checkout(facts: Facts, plan: Plan, *, can_ask: bool) -> tuple[Step, ...]:
    """Last of all, once nothing more can need it; what is left of it, if anything."""
    if plan.checkout is None:
        return ()
    Carrying(facts, can_ask=can_ask).run((plan.checkout,))
    return (plan.checkout,) if Path(plan.checkout.target).exists() else ()


def carry_out(env: Mapping[str, str], choices: Choices, *, can_ask: bool) -> int:
    """In goes, looking again before each: stopping the windows writes a file of its own, and only a look at
    the end says what is really gone."""

    def plan_now() -> tuple[Facts, Plan]:
        facts = uninstall_detect.detect(env, ROOT)
        return facts, up.build_plan(facts, choices)

    facts, plan = plan_now()
    Carrying(facts, can_ask=can_ask).run(tuple(step for step in plan.steps if step.kind != up.FILE))
    facts, plan = plan_now()
    Carrying(facts, can_ask=can_ask).run(tuple(step for step in plan.steps if step.kind == up.FILE))
    remove_empty_folders(facts)
    facts, plan = plan_now()
    left = plan.steps or remove_checkout(facts, plan, can_ask=can_ask)
    inside = Path(os.environ.get("PWD", "/")).is_relative_to(ROOT) and not ROOT.exists()
    print()
    print(up.render_done(plan, left, facts, inside_checkout=inside), end="")
    return EXIT_LEFT if left else 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    env = os.environ
    refused = refusal(args, env)
    if refused:
        print(f"bosun uninstall: {refused}", file=sys.stderr)
        return EXIT_REFUSED
    facts = uninstall_detect.detect(env, ROOT)
    plan = up.build_plan(facts, from_flags(args))
    print(up.render_plan(plan, facts, asking=not args.yes))
    if args.dry_run:
        for question in up.questions(facts, from_flags(args)):
            print(f"It would ask: {question.text}")
        print("Nothing was changed: this was a dry run.")
        return 0
    if plan.empty:
        return 0
    try:
        choices = ask(args, facts)
    except (EOFError, KeyboardInterrupt):
        choices = None
    if choices is None:
        print("\nNothing was changed.")
        return 0
    print()
    try:
        return carry_out(env, choices, can_ask=sys.stdin.isatty())
    except KeyboardInterrupt:
        print("\nStopped part way. Running bosun uninstall again finishes the job.")
        return EXIT_LEFT


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
