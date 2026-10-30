"""Unit tests for workstreams (lib/workstreams.py, lib/ws_summary.py, and the ledger commands that
use them): the part_of relation (one per item, two levels, cycles), resolution order and
inheritance through every lineage path, aliases, epic mapping through ticket parents, site rules,
lint findings, bulk assignment, the stand-up summary, list and show. Fictional data: acme's
widget search."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import ledger
import lineage
import workstreams
import ws_summary
from lineage import LineageError
from workstreams import WorkstreamError


WS = "ws:widget-search"
UI = "ws:widget-ui"
OTHER = "ws:billing"
PR = "mypr:acme/widgets#12"
PR2 = "mypr:acme/widgets#13"
OTHER_PR = "mypr:acme/invoices#4"
REVIEW = "review:acme/widgets#20"
MENTION = "slack:C0000000042/1700000001.000001"
OTHER_MENTION = "slack:C0000000099/1700000002.000002"
OUT = f"out:{PR}/1"
EPIC = "jira:WID-100"
STORY = "jira:WID-101"
SUBTASK = "jira:WID-102"
TODAY = date.today().isoformat()
TASK = f"task:{TODAY}/1"
TASK2 = f"task:{TODAY}/2"
SLACK = "https://acme.slack.com/archives/C0000000042/p1700000041000041"


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


def lint_text() -> str:
    text, _code = run("lint")
    return str(text)


class WorkstreamTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ledger.configure(Path(self.tmp.name) / "state")
        self.config = Path(self.tmp.name) / "config"
        self.config.mkdir()
        ledger.configure_site(self.config)
        run("ws", "add", "widget-search", "--title", "Widget search", "--alias", "widgets")
        run("upsert", PR, "--kind", "my-pr", "--title", "Widget search index")

    def items(self) -> dict[str, Any]:
        found: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())["items"]
        return found

    def resolve(self, item_id: str) -> workstreams.Resolution:
        items = self.items()
        return workstreams.Resolver(items, ledger.read_rules(items)).resolve(item_id)

    def rules(self, *rules: dict[str, Any]) -> None:
        (self.config / workstreams.RULES_FILE).write_text(json.dumps({"rules": list(rules)}))

    def derived(self, item_id: str) -> dict[str, Any]:
        shown: dict[str, Any] = json.loads(run("show", item_id))
        return dict(shown.get("derived") or {})


class PartOfTest(WorkstreamTestCase):
    def test_link_by_alias_and_show_says_explicit(self) -> None:
        self.assertIn(f"ok: {PR} part_of {WS}", run("link", PR, "part_of", "widgets"))
        found = self.derived(PR)["workstream"]
        self.assertEqual((found["id"], found["basis"]), (WS, workstreams.EXPLICIT))

    def test_one_workstream_per_item_unless_moved(self) -> None:
        run("ws", "add", "billing", "--title", "Billing export")
        run("link", PR, "part_of", WS)
        with self.assertRaisesRegex(LineageError, "belongs to one workstream"):
            run("link", PR, "part_of", OTHER)
        self.assertIn(f"moved from {WS}", run("link", PR, "part_of", OTHER, "--move"))
        self.assertEqual(lineage.stored_part_of(self.items()[PR]), [OTHER])
        self.assertIn("unchanged", run("link", PR, "part_of", OTHER))

    def test_relates_to_a_second_workstream_does_not_count(self) -> None:
        run("ws", "add", "billing", "--title", "Billing export")
        run("link", PR, "part_of", WS)
        run("link", PR, "relates_to", OTHER)
        self.assertEqual(self.resolve(PR).ws, WS)

    def test_part_of_takes_only_a_workstream(self) -> None:
        run("upsert", PR2, "--kind", "my-pr", "--title", "Preview")
        with self.assertRaises(WorkstreamError):
            run("link", PR, "part_of", PR2)
        with self.assertRaisesRegex(LineageError, "takes only part_of and relates_to"):
            run("link", PR, "spawned_from", WS)

    def test_move_is_only_for_part_of(self) -> None:
        run("upsert", PR2, "--kind", "my-pr", "--title", "Preview")
        with self.assertRaisesRegex(ledger.LedgerError, "--move is for part_of"):
            run("link", PR, "relates_to", PR2, "--move")


class HierarchyTest(WorkstreamTestCase):
    def test_two_levels_at_most_and_no_cycles(self) -> None:
        run("ws", "add", "widget-ui", "--title", "Widget UI", "--parent", "widgets")
        run("ws", "add", "widget-ui-dark", "--title", "Dark mode")
        with self.assertRaisesRegex(LineageError, "3 levels"):
            run("ws", "add", "widget-ui-dark", "--parent", UI)
        with self.assertRaisesRegex(LineageError, "cycle"):
            run("link", WS, "part_of", UI)
        run("ws", "add", "platform", "--title", "Platform")
        with self.assertRaisesRegex(LineageError, "3 levels"):
            run("link", WS, "part_of", "ws:platform")

    def test_rollup_counts_and_summary_include_children(self) -> None:
        run("ws", "add", "widget-ui", "--title", "Widget UI", "--parent", "widgets")
        run("upsert", PR2, "--kind", "my-pr", "--title", "Preview pane")
        run("link", PR2, "part_of", UI)
        run("link", PR, "part_of", WS)
        listed = run("ws", "list")
        self.assertIn(
            f"{WS}  [active] Widget search | 1 item(s) (explicit 1), 1 open; "
            "with the workstreams under it: 2 item(s), 2 open",
            listed,
        )
        self.assertIn(f"  {UI}  [active] Widget UI | 1 item(s)", listed)
        self.assertEqual(run("list", "--ws", "widgets").count("mypr:"), 2)
        self.assertEqual(run("list", "--ws", UI).count("mypr:"), 1)
        text = run("summary", "--ws", WS)
        self.assertIn(f"Preview pane: waiting on reviewers ({PR2}) [{UI}]", text)
        self.assertIn(f"includes {UI}", text)

    def test_the_deeper_workstream_wins_between_nested_parents(self) -> None:
        run("ws", "add", "widget-ui", "--title", "Widget UI", "--parent", "widgets")
        run("upsert", PR2, "--kind", "my-pr", "--title", "Preview pane")
        run("link", PR, "part_of", WS)
        run("link", PR2, "part_of", UI)
        run("spawn", PR, "--title", "Fix both", "--ask", "fix it", "--via", "user: do it")
        run("link", PR2, "spawned_from", TASK)
        run("link", TASK, "relates_to", PR2)
        items = self.items()
        resolver = workstreams.Resolver(items)
        combined = resolver.combine([(PR, resolver.resolve(PR)), (PR2, resolver.resolve(PR2))])
        self.assertEqual((combined.ws, combined.basis), (UI, workstreams.INHERITED))


class InheritanceTest(WorkstreamTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("link", PR, "part_of", WS)

    def test_outreach_inherits_from_its_parent(self) -> None:
        run("reach-out", PR, "--to", "Dana", "--subject", "Index refresh rate?")
        found = self.resolve(OUT)
        self.assertEqual((found.ws, found.basis, found.via, found.origin), (WS, "inherited", PR, "explicit"))
        self.assertEqual(found.describe(), f"inherited from {PR} (explicit)")

    def test_spawned_tasks_and_their_prs_inherit(self) -> None:
        run("spawn", PR, "--title", "Same fix in the preview", "--ask", "port it", "--via", "user: go")
        run("upsert", PR2, "--kind", "my-pr", "--title", "Port the index")
        run("link", TASK, "produces", PR2)
        self.assertEqual((self.resolve(TASK).ws, self.resolve(TASK).via), (WS, PR))
        found = self.resolve(PR2)
        self.assertEqual((found.ws, found.via, found.origin), (WS, TASK, "explicit"))

    def test_a_folded_item_inherits_from_what_carries_it(self) -> None:
        run("spawn", "--title", "Tidy the index fields", "--ask", "tidy", "--via", "user: tidy")
        self.assertEqual(self.resolve(TASK).basis, workstreams.UNASSIGNED)
        run("fold", TASK, "--into", PR)
        self.assertEqual((self.resolve(TASK).ws, self.resolve(TASK).via), (WS, PR))

    def test_lineage_parents_come_before_what_carries_it(self) -> None:
        run("ws", "add", "billing", "--title", "Billing")
        run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "Invoices")
        run("link", OTHER_PR, "part_of", OTHER)
        run("spawn", PR, "--title", "Follow-up", "--ask", "do it", "--via", "user: go")
        run("link", TASK, "duplicate_of", OTHER_PR)
        self.assertEqual(self.resolve(TASK).ws, WS)

    def test_parents_in_different_workstreams_leave_it_unassigned_with_candidates(self) -> None:
        run("ws", "add", "billing", "--title", "Billing")
        run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "Invoices")
        run("link", OTHER_PR, "part_of", OTHER)
        run("spawn", PR, "--title", "Shared fix", "--ask", "fix", "--via", "user: go")
        run("link", TASK, "spawned_from", OTHER_PR)
        found = self.resolve(TASK)
        self.assertEqual((found.ws, found.basis, found.candidates), ("", "conflict", (OTHER, WS)))
        self.assertIn("pick one with part_of", found.describe())
        lint = lint_text()
        self.assertIn(
            f"propose: ambiguous: {TASK}: evidence points at {OTHER}, {WS}", lint.replace("\n", " ")
        )

    def test_explicit_beats_inherited_and_lint_flags_the_override(self) -> None:
        run("ws", "add", "billing", "--title", "Billing")
        run("reach-out", PR, "--to", "Dana", "--subject", "Index refresh rate?")
        run("link", OUT, "part_of", OTHER)
        self.assertEqual(self.resolve(OUT).ws, OTHER)
        self.assertIn(f"warn: overrides-parent: {OUT}: is part_of {OTHER} while {PR}", lint_text())

    def test_a_tag_below_the_same_workstream_is_redundant(self) -> None:
        run("reach-out", PR, "--to", "Dana", "--subject", "Index refresh rate?")
        output = run("link", OUT, "part_of", WS)
        self.assertIn("only roots need the tag", output)
        self.assertIn(f"info: redundant: {OUT}", lint_text())

    def test_a_child_workstream_below_the_parents_is_not_an_override(self) -> None:
        run("ws", "add", "widget-ui", "--title", "Widget UI", "--parent", WS)
        run("reach-out", PR, "--to", "Dana", "--subject", "Index refresh rate?")
        run("link", OUT, "part_of", UI)
        self.assertEqual(lint_text(), "(no findings)")


class EpicTest(WorkstreamTestCase):
    def setUp(self) -> None:
        super().setUp()
        run("ws", "add", WS, "--epic", "WID-100")

    def test_the_epic_ticket_itself(self) -> None:
        self.assertEqual(self.items()[WS]["fields"]["epic"], EPIC)
        run("link", PR, "tracks", EPIC)
        found = self.resolve(PR)
        self.assertEqual((found.ws, found.basis), (WS, workstreams.EPIC))
        self.assertIn(f"epic: tracks {EPIC} (the epic of {WS})", found.describe())

    def test_through_the_ticket_parents_ticket_sync_recorded(self) -> None:
        run("link", PR, "tracks", SUBTASK)
        self.assertEqual(self.resolve(PR).basis, workstreams.UNASSIGNED)
        run("ticket-sync", SUBTASK, "--status", "In Progress", "--parent", "WID-101")
        run("ticket-sync", STORY, "--status", "In Progress", "--epic", EPIC)
        self.assertEqual(self.items()[SUBTASK]["fields"]["parent_ticket"], STORY)
        found = self.resolve(PR)
        self.assertEqual((found.ws, found.via), (WS, f"{SUBTASK} -> {STORY} -> {EPIC} (the epic of {WS})"))

    def test_a_ticket_cannot_be_its_own_parent(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "its own parent"):
            run("ticket-sync", STORY, "--status", "To Do", "--parent", "WID-101")

    def test_inherited_beats_epic_and_lint_says_they_disagree(self) -> None:
        run("ws", "add", "billing", "--title", "Billing")
        run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "Invoices")
        run("link", OTHER_PR, "part_of", OTHER)
        run("spawn", OTHER_PR, "--title", "Widget follow-up", "--ask", "do it", "--via", "user: go")
        run("link", TASK, "tracks", EPIC)
        self.assertEqual(self.resolve(TASK).ws, OTHER)
        self.assertIn(f"warn: rule-vs-inherited: {TASK}: inherits {OTHER}", lint_text())

    def test_an_epic_named_by_two_workstreams_is_flagged_and_ambiguous(self) -> None:
        run("ws", "add", "billing", "--title", "Billing", "--epic", EPIC)
        run("link", PR, "tracks", EPIC)
        self.assertEqual(self.resolve(PR).basis, workstreams.CONFLICT)
        self.assertIn(f"warn: epic-claimed-twice: {EPIC}: is the epic of {OTHER}, {WS}", lint_text())


class RulesTest(WorkstreamTestCase):
    def test_repo_and_title_keywords(self) -> None:
        self.rules({"ws": "widgets", "repo": "acme/widget*", "title": ["index", "preview pane"]})
        run("upsert", PR2, "--kind", "my-pr", "--title", "Preview pane polish")
        run("upsert", "mypr:acme/widgets#14", "--kind", "my-pr", "--title", "Bump deps")
        self.assertEqual((self.resolve(PR).ws, self.resolve(PR).basis), (WS, workstreams.RULE))
        self.assertIn("rule 1 (repo acme/widget*; title index, preview pane)", self.resolve(PR).describe())
        self.assertEqual(self.resolve(PR2).ws, WS)
        self.assertEqual(self.resolve("mypr:acme/widgets#14").basis, workstreams.UNASSIGNED)

    def test_keywords_are_whole_words(self) -> None:
        self.rules({"ws": WS, "title": "ind"})
        self.assertEqual(self.resolve(PR).basis, workstreams.UNASSIGNED)

    def test_origin_channel_of_a_mention_and_of_outreach_messages(self) -> None:
        self.rules({"ws": WS, "channel": "C0000000042", "kind": ["mention", "outreach"]})
        run("upsert", MENTION, "--kind", "mention", "--title", "Can widgets do SVG?")
        run("upsert", OTHER_MENTION, "--kind", "mention", "--title", "Lunch?")
        run("reach-out", "--to", "Dana", "--subject", "Widget sizes?")
        out = f"out:{TODAY}/1"
        run("sent", out, "--ref", SLACK)
        self.assertEqual(self.resolve(MENTION).ws, WS)
        self.assertEqual(self.resolve(OTHER_MENTION).ws, "")
        self.assertEqual(self.resolve(out).ws, WS)

    def test_a_workstreams_own_channel_is_a_rule(self) -> None:
        run("ws", "add", WS, "--channel", "C0000000042")
        run("upsert", MENTION, "--kind", "mention", "--title", "Can widgets do SVG?")
        self.assertEqual(self.resolve(MENTION).describe(), "rule: its channel (channel C0000000042)")

    def test_rules_apply_within_the_workstreams_dates(self) -> None:
        self.rules({"ws": WS, "title": "index"})
        run("ws", "add", WS, "--started", "2999-01-01")
        self.assertEqual(self.resolve(PR).basis, workstreams.UNASSIGNED)
        run("ws", "add", WS, "--started", "2000-01-01", "--ended", "2000-12-31")
        self.assertEqual(self.resolve(PR).basis, workstreams.UNASSIGNED)

    def test_two_workstreams_matching_is_ambiguous(self) -> None:
        run("ws", "add", "billing", "--title", "Billing")
        self.rules({"ws": WS, "title": "search"}, {"ws": "billing", "title": "index"})
        found = self.resolve(PR)
        self.assertEqual((found.basis, found.candidates), (workstreams.CONFLICT, (OTHER, WS)))

    def test_rules_that_cannot_be_used_are_named_not_fatal(self) -> None:
        self.rules(
            {"ws": "ws:nope", "title": "x"},
            {"ws": WS, "kind": "my-pr"},
            {"ws": WS, "colour": "blue"},
            {"ws": WS, "title": "index"},
        )
        found = ledger.read_rules(self.items())
        self.assertEqual(len(found.rules), 1)
        problems = " ".join(found.problems)
        for expected in ("no workstream 'ws:nope'", "kind alone", "unknown key(s) colour"):
            self.assertIn(expected, problems)
        self.assertIn("warn: rules: ", lint_text())

    def test_the_generic_example_parses(self) -> None:
        example = Path(__file__).resolve().parents[2] / "examples" / "generic" / workstreams.RULES_FILE
        found = workstreams.parse_rules(self.items(), json.loads(example.read_text()))
        self.assertEqual((len(found.rules), found.problems), (2, []))

    def test_a_malformed_rules_file_is_an_error(self) -> None:
        (self.config / workstreams.RULES_FILE).write_text("{not json")
        with self.assertRaisesRegex(ledger.LedgerError, "not valid JSON"):
            run("summary")


class AliasTest(WorkstreamTestCase):
    def test_aliases_rename_safely(self) -> None:
        self.assertIn("also answers to 'widget lookup'", run("ws", "alias", WS, "widget lookup"))
        run("ws", "add", WS, "--title", "Widget search v2")
        run("link", PR, "part_of", "Widget Lookup")
        self.assertEqual(self.resolve(PR).ws, WS)
        self.assertIn("unchanged", run("ws", "alias", "widgets", "WIDGETS"))
        self.assertIn("no longer answers", run("ws", "alias", WS, "widgets", "--remove"))
        with self.assertRaises(WorkstreamError):
            workstreams.find(self.items(), "widgets")

    def test_an_alias_names_one_workstream(self) -> None:
        run("ws", "add", "billing", "--title", "Billing")
        with self.assertRaisesRegex(ledger.LedgerError, "already answers to"):
            run("ws", "alias", OTHER, "widgets")
        with self.assertRaisesRegex(ledger.LedgerError, "already answers to"):
            run("ws", "alias", OTHER, "widget-search")

    def test_slugs_are_checked(self) -> None:
        with self.assertRaisesRegex(WorkstreamError, "ws:<slug>"):
            run("ws", "add", "Widget Search", "--title", "x")
        with self.assertRaisesRegex(ledger.LedgerError, "needs --title"):
            run("ws", "add", "new-one")
        with self.assertRaisesRegex(WorkstreamError, "workstream ids"):
            run("upsert", "ws:Bad", "--kind", "workstream")


class StatesTest(WorkstreamTestCase):
    def test_set_state_records_the_end_and_done_with_open_items_is_flagged(self) -> None:
        run("link", PR, "part_of", WS)
        self.assertEqual(
            run("ws", "set-state", WS, "paused", "--note", "on hold"), f"ok: {WS} active -> paused"
        )
        run("ws", "set-state", WS, "done", "--note", "shipped")
        self.assertEqual(self.items()[WS]["fields"]["ended"], TODAY)
        self.assertIn(f"warn: done-with-open-items: {WS}: is done but 1 open item(s)", lint_text())
        self.assertNotIn(WS, run("ws", "list"))
        self.assertIn(WS, run("ws", "list", "--all"))

    def test_past_target_is_flagged(self) -> None:
        run("ws", "add", WS, "--target", "2000-01-01")
        self.assertIn(f"info: past-target: {WS}", lint_text())
        with self.assertRaisesRegex(ledger.LedgerError, "YYYY-MM-DD"):
            run("ws", "add", WS, "--target", "next week")

    def test_prune_keeps_workstreams_and_part_of_keeps_nothing(self) -> None:
        run("link", PR, "part_of", WS)
        run("transition", PR, "merged", "--note", "merged")
        run("ws", "set-state", WS, "done", "--note", "shipped")
        data = json.loads(ledger.STORE.ledger_file.read_text())
        for item in data["items"].values():
            item["updated_at"] = "2000-01-01T00:00:00+00:00"
        ledger.STORE.ledger_file.write_text(json.dumps(data))
        self.assertIn("pruned 1 item(s)", run("prune", "--days", "1"))
        self.assertEqual(list(self.items()), [WS])


class AssignTest(WorkstreamTestCase):
    def test_dry_run_then_apply_tags_only_the_topmost(self) -> None:
        self.rules({"ws": WS, "title": ["search", "index"]})
        run("reach-out", PR, "--to", "Dana", "--subject", "Index refresh rate?")
        run("upsert", MENTION, "--kind", "mention", "--title", "Lunch?")
        dry = run("assign")
        self.assertIn(f"would assign: {PR} -> {WS} (rule: rule 1", dry)
        self.assertNotIn(OUT, dry)  # it inherits from the PR
        self.assertIn("(1 change(s), 0 proposal(s); dry run", dry)
        self.assertEqual(lineage.stored_part_of(self.items()[PR]), [])
        applied = run("assign", "--apply")
        self.assertIn("1 link(s) recorded", applied)
        self.assertEqual(lineage.stored_part_of(self.items()[PR]), [WS])
        self.assertEqual(self.resolve(OUT).describe(), f"inherited from {PR} (explicit)")
        self.assertIn("(0 change(s)", run("assign", "--apply"))

    def test_uncertain_cases_are_proposals(self) -> None:
        run("ws", "add", "billing", "--title", "Billing")
        self.rules({"ws": WS, "title": "search"}, {"ws": "billing", "title": "index"})
        output = run("assign", "--apply")
        self.assertIn(f"propose: {PR}: evidence points at {OTHER}, {WS}", output)
        self.assertIn("0 link(s) recorded", output)

    def test_only_one_workstream(self) -> None:
        run("ws", "add", "billing", "--title", "Billing")
        run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "Invoice totals")
        self.rules({"ws": WS, "title": "index"}, {"ws": "billing", "title": "invoice"})
        output = run("assign", "--ws", "billing")
        self.assertIn(OTHER_PR, output)
        self.assertNotIn(PR, output)

    def test_dry_run_and_apply_together_are_refused(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, "not both"):
            run("assign", "--dry-run", "--apply")


class LintTest(WorkstreamTestCase):
    def test_a_growing_unassigned_count(self) -> None:
        for n in range(4):
            run("upsert", f"mypr:acme/misc#{n}", "--kind", "my-pr", "--title", f"Misc {n}")
        text, code = run("lint", "--strict")
        self.assertIn(
            "warn: unassigned-growing: 5 open unassigned item(s) created in the last 7 days (0", text
        )
        self.assertEqual(code, 1)
        found = json.loads(run("lint", "--json")[0])
        self.assertEqual(found[0]["code"], "unassigned-growing")

    def test_not_growing_when_last_week_had_as_many(self) -> None:
        items: dict[str, Any] = {
            f"mypr:acme/misc#{n}": {
                "id": f"mypr:acme/misc#{n}",
                "kind": "my-pr",
                "state": "awaiting-review",
                "created_at": (datetime.now(UTC) - timedelta(days=1 + 7 * (n % 2))).isoformat(),
                "fields": {},
            }
            for n in range(8)
        }
        resolver = workstreams.Resolver(items)
        self.assertEqual(workstreams.unassigned_growth(resolver, ledger.is_open, datetime.now(UTC)), [])


class ShowAndListTest(WorkstreamTestCase):
    def test_show_says_unassigned_and_for_a_workstream_its_counts(self) -> None:
        self.assertEqual(self.derived(PR)["workstream"], {"basis": "unassigned", "how": "unassigned"})
        run("link", PR, "part_of", WS)
        self.assertEqual(self.derived(WS)["workstream"], {"items": 1, "open_items": 1})
        shown = run("ws", "show", "widgets")
        self.assertIn("aliases: widgets", shown)
        self.assertIn("my-pr: 1 (1 open)", shown)
        self.assertIn(f"tagged: {PR}", shown)

    def test_list_unassigned_and_unknown(self) -> None:
        run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "Invoices")
        run("link", PR, "part_of", WS)
        self.assertEqual(run("list", "--ws", "unassigned").count("mypr:"), 1)
        self.assertIn(OTHER_PR, run("list", "--ws", "unassigned"))
        with self.assertRaisesRegex(WorkstreamError, "no workstream 'nope'"):
            run("list", "--ws", "nope")

    def test_kinds_lists_the_workstream_machine(self) -> None:
        self.assertIn("workstream: A time-bound effort", run("kinds"))
        self.assertIn("  active -> done, paused", run("kinds"))


# ---- the summary, over a fixture week ------------------------------------------------------------

MONDAY = datetime(2026, 10, 19, tzinfo=UTC)


def at(days: float) -> str:
    return (MONDAY + timedelta(days=days)).isoformat()


def item(item_id: str, kind: str, state: str, title: str = "", **extra: Any) -> dict[str, Any]:
    fields = extra.pop("fields", {})
    links = extra.pop("links", [])
    history = extra.pop("history", [])
    return {
        "id": item_id,
        "kind": kind,
        "state": state,
        "title": title,
        "url": extra.pop("url", ""),
        "fields": fields,
        "links": links,
        "created_at": extra.pop("created_at", at(-3)),
        "history": history,
        **extra,
    }


def moved(state: str, days: float) -> dict[str, Any]:
    return {"at": at(days), "event": "transition", "to": state}


def week() -> dict[str, dict[str, Any]]:
    """A week of acme's widget search: one PR merged, one in review waiting on Dana, a decision
    from Marco, a task blocked on outreach, a proposed task, and an unrelated PR."""
    part = [{"rel": "part_of", "id": WS}]
    rows = [
        item(WS, "workstream", "active", "Widget search", fields={"epic": EPIC}),
        item(UI, "workstream", "active", "Widget UI", links=[{"rel": "part_of", "id": WS}]),
        item(
            PR,
            "my-pr",
            "merged",
            "Widget search index",
            links=part,
            url="https://github.com/acme/widgets/pull/12",
            history=[moved("merged", 1)],
        ),
        item(
            "mypr:acme/widgets#9", "my-pr", "merged", "Old merge", links=part, history=[moved("merged", -5)]
        ),
        item(PR2, "my-pr", "awaiting-review", "Preview pane", links=[{"rel": "part_of", "id": UI}]),
        item(f"out:{PR2}/1", "outreach", "sent", "Review the preview?", fields={"parent": PR2, "to": "Dana"}),
        item(
            f"out:{PR}/1",
            "outreach",
            "applied",
            "Index refresh: 1h or 1d?",
            fields={"parent": PR, "to": "Marco", "purpose": "decision", "answer": "1d", "replied_at": at(2)},
        ),
        item(
            TASK,
            "task",
            "delegated",
            "Port search to exporter",
            links=[*part, {"rel": "blocked_by", "id": f"out:{TASK}/1"}],
        ),
        item(f"out:{TASK}/1", "outreach", "sent", "Exporter owner?", fields={"parent": TASK, "to": "Priya"}),
        item(
            f"out:{TASK}/2",
            "outreach",
            "no-reply",
            "Exporter test data?",
            fields={"parent": TASK, "to": "Lee"},
        ),
        item(f"out:{PR}/2", "outreach", "no-reply", "Old question", fields={"parent": PR, "to": "Lee"}),
        item(TASK2, "task", "proposed", "Widget sizes", links=part),
        item(OTHER_PR, "my-pr", "awaiting-review", "Invoice totals"),
    ]
    return {row["id"]: row for row in rows}


class SummaryTest(unittest.TestCase):
    def summary(self, ws: str = WS) -> ws_summary.Summary:
        resolver = workstreams.Resolver(week())
        return ws_summary.workstream_summary(resolver, ws, MONDAY, ledger.is_open)

    def test_sections_from_the_fixture_week(self) -> None:
        found = self.summary()
        texts = {name: [e.text for e in entries] for name, entries in found.sections.items()}
        self.assertEqual(texts[ws_summary.SHIPPED], ["merged: Widget search index"])
        self.assertEqual(
            texts[ws_summary.IN_REVIEW],
            [f"Preview pane: waiting on reviewers; waiting on Dana (out:{PR2}/1)"],
        )
        self.assertEqual(texts[ws_summary.WAITING], ["Dana: Review the preview?", "Priya: Exporter owner?"])
        self.assertEqual(texts[ws_summary.DECISIONS], ["Index refresh: 1h or 1d?: 1d (Marco)"])
        # Outreach given up on blocks only while the work it was for is open.
        self.assertCountEqual(
            texts[ws_summary.BLOCKED],
            [
                f"Port search to exporter: blocked by out:{TASK}/1 (sent)",
                "no reply from Lee: Exporter test data?",
            ],
        )
        self.assertEqual(texts[ws_summary.NEXT], ["Widget sizes: proposed (awaits my yes)"])
        self.assertEqual(found.header, [f"epic {EPIC}", f"includes {UI}"])

    def test_child_entries_are_tagged_and_the_child_summary_has_only_its_own(self) -> None:
        tags = {e.item: e.ws for entries in self.summary().sections.values() for e in entries}
        self.assertEqual(tags[PR2], UI)
        self.assertEqual(tags[PR], "")
        child = self.summary(UI).counts()
        self.assertEqual(child[ws_summary.IN_REVIEW], 1)
        self.assertEqual(child[ws_summary.SHIPPED], 0)

    def test_markdown_links_and_all_with_unassigned(self) -> None:
        resolver = workstreams.Resolver(week())
        found = ws_summary.all_summaries(resolver, MONDAY, ledger.is_open)
        self.assertEqual([s.title for s in found][-1], ws_summary.UNASSIGNED_TITLE)
        text = ws_summary.render_all(found, markdown=True)
        self.assertIn(
            f"- merged: Widget search index ([{PR}](https://github.com/acme/widgets/pull/12))", text
        )
        self.assertIn("**Widget search (ws:widget-search, active)**, since Mon 2026-10-19", text)
        self.assertIn(f"- Invoice totals: waiting on reviewers (`{OTHER_PR}`)", text)
        plain = ws_summary.render_all(found)
        self.assertIn("Shipped (1)\n  - merged: Widget search index (mypr:acme/widgets#12)", plain)

    def test_week_start_is_monday_midnight(self) -> None:
        self.assertEqual(ws_summary.week_start(datetime(2026, 10, 21, 15, 30, tzinfo=UTC)), MONDAY)
        self.assertEqual(ws_summary.week_start(MONDAY), MONDAY)

    def test_since_takes_a_date(self) -> None:
        self.assertEqual(ledger.summary_since("2026-10-19").date(), date(2026, 10, 19))
        self.assertEqual(ledger.summary_since(None).weekday(), 0)


if __name__ == "__main__":
    unittest.main()
