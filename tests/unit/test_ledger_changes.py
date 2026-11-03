"""Unit tests for lib/ledger_changes.py: the field table and the diff of a ledger write, and that
nothing of mine (my words, a call's context) is in an edit that may leave the Mac. Fictional data
(the acme fixture ledger and placeholders)."""

from __future__ import annotations

import copy
import json
import sys
import unittest
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
from change_log import Edit, canonical
from ledger_changes import (
    LOCAL,
    PRIVATE,
    SEALED,
    SET,
    classify,
    diff,
    drop_entry,
    list_edits,
    strip_local,
)
from test_approvals import OTHER_PR, PR, QUOTE as APPROVING_WORDS, run
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
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaf_paths((*path, key), child)
    else:
        yield path


def fixture_paths(data: dict[str, Any]) -> Iterator[tuple[str, ...]]:
    """Every leaf of the items; the entries of sources and runs (each a whole value); said."""
    yield from leaf_paths(("items",), data["items"])
    for name in ("sources", "runs"):
        yield from ((name, key) for key in data[name])
    yield ("said",)


def masked(path: tuple[str, ...]) -> str:
    """A path with its item id or map key as *, to name a field once."""
    return ".".join(("*" if index == 1 and len(path) > 1 else part) for index, part in enumerate(path))


class TestFieldTable(unittest.TestCase):
    def test_every_fixture_field_is_classified(self) -> None:
        data = json.loads(FIXTURE.read_text())
        paths = list(fixture_paths(data))
        self.assertGreater(len(paths), 100)
        unclassified = sorted(
            {
                masked(path)
                for path in paths
                if not any(row is classify(path) for row in ledger_changes.FIELDS)
            }
        )
        self.assertEqual(unclassified, [])

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
        self.assertEqual(classify(("items", ITEM, "proposal", "action")).pattern[-1], "*")

    def test_free_text_and_names_are_sealed(self) -> None:
        paths = [
            "title",
            "url",
            "proposal.spec",
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
            "proposal.action",
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
        before = a_ledger(an_item(fields={"repo": "acme/exporter", "extra": {"a": 1}}))
        edits = changed(before, lambda data: data["items"][ITEM]["fields"].update(extra="flat"))
        self.assertEqual(edits, [a_set(["fields", "extra"], "flat", {"a": 1})])

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


WINDOW = "exporter"
SAID = "keep the zebra totals per customer"
TOOL_USE_ID = "toolu_01"  # what PostToolUseTestCase.post gives the call


def edit_text(edit: Edit) -> str:
    return canonical({"op": edit.op, "target": edit.target, "body": edit.body}).decode()


class NothingOfMineLeavesTest(PostToolUseTestCase):
    """Real commands, the prompt hook and the guard on a scratch ledger: every edit that may leave
    the Mac (not local) is free of my words (an approval's quote, what I typed in a window) and of
    a call's context (session, transcript, tool_use_id). A task's `via: user: ...` is sealed and a
    link's via private by decision, so it is not checked here."""

    def setUp(self) -> None:
        super().setUp()
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

    def work(self) -> None:
        """A delegation, a prompt I typed in its window, a task from my words, an approved call
        that succeeded and one that failed."""
        run("record-delegation", PR, "--window", WINDOW, "--task", "fix-my-pr", "--ask", "fix the footer")
        hook.record_prompt(
            WINDOW, {"prompt": SAID, "session_id": SESSION}, {"BOSUN_STATE_DIR": str(self.state)}
        )
        run("spawn", PR, "--title", "Footer totals", "--ask", "add the totals", "--via", "user: totals too")
        self.post(self.use())
        run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "Credit notes")
        self.post(self.use(OTHER_PR, OTHER_COMMAND), approvals.POST_TOOL_USE_FAILURE)

    def edits(self) -> list[Edit]:
        """The edits of every write, and of the genesis of the ledger they made."""
        final = json.loads(ledger.STORE.ledger_file.read_text())
        return [edit for before, after in self.writes for edit in diff(before, after)] + diff({}, final)

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
