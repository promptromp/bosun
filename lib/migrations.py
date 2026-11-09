"""The one registry of migrations: what moves each store from an older format to the code's, and
the config's keys from their old names (design: docs/design/update.md, "Migrations").

A store's format is the version its files record (`ledger.store_format`,
`knowledge_store.store_format`), pinned to what is on disk by tests/unit/test_store_formats.py.
REGISTRY lists every migration: its store, the format it goes from and to, the function that does
it and the line it adds to the summary; check_registry holds it to FORMATS. `bosun update` runs
them on a rehearsal copy first, then on the real files while it holds the stores' locks. `run`
takes each store from its format to the code's, one step at a time, recording the store's version
after each, so that a run cut short goes on from the last step that finished. Each migration can
run twice with the same result, is tested from a fixture in the old format
(tests/fixtures/migrations/), and leaves the store passing its own check (`validate_unlocked`). A
rules change that alters what earlier changes mean (the decisions of 2026-11-04 in
docs/design/sync.md) is a migration of its store too.

A journal is never rewritten: a migration never changes what was recorded. The store's reader
reads an older envelope as the current one (upcasting), and the migration rebuilds the files
derived from the journal (`ledger.json`, the knowledge files), as a rebuild is in the code's format.
A store that has a journal has those files rebuilt from it and stamped with the journal position
and digest, as a write saves them, never edited in place: an edit in place is a change outside the
journal, which validate reports and the next write adopts. Only a store from before journals (no
journal yet) is converted in place.

The config: a new key needs nothing, as the defaults in lib/common.sh apply; a key renamed or
removed (CONFIG_RENAMES) is edited in bosun.env, which is kept first as bosun.env.pre-update-<time>.
A key whose default would change what an older install did (CONFIG_ADDED, with a kept value) is
written to bosun.env with that value, when the update adds it and the file does not set it, with the
same copy kept; each added key with something to say carries its note there, for the plan, the
summary and bosun doctor (added_notes), and a kept key a note for while it is at its default (unset):
no bosun.env to write it to (keys_unwritten), or one that does not set it, which bosun doctor warns of
(unset_notes, `migrations.py unset`).
My own files (sessions.yml, setup.sh, policy.md, learn.md, tasks/, workstreams.json) are never
edited: what to change in one is in USER_FILE_CHANGES, for the plan, the summary and bosun doctor
to say. Doctor reads it from the last update's record (update_summary.left_to_me), with the config
keys that update added and bosun.env does not set (assigned_keys).
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
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
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # a config key: a shell name
SUMMARY_COUNT = 1  # what check_registry formats each summary with
# A migration that ran, as the update's record keeps it (Applied.as_record).
APPLIED = re.compile(r"(?P<store>[a-z_]+) (?P<from>\d+) -> (?P<to>\d+): (?P<line>.*)", re.DOTALL)


class MigrationError(ValueError):
    """No way from a store's format to the code's (a step missing, two for one step, one that does
    not go forward, a store newer than the code), or a registry or rename table that does not hold
    together."""


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

    def as_record(self) -> str:
        """As the update's record keeps it: "ledger 1 -> 2: 412 items migrated"."""
        return f"{self.store} {self.from_version} -> {self.to_version}: {self.line}"

    @classmethod
    def from_record(cls, text: str) -> Applied | None:
        """The migration <text> records (as_record); None for a line of another shape."""
        found = APPLIED.fullmatch(text)
        if found is None:
            return None
        return cls(found["store"], int(found["from"]), int(found["to"]), found["line"])


# Every migration, in order (see the module's docstring for what each must do). None yet.
REGISTRY: tuple[Migration, ...] = ()
# Each store's format in this code, in the order they are migrated (the lock order).
FORMATS: Mapping[str, int] = {"ledger": ledger.LEDGER_FORMAT, "knowledge": knowledge_store.KNOWLEDGE_FORMAT}
# Each config key renamed (old, new) or removed (old, ""), in bosun.env; one row per old key, and a
# key renamed again is a row of its own (config_names follows them).
CONFIG_RENAMES: tuple[tuple[str, str], ...] = ()
# What to change in one of my own files (the file, what to change in it): never applied.
USER_FILE_CHANGES: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AddedKey:
    """A config key an update adds that has something to say: <kept>, the value written to bosun.env
    so that an install from before the key keeps doing what it did (None: its default is right for
    it too); <note>, what the plan, the summary and bosun doctor say of it; for a kept key, <unset>,
    what they say while bosun.env does not set it (no bosun.env to write it to, a checkout updated with
    git pull, a line removed): its default, which changed what such an install did."""

    key: str
    kept: str | None
    note: str
    unset: str | None = None


# The keys an update adds with a kept value or a note (the others need nothing: their defaults apply).
# A kept value goes between double quotes in bosun.env, so it holds none of " $ ` \.
CONFIG_ADDED: tuple[AddedKey, ...] = (
    AddedKey(
        "BOSUN_ORCHESTRATOR_CONNECTORS",
        "all",
        "the orchestrator keeps every connector, as before; name the ones it needs and drop all "
        "(bosun doctor lists them)",
        "before this version the orchestrator had every connector, and now it starts with none: set it in "
        "bosun.env to the connectors it needs, or to all",
    ),
    AddedKey(
        "BOSUN_CONNECTORS",
        None,
        "repo windows now start without connectors, a repo's own .mcp.json servers included; give one "
        "back with BOSUN_CONNECTORS or connectors: in sessions.yml",
    ),
)


@dataclass(frozen=True)
class _Store:
    """How to read a store's format, and record it after a migration."""

    read: Callable[[Context], int]
    record: Callable[[Context, int], None]


# By store, the same stores as FORMATS (check_registry).
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


def store_format(ctx: Context, store: str) -> int:
    """<store>'s format on disk in <ctx> (0: no store yet). A store whose format cannot be read is
    the store's own error (ledger.LedgerError, knowledge_store.StoreError)."""
    return _STORES[store].read(ctx)


def store_formats(ctx: Context) -> dict[str, int]:
    """Each store's format on disk in <ctx> (store_format)."""
    return {store: store_format(ctx, store) for store in FORMATS}


def _registry_problem(migration: Migration) -> str:
    """What is wrong with <migration> against FORMATS ("" when nothing)."""
    if migration.store not in FORMATS:
        return f"{migration.store!r} is not a store of this bosun ({', '.join(FORMATS)})"
    if not 0 < migration.from_version < migration.to_version <= FORMATS[migration.store]:
        return f"it must go forward, to this bosun's format {FORMATS[migration.store]} at most"
    try:
        migration.summary.format(n=SUMMARY_COUNT)
    except (KeyError, IndexError, ValueError, TypeError, AttributeError) as exc:  # {n[0]}, {n.x}
        return f"its summary {migration.summary!r} must take {{n}} and nothing else ({exc!r})"
    return ""


def check_registry(registry: Sequence[Migration] | None = None) -> None:
    """MigrationError where <registry> (REGISTRY) and FORMATS do not hold together: FORMATS and the
    stores this module reads and records not the same, a migration of a store that is not one, one
    that does not go forward or goes past the code's format, or a summary that does not take {n}.
    (A step missing, or two for one, is path's to find.)"""
    if set(FORMATS) != set(_STORES):
        raise MigrationError(f"FORMATS names {sorted(FORMATS)}, but the stores are {sorted(_STORES)}")
    for migration in REGISTRY if registry is None else registry:
        problem = _registry_problem(migration)
        if problem:
            step = f"{migration.from_version} -> {migration.to_version}"
            raise MigrationError(f"the migration of the {migration.store} {step}: {problem}")


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


def path(store: str, found: int, target: int, registry: Sequence[Migration] | None = None) -> list[Migration]:
    """The migrations of <registry> (REGISTRY) that take <store> from format <found> to <target>, in
    order. A store not made yet (0) needs none: it is made in the code's format. MigrationError for
    a step missing (named), or a store newer than <target>."""
    registry = REGISTRY if registry is None else registry
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
    """<migration> run, then its store's new format recorded (its line made first, so that nothing
    can fail once the store has moved)."""
    line = migration.summary.format(n=migration.run(ctx))
    _STORES[migration.store].record(ctx, migration.to_version)
    return Applied(migration.store, migration.from_version, migration.to_version, line)


def run(ctx: Context, registry: Sequence[Migration] | None = None) -> list[Applied]:
    """Each store in <ctx> taken from its format to the code's (FORMATS) by <registry> (REGISTRY),
    recording its version after each migration. The registry is checked (check_registry) and every
    store's steps are found before any runs, so a registry that does not hold together, or a step
    missing, changes nothing (MigrationError); a store whose format cannot be read raises its own
    error (ledger.LedgerError, knowledge_store.StoreError), from store_formats. A store already at
    the code's format gets none: a second run applies nothing."""
    registry = REGISTRY if registry is None else registry
    check_registry(registry)
    found = store_formats(ctx)
    planned = [
        step for store, target in FORMATS.items() for step in path(store, found[store], target, registry)
    ]
    return [_apply(ctx, migration) for migration in planned]


# ---- the config -------------------------------------------------------------------------------


def _chain(key: str, table: Mapping[str, str]) -> list[str]:
    """<key> and each name it was renamed to after it, the last one "" if it was removed."""
    chain = [key]
    while chain[-1] in table:
        name = table[chain[-1]]
        if name in chain:
            raise MigrationError(f"the config renames go round: {' -> '.join([*chain, name])}")
        chain.append(name)
    return chain


def _chains(renames: Sequence[tuple[str, str]]) -> dict[str, list[str]]:
    """Each old key of <renames> and its chain of names (_chain); MigrationError where the table
    does not hold together."""
    table: dict[str, str] = {}
    for old, new in renames:
        if not KEY.fullmatch(old) or (new and not KEY.fullmatch(new)):
            raise MigrationError(f"the config rename {old!r} -> {new!r} does not name a key")
        if old in table:
            raise MigrationError(f"the config key {old} is renamed twice; there must be one row for it")
        table[old] = new
    return {old: _chain(old, table) for old in table}


def config_names(renames: Sequence[tuple[str, str]] | None = None) -> dict[str, str]:
    """Each old key of <renames> (CONFIG_RENAMES) and the name it has now, each rename followed to
    the last, so that a bosun.env from several versions back gets the current name ("" when the
    chain ends in a removal). The table is closed: MigrationError for a row that does not name a
    key, an old key in two rows, or renames that go round."""
    chains = _chains(CONFIG_RENAMES if renames is None else renames)
    return {old: chain[-1] for old, chain in chains.items()}


def _setters(found: Sequence[re.Match[str] | None], chains: Mapping[str, list[str]]) -> dict[str, str]:
    """For each name the file ends up setting, the key whose lines set it: the one set nearest to
    it (the name itself, then the key renamed fewest times since), the first in the file of
    equals. Under its old name, each was what the bosun of its time read."""
    best: dict[str, tuple[int, str]] = {}
    for match in found:
        chain = chains.get(match["key"], [match["key"]]) if match else [""]
        name, renamed = chain[-1], len(chain) - 1
        if name and (name not in best or renamed < best[name][0]):
            best[name] = (renamed, chain[0])
    return {name: key for name, (_, key) in best.items()}


def _renamed(
    line: str, match: re.Match[str] | None, chains: Mapping[str, list[str]], setters: Mapping[str, str]
) -> tuple[str, str]:
    """<line> as it becomes ("" when it goes), and what changed in it ("" when nothing did)."""
    if not match or match["key"] not in chains:
        return line, ""
    old, new = match["key"], chains[match["key"]][-1]
    if not new:
        return "", f"{old} removed: bosun no longer reads it"
    if setters[new] != old:
        return "", f"{old} dropped: {new} is already set"
    return f"{match['lead']}{new}{line[match.end('key') :]}", f"{old} is now {new}"


def migrate_config(text: str, renames: Sequence[tuple[str, str]] | None = None) -> tuple[str, list[str]]:
    """<text>, a bosun.env, with each assignment of an old key of <renames> (CONFIG_RENAMES; followed
    to its current name, config_names) renamed, or its line removed if the key was removed; every
    other line as it is; and a line per key changed. A name is never set by two keys: where the
    file sets it already (or sets a key renamed fewer times to it), the old key's line is dropped,
    and said. Only an assignment (NAME=..., export NAME=...) changes: another mention of an old key
    (`export OLD` alone, `$OLD` in a value, a comment) is left as written."""
    chains = _chains(CONFIG_RENAMES if renames is None else renames)
    lines = text.splitlines(keepends=True)
    found = [ASSIGNMENT.match(line) for line in lines]
    setters = _setters(found, chains)
    changed = [_renamed(line, match, chains, setters) for line, match in zip(lines, found, strict=True)]
    said = [change for change in dict.fromkeys(change for _, change in changed) if change]
    return "".join(kept for kept, _ in changed), said


def config_copy(config_dir: Path, now: datetime) -> Path:
    """Where write_config keeps bosun.env as it was before an update at <now> changed it (its time
    in UTC, as the update's backups are named)."""
    return config_dir / f"{CONFIG_FILE}.pre-update-{now.astimezone(UTC):%Y%m%d-%H%M%S}"


def _replace(path: Path, data: bytes, mode: int) -> None:
    """<data> as <path>, with <mode>: written whole to a file of its own beside it, flushed to the
    disk, then renamed over it with the folder flushed (journal.replace_durably)."""
    tmp = path.with_name(f".{path.name}.update.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        journal.write_all(fd, data)
        journal.flush_to_disk(fd)
    finally:
        os.close(fd)
    journal.replace_durably(tmp, path)


def _read_config(config_dir: Path) -> str | None:
    """<config_dir>'s bosun.env, its bytes that are not UTF-8 kept as they were (the shell reads
    bytes); None without one."""
    try:
        return (config_dir / CONFIG_FILE).read_bytes().decode("utf-8", "surrogateescape")
    except FileNotFoundError:
        return None


def _assigned(text: str) -> set[str]:
    return {found["key"] for line in text.splitlines() if (found := ASSIGNMENT.match(line))}


def assigned_keys(config_dir: Path) -> set[str]:
    """The keys <config_dir>'s bosun.env assigns (NAME=..., export NAME=...); none without one."""
    text = _read_config(config_dir)
    return set() if text is None else _assigned(text)


def kept_values(added: Collection[str], assigned: Collection[str] = ()) -> dict[str, str]:
    """Each key of <added> (an update's keys_added) that CONFIG_ADDED gives a kept value, and not among
    <assigned> (what bosun.env sets), and that value: what the update writes to bosun.env."""
    return {
        entry.key: entry.kept
        for entry in CONFIG_ADDED
        if entry.kept is not None and entry.key in added and entry.key not in assigned
    }


def keys_to_keep(config_dir: Path, added: Collection[str]) -> dict[str, str]:
    """kept_values for <config_dir>'s bosun.env, as write_config would write them: none without one."""
    text = _read_config(config_dir)
    return {} if text is None else kept_values(added, _assigned(text))


def keys_unwritten(config_dir: Path, added: Collection[str]) -> tuple[str, ...]:
    """The keys of <added> with a kept value that the update cannot write, as <config_dir> has no
    bosun.env: they start at their defaults (the unset note of each says what that changes)."""
    return () if _read_config(config_dir) is not None else tuple(kept_values(added))


def _note(entry: AddedKey, written: Collection[str], unwritten: Collection[str]) -> str | None:
    if entry.kept is None or entry.key in written:
        return entry.note
    return entry.unset if entry.key in unwritten else None


def added_notes(
    added: Collection[str], written: Collection[str], unwritten: Collection[str] = ()
) -> list[tuple[str, str]]:
    """Each key of <added> with a note that holds, and the note, in CONFIG_ADDED's order: a key whose
    default applies; one with a kept value where the update wrote it (<written>), since its note says the
    install keeps what it did, and its unset note where it is at its default (<unwritten>)."""
    notes = ((entry.key, _note(entry, written, unwritten)) for entry in CONFIG_ADDED if entry.key in added)
    return [(key, note) for key, note in notes if note]


def unset_notes(config_dir: Path, keys: Collection[str]) -> list[tuple[str, str]]:
    """Each of <keys> with a kept value (CONFIG_ADDED) that <config_dir>'s bosun.env does not set (or there
    is no bosun.env), and its unset note: read as the update reads the file, so a key set to nothing is
    set. What bosun doctor warns of (`migrations.py unset`)."""
    assigned = assigned_keys(config_dir)
    return [
        (entry.key, entry.unset)
        for entry in CONFIG_ADDED
        if entry.key in keys and entry.unset is not None and entry.key not in assigned
    ]


def assignment(key: str, value: str) -> str:
    """How bosun.env sets <key> to a kept <value>, and how the plan shows it."""
    return f'{key}="{value}"'


def _with_kept(text: str, added: Collection[str]) -> tuple[str, list[str]]:
    """<text> with each kept value of <added> it does not set appended (kept_values), each under a
    comment with its note, and a line per key."""
    kept = kept_values(added, _assigned(text))
    if not kept:
        return text, []
    notes = {entry.key: entry.note for entry in CONFIG_ADDED}
    lines = [
        f"# Added by bosun update: {notes[key]}\n{assignment(key, value)}\n" for key, value in kept.items()
    ]
    lead = text if not text or text.endswith("\n") else text + "\n"
    return lead + "".join(lines), [f"{assignment(key, value)} added" for key, value in kept.items()]


def write_config(config_dir: Path, now: datetime, added: Collection[str] = ()) -> list[str]:
    """CONFIG_RENAMES applied to <config_dir>'s bosun.env (migrate_config), then each key of <added>
    (the update's keys_added) with a kept value that it does not set appended (CONFIG_ADDED), and a
    line per key changed. When anything changed, the file as it was is kept first, once, in
    <config_dir> (config_copy), with its mode; a bosun.env that is a link (to my dotfiles) stays one,
    and its target is what changes. Without a bosun.env nothing is written: the defaults apply."""
    env = config_dir / CONFIG_FILE
    try:
        was = env.read_bytes()
    except FileNotFoundError:
        return []
    # bytes that are not UTF-8 go through as they were (the shell reads bytes)
    renamed, said = migrate_config(was.decode("utf-8", "surrogateescape"))
    changed, added_said = _with_kept(renamed, added)
    said += added_said
    if not said:
        return []
    target = env.resolve()
    mode = stat.S_IMODE(target.stat().st_mode)
    journal.create_new(config_copy(config_dir, now), was, mode)
    _replace(target, changed.encode("utf-8", "surrogateescape"), mode)
    return said


def main(argv: Sequence[str]) -> int:
    """`unset --config-dir <dir> <key>...`, which bosun doctor runs: unset_notes, a line each, the key and a
    tab first."""
    parser = argparse.ArgumentParser(prog="migrations.py", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    unset = commands.add_parser("unset", help="the kept keys bosun.env does not set, and what that means")
    unset.add_argument("--config-dir", type=Path, required=True)
    unset.add_argument("keys", nargs="+")
    args = parser.parse_args(list(argv))
    for key, note in unset_notes(args.config_dir, args.keys):
        print(f"{key}\t{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
