"""Which parts of the Mac VM suites a change reaches (`tests/sandbox.sh mac --changed <ref>`, CI's install
job).

The suites, each run in a throwaway Mac by tests/sandbox/mac.sh: run (tests/install_e2e.sh), work
(tests/work_e2e.sh), app (tests/app_e2e.sh) and live (tests/sandbox/live.sh: the real agents, paid). Each is
in parts, one `part` line each in its script, which runs the function part_<id> (tests/sandbox/e2e_parts.sh):

    part <id> "<name>" [needs <id>,...] [last] [paid] covers '<glob>,...'

- covers: the files the part's behaviour on a real Mac rests on (its command, its templates, its lib
  modules), a curated list that tests/unit/test_vm_selection.py keeps honest. An entry
  `lib/common.sh#<function glob>` names functions of lib/common.sh, which changes too often for a change to
  it to run everything: a file that a `#` entry names is read by function (tools/shell_library.py), and a
  glob without `#` never matches it;
- needs: the parts whose state it starts from. The host resolves them, so the VM runs the closure, in file
  order;
- last: it runs after every other part (an uninstall, a restart), so it stands after them in the file;
- paid: it calls a real agent's API.

The rules are in tests/vm_selection.txt:

    all <suite>[,<suite>...] <glob>   (or `all * <glob>`) a change to it runs those suites whole
    none <glob>                       no VM part needs it: the smoke suite and the unit tests are enough
    year <glob>                       a change to it gives work's afternoon update a year of data
    minutes <suite>[/<part>] <n>      the estimate: a suite's clone and setup, or a part (`work/year`:
                                      what a year of data adds to the afternoon update, over 30 days)

For each changed file (or each changed function of a file read by function), in this order. A direct match:
no import or call is followed, as tools/smoke_select.py does, since the VM runs behind the smoke suite and the
unit tests, which follow them, and following them reaches nearly every part from nearly any file:

1. the `all` rules it matches: those suites whole;
2. the parts whose `covers` match it: those parts, with what they need;
3. if neither, a `none` rule: nothing;
4. otherwise it is unknown, which fails closed: run, work and app whole, with the rule to add. Never live: a
   paid suite runs only what its parts' covers name, and only when asked (`--paid`).

A changed line of a function-read file outside every function (but a blank line or a comment where a
statement could begin), or such a file that does not read as functions, is unknown too. Work's afternoon
update gets 365 days of data when a changed file matches a `year` rule (the update's freeze), else 30, and its
freeze check then says it measured 30.

Run it as `vm_select.py [--root <repo>] [--suite <suite>] (--changed <ref> | --only <part>[,<part>...])`.
--changed compares with the merge-base of <ref> and HEAD (committed, staged, unstaged and untracked files);
--only takes the parts named, with what they need (one suite). The report goes to stdout, its last line the
answer: with --suite `=> all`, `=> none` or `=> only <id>,<id>` (what to run, in order), and for work
` year=<days>` after it; without, `=> run:<ids|all|none> work:... app:... live:...`, live's marked `(paid)`.
Whatever goes wrong with --changed runs run, work and app whole (live: nothing); an --only naming no part is
an error (exit 2). Plain python3 3.9 is enough, as for tools/smoke_select.py."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell_library
from selection import (
    Marker,
    SelectionError,
    changed_files,
    file_change,
    marker_lines,
    matches,
    merge_base,
    needed,
    rule_lines,
)


ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "run": "tests/install_e2e.sh",
    "work": "tests/work_e2e.sh",
    "app": "tests/app_e2e.sh",
    "live": "tests/sandbox/live.sh",
}
FAIL_CLOSED = ("run", "work", "app")  # what an unknown file runs whole: never live, which is paid
KEYWORD = "part"
FLAGS = ("last", "paid")
RULES = "tests/vm_selection.txt"
YEAR, SHORT_YEAR = 365, 30  # work's afternoon update: days of data
YEAR_SUITE, YEAR_PART = "work", "afternoon-update"
YEAR_MINUTES = "work/year"  # what 365 days add over 30
UNKNOWN_HINT = (
    "an unknown file runs run, work and app whole. For each, one of: the part whose behaviour on a real "
    "Mac rests on it: add it to that part's `covers '...'` in its suite's script; no VM part needs it (the "
    "smoke suite and the unit tests are enough): `none <path>` in tests/vm_selection.txt; unsure: "
    "`all run,work,app <path>` there"
)
ALL_WORDS, ONE_WORDS = 3, 2  # `all <suites> <glob>` and `minutes <key> <n>`; `none <glob>` and `year <glob>`

Parts = Mapping[str, Sequence[Marker]]  # suite -> its parts, in file order
# A function-read file -> the functions of it that changed, or None when that cannot be said.
FunctionsOf = Callable[[str], "frozenset[str] | None"]


@dataclass(frozen=True)
class Whole:
    """An `all` rule: the suites it runs whole (`written` as the rule says them), for a change to <glob>."""

    suites: tuple[str, ...]
    written: str
    glob: str


@dataclass(frozen=True)
class Rules:
    whole: tuple[Whole, ...]
    none: tuple[str, ...]
    year: tuple[str, ...]
    minutes: Mapping[str, float]


@dataclass
class Plan:
    """What the changed files reach, before what the parts need: the suites run whole and why, the parts each
    item picked, the items no part needs, the unknown ones, and the changed files on a `year` rule."""

    whole: dict[str, list[str]] = field(default_factory=dict)
    picked: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    year: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Choice:
    """What one suite runs: none (no parts), all of it (whole), or these parts, in run order."""

    suite: str
    parts: tuple[str, ...]
    whole: bool = False
    year: int = 0  # work only: the afternoon update's days of data
    minutes: float = 0.0
    paid: bool = False


# ---- the files ---------------------------------------------------------------------------------------------


def parse_parts(text: str) -> list[Marker]:
    return [marker for _, marker in marker_lines(text, KEYWORD, FLAGS)]


def read_parts(root: Path) -> dict[str, list[Marker]]:
    found = {
        suite: parse_parts((root / script).read_text(encoding="utf-8")) for suite, script in SUITES.items()
    }
    empty = [SUITES[suite] for suite, parts in found.items() if not parts]
    if empty:
        raise SelectionError(f"no parts found in {', '.join(empty)}")
    return found


def parse_suites(word: str) -> tuple[str, ...]:
    if word == "*":
        return tuple(SUITES)
    named = tuple(word.split(","))
    unknown = [s for s in named if s not in SUITES]
    if unknown:
        raise SelectionError(f"no suite {', '.join(unknown)} (one of {', '.join(SUITES)}, or *)")
    return named


def parse_rules(text: str) -> Rules:
    whole: list[Whole] = []
    none: list[str] = []
    year: list[str] = []
    minutes: dict[str, float] = {}
    for number, raw, line in rule_lines(text):
        words = line.split()
        try:
            if words[0] == "all" and len(words) == ALL_WORDS:
                whole.append(Whole(parse_suites(words[1]), words[1], words[2]))
            elif words[0] in ("none", "year") and len(words) == ONE_WORDS:
                (none if words[0] == "none" else year).append(words[1])
            elif words[0] == "minutes" and len(words) == ALL_WORDS:
                minutes[words[1]] = float(words[2])
            else:
                raise SelectionError("want all <suites> <glob>, none <glob>, year <glob>, minutes <key> <n>")
        except (SelectionError, ValueError) as error:
            raise SelectionError(f"{RULES}, line {number}: {error}: {raw}") from error
    return Rules(tuple(whole), tuple(none), tuple(year), minutes)


def function_files(parts: Parts, rules: Rules) -> frozenset[str]:
    """The files read by function: those a `#` entry of a part's covers or of a rule names."""
    globs = [g for ps in parts.values() for p in ps for g in p.covers]
    globs += [w.glob for w in rules.whole] + list(rules.none)
    return frozenset(g.split("#", 1)[0] for g in globs if "#" in g)


def item_matches(item: str, glob: str) -> bool:
    """Whether <glob> names <item>: a file, or `<file>#<function>` of a file read by function, which only a
    `#` entry names (its file part a glob of the path, its function part a glob of the name)."""
    path, _, function = item.partition("#")
    files, mark, functions = glob.partition("#")
    if not mark:
        return not function and matches(item, glob)
    return bool(function) and matches(path, files) and matches(function, functions)


# ---- the plan ----------------------------------------------------------------------------------------------


def whole_by_rules(item: str, plan: Plan, rules: Rules) -> bool:
    """The suites the `all` rules that match <item> run whole, in <plan>; whether there was one."""
    hit = False
    for rule in rules.whole:
        if item_matches(item, rule.glob):
            for suite in rule.suites:
                plan.whole.setdefault(suite, []).append(f"{item} (all {rule.written} {rule.glob})")
            hit = True
    return hit


def picked_by_covers(item: str, plan: Plan, parts: Parts) -> bool:
    """The parts whose covers match <item>, in <plan>; whether there was one."""
    hit = False
    for suite, suite_parts in parts.items():
        for part in suite_parts:
            if any(item_matches(item, g) for g in part.covers):
                plan.picked.setdefault((suite, part.id), []).append(item)
                hit = True
    return hit


def place(item: str, plan: Plan, parts: Parts, rules: Rules) -> None:
    """<item>'s part in <plan>: the suites an `all` rule runs whole and the parts that cover it; else no part
    needs it (`none`), or it is unknown."""
    whole = whole_by_rules(item, plan, rules)
    if picked_by_covers(item, plan, parts) or whole:
        return
    if any(item_matches(item, g) for g in rules.none):
        plan.ignored.append(item)
    else:
        plan.unknown.append(item)


def plan_for(changed: Sequence[str], parts: Parts, rules: Rules, functions_of: FunctionsOf) -> Plan:
    """The plan for <changed>; <functions_of> says which functions of a function-read file changed."""
    plan = Plan()
    by_function = function_files(parts, rules)
    for path in changed:
        if any(matches(path, g) for g in rules.year):
            plan.year.append(path)
        if path not in by_function:
            place(path, plan, parts, rules)
            continue
        names = functions_of(path)
        if names is None:
            plan.unknown.append(f"{path} (a changed line outside its functions, or it reads as none)")
        elif not names:
            plan.ignored.append(f"{path} (no function of it changed)")
        for name in sorted(names or ()):
            place(f"{path}#{name}", plan, parts, rules)
    return plan


def in_order(suite_parts: Sequence[Marker], ids: set[str]) -> tuple[str, ...]:
    return tuple(p.id for p in suite_parts if p.id in ids)


def estimate(suite: str, ids: Sequence[str], year: int, rules: Rules) -> float:
    """Minutes, by the rules' `minutes` lines: the suite's clone and setup, its parts, and a year of data."""
    total = rules.minutes.get(suite, 0.0) + sum(rules.minutes.get(f"{suite}/{i}", 0.0) for i in ids)
    if suite == YEAR_SUITE and YEAR_PART in ids and year == YEAR:
        total += rules.minutes.get(YEAR_MINUTES, 0.0)
    return total


def make_choice(
    suite: str, suite_parts: Sequence[Marker], ids: set[str], whole: bool, year: int, rules: Rules
) -> Choice:
    chosen = in_order(suite_parts, ids)
    year = year if suite == YEAR_SUITE and chosen else 0
    paid = any("paid" in p.flags for p in suite_parts if p.id in ids)
    return Choice(suite, chosen, whole, year, estimate(suite, chosen, year, rules), paid)


def choose(suite: str, suite_parts: Sequence[Marker], plan: Plan, rules: Rules) -> Choice:
    """What <suite> runs for <plan>: whole for an `all` rule (or, but for live, an unknown file); else the
    parts picked and what they need."""
    year = YEAR if plan.year else SHORT_YEAR
    if suite in plan.whole or (plan.unknown and suite in FAIL_CLOSED):
        return make_choice(suite, suite_parts, {p.id for p in suite_parts}, True, year, rules)
    picked = [p.id for p in suite_parts if (suite, p.id) in plan.picked]
    return make_choice(suite, suite_parts, set(picked) | set(needed(suite_parts, picked)), False, year, rules)


def only(suite: str, suite_parts: Sequence[Marker], names: Sequence[str], rules: Rules) -> Choice:
    """The parts <names> of <suite>, with what they need; an error for a name that is no part."""
    known = {p.id for p in suite_parts}
    unknown = [n for n in names if n not in known]
    if unknown:
        parts = " ".join(sorted(known))
        raise SelectionError(f"{suite} has no part {', '.join(unknown)} (its parts: {parts})")
    chosen = set(names) | set(needed(suite_parts, names))
    return make_choice(suite, suite_parts, chosen, False, SHORT_YEAR, rules)


# ---- what it says ------------------------------------------------------------------------------------------


def year_note(choice: Choice, plan: Plan | None) -> str:
    """`year <days>`, with the files that asked for a year, when the afternoon update runs; else nothing."""
    if YEAR_PART not in choice.parts:
        return ""
    why = f": {', '.join(plan.year)}" if plan and plan.year and choice.year == YEAR else ""
    return f"year {choice.year}{why}"


def choice_line(choice: Choice, plan: Plan | None = None) -> str:
    """`<suite>: <parts>   ~<minutes> min`, or nothing to run; live's says it is paid."""
    head = f"{choice.suite + ':':<6}"
    if not choice.parts:
        return f"{head}nothing to run"
    year = year_note(choice, plan)
    shown = " ".join(f"{p}({year})" if p == YEAR_PART else p for p in choice.parts)
    if choice.whole:
        shown = f"whole ({len(choice.parts)} parts{', ' + year if year else ''})"
    paid = ", paid: it runs only with --paid" if choice.paid else ""
    return f"{head}{shown}   ~{choice.minutes:.1f} min{paid}"


def answer(choice: Choice) -> str:
    """The last line for one suite."""
    if not choice.parts:
        return "=> none"
    year = f" year={choice.year}" if choice.year else ""
    return ("=> all" if choice.whole else "=> only " + ",".join(choice.parts)) + year


def summary(choices: Sequence[Choice]) -> str:
    """The last line for every suite."""
    words = []
    for choice in choices:
        what = "none" if not choice.parts else "all" if choice.whole else ",".join(choice.parts)
        words.append(f"{choice.suite}{'(paid)' if choice.paid else ''}:{what}")
    return "=> " + " ".join(words)


def plan_lines(plan: Plan) -> list[str]:
    lines = [f"  {suite} whole: {', '.join(plan.whole[suite])}" for suite in SUITES if suite in plan.whole]
    for (suite, part), items in plan.picked.items():
        lines.append(f"  {suite}/{part}: {', '.join(items)}")
    lines += [f"  (no part needs it): {item}" for item in plan.ignored]
    lines += [f"  (unknown): {item}" for item in plan.unknown]
    if plan.unknown:
        lines.append(f"vm selection: {UNKNOWN_HINT}")
    return lines


def report(changed: Sequence[str], plan: Plan, choices: Sequence[Choice]) -> list[str]:
    lines = [f"vm selection: {len(changed)} changed file(s)", *plan_lines(plan)]
    lines += [choice_line(c, plan) for c in choices]
    return [*lines, answer(choices[0]) if len(choices) == 1 else summary(choices)]


def failed(error: Exception, suites: Sequence[str]) -> list[str]:
    """The report when the selection cannot be made: run, work and app whole; live nothing (it is paid)."""
    lines = [f"vm selection: {error}: run, work and app run whole, live runs nothing"]
    if len(suites) == 1:
        return [*lines, "=> none" if suites[0] not in FAIL_CLOSED else "=> all"]
    return [*lines, "=> " + " ".join(f"{s}:{'all' if s in FAIL_CLOSED else 'none'}" for s in suites)]


# ---- git and the command -----------------------------------------------------------------------------------


def functions_changed(root: Path, base: str) -> FunctionsOf:
    """The functions of a file that changed since <base>, on both sides of the diff; None when that cannot be
    said (shell_library.changed_functions, where a blank line or a comment outside the functions, where a
    statement could begin, is no change: a function added with the comment above it). The one place this tool
    reads a shell file by function."""

    def of(path: str) -> frozenset[str] | None:
        change = file_change(root, base, path)
        new = shell_library.read(change.new)
        return shell_library.changed_functions(new, change.hunks, shell_library.read(change.old), True)

    return of


def select_changed(root: Path, rules_path: Path, base: str, suites: Sequence[str]) -> list[str]:
    """The report for the change since <base>, for <suites>. Whatever goes wrong: run, work and app whole."""
    try:
        parts = read_parts(root)
        rules = parse_rules(rules_path.read_text(encoding="utf-8"))
        since = merge_base(root, base)
        changed = changed_files(root, since)
        plan = plan_for(changed, parts, rules, functions_changed(root, since))
        return report(changed, plan, [choose(s, parts[s], plan, rules) for s in suites])
    except Exception as error:  # noqa: BLE001 - failing closed is the point: any trouble runs the suites whole
        return failed(error, suites)


def select_only(root: Path, rules_path: Path, suite: str, names: Sequence[str]) -> list[str]:
    parts = read_parts(root)
    choice = only(suite, parts[suite], names, parse_rules(rules_path.read_text(encoding="utf-8")))
    extra = [p for p in choice.parts if p not in names]
    needs = f" (and, as they need them: {' '.join(extra)})" if extra else ""
    return [f"vm selection: --only {','.join(names)}{needs}", choice_line(choice), answer(choice)]


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--rules", type=Path, help=f"default: <root>/{RULES}")
    parser.add_argument("--suite", choices=tuple(SUITES))
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--changed", metavar="REF")
    what.add_argument("--only", metavar="PART,...")
    args = parser.parse_args(argv)
    rules = args.rules or args.root / RULES
    if args.changed is not None:
        suites = [args.suite] if args.suite else list(SUITES)
        print("\n".join(select_changed(args.root, rules, args.changed, suites)))
        return 0
    if args.suite is None:
        parser.error("--only goes with --suite")
    try:
        print("\n".join(select_only(args.root, rules, args.suite, args.only.split(","))))
    except (SelectionError, OSError) as error:
        print(f"vm_select.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
