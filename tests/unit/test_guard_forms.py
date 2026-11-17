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
    ("bash -c 'x=$(cat <<E\ngit push -f\nE\n); echo'", None),
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
    "bash -c 'x=$(cat <<E\ngit push -f\nE\n); echo'": "the push is data: a heredoc body cat prints into x",
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


# The review's round-3 probes (scratchpad rv3): ordinary work (ord*, hash, ar, split, ansi), exotic forms (exo*)  # noqa: E501 (the probe as the review wrote it)
# and the docs' claims (docs*), each with what it gets.
ROUND_THREE_ORDINARY: tuple[tuple[str, str | None], ...] = (
    (
        "bosun ledger propose 'mypr:acme/x#12' - <<'EOF'\nMerge it: gh pr merge https://github.com/acme/x/pull/12 --squash --auto, then git push the tag.\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger propose 'mypr:acme/x#12' --run 'gh pr merge https://github.com/acme/x/pull/12 --squash --auto'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger propose 'out-20261117-1' - <<'EOF'\nReply in the thread: \"I'll git push the fix after lunch; then gh pr ready.\"\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger propose 'out-20261117-1' - <<EOF\nReply: I will git push the fix, then gh pr ready.\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger propose 'out-20261117-1' --send slack --channel C00000000 --thread 1700000000.000100 --text - <<'EOF'\nDone: I'll git push now and run gh pr merge after CI. Don't force push.\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger reach-out 'task-1' --channel slack --to 'Dana' --purpose ask --subject 'can you git push your branch?'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger reach-out 'task-1' --channel slack --to 'Dana' --purpose ask --subject \"can you git push -f your branch? (it's rebased)\"",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger reach-out 'task-1' --channel slack --to 'Dana' --purpose ask --subject 'gh pr merge --admin is blocked, can you merge?'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun pr-threads https://github.com/acme/x/pull/12", None),
    ("bosun pr-threads https://github.com/acme/x/pull/12 --awaiting-author", None),
    (
        "gh pr view https://github.com/acme/x/pull/12 --json state,author,title,headRefOid,reviews,reviewRequests,reviewDecision,latestReviews,mergedAt,closedAt",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("gh pr view https://github.com/acme/x/pull/12 --json comments,reviews --jq '.comments[].body'", None),
    ("gh pr checks https://github.com/acme/x/pull/12", None),
    ("gh pr diff https://github.com/acme/x/pull/12", None),
    ("gh issue view https://github.com/acme/x/issues/3 --comments", None),
    ("gh search prs --author @me --state open", None),
    ("gh api repos/acme/x/pulls/12/comments --jq '.[].body'", None),
    ("gh api graphql -f query='query { viewer { login } }'", None),
    (
        "bosun kg propose --subject 'person:dana' --predicate reviews --object 'product:invoice-exporter' --from-user --quote \"Dana's the one who reviews the exporter\"",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun kg propose --subject 'person:dana' --predicate reviews --object 'product:invoice-exporter' --from-user --quote 'Dana'\\''s the one who reviews the exporter'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun kg propose --subject 'process:release' --predicate step --value \"git push the tag, then gh release create\" --source 'slack:https://acme.slack.com/archives/C0/p1'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("git log --oneline -20", None),
    ("git -C ~/src/acme-x status", None),
    ("git -C ~/src/acme-x log --oneline origin/main..HEAD", None),
    ("git -C ~/src/acme-x log -1 --format='%H %s' --grep='push'", None),
    ('bosun ledger list --open --json | jq -r \'.[] | select(.title | test("push"; "i")) | .id\'', None),
    ("bosun ledger list --open --json | jq -r '.[] | \"\\(.id) git push pending\"'", None),
    ('python3 -c \'import json,sys; d=json.load(sys.stdin); print("push:", d["title"])\' < /dev/null', None),
    (
        'gh pr view 12 --json title | python3 -c \'import json,sys; print(json.load(sys.stdin)["title"].replace("push","PUSH"))\'',  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        'bosun ledger show \'mypr:acme/x#12\' --json | python3 -c \'import json,sys; d=json.load(sys.stdin); print(d.get("proposal",{}).get("text",""))\'',  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun delegate --update 'task-1' --details - <<'EOF'\nAlso run gh pr create when done, and git push the branch (never git push -f).\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun delegate new-work --id 'task-1' --window acme-x - <<'EOF'\nOpen a PR with gh pr create; git push the branch first. Never gh pr merge --admin.\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun send --no-item orchestrator - <<'EOF'\nnote: git push failed\nEOF", None),
    ("bosun ledger approve 'mypr:acme/x#12' --quote 'yes, merge it with gh pr merge'", None),
    ("bosun ledger transition 'out-1' sent --note 'asked them to git push; gh pr ready next'", None),
    ("bosun ledger note 'task-1' 'git push -f is denied by the guard'", None),
    ("gh pr merge https://github.com/acme/x/pull/12 --squash --auto", None),
    ("git push origin feat", None),
    (
        "bosun ledger propose 'mypr:acme/x#12' --run 'gh pr merge https://github.com/acme/x/pull/12 --squash --auto' && bosun ledger show 'mypr:acme/x#12'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger list --open --json | jq -r '.[] | .id' | while read -r id; do bosun ledger show \"$id\"; done",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        'for u in https://github.com/acme/x/pull/12 https://github.com/acme/x/pull/13; do gh pr view "$u" --json state,mergeable; done',  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "gh pr view https://github.com/acme/x/pull/12 --json statusCheckRollup --jq '.statusCheckRollup[] | select(.conclusion==\"FAILURE\") | .name'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun ledger seen https://acme.slack.com/archives/C00000000/p1700000000000100", None),
    ("bosun my-prs --since 7d --json | jq -r '.[] | \"\\(.url) \\(.state)\"'", None),
    ("git -C ~/src/acme-x fetch origin && git -C ~/src/acme-x log --oneline HEAD..origin/main", None),
    ("bosun ledger propose 'mypr:acme/x#12' \"rebase on main and git push (no force)\"", None),
    ("bosun ledger propose 'mypr:acme/x#12' \"merge with gh pr merge --squash\"", None),
    ("echo \"will git push after review\" && bosun ledger show 'task-1'", None),
    (
        "bosun ledger list --json | python3 -c \"import json,sys\nfor i in json.load(sys.stdin):\n    print(i['id'], i['title'])\"",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger record-source 'https://github.com/acme/x/pull/12#discussion_r1' --class pr-review --item 'mypr:acme/x#12'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("gh api repos/acme/x/pulls/12/reviews --paginate --jq '.[] | {user: .user.login, state}'", None),
    (
        "bosun ledger reach-out 'task-1' --channel github --to 'dana' --purpose nudge --subject 'ping on review' --via 'gh pr comment https://github.com/acme/x/pull/12'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun kg propose --subject 'repo:acme/x' --predicate deploys-with --value 'git push origin main triggers the deploy' --from-user --quote 'git push origin main triggers the deploy'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "L() { bosun ledger \"$@\"; }\nL transition 'mypr:acme/x#12' changes-requested --note 'Dana asked to git push a fix; then gh pr ready' >/dev/null\nL note 'task-1' 'no git push -f here'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("L=\"bosun ledger\"; $L note 'task-1' 'asked to git push'", None),
    (
        "bosun send exporter-tax - 2>/dev/null <<'EOF' || bosun send exporter-tax 'Addendum: git push the branch, then gh pr create.'\nAddendum: git push the branch, then gh pr create.\nEOF",  # noqa: E501 (the probe as the review wrote it)
        "deny",
    ),
    ("bosun ledger propose 'out-1' - <<-'EOF'\n\tReply: git push done, gh pr ready next.\n\tEOF", None),
    (
        "bosun ledger note 'mypr:acme/x#12' \"Lena CHANGES_REQUESTED, 4 threads; will git push after fixes\" && \\\nbosun delegate fix-my-pr --id 'mypr:acme/x#12' --window acme-x --details - <<'EOF'\nFix the 4 threads, git push, reply on each thread.\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("msg='git push done'; bosun ledger note 'task-1' \"$msg\"", None),
    (
        "for id in out-1 out-2; do bosun ledger transition \"$id\" sent --note 'asked them to git push'; done",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("echo $((1<<2)); bosun ledger show 'task-1'", None),
    ('n=$(( $(bosun ledger list --open --json | jq length) + 1 )); echo "$n items; git push pending"', None),
    ("$( case x in x) git push -f;; esac )", "deny"),
    ('x=$(case "$1" in a) echo 1;; esac); echo "$x"', None),
    (
        "gh pr view https://github.com/acme/x/pull/12 --json title,body --jq '.title + \" \" + .body' | grep -i 'git push'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun ledger show 'task-1'  # then gh pr merge", None),
    (
        "bosun ledger propose 'mypr:acme/x#12' \\\n  --run 'gh pr merge https://github.com/acme/x/pull/12 --squash --auto'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun ledger propose 'mypr:acme/x#12' --run 'git -C ~/src/acme-x push origin feat-1'", None),
    ("git -C ~/src/acme-x push origin feat-1", "ask"),
    ("cd ~/src/acme-x && git push origin feat-1", None),
    (
        'bosun ledger propose \'task-1\' --tool mcp__slack__slack_send_message --args-json - <<\'EOF\'\n{"channel": "C00000000", "text": "I\'ll git push the fix and gh pr merge it after CI"}\nEOF',  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        'bosun ledger list --open --json | jq -r \'.[] | select(.kind == "mypr") | "\\(.id)\\t\\(.state)"\' | sort',  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun ledger list --open --json | jq '.[] | {id, title}'", None),
    (
        "gh pr view https://github.com/acme/x/pull/12 --json reviews --jq '.reviews[] | {author: .author.login, state}'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun ledger propose 'out-1' \"post in #releases: 'git push of v1.2 done'\"", None),
    ("bosun ledger approve 'out-1' --quote \"ok, send it\"", None),
    ("bosun ledger said 'task-1' - <<'EOF'\nyes, go ahead and git push\nEOF", None),
    ("gh pr view 12 --repo acme/x --json mergeable,mergeStateStatus && gh pr checks 12 --repo acme/x", None),
    ("sleep 60; gh pr checks https://github.com/acme/x/pull/12 --watch --fail-fast", None),
    (
        "bosun ledger propose 'mypr:acme/x#12' --run \"gh pr merge https://github.com/acme/x/pull/12 --squash --auto\" 2>&1 | tail -3",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger propose 'mypr:acme/x#12' - <<'EOF'\n1. gh pr merge https://github.com/acme/x/pull/12 --squash\n2. git push origin v1.2 (tag)\n3. gh release create v1.2\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger reach-out 'task-1' --channel slack --to 'Dana' --purpose ask --subject 'push -f ok?'",
        None,
    ),
    (
        "bosun kg supersede 'f-123' --value \"release: git push the tag, then gh release create\" --source 'pr:https://github.com/acme/x/pull/12'",
        None,
    ),
    (
        "bosun kg propose --subject 'person:dana' --predicate prefers --value \"she'd rather we gh pr merge with --squash\" --from-user --quote \"she'd rather we gh pr merge with --squash\"",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun kg batch apply 3 --decision 'confirm 1,2; reject 3' --from-user --quote \"confirm 1 and 2, reject 3 (it's wrong)\"",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("cat ~/.config/bosun/policy.md | grep -n 'git push'", None),
    (
        "bosun ledger show 'task-1' --json | jq -r '.history[] | select(.note // \"\" | test(\"push\")) | .note'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("python3 - <<'EOF'\nimport json, subprocess\nprint(\"will not git push\")\nEOF", "ask"),
    ('bosun ledger list --json > /dev/null && echo "ok: nothing to git push"', None),
    ("printf '%s\\n' 'git push origin main' | bosun ledger propose 'task-1' -", None),
    (
        "L() { bosun ledger \"$@\"; }\nL propose 'x' - <<'EOF'\ngh pr merge https://github.com/acme/x/pull/1 --squash\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("timeout 30 bosun ledger propose 'x' - <<'EOF'\ngit push origin feat\nEOF", None),
    ("~/.local/bin/bosun ledger propose 'x' - <<'EOF'\ngit push origin feat\nEOF", None),
    (
        "bosun send --id 'x' exporter-tax - 2>/dev/null <<'EOF' || bosun send --id 'x' exporter-tax 'Addendum: git push the branch, then gh pr create.'\nAddendum: git push the branch, then gh pr create.\nEOF",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("rg -n 'git push' ~/.config/bosun/", None),
    ("bosun ledger propose 'x' --text '\n# Plan\ngit push the tag\n'", None),
    ("bosun ledger propose 'x' - <<'EOF'\n# Plan\n1. git push origin v1\nEOF", None),
    ("cd ~/src/acme-x && git status --short && git log --oneline -3", None),
    ("git -C ~/src/acme-x diff --stat origin/main...HEAD", None),
    ("gh pr view https://github.com/acme/x/pull/12 --json commits --jq '.commits[].messageHeadline'", None),
    ("bosun ledger propose 'x' --run 'git -C ~/src/acme-x push origin feat' && echo proposed", None),
    ("bosun ledger propose 'x' - <<'EOF' && bosun ledger show 'x'\ngit push origin feat\nEOF", None),
    (
        "bosun ledger propose 'x' - <<'EOF'; bosun ledger propose 'y' - <<'EOF2'\ngit push origin feat\nEOF\ngh pr merge 1 --squash\nEOF2",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        'bosun ledger propose \'mypr:acme/x#12\' --run "gh pr merge https://github.com/acme/x/pull/12 --squash --auto" --note "after CI; never --admin"',  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger reach-out 'task-1' --channel slack --to 'Dana' --purpose ask --subject \"Can you git push your fix? I'll gh pr merge it after\"",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun ledger said 'task-1' 'Dana: pushed with git push -f, sorry'", None),
    (
        "bosun kg propose --subject 'repo:acme/x' --predicate rule --value 'never gh pr merge --admin' --source 'doc:CONTRIBUTING.md'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger list --open --json | jq -r '.[] | select(.title | test(\"git push\")) | .id' | head -5",
        None,
    ),
    ("gh pr list --repo acme/x --search 'git push in:title' --json number", None),
    ("gh pr view 12 --repo acme/x --json body --jq .body | head -40", None),
    ("bosun ledger propose 'x' \"Run: gh api -X POST repos/acme/x/issues/1/comments -f body='ping'\"", None),
    (
        "bosun ledger propose 'x' --run \"gh api -X POST repos/acme/x/pulls/12/requested_reviewers -f 'reviewers[]=dana'\"",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("gh api -X POST repos/acme/x/pulls/12/requested_reviewers -f 'reviewers[]=dana'", None),
    ("L=\"bosun ledger\"; $L propose 'x' - <<'EOF'\ngit push origin feat\nEOF", None),
    ("L() { bosun ledger \"$@\"; }\nL propose 'x' - <<'EOF'\nI'll git push the fix\nEOF", None),
    ("L() { bosun ledger \"$@\"; }\nL propose 'x' - <<'EOF'\nReply to Dana: thanks, merged.\nEOF", None),
    ("L() { bosun ledger \"$@\"; }\nL propose 'x' --run 'gh pr merge 1 --squash'", None),
    ("D() { bosun delegate \"$@\"; }\nD --update 'x' --details - <<'EOF'\nalso git push\nEOF", None),
    ("bosun ledger note 'mypr:acme/x#12' '#12 needs a git push after the fix'", None),
    (
        "bosun ledger propose 'out-1' --send slack --channel C00000000 --text '#12 is merged: gh pr merge went through'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    (
        "bosun ledger reach-out 'task-1' --channel slack --to 'Dana' --purpose ask --subject '#104: can you git push the rebase?'",  # noqa: E501 (the probe as the review wrote it)
        None,
    ),
    ("bosun ledger note 'mypr:acme/x#12' \"#12: rebased; git push next\"", None),
    ("bosun ledger note 'mypr:acme/x#12' 'rebased #12; git push next'", None),
    ("echo '#12 git push'", None),
    ('n=$(( $(bosun ledger list --open --json | jq length) + 1 )); echo "$n"', None),
    ("echo $(( $(bosun ledger list --open --json | jq length) + 1 ))", None),
    ("n=$(bosun ledger list --open --json | jq length); echo $((n + 1))", None),
    ("echo $(( 3 + $(date +%s) ))", None),
    ("total=$(( $(gh pr list --json number --jq length) ))", None),
    ("(( $(bosun ledger list --open --json | jq length) > 0 )) && echo some", None),
    ("echo $(( `date +%s` - 100 ))", None),
    ("echo $[ $(date +%s) - 100 ]", None),
    ("echo $(( $(echo 1) ))", None),
    ("x=$(( (1+2) * 3 )); echo $x", None),
    ("echo $(( $(date +%s) - $(date -j -f %Y-%m-%d 2026-11-01 +%s) ))", None),
    ("echo ready to git push", None),
    ("bosun ledger note x git push failed", None),
    ('echo "ready to" git push', None),
    ("bosun ledger propose x --run gh\\ pr\\ merge\\ 1", None),
    ("bosun ledger show x; echo ---; echo next: gh pr merge", None),
    ("printf '%s %s\\n' git push", None),
    ("bosun ledger note x $'line1\\nthen git push'", None),
    ("B=\"bosun\"; $B ledger note x 'git push later'", None),
    ("git push origin '{a,b}'", None),
)

ROUND_THREE_EXOTIC: tuple[tuple[str, str | None], ...] = (
    ('f() { echo; "$@"; }; f git push -f origin main', "ask"),
    ('L() { bosun ledger "$@"; git "$@"; }; L push -f origin main', "ask"),
    ('L() { bosun ledger "$@"; }; L note x hi; L() { git "$@"; }; L push -f origin main', "ask"),
    ("G=git; $G push origin main", "ask"),
    ("${x:-git} push origin main", "ask"),
    ("${x:-git} push -f origin main", "ask"),
    ('x="git:push:-f"; IFS=:; $x', "ask"),
    (
        'curl -s -X PUT -H "Authorization: token $(gh auth token)" https://api.github.com/repos/acme/x/pulls/1/merge',
        "ask",
    ),
    ("git send-pack origin +main:main", "deny"),
    ("git -c remote.origin.push=+main:main push origin", "ask"),
    ("git config remote.origin.push +refs/heads/main:refs/heads/main && git push origin", "ask"),
    ("echo 'git push -f origin main' > ~/x.sh; . ~/x.sh", None),
    ("builtin eval 'git push -f'", "ask"),
    ("trap 'git push -f' EXIT", "ask"),
    ("bosun ledger note x 'git push -f'; bosun ledger show x --json | jq -r '.history[-1].note' | sh", "ask"),
    ("echo git push -f | xargs -I{} sh -c '{}'", "ask"),
    ("printf '%s' git | xargs -I{} {} push -f origin main", "ask"),
    ("jq -rn '\"git push -f\"' | bash", "ask"),
    ('echo "git pu""sh -f" | sh', "ask"),
    ("g\\it push -f", "deny"),
    ("git push origin main~0:+main", None),
    ("git push origin :main", None),
    ("git push --delete origin main", None),
    ("gh pr merge 1 --admi\\n", "deny"),
    ('gh pr merge 1 "--admin"', "deny"),
    ('gh pr merge 1 --ad""min', "deny"),
    ("gh api -X PUT repos/acme/x/pulls/1/merge -f merge_method=squash", None),
    ("gh pr merge 1 --auto --squash -d", None),
    ("gh repo delete acme/x --yes", None),
    ("gh secret set X -b y", None),
    ("gh pr review 1 --approve", None),
    ("gh release upload v1 f", None),
    ("gh repo edit --visibility public", None),
    ("git push origin HEAD:refs/heads/main --no-verify", None),
    ("G=git P=push; $G $P -f origin main", "ask"),
    ('eval "$(echo git push -f)"', "ask"),
    ('L() { bosun ledger "$@"; git "$@"; }; L push origin main', "ask"),
    ('echo() { git "$@"; }; echo push -f origin main', "ask"),
    ("echo() { git \"$@\"; }; echo 'push -f' origin main", "ask"),
    ('cat() { command git "$@"; }; cat push origin main', "ask"),
    ("alias echo=git; echo push -f", "ask"),
    ('L="bosun ledger"; L="git"; $L push -f origin main', "ask"),
    ("L=\"echo\"; $L 'push -f'", None),
)

ROUND_THREE_DOCUMENTED: tuple[tuple[str, str | None], ...] = (
    ("bosun ledger propose 'x' --run 'gh pr merge https://github.com/acme/x/pull/1 --squash'", None),
    ("bosun delegate new-work --id 'x' --window w - <<'EOF'\nremember to git push\nEOF", None),
    ("ssh host 'git push -f'", "ask"),
    ("find . -exec git push -f \\;", "ask"),
    ("ls # git push", None),
    ("git push origin main", None),
    ("echo 'git push origin main' | sh", "ask"),
    ("cat <<'EOF' | sh\ngit push origin main\nEOF", "ask"),
    ('x=gi; $x"t" push origin main', "ask"),
    ("git push $FLAGS", "ask"),
    ("git -c alias.p=push p", "ask"),
    ("gh alias set m 'pr merge --admin'", "ask"),
    ("coproc NAME { git push -f; }", "deny"),
    ("git push $'-f'", "ask"),
    ("git push origin --{force,}", "ask"),
    ("/usr/bin/g[i]t push", "ask"),
    ("git${IFS}push origin main", "ask"),
    ("$'git' push", "ask"),
    ('"git" push origin main', "ask"),
    ("\\git push origin main", "ask"),
    ("python3 -Ic 'import os; os.system(\"git push\")'", "ask"),
    ("perl -we 'system(\"git push\")'", "ask"),
    ('node --eval=\'require("child_process").execSync("git push")\'', "ask"),
    ("osascript -e 'do shell script \"gh pr merge 1\"'", "ask"),
    ("time -p git push -f", "deny"),
    ("uv run git push -f", "deny"),
    ("poetry run git push -f", "deny"),
    ("pipx run git push -f", "deny"),
    ("doas git push -f", "deny"),
    ("setsid git push -f", "deny"),
    ("flock /tmp/l git push -f", "deny"),
    ("flock /tmp/l -c 'git push -f'", "deny"),
    ("caffeinate git push -f", "deny"),
    ("env -S 'git push -f'", "deny"),
    ("script -q /dev/null git push -f", "deny"),
    ("script -c 'git push -f' /dev/null", "deny"),
    ("a=(git push -f); echo ok", "ask"),
    ("echo $((1<<2)); git push -f", "deny"),
    ("x=$[1+2]; git push -f", "deny"),
    ("cat <(git push -f)", "deny"),
    ("echo `echo \\`git push -f\\``", "deny"),
    ("git push origin main 2>&1 >|out", None),
    ("git push origin main &>log", None),
    ("bash -c 'git push -f'", "deny"),
    ("fish -c 'git push -f'", "deny"),
    ("eval 'git push -f'", "deny"),
    ("gh pr merge 1 --admin=true", "deny"),
    ("git push --mirror", "deny"),
    ("git push origin +main", "deny"),
    ("git push --force-with-lease", "deny"),
    ("gh -R acme/x pr merge 1 --admin", "deny"),
    ("git -C dir push -f", "deny"),
    ('L="bosun ledger"; $L show x', None),
    ('L() { bosun ledger "$@"; }; L show x', None),
    ("L() { bosun ledger \"$@\"; }; L note x 'git push -f later'", None),
    ("L=\"bosun ledger\"; $L note x 'git push -f later'", None),
    ("# git push -f\nbosun ledger show x", None),
    ("bosun ledger show x \\\n# git push -f", None),
    ("function L { bosun ledger \"$@\"; }; L note x 'git push later'", None),
    ("gh m", None),
    ("git p", None),
    ("gh alias set m 'pr merge'", "ask"),
    ("echo 'git push -f' | sh", "ask"),
    ("cat <<'EOF' | bash\ngit push -f origin main\nEOF", "ask"),
    ("printf 'gh pr merge 1 --admin\\n' | sh", "ask"),
    ("bosun ledger show x | sh", "ask"),
    ("G=git; $G push -f", "ask"),
    ("F=-f; git push $F", "ask"),
    ("git push origin main --force-if-includes", "deny"),
    ("cat <<'EOF' > /tmp/x.sh\ngit push -f\nEOF", None),
    ("grep -q x f && git push origin main", None),
)

# The ordinary probes that do not pass, and why.
ROUND_THREE_ORDINARY_NOT_PASSING = {
    "bosun send exporter-tax - 2>/dev/null <<'EOF' || bosun send exporter-tax 'Addendum: git push the branch, "  # noqa: E501 (the probe as the review wrote it)
    "then gh pr create.'\nAddendum: git push the branch, then gh pr create.\nEOF": "a send that names no work item",  # noqa: E501 (the probe as the review wrote it)
    "$( case x in x) git push -f;; esac )": "a force push, as main denies it",
    "git -C ~/src/acme-x push origin feat-1": "a push in a form the ask rule does not see",
    "python3 - <<'EOF'\nimport json, subprocess\nprint(\"will not git push\")\nEOF": "code that names git push",  # noqa: E501 (the probe as the review wrote it)
}
# The exotic probes that pass, and why each is in docs/permissions.md's best-effort list (or harmless).
ROUND_THREE_EXOTIC_PASSES = {
    "echo 'git push -f origin main' > ~/x.sh; . ~/x.sh": "a script file sourced",
    "git push origin main~0:+main": PLAIN + " (a + after the colon forces nothing)",
    "git push origin :main": PLAIN,
    "git push --delete origin main": PLAIN,
    "gh api -X PUT repos/acme/x/pulls/1/merge -f merge_method=squash": PLAIN,
    "gh pr merge 1 --auto --squash -d": PLAIN,
    "gh repo delete acme/x --yes": PLAIN,
    "gh secret set X -b y": PLAIN,
    "gh pr review 1 --approve": PLAIN,
    "gh release upload v1 f": PLAIN,
    "gh repo edit --visibility public": PLAIN,
    "git push origin HEAD:refs/heads/main --no-verify": PLAIN,
    "L=\"echo\"; $L 'push -f'": "echo prints it",
}


class RoundThreeProbesTest(unittest.TestCase):
    def test_ordinary_work_passes(self) -> None:
        for command, wanted in ROUND_THREE_ORDINARY:
            with self.subTest(command=command):
                self.assertEqual(decision(command), wanted)
                self.assertTrue(wanted is None or command in ROUND_THREE_ORDINARY_NOT_PASSING)

    def test_an_exotic_form_asks_or_is_denied_or_is_listed(self) -> None:
        for command, wanted in ROUND_THREE_EXOTIC:
            with self.subTest(command=command):
                self.assertEqual(decision(command), wanted)
                self.assertTrue(wanted is not None or command in ROUND_THREE_EXOTIC_PASSES)

    def test_the_docs_claims(self) -> None:
        for command, wanted in ROUND_THREE_DOCUMENTED:
            with self.subTest(command=command):
                self.assertEqual(decision(command), wanted)

    def test_every_exception_is_one(self) -> None:
        self.assertEqual(
            {c for c, wanted in ROUND_THREE_ORDINARY if wanted is not None},
            set(ROUND_THREE_ORDINARY_NOT_PASSING),
        )
        self.assertEqual(
            {c for c, wanted in ROUND_THREE_EXOTIC if wanted is None}, set(ROUND_THREE_EXOTIC_PASSES)
        )


if __name__ == "__main__":
    unittest.main()
