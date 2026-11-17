"""Unit tests for tests/llm/gh_stand_in.py, the scratch world's gh: what it answers is what
lib/pr_threads.py reads from GitHub, so a real-Claude case that needs a PR's live state gets it."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "lib"), str(ROOT / "tests" / "llm")]
import gh_stand_in  # noqa: E402
import pr_threads  # noqa: E402


URL = "https://github.com/acme/exporter/pull/7"
ME = {"login": "acme-dev", "__typename": "User"}
REVIEWER = {"login": "reviewer", "__typename": "User"}


def thread(n: int, last: dict[str, str]) -> dict[str, Any]:
    first = {"author": REVIEWER, "createdAt": "2026-01-01T10:00:00Z", "url": f"{URL}#r{n}", "body": "fix"}
    return {
        "id": f"T{n}",
        "isResolved": False,
        "path": "a.py",
        "line": n,
        "first": {"totalCount": 2, "nodes": [{**first, "pullRequestReview": {"id": "R1"}}]},
        "last": {"nodes": [{"author": last, "createdAt": "2026-01-02T10:00:00Z", "url": f"{URL}#r{n}0"}]},
    }


PR = {
    "pullRequest": {
        "url": URL,
        "author": ME,
        "reviews": {"nodes": [{"id": "R1", "author": REVIEWER, "state": "CHANGES_REQUESTED"}]},
        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": [thread(1, ME), thread(2, REVIEWER)]},
    },
    "view": {"number": 7, "state": "OPEN", "reviewDecision": "CHANGES_REQUESTED"},
}


class GhStandInTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Path(tmp.name)
        path = gh_stand_in.url_file(self.store, URL)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(PR))

    def gh(self, *args: str) -> str:
        return gh_stand_in.answer(self.store, list(args))

    def test_pr_threads_reads_the_pull_request(self) -> None:
        pr = pr_threads.fetch(URL, runner=lambda args, _seconds: self.gh(*args))
        self.assertEqual((pr.author, len(pr.threads)), ("acme-dev", 2))
        self.assertEqual([t.id for t in pr_threads.awaiting(pr)], ["T2"])

    def test_a_pull_request_the_world_lacks_fails_as_on_github(self) -> None:
        other = URL.replace("/7", "/8")
        with self.assertRaisesRegex(gh_stand_in.GhError, "Could not resolve to a PullRequest .* 8"):
            pr_threads.fetch(other, runner=lambda args, _seconds: self.gh(*args))

    def test_pr_view_gives_the_fields_asked_for(self) -> None:
        self.assertEqual(json.loads(self.gh("pr", "view", URL, "--json", "state,title")), {"state": "OPEN"})
        by_number = self.gh("pr", "view", "7", "--repo", "acme/exporter", "--json", "reviewDecision")
        self.assertEqual(json.loads(by_number), {"reviewDecision": "CHANGES_REQUESTED"})
        with self.assertRaisesRegex(gh_stand_in.GhError, "--repo"):
            self.gh("pr", "view", "7", "--json", "state")

    def test_any_other_command_fails(self) -> None:
        with self.assertRaisesRegex(gh_stand_in.GhError, "not in this scratch world"):
            self.gh("pr", "comment", URL, "--body", "hi")


if __name__ == "__main__":
    unittest.main()
