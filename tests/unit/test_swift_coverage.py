"""tools/swift_coverage.py: the line-coverage floor of the menu bar app's core, over a recorded
`llvm-cov export -summary-only` (what `swift test --enable-code-coverage` leaves). Fictional file names."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import swift_coverage as sc


APP = "/Users/me/bosun/app"


def entry(path: str, count: int, covered: int) -> dict[str, Any]:
    percent = 100.0 * covered / count if count else 0.0
    return {
        "filename": f"{APP}/{path}",
        "summary": {"lines": {"count": count, "covered": covered, "percent": percent}},
    }


def summary(*files: dict[str, Any]) -> dict[str, Any]:
    return {"type": "llvm.coverage.json.export", "version": "2.0.1", "data": [{"files": list(files)}]}


CORE = "Sources/BosunMenuCore/"
GOOD = summary(
    entry(f"{CORE}Contract.swift", 100, 100),
    entry(f"{CORE}MenuModel.swift", 200, 190),
    entry("Tests/BosunMenuCoreTests/ContractTests.swift", 500, 500),  # not the core
    entry("Sources/BosunMenu/App.swift", 80, 0),  # the AppKit shell: no floor
)


class ReadTest(unittest.TestCase):
    def test_only_the_cores_files_are_read_with_their_names_from_the_core(self) -> None:
        files = sc.core_files(GOOD)
        self.assertEqual(
            [(f.name, f.lines, f.covered) for f in files],
            [("Contract.swift", 100, 100), ("MenuModel.swift", 200, 190)],
        )

    def test_a_summary_of_another_shape_reads_as_no_files(self) -> None:
        odds: list[dict[str, Any]] = [
            {},
            {"data": []},
            {"data": [{}]},
            {"data": [{"files": [{"filename": "x"}]}]},
        ]
        for odd in odds:
            with self.subTest(odd=odd):
                self.assertEqual(sc.core_files(odd), [])

    def test_a_folder_that_only_looks_like_the_core_is_not_it(self) -> None:
        other = summary(entry("Sources/BosunMenuCoreExtras/A.swift", 10, 0))
        self.assertEqual(sc.core_files(other), [])


class PercentTest(unittest.TestCase):
    def test_the_percentage_is_of_all_lines_not_the_mean_of_the_files(self) -> None:
        files = sc.core_files(GOOD)
        self.assertAlmostEqual(sc.percent(files), 100 * 290 / 300)

    def test_a_file_with_no_lines_counts_for_nothing_and_is_full(self) -> None:
        files = sc.core_files(summary(entry(f"{CORE}Empty.swift", 0, 0), entry(f"{CORE}A.swift", 10, 10)))
        self.assertEqual(sc.percent(files), 100.0)
        self.assertEqual(sc.below(files, 95.0), [])

    def test_no_lines_at_all_is_zero(self) -> None:
        self.assertEqual(sc.percent([]), 0.0)


class VerdictTest(unittest.TestCase):
    def test_at_the_floor_passes_and_says_the_figure(self) -> None:
        ok, message = sc.verdict(sc.core_files(GOOD), 95.0)
        self.assertTrue(ok)
        self.assertIn("96.7%", message)
        self.assertIn("floor 95%", message)

    def test_under_the_floor_fails_and_names_the_files_under_it_worst_first(self) -> None:
        files = sc.core_files(
            summary(
                entry(f"{CORE}A.swift", 100, 100),
                entry(f"{CORE}B.swift", 100, 80),
                entry(f"{CORE}C.swift", 100, 60),
            )
        )
        ok, message = sc.verdict(files, 95.0)
        self.assertFalse(ok)
        self.assertIn("80.0%", message)  # the total
        self.assertLess(message.index("C.swift"), message.index("B.swift"))
        self.assertNotIn("A.swift", message)
        self.assertIn("60 of 100 lines", message)

    def test_exactly_the_floor_passes(self) -> None:
        ok, _message = sc.verdict(sc.core_files(summary(entry(f"{CORE}A.swift", 100, 95))), 95.0)
        self.assertTrue(ok)

    def test_no_core_files_fails_and_says_to_run_the_tests_with_coverage(self) -> None:
        ok, message = sc.verdict([], 95.0)
        self.assertFalse(ok)
        self.assertIn("--enable-code-coverage", message)


class MainTest(unittest.TestCase):
    def run_main(self, data: dict[str, Any], *extra: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            path.write_text(json.dumps(data))
            out, err = io.StringIO(), io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                code = sc.main(["--summary", str(path), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_a_good_summary_exits_0_and_says_one_line(self) -> None:
        code, out, _err = self.run_main(GOOD)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_a_low_one_exits_1_and_says_why_on_stderr(self) -> None:
        code, _out, err = self.run_main(summary(entry(f"{CORE}A.swift", 100, 10)))
        self.assertEqual(code, 1)
        self.assertIn("A.swift", err)

    def test_the_floor_can_be_given(self) -> None:
        code, _out, _err = self.run_main(summary(entry(f"{CORE}A.swift", 100, 50)), "--floor", "40")
        self.assertEqual(code, 0)

    def test_a_file_that_is_not_json_is_one_line_and_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            path.write_text("not json")
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                code = sc.main(["--summary", str(path)])
        self.assertEqual(code, 2)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)


class LocateTest(unittest.TestCase):
    """Without --summary the tool asks swift where the coverage went and runs llvm-cov over it."""

    def completed(self, out: str, code: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], code, out, "")

    def test_the_profile_and_the_test_binary_come_from_where_swift_leaves_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            debug = Path(tmp) / ".build" / "arm64-apple-macosx" / "debug"
            (debug / "codecov").mkdir(parents=True)
            (debug / "codecov" / "default.profdata").write_text("")
            (debug / "codecov" / "BosunMenu.json").write_text("{}")
            binary = debug / "BosunMenuPackageTests.xctest" / "Contents" / "MacOS" / "BosunMenuPackageTests"
            binary.parent.mkdir(parents=True)
            binary.write_text("")
            found = sc.locate(debug / "codecov" / "BosunMenu.json")
        self.assertEqual(found, (debug / "codecov" / "default.profdata", binary))

    def test_no_profile_is_an_error_that_says_to_run_the_tests_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(sc.CoverageError) as caught:
            sc.locate(Path(tmp) / "codecov" / "BosunMenu.json")
        self.assertIn("--enable-code-coverage", str(caught.exception))

    def test_the_summary_is_what_llvm_cov_prints(self) -> None:
        calls: list[list[str]] = []

        def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            return self.completed(json.dumps(GOOD))

        data = sc.llvm_summary(Path("/p/default.profdata"), Path("/p/bin"), run=run)
        self.assertEqual(data, GOOD)
        self.assertEqual(calls[0][:3], ["xcrun", "llvm-cov", "export"])
        self.assertIn("-summary-only", calls[0])
        self.assertIn("-instr-profile=/p/default.profdata", calls[0])
        self.assertEqual(calls[0][-1], "/p/bin")

    def test_llvm_cov_failing_is_one_line(self) -> None:
        def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 1, "", "error: no such file\nmore")

        with self.assertRaises(sc.CoverageError) as caught:
            sc.llvm_summary(Path("/p/a"), Path("/p/b"), run=run)
        self.assertIn("no such file", str(caught.exception))
        self.assertNotIn("\n", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
