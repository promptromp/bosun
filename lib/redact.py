"""Redaction of credentials and email addresses, for text bosun keeps from sessions: learning's
digest, and what I type in a repo window (the ledger's user-said entries).

Redact a text whole, before cutting it short: a cut can drop a private key's END marker or shorten a
token below its rule's length. A BEGIN marker with no END is still redacted, with the key body after it."""

from __future__ import annotations

import re


# A word that makes a name secret, anywhere in the name (AWS_SECRET_ACCESS_KEY, db_password, apiKey).
CREDENTIAL_WORD = r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
# The name, its closing quote if it is a quoted key (JSON), and the = or : after it, on its line.
CREDENTIAL_NAME = rf"\b([A-Za-z0-9_]*{CREDENTIAL_WORD}[A-Za-z0-9_]*)([\"']?[ \t]*[=:][ \t]*)"
# The value: double or single quoted (kept quoted), or bare up to a space or quote.
CREDENTIAL_VALUE = r"""(?:(")(?:[^"\\\n]|\\.){6,}"|(')[^'\n]{6,}'|[^\s'"]{6,})"""
BASE64 = r"[A-Za-z0-9+/=]"
LINE_GAP = r"[ \t]*(?:\r?\n|(?:\\r)?\\n)?[ \t]*"

REDACTIONS = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "<key>",
    ),
    # A key cut short (no END marker): the marker and the base64 body after it, over single line
    # breaks (or JSON's escaped ones), up to a blank line or anything that is not base64.
    (re.compile(rf"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:{LINE_GAP}{BASE64}+)*"), "<key>"),
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
    (re.compile(f"(?i){CREDENTIAL_NAME}{CREDENTIAL_VALUE}"), r"\1\2\3\4<redacted>\3\4"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
)


def redact(text: str) -> str:
    """Remove credentials (tokens, keys, passwords, signed URL queries) and email addresses."""
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text
