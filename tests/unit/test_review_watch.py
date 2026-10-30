"""Unit tests for `bosun review-watch` (lib/review_watch.py): a new review on my PR after its
last hand-over (or report) is recorded once on the item, without a state change, and queued as a
`review` event; what counts as new, what is left out, dry runs, unreadable PRs, and the event's
line in the orchestrator's summary. GitHub is a fake; ledger times are pinned. Fictional data:
acme's invoice exporter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import event_model
import event_summary
import events
import ledger
import pr_threads
import review_watch


RENDER = event_summary.Render(inline_chars=600, max_lines=10)


PR = "mypr:acme/exporter#102"
PR_URL = "https://github.com/acme/exporter/pull/102"
OTHER_PR = "mypr:acme/exporter#103"
AUTHOR = "me-acme"
REVIEWER = "dana-acme"
WINDOW = "exporter"
CREATED = "2026-01-05T08:00:00+00:00"
HANDED_OVER = "2026-01-05T09:00:00+00:00"
REPORTED = "2026-01-05T11:00:00+00:00"


def run(*argv: str) -> Any:
    args = ledger.build_parser().parse_args(list(argv))
    return args.func(args)


@contextmanager
def at(moment: str) -> Iterator[None]:
    with mock.patch.object(ledger, "now", return_value=moment):
        yield


def review(
    review_id: str, submitted: str, state: str = "CHANGES_REQUESTED", author: str = REVIEWER
) -> pr_threads.Review:
    return pr_threads.Review(review_id, f"{PR_URL}#pullrequestreview-{review_id}", author, state, submitted)


def thread(review_id: str, last_by: str, number: int = 1) -> pr_threads.Thread:
    url = f"{PR_URL}#discussion_r{number}"
    first = pr_threads.Comment(REVIEWER, "2026-01-05T10:00:00Z", url, "please fix")
    last = pr_threads.Comment(last_by, "2026-01-05T10:30:00Z", url)
    return pr_threads.Thread(f"T{number}", "a.py", number, False, False, first, last, 2, review_id)


ROUND_1 = review("R1", "2026-01-05T08:30:00Z")  # before the hand-over
ROUND_2 = review("R2", "2026-01-05T10:00:00Z")  # while the window works
ROUND_3 = review("R3", "2026-01-05T12:00:00Z")  # after the report


def pull(*reviews: pr_threads.Review, threads: tuple[pr_threads.Thread, ...] = ()) -> pr_threads.PullRequest:
    return pr_threads.PullRequest(PR_URL, AUTHOR, reviews, threads)


class FakeFetch:
    def __init__(self, prs: dict[str, pr_threads.PullRequest | str]) -> None:
        self.prs = prs
        self.urls: list[str] = []

    def __call__(
        self, url: str, seconds: float, reviewers: pr_threads.ReviewerBots
    ) -> pr_threads.PullRequest:
        self.urls.append(url)
        self.reviewers = reviewers
        found = self.prs[url]
        if isinstance(found, str):
            raise pr_threads.PrThreadsError(found)
        return found


class ReviewWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"
        ledger.configure(self.state, events_enabled=True, caller_window="checkin")
        self.addCleanup(ledger.configure, self.state)
        with at(CREATED):
            run("upsert", PR, "--kind", "my-pr", "--title", "footer", "--url", PR_URL)
            run("transition", PR, "changes-requested", "--note", "round 1")
            run("transition", PR, "addressing", "--note", "fixing")
        with at(HANDED_OVER):
            run("record-delegation", PR, "--window", WINDOW, "--task", "fix-my-pr")

    def item(self, item_id: str = PR) -> dict[str, Any]:
        found: dict[str, Any] = json.loads(ledger.STORE.ledger_file.read_text())["items"][item_id]
        return found

    def arrived(self, item_id: str = PR) -> list[dict[str, Any]]:
        return [h for h in self.item(item_id)["history"] if h["event"] == review_watch.ARRIVED]

    def queued(self) -> list[event_model.Event]:
        return [e for e in events.read_queue(events.events_dir(self.state)) if e.type == review_watch.EVENT]

    def watch(self, pr: pr_threads.PullRequest, *ids: str, **options: Any) -> list[str]:
        return review_watch.watch(
            list(ids), options.pop("dry_run", False), FakeFetch({PR_URL: pr}), **options
        )

    def test_a_review_after_the_hand_over_is_recorded_once_and_queued(self) -> None:
        state_before = self.item()["state"]
        lines = self.watch(pull(ROUND_1, ROUND_2, threads=(thread("R2", REVIEWER),)))
        self.assertEqual(len(lines), 1)
        self.assertIn(
            f"{PR} [addressing] (held by {WINDOW}): new review CHANGES_REQUESTED by {REVIEWER}", lines[0]
        )
        self.assertIn("1 thread started; 1 awaiting the author now (round 1: 0 threads", lines[0])
        self.assertIn(f"bosun delegate --update '{PR}'", lines[0])
        self.assertIn(f"bosun pr-threads '{PR_URL}' --awaiting-author", lines[0])
        (entry,) = self.arrived()
        self.assertEqual(entry["review"], ROUND_2.url)
        self.assertEqual(entry["submitted_at"], ROUND_2.at)
        self.assertEqual(self.item()["state"], state_before)  # no state change
        (event,) = self.queued()
        self.assertEqual((event.item, event.window, event.origin), (PR, WINDOW, "checkin"))
        # again: nothing new recorded or queued, but still listed while a thread awaits the author
        lines = self.watch(pull(ROUND_1, ROUND_2, threads=(thread("R2", REVIEWER),)))
        self.assertIn("review (recorded before)", lines[0])
        self.assertEqual((len(self.arrived()), len(self.queued())), (1, 1))

    def test_a_hand_over_after_it_settles_it(self) -> None:
        self.watch(pull(ROUND_2))
        with at("2026-01-05T10:15:00+00:00"):
            run("record-update", PR, "--text", "also the round 2 threads")
        self.assertEqual(self.watch(pull(ROUND_2)), ["no new reviews on 1 PR(s)"])

    def test_bot_reviews_are_never_raised(self) -> None:
        bot = replace(review("B1", "2026-01-05T10:00:00Z", "COMMENTED", "acme-review-bot"), bot=True)
        bot_changes = replace(review("B2", "2026-01-05T10:05:00Z", author="lint-acme[bot]"), bot=True)
        self.assertEqual(self.watch(pull(bot, bot_changes)), ["no new reviews on 1 PR(s)"])
        self.assertEqual((self.arrived(), self.queued()), ([], []))

    def test_a_reviewer_bots_review_is_raised_with_its_verdict(self) -> None:
        automated = pr_threads.Review(
            "V1",
            f"{PR_URL}#pullrequestreview-V1",
            "acme-reviewer",
            "COMMENTED",
            "2026-01-05T10:00:00Z",
            "## Verdict: Request changes",
            automated=True,
            verdict="Request changes",
        )
        reviewers = pr_threads.ReviewerBots.from_settings("acme-reviewer", "")
        fetch = FakeFetch({PR_URL: pull(automated)})
        (line,) = review_watch.watch([], False, fetch, reviewers=reviewers)
        self.assertIn("new review COMMENTED by acme-reviewer (automated verdict: Request changes)", line)
        self.assertIs(fetch.reviewers, reviewers)

    def test_what_is_left_out(self) -> None:
        mine = review("A1", "2026-01-05T10:00:00Z", "COMMENTED", AUTHOR)
        approval = review("R4", "2026-01-05T10:00:00Z", "APPROVED")
        pending = review("R5", "2026-01-05T10:00:00Z", "PENDING")
        self.assertEqual(self.watch(pull(ROUND_1, mine, approval, pending)), ["no new reviews on 1 PR(s)"])
        self.assertEqual(self.arrived(), [])

    def test_a_transition_does_not_hide_a_review(self) -> None:
        # the orchestrator moved the item on without having seen the review
        with at("2026-01-05T10:30:00+00:00"):
            run("transition", PR, "awaiting-review", "--note", "pushed")
        lines = self.watch(pull(ROUND_2))
        self.assertIn(f"{PR} [awaiting-review]", lines[0])
        self.assertIn("chores step 4", lines[0])

    def test_after_a_report_only_later_reviews_count_outside_addressing(self) -> None:
        with at(REPORTED):
            run("report", PR, "--summary", "pushed the fix", "--ci", "green")
            run("transition", PR, "awaiting-review", "--note", "re-review asked")
        self.assertEqual(self.watch(pull(ROUND_2)), ["no new reviews on 1 PR(s)"])
        lines = self.watch(pull(ROUND_2, ROUND_3))
        self.assertEqual(len(lines), 1)
        self.assertIn("R3", self.arrived()[0]["review"])

    def test_a_comment_outside_addressing_is_read_not_fixed(self) -> None:
        with at(REPORTED):
            run("transition", PR, "awaiting-review", "--note", "re-review asked")
        (text,) = self.watch(pull(review("R7", "2026-01-05T12:00:00Z", "COMMENTED")))
        self.assertIn(f"read it (gh pr view '{PR_URL}' --json reviews)", text)
        self.assertNotIn("fix-my-pr", text)

    def test_a_comment_answered_since_is_not_listed_again(self) -> None:
        comment = review("R6", "2026-01-05T10:00:00Z", "COMMENTED")
        self.assertEqual(len(self.watch(pull(comment, threads=(thread("R6", REVIEWER),)))), 1)
        self.assertEqual(
            self.watch(pull(comment, threads=(thread("R6", AUTHOR),))), ["no new reviews on 1 PR(s)"]
        )

    def test_dry_run_writes_nothing(self) -> None:
        before = ledger.STORE.ledger_file.read_text()
        lines = self.watch(pull(ROUND_2), dry_run=True)
        self.assertIn("new review", lines[0])
        self.assertEqual(ledger.STORE.ledger_file.read_text(), before)
        self.assertEqual(self.queued(), [])

    def test_nothing_new_leaves_the_ledger_file_alone(self) -> None:
        before = ledger.STORE.ledger_file.stat().st_mtime_ns
        self.watch(pull(ROUND_1))
        self.assertEqual(ledger.STORE.ledger_file.stat().st_mtime_ns, before)

    def test_quiet_prints_only_new_reviews(self) -> None:
        self.assertEqual(self.watch(pull(ROUND_1), quiet=True), [])
        self.assertEqual(len(self.watch(pull(ROUND_2), quiet=True)), 1)
        self.assertEqual(self.watch(pull(ROUND_2), quiet=True), [])

    def test_which_items_are_watched(self) -> None:
        with at(CREATED):
            run("upsert", OTHER_PR, "--kind", "my-pr", "--title", "rounding")
            run("transition", OTHER_PR, "changes-requested", "--note", "comments")
        other_url = "https://github.com/acme/exporter/pull/103"
        fetch = FakeFetch({PR_URL: pull(ROUND_2), other_url: "gh timed out after 20s"})
        lines = review_watch.watch([], False, fetch)
        self.assertEqual(fetch.urls, [PR_URL])  # changes-requested is not watched
        lines = review_watch.watch([OTHER_PR], False, fetch)
        self.assertEqual(lines, [f"warn: {OTHER_PR}: gh timed out after 20s"])

    def test_the_recorded_review_does_not_end_the_hand_over(self) -> None:
        self.watch(pull(ROUND_2))
        self.assertTrue(ledger.awaiting_report(self.item()))


class ReviewEventTest(unittest.TestCase):
    def test_the_summary_line_and_what_to_do(self) -> None:
        now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        new = event_model.new_event(
            "review", item=PR, window=WINDOW, summary="CHANGES_REQUESTED by dana-acme", now=now
        )
        report = event_model.new_event("report", item=PR, window=WINDOW, summary="pushed", now=now)
        slack = event_model.new_event("slack", summary="a mention", now=now)
        text = event_summary.compose([new], RENDER)
        self.assertIn("1 new from GitHub since", text)
        self.assertIn(f"new review on {PR} (held by {WINDOW}): CHANGES_REQUESTED by dana-acme", text)
        self.assertIn(event_summary.REVIEW_TAIL, text)
        self.assertIn("from the repo sessions and GitHub", event_summary.compose([report, new], RENDER))
        self.assertIn(
            "from the repo sessions, GitHub and Slack", event_summary.compose([report, new, slack], RENDER)
        )
        self.assertNotIn(event_summary.REVIEW_TAIL, event_summary.compose([report], RENDER))
