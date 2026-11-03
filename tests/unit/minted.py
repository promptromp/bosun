"""The ids ledger commands mint in a test. A task or outreach id ends in six characters hashed from
this Mac's replica and the journal position (lib/ledger.py, mint_id), not in a count, so a test
names the item it made by when it made it: made(PR, 2) is the second outreach it made for PR (or
for a day, outreach without a parent), made(TASKS) its first task.

run() is a ledger command in this process (as the tests' own run() is) that remembers what it
made; each test's setUp calls forget()."""

from __future__ import annotations

from typing import Any

import ledger


TASKS = "task"  # the tasks a test made, counted together
MAKERS = ("spawn", "reach-out", "ask")
MADE: dict[str, list[str]] = {}


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    output = args.func(args)
    made_id, _, rest = str(output).partition("\n")
    if argv[0] in MAKERS and rest.startswith("created: "):
        MADE.setdefault(scope_of(made_id), []).append(made_id)
    return output


def scope_of(item_id: str) -> str:
    """An outreach id's parent id (or day), or TASKS for a task."""
    found = ledger.OUTREACH_ID.match(item_id)
    return found["scope"] if found else TASKS


def made(scope: str, n: int = 1) -> str:
    """The <n>th item this test made in <scope>."""
    return MADE[scope][n - 1]


def forget() -> None:
    MADE.clear()
