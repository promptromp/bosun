"""Unit tests for `bosun pr-threads` (lib/pr_threads.py): replies and the last commenter per
thread, which threads await the PR's author, review rounds, paging, errors, the text and JSON
output and the exit codes. GitHub is a fake runner. Fictional data: acme's invoice exporter."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import pr_threads


URL = "https://github.com/acme/exporter/pull/102"
AUTHOR = "me-acme"
REVIEWER = "dana-acme"
OTHER = "marco-acme"


def comment(author: str, at: str, url: str, body: str = "", review: str = "") -> dict[str, Any]:
    node: dict[str, Any] = {"author": {"login": author}, "createdAt": at, "url": url, "body": body}
    if review:
        node["pullRequestReview"] = {"id": review}
    return node


def thread_node(
    number: int, review: str, replies: list[str], resolved: bool = False, author: str = REVIEWER
) -> dict[str, Any]:
    """A thread started in <review> by <author>, then one reply per name in <replies>."""
    first = comment(
        author, f"2026-01-0{number}T10:00:00Z", f"{URL}#discussion_r{number}", "please fix", review
    )
    last = (
        comment(replies[-1], f"2026-01-0{number}T12:00:00Z", f"{URL}#discussion_r{number}9")
        if replies
        else first
    )
    return {
        "id": f"T{number}",
        "isResolved": resolved,
        "isOutdated": False,
        "path": "exporter/footer.py",
        "line": number,
        "first": {"totalCount": 1 + len(replies), "nodes": [first]},
        "last": {"nodes": [last]},
    }


def review_node(review_id: str, author: str, state: str, at: str, body: str = "") -> dict[str, Any]:
    return {
        "id": review_id,
        "url": f"{URL}#pullrequestreview-{review_id}",
        "author": {"login": author},
        "state": state,
        "submittedAt": at,
        "body": body,
    }


ROUND_1 = review_node("R1", REVIEWER, "CHANGES_REQUESTED", "2026-01-01T09:00:00Z")
REPLY_REVIEW = review_node("A1", AUTHOR, "COMMENTED", "2026-01-01T12:00:00Z")
ROUND_2 = review_node("R2", REVIEWER, "CHANGES_REQUESTED", "2026-01-03T09:00:00Z")
THREAD_REPLY = review_node("R3", OTHER, "COMMENTED", "2026-01-04T09:00:00Z")  # no body, no threads


def page(
    threads: list[dict[str, Any]], reviews: list[dict[str, Any]] | None = None, cursor: str = ""
) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "url": URL,
                    "author": {"login": AUTHOR},
                    "reviews": {
                        "nodes": reviews if reviews is not None else [ROUND_1, REPLY_REVIEW, ROUND_2]
                    },
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": bool(cursor), "endCursor": cursor or None},
                        "nodes": threads,
                    },
                }
            }
        }
    }


# Round 1: two threads, both answered by the author; round 2: one thread answered, one where the
# reviewer replied last (awaits the author again), one never answered; a resolved thread.
THREADS = [
    thread_node(1, "R1", [AUTHOR]),
    thread_node(2, "R1", [AUTHOR]),
    thread_node(3, "R2", [AUTHOR]),
    thread_node(4, "R2", [AUTHOR, REVIEWER]),
    thread_node(5, "R2", []),
    thread_node(6, "R1", [], resolved=True),
]


class FakeGh:
    """Answers each call with the next page; records the arguments and the time it was given."""

    def __init__(self, *pages: dict[str, Any]) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, args: list[str], timeout: float) -> str:
        self.calls.append((args, timeout))
        return json.dumps(self.pages.pop(0))


def fetched(*pages: dict[str, Any]) -> pr_threads.PullRequest:
    return pr_threads.fetch(URL, runner=FakeGh(*pages))


class AutoMergeTest(unittest.TestCase):
    """Whether the PR's repository allows auto-merge: the policy reads it here, since `gh api`
    always asks."""

    def with_repo(self, allowed: object) -> dict[str, Any]:
        data = page(THREADS)
        data["data"]["repository"]["pullRequest"]["baseRepository"] = {"autoMergeAllowed": allowed}
        return data

    def test_shown_when_github_says(self) -> None:
        allowed = fetched(self.with_repo(True))
        self.assertTrue(allowed.auto_merge_allowed)
        self.assertIn("auto-merge: allowed in this repository", pr_threads.render(allowed, False))
        refused = fetched(self.with_repo(False))
        self.assertIn("auto-merge: not allowed in this repository", pr_threads.render(refused, False))
        self.assertFalse(pr_threads.as_json(refused, False)["auto_merge_allowed"])

    def test_left_out_when_unknown(self) -> None:
        unknown = fetched(page(THREADS))
        self.assertIsNone(unknown.auto_merge_allowed)
        self.assertNotIn("auto-merge", pr_threads.render(unknown, False))
        self.assertIsNone(fetched(self.with_repo("yes")).auto_merge_allowed)

    def test_the_query_asks_for_it(self) -> None:
        self.assertIn("baseRepository { autoMergeAllowed }", pr_threads.QUERY)


class ThreadsTest(unittest.TestCase):
    def test_replies_and_the_last_commenter(self) -> None:
        pr = fetched(page(THREADS))
        self.assertEqual(pr.author, AUTHOR)
        by_id = {t.id: t for t in pr.threads}
        self.assertEqual((by_id["T1"].replies, by_id["T1"].last.author), (1, AUTHOR))
        self.assertEqual((by_id["T4"].replies, by_id["T4"].last.author), (2, REVIEWER))
        self.assertEqual((by_id["T5"].replies, by_id["T5"].last.author), (0, REVIEWER))

    def test_a_thread_awaits_the_author_until_the_author_commented_last(self) -> None:
        pr = fetched(page(THREADS))
        self.assertEqual([t.id for t in pr_threads.awaiting(pr)], ["T4", "T5"])
        self.assertEqual(len(pr_threads.unresolved(pr)), 5)

    def test_a_reply_by_someone_else_does_not_answer_it(self) -> None:
        pr = fetched(page([thread_node(1, "R1", [OTHER])]))
        self.assertEqual([t.id for t in pr_threads.awaiting(pr)], ["T1"])

    def test_with_no_author_known_every_open_thread_awaits(self) -> None:
        data = page(THREADS[:1])
        data["data"]["repository"]["pullRequest"]["author"] = None
        self.assertEqual(len(pr_threads.awaiting(fetched(data))), 1)

    def test_rounds_count_the_threads_each_review_started(self) -> None:
        pr = fetched(page(THREADS, [ROUND_1, REPLY_REVIEW, ROUND_2, THREAD_REPLY]))
        found = [(r.number, r.review.id, r.threads, r.awaiting) for r in pr_threads.rounds(pr)]
        # the author's replies and a reviewer's bare thread reply are no rounds
        self.assertEqual(found, [(1, "R1", 3, 0), (2, "R2", 3, 2)])
        self.assertEqual(
            pr_threads.round_summary(pr), "round 1: 3 threads, 0 awaiting; round 2: 3 threads, 2 awaiting"
        )

    def test_a_review_with_a_body_and_no_threads_is_a_round(self) -> None:
        summary_only = review_node("R4", OTHER, "COMMENTED", "2026-01-05T09:00:00Z", body="Looks close.")
        pr = fetched(page([], [summary_only]))
        self.assertEqual([(r.review.id, r.threads) for r in pr_threads.rounds(pr)], [("R4", 0)])
        self.assertEqual(pr_threads.round_summary(fetched(page([], []))), "no review rounds")

    def test_reviews_after_a_time_leave_out_the_author_and_pending_ones(self) -> None:
        pending = review_node("P1", OTHER, "PENDING", "2026-01-06T09:00:00Z")
        mine = review_node("A2", AUTHOR, "COMMENTED", "2026-01-06T09:00:00Z")
        pr = fetched(page([], [ROUND_1, ROUND_2, pending, mine]))
        self.assertEqual([r.id for r in pr_threads.reviews_after(pr, "2026-01-02T00:00:00+00:00")], ["R2"])
        self.assertEqual(pr_threads.reviews_after(pr, "2026-01-03T09:00:00"), [])  # naive: UTC


class FetchTest(unittest.TestCase):
    def test_threads_are_paged(self) -> None:
        gh = FakeGh(page(THREADS[:2], cursor="abc"), page(THREADS[2:]))
        pr = pr_threads.fetch(URL, runner=gh)
        self.assertEqual(len(pr.threads), len(THREADS))
        first, second = (args for args, _ in gh.calls)
        self.assertNotIn("cursor=abc", " ".join(first))
        self.assertIn("cursor=abc", second)
        self.assertIn("owner=acme", first)
        self.assertIn("number=102", first)

    def test_each_call_gets_the_time_left(self) -> None:
        gh = FakeGh(page(THREADS))
        pr_threads.fetch(URL, 5.0, gh)
        self.assertLessEqual(gh.calls[0][1], 5.0)
        with (
            mock.patch("time.monotonic", side_effect=[0.0, 10.0]),
            self.assertRaisesRegex(pr_threads.PrThreadsError, "more than 5s"),
        ):
            pr_threads.fetch(URL, 5.0, FakeGh(page(THREADS)))

    def test_errors(self) -> None:
        with self.assertRaisesRegex(pr_threads.PrThreadsError, "not a GitHub PR URL"):
            pr_threads.fetch("https://example.com/x", runner=FakeGh())
        with self.assertRaisesRegex(pr_threads.PrThreadsError, "GitHub: Could not resolve"):
            fetched({"data": {"repository": None}, "errors": [{"message": "Could not resolve"}]})
        with self.assertRaisesRegex(pr_threads.PrThreadsError, "no JSON"):
            pr_threads.fetch(URL, runner=lambda _args, _timeout: "<html>")

    def test_gh_failures_are_errors(self) -> None:
        failed = subprocess.CompletedProcess(["gh"], 1, "", "HTTP 502")
        with (
            mock.patch("subprocess.run", return_value=failed),
            self.assertRaisesRegex(pr_threads.PrThreadsError, "gh failed: HTTP 502"),
        ):
            pr_threads.run_gh(["api"], 1.0)
        timeout = subprocess.TimeoutExpired(["gh"], 1.0)
        with (
            mock.patch("subprocess.run", side_effect=timeout),
            self.assertRaisesRegex(pr_threads.PrThreadsError, "timed out"),
        ):
            pr_threads.run_gh(["api"], 1.0)
        with (
            mock.patch("subprocess.run", side_effect=FileNotFoundError),
            self.assertRaisesRegex(pr_threads.PrThreadsError, "not installed"),
        ):
            pr_threads.run_gh(["api"], 1.0)


BOT = "acme-review-bot"  # a GitHub App: GraphQL type Bot, no [bot] in its login
SUFFIX_BOT = "lint-acme[bot]"


def by_bot(node: dict[str, Any], typename: str = "Bot") -> dict[str, Any]:
    """<node> (a review or a thread) with its author, and a thread's first comment's, typed <typename>."""
    for target in (node, *(node.get("first") or {}).get("nodes", [])):
        if "author" in target:
            target["author"]["__typename"] = typename
    return node


def bot_page() -> dict[str, Any]:
    """A person's round with one open thread, and two bot rounds with open, unanswered threads."""
    reviews = [
        ROUND_1,
        by_bot(review_node("B1", BOT, "COMMENTED", "2026-01-02T09:00:00Z", body="Summary.")),
        review_node("B2", SUFFIX_BOT, "CHANGES_REQUESTED", "2026-01-03T09:00:00Z"),
    ]
    threads = [
        thread_node(1, "R1", []),
        by_bot(thread_node(2, "B1", [], author=BOT)),
        thread_node(3, "B2", [], author=SUFFIX_BOT),
    ]
    return page(threads, reviews)


class BotTest(unittest.TestCase):
    """Bots (type Bot, or a login ending in [bot]) are shown, marked, but never count."""

    def test_bot_threads_never_await_the_author(self) -> None:
        pr = fetched(bot_page())
        self.assertEqual([t.id for t in pr_threads.awaiting(pr)], ["T1"])
        self.assertEqual([t.bot for t in pr.threads], [False, True, True])
        self.assertEqual(
            [(r.review.id, r.review.bot, r.awaiting) for r in pr_threads.rounds(pr)],
            [("R1", False, 1), ("B1", True, 0), ("B2", True, 0)],
        )
        self.assertIn("round 2 (bot): 1 thread, 0 awaiting", pr_threads.round_summary(pr))

    def test_a_bot_reply_does_not_answer_a_person(self) -> None:
        pr = fetched(page([thread_node(1, "R1", [SUFFIX_BOT])]))
        self.assertEqual([t.id for t in pr_threads.awaiting(pr)], ["T1"])

    def test_bot_reviews_are_never_newer(self) -> None:
        pr = fetched(bot_page())
        self.assertEqual(pr_threads.reviews_after(pr, "2026-01-01T12:00:00+00:00"), [])

    def test_shown_and_marked_but_never_exit_3(self) -> None:
        only_bots = page(
            [by_bot(thread_node(2, "B1", [], author=BOT))],
            [by_bot(review_node("B1", BOT, "COMMENTED", "2026-01-02T09:00:00Z", "x"))],
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pr_threads.main([URL, "--awaiting-author"], FakeGh(only_bots))
        self.assertEqual(code, pr_threads.EXIT_OK)
        self.assertIn(f"COMMENTED by {BOT} (bot)", out.getvalue())
        with contextlib.redirect_stdout(out := io.StringIO()):
            pr_threads.main([URL], FakeGh(only_bots))
        self.assertIn(f"- {BOT} (bot) on exporter/footer.py:2", out.getvalue())
        self.assertIn("(a bot's thread: never awaits the author)", out.getvalue())
        with contextlib.redirect_stdout(out := io.StringIO()):
            pr_threads.main([URL, "--json"], FakeGh(only_bots))
        data = json.loads(out.getvalue())
        self.assertEqual(
            (data["awaiting_author"], data["threads"][0]["bot"], data["rounds"][0]["bot"]), (0, True, True)
        )


REVIEWER_BOT = "acme-reviewer"  # an automated reviewer the site relies on (a GitHub App: type Bot)
VERDICT_BODY = "## Verdict: Request changes\n\nThe footer drops the total."
REVIEWERS = pr_threads.ReviewerBots.from_settings(f"{REVIEWER_BOT.upper()} other-reviewer", "")


def reviewer_bot_page(state: str = "COMMENTED", body: str = VERDICT_BODY) -> dict[str, Any]:
    """An automated reviewer's round with two open threads (one answered by the author), and an
    ordinary bot's round with one open thread."""
    reviews = [
        by_bot(review_node("V1", REVIEWER_BOT, state, "2026-01-02T09:00:00Z", body=body)),
        by_bot(review_node("B1", BOT, "COMMENTED", "2026-01-03T09:00:00Z", body="Lint.")),
    ]
    threads = [
        by_bot(thread_node(1, "V1", [], author=REVIEWER_BOT)),
        by_bot(thread_node(2, "V1", [AUTHOR], author=REVIEWER_BOT)),
        by_bot(thread_node(3, "B1", [], author=BOT)),
    ]
    return page(threads, reviews)


def reviewer_fetched(
    data: dict[str, Any], reviewers: pr_threads.ReviewerBots = REVIEWERS
) -> pr_threads.PullRequest:
    return pr_threads.fetch(URL, runner=FakeGh(data), reviewers=reviewers)


class ReviewerBotTest(unittest.TestCase):
    """Reviewer bots (BOSUN_REVIEWER_BOTS) count like people; other bots still never count."""

    def test_their_threads_await_the_author_and_other_bots_still_do_not(self) -> None:
        pr = reviewer_fetched(reviewer_bot_page())
        self.assertEqual([t.id for t in pr_threads.awaiting(pr)], ["T1"])
        self.assertEqual(
            [(t.bot, t.automated) for t in pr.threads], [(False, True), (False, True), (True, False)]
        )
        found = [(r.review.id, r.threads, r.awaiting) for r in pr_threads.rounds(pr)]
        self.assertEqual(found, [("V1", 2, 1), ("B1", 1, 0)])
        self.assertIn("round 1 (automated): 2 threads, 1 awaiting", pr_threads.round_summary(pr))

    def test_without_the_setting_they_are_ordinary_bots(self) -> None:
        pr = reviewer_fetched(reviewer_bot_page(), pr_threads.NO_REVIEWER_BOTS)
        self.assertEqual(pr_threads.awaiting(pr), [])
        self.assertEqual(pr_threads.verdicts(pr), [])

    def test_their_reviews_can_be_newer(self) -> None:
        pr = reviewer_fetched(reviewer_bot_page())
        self.assertEqual([r.id for r in pr_threads.reviews_after(pr, "2026-01-01T00:00:00+00:00")], ["V1"])

    def test_the_latest_verdict_is_read_from_the_body(self) -> None:
        older = by_bot(
            review_node("V0", REVIEWER_BOT, "COMMENTED", "2026-01-01T09:00:00Z", "## Verdict: Comments")
        )
        data = reviewer_bot_page()
        data["data"]["repository"]["pullRequest"]["reviews"]["nodes"].insert(0, older)
        pr = reviewer_fetched(data)
        self.assertEqual([(r.id, r.verdict) for r in pr_threads.verdicts(pr)], [("V1", "Request changes")])

    def test_a_body_without_a_verdict_and_a_custom_pattern(self) -> None:
        pr = reviewer_fetched(reviewer_bot_page(body="Looks fine."))
        self.assertEqual(pr_threads.verdicts(pr), [])
        custom = pr_threads.ReviewerBots.from_settings(REVIEWER_BOT, r"^Result: (\w+)")
        pr = reviewer_fetched(reviewer_bot_page(body="intro\nResult: approvable\n"), custom)
        self.assertEqual([r.verdict for r in pr_threads.verdicts(pr)], ["approvable"])

    def test_an_invalid_pattern_is_an_error(self) -> None:
        with self.assertRaisesRegex(pr_threads.PrThreadsError, "not a regular expression"):
            pr_threads.ReviewerBots.from_settings(REVIEWER_BOT, "(")

    def test_never_an_approval(self) -> None:
        pr = reviewer_fetched(reviewer_bot_page(state="APPROVED"))
        self.assertEqual(pr_threads.approvals(pr), [])
        person = review_node("R9", REVIEWER, "APPROVED", "2026-01-04T09:00:00Z")
        data = reviewer_bot_page(state="APPROVED")
        data["data"]["repository"]["pullRequest"]["reviews"]["nodes"].append(person)
        self.assertEqual([r.id for r in pr_threads.approvals(reviewer_fetched(data))], ["R9"])

    def test_cli_reads_the_settings_shows_the_verdict_and_exits_3(self) -> None:
        env = {pr_threads.REVIEWER_BOTS_SETTING: REVIEWER_BOT}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = pr_threads.main([URL, "--awaiting-author"], FakeGh(reviewer_bot_page()), env)
        self.assertEqual(code, pr_threads.EXIT_AWAITING)
        text = out.getvalue()
        self.assertIn(
            f"automated verdict: Request changes (by {REVIEWER_BOT} at 2026-01-02T09:00:00Z; "
            "never an approval)",
            text,
        )
        self.assertIn(f"- {REVIEWER_BOT} (automated) on exporter/footer.py:1", text)
        with contextlib.redirect_stdout(out := io.StringIO()):
            code = pr_threads.main([URL, "--awaiting-author"], FakeGh(reviewer_bot_page()), {})
        self.assertEqual(code, pr_threads.EXIT_OK)
        with contextlib.redirect_stdout(out := io.StringIO()):
            pr_threads.main([URL, "--json"], FakeGh(reviewer_bot_page()), env)
        data = json.loads(out.getvalue())
        self.assertEqual(data["verdicts"][0]["verdict"], "Request changes")
        self.assertEqual([t["automated"] for t in data["threads"]], [True, True, False])

    def test_a_bad_pattern_in_the_cli_is_an_error(self) -> None:
        env = {pr_threads.REVIEWER_BOTS_SETTING: REVIEWER_BOT, pr_threads.VERDICT_PATTERN_SETTING: "("}
        with contextlib.redirect_stderr(err := io.StringIO()):
            code = pr_threads.main([URL], FakeGh(reviewer_bot_page()), env)
        self.assertEqual(code, pr_threads.EXIT_ERROR)
        self.assertIn("not a regular expression", err.getvalue())


class OutputTest(unittest.TestCase):
    def run_main(self, *argv: str, pages: list[dict[str, Any]] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        gh = FakeGh(*(pages or [page(THREADS)]))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pr_threads.main([URL, *argv], gh)
        return code, out.getvalue(), err.getvalue()

    def test_the_default_output_keeps_its_first_lines(self) -> None:
        code, out, _ = self.run_main()
        self.assertEqual(code, pr_threads.EXIT_OK)
        lines = out.splitlines()
        self.assertEqual(lines[0], f"unresolved threads: 5 (awaiting the author, {AUTHOR}: 2)")
        self.assertIn(f"- {REVIEWER} on exporter/footer.py:1 2026-01-01T10:00:00Z {URL}#discussion_r1", lines)
        self.assertIn(
            "  round 1; 1 reply, last: me-acme 2026-01-01T12:00:00Z (answered by the author)", lines
        )
        self.assertIn(
            "  round 2; 2 replies, last: dana-acme 2026-01-04T12:00:00Z (awaiting the author)", lines
        )
        self.assertIn("  round 2; no replies (awaiting the author)", lines)
        self.assertIn(
            "  2. CHANGES_REQUESTED by dana-acme 2026-01-03T09:00:00Z: 3 threads, 2 awaiting the author",
            lines,
        )
        self.assertNotIn("discussion_r6", out)  # resolved

    def test_awaiting_author_lists_only_those_and_exits_3(self) -> None:
        code, out, _ = self.run_main("--awaiting-author")
        self.assertEqual(code, pr_threads.EXIT_AWAITING)
        self.assertTrue(out.startswith(f"awaiting the author ({AUTHOR}): 2 of 5 unresolved threads"))
        self.assertNotIn("discussion_r1 ", out + " ")
        self.assertIn("discussion_r4", out)
        answered = [thread_node(1, "R1", [AUTHOR])]
        code, out, _ = self.run_main("--awaiting-author", pages=[page(answered)])
        self.assertEqual(code, pr_threads.EXIT_OK)
        self.assertIn("0 of 1 unresolved threads", out)

    def test_json(self) -> None:
        code, out, _ = self.run_main("--json", "--awaiting-author")
        self.assertEqual(code, pr_threads.EXIT_AWAITING)
        data = json.loads(out)
        self.assertEqual((data["unresolved"], data["awaiting_author"]), (5, 2))
        self.assertEqual([t["id"] for t in data["threads"]], ["T4", "T5"])
        self.assertEqual(data["threads"][0]["round"], 2)
        self.assertEqual(
            [(r["round"], r["threads"], r["awaiting"]) for r in data["rounds"]], [(1, 3, 0), (2, 3, 2)]
        )
        _, out, _ = self.run_main("--json")
        self.assertEqual(len(json.loads(out)["threads"]), len(THREADS))  # resolved ones too

    def test_an_unreadable_pr_exits_1(self) -> None:
        code, out, err = self.run_main(pages=[{"errors": [{"message": "Bad credentials"}]}])
        self.assertEqual((code, out), (pr_threads.EXIT_ERROR, ""))
        self.assertIn("bosun pr-threads: GitHub: Bad credentials", err)


if __name__ == "__main__":
    unittest.main()
