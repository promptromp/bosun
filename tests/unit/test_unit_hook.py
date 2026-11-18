"""The commit hook's unit tests (tools/unit_hook.py): the key of a run and the pass record that skips a run
whose key passed before. Checked in a scratch git repository, with pytest stood in for, so nothing here runs
the suite. The rule under test is that the record can only skip a run whose inputs are the recorded pass's:
any change to the files, the index, HEAD, the Python, the lock, the rules, the arguments or the environment
that can change a test makes another key, and anything odd runs pytest.

Also the hooks' order that goes with it (.pre-commit-config.yaml): the tests last, fail_fast on, and CI's
`lint` job turning it off again so that it reports every finding."""

from __future__ import annotations

import os
import re
import sys
import time
import unittest
from collections.abc import Sequence
from unittest import mock

from test_smoke_cache import ROOT, Scratch, clean_env, git


sys.path.insert(0, str(ROOT / "tools"))
import unit_hook as uh


ARGS = ("-q", "-n", "auto", "--impacted", "--impacted-git-mode=unstaged")


class Checkout(Scratch):
    """A scratch repository with one commit, a lock, the rules, a test and an ignored folder."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.dir / "checkout"
        self.root.mkdir()
        self.write("uv.lock", "lock 1\n")
        self.write("pyproject.toml", "[tool.pytest]\n")
        self.write("lib/a.py", "A = 1\n")
        self.write("tests/unit/test_a.py", "def test_a(): pass\n")
        self.write(".gitignore", "ignored/\n")
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self.env = clean_env()
        self.record = self.dir / "cache" / "passes"

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def key(self, args: Sequence[str] = ARGS, **env: str) -> str | None:
        return uh.run_key(self.root, args, {**self.env, **env})


class KeyTest(Checkout):
    def test_the_same_inputs_give_the_same_key(self) -> None:
        first = self.key()
        assert first is not None
        self.assertTrue(uh.is_key(first))
        self.assertEqual(self.key(), first)

    def test_a_change_to_the_files_on_disk_changes_it(self) -> None:
        before = self.key()
        self.write("lib/a.py", "A = 2\n")  # unstaged
        self.assertNotEqual(self.key(), before)

    def test_a_same_size_edit_in_the_second_the_index_was_written_changes_it(self) -> None:
        """git trusts an entry's stat only when the entry is older than the index file; one from the index's
        own second is racy, and git compares its content. The key's copy of the index keeps the index's time,
        or an edit of the same size in that second keeps the key: what the test above hit now and then, when
        its commit, first key and edit fell in one second and the copy was made in the next. Made certain
        here: git comparing only whole seconds and sizes, and the file, the entry and the index all of one
        second in the past (the index set back after each write of it, as one written in that second)."""
        git(self.root, "config", "core.checkStat", "minimal")
        git(self.root, "config", "core.trustctime", "false")
        path, then = self.root / "lib" / "a.py", int(time.time()) - 100
        index = uh.index_file(self.root, self.env)
        assert index is not None
        os.utime(path, (then, then))
        # The entry's stat in the index: that second, and the size.
        git(self.root, "commit", "-q", "-a", "--allow-empty", "-m", "the time in the past")
        os.utime(index, (then, then))
        before = self.key()  # its `git write-tree` may write the index again
        self.write("lib/a.py", "A = 2\n")  # the same size
        os.utime(path, (then, then))
        os.utime(index, (then, then))
        self.assertNotEqual(self.key(), before)

    def test_an_untracked_file_changes_it_and_an_ignored_one_does_not(self) -> None:
        before = self.key()
        self.write("ignored/cache.bin", "x\n")
        self.assertEqual(self.key(), before)
        self.write("tests/unit/test_new.py", "def test_new(): pass\n")
        self.assertNotEqual(self.key(), before)

    def test_staging_what_is_on_disk_changes_it(self) -> None:
        """The same files on disk, another index: the selection counts staged and unstaged changes both,
        and the tests list what is tracked."""
        self.write("lib/b.py", "B = 1\n")
        untracked = self.key()
        git(self.root, "add", "lib/b.py")
        self.assertNotEqual(self.key(), untracked)

    def test_a_new_head_changes_it(self) -> None:
        self.write("lib/a.py", "A = 2\n")
        git(self.root, "add", "-A")
        staged = self.key()
        git(self.root, "commit", "-q", "-m", "two")
        self.assertNotEqual(self.key(), staged)  # the same tree and index, compared against another HEAD

    def test_the_lock_and_the_rules_change_it(self) -> None:
        before = self.key()
        for name in uh.INPUT_FILES:
            with self.subTest(name):
                original = (self.root / name).read_text()
                self.write(name, original + "# changed\n")
                self.assertNotEqual(self.key(), before)
                self.write(name, original)
        self.assertEqual(self.key(), before)

    def test_another_python_changes_it(self) -> None:
        before = self.key()
        with mock.patch.object(sys, "version", "3.99.0 (another build)"):
            self.assertNotEqual(self.key(), before)

    def test_other_arguments_change_it(self) -> None:
        self.assertNotEqual(self.key(ARGS), self.key((*ARGS, "-x")))

    def test_the_variables_that_can_change_a_test_change_it_and_others_do_not(self) -> None:
        before = self.key()
        for name in ("BOSUN_STATE_DIR", "PYTEST_ADDOPTS", "PYTHONPATH", "PATH"):
            with self.subTest(name):
                self.assertNotEqual(self.key(**{name: "/elsewhere"}), before)
        self.assertEqual(self.key(TERM_PROGRAM="other", BOSUN_UNIT_NO_CACHE="1"), before)

    def test_path_counts_as_the_tests_search_it(self) -> None:
        """The hook's run and one by hand: git puts its own folder first for a hook, and `uv run pre-commit`
        then the hook's `uv run` name the virtualenv's folder twice."""
        exec_path = uh.git(self.root, "--exec-path")
        assert exec_path is not None
        path = self.env.get("PATH", "")
        first = path.split(":")[0]
        self.assertEqual(self.key(PATH=f"{exec_path}:{path}"), self.key(PATH=path))
        self.assertEqual(self.key(PATH=f"{first}:{path}"), self.key(PATH=path))
        self.assertNotEqual(self.key(PATH=f"{path}:/elsewhere"), self.key(PATH=path))
        self.assertEqual(uh.search_path("/a:/git:/b:/a", "/git"), "/a:/b")
        self.assertEqual(uh.search_path("/b:/a", None), "/b:/a")  # the order is kept: it is what finds a name

    def test_it_reads_the_index_the_commit_names_and_leaves_the_real_one_alone(self) -> None:
        index = self.root / ".git" / "index"
        before = index.read_bytes()
        self.write("lib/b.py", "B = 1\n")
        plain = self.key()
        self.assertEqual(index.read_bytes(), before)
        self.assertIn("?? lib/b.py", git(self.root, "status", "--porcelain"))  # still untracked
        partial = self.dir / "partial-index"
        partial.write_bytes(before)
        git_env = {**self.env, "GIT_INDEX_FILE": str(partial)}
        uh.git(self.root, "add", "lib/b.py", env=git_env)  # a partial commit's own index holds it
        self.assertNotEqual(self.key(GIT_INDEX_FILE=str(partial)), plain)
        self.assertEqual(index.read_bytes(), before)

    def test_outside_a_repository_there_is_no_key(self) -> None:
        outside = self.dir / "plain"
        outside.mkdir()
        self.assertIsNone(uh.run_key(outside, ARGS, self.env))


class HookTest(Checkout):
    def run_hook(self, code: int = 0, **env: str) -> tuple[int, list[str]]:
        """main() with pytest stood in for (it exits <code>); its exit and what it printed."""
        printed: list[str] = []
        with (
            mock.patch.object(uh, "pytest", return_value=code) as pytest,
            mock.patch("builtins.print", side_effect=lambda *a, **_: printed.append(" ".join(map(str, a)))),
        ):
            exit_code = uh.main(ARGS, {**self.env, **env}, self.root, self.record)
        self.pytest_calls = pytest.call_count
        return exit_code, printed

    def test_a_pass_is_recorded_and_the_same_inputs_skip_with_one_line(self) -> None:
        self.assertEqual(self.run_hook(), (0, []))
        self.assertEqual(self.pytest_calls, 1)
        self.assertRegex(self.record.read_text(), r"^[0-9a-f]{64} \d+\n$")
        code, printed = self.run_hook()
        self.assertEqual((code, self.pytest_calls), (0, 0))
        self.assertEqual(len(printed), 1)
        self.assertIn("these exact inputs passed at", printed[0])
        self.assertIn("BOSUN_UNIT_NO_CACHE=1", printed[0])

    def test_a_failure_is_not_recorded_and_its_code_is_the_hook_s(self) -> None:
        self.assertEqual(self.run_hook(code=1)[0], 1)
        self.assertFalse(self.record.exists())
        self.assertEqual(self.run_hook(code=1)[0], 1)
        self.assertEqual(self.pytest_calls, 1)

    def test_any_change_runs_again(self) -> None:
        self.run_hook()
        self.write("lib/a.py", "A = 3\n")
        self.run_hook()
        self.assertEqual(self.pytest_calls, 1)

    def test_no_cache_runs_and_still_records(self) -> None:
        self.run_hook()
        self.record.write_text("")
        self.run_hook(BOSUN_UNIT_NO_CACHE="1")
        self.assertEqual(self.pytest_calls, 1)
        self.run_hook(BOSUN_UNIT_NO_CACHE="1")
        self.assertEqual(self.pytest_calls, 1)  # it ignores the record it just wrote
        self.run_hook()
        self.assertEqual(self.pytest_calls, 0)

    def test_a_run_that_changed_the_checkout_is_not_recorded(self) -> None:
        def leaves_a_file(_: Sequence[str]) -> int:
            self.write("left-behind.txt", "x\n")
            return 0

        with mock.patch.object(uh, "pytest", side_effect=leaves_a_file):
            self.assertEqual(uh.main(ARGS, self.env, self.root, self.record), 0)
        self.assertFalse(self.record.exists())

    def test_a_pass_a_day_old_or_in_the_future_runs_again(self) -> None:
        key = self.key()
        assert key is not None
        now = int(time.time())
        for when in (now - uh.MAX_AGE, now - uh.MAX_AGE - 5, now + 600):
            with self.subTest(when=when):
                self.record.parent.mkdir(parents=True, exist_ok=True)
                self.record.write_text(f"{key} {when}\n")
                self.run_hook()
                self.assertEqual(self.pytest_calls, 1)
        self.record.write_text(f"{key} {now - 60}\n")
        self.run_hook()
        self.assertEqual(self.pytest_calls, 0)

    def test_an_odd_or_unreadable_record_runs(self) -> None:
        key = self.key()
        assert key is not None
        now = int(time.time())
        self.record.parent.mkdir(parents=True, exist_ok=True)
        for text in (f"{key}\n", f"{key} soon\n", f"{key} {now} extra\n", f"{key[:-1]} {now}\n", "\xff\n"):
            with self.subTest(text=text):
                self.record.write_text(text)
                self.run_hook(code=1)  # a failure: nothing recorded over the odd line
                self.assertEqual(self.pytest_calls, 1)
        self.record.unlink()
        self.record.mkdir()  # a folder where the file should be: unreadable, and unwritable
        self.assertEqual(self.run_hook()[0], 0)
        self.assertEqual(self.pytest_calls, 1)

    def test_outside_a_repository_it_runs_and_records_nothing(self) -> None:
        outside = self.dir / "plain"
        outside.mkdir()
        with mock.patch.object(uh, "pytest", return_value=0) as pytest:
            self.assertEqual(uh.main(ARGS, self.env, outside, self.record), 0)
            self.assertEqual(uh.main(ARGS, self.env, outside, self.record), 0)
        self.assertEqual(pytest.call_count, 2)
        self.assertFalse(self.record.exists())

    def test_the_record_keeps_its_last_lines(self) -> None:
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text("".join(f"{i:064x} 1\n" for i in range(uh.KEEP + 20)))
        self.run_hook()
        lines = self.record.read_text().splitlines()
        self.assertEqual(len(lines), uh.KEEP)
        self.assertEqual(lines[-1].split()[0], self.key())


class HookOrderTest(unittest.TestCase):
    """.pre-commit-config.yaml and CI's `lint` job, read as text (plain python3 has no YAML)."""

    config = (ROOT / ".pre-commit-config.yaml").read_text()

    def hook_ids(self) -> list[str]:
        return re.findall(r"^      - id: (\S+)$", self.config, re.MULTILINE)

    def test_a_failing_hook_stops_the_run_before_the_tests(self) -> None:
        self.assertRegex(self.config, r"(?m)^fail_fast: true$")
        ids = self.hook_ids()
        self.assertEqual(ids[-2:], ["unit-tests", "smoke-tests"])  # the commit's last, and the push's

    def test_the_hook_runs_the_unit_tests_through_the_record(self) -> None:
        entry = re.search(r"- id: unit-tests\n(?:        .*\n)*?        entry: (.*)\n", self.config)
        assert entry is not None
        self.assertEqual(entry.group(1), f"uv run python tools/unit_hook.py {' '.join(ARGS)}")

    def test_ci_runs_every_hook_to_the_end(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("sed 's/^fail_fast: true$/fail_fast: false/' .pre-commit-config.yaml", workflow)
        self.assertIn('uv run pre-commit run --config "$config" --all-files', workflow)
        self.assertNotRegex(workflow, r"pre-commit run --all-files")  # not the stopping config


if __name__ == "__main__":
    unittest.main()
