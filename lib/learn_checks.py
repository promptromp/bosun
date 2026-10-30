"""Learning: cheap, deterministic checks over the ledger, given to the analysis as hints.

They point at things worth a look, not at verdicts: outreach that overlaps other open outreach
(same parent, a shared recipient and similar wording, or the same conversation as one already
sent), `info-ack` outreach still waiting for a confirmation (maybe it should be `fyi`), a
security-sensitive subject addressed to a channel rather than a direct message, an item handed
to two windows at once; and lineage: a PR a report names that has no item or no link to its work,
outreach closed after a delegate proposal for its reply with no task spawned from it, tasks left
proposed for days, and tickets out of step with the work that tracks them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from itertools import combinations
from typing import Any

import ledger
import lineage
import tickets
from similar import containment, words


Item = dict[str, Any]

OPEN_OUTREACH = ("draft", "proposed", "sent", "replied")
WAITING = ("draft", "proposed", "sent")
MIN_REASONS = 2
SIMILAR_WORDS = 0.35  # share of the shorter subject's content words from which two subjects are similar
SHARED_SHOWN = 6
STALE_TASK_DAYS = 3  # a task proposed longer than this still waits for my yes or no
SLACK_TS = re.compile(r"(\d{10})\.?(\d{6})")
SENSITIVE = re.compile(
    r"(?i)\b(api[ _-]?key|password|passwd|secret|token|credential|private key|leak(?:ed|s)?|"
    r"vulnerab\w*|cve-\d+|security)\b"
)
CHANNEL_HINT = re.compile(r"(?:^|[\s(,])#[a-z0-9][\w-]*|\b[CG][A-Z0-9]{8,}\b")
DM_HINT = re.compile(r"(?i)\b(?:dm|direct message|privately)\b|\bD[A-Z0-9]{8,}\b")


def recipients(to: str) -> set[str]:
    """People or teams in a `to` field: split at commas, "and", "&", ";" and "cc"; without
    parenthesised notes and ids; lower case."""
    plain = re.sub(r"\([^)]*\)", " ", to)
    parts = re.split(r",|;|&|\band\b|\bcc\b", plain, flags=re.IGNORECASE)
    return {" ".join(p.split()).lower() for p in parts if len(p.strip()) > 1}


def text_of(item: Item) -> str:
    fields = item.get("fields") or {}
    return " ".join(str(fields.get(k, "")) for k in ("subject", "details", "options", "to"))


def conversations(item: Item) -> set[str]:
    """Slack conversations (thread or message timestamps) the item names or sent messages in."""
    fields = item.get("fields") or {}
    refs = [text_of(item), *(str(m.get("ref", "")) for m in fields.get("messages") or [])]
    return {f"{a}.{b}" for ref in refs for a, b in SLACK_TS.findall(ref)}


def outreach(items: Iterable[Item]) -> list[Item]:
    return [i for i in items if i.get("kind") == "outreach" and i.get("state") in OPEN_OUTREACH]


def created_since(item: Item, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(item.get("created_at"))) >= since
    except ValueError:
        return False


def same_parent(a: Item, b: Item) -> str:
    parent = (a.get("fields") or {}).get("parent")
    return f"same parent {parent}" if parent and parent == (b.get("fields") or {}).get("parent") else ""


def shared_recipients(a: Item, b: Item) -> str:
    to_a, to_b = ((i.get("fields") or {}).get("to", "") for i in (a, b))
    shared = recipients(str(to_a)) & recipients(str(to_b))
    return f"shared recipient {', '.join(sorted(shared))}" if shared else ""


def similar_wording(a: Item, b: Item) -> str:
    wa, wb = (words(str((i.get("fields") or {}).get("subject", ""))) for i in (a, b))
    score = containment(wa, wb)
    shown = ", ".join(sorted(wa & wb)[:SHARED_SHOWN])
    return f"similar subject ({score:.0%} shared: {shown})" if score >= SIMILAR_WORDS else ""


def same_conversation(a: Item, b: Item) -> str:
    shared = conversations(a) & conversations(b)
    return f"same Slack conversation {min(shared)}" if shared else ""


def overlap_reasons(a: Item, b: Item) -> tuple[list[str], bool]:
    """What <a> and <b> share, and whether any of it is telling (wording or conversation)."""
    wording, conversation = similar_wording(a, b), same_conversation(a, b)
    reasons = [same_parent(a, b), shared_recipients(a, b), wording, conversation]
    return [r for r in reasons if r], bool(wording or conversation)


def overlap(a: Item, b: Item) -> str:
    """Why <a> and <b> may be the same ask, or ""."""
    reasons, telling = overlap_reasons(a, b)
    return "; ".join(reasons) if telling and len(reasons) >= MIN_REASONS else ""


def overlap_hints(items: list[Item], since: datetime) -> list[str]:
    """Pairs of open outreach, at least one created in the window, that may be the same ask."""
    hints = []
    for a, b in combinations(sorted(outreach(items), key=lambda i: str(i["id"])), 2):
        if not (created_since(a, since) or created_since(b, since)):
            continue
        why = overlap(a, b)
        if why:
            hints.append(
                f"overlapping outreach (a duplicate, or a follow-up that belongs on the earlier item): "
                f"{a['id']} ({a['state']}) and {b['id']} ({b['state']}): {why}"
            )
    return hints


def waits_without_reply(item: Item) -> str:
    fields = item.get("fields") or {}
    if fields.get("purpose") != "info-ack" or item["state"] not in WAITING:
        return ""
    return (
        f"{item['id']} is an info-ack in {item['state']}: it waits for them to confirm they saw it "
        "and is nudged; if no confirmation is needed it should be purpose fyi (closed once sent)"
    )


def sensitive_in_channel(item: Item) -> str:
    fields = item.get("fields") or {}
    where = f"{fields.get('to', '')} {fields.get('details', '')}"
    match = SENSITIVE.search(f"{fields.get('subject', '')} {fields.get('details', '')}")
    if not match or not CHANNEL_HINT.search(where) or DM_HINT.search(where):
        return ""
    return f"{item['id']} has a security-sensitive subject ({match[0]}) addressed to a channel, not a DM"


def scope_hints(items: list[Item]) -> list[str]:
    """Outreach that waits for a reply it does not expect, or puts a sensitive subject in a channel."""
    found = (check(item) for item in outreach(items) for check in (waits_without_reply, sensitive_in_channel))
    return [hint for hint in found if hint]


def second_handovers(item: Item, since: datetime) -> list[str]:
    hints, window = [], ""
    for entry in item.get("history") or []:
        event, target = entry.get("event"), str(entry.get("window") or "")
        if event == "report":
            window = ""
            continue
        if event not in ("delegated", "moved") or not target:
            continue
        if (
            event == "delegated"
            and window not in ("", target)
            and str(entry.get("at", "")) >= since.isoformat()
        ):
            hints.append(
                f"{item['id']} was delegated to {target} at {local_text(entry['at'])} while its hand-over to "
                f"{window} had no report"
            )
        window = target
    return hints


def local_text(stamp: str) -> str:
    try:
        return f"{datetime.fromisoformat(stamp).astimezone():%a %H:%M}"
    except ValueError:
        return stamp


def handover_hints(items: list[Item], since: datetime) -> list[str]:
    """Items handed to a second window while the first hand-over had no report yet."""
    return [hint for item in items for hint in second_handovers(item, since)]


# ---- lineage: work without a ledger id, tasks left waiting, tickets out of step ----------------


def unlinked_prs(by_id: dict[str, Item], index: lineage.Index, since: datetime) -> list[str]:
    """A PR named in a report since <since> (of work handed over, not a PR or review itself) that
    has no mypr item, or one not linked to the work that opened it."""
    hints = []
    for item in by_id.values():
        if not item.get("report") or str(item["report"].get("at", "")) < since.isoformat():
            continue
        for pr_id, url in ledger.produced_prs(item):
            if pr_id not in by_id:
                hints.append(f"{item['id']} reported PR {url}, which has no ledger item ({pr_id})")
            elif not index.into(pr_id, lineage.PRODUCES) and not index.out(pr_id, lineage.SPAWNED_FROM):
                hints.append(f"{pr_id} came from {item['id']} (its report names it) but is not linked to it")
    return hints


def outreach_closed_since(items: Iterable[Item], since: datetime) -> list[Item]:
    return [
        i
        for i in items
        if i.get("kind") == "outreach"
        and i.get("state") not in OPEN_OUTREACH
        and str(i.get("updated_at", "")) >= since.isoformat()
    ]


def work_without_task(by_id: dict[str, Item], index: lineage.Index, since: datetime) -> list[str]:
    """Outreach closed in the window after a delegate command was proposed for its reply, with no
    task spawned from it: the work it stood for has no ledger id."""
    return [
        f"{item['id']} closed ({item['state']}) after a delegate command was proposed for its reply, "
        "but no task was spawned from it: the work has no ledger id"
        for item in outreach_closed_since(by_id.values(), since)
        if any(ledger.DELEGATE_PROPOSAL in str(h.get("note", "")) for h in item.get("history") or [])
        and not index.into(item["id"], lineage.SPAWNED_FROM)
    ]


def waiting_tasks(by_id: dict[str, Item], now: datetime) -> list[str]:
    """Tasks proposed longer than STALE_TASK_DAYS ago: still waiting for my yes or no."""
    cutoff = (now - timedelta(days=STALE_TASK_DAYS)).isoformat()
    return [
        f"{item['id']} has been proposed since {local_text(str(item['created_at']))}: {item.get('title', '')}"
        for item in by_id.values()
        if item.get("kind") == "task" and item.get("state") == "proposed" and str(item["created_at"]) < cutoff
    ]


def ticket_mismatches(by_id: dict[str, Item], index: lineage.Index) -> list[str]:
    found = []
    for item in by_id.values():
        if item.get("kind") == "ticket":
            work = ledger.linked_work(by_id, index, str(item["id"]))
            found += tickets.mismatch_hints(item, work, ledger.is_open, None)
    return found


def lineage_hints(items: list[Item], since: datetime, now: datetime | None = None) -> list[str]:
    by_id = {str(item["id"]): item for item in items}
    index = lineage.Index.of(by_id)
    return [
        *unlinked_prs(by_id, index, since),
        *work_without_task(by_id, index, since),
        *waiting_tasks(by_id, now or datetime.now(UTC)),
        *ticket_mismatches(by_id, index),
    ]


def ledger_hints(items: list[Item], since: datetime) -> list[str]:
    return [
        *overlap_hints(items, since),
        *scope_hints(items),
        *handover_hints(items, since),
        *lineage_hints(items, since),
    ]
