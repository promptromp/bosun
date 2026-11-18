"""A shell script read function by function (tools/shell_library.py): its functions and statements, what keeps
a script from reading as them, the calls a piece of code makes and the ones by a name it builds.

The rule under test is the selection's: the reader can only find more (a word that might be a call is one),
and whatever it cannot read is a problem its caller fails closed on."""

from __future__ import annotations

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
        self.assertEqual(
            [(st.first, st.assigns) for st in script.statements], [(7, ()), (8, ()), (9, ()), (10, ())]
        )

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


class StatementsTest(unittest.TestCase):
    def test_assignments_name_what_they_assign(self) -> None:
        script = read(
            """\
            ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
            A="" B="" C=1 # several
            LIST="one
              two"
            ARR=(a
              b)
            export PATH="$HOME/bin:$PATH"
            X=1; Y=2
            """
        )
        found = [(s.first, s.last, s.assigns, s.exported) for s in script.statements]
        self.assertEqual(
            found,
            [
                (1, 1, ("ROOT",), False),
                (2, 2, ("A", "B", "C"), False),
                (3, 4, ("LIST",), False),
                (5, 6, ("ARR",), False),
                (7, 7, ("PATH",), True),
                (8, 8, ("X", "Y"), False),
            ],
        )

    def test_any_other_code_is_a_statement_that_assigns_nothing(self) -> None:
        script = read(
            """\
            case "$x" in
              a) V=1 ;;
            esac
            source "$ROOT/lib/agents.sh"
            V=1 some_command
            unset V
            export V
            make |
              tee log
            """
        )
        found = [(s.first, s.last, s.assigns) for s in script.statements]
        self.assertEqual(found, [(1, 3, ()), (4, 4, ()), (5, 5, ()), (6, 6, ()), (7, 7, ()), (8, 9, ())])
        self.assertTrue(script.statements[4].exported)

    def test_at_says_which_function_or_statement_a_line_is_in(self) -> None:
        script = read("V=1\n\n# note\nf() {\n  :\n}\n")
        self.assertEqual(script.at(1), script.statements[0])
        self.assertIsNone(script.at(2))
        self.assertIsNone(script.at(3))
        self.assertEqual(script.at(5), script.functions[0])


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

    def test_mentions_is_a_variable_s_use(self) -> None:
        self.assertTrue(sl.mentions('echo "${NAME:-x}"', "NAME"))
        self.assertTrue(sl.mentions("for v in NAME OTHER", "NAME"))
        self.assertFalse(sl.mentions("echo $NAMES", "NAME"))


class ComputedTest(unittest.TestCase):
    NAMES = frozenset({"codex_args", "run_step", "load_config"})

    def computed(self, text: str) -> list[str]:
        script = read(text)
        return script.computed(range(1, len(script.lines) + 1), self.NAMES)

    def test_eval_a_variable_and_a_command_s_output_are_names_no_reading_can_tell(self) -> None:
        self.assertEqual(self.computed('eval "$x"\n'), ["line 1: eval"])
        self.assertEqual(self.computed('f() { local fn="$(pick)"; "$fn" a; }\n'), ['line 1: "$fn"'])
        self.assertEqual(self.computed('"$(pick)" --version\n'), ['line 1: "$(pick)"'])
        self.assertEqual(self.computed("x=1 $cmd\n"), ["line 1: $cmd"])

    def test_a_name_glued_from_an_expansion_that_could_spell_a_function(self) -> None:
        self.assertEqual(self.computed('wrap "${agent}_args"\n'), ['line 1: "${agent}_args"'])
        self.assertEqual(self.computed('echo "run_$mode"\n'), ['line 1: "run_$mode"'])
        self.assertEqual(self.computed('sleep "${n}s"; echo "v$version"\n'), [])  # (no function's name)

    def test_names_the_caller_wrote_programs_and_paths_are_not_computed(self) -> None:
        for text in (
            '"$@"\n',
            '"$1" x\n',
            '"${@:2}"\n',
            '"$BOSUN" ledger\n',
            '"${BOSUN_CMD}" launch\n',
            '"${ARGS[@]}"\n',
            '"$ROOT/bin/bosun" x\n',
            'done <<<"$table"\n',
            "done < <(list)\n",
            'echo x >"$file"\n',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.computed(text), [])

    def test_a_variable_that_only_holds_what_is_written_out_is_no_computed_name(self) -> None:
        self.assertEqual(self.computed('for sh in /bin/bash bash; do "$sh" -c :; done\n'), [])
        self.assertEqual(self.computed('f() { local w; w=(sudo pmset x); "${w[@]}"; }\n'), [])
        self.assertEqual(self.computed('f() { local fn="$1"; "$fn"; }\n'), [])
        self.assertEqual(
            self.computed('f() { local w=(sudo); w=("$(pick)"); "${w[@]}"; }\n'), ['line 1: "${w[@]}"']
        )

    def test_the_lines_asked_about_only(self) -> None:
        script = read('eval x\n"$fn"\n')
        self.assertEqual(script.computed([2], self.NAMES), ['line 2: "$fn"'])


if __name__ == "__main__":
    unittest.main()
