"""Approvals for the orchestrator's outward actions (design: docs/design/approvals.md).

Pure functions over the ledger's items, a tool call and what I said in the session; lib/ledger.py
stores proposals and approvals, lib/guard.py runs the checks as hooks.

- A proposal may carry an action spec: the exact outward call it stands for. `slack` (channel,
  thread, text), `run` (an exact shell command) or `mcp` (a tool and its arguments). A call matches
  a spec only exactly: the same channel, thread and text (whitespace-normalized), the same command
  string, the same tool and arguments.
- An approval (`ledger approve <id> --quote '<my words>'`) is good for one call, and only while
  the quote appears in a prompt I typed (or sent from my phone) in the calling session, between the
  proposal and the approval. Text bosun pasted, other sessions' messages, tool results and
  context summaries never count.
- Standing classes (BOSUN_STANDING_CLASSES) allow routine PR traffic without a chat approval,
  under the conditions in STANDING_CLASSES below.
- After the call (PostToolUse / PostToolUseFailure): which used approval the call carried out, and
  whether it succeeded; and where a proposal stands for me (`proposal_status`): waiting on my
  approval, approved and not yet made, or made.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import activity
import claude_state


Item = dict[str, Any]
SLACK = "slack"
RUN = "run"
MCP = "mcp"
SPEC_TYPES = (SLACK, RUN, MCP)
SLACK_SEND_TOOL = "slack_send_message"
HASH_LENGTH = 16
# A prompt counts for an approval recorded up to this long after it (clock skew, a slow turn).
APPROVAL_GRACE = timedelta(minutes=2)
# Injected text looks like a tag (<cross-session-message>, <agent-message>, <task-notification>,
# <local-command-stdout>): never my words, whatever activity.is_mine knows of.
INJECTED_OPENING = "<"

# ---- action specs ----------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Text as compared: runs of whitespace (newlines too) as one space, trimmed."""
    return " ".join(str(text).split())


def tool_suffix(tool_name: str) -> str:
    """The tool part of mcp__<server>__<tool> (the same tool under any server name)."""
    return tool_name.rsplit("__", maxsplit=1)[-1]


def slack_spec(channel: str, text: str, thread: str = "") -> dict[str, str]:
    return {"type": SLACK, "channel": channel.strip(), "thread": (thread or "").strip(), "text": text}


def run_spec(command: str) -> dict[str, str]:
    return {"type": RUN, "command": command.strip()}


def mcp_spec(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": MCP, "tool": tool_suffix(tool.strip()), "args": args}


def spec_hash(spec: dict[str, Any]) -> str:
    """What an approval is tied to: the spec as compared (normalized Slack text)."""
    canonical = dict(spec, text=normalize(spec["text"])) if spec.get("type") == SLACK else spec
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:HASH_LENGTH]


def describe(spec: dict[str, Any]) -> str:
    """The proposal's action line for a spec given without one."""
    if spec["type"] == SLACK:
        where = f"thread {spec['thread']} in {spec['channel']}" if spec["thread"] else spec["channel"]
        return f"Slack message to {where}: {spec['text']}"
    if spec["type"] == RUN:
        return f"run: {spec['command']}"
    return f"{spec['tool']} {json.dumps(spec['args'], sort_keys=True)}"


def slack_call(tool_name: str) -> bool:
    return tool_name.startswith("mcp__") and tool_suffix(tool_name) == SLACK_SEND_TOOL


def slack_matches(spec: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> bool:
    return (
        slack_call(tool_name)
        and not tool_input.get("reply_broadcast")
        and str(tool_input.get("channel_id", "")).strip() == spec["channel"]
        and str(tool_input.get("thread_ts") or "").strip() == spec["thread"]
        and normalize(str(tool_input.get("message", ""))) == normalize(spec["text"])
    )


def run_matches(spec: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> bool:
    return tool_name == "Bash" and str(tool_input.get("command", "")).strip() == spec["command"]


def mcp_matches(spec: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> bool:
    return (
        tool_name.startswith("mcp__")
        and tool_suffix(tool_name) == spec["tool"]
        and tool_input == spec["args"]
    )


MATCHERS: dict[str, Callable[[dict[str, Any], str, dict[str, Any]], bool]] = {
    SLACK: slack_matches,
    RUN: run_matches,
    MCP: mcp_matches,
}


def call_matches(spec: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Whether a tool call is exactly the action <spec> stands for."""
    matcher = MATCHERS.get(str(spec.get("type")))
    return bool(matcher and matcher(spec, tool_name, tool_input))


# ---- my words in the session ------------------------------------------------------------------


@dataclass(frozen=True)
class Speaker:
    """How to tell my prompts in the orchestrator's transcript from everything else: what
    bosun pasted there (hashes, `bosun send`) and its drain prompt."""

    pasted: frozenset[str] = frozenset()
    drain_prompt: str = ""


def is_genuine(text: str, speaker: Speaker) -> bool:
    """A prompt I typed or sent from my phone: not pasted by bosun ([bosun ...], "Ledger
    item", check-in and drain prompts, slash commands), not injected (a tag-like opening)."""
    stripped = activity.unwrap(text)
    if stripped.startswith(INJECTED_OPENING):
        return False
    return activity.is_mine(text, speaker.pasted, speaker.drain_prompt)


def quote_in(quote: str, texts: Iterable[str]) -> bool:
    """Whether <quote> appears verbatim (whitespace-normalized, case-insensitive) in one of <texts>."""
    wanted = normalize(quote).casefold()
    return bool(wanted) and any(wanted in normalize(text).casefold() for text in texts)


PromptSource = Callable[[datetime], list[claude_state.Prompt]]


def transcript_prompts(transcript: str) -> PromptSource:
    """The prompts delivered to the session of <transcript> since a time (read once, lazily)."""
    cache: dict[str, list[claude_state.Prompt]] = {}

    def since(moment: datetime) -> list[claude_state.Prompt]:
        if "all" not in cache:
            path = Path(transcript) if transcript else None
            cache["all"] = claude_state.delivered_prompts(path) if path and path.is_file() else []
        return [p for p in cache["all"] if p.at >= moment]

    return since


def quote_verified(
    approval: dict[str, Any], proposed_at: str, prompts: PromptSource, speaker: Speaker
) -> bool:
    """The approval's quote is in a genuine prompt made after the proposal and before the approval."""
    start = datetime.fromisoformat(proposed_at)
    end = datetime.fromisoformat(approval["at"]) + APPROVAL_GRACE
    texts = [p.text for p in prompts(start) if p.at <= end and is_genuine(p.text, speaker)]
    return quote_in(str(approval.get("quote", "")), texts)


# ---- verdicts ---------------------------------------------------------------------------------

APPROVED = "approved"
STANDING = "standing"
REFUSED = "refused"


@dataclass(frozen=True)
class Verdict:
    outcome: str
    item_id: str = ""
    reason: str = ""
    cls: str = ""
    comment: str = ""  # the standing PR comment body it allows (pr-housekeeping), recorded with it

    @property
    def allowed(self) -> bool:
        return self.outcome in (APPROVED, STANDING)


def pending_specs(items: dict[str, Item]) -> Iterable[tuple[Item, dict[str, Any]]]:
    """Open items whose pending proposal carries an action spec."""
    for item in items.values():
        proposal = item.get("proposal") or {}
        if proposal.get("spec") and item.get("state") not in TERMINAL_STATES.get(item.get("kind", ""), ()):
            yield item, proposal


def approval_problem(proposal: dict[str, Any], prompts: PromptSource, speaker: Speaker) -> str | None:
    """Why a matching proposal's approval does not let the call through, or None."""
    approval = proposal.get("approval")
    if not approval:
        return "is proposed but not approved yet"
    if approval.get("hash") != spec_hash(proposal["spec"]):
        return "was approved in an earlier version; the proposal changed since"
    if approval.get("used_at"):
        return f"had its one approval used at {approval['used_at']}; propose and approve it again"
    if not quote_verified(approval, proposal["proposed_at"], prompts, speaker):
        return (
            f"has an approval whose quote ({approval.get('quote', '')!r}) is not in anything the user typed "
            "in this session after the proposal (tool results, pasted and injected text do not count)"
        )
    return None


def approved(items: dict[str, Item], call: Call, prompts: PromptSource, speaker: Speaker) -> Verdict:
    """The approved, unused proposal this call carries out, or why there is none."""
    problems = []
    for item, proposal in pending_specs(items):
        if not call_matches(proposal["spec"], call.tool, call.tool_input):
            continue
        problem = approval_problem(proposal, prompts, speaker)
        if problem is None:
            return Verdict(APPROVED, item["id"])
        problems.append(f"{item['id']} {problem}")
    reason = "; ".join(problems) or "no pending proposal has exactly this action"
    return Verdict(REFUSED, reason=reason)


# ---- standing classes --------------------------------------------------------------------------

PR_REVIEW_TRAFFIC = "pr-review-traffic"
PR_HOUSEKEEPING = "pr-housekeeping"
STANDING_CLASSES = {
    PR_REVIEW_TRAFFIC: (
        "a Slack message (send, not schedule; no broadcast) in a configured channel (never a DM), "
        "mentioning only configured people, with at most one URL, the parent PR's; it is the exact "
        "proposed message of an outreach item (purpose review, info-ack or fyi) whose parent is my "
        "PR in the ledger: open and ready (see pr_ready: a CI-green report after the last "
        "hand-over), or merged for an fyi note"
    ),
    PR_HOUSEKEEPING: (
        "gh pr ready <url>, gh pr edit <url> with only --title, --body and --add-reviewer "
        "<configured reviewers>, or gh pr comment <url> --body <a configured comment> (at most the "
        "configured number per PR), on my open, ready PR in the ledger (pr_ready); one plain "
        "command: no chains, pipes, substitutions or variables"
    ),
}
STANDING_PURPOSES = ("review", "info-ack", "fyi")
FYI = "fyi"
MY_PR_KIND = "my-pr"
OUTREACH_KIND = "outreach"
# Terminal states per kind, as lib/ledger.py's KINDS has them (kept here so this module stays pure;
# tests check they agree).
TERMINAL_STATES = {
    "review-request": ("closed", "dismissed"),
    "td-review": ("closed", "dismissed"),
    MY_PR_KIND: ("merged", "closed", "dismissed"),
    "mention": ("done", "dismissed"),
    OUTREACH_KIND: ("applied", "no-reply", "dropped"),
    "learning": ("applied", "rejected", "dismissed"),
    "task": ("done", "declined", "dropped"),
    "ticket": ("done", "cancelled"),
    "workstream": ("done",),
}
MERGED = "merged"
PRODUCES = "produces"
CI_GREEN = "green"
HANDOVER_EVENTS = ("delegated", "update", "moved")
REPORT_EVENT = "report"
SLACK_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
SLACK_SPECIAL_MENTION = re.compile(r"<!|(?<![\w@])@(?:here|channel|everyone)\b")
URL = re.compile(r"https?://[^\s<>|\"')\]]+")
URL_TRAILING = ".,;:!?"
DM_PREFIXES = ("D", "U", "W")  # a DM channel, or a user id used as the channel (opens a DM)
PR_URL = re.compile(r"^https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/pull/(?P<number>\d+)/?$")
GH_READY = ["gh", "pr", "ready"]
GH_EDIT = ["gh", "pr", "edit"]
GH_COMMENT = ["gh", "pr", "comment"]
GH_COMMENT_BODY_FLAGS = ("--body", "-b")
COMMENT_SEPARATOR = "|"
DEFAULT_COMMENTS_PER_PR = 3
# The ledger event a standing call records on its item (lib/ledger.py uses this one).
AUTO_APPROVED = "auto-approved"
GH_EDIT_TEXT_FLAGS = ("--title", "--body")
GH_ADD_REVIEWER = "--add-reviewer"
SHELL_SPECIALS = ("$", "`", "\n", ";", "&", "|", "<", ">", "\\")


@dataclass(frozen=True)
class Standing:
    """The site's standing approvals (BOSUN_STANDING_*); no classes: none apply."""

    classes: frozenset[str] = frozenset()
    slack_channels: frozenset[str] = frozenset()
    slack_mentions: frozenset[str] = frozenset()
    gh_reviewers: frozenset[str] = frozenset()
    gh_comments: frozenset[str] = frozenset()  # exact PR comment bodies, e.g. a re-review command
    comments_per_pr: int = DEFAULT_COMMENTS_PER_PR  # standing comments per body and PR; 0: no cap

    @classmethod
    def from_settings(
        cls,
        classes: str,
        channels: str,
        mentions: str,
        reviewers: str,
        comments: str = "",
        comments_per_pr: str = "",
    ) -> Standing:
        """From the settings' text: ids and logins space-separated, comment bodies '|'-separated."""
        return cls(
            frozenset(classes.split()) & set(STANDING_CLASSES),
            frozenset(c for c in channels.split() if not c.startswith(DM_PREFIXES)),
            frozenset(mentions.split()),
            frozenset(r.casefold() for r in reviewers.split()),
            frozenset(c.strip() for c in comments.split(COMMENT_SEPARATOR) if c.strip()),
            comment_cap(comments_per_pr),
        )


def comment_cap(setting: str) -> int:
    """BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR: a whole number (0: no cap); else the default."""
    text = setting.strip()
    return int(text) if text.isdigit() else DEFAULT_COMMENTS_PER_PR


@dataclass(frozen=True)
class Call:
    """A tool call as the hooks see it; <commands>: a Bash command's simple commands."""

    tool: str
    tool_input: dict[str, Any]
    commands: list[list[str]] = field(default_factory=list)


def last_work_entry(item: Item) -> dict[str, Any] | None:
    """The item's latest hand-over to a repo session or report from one, whichever came last."""
    return next(
        (
            h
            for h in reversed(item.get("history") or [])
            if h.get("event") in (*HANDOVER_EVENTS, REPORT_EVENT)
        ),
        None,
    )


def producers(pr: Item, items: dict[str, Item]) -> list[Item]:
    """The PR, and the items whose work produced it (a task: `produces` links to the PR)."""
    makers = [
        item
        for item in items.values()
        if any(
            link.get("rel") == PRODUCES and link.get("id") == pr.get("id") for link in item.get("links") or []
        )
    ]
    return [pr, *makers]


def pr_report(pr: Item, items: dict[str, Item]) -> dict[str, Any] | None:
    """The latest report on the PR's work (its own, or the task's that produced it), if nothing
    was handed over again since: None when the newest hand-over awaits a report, or none exists."""
    entries = [entry for item in producers(pr, items) if (entry := last_work_entry(item))]
    newest = max(entries, key=lambda entry: str(entry.get("at", "")), default=None)
    return newest if newest and newest.get("event") == REPORT_EVENT else None


def pr_ready(pr: Item, items: dict[str, Item]) -> bool:
    """My open PR is ready for reviewers: the latest report on its work, after its last hand-over,
    says CI is green (`ledger report --ci green`) and leaves nothing open. The ledger state alone
    (awaiting-review) is not enough: it cannot see CI."""
    if pr.get("kind") != MY_PR_KIND or pr.get("state") in TERMINAL_STATES[MY_PR_KIND]:
        return False
    report = pr_report(pr, items)
    return report is not None and report.get("ci") == CI_GREEN and not report.get("open")


def pr_url(item_id: str) -> str:
    repo, number = item_id.removeprefix("mypr:").rsplit("#", 1)
    return f"https://github.com/{repo}/pull/{number}"


def same_url(one: str, other: str) -> bool:
    return one.rstrip(URL_TRAILING).rstrip("/").casefold() == other.rstrip("/").casefold()


def slack_text_problem(text: str, parent_id: str, standing: Standing) -> str | None:
    """Why a Slack text is not routine PR traffic for <parent_id>, or None."""
    if SLACK_SPECIAL_MENTION.search(text):
        return "it uses a group mention (@here, @channel, a user group)"
    extra = set(SLACK_MENTION.findall(text)) - standing.slack_mentions
    if extra:
        return f"it mentions people outside the configured list ({len(extra)})"
    urls = URL.findall(text)
    if len(urls) > 1:
        return "it has more than one URL"
    if urls and not same_url(urls[0], pr_url(parent_id)):
        return "its URL is not the parent PR's"
    return None


def outreach_problem(item: Item, items: dict[str, Item]) -> str | None:
    """Why an outreach item's message is not PR review traffic, or None."""
    fields = item.get("fields") or {}
    purpose = fields.get("purpose")
    if fields.get("channel") != SLACK or purpose not in STANDING_PURPOSES:
        return f"{item['id']} is {purpose} outreach by {fields.get('channel')}, not PR review traffic"
    parent = items.get(str(fields.get("parent", "")))
    if parent is None or parent.get("kind") != MY_PR_KIND:
        return f"{item['id']} does not belong to one of my PRs"
    if purpose == FYI and parent.get("state") == MERGED:
        return None
    if not pr_ready(parent, items):
        return (
            f"{parent['id']} is not ready for reviewers in the ledger: the latest report on its work, "
            "after its last hand-over, must say --ci green and leave nothing open"
        )
    return None


def standing_slack(items: dict[str, Item], call: Call, standing: Standing) -> Verdict:
    if PR_REVIEW_TRAFFIC not in standing.classes or not slack_call(call.tool):
        return Verdict(REFUSED, reason="no standing class covers it")
    if str(call.tool_input.get("channel_id", "")) not in standing.slack_channels:
        return Verdict(REFUSED, reason="its channel is not a configured PR-review channel")
    problems = []
    for item, proposal in pending_specs(items):
        if item.get("kind") == OUTREACH_KIND and call_matches(proposal["spec"], call.tool, call.tool_input):
            problem = review_traffic_problem(items, item, proposal, call, standing)
            if not problem:
                return Verdict(STANDING, item["id"], cls=PR_REVIEW_TRAFFIC)
            problems.append(problem)
    return Verdict(REFUSED, reason="; ".join(problems) or "no proposed outreach message matches it exactly")


def review_traffic_problem(
    items: dict[str, Item], item: Item, proposal: dict[str, Any], call: Call, standing: Standing
) -> str | None:
    """Why the outreach message this call sends is not PR review traffic, or None."""
    if proposal.get("auto_approved"):
        return f"{item['id']} was already sent once under a standing approval"
    text = str(call.tool_input.get("message", ""))
    return outreach_problem(item, items) or slack_text_problem(text, item["fields"]["parent"], standing)


def plain_command(call: Call) -> list[str] | None:
    """The one simple command of a Bash call with nothing the shell would expand or chain."""
    command = str(call.tool_input.get("command", ""))
    if call.tool != "Bash" or len(call.commands) != 1 or any(ch in command for ch in SHELL_SPECIALS):
        return None
    return call.commands[0]


def flag_pairs(arguments: list[str]) -> list[tuple[str, str]] | None:
    """--flag value and --flag=value pairs; None if a flag lacks its value."""
    pairs = []
    rest = list(arguments)
    while rest:
        flag, sep, value = rest.pop(0).partition("=")
        if not sep:
            if not rest:
                return None
            value = rest.pop(0)
        pairs.append((flag, value))
    return pairs


def gh_edit_problem(arguments: list[str], standing: Standing) -> str | None:
    """Why gh pr edit's flags are not housekeeping, or None."""
    pairs = flag_pairs(arguments)
    if pairs is None:
        return "a flag has no value"
    for flag, value in pairs:
        if flag == GH_ADD_REVIEWER:
            reviewers = {r.strip().casefold() for r in value.split(",") if r.strip()}
            if not reviewers or not reviewers <= standing.gh_reviewers:
                return "it adds a reviewer outside the configured list"
        elif flag not in GH_EDIT_TEXT_FLAGS:
            return f"{flag} is not one of --title, --body, --add-reviewer"
    return None if pairs else "it changes nothing"


def comment_body(arguments: list[str]) -> str | None:
    """The body of gh pr comment's arguments when they are exactly one --body/-b and its text."""
    pairs = flag_pairs(arguments)
    if not pairs or len(pairs) != 1 or pairs[0][0] not in GH_COMMENT_BODY_FLAGS:
        return None
    return pairs[0][1]


def posted_comment(words: list[str]) -> str | None:
    """The body that `gh pr comment <url> --body <text>` (exactly that) posts, or None."""
    if words[: len(GH_COMMENT)] != GH_COMMENT:
        return None
    return comment_body(words[len(GH_COMMENT) + 1 :])


def standing_comments(item: Item, body: str) -> int:
    """How many comments with <body> standing approvals have let through on <item>."""
    return sum(
        1 for h in item.get("history") or [] if h.get("event") == AUTO_APPROVED and h.get("comment") == body
    )


def gh_comment_problem(item: Item, arguments: list[str], standing: Standing) -> str | None:
    """Why gh pr comment's arguments are not a standing comment on <item>, or None."""
    body = comment_body(arguments)
    if body is None:
        return "gh pr comment takes exactly one --body here"
    if body not in standing.gh_comments:
        return "its body is not one of the configured standing comments"
    cap = standing.comments_per_pr
    if cap and standing_comments(item, body) >= cap:
        return f"{cap} such comments already went out on {item['id']} under a standing approval"
    return None


def gh_target(words: list[str]) -> tuple[str, list[str]] | None:
    """(the PR's ledger id, the remaining arguments) of gh pr ready|edit|comment <PR URL> ..."""
    if words[:3] not in (GH_READY, GH_EDIT, GH_COMMENT) or len(words) < len(GH_READY) + 1:
        return None
    match = PR_URL.match(words[3])
    if not match:
        return None
    return f"mypr:{match['repo']}#{match['number']}", words[4:]


def housekeeping_problem(
    items: dict[str, Item], words: list[str], standing: Standing
) -> tuple[str, str | None]:
    """(the PR's ledger id, why the gh command is not housekeeping on it, or None)."""
    target = gh_target(words)
    if target is None:
        return "", "only a single plain `gh pr ready|edit|comment <PR URL> ...` can be housekeeping"
    item_id, arguments = target
    item = items.get(item_id)
    if item is None or not pr_ready(item, items):
        return item_id, f"{item_id} is not my open PR with a CI-green report after its last hand-over"
    if words[:3] == GH_COMMENT:
        return item_id, gh_comment_problem(item, arguments, standing)
    if words[:3] != GH_READY:
        return item_id, gh_edit_problem(arguments, standing)
    return item_id, ("gh pr ready takes no flags here" if arguments else None)


def standing_gh(items: dict[str, Item], call: Call, standing: Standing) -> Verdict:
    if PR_HOUSEKEEPING not in standing.classes:
        return Verdict(REFUSED, reason="no standing class covers it")
    words = plain_command(call)
    if not words:
        return Verdict(REFUSED, reason="not a plain command")
    item_id, problem = housekeeping_problem(items, words, standing)
    if problem:
        return Verdict(REFUSED, reason=problem)
    return Verdict(STANDING, item_id, cls=PR_HOUSEKEEPING, comment=posted_comment(words) or "")


def standing_verdict(items: dict[str, Item], call: Call, standing: Standing) -> Verdict:
    """The standing class that covers the call, or why none does."""
    if call.tool == "Bash":
        return standing_gh(items, call, standing)
    return standing_slack(items, call, standing)


def evaluate(
    items: dict[str, Item], call: Call, prompts: PromptSource, speaker: Speaker, standing: Standing
) -> Verdict:
    """Approved in chat first, then a standing class; else refused, with both reasons."""
    verdict = approved(items, call, prompts, speaker)
    if verdict.allowed:
        return verdict
    covered = standing_verdict(items, call, standing)
    if covered.allowed:
        return covered
    if not standing.classes:
        return verdict
    return Verdict(REFUSED, reason=f"{verdict.reason}; standing approvals: {covered.reason}")


# ---- after the call ----------------------------------------------------------------------------
#
# The PermissionRequest event has no tool_use_id (verified on 2.1.306, docs/design/approvals.md),
# so the approval it uses up records the call's hash, the session and the time; the PostToolUse or
# PostToolUseFailure event of the same call matches on those (and on the tool_use_id once one was
# recorded).

POST_TOOL_USE = "PostToolUse"
POST_TOOL_USE_FAILURE = "PostToolUseFailure"
# A call's result comes at most this long after its approval was used (Bash times out at 10 min).
CALL_WINDOW = timedelta(minutes=30)
# Our timestamps have whole seconds; Claude Code's have milliseconds.
CLOCK_SKEW = timedelta(seconds=5)
MAX_REASON_LENGTH = 200
EXIT_CODE_KEYS = ("exit_code", "exitCode", "returnCode")


def call_spec(call: Call) -> dict[str, Any]:
    """A spec describing a call as it was made: a Bash call by its command (not its description),
    any MCP call (Slack too) by its tool and arguments."""
    if call.tool == "Bash":
        return run_spec(str(call.tool_input.get("command", "")))
    return mcp_spec(call.tool, call.tool_input)


def call_hash(call: Call) -> str:
    return spec_hash(call_spec(call))


def used_grant(proposal: dict[str, Any] | None) -> dict[str, Any] | None:
    """What let a call through for the proposal and was used up by it: the chat approval once used,
    or the standing approval it was sent under (the stored record itself, which the ledger
    updates when the call's result comes)."""
    if not proposal:
        return None
    approval: dict[str, Any] | None = proposal.get("approval")
    if approval and approval.get("used_at"):
        return approval
    standing: dict[str, Any] | None = proposal.get("auto_approved")
    return standing or None


def grant_time(grant: dict[str, Any]) -> str:
    return str(grant.get("used_at") or grant.get("at") or "")


@dataclass(frozen=True)
class Outcome:
    """How a call ended, as the hook event says: ok, failed (with why), or unknown (None)."""

    ok: bool | None
    reason: str = ""


def short_reason(text: object) -> str:
    reason = normalize(str(text or "")) or "no reason given"
    return reason if len(reason) <= MAX_REASON_LENGTH else reason[: MAX_REASON_LENGTH - 3] + "..."


def response_failure(response: dict[str, Any]) -> str | None:
    """Why a tool_response says the call failed, or None. Claude Code reports failures as
    PostToolUseFailure; these fields are a second line of defence."""
    if response.get("is_error") or response.get("isError"):
        return short_reason(response.get("error") or response.get("content") or "the tool reported an error")
    if response.get("interrupted"):
        return "the call was interrupted"
    code = next((response[key] for key in EXIT_CODE_KEYS if key in response), 0)
    return f"exit code {code}" if isinstance(code, int) and code != 0 else None


def call_outcome(event: dict[str, Any]) -> Outcome:
    """Whether a PostToolUse or PostToolUseFailure event's call succeeded. A Bash call sent to the
    background only started, so its outcome is unknown."""
    if event.get("hook_event_name") == POST_TOOL_USE_FAILURE:
        return Outcome(False, short_reason(event.get("error")))
    response = event.get("tool_response")
    if isinstance(response, dict):
        if response.get("backgroundTaskId"):
            return Outcome(None, "it runs in the background")
        failure = response_failure(response)
        if failure:
            return Outcome(False, failure)
    return Outcome(True)


@dataclass(frozen=True)
class CallContext:
    """Where and when a call ran, from its hook event (empty strings when the event lacks them)."""

    session: str = ""
    tool_use_id: str = ""
    transcript: str = ""

    @classmethod
    def of(cls, event: dict[str, Any]) -> CallContext:
        return cls(
            str(event.get("session_id") or ""),
            str(event.get("tool_use_id") or ""),
            str(event.get("transcript_path") or ""),
        )

    def record(self) -> dict[str, str]:
        """What an approval keeps of the call that used it."""
        recorded = {"session": self.session, "tool_use_id": self.tool_use_id, "transcript": self.transcript}
        return {key: value for key, value in recorded.items() if value}


def same_if_both(one: object, other: str) -> bool:
    return not one or not other or one == other


def grant_matches(grant: dict[str, Any], call: Call, context: CallContext, moment: datetime) -> bool:
    """Whether <call>, ending at <moment>, is the one that used <grant>: the same call (hash), the
    same session and tool_use_id where both are known, within CALL_WINDOW after the use, and not
    settled by an earlier result."""
    if grant.get("call") != call_hash(call) or grant.get("carried_out") or grant.get("failed"):
        return False
    if not (
        same_if_both(grant.get("session"), context.session)
        and same_if_both(grant.get("tool_use_id"), context.tool_use_id)
    ):
        return False
    used = datetime.fromisoformat(grant_time(grant))
    return used - CLOCK_SKEW <= moment <= used + CALL_WINDOW


def carried_out_by(items: dict[str, Item], call: Call, context: CallContext, moment: datetime) -> Item | None:
    """The open item whose used approval this call is (the earliest unsettled one, if two items
    proposed the same call), or None."""
    found = [
        (grant_time(grant), item["id"])
        for item, proposal in pending_specs(items)
        if (grant := used_grant(proposal)) and grant_matches(grant, call, context, moment)
    ]
    return items[min(found)[1]] if found else None


# ---- where a proposal stands for me -------------------------------------------------------------

NEEDS_YOU = "needs-you"
APPROVED_UNUSED = "approved"
USED = "used"
PROPOSAL_STATUSES = {
    NEEDS_YOU: "awaiting your approval",
    APPROVED_UNUSED: "approved, not yet made",
    USED: "carried out, closing",
}
FAILED_LABEL = "the approved call failed; needs a new approval"


def proposal_status(proposal: dict[str, Any] | None) -> str | None:
    """Where a pending proposal stands: NEEDS_YOU (no approval, an approval for an earlier version,
    or the approved call failed: approvals are single-use), APPROVED_UNUSED (waiting on bosun to
    make the call), USED (made; the PostToolUse hook or `ledger sent` closes it). None: no proposal."""
    if not proposal:
        return None
    grant = used_grant(proposal)
    if grant:
        return NEEDS_YOU if grant.get("failed") else USED
    approval, spec = proposal.get("approval") or {}, proposal.get("spec")
    if approval and spec and approval.get("hash") == spec_hash(spec):
        return APPROVED_UNUSED
    return NEEDS_YOU


def proposal_label(proposal: dict[str, Any] | None) -> str:
    status = proposal_status(proposal)
    if status is None:
        return ""
    grant = used_grant(proposal) or {}
    return FAILED_LABEL if grant.get("failed") else PROPOSAL_STATUSES[status]


def needs_you(proposal: dict[str, Any] | None) -> bool:
    return proposal_status(proposal) == NEEDS_YOU
