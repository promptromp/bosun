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
import time
from collections.abc import Callable, Collection, Mapping
from pathlib import Path

import agent_changes
import claude_state
import orphans
import uninstall_detect
import uninstall_macos
import uninstall_plan as up
from agent_state import AGENTS
from uninstall_plan import Choices, Facts, Plan, Step


ROOT = Path(__file__).resolve().parents[1]
EXIT_LEFT = 1
EXIT_REFUSED = 2
COMMAND_TIMEOUT_SECONDS = 120
STOPPED_SECONDS = 15  # the most it waits for what the stopped windows left running
STOPPED_POLL_SECONDS = 0.05


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
    other = uninstall_detect.other_install(uninstall_detect.install_roots(ROOT))
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


def session_id(pid: int) -> int | None:
    try:
        return os.getsid(pid)
    except OSError:  # gone, or not ours to ask
        return None


def processes() -> tuple[list[orphans.Proc], list[tuple[int, int, int | None]]]:
    """Every process now, and as up.still_running reads them: pid, parent, session."""
    procs = orphans.parse_ps(orphans.read_ps("-A"))
    return procs, [(p.pid, p.ppid, session_id(p.pid)) for p in procs]


def left_running(sessions: Collection[int], watched: Mapping[int, str]) -> list[str]:
    """What the stopped windows left running (up.still_running), waited for until it ends, for
    STOPPED_SECONDS at the most: an agent ending as its window stops may start an async hook (Claude
    Code's SessionEnd) that writes bosun's state a moment later, after its files would be deleted. A hook
    is found through the agent that starts it, which Claude Code does a second or so after its window
    stopped, while it still runs. What is still there then, said with its window (up.still_running_note)."""
    deadline = time.monotonic() + STOPPED_SECONDS
    while True:
        procs, table = processes()
        watched = up.still_running(table, sessions, watched)
        if not watched or time.monotonic() >= deadline:
            return [
                up.still_running_note(watched[p.pid], p.pid, p.command) for p in procs if p.pid in watched
            ]
        time.sleep(STOPPED_POLL_SECONDS)


def stop_windows(env: Mapping[str, str]) -> list[str]:
    """`bosun stop`, then a wait for what its windows ran (up.ran_in_windows, before the stop) and what
    that started apart from them to end, so nothing of theirs writes after bosun's files are gone. The
    windows are gone then; what they started and still runs is said, not stopped."""
    panes = uninstall_detect.panes(env)
    sessions = {sid: window for pid, window in panes.items() if (sid := session_id(pid)) is not None}
    watched = up.ran_in_windows(processes()[1], sessions, panes)
    command = [str(ROOT / "bin" / "bosun"), "stop"]
    done = subprocess.run(command, check=False, capture_output=True, timeout=COMMAND_TIMEOUT_SECONDS)
    if done.returncode != 0:
        raise StepFailed("bosun stop failed")
    return left_running(sessions, watched)


class Carrying:
    """One run of the steps: what they need, what failed, and what a step leaves to say after it."""

    def __init__(self, facts: Facts, *, can_ask: bool, env: Mapping[str, str] = os.environ) -> None:
        self.facts = facts
        self.can_ask = can_ask
        self.env = env
        self.state_dir = Path(facts.state_dir)
        self.jobs = {job.label: job for job in facts.jobs}
        self.notes: list[str] = []
        self.actions: dict[str, Callable[[Step], None]] = {
            up.WAKE: self.cancel_wake,
            up.JOB: self.remove_job,
            up.STOP: self.stop,
            up.TRUST: self.revoke_trust,
            up.NOTE: self.remove_note,
            up.LINK: lambda step: remove_path(Path(step.target)),
            up.FILE: lambda step: remove_path(Path(step.target)),
            up.CHECKOUT: lambda step: shutil.rmtree(step.target),
        }

    def stop(self, _step: Step) -> None:
        self.notes = stop_windows(self.env)

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
                for note in self.notes:
                    print(note)
            self.notes = []


def remove_empty_folders(facts: Facts) -> None:
    """The folders themselves, if nothing stayed in them. Their parents may hold other tools' files."""
    for folder in (facts.log_dir, facts.state_dir, facts.config_dir):
        if up.safe_folder(folder, facts):
            try:
                os.rmdir(folder)
            except OSError:
                continue


def remove_install(facts: Facts, plan: Plan, *, can_ask: bool) -> tuple[Step, ...]:
    """Last of all, once nothing more can need it: the checkout, or the versions folder with the checkouts
    from before versions in it, then the link that led into it, only once every folder has gone, as the
    line to run again goes through it; what is left of it, if anything."""
    folders = tuple(step for step in plan.install if step.kind != up.LINK)
    links = tuple(step for step in plan.install if step.kind == up.LINK)
    Carrying(facts, can_ask=can_ask).run(folders)
    if not any(os.path.lexists(step.target) for step in folders):
        Carrying(facts, can_ask=can_ask).run(links)
    return tuple(step for step in plan.install if os.path.lexists(step.target))


def inside_install(facts: Facts) -> bool:
    """Whether your shell is in a folder of the install that is gone now."""
    places = [ROOT, Path(facts.checkout.root), *([Path(facts.install_link)] if facts.install_link else [])]
    here = Path(os.environ.get("PWD", "/"))
    return any(here.is_relative_to(place) for place in places) and not ROOT.exists()


def carry_out(env: Mapping[str, str], choices: Choices, *, can_ask: bool) -> int:
    """In goes, looking again before each: stopping the windows writes a file of its own, and only a look at
    the end says what is really gone."""

    def plan_now() -> tuple[Facts, Plan]:
        facts = uninstall_detect.detect(env, ROOT)
        return facts, up.build_plan(facts, choices)

    facts, plan = plan_now()
    Carrying(facts, can_ask=can_ask, env=env).run(tuple(step for step in plan.steps if step.kind != up.FILE))
    facts, plan = plan_now()
    Carrying(facts, can_ask=can_ask).run(tuple(step for step in plan.steps if step.kind == up.FILE))
    remove_empty_folders(facts)
    facts, plan = plan_now()
    left = plan.steps or remove_install(facts, plan, can_ask=can_ask)
    print()
    print(up.render_done(plan, left, facts, inside_checkout=inside_install(facts)), end="")
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
