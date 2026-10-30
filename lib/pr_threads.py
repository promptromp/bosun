#!/usr/bin/env python3
"""Review threads and review rounds on a GitHub PR, read-only (`bosun pr-threads`).

Usage: pr_threads.py <pr-url> [--awaiting-author] [--json]
(the reviewer bots come from BOSUN_REVIEWER_BOTS and BOSUN_REVIEWER_BOT_VERDICT_PATTERN)

One GraphQL query (`gh api graphql`, paged by 100 threads) reads the PR's author, its latest 100
reviews and every review thread with its first and last comment and comment count. A thread
**awaits the author** when it is unresolved and its last comment is not by the PR's author
(`author.login`): a reply by anyone else, the gh user included when it is not the author, does not
answer it. Threads are never resolved here, so "unresolved" alone says nothing about whether a
thread was answered.

A **review round** is a review by someone other than the author (not pending) that started
threads, requested changes, approved, or has a body; each thread belongs to the round whose review
holds its first comment. The author's own replies create reviews too; they are not rounds.

**Bots never count.** A review or comment by a bot (author `__typename` Bot, or a login ending in
`[bot]`: review and AI bots that comment on every push) is shown, marked as a bot, but a thread a
bot started never awaits the author (so it never makes --awaiting-author exit 3), and a bot's
review is never "newer" (reviews_after): neither the guard nor `bosun review-watch` acts on
it. A bot's reply in a person's thread does not answer it either.

**Reviewer bots count like people.** An automated reviewer a site relies on (ReviewerBots, from
BOSUN_REVIEWER_BOTS: its GitHub logins) is not one of those bots: its threads await the author
and its reviews are rounds and can be newer, like a person's, marked "(automated)". Its latest
verdict is read from its review body (BOSUN_REVIEWER_BOT_VERDICT_PATTERN, the first group of a
multi-line regular expression) and shown. Its review is never an approval: approvals() lists only
people's.

The guard (lib/guard.py) and `bosun review-watch` use fetch() and the helpers below.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AWAITING = 3  # --awaiting-author: threads await the author
PAGE_SIZE = 100
MAX_PAGES = 10  # at most 1000 threads
CLI_SECONDS = 60.0
BODY_LENGTH = 160
PENDING = "PENDING"
BOT_TYPE = "Bot"
BOT_SUFFIX = "[bot]"
APPROVED = "APPROVED"
ROUND_STATES = ("CHANGES_REQUESTED", APPROVED)
DEFAULT_VERDICT_PATTERN = r"^#+\s*Verdict:\s*(.+?)\s*$"
REVIEWER_BOTS_SETTING = "BOSUN_REVIEWER_BOTS"
VERDICT_PATTERN_SETTING = "BOSUN_REVIEWER_BOT_VERDICT_PATTERN"
PR_URL = re.compile(r"github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/pull/(?P<number>\d+)")

# GraphQL, not Python: reviews(last: 100) keeps the newest when a PR has more.
QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      url
      author { login __typename }
      baseRepository { autoMergeAllowed }
      reviews(last: 100) { nodes { id url author { login __typename } state submittedAt body } }
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line
          first: comments(first: 1) {
            totalCount
            nodes { author { login __typename } createdAt url body pullRequestReview { id } }
          }
          last: comments(last: 1) { nodes { author { login __typename } createdAt url } }
        }
      }
    }
  }
}
"""


class PrThreadsError(Exception):
    pass


@dataclass(frozen=True)
class ReviewerBots:
    """The bots whose reviews count like a person's (their GitHub logins, compared without case), and
    how to read a verdict from their review bodies."""

    logins: frozenset[str] = frozenset()
    verdict: re.Pattern[str] = field(default=re.compile(DEFAULT_VERDICT_PATTERN, re.MULTILINE))

    @classmethod
    def from_settings(cls, logins: str, pattern: str) -> ReviewerBots:
        """From the settings' text; a pattern that is not a regular expression is a PrThreadsError."""
        try:
            verdict = re.compile(pattern or DEFAULT_VERDICT_PATTERN, re.MULTILINE)
        except re.error as exc:
            raise PrThreadsError(f"{VERDICT_PATTERN_SETTING} is not a regular expression ({exc})") from exc
        return cls(frozenset(name.casefold() for name in logins.split()), verdict)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ReviewerBots:
        return cls.from_settings(env.get(REVIEWER_BOTS_SETTING, ""), env.get(VERDICT_PATTERN_SETTING, ""))

    def counts(self, login: str) -> bool:
        return login.casefold() in self.logins

    def verdict_of(self, body: str) -> str:
        """The verdict a review body states (the pattern's first group, else its match), or ""."""
        match = self.verdict.search(body)
        if not match:
            return ""
        return (match.group(1) if match.groups() else match.group(0)).strip()


NO_REVIEWER_BOTS = ReviewerBots()


@dataclass(frozen=True)
class Comment:
    author: str
    at: str
    url: str
    body: str = ""
    bot: bool = False  # a bot that never counts
    automated: bool = False  # a reviewer bot: counts like a person


@dataclass(frozen=True)
class Thread:
    id: str
    path: str
    line: int | None
    resolved: bool
    outdated: bool
    first: Comment
    last: Comment
    comments: int
    review: str  # the id of the review holding its first comment ("" if unknown)

    @property
    def replies(self) -> int:
        return max(self.comments - 1, 0)

    @property
    def bot(self) -> bool:
        """A bot that never counts started it."""
        return self.first.bot

    @property
    def automated(self) -> bool:
        """A reviewer bot started it."""
        return self.first.automated


@dataclass(frozen=True)
class Review:
    id: str
    url: str
    author: str
    state: str
    at: str
    body: str = ""
    bot: bool = False  # a bot that never counts
    automated: bool = False  # a reviewer bot: counts like a person, but never approves
    verdict: str = ""  # a reviewer bot's verdict, read from its body


@dataclass(frozen=True)
class PullRequest:
    url: str
    author: str
    reviews: tuple[Review, ...]
    threads: tuple[Thread, ...]
    auto_merge_allowed: bool | None = None  # the base repository allows auto-merge; None: unknown


@dataclass(frozen=True)
class Round:
    number: int
    review: Review
    threads: int
    awaiting: int


# ---- reading GitHub ----------------------------------------------------------------------------

Runner = Callable[[list[str], float], str]


def run_gh(args: list[str], timeout: float) -> str:
    """`gh <args>`'s output; a failure, a timeout or no gh at all is a PrThreadsError."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise PrThreadsError("gh is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise PrThreadsError(f"gh timed out after {timeout:.0f}s") from exc
    if done.returncode != 0:
        raise PrThreadsError(f"gh failed: {(done.stderr or done.stdout).strip()[-BODY_LENGTH:]}")
    return done.stdout


def parse_url(url: str) -> tuple[str, str, int]:
    match = PR_URL.search(url)
    if not match:
        raise PrThreadsError(f"not a GitHub PR URL: {url}")
    return match["owner"], match["repo"], int(match["number"])


def text(node: dict[str, Any], key: str) -> str:
    return str(node.get(key) or "")


def part(node: dict[str, Any], key: str) -> dict[str, Any]:
    value = node.get(key)
    return value if isinstance(value, dict) else {}


def nodes(node: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [n for n in part(node, key).get("nodes") or [] if isinstance(n, dict)]


def login(node: dict[str, Any]) -> str:
    return text(part(node, "author"), "login")


def is_bot(node: dict[str, Any], reviewers: ReviewerBots = NO_REVIEWER_BOTS) -> bool:
    """Whether the node's author is a bot that never counts: GraphQL type Bot, or a login ending in
    [bot], and not one of the reviewer bots."""
    author = part(node, "author")
    looks_bot = text(author, "__typename") == BOT_TYPE or text(author, "login").endswith(BOT_SUFFIX)
    return looks_bot and not reviewers.counts(login(node))


def comment(node: dict[str, Any], reviewers: ReviewerBots = NO_REVIEWER_BOTS) -> Comment:
    return Comment(
        login(node),
        text(node, "createdAt"),
        text(node, "url"),
        text(node, "body"),
        is_bot(node, reviewers),
        reviewers.counts(login(node)),
    )


def thread(node: dict[str, Any], reviewers: ReviewerBots = NO_REVIEWER_BOTS) -> Thread:
    firsts = nodes(node, "first") or [{}]
    lasts = nodes(node, "last") or firsts
    return Thread(
        id=text(node, "id"),
        path=text(node, "path"),
        line=node.get("line"),
        resolved=bool(node.get("isResolved")),
        outdated=bool(node.get("isOutdated")),
        first=comment(firsts[0], reviewers),
        last=comment(lasts[0], reviewers),
        comments=int(part(node, "first").get("totalCount") or len(firsts)),
        review=text(part(firsts[0], "pullRequestReview"), "id"),
    )


def review(node: dict[str, Any], reviewers: ReviewerBots = NO_REVIEWER_BOTS) -> Review:
    automated = reviewers.counts(login(node))
    return Review(
        id=text(node, "id"),
        url=text(node, "url"),
        author=login(node),
        state=text(node, "state"),
        at=text(node, "submittedAt"),
        body=text(node, "body"),
        bot=is_bot(node, reviewers),
        automated=automated,
        verdict=reviewers.verdict_of(text(node, "body")) if automated else "",
    )


def page_request(owner: str, repo: str, number: int, cursor: str) -> list[str]:
    args = ["api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"repo={repo}"]
    args += ["-F", f"number={number}"]
    return args + (["-f", f"cursor={cursor}"] if cursor else [])


def pull_request_node(output: str) -> dict[str, Any]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise PrThreadsError(f"gh returned no JSON ({exc})") from exc
    node = part(part(data, "data"), "repository").get("pullRequest")
    if not isinstance(node, dict):
        errors = "; ".join(str(e.get("message", e)) for e in data.get("errors") or []) or "no such PR"
        raise PrThreadsError(f"GitHub: {errors}")
    return node


def next_cursor(node: dict[str, Any]) -> str:
    """Where the next page of threads starts, or "" after the last."""
    info = part(part(node, "reviewThreads"), "pageInfo")
    return text(info, "endCursor") if info.get("hasNextPage") else ""


def fetch(
    url: str,
    seconds: float = CLI_SECONDS,
    runner: Runner = run_gh,
    reviewers: ReviewerBots = NO_REVIEWER_BOTS,
) -> PullRequest:
    """The PR at <url>: its author, newest reviews and every review thread, within <seconds>; the
    <reviewers> count like people."""
    owner, repo, number = parse_url(url)
    deadline = time.monotonic() + seconds
    pages: list[dict[str, Any]] = []
    cursor = ""
    while len(pages) < MAX_PAGES and (cursor or not pages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PrThreadsError(f"reading the PR took more than {seconds:.0f}s")
        pages.append(pull_request_node(runner(page_request(owner, repo, number, cursor), remaining)))
        cursor = next_cursor(pages[-1])
    first = pages[0]
    threads = tuple(thread(n, reviewers) for page in pages for n in nodes(page, "reviewThreads"))
    reviews = tuple(review(n, reviewers) for n in nodes(first, "reviews"))
    allowed = part(first, "baseRepository").get("autoMergeAllowed")
    auto_merge = allowed if isinstance(allowed, bool) else None
    return PullRequest(text(first, "url") or url, login(first), reviews, threads, auto_merge)


# How the guard and review-watch read a PR (their tests pass their own): (url, seconds, reviewers).
Fetch = Callable[[str, float, ReviewerBots], PullRequest]


def read(url: str, seconds: float, reviewers: ReviewerBots) -> PullRequest:
    """fetch() through gh."""
    return fetch(url, seconds, run_gh, reviewers)


# ---- what it means -----------------------------------------------------------------------------


def awaits_author(item: Thread, author: str) -> bool:
    """Unresolved, not started by a bot, and its last comment is not by the PR's author."""
    return not item.resolved and not item.bot and (not author or item.last.author != author)


def awaiting(pr: PullRequest) -> list[Thread]:
    return [t for t in pr.threads if awaits_author(t, pr.author)]


def unresolved(pr: PullRequest) -> list[Thread]:
    return [t for t in pr.threads if not t.resolved]


def by_others(pr: PullRequest) -> list[Review]:
    """Reviews by someone other than the PR's author, oldest first (pending ones left out)."""
    found = [r for r in pr.reviews if r.state != PENDING and (not pr.author or r.author != pr.author)]
    return sorted(found, key=lambda r: r.at)


def rounds(pr: PullRequest) -> list[Round]:
    """The review rounds, oldest first, with the threads each started and how many await the author."""
    started: dict[str, list[Thread]] = {}
    for item in pr.threads:
        started.setdefault(item.review, []).append(item)
    found: list[Round] = []
    for candidate in by_others(pr):
        threads = started.get(candidate.id, [])
        if threads or candidate.state in ROUND_STATES or candidate.body.strip():
            waiting = sum(1 for t in threads if awaits_author(t, pr.author))
            found.append(Round(len(found) + 1, candidate, len(threads), waiting))
    return found


def reviews_after(pr: PullRequest, moment: str) -> list[Review]:
    """Reviews by other people and reviewer bots (other bots never count) submitted after <moment>
    (an ISO time)."""
    since = parse_time(moment)
    return [r for r in by_others(pr) if not r.bot and r.at and parse_time(r.at) > since]


def approvals(pr: PullRequest) -> list[Review]:
    """The approving reviews by people, oldest first: a bot's, a reviewer bot's included, never counts."""
    return [r for r in by_others(pr) if r.state == APPROVED and not (r.bot or r.automated)]


def verdicts(pr: PullRequest) -> list[Review]:
    """Each reviewer bot's latest review that states a verdict, oldest first."""
    latest = {r.author.casefold(): r for r in by_others(pr) if r.automated and r.verdict}
    return sorted(latest.values(), key=lambda r: r.at)


def parse_time(value: str) -> datetime:
    """An ISO time; one without an offset is UTC."""
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def round_summary(pr: PullRequest) -> str:
    """'round 1: 4 threads, 0 awaiting; round 2: 3 threads, 3 awaiting' (or 'no review rounds')."""
    parts = [
        f"round {r.number}{author_mark(r.review)}: {plural(r.threads, 'thread')}, {r.awaiting} awaiting"
        for r in rounds(pr)
    ]
    return "; ".join(parts) or "no review rounds"


def bot_mark(bot: bool) -> str:
    return " (bot)" if bot else ""


def author_mark(item: Comment | Review) -> str:
    """ " (bot)" for a bot that never counts, " (automated)" for a reviewer bot, else ""."""
    return " (automated)" if item.automated else bot_mark(item.bot)


def plural(count: int, word: str, words: str = "") -> str:
    return f"{count} {word if count == 1 else words or word + 's'}"


# ---- output ------------------------------------------------------------------------------------


def one_line(text: str, limit: int = BODY_LENGTH) -> str:
    return " ".join(text.split())[:limit]


def round_of(pr: PullRequest) -> dict[str, int]:
    return {r.review.id: r.number for r in rounds(pr)}


def thread_lines(pr: PullRequest, item: Thread, numbers: dict[str, int]) -> list[str]:
    where = f"{item.path}:{item.line if item.line is not None else '-'}"
    outdated = " (outdated)" if item.outdated else ""
    who = f"{item.first.author}{author_mark(item.first)}"
    head = f"- {who} on {where}{outdated} {item.first.at} {item.first.url}"
    number = numbers.get(item.review)
    origin = f"round {number}" if number else "an earlier review"
    if item.replies:
        last = f"{plural(item.replies, 'reply', 'replies')}, last: {item.last.author} {item.last.at}"
    else:
        last = "no replies"
    status = thread_status(item, pr.author)
    return [head, f"  {one_line(item.first.body)}", f"  {origin}; {last} ({status})"]


def thread_status(item: Thread, author: str) -> str:
    if item.bot and not item.resolved:
        return "a bot's thread: never awaits the author"
    return "awaiting the author" if awaits_author(item, author) else "answered by the author"


def round_lines(pr: PullRequest) -> list[str]:
    found = rounds(pr)
    if not found:
        return ["review rounds: none"]
    return [
        "review rounds (reviews by others, oldest first):",
        *(
            f"  {r.number}. {r.review.state} by {r.review.author}{author_mark(r.review)} {r.review.at}: "
            f"{plural(r.threads, 'thread')}, {r.awaiting} awaiting the author"
            for r in found
        ),
    ]


def verdict_lines(pr: PullRequest) -> list[str]:
    """One line per reviewer bot's latest verdict (never an approval)."""
    return [
        f"automated verdict: {r.verdict} (by {r.author} at {r.at}; never an approval)" for r in verdicts(pr)
    ]


def auto_merge_lines(pr: PullRequest) -> list[str]:
    """Whether the repository allows auto-merge (`gh pr merge --auto`), when GitHub said."""
    if pr.auto_merge_allowed is None:
        return []
    return [f"auto-merge: {'allowed' if pr.auto_merge_allowed else 'not allowed'} in this repository"]


def render(pr: PullRequest, awaiting_only: bool) -> str:
    """The text output: counts, the rounds, then the threads (unresolved, or only those awaiting)."""
    waiting, open_threads = awaiting(pr), unresolved(pr)
    author = pr.author or "unknown"
    if awaiting_only:
        head = f"awaiting the author ({author}): {len(waiting)} of {len(open_threads)} unresolved threads"
    else:
        head = f"unresolved threads: {len(open_threads)} (awaiting the author, {author}: {len(waiting)})"
    numbers = round_of(pr)
    shown = waiting if awaiting_only else open_threads
    lines = [head, *round_lines(pr), *verdict_lines(pr), *auto_merge_lines(pr)]
    for item in shown:
        lines += thread_lines(pr, item, numbers)
    return "\n".join(lines)


def as_json(pr: PullRequest, awaiting_only: bool) -> dict[str, Any]:
    numbers = round_of(pr)
    shown = awaiting(pr) if awaiting_only else list(pr.threads)
    return {
        "url": pr.url,
        "author": pr.author,
        "unresolved": len(unresolved(pr)),
        "awaiting_author": len(awaiting(pr)),
        "auto_merge_allowed": pr.auto_merge_allowed,
        "rounds": [
            {**asdict(r.review), "round": r.number, "threads": r.threads, "awaiting": r.awaiting}
            for r in rounds(pr)
        ],
        "verdicts": [
            {"author": r.author, "at": r.at, "verdict": r.verdict, "url": r.url} for r in verdicts(pr)
        ],
        "threads": [
            {
                **asdict(t),
                "replies": t.replies,
                "bot": t.bot,
                "automated": t.automated,
                "round": numbers.get(t.review),
                "awaiting_author": awaits_author(t, pr.author),
            }
            for t in shown
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun pr-threads", add_help=False)
    parser.add_argument("url")
    parser.add_argument("--awaiting-author", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str], runner: Runner = run_gh, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        reviewers = ReviewerBots.from_env(os.environ if env is None else env)
        pr = fetch(args.url, CLI_SECONDS, runner, reviewers)
    except PrThreadsError as exc:
        print(f"bosun pr-threads: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps(as_json(pr, args.awaiting_author), indent=2))
    else:
        print(render(pr, args.awaiting_author))
    return EXIT_AWAITING if args.awaiting_author and awaiting(pr) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
