"""The commit hook's test selection (pytest-impacted, `--impacted`) as rules the tests can check:
pyproject.toml's impacted_map and impacted_invalidate_all, read here and applied as the plugin
applies them (its own matcher, pytest_impacted.strategies.matches_any_glob: globs matched from the
right, `*` within a segment, `**` across them; a mapped path counts as changed and leads on through
the other mappings). conftest.py checks with them that a change to each file a test
reads or runs selects that test; test_impacted_rules.py that every file has a rule, and that the
plugin follows the imports in this layout.

What the plugin's own import analysis adds (a changed module selects the tests that import it, among
the modules of lib/ and the helpers of tests/unit) is not in `selected_by`: `covers` adds it from
`import_closure`."""

from __future__ import annotations

import ast
import subprocess
import tomllib
from collections import deque
from functools import cache
from pathlib import Path, PurePosixPath

from pytest_impacted.strategies import matches_any_glob


ROOT = Path(__file__).resolve().parents[2]
TESTS = "tests/unit"
ALL = frozenset({"*"})  # selected_by's answer when a change runs every test


@cache
def options() -> dict[str, object]:
    """pyproject.toml's [tool.pytest.ini_options]."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_options: dict[str, object] = pyproject["tool"]["pytest"]["ini_options"]
    return pytest_options


@cache
def file_map() -> tuple[tuple[str, str], ...]:
    """impacted_map's GLOB=PATH entries."""
    entries = options().get("impacted_map", [])
    assert isinstance(entries, list)
    return tuple((glob.strip(), path.strip()) for glob, _, path in (str(e).partition("=") for e in entries))


@cache
def invalidate_all() -> tuple[str, ...]:
    globs = options().get("impacted_invalidate_all", [])
    assert isinstance(globs, list)
    return tuple(str(glob) for glob in globs)


def matches(path: str, glob: str) -> bool:
    """Whether <glob> matches <path>, as the plugin matches every pattern it reads."""
    return bool(matches_any_glob(path, [glob]))  # bool(): the plugin ships no type information


def is_test_file(path: str) -> bool:
    return path.startswith(f"{TESTS}/test_") and path.endswith(".py")


def mapped(path: str) -> list[str]:
    """<path>, then every path the mappings lead to from it, followed on."""
    seen = [path]
    queue = deque([path])
    while queue:
        changed = queue.popleft()
        for glob, target in file_map():
            if matches(changed, glob) and target not in seen:
                seen.append(target)
                queue.append(target)
    return seen


def selected_by(path: str) -> frozenset[str]:
    """The test files a change to <path> (relative to the repository) selects by the rules alone,
    or ALL. A dependency file (pyproject.toml, uv.lock) runs everything, as the plugin does."""
    paths = mapped(path)
    if any(matches(p, glob) for p in paths for glob in invalidate_all()):
        return ALL
    if any(PurePosixPath(p).name in ("pyproject.toml", "uv.lock") for p in paths):
        return ALL
    return frozenset(p for p in paths if is_test_file(p))


def covers(test: str, name: str) -> bool:
    """Whether a change to <name> selects <test>: by the rules, or (a module) by the imports."""
    selected = selected_by(name)
    return selected is ALL or test in selected or name in import_closure(test)


# Where a bare `import x` in lib/, tools/ or the tests finds x: the folders each puts on sys.path.
SEARCHED = ("lib", "tools", TESTS, "tests/llm", "tests/sandbox")


@cache
def modules() -> dict[str, str]:
    """Module name -> its file, relative to the repository; the first folder of SEARCHED wins."""
    found: dict[str, str] = {}
    for folder in SEARCHED:
        for path in sorted((ROOT / folder).glob("*.py")):
            found.setdefault(path.stem, f"{folder}/{path.name}")
    return found


@cache
def imports_of(path: str) -> frozenset[str]:
    """The files of modules() that <path> imports by name."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.partition(".")[0])
    return frozenset(modules()[name] for name in names if name in modules())


@cache
def import_closure(path: str) -> frozenset[str]:
    """The files <path> imports, directly or through the others."""
    seen: set[str] = set()
    queue = deque(imports_of(path))
    while queue:
        module = queue.popleft()
        if module not in seen:
            seen.add(module)
            queue.extend(imports_of(module))
    return frozenset(seen)


@cache
def imported_helpers() -> frozenset[str]:
    """The helpers of tests/unit that a test imports, directly or through another."""
    tests = (name for name in repository_files() if is_test_file(name))
    imported = {module for test in tests for module in import_closure(test)}
    return frozenset(name for name in imported if name.startswith(f"{TESTS}/"))


def is_followed(name: str) -> bool:
    """Whether the plugin's import analysis follows <name> to the tests that import it: a module of
    lib/ (an import root: pytest's `pythonpath`), or a helper of tests/unit that a test imports."""
    return (name.startswith("lib/") and name.endswith(".py") and "/" not in name[4:]) or (
        name in imported_helpers()
    )


@cache
def repository_files() -> frozenset[str]:
    """The files git lists here: tracked, and untracked but not ignored."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return frozenset(listed.stdout.splitlines())
