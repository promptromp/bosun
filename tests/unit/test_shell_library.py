"""A shell script read as its functions (tools/shell_library.py), what keeps it from reading as them, a diff's
hunks mapped to the functions they changed, and the calls a piece of code makes.

The rule under test is the selection's: whatever the reader cannot read or place is the whole file (None), and
a word that might be a call is one."""

from __future__ import annotations

import difflib
import sys
import textwrap
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import shell_library as sl


def read(text: str) -> sl.Script:
    return sl.read(textwrap.dedent(text))


def spans(script: sl.Script) -> list[tuple[str, int, int]]:
    return [(f.name, f.first, f.last) for f in script.functions]


class FunctionsTest(unittest.TestCase):
    def test_one_line_and_many_line_functions_with_their_lines(self) -> None:
        script = read(
            """\
            # a comment
            one() { echo one; }
            two() { # <arg>
              local x="$1"
              echo "$x"
            }
            three ()
            {
              :
            }
            """
        )
        self.assertEqual(spans(script), [("one", 2, 2), ("two", 3, 6)])
        # (a body on the next line is not shfmt's layout: it reads as top-level code, which fails closed)
        self.assertEqual([script.function_at(n) for n in (7, 8, 9, 10)], [None] * 4)

    def test_the_function_keyword_and_a_subshell_body(self) -> None:
        script = read(
            """\
            function kw {
              echo kw
            }
            sub() (
              cd /tmp && pwd
            )
            inline() ( echo in )
            """
        )
        self.assertEqual(spans(script), [("kw", 1, 3), ("sub", 4, 6), ("inline", 7, 7)])
        self.assertEqual(script.problems, ())

    def test_a_function_inside_another_is_part_of_it_and_an_indented_one_alone_is_its_own(self) -> None:
        script = read(
            """\
            outer() {
              inner() { echo in; }
              inner
            }
            if true; then
              helper() {
                echo helper
              }
            fi
            """
        )
        self.assertEqual(spans(script), [("outer", 1, 4), ("helper", 6, 8)])

    def test_braces_in_strings_here_documents_and_case_patterns_do_not_end_a_function(self) -> None:
        script = read(
            """\
            render() {
              cat <<EOF
            {
            }
            EOF
              jq '. as $x | {
            }' <<<"{}"
              case "$(ps -o command= -p "$1")" in
                *" launch "*) return 0 ;;
                *}) echo "}" ;;
              esac
              echo "${x:-}" "${#y}" "$((1 << 2))"
            }
            after() { :; }
            """
        )
        self.assertEqual(spans(script), [("render", 1, 13), ("after", 14, 14)])
        self.assertEqual(script.problems, ())

    def test_a_brace_after_esac_or_fi_closes_a_one_line_function(self) -> None:
        script = read(
            """\
            takes() { case "$1" in idle | busy) return 0 ;; *) return 1 ;; esac }
            valid() { [ "$1" = auto ] || { [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ]; }; }
            last() { if true; then :; fi }
            """
        )
        self.assertEqual(spans(script), [("takes", 1, 1), ("valid", 2, 2), ("last", 3, 3)])


class LinesTest(unittest.TestCase):
    def test_function_at_and_has_code(self) -> None:
        script = read("V=1\n\n# note\nf() {\n  :\n}\n")
        self.assertIsNone(script.function_at(1))
        self.assertEqual(script.function_at(5), script.functions[0])
        self.assertEqual([script.has_code(n) for n in (1, 2, 3, 5, 40)], [True, False, False, True, False])


class ProblemsTest(unittest.TestCase):
    def test_what_does_not_close_is_a_problem(self) -> None:
        for text, problem in (
            ("echo 'open\n", "line 1: a single quote that does not close"),
            ('echo "open\n', "line 1: a double quote that does not close"),
            ("x=$(echo\n", "line 1: a $( that does not close"),
            ("cat <<EOF\nno end\n", "line 1: a here-document (EOF) that does not end"),
            ("f() {\n  :\n", "line 1: the function f does not end where its braces balance"),
            ("f() {\n  {\n}\n", "line 1: the function f does not end where its braces balance"),
        ):
            with self.subTest(text=text):
                self.assertIn(problem, read(text).problems)

    def test_comments_are_not_code(self) -> None:
        script = read("echo 'a # b' # a comment\n# fn_in_comment\n")
        self.assertEqual(script.code, "echo 'a # b'            \n               \n")


class CallsTest(unittest.TestCase):
    NAMES = frozenset({"load_config", "orphans", "window_specs", "codex_args", "run_step"})

    def test_a_call_is_a_word_anywhere_in_the_code_strings_too(self) -> None:
        code = "load_config\nx=\"$(window_specs)\"\nbash -c 'source lib/common.sh; codex_args' _\n"
        self.assertEqual(sl.calls(code, self.NAMES), {"load_config", "window_specs", "codex_args"})

    def test_a_path_a_file_name_a_variable_or_a_longer_word_is_not_a_call(self) -> None:
        code = 'python3 "$ROOT/lib/orphans.py"\n"$BOSUN" bosun-orphans\necho "$run_step" load_configs\n'
        self.assertEqual(sl.calls(code, self.NAMES), set())


LIBRARY = """\
A=1
one() {
  echo one
}

two() {
  echo two
  echo more
}
"""


class HunksTest(unittest.TestCase):
    """A diff's hunks mapped to the functions they changed, the whole file (None) when that cannot be said."""

    def changed(
        self, new: str, *hunks: tuple[int, int, int, int], old: str = LIBRARY
    ) -> frozenset[str] | None:
        return sl.changed_functions(sl.read(new), hunks, sl.read(old))

    def test_a_git_diff_s_hunks(self) -> None:
        diff = "diff --git a/x b/x\n@@ -3 +3 @@\n-a\n+b\n@@ -7,0 +8,2 @@\n+c\n+d\n@@ -12,3 +13,0 @@\n"
        self.assertEqual(sl.parse_hunks(diff), ((3, 1, 3, 1), (7, 0, 8, 2), (12, 3, 13, 0)))

    def test_the_lines_a_diff_left_alone_paired(self) -> None:
        pairs = sl.unchanged_pairs([(3, 1, 2, 0), (5, 0, 5, 1)], 6, 6)
        self.assertEqual(pairs, [(1, 1), (2, 2), (4, 3), (5, 4), (6, 6)])

    def test_lines_changed_in_functions_name_them(self) -> None:
        new = LIBRARY.replace("echo one", "echo 1").replace("echo more", "echo much")
        self.assertEqual(self.changed(new, (3, 1, 3, 1), (8, 1, 8, 1)), {"one", "two"})

    def test_a_line_outside_every_function_is_the_whole_file_even_a_comment(self) -> None:
        for new, hunk in (
            (LIBRARY.replace("A=1", "A=2"), (1, 1, 1, 1)),
            (LIBRARY.replace("\n\ntwo", "\n# two\ntwo"), (5, 1, 5, 1)),
            (LIBRARY.replace("echo one", "echo 1"), (40, 1, 40, 1)),  # past the end
        ):
            with self.subTest(hunk=hunk):
                self.assertIsNone(self.changed(new, hunk))

    def test_a_change_is_a_change_however_small(self) -> None:
        """No comparison: a hunk is a change, spacing inside a string or a joined line too."""
        self.assertEqual(
            self.changed(LIBRARY.replace("echo two\n  echo more", "echo two;  echo more"), (7, 2, 7, 1)),
            {"two"},
        )

    def test_lines_taken_out(self) -> None:
        self.assertEqual(self.changed(LIBRARY.replace("  echo more\n", ""), (8, 1, 7, 0)), {"two"})
        self.assertIsNone(self.changed(LIBRARY.replace("A=1\n", ""), (1, 1, 0, 0)))

    def test_a_brace_moved_over_top_level_code_is_the_whole_file(self) -> None:
        """`TOP=1` no longer runs when the file is sourced: the lines it left alone moved into a function."""
        old = "bar() {\n  x\n}\nTOP=1\n"
        new = "bar() {\n  x\nTOP=1\n}\n"
        self.assertIsNone(self.changed(new, (3, 1, 2, 0), (4, 0, 4, 1), old=old))

    def test_code_moved_between_functions_names_both(self) -> None:
        old = "a() {\n  x\n  y\n}\nb() {\n  z\n}\n"
        new = "a() {\n  x\n}\nb() {\n  y\n  z\n}\n"
        self.assertEqual(self.changed(new, *diff(old, new), old=old), {"a", "b"})

    def test_a_blank_line_a_moved_brace_takes_into_a_function_changes_nothing_more(self) -> None:
        old = "a() {\n  x\n}\n\nb=1\n"
        new = "a() {\n  x\n\n}\nb=1\n"
        self.assertEqual(self.changed(new, (3, 1, 2, 0), (4, 0, 4, 1), old=old), {"a"})  # the `}` out, and in

    def test_a_side_that_does_not_read_is_the_whole_file(self) -> None:
        broken = LIBRARY.replace("echo one", "echo 'one")
        self.assertIsNone(self.changed(broken, (3, 1, 3, 1)))
        self.assertIsNone(self.changed(LIBRARY, (3, 1, 3, 1), old=broken))


def diff(old: str, new: str) -> tuple[tuple[int, int, int, int], ...]:
    """The hunks `git diff -U0` gives from <old> to <new>."""
    found = []
    matcher = difflib.SequenceMatcher(a=old.split("\n"), b=new.split("\n"), autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            found.append((i1 + (i2 > i1), i2 - i1, j1 + (j2 > j1), j2 - j1))
    return tuple(found)


if __name__ == "__main__":
    unittest.main()
