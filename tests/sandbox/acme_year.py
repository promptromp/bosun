"""A year of acme work in the ledger and the knowledge base, for the update's freeze to be measured on
(tests/work_e2e.sh, "a mid-afternoon update"): what a working day of tests/sandbox/acme_week.sh writes,
day after day, through the installed bosun's own commands, so both journals, ledger.json, the knowledge
files and the knowledge folder's git history grow as a year of use grows them. Each day a review
request and the Slack messages that asked for it, a task taken on, handed over, worked, reported and
done (with outreach some days, a proposal approved others, one in five left open), the day's prune; in
the knowledge base a product fact from two sources, a rule that waits for review and is confirmed or
rejected, some days a new feature, a correction or a preference a person stated.

Usage: acme_year.py <the installed bosun's lib folder> <days>

It runs in one process, calling the commands' own main functions, with the settings `bosun ledger` and
`bosun kg` pass in its environment. Only flushing to disk is left out (journal.flush_to_disk and
flush_folder), which changes no byte written and makes a year take minutes. Prints what it made and
how long it took."""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path


SLACK = "https://acme.slack.com/archives/C00000000"
FACT_ID = re.compile(r"^ok: (f-[0-9a-hjkmnp-tv-z]{6})\b", re.MULTILINE)


class Year:
    """The commands, run in this process, and what they printed."""

    def __init__(self, ledger_main: Callable[[list[str]], int], kg_main: Callable[[list[str]], int]) -> None:
        self.ledger_main = ledger_main
        self.kg_main = kg_main
        self.writes = {"ledger": 0, "kg": 0}

    def _run(self, store: str, main: Callable[[list[str]], int], argv: list[str]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(argv)
        if code != 0:
            raise SystemExit(f"acme_year.py: `bosun {store} {' '.join(argv)}` failed (exit {code})")
        self.writes[store] += 1
        return out.getvalue()

    def ledger(self, *argv: str) -> str:
        return self._run("ledger", self.ledger_main, list(argv))

    def kg(self, *argv: str) -> str:
        return self._run("kg", self.kg_main, list(argv))


def permalink(day: int, n: int) -> str:
    return f"{SLACK}/p{1710000000 + day * 100 + n}000100"


def review(year: Year, day: int) -> str:
    item, url = f"review:acme/api#{1000 + day}", f"https://github.com/acme/api/pull/{1000 + day}"
    year.ledger(
        "upsert", item, "--kind", "review-request", "--title", f"Retry export batch {day}", "--url", url
    )
    year.ledger("record-source", permalink(day, 1), "--class", "pr-review", "--item", item)
    year.ledger("record-source", permalink(day, 2), "--class", "fyi")
    return item


def outreach(year: Year, day: int, task: str) -> str:
    subject = f"Which totals should export batch {day} show?"
    out = year.ledger("reach-out", task, "--to", "Dana (finance)", "--subject", subject).splitlines()[0]
    year.ledger("sent", out, "--ref", permalink(day, 3), "--at", now())
    reply = ("--evidence", permalink(day, 4), "--summary", "Net totals, in UTC", "--at", now())
    year.ledger("replied", out, *reply)
    year.ledger("transition", out, "applied", "--note", "the export shows net totals")
    return out


def proposal(year: Year, day: int, task: str) -> None:
    year.ledger("propose", task, "--run", f"gh pr create --repo acme/api --title 'Export check {day}'")
    year.ledger("approve", task, "--quote", f"yes, open the PR for export check {day}")


def finish(year: Year, day: int, task: str, proposed: bool) -> None:
    report = (
        "--summary",
        f"Export check {day} done",
        "--result",
        f"https://github.com/acme/api/pull/{2000 + day}",
    )
    if proposed:
        year.ledger("report", task, *report, "--ci", "green")
        year.ledger("resolve", task, "done", "--note", "the PR is open")
        year.ledger("transition", task, "done", "--note", "merged")
    else:
        year.ledger("report", task, *report, "--ci", "green", "--state", "done")


def task_of_the_day(year: Year, day: int) -> str:
    title, ask = f"Export check {day}", f"Check export batch {day}"
    task = year.ledger("spawn", "--title", title, "--ask", ask, "--via", f"user: {ask.lower()}").splitlines()[
        0
    ]
    year.ledger("link", task, "part_of", "invoice-exports")
    year.ledger(
        "transition", task, "accepted", "--note", "accepted in chat", "--evidence", "user: yes, do it"
    )
    year.ledger("record-delegation", task, "--window", "acme-api", "--task", "new-work", "--ask", title)
    year.ledger("working", task, "--window", "acme-api")
    for run in range(1, 5):
        year.ledger("note", task, f"CI run {run} on export check {day}: green")
    return task


def ledger_day(year: Year, day: int) -> str:
    item = review(year, day)
    task = task_of_the_day(year, day)
    if day % 3 == 0:
        outreach(year, day, task)
    proposed = day % 2 == 0
    if proposed:
        proposal(year, day, task)
    if day % 5 != 0:  # one in five stays open
        finish(year, day, task, proposed)
    year.ledger("transition", item, "reviewed", "--note", "two comments on the backoff")
    year.ledger("transition", item, "closed", "--note", "merged upstream")
    year.ledger("prune", "--days", "0")
    return task


def fact_id(said: str) -> str:
    found = FACT_ID.search(said)
    if not found:
        raise SystemExit(f"acme_year.py: no fact id in: {said.strip()}")
    return found[1]


def knowledge_day(year: Year, day: int, task: str, caveats: list[str]) -> None:
    sources = ("--source", f"ledger:{task}", "--source", f"report:{task}")
    caveat = f"Export batch {day} keeps the invoices in date order"
    caveats.append(fact_id(year.kg("propose", "feature/csv-export", "caveat", "--value", caveat, *sources)))
    rule = f"Export batch {day} runs after the nightly close"
    proposed = fact_id(year.kg("propose", "repo/invoice-exporter", "rule", "--value", rule, *sources[:2]))
    year.kg("confirm" if day % 2 else "reject", proposed)
    if day % 7 == 0:
        feature = f"feature/export-{day}"
        year.kg("add-entity", feature, "--name", f"Export feature {day}")
        year.kg("propose", feature, "caveat", "--value", f"Export feature {day} pages by 500", *sources)
    if day % 10 == 0 and caveats:
        corrected = f"Export batch {day}: invoices in date order, then by id"
        year.kg("supersede", caveats.pop(0), "--value", corrected, *sources[2:])
    if day % 15 == 0:
        said = ("--authority", "stakeholder_said", *sources[:2])
        year.kg(
            "propose",
            "person/dana",
            "stated_preference",
            "--value",
            f"Wants batch {day} totals by noon",
            *said,
        )


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sizes(state: Path, config: Path) -> str:
    journal = sorted((state / "journal" / "ledger").glob("*.jsonl"))
    kg_journal = sorted((config / "knowledge" / "journal").glob("*.jsonl"))
    lines = sum(len(p.read_bytes().splitlines()) for p in journal)
    kg_lines = sum(len(p.read_bytes().splitlines()) for p in kg_journal)
    git_files = sum(len(files) for _, _, files in os.walk(config / "knowledge" / ".git"))
    ledger_kb = (state / "ledger.json").stat().st_size // 1024
    facts_kb = (config / "knowledge" / "facts.jsonl").stat().st_size // 1024
    return (
        f"ledger.json {ledger_kb} KB; the ledger's journal {lines} changes, "
        f"{sum(p.stat().st_size for p in journal) // 1024} KB; facts.jsonl {facts_kb} KB; "
        "the knowledge journal "
        f"{kg_lines} changes, {sum(p.stat().st_size for p in kg_journal) // 1024} KB; "
        f"its git history {git_files} files"
    )


def main(argv: list[str]) -> int:
    lib, days = Path(argv[0]), int(argv[1])
    sys.path.insert(0, str(lib))
    import journal  # the installed bosun's
    import knowledge
    import ledger

    journal.flush_to_disk = lambda fd: None
    journal.flush_folder = lambda folder: None
    year, caveats, started = Year(ledger.main, knowledge.main), list[str](), time.monotonic()
    for day in range(1, days + 1):
        task = ledger_day(year, day)
        knowledge_day(year, day, task, caveats)
    took = round(time.monotonic() - started)
    state, config = Path(os.environ["BOSUN_STATE_DIR"]), Path(os.environ["BOSUN_CONFIG_DIR"])
    writes = f"{year.writes['ledger']} ledger writes and {year.writes['kg']} knowledge writes"
    print(f"{days} days: {writes} in {took} s")
    print(sizes(state, config))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
