"""The console's access token, kept across restarts (`bosun console`, docs/design/console.md).

The token lives in $BOSUN_STATE_DIR/console/token: the file 0600, its folder 0700, written
atomically. A start reuses it, so an open browser tab stays signed in when the console restarts.
A file that is missing, unreadable, malformed, or readable by anyone but its owner is replaced
by a new token: a token others could read is not reused. With persistence off, every start mints
a new one and leaves the file alone. The token itself is never logged here.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
from pathlib import Path


TOKEN_BYTES = 32
TOKEN_DIR = "console"  # noqa: S105 - a folder name, not a secret
TOKEN_FILE = "token"  # noqa: S105 - a file name, not a secret
DIR_MODE = 0o700
FILE_MODE = 0o600
# What secrets.token_urlsafe(TOKEN_BYTES) produces: 43 URL-safe characters, unpadded.
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,}")
OTHERS_BITS = stat.S_IRWXG | stat.S_IRWXO


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_path(state_dir: Path) -> Path:
    return state_dir / TOKEN_DIR / TOKEN_FILE


def private(path: Path) -> bool:
    """Only its owner can read or write <path>."""
    return not path.stat().st_mode & OTHERS_BITS


def stored_token(path: Path) -> str | None:
    """The token in <path>, if it is there, private and well-formed; None otherwise."""
    try:
        if not private(path):
            return None
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if TOKEN_PATTERN.fullmatch(value) else None


def private_dir(directory: Path) -> None:
    directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    os.chmod(directory, DIR_MODE)


def write_token(path: Path, token: str) -> None:
    """Write <token> to <path> atomically, created 0600 in a 0700 folder."""
    private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(token + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def console_token(state_dir: Path, persist: bool, rotate: bool = False) -> str:
    """The token this start uses: the stored one unless <rotate>, else a new one, stored when
    <persist>."""
    if not persist:
        return new_token()
    path = token_path(state_dir)
    token = None if rotate else stored_token(path)
    if token is None:
        token = new_token()
        write_token(path, token)
    return token


def persist_setting(value: str | None) -> bool:
    """BOSUN_CONSOLE_PERSIST_TOKEN: on unless "0" (default on)."""
    return (value or "1").strip() != "0"
