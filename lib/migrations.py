"""The one registry of migrations: what moves each store from an older format to the code's, and
the config's keys from their old names (design: docs/design/update.md, "Migrations").

A store's format is the version its files record (`ledger.store_format`,
`knowledge_store.store_format`), pinned to what is on disk by tests/unit/test_store_formats.py.
REGISTRY lists every migration: its store, the format it goes from and to, the function that does
it and the line it adds to the summary. `bosun update` runs them on a rehearsal copy first, then on
the real files while it holds the stores' locks. `run` takes each store from its format to the
code's, one step at a time, recording the store's version after each, so that a run cut short goes
on from the last step that finished. Each migration can run twice with the same result, is tested
from a fixture in the old format (tests/fixtures/migrations/), and leaves the store passing its own
check (`validate_unlocked`). A rules change that alters what earlier changes mean (the decisions
of 2026-11-04 in docs/design/sync.md) is a migration of its store too.

A journal is never rewritten: a migration never changes what was recorded. The store's reader
reads an older envelope as the current one (upcasting), and the migration rebuilds the files
derived from the journal (`ledger.json`, the knowledge files), as a rebuild is in the code's format.

The config: a new key needs nothing, as the defaults in lib/common.sh apply; a key renamed or
removed (CONFIG_RENAMES) is edited in bosun.env, which is kept first as bosun.env.pre-update-<time>.
My own files (sessions.yml, setup.sh, policy.md, learn.md, tasks/, workstreams.json) are never
edited: what to change in one is in USER_FILE_CHANGES, for the plan, the summary and bosun doctor
to say.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import journal
import knowledge_store
import ledger
import update_gate


CONFIG_FILE = "bosun.env"
# NAME=value or export NAME=value: what a key is renamed or removed in; a comment never is.
ASSIGNMENT = re.compile(r"^(?P<lead>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=")


class MigrationError(ValueError):
    """No way from a store's format to the code's: a step missing, two for one step, one that does
    not go forward, or a store newer than the code."""


@dataclass(frozen=True)
class Context:
    """The folders whose files a migration changes in place; the caller holds the stores' locks (or
    the folders are the rehearsal's copy, which no one else writes)."""

    state_dir: Path
    config_dir: Path

    @property
    def knowledge(self) -> knowledge_store.Paths:
        return knowledge_store.Paths(self.config_dir / update_gate.KNOWLEDGE_DIR)


@dataclass(frozen=True)
class Migration:
    """<store> from format <from_version> to <to_version>: <run> changes the files and returns how
    many records it changed; <summary> is its line, with {n} for that ("{n} items migrated")."""

    store: str
    from_version: int
    to_version: int
    run: Callable[[Context], int]
    summary: str


@dataclass(frozen=True)
class Applied:
    """A migration that ran, and its line for the summary."""

    store: str
    from_version: int
    to_version: int
    line: str


# Every migration, in order (see the module's docstring for what each must do). None yet.
REGISTRY: tuple[Migration, ...] = ()
# Each store's format in this code, in the order they are migrated (the lock order).
FORMATS: Mapping[str, int] = {"ledger": ledger.LEDGER_FORMAT, "knowledge": knowledge_store.KNOWLEDGE_FORMAT}
# Each config key renamed (old, new) or removed (old, ""), in bosun.env.
CONFIG_RENAMES: tuple[tuple[str, str], ...] = ()
# What to change in one of my own files (the file, what to change in it): never applied.
USER_FILE_CHANGES: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _Store:
    """How to read a store's format, and record it after a migration."""

    read: Callable[[Context], int]
    record: Callable[[Context, int], None]


_STORES: Mapping[str, _Store] = {
    "ledger": _Store(
        read=lambda ctx: ledger.store_format(ctx.state_dir),
        record=lambda ctx, version: ledger.write_format(ctx.state_dir, version),
    ),
    "knowledge": _Store(
        read=lambda ctx: knowledge_store.store_format(ctx.knowledge),
        record=lambda ctx, version: knowledge_store.write_format(ctx.knowledge, version),
    ),
}


def store_formats(ctx: Context) -> dict[str, int]:
    """Each store's format on disk in <ctx> (0: no store yet)."""
    return {store: _STORES[store].read(ctx) for store in FORMATS}


def _step_from(store: str, at: int, target: int, registry: Sequence[Migration]) -> Migration:
    """The one migration of <store> from format <at>, which goes forward, to <target> at most."""
    steps = [m for m in registry if m.store == store and m.from_version == at]
    if not steps:
        raise MigrationError(
            f"no migration of the {store} from format {at}: the step {at} -> {at + 1} is missing"
        )
    if len(steps) > 1:
        raise MigrationError(f"{len(steps)} migrations of the {store} from format {at}; there must be one")
    if not at < steps[0].to_version <= target:
        raise MigrationError(
            f"the migration of the {store} from format {at} goes to {steps[0].to_version}, "
            f"not forward to {target} at most"
        )
    return steps[0]


def path(store: str, found: int, target: int, registry: Sequence[Migration] = REGISTRY) -> list[Migration]:
    """The migrations that take <store> from format <found> to <target>, in order. A store not made
    yet (0) needs none: it is made in the code's format. MigrationError for a step missing (named),
    or a store newer than <target>."""
    if found > target:
        raise MigrationError(
            f"the {store} is format {found}, newer than this bosun's {target}: going back is not supported"
        )
    steps: list[Migration] = []
    at = found
    while found and at < target:
        steps.append(_step_from(store, at, target, registry))
        at = steps[-1].to_version
    return steps


def _apply(ctx: Context, migration: Migration) -> Applied:
    """<migration> run, then its store's new format recorded."""
    changed = migration.run(ctx)
    _STORES[migration.store].record(ctx, migration.to_version)
    return Applied(
        migration.store, migration.from_version, migration.to_version, migration.summary.format(n=changed)
    )


def run(ctx: Context, registry: Sequence[Migration] = REGISTRY) -> list[Applied]:
    """Each store in <ctx> taken from its format to the code's (FORMATS), recording its version
    after each migration. Every store's steps are found before any runs, so a step missing changes
    nothing (MigrationError). A store already at the code's format gets none: a second run applies
    nothing."""
    found = store_formats(ctx)
    planned = [
        step for store, target in FORMATS.items() for step in path(store, found[store], target, registry)
    ]
    return [_apply(ctx, migration) for migration in planned]


# ---- the config -------------------------------------------------------------------------------


def _renamed(line: str, renames: Mapping[str, str]) -> tuple[str, str]:
    """<line> with the key it assigns renamed, or "" if the key was removed, and what changed in
    it ("" when nothing did)."""
    match = ASSIGNMENT.match(line)
    if not match or match["key"] not in renames:
        return line, ""
    old, new = match["key"], renames[match["key"]]
    if not new:
        return "", f"{old} removed: bosun no longer reads it"
    return f"{match['lead']}{new}{line[match.end('key') :]}", f"{old} is now {new}"


def migrate_config(text: str, renames: Sequence[tuple[str, str]] = CONFIG_RENAMES) -> tuple[str, list[str]]:
    """<text>, a bosun.env, with each key in <renames> renamed (old, new) or its line removed (old,
    ""), every other line as it was, and a line per key changed."""
    table = dict(renames)
    lines: list[str] = []
    said: list[str] = []
    for line in text.splitlines(keepends=True):
        kept, change = _renamed(line, table)
        lines.append(kept)
        said.append(change)
    return "".join(lines), [change for change in dict.fromkeys(said) if change]


def config_copy(config_dir: Path, now: datetime) -> Path:
    """Where write_config keeps bosun.env as it was before an update at <now> changed it."""
    return config_dir / f"{CONFIG_FILE}.pre-update-{now.astimezone(UTC):%Y%m%d-%H%M%S}"


def _replace(path: Path, data: bytes, mode: int) -> None:
    """<data> as <path>, with <mode>: written whole to a file of its own, flushed to the disk, then
    renamed over it with the folder flushed (journal.replace_durably)."""
    tmp = path.with_name(f".{path.name}.update.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        journal.write_all(fd, data)
        journal.flush_to_disk(fd)
    finally:
        os.close(fd)
    journal.replace_durably(tmp, path)


def write_config(config_dir: Path, now: datetime) -> list[str]:
    """CONFIG_RENAMES applied to <config_dir>'s bosun.env, and a line per key changed. When anything
    changed, the file as it was is kept first (config_copy), with its mode; nothing else is
    written."""
    env = config_dir / CONFIG_FILE
    try:
        was = env.read_bytes()
    except FileNotFoundError:
        return []
    # bytes that are not UTF-8 go through as they were (the shell reads bytes)
    changed, said = migrate_config(was.decode("utf-8", "surrogateescape"), CONFIG_RENAMES)
    if not said:
        return []
    mode = stat.S_IMODE(env.stat().st_mode)
    journal.create_new(config_copy(config_dir, now), was, mode)
    _replace(env, changed.encode("utf-8", "surrogateescape"), mode)
    return said
