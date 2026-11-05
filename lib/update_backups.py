"""The backups `bosun update` takes before it changes anything, and the restore of what a failed
step changed (design: docs/design/update.md, "Backups").

A backup is a folder `$BOSUN_STATE_DIR/backups/<time>-<from>-<to>/` holding `state/<name>` and
`config/<name>` for everything the file tables class as ledger, knowledge or settings, plus the
working files that carry work in flight (IN_FLIGHT). It is made with APFS clones where there are
any, and ends with its manifest (every file with its SHA-256, both versions, the format versions),
so a folder without one was cut short and is no backup. The last KEEP are kept.

A deletion of a person deletes every backup (delete_all, called as the last step of the knowledge
base's forget_in_journal): a backup holds the whole knowledge base, so restoring one from before
the forget would bring the person back.

No store (ledger, knowledge) is imported here: the stores import this module."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import journal
from uninstall_plan import CONFIG_FILES, KNOWLEDGE, LEDGER, SETTINGS, STATE_FILES, kind_of


BACKUPS_DIR = "backups"
KEEP = 3
MANIFEST = "manifest.json"
SUMMARY = "summary.md"
REHEARSAL = Path("update") / "rehearsal"

# Working files that carry work in flight, backed up with the records.
IN_FLIGHT = ("events", "signals", "slack-watch", "login.json", "codex-sessions.json", "agent-changes.json")
# What the freeze's locks cover, so what a failed freeze puts back (an event queued meanwhile is
# never undone).
FROZEN = (
    "state/ledger.json",
    "state/ledger.json.bak",
    "state/ledger.md",
    "state/journal",
    "state/replica.json",
    "config/knowledge",
)
_KEPT_KINDS = (LEDGER, KNOWLEDGE, SETTINGS)
_NEVER = (BACKUPS_DIR, "logs", "dry-runs", "update")
_CHUNK = 1 << 20


class BackupError(Exception):
    """A backup that cannot be made, read or restored from."""


@dataclass(frozen=True)
class Manifest:
    from_commit: str
    to_commit: str
    taken_at: str
    formats: Mapping[str, int]
    files: Mapping[str, str]  # relative path ("state/ledger.json") to SHA-256


def _entries(folder: Path, table: tuple[tuple[str, str], ...], extra: Sequence[str]) -> list[Path]:
    if not folder.is_dir():
        return []
    names = sorted(
        entry.name
        for entry in folder.iterdir()
        if entry.name not in _NEVER
        and (
            kind_of(entry.name, table) in _KEPT_KINDS
            or any(fnmatch.fnmatchcase(entry.name, x) for x in extra)
        )
    )
    return [folder / name for name in names]


def backup_set(state_dir: Path, config_dir: Path) -> list[tuple[str, Path]]:
    """Each file or folder classed ledger, knowledge or settings, plus IN_FLIGHT, as
    ("state/<name>" | "config/<name>", path); never the backups, logs, dry runs or the update's."""
    return [
        *((f"state/{p.name}", p) for p in _entries(state_dir, STATE_FILES, IN_FLIGHT)),
        *((f"config/{p.name}", p) for p in _entries(config_dir, CONFIG_FILES, ())),
    ]


def digest(path: Path) -> str:
    """The SHA-256 of <path>'s content."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest()


def _clone_flags() -> list[list[str]]:
    """The ways `cp` clones: macOS's -c, GNU's --reflink=auto; a plain copy last (found by trying).
    -H follows a link given as the entry itself (a stow- or chezmoi-linked folder), so the backup
    holds the data, not a bare link; links inside it stay links."""
    return [["cp", "-c", "-H", "-pR"], ["cp", "-H", "-pR", "--reflink=auto"], ["cp", "-H", "-pR"]]


def _copy(source: Path, target: Path) -> None:
    last = ""
    for command in _clone_flags():
        done = subprocess.run(
            [*command, str(source), str(target)], capture_output=True, text=True, check=False
        )
        if done.returncode == 0:
            return
        last = done.stderr.strip()
        if target.exists():
            shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink()
    raise BackupError(f"could not copy {source}: {last}")


LINK = "link:"  # a link's manifest value: its target text (a digest is only hex, so no clash)


def _entries_under(root: Path) -> Iterator[Path]:
    """Every file and link under <root>, a folder or a link to one (a stow-linked folder is walked
    through; links inside it are not followed); <root> itself when it is a file or a link to none."""
    if not root.is_dir():
        if root.is_symlink() or root.exists():
            yield root
        return
    for folder, dirs, names in os.walk(root):
        for name in (*dirs, *names):
            path = Path(folder) / name
            if path.is_symlink() or not path.is_dir():
                yield path


def _value(path: Path) -> str:
    return LINK + os.readlink(path) if path.is_symlink() else digest(path)


def _digests(backup: Path) -> dict[str, str]:
    return {
        path.relative_to(backup).as_posix(): _value(path)
        for side in ("state", "config")
        if (backup / side).is_dir()
        for path in sorted(_entries_under(backup / side))
    }


def _write_manifest(backup: Path, manifest: Manifest) -> None:
    body = {
        "from": manifest.from_commit,
        "to": manifest.to_commit,
        "taken_at": manifest.taken_at,
        "formats": dict(manifest.formats),
        "files": dict(manifest.files),
    }
    tmp = backup / f".{MANIFEST}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        journal.write_all(fd, (json.dumps(body, indent=1, sort_keys=True) + "\n").encode())
        journal.flush_to_disk(fd)
    finally:
        os.close(fd)
    journal.replace_durably(tmp, backup / MANIFEST)


def read_manifest(backup: Path) -> Manifest:
    """<backup>'s manifest; BackupError when there is none (a backup cut short) or it is unreadable."""
    try:
        body = json.loads((backup / MANIFEST).read_text())
        return Manifest(
            str(body["from"]),
            str(body["to"]),
            str(body["taken_at"]),
            {str(k): int(v) for k, v in body["formats"].items()},
            {str(k): str(v) for k, v in body["files"].items()},
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise BackupError(f"{backup} has no readable manifest: {exc}") from exc


def take(
    state_dir: Path,
    config_dir: Path,
    from_commit: str,
    to_commit: str,
    formats: Mapping[str, int],
    now: datetime,
) -> Path:
    """A backup of the backup set, as <state_dir>/backups/<time>-<from>-<to>/, its manifest last.
    A folder that cannot be finished is deleted, so there is never half of one with a manifest."""
    moment = now.astimezone(UTC)
    backup = state_dir / BACKUPS_DIR / f"{moment:%Y%m%d-%H%M%S}-{from_commit[:7]}-{to_commit[:7]}"
    backup.mkdir(parents=True, mode=0o700)
    try:
        for relative, path in backup_set(state_dir, config_dir):
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy(path, target)
        manifest = Manifest(from_commit, to_commit, moment.isoformat(), dict(formats), _digests(backup))
        _write_manifest(backup, manifest)
    except BaseException:
        shutil.rmtree(backup, ignore_errors=True)
        raise
    return backup


def _under(relative: str, prefixes: Sequence[str]) -> bool:
    return any(relative == p or relative.startswith(p.rstrip("/") + "/") for p in prefixes)


def _destination(relative: str, state_dir: Path, config_dir: Path) -> Path:
    side, _, rest = relative.partition("/")
    if side not in ("state", "config") or not rest or ".." in rest.split("/"):
        raise BackupError(f"the manifest names {relative!r}, which is not a state or config file")
    return (state_dir if side == "state" else config_dir) / rest


def _flush_file(path: Path) -> None:
    fd = os.open(path, os.O_RDWR)
    try:
        journal.flush_to_disk(fd)
    finally:
        os.close(fd)


def _put_back(source: Path, target: Path, expected: str) -> None:
    """<source> written beside <target> (through it when it is a link), flushed, checked, renamed over."""
    if target.is_symlink() and not expected.startswith(LINK):
        target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.restoring")
    tmp.unlink(missing_ok=True)
    if expected.startswith(LINK):
        os.symlink(expected[len(LINK) :], tmp)
    else:
        shutil.copy2(source, tmp)
        _flush_file(tmp)
        if digest(tmp) != expected:
            tmp.unlink()
            raise BackupError(f"{target} did not read back as the backup has it")
    journal.replace_durably(tmp, target)


def _same_now(target: Path, expected: str) -> bool:
    if expected.startswith(LINK):
        return target.is_symlink() and LINK + os.readlink(target) == expected
    return target.is_file() and digest(target) == expected


def _put_back_all(
    manifest: Manifest, backup: Path, state_dir: Path, config_dir: Path, only: Sequence[str]
) -> list[str]:
    done: list[str] = []
    for relative, expected in sorted(manifest.files.items()):
        if not _under(relative, only):
            continue
        target = _destination(relative, state_dir, config_dir)
        if _same_now(target, expected):
            continue
        source = backup / relative
        if not expected.startswith(LINK) and (not source.is_file() or digest(source) != expected):
            raise BackupError(f"{source} is not as the manifest has it: nothing more was restored")
        _put_back(source, target, expected)
        done.append(relative)
    return done


def _key(root: Path, prefix: str, path: Path) -> str:
    return prefix if path == root else f"{prefix}/{path.relative_to(root).as_posix()}"


def _remove_extra(
    backup: Path, state_dir: Path, config_dir: Path, manifest: Manifest, only: Sequence[str]
) -> list[str]:
    """What is under each prefix and the manifest does not list goes, lock files excepted: the
    freeze holds them open."""
    removed: list[str] = []
    for prefix in only:
        root = _destination(prefix, state_dir, config_dir)
        for path in list(_entries_under(root)):
            key = _key(root, prefix, path)
            if key not in manifest.files and not path.name.endswith(".lock"):
                path.unlink()
                removed.append(key)
        _remove_empty_folders(root, prefix, backup)
    return removed


def _remove_empty_folders(root: Path, prefix: str, backup: Path) -> None:
    """The folders the backup does not have at all, left empty, go (deepest first)."""
    if not root.is_dir():
        return
    for folder in [Path(f) for f, _dirs, _names in os.walk(root, topdown=False)]:
        absent = not (backup / _key(root, prefix, folder)).is_dir()
        if absent and not any(folder.iterdir()) and (folder != root or not root.is_symlink()):
            folder.rmdir()


def restore(backup: Path, state_dir: Path, config_dir: Path, only: Sequence[str]) -> list[str]:
    """Makes each <only> prefix as the manifest has it: each file or link whose content differs now
    is put back (checked against the manifest, before and after), and what is there and the manifest
    does not list is deleted (files, links and the folders the backup lacks; lock files stay: the
    freeze holds them). Under the freeze's locks only the update wrote there, so what is new came
    from the step that failed. Returns what it put back and what it deleted."""
    manifest = read_manifest(backup)
    done = _put_back_all(manifest, backup, state_dir, config_dir, only)
    return sorted({*done, *_remove_extra(backup, state_dir, config_dir, manifest, only)})


def _folders(state_dir: Path) -> list[Path]:
    folder = state_dir / BACKUPS_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))


def listed(state_dir: Path) -> list[Path]:
    """The backups, oldest first (their names start with the time): the folders with a manifest,
    which is written last, so one without is a backup cut short and none."""
    return [p for p in _folders(state_dir) if (p / MANIFEST).is_file()]


def prune(state_dir: Path, keep: int = KEEP) -> list[Path]:
    """Deletes the oldest backups beyond <keep>, and the folders of backups cut short (they hold
    copied records); returns them."""
    good = listed(state_dir)
    kept = good[-keep:] if keep > 0 else []
    gone = [p for p in _folders(state_dir) if p not in kept]
    for backup in gone:
        shutil.rmtree(backup)
    return gone


def delete_all(state_dir: Path) -> int:
    """Every backup, whole, and a rehearsal copy left in update/rehearsal; returns how many backups.
    A failure raises, so a deletion that could not finish is not taken for done."""
    count = len(listed(state_dir))
    folder = state_dir / BACKUPS_DIR
    if folder.is_dir():
        shutil.rmtree(folder)
    rehearsal = state_dir / REHEARSAL
    if rehearsal.is_dir():
        shutil.rmtree(rehearsal)
    return count
