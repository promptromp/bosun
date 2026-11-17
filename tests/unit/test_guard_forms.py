"""The review's round-2 probes of the guard (scratchpad rv2: cmds1-5, sh/c*.sh), each with what it must get:
a form that hides a command is denied or asks; an ordinary command gets no decision; and what bash runs
is what the parse reads (the sh probes, which bash ran to show what it reaches)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import guard


def decision(command: str) -> str | None:
    said = guard.decide({"tool_name": "Bash", "tool_input": {"command": command}})
    return said["permissionDecision"] if said else None


# Each must get one of: deny, ask; or pass where DANGEROUS_PASSES says why it may.
DANGEROUS: tuple[tuple[str, str | None], ...] = (
    ("bash -c \"$(cat <<'X'\ngit push -f\nX\n)\"", "ask"),
    ("bash -c 'x=$(cat <<E\ngit push -f\nE\n); echo'", "ask"),
    ("gi\\\nt push -f", "deny"),
    ("git pu\\\nsh -f", "deny"),
    ("g\\\nit push origin main", "ask"),
    ("IFS=,; x=git,push,-f; $x", "ask"),
    ("IFS=,; cmd=git,push; $cmd", "ask"),
    ("{git,} push -f", "ask"),
    ("{git,x} push -f", "ask"),
    ("git${x} push -f", "ask"),
    ("git${x} push origin main", "ask"),
    ("echo \\$(git push -f)", "deny"),
    ("echo \\$(git push origin main)", None),
    ('a=(git push -f); "${a[@]}"', "ask"),
    ('a=(git push origin main); "${a[@]}"', "ask"),
    ("exec -a name git push -f", "deny"),
    ("exec -a name git push origin main", "ask"),
    ("nohup -- git push -f", "deny"),
    ("nohup -- git push origin main", "ask"),
    ("time git push -f", "deny"),
    ("time -p git push -f", "deny"),
    ("time -p git push origin main", "ask"),
    ("! git push -f", "deny"),
    ("! git push origin main", "ask"),
    ("[[ -n x ]] && git push -f", "deny"),
    ("[[ -n x ]] && git push origin main", None),
    ("[ -n x ] && git push origin main", None),
    ("git push origin main", None),
    ("gh pr merge 3", None),
    ("gh -R acme/x pr merge 3 --admin", "deny"),
    ("gh --repo=acme/x pr merge 3", "ask"),
    ("gh --repo=acme/x pr merge 3 --admin", "deny"),
    ("gh -Racme/x pr merge 3 --admin", "deny"),
    ("gh -Racme/x pr merge 3", "ask"),
    ("git --git-dir=/x push -f", "deny"),
    ("git --git-dir=/x push origin main", "ask"),
    ("git -C/x push origin main", "ask"),
    ("git -C/x push -f", "deny"),
    ("git -c x=y -C /x push origin HEAD:main", "ask"),
    ("/usr/bin/env git push -f", "deny"),
    ("/usr/bin/env -i git push origin main", "ask"),
    ("/usr/bin/timeout 5 git push origin main", "ask"),
    ("/usr/bin/nohup git push origin main", "ask"),
    ("/usr/bin/xargs git push origin main", "ask"),
    ("echo main | xargs -I{} git push origin {}", "ask"),
    ("echo -f | xargs git push origin", "ask"),
    ("env -S 'git push -f'", "deny"),
    ("env -S'git push -f'", "deny"),
    ("env --split-string='git push -f'", "deny"),
    ("env -S 'git push' origin main", "ask"),
    ("env -iS 'git push -f'", "deny"),
    ("env -u X -S 'git push -f'", "deny"),
    ("script -q /dev/null git push origin main", "ask"),
    ("script -q -c 'git push -f' /dev/null", "deny"),
    ("script -c 'git push origin main' /dev/null", "ask"),
    ("arch -arm64 git push origin main", "ask"),
    ("arch -x86_64 git push -f", "deny"),
    ("coproc git push -f", "deny"),
    ("coproc git push origin main", "ask"),
    ("coproc NAME { git push -f; }", "deny"),
    ("coproc NAME { git push origin main; }", "ask"),
    ("csh -c 'git push -f'", "deny"),
    ("fish -c 'git push origin main'", "ask"),
    ("tcsh -fc 'git push origin main'", "ask"),
    ("python3 -c 'import os; os.system(\"git push -f\")'", "ask"),
    ("python3.13 -c 'import os; os.system(\"git push origin main\")'", "ask"),
    ("/usr/bin/python3 -c \"import subprocess; subprocess.run(['git','push','-f'])\"", "ask"),
    ("python -c \"import os; os.system('g'+'it push -f')\"", "ask"),
    ("perl -e 'system(\"git push -f\")'", "ask"),
    ('node -e \'require("child_process").execSync("git push -f")\'', "ask"),
    ("osascript -e 'do shell script \"git push -f\"'", "ask"),
    ("ruby -e '`git push -f`'", "ask"),
    ("python3 -Ic 'import os; os.system(\"git push -f\")'", "ask"),
    ("python3 -u -c 'import os; os.system(\"git push -f\")'", "ask"),
    ("perl -we 'system(\"git push -f\")'", "ask"),
    ('node --eval=\'require("child_process").execSync("git push -f")\'', "ask"),
    ("git push --mirror", "deny"),
    ("git push origin --mirror", "deny"),
    ("git push --force-with-lease", "deny"),
    ("git push origin +main", "deny"),
    ("git push $'-f'", "ask"),
    ("git push origin $'-f'", "ask"),
    ("git push $'origin' main", "ask"),
    ("git push origin --{force,}", "ask"),
    ("git push origin -{f,}", "deny"),
    ("/usr/bin/g[i]t push origin main", "ask"),
    ("/usr/bin/g?t push -f", "ask"),
    ("/usr/*/git push -f", "deny"),
    ("$'git' push -f", "ask"),
    ("$'git' push origin main", "ask"),
    ("\\git push origin main", "ask"),
    ('"git" push origin main', "ask"),
    ("'git' push origin main", "ask"),
    ('g"it" push origin main', "ask"),
    ("g''it push origin main", "ask"),
    ("gh pr merge 3 --admin=true", "deny"),
    ("gh pr merge --admin=1 3", "deny"),
    ('"gh" pr merge 3 --admin', "deny"),
    ("gh pr merge 3 --ad\\min", "deny"),
    ('gh pr merge 3 "--admin"', "deny"),
    ('git push "-f"', "deny"),
    ('git push -"f"', "deny"),
    ('git push origin "+main"', "deny"),
    ("git push origin '+'main", "deny"),
    ("echo a\\\n#;git push -f", "deny"),
    ("echo a\\\n#;git push origin main", "ask"),
    ("echo $(true)#;git push -f", "deny"),
    ("echo $(true)#;gh pr merge 3 --admin", "deny"),
    ("echo $((1))#;git push -f", "deny"),
    ("echo $[1<<2]\ngit push -f\n2]", "deny"),
    ("(( ((1)) << 1 ))\ngit push -f\n1", "deny"),
    ("echo ${x:-a<<b}\ngit push -f\nb}", "deny"),
    ("echo ${x:-a<<b}\ngh pr merge 3 --admin\nb}", "deny"),
    ("git${IFS}push${IFS}-f", "ask"),
    ("time -p gh pr merge 3 --admin", "deny"),
    ("time -- git push -f", "deny"),
    ("env -vS 'gh pr merge 3 --admin'", "deny"),
    ("/usr/bin/time git push -f", "deny"),
    ("/usr/bin/time gh pr merge 3 --admin", "deny"),
    ("genv git push -f", "deny"),
    ("doas git push -f", "deny"),
    ("uv run git push -f", "deny"),
    ("setsid git push -f", "deny"),
    ("flock /tmp/l git push -f", "deny"),
    ("caffeinate -i git push -f", "deny"),
    ("sudo --user bob git push -f", "deny"),
    ("xargs --max-args 1 git push -f", "deny"),
    ("python3 - <<'PY'\nimport os; os.system(\"git push -f\")\nPY", "ask"),
    ("git pu\\\nsh origin main", "ask"),
    ("git \\\n  push origin main", "ask"),
    ("git push \\\n  origin main", None),
    ("cat <(git push origin main)", "ask"),
    ('echo "$(git push -f)"', "deny"),
    ("bash -c 'cat <<EOF\n$(git push -f)\nEOF'", "deny"),
    ("bash -c \"cat <<'EOF'\n\\$(git push -f)\nEOF\"", None),
    ("cat <<EOF\n$(git push -f)\nEOF", "deny"),
    ("cat <<'EOF'\n$(git push -f)\nEOF", None),
    ('cat <<"E"OF\n$(git push -f)\nEOF', None),
    ("cat <<EOF-\n$(git push -f)\nEOF-", "deny"),
    ("cat <<EOF\nnever closes", "ask"),
    ("cat <<<'x'; git push -f", "deny"),
    ("echo $((1<<2)); git push -f", "deny"),
    ("echo $( echo ')' ; git push -f )", "deny"),
    ('echo $( echo ")" ; git push -f )', "deny"),
    ("echo $( echo \\) ; git push -f )", "deny"),
    ("echo $( case x in x) git push -f;; esac )", "deny"),
    ("echo $(echo 'never closes", "ask"),
    ("echo `echo \\`git push -f\\``", "deny"),
    ('echo "$(echo "$(git push -f)")"', "deny"),
    ("eval 'git push -f'", "deny"),
    ('eval "$(echo git push -f)"', "ask"),
    ("x='git push -f'; eval \"$x\"", "ask"),
    ('bash -c \'bash -c "bash -c \\"git push -f\\""\'', "deny"),
    (
        'bash -c \'bash -c "bash -c \\"bash -c \\\\\\"bash -c \\\\\\\\\\\\\\"bash -c git\\\\\\\\\\\\\\\\ push\\\\\\\\\\\\\\\\ -f\\\\\\\\\\\\\\"\\\\\\"\\""\'',  # noqa: E501 (the probe as the review wrote it)
        "ask",
    ),
    ("2>/dev/null git push -f", "deny"),
    ("2>&1 git push -f", "deny"),
    (">&2 git push -f", "deny"),
    ("2>&1 git push origin main", "ask"),
    (">|out git push -f", "deny"),
    ("&>log git push -f", "deny"),
    ("<in git push -f", "deny"),
    ("2> /dev/null git push -f", "deny"),
    ("1>&2 gh pr merge 3 --admin", "deny"),
)
ORDINARY: tuple[tuple[str, str | None], ...] = (
    ("bosun ledger list --proposals", None),
    ("gh pr view 12 --repo acme/exporter --json mergeStateStatus,headRefOid", None),
    ("gh -R acme/exporter pr view 12", None),
    ("gh -R acme/exporter pr list --author @me", None),
    ("git -C ~/src/exporter log --oneline -5", None),
    ("jq -r '.items[] | select(.title | test(\"#[0-9]+\")) | .id' ledger.json", None),
    ("grep -n '#' README.md | head", None),
    ('echo "a)b" && ls', None),
    ('for f in *.md; do wc -l "$f"; done', None),
    ("ls docs/*.md | wc -l", None),
    ("[ -f x ] && cat x || echo missing", None),
    (
        "bosun ledger propose 'mypr:acme/x#7' --send slack --channel C000 --text - <<'EOF'\nIt's ready (finally). Use $(date) literally; don't run `this`.\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        'bosun kg propose repo/x caveat --value "can\'t deploy on fridays" --from-user --quote "can\'t deploy on fridays"',  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("python3 -c \"import json,sys; d=json.load(sys.stdin); print(d['html_url'])\" < pr.json", None),
    ("python3 -c 'print(\"see https://example.com/docs\")'", "ask"),
    ("gh api repos/acme/x/pulls/7/comments --jq '.[].body'", None),
    ("gh pr comment 7 --body $'Thanks!\\nMerged.'", "ask"),
    ("cat > /tmp/notes.txt <<EOF\ntotal: $((3 << 1))\nEOF", None),
    ("echo $(( (2 + 3) * 4 ))", None),
    ("x=$(git rev-parse HEAD) # current head", None),
    ("git log --format='%h %s' -3 # what's new", None),
    ("gh pr view 7 --json body --jq .body | grep -c '#'", None),
    ("find . -name '*.py' -newer x | head", None),
    ("{ echo a; echo b; } | sort", None),
    ("time bosun doctor", None),
    ("time -p bosun doctor", None),
    ('bosun send orch --no-item "re-read policy.md"', None),
    ("bosun delegate 'task:acme/x#3' --window exporter", None),
    ("env BOSUN_STATE_DIR=/tmp/s bosun ledger list", None),
    ("if git diff --quiet; then echo clean; fi", None),
    ('while read -r l; do echo "$l"; done < file.txt', None),
    ('echo "It\'s $(date +%H:%M) now"', None),
    ("printf '%s\\n' \"a#b\" 'c#d'", None),
    ("sed -n '1,20p' lib/guard.py", None),
    ("uv run pytest -q tests/unit/test_guard.py", None),
    ("git push origin feature/x", None),
    ("echo $((3*4))", None),
    ("n=$((n*2))", None),
    ("echo $((x > 1 ? 1 : 0))", None),
    ("for ((i=0;i<3;i++)); do echo $i; done", None),
    ("(( total = a * b ))", None),
    ("echo $(( 1 + 2 ))", None),
    ("sleep $((60*5)) && bosun ledger list", None),
    ("echo $(( (1+2) ))", None),
)
# What bash ran (each echo REACHED-... line shows what it reaches): the parse must read that command, or ask.
SHELL_PROBES: tuple[str, ...] = (
    "echo a\\\n#;echo REACHED-continuation-hash",
    "echo $(true)#;echo REACHED-subst-hash",
    "echo $((1))#;echo REACHED-arith-hash",
    "echo $[1<<2]\necho REACHED-legacy-arith\n2]",
    "(( ((1)) << 1 ))\necho REACHED-nested-arith\n1",
    "echo ${x:-a<<b}\necho REACHED-param-heredoc\nb}",
    "x=; echo${x} REACHED-var-in-name",
    "time -p echo REACHED-time-p 2>/dev/null",
    'env -iS "/bin/echo REACHED-env-iS"',
    "/usr/bin/time echo REACHED-usr-bin-time",
    "echo${IFS}REACHED-IFS",
    "echo `echo \\`echo REACHED-nested-backtick\\``",
    "echo $( case x in x) echo REACHED-case;; esac )",
    "2>&1 echo REACHED-dup\n>&2 echo REACHED-dup2\n>|/dev/null echo REACHED-clobber",
)

# The forms of DANGEROUS that may pass, and why: none hides an outward call from the guard and the rules.
PLAIN = "a plain command the settings' ask rule sees, as written"
DATA = "the push is data: the body of a quoted heredoc that cat prints"
DANGEROUS_PASSES = {
    "echo \\$(git push origin main)": "bash refuses it (the escaped $ leaves a bare parenthesis)",
    "[[ -n x ]] && git push origin main": PLAIN,
    "[ -n x ] && git push origin main": PLAIN,
    "git push origin main": PLAIN,
    "gh pr merge 3": PLAIN,
    "git push \\\n  origin main": PLAIN + " (it starts with git push)",
    "bash -c \"cat <<'EOF'\n\\$(git push -f)\nEOF\"": DATA,
    "cat <<'EOF'\n$(git push -f)\nEOF": DATA,
    'cat <<"E"OF\n$(git push -f)\nEOF': DATA,
}
# The forms of ORDINARY that ask, and why.
ORDINARY_ASKS = {
    "python3 -c 'print(\"see https://example.com/docs\")'": "inline code that names a URL",
    "gh pr comment 7 --body $'Thanks!\\nMerged.'": "a gated command with ANSI-C quoting in its arguments",
}


class RoundTwoProbesTest(unittest.TestCase):
    def test_a_form_that_hides_a_command_is_denied_or_asks(self) -> None:
        for command, wanted in DANGEROUS:
            with self.subTest(command=command):
                self.assertEqual(decision(command), wanted)
                self.assertTrue(wanted in ("deny", "ask") or command in DANGEROUS_PASSES)

    def test_an_ordinary_command_gets_no_decision(self) -> None:
        for command, wanted in ORDINARY:
            with self.subTest(command=command):
                self.assertEqual(decision(command), wanted)
                self.assertTrue(wanted is None or command in ORDINARY_ASKS)

    def test_what_bash_runs_is_what_the_parse_reads_or_it_asks(self) -> None:
        for command in SHELL_PROBES:
            with self.subTest(command=command):
                reached = [
                    words
                    for words in guard.simple_commands(command)
                    if words[0] == "echo" or words[0].endswith("/echo")
                ]
                read = any(any(word.startswith("REACHED-") for word in words) for words in reached)
                self.assertTrue(read or decision(command) == "ask")

    def test_every_pass_is_listed(self) -> None:
        self.assertEqual({c for c, wanted in DANGEROUS if wanted is None}, set(DANGEROUS_PASSES))
        self.assertEqual({c for c, wanted in ORDINARY if wanted is not None}, set(ORDINARY_ASKS))


if __name__ == "__main__":
    unittest.main()
