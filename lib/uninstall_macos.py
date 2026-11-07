"""What `bosun uninstall` has to undo that only macOS knows about: launchd's jobs and pmset's repeating wake.
install.sh does the other half. Another OS would be another module with these functions. Design:
docs/design/uninstall.md.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from xml.parsers.expat import ExpatError

import init_detect
from uninstall_plan import Job


COMMAND_TIMEOUT_SECONDS = 60
RENDERED_SHELL = ["/bin/bash", "-lc"]  # what every job of templates/launchd*.plist runs its command with
UNLOAD_TRIES = 10
UNLOAD_WAIT_SECONDS = 0.5


def domain() -> str:
    return f"gui/{os.getuid()}"


def agents_folder() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def is_loaded(label: str) -> bool:
    return init_detect.output_of("launchctl", "print", f"{domain()}/{label}") is not None


def plist_job(path: Path, labels: Sequence[str], roots: Sequence[str]) -> str:
    """The label of the job in <path> if it is bosun's: one of <labels>, or one install.sh rendered to run
    the bosun of one of <roots> (its label may have changed in bosun.env since): the checkout, or with
    versions side by side the install link, a version's folder or the checkout from before versions, which
    an older plist may name. Otherwise nothing."""
    try:
        job = plistlib.loads(path.read_bytes())
        label = str(job.get("Label", ""))
        arguments = [str(argument) for argument in job.get("ProgramArguments", [])]
    except (
        OSError,
        ValueError,
        AttributeError,
        TypeError,
        ExpatError,
    ):  # someone else's, broken: not bosun's
        return ""
    # As templates/launchd*.plist render it, to the letter: your own job that runs bosun is not bosun's.
    runs_this_bosun = arguments[:2] == RENDERED_SHELL and arguments[2:] == [arguments[-1]]
    runs_this_bosun = runs_this_bosun and any(
        arguments[-1].startswith(f'exec "{root}/bin/bosun" ') for root in roots
    )
    return label if label in labels or runs_this_bosun else ""


def jobs(labels: Sequence[str], roots: Sequence[str]) -> tuple[Job, ...]:
    """Each job of bosun's with a plist or loaded in launchd, which can be one without the other."""
    plists = {}
    for path in sorted(agents_folder().glob("*.plist")):
        label = plist_job(path, labels, roots)
        if label:
            plists[label] = str(path)
    known = dict.fromkeys([*labels, *plists])
    found = ((label, plists.get(label, ""), is_loaded(label)) for label in known)
    return tuple(Job(label, plist, loaded) for label, plist, loaded in found if plist or loaded)


def remove_job(job: Job) -> None:
    """Unload it first, so that it cannot start again, then delete its plist. launchd takes a moment to let
    go of a job that is running."""
    command = ["launchctl", "bootout", f"{domain()}/{job.label}"]
    subprocess.run(command, check=False, capture_output=True, timeout=COMMAND_TIMEOUT_SECONDS)
    if job.plist:
        Path(job.plist).unlink(missing_ok=True)
    for _ in range(UNLOAD_TRIES):
        if not is_loaded(job.label):
            return
        time.sleep(UNLOAD_WAIT_SECONDS)


def repeating_events() -> tuple[str, ...]:
    return init_detect.repeating_events(init_detect.output_of("pmset", "-g", "sched") or "")


def cancel_wake(*, can_ask: bool) -> bool:
    """`pmset repeat cancel` cancels every repeating event, which is why the plan only asks for this when
    bosun's wake is the only one. Without a terminal, sudo must not wait for a password nobody can type."""
    sudo = ["sudo"] if can_ask else ["sudo", "-n"]
    return subprocess.run([*sudo, "pmset", "repeat", "cancel"], check=False).returncode == 0
