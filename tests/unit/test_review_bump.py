"""Unit tests for the guard's review-bump check (BOSUN_APPROVALS_CHECK_REVIEW_THREADS): an
allowed call that asks for a re-review on my PR goes out only while the live PR agrees (no thread
awaits the author, no review newer than the proposed message); when the PR cannot be read, Claude
Code asks. GitHub is a fake. Fictional data: acme's invoice exporter."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard
import ledger
import pr_threads
from test_approvals import (
    CHANNEL,
    PR,
    PR_URL,
    QUOTE,
    REVIEW_REQUEST,
    SEND,
    TEXT,
    THREAD,
    StandingTestCase,
    run,
    typed,
)


AUTHOR = "me-acme"
REVIEWER = "dana-acme"
OLD = "2000-01-01T00:00:00Z"
LATER = "2999-01-01T00:00:00Z"
CHECK = {"BOSUN_APPROVALS_CHECK_REVIEW_THREADS": "1"}
EDIT = f"gh pr edit {PR_URL} --add-reviewer {REVIEWER}"


def comment(author: str, url: str = f"{PR_URL}#discussion_r1") -> pr_threads.Comment:
    return pr_threads.Comment(author, OLD, url, "please fix")


def thread(last_by: str, number: int = 1) -> pr_threads.Thread:
    url = f"{PR_URL}#discussion_r{number}"
    return pr_threads.Thread(
        f"T{number}", "a.py", number, False, False, comment(REVIEWER, url), comment(last_by), 2, "R1"
    )


def review(at: str = OLD, state: str = "CHANGES_REQUESTED", author: str = REVIEWER) -> pr_threads.Review:
    return pr_threads.Review(
        "R1" if at == OLD else f"R-{at}", f"{PR_URL}#pullrequestreview-1", author, state, at
    )


def pull(
    *threads: pr_threads.Thread, reviews: tuple[pr_threads.Review, ...] = (review(),)
) -> pr_threads.PullRequest:
    return pr_threads.PullRequest(PR_URL, AUTHOR, reviews, threads)


ANSWERED = pull(thread(AUTHOR))


class FakeFetch:
    def __init__(self, pr: pr_threads.PullRequest | None = None, error: str = "") -> None:
        self.pr, self.error = pr, error
        self.urls: list[str] = []

    def __call__(
        self, url: str, seconds: float, reviewers: pr_threads.ReviewerBots
    ) -> pr_threads.PullRequest:
        self.reviewers = reviewers
        self.urls.append(url)
        self.assertable_seconds = seconds
        if self.error:
            raise pr_threads.PrThreadsError(self.error)
        assert self.pr is not None
        return self.pr


class ReviewBumpTest(StandingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.report()  # ready for reviewers: CI green, nothing open
        self.env.update(CHECK)

    def with_pr(self, fetch: FakeFetch) -> Any:
        return mock.patch.object(guard, "FETCH_PR", fetch)

    def test_all_answered_goes_out(self) -> None:
        fetch = FakeFetch(ANSWERED)
        call = self.review_request()
        with self.with_pr(fetch):
            self.assertIsNone(self.denial(call))
            self.assertTrue(self.allowed(call))
        self.assertEqual(fetch.urls, [PR_URL, PR_URL])
        self.assertLessEqual(fetch.assertable_seconds, guard.BUMP_CHECK_SECONDS)

    def test_a_thread_awaiting_the_author_stops_it(self) -> None:
        fetch = FakeFetch(pull(thread(AUTHOR), thread(REVIEWER, 2)))
        call = self.review_request()
        with self.with_pr(fetch):
            reason = self.denial(call) or ""
            self.assertFalse(self.allowed(call))
        self.assertIn("1 review thread(s) await a reply from the author", reason)
        self.assertIn(f"{PR_URL}#discussion_r2", reason)
        self.assertIn("round 1: 2 threads, 1 awaiting", reason)
        self.assertIn(f"bosun pr-threads '{PR_URL}' --awaiting-author", reason)
        # the standing approval was not used up: once the thread is answered it goes out
        self.assertNotEqual(self.item(REVIEW_REQUEST)["history"][-1]["event"], ledger.AUTO_APPROVED)
        with self.with_pr(FakeFetch(ANSWERED)):
            self.assertTrue(self.allowed(call))

    def test_a_review_newer_than_the_message_stops_it(self) -> None:
        later = review(LATER, "COMMENTED")
        call = self.review_request()
        with self.with_pr(FakeFetch(pull(thread(AUTHOR), reviews=(review(), later)))):
            reason = self.denial(call) or ""
            self.assertFalse(self.allowed(call))
        self.assertIn("1 review(s) came after the message was proposed", reason)
        self.assertIn(f"COMMENTED by {REVIEWER} at {LATER}", reason)

    def test_bots_never_stop_it(self) -> None:
        bot_review = pr_threads.Review(
            "B1", f"{PR_URL}#pullrequestreview-9", "acme-review-bot", "COMMENTED", LATER, bot=True
        )
        bot_first = pr_threads.Comment("acme-review-bot", OLD, f"{PR_URL}#discussion_r9", "nit", bot=True)
        bot_thread = pr_threads.Thread("T9", "a.py", 9, False, False, bot_first, bot_first, 1, "B1")
        call = self.review_request()
        with self.with_pr(FakeFetch(pull(thread(AUTHOR), bot_thread, reviews=(review(), bot_review)))):
            self.assertIsNone(self.denial(call))
            self.assertTrue(self.allowed(call))

    def test_my_own_replies_are_not_newer_reviews(self) -> None:
        mine = review(LATER, "COMMENTED", AUTHOR)
        call = self.review_request()
        with self.with_pr(FakeFetch(pull(thread(AUTHOR), reviews=(review(), mine)))):
            self.assertTrue(self.allowed(call))

    def test_an_unreadable_pr_is_asked_about_never_sent_unchecked(self) -> None:
        call = self.review_request()
        with self.with_pr(FakeFetch(error="gh timed out after 20s")):
            result = self.respond("--approvals", call)
            self.assertFalse(self.allowed(call))
        assert result is not None
        self.assertEqual(result["permissionDecision"], "ask")
        self.assertIn("could not read", result["permissionDecisionReason"])
        self.assertIn("gh timed out", result["permissionDecisionReason"])

    def test_off_no_network(self) -> None:
        fetch = FakeFetch(error="must not be called")
        call = self.review_request()
        with self.with_pr(fetch):
            self.assertTrue(self.allowed(call, BOSUN_APPROVALS_CHECK_REVIEW_THREADS="0"))
        self.assertEqual(fetch.urls, [])

    def test_other_messages_are_not_checked(self) -> None:
        fetch = FakeFetch(error="must not be called")
        call = self.review_request(purpose="info-ack")
        with self.with_pr(fetch):
            self.assertTrue(self.allowed(call))
        self.assertEqual(fetch.urls, [])

    def test_a_refused_call_is_not_checked(self) -> None:
        fetch = FakeFetch(error="must not be called")
        with self.with_pr(fetch):
            other = self.event(SEND, channel_id=CHANNEL, thread_ts=THREAD, message="unrelated")
            self.assertIn("no proposed outreach message matches", self.denial(other) or "")
        self.assertEqual(fetch.urls, [])

    def test_a_standing_re_review_comment_is_checked(self) -> None:
        comment = self.event("Bash", command=f"gh pr comment {PR_URL} --body /review-pr")
        env = {"BOSUN_STANDING_GH_COMMENTS": "/review-pr"}
        fetch = FakeFetch(pull(thread(REVIEWER)))
        with self.with_pr(fetch):
            self.assertIn("1 review thread(s) await a reply", self.denial(comment, **env) or "")
            self.assertFalse(self.allowed(comment, **env))
        with self.with_pr(FakeFetch(ANSWERED)):
            self.assertIsNone(self.denial(comment, **env))
            self.assertTrue(self.allowed(comment, **env))

    def test_a_reviewer_bots_thread_stops_it_and_the_settings_reach_the_reader(self) -> None:
        bot = pr_threads.Comment("acme-reviewer", OLD, f"{PR_URL}#discussion_r2", "fix", automated=True)
        automated = pr_threads.Thread("T2", "a.py", 2, False, False, bot, bot, 1, "R1")
        fetch = FakeFetch(pull(thread(AUTHOR), automated))
        env = {"BOSUN_REVIEWER_BOTS": "acme-reviewer"}
        with self.with_pr(fetch):
            self.assertIn("1 review thread(s) await a reply", self.denial(self.review_request(), **env) or "")
        self.assertEqual(fetch.reviewers.logins, frozenset({"acme-reviewer"}))

    def test_a_bad_verdict_pattern_asks(self) -> None:
        env = {"BOSUN_REVIEWER_BOTS": "acme-reviewer", "BOSUN_REVIEWER_BOT_VERDICT_PATTERN": "("}
        with self.with_pr(FakeFetch(ANSWERED)):
            result = self.respond("--approvals", self.review_request(), **env)
        assert result is not None
        self.assertEqual(result["permissionDecision"], "ask")
        self.assertIn("not a regular expression", result["permissionDecisionReason"])

    def test_an_approved_add_reviewer_is_checked(self) -> None:
        run("propose", PR, "--run", EDIT)
        self.say(typed(QUOTE, datetime.now(UTC)))
        run("approve", PR, "--quote", QUOTE)
        call = self.event("Bash", command=EDIT)
        with self.with_pr(FakeFetch(pull(thread(REVIEWER)))):
            self.assertIn("await a reply from the author", self.denial(call) or "")
            self.assertFalse(self.allowed(call))
        with self.with_pr(FakeFetch(ANSWERED)):
            self.assertTrue(self.allowed(call))

    def test_outreach_names_its_pr_without_a_pr_parent(self) -> None:
        item = {"kind": "outreach", "fields": {"purpose": "review", "subject": f"another look at {PR_URL}?"}}
        self.assertEqual(guard.outreach_pr(item), PR_URL)
        self.assertEqual(guard.outreach_pr({"kind": "outreach", "fields": {"purpose": "review"}}), "")
        self.assertEqual(guard.outreach_pr({"fields": {"parent": PR}}), PR_URL)

    def test_the_message_time_is_its_proposal(self) -> None:
        self.review_request()
        proposal = self.item(REVIEW_REQUEST)["proposal"]
        self.assertEqual(guard.proposed_at(self.item(REVIEW_REQUEST)), proposal["proposed_at"])
        self.assertEqual(guard.proposed_at({"created_at": OLD}), OLD)
        self.assertIn(PR_URL, TEXT)
