"""What `bosun update --check` answers, from facts the caller gathered, and the release tag rule.

Pure: no git, no files. lib/update_lookup.py gathers the facts (the remote's tags, the installed commit,
the notes, what was skipped) and tools/release.py, which makes the tags, uses the tag rule from here, so
the rule that makes a tag and the rule that finds the newest one are one.

A release tag is `v<YYYY.MM.DD>`; a second one the same day is `v<YYYY.MM.DD>.2`, then `.3` (`.0` and `.1`
are not release tags). The newest is the latest by date, then by number. Design: docs/design/menu-bar.md,
"Releases".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


CONTRACT = 1  # the shape of the check's JSON; the menu bar app reads it
CHANNELS = ("releases", "main")
RELEASE_TAG = re.compile(r"^v(\d{4})\.(\d{2})\.(\d{2})(?:\.(\d+))?$")
FIRST_REPEAT = 2  # a second release of a day is `.2`
PEELED = "^{}"  # what `git ls-remote` appends to a tag's name for the commit an annotated tag points at
TAGS_PREFIX = "refs/tags/"
SHORT = 12  # as many characters of a commit as a version folder's name


def release_key(tag: str) -> tuple[int, int, int, int] | None:
    """(year, month, day, number) of a release tag, to order them by; None for any other tag. The
    day's first has no number and a later one starts at 2, so `.0` and `.1` are not release tags."""
    found = RELEASE_TAG.match(tag.strip())
    if not found:
        return None
    if found[4] is None:
        return int(found[1]), int(found[2]), int(found[3]), 1
    if int(found[4]) < FIRST_REPEAT:
        return None
    return int(found[1]), int(found[2]), int(found[3]), int(found[4])


def parse_ls_remote(text: str) -> list[tuple[str, str]]:
    """(ref, commit) for each line of `git ls-remote` output, `refs/tags/` taken off the ref (its
    `^{}` kept: it marks the commit of an annotated tag)."""
    pairs = []
    for line in text.splitlines():
        sha, _, ref = line.partition("\t")
        if sha and ref:
            pairs.append((ref.removeprefix(TAGS_PREFIX), sha))
    return pairs


def release_commits(tags: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Each release tag and its commit, from (ref, sha) pairs of `git ls-remote --tags`: for an
    annotated tag the sha of its peeled entry (`<tag>^{}`), not of the tag object. Other tags are left out."""
    plain: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for ref, sha in tags:
        name = ref.removeprefix(TAGS_PREFIX)
        into = peeled if name.endswith(PEELED) else plain
        into[name.removesuffix(PEELED)] = sha
    return {tag: peeled.get(tag, sha) for tag, sha in plain.items() if release_key(tag)}


def newest_release(tags: Iterable[tuple[str, str]]) -> tuple[str, str] | None:
    """(tag, commit) of the newest release among <tags> (release_commits's input); None for none."""
    commits = release_commits(tags)
    keyed = [(key, tag) for tag in commits if (key := release_key(tag))]
    if not keyed:
        return None
    tag = max(keyed)[1]
    return tag, commits[tag]


def version_at(commit: str, tags: Iterable[tuple[str, str]]) -> str | None:
    """The newest release tag that points at <commit>; None when none does."""
    at = [
        (key, tag)
        for tag, sha in release_commits(tags).items()
        if sha == commit and (key := release_key(tag))
    ]
    return max(at)[1] if at else None


def url_without_credentials(url: str) -> str:
    """<url> as it may be shown: without a user name, password or token before its host."""
    if "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    return f"{scheme}://{host.rpartition('@')[2]}{slash}{path}"


def count_notes(notes: str) -> int:
    """How many bullets (`- <subject>` lines) <notes> holds."""
    return sum(1 for line in notes.splitlines() if line.strip())


def check(  # noqa: PLR0913 - the five the caller always has, and two it may name
    installed_commit: str,
    installed_version: str | None,
    newest: tuple[str, str] | None,
    skipped: str | None,
    ahead: bool,
    *,
    channel: str = "releases",
    notes: str = "",
) -> dict[str, Any]:
    """The answer to "is there an update?".

    <newest> is the (version, commit) an update would install: the newest release's tag and commit, or
    on the `main` channel the branch head's short commit and commit; None when there is none. <skipped>
    is the version the person asked not to be offered; skipped is true while it is the latest, and the
    update stays available (the one who asks decides). <ahead>: the installed commit descends from the
    latest, so there is nothing to update to. <notes> is what the latest holds that the install lacks."""
    latest = None if newest is None else {"version": newest[0], "commit": newest[1], "notes": notes}
    differs = newest is not None and newest[1] != installed_commit
    return {
        "contract": CONTRACT,
        "channel": channel,
        "installed": {"version": installed_version, "commit": installed_commit},
        "latest": latest,
        "available": differs and not ahead,
        "skipped": newest is not None and newest[0] == skipped,
        "ahead": differs and ahead,
        "error": None,
    }


def failed(channel: str, installed_commit: str | None, error: str) -> dict[str, Any]:
    """The answer when the check could not be made: <error> says why, and nothing is to be had."""
    return {
        "contract": CONTRACT,
        "channel": channel,
        "installed": {"version": None, "commit": installed_commit},
        "latest": None,
        "available": False,
        "skipped": False,
        "ahead": False,
        "error": error,
    }


def _you_have(answer: Mapping[str, Any]) -> str:
    installed = answer["installed"]
    return installed["version"] or str(installed["commit"])[:SHORT]


def describe(answer: Mapping[str, Any]) -> str:
    """The check's answer as one line."""
    latest = answer["latest"]
    if answer["error"]:
        return f"bosun update --check: {answer['error']}"
    if latest is None:
        return f"bosun has no release yet (you have {_you_have(answer)})"
    if answer["ahead"]:
        what = "the newest release" if answer["channel"] == "releases" else "the head of main"
        return f"bosun is ahead of {what} ({latest['version']})"
    if not answer["available"]:
        return f"bosun is up to date ({_you_have(answer)})"
    if answer["channel"] == "main":
        count = count_notes(latest["notes"])
        return f"{count} commit{'' if count == 1 else 's'} on main since yours"
    skipped = " (skipped)" if answer["skipped"] else ""
    return f"bosun {latest['version']} is available (you have {_you_have(answer)}){skipped}"
