"""The line-coverage floor of the menu bar app's core (docs/plans/menu-bar-app.md, Global Constraints).

`swift test --enable-code-coverage` (run in app/) leaves a profile and the test binaries (one per test
target); this reads `llvm-cov export -summary-only` over them, keeps the files of Sources/BosunMenuCore
(the core is pure; the AppKit target has no floor) and fails under 95% of their lines. The message names
the files under the floor, worst first.

    uv run python tools/swift_coverage.py [--floor 95] [--summary <llvm-cov summary json>]

Exit 0 at or over the floor, 1 under it, 2 when the coverage cannot be read (no run with coverage, no
llvm-cov).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
CORE = "/Sources/BosunMenuCore/"
FLOOR = 95.0
PROFILE = "default.profdata"
RunCommand = Callable[..., "subprocess.CompletedProcess[str]"]


class CoverageError(Exception):
    """The coverage cannot be read, in the one line it is shown as."""


@dataclass(frozen=True)
class FileCoverage:
    name: str  # relative to Sources/BosunMenuCore
    lines: int
    covered: int

    @property
    def percent(self) -> float:
        return 100.0 if self.lines == 0 else 100.0 * self.covered / self.lines


def core_files(summary: Mapping[str, Any]) -> list[FileCoverage]:
    """The core's files in an `llvm-cov export -summary-only` object (another shape holds none)."""
    files: list[FileCoverage] = []
    for block in summary.get("data") or []:
        for item in block.get("files") or []:
            name = str(item.get("filename", ""))
            lines = (item.get("summary") or {}).get("lines")
            if CORE not in name or not isinstance(lines, dict):
                continue
            files.append(FileCoverage(name.split(CORE, 1)[1], int(lines["count"]), int(lines["covered"])))
    return sorted(files, key=lambda f: f.name)


def percent(files: Sequence[FileCoverage]) -> float:
    """Covered lines over all lines (not the mean of the files'); 0 with no lines at all."""
    total = sum(f.lines for f in files)
    return 100.0 * sum(f.covered for f in files) / total if total else 0.0


def below(files: Sequence[FileCoverage], floor: float) -> list[FileCoverage]:
    """The files under <floor>, worst first."""
    return sorted((f for f in files if f.percent < floor), key=lambda f: (f.percent, f.name))


def verdict(files: Sequence[FileCoverage], floor: float) -> tuple[bool, str]:
    """Whether the core's coverage holds the floor, and the line that says so."""
    if not files:
        return False, (
            "no coverage for Sources/BosunMenuCore: run `swift test --enable-code-coverage` in app/ first"
        )
    total = percent(files)
    if total >= floor:
        return True, f"BosunMenuCore line coverage {total:.1f}% (floor {floor:g}%)"
    under = "; ".join(
        f"{f.name} {f.percent:.1f}% ({f.covered} of {f.lines} lines)" for f in below(files, floor)
    )
    return (
        False,
        f"BosunMenuCore line coverage {total:.1f}% is under the floor of {floor:g}%. Under it: {under}",
    )


# ---- where swift leaves it ----------------------------------------------------------------------


def codecov_json(app: Path, run: RunCommand = subprocess.run) -> Path:
    """The file `swift test --show-codecov-path` names; the profile and the test binaries are beside it."""
    done = run(["swift", "test", "--show-codecov-path"], cwd=app, capture_output=True, text=True, check=False)
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        raise CoverageError("swift could not say where the coverage is: " + one_line(done.stderr))
    return Path(lines[-1])


def locate(codecov: Path) -> tuple[Path, list[Path]]:
    """(the profile, every test binary) that `swift test --enable-code-coverage` left beside <codecov>."""
    profile = codecov.parent / PROFILE
    if not profile.is_file():
        raise CoverageError(
            f"no coverage profile at {profile}: run `swift test --enable-code-coverage` in app/"
        )
    products = codecov.parent.parent
    binaries = sorted(products.glob("*.xctest/Contents/MacOS/*"))
    if not binaries:
        raise CoverageError(
            f"no test binary under {products}: run `swift test --enable-code-coverage` in app/"
        )
    return profile, binaries


def one_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else "no message"


def llvm_summary(profile: Path, binaries: Sequence[Path], run: RunCommand = subprocess.run) -> dict[str, Any]:
    """`llvm-cov export -summary-only` over every test binary: the first is the object, the rest `-object`."""
    objects = [str(binaries[0]), *(arg for binary in binaries[1:] for arg in ("-object", str(binary)))]
    argv = ["xcrun", "llvm-cov", "export", "-summary-only", f"-instr-profile={profile}", *objects]
    done = run(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise CoverageError("llvm-cov failed: " + one_line(done.stderr))
    try:
        data = json.loads(done.stdout)
    except ValueError as error:
        raise CoverageError(f"llvm-cov did not print JSON: {error}") from error
    return data if isinstance(data, dict) else {}


def read_summary(path: Path | None, app: Path = APP) -> dict[str, Any]:
    if path is not None:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise CoverageError(f"{path} cannot be read as an llvm-cov summary: {error}") from error
        return data if isinstance(data, dict) else {}
    profile, binaries = locate(codecov_json(app))
    return llvm_summary(profile, binaries)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="swift_coverage.py", allow_abbrev=False, description=__doc__)
    parser.add_argument("--floor", type=float, default=FLOOR, help="the percentage of lines (default 95)")
    parser.add_argument(
        "--summary", type=Path, help="an llvm-cov export -summary-only file, instead of swift's own"
    )
    args = parser.parse_args(list(argv))
    try:
        files = core_files(read_summary(args.summary))
    except (CoverageError, FileNotFoundError) as error:
        print(f"swift_coverage: {error}", file=sys.stderr)
        return 2
    ok, message = verdict(files, args.floor)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
