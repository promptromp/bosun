"""A fictional bosun world for `bosun learn`: three days of acme's invoice exporter.

Shared by the unit tests (tests/unit/test_learn*.py), which check the digest built from it, and by
the eval (tests/llm/learn_eval.py), which checks what the real Claude finds in it. Everything here is
made up: the people (Priya, Marco, Dana), the repos (acme/exporter), the Slack ids (zero-filled).

The world runs from Monday 08:00 to Wednesday 12:00 (local time) and has planted issues, each with
the words a finding about it must mention (PLANTED), next to clean noise: check-ins that find
nothing, a review delegated and reported on time, a one-off error that was fixed, an unrelated
session outside bosun's windows, and a session excluded by id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any


# ruff: noqa: E501  (fixture commands and subjects stay on one line, as the sessions typed them)


MONDAY = date(2026, 1, 5)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)
WINDOW_START = (MONDAY, "08:00")
WINDOW_END = (WEDNESDAY, "12:00")
ORCH_MONDAY = "00000000-0000-4000-8000-0000000000a1"
ORCH_TUESDAY = "00000000-0000-4000-8000-0000000000a2"
ORCH_WEDNESDAY = "00000000-0000-4000-8000-0000000000a3"
EXPORTER = "00000000-0000-4000-8000-0000000000b1"
EXPORTER_TAX = "00000000-0000-4000-8000-0000000000c1"
EXPORTER_PDF = "00000000-0000-4000-8000-0000000000d1"
UNRELATED = "00000000-0000-4000-8000-0000000000e1"
EXCLUDED = "00000000-0000-4000-8000-0000000000f1"
DRAIN = "Bosun resumed or restarted this session (for example after sleep)."
PUBLIC_CHANNEL = "C00000000AB"
MENTION_CHANNEL = "C00000000AA"
DECISION_THREAD = "1767600000.000100"


@dataclass(frozen=True)
class Planted:
    """An issue planted in the world: a finding matches it if its category is one of <categories>
    and its text (title, summary, evidence, proposal) mentions every group of <needles> (any word
    of a group)."""

    id: str
    what: str
    categories: tuple[str, ...]
    needles: tuple[tuple[str, ...], ...]


PLANTED = (
    Planted(
        "stalled-permission",
        "the orchestrator waited 15 hours at a permission prompt for `gh api` overnight; check-ins skipped",
        ("stall", "setup", "prompt-gap", "mishap"),
        (("gh api", "permission"),),
    ),
    Planted(
        "repeated-guard-denial",
        "the guard denied the same `bosun send` without --id three times",
        ("mishap", "prompt-gap", "repeated-correction", "gap", "setup"),
        (("guard", "denied", "deny"), ("--id", "no-item", "bosun send")),
    ),
    Planted(
        "duplicate-delegation",
        "review:acme/exporter#57 delegated to exporter and to exporter-tax before any report",
        ("duplicate", "mishap", "ledger-hygiene", "gap"),
        (("#57",), ("exporter-tax", "--again", "second", "two windows", "twice")),
    ),
    Planted(
        "repeated-correction",
        "the user told the exporter session twice to open normal PRs, not drafts",
        ("repeated-correction", "prompt-gap", "memory", "mishap"),
        (("draft",), ("normal", "ready")),
    ),
    Planted(
        "report-never-recorded",
        "exporter-pdf posted its review of #58 but never ran `ledger report`; the item stays delegated",
        ("gap", "ledger-hygiene", "mishap", "stall"),
        (("#58",), ("report",)),
    ),
    Planted(
        "outreach-no-nudge",
        "the question to Priya (out:mypr:acme/exporter#60/1) went unanswered for two business days, no nudge",
        ("stall", "outreach", "gap"),
        (("#60", "priya"), ("nudge", "follow-up", "follow up", "unanswered", "no reply")),
    ),
    Planted(
        "idle-delegated-window",
        "exporter-tax got an investigation on Tuesday 09:00, said it would continue, and went idle",
        ("stall", "gap", "mishap"),
        (("exporter-tax", "c00000000aa"),),
    ),
    Planted(
        "near-duplicate-outreach",
        "two outreach drafts to Marco about the same exposed SFTP password (#61/1 and #61/3)",
        ("duplicate", "mishap", "outreach", "ledger-hygiene"),
        (("61/3", "61/1"), ("duplicate", "overlap", "same", "twice")),
    ),
    Planted(
        "follow-up-as-new-outreach",
        "an info-ack FYI recorded as new outreach (#62/2) in the thread of the already sent #62/1",
        ("duplicate", "mishap", "outreach", "ledger-hygiene", "gap"),
        (("62/2",), ("62/1", "follow-up", "follow up", "info-ack", "no reply")),
    ),
    Planted(
        "drafts-never-proposed",
        "four outreach drafts from Tuesday afternoon were never proposed by the check-ins or the morning run",
        ("gap", "stall", "outreach", "mishap"),
        (("draft",), ("propos", "never", "not")),
    ),
    Planted(
        "sensitive-in-public-channel",
        "outreach about a password in clear addressed to the public #exporter-dev channel",
        ("mishap", "outreach", "duplicate", "prompt-gap", "task-rule"),
        (("channel", "public"), ("password", "sensitive", "security", "secret", "credential")),
    ),
)


# ---- transcripts -------------------------------------------------------------------------------


def stamp(day: date, hhmm: str, seconds: int = 0) -> str:
    """A transcript timestamp: local wall time as UTC ISO with Z, like Claude Code writes."""
    local = datetime.combine(day, time.fromisoformat(hhmm)).astimezone() + timedelta(seconds=seconds)
    return local.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso(day: date, hhmm: str) -> str:
    """A ledger timestamp (UTC, seconds)."""
    return (
        datetime.combine(day, time.fromisoformat(hhmm))
        .astimezone()
        .astimezone(UTC)
        .isoformat(timespec="seconds")
    )


@dataclass(frozen=True)
class Outcome:
    text: str
    error: bool = False
    answered_after: float | None = None


@dataclass
class Script:
    """One transcript, written entry by entry."""

    session_id: str
    title: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0

    def add(self, entry: dict[str, Any]) -> None:
        self.entries.append({"sessionId": self.session_id, **entry})

    def prompt(self, day: date, hhmm: str, text: str, source: str = "typed") -> None:
        self.add(
            {
                "type": "user",
                "timestamp": stamp(day, hhmm),
                "origin": {"kind": "human"},
                "promptSource": source,
                "message": {"role": "user", "content": text},
            }
        )

    def tool(self, day: date, hhmm: str, name: str, tool_input: dict[str, Any], outcome: Outcome) -> None:
        """A tool call and its result; with outcome.answered_after (seconds), a permission prompt."""
        result, error, answered_after = outcome.text, outcome.error, outcome.answered_after
        self.calls += 1
        call_id = f"toolu_{self.session_id[-4:]}{self.calls:04d}"
        self.add(
            {
                "type": "assistant",
                "timestamp": stamp(day, hhmm),
                "message": {
                    "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]
                },
            }
        )
        decision = {"decision": "accept", "source": "user_temporary" if answered_after else "config"}
        self.add(
            {
                "type": "user",
                "timestamp": stamp(day, hhmm, int(answered_after or 2)),
                "permissionDecision": decision,
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": call_id, "content": result, "is_error": error}
                    ]
                },
            }
        )

    def bash(
        self,
        day: date,
        hhmm: str,
        command: str,
        result: str = "ok",
        *,
        error: bool = False,
        answered_after: float | None = None,
    ) -> None:
        self.tool(day, hhmm, "Bash", {"command": command}, Outcome(result, error, answered_after))

    def denied(self, day: date, hhmm: str, command: str, reason: str) -> None:
        """A Bash call the guard hook refused: the call, the hook's answer, the refusal."""
        self.calls += 1
        call_id = f"toolu_{self.session_id[-4:]}{self.calls:04d}"
        use = {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}
        self.add({"type": "assistant", "timestamp": stamp(day, hhmm), "message": {"content": [use]}})
        output = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
        self.add(
            {
                "type": "attachment",
                "timestamp": stamp(day, hhmm, 1),
                "attachment": {
                    "type": "hook_success",
                    "hookEvent": "PreToolUse",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": call_id,
                    "stdout": json.dumps({"hookSpecificOutput": output}),
                },
            }
        )
        result = {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": f"Hook denied: {reason}",
            "is_error": True,
        }
        self.add({"type": "user", "timestamp": stamp(day, hhmm, 1), "message": {"content": [result]}})

    def reply(self, day: date, hhmm: str, text: str) -> None:
        self.add(
            {
                "type": "assistant",
                "timestamp": stamp(day, hhmm),
                "message": {"content": [{"type": "text", "text": text}]},
            }
        )
        self.add({"type": "system", "subtype": "turn_duration", "timestamp": stamp(day, hhmm, 1)})

    def write(self, project: Path) -> Path:
        project.mkdir(parents=True, exist_ok=True)
        path = project / f"{self.session_id}.jsonl"
        lines = [
            json.dumps({"type": "custom-title", "customTitle": self.title, "sessionId": self.session_id})
        ]
        lines += [json.dumps(entry) for entry in self.entries]
        path.write_text("\n".join(lines) + "\n")
        return path


def checkin(script: Script, day: date, hhmm: str, reply: str = "Check-in: nothing new.") -> None:
    script.prompt(day, hhmm, "/bosun:chores check-in")
    script.bash(day, hhmm, "bosun ledger mark-run check-in; bosun context")
    script.reply(day, hhmm, reply)


def orchestrator_monday() -> Script:
    s = Script(ORCH_MONDAY, "orchestrator-2026-01-05")
    s.prompt(MONDAY, "08:01", "/bosun:chores morning")
    s.bash(MONDAY, "08:01", "bosun ledger mark-run morning")
    s.bash(
        MONDAY,
        "08:03",
        "bosun delegate review --id 'review:acme/exporter#55' --ask 'Review the CSV header fix' --window exporter",
    )
    s.reply(
        MONDAY,
        "08:04",
        "Morning run done. Handed review:acme/exporter#55 to exporter. Nothing needs your approval.",
    )
    checkin(s, MONDAY, "09:00")
    s.bash(
        MONDAY,
        "09:10",
        "bosun delegate review --id 'review:acme/exporter#57' --ask 'Review the totals rounding' --window exporter",
    )
    s.reply(MONDAY, "09:11", "Handed review:acme/exporter#57 to exporter.")
    for minute in ("10:05", "10:07", "10:09"):
        s.denied(
            MONDAY,
            minute,
            "bosun send exporter-pdf 'Please also check the retry path'",
            "bosun guard: bosun send needs --id <ledger id> or --no-item",
        )
    s.reply(MONDAY, "10:10", "I could not send the note to exporter-pdf: the guard blocks the send.")
    s.prompt(MONDAY, "10:20", "Approve 1, the reply to Dana.")
    s.reply(MONDAY, "10:21", "Sent the reply to Dana in the thread.")
    s.bash(
        MONDAY,
        "10:40",
        "bosun delegate review --id 'review:acme/exporter#57' --ask 'Review the totals rounding' --window exporter-tax --again",
    )
    s.reply(
        MONDAY, "10:41", "Handed review:acme/exporter#57 to exporter-tax as well, since exporter looked busy."
    )
    checkin(s, MONDAY, "11:00")
    s.bash(
        MONDAY,
        "11:30",
        "bosun delegate review --id 'review:acme/exporter#58' --ask 'Review the API pagination' --window exporter-pdf",
    )
    s.reply(MONDAY, "11:31", "Handed review:acme/exporter#58 to exporter-pdf.")
    checkin(s, MONDAY, "14:00")
    s.prompt(MONDAY, "17:00", "/bosun:chores check-in")
    s.bash(MONDAY, "17:01", "bosun ledger mark-run check-in; bosun context")
    s.bash(
        MONDAY,
        "17:05",
        "gh api repos/acme/exporter/pulls/41/reviews --jq '.[].state'",
        '"APPROVED"',
        answered_after=15 * 3600 + 5 * 60,
    )
    s.reply(TUESDAY, "08:12", "Check-in (late): mypr:acme/exporter#41 is approved. Propose a squash-merge?")
    return s


def orchestrator_tuesday() -> Script:
    s = Script(ORCH_TUESDAY, "orchestrator-2026-01-06")
    s.prompt(TUESDAY, "08:30", "/bosun:chores morning")
    s.bash(TUESDAY, "08:31", "bosun ledger mark-run morning")
    s.bash(
        TUESDAY,
        "09:00",
        f"bosun delegate investigate --id 'slack:{MENTION_CHANNEL}/1767690000.000200' --ask 'Why do PDF exports time out?' --window exporter-tax",
    )
    s.reply(TUESDAY, "09:01", "Handed the PDF timeout question to exporter-tax.")
    for hhmm in ("10:00", "11:00", "13:00", "15:00"):
        checkin(s, TUESDAY, hhmm)
    s.prompt(TUESDAY, "16:00", "yes, squash-merge #41")
    s.bash(
        TUESDAY,
        "16:01",
        "gh pr merge https://github.com/acme/exporter/pull/41 --squash",
        "merged",
        answered_after=40,
    )
    s.reply(TUESDAY, "16:02", "Merged acme/exporter#41.")
    return s


def orchestrator_wednesday() -> Script:
    s = Script(ORCH_WEDNESDAY, "orchestrator-2026-01-07")
    s.prompt(WEDNESDAY, "08:30", "/bosun:chores morning")
    s.bash(WEDNESDAY, "08:31", "bosun ledger mark-run morning; bosun ledger outreach --open")
    s.reply(
        WEDNESDAY,
        "08:33",
        "Morning run done. Nothing needs your approval. FYI: waiting on Priya since Monday: "
        "may the export drop the shared SFTP password?",
    )
    checkin(s, WEDNESDAY, "10:00")
    checkin(s, WEDNESDAY, "11:00")
    return s


def exporter() -> Script:
    s = Script(EXPORTER, "exporter")
    s.prompt(
        MONDAY,
        "08:03",
        "Ledger id: review:acme/exporter#55\nAsk: Review the CSV header fix\n\nReview someone else's PR: ...",
    )
    s.bash(
        MONDAY,
        "08:05",
        "ls exporter/csv_headers.py",
        "ls: exporter/csv_headers.py: No such file or directory",
        error=True,
    )
    s.bash(MONDAY, "08:06", "gh pr diff https://github.com/acme/exporter/pull/55")
    s.bash(
        MONDAY,
        "08:20",
        "bosun ledger report 'review:acme/exporter#55' --state reviewed --summary 'approved; header order fixed'",
    )
    s.reply(MONDAY, "08:21", "Reviewed and approved #55; report recorded.")
    s.prompt(MONDAY, "10:30", "Fix the rounding in exporter/totals.py and open a PR.")
    s.bash(
        MONDAY,
        "10:50",
        "gh pr create --draft --title 'Round totals per invoice'",
        "https://github.com/acme/exporter/pull/63",
    )
    s.reply(MONDAY, "10:51", "Opened draft PR acme/exporter#63.")
    s.prompt(MONDAY, "11:00", "Don't open draft PRs for my work, open normal PRs. Mark #63 ready.")
    s.reply(MONDAY, "11:02", "Marked #63 ready for review. I will open normal PRs from now on.")
    s.prompt(TUESDAY, "13:30", "Add a test for the rounding of negative totals and open a PR.")
    s.bash(
        TUESDAY,
        "13:50",
        "gh pr create --draft --title 'Test negative totals'",
        "https://github.com/acme/exporter/pull/64",
    )
    s.reply(TUESDAY, "13:51", "Opened draft PR acme/exporter#64.")
    s.prompt(TUESDAY, "14:00", "Again a draft. I said normal PRs, not drafts. Please remember this.")
    s.reply(TUESDAY, "14:01", "Sorry. Marked #64 ready; normal PRs from now on.")
    s.bash(
        TUESDAY,
        "14:20",
        "bosun ledger reach-out 'mypr:acme/exporter#61' --to 'Marco (upload owner)' --purpose decision --subject 'OK to skip the upload suite for now, and flag: the sandbox SFTP password is in the PR description'",
    )
    s.bash(
        TUESDAY,
        "14:40",
        f"bosun ledger reach-out 'mypr:acme/exporter#61' --to 'Marco and Priya in #exporter-dev ({PUBLIC_CHANNEL})' --purpose review --subject 'the sandbox SFTP password was posted in the PR description; recommend rotating it'",
    )
    s.bash(
        TUESDAY,
        "14:50",
        f"bosun ledger reach-out 'mypr:acme/exporter#62' --to 'Dana (decision A thread, {DECISION_THREAD})' --purpose info-ack --subject 'Add to decision A: rounding also changes the CSV footer'",
    )
    s.reply(TUESDAY, "14:51", "Recorded three outreach drafts for the orchestrator to propose.")
    return s


def exporter_tax() -> Script:
    s = Script(EXPORTER_TAX, "exporter-tax")
    s.prompt(
        MONDAY,
        "10:40",
        "Ledger id: review:acme/exporter#57\nAsk: Review the totals rounding\n\nReview someone else's PR: ...",
    )
    s.bash(MONDAY, "10:45", "gh pr diff https://github.com/acme/exporter/pull/57")
    s.reply(MONDAY, "11:20", "Posted a COMMENT review on #57.")
    s.prompt(
        TUESDAY,
        "09:00",
        f"Ledger id: slack:{MENTION_CHANNEL}/1767690000.000200\nAsk: Why do PDF exports time out?\n\nInvestigate or answer a question: ...",
    )
    s.bash(TUESDAY, "09:05", "rg -n timeout exporter/pdf.py", "exporter/pdf.py:12: TIMEOUT = 30")
    s.reply(
        TUESDAY, "09:20", "Started the investigation; the build is slow, so I will continue once it finishes."
    )
    return s


def exporter_pdf() -> Script:
    s = Script(EXPORTER_PDF, "exporter-pdf")
    s.prompt(
        MONDAY,
        "11:30",
        "Ledger id: review:acme/exporter#58\nAsk: Review the API pagination\n\nReview someone else's PR: ...",
    )
    s.bash(MONDAY, "11:35", "gh pr diff https://github.com/acme/exporter/pull/58")
    s.bash(
        MONDAY,
        "11:50",
        "gh pr review https://github.com/acme/exporter/pull/58 --approve --body 'Looks good'",
        "approved",
    )
    s.reply(MONDAY, "11:51", "Review posted on #58: approved.")
    return s


def unrelated() -> Script:
    s = Script(UNRELATED, "scratch-notes")
    s.prompt(MONDAY, "12:00", "Summarize the release notes")
    s.bash(MONDAY, "12:01", "cat RELEASE.md", "cat: RELEASE.md: No such file or directory", error=True)
    return s


def excluded() -> Script:
    s = Script(EXCLUDED, "orchestrator-2026-01-06")
    s.prompt(TUESDAY, "12:00", "This conversation is private and must never be read.")
    return s


# ---- ledger, logs, config ----------------------------------------------------------------------


def item(
    item_id: str,
    kind: str,
    state: str,
    title: str,
    created: str,
    history: list[dict[str, Any]],
    **fields: Any,
) -> dict[str, Any]:
    first = next((h["from"] for h in history if h.get("from")), state)
    entries = [{"at": created, "event": "created", "state": first}, *history]
    return {
        "id": item_id,
        "kind": kind,
        "state": state,
        "title": title,
        "url": "",
        "fields": fields,
        "proposal": None,
        "created_at": created,
        "updated_at": entries[-1]["at"],
        "history": entries,
    }


def delegated(at: str, window: str, task: str = "review") -> dict[str, Any]:
    return {"at": at, "event": "delegated", "window": window, "task": task}


def moved(at: str, frm: str, to: str, note: str = "") -> dict[str, Any]:
    return {"at": at, "event": "transition", "from": frm, "to": to, "note": note}


def sent_message(day: date, hhmm: str, ts: str) -> dict[str, Any]:
    return {
        "ref": f"https://acme.slack.com/archives/{PUBLIC_CHANNEL}/p{ts.replace('.', '')}",
        "channel": "slack",
        "at": iso(day, hhmm),
    }


def ledger_items() -> dict[str, dict[str, Any]]:
    m, t = MONDAY, TUESDAY
    items = [
        item(
            "review:acme/exporter#55",
            "review-request",
            "reviewed",
            "CSV header fix",
            iso(m, "08:02"),
            [
                moved(iso(m, "08:03"), "requested", "delegated"),
                delegated(iso(m, "08:03"), "exporter"),
                {
                    "at": iso(m, "08:20"),
                    "event": "report",
                    "note": "approved; header order fixed",
                    "window": "exporter",
                },
                moved(iso(m, "08:20"), "delegated", "reviewed", "approved; header order fixed"),
            ],
        ),
        item(
            "review:acme/exporter#57",
            "review-request",
            "delegated",
            "Totals rounding",
            iso(m, "09:05"),
            [
                moved(iso(m, "09:10"), "requested", "delegated"),
                delegated(iso(m, "09:10"), "exporter"),
                delegated(iso(m, "10:40"), "exporter-tax"),
            ],
        ),
        item(
            "review:acme/exporter#58",
            "review-request",
            "delegated",
            "API pagination",
            iso(m, "11:25"),
            [
                moved(iso(m, "11:30"), "requested", "delegated"),
                delegated(iso(m, "11:30"), "exporter-pdf"),
            ],
        ),
        item(
            "mypr:acme/exporter#41",
            "my-pr",
            "merged",
            "Retry uploads",
            iso(m, "08:02"),
            [
                moved(iso(t, "08:12"), "awaiting-review", "approved"),
                moved(iso(t, "16:02"), "approved", "merged"),
            ],
        ),
        item(
            f"slack:{MENTION_CHANNEL}/1767690000.000200",
            "mention",
            "surfaced",
            "Why do PDF exports time out?",
            iso(t, "08:40"),
            [
                moved(iso(t, "08:45"), "new", "surfaced"),
                delegated(iso(t, "09:00"), "exporter-tax", "investigate"),
            ],
        ),
        item(
            "mypr:acme/exporter#60",
            "my-pr",
            "awaiting-review",
            "Drop the shared SFTP password",
            iso(m, "09:30"),
            [],
        ),
        item(
            "out:mypr:acme/exporter#60/1",
            "outreach",
            "sent",
            "May the export drop the shared SFTP password?",
            iso(m, "09:40"),
            [
                {"at": iso(m, "10:00"), "event": "outreach", "note": "sent", "ref": "slack"},
                moved(iso(m, "10:00"), "proposed", "sent"),
            ],
            parent="mypr:acme/exporter#60",
            channel="slack",
            purpose="decision",
            to="Priya (security)",
            subject="May the export drop the shared SFTP password?",
            messages=[sent_message(m, "10:00", "1767603600.000300")],
        ),
        item("mypr:acme/exporter#61", "my-pr", "awaiting-review", "Upload suite", iso(m, "09:30"), []),
        item(
            "out:mypr:acme/exporter#61/1",
            "outreach",
            "draft",
            "Skip the upload suite; password in the PR description",
            iso(t, "14:20"),
            [],
            parent="mypr:acme/exporter#61",
            channel="slack",
            purpose="decision",
            to="Marco (upload owner)",
            subject="OK to skip the upload suite for now, and flag: the sandbox SFTP password is in the PR description",
        ),
        item(
            "out:mypr:acme/exporter#61/2",
            "outreach",
            "draft",
            "What does ready mean for the upload suite?",
            iso(t, "14:30"),
            [],
            parent="mypr:acme/exporter#61",
            channel="slack",
            purpose="clarification",
            to="Dana (assigner)",
            subject="Should a failing check block merges, or only warn?",
        ),
        item(
            "out:mypr:acme/exporter#61/3",
            "outreach",
            "draft",
            "Rotate the SFTP password posted in the PR description",
            iso(t, "14:40"),
            [],
            parent="mypr:acme/exporter#61",
            channel="slack",
            purpose="review",
            to=f"Marco and Priya in #exporter-dev ({PUBLIC_CHANNEL})",
            subject="the sandbox SFTP password was posted in the PR description; recommend rotating it",
        ),
        item(
            "mypr:acme/exporter#62",
            "my-pr",
            "awaiting-review",
            "Round totals per invoice",
            iso(m, "09:30"),
            [],
        ),
        item(
            "out:mypr:acme/exporter#62/1",
            "outreach",
            "sent",
            "Decision A: round per line or per invoice?",
            iso(m, "12:00"),
            [
                {"at": iso(m, "12:10"), "event": "outreach", "note": "sent", "ref": "slack"},
                moved(iso(m, "12:10"), "proposed", "sent"),
            ],
            parent="mypr:acme/exporter#62",
            channel="slack",
            purpose="decision",
            to="Dana",
            subject="Decision A: round totals per line or per invoice?",
            messages=[sent_message(m, "12:10", DECISION_THREAD)],
        ),
        item(
            "out:mypr:acme/exporter#62/2",
            "outreach",
            "draft",
            "Add to decision A: the CSV footer",
            iso(t, "14:50"),
            [],
            parent="mypr:acme/exporter#62",
            channel="slack",
            purpose="info-ack",
            to=f"Dana (decision A thread, {DECISION_THREAD})",
            subject="Add to decision A: rounding also changes the CSV footer",
        ),
    ]
    return {i["id"]: i for i in items}


def checkin_log(day: date) -> str:
    lines = []
    if day == MONDAY:
        lines += [
            f"2026-01-05 {h}:00:30 [send] delivered to main:orchestrator" for h in ("09", "11", "14", "17")
        ]
        lines += [
            f"2026-01-05 {h}:00:31 [checkin] skip: orchestrator is waiting (for {s}s)"
            for h, s in (("18", 3300), ("19", 6900), ("20", 10500), ("21", 14100), ("22", 17700))
        ]
    elif day == TUESDAY:
        lines += [
            f"2026-01-06 0{h}:00:31 [checkin] skip: orchestrator is waiting (for {s}s)"
            for h, s in (("6", 46500), ("7", 50100), ("8", 53700))
        ]
        lines += [
            f"2026-01-06 {h}:00:30 [send] delivered to main:orchestrator" for h in ("10", "11", "13", "15")
        ]
    else:
        lines += [f"2026-01-07 {h}:00:30 [send] delivered to main:orchestrator" for h in ("10", "11")]
    return "\n".join(lines) + "\n"


def morning_log(day: date) -> str:
    stamp_ = f"{day:%Y-%m-%d} 08:00"
    return (
        f"{stamp_}:02 [morning] === morning run (trigger=launchd, session=main, dry_run=0) ===\n"
        f"{stamp_}:05 [setup] ok: aws profile dev is valid, no login needed\n"
        f"{stamp_}:30 [morning] morning run complete\n"
    )


@dataclass
class LearnWorld:
    base: Path
    projects: Path
    state: Path
    config: Path
    repos: Path
    sessions_file: Path

    @property
    def since(self) -> datetime:
        return datetime.combine(WINDOW_START[0], time.fromisoformat(WINDOW_START[1])).astimezone()

    @property
    def until(self) -> datetime:
        return datetime.combine(WINDOW_END[0], time.fromisoformat(WINDOW_END[1])).astimezone()

    def env(self) -> dict[str, str]:
        """The settings `bosun learn` would pass for this world."""
        return {
            "BOSUN_STATE_DIR": str(self.state),
            "BOSUN_LOG_DIR": str(self.state / "logs"),
            "BOSUN_CONFIG_DIR": str(self.config),
            "BOSUN_ORCHESTRATOR": "orchestrator",
            "BOSUN_DRAIN_PROMPT": DRAIN,
            "BOSUN_DAYS": "MTWRF",
            "BOSUN_LEARN_EXCLUDE_SESSIONS": EXCLUDED,
        }


def project_dir(projects: Path, folder: Path) -> Path:
    resolved = str(folder.resolve())
    return projects / "".join(c if c.isalnum() or c == "-" else "-" for c in resolved)


def window(
    name: str, kind: str, folder: Path, session_id: str | None, status: str = "idle"
) -> dict[str, Any]:
    claude = {"sessionId": session_id, "status": status, "name": name} if session_id else None
    return {
        "name": name,
        "kind": kind,
        "dir": str(folder),
        "model": "opus",
        "effort": "medium",
        "window": True,
        "claude": claude,
    }


def build(base: Path) -> LearnWorld:
    """Write the world under <base>; transcripts go to <base>/claude/projects (patch
    claude_state.PROJECTS_DIR to read them)."""
    repos = base / "repos"
    folders = {name: repos / name for name in ("exporter", "exporter-tax", "exporter-pdf")}
    for folder in (repos, *folders.values()):
        folder.mkdir(parents=True, exist_ok=True)
    world = LearnWorld(
        base, base / "claude" / "projects", base / "state", base / "config", repos, base / "sessions.json"
    )
    for script, folder in (
        (orchestrator_monday(), repos), (orchestrator_tuesday(), repos), (orchestrator_wednesday(), repos),
        (excluded(), repos), (unrelated(), repos), (exporter(), folders["exporter"]),
        (exporter_tax(), folders["exporter-tax"]), (exporter_pdf(), folders["exporter-pdf"]),
    ):  # fmt: skip
        script.write(project_dir(world.projects, folder))
    logs = world.state / "logs"
    logs.mkdir(parents=True)
    (world.state / "ledger.json").write_text(
        json.dumps({"version": 1, "items": ledger_items(), "sources": {}, "runs": {}}, indent=2)
    )
    for day in (MONDAY, TUESDAY, WEDNESDAY):
        (logs / f"checkin-{day:%Y-%m-%d}.log").write_text(checkin_log(day))
        (logs / f"morning-{day:%Y-%m-%d}.log").write_text(morning_log(day))
    (world.state / "watchdog.json").write_text(
        json.dumps({"alerted": {"waiting:orchestrator:101:1": "2026-01-06 08:00:00"}})
    )
    world.config.mkdir(parents=True)
    world.sessions_file.write_text(
        json.dumps(
            [
                window("orchestrator", "orchestrator", repos, ORCH_WEDNESDAY),
                window("exporter", "session", folders["exporter"], EXPORTER),
                window("exporter-tax", "session", folders["exporter-tax"], EXPORTER_TAX),
                window("exporter-pdf", "session", folders["exporter-pdf"], EXPORTER_PDF),
                {"name": "checkin", "kind": "checkin", "dir": str(base), "window": True, "claude": None},
            ],
            indent=2,
        )
    )
    return world
