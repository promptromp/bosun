"""Loose text similarity for near-duplicate hints: content words and how much of the shorter
set the other contains. Used by learning's outreach overlap check, by `ledger spawn` and by
lib/activity.py (which held item a prompt is about)."""

from __future__ import annotations

import re


WORD = re.compile(r"[a-z0-9_]{3,}")
STOPWORD_TEXT = (
    "the and for with that this from are was were but not you your our can should would could "
    "have has had does did will what when where which who how why than then them they its into "
    "about also only one more some any all both each per via ask asks asked please thread reply "
    "message"
)
STOPWORDS = frozenset(STOPWORD_TEXT.split())
MIN_WORDS = 3


def words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in STOPWORDS}


def containment(a: set[str], b: set[str]) -> float:
    """How much of the shorter word set the other contains (a near-duplicate often adds to it)."""
    return len(a & b) / min(len(a), len(b)) if len(a) >= MIN_WORDS and len(b) >= MIN_WORDS else 0.0
