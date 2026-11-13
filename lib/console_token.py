"""The console's access token, kept across restarts (`bosun console`, docs/design/console.md), and
the lock a running console holds in the same folder, so that `bosun update` can tell it runs.

The token lives in $BOSUN_STATE_DIR/console/token: the file 0600, its folder 0700, written
atomically. A start reuses it, so an open browser tab stays signed in when the console restarts.
A file that is missing, unreadable, malformed, or readable by anyone but its owner is replaced
by a new token: a token others could read is not reused. With persistence off, every start mints
a new one and leaves the file alone. The token itself is never logged here.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Iterator
from pathlib import Path


TOKEN_BYTES = 32
PORT_LIMIT = 65535  # the highest port
TOKEN_DIR = "console"  # noqa: S105 - a folder name, not a secret
TOKEN_FILE = "token"  # noqa: S105 - a file name, not a secret
RUNNING_LOCK = "running.lock"  # held, shared, by each console while it serves
DIR_MODE = 0o700
FILE_MODE = 0o600
WAIT_POLL_SECONDS = 3.0  # how often the launchd job looks again while a console run by hand serves
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


@contextlib.contextmanager
def serving(state_dir: Path, port: int | None = None) -> Iterator[None]:
    """The console's running lock, held (shared) for the block: while the server runs. It keeps
    the port the console listens on, which `bosun console --url` reads back (served_port)."""
    private_dir(state_dir / TOKEN_DIR)
    fd = os.open(state_dir / TOKEN_DIR / RUNNING_LOCK, os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        if port is not None:
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{port}\n".encode("ascii"), 0)
        yield
    finally:
        os.close(fd)


def served_port(state_dir: Path) -> int | None:
    """The port the console serving from <state_dir> recorded in its running lock; None when it
    recorded none (a console from before it did) or the file holds anything else."""
    try:
        text = (state_dir / TOKEN_DIR / RUNNING_LOCK).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() and 0 < int(text) <= PORT_LIMIT else None


def running(state_dir: Path) -> bool:
    """Whether a console serves from <state_dir>: its running lock is held. The lock goes with the
    process, so a console that died is not running."""
    try:
        fd = os.open(state_dir / TOKEN_DIR / RUNNING_LOCK, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def wait_free(
    state_dir: Path,
    say: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Return once no console serves from <state_dir>: its running lock could be taken exclusively (and
    is let go again). The launchd job waits like this for a console run by hand to end, polling every
    WAIT_POLL_SECONDS and calling <say> once, instead of exiting for launchd to start it again."""
    private_dir(state_dir / TOKEN_DIR)
    fd = os.open(state_dir / TOKEN_DIR / RUNNING_LOCK, os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        told = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if say is not None and not told:
                    say()
                    told = True
                sleep(WAIT_POLL_SECONDS)
    finally:
        os.close(fd)
