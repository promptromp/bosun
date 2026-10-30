#!/usr/bin/env python3
"""New reviews on my open PRs, raised to the orchestrator (`bosun review-watch`).

Usage: review_watch.py [--dry-run] [--id <mypr id> ...]

For each open my-pr item in WATCH_STATES (all of them, or the --id ones), it reads the PR
(lib/pr_threads.py, read-only) and finds the reviews by someone other than the PR's author, with
changes requested or comments, submitted after the item's **reference time**: the latest of when
the item was created and its last hand-over to a repo session (`delegated`, `update`, `moved`);
for an item that is not in `addressing`, its last report too. A transition does not count: the
orchestrator may move an item without having read a review that just came.

Each such review is recorded once on the item, as a `review-arrived` history entry (no state
change), and with BOSUN_EVENTS_ENABLED=1 queued as a `review` event, so the orchestrator hears
of it even while the item's window is still working. The output has one line per review still to
act on (new or recorded before, until the next hand-over), with the next step: for `addressing`,
forward the threads to the window with `bosun delegate --update`; otherwise chores step 4.
--dry-run records and queues nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import approvals
import ledger
import pr_threads


ADDRESSING = "addressing"
WATCH_STATES = (ADDRESSING, "awaiting-review", "approved")
CHANGES_REQUESTED = "CHANGES_REQUESTED"
RAISED_STATES = (CHANGES_REQUESTED, "COMMENTED")
EPOCH = "1970-01-01T00:00:00+00:00"
ARRIVED = "review-arrived"
EVENT = "review"
REPORT = "report"
SECONDS_PER_PR = 20.0


@dataclass(frozen=True)
class Finding:
    item_id: str
    state: str  # the item's
    url: str  # the PR's
    review: pr_threads.Review
    summary: str
    window: str  # the window holding the item, if any
    awaiting: int  # threads awaiting the author now
    new: bool  # not recorded on the item yet

    @property
    def still_open(self) -> bool:
        """A review recorded before still needs acting on: threads await the author, or it asked
        for changes."""
        return self.new or self.awaiting > 0 or self.review.state == CHANGES_REQUESTED


def watched(item: dict[str, Any], ids: list[str]) -> bool:
    if item.get("kind") != approvals.MY_PR_KIND:
        return False
    return item["id"] in ids if ids else item.get("state") in WATCH_STATES


def pr_url(item: dict[str, Any]) -> str:
    return str(item.get("url") or approvals.pr_url(str(item["id"])))


def reference_time(item: dict[str, Any]) -> str:
    """Reviews after this are new to the item's window (see the module docstring)."""
    counted = ledger.HANDOVER_EVENTS if item.get("state") == ADDRESSING else (*ledger.HANDOVER_EVENTS, REPORT)
    times = [str(item.get("created_at") or "")]
    times += [str(h["at"]) for h in item.get("history") or [] if h.get("event") in counted and h.get("at")]
    return max((t for t in times if t), key=pr_threads.parse_time, default=EPOCH)


def recorded(item: dict[str, Any]) -> set[str]:
    return {str(h.get("review")) for h in item.get("history") or [] if h.get("event") == ARRIVED}


def summary(pr: pr_threads.PullRequest, review: pr_threads.Review) -> str:
    started = sum(1 for t in pr.threads if t.review == review.id)
    verdict = f" (automated verdict: {review.verdict})" if review.verdict else ""
    return (
        f"{review.state} by {review.author}{verdict} at {review.at}: {pr_threads.plural(started, 'thread')} "
        f"started; {len(pr_threads.awaiting(pr))} awaiting the author now ({pr_threads.round_summary(pr)})"
    )


def findings(item: dict[str, Any], pr: pr_threads.PullRequest) -> list[Finding]:
    """The reviews to raise on <item>, oldest first."""
    known = recorded(item)
    window = str((item.get("delegation") or {}).get("window") or "")
    waiting = len(pr_threads.awaiting(pr))
    return [
        Finding(item["id"], item["state"], pr.url, r, summary(pr, r), window, waiting, r.url not in known)
        for r in pr_threads.reviews_after(pr, reference_time(item))
        if r.state in RAISED_STATES
    ]


def record_new(items: dict[str, Any], new: list[Finding]) -> list[Finding]:
    """Record each new finding on its item (the caller holds the write lock); the ones recorded
    now (another run may have recorded some in between)."""
    done = []
    for finding in new:
        item = items.get(finding.item_id)
        if item is not None and finding.review.url not in recorded(item):
            ledger.record(
                item, ARRIVED, finding.summary, review=finding.review.url, submitted_at=finding.review.at
            )
            done.append(finding)
    return done


def next_step(finding: Finding) -> str:
    if finding.state == ADDRESSING:
        return (
            f"bosun pr-threads '{finding.url}' --awaiting-author, then send what it lists with "
            f"bosun delegate --update '{finding.item_id}'"
        )
    if finding.review.state == CHANGES_REQUESTED:
        return f"chores step 4 for {finding.item_id} (changes requested: changes-requested, then fix-my-pr)"
    read = f"gh pr view '{finding.url}' --json reviews"
    return f"read it ({read}) and handle {finding.item_id} as chores step 4 says"


def line(finding: Finding) -> str:
    held = f" (held by {finding.window})" if finding.window else ""
    mark = "new review" if finding.new else "review (recorded before)"
    return (
        f"{finding.item_id} [{finding.state}]{held}: {mark} {finding.summary}\n  next: {next_step(finding)}"
    )


def targets(ids: list[str]) -> list[tuple[str, str]]:
    with ledger.locked_ledger(write=False) as book:
        return [(item["id"], pr_url(item)) for item in book["items"].values() if watched(item, ids)]


def read_prs(
    wanted: list[tuple[str, str]], fetch: pr_threads.Fetch, reviewers: pr_threads.ReviewerBots
) -> tuple[dict[str, pr_threads.PullRequest], list[str]]:
    """Each PR, outside the ledger lock; the warnings for those that could not be read."""
    prs, warnings = {}, []
    for item_id, url in wanted:
        try:
            prs[item_id] = fetch(url, SECONDS_PER_PR, reviewers)
        except pr_threads.PrThreadsError as exc:
            warnings.append(f"warn: {item_id}: {exc}")
    return prs, warnings


def find_all(prs: dict[str, pr_threads.PullRequest]) -> list[Finding]:
    with ledger.locked_ledger(write=False) as book:
        items = book["items"]
        return [f for item_id, pr in prs.items() if item_id in items for f in findings(items[item_id], pr)]


def raise_new(new: list[Finding]) -> None:
    """Record the new findings on their items and queue an event for each one recorded now."""
    with ledger.locked_ledger(write=True) as book:
        recorded_now = record_new(book["items"], new)
    for finding in recorded_now:
        ledger.emit(EVENT, finding.item_id, finding.window, finding.summary)


def output(found: list[Finding], quiet: bool) -> list[str]:
    """The lines for the findings: <quiet>, only the new ones; else every one still open."""
    return [line(f) for f in found if (f.new if quiet else f.still_open)]


def watch(
    ids: list[str],
    dry_run: bool,
    fetch: pr_threads.Fetch = pr_threads.read,
    quiet: bool = False,
    reviewers: pr_threads.ReviewerBots = pr_threads.NO_REVIEWER_BOTS,
) -> list[str]:
    """Raise new reviews on the watched items (<reviewers>' count like people's); the output lines
    (<quiet>: only the new reviews and warnings, nothing when there are none). The ledger is written
    only when there is something new."""
    prs, warnings = read_prs(targets(ids), fetch, reviewers)
    found = find_all(prs)
    new = [f for f in found if f.new]
    if new and not dry_run:
        raise_new(new)
    lines = warnings + output(found, quiet)
    if lines or quiet:
        return lines
    return [f"no new reviews on {len(prs)} PR(s)"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bosun review-watch", add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if not os.environ.get("BOSUN_STATE_DIR"):
        print(
            "review-watch: BOSUN_STATE_DIR is not set; run it as `bosun review-watch`",
            file=sys.stderr,
        )
        return 2
    try:
        ledger.configure_from_environment()
        reviewers = pr_threads.ReviewerBots.from_env(os.environ)
        lines = watch(args.id, args.dry_run, quiet=args.quiet, reviewers=reviewers)
    except (ledger.LedgerError, pr_threads.PrThreadsError) as exc:
        print(f"review-watch: {exc}", file=sys.stderr)
        return 1
    if lines:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
