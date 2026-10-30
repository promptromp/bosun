#!/usr/bin/env python3
"""Organizational knowledge for bosun: People, Process, Product (design: docs/design/knowledge.md).

Usage: bosun kg <command> [args]

Writing (every change is one commit in the knowledge directory's own git history):
  add-entity <type>/<slug> [--name N] [--alias A ...] [--weak-alias W ...] [--summary S]
             [--allow-forgotten]
                                     add an entity, or merge new aliases into it (strong alias: a
                                     true synonym, used to resolve names; weak: a related term,
                                     used by search). Near matches are reported as merge suggestions.
                                     --allow-forgotten: re-add an id deleted on request
  propose <subject> <predicate> [<object>] [--value V] --source kind:ref [--source ...]
          [--authority user_said|stakeholder_said|observed|inferred] [--valid-from T] [--order N]
          [--rework] [--note N]
  propose ... --from-user --quote '<my exact words>' --transcript <session.jsonl>
                                     record a fact (an object entity or a --value). Proposing the
                                     same fact again re-observes it (adds the source). It is
                                     confirmed at once only when: the quote is in a prompt I typed
                                     in that transcript; or two trusted sources of different kinds
                                     back a low-risk fact naming no person; or a profile source
                                     gives a person's title or timezone. Slack, PR, ticket and doc
                                     text never confirms anything. Otherwise it waits for kg batch
  confirm <fact-id|group-key> ...    confirm facts after review (a group key: an entity id, or
  reject <fact-id|group-key> ...     <entity>#reviews|owns|approves, as kg batch groups them)
  supersede <fact-id> (--value V | --object E | --by <fact-id> | --close) [--source ... |
            --from-user --quote Q --transcript P] [--note N]
                                     close a fact (valid_to, superseded_by), never delete it
  batch [--cap N] [--max-facts N] [--json]
                                     the review batch: proposals grouped by entity and axis, one
                                     numbered list, rework-preventing groups first; at most --cap
                                     groups (BOSUN_KNOWLEDGE_BATCH_CAP, 10), --max-facts facts
                                     (BOSUN_KNOWLEDGE_BATCH_FACTS, 40) and
                                     BOSUN_KNOWLEDGE_GROUP_FACTS (8) facts a group
  batch apply '<decision>' | --confirm 1,3 --reject 2b [--skip 4]  [--dry-run]
                                     answer the last batch in one line: 'confirm all except 2b'
                                     (2b stays proposed), 'confirm 1,3 reject 2b', 'skip 4';
                                     unanswered facts stay proposed for a later batch
  import <file> [--dry-run | --apply] [--auto-confirm]
                                     entities and facts from a JSON or JSON-lines file, all as
                                     proposals (--auto-confirm: apply the rules above); a dry run
                                     unless --apply: counts, refusals with reasons, batches
  identify <person|team> slack|github|jira|other <id>
                                     a restricted identifier for mentions (never in context packs)
  forget person/<slug> [--dry-run]   delete a person on request: entity, facts, identifiers, and
                                     the git history (restarted); only a hash and counts are kept
  expire [--dry-run]                 record expired proposals (unreviewed for 30 days) and delete
                                     person facts not re-observed for 12 months

Reading:
  lookup <entity> [--json]           current confirmed facts with sources, links to it, what waits
  facts <entity> [--predicate P] [--as-of T] [--status confirmed|proposed|rejected|expired|all]
        [--closed] [--json]          facts about an entity, now or as they held at --as-of
  search <text> [--limit N]          entities by name, alias, related term, summary or fact text
  neighbors <entity> [--hops N]      linked entities within N hops (default 1, at most 3)
  context --for <entity|repo|topic> [--max-tokens N]
                                     a small pack of confirmed, cited facts (about 1.5k tokens)
  lint [--stale-days N] [--json] [--strict]
                                     contradictions, expired proposals, retention, stale facts, alias
                                     collisions, merge suggestions, orphans (--strict: exit 1 if any)
  stats                              counts (what bosun doctor shows)
  schema [--json]                    entity types, predicates, the People whitelist

Entities are <type>/<slug> (person/dana, process/release, repo/invoice-exporter); anywhere an
entity is expected, a name or strong alias works too. Sources are kind:ref with kind one of user,
ledger, report, memory, transcript, profile (trusted) or slack, pr, ticket, doc (untrusted).
The knowledge base lives in $BOSUN_CONFIG_DIR/knowledge/ and never in the repository.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import approvals
import claude_state
import events
import knowledge_model as km
import knowledge_query as kq
import knowledge_review as kr
import knowledge_schema as ks
import knowledge_store as store
from knowledge_model import KB, KnowledgeError, Proposal, Record
from knowledge_schema import SchemaError
from knowledge_store import Paths, StoreError


MAX_QUOTE_LENGTH = 500
Handler = Callable[[argparse.Namespace], "str | tuple[str, int]"]


@dataclass
class Settings:
    """What `bosun kg` passes (load_config does not export it); tests replace CLOCK."""

    root: Path = Path()
    git: bool = True
    batch_cap: int = kr.DEFAULT_BATCH_CAP
    batch_facts: int = kr.DEFAULT_BATCH_FACTS
    group_facts: int = kr.DEFAULT_GROUP_FACTS
    stale_days: int = kr.DEFAULT_STALE_DAYS
    state_dir: str = ""
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


def load(p: Paths) -> KB:
    kb = KB(ks.load_site_schema(p.schema), CLOCK())
    kb.entities = {e["id"]: e for e in store.read_jsonl(p.entities)}
    kb.records = store.read_jsonl(p.facts)
    kb.facts = km.fold_all(kb.records)
    kb.identifiers = store.read_jsonl(p.identifiers)
    kb.forgotten = store.read_jsonl(p.forgotten)
    return kb


def save(p: Paths, kb: KB, message: str) -> None:
    if kb.entities_changed:
        entities = [kb.entities[i] for i in sorted(kb.entities)]
        store.write_atomically(p.entities, store.jsonl(entities))
    if kb.rewrite:
        store.write_atomically(p.facts, store.jsonl(kb.records))
        p.batch.unlink(missing_ok=True)  # its numbers may point at deleted facts
    else:
        store.append_records(p.facts, kb.new_records)
    if kb.identifiers_changed:
        store.write_atomically(p.identifiers, store.jsonl(kb.identifiers), store.PRIVATE_MODE)
    store.append_records(p.forgotten, kb.new_forgotten)
    if not SETTINGS.git:
        return
    restart = store.restart_history if kb.restart_history else store.commit
    warning = restart(p.root, f"kg {message}")
    if warning:
        SETTINGS.warnings.append(warning)


@contextlib.contextmanager
def opened(write: bool, message: str = "", dry_run: bool = False) -> Iterator[KB]:
    """The knowledge base under its lock; saved afterwards if <write>, it changed and this is not a
    dry run."""
    p = paths()
    with store.locked(p, write and not dry_run):
        kb = load(p)
        yield kb
        if write and not dry_run and kb.changed:
            save(p, kb, message)


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


def configure_from_environment() -> None:
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
    )


def main() -> int:
    args = build_parser().parse_args()
    if not os.environ.get("BOSUN_CONFIG_DIR"):
        print("kg: BOSUN_CONFIG_DIR is not set; run it as `bosun kg`", file=sys.stderr)
        return 2
    handler: Handler = args.func
    try:
        configure_from_environment()
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
    sys.exit(main())
