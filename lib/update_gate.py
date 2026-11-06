"""Every write checks the store first (design: docs/design/update.md, "Old code that writes after
the switch" and "Cut short").

A process keeps the code of the version it started on, so after an update switched the install link
and migrated the stores, a command, a hook's child or the console can still be old code writing to a
newer store. And an update that dies inside its freeze leaves the stores changed part way, with its
backup the truth. So each store's write, once it holds the store's exclusive lock and before
anything is journaled, checks two things in this order, and nothing is written when:

- check_freeze, before anything of the store is read (a store changed part way may not even
  parse): a freeze is recorded (update.json at one of FREEZE_STEPS): UpdateBusy while its update
  runs (holds update.lock), UpdateCutShort when it died; the record is at a step this code does not
  know (UpdateNewer: a newer bosun wrote it, which the installed code knows), or is there but cannot
  be read (UpdateUnreadable: it may be a freeze, so the write fails closed);
- check_format, on the format the store's file records, before its shape is checked: the store is
  newer than this code (StoreNewer), or older (StoreOlder, which `bosun update` migrates; format 0
  is no store yet, made in this code's format).

The commands that write run under guarded, which does what each refusal calls for, outside every
store lock: a newer store or a newer bosun's record runs the command again through the install
link, with the same arguments and stdin; a freeze cut short is put back first (recover), then the
same; a busy freeze is waited out (WAIT_SECONDS; put back if its update stopped inside it), then the
same; an older store, or a record that cannot be read, is refused in one line (run_again False). A
run again is never run again (RERUN_ENV). Writers that are not commands (bosun's own background
work) catch WriteRefused and report a failed run.

Lock order everywhere: update.lock, the ledger's, the knowledge base's. No store is imported at module
level, as the stores import this module; store_locks imports them when it runs.
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import os
import subprocess
import sys
import time
from collections.abc import Buffer, Callable, Iterator
from pathlib import Path
from typing import BinaryIO

import install_paths
import update_backups
import update_state
import versions
from update_state import UpdateState


RERUN_ENV = "BOSUN_RERUN"
CONFIG_ENV = "BOSUN_CONFIG_DIR"
# The version this process runs: what it runs again goes through this version's install link.
ROOT = Path(__file__).resolve().parents[1]
# How long a refused write waits for an update's lock (another write putting back a freeze cut
# short): within the 10 s of the shortest hook that writes (PostToolUse).
WAIT_SECONDS = 8.0
KNOWLEDGE_DIR = "knowledge"  # the knowledge base, in the config folder
SHORT = 7  # a commit as the messages name it


class WriteRefused(Exception):
    """A write check_freeze or check_format refused: nothing was written. <run_again>: whether
    guarded runs the command again (once what was found is put right), or only says why."""

    run_again = True


class FormatRefused(WriteRefused):
    """A store in another format than this code writes: <found> on disk, <mine> here."""

    def __init__(self, message: str, store: str, found: int, mine: int) -> None:
        super().__init__(message)
        self.store = store
        self.found = found
        self.mine = mine


class StoreNewer(FormatRefused):
    def __init__(self, store: str, found: int, mine: int) -> None:
        super().__init__(f"the {store} is format {found}, newer than this bosun's {mine}", store, found, mine)


class StoreOlder(FormatRefused):
    run_again = False

    def __init__(self, store: str, found: int, mine: int) -> None:
        super().__init__(
            f"the {store} is format {found} and this bosun writes format {mine}; nothing was written: "
            "run bosun update to migrate it",
            store,
            found,
            mine,
        )


class UpdateCutShort(WriteRefused):
    """The update recorded in <state_dir> died inside its freeze: its backup is the truth."""

    def __init__(self, state: UpdateState, state_dir: Path) -> None:
        super().__init__(
            f"an update from {state.from_commit[:SHORT]} to {state.to_commit[:SHORT]} was cut short "
            "while writes were paused"
        )
        self.state = state
        self.state_dir = state_dir


class UpdateBusy(WriteRefused):
    """A freeze recorded while its update still runs (holds update.lock)."""

    def __init__(self, state: UpdateState, state_dir: Path) -> None:
        super().__init__(
            f"an update from {state.from_commit[:SHORT]} to {state.to_commit[:SHORT]} is pausing "
            f"writes (step {state.step})"
        )
        self.state = state
        self.state_dir = state_dir


def _what_to_do(record: str) -> str:
    """What to do about an update record this code cannot act on (<record>: how to name the file)."""
    return (
        "run bosun update first: it resumes an update in progress and puts back one cut short; "
        f"move {record} aside only if bosun update cannot read it either"
    )


class UpdateNewer(WriteRefused):
    """The update's record is at a step this code does not know: a newer bosun wrote it, and the
    installed bosun, which knows the step, decides."""

    def __init__(self, state: UpdateState, state_dir: Path) -> None:
        super().__init__(f"the update's record is at step {state.step}, which a newer bosun wrote")
        self.state = state
        self.path = state_dir / update_state.STATE_FILE

    def again(self) -> str:
        """What a run again refused too says."""
        return (
            f"the update's record is at step {self.state.step}, and the installed bosun does not know it "
            f"either; nothing was written: {_what_to_do(str(self.path))}"
        )


class UpdateUnreadable(WriteRefused):
    """The update's record is there but cannot be read: it may record a freeze, so nothing is
    written, and nothing is run again."""

    run_again = False

    def __init__(self, unreadable: update_state.RecordUnreadable) -> None:
        super().__init__(f"{unreadable}; nothing was written: {_what_to_do('it')}")
        self.path = unreadable.path


def check_freeze(state_dir: Path) -> None:
    """Refuses a write (UpdateBusy, UpdateCutShort) while update.json in <state_dir> records a
    freeze, and (UpdateNewer, UpdateUnreadable) while it holds what this code cannot tell from one.
    Called with the store's exclusive lock held, before anything of the store is read: the backup is
    then the truth, whatever the store holds."""
    try:
        state = update_state.load(state_dir)
    except update_state.RecordUnreadable as exc:
        raise UpdateUnreadable(exc) from exc
    if state is None:
        return
    if not update_state.known(state):
        raise UpdateNewer(state, state_dir)
    if update_state.in_freeze(state):
        if update_state.running(state_dir):
            raise UpdateBusy(state, state_dir)
        raise UpdateCutShort(state, state_dir)


def check_format(store: str, found: int, mine: int) -> None:
    """Refuses a write to <store> (StoreNewer, StoreOlder) whose format on disk is <found> (0: none
    yet) where this code writes <mine>."""
    if found > mine:
        raise StoreNewer(store, found, mine)
    if 0 < found < mine:
        raise StoreOlder(store, found, mine)


def check_write(store: str, found: int, mine: int, state_dir: Path) -> None:
    """Both checks, in their order, for a caller that has the format in hand."""
    check_freeze(state_dir)
    check_format(store, found, mine)


# ---- putting back a freeze cut short ------------------------------------------------------------


@contextlib.contextmanager
def store_locks(state_dir: Path, config_dir: Path) -> Iterator[None]:
    """Both stores' write locks, in the one order: the ledger's, then the knowledge base's (where
    there is one: a knowledge write that makes it meanwhile finds the freeze and writes nothing)."""
    import knowledge_store  # noqa: PLC0415  (the stores import this module)
    import ledger  # noqa: PLC0415

    paths = knowledge_store.Paths(config_dir / KNOWLEDGE_DIR)
    with ledger.lock_at(state_dir, fcntl.LOCK_EX):
        if not paths.root.is_dir():
            yield
            return
        with knowledge_store.locked(paths, write=True):
            yield


def _point_back(from_root: Path) -> None:
    """The install link at <from_root> again, where the update came from a version folder and the
    link is there (a checkout from before versions is never made a link)."""
    install = install_paths.install_path(from_root)
    if install != from_root and install.is_symlink():
        versions.point(install, from_root)


def _past_backup(state: UpdateState) -> bool:
    """Whether <state> is past `backed-up`: the update may have changed the stores since."""
    steps = update_state.STEPS
    return update_state.known(state) and steps.index(state.step) > steps.index("backed-up")


def put_back_held(state_dir: Path, config_dir: Path, state: UpdateState) -> UpdateState:
    """The update <state> records put back, for a caller that holds update.lock and both stores'
    write locks (store_locks), none of which it takes: flock is per open file, so taking one again
    would conflict with the caller's own. The parts of the backup the update could have changed
    restored, the install link pointed back at the version the update came from, and `restored`
    recorded, which it returns. BackupError, with nothing changed, for a record past `backed-up`
    that names no backup or nothing to restore from it (a record before has changed nothing)."""
    if _past_backup(state) and not (state.backup and state.restorable):
        raise update_backups.BackupError(
            f"the update's record is at step {state.step}, past its backup, but names no backup or "
            "nothing to restore from it"
        )
    if state.backup and state.restorable:
        update_backups.restore(Path(state.backup), state_dir, config_dir, state.restorable)
    _point_back(Path(state.from_root))
    restored = update_state.advanced(state, "restored")
    update_state.write(state_dir, restored)
    return restored


def recover(state_dir: Path, config_dir: Path) -> bool:
    """Puts back an update cut short inside its freeze: under update.lock (UpdateRunning when another
    still holds it after WAIT_SECONDS) and then the stores' locks, the record read again (another
    process may have put it back: False), then put_back_held: True. Nothing has been written since
    the backup, so nothing is lost."""
    with update_state.one_update(state_dir, wait=WAIT_SECONDS), store_locks(state_dir, config_dir):
        state = update_state.read(state_dir)
        if state is None or not update_state.in_freeze(state):
            return False
        put_back_held(state_dir, config_dir, state)
    return True


# ---- what a refused command does next -----------------------------------------------------------


class _Recorder(io.RawIOBase):
    """stdin as the command reads it: every byte passed on is kept, so a run again reads the same."""

    def __init__(self, source: BinaryIO) -> None:
        super().__init__()
        self.source = source
        self.seen = bytearray()
        self.ended = False

    def readable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return self.source.isatty()

    def readinto(self, buffer: Buffer, /) -> int:
        view = memoryview(buffer).cast("B")
        read1 = getattr(self.source, "read1", self.source.read)
        data: bytes = read1(len(view))
        view[: len(data)] = data
        self.seen += data
        self.ended = self.ended or not data
        return len(data)

    def replay(self) -> bytes | None:
        """What a run again reads: None for this stdin itself, when nothing of it was read; else the
        bytes read and the rest (none past its end, nor from a terminal, which would wait)."""
        if not self.seen and not self.ended:
            return None
        if self.ended or self.source.isatty():
            return bytes(self.seen)
        return bytes(self.seen) + self.source.read()


@contextlib.contextmanager
def _recorded_stdin() -> Iterator[_Recorder | None]:
    """sys.stdin read through a _Recorder for the block (None without a byte stream under it), and
    put back after."""
    original = sys.stdin
    source = getattr(original, "buffer", None)
    if source is None:
        yield None
        return
    recorder = _Recorder(source)
    sys.stdin = io.TextIOWrapper(
        io.BufferedReader(recorder),
        encoding=getattr(original, "encoding", None) or "utf-8",
        errors=getattr(original, "errors", None) or "strict",
        newline="\n",  # as Python's own stdin: lines split at \n, nothing translated
    )
    try:
        yield recorder
    finally:
        sys.stdin = original


def _say(command: str, message: str) -> int:
    print(f"bosun {command}: {message}", file=sys.stderr)
    return 1


def _rerun(command: str, argv: list[str], stdin: _Recorder | None) -> int:
    """<command> run again through the install link, with the same arguments and stdin; its exit
    code."""
    sys.stdout.flush()
    sys.stderr.flush()
    done = subprocess.run(
        [str(install_paths.install_bin(ROOT)), command, *argv],
        input=stdin.replay() if stdin else None,
        env={**os.environ, RERUN_ENV: "1"},
        check=False,
    )
    return done.returncode if done.returncode >= 0 else 128 - done.returncode


def _put_back(refused: UpdateCutShort) -> str:
    """Says what it does and puts the update back (recover); why it could not, or ""."""
    print(f"{refused}; putting back its backup and {refused.state.from_commit[:SHORT]}", file=sys.stderr)
    config_dir = os.environ.get(CONFIG_ENV)
    if not config_dir:
        return (
            f"{CONFIG_ENV} is not set, so the backup cannot be put back; nothing was written: "
            "run bosun update"
        )
    try:
        recover(refused.state_dir, Path(config_dir))
    except (update_state.UpdateRunning, update_backups.BackupError, OSError) as exc:
        return f"the update's backup could not be put back ({exc}); nothing was written: run bosun update"
    return ""


def _waited_out(state_dir: Path, seconds: float) -> bool:
    """Whether the freeze recorded in <state_dir> ended, or its update stopped, within <seconds>."""
    deadline = time.monotonic() + seconds
    while True:
        state = update_state.read(state_dir)
        if state is None or not update_state.in_freeze(state) or not update_state.running(state_dir):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(update_state.POLL_SECONDS)


def _again(refused: WriteRefused) -> str:
    """Why a run again was refused too."""
    if isinstance(refused, StoreNewer):
        return (
            f"the {refused.store} is format {refused.found}, newer than the installed bosun's "
            f"{refused.mine} too; nothing was written"
        )
    if isinstance(refused, UpdateNewer):
        return refused.again()
    return f"{refused}, and this already ran again on the installed bosun; nothing was written: run it again"


def _settled(refused: WriteRefused) -> str:
    """What the refusal found, put right before the one run again: a freeze cut short put back; a
    busy one waited out, and put back if its update stopped inside it (a run again would only find
    it cut short, and stop). Why it could not be, or ""."""
    if isinstance(refused, UpdateBusy):
        if not _waited_out(refused.state_dir, WAIT_SECONDS):
            return f"{refused}; nothing was written: run it again once the update is done"
        state = update_state.read(refused.state_dir)
        if state is None or not update_state.in_freeze(state):
            return ""
        refused = UpdateCutShort(state, refused.state_dir)
    return _put_back(refused) if isinstance(refused, UpdateCutShort) else ""


def _refused(command: str, argv: list[str], refused: WriteRefused, stdin: _Recorder | None) -> int:
    if not refused.run_again:
        return _say(command, str(refused))
    problem = _settled(refused)
    if problem:
        return _say(command, problem)
    if os.environ.get(RERUN_ENV) == "1":
        return _say(command, _again(refused))
    return _rerun(command, argv, stdin)


def guarded(command: str, run: Callable[[list[str]], int], argv: list[str]) -> int:
    """<run>(<argv>), the main of `bosun <command>`, with what the checks refuse done right: run
    again through the install link with the same arguments and stdin (a store newer than this code,
    or a newer bosun's update record; a freeze cut short, once put back; a busy one, once waited
    out), or refused in one line (a store older than this code, an update record that cannot be
    read, or a run again refused too). Returns the exit code."""
    with _recorded_stdin() as stdin:
        try:
            return run(argv)
        except WriteRefused as refused:
            return _refused(command, argv, refused, stdin)
