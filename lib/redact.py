"""Redaction of credentials and email addresses, for text bosun keeps from sessions: learning's
digest, and what I type in a repo window (the ledger's user-said entries)."""

from __future__ import annotations

import re


REDACTIONS = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "<key>",
    ),
    (re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"), "<token>"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "<token>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "<token>"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<aws-key>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "<jwt>"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 <token>"),
    (
        re.compile(r"(?i)\b([A-Z0-9_]*(?:password|passwd|secret|token|api[_-]?key))(\s*[=:]\s*)[^\s'\"]{6,}"),
        r"\1\2<redacted>",
    ),
    (
        re.compile(
            r"(https?://[^\s?#\"'<>]+)\?[^\s\"'<>]*(?:code|token|state|secret|key|sig|password|auth)=[^\s\"'<>]*"
        ),
        r"\1?<redacted>",
    ),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
)


def redact(text: str) -> str:
    """Remove credentials (tokens, keys, passwords, signed URL queries) and email addresses."""
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text
