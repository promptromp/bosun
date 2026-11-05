"""The Knowledge view's only I/O (design: docs/design/console.md, section 6): reading the knowledge
base under $BOSUN_CONFIG_DIR/knowledge/ and the batch `kg batch` saved, and saving a review
decision, the console's one write.

- Reads go through knowledge.load (shared lock, as `kg lookup` does; the journal's rebuild where
  the files are behind it, in memory) with identifiers left out: they read entities.jsonl,
  facts.jsonl and schema.json (and the journal), never identifiers.jsonl, forgotten.jsonl or
  secret.key (restricted). A knowledge base that does not exist reads as none (nothing is
  created); one that cannot be read says why.
- A decision goes through lib/knowledge.py's own `opened` (the exclusive lock, the save and the
  commit in the knowledge directory's git history) and km.review, as `kg batch apply` does. It is
  checked against today's batch under that same lock. A save the update gate refuses
  (lib/update_gate.py: bosun was updated since the console started, or an update was cut short) is
  a 409 that asks for a restart (`bosun console`): the console runs the code it started with.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_kg_views as views
import console_knowledge as ck
import knowledge
import knowledge_model as km
import knowledge_review as kr
import knowledge_schema as ks
import knowledge_store as store
import update_gate
from console_data import MISSING_STAMP, Params, QueryError, file_stamp
from knowledge_model import KB


KNOWLEDGE_SUBDIR = "knowledge"
REVIEW_COMMAND = "console review"  # what the knowledge base's journal records as a decision's command
READ_ERRORS = (store.StoreError, ks.SchemaError, km.KnowledgeError, OSError, KeyError, TypeError, ValueError)


def int_value(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name, "")
    try:
        return int(value) if value.strip() else default
    except ValueError as exc:
        raise km.KnowledgeError(f"{name} must be a whole number, not {value!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Where the knowledge base is, the batch limits, and the state folder that keeps this Mac's
    replica id for the journal (passed in by `bosun console`)."""

    root: Path | None = None
    git: bool = True
    batch_cap: int = kr.DEFAULT_BATCH_CAP
    batch_facts: int = kr.DEFAULT_BATCH_FACTS
    group_facts: int = kr.DEFAULT_GROUP_FACTS
    state_dir: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """The settings `bosun kg` uses; KnowledgeError for a limit that is not a number."""
        config = env.get("BOSUN_CONFIG_DIR", "")
        settings = cls(
            root=Path(config).expanduser() / KNOWLEDGE_SUBDIR if config else None,
            state_dir=env.get("BOSUN_STATE_DIR", ""),
            git=env.get("BOSUN_KNOWLEDGE_GIT", "1") == "1",
            batch_cap=int_value(env, "BOSUN_KNOWLEDGE_BATCH_CAP", kr.DEFAULT_BATCH_CAP),
            batch_facts=int_value(env, "BOSUN_KNOWLEDGE_BATCH_FACTS", kr.DEFAULT_BATCH_FACTS),
            group_facts=int_value(env, "BOSUN_KNOWLEDGE_GROUP_FACTS", kr.DEFAULT_GROUP_FACTS),
        )
        settings.limits  # noqa: B018  # checked at start: impossible limits fail loudly
        return settings

    @property
    def limits(self) -> kr.Limits:
        return kr.Limits(self.batch_cap, self.batch_facts, self.group_facts)

    @property
    def exists(self) -> bool:
        return self.root is not None and self.root.is_dir()


def read_kb(root: Path, now: datetime) -> KB:
    """The entities and facts (no identifiers), under the shared lock, as `knowledge.load` makes
    them: the journal's rebuild where the files are behind it."""
    return knowledge.read_shared(store.Paths(root), now)


def loaded(settings: Settings, now: datetime) -> tuple[KB | None, str | None]:
    """The knowledge base, or None and why (None: there is none)."""
    if settings.root is None or not settings.exists:
        return None, None
    try:
        return read_kb(settings.root, now), None
    except READ_ERRORS as exc:
        return None, f"the knowledge base could not be read: {exc}"


def saved_batch(settings: Settings) -> Any:
    """The batch `kg batch` last printed (batch.json), or None."""
    if settings.root is None:
        return None
    try:
        return json.loads(store.Paths(settings.root).batch.read_text())
    except (OSError, ValueError):
        return None


def zone(now: datetime) -> tzinfo:
    """The local time zone: "today's" batch is today's here."""
    return now.astimezone().tzinfo or UTC


def batch(settings: Settings, kb: KB, ledger: Mapping[str, Any]) -> dict[str, Any]:
    return ck.review_model(kb, saved_batch(settings), settings.limits, ledger, zone(kb.now))


# ---- the models ---------------------------------------------------------------------------------


def overview(settings: Settings, ledger: Mapping[str, Any], params: Params, now: datetime) -> dict[str, Any]:
    search = ck.Search.parse(params)  # a bad query is refused before anything is read
    kb, error = loaded(settings, now)
    if kb is None:
        return ck.empty_overview(error)
    return ck.overview_model(kb, ledger, search, batch(settings, kb, ledger))


def entity(settings: Settings, key: str, ledger: Mapping[str, Any], now: datetime) -> dict[str, Any] | None:
    """An entity's page by its key; None (404) for a key no entity has."""
    kb, _ = loaded(settings, now)
    entity_id = ck.keys(kb).get(key) if kb is not None else None
    if kb is None or entity_id is None:
        return None
    return ck.entity_model(kb, entity_id, ledger)


def review(settings: Settings, ledger: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    kb, error = loaded(settings, now)
    if kb is None:
        return ck.empty_review(error)
    return batch(settings, kb, ledger)


def briefing(settings: Settings, now: datetime) -> dict[str, Any]:
    """The Briefing: headline, this week's timeline, the most connected (no review: the page
    reads that from review(), with its CSRF token)."""
    kb, error = loaded(settings, now)
    return views.empty_briefing(error) if kb is None else views.briefing_model(kb, zone(kb.now))


def growth(settings: Settings, now: datetime) -> dict[str, Any] | None:
    """What the Impact view's learning panel counts: each shown fact's recorded_at and status, and
    the entity count; None when there is no knowledge base, an error when it cannot be read."""
    kb, error = loaded(settings, now)
    if kb is None:
        return {"error": error} if error else None
    facts = [
        {"recorded_at": f.get("recorded_at"), "status": ck.status(kb, f)}
        for f in kb.facts.values()
        if ck.visible(kb, f)
    ]
    return {"facts": facts, "entities": len(kb.entities)}


def constellation(settings: Settings, now: datetime) -> dict[str, Any]:
    kb, error = loaded(settings, now)
    return views.empty_constellation(error) if kb is None else views.constellation_model(kb, zone(kb.now))


# ---- the one write ------------------------------------------------------------------------------


def parse_body(body: bytes) -> ck.Decision:
    try:
        data = json.loads(body.decode())
    except (UnicodeDecodeError, ValueError) as exc:
        raise QueryError("the body must be a JSON decision") from exc
    return ck.Decision.parse(data)


def decide(settings: Settings, body: bytes, ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a review decision (POST /api/knowledge/review) to today's batch and save it, as `kg
    batch apply` does. QueryError: a malformed decision; ck.ReviewError: refused (no knowledge
    base, a fact not waiting in today's batch) or failed."""
    decision = parse_body(body)
    if settings.root is None or not settings.exists:
        raise ck.ReviewError(404, "there is no knowledge base")
    knowledge.configure(
        settings.root,
        git=settings.git,
        batch_cap=settings.batch_cap,
        batch_facts=settings.batch_facts,
        group_facts=settings.group_facts,
        state_dir=settings.state_dir,
        command=REVIEW_COMMAND,
    )
    message = f"console review: {decision.action} {' '.join(decision.facts)}"
    try:
        with knowledge.opened(True, message) as kb:
            saved = saved_batch(settings)
            shown = ck.review_model(kb, saved, settings.limits, ledger, zone(kb.now))
            result = ck.apply_decision(kb, decision, ck.batch_fact_ids(shown))
            answered = shown["summary"]["source"] == ck.SAVED and ck.batch_answered(kb, saved)
            if answered:
                knowledge.mark_answered(saved, kb)  # as `kg batch apply` does: the CLI agrees
    except update_gate.WriteRefused as exc:
        raise ck.ReviewError(409, updated_refusal(exc)) from exc
    except (store.StoreError, ks.SchemaError, km.KnowledgeError, OSError) as exc:
        raise ck.ReviewError(500, f"the knowledge base could not be updated: {exc}") from exc
    return {**result, "batch_answered": answered, "warnings": list(knowledge.SETTINGS.warnings)}


def updated_refusal(refused: update_gate.WriteRefused) -> str:
    """What a decision the update gate refused says: nothing was saved, and what to do."""
    if isinstance(refused, update_gate.UpdateBusy):
        return f"not saved: {refused}; try again in a moment"
    if isinstance(refused, update_gate.FormatRefused):
        return (
            f"not saved: the {refused.store} is format {refused.found} and this console writes format "
            f"{refused.mine}, as bosun was updated since it started; restart it (bosun console)"
        )
    return (
        f"not saved: {refused}. The next bosun command that writes puts it back; then restart the "
        "console (bosun console)"
    )


# ---- the change stamp ---------------------------------------------------------------------------

STAMPED = ("entities", "facts", "batch")  # what the Knowledge views show changes with these files


def version(settings: Settings) -> dict[str, Any]:
    """The knowledge base's part of /api/version: a stamp from the inode, mtime and size of
    entities.jsonl, facts.jsonl and batch.json (stat only: no read, no lock), so the page notices
    a change cheaply."""
    if settings.root is None or not settings.exists:
        return {"exists": False, "stamp": MISSING_STAMP}
    paths = store.Paths(settings.root)
    parts = []
    for name in STAMPED:
        try:
            parts.append(file_stamp(getattr(paths, name).stat()))
        except OSError:
            parts.append(MISSING_STAMP)
    return {"exists": True, "stamp": "/".join(parts)}
