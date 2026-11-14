"""The commit hook's unit tests (.pre-commit-config.yaml, `unit-tests`): the tests the change reaches
(`pytest --impacted`), skipped with one line when exactly the same inputs passed them before.

Run it as `unit_hook.py <pytest arguments>`: it runs `python -m pytest <arguments>` with the interpreter
it runs on, and exits with pytest's code. When pytest passes (exit 0) and the checkout is the same at the
end as at the start, it records the key of that run in tests/.unit-cache/passes (git-ignored, per checkout;
UNIT_CACHE_DIR names another folder), one `<key> <epoch seconds>` line per pass, the last 200 kept. The next
run with the same key, less than a day later, prints that it already passed and exits 0 without pytest.
BOSUN_UNIT_NO_CACHE=1 ignores the record (and still records a pass).

The key is a hash of everything the run's selection and its tests read that can differ between two runs in
one checkout:
  - the files on disk: the tree `git add -A` would stage, taken in a copy of the index (tracked files as
    they are now, and untracked ones that are not ignored, which pytest collects and the selection counts);
  - the index's own tree (`git write-tree`: what the commit would hold), since the selection counts staged
    and unstaged changes both, and the tests list what is tracked. Under the commit hook pre-commit has set
    the unstaged changes aside, so the files on disk are the index plus the untracked files;
  - HEAD, the commit the selection compares against;
  - the Python that runs pytest (its version, build and path), uv.lock and pyproject.toml (the locked tools
    and the selection rules; both are in the trees too, named here so the key says so), the arguments, and
    the environment variables that can change a test (PATH as the tests search it: without the folder git
    puts first for its hooks or a folder named twice; PYTHON*, PYTEST*, BOSUN_*).
Whatever git cannot say, an unreadable or odd record, a pass a day old or in the future: pytest runs. The
record can only ever skip a run whose key it holds; it never fails or passes a run by itself.
Plain python3 is enough; no third-party module."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path


MAX_AGE = 86400  # a pass is good for a day
KEEP = 200  # lines the record keeps
KEY_LENGTH = 64  # a SHA-256 in hex
LINE_FIELDS = 2  # `<key> <epoch seconds>`
EPOCH_DIGITS = 12  # more is no time of ours
NO_CACHE = "BOSUN_UNIT_NO_CACHE"
ENV_PREFIXES = ("PYTHON", "PYTEST", "BOSUN_")
INPUT_FILES = ("uv.lock", "pyproject.toml")


def git(root: Path, *args: str, env: Mapping[str, str] | None = None) -> str | None:
    """git's output, stripped, or None if it failed."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def index_file(root: Path, env: Mapping[str, str]) -> Path | None:
    """The index git uses here: GIT_INDEX_FILE if the commit set one (a partial commit has its own), or
    this checkout's (a worktree's own)."""
    named = env.get("GIT_INDEX_FILE")
    if named:
        return Path(named) if os.path.isabs(named) else root / named
    found = git(root, "rev-parse", "--git-path", "index")
    if found is None:
        return None
    return Path(found) if os.path.isabs(found) else root / found


def disk_tree(root: Path, env: Mapping[str, str]) -> str | None:
    """The tree of the files on disk, as `git add -A` stages them, in a copy of the index: the real one is
    not touched, and the copy's stat information spares hashing every file again."""
    source = index_file(root, env)
    with tempfile.TemporaryDirectory(prefix="unit-index.") as scratch:
        copy = Path(scratch) / "index"
        if source is not None and source.is_file():
            shutil.copyfile(source, copy)
        scratch_env = {**env, "GIT_INDEX_FILE": str(copy)}
        if git(root, "add", "-A", env=scratch_env) is None:
            return None
        return git(root, "write-tree", env=scratch_env)


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def run_key(root: Path, args: Sequence[str], env: Mapping[str, str]) -> str | None:
    """The key of a run of pytest with <args> in <root>, or None when git cannot give every part of it."""
    trees = {
        "disk": disk_tree(root, env),
        "index": git(root, "write-tree", env=env),
        "head": git(root, "rev-parse", "--verify", "-q", "HEAD", env=env) or "none",  # none: the first commit
    }
    if trees["disk"] is None or trees["index"] is None:
        return None
    variables = {k: v for k, v in env.items() if wanted_variable(k)}
    variables["PATH"] = search_path(env.get("PATH", ""), git(root, "--exec-path"))
    parts = {
        **trees,
        "python": [sys.version, sys.executable, os.path.realpath(sys.executable)],
        "files": {name: file_digest(root / name) for name in INPUT_FILES},
        "args": list(args),
        "env": variables,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def search_path(path: str, exec_path: str | None) -> str:
    """PATH as the tests search it, the same for the hook's run and one by hand: without the folder of
    git's own commands, which git puts first for a hook it runs, and without a folder again after its first
    time (`uv run` under `uv run` puts the virtualenv's in twice), neither of which changes what a name
    finds."""
    skip = {os.path.realpath(exec_path)} if exec_path else set()
    kept = []
    for folder in path.split(os.pathsep):
        real = os.path.realpath(folder) if folder else folder
        if real not in skip:
            kept.append(folder)
            skip.add(real)
    return os.pathsep.join(kept)


def wanted_variable(name: str) -> bool:
    return name.startswith(ENV_PREFIXES) and name != NO_CACHE


def is_key(text: str) -> bool:
    return len(text) == KEY_LENGTH and all(c in "0123456789abcdef" for c in text)


def passed_at(record: Path, key: str, now: int) -> int | None:
    """When <key> was recorded as passing, if less than a day before <now> and not after it."""
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in reversed(lines):
        fields = line.split()
        if (
            len(fields) != LINE_FIELDS
            or fields[0] != key
            or not fields[1].isdigit()
            or len(fields[1]) > EPOCH_DIGITS
        ):
            continue
        when = int(fields[1])
        if 0 <= now - when < MAX_AGE:
            return when
    return None


def record_pass(record: Path, key: str, now: int) -> None:
    """<key> recorded as passing at <now>, its earlier lines dropped, the last KEEP kept, in one rename.
    Best effort: a record that cannot be written is no failure."""
    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        try:
            earlier = record.read_text(encoding="utf-8").splitlines()
            kept = [line for line in earlier if line.split()[:1] != [key]]
        except (OSError, UnicodeDecodeError):
            kept = []
        lines = [*kept, f"{key} {now}"][-KEEP:]
        handle, temporary = tempfile.mkstemp(dir=record.parent, prefix="passes.")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write("".join(f"{line}\n" for line in lines))
        os.replace(temporary, record)
    except OSError:
        return


def clock(epoch: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def pytest(args: Sequence[str]) -> int:
    return subprocess.run([sys.executable, "-m", "pytest", *args], check=False).returncode


def main(argv: Sequence[str], env: Mapping[str, str], root: Path, record: Path) -> int:
    key = run_key(root, argv, env)
    now = int(time.time())
    if key is not None and env.get(NO_CACHE) != "1":
        when = passed_at(record, key, now)
        if when is not None:
            print(
                f"unit tests: these exact inputs passed at {clock(when)}, skipping ({NO_CACHE}=1 runs them)"
            )
            return 0
    code = pytest(argv)
    if code == 0 and key is not None and run_key(root, argv, env) == key:
        record_pass(record, key, int(time.time()))
    return code


if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parents[1]
    CACHE = Path(os.environ.get("UNIT_CACHE_DIR") or ROOT / "tests" / ".unit-cache")
    sys.exit(main(sys.argv[1:], os.environ, ROOT, CACHE / "passes"))
