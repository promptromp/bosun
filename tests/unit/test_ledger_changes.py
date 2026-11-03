"""Unit tests for lib/ledger_changes.py: the field table and the diff of a ledger write, that
nothing of mine (my words, a call's context) is in an edit that may leave the Mac, and the rebuild
of a journal with the conflicts of two Macs' changes. Fictional data (the acme fixture ledger and
placeholders)."""

from __future__ import annotations

import copy
import dataclasses
import json
import random
import sys
import tempfile
import unittest
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import approvals
import hook
import ledger
import ledger_changes
import lineage
from change_log import Change, Edit, canonical
from journal import Journal
from ledger_changes import (
    EMPTY,
    LOCAL,
    PRIVATE,
    SEALED,
    SET,
    classify,
    diff,
    drop_entry,
    list_edits,
    rebuild,
    state_hash,
    strip_local,
)
from test_approvals import (
    CHANNEL,
    DM,
    OTHER_PR,
    PR,
    PR_URL,
    QUOTE as APPROVING_WORDS,
    REVIEW_REQUEST,
    REVIEWER,
    SEND,
    SENT_REF,
    TEXT,
    THREAD,
    run,
)
from test_post_tool_use import OTHER_COMMAND, SESSION, PostToolUseTestCase


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "console" / "acme-ledger.json"
T0 = "2026-10-20T09:00:00+00:00"
T1 = "2026-10-20T10:00:00+00:00"
T2 = "2026-10-20T11:00:00+00:00"
ITEM = "review:acme/exporter#9"
TASK = "task:2026-10-20/7"
MENTION = "slack:C0000000001/1700000001.000001"
WS_OLD, WS_NEW = "ws:invoices", "ws:exports"
QUOTE = "yes, send it"


def an_item(item_id: str = ITEM, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": item_id,
        "kind": "review-request",
        "state": "requested",
        "title": "Export totals per customer",
        "url": "https://github.com/acme/exporter/pull/9",
        "fields": {"repo": "acme/exporter"},
        "proposal": None,
        "created_at": T0,
        "updated_at": T0,
        "history": [{"at": T0, "event": "created", "state": "requested"}],
    }
    item.update(extra)
    return item


def a_ledger(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "items": {item["id"]: item for item in items},
        "sources": {},
        "runs": {},
        "said": [],
    }


def changed(before: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> list[Edit]:
    """The diff of <before> and a copy of it that <change> edited."""
    after = copy.deepcopy(before)
    change(after)
    return diff(before, after)


def a_proposal(**extra: Any) -> dict[str, Any]:
    spec = {"kind": "slack", "channel": "C0000000001", "text": "The export is fixed."}
    return {"action": "post the fix in the thread", "proposed_at": T0, "spec": spec, **extra}


def add_note(item: dict[str, Any], entry: dict[str, Any]) -> None:
    """What `ledger.record` does to the history: append, then trim."""
    item["history"].append(entry)
    ledger_changes.trim_history(item)


def a_set(path: list[str], value: Any, prev: Any, local: bool = False) -> Edit:
    return Edit("item.set", ITEM, {"path": path, "value": value, "prev": prev}, local)


def leaf_paths(path: tuple[str, ...], value: Any) -> Iterator[tuple[str, ...]]:
    """The paths diff compares as one value: it looks into a dict only where the table names its
    keys (a container), so a dict under a key nobody named is one unclassified value."""
    if ledger_changes.is_container(path, value):
        for key, child in value.items():
            yield from leaf_paths((*path, key), child)
    else:
        yield path


def ledger_paths(data: dict[str, Any]) -> Iterator[tuple[str, ...]]:
    for name in ("items", "sources", "runs", "said"):
        yield from leaf_paths((name,), data.get(name))


def masked(path: tuple[str, ...]) -> str:
    """A path with its item id or map key as *, to name a field once."""
    return ".".join(("*" if index == 1 and len(path) > 1 else part) for index, part in enumerate(path))


def unclassified(data: dict[str, Any]) -> list[str]:
    """The fields of <data> with no row of their own (they fall back to local)."""
    rows = ledger_changes.FIELDS
    return sorted(
        {masked(path) for path in ledger_paths(data) if not any(row is classify(path) for row in rows)}
    )


class TestFieldTable(unittest.TestCase):
    def test_every_fixture_field_is_classified(self) -> None:
        data = json.loads(FIXTURE.read_text())
        self.assertGreater(len(list(ledger_paths(data))), 100)
        self.assertEqual(unclassified(data), [])

    def test_no_wildcard_below_an_item(self) -> None:
        wild = [row.pattern for row in ledger_changes.FIELDS if "*" in row.pattern[2:]]
        self.assertEqual(wild, [])
        patterns = [row.pattern for row in ledger_changes.FIELDS]
        self.assertEqual(len(patterns), len(set(patterns)))  # each field has one row

    def test_unknown_keys_in_a_container_are_local(self) -> None:
        paths = [
            "fields.text",
            "fields.vendor_contact",
            "delegation.context",
            "report.extra",
            "proposal.reason",
            "proposal.approval.cwd",
            "proposal.auto_approved.prompt",
            "proposal.approval.failed.stderr",
        ]
        classes = {path: classify(("items", ITEM, *path.split("."))).klass for path in paths}
        self.assertEqual({path for path, klass in classes.items() if klass != LOCAL}, set())

    def test_a_container_is_classified_like_its_whole_value(self) -> None:
        containers = [
            (),
            ("fields",),
            ("delegation",),
            ("report",),
            ("proposal",),
            ("proposal", "approval"),
            ("proposal", "auto_approved"),
            ("proposal", "approval", "failed"),
            ("proposal", "auto_approved", "carried_out"),
        ]
        for inner in containers:
            path = ("items", ITEM, *inner)
            self.assertTrue(ledger_changes.is_container(path, {}), path)
            self.assertEqual(classify(path).klass, PRIVATE, path)

    def test_the_header_and_the_call_context_are_ledgers_and_approvals(self) -> None:
        self.assertEqual(ledger_changes.TASK_HEADER, ledger.TASK_HEADER_FIELDS)
        context = tuple(field.name for field in dataclasses.fields(approvals.CallContext))
        self.assertEqual(ledger_changes.CALL_CONTEXT, context)
        self.assertIn(ledger.APPROVAL_USED, ledger_changes.LOCAL_HISTORY_EVENTS)
        task_fields = {name: classify(("items", TASK, "fields", name)) for name in ledger.TASK_FIELDS}
        self.assertEqual(
            {name for name, rule in task_fields.items() if rule not in ledger_changes.FIELDS}, set()
        )

    def test_unknown_paths_are_local(self) -> None:
        rule = classify(("items", ITEM, "mystery"))
        self.assertEqual((rule.rule, rule.klass, rule.important), (SET, LOCAL, False))
        self.assertEqual(classify(("journal",)).klass, LOCAL)

    def test_important_fields(self) -> None:
        important = {
            path: classify(("items", ITEM, *path.split("."))).important
            for path in (
                "state",
                "proposal",
                "proposal.approval",
                "proposal.proposed_at",
                "proposal.approval.used_at",
                "fields.key",
                "delegation",
                "report",
                "proposal.action",
                "fields.repo",
            )
        }
        self.assertEqual([path for path, yes in important.items() if yes], list(important)[:6])

    def test_a_container_and_its_fields_have_their_own_rows(self) -> None:
        self.assertEqual(classify(("items", ITEM, "proposal", "approval")).pattern[-1], "approval")
        self.assertEqual(classify(("items", ITEM, "proposal", "approval", "quote")).klass, LOCAL)
        self.assertEqual(classify(("items", ITEM, "proposal", "approval", "hash")).klass, PRIVATE)
        self.assertEqual(classify(("items", ITEM, "proposal", "action")).pattern[-1], "action")

    def test_free_text_and_names_are_sealed(self) -> None:
        paths = [
            "title",
            "url",
            "proposal.action",
            "proposal.spec",
            "fields.review_url",
            "fields.aliases",
            "report.summary",
            "report.open",
            "report.results",
            "proposal.approval.failed.reason",
            "proposal.auto_approved.failed.reason",
            *(f"delegation.{name}" for name in ("ask", "requester", "details", "link")),
            *(
                f"fields.{name}"
                for name in ("subject", "to", "requester", "assignee", "answer", "details", "ask")
            ),
            *(f"fields.{name}" for name in ("done_when", "via", "options", "reply_ref", "request_permalink")),
        ]
        classes = {path: classify(("items", ITEM, *path.split("."))).klass for path in paths}
        self.assertEqual({path for path, klass in classes.items() if klass != SEALED}, set())
        self.assertEqual(
            classify(("sources", "https://acme.slack.com/archives/C0000000001/p1")).klass, SEALED
        )

    def test_ids_times_and_kinds_stay_private(self) -> None:
        paths = [
            "id",
            "kind",
            "created_at",
            "fields.repo",
            "fields.parent",
            "fields.key",
            "fields.purpose",
            "fields.reviewed_sha",
            "fields.nudge_count",
            "proposal.proposed_at",
            "proposal.approval.hash",
            "proposal.approval.failed.at",
            "proposal.auto_approved.class",
            "proposal.auto_approved.carried_out.tool",
            "delegation.window",
            "delegation.task",
            "report.ci",
            "report.window",
        ]
        classes = {path: classify(("items", ITEM, *path.split("."))).klass for path in paths}
        self.assertEqual({path for path, klass in classes.items() if klass != PRIVATE}, set())
        self.assertEqual(classify(("runs", "morning")).klass, PRIVATE)

    def test_the_call_context_is_local_wherever_it_sits(self) -> None:
        places = [
            "proposal.approval",
            "proposal.auto_approved",
            "proposal.approval.failed",
            "proposal.approval.carried_out",
            "proposal.auto_approved.failed",
            "proposal.auto_approved.carried_out",
        ]
        classes = {
            f"{place}.{name}": classify(("items", ITEM, *place.split("."), name)).klass
            for place in places
            for name in ("session", "tool_use_id", "transcript")
        }
        self.assertEqual({path for path, klass in classes.items() if klass != LOCAL}, set())

    def test_history_that_quotes_me_is_local(self) -> None:
        local = [
            {"at": T0, "event": "approved", "note": QUOTE},
            {"at": T0, "event": "approval-used", "note": QUOTE, "tool": "Bash"},
            {"at": T0, "event": "user-said", "note": "go on", "window": "exporter"},
            {"at": T0, "event": "approval-failed", "note": "exit 1", "tool_use_id": "tu-1"},
        ]
        others = [{"at": T0, "event": "note", "note": "checked"}, {"at": T0, "event": "transition"}, "odd"]
        self.assertEqual([ledger_changes.is_local_entry(entry) for entry in local], [True] * 4)
        self.assertEqual([ledger_changes.is_local_entry(entry) for entry in others], [False] * 3)

    def test_strip_local_leaves_the_rest(self) -> None:
        approval = {"at": T1, "quote": QUOTE, "hash": "h1", "session": "s-0001", "transcript": "/Users/me/t"}
        proposal = a_proposal(approval=approval)
        stripped = strip_local(("items", ITEM, "proposal"), proposal)
        self.assertEqual(stripped, a_proposal(approval={"at": T1, "hash": "h1"}))
        self.assertEqual(proposal["approval"]["quote"], QUOTE)
        self.assertEqual(strip_local(("items", ITEM, "proposal"), None), None)


class TestDiff(unittest.TestCase):
    def test_no_change_no_edits(self) -> None:
        data = json.loads(FIXTURE.read_text())
        self.assertEqual(diff(data, copy.deepcopy(data)), [])

    def test_a_new_item_is_created_without_its_local_leaves(self) -> None:
        approval = {"at": T1, "quote": QUOTE, "hash": "h1"}
        new = an_item(proposal=a_proposal(approval=approval), mystery="?")
        edits = diff(a_ledger(), a_ledger(new))
        bare = an_item(proposal=a_proposal(approval={"at": T1, "hash": "h1"}))
        self.assertEqual(
            edits,
            [
                Edit("item.create", ITEM, {"item": bare}, False),
                a_set(["mystery"], "?", None, local=True),
                a_set(["proposal", "approval", "quote"], QUOTE, None, local=True),
            ],
        )

    def test_a_field_change_records_its_prev(self) -> None:
        before = a_ledger(an_item(fields={"repo": "a"}))
        edits = changed(before, lambda data: data["items"][ITEM]["fields"].update(repo="b"))
        self.assertEqual(edits, [a_set(["fields", "repo"], "b", "a")])

    def test_a_state_change_records_from_and_to(self) -> None:
        entry = {"at": T1, "event": "transition", "from": "requested", "to": "delegated"}

        def move(data: dict[str, Any]) -> None:
            item = data["items"][ITEM]
            item.update(state="delegated", updated_at=T1)
            item["history"].append(entry)

        self.assertEqual(
            changed(a_ledger(an_item()), move),
            [
                a_set(["updated_at"], T1, T0),
                Edit("item.state", ITEM, {"from": "requested", "to": "delegated"}, False),
                Edit("item.append", ITEM, {"path": ["history"], "entry": entry}, False),
            ],
        )

    def test_each_transition_of_a_write_is_a_state_change(self) -> None:
        """A one-way message is sent, then applied, in one write: two state changes, each one the
        kind allows, as the history records them."""
        steps = [("proposed", "sent"), ("sent", "applied")]
        notes = [{"at": T1, "event": "transition", "from": was, "to": to} for was, to in steps]
        moved = {"at": T1, "event": "moved", "from": "exporter", "to": "exporter-2"}

        def send(data: dict[str, Any]) -> None:
            data["items"][ITEM].update(state="applied")
            data["items"][ITEM]["history"] += [notes[0], moved, notes[1]]

        edits = changed(a_ledger(an_item(state="proposed")), send)
        states = [edit.body for edit in edits if edit.op == "item.state"]
        self.assertEqual(states, [{"from": was, "to": to} for was, to in steps])
        # history that does not lead from the old state to the new one: the one change
        edits = changed(a_ledger(an_item(state="draft")), send)
        states = [edit.body for edit in edits if edit.op == "item.state"]
        self.assertEqual(states, [{"from": "draft", "to": "applied"}])

    def test_history_appends_and_ignores_trimming(self) -> None:
        full = an_item()
        full["history"] += [
            {"at": T0, "event": "note", "note": f"note {n}"} for n in range(ledger_changes.MAX_HISTORY - 1)
        ]
        trimmed = copy.deepcopy(full)
        add_note(trimmed, {"at": T0, "event": "note", "note": "one more"})
        self.assertEqual(trimmed["history"][1]["event"], "history-trimmed")
        entry = {"at": T1, "event": "note", "note": "the newest"}
        for item in (full, trimmed):
            edits = changed(a_ledger(item), lambda data: add_note(data["items"][ITEM], entry))
            self.assertEqual(edits, [Edit("item.append", ITEM, {"path": ["history"], "entry": entry}, False)])

    def test_a_repeated_history_entry_is_appended_again(self) -> None:
        entry = {"at": T1, "event": "note", "note": "go on"}
        before = a_ledger(an_item())
        before["items"][ITEM]["history"].append(entry)
        edits = changed(before, lambda data: data["items"][ITEM]["history"].append(dict(entry)))
        self.assertEqual(edits, [Edit("item.append", ITEM, {"path": ["history"], "entry": entry}, False)])

    def test_links_and_unlinks_are_their_own_edits(self) -> None:
        kept = {"rel": "tracks", "id": "jira:EXP-12", "at": T0}
        old = {"rel": "part_of", "id": WS_OLD, "at": T0}
        new = {"rel": "part_of", "id": WS_NEW, "at": T1, "via": "user: move it"}
        before = a_ledger(an_item(links=[kept, old]))
        edits = changed(before, lambda data: data["items"][ITEM].update(links=[kept, new]))
        self.assertEqual(edits, [Edit("item.unlink", ITEM, old, False), Edit("item.link", ITEM, new, False)])

    def test_a_link_with_a_new_time_is_a_new_link(self) -> None:
        first = {"rel": "relates_to", "id": "jira:EXP-12", "at": T0}
        again = {**first, "at": T1}
        before = a_ledger(an_item(links=[first]))
        edits = changed(before, lambda data: data["items"][ITEM].update(links=[again]))
        self.assertEqual(
            edits, [Edit("item.unlink", ITEM, first, False), Edit("item.link", ITEM, again, False)]
        )

    def test_a_new_item_comes_before_a_link_to_it(self) -> None:
        link = {"rel": "blocked_by", "id": TASK, "at": T1}

        def spawn(data: dict[str, Any]) -> None:
            data["items"][TASK] = an_item(TASK, kind="task", state="proposed")
            data["items"][MENTION]["links"] = [link]

        edits = changed(a_ledger(an_item(MENTION, kind="mention", state="new")), spawn)
        self.assertEqual(
            [(edit.op, edit.target) for edit in edits], [("item.create", TASK), ("item.link", MENTION)]
        )

    def test_an_approval_quote_stays_local(self) -> None:
        before = a_ledger(an_item(proposal=a_proposal()))

        def approve(data: dict[str, Any]) -> None:
            data["items"][ITEM]["proposal"]["approval"] = {"at": T1, "quote": QUOTE, "hash": "h1"}

        self.assertEqual(
            changed(before, approve),
            [
                a_set(["proposal", "approval"], {"at": T1, "hash": "h1"}, None),
                a_set(["proposal", "approval", "quote"], QUOTE, None, local=True),
            ],
        )

    def test_using_an_approval_keeps_the_call_context_local(self) -> None:
        before = a_ledger(an_item(proposal=a_proposal(approval={"at": T1, "quote": QUOTE, "hash": "h1"})))
        used = {"used_at": T2, "call": "c1", "session": "s-0001", "transcript": "/Users/me/t.jsonl"}
        edits = changed(before, lambda data: data["items"][ITEM]["proposal"]["approval"].update(used))
        approval = ["proposal", "approval"]
        self.assertEqual(
            edits,
            [
                a_set([*approval, "call"], "c1", None),
                a_set([*approval, "session"], "s-0001", None, local=True),
                a_set([*approval, "transcript"], "/Users/me/t.jsonl", None, local=True),
                a_set([*approval, "used_at"], T2, None),
            ],
        )

    def test_clearing_a_proposal_keeps_its_local_leaves_out_of_prev(self) -> None:
        approval = {"at": T1, "quote": QUOTE, "hash": "h1", "used_at": T2, "tool_use_id": "tu-1"}
        before = a_ledger(an_item(proposal=a_proposal(approval=approval)))
        edits = changed(before, lambda data: data["items"][ITEM].update(proposal=None))
        prev = a_proposal(approval={"at": T1, "hash": "h1", "used_at": T2})
        self.assertEqual(edits, [a_set(["proposal"], None, prev)])

    def test_a_standing_approval_keeps_its_call_context_local(self) -> None:
        before = a_ledger(an_item(proposal=a_proposal()))
        granted = {"at": T1, "class": "touch", "call": "c1", "session": "s-0001", "transcript": "/Users/me/t"}
        edits = changed(before, lambda data: data["items"][ITEM]["proposal"].update(auto_approved=granted))
        self.assertEqual(
            edits,
            [
                a_set(["proposal", "auto_approved"], {"at": T1, "class": "touch", "call": "c1"}, None),
                a_set(["proposal", "auto_approved", "session"], "s-0001", None, local=True),
                a_set(["proposal", "auto_approved", "transcript"], "/Users/me/t", None, local=True),
            ],
        )

    def test_a_failed_call_keeps_its_tool_use_id_local(self) -> None:
        approval = {"at": T1, "quote": QUOTE, "hash": "h1", "used_at": T2, "call": "c1"}
        before = a_ledger(an_item(proposal=a_proposal(approval=approval)))
        failed = {"at": T2, "reason": "exit 1", "tool": "Bash", "tool_use_id": "tu-1"}
        edits = changed(
            before, lambda data: data["items"][ITEM]["proposal"]["approval"].update(failed=failed)
        )
        where = ["proposal", "approval", "failed"]
        self.assertEqual(
            edits,
            [
                a_set(where, {"at": T2, "reason": "exit 1", "tool": "Bash"}, None),
                a_set([*where, "tool_use_id"], "tu-1", None, local=True),
            ],
        )

    def test_history_that_quotes_me_is_a_local_append(self) -> None:
        said = {"at": T1, "event": "user-said", "note": "go on", "window": "exporter"}
        note = {"at": T1, "event": "note", "note": "checked"}
        edits = changed(a_ledger(an_item()), lambda data: data["items"][ITEM]["history"].extend([said, note]))
        appended = [(edit.body["entry"]["event"], edit.local) for edit in edits]
        self.assertEqual(appended, [("user-said", True), ("note", False)])

    def test_a_new_items_history_keeps_its_order(self) -> None:
        history = [
            {"at": T0, "event": "created", "state": "requested"},
            {"at": T0, "event": "note", "note": "first"},
            {"at": T1, "event": "approved", "note": QUOTE},
            {"at": T1, "event": "note", "note": "second"},
            {"at": T2, "event": "user-said", "note": "go on"},
        ]
        edits = diff(a_ledger(), a_ledger(an_item(history=history)))
        self.assertEqual(edits[0].body["item"]["history"], history[:2])
        appends = [(edit.body["entry"], edit.local) for edit in edits[1:]]
        self.assertEqual(appends, [(history[2], True), (history[3], False), (history[4], True)])
        rebuilt = edits[0].body["item"]["history"] + [entry for entry, _ in appends]
        self.assertEqual(rebuilt, history)
        shared = edits[0].body["item"]["history"] + [entry for entry, local in appends if not local]
        self.assertEqual(shared, [history[0], history[1], history[3]])

    def test_a_removed_leaf_is_unset(self) -> None:
        delegation = {"window": "exporter", "task": "review", "at": T0, "updates": 1, "last_update_at": T1}
        before = a_ledger(an_item(delegation=delegation))

        def move(data: dict[str, Any]) -> None:
            data["items"][ITEM]["delegation"].update(window="exporter-2")
            del data["items"][ITEM]["delegation"]["last_update_at"]

        unset = Edit(
            "item.set",
            ITEM,
            {"path": ["delegation", "last_update_at"], "value": None, "prev": T1, "unset": True},
            False,
        )
        self.assertEqual(
            changed(before, move), [unset, a_set(["delegation", "window"], "exporter-2", "exporter")]
        )

    def test_a_dict_that_appears_is_one_set(self) -> None:
        report = {
            "at": T1,
            "summary": "fixed",
            "results": ["https://github.com/acme/exporter/pull/9"],
            "open": "",
        }
        edits = changed(a_ledger(an_item()), lambda data: data["items"][ITEM].update(report=report))
        self.assertEqual(edits, [a_set(["report"], report, None)])

    def test_a_list_without_a_rule_is_one_set(self) -> None:
        report = {"at": T1, "summary": "fixed", "results": ["one"], "open": ""}
        before = a_ledger(an_item(report=report))
        edits = changed(before, lambda data: data["items"][ITEM]["report"]["results"].append("two"))
        self.assertEqual(edits, [a_set(["report", "results"], ["one", "two"], ["one"])])

    def test_a_dict_that_turns_into_a_value_is_one_set(self) -> None:
        delegation = {"window": "exporter", "task": "review", "at": T0, "updates": 0, "session": "s-1"}
        before = a_ledger(an_item(delegation=delegation))
        edits = changed(before, lambda data: data["items"][ITEM].update(delegation="gone"))
        known = {key: value for key, value in delegation.items() if key != "session"}
        self.assertEqual(edits, [a_set(["delegation"], "gone", known)])

    def test_an_unknown_key_in_a_container_is_a_local_set(self) -> None:
        before = a_ledger(an_item())
        edits = changed(before, lambda data: data["items"][ITEM]["fields"].update(text="the new rule"))
        self.assertEqual(edits, [a_set(["fields", "text"], "the new rule", None, local=True)])
        edits = diff(a_ledger(), a_ledger(an_item(fields={"repo": "acme/exporter", "text": "the new rule"})))
        self.assertEqual(edits[0].body["item"]["fields"], {"repo": "acme/exporter"})
        self.assertEqual(edits[1:], [a_set(["fields", "text"], "the new rule", None, local=True)])

    def test_fields_that_appear_whole_are_private_with_their_local_keys_apart(self) -> None:
        item = an_item()
        del item["fields"]
        edits = changed(
            a_ledger(item), lambda data: data["items"][ITEM].update(fields={"repo": "r", "text": "t"})
        )
        self.assertEqual(
            edits, [a_set(["fields"], {"repo": "r"}, None), a_set(["fields", "text"], "t", None, local=True)]
        )

    def test_messages_keep_their_order(self) -> None:
        messages = [{"ref": f"m{n}", "channel": "slack", "at": T0} for n in range(3)]
        before = a_ledger(an_item(fields={"messages": messages}))
        edits = changed(before, lambda data: data["items"][ITEM]["fields"]["messages"][1].update(at=T1))
        path = ["fields", "messages"]
        self.assertEqual(
            edits,
            [
                Edit("item.drop", ITEM, {"path": path, "entry": messages[1]}, False),
                Edit("item.drop", ITEM, {"path": path, "entry": messages[2]}, False),
                Edit("item.append", ITEM, {"path": path, "entry": {**messages[1], "at": T1}}, False),
                Edit("item.append", ITEM, {"path": path, "entry": messages[2]}, False),
            ],
        )

    def test_unknown_field_is_local(self) -> None:
        edits = changed(a_ledger(an_item()), lambda data: data["items"][ITEM].update(mystery={"a": 1}))
        self.assertEqual(edits, [a_set(["mystery"], {"a": 1}, None, local=True)])

    def test_prune_is_a_delete(self) -> None:
        before = a_ledger(an_item(), an_item(TASK))
        edits = changed(before, lambda data: data["items"].pop(TASK))
        self.assertEqual(edits, [Edit("item.delete", TASK, {}, False)])

    def test_said_is_local(self) -> None:
        old = {"at": T0, "window": "exporter", "text": "first", "items": [ITEM], "also_held": []}
        new = {"at": T1, "window": "exporter", "text": "second", "items": [ITEM], "also_held": []}
        before = a_ledger(an_item())
        before["said"] = [old]
        edits = changed(before, lambda data: data.update(said=[new]))
        self.assertEqual(
            edits,
            [
                Edit("said.drop", "said", {"entry": old}, True),
                Edit("said.append", "said", {"entry": new}, True),
            ],
        )

    def test_sources_and_runs_are_map_edits(self) -> None:
        source = {"classification": "fyi", "item": None, "seen_at": T0}
        before = a_ledger()
        before["sources"] = {"https://acme.slack.com/archives/C0000000001/p1": source}
        before["runs"] = {"morning": T0}

        def scan(data: dict[str, Any]) -> None:
            data["sources"] = {"https://acme.slack.com/archives/C0000000001/p2": source}
            data["runs"].update({"morning": T1, "check-in": T1})

        self.assertEqual(
            changed(before, scan),
            [
                Edit(
                    "map.delete",
                    "sources",
                    {"key": "https://acme.slack.com/archives/C0000000001/p1", "prev": source},
                    False,
                ),
                Edit(
                    "map.put",
                    "sources",
                    {"key": "https://acme.slack.com/archives/C0000000001/p2", "value": source},
                    False,
                ),
                Edit("map.put", "runs", {"key": "check-in", "value": T1}, False),
                Edit("map.put", "runs", {"key": "morning", "value": T1, "prev": T0}, False),
            ],
        )

    def test_only_items_sources_runs_and_said_are_compared(self) -> None:
        edits = changed(a_ledger(), lambda data: data.update(version=2, journal={"r1": 3}))
        self.assertEqual(edits, [])

    def test_genesis_of_the_fixture(self) -> None:
        data = json.loads(FIXTURE.read_text())
        edits = diff({}, data)
        ops = [edit.op for edit in edits]
        self.assertEqual(ops.count("item.create"), len(data["items"]))
        self.assertEqual(ops.count("map.put"), len(data["sources"]) + len(data["runs"]))
        self.assertEqual(ops.count("said.append"), len(data["said"]))
        self.assertEqual(set(ops), {"item.create", "item.append", "map.put", "said.append"})
        self.assertEqual([edit.target for edit in edits if edit.op == "item.create"], sorted(data["items"]))
        # the fixture's user-said entry and what follows it in that history: appends after the create
        appended = [(edit.body["entry"]["event"], edit.local) for edit in edits if edit.op == "item.append"]
        self.assertIn(("user-said", True), appended)
        for edit in edits:
            if edit.op == "item.create":
                history = edit.body["item"]["history"]
                self.assertFalse(any(ledger_changes.is_local_entry(entry) for entry in history))

    def test_edits_do_not_share_the_ledgers_objects(self) -> None:
        after = a_ledger(an_item(fields={"repo": "acme/exporter", "options": ["a"]}))
        edits = diff(a_ledger(), after)
        after["items"][ITEM]["fields"]["options"].append("b")
        self.assertEqual(edits[0].body["item"]["fields"]["options"], ["a"])

    def test_ledger_keeps_its_history_trimming(self) -> None:
        self.assertIs(ledger.trim_history, ledger_changes.trim_history)
        self.assertEqual(ledger.MAX_HISTORY, ledger_changes.MAX_HISTORY)


class TestListEdits(unittest.TestCase):
    def test_drops_then_appends_give_the_new_list(self) -> None:
        a, b, c, d = ({"n": n} for n in range(4))
        cases = [
            ([], [a]),
            ([a, b, c], [b, c, d]),
            ([a, b, c], [a, c]),
            ([a, b, a], [a, b]),
            ([a, b, a], [b, a]),
            ([a, b, a], [a, a, b]),
            ([a, b], [b, a]),
            ([a, b], []),
        ]
        for before, after in cases:
            drops, appends = list_edits(before, after)
            entries = copy.deepcopy(before)
            for entry in drops:
                self.assertTrue(drop_entry(entries, entry))
            self.assertEqual(entries + appends, after, (before, after))

    def test_an_unchanged_start_is_kept(self) -> None:
        a, b, c = ({"n": n} for n in range(3))
        self.assertEqual(list_edits([a, b], [a, b, c]), ([], [c]))
        self.assertEqual(list_edits([a, b, c], [b, c]), ([a], []))

    def test_drop_entry_without_a_match(self) -> None:
        self.assertFalse(drop_entry([{"n": 1}], {"n": 2}))


REPLICA_A, REPLICA_B, REPLICA_C = "a" * 32, "b" * 32, "c" * 32
REVIEW_7 = "review:acme/api#7"
SPAWNED_FROM = "spawned_from"
Write = Callable[[dict[str, Any]], None]


def without_journal(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key != "journal"}


def as_rebuilt(data: dict[str, Any]) -> dict[str, Any]:
    """<data> as the rebuild gives it: every section (a ledger never given `said` has none), no
    journal position."""
    return {**copy.deepcopy(EMPTY), **without_journal(data)}


def committed(journal: Journal, edits: list[Edit], cmd: str = "test") -> list[Change]:
    """<edits> as one transaction: real envelopes, the local ones in the local file."""
    transaction = journal.begin(cmd)
    for edit in edits:
        transaction.add(edit.op, edit.target, edit.body, local=edit.local)
    return transaction.commit()


def journaled(edits: list[Edit]) -> list[Change]:
    with tempfile.TemporaryDirectory() as root:
        return committed(Journal(Path(root), "ledger", REPLICA_A, 1), edits)


def outcome(changes: list[Change]) -> tuple[str, list[tuple[str, str, str, str]]]:
    """What a rebuild gives, to compare: its state hash and its conflicts."""
    result = rebuild(changes, ledger.RULES)
    return state_hash(result.ledger), [dataclasses.astuple(conflict) for conflict in result.conflicts]


class Mac:
    """A replica with its ledger and its own journal; each write is diffed into one transaction at
    the time given, so that two Macs' changes interleave in a known order."""

    def __init__(self, root: Path, replica: str, data: dict[str, Any]) -> None:
        self.now = 0
        self.journal = Journal(root / replica, "ledger", replica, 1, now_ms=lambda: self.now)
        self.ledger = copy.deepcopy(data)

    def write(self, at_ms: int, change: Write) -> list[Change]:
        after = copy.deepcopy(self.ledger)
        change(after)
        self.now = at_ms
        changes = committed(self.journal, diff(self.ledger, after))
        self.ledger = after
        return changes


def made(data: dict[str, Any]) -> Write:
    """The write that makes <data> from an empty ledger: its genesis."""
    return lambda empty: empty.update(copy.deepcopy(data))


def to_state(item_id: str, state: str) -> Write:
    return lambda data: data["items"][item_id].update(state=state)


def linked(source: str, rel: str, target: str, at: str = T1) -> Write:
    """<source>'s links become this one link."""
    return lambda data: data["items"][source].update(links=[{"rel": rel, "id": target, "at": at}])


def used(at: str) -> Write:
    return lambda data: data["items"][REVIEW_7]["proposal"]["approval"].update(used_at=at)


def proposed(proposal: dict[str, Any]) -> Write:
    return lambda data: data["items"][REVIEW_7].update(proposal=proposal)


def area(value: str) -> Write:
    return lambda data: data["items"][REVIEW_7]["fields"].update(area=value)


def pruned(item_id: str) -> Write:
    return lambda data: data["items"].pop(item_id)


def both(*writes: Write) -> Write:
    def write(data: dict[str, Any]) -> None:
        for each in writes:
            each(data)

    return write


def scribble(value: Any) -> None:
    """Edit every dict and list in <value>, all the way down."""
    if isinstance(value, dict):
        for child in list(value.values()):
            scribble(child)
        value["scribbled"] = True
    elif isinstance(value, list):
        for child in list(value):
            scribble(child)
        value.append("scribbled")


def a_task(**extra: Any) -> dict[str, Any]:
    return an_item(TASK, kind="task", state="proposed", **extra)


def a_mention(**extra: Any) -> dict[str, Any]:
    return an_item(MENTION, kind="mention", state="new", **extra)


class TestRebuild(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def two_macs(
        self, base: dict[str, Any], on_a: Write, on_b: Write, a_ms: int = 2000, b_ms: int = 3000
    ) -> tuple[list[Change], list[Change]]:
        """<base> made on Mac A and read by B, then a write on each that the other has not seen:
        A's changes (the base's with them) and B's."""
        root = Path(tempfile.mkdtemp(dir=self.root))
        a = Mac(root, REPLICA_A, EMPTY)
        shared = a.write(1000, made(base))
        b = Mac(root, REPLICA_B, a.ledger)
        return shared + a.write(a_ms, on_a), b.write(b_ms, on_b)

    def either_way(self, ours: list[Change], theirs: list[Change]) -> ledger_changes.Rebuilt:
        """The rebuild of both Macs' changes, after checking it is the same whichever arrives first."""
        self.assertEqual(outcome(ours + theirs), outcome(theirs + ours))
        return rebuild(theirs + ours, ledger.RULES)

    def conflicts(self, result: ledger_changes.Rebuilt) -> list[tuple[str, str]]:
        return [(conflict.change, conflict.kind) for conflict in result.conflicts]

    def test_two_transitions_from_one_state(self) -> None:
        base = a_ledger(an_item(REVIEW_7))
        to_delegated, to_closed = to_state(REVIEW_7, "delegated"), to_state(REVIEW_7, "closed")
        for a_ms, b_ms, first in ((2000, 3000, "delegated"), (3000, 2000, "closed")):
            ours, theirs = self.two_macs(base, to_delegated, to_closed, a_ms, b_ms)
            result = self.either_way(ours, theirs)
            self.assertEqual(result.ledger["items"][REVIEW_7]["state"], first)
            later = theirs[0] if a_ms < b_ms else ours[-1]
            self.assertEqual(self.conflicts(result), [(later["id"], "state")])
            self.assertEqual(result.conflicts[0].target, REVIEW_7)

    def test_a_transition_the_kind_does_not_allow_is_a_conflict(self) -> None:
        a = Mac(self.root, REPLICA_A, EMPTY)
        changes = a.write(1000, made(a_ledger(an_item(REVIEW_7))))
        changes += a.write(2000, to_state(REVIEW_7, "rereview-requested"))
        result = rebuild(changes, ledger.RULES)
        self.assertEqual(self.conflicts(result), [(changes[-1]["id"], "state")])
        self.assertEqual(result.ledger["items"][REVIEW_7]["state"], "requested")

    def test_a_cycle_made_on_two_machines_is_refused(self) -> None:
        base = a_ledger(a_task(), a_mention())
        ours, theirs = self.two_macs(
            base, linked(TASK, SPAWNED_FROM, MENTION), linked(MENTION, SPAWNED_FROM, TASK)
        )
        result = self.either_way(ours, theirs)
        items = result.ledger["items"]
        self.assertEqual(items[TASK]["links"], [{"rel": SPAWNED_FROM, "id": MENTION, "at": T1}])
        self.assertNotIn("links", items[MENTION])
        self.assertEqual(self.conflicts(result), [(theirs[0]["id"], "link")])
        self.assertIn("cycle", result.conflicts[0].detail)

    def test_an_approval_used_twice(self) -> None:
        approval = {"at": T1, "hash": "h1", "quote": QUOTE}
        base = a_ledger(an_item(REVIEW_7, proposal=a_proposal(approval=approval)))
        ours, theirs = self.two_macs(base, used(T1), used(T2))
        result = self.either_way(ours, theirs)
        self.assertEqual(
            result.ledger["items"][REVIEW_7]["proposal"]["approval"], {**approval, "used_at": T1}
        )
        self.assertEqual(self.conflicts(result), [(theirs[0]["id"], "field")])

    def test_two_proposals_in_one_slot(self) -> None:
        mine, other = a_proposal(), a_proposal(action="close the PR")
        ours, theirs = self.two_macs(a_ledger(an_item(REVIEW_7)), proposed(mine), proposed(other))
        result = self.either_way(ours, theirs)
        self.assertEqual(result.ledger["items"][REVIEW_7]["proposal"], mine)
        self.assertEqual(self.conflicts(result), [(theirs[0]["id"], "field")])

    def test_a_field_that_is_not_important_takes_the_last_value(self) -> None:
        ours, theirs = self.two_macs(a_ledger(an_item(REVIEW_7)), area("invoicing"), area("exports"))
        result = self.either_way(ours, theirs)
        self.assertEqual(result.ledger["items"][REVIEW_7]["fields"]["area"], "exports")
        self.assertEqual(result.conflicts, [])

    def test_delete_wins(self) -> None:
        note = {"at": T1, "event": "transition", "from": "requested", "to": "delegated"}
        move = both(
            to_state(REVIEW_7, "delegated"), lambda data: data["items"][REVIEW_7]["history"].append(note)
        )
        ours, theirs = self.two_macs(a_ledger(an_item(REVIEW_7)), pruned(REVIEW_7), move)
        result = self.either_way(ours, theirs)
        self.assertNotIn(REVIEW_7, result.ledger["items"])
        self.assertEqual(self.conflicts(result), [(change["id"], "deleted") for change in theirs])
        # made again later, on a Mac that never had it: still gone
        again = Mac(self.root, REPLICA_C, EMPTY).write(4000, made(a_ledger(an_item(REVIEW_7))))
        result = rebuild(ours + theirs + again, ledger.RULES)
        self.assertNotIn(REVIEW_7, result.ledger["items"])
        self.assertEqual(self.conflicts(result)[-1], (again[0]["id"], "deleted"))

    def test_a_second_delete_and_an_earlier_change_are_no_conflict(self) -> None:
        base = a_ledger(an_item(REVIEW_7))
        ours, theirs = self.two_macs(base, pruned(REVIEW_7), pruned(REVIEW_7))
        self.assertEqual(self.either_way(ours, theirs).conflicts, [])
        ours, theirs = self.two_macs(base, pruned(REVIEW_7), to_state(REVIEW_7, "closed"), 3000, 2000)
        result = self.either_way(ours, theirs)
        self.assertEqual((result.ledger["items"], result.conflicts), ({}, []))

    def test_a_concurrent_unlink_keeps_a_re_add(self) -> None:
        first = {"rel": "relates_to", "id": MENTION, "at": T0}
        base = a_ledger(a_task(links=[first]), a_mention())
        for a_ms, b_ms in ((2000, 3000), (3000, 2000)):
            ours, theirs = self.two_macs(
                base,
                lambda data: data["items"][TASK].update(links=[]),
                linked(TASK, "relates_to", MENTION, T1),
                a_ms,
                b_ms,
            )
            result = self.either_way(ours, theirs)
            self.assertEqual(result.ledger["items"][TASK]["links"], [{**first, "at": T1}])
            self.assertEqual(result.conflicts, [])

    def test_order_of_delivery_does_not_matter(self) -> None:
        approval = {"at": T1, "hash": "h1", "quote": QUOTE}
        review = an_item(REVIEW_7, proposal=a_proposal(approval=approval))
        base = a_ledger(review, a_task(), a_mention(), an_item())
        on_a = both(to_state(REVIEW_7, "delegated"), used(T1), linked(TASK, SPAWNED_FROM, MENTION))
        on_b = both(to_state(REVIEW_7, "closed"), used(T2), linked(MENTION, SPAWNED_FROM, TASK), pruned(ITEM))
        ours, theirs = self.two_macs(base, on_a, on_b)
        late = Mac(self.root, REPLICA_C, base).write(4000, to_state(ITEM, "delegated"))
        changes = ours + theirs + late
        expected = outcome(changes)
        self.assertEqual(sorted(kind for *_, kind, _ in expected[1]), ["deleted", "field", "link", "state"])
        shuffler = random.Random(7)  # noqa: S311  # a fixed shuffle, not cryptography
        for _ in range(20):
            delivered = changes + shuffler.sample(changes, len(changes) // 2)
            shuffler.shuffle(delivered)
            self.assertEqual(outcome(delivered), expected)

    def test_rebuilding_does_not_change_the_changes(self) -> None:
        """The rebuilt ledger shares nothing with the changes: editing every dict and list of it
        leaves them as they were."""
        base = a_ledger(a_task(links=[{"rel": "relates_to", "id": MENTION, "at": T0}]), a_mention())
        base["said"] = [{"at": T0, "window": "exporter", "text": "go on", "items": [TASK]}]

        def add(data: dict[str, Any]) -> None:
            data["items"][TASK]["history"].append({"at": T1, "event": "note", "by": {"mac": "a"}})
            data["items"][TASK]["fields"].update(messages=[{"ref": "m1", "at": T1}], options=["a"])
            data["sources"]["p1"] = {"classification": "fyi", "seen_at": T1}
            data["said"].append({"at": T1, "window": "exporter", "text": "and this", "items": [TASK]})

        on_a = both(linked(TASK, SPAWNED_FROM, MENTION), add)
        ours, theirs = self.two_macs(base, on_a, to_state(TASK, "accepted"))
        changes = ours + theirs
        kept = copy.deepcopy(changes)
        scribble(rebuild(changes, ledger.RULES).ledger)
        self.assertEqual(changes, kept)

    def test_genesis_of_the_fixture_rebuilds_it_exactly(self) -> None:
        data = json.loads(FIXTURE.read_text())
        result = rebuild(journaled(diff(EMPTY, data)), ledger.RULES)
        self.assertEqual(result.conflicts, [])
        self.assertEqual(result.ledger, data)

    def test_history_that_quotes_me_keeps_its_place(self) -> None:
        history = [
            {"at": T0, "event": "created", "state": "requested"},
            {"at": T0, "event": "note", "note": "first"},
            {"at": T1, "event": "approved", "note": QUOTE},
            {"at": T1, "event": "note", "note": "second"},
        ]
        later = [
            {"at": T2, "event": "note", "note": "third"},
            {"at": T2, "event": "user-said", "note": "go on"},
            {"at": T2, "event": "note", "note": "fourth"},
        ]
        start = a_ledger(an_item(history=history))
        end = copy.deepcopy(start)
        end["items"][ITEM]["history"] += later
        journal = Journal(self.root, "ledger", REPLICA_A, 1)
        committed(journal, diff(EMPTY, start))
        committed(journal, diff(start, end))
        files = sorted(path.name for path in self.root.iterdir())
        self.assertEqual(files, [f"{REPLICA_A}.jsonl", "local.jsonl"])
        result = rebuild(journal.changes(), ledger.RULES)
        self.assertEqual(result.ledger["items"][ITEM]["history"], history + later)

    def test_a_trimmed_history_rebuilds_as_written(self) -> None:
        a = Mac(self.root, REPLICA_A, EMPTY)
        changes = a.write(1000, made(a_ledger(an_item())))
        for n in range(25):
            notes = [{"at": T1, "event": "note", "note": f"note {n}.{m}"} for m in range(10)]

            def write(data: dict[str, Any], notes: list[dict[str, Any]] = notes) -> None:
                for note in notes:
                    add_note(data["items"][ITEM], note)

            changes += a.write(2000 + n, write)
        self.assertEqual(a.ledger["items"][ITEM]["history"][1]["event"], "history-trimmed")
        self.assertEqual(rebuild(changes, ledger.RULES).ledger, a.ledger)

    def test_a_removed_key_goes_and_a_drop_of_nothing_is_nothing(self) -> None:
        message = {"ref": "m1", "channel": "slack", "at": T0}
        fields = {"repo": "acme/exporter", "origin": "user: this", "messages": [message]}
        a = Mac(self.root, REPLICA_A, EMPTY)
        changes = a.write(1000, made(a_ledger(an_item(fields=fields))))
        changes += a.write(2000, lambda data: data["items"][ITEM]["fields"].pop("origin"))
        gone = {"path": ["fields", "messages"], "entry": {**message, "ref": "m9"}}
        changes += journaled([Edit("item.drop", ITEM, gone, False)])
        result = rebuild(changes, ledger.RULES)
        self.assertEqual(
            result.ledger["items"][ITEM]["fields"], {"repo": "acme/exporter", "messages": [message]}
        )
        self.assertEqual(result.conflicts, [])

    def test_an_unknown_op_is_kept_as_a_conflict(self) -> None:
        strange = Edit("item.frobnicate", ITEM, {"how": "hard"}, False)
        changes = journaled([*diff(EMPTY, a_ledger(an_item())), strange])
        result = rebuild(changes, ledger.RULES)
        self.assertEqual(self.conflicts(result), [(changes[-1]["id"], "unknown")])
        self.assertEqual(result.ledger, a_ledger(an_item()))

    def test_a_body_this_bosun_cannot_apply_is_a_conflict(self) -> None:
        changes = journaled([*diff(EMPTY, a_ledger(an_item())), Edit("item.set", ITEM, {"value": 1}, False)])
        result = rebuild(changes, ledger.RULES)
        self.assertEqual(self.conflicts(result), [(changes[-1]["id"], "unknown")])
        self.assertEqual(result.ledger, a_ledger(an_item()))

    def test_a_change_for_an_item_never_made_is_a_conflict(self) -> None:
        changes = journaled([Edit("item.state", TASK, {"from": "proposed", "to": "accepted"}, False)])
        result = rebuild(changes, ledger.RULES)
        self.assertEqual(self.conflicts(result), [(changes[0]["id"], "missing")])

    def test_an_item_made_on_two_macs_is_merged(self) -> None:
        mine = an_item(REVIEW_7, fields={"repo": "acme/api"})
        theirs = an_item(
            REVIEW_7,
            state="delegated",
            fields={"repo": "acme/other", "area": "exports"},
            history=[
                {"at": T1, "event": "created", "state": "requested"},
                {"at": T2, "event": "note", "note": "seen on the other Mac"},
            ],
            links=[{"rel": "relates_to", "id": MENTION, "at": T1}],
        )
        changes = Mac(self.root, REPLICA_A, EMPTY).write(1000, made(a_ledger(mine, a_mention())))
        changes += Mac(self.root, REPLICA_B, EMPTY).write(2000, made(a_ledger(theirs)))
        result = rebuild(changes, ledger.RULES)
        merged = result.ledger["items"][REVIEW_7]
        self.assertEqual(merged["state"], "requested")
        self.assertEqual(merged["fields"], {"repo": "acme/api", "area": "exports"})
        self.assertEqual(merged["history"], [mine["history"][0], *theirs["history"]])
        self.assertEqual(merged["links"], theirs["links"])
        self.assertEqual(result.conflicts, [])
        kept = copy.deepcopy(changes)
        scribble(result.ledger)
        self.assertEqual(changes, kept)  # the merge shares nothing with the changes either

    def test_a_merged_history_keeps_both_in_time_order(self) -> None:
        def noted(*times: str) -> list[dict[str, Any]]:
            return [
                {"at": T0, "event": "created"},
                *({"at": at, "event": "note", "note": at} for at in times),
            ]

        changes = Mac(self.root, REPLICA_A, EMPTY).write(1000, made(a_ledger(an_item(history=noted(T1)))))
        changes += Mac(self.root, REPLICA_B, EMPTY).write(
            2000, made(a_ledger(an_item(history=noted(T0, T2))))
        )
        history = rebuild(changes, ledger.RULES).ledger["items"][ITEM]["history"]
        self.assertEqual([entry.get("note") for entry in history], [None, T0, T1, T2])

    def test_a_link_a_merged_item_refuses_is_a_conflict(self) -> None:
        workstreams = [an_item(ws, kind="workstream", state="active") for ws in (WS_OLD, WS_NEW)]
        mine = a_task(links=[{"rel": "part_of", "id": WS_OLD, "at": T0}])
        theirs = a_task(links=[{"rel": "part_of", "id": WS_NEW, "at": T1}], title="Another title")
        changes = Mac(self.root, REPLICA_A, EMPTY).write(1000, made(a_ledger(mine, *workstreams)))
        changes += Mac(self.root, REPLICA_B, EMPTY).write(2000, made(a_ledger(theirs)))
        result = rebuild(changes, ledger.RULES)
        self.assertEqual(result.ledger["items"][TASK]["links"], mine["links"])
        self.assertEqual(result.ledger["items"][TASK]["title"], mine["title"])
        self.assertEqual(self.conflicts(result), [(changes[-1]["id"], "link")])
        self.assertIn("part_of", result.conflicts[0].detail)

    def test_the_state_hash_leaves_out_the_journal_position(self) -> None:
        data = a_ledger(an_item())
        self.assertEqual(state_hash(data), state_hash({**data, "journal": {REPLICA_A: 3}}))
        self.assertNotEqual(state_hash(data), state_hash(a_ledger(an_item(title="Another"))))
        self.assertRegex(state_hash(data), "^[0-9a-f]{64}$")


class TestRules(unittest.TestCase):
    def test_allowed_follows_the_kinds(self) -> None:
        self.assertTrue(ledger.RULES.allowed("review-request", "requested", "delegated"))
        self.assertTrue(ledger.RULES.allowed("review-request", "requested", "dismissed"))
        self.assertFalse(ledger.RULES.allowed("review-request", "requested", "rereview-requested"))
        self.assertFalse(ledger.RULES.allowed("no-such-kind", "requested", "delegated"))

    def test_link_problem_is_lineages_refusal(self) -> None:
        items = a_ledger(a_task(), a_mention())["items"]
        self.assertIsNone(ledger.RULES.link_problem(items, lineage.Link(TASK, SPAWNED_FROM, MENTION)))
        missing = ledger.RULES.link_problem(items, lineage.Link(TASK, SPAWNED_FROM, "slack:C0/1"))
        self.assertIn("no item", missing or "")
        itself = ledger.RULES.link_problem(items, lineage.Link(TASK, SPAWNED_FROM, TASK))
        self.assertIn("itself", itself or "")


WINDOW = "exporter"
SAID = "keep the zebra totals per customer"
TOOL_USE_ID = "toolu_01"  # what PostToolUseTestCase.post gives the call
QUESTION = f"out:{OTHER_PR}/1"
QUESTION_TEXT = "Do credit notes reuse the invoice number sequence?"
REVIEW = "review:acme/exporter#9"
STANDING = {
    "BOSUN_STANDING_CLASSES": "pr-review-traffic pr-housekeeping",
    "BOSUN_STANDING_SLACK_CHANNELS": f"{CHANNEL} {DM}",
    "BOSUN_STANDING_SLACK_MENTIONS": REVIEWER,
    "BOSUN_STANDING_GH_REVIEWERS": "dana-acme",
}


def edit_text(edit: Edit) -> str:
    return canonical({"op": edit.op, "target": edit.target, "body": edit.body}).decode()


class RealWritesTest(PostToolUseTestCase):
    """Real commands, the prompt hook and the guard on a scratch ledger, every write captured:
    every field they write has a row, and every edit that may leave the Mac (not local) is free of
    my words (an approval's quote, what I typed in a window) and of a call's context (session,
    transcript, tool_use_id). A task's `via: user: ...` is sealed and a link's via private by
    decision, so it is not checked here."""

    def setUp(self) -> None:
        super().setUp()
        self.env.update(STANDING)
        self.writes: list[tuple[dict[str, Any], dict[str, Any]]] = []
        loaded: list[dict[str, Any]] = []
        load, save = ledger.load, ledger.save

        def loading() -> dict[str, Any]:
            data = load()
            loaded.append(copy.deepcopy(data))
            return data

        def saving(data: dict[str, Any]) -> None:
            self.writes.append((loaded.pop(), copy.deepcopy(data)))
            save(data)

        environment = {"BOSUN_STATE_DIR": str(self.state), "BOSUN_CONFIG_DIR": str(self.state / "config")}
        for patcher in (
            mock.patch.object(ledger, "load", loading),
            mock.patch.object(ledger, "save", saving),
            mock.patch.dict("os.environ", environment),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def hand_over(self) -> None:
        """My PR back with a window: the task header, an update, a prompt I typed there, a report."""
        run("transition", PR, "changes-requested", "--note", "comments")
        run("transition", PR, "addressing", "--note", "fixing")
        header = ["--ask", "fix the footer", "--link", PR_URL, "--requester", "Dana", "--details", "totals"]
        run("record-delegation", PR, "--window", WINDOW, "--task", "fix-my-pr", *header)
        run("record-update", PR, "--text", "also the VAT line")
        hook.record_prompt(
            WINDOW, {"prompt": SAID, "session_id": SESSION}, {"BOSUN_STATE_DIR": str(self.state)}
        )
        run("report", PR, "--summary", "pushed the fix", "--result", PR_URL, "--ci", "green")

    def review_request(self) -> None:
        """A re-review request under a standing class: let through, carried out, sent, answered."""
        run("reach-out", PR, "--to", "Dana", "--subject", "re-review", "--purpose", "review")
        spec = ["--send", "slack", "--channel", CHANNEL, "--thread", THREAD, "--text", TEXT]
        run("propose", REVIEW_REQUEST, *spec)
        call = self.event(SEND, channel_id=CHANNEL, thread_ts=THREAD, message=TEXT)
        self.assertTrue(self.allowed(call))
        self.post(call)
        run("sent", REVIEW_REQUEST, "--ref", SENT_REF)
        run("replied", REVIEW_REQUEST, "--evidence", f"{SENT_REF}9", "--summary", "looks good now")

    def question(self) -> None:
        """A question I approve in chat, carried out; then an approved command that fails."""
        run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "Credit notes")
        options = ["--option", "reuse", "--option", "own sequence", "--details", "the export needs one"]
        reach = ["--to", "Lena", "--subject", "credit notes?", "--purpose", "decision", *options]
        run("reach-out", OTHER_PR, *reach, "--expect-by", "2026-12-01")
        self.approve(QUESTION, "--send", "slack", "--channel", CHANNEL, "--text", QUESTION_TEXT)
        call = self.event(SEND, channel_id=CHANNEL, message=QUESTION_TEXT)
        self.assertTrue(self.allowed(call))
        self.post(call)
        self.post(self.use(OTHER_PR, OTHER_COMMAND), approvals.POST_TOOL_USE_FAILURE)

    def bookkeeping(self) -> None:
        """A task from my words, a ticket, a workstream, a learning, what chores.md stores, a
        source, a run, a window's own work and a move."""
        task = ["--requester", "Dana", "--repo", "acme/exporter", "--area", "process/invoicing"]
        run(
            "spawn", PR, "--title", "Footer totals", "--ask", "add totals", "--via", "user: totals too", *task
        )
        run(
            "ticket-sync", "jira:EXP-12", "--status", "In Progress", "--assignee", "Lena", "--parent", "EXP-1"
        )
        run("link", PR, "tracks", "jira:EXP-12")
        ws = ["--epic", "EXP-1", "--channel", CHANNEL, "--repo", "acme/exporter", "--alias", "inv"]
        run(
            "ws",
            "add",
            "invoices",
            "--title",
            "Invoices",
            *ws,
            "--started",
            "2026-10-01",
            "--target",
            "2026-12-01",
        )
        learned = ["key=k1", "category=policy", "target=memory", "run=r1"]
        run(
            "upsert",
            "learn:2026-10-21/1",
            "--kind",
            "learning",
            "--title",
            "T",
            *[f"--field={f}" for f in learned],
        )
        chores = [
            "requester=Dana",
            f"request_permalink={SENT_REF}",
            "reviewed_sha=abc1",
            f"reviewed_at={T1}",
        ]
        run("upsert", REVIEW, "--kind", "review-request", *[f"--field={f}" for f in chores])
        mine = [f"review_thread={SENT_REF}", f"last_push_at={T1}", "nudge_count=1", f"last_nudged_at={T1}"]
        run("upsert", PR, *[f"--field={f}" for f in [*mine, f"commented_at={T1}"]])
        run("record-source", f"{SENT_REF}8", "--class", "fyi")
        run("mark-run", "morning")
        run("working", OTHER_PR, "--window", "credit")
        run("record-move", PR, "--window", "exporter-2", "--text", "carry on there")

    def work(self) -> None:
        self.hand_over()
        self.review_request()
        self.question()
        self.post(self.use())
        self.bookkeeping()

    def more_work(self) -> None:
        """The rest of what writes: a message's time corrected, a one-way message (sent, then
        applied, in one write), a move to another workstream, an unlink, a ticket done, then a
        prune of it, of the sources and of what I said, once old."""
        run("sent", REVIEW_REQUEST, "--ref", SENT_REF, "--at", T2)
        run("reach-out", PR, "--to", "Dana", "--subject", "fixed", "--purpose", "fyi")
        (fyi,) = [key for key in json.loads(self.ledger_text())["items"] if key.endswith("/2")]
        run("sent", fyi, "--ref", f"{SENT_REF}7")
        self.assertEqual(json.loads(self.ledger_text())["items"][fyi]["state"], "applied")
        run("ws", "add", "exports", "--title", "Exports")
        run("link", PR, "part_of", WS_OLD)
        run("link", PR, "part_of", WS_NEW, "--move")
        run("unlink", PR, "tracks", "jira:EXP-12")
        run("ticket-sync", "jira:EXP-12", "--status", "Done")
        data = ledger.load()
        data["items"]["jira:EXP-12"]["updated_at"] = T0
        for source in data["sources"].values():
            source["seen_at"] = T0
        for entry in data["said"]:
            entry["at"] = T0
        ledger.save(data)
        run("prune", "--days", "1")

    def edits(self) -> list[Edit]:
        """The edits of every write, and of the genesis of the ledger they made."""
        final = json.loads(ledger.STORE.ledger_file.read_text())
        return [edit for before, after in self.writes for edit in diff(before, after)] + diff({}, final)

    def test_every_field_the_commands_write_has_a_row(self) -> None:
        self.work()
        self.assertGreater(len(self.writes), 30)
        found = sorted({path for _, after in self.writes for path in unclassified(after)})
        self.assertEqual(found, [])

    def test_rebuild_of_diffs_reproduces_the_ledger(self) -> None:
        """Every write diffed into a real journal (genesis first, as a first write does): after
        each, the rebuild is that write's ledger, with no conflict; at the end, ledger.json."""
        self.work()
        self.more_work()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        journal = Journal(Path(tmp.name), "ledger", REPLICA_A, 1)
        written = as_rebuilt(self.writes[0][0])
        committed(journal, diff(EMPTY, written), "genesis")
        ops: Counter[str] = Counter()
        for before, after in self.writes:
            self.assertEqual(as_rebuilt(before), written)  # each write starts where the last ended
            edits = diff(before, after)
            ops.update(edit.op for edit in edits)
            committed(journal, edits)
            result = rebuild(journal.changes(), ledger.RULES)
            self.assertEqual(result.conflicts, [])
            written = as_rebuilt(after)
            self.assertEqual(result.ledger, written)
        final = json.loads(ledger.STORE.ledger_file.read_text())
        self.assertEqual(rebuild(journal.changes(), ledger.RULES).ledger, without_journal(final))
        every = {"item.create", "item.delete", "item.set", "item.state", "item.append", "item.drop"}
        every |= {"item.link", "item.unlink", "map.put", "map.delete", "said.append", "said.drop"}
        self.assertEqual(set(ops), every)  # the work reached every op

    def test_no_edit_that_may_leave_holds_my_words_or_a_calls_context(self) -> None:
        self.work()
        edits = self.edits()
        mine = [APPROVING_WORDS, SAID, SESSION, str(self.transcript), TOOL_USE_ID]
        kept = "".join(edit_text(edit) for edit in edits if edit.local)
        self.assertEqual([text for text in mine if text not in kept], [])  # the work reached them all
        leaving = [edit for edit in edits if not edit.local]
        leaked = [
            (edit.op, edit.target, text) for edit in leaving for text in mine if text in edit_text(edit)
        ]
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
