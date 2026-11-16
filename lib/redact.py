"""Redaction of credentials and email addresses, for text bosun keeps from sessions: learning's
digest, and what I type in a repo window (the ledger's user-said entries).

Redact a text whole, before cutting it short: a cut can drop a private key's END marker or shorten a
token below its rule's length. A BEGIN marker with no END is still redacted, with the key body after it.

Every rule is bounded (no unbounded run on both sides of a fixed part, no unbounded lazy scan), so
text of any length takes time in proportion to its length: callers redact what they are given whole."""

from __future__ import annotations

import re
from collections.abc import Callable


# A word that makes a name secret, anywhere in the name (AWS_SECRET_ACCESS_KEY, db_password, apiKey).
# The console's Config view masks config keys by these words too, and by any KEY (console_library).
CREDENTIAL_WORD = (
    r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|cookie)"
)
NAME_PART = r"[A-Za-z0-9_]{0,64}"  # bounded: unbounded on both sides of the word was quadratic
# The name, its closing quote if it is a quoted key (JSON; escaped in JSON inside JSON), and the =
# or : after it, on its line.
CREDENTIAL_NAME = (
    rf"\b(?P<name>{NAME_PART}{CREDENTIAL_WORD}{NAME_PART})(?P<sep>(?:\\?[\"'])?[ \t]*[=:][ \t]*)"
)
# A character of a bare value: not space, a quote or what closes the value in code or markup, nor
# an escaped line break or quote (\n, \" in a JSON-serialized text).
BARE = r"""(?:[^\s'"`,;})\]\\]|\\(?![nrt"]))"""
# ... and one that makes it look like a secret: anything but a letter or _.
SYMBOL = r"""(?:[^A-Za-z_\s'"`,;})\]\\]|\\(?![nrt"]))"""
# The value: quoted (escaped quotes too, as in a JSON-serialized tool call), up to its closing quote
# or the end of its line or text (a value cut short); or bare, when it looks like a secret: 16
# characters or more, or 6 or more with something other than a letter or _ (prose such as
# "Password: required" or max_tokens=LOOKUP_TOKENS stays).
CREDENTIAL_VALUE = (
    r"(?:(?P<eq>\\\")(?:[^\"\\\n]|\\[^\"nr\n]){6,}(?P<eqc>\\?\")?"
    r"|(?P<dq>\")(?:[^\"\\\n]|\\.){6,}(?P<dqc>\")?"
    r"|(?P<sq>\\?')(?:[^'\\\n]|\\[^'\n]){6,}(?P<sqc>\\?')?"
    rf"|{BARE}{{16,}}|(?={BARE}{{0,15}}{SYMBOL}){BARE}{{6,}})"
)
BASE64 = r"[A-Za-z0-9+/=]"
LINE_GAP = r"[ \t]*(?:\r?\n|(?:\\r)?\\n)?[ \t]*"
KEY_MARKER = r"[A-Z ]{0,32}PRIVATE KEY(?: BLOCK)?-----"
# An encrypted key's headers (Proc-Type, DEK-Info), and the blank line after them.
KEY_HEADERS = rf"(?:(?:{LINE_GAP}(?:Proc-Type|DEK-Info):[^\n]{{0,200}})+[ \t]*(?:\r?\n|\\n)?)?"
KEY_LENGTH = 20_000  # characters a whole key may span between its markers (a PGP key block too)


def credential(match: re.Match[str]) -> str:
    """A secret-named value as <redacted>, kept in the quotes it had."""
    groups = match.groupdict()
    opened = groups["eq"] or groups["dq"] or groups["sq"] or ""
    closed = groups["eqc"] or groups["dqc"] or groups["sqc"] or ""
    return f"{groups['name']}{groups['sep']}{opened}<redacted>{closed}"


Replacement = str | Callable[[re.Match[str]], str]
REDACTIONS: tuple[tuple[re.Pattern[str], Replacement], ...] = (
    (
        re.compile(
            rf"-----BEGIN {KEY_MARKER}(?:(?!-----BEGIN )[\s\S]){{0,{KEY_LENGTH}}}?-----END {KEY_MARKER}"
        ),
        "<key>",
    ),
    # A key cut short (no END marker): the marker, an encrypted key's headers, and the base64 body
    # after them in lines of 16 or more, over single line breaks (or JSON's escaped ones), up to a
    # blank line or anything else.
    (re.compile(rf"-----BEGIN {KEY_MARKER}{KEY_HEADERS}(?:{LINE_GAP}{BASE64}{{16,}})*"), "<key>"),
    (re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"), "<token>"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "<token>"),
    (re.compile(r"\b(?:xox[abeoprs]|xapp)(?:\.xox[bp])?-[A-Za-z0-9-]{10,}"), "<token>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "<token>"),
    (re.compile(r"\b(?:[rs]k_(?:live|test)|whsec)_[A-Za-z0-9]{16,}"), "<token>"),
    (re.compile(r"\bATATT[A-Za-z0-9_=-]{20,}"), "<token>"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}"), "<token>"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"), "<token>"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<aws-key>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "<jwt>"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 <token>"),
    (
        re.compile(
            r"(https?://[^\s?#\"'<>]+)\?[^\s\"'<>]*(?:code|token|state|secret|key|sig|password|auth)=[^\s\"'<>]*"
        ),
        r"\1?<redacted>",
    ),
    # A password in a connection URL, between the user's colon and the @ of the host: the scheme,
    # user and host stay.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://[^\s:/@]{0,64}:)[^\s/@]{1,256}@"), r"\1<redacted>@"),
    (re.compile(f"(?i){CREDENTIAL_NAME}{CREDENTIAL_VALUE}"), credential),
    (re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}\b"), "<email>"),
)


def redact(text: str) -> str:
    """Remove credentials (tokens, keys, passwords, signed URL queries) and email addresses."""
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text
