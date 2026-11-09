#!/usr/bin/env python3
"""Organizational knowledge for bosun: People, Process, Product (design: docs/design/knowledge.md).

Usage and commands: the header of libexec/bosun-kg (`bosun kg --help`).
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import functools
import itertools
import json
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import approvals
import change_log
import claude_state
import events
import knowledge_changes as kc
import knowledge_model as km
import knowledge_query as kq
import knowledge_review as kr
import knowledge_schema as ks
import knowledge_store as store
import update_backups
import update_gate
import validation
from change_log import ADOPT, DIGEST, Change, ChangeLogError, Edit, reader_rebuilds
from journal import (
    REPLICA_FILE,
    Journal,
    JournalError,
    Transaction,
    as_saved,
    forgotten,
    new_replica_id,
    replica_id,
    up_to,
)
from knowledge_model import KB, KnowledgeError, Proposal, Record
from knowledge_schema import SchemaError
from knowledge_store import Paths, StoreError
from validation import Finding


MAX_QUOTE_LENGTH = 500
# The journal every write goes through (lib/journal.py, docs/design/sync.md), next to the files:
# the knowledge base's stream; the version of the rules its changes are checked against when
# rebuilt (knowledge_changes.APPLIED, SKIPPED and CHECKED), bumped by hand when a change to them
# would rebuild old changes differently (a test in test_knowledge_changes pins their digest to it,
# as test_ledger_changes does for the ledger's RULES_VERSION); the command of the transaction that
# puts a knowledge base from before the journal in it (a change made to the files outside it is
# change_log.ADOPT); view.json's key, the journal position the files reflect and the digest of
# each; and how the files stand against the journal (standing).
KG_STREAM = "kg"
KG_RULES_VERSION = 1
GENESIS = "genesis"
VIEW_KEY = "journal"
SAVED, BEHIND, EDITED_OUTSIDE = "saved", "behind", "edited"
# The files the journal's rebuild makes (a write saves them whole when one does not parse).
REBUILT_FILES = (kc.ENTITIES_FILE, kc.FACTS_FILE)
EDITED = (
    "the knowledge files were changed outside their journal (by hand, a git checkout or revert in "
    "the knowledge folder, or a bosun that does not journal); the next write adopts it"
)
Handler = Callable[[argparse.Namespace], "str | tuple[str, int]"]


@dataclass
class Settings:
    """What `bosun kg` passes (load_config does not export it); tests replace CLOCK. state_dir
    keeps this Mac's replica id (a write needs it); command is what the journal records as each
    write's command ("kg propose", "console review")."""

    root: Path = Path()
    git: bool = True
    batch_cap: int = kr.DEFAULT_BATCH_CAP
    batch_facts: int = kr.DEFAULT_BATCH_FACTS
    group_facts: int = kr.DEFAULT_GROUP_FACTS
    stale_days: int = kr.DEFAULT_STALE_DAYS
    state_dir: str = ""
    command: str = ""
    drain_prompt: str = ""
    caller_window: str = ""
    warnings: list[str] = field(default_factory=list)


SETTINGS = Settings()


def clock() -> datetime:
    return datetime.now(UTC)


CLOCK: Callable[[], datetime] = clock


def configure(root: Path, *, git: bool = True, **options: Any) -> None:
    global SETTINGS  # noqa: PLW0603  (one configuration per process, as in lib/ledger.py)
    SETTINGS = Settings(root, git, **options)


def limits(groups: int | None = None, facts: int | None = None) -> kr.Limits:
    return kr.Limits(
        groups if groups is not None else SETTINGS.batch_cap,
        facts if facts is not None else SETTINGS.batch_facts,
        SETTINGS.group_facts,
    )


def paths() -> Paths:
    return Paths(SETTINGS.root)


# ---- loading and saving -------------------------------------------------------------------------


def load(p: Paths, *, identifiers: bool = True, now: datetime | None = None) -> KB:
    """The knowledge base as a reader sees it, never written: the files, or the journal's rebuild
    in memory where they are behind it (a write cut short between the two) or one does not parse
    (cut short during its append). An unfinished transaction at the end of the journal is left out
    (it was never committed); a journal that cannot be read, or one behind the files, gets one
    warning on stderr and the files as they are, so that readers keep working (unless a file does
    not parse: then nothing can read it, refuse_unreadable); the next write repairs it or says why
    not. identifiers=False (the console) reads none of identifiers.jsonl, forgotten.jsonl and
    secret.key, and computes no key_problem; <now> replaces the clock."""
    kb = read_files(p, identifiers=identifiers, now=now)
    try:
        return read_caught_up(p, kb, open_journal(p))
    except JournalError as exc:
        refuse_unreadable(kb)
        warn_reader(f"the knowledge base's journal in {p.journal} is damaged: {exc}")
        return kb


def read_files(p: Paths, *, identifiers: bool = True, now: datetime | None = None) -> KB:
    """The knowledge files as they are (a write starts from them; validate compares them); without
    <identifiers>, only the entities and the facts. Of a file the journal rebuilds or holds the
    records of (forgotten.jsonl's, in its deletion markers), the lines that parse, and why one does
    not in kb.unreadable; identifiers.jsonl, which nothing else holds, must parse."""
    kb = KB(ks.load_site_schema(p.schema), now or CLOCK())
    kb.entities = {e["id"]: e for e in rebuildable(kb, p.entities)}
    kb.records = rebuildable(kb, p.facts)
    kb.facts = km.fold_all(kb.records)
    if not identifiers:
        return kb
    kb.identifiers = store.read_jsonl(p.identifiers)
    kb.forgotten = rebuildable(kb, p.forgotten)
    kb.secret = store.read_secret(p.key_file)
    kb.key_problem = key_problem(p, kb)
    return kb


def rebuildable(kb: KB, path: Path) -> list[Record]:
    """The records of a file the journal can write again: the lines that parse, and why the first
    one that does not is not a record in kb.unreadable (a write cut short during its append)."""
    records, problem = store.read_lines(path)
    if problem:
        kb.unreadable[path.name] = problem
    return records


def refuse_unreadable(kb: KB) -> None:
    """A knowledge file that does not parse, where no journal can write it again (none yet, one
    that cannot be read, or one behind view.json): nothing else holds what it held, so every
    command stops, naming the (first) line."""
    if kb.unreadable:
        raise StoreError(store.unreadable(next(iter(kb.unreadable.values()))))


def rebuilds_files(kb: KB) -> bool:
    """Whether a file the journal rebuilds does not parse, so that its rebuild takes its place."""
    return any(name in kb.unreadable for name in REBUILT_FILES)


def say_unreadable(kb: KB) -> None:
    """A reader's line on stderr for each knowledge file that does not parse: what it reads."""
    for name, problem in kb.unreadable.items():
        what = "the journal's rebuild of it" if name in REBUILT_FILES else "its other lines"
        print(f"kg: {problem}; reading {what} until the next write writes it again", file=sys.stderr)


def key_problem(p: Paths, kb: KB) -> str:
    """Why the forgotten people cannot be recognised (keyed records, and no readable key), or "".
    A key file that is not a key, with no keyed record, is no problem: the next write replaces it
    (made_key)."""
    if kb.secret or not km.has_keyed_records(kb):
        return ""
    return key_missing(p)


def key_missing(p: Paths) -> str:
    """The key of the forgotten is missing, and what to do. The journal's deletion markers hold the
    same records as forgotten.jsonl, so the way out when the key is gone for good moves both aside,
    with the key file if one is there (replica.json may stay, as for a damaged journal)."""
    replica_file = Path(SETTINGS.state_dir) / REPLICA_FILE
    return (
        f"the key of the forgotten people, {p.key_file}, is missing, unreadable or not a key; restore "
        "it (a new one would stop the deleted people being recognised; if it is gone for good, move "
        f"forgotten.jsonl and the journal folder {p.journal} aside, and {p.key_file.name}, if it is "
        "there, all together: the people in them can then be added again, and the next write makes a "
        f"new key and starts a new journal as a new replica; {replica_file} may stay)"
    )


def made_key(p: Paths) -> bytes:
    """The key of the forgotten, made by a write when it has none and no keyed record needs one. A
    file there that is not a key (empty, the wrong length, unreadable: cut short as it was made,
    edited, restored) is moved aside first, kept, and said on stderr."""
    if os.path.lexists(p.key_file):
        aside = store.set_key_aside(p.key_file)
        print(
            f"kg: {p.key_file} was not a key (empty, the wrong length or unreadable), and no forgotten "
            f"record needs it; moved it aside to {aside.name} (kept, not in the history) and made a "
            "new one",
            file=sys.stderr,
        )
    return store.create_secret(p.key_file)


def write_files(p: Paths, kb: KB) -> None:
    """The files a write changed (after its changes are in the journal). The identifiers and
    forgotten.jsonl first, each whole: the journal does not hold them, so a deletion cut short
    between the data files (which the next write finishes from the journal) has already done its
    part there. Then the entities and the fact log, both whole when the write found them behind the
    journal (or not parsing) and goes on from its rebuild; else the fact log only gains lines."""
    if kb.identifiers_changed:
        store.write_atomically(p.identifiers, store.jsonl(kb.identifiers), store.PRIVATE_MODE)
    if kb.new_forgotten:
        store.write_atomically(p.forgotten, store.jsonl(kb.forgotten + kb.new_forgotten))
    if kb.entities_changed or kb.rebuilt:
        entities = [kb.entities[i] for i in sorted(kb.entities)]
        store.write_atomically(p.entities, store.jsonl(entities))
    if kb.rewrite or kb.rebuilt:
        store.write_atomically(p.facts, store.jsonl(kb.records))
    else:
        store.append_records(p.facts, kb.new_records)
    if kb.rewrite or kb.stale_batch:
        p.batch.unlink(missing_ok=True)  # its numbers may point at deleted facts


def write_view(p: Paths, stamp: Mapping[str, Any]) -> None:
    """view.json, written last: the journal position the files reflect and the digest of what each
    data file holds (so a write cut short is told apart from a change made outside the journal)."""
    store.write_atomically(p.view, json.dumps({VIEW_KEY: stamp}, indent=2, sort_keys=True) + "\n")


def restart_history(p: Paths) -> None:
    """The history restart a write owes (it deleted a person, finished a deletion cut short, or
    dropped or refused a person), paid once its files are final and before view.json, so that a
    write cut short before it owes it again: the history is deleted here, whatever
    BOSUN_KNOWLEDGE_GIT says (one kept before may hold the person), and the save's commit after
    view.json starts it again with the files as they are now, when the history is on."""
    store.drop_history(p.root)


def record_history(p: Paths, message: str) -> None:
    if not SETTINGS.git:
        return
    warning = store.commit(p.root, f"kg {message}")
    if warning:
        SETTINGS.warnings.append(warning)


@dataclass
class Write:
    """A write in progress: its journal, as read once at its start and changed since (<changes>),
    its transaction, the entities as it found them, and what of the files as changed outside the
    journal it could not take."""

    journal: Journal
    changes: list[Change]
    transaction: Transaction
    entities: dict[str, Record]
    left_out: LeftOut | None = None


def save(p: Paths, kb: KB, message: str, write: Write) -> None:
    """What the write changed: first what it could not take of files changed outside the journal
    (said, and the files as edited kept, while they are still there: say_left_out); then in the
    journal, the files, the history restart it owes (if any), view.json, and the history."""
    say_left_out(p, kb, write.left_out)
    with journal_errors(p):
        stamp = journaled(p, kb, write)
    write_files(p, kb)
    if kb.restart_history:
        restart_history(p)
    write_view(p, stamp)
    record_history(p, message)


def journaled(p: Paths, kb: KB, write: Write) -> dict[str, Any]:
    """Put what the write changed in the journal, as one transaction; refused whole, before
    anything is written, when it holds what the journal cannot or a deletion would have to remove
    a change from the private journal. A deletion commits its marker (kc.deletion_marker: the
    position moves; it names the facts removed) with the transaction, then turns what it removed
    into placeholders in local.jsonl (forget_in_journal), so a cut between the two is finished by
    the next write. Returns view.json's stamp: the journal's new position and the digest of each
    file."""
    edits = kc.edits(write.entities, kb)
    refuse_unencodable(edits)
    held = files_digest(kb)
    targets = kc.removed(write.entities, kb) if kb.rewrite else set()
    if kb.rewrite:
        refuse_private(write.changes, targets)
        marker = kc.deletion_marker(kb.new_forgotten, sorted(kb.removed))
        edits = [*(e for e in edits if e.target not in targets), marker]
    write.transaction.commit_edits(edits)
    if kb.rewrite:
        forget_in_journal(p, write.journal, write.changes, targets, bool(targets))
    return change_log.stamped(write.journal.position(), held)


def forget_in_journal(
    p: Paths, journal: Journal, changes: list[Change], targets: set[str], deleted: bool
) -> list[Change]:
    """A deletion's last step (kg forget, the retention purge, one made outside the journal, one
    cut short and finished): every change of <targets> a placeholder in local.jsonl, with what
    writes cut short left of that file (Journal.forget), and what they left in the knowledge
    folder (store.remove_temporaries: batch.json.tmp names people), under the exclusive lock;
    then, when <deleted> (it removed something: a marker is committed only then, so one found
    unfinished says so even when <targets> is empty, a person with no facts), every update backup
    (delete_update_backups). Returns the journal as read (<changes>) as it is now."""
    journal.forget(targets)
    store.remove_temporaries(p.root)
    if deleted:
        delete_update_backups()
    return forgotten(changes, targets)


def delete_update_backups() -> None:
    """Every update backup deleted (update_backups.delete_all), as a deletion's last step: a backup
    holds the whole knowledge base, so restoring one from before would bring the person back. Says
    how many, never whom."""
    count = update_backups.delete_all(state_dir())
    if count:
        noun = "backup" if count == 1 else "backups"
        print(
            f"kg: deleted {count} update {noun} taken before this; going back past it is no longer possible",
            file=sys.stderr,
        )


def refuse_unencodable(edits: list[Edit]) -> None:
    """The write boundary: a change the journal cannot hold (a float, text that is not valid
    Unicode) is refused before anything is journaled, naming the fact or entity and the field."""
    problem = change_log.refusal(edits, kc.place)
    if problem:
        raise KnowledgeError(problem)


def files_digest(kb: KB) -> dict[str, str]:
    """The digest of what each file will hold; refused, naming where, when they hold what the
    journal cannot (written outside it, after it began)."""
    try:
        return kc.file_digests(kb.entities, kb.records)
    except ChangeLogError as exc:
        problem = change_log.refusal(kc.snapshot(kb), kc.place) or f"nothing was written: {exc}"
        raise KnowledgeError(problem) from exc


def refuse_private(changes: list[Change], targets: set[str]) -> None:
    """A deletion (kg forget, the retention purge, one made outside the journal) turns every change
    of what it removed into a placeholder in local.jsonl (Journal.forget); refused, before anything
    is written, when the private journal (in <changes>, the journal as read) holds a change of it,
    as that file is never rewritten."""
    held = kc.held_privately(changes, targets)
    if held:
        raise KnowledgeError(
            f"nothing was written: {', '.join(held)} is in the private journal, which a deletion never "
            "rewrites: it removes only what names a person, which never leaves this Mac"
        )


# ---- the journal --------------------------------------------------------------------------------


def open_journal(p: Paths, replica: str = "") -> Journal:
    """The knowledge base's journal; a reader needs no replica (it never begins a transaction)."""
    return Journal(p.journal, KG_STREAM, replica, KG_RULES_VERSION)


def state_dir() -> Path:
    """Where this Mac's replica id is kept: a write needs it, as its journal file is named by it."""
    if not SETTINGS.state_dir:
        raise KnowledgeError(
            "a write needs the state folder, where this Mac's replica id is kept (BOSUN_STATE_DIR); "
            "run it as `bosun kg`"
        )
    return Path(SETTINGS.state_dir)


def start_again(p: Paths) -> str:
    """How to go on when the journal cannot be used: the files are whole, so a new journal can
    start from them. Under a new replica, so that no fact id is minted twice from the same seq (as
    for the ledger, which then goes on as that replica too): the write that starts it makes one
    (change_log.starts_again), so replica.json may stay."""
    replica_file = Path(SETTINGS.state_dir) / REPLICA_FILE
    return (
        f"move the folder {p.journal} aside (keep it): the next write starts a new journal from the "
        f"files, as a new replica ({replica_file} may stay: a new id is made either way)"
    )


@contextlib.contextmanager
def journal_errors(p: Paths) -> Iterator[None]:
    """A journal that cannot be read, or a change it refuses, as a KnowledgeError: nothing written."""
    try:
        yield
    except JournalError as exc:
        raise KnowledgeError(
            f"the knowledge base's journal in {p.journal} is damaged: {exc}. Nothing was written. To "
            f"go on from the files as they are, {start_again(p)}"
        ) from exc
    except ChangeLogError as exc:
        raise KnowledgeError(f"nothing was written: {exc}") from exc


def saved_stamp(p: Paths) -> Any:
    """What view.json records of the journal (the position the files reflect, their digests); None
    without one."""
    try:
        view = json.loads(p.view.read_text())
    except (OSError, ValueError):
        return None
    return view.get(VIEW_KEY) if isinstance(view, dict) else None


def saved_position(p: Paths) -> dict[str, int]:
    """The journal position view.json says the files reflect; {} without one."""
    return change_log.position_in(saved_stamp(p))


def saved_digests(p: Paths) -> dict[str, str] | None:
    """The digest of each file view.json records; None without them (files from before)."""
    stamp = saved_stamp(p)
    recorded = stamp.get(DIGEST) if isinstance(stamp, dict) else None
    return recorded if isinstance(recorded, dict) else None


def standing(p: Paths, kb: KB, position: Mapping[str, int], rebuild: Callable[[], kc.RebuiltKB]) -> str:
    """How the files (<kb> as read) stand against the journal (at <position>): SAVED as a write
    left them; EDITED_OUTSIDE, one holding neither what view.json recorded nor what the journal's
    rebuild makes (by hand, a git checkout or revert in the knowledge folder, a bosun that does not
    journal and leaves view.json as it was); else BEHIND, a write cut short somewhere between the
    journal and view.json (each file as recorded or as the rebuild), which the rebuild finishes,
    even at the same position; and BEHIND when one does not parse (cut short during its append), as
    only the rebuild can be read whole. Files saved before a digest each are trusted."""
    recorded = saved_digests(p)
    if rebuilds_files(kb):
        return BEHIND
    held = kc.held_digests(kb.entities, kb.records)
    at_position = saved_position(p) == position
    if held == recorded or (recorded is None and None not in held.values()):
        return SAVED if at_position else BEHIND
    result = rebuild()
    if kc.edited_files(recorded, held, kc.held_digests(result.entities, result.records)):
        return EDITED_OUTSIDE
    return BEHIND


def warn_reader(problem: str) -> None:
    print(f"kg: {problem}; reading the knowledge files as they are", file=sys.stderr)


def read_caught_up(p: Paths, kb: KB, journal: Journal) -> KB:
    """The files (<kb>), or the journal's rebuild where they are behind it or one does not parse
    (said on stderr). A journal behind the files is never rebuilt from (change_log.reader_rebuilds),
    nor an empty one: then a file that does not parse stops the reader (refuse_unreadable)."""
    position = journal.position()
    rebuild, gaps = reader_rebuilds(position, saved_position(p), p.view.name, written=journal.written())
    if gaps or not position:
        refuse_unreadable(kb)
    if gaps:
        warn_reader(f"the knowledge base's journal in {p.journal} is behind {p.view.name} ({gaps})")
    say_unreadable(kb)
    return rebuilt(kc.rebuild(journal.changes()), kb) if rebuild or rebuilds_files(kb) else kb


def rebuild_once(changes: list[Change]) -> Callable[[], kc.RebuiltKB]:
    """The rebuild of <changes>, made the first time it is asked for, then kept."""
    return functools.cache(lambda: kc.rebuild(changes))


def rebuilt(result: kc.RebuiltKB, kb: KB, *, writing: bool = False) -> KB:
    """<kb> with the journal's rebuild (<result>; the entities and the fact log; the identifiers,
    the forgotten records and the key stay as the files have them). A write that
    rebuilds, and so saves the rebuild, says on stderr what it keeps but could not apply and which
    file that did not parse it replaces, drops the identifiers of entities gone, and deletes
    batch.json if facts went."""
    facts = km.fold_all(result.records)
    kb.stale_batch = bool(set(kb.facts) - set(facts))
    kb.entities, kb.records, kb.facts = result.entities, result.records, facts
    kb.rebuilt = True
    replaced = [kb.unreadable.pop(name) for name in REBUILT_FILES if name in kb.unreadable]
    note = validation.unapplied_note(len(result.conflicts), len(result.pending), "bosun kg validate")
    if writing:
        km.drop_orphan_identifiers(kb)
        for problem in replaced:
            print(f"kg: {problem}; saved again as the journal's rebuild", file=sys.stderr)
        if note:
            print(f"kg: {note}", file=sys.stderr)
    return kb


def adopted_base(p: Paths, changes: list[Change], whole: kc.RebuiltKB | None = None) -> kc.RebuiltKB:
    """What the files held when a write last saved them, as the journal (<changes>, as read) has
    it: the rebuild at their position, with what was adopted from them since (by a write cut short
    after it). When that is the whole journal (the files were saved at its position), the rebuild
    of it all (<whole>), if the caller has it."""
    saved = as_saved(changes, saved_position(p), ADOPT)
    if whole is not None and len(saved) == len(changes):
        return whole
    return kc.rebuild(saved)


def history_on() -> bool:
    return SETTINGS.git and store.git_available()


def forget_outside(
    p: Paths, journal: Journal, changes: list[Change], kb: KB, persons: set[str], whole: kc.RebuiltKB
) -> list[Change]:
    """A deletion on request made outside the journal (<persons>: gone from the files, matched by a
    forgotten record; an older bosun's kg forget, or a hand removal), made in it too, as kg forget
    makes one: its marker first (holding their forgotten records and the facts naming them, so that
    a cut after it is finished by the next write), then every change of them and of those facts (in
    the whole journal: <changes>, rebuilt as <whole>) a placeholder in local.jsonl
    (forget_in_journal), and the history restart owed for the save (the history holds the
    journal). Returns the journal as read, as it is now."""
    facts = km.fold_all(whole.records)
    targets = kc.naming(persons, facts)
    refuse_private(changes, targets)
    records = kc.records_of(persons, km.forgotten_records(kb), kb.secret)
    marker = kc.deletion_marker(records, sorted(targets - persons))
    changes = [*changes, *journal.begin(ADOPT).commit_edits([marker])]
    changes = forget_in_journal(p, journal, changes, targets, bool(targets))
    kb.restart_history = True
    print(
        f"kg: {len(persons)} person(s) deleted on request are gone from the knowledge files and deleted "
        "from their journal too: their changes in local.jsonl are placeholders now, and the knowledge "
        "history starts again without them",
        file=sys.stderr,
    )
    return changes


def adopt(p: Paths, journal: Journal, base: kc.RebuiltKB, kb: KB, refused: set[str]) -> list[Change]:
    """Put in the journal what was changed in the files outside it, as one transaction (ADOPT):
    what they hold beyond what they held when a write last saved them (<base>; kc.outside), never
    the people <refused>; returns its changes. Nothing when there is nothing to adopt (a write cut
    short may have adopted it already, or the journal cannot hold it: the fact log only grows)."""
    edits = kc.outside(base, kb, refused)
    if not edits:
        return []
    refuse_unencodable(edits)
    done = journal.begin(ADOPT).commit_edits(edits)
    print(
        f"kg: the knowledge files in {p.root} were changed outside their journal (by hand, a git "
        "checkout or revert there, or a bosun that does not journal); adopted "
        f"{len(edits)} change(s) into the journal (transaction '{ADOPT}')",
        file=sys.stderr,
    )
    return done


def kept_as_edited(p: Paths, refused: set[str], key_problem: str, *, restart: bool) -> str:
    """Where the files as changed outside the journal are kept once the write saves the journal's
    rebuild over them: in the knowledge folder's git history (committed now), when it is on, they
    name no person refused (no history may hold one deleted on request), and the write does not
    start the history again (<restart>: it would delete that commit, and keeping the history would
    keep what the write deletes)."""
    if refused and key_problem:
        return (
            "nothing keeps the files as they were edited, as they name a person whom the forgotten "
            "records cannot be checked against without their key"
        )
    if refused:
        return "nothing keeps the files as they were edited, as they name a person deleted on request"
    if restart:
        return (
            "nothing keeps the files as they were edited: the knowledge history holds what this write "
            "deletes, so it starts again without them"
        )
    if not history_on():
        return "the knowledge history is off, so nothing keeps the files as they were edited"
    warning = store.commit(p.root, f"kg {ADOPT}: the files as changed outside the journal")
    if warning:
        return f"keeping the files as they were edited in the knowledge history failed ({warning})"
    return f"the knowledge folder's git history keeps the files as they were edited (git -C {p.root} log)"


@dataclass
class LeftOut:
    """What of the files as changed outside their journal it could not take (<parts>: a record
    removed or edited in place, as the fact log only grows), and the people refused from them."""

    parts: int
    refused: set[str]


def left_out(base: kc.RebuiltKB, edited: KB, refused: set[str]) -> LeftOut | None:
    """What of the files as edited the journal could not take (<base>: what it holds of them, what
    it adopted included), the people refused aside; None for nothing."""
    aside = kc.naming(refused, edited.facts)
    found = kc.differences(base, edited.entities, edited.records)
    parts = [f for f in found if f.get("item") not in aside]
    return LeftOut(len(parts), refused) if parts else None


def say_left_out(p: Paths, kb: KB, left: LeftOut | None) -> None:
    """One line on stderr counting what of the files as edited the journal could not take, and
    where the files as edited are kept (kept_as_edited). Said by the save, before the files are
    replaced: only then is it known whether the write starts the history again (a deletion it
    finishes or applies, or the command's own)."""
    if left:
        print(
            f"kg: {left.parts} part(s) of the knowledge files as changed outside their journal "
            "could not go into it and are as the journal has them (the fact log only grows: a record "
            "removed comes back, and one edited in place comes back beside its edited copy; close a "
            "fact with kg supersede, delete a person with kg forget); "
            f"{kept_as_edited(p, left.refused, kb.key_problem, restart=kb.restart_history)}",
            file=sys.stderr,
        )


def taken_in(
    p: Paths, journal: Journal, changes: list[Change], edited: KB, whole: kc.RebuiltKB
) -> tuple[KB, LeftOut | None, list[Change]]:
    """Files changed outside the journal (<changes>, as read; <whole>, its rebuild), taken in: what
    the journal cannot hold of them is converted once (kc.taken_in, a note each); a person deleted
    on request and gone from them is deleted from the journal too (forget_outside); what they hold
    beyond what they held when a write last saved them (adopted_base, made once) is adopted, never
    a person deleted on request (kc.returning, one line saying so, without naming them); and the
    write goes on from the journal's rebuild, so that they stay its rebuild. Also returns what the
    journal could not take, which the save says (say_left_out), and the journal as it is now."""
    for note in kc.taken_in(edited, "the knowledge files"):
        print(f"kg: {note}", file=sys.stderr)
    base = adopted_base(p, changes, whole)
    gone = kc.forgotten_outside(base, edited)
    if gone:
        changes = forget_outside(p, journal, changes, edited, gone, whole)
        base = adopted_base(p, changes)
    refused = kc.returning(base, edited)
    say_held_back(base, edited, refused)
    changes = [*changes, *adopt(p, journal, base, edited, refused)]
    result = kc.rebuild(changes)
    left = left_out(adopted_base(p, changes, result), edited, refused)
    return rebuilt(result, edited, writing=True), left, changes


def say_held_back(base: kc.RebuiltKB, edited: KB, refused: set[str]) -> None:
    """One line on stderr, naming nobody, when adopt leaves people out: while the key of the
    forgotten cannot be read, every person entity the files hold otherwise than the journal (why,
    and that the check fails closed); else the people deleted on request (kc.returning)."""
    held = kc.held_back(base, edited, refused)
    if edited.key_problem and held:
        print(
            f"kg: no person is taken in from the knowledge files as changed outside their journal "
            f"({len(held)} left out, and they go from the files: restore those again once the key is "
            f"back), as {edited.key_problem}",
            file=sys.stderr,
        )
    elif refused:
        print(
            f"kg: the knowledge files as changed outside their journal name {len(refused)} person(s) "
            "deleted on request, who are not taken back into it and go from the files (kg add-entity "
            "<id> --allow-forgotten adds one again, if they asked)",
            file=sys.stderr,
        )


def take_forgotten(p: Paths, changes: list[Change], kb: KB) -> None:
    """Forgotten is forgotten.jsonl and the records the journal's deletion markers carry: a record
    forgotten.jsonl lacks (a deletion cut short before its files, a forgotten.jsonl restored from
    before it) is written now, before anything is taken in, so that every check sees it. A
    forgotten.jsonl that does not parse is written again then too, from its other lines (said on
    stderr)."""
    missing = kc.unrecorded(changes, kb.forgotten)
    problem = kb.unreadable.pop(p.forgotten.name, "")
    if not (missing or problem):
        return
    kb.forgotten += missing
    store.write_atomically(p.forgotten, store.jsonl(kb.forgotten))
    if problem:
        print(
            f"kg: {problem}; written again from its other lines, with the records the journal's "
            "deletion markers carry",
            file=sys.stderr,
        )
    kb.key_problem = key_problem(p, kb)


def unfinished(p: Paths, changes: list[Change], position: Mapping[str, int]) -> list[Change]:
    """The journal's changes (<changes>, as read; at <position>) the files do not reflect yet:
    those after their saved position (a write cut short somewhere between the journal and
    view.json); none when the files are at the journal's position."""
    saved = saved_position(p)
    if saved == position:
        return []
    kept = {change["id"] for change in up_to(changes, saved)}
    return [change for change in changes if change["id"] not in kept]


def deletion_cut_short() -> bool:
    """Whether a deletion of a person (kg forget, the retention purge, one made outside the
    journal) was cut short: its marker among the journal's changes the files do not reflect yet
    (unfinished), which the next write finishes. Until then the person is in the files, so `bosun
    update` stops at its plan rather than copy them into a backup. Read under the shared lock; a
    knowledge base that does not exist has none. A journal that cannot be read is a JournalError."""
    p = paths()
    with store.locked(p, False):
        journal = open_journal(p)
        position = journal.position()
        if not position:
            return False
        later = unfinished(p, journal.changes(), position)
    return any(change.get("op") == kc.DELETION for change in later)


def refuse_finishing_without_key(p: Paths, kb: KB, later: list[Change]) -> None:
    """A deletion on request cut short (a keyed record in a deletion marker <later>) is finished
    by matching the people in the journal against its record, which takes the key: without one
    the write is refused whole, before anything is written (it would move view.json past the
    deletion, which would then never be finished), naming the key, as adding a person is."""
    if kb.secret or not km.any_keyed(kc.marker_records(later)):
        return
    raise KnowledgeError(
        "nothing was written: a deletion on request was cut short, and the write that finishes it "
        f"needs the key to tell whom it deleted: {key_missing(p)}. Once the key is back, the next "
        "write finishes the deletion (with the journal moved aside instead, it is never finished: "
        "run kg forget for them again)"
    )


def finish_deletions(
    p: Paths, journal: Journal, changes: list[Change], kb: KB, later: list[Change]
) -> list[Change]:
    """A deletion the files do not reflect yet (its marker in <later>, unfinished) is finished:
    the placeholders for its forgotten records' hashed ids, and for the facts it names (a
    retention purge's), are put in again (a cut between its marker and its rewrite leaves them
    out), what writes cut short left is deleted (a cut after the rewrite leaves it:
    forget_in_journal does both), and the history restart it owed is owed by this write. Returns
    the journal as read (<changes>), as it is now."""
    if not any(change.get("op") == kc.DELETION for change in later):
        return changes
    kb.restart_history = True
    result = kc.rebuild(changes)
    facts = km.fold_all(result.records)
    persons = kc.matched(kc.people(result.entities, facts), kc.marker_records(later), kb.secret)
    targets = kc.naming(persons, facts) | kc.marker_facts(later)
    refuse_private(changes, targets)
    return forget_in_journal(p, journal, changes, targets, True)


def refuse_if_behind(p: Paths, journal_has: Mapping[str, int]) -> None:
    """A journal that lost changes the files reflect (a journal file deleted, a partial restore)
    is never written to: a rebuild from it would lose them too. An empty journal is not behind:
    the next write starts it again from the files."""
    gaps = change_log.behind(journal_has, saved_position(p), p.view.name) if journal_has else []
    if gaps:
        raise KnowledgeError(
            f"the knowledge base's journal in {p.journal} is behind {p.view.name} ({'; '.join(gaps)}), "
            "so it cannot rebuild the files. Nothing was written. If journal files were lost, "
            f"{start_again(p)}"
        )


def unstamp(p: Paths) -> None:
    """view.json saved without the position of a journal gone (nor the digests recorded with it),
    as for files from before the journal, when the journal starts again (change_log.starts_again),
    before anything is journaled: so a write cut short between the new journal's first transaction
    (genesis) and view.json is finished by the next write, the files being behind the journal,
    never refused as a journal that lost lines (the old position names seqs the new journal never
    had)."""
    write_view(p, {})


def genesis(p: Paths, journal: Journal, kb: KB) -> list[Change]:
    """Put a knowledge base from before the journal in it whole (kc.snapshot), as one transaction
    (GENESIS), what the journal cannot hold converted once with a note on stderr; then save the
    files as they are now (rewritten only if something was converted) and view.json, at once, so
    that a command that then fails leaves them at the journal's position. Returns the journal: that
    transaction."""
    notes = kc.taken_in(kb, "the knowledge files")
    for note in notes:
        print(f"kg: {note}", file=sys.stderr)
    done = journal.begin(GENESIS).commit_edits(kc.snapshot(kb))
    if notes:
        store.write_atomically(p.entities, store.jsonl([kb.entities[i] for i in sorted(kb.entities)]))
        store.write_atomically(p.facts, store.jsonl(kb.records))
    write_view(p, change_log.stamped(journal.position(), kc.file_digests(kb.entities, kb.records)))
    return done


def take_replica(p: Paths, journal: Journal, state: Path, written: Mapping[str, int]) -> None:
    """<journal>'s replica id for this write, decided once (change_log.starts_again, on what the
    journal has <written>), after the files are read, so that a write refused before then makes
    none: a journal that starts again gets a new one, made just before view.json is saved without
    the old position (unstamp), so that a cut between the two makes one more id at the next write,
    never the old one again; any other gets this Mac's (replica_id, made once). Both in <state>."""
    if change_log.starts_again(written, saved_position(p)):
        journal.replica = new_replica_id(state)
        unstamp(p)
    else:
        journal.replica = replica_id(state)


def write_start(p: Paths, journal: Journal, state: Path) -> tuple[KB, LeftOut | None, list[Change]]:
    """The knowledge base a write starts from, in memory (the command's one save writes it), as
    the ledger's write does. Refused when the journal lost changes the files reflect (a file
    deleted, a partial restore); then the journal takes its replica id, and one that starts again a
    new id and view.json saved without the old position (take_replica); an unfinished transaction
    at the end of the journal is set aside (repair_tail); a knowledge base from before the journal
    is put in it (genesis); a change made to the files outside the journal is taken in (adopted
    where it can be, and the journal's rebuild goes on); and where the files are behind the journal
    (a write cut short anywhere between the journal and view.json), the journal's rebuild replaces
    them (standing). Before any of that, a deletion cut short that cannot be finished without the
    key is refused
    (refuse_finishing_without_key), the forgotten records the journal's markers carry are in
    forgotten.jsonl (take_forgotten), and a deletion cut short is finished (finish_deletions). A
    write that drops a person the files held owes a history restart. The journal is read once and
    that read passed on (rebuilt once, when it must be). Also returns what the journal could not
    take of files changed outside it, for the save to say (taken_in), and the journal as read."""
    kb = read_files(p)
    written = journal.written()
    refuse_if_behind(p, written)
    take_replica(p, journal, state, written)
    journal.repair_tail()
    position = journal.position()
    if not position:
        refuse_unreadable(kb)
        return kb, None, genesis(p, journal, kb) if kb.entities or kb.records else []
    refuse_if_behind(p, position)
    changes = journal.changes()
    later = unfinished(p, changes, position)
    refuse_finishing_without_key(p, kb, later)
    take_forgotten(p, changes, kb)
    changes = finish_deletions(p, journal, changes, kb, later)
    whole = rebuild_once(changes)
    found = standing(p, kb, position, whole)
    if found == SAVED:
        return kb, None, changes
    held = kc.people(kb.entities, kb.facts)
    left = None
    if found == EDITED_OUTSIDE:
        kb, left, changes = taken_in(p, journal, changes, kb, whole())
    else:
        kb = rebuilt(whole(), kb, writing=True)
    kb.restart_history = kb.restart_history or bool(held - kc.people(kb.entities, kb.facts))
    return kb, left, changes


def trial_mint() -> Callable[[], str]:
    """A dry run's fact ids: never stored, so minted from a count (no journal is read or made)."""
    count = itertools.count()
    return lambda: change_log.mint_suffix("dry-run", "", next(count))


def writing(p: Paths, message: str, state: Path) -> Iterator[KB]:
    """A write under the exclusive lock: first the update gate (update_gate; refused, nothing is
    written): a freeze, before anything of the knowledge base is read, then the format format.json
    records, before the files; then the knowledge base it starts from (write_start), its fact ids
    minted from its one journal transaction, named by its command; then, if it changed anything,
    saved."""
    update_gate.check_freeze(state)
    found = store.store_format(p)
    update_gate.check_format("knowledge base", found, store.KNOWLEDGE_FORMAT)
    new = found == 0
    with journal_errors(p):
        journal = open_journal(p)  # its replica id once write_start has read the files (take_replica)
        kb, left, changes = write_start(p, journal, state)
        transaction = journal.begin(SETTINGS.command)
    if not kb.secret and not kb.key_problem:
        kb.secret = made_key(p)
    kb.mint = transaction.mint
    entities = copy.deepcopy(kb.entities)
    yield kb
    if kb.changed:
        if new:  # a store's version changes only when it is made, and by a migration
            store.write_format(p, store.KNOWLEDGE_FORMAT)
        save(p, kb, message, Write(journal, changes, transaction, entities, left))


def read_shared(p: Paths, now: datetime) -> KB:
    """The entities and facts for a reader that must never see the restricted files (the console),
    under the shared lock, as `load` makes them."""
    with store.locked(p, False):
        return load(p, identifiers=False, now=now)


@contextlib.contextmanager
def opened(write: bool, message: str = "", dry_run: bool = False) -> Iterator[KB]:
    """The knowledge base under its lock. A write (not a dry run) goes through the journal
    (writing); a reader or a dry run writes nothing, and a dry run's fact ids are never stored."""
    p = paths()
    if write and not dry_run:
        state = state_dir()
        with store.locked(p, True):
            yield from writing(p, message, state)
        return
    with store.locked(p, False):
        kb = load(p)
        if dry_run:
            kb.mint = trial_mint()
        yield kb


# ---- my words ----------------------------------------------------------------------------------


def speaker() -> approvals.Speaker:
    pasted: set[str] = set()
    if SETTINGS.state_dir and SETTINGS.caller_window:
        pasted = events.pasted_hashes(Path(SETTINGS.state_dir), SETTINGS.caller_window, CLOCK())
    return approvals.Speaker(frozenset(pasted), SETTINGS.drain_prompt)


def quote_verified(quote: str, transcript: Path) -> bool:
    """Whether <quote> is in a prompt I typed (or sent from my phone) in that session: the same
    check as an approval's quote. Pasted and injected text and tool results never count."""
    who = speaker()
    texts = [p.text for p in claude_state.delivered_prompts(transcript) if approvals.is_genuine(p.text, who)]
    return approvals.quote_in(quote, texts)


def user_source(args: argparse.Namespace) -> Record:
    quote = approvals.normalize(args.quote or "")
    if not quote or len(quote) > MAX_QUOTE_LENGTH:
        raise KnowledgeError(
            f"--from-user needs --quote with my exact words (1 to {MAX_QUOTE_LENGTH} characters)"
        )
    if not args.transcript or not Path(args.transcript).is_file():
        raise KnowledgeError("--from-user needs --transcript <the session's .jsonl> to check the quote in")
    transcript = Path(args.transcript)
    return {
        "kind": ks.USER_SOURCE,
        "ref": transcript.stem,
        "quote": quote,
        "at": km.iso(CLOCK()),
        "verified": quote_verified(quote, transcript),
    }


def sources_of(args: argparse.Namespace) -> list[Record]:
    sources = [km.parse_source(s, CLOCK()) for s in args.source or []]
    if getattr(args, "from_user", False):
        sources.insert(0, user_source(args))
    return sources


def proposal_of(args: argparse.Namespace, subject: str, predicate: str, obj: str | None) -> Proposal:
    sources = sources_of(args)
    authority = args.authority or (ks.USER_SAID if args.from_user else ks.OBSERVED)
    return Proposal(
        subject,
        predicate,
        obj,
        args.value,
        sources,
        authority,
        args.valid_from,
        getattr(args, "order", None),
        getattr(args, "rework", False),
        args.note or "",
    )


def unverified_note(proposal: Proposal) -> str:
    users = [s for s in proposal.sources if s["kind"] == ks.USER_SOURCE]
    if users and not users[0].get("verified"):
        return "\nnote: the quote is not in anything you typed in that transcript, so it stays proposed"
    return ""


# ---- writing commands ---------------------------------------------------------------------------


def cmd_add_entity(args: argparse.Namespace) -> str:
    with opened(True, f"add-entity {args.id}") as kb:
        spec = km.EntitySpec(args.id, args.name or "", args.alias, args.weak_alias, args.summary or "")
        messages = km.add_entity(kb, spec, allow_forgotten=args.allow_forgotten)
    return "\n".join(messages)


def cmd_identify(args: argparse.Namespace) -> str:
    with opened(True, "identify") as kb:
        return km.add_identifier(kb, args.entity, args.system, args.id)


def cmd_propose(args: argparse.Namespace) -> str:
    proposal = proposal_of(args, args.subject, args.predicate, args.object)
    with opened(True, f"propose {args.subject} {args.predicate}") as kb:
        outcome = km.propose(kb, proposal)
    return "\n".join([outcome.line(), *outcome.messages]) + unverified_note(proposal)


def group_ref(kb: KB, ref: str) -> str:
    """A group key as kg batch makes them, from what was typed (names resolve)."""
    if ref.startswith(kr.AXIS_PREFIX):
        return ref
    entity, sep, predicate = ref.partition(kr.AREA_SEPARATOR)
    return km.resolve(kb, entity) + (sep + predicate if sep else "")


def targets(kb: KB, refs: list[str]) -> list[str]:
    """Fact ids from fact ids and group keys (an entity, <entity>#reviews|owns|approves, or
    axis:<people|process|product>, as kg batch --json shows them)."""
    found: list[str] = []
    keys = kr.group_keys(kb)
    for ref in refs:
        if ref in kb.facts:
            found.append(ref)
            continue
        key = group_ref(kb, ref)
        group = [fact_id for fact_id, group_key in keys.items() if group_key == key]
        if not group:
            raise KnowledgeError(f"{ref}: neither a fact id nor a group with proposals waiting")
        found += group
    return found


def cmd_review(args: argparse.Namespace, confirm: bool) -> str:
    verb = "confirm" if confirm else "reject"
    with opened(True, f"{verb} {' '.join(args.refs)}") as kb:
        messages = km.review(kb, targets(kb, args.refs), confirm, args.note or "")
    return "\n".join(messages)


def cmd_confirm(args: argparse.Namespace) -> str:
    return cmd_review(args, True)


def cmd_reject(args: argparse.Namespace) -> str:
    return cmd_review(args, False)


def replacement_of(kb: KB, args: argparse.Namespace) -> Proposal | None:
    if args.value is None and args.object is None:
        return None
    old = km.get_fact(kb, args.id)
    proposal = proposal_of(args, old["subject"], old["predicate"], args.object)
    if args.from_user and not km.verified_user(proposal.sources):
        raise KnowledgeError(
            "the quote is not in anything you typed in that transcript; give a --source instead"
        )
    if not proposal.sources:
        raise KnowledgeError("a correction needs its source: --source kind:ref, or --from-user --quote ...")
    return proposal


def cmd_supersede(args: argparse.Namespace) -> str:
    chosen = [args.value is not None, args.object is not None, args.by is not None, args.close]
    if sum(chosen) != 1:
        raise KnowledgeError("supersede takes exactly one of --value, --object, --by or --close")
    with opened(True, f"supersede {args.id}") as kb:
        replacement = replacement_of(kb, args)
        messages = km.supersede(kb, args.id, replacement, args.by, args.note or "")
    return "\n".join(messages)


def cmd_forget(args: argparse.Namespace) -> str:
    with opened(True, "forget: a person deleted on request", dry_run=args.dry_run) as kb:
        counts = km.forget(kb, args.entity)
    done = "would delete" if args.dry_run else "deleted"
    history = "" if args.dry_run or not SETTINGS.git else "; the knowledge history was restarted without it"
    return f"ok: {done} the person, {counts['facts']} facts and {counts['identifiers']} identifiers{history}"


def cmd_expire(args: argparse.Namespace) -> str:
    with opened(True, "expire", dry_run=args.dry_run) as kb:
        expired, purged = km.expire(kb)
    prefix = "would record" if args.dry_run else "recorded"
    return f"ok: {prefix} {len(expired)} expired proposals; {purged} person facts past retention deleted" + (
        " (dry run)" if args.dry_run else ""
    )


# ---- the batch ----------------------------------------------------------------------------------


def cmd_batch(args: argparse.Namespace) -> str:
    if args.rest and args.rest[0] == "apply":
        return batch_apply(args, " ".join(args.rest[1:]))
    if args.rest:
        raise KnowledgeError(f"kg batch takes no {args.rest[0]!r}; did you mean kg batch apply '...'?")
    chosen_limits = limits(args.cap, args.max_facts)
    with opened(True, "batch") as kb:
        groups = kr.build_groups(kb, chosen_limits.group_facts)
        chosen = kr.select_batch(groups, chosen_limits)
        record = kr.batch_record(kb, chosen)
        if chosen:
            paths().root.mkdir(parents=True, exist_ok=True)
            store.write_atomically(paths().batch, json.dumps(record, indent=2))
        if args.json:
            return json.dumps({**record, "waiting_groups": len(groups)}, indent=2)
        return kr.render_batch(kb, chosen, len(groups))


def read_batch() -> Record:
    try:
        batch = json.loads(paths().batch.read_text())
    except FileNotFoundError:
        raise KnowledgeError("no batch to answer; run kg batch first") from None
    if batch.get("applied_at"):
        raise KnowledgeError(
            f"the last batch was answered at {batch['applied_at']}; run kg batch for a new one"
        )
    return dict(batch)


def decision_clauses(args: argparse.Namespace, decision: str) -> list[kr.Clause]:
    clauses = kr.flag_clauses(args.confirm, args.reject, args.skip) or kr.parse_clauses(decision)
    if not clauses:
        raise KnowledgeError("say what to do: kg batch apply 'confirm all except 2b' (or --confirm/--reject)")
    return clauses


def batch_apply(args: argparse.Namespace, decision: str) -> str:
    clauses = decision_clauses(args, decision)
    with opened(True, "batch apply", dry_run=args.dry_run) as kb:
        batch = read_batch()
        decisions = kr.decide(clauses, [g["facts"] for g in batch["groups"]])
        confirm = [i for i, verb in decisions.items() if verb == kr.CONFIRM]
        reject = [i for i, verb in decisions.items() if verb == kr.REJECT]
        messages = km.review(kb, confirm, True, "batch") + km.review(kb, reject, False, "batch")
        if not args.dry_run:
            mark_answered(batch, kb)
    left = sum(len(g["facts"]) for g in batch["groups"]) - len(decisions)
    summary = f"{len(confirm)} confirmed, {len(reject)} rejected, {left} left proposed"
    return "\n".join([*messages, summary + (" (dry run)" if args.dry_run else "")])


def mark_answered(batch: Record, kb: KB) -> None:
    store.write_atomically(paths().batch, json.dumps(dict(batch, applied_at=km.iso(kb.now)), indent=2))


# ---- import -------------------------------------------------------------------------------------


@dataclass
class ImportReport:
    entities_added: int = 0
    entities_updated: int = 0
    facts_added: int = 0
    facts_observed: int = 0
    confirmed: int = 0
    proposed: int = 0
    other: int = 0
    refused: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    groups: int = 0
    batches: int = 0
    first_batch: tuple[int, int] = (0, 0)

    def refuse(self, number: int, reason: str, category: str) -> None:
        self.refused[category] = self.refused.get(category, 0) + 1
        self.problems.append(f"  record {number}: {reason}")


def read_import(path: Path) -> list[Record]:
    text = path.read_text()
    if text.lstrip().startswith("["):
        records = json.loads(text)
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise KnowledgeError(f"{path}: a JSON array of objects expected")
        return records
    return store.read_jsonl(path)


def import_source(value: Any, now: datetime) -> Record:
    if isinstance(value, str):
        return km.parse_source(value, now)
    if isinstance(value, dict) and value.get("kind") in ks.SOURCE_KINDS and value.get("ref"):
        return {"kind": value["kind"], "ref": str(value["ref"]), "at": km.iso(now)}
    raise KnowledgeError(f"a source is kind:ref or {{kind, ref}}, not {value!r}")


def import_entity(kb: KB, record: Record, report: ImportReport) -> None:
    aliases = record.get("aliases") or []
    strong = aliases.get("strong", []) if isinstance(aliases, dict) else list(aliases)
    weak = (aliases.get("weak", []) if isinstance(aliases, dict) else []) + list(
        record.get("weak_aliases") or []
    )
    existed = record.get("id") in kb.entities
    spec = km.EntitySpec(
        str(record.get("id", "")), str(record.get("name", "")), strong, weak, str(record.get("summary", ""))
    )
    km.add_entity(kb, spec)
    if existed:
        report.entities_updated += 1
    else:
        report.entities_added += 1


def import_fact(kb: KB, record: Record, report: ImportReport) -> None:
    sources = [import_source(s, kb.now) for s in record.get("sources") or [record.get("source")] if s]
    proposal = Proposal(
        str(record.get("subject", "")),
        str(record.get("predicate", "")),
        record.get("object"),
        record.get("value"),
        sources,
        str(record.get("authority") or ks.OBSERVED),
        record.get("valid_from"),
        record.get("order"),
        bool(record.get("rework")),
        str(record.get("note") or ""),
        int(record.get("observations") or 1),
    )
    outcome = km.propose(kb, proposal)
    if outcome.action == "added":
        report.facts_added += 1
    else:
        report.facts_observed += 1
    if outcome.status == km.CONFIRMED:
        report.confirmed += 1
    elif outcome.status == km.PROPOSED:
        report.proposed += 1
    else:
        report.other += 1


def import_record(kb: KB, number: int, record: Record, report: ImportReport) -> None:
    kind = record.get("type")
    try:
        if kind == "entity":
            import_entity(kb, record, report)
        elif kind == "fact":
            import_fact(kb, record, report)
        else:
            report.refuse(number, f"type must be entity or fact, not {kind!r}", "format")
    except SchemaError as exc:
        report.refuse(number, str(exc), "people whitelist" if exc.whitelist else "schema")
    except KnowledgeError as exc:
        report.refuse(number, str(exc), "unresolved or invalid")


def run_import(kb: KB, records: list[Record]) -> ImportReport:
    report = ImportReport()
    ordered = sorted(enumerate(records, start=1), key=lambda pair: pair[1].get("type") != "entity")
    for number, record in ordered:
        import_record(kb, number, record, report)
    groups = kr.build_groups(kb, SETTINGS.group_facts)
    first = kr.select_batch(groups, limits())
    report.groups = len(groups)
    report.batches = kr.batches_needed(groups, limits())
    report.first_batch = (len(first), sum(len(g.fact_ids) for g in first))
    return report


def import_summary(report: ImportReport, applied: bool) -> str:
    refused = ", ".join(f"{n} {why}" for why, n in sorted(report.refused.items())) or "none"
    lines = [
        ("imported" if applied else "dry run (nothing written; --apply imports)") + ":",
        f"  entities: {report.entities_added} new, {report.entities_updated} already known",
        f"  facts: {report.facts_added} new, {report.facts_observed} re-observed",
        f"  confirmed automatically: {report.confirmed}; waiting for review: {report.proposed}"
        + (f"; other: {report.other}" if report.other else ""),
        f"  refused: {refused}",
        (
            f"  review: {kr.plural(report.groups, 'group')} in {report.batches} daily "
            f"{'batch' if report.batches == 1 else 'batches'} (the first: "
            f"{report.first_batch[0]} groups, {report.first_batch[1]} facts; at most "
            f"{SETTINGS.batch_cap} groups and {SETTINGS.batch_facts} facts a batch)"
        ),
    ]
    return "\n".join(lines + (["refusals:", *report.problems] if report.problems else []))


def cmd_import(args: argparse.Namespace) -> str:
    if args.apply and args.dry_run:
        raise KnowledgeError("--dry-run or --apply, not both")
    records = read_import(Path(args.file))
    with opened(True, f"import {Path(args.file).name}", dry_run=not args.apply) as kb:
        kb.auto_confirm = args.auto_confirm
        report = run_import(kb, records)
    return import_summary(report, args.apply)


# ---- reading commands ---------------------------------------------------------------------------


def cmd_lookup(args: argparse.Namespace) -> str:
    with opened(False) as kb:
        if args.json:
            entity_id = km.resolve(kb, args.entity)
            facts = kq.outgoing(kb, entity_id) + kq.incoming(kb, entity_id)
            return json.dumps({"entity": kb.entities[entity_id], "facts": facts}, indent=2)
        return kq.lookup(kb, args.entity)


def cmd_facts(args: argparse.Namespace) -> str:
    as_of = km.parse_time(args.as_of) if args.as_of else None
    query = kq.FactQuery(args.entity, args.predicate, as_of, args.status, args.closed)
    with opened(False) as kb:
        facts = kq.select_facts(kb, query)
        if args.json:
            return json.dumps(facts, indent=2)
        return "\n".join(kq.fact_line(kb, f, status=True) for f in facts) or "no facts"


def cmd_search(args: argparse.Namespace) -> str:
    with opened(False) as kb:
        hits = kq.search(kb, args.text, args.limit)
        return "\n".join(f"{e}  {km.label(kb, e)}  ({why})" for _, e, why in hits) or "no matches"


def cmd_neighbors(args: argparse.Namespace) -> str:
    with opened(False) as kb:
        found = kq.neighbors(kb, args.entity, args.hops)
        return "\n".join(f"{depth} {kq.fact_line(kb, f, sources=False)}" for depth, f in found) or "no links"


def cmd_context(args: argparse.Namespace) -> str:
    with opened(False) as kb:
        return kq.context_pack(kb, args.target, args.max_tokens)


def cmd_lint(args: argparse.Namespace) -> str | tuple[str, int]:
    with opened(False) as kb:
        findings = kr.lint(kb, args.stale_days or SETTINGS.stale_days)
    code = 1 if args.strict and findings else 0
    if args.json:
        return json.dumps([kr.finding_json(f) for f in findings], indent=2), code
    return ("\n".join(f"{f.kind}: {f.message}" for f in findings) or "ok: nothing to report"), code


def journal_findings(p: Paths, kb: KB) -> list[Finding]:
    """The journal against the files as saved (<kb>; validation.journal_findings): its damage, the
    conflicts and waiting updates of its rebuild, an outside edit (as a write sees it: standing; a
    write cut short is none), and, at the same position, each entity or fact that differs (unless
    the files were edited outside the journal: the next write adopts the difference). First, each
    file that does not parse, which the next write writes again (where no journal can:
    refuse_unreadable, as for every command)."""
    try:
        journal = open_journal(p)
        changes, position, written = journal.changes(), journal.position(), journal.written()
    except JournalError as exc:
        refuse_unreadable(kb)
        return [validation.damage_finding(exc)]
    if not position or change_log.behind(position, saved_position(p), p.view.name):
        refuse_unreadable(kb)
    result = kc.rebuild(changes)
    against = validation.Against(
        file_name=p.view.name,
        position=position,
        saved=saved_position(p),
        written=written,
        conflicts=result.conflicts,
        pending=result.pending,
        has_records=bool(kb.entities or kb.records),
        edited=standing(p, kb, position, lambda: result) == EDITED_OUTSIDE,
        edited_detail=EDITED,
        differences=lambda: [] if rebuilds_files(kb) else kc.differences(result, kb.entities, kb.records),
    )
    return [*unreadable_findings(kb), *validation.journal_findings(against)]


def unreadable_findings(kb: KB) -> list[Finding]:
    """Each knowledge file that does not parse, which the next write writes again."""
    return [
        validation.finding(
            "unreadable",
            f"{problem}; the next write writes it again "
            + (
                "as the journal's rebuild"
                if name in REBUILT_FILES
                else "from its other lines and the journal"
            ),
            name,
        )
        for name, problem in kb.unreadable.items()
    ]


def validate_unlocked(p: Paths) -> list[Finding]:
    """What `kg validate` finds in the knowledge base at <p>: the files as saved against the
    journal's rebuild. It takes no lock and writes nothing: the caller holds the lock, or the folder
    is a copy no one else writes (an update's rehearsal). Under a lock its own process holds, a lock
    of its own would wait for ever (flock is per open file)."""
    return journal_findings(p, read_files(p))


def cmd_validate(args: argparse.Namespace) -> tuple[str, int]:
    """The files as saved against the journal's rebuild, read under the shared lock: nothing is
    written. Exit 1 on findings (a note aside); --json carries each one's severity and line."""
    p = paths()
    with store.locked(p, False):
        found = validate_unlocked(p)
    return validation.report(found, args.json, "knowledge base is valid")


def cmd_stats(_args: argparse.Namespace) -> str:
    with opened(False) as kb:
        counts: dict[str, int] = {}
        for fact in kb.facts.values():
            status = km.effective_status(fact, kb.now)
            counts[status] = counts.get(status, 0) + 1
    parts = ", ".join(f"{counts.get(s, 0)} {s}" for s in km.STATUSES)
    return f"{len(kb.entities)} entities, {len(kb.facts)} facts ({parts})"


def cmd_schema(args: argparse.Namespace) -> str:
    schema = ks.load_site_schema(paths().schema)
    if args.json:
        rows = {
            n: {
                "subjects": sorted(p.subjects),
                "objects": sorted(p.objects),
                "value": p.value,
                "single": p.single,
                "risk": p.risk,
            }
            for n, p in schema.predicates.items()
        }
        return json.dumps({"entity_types": schema.entity_types, "predicates": rows}, indent=2)
    lines = [
        f"{axis}: {', '.join(t for t, a in schema.entity_types.items() if a == axis)}" for axis in ks.AXES
    ]
    lines.append(f"people whitelist: {', '.join(sorted(schema.people_whitelist))}")
    for name, predicate in sorted(schema.predicates.items()):
        single = ", single-valued" if predicate.single else ""
        lines.append(f"  {name}: {predicate.description} ({predicate.risk} risk{single})")
    return "\n".join(lines)


# ---- the command line ---------------------------------------------------------------------------

Arguments = list[tuple[tuple[str, ...], dict[str, Any]]]
SOURCE: Arguments = [(("--source",), {"action": "append", "metavar": "KIND:REF"})]
USER: Arguments = [
    (("--from-user",), {"action": "store_true"}),
    (("--quote",), {}),
    (("--transcript",), {}),
    (("--authority",), {"choices": ks.AUTHORITIES}),
    (("--valid-from",), {}),
    (("--note",), {}),
]
JSON: Arguments = [(("--json",), {"action": "store_true"})]
DRY: Arguments = [(("--dry-run",), {"action": "store_true"})]

COMMANDS: dict[str, tuple[Handler, Arguments]] = {
    "add-entity": (
        cmd_add_entity,
        [
            (("id",), {}),
            (("--name",), {}),
            (("--alias",), {"action": "append", "default": []}),
            (("--weak-alias",), {"action": "append", "default": []}),
            (("--summary",), {}),
            (("--allow-forgotten",), {"action": "store_true"}),
        ],
    ),
    "identify": (cmd_identify, [(("entity",), {}), (("system",), {}), (("id",), {})]),
    "propose": (
        cmd_propose,
        [
            (("subject",), {}),
            (("predicate",), {}),
            (("object",), {"nargs": "?"}),
            (("--value",), {}),
            (("--order",), {"type": int}),
            (("--rework",), {"action": "store_true"}),
            *SOURCE,
            *USER,
        ],
    ),
    "confirm": (cmd_confirm, [(("refs",), {"nargs": "+"}), (("--note",), {})]),
    "reject": (cmd_reject, [(("refs",), {"nargs": "+"}), (("--note",), {})]),
    "supersede": (
        cmd_supersede,
        [
            (("id",), {}),
            (("--value",), {}),
            (("--object",), {}),
            (("--by",), {}),
            (("--close",), {"action": "store_true"}),
            *SOURCE,
            *USER,
        ],
    ),
    "batch": (
        cmd_batch,
        [
            (("rest",), {"nargs": "*"}),
            (("--cap",), {"type": int}),
            (("--max-facts",), {"type": int}),
            (("--confirm",), {}),
            (("--reject",), {}),
            (("--skip",), {}),
            *JSON,
            *DRY,
        ],
    ),
    "import": (
        cmd_import,
        [
            (("file",), {}),
            (("--apply",), {"action": "store_true"}),
            (("--auto-confirm",), {"action": "store_true"}),
            *DRY,
        ],
    ),
    "forget": (cmd_forget, [(("entity",), {}), *DRY]),
    "expire": (cmd_expire, DRY),
    "lookup": (cmd_lookup, [(("entity",), {}), *JSON]),
    "facts": (
        cmd_facts,
        [
            (("entity",), {}),
            (("--predicate",), {}),
            (("--as-of",), {}),
            (("--status",), {"default": km.CONFIRMED, "choices": [*km.STATUSES, "all"]}),
            (("--closed",), {"action": "store_true"}),
            *JSON,
        ],
    ),
    "search": (
        cmd_search,
        [(("text",), {}), (("--limit",), {"type": int, "default": kq.DEFAULT_SEARCH_LIMIT})],
    ),
    "neighbors": (cmd_neighbors, [(("entity",), {}), (("--hops",), {"type": int, "default": 1})]),
    "context": (
        cmd_context,
        [
            (("--for",), {"dest": "target", "required": True}),
            (("--max-tokens",), {"type": int, "default": kq.DEFAULT_PACK_TOKENS}),
        ],
    ),
    "lint": (
        cmd_lint,
        [(("--stale-days",), {"type": int}), (("--strict",), {"action": "store_true"}), *JSON],
    ),
    "validate": (cmd_validate, JSON),
    "stats": (cmd_stats, []),
    "schema": (cmd_schema, JSON),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bosun kg", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (handler, arguments) in COMMANDS.items():
        command = sub.add_parser(name)
        for flags, options in arguments:
            command.add_argument(*flags, **options)
        command.set_defaults(func=handler)
    return parser


def int_setting(name: str, default: int) -> int:
    value = os.environ.get(name) or str(default)
    try:
        return int(value)
    except ValueError as exc:
        raise KnowledgeError(f"{name} must be a whole number, not {value!r}") from exc


def command_name(args: argparse.Namespace) -> str:
    """What the journal records as a write's command: kg and its subcommand ("kg batch apply")."""
    words = ["kg", args.command]
    if args.command == "batch" and args.rest[:1] == ["apply"]:
        words.append("apply")
    return " ".join(words)


def configure_from_environment(command: str = "") -> None:
    """The settings `bosun kg` passes; <command> names the writes in the journal."""
    configure(
        Path(os.environ["BOSUN_CONFIG_DIR"]) / "knowledge",
        git=os.environ.get("BOSUN_KNOWLEDGE_GIT", "1") == "1",
        batch_cap=int_setting("BOSUN_KNOWLEDGE_BATCH_CAP", kr.DEFAULT_BATCH_CAP),
        batch_facts=int_setting("BOSUN_KNOWLEDGE_BATCH_FACTS", kr.DEFAULT_BATCH_FACTS),
        group_facts=int_setting("BOSUN_KNOWLEDGE_GROUP_FACTS", kr.DEFAULT_GROUP_FACTS),
        stale_days=int_setting("BOSUN_KNOWLEDGE_STALE_DAYS", kr.DEFAULT_STALE_DAYS),
        state_dir=os.environ.get("BOSUN_STATE_DIR", ""),
        drain_prompt=os.environ.get("BOSUN_DRAIN_PROMPT", ""),
        caller_window=os.environ.get("BOSUN_CALLER_WINDOW", ""),
        command=command,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.environ.get("BOSUN_CONFIG_DIR"):
        print("kg: BOSUN_CONFIG_DIR is not set; run it as `bosun kg`", file=sys.stderr)
        return 2
    handler: Handler = args.func
    try:
        configure_from_environment(command_name(args))
        result = handler(args)
    except (KnowledgeError, SchemaError, StoreError) as exc:
        print(f"kg: {exc}", file=sys.stderr)
        return 1
    output, code = result if isinstance(result, tuple) else (result, 0)
    if output:
        print(output)
    for warning in SETTINGS.warnings:
        print(warning, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(update_gate.guarded("kg", main, sys.argv[1:]))
