#!/usr/bin/env python3
"""Learning: look back over recent work and propose what should change.

Usage: learn.py run [--since 24h|3d|<ISO time>] [--until <ISO time>] [--dry-run | --collect-only]
                    [--scheduled] [--out DIR] --sessions FILE
       learn.py status
       learn.py apply <learn-id>
       learn.py done <learn-id> [--note N]
       learn.py reject <learn-id> [--note N]

Run it as `bosun learn`, which passes the settings in the environment and the windows
(`bosun sessions --json`) in a file.

A run has two stages. lib/learn_digest.py turns the window's sessions, ledger, logs and watchdog
alerts into a compact, redacted digest. Then one headless Claude, with no tools and no MCP servers,
reads it with the prompt in orchestrator/learn.md (plus $BOSUN_CONFIG_DIR/learn.md) and returns
findings as JSON: each with a category, a stable key, evidence quoted from the digest and, if
something should change, one proposal. Findings whose evidence is not in the digest are dropped.

Nothing is applied on its own. New proposals (at most BOSUN_LEARN_MAX_PROPOSALS a run) become
`learn:<YYYY-MM-DD>/<n>` ledger items with the exact change as their pending proposal, so the
orchestrator lists them under "Needs your approval". `apply` makes an approved change: a memory note
for a window's folder, a rule appended to the site policy ($BOSUN_CONFIG_DIR/policy.md) or to
the site's shared task rules ($BOSUN_CONFIG_DIR/tasks/_common.md). Other changes (to this repo,
sessions.yml, outreach) you make yourself, then `done`. `reject` declines one; it is never proposed
again. Every finding is remembered by key in $BOSUN_STATE_DIR/learn/state.json, so a later run
counts it again instead of proposing it twice.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_state
import learn_digest
import ledger
from learn_digest import Window


ROOT = Path(__file__).resolve().parent.parent
PROMPT_FILE = ROOT / "orchestrator" / "learn.md"
STATE_SUBDIR = "learn"
STATE_FILE = "state.json"
LOCK_FILE = "learn.lock"
DAY_LETTERS = "MTWRFSU"
STAMP = "%Y-%m-%d %H:%M:%S"
RUN_STAMP = "%Y%m%d-%H%M%S"
SECONDS_PER_MINUTE = 60
KEPT_RUNS = 30
RUN_RETENTION_DAYS = 14  # run folders hold excerpts of conversations; they go after this
KEY_LENGTH = 60
TEXT_LENGTH = 1500
QUOTE_SEGMENT_MIN = 4
ID_PREFIX = "learn:"
KIND = "learning"
SEVERITIES = ("high", "medium", "low")
# The cost of a run, estimated before the call: Claude Code checks --max-budget-usd only after a
# turn, so one large digest can cost far more than the budget (a 1.1 MB digest cost $4.05 against
# $2). A one-shot call pays its whole prompt as a cache write.
CHARS_PER_TOKEN = 2.3  # measured: a 1.11M-character digest was 477k tokens
INPUT_USD_PER_MTOK = (("haiku", 1.25), ("sonnet", 3.75), ("opus", 7.5))  # by model family
UNKNOWN_MODEL_USD_PER_MTOK = 7.5  # an unknown model is priced like the dearest family
REPLY_USD = 0.5  # the findings and the thinking behind them
TOKENS_PER_MTOK = 1_000_000
CATEGORIES = (
    "mishap",
    "stall",
    "gap",
    "duplicate",
    "repeated-correction",
    "prompt-gap",
    "setup",
    "outreach",
    "memory",
    "ledger-hygiene",
)
# Where a change goes. `apply` makes the first three; the others are done by hand.
TARGETS = ("memory", "policy", "task-rule", "repo-change", "setup", "outreach", "ledger", "none")
APPLIED_BY_HAND = ("repo-change", "setup", "outreach", "ledger")
TARGET_TEXT = {
    "memory": "a memory note for the {window} window's folder ({harness})",
    "policy": "a rule appended to the site policy, $BOSUN_CONFIG_DIR/policy.md",
    "task-rule": "a rule for every delegated task, appended to $BOSUN_CONFIG_DIR/tasks/_common.md",
    "repo-change": "a change to the bosun repo (you make and commit it)",
    "setup": "a change to the tmux setup: sessions.yml or bosun.env (you make it)",
    "outreach": "a message to someone (tell the orchestrator, which proposes it as outreach)",
    "ledger": "a correction in the ledger (tell the orchestrator)",
}
CLOSED_STATUSES = ("proposed", "applied", "rejected")
SEVERITY_RANK = {severity: rank for rank, severity in enumerate(SEVERITIES)}

EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": ["session", "ledger", "log", "watchdog"]},
        "ref": {"type": "string", "description": "window name, ledger id or log file"},
        "at": {"type": "string", "description": "day and time as the digest shows them"},
        "quote": {"type": "string", "description": "a verbatim part of one digest line"},
    },
    "required": ["source", "ref", "quote"],
}
PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string", "enum": list(TARGETS)},
        "window": {"type": "string", "description": "for memory: whose folder (default: the orchestrator)"},
        "text": {"type": "string", "maxLength": TEXT_LENGTH},
        "rationale": {"type": "string"},
    },
    "required": ["target", "text", "rationale"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "evidence": {"type": "array", "items": EVIDENCE_SCHEMA, "minItems": 1},
                    "proposal": PROPOSAL_SCHEMA,
                },
                "required": ["key", "category", "severity", "title", "summary", "evidence"],
            },
        },
    },
    "required": ["summary", "findings"],
}


class LearnError(Exception):
    pass


# ---- settings ----------------------------------------------------------------------------------


def parse_hhmm(value: str) -> time:
    try:
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise LearnError(f"BOSUN_LEARN_AT must be HH:MM, got {value!r}") from exc


@dataclass(frozen=True)
class Config:
    state_dir: Path
    log_dir: Path
    config_dir: Path
    orchestrator: str = "orchestrator"
    drain_prompt: str = ""
    claude_bin: str = "claude"
    model: str = "opus"
    effort: str = "medium"
    max_budget_usd: float = 2.0
    max_digest_kb: float = 400.0
    max_proposals: int = 5
    max_events: int = 250
    hours: float = 24.0
    timeout_minutes: float = 30.0
    exclude: frozenset[str] = frozenset()
    enabled: bool = False
    at: time = time(18, 30)
    days: str = "MTWRF"

    @property
    def learn_dir(self) -> Path:
        return self.state_dir / STATE_SUBDIR

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        def text(name: str, default: str = "") -> str:
            return env.get(f"BOSUN_{name}", default)

        def number(name: str, default: float) -> float:
            try:
                return float(text(name, str(default)))
            except ValueError as exc:
                raise LearnError(f"BOSUN_{name} must be a number, got {text(name)!r}") from exc

        if not text("STATE_DIR"):
            raise LearnError("BOSUN_STATE_DIR is not set; run this as `bosun learn`")
        state_dir = Path(text("STATE_DIR"))
        return cls(
            state_dir=state_dir,
            log_dir=Path(text("LOG_DIR", str(state_dir / "logs"))),
            config_dir=Path(text("CONFIG_DIR", str(Path.home() / ".config" / "bosun"))),
            orchestrator=text("ORCHESTRATOR"),
            drain_prompt=text("DRAIN_PROMPT"),
            claude_bin=text("CLAUDE_BIN", "claude"),
            model=text("LEARN_MODEL", "opus"),
            effort=text("LEARN_EFFORT", "medium"),
            max_budget_usd=number("LEARN_MAX_BUDGET_USD", 2),
            max_digest_kb=number("LEARN_MAX_DIGEST_KB", 400),
            max_proposals=int(number("LEARN_MAX_PROPOSALS", 5)),
            max_events=int(number("LEARN_MAX_EVENTS", 250)),
            hours=number("LEARN_HOURS", 24),
            timeout_minutes=number("LEARN_TIMEOUT_MINUTES", 30),
            exclude=frozenset(text("LEARN_EXCLUDE_SESSIONS").split()),
            enabled=text("LEARN_ENABLED", "0") == "1",
            at=parse_hhmm(text("LEARN_AT", "18:30")),
            days=text("DAYS", "MTWRF"),
        )


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse_moment(value: str, reference: datetime) -> datetime:
    """48h, 3d, 90m (back from <reference>) or an ISO time (local if it has no zone)."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([mhd])", value.strip())
    if match:
        unit = {"m": "minutes", "h": "hours", "d": "days"}[match[2]]
        return reference - timedelta(**{unit: float(match[1])})
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LearnError(f"expected 48h, 3d or an ISO time, not {value!r}") from exc
    return moment if moment.tzinfo else moment.astimezone()


def log(config: Config | None, message: str) -> None:
    """A stamped line on stdout and, unless <config> is None (dry runs), in the day's learn log."""
    line = f"{datetime.now():{STAMP}} [learn] {message}"
    print(line, flush=True)
    if config is not None:
        config.log_dir.mkdir(parents=True, exist_ok=True)
        with (config.log_dir / f"learn-{datetime.now():%Y-%m-%d}.log").open("a") as fh:
            fh.write(line + "\n")


# ---- state -------------------------------------------------------------------------------------


@dataclass
class State:
    runs: list[dict[str, Any]] = field(default_factory=list)  # newest last
    running: dict[str, Any] | None = None  # pid, started
    findings: dict[str, dict[str, Any]] = field(default_factory=dict)  # key -> what we know of it
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)  # learn id -> its change

    @classmethod
    def load(cls, learn_dir: Path) -> State:
        try:
            data = json.loads((learn_dir / STATE_FILE).read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            raise LearnError(f"{learn_dir / STATE_FILE} is unreadable ({exc}); move it aside") from exc
        return cls(
            runs=list(data.get("runs") or []),
            running=data.get("running"),
            findings=dict(data.get("findings") or {}),
            proposals=dict(data.get("proposals") or {}),
        )

    def save(self, learn_dir: Path) -> None:
        learn_dir.mkdir(parents=True, exist_ok=True)
        self.runs = self.runs[-KEPT_RUNS:]
        tmp = learn_dir / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        tmp.replace(learn_dir / STATE_FILE)

    def last_ok(self) -> dict[str, Any] | None:
        return next((r for r in reversed(self.runs) if r.get("outcome") == "ok"), None)


@contextlib.contextmanager
def state_lock(learn_dir: Path) -> Iterator[None]:
    """One run, apply or close at a time."""
    learn_dir.mkdir(parents=True, exist_ok=True)
    with (learn_dir / LOCK_FILE).open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LearnError("another `bosun learn` is running") from None
        yield


def update_state(learn_dir: Path, change: Callable[[State], None]) -> State:
    state = State.load(learn_dir)
    change(state)
    state.save(learn_dir)
    return state


# ---- findings ----------------------------------------------------------------------------------


@dataclass
class Finding:
    key: str
    category: str
    severity: str
    title: str
    summary: str
    evidence: list[dict[str, str]]
    proposal: dict[str, str] | None = None
    seen_before: int = 0  # times earlier runs saw it
    status: str = "new"  # new, or what happened to it before (reported, proposed, applied, rejected)


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:KEY_LENGTH].strip("-")


def squeeze(text: str) -> str:
    return " ".join(text.split())


def grounded(quote: str, digest: str) -> bool:
    """Whether every part of <quote> (split at ellipses) appears in the digest, spaces aside."""
    parts = [p.strip() for p in re.split(r"\.\.\.|…", squeeze(quote))]
    parts = [p for p in parts if len(p) >= QUOTE_SEGMENT_MIN]
    return bool(parts) and all(p in digest for p in parts)


def grounded_evidence(raw: dict[str, Any], digest: str) -> list[dict[str, str]]:
    evidence = [e for e in raw.get("evidence") or [] if isinstance(e, dict)]
    return [
        {str(k): squeeze(str(v)) for k, v in e.items()}
        for e in evidence
        if grounded(str(e.get("quote", "")), digest)
    ]


def to_finding(raw: dict[str, Any], digest: str) -> Finding | str:
    """A checked finding, or why it was dropped."""
    title = squeeze(str(raw.get("title", "")))[:200]
    evidence = grounded_evidence(raw, digest)
    if not evidence:
        return f"{title or raw.get('key')}: none of its evidence is in the digest"
    if raw.get("category") not in CATEGORIES or raw.get("severity") not in SEVERITIES:
        return f"{title}: unknown category or severity"
    return Finding(
        key=slug(str(raw.get("key") or title)) or "finding",
        category=str(raw["category"]),
        severity=str(raw["severity"]),
        title=title,
        summary=squeeze(str(raw.get("summary", ""))),
        evidence=evidence,
        proposal=clean_proposal(raw.get("proposal")),
    )


def clean_proposal(proposal: object) -> dict[str, str] | None:
    """The proposal with a known target and some text, else None (the finding stays)."""
    if (
        not isinstance(proposal, dict)
        or proposal.get("target") not in TARGETS
        or proposal["target"] == "none"
    ):
        return None
    cleaned = {str(k): str(v).strip()[:TEXT_LENGTH] for k, v in proposal.items()}
    return cleaned if cleaned.get("text") else None


def check_output(output: Any, digest: str) -> tuple[str, list[Finding], list[str]]:
    """The summary, the findings that hold up, and the ones dropped (with why)."""
    if not isinstance(output, dict) or not isinstance(output.get("findings"), list):
        raise LearnError("the analysis returned no findings list")
    flat = squeeze(digest)
    merged: dict[str, Finding] = {}
    dropped = []
    for raw in output["findings"]:
        result = to_finding(raw, flat) if isinstance(raw, dict) else "not an object"
        if isinstance(result, str):
            dropped.append(result)
        elif result.key in merged:
            merged[result.key].evidence += result.evidence
        else:
            merged[result.key] = result
    return squeeze(str(output.get("summary", ""))), list(merged.values()), dropped


def remember(state: State, findings: list[Finding], at: str) -> None:
    """Count each finding in the registry and note what happened to it before."""
    for finding in findings:
        known = state.findings.get(finding.key)
        if known:
            finding.seen_before = int(known.get("times_seen", 1))
            finding.status = str(known.get("status", "reported"))
            known.update(times_seen=finding.seen_before + 1, last_seen=at)
        else:
            state.findings[finding.key] = {
                "title": finding.title,
                "category": finding.category,
                "first_seen": at,
                "last_seen": at,
                "times_seen": 1,
                "status": "reported",
            }


def to_propose(findings: list[Finding], limit: int) -> list[Finding]:
    """New proposals: findings with a change that was never proposed, most severe first."""
    fresh = [f for f in findings if f.proposal and f.status not in CLOSED_STATUSES]
    return sorted(fresh, key=lambda f: (SEVERITY_RANK[f.severity], -f.seen_before))[: max(0, limit)]


def known_findings(state: State) -> tuple[dict[str, Any], ...]:
    return tuple({"key": key, **info} for key, info in sorted(state.findings.items()))


def sync_statuses(state: State) -> None:
    """Take each proposal's outcome from the ledger (I may have answered it any way)."""
    try:
        items = ledger.load()["items"]
    except ledger.LedgerError:
        return
    for learn_id, change in state.proposals.items():
        item = items.get(learn_id)
        known = state.findings.get(change.get("key", ""))
        if item and known and item["state"] in ("applied", "rejected", "dismissed"):
            known["status"] = "rejected" if item["state"] == "dismissed" else item["state"]


# ---- the analysis ------------------------------------------------------------------------------


def front_matter_free(text: str) -> str:
    return text.split("\n---\n", 1)[1] if text.startswith("---\n") else text


def prompt_text(config: Config) -> str:
    prompt = front_matter_free(PROMPT_FILE.read_text()).strip()
    site = config.config_dir / "learn.md"
    if site.is_file():
        prompt += f"\n\n# Site additions ({site})\n\n{site.read_text().strip()}"
    return prompt + "\n"


def claude_command(config: Config, prompt: str) -> list[str]:
    """One headless Claude with no tools, no MCP servers and no permission prompts."""
    return [
        config.claude_bin, "-p", "--model", config.model,
        "--settings", json.dumps({"effortLevel": config.effort}),
        "--strict-mcp-config", "--tools", "", "--permission-mode", "dontAsk",
        "--permission-prompts", "none", "--no-session-persistence", "--output-format", "json",
        "--max-budget-usd", f"{config.max_budget_usd:g}", "--json-schema", json.dumps(SCHEMA),
        "--system-prompt", prompt,
    ]  # fmt: skip


def digest_message(digest: str) -> str:
    return f"Here is the digest to analyse.\n\n<digest>\n{digest}</digest>\n"


def estimated_cost(model: str, chars: int) -> float:
    """What a call with <chars> of prompt costs, about (see CHARS_PER_TOKEN)."""
    rate = next(
        (usd for family, usd in INPUT_USD_PER_MTOK if family in model.lower()), UNKNOWN_MODEL_USD_PER_MTOK
    )
    return chars / CHARS_PER_TOKEN / TOKENS_PER_MTOK * rate + REPLY_USD


def check_affordable(config: Config, estimate: float, digest_chars: int) -> None:
    if estimate > config.max_budget_usd:
        raise LearnError(
            f"the analysis would cost about ${estimate:.2f}, over the budget of ${config.max_budget_usd:.2f} "
            f"(the digest is {digest_chars // learn_digest.BYTES_PER_KB} KB); not calling Claude. Shorten "
            "the window (--since), lower BOSUN_LEARN_MAX_DIGEST_KB or raise "
            "BOSUN_LEARN_MAX_BUDGET_USD"
        )


def failure_text(result: dict[str, Any], config: Config, run_dir: Path) -> str:
    cost = float(result.get("total_cost_usd") or 0)
    subtype = result.get("subtype", "no structured output")
    over = f", over the budget of ${config.max_budget_usd:.2f}" if subtype == "error_max_budget_usd" else ""
    return f"the analysis failed ({subtype}; it cost ${cost:.2f}{over}); see {run_dir}"


def analyze(config: Config, digest: str, run_dir: Path) -> dict[str, Any]:
    """Run the analysis; returns Claude's JSON result (structured_output holds the findings)."""
    prompt = prompt_text(config)
    (run_dir / "prompt.md").write_text(prompt)
    message = digest_message(digest)
    try:
        done = subprocess.run(
            claude_command(config, prompt),
            input=message,
            capture_output=True,
            text=True,
            cwd=run_dir,
            timeout=config.timeout_minutes * SECONDS_PER_MINUTE,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LearnError(f"the analysis took longer than {config.timeout_minutes:g} minutes") from exc
    (run_dir / "result.json").write_text(done.stdout)
    (run_dir / "stderr.log").write_text(done.stderr)
    try:
        result: dict[str, Any] = json.loads(done.stdout[done.stdout.index("{") :])
    except ValueError as exc:
        raise LearnError(f"claude exited {done.returncode} without a JSON result; see {run_dir}") from exc
    if result.get("is_error") or not isinstance(result.get("structured_output"), dict):
        raise LearnError(failure_text(result, config, run_dir))
    return result


# ---- proposals in the ledger -------------------------------------------------------------------


def ledger_call(*argv: str) -> str:
    """A ledger command, in this process, with the ledger's own validation."""
    args = ledger.build_parser().parse_args(list(argv))
    try:
        result = args.func(args)
    except ledger.LedgerError as exc:
        raise LearnError(f"ledger: {exc}") from exc
    return str(result[0] if isinstance(result, tuple) else result)


def next_id(today: date) -> str:
    items = ledger.load()["items"]
    prefix = f"{ID_PREFIX}{today:%Y-%m-%d}/"
    numbers = [int(i[len(prefix) :]) for i in items if i.startswith(prefix) and i[len(prefix) :].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1}"


def target_dir(finding: Finding, windows: list[Window], orchestrator: str) -> tuple[str, str]:
    """The window (and its folder) a memory note is for: the one named, else the orchestrator."""
    wanted = (finding.proposal or {}).get("window") or orchestrator
    window = next((w for w in windows if w.name == wanted), None) or next(
        (w for w in windows if w.kind == "orchestrator"), None
    )
    return (window.name, window.dir) if window else ("", "")


def evidence_line(evidence: dict[str, str]) -> str:
    when = f" {evidence['at']}" if evidence.get("at") else ""
    return f'{evidence.get("source", "?")} {evidence.get("ref", "?")}{when}: "{evidence.get("quote", "")}"'


def action_text(learn_id: str, finding: Finding, change: dict[str, Any]) -> str:
    proposal = finding.proposal or {}
    where = TARGET_TEXT[change["target"]].format(window=change.get("window") or "?", harness="Claude Code")
    how = (
        f"do it, then: bosun learn done '{learn_id}'"
        if change["target"] in APPLIED_BY_HAND
        else f"bosun learn apply '{learn_id}'"
    )
    seen = f" (seen {finding.seen_before + 1}x)" if finding.seen_before else ""
    return "\n".join(
        [
            f"[learn: {change['target']}] {finding.title}{seen}",
            f"Change: {where}:",
            proposal.get("text", ""),
            f"Why: {proposal.get('rationale', '')}",
            f"Evidence: {evidence_line(finding.evidence[0])}",
            f"If yes: {how}. If no: bosun learn reject '{learn_id}' --note '<why>'",
        ]
    )


def record_proposal(
    finding: Finding, windows: list[Window], config: Config, run: str
) -> tuple[str, dict[str, Any]]:
    """A learn: ledger item whose pending proposal is the exact change; returns its id and change."""
    learn_id = next_id(datetime.now().date())
    target = (finding.proposal or {})["target"]
    window, folder = target_dir(finding, windows, config.orchestrator) if target == "memory" else ("", "")
    change = {
        "key": finding.key,
        "target": target,
        "title": finding.title,
        "text": (finding.proposal or {})["text"],
        "window": window,
        "dir": folder,
        "run": run,
    }
    ledger_call(
        "upsert", learn_id, "--kind", KIND, "--title", finding.title,
        "--field", f"key={finding.key}", "--field", f"category={finding.category}",
        "--field", f"target={target}", "--field", f"run={run}",
    )  # fmt: skip
    ledger_call("propose", learn_id, action_text(learn_id, finding, change))
    return learn_id, change


# ---- the report --------------------------------------------------------------------------------


def finding_lines(finding: Finding, proposed: dict[str, str]) -> list[str]:
    history = f"seen before {finding.seen_before}x, {finding.status}" if finding.seen_before else "new"
    lines = [
        f"- **{finding.title}** ({finding.severity}; `{finding.key}`; {history})",
        f"  {finding.summary}",
        *(f"  - {evidence_line(e)}" for e in finding.evidence),
    ]
    if finding.proposal:
        where = f" -> {proposed[finding.key]}" if finding.key in proposed else ""
        lines.append(
            f"  - proposal ({finding.proposal['target']}){where}: {squeeze(finding.proposal['text'])}"
        )
    return lines


def render_report(
    run: dict[str, Any], summary: str, findings: list[Finding], dropped: list[str], proposed: dict[str, str]
) -> str:
    lines = [
        f"# Learning report {run['started']}",
        "",
        (
            f"Window {run['since']} to {run['until']}; {run.get('sessions', 0)} session(s) read; "
            f"cost ${run.get('cost', 0):.2f}; model {run.get('model', '?')}."
        ),
        "",
        "## Summary",
        "",
        summary or "(none)",
        "",
        "## Proposals waiting for your approval",
        "",
        *([f"- {learn_id}: {key}" for key, learn_id in proposed.items()] or ["- none"]),
    ]
    lines += category_sections(findings, proposed)
    if dropped:
        lines += ["", "## Dropped (evidence not found in the digest)", "", *(f"- {d}" for d in dropped)]
    return "\n".join(lines) + "\n"


def category_sections(findings: list[Finding], proposed: dict[str, str]) -> list[str]:
    lines = []
    for category in CATEGORIES:
        chosen = [f for f in findings if f.category == category]
        if chosen:
            lines += [
                "",
                f"## {category}",
                "",
                *(line for f in chosen for line in finding_lines(f, proposed)),
            ]
    return lines


# ---- a run -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOptions:
    since: str | None = None
    until: str | None = None
    dry_run: bool = False
    collect_only: bool = False
    scheduled: bool = False
    out: Path | None = None


def window_of_run(config: Config, state: State, options: RunOptions) -> tuple[datetime, datetime]:
    until = parse_moment(options.until, now()) if options.until else now()
    if options.since:
        return parse_moment(options.since, until), until
    last = state.last_ok()
    last_until = datetime.fromisoformat(last["until"]) if last else None
    return learn_digest.window_bounds(until, config.hours, last_until)


def digest_settings(config: Config, state: State, since: datetime, until: datetime) -> learn_digest.Settings:
    return learn_digest.Settings(
        since=since,
        until=until,
        state_dir=config.state_dir,
        log_dir=config.log_dir,
        config_dir=config.config_dir,
        root=ROOT,
        orchestrator=config.orchestrator,
        drain_prompt=config.drain_prompt,
        max_events=config.max_events,
        max_chars=int(config.max_digest_kb * learn_digest.BYTES_PER_KB),
        exclude=config.exclude,
        known=known_findings(state),
    )


def make_run_dir(config: Config, options: RunOptions, started: datetime) -> Path:
    base = options.out or config.learn_dir / ("dry-runs" if options.dry_run else "runs")
    base.mkdir(parents=True, exist_ok=True)
    name = f"{started.astimezone():{RUN_STAMP}}"
    for suffix in ("", *(f"-{n}" for n in range(2, 100))):
        with contextlib.suppress(FileExistsError):
            (base / (name + suffix)).mkdir()
            return base / (name + suffix)
    raise LearnError(f"too many runs named {name} in {base}")


def prune_runs(config: Config) -> None:
    cutoff = datetime.now() - timedelta(days=RUN_RETENTION_DAYS)
    for base in (config.learn_dir / "runs", config.learn_dir / "dry-runs"):
        for run_dir in base.glob("*"):
            with contextlib.suppress(ValueError):
                if datetime.strptime(run_dir.name[: len("YYYYmmdd-HHMMSS")], RUN_STAMP) < cutoff:
                    shutil.rmtree(run_dir, ignore_errors=True)


def due_today(config: Config, state: State, moment: datetime) -> bool:
    """For the launchd agent: a scheduled day, past BOSUN_LEARN_AT, and no run since then."""
    local_now = moment.astimezone().replace(tzinfo=None)
    if DAY_LETTERS[local_now.weekday()] not in config.days or local_now.time() < config.at:
        return False
    since = datetime.combine(local_now.date(), config.at)
    started = [datetime.fromisoformat(r["started"]).astimezone().replace(tzinfo=None) for r in state.runs]
    return not any(s >= since for s in started)


@dataclass
class Outcome:
    record: dict[str, Any]
    report: str = ""
    proposed: dict[str, str] = field(default_factory=dict)  # finding key -> learn id


def collect_and_analyze(
    config: Config, options: RunOptions, windows: list[Window], run: dict[str, Any], run_dir: Path
) -> Outcome:
    state = State.load(config.learn_dir)
    ledger.configure(config.state_dir)
    sync_statuses(state)
    since, until = window_of_run(config, state, options)
    run.update(since=iso(since), until=iso(until), model=config.model, run_dir=str(run_dir))
    digest = learn_digest.collect(digest_settings(config, state, since, until), windows)
    (run_dir / "digest.md").write_text(digest.text)
    prompt_chars = len(prompt_text(config)) + len(digest_message(digest.text))
    estimate = estimated_cost(config.model, prompt_chars)
    run.update(sessions=len(digest.sessions), digest_chars=len(digest.text), estimate=round(estimate, 2))
    if options.collect_only:
        return Outcome(run)
    check_affordable(config, estimate, len(digest.text))
    result = analyze(config, digest.text, run_dir)
    run["cost"] = float(result.get("total_cost_usd") or 0)
    summary, findings, dropped = check_output(result["structured_output"], digest.text)
    remember(state, findings, run["started"])
    proposed: dict[str, str] = {}
    if not options.dry_run:
        for finding in to_propose(findings, config.max_proposals):
            learn_id, change = record_proposal(finding, windows, config, run["started"])
            state.proposals[learn_id] = change
            state.findings[finding.key]["status"] = "proposed"
            proposed[finding.key] = learn_id
        state.runs.append(
            {**run, "outcome": "ok", "findings": len(findings), "proposals": list(proposed.values())}
        )
        state.running = None
        state.save(config.learn_dir)
    run.update(findings=len(findings), dropped=len(dropped), proposals=list(proposed.values()))
    return Outcome(run, render_report(run, summary, findings, dropped, proposed), proposed)


def finish(config: Config, outcome: str, run: dict[str, Any], error: str = "") -> None:
    def change(state: State) -> None:
        state.running = None
        if not any(r.get("started") == run["started"] for r in state.runs):
            state.runs.append({**run, "outcome": outcome, **({"error": error} if error else {})})

    update_state(config.learn_dir, change)


def run_tracked(config: Config, options: RunOptions, windows: list[Window]) -> Outcome:
    """A real run: locked, marked as running for the watchdog, recorded however it ends."""
    with state_lock(config.learn_dir):
        state = State.load(config.learn_dir)
        if options.scheduled and not (config.enabled and due_today(config, state, now())):
            return Outcome({"outcome": "skipped"})
        started = now()
        run: dict[str, Any] = {"started": iso(started), "pid": os.getpid()}
        update_state(
            config.learn_dir, lambda s: setattr(s, "running", {"pid": os.getpid(), "started": run["started"]})
        )
        prune_runs(config)
        try:
            result = collect_and_analyze(
                config, options, windows, run, make_run_dir(config, options, started)
            )
        except (LearnError, OSError, subprocess.SubprocessError) as exc:
            finish(config, "failed", run, str(exc))
            log(config, f"ERROR: {exc}")
            raise
        finish(config, "ok", run)
        return result


def run_untracked(config: Config, options: RunOptions, windows: list[Window]) -> Outcome:
    """A dry run or a digest only: no lock, no state, no ledger, no log file."""
    started = now()
    run: dict[str, Any] = {"started": iso(started)}
    return collect_and_analyze(config, options, windows, run, make_run_dir(config, options, started))


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    options = RunOptions(args.since, args.until, args.dry_run, args.collect_only, args.scheduled, args.out)
    windows = Window.from_json(json.loads(Path(args.sessions).read_text()))
    untracked = options.dry_run or options.collect_only
    outcome = (run_untracked if untracked else run_tracked)(config, options, windows)
    run = outcome.record
    if run.get("outcome") == "skipped":
        log(None, "not due (or BOSUN_LEARN_ENABLED is not 1); nothing to do")
        return 0
    run_dir = Path(run["run_dir"])
    if outcome.report:
        (run_dir / "report.md").write_text(outcome.report)
    log_to = None if untracked else config
    if options.collect_only:
        log(
            log_to,
            f"digest of {run['sessions']} session(s), {run['digest_chars']} chars (a run would cost about "
            f"${run['estimate']:.2f} of ${config.max_budget_usd:.2f}): {run_dir / 'digest.md'}",
        )
        return 0
    proposals = ", ".join(run.get("proposals") or []) or "none"
    log(
        log_to,
        f"{'dry run: ' if options.dry_run else ''}{run['findings']} finding(s) ({run['dropped']} dropped), "
        f"new proposals: {proposals}; cost ${run.get('cost', 0):.2f} (estimated ${run['estimate']:.2f}, "
        f"budget ${config.max_budget_usd:.2f}); report: {run_dir / 'report.md'}",
    )
    return 0


# ---- apply, done, reject -----------------------------------------------------------------------


def pending_change(state: State, learn_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    item = ledger.get_item(ledger.load(), learn_id)
    if item["kind"] != KIND:
        raise LearnError(f"{learn_id} is not a learning proposal")
    if item["state"] != "proposed" or not item.get("proposal"):
        raise LearnError(f"{learn_id} is {item['state']}, with no pending proposal")
    change = state.proposals.get(learn_id)
    if change is None:
        raise LearnError(f"{learn_id}: its change is not in {STATE_FILE}; reject it")
    return item, change


def backup(config: Config, learn_id: str, path: Path) -> None:
    if path.is_file():
        folder = config.learn_dir / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, folder / f"{slug(learn_id)}-{path.name}")


def append_rule(config: Config, learn_id: str, path: Path, text: str) -> str:
    """Append <text> under a marker naming <learn_id>; once only."""
    marker = f"<!-- {learn_id} (bosun learn) -->"
    existing = path.read_text() if path.is_file() else ""
    if marker in existing:
        return f"already in {path}"
    backup(config, learn_id, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        existing.rstrip("\n") + ("\n\n" if existing.strip() else "") + f"{marker}\n{text.strip()}\n"
    )
    return f"appended to {path}"


def apply_memory(config: Config, learn_id: str, change: dict[str, Any]) -> str:
    if not change.get("dir"):
        raise LearnError(
            f"{learn_id}: no folder for the memory note (window {change.get('window')!r} is unknown)"
        )
    name = slug(f"bosun-{change['key']}")
    for suffix in ("", "-2", "-3", "-4"):
        with contextlib.suppress(FileExistsError):
            note = claude_state.add_memory_note(change["dir"], name + suffix, change["title"], change["text"])
            return f"memory note {note}"
    raise LearnError(f"{learn_id}: memory notes named {name} already exist")


APPLIERS: dict[str, Callable[[Config, str, dict[str, Any]], str]] = {
    "memory": apply_memory,
    "policy": lambda config, learn_id, change: append_rule(
        config, learn_id, config.config_dir / "policy.md", change["text"]
    ),
    "task-rule": lambda config, learn_id, change: append_rule(
        config, learn_id, config.config_dir / "tasks" / "_common.md", f"- {change['text']}"
    ),
}


def close_item(config: Config, learn_id: str, state_to: str, note: str) -> None:
    try:
        ledger.resolve_proposal(
            learn_id, "done" if state_to == "applied" else "rejected", note, by_learn=True
        )
    except ledger.LedgerError as exc:
        raise LearnError(f"ledger: {exc}") from exc
    ledger_call("transition", learn_id, state_to, "--note", note)

    def change(state: State) -> None:
        key = state.proposals.get(learn_id, {}).get("key", "")
        if key in state.findings:
            state.findings[key]["status"] = state_to

    update_state(config.learn_dir, change)


def cmd_apply(config: Config, args: argparse.Namespace) -> int:
    with state_lock(config.learn_dir):
        _item, change = pending_change(State.load(config.learn_dir), args.id)
        applier = APPLIERS.get(change["target"])
        if applier is None:
            raise LearnError(
                f"{args.id} is a change you make yourself ({TARGET_TEXT[change['target']]}); "
                f"once done: bosun learn done '{args.id}'"
            )
        what = applier(config, args.id, change)
        close_item(config, args.id, "applied", f"applied: {what}")
    log(config, f"{args.id}: {what}")
    return 0


def cmd_close(config: Config, args: argparse.Namespace) -> int:
    state_to = "applied" if args.command == "done" else "rejected"
    with state_lock(config.learn_dir):
        pending_change(State.load(config.learn_dir), args.id)
        default = "done by hand" if state_to == "applied" else "declined"
        close_item(config, args.id, state_to, args.note or default)
    log(config, f"{args.id}: {state_to}")
    return 0


# ---- status ------------------------------------------------------------------------------------


def next_due(config: Config, moment: datetime) -> str:
    local_now = moment.astimezone().replace(tzinfo=None)
    for offset in range(8):
        day = local_now.date() + timedelta(days=offset)
        when = datetime.combine(day, config.at)
        if DAY_LETTERS[day.weekday()] in config.days and when > local_now:
            return f"{when:%a %Y-%m-%d %H:%M}"
    return "never (no scheduled days)"


def run_line(run: dict[str, Any]) -> str:
    error = f": {run['error']}" if run.get("error") else ""
    found = (
        f", {run.get('findings', 0)} finding(s), proposals {', '.join(run.get('proposals') or []) or 'none'}"
    )
    return f"  {run['started']} {run.get('outcome', '?')}{error}{found if run.get('outcome') == 'ok' else ''}"


def status_counts(state: State) -> str:
    counts: dict[str, int] = {}
    for info in state.findings.values():
        status = str(info.get("status", "?"))
        counts[status] = counts.get(status, 0) + 1
    detail = ", ".join(f"{count} {status}" for status, count in sorted(counts.items())) or "none"
    return f"findings remembered: {sum(counts.values())} ({detail})"


def waiting_lines() -> list[str]:
    items = ledger.load()["items"].values()
    waiting = [f"  {i['id']}: {i['title']}" for i in items if i["kind"] == KIND and i.get("proposal")]
    return ["waiting for your approval:", *waiting] if waiting else ["waiting for your approval: none"]


def cmd_status(config: Config, _args: argparse.Namespace) -> int:
    state = State.load(config.learn_dir)
    ledger.configure(config.state_dir)
    sync_statuses(state)
    schedule = f"daily at {config.at:%H:%M} on {config.days}, next {next_due(config, now())}"
    lines = [f"learn: {'enabled' if config.enabled else 'off (BOSUN_LEARN_ENABLED=0)'}; {schedule}"]
    if state.running:
        lines.append(f"running since {state.running['started']} (pid {state.running['pid']})")
    lines += ["last runs:", *map(run_line, state.runs[-5:])] if state.runs else ["no runs yet"]
    print("\n".join([*lines, status_counts(state), *waiting_lines()]))
    return 0


# ---- command line ------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bosun learn", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--since")
    run.add_argument("--until")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--collect-only", action="store_true")
    run.add_argument("--scheduled", action="store_true")
    run.add_argument("--out", type=Path)
    run.add_argument("--sessions", required=True)
    sub.add_parser("status")
    sub.add_parser("apply").add_argument("id")
    for name in ("done", "reject"):
        closing = sub.add_parser(name)
        closing.add_argument("id")
        closing.add_argument("--note", default="")
    return parser


COMMANDS: dict[str, Callable[[Config, argparse.Namespace], int]] = {
    "run": cmd_run,
    "status": cmd_status,
    "apply": cmd_apply,
    "done": cmd_close,
    "reject": cmd_close,
}


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.from_env(os.environ)
        ledger.configure(config.state_dir)
        return COMMANDS[args.command](config, args)
    except (LearnError, ledger.LedgerError, OSError, json.JSONDecodeError) as exc:
        print(f"learn: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
