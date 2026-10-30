"""Unit tests for the console's ledger views (lib/console_ledger.py): the state groups derived from
lib/ledger.py's state machines, list rows, filters, search and sorting, the links out (anchors),
the item page (lineage tree and links, outreach, said, history) and "needs you". Fictional data:
acme's invoice exporter (tests/fixtures/console/acme-ledger.json, built by make-acme-ledger.sh on
2026-10-20, hence the task ids), plus small hand-made ledgers for the edge cases."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import approvals
import console_data
import console_ledger
import ledger
import tickets
from console_data import QueryError
from console_ledger import Query


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "console" / "acme-ledger.json"
DATA: dict[str, Any] = json.loads(FIXTURE.read_text())
NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
TASK = "task:2026-10-20/1"
DUPLICATE = "task:2026-10-20/2"
FOLDED = "task:2026-10-20/3"
RELEASE = "task:2026-10-20/4"
MENTION = "slack:C0000000001/1700000001.000001"
DISMISSED = "slack:C0000000003/1700000003.000003"
SCRIPT_URL = "slack:C0000000004/1700000004.000004"
PR_102 = "mypr:acme/exporter#102"
PR_103 = "mypr:acme/exporter#103"
PR_104 = "mypr:acme/exporter#104"
REVIEW = "review:acme/ledger-sync#57"
TD = "td:000000001"
LEARN = "learn:2026-10-20/1"
JIRA = "jira:EXP-12"
ISSUE = "ghissue:acme/exporter#7"
ASKED_DANA = f"out:{TASK}/1"
ASK_MARCO = f"out:{PR_102}/1"
ASKED_LENA = "out:2026-10-20/1"
FYI = "out:2026-10-20/2"
T0 = "2026-10-01T09:00:00+00:00"


def make(item_id: str, kind: str, state: str, **extra: Any) -> dict[str, Any]:
    """A minimal item as lib/ledger.py stores one."""
    built: dict[str, Any] = {
        "id": item_id,
        "kind": kind,
        "state": state,
        "title": "",
        "url": "",
        "fields": {},
        "proposal": None,
        "created_at": T0,
        "updated_at": T0,
        "history": [],
    }
    return built | extra


def ledger_of(*items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"items": {item["id"]: item for item in items}, "sources": {}, "runs": {}, **extra}


def rows(data: dict[str, Any], **params: str) -> list[dict[str, Any]]:
    query = Query.parse({key: [value] for key, value in params.items()}, NOW)
    return list(console_ledger.items_model(data, query)["rows"])


def ids(found: list[dict[str, Any]]) -> set[str]:
    return {row["id"] for row in found}


def item(item_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    model = console_ledger.item_model(DATA if data is None else data, item_id, NOW)
    assert model is not None
    return model


class GroupsTestCase(unittest.TestCase):
    def test_every_state_of_every_kind_has_a_group(self) -> None:
        view = console_ledger.LedgerView.of(ledger_of())
        for kind, spec in ledger.KINDS.items():
            states = {spec["initial"], *spec["terminal"], *spec["transitions"]}
            states |= {target for targets in spec["transitions"].values() for target in targets}
            for state in states:
                with self.subTest(kind=kind, state=state):
                    group = console_ledger.group_of(make(f"x:{state}", kind, state), view)
                    self.assertIn(group, console_ledger.GROUPS)
                    self.assertEqual(group == console_ledger.CLOSED, state in spec["terminal"])

    def test_waiting_states_are_open_states_of_real_kinds(self) -> None:
        for kind, states in console_ledger.WAITING_STATES.items():
            spec = ledger.KINDS[kind]
            for state in states:
                with self.subTest(kind=kind, state=state):
                    self.assertIn(state, spec["transitions"])
                    self.assertNotIn(state, spec["terminal"])

    def test_needs_me_states_come_from_the_state_machines(self) -> None:
        self.assertEqual(console_ledger.needs_me_states("outreach"), {"proposed"})
        self.assertEqual(console_ledger.needs_me_states("task"), {"proposed"})
        self.assertEqual(console_ledger.needs_me_states("my-pr"), set())
        self.assertEqual(console_ledger.needs_me_states("no-such-kind"), set())

    def test_the_fixtures_groups(self) -> None:
        groups = {row["id"]: row["group"] for row in rows(DATA, group="all")}
        expected = {
            # pending proposals, outreach awaiting approval, mismatches with the tickets
            "needs-me": {RELEASE, LEARN, ASK_MARCO, ISSUE, TASK, PR_104},
            "waiting": {ASKED_DANA, PR_102, PR_103, REVIEW},
            "open": {MENTION, SCRIPT_URL, TD, ASKED_LENA},
            "closed": {DUPLICATE, FOLDED, DISMISSED, FYI, JIRA},
        }
        for group, members in expected.items():
            with self.subTest(group=group):
                self.assertEqual({i for i, g in groups.items() if g == group}, members)

    def test_a_proposal_beats_waiting(self) -> None:
        pr = make(
            "mypr:acme/a#1", "my-pr", "awaiting-review", proposal={"action": "merge it", "proposed_at": T0}
        )
        self.assertEqual(rows(ledger_of(pr))[0]["group"], "needs-me")

    def test_open_outreach_makes_the_parent_wait_and_closed_outreach_does_not(self) -> None:
        parent = make("mypr:acme/a#1", "my-pr", "approved")
        ask = make("out:mypr:acme/a#1/1", "outreach", "draft", fields={"parent": parent["id"], "to": "Dana"})
        waiting = rows(ledger_of(parent, ask), group="waiting")
        self.assertEqual([row["id"] for row in waiting], [parent["id"]])
        self.assertEqual(waiting[0]["waiting_on"], [{"id": ask["id"], "who": "Dana", "state": "draft"}])
        ask["state"] = "applied"
        self.assertEqual(ids(rows(ledger_of(parent, ask), group="open")), {parent["id"]})

    def test_an_open_blocker_makes_an_item_wait(self) -> None:
        blocker = make("mypr:acme/a#1", "my-pr", "approved")
        task = make(
            "task:2026-10-01/1", "task", "accepted", links=[{"rel": "blocked_by", "id": blocker["id"]}]
        )
        found = {row["id"]: row for row in rows(ledger_of(blocker, task))}
        self.assertEqual(found[task["id"]]["group"], "waiting")
        self.assertEqual(
            found[task["id"]]["waiting_on"], [{"id": blocker["id"], "who": "", "state": "approved"}]
        )
        blocker["state"] = "merged"
        self.assertEqual(
            {row["id"]: row["group"] for row in rows(ledger_of(blocker, task))}[task["id"]], "open"
        )

    def test_outreach_waits_on_its_recipient_only_once_sent(self) -> None:
        view = console_ledger.LedgerView.of(DATA)
        self.assertEqual(
            console_ledger.waiting_on(DATA["items"][ASKED_DANA], view),
            [{"id": "", "who": "Dana (finance)", "state": "sent"}],
        )
        self.assertEqual(console_ledger.waiting_on(DATA["items"][ASKED_LENA], view), [])

    def test_a_mismatch_flags_the_side_to_change(self) -> None:
        view = console_ledger.LedgerView.of(DATA)
        self.assertEqual(set(view.mismatches), {JIRA, ISSUE})
        # the done ticket's open work; the open ticket itself
        self.assertEqual(view.flagged, {TASK: [JIRA], PR_104: [JIRA], ISSUE: [ISSUE]})

    def test_an_unknown_kind_is_open_unless_closed(self) -> None:
        later = make("idea:1", "idea", "new")
        self.assertEqual(rows(ledger_of(later))[0]["group"], "open")
        later["state"] = "closed"
        self.assertEqual(rows(ledger_of(later))[0]["group"], "closed")
        self.assertEqual(item("idea:1", ledger_of(later))["hints"], [])


class QueryTestCase(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(Query.parse({}, NOW), Query())
        self.assertEqual(Query.parse({"group": [""], "sort": [""]}, NOW), Query())

    def test_refuses_unknown_values(self) -> None:
        for params in ({"kind": ["bug"]}, {"group": ["mine"]}, {"sort": ["title"]}, {"since": ["yesterday"]}):
            with self.subTest(params=params), self.assertRaises(QueryError):
                Query.parse(params, NOW)

    def test_since(self) -> None:
        self.assertIsNone(console_ledger.parse_since(" ", NOW))
        self.assertEqual(console_ledger.parse_since("90m", NOW), NOW - timedelta(minutes=90))
        self.assertEqual(console_ledger.parse_since("24h", NOW), NOW - timedelta(hours=24))
        self.assertEqual(console_ledger.parse_since("7d", NOW), NOW - timedelta(days=7))
        self.assertEqual(console_ledger.parse_since("2w", NOW), NOW - timedelta(weeks=2))
        self.assertEqual(
            console_ledger.parse_since("2026-10-20T10:00", NOW), datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
        )
        offset = console_ledger.parse_since("2026-10-20T10:00+02:00", NOW)
        self.assertEqual(offset, datetime(2026, 10, 20, 8, 0, tzinfo=UTC))

    def test_since_filters_on_the_update_time(self) -> None:
        old = make("mypr:acme/a#1", "my-pr", "approved")
        new = make("mypr:acme/a#2", "my-pr", "approved", updated_at="2026-10-20T12:00:00+00:00")
        broken = make("mypr:acme/a#3", "my-pr", "approved", updated_at="not a time")
        data = ledger_of(old, new, broken)
        self.assertEqual(ids(rows(data, since="7d")), {new["id"]})
        self.assertEqual(ids(rows(data, since="2026-09-30")), {old["id"], new["id"]})

    def test_kind_window_and_group(self) -> None:
        self.assertEqual(
            ids(rows(DATA, kind="outreach", group="all")), {ASKED_DANA, ASK_MARCO, ASKED_LENA, FYI}
        )
        self.assertEqual(ids(rows(DATA, window="exporter")), {TASK})
        self.assertEqual(ids(rows(DATA, window="nowhere")), set())
        active = {i for i, it in DATA["items"].items() if ledger.is_open(it)}
        self.assertEqual(ids(rows(DATA, group="active")), active)
        self.assertEqual(len(rows(DATA)), len(DATA["items"]))

    def test_person_is_a_requester_or_a_recipient(self) -> None:
        self.assertEqual(ids(rows(DATA, person="priya")), {TASK})
        # the outreach itself, and the item it was sent for
        self.assertEqual(ids(rows(DATA, person="DANA")), {ASKED_DANA, TASK})
        self.assertEqual(ids(rows(DATA, person="omar")), {REVIEW})
        self.assertEqual(ids(rows(DATA, person="nobody")), set())

    def test_search(self) -> None:
        cases = {
            "credit notes": {TASK, DUPLICATE, PR_104, JIRA, MENTION, ASKED_DANA},  # titles, fields, notes
            "EXPORTER#10": {PR_102, PR_103, PR_104, ASK_MARCO, TASK, RELEASE},  # ids, urls, notes
            "onerror": {TASK, DISMISSED},  # a note and a title
            "numbering still open": {TASK},  # the report summary
            "finance owner": {LEARN},  # the pending proposal
            "thursday": {ASKED_LENA, FYI},
            "credit sequence": {TASK, ASKED_DANA},  # every word, anywhere
            "no such words": set(),
        }
        for q, expected in cases.items():
            with self.subTest(q=q):
                self.assertEqual(ids(rows(DATA, q=q, group="all")), expected)

    def test_group_counts_follow_the_other_filters(self) -> None:
        query = Query.parse({"kind": ["outreach"], "group": ["closed"]}, NOW)
        model = console_ledger.items_model(DATA, query)
        self.assertEqual(model["groups"], {"needs-me": 1, "waiting": 1, "open": 1, "closed": 1})
        self.assertEqual(model["count"], 1)
        self.assertEqual(model["total"], len(DATA["items"]))
        self.assertEqual(model["windows"], ["exporter", "ledger-sync"])
        self.assertEqual(model["kinds"], list(ledger.KINDS))

    def test_sorting(self) -> None:
        first = make("mypr:acme/a#1", "my-pr", "approved", created_at="2026-10-03", updated_at="2026-10-05")
        second = make(
            "review:acme/a#2", "review-request", "requested", created_at="2026-10-04", updated_at="2026-10-04"
        )
        third = make("mypr:acme/a#3", "my-pr", "approved", created_at="2026-10-01", updated_at="2026-10-06")
        data = ledger_of(first, second, third)
        order = {sort: [row["id"] for row in rows(data, sort=sort)] for sort in console_ledger.SORTS}
        self.assertEqual(order["updated"], [third["id"], first["id"], second["id"]])
        self.assertEqual(order["created"], [second["id"], first["id"], third["id"]])
        self.assertEqual(order["kind"], [third["id"], first["id"], second["id"]])

    def test_an_empty_or_missing_ledger(self) -> None:
        for data in ({}, ledger_of()):
            with self.subTest(data=data):
                model = console_ledger.items_model(data, Query())
                self.assertEqual((model["total"], model["count"], model["rows"]), (0, 0, []))

    def test_a_row(self) -> None:
        row = {r["id"]: r for r in rows(DATA)}[TASK]
        self.assertEqual(
            {key: row[key] for key in ("kind", "state", "window", "handover", "pending", "url")},
            {
                "kind": "task",
                "state": "delegated",
                "window": "exporter",
                "handover": "reported",
                "pending": False,
                "url": None,
            },
        )
        self.assertEqual(row["waiting_on"], [{"id": ASKED_DANA, "who": "Dana (finance)", "state": "sent"}])
        self.assertEqual({r["id"]: r for r in rows(DATA)}[RELEASE]["pending"], True)


class AnchorsTestCase(unittest.TestCase):
    def test_only_http_urls_are_web_urls(self) -> None:
        for good in ("https://github.com/acme/exporter/pull/102", "http://example.com/x?y=1#z"):
            with self.subTest(url=good):
                self.assertEqual(console_data.web_url(good), good)
        for bad in (
            None,
            "",
            42,
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "https://",
            "//example.com/x",
            "https://exa mple.com",
            " https://example.com",
            "https://example.com/\nx",
            "http://[::1",
        ):
            with self.subTest(url=bad):
                self.assertIsNone(console_data.web_url(bad))

    def test_systems(self) -> None:
        cases = {
            "https://github.com/acme/exporter/pull/1": "github",
            "https://gist.github.com/acme/1": "github",
            "https://acme.slack.com/archives/C0000000001/p1700000001000001": "slack",
            "https://acme.atlassian.net/browse/EXP-12": "jira",
            "https://jira.example.com/issues/1": "jira",
            "https://acme.atlassian.net/wiki/spaces/ENG/pages/000000001": "confluence",
            "https://notgithub.com/acme": "other",
            "https://example.com/browse/lowercase-1": "other",
        }
        for url, system in cases.items():
            with self.subTest(url=url):
                self.assertEqual(console_ledger.system_of(url), system)

    def test_urls_in_text(self) -> None:
        text = "see https://example.com/a, (https://example.com/b) and <https://example.com/c>. javascript:x"
        self.assertEqual(
            console_ledger.urls_in(text),
            ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        )

    def test_every_url_of_the_task_once_with_where_it_was(self) -> None:
        anchors = {a["url"]: a for a in item(TASK)["anchors"]}
        thread = "https://acme.slack.com/archives/C0000000001/p1700000001000001"
        self.assertEqual(set(anchors), {thread, "https://github.com/acme/exporter/pull/104"})
        self.assertEqual(anchors[thread]["label"], "Slack")
        self.assertEqual(anchors[thread]["where"], ["delegation", "link spawned_from", "history linked"])

    def test_sources_and_history_evidence(self) -> None:
        mention = {a["url"]: a["where"] for a in item(MENTION)["anchors"]}
        self.assertIn("source", mention["https://acme.slack.com/archives/C0000000001/p1700000001000001"])
        lena = {a["url"]: a for a in item(ASKED_LENA)["anchors"]}
        reply = lena["https://acme.slack.com/archives/D0000000002/p1792486800000200"]
        self.assertEqual(reply["system"], "slack")
        self.assertIn("field reply_ref", reply["where"])
        self.assertIn("history transition", reply["where"])
        self.assertEqual([a["label"] for a in item(JIRA)["anchors"]], ["Jira"])
        self.assertEqual([a["label"] for a in item(TD)["anchors"]], ["Confluence"])

    def test_a_javascript_url_is_never_a_link(self) -> None:
        model = item(SCRIPT_URL)
        self.assertIsNone(model["url"])
        self.assertEqual(model["url_text"], "javascript:alert(document.cookie)")
        self.assertEqual(model["anchors"], [])


class ItemPageTestCase(unittest.TestCase):
    def test_unknown_ids(self) -> None:
        self.assertIsNone(console_ledger.item_model(DATA, "mypr:acme/exporter#999", NOW))
        self.assertIsNone(console_ledger.item_model({}, TASK, NOW))

    def test_every_fixture_item_is_json(self) -> None:
        for item_id in DATA["items"]:
            with self.subTest(item=item_id):
                json.dumps(item(item_id))

    def test_header_and_untrusted_text_stays_verbatim(self) -> None:
        model = item(PR_103)
        self.assertEqual(model["title"], "Header fix <script>alert(1)</script>")
        self.assertEqual(model["url"], "https://github.com/acme/exporter/pull/103")
        self.assertTrue(model["open"])
        self.assertIn("own open PR", model["description"])
        notes = [entry["note"] for entry in item(TASK)["history"]]
        self.assertIn("Odd note <img src=x onerror=alert(1)> kept as text", notes)

    def test_the_lineage_tree(self) -> None:
        tree = item(TASK)["tree"]
        self.assertEqual([node["id"] for node in tree], [MENTION])
        children = {node["id"]: node for node in tree[0]["children"]}
        self.assertEqual(set(children), {TASK, DUPLICATE, FOLDED})
        task = children[TASK]
        self.assertTrue(task["focus"])
        self.assertEqual(task["how"], "spawned")
        self.assertEqual(
            [(c["id"], c["how"]) for c in task["children"]], [(PR_104, "produces"), (ASKED_DANA, "outreach")]
        )
        self.assertEqual(item(TD)["tree"], [])

    def test_the_links_both_ways(self) -> None:
        def chips(item_id: str) -> set[tuple[str, str]]:
            return {(chip["label"], chip["id"]) for chip in item(item_id)["links"]}

        self.assertEqual(chips(TASK), {("tracks", JIRA), ("duplicates", DUPLICATE), ("absorbs", FOLDED)})
        self.assertEqual(chips(DUPLICATE), {("duplicate of", TASK)})
        self.assertEqual(chips(FOLDED), {("folded into", TASK)})
        self.assertEqual(chips(RELEASE), {("blocked by", PR_102)})
        self.assertEqual(chips(PR_102), {("blocks", RELEASE)})
        self.assertEqual(chips(JIRA), {("tracked by", TASK)})

    def test_links_that_look_cyclic_or_point_at_nothing(self) -> None:
        one = make(
            "task:2026-10-01/1", "task", "accepted", links=[{"rel": "relates_to", "id": "task:2026-10-01/2"}]
        )
        two = make(
            "task:2026-10-01/2", "task", "accepted", links=[{"rel": "relates_to", "id": "task:2026-10-01/1"}]
        )
        gone = make(
            "task:2026-10-01/3", "task", "accepted", links=[{"rel": "spawned_from", "id": "slack:C0/1.0"}]
        )
        data = ledger_of(one, two, gone)
        self.assertEqual(len(item(one["id"], data)["links"]), 2)
        tree = item(gone["id"], data)["tree"]
        self.assertEqual((tree[0]["id"], tree[0]["missing"]), ("slack:C0/1.0", True))

    def test_hints(self) -> None:
        self.assertTrue(any("propose closing task:2026-10-20/1" in hint for hint in item(TASK)["hints"]))
        self.assertTrue(any("in-review" in hint for hint in item(ISSUE)["hints"]))
        self.assertEqual(item(TD)["hints"], [])

    def test_delegation_and_report(self) -> None:
        model = item(TASK)
        delegation = model["delegation"]
        self.assertEqual(
            (delegation["window"], delegation["task"], delegation["requester"]),
            ("exporter", "new-work", "Priya"),
        )
        self.assertEqual(
            (delegation["active"], delegation["awaiting_report"], delegation["handover"]),
            (True, False, "reported"),
        )
        self.assertEqual(
            delegation["link_url"], "https://acme.slack.com/archives/C0000000001/p1700000001000001"
        )
        report = model["report"]
        self.assertEqual(report["summary"], "Opened the credit notes PR; numbering still open")
        self.assertEqual(
            report["results"],
            [
                {
                    "text": "https://github.com/acme/exporter/pull/104",
                    "url": "https://github.com/acme/exporter/pull/104",
                }
            ],
        )
        self.assertIsNone(item(TD)["delegation"])
        self.assertIsNone(item(TD)["report"])

    def test_a_delegation_awaiting_its_report(self) -> None:
        task = make(
            "task:2026-10-01/1",
            "task",
            "delegated",
            delegation={"window": "exporter", "task": "new-work", "at": T0, "updates": 0},
            history=[{"at": T0, "event": "delegated"}],
        )
        delegation = item(task["id"], ledger_of(task))["delegation"]
        self.assertEqual((delegation["awaiting_report"], delegation["handover"]), (True, "awaiting report"))
        task["state"] = "done"
        self.assertEqual(item(task["id"], ledger_of(task))["delegation"]["active"], False)

    def test_outreach_that_went_out(self) -> None:
        outreach = item(ASKED_DANA)["outreach"]
        self.assertEqual(
            (outreach["to"], outreach["channel"], outreach["purpose"]),
            ("Dana (finance)", "slack", "question"),
        )
        self.assertEqual(outreach["parent"], TASK)
        self.assertEqual(outreach["options"], ["reuse", "own sequence"])
        self.assertEqual(outreach["nudges"], 0)
        self.assertEqual(
            outreach["messages"][0]["url"], "https://acme.slack.com/archives/D0000000001/p1792483200000100"
        )
        self.assertEqual(outreach["waiting_seconds"], 10 * 3600)  # sent 08:00, now 18:00
        self.assertNotIn("messages", {field["name"] for field in item(ASKED_DANA)["fields"]})

    def test_outreach_replied_proposed_and_by_email(self) -> None:
        lena = item(ASKED_LENA)["outreach"]
        self.assertEqual(
            (lena["answer"], lena["waiting_seconds"], lena["parent"]), ("Yes, Thursday", None, None)
        )
        self.assertEqual(
            lena["messages"][0],
            {
                "ref": "<msg-0001@example.com>",
                "url": None,
                "channel": "email",
                "at": lena["messages"][0]["at"],
            },
        )
        self.assertIsNotNone(lena["reply_url"])
        marco = item(ASK_MARCO)
        self.assertEqual(marco["outreach"]["messages"], [])
        self.assertTrue(marco["proposal"]["action"].startswith("Slack DM to Marco"))
        self.assertIsNone(item(TASK)["outreach"])

    def test_nudges_and_the_wait_since_the_latest(self) -> None:
        messages = [
            {"ref": "r2", "channel": "slack", "at": "2026-10-20T12:00:00+00:00"},
            {"ref": "r1", "channel": "slack", "at": "2026-10-19T12:00:00+00:00"},
        ]
        ask = make("out:2026-10-19/1", "outreach", "sent", fields={"to": "Dana", "messages": messages})
        outreach = item(ask["id"], ledger_of(ask))["outreach"]
        self.assertEqual([m["ref"] for m in outreach["messages"]], ["r1", "r2"])
        self.assertEqual((outreach["nudges"], outreach["waiting_seconds"]), (1, 6 * 3600))

    def test_said(self) -> None:
        self.assertEqual(
            [(s["window"], s["text"], s["held_only"]) for s in item(TASK)["said"]],
            [("exporter", "keep the numbering per customer, as Dana said", False)],
        )
        held = {
            "at": T0,
            "window": "exporter",
            "text": "ok",
            "items": ["task:2026-10-01/1"],
            "also_held": ["task:2026-10-01/2"],
        }
        data = ledger_of(
            make("task:2026-10-01/1", "task", "accepted"),
            make("task:2026-10-01/2", "task", "accepted"),
            said=[held],
        )
        self.assertEqual([s["held_only"] for s in item("task:2026-10-01/2", data)["said"]], [True])
        self.assertEqual(item(TD)["said"], [])

    def test_history(self) -> None:
        history = item(ASKED_LENA)["history"]
        self.assertEqual(history[0]["event"], "created")
        replied = next(
            entry for entry in history if entry["event"] == "transition" and "Thursday" in entry["note"]
        )
        self.assertIn({"name": "to", "value": "replied"}, replied["details"])

    def test_an_item_with_no_history_fields_or_links(self) -> None:
        bare = {"id": "mypr:acme/a#1", "kind": "my-pr", "state": "approved"}
        model = item(bare["id"], ledger_of(bare))
        self.assertEqual((model["history"], model["fields"], model["tree"], model["links"]), ([], [], [], []))
        self.assertEqual((model["url"], model["url_text"], model["proposal"]), (None, "", None))


class NeedsYouTestCase(unittest.TestCase):
    def test_the_fixture(self) -> None:
        model = console_ledger.needs_you_model(DATA)
        self.assertEqual([p["id"] for p in model["proposals"]], [RELEASE, LEARN])
        self.assertTrue(model["proposals"][1]["action"].startswith("Add to policy.md"))
        self.assertEqual([o["id"] for o in model["outreach"]], [ASK_MARCO])
        self.assertEqual((model["outreach"][0]["to"], model["outreach"][0]["parent"]), ("Marco", PR_102))
        # one entry per item a mismatch asks me to decide on, with the tickets that flag it
        self.assertEqual([m["id"] for m in model["mismatches"]], [ISSUE, PR_104, TASK])
        self.assertEqual([m["tickets"] for m in model["mismatches"]], [[ISSUE], [JIRA], [JIRA]])
        self.assertEqual(len(model["mismatches"][2]["hints"]), 2)
        self.assertEqual((model["count"], model["items"]), (6, 6))
        self.assertIsNone(model["windows"])

    def test_the_strip_counts_what_the_needs_me_group_holds(self) -> None:
        groups = console_ledger.items_model(DATA, Query())["groups"]
        self.assertEqual(console_ledger.needs_you_model(DATA)["items"], groups[console_ledger.NEEDS_ME])

    def test_an_item_counts_once(self) -> None:
        ticket = make("jira:EXP-1", "ticket", "done", fields={"system": "jira", "status_category": "done"})
        flagged_with_proposal = make(
            "task:2026-10-01/1",
            "task",
            "accepted",
            proposal={"action": "close it", "proposed_at": T0},
            links=[{"rel": "tracks", "id": ticket["id"]}],
        )
        model = console_ledger.needs_you_model(ledger_of(ticket, flagged_with_proposal))
        self.assertEqual([p["id"] for p in model["proposals"]], [flagged_with_proposal["id"]])
        self.assertEqual([m["id"] for m in model["mismatches"]], [flagged_with_proposal["id"]])
        self.assertEqual(model["count"], 1)

    def test_windows_waiting_at_a_prompt_count(self) -> None:
        waiting = [{"name": "exporter", "status": "waiting", "seconds": 300}]
        model = console_ledger.needs_you_model(DATA, waiting)
        self.assertEqual((model["count"], model["windows"]), (7, waiting))

    def test_closed_items_and_an_empty_ledger(self) -> None:
        closed = make("mypr:acme/a#1", "my-pr", "merged", proposal={"action": "x", "proposed_at": T0})
        self.assertEqual(console_ledger.needs_you_model(ledger_of(closed))["count"], 0)
        self.assertEqual(console_ledger.needs_you_model({})["count"], 0)

    def test_proposed_outreach_without_a_message_yet(self) -> None:
        ask = make("out:2026-10-01/1", "outreach", "proposed", fields={"to": "Dana", "channel": "email"})
        entry = console_ledger.needs_you_model(ledger_of(ask))["outreach"][0]
        self.assertEqual((entry["action"], entry["to"], entry["parent"]), ("", "Dana", None))


class ProposalStandingTestCase(unittest.TestCase):
    """An approved proposal waits on bosun, a used one is closing; only one awaiting my approval
    (none yet, or its approved call failed) needs me (approvals.proposal_status)."""

    SPEC: ClassVar[dict[str, str]] = {
        "type": "run",
        "command": "gh pr merge https://github.com/acme/a/pull/1 --squash",
    }
    USED = "2026-10-01T09:05:00+00:00"

    def proposal(self, **approval: Any) -> dict[str, Any]:
        hashed = {"at": "2026-10-01T09:01:00+00:00", "quote": "yes", "hash": approvals.spec_hash(self.SPEC)}
        built: dict[str, Any] = {"action": "merge it", "proposed_at": T0, "spec": self.SPEC}
        return built | ({"approval": hashed | approval} if approval.pop("approved", True) else {})

    def model(self, *items: dict[str, Any]) -> dict[str, Any]:
        return console_ledger.needs_you_model(ledger_of(*items))

    def test_every_state(self) -> None:
        cases = {
            "unapproved": (self.proposal(approved=False), True, "awaiting your approval"),
            "approved": (self.proposal(), False, "approved, not yet made"),
            "used": (self.proposal(used_at=self.USED), False, "carried out, closing"),
            "failed": (
                self.proposal(used_at=self.USED, failed={"at": self.USED, "reason": "Exit code 1"}),
                True,
                approvals.FAILED_LABEL,
            ),
        }
        for name, (proposal, needs, label) in cases.items():
            with self.subTest(name):
                pr = make("mypr:acme/a#1", "my-pr", "approved", proposal=proposal)
                model = self.model(pr)
                self.assertEqual(model["count"], int(needs))
                self.assertEqual([p["status"] for p in model["proposals"]], [label] if needs else [])
                self.assertEqual([p["status"] for p in model["in_flight"]], [] if needs else [label])
                row = console_ledger.row_model(pr, console_ledger.LedgerView.of(ledger_of(pr)))
                self.assertEqual((row["pending"], row["proposal_status"]), (needs, label))
                self.assertEqual(row["group"] == console_ledger.NEEDS_ME, needs)
                page = console_ledger.item_model(ledger_of(pr), pr["id"], NOW)
                self.assertEqual(page["proposal"]["status"], label)  # type: ignore[index]

    def test_proposed_outreach_whose_message_was_approved_or_sent_no_longer_needs_me(self) -> None:
        slack = {"type": "slack", "channel": "C0000000001", "thread": "", "text": "hi"}
        hashed = {"at": T0, "quote": "yes", "hash": approvals.spec_hash(slack)}
        for approval, needs in (
            (None, True),
            (hashed, False),
            (hashed | {"used_at": self.USED, "carried_out": {"at": self.USED}}, False),
            (hashed | {"used_at": self.USED, "failed": {"at": self.USED, "reason": "x"}}, True),
        ):
            with self.subTest(approval=approval):
                proposal = {"action": "hi", "proposed_at": T0, "spec": slack} | (
                    {"approval": approval} if approval else {}
                )
                ask = make(
                    "out:2026-10-01/1",
                    "outreach",
                    "proposed",
                    proposal=proposal,
                    fields={"to": "Dana", "channel": "slack"},
                )
                model = self.model(ask)
                self.assertEqual(
                    ([o["id"] for o in model["outreach"]], model["count"]),
                    ([ask["id"]] if needs else [], int(needs)),
                )
                self.assertEqual(
                    console_ledger.group_of(ask, console_ledger.LedgerView.of(ledger_of(ask)))
                    == console_ledger.NEEDS_ME,
                    needs,
                )

    def test_another_teams_ticket_is_fyi(self) -> None:
        ours = make("jira:EXP-1", "ticket", "todo", fields={"system": "jira"}, links=[])
        theirs = make("jira:OPS-1", "ticket", "todo", fields={"system": "jira"})
        pr = make(
            "mypr:acme/a#1",
            "my-pr",
            "awaiting-review",
            links=[{"rel": "tracks", "id": ours["id"]}, {"rel": "tracks", "id": theirs["id"]}],
        )
        data = ledger_of(ours, theirs, pr)
        self.assertEqual(
            {m["id"] for m in console_ledger.needs_you_model(data)["mismatches"]}, {ours["id"], theirs["id"]}
        )
        owned = tickets.parse_owned("EXP")
        model = console_ledger.needs_you_model(data, owned=owned)
        self.assertEqual([m["id"] for m in model["mismatches"]], [ours["id"]])
        self.assertEqual(model["count"], 1)
        groups = console_ledger.items_model(data, Query(), owned=owned)["groups"]
        self.assertEqual(groups[console_ledger.NEEDS_ME], 1)
        hints = console_ledger.item_model(data, theirs["id"], NOW, owned=owned)["hints"]  # type: ignore[index]
        self.assertTrue(hints and all(h.startswith(tickets.NOT_OURS) for h in hints))
        page = console_ledger.item_model(data, pr["id"], NOW, owned=owned) or {}
        self.assertTrue(any(h.startswith(tickets.NOT_OURS) for h in page["hints"]))


class WorkstreamTestCase(unittest.TestCase):
    """Rows carry the resolved workstream; ?ws= filters on it (the workstreams under it too)."""

    def data(self) -> dict[str, Any]:
        return ledger_of(
            make("ws:widgets", "workstream", "active", title="Widgets", fields={"aliases": ["wg"]}),
            make("ws:widget-ui", "workstream", "active", links=[{"rel": "part_of", "id": "ws:widgets"}]),
            make(
                "mypr:acme/widgets#1",
                "my-pr",
                "awaiting-review",
                links=[{"rel": "part_of", "id": "ws:widgets"}],
            ),
            make(
                "mypr:acme/widgets#2",
                "my-pr",
                "awaiting-review",
                links=[{"rel": "part_of", "id": "ws:widget-ui"}],
            ),
            make("out:mypr:acme/widgets#1/1", "outreach", "sent", fields={"parent": "mypr:acme/widgets#1"}),
            make("mypr:acme/misc#3", "my-pr", "awaiting-review"),
        )

    def test_rows_and_filter(self) -> None:
        data = self.data()
        every = {row["id"]: row["workstream"] for row in rows(data)}
        self.assertEqual(every["out:mypr:acme/widgets#1/1"], "ws:widgets")
        self.assertEqual(every["mypr:acme/misc#3"], "")
        self.assertEqual(
            ids(rows(data, ws="wg")),
            {
                "mypr:acme/widgets#1",
                "mypr:acme/widgets#2",
                "out:mypr:acme/widgets#1/1",
                "ws:widgets",
                "ws:widget-ui",
            },
        )
        self.assertEqual(ids(rows(data, ws="ws:widget-ui")), {"mypr:acme/widgets#2", "ws:widget-ui"})
        self.assertEqual(ids(rows(data, ws="unassigned")), {"mypr:acme/misc#3"})
        with self.assertRaises(QueryError):
            rows(data, ws="nope")
        model = console_ledger.items_model(data, Query.parse({}, NOW))
        self.assertEqual(model["workstreams"], ["ws:widget-ui", "ws:widgets"])

    def test_the_item_page_says_how(self) -> None:
        how = item("out:mypr:acme/widgets#1/1", self.data())["workstream_how"]
        self.assertEqual(how["basis"], "inherited")
        self.assertEqual(how["how"], "inherited from mypr:acme/widgets#1 (explicit)")


if __name__ == "__main__":
    unittest.main()
