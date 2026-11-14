"""Which sections of the smoke suite one shard of a split run runs (`tests/smoke.sh --shard K/N`).

CI's `smoke` runs the suite on N machines at once, each with `--shard K/N`. Of the sections a run would run
(the whole suite, or what --changed, --only or --last-failed chose), each goes to exactly one shard, its own;
a shard also runs again the sections its own ones `needs`, as --only does, and every shard runs the shared
setup and the `always_run` parts. Each shard works the split out for itself from the same files, so they
agree.

The shards are balanced by how long their sections take, from tests/smoke_weights.txt: one `<section id>
<seconds>` line per section, from a run on one machine (only the ratios matter; `#` comments). A section it
does not list counts as the median of those it does. The split is greedy, the longest first, where a
section's length includes what it needs. Each goes to the shard it adds the least to (what it needs costs
nothing in a shard that runs that already, so a section that needs a long one goes where that one runs) of
those it leaves within an even share: the selection's time over N, or the longest section's if that is more;
then to the shorter, then the lower number. Where it fits in none, it goes to the shard that leaves the
longest one shortest. On the suite's own weights this splits it into thirds that run nothing twice, and on a
selection it comes within a few percent of the best split there is (tests/unit/test_smoke_select.py).

One section is in every shard that runs anything: `uninstall-files` (EVERY_SHARD), which checks that
uninstall knows every file the run left behind. A shard leaves only its own files, so it checks those, and
the shards together check what a whole run leaves.

Run it as `smoke_shard.py --script <tests/smoke.sh> --weights <file> --shard K/N [--sections <id>,<id>]`
(no --sections: every section): a line about the split on stdout, then the answer, `=> only <id>,<id>` (this
shard's own sections, in file order; the suite adds what they need) or `=> none`. Whatever goes wrong is
`=> all`: the shard runs every section it was given, so a broken split can only make a run longer. After a
run, `--record <tests/.smoke-cache/durations>` in place of --shard writes the weights file anew from what the
run measured (a section the record lacks keeps its weight). Plain python3 3.9 is enough, as for
smoke_select.py."""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_select import Section, parse_sections


# What a whole run leaves is what this section checks: in every shard, each checking its own part.
EVERY_SHARD = ("uninstall-files",)
SHARD = re.compile(r"^([1-9][0-9]*)/([1-9][0-9]*)$")
WEIGHT = re.compile(r"^([a-z0-9-]+)\s+([0-9]+)$")
WEIGHTS_HEADER = """\
# How long each section of tests/smoke.sh takes, in seconds: what `tests/smoke.sh --shard K/N` balances
# its shards by (tools/smoke_shard.py). From the last time each section passed in a run on one machine;
# only the ratios matter. A section not listed counts as the median of these. To measure them again, run
# the whole suite, then:
#   python3 tools/smoke_shard.py --script tests/smoke.sh --weights tests/smoke_weights.txt \\
#     --record tests/.smoke-cache/durations
"""


class ShardError(Exception):
    """The split cannot be made: the shard runs every section it was given."""


@dataclass(frozen=True)
class Shard:
    own: tuple[str, ...]  # its sections, in file order
    needed: tuple[str, ...]  # what they need that is not its own, in file order
    seconds: int  # the weights of both


def parse_shard(text: str) -> tuple[int, int]:
    """`K/N` as (K, N): 1 <= K <= N."""
    match = SHARD.match(text)
    if match is None or int(match[1]) > int(match[2]):
        raise ShardError(f"--shard wants K/N with 1 <= K <= N, not {text!r}")
    return int(match[1]), int(match[2])


def parse_weights(text: str) -> dict[str, int]:
    weights: dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = WEIGHT.match(line)
        if match is None:
            raise ShardError(f"weights file, line {number}: want <section id> <seconds>, not: {raw}")
        weights[match[1]] = int(match[2])
    return weights


def read_weights(path: Path) -> dict[str, int]:
    """The weights file's; none when there is no file (every section then counts the same)."""
    return parse_weights(path.read_text(encoding="utf-8")) if path.is_file() else {}


def requirements(sections: Sequence[Section]) -> dict[str, frozenset[str]]:
    """For each section, every section it needs, and what those need in turn."""
    needs = {s.id: s.needs for s in sections}
    found: dict[str, frozenset[str]] = {}
    for section in sections:
        want: set[str] = set()
        queue = list(section.needs)
        while queue:
            need = queue.pop()
            if need not in want:
                want.add(need)
                queue.extend(needs.get(need, ()))
        found[section.id] = frozenset(want)
    return found


class Split:
    """The shards being filled: what each runs, and what of it is its own."""

    def __init__(self, sections: Sequence[Section], weights: Mapping[str, int], count: int) -> None:
        self.order = {s.id: i for i, s in enumerate(sections)}
        self.needs = requirements(sections)
        self.weights = weights
        self.fallback = round(statistics.median(weights.values())) if weights else 1
        self.own: list[set[str]] = [set() for _ in range(count)]
        self.runs: list[set[str]] = [set() for _ in range(count)]
        self.share = 0.0  # what a shard may take before a section fits in it no more (aim_at)

    def aim_at(self, placed: Sequence[str]) -> None:
        """An even share of <placed>: their time with what they need over the shards, or the longest one's."""
        whole = self.cost(set().union(*(self.with_needs(s) for s in placed)))
        self.share = max([whole / len(self.own), *(self.cost(self.with_needs(s)) for s in placed)])

    def cost(self, ids: set[str] | frozenset[str]) -> int:
        return sum(self.weights.get(i, self.fallback) for i in ids)

    def with_needs(self, section: str) -> frozenset[str]:
        return self.needs[section] | {section}

    def choice(self, section: str, shard: int) -> tuple[int, ...]:
        """How good a place <shard> is for <section>, the least winning: a shard it leaves within the share
        before one it does not; among those, what it adds, then the shard's length after; among these, the
        longest shard after, then what it adds and the length; then the shard's number."""
        after = [self.cost(r) for r in self.runs]
        added = self.cost(self.with_needs(section) - self.runs[shard])
        after[shard] += added
        if after[shard] <= self.share:
            return 0, added, after[shard], shard
        return 1, max(after), added, after[shard], shard

    def place(self, section: str, shard: int) -> None:
        self.own[shard].add(section)
        self.runs[shard] |= self.with_needs(section)

    def fill(self, placed: Sequence[str]) -> None:
        """Each of <placed> in the shard that suits it best, the longest first."""
        self.aim_at(placed)
        for section in sorted(placed, key=lambda s: (-self.cost(self.with_needs(s)), self.order[s])):
            self.place(section, min(range(len(self.own)), key=lambda shard: self.choice(section, shard)))

    def spread(self, sections: Sequence[str]) -> None:
        """Each of <sections> in every shard that has a section of its own (the first, if none has)."""
        busy = [shard for shard, own in enumerate(self.own) if own] or [0]
        for section in sections:
            for shard in busy:
                self.place(section, shard)

    def shards(self) -> list[Shard]:
        return [
            Shard(self.in_order(own), self.in_order(self.runs[shard] - own), self.cost(self.runs[shard]))
            for shard, own in enumerate(self.own)
        ]

    def in_order(self, ids: set[str]) -> tuple[str, ...]:
        return tuple(sorted(ids, key=self.order.__getitem__))


def split(
    sections: Sequence[Section], chosen: Sequence[str], weights: Mapping[str, int], count: int
) -> list[Shard]:
    """The <count> shards of the <chosen> sections (each in one shard, and EVERY_SHARD's in every one that
    runs anything), balanced by <weights>."""
    plan = Split(sections, weights, count)
    wanted = list(dict.fromkeys(chosen))
    unknown = [c for c in wanted if c not in plan.order]
    if unknown:
        raise ShardError(f"no section {', '.join(unknown)}")
    plan.fill([c for c in wanted if c not in EVERY_SHARD])
    plan.spread([c for c in wanted if c in EVERY_SHARD])
    return plan.shards()


def answer(shards: Sequence[Shard], number: int, chosen: int, by: str) -> list[str]:
    """The report on shard <number> (1-based) of <shards>, of <chosen> sections, weighed <by>, ending in the
    answer."""
    shard = shards[number - 1]
    lengths = ", ".join(f"{s.seconds} s" for s in shards)
    line = (
        f"smoke shard {number} of {len(shards)}: {len(shard.own)} of the {chosen} selected section(s), "
        f"about {shard.seconds} s by {by} (the shards: {lengths})"
    )
    return [line, "=> only " + ",".join(shard.own) if shard.own else "=> none"]


def shard_report(script: Path, weights_path: Path, shard: str, sections_arg: str | None) -> list[str]:
    """The report for one shard. Whatever goes wrong is every section given, in this shard."""
    try:
        number, count = parse_shard(shard)
        sections = parse_sections(script.read_text(encoding="utf-8"))
        if not sections:
            raise ShardError(f"no sections found in {script}")
        chosen = [s for s in (sections_arg or "").split(",") if s] or [s.id for s in sections]
        shards = split(sections, chosen, read_weights(weights_path), count)
        by = weights_path.name if weights_path.is_file() else f"count ({weights_path.name} is missing)"
        return answer(shards, number, len(dict.fromkeys(chosen)), by)
    except Exception as error:  # noqa: BLE001 - failing closed is the point: any trouble runs them all here
        return [f"smoke shard {shard}: no split ({error}): this shard runs every selected section", "=> all"]


def refreshed(sections: Sequence[Section], old: Mapping[str, int], record: Mapping[str, int]) -> str:
    """The weights file: what the record measured (at least a second), else the old weight, for each section
    there is, in file order."""
    lines = []
    for section in sections:
        if section.id in record:
            lines.append(f"{section.id} {max(1, record[section.id])}")
        elif section.id in old:
            lines.append(f"{section.id} {old[section.id]}")
    return WEIGHTS_HEADER + "".join(f"{line}\n" for line in lines)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--shard", help="K/N")
    task.add_argument("--record", type=Path, help="the durations a run recorded")
    parser.add_argument("--sections", help="id,id,... (default: every section)")
    args = parser.parse_args(argv)
    if args.record is not None:
        sections = parse_sections(args.script.read_text(encoding="utf-8"))
        record = parse_weights(args.record.read_text(encoding="utf-8"))
        args.weights.write_text(refreshed(sections, read_weights(args.weights), record), encoding="utf-8")
        return 0
    print("\n".join(shard_report(args.script, args.weights, args.shard, args.sections)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
