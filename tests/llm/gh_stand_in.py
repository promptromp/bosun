#!/usr/bin/env python3
"""gh for a scratch world (tests/llm/world.py): it answers about the pull requests the world's seeds gave
it, and never reaches GitHub.

Usage: gh_stand_in.py <store> <gh arguments...>

A pull request is <store>/<owner>/<repo>/<number>.json: {"pullRequest": <its node in the shape of
lib/pr_threads.py's GraphQL query>, "view": <the fields `gh pr view --json` may ask for>}. It answers
`gh api graphql` with that query's variables (owner, repo, number), as GitHub does, `gh pr view <url |
number --repo owner/repo> [--json fields [--jq expression]]` and `gh pr checks` (from the view's
statusCheckRollup). A pull request the world has none of
fails as GitHub's answer does; any other command fails, saying so.
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PR_URL = re.compile(r"github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/pull/(?P<number>\d+)")
FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field")
REPO_FLAGS = ("-R", "--repo")


class GhError(Exception):
    pass


def pr_file(store: Path, owner: str, repo: str, number: str) -> Path:
    return store / owner / repo / f"{number}.json"


def url_file(store: Path, url: str) -> Path:
    match = PR_URL.search(url)
    if not match:
        raise GhError(f"not a GitHub PR URL: {url}")
    return pr_file(store, match["owner"], match["repo"], match["number"])


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise GhError(
            f"GraphQL: Could not resolve to a PullRequest with the number of {path.stem}. "
            "(repository.pullRequest)"
        )
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def option(args: list[str], *names: str) -> str:
    return next((value for flag, value in itertools.pairwise(args) if flag in names), "")


def graphql(store: Path, args: list[str]) -> str:
    pairs = [value for flag, value in itertools.pairwise(args) if flag in FIELD_FLAGS]
    fields = dict(pair.split("=", 1) for pair in pairs if "=" in pair)
    node = load(pr_file(store, fields.get("owner", ""), fields.get("repo", ""), fields.get("number", "")))
    return json.dumps({"data": {"repository": {"pullRequest": node["pullRequest"]}}})


def viewed_file(store: Path, args: list[str]) -> Path:
    """The PR `gh pr view` or `gh pr checks` names: a URL, or a number with --repo."""
    which = args[2] if len(args) > 2 and not args[2].startswith("-") else ""
    if PR_URL.search(which):
        return url_file(store, which)
    repo = option(args, *REPO_FLAGS)
    if which.isdigit() and "/" in repo:
        owner, name = repo.split("/", 1)
        return pr_file(store, owner, name, which)
    raise GhError("this scratch world's gh needs the PR's URL, or its number with --repo owner/repo")


def jq(expression: str, text: str) -> str:
    if not shutil.which("jq"):
        raise GhError("--jq needs jq, which is not installed")
    done = subprocess.run(["jq", "-r", expression], input=text, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise GhError(done.stderr.strip())
    return done.stdout.rstrip("\n")


def pr_view(store: Path, args: list[str]) -> str:
    view: dict[str, Any] = load(viewed_file(store, args))["view"]
    wanted = option(args, "--json")
    if not wanted:
        return "\n".join(str(view.get(name, "")) for name in ("title", "state", "url"))
    text = json.dumps({name: view[name] for name in wanted.split(",") if name in view})
    expression = option(args, "--jq", "-q")
    return jq(expression, text) if expression else text


def pr_checks(store: Path, args: list[str]) -> str:
    """As gh prints them to a pipe: name, result, duration, link."""
    checks = load(viewed_file(store, args))["view"].get("statusCheckRollup") or []
    results = {"SUCCESS": "pass", "FAILURE": "fail"}
    return "\n".join(f"{c['name']}\t{results.get(c.get('conclusion'), 'pending')}\t1m0s\t" for c in checks)


def answer(store: Path, args: list[str]) -> str:
    if args[:2] == ["api", "graphql"]:
        return graphql(store, args)
    if args[:2] == ["pr", "view"]:
        return pr_view(store, args)
    if args[:2] == ["pr", "checks"]:
        return pr_checks(store, args)
    raise GhError(f"gh {' '.join(args[:2])}: not in this scratch world (only gh pr view and pr checks)")


def main(argv: list[str]) -> int:
    try:
        print(answer(Path(argv[0]), argv[1:]))
    except GhError as error:
        print(f"gh: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
