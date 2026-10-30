#!/usr/bin/env python3
"""Work-item ledger for the bosun orchestrator: stable ids, per-kind state machines,
processed-source bookkeeping and pending proposals that need human approval.

Usage: bosun ledger <command> [args]

  list [--open] [--kind K] [--proposals]       table of items (@window: where it was delegated;
                                               "waiting on out:...": open outreach it waits on)
  show <id>                                    one item as JSON
  upsert <id> [--kind K] [--title T] [--url U] [--field k=v ...]
                                               create (in the kind's initial state; needs --kind)
                                               or update fields; never changes state
  transition <id> <state> --note N [--evidence E] [--field k=v ...] [--force-open-outreach]
                                               validated state change, recorded in history. An
                                               item waiting on open outreach is not finished (a
                                               terminal state) without --force-open-outreach
                                               (report --state takes it too), except a PR that
                                               merged or closed: the note names the outreach
  note <id> <text>                             append a note to the history
  report <id> --summary S [--result R ...] [--open O] [--ci green|red|pending|none]
         [--state S [--evidence E]] [--field k=v ...]
                                               a repo session's outcome: one-line summary,
                                               results (PR/review URL, report path), what is
                                               left open, the CI status of the pushed commit;
                                               --state also transitions the item
  reports [--since <ISO time>]                 latest reports: of open items, or all since a time
  delegation <id>                              where the item was delegated and whether it awaits
                                               a report (JSON); fails if it never was or is
                                               finished
  said [<id>] [--since T] [--window W]         what I typed in repo windows (recorded by the
                                               UserPromptSubmit hook), oldest first, verbatim
  working <id> [--window W] [--note N]         a window works on the item now (its active item);
                                               an item no window holds yet gets the window
  idle <id> [--note N]                         the window put the item aside
  record-delegation <id> --window W --task T [--ask A] [--link L] [--requester R] [--details D]
  record-update <id> --text T
  record-move <id> --window W --text T [--notified]
                                               used by `bosun delegate` (--update, and
                                               --update --window to move an item)
  propose <id> <action | ->                    record an action that awaits approval ("-": read
                                               it from stdin, e.g. a quoted heredoc)
  propose <id> [<action>] --send slack --channel C [--thread TS] --text <text | ->
  propose <id> [<action>] --run '<exact command>'
  propose <id> [<action>] --tool <MCP tool> --args-json <JSON object | ->
                                               the same, with the exact outward call it stands
                                               for (an action spec; the action defaults to it)
  approve <id> --quote '<my exact words>'      I approved the pending proposal's action spec in
                                               chat: the guard lets exactly that call through,
                                               once, while my words are in the session
                                               (BOSUN_APPROVALS_ENABLED; docs/design/approvals.md)
  approvals [--since T] [--auto]               calls the guard let through: approved in chat, or
                                               (--auto: only) under a standing class
  resolve <id> done|rejected|withdrawn [--note N]
                                               close the pending proposal (learn: items close
                                               with bosun learn apply|done|reject instead).
                                               With approvals on, a proposal whose approved call
                                               succeeded is closed by `bosun guard
                                               --post-tool-use`; a failed one records
                                               approval-failed and needs a new proposal
  resolve-carried-out [--dry-run] [--transcript PATH ...]
                                               one-time cleanup: pending proposals whose approval
                                               was used, settled by what the transcript shows of
                                               that call (recorded at use, else PATH, else the
                                               orchestrator's transcripts since): success closes
                                               them (outreach: marked carried out), failure
                                               records approval-failed; no evidence: listed for
                                               resolving by hand, unchanged
  mark-run <name>                              print when <name> (morning, check-in) last ran,
                                               record now, and print where the Slack scan starts
                                               (scan from): a check-in from the last run, a
                                               morning from the earlier of the last run and the
                                               lookback, never more than the maximum lookback
  seen <source-key>                            exit 0 and print the record if already processed;
                                               otherwise exit 1, naming the open outreach whose
                                               Slack thread (or DM) the message is in, if any (all
                                               of them, latest message first, if several)
  record-source <source-key> --class C [--item ID]
                                               remember that a source (e.g. a Slack permalink)
                                               was processed, so it is never classified twice
  kinds                                        print the state machines
  prune [--days N]                             drop finished items and sources older than N days
  diff <before.json>                           items added or changed since a copy of the ledger
  render                                       rewrite ledger.md

Outreach: what we sent (or will send) to someone outside this machine and wait on (kind outreach;
ids out:<parent-id>/<n>, or out:<YYYY-MM-DD>/<n> when it belongs to no item):

  reach-out [<parent-id>] --to WHO --subject TEXT [--channel C] [--purpose P] [--option O ...]
            [--details D | --details -] [--expect-by YYYY-MM-DD]
                                               record one, in draft; prints its id first, and
                                               warns if the parent has open outreach to the same
                                               person. --channel: slack (default), email, jira,
                                               github, confluence, other. --purpose: question
                                               (default), decision, clarification, approval,
                                               review, info-ack (they confirm they saw it), fyi
                                               (one-way: no reply expected, done once sent)
  ask ... --question TEXT                      the same command (--question is --subject)
  upsert <out-id> [--title T] [--field subject=T] [--field options='a | b'] [--field to=WHO]
                                               keep it in line with the message (title and
                                               subject are one); also channel, purpose
  propose <out-id> <message | ->               the outgoing message awaits approval (-> proposed;
                                               rejecting the proposal goes back to draft)
  sent <out-id> [<out-id> ...] --ref <permalink|message id> [--channel C] [--note N] [--at TIME]
                                               a message went out: the first (-> sent; fyi: ->
                                               applied) or, once sent, a follow-up/nudge (kept in
                                               a list of refs); resolves the pending proposal.
                                               Several ids: one message covering them all, recorded
                                               on each. Its time: --at, else a Slack permalink's
                                               own time, else now. --at with a ref already
                                               recorded corrects that message's time
  replied <out-id> --evidence <reply ref> --summary S [--at TIME]
                                               the reply (-> replied); says where it goes next.
                                               Its time: --at, else the Slack permalink's, else now
  TIME: ISO 8601 with an offset (2026-10-15T12:20+01:00, ...Z) or local 'YYYY-MM-DD HH:MM'; not
  in the future. Pass --at when it happened before you record it and the ref has no Slack time.
  outreach [--open] [--parent ID]              outreach with status, messages and replies
  in-flight <window>                           ids of the items <window> holds with work not done
  compact-context (--window <w> | --orchestrator) [--max-chars N]
                                               the block a session gets back after a compaction

  Hand a reply to the window holding the parent with `bosun delegate --update <out-id>`
  (-> applied); otherwise tell me and transition it to applied. Gave up after the last nudge:
  -> no-reply. Any state -> dropped. Its questions go into another message: fold <out-id> --into
  <other-id> (records folded_into); it asks exactly what another asks: --note 'duplicate of
  <other-id>' (records duplicate_of).

Tasks and links: work we decided to do that has no artifact of its own (kind task; ids
task:<YYYY-MM-DD>/<n>), and typed links between items (stored once, the inverse computed):

  spawn [<from-id>] --title T --ask A [--via V] [--requester R] [--repo R] [--area A]
        [--done-when D] [--blocks]
                                               record a proposed task spawned from <from-id>
                                               (--via: the message that asked for it, or 'user:
                                               <what I said>'; without <from-id>, --via is
                                               needed); prints its id first and warns about a
                                               similar open task from the same source. --blocks:
                                               <from-id> is not finished while the task is open.
                                               Only my word accepts it: transition <id> accepted
                                               --evidence 'user: ...'; delegate then -> delegated
  link <id> <rel> <other-id> [--via V]         rel: spawned_from, produces, blocked_by,
                                               duplicate_of, folded_into, tracks (<other-id> a
                                               ticket, created if new), relates_to. Refuses unknown
                                               ids, self-links and cycles
  unlink <id> <rel> <other-id>
  fold <id> --into <other-id> [--note N] [--force-closed-target] [--force-open-outreach]
                                               <id>'s work goes on as part of <other-id> (e.g. in
                                               its PR): records folded_into, withdraws <id>'s
                                               pending proposal and closes <id> in its kind's
                                               escape state (dropped, else dismissed) with the note
                                               'folded into <other-id>: N'. Refuses an open-outreach
                                               or blocked <id> as transition does, and a finished
                                               <other-id> without --force-closed-target. If
                                               <other-id> is with a window, prints the delegate
                                               --update that carries the work there. Idempotent.
                                               A drop note 'merged into <id>' or 'folded into <id>'
                                               records folded_into too; 'duplicate of <id>' records
                                               duplicate_of (the same item twice)
  tree <id>                                    lineage up and down (spawned, produces, outreach),
                                               with states, other links and hints
  migrate-links [--dry-run]                    one-shot: fields.origin naming an item ->
                                               spawned_from; drop notes 'merged into <id>' ->
                                               folded_into, 'duplicate of <id>' -> duplicate_of
                                               (also a duplicate_of recorded from a 'merged into'
                                               note). Idempotent

External tickets (kind ticket; ids jira:<KEY>-<n>, ghissue:<owner>/<repo>#<n>), a read-only
mirror of the tracker:

  ticket-sync <ticket-id> --status RAW [--category C] [--title T] [--url U] [--assignee A]
                                               record what the tracker shows (created if new);
                                               the category comes from BOSUN_TICKET_STATUS_MAP,
                                               then the built-in status names, then --category
                                               (ours, or the tracker's own: Jira statusCategory)
  tickets [--all] [--mismatch]                 tickets of open work, how to read each, and where
                                               the ticket and our work disagree (proposals only;
                                               for a ticket outside BOSUN_TICKET_OWNED_PROJECTS,
                                               FYI lines: another team's to move, never ours)
  ticket-sync ... --parent <key>               also records the ticket's parent (Jira's parent
                                               field: the epic of a story), for workstreams

Workstreams: a time-bound effort I drive (kind workstream; ids ws:<slug>), grouping items for
stand-up summaries and stats (docs/design/workstreams.md):

  ws add <slug> [--title T] [--epic <ticket>] [--channel <Slack channel id> ...] [--repo R ...]
         [--started D] [--ended D] [--target D] [--area <knowledge entity>] [--parent <ws>]
         [--alias A ...]
                                               create (needs --title) or update the fields given;
                                               --parent: it is part of another workstream (two
                                               levels at most). Its epic and channel assign items
  ws list [--all]                              workstreams (done ones with --all), with counts by
                                               how items were assigned, and the unassigned count
  ws show <ws>                                 fields, the workstreams under it, items by kind
  ws set-state <ws> active|paused|done --note N
  ws alias <ws> <alias> [--remove]             another name it answers to (any <ws> argument
                                               takes the id, the slug or an alias)
  link <id> part_of <ws> [--move]              the item's one workstream (refuses a second unless
                                               --move, which leaves the old one; relates_to for a
                                               looser tie). Tag the root: what came from it
                                               inherits it
  assign [--dry-run|--apply] [--ws W]          make epic and rule assignments explicit on the
                                               topmost items (dry run by default); uncertain
                                               cases are printed as proposals
  summary [--ws W|--all] [--since S] [--markdown]
                                               a stand-up draft: shipped and decisions since S
                                               (a date, an ISO time; default: this Monday), and
                                               now in review, waiting on others, blocked, next;
                                               --all: per workstream, then the unassigned
  lint [--json] [--strict]                     doubtful assignments: evidence that disagrees, a
                                               child tagged differently from its parent, a
                                               growing unassigned count (--strict: exit 1)
  list --ws <ws|unassigned>                    only the items in that workstream (or under it)
  Resolution order: part_of > inherited (spawned_from, produced_by, outreach parent, then folded
  into) > a tracked ticket whose epic a workstream names > rules in
  $BOSUN_CONFIG_DIR/workstreams.json and a workstream's channel > unassigned. `show` says
  which (derived.workstream).

State lives in $BOSUN_STATE_DIR/ledger.json; ledger.md is a readable view of it.
review: and mypr: items are only created for repositories matching BOSUN_REPO_MASK.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity
import approvals
import claude_state
import compact_context
import event_model
import events
import lineage
import redact
import repo_mask
import tickets
import workstreams
import ws_summary
from lineage import Index, LineageError, Link
from tickets import TicketError
from workstreams import WorkstreamError


DEFAULT_PRUNE_DAYS = 30
DEFAULT_TICKET_STALE_HOURS = 24.0
CHECKIN_OVERLAP = timedelta(minutes=15)
# The Slack scan window (BOSUN_SLACK_LOOKBACK_HOURS, BOSUN_SLACK_MAX_LOOKBACK_HOURS).
DEFAULT_LOOKBACK_HOURS = 24.0
DEFAULT_MAX_LOOKBACK_HOURS = 96.0
SCAN_RUNS = ("check-in", "morning")
# History per item: the first entry, a trim marker and the newest entries beyond this.
MAX_HISTORY = 200
# Report fields are one line each; detail belongs in the PR, the review or a report file.
MAX_SUMMARY_LENGTH = 500
MAX_RESULT_LENGTH = 300
# A report's CI status (`report --ci`): what the window saw on its pushed commit. Standing approvals
# (lib/approvals.py) need green on the PR's latest report.
CI_STATES = ("green", "red", "pending", "none")
COMPACT_ROW_CHARS = 220  # one row of the post-compaction block (lib/compact_context.py)
# History events that hand work to a repo session. A hand-over ends with a report, or when the item
# reaches a state that shows the work done (the kind's `work_done`, or a terminal state).
HANDOVER_EVENTS = ("delegated", "update", "moved")
# What the task needs to be sent again (to another window), kept with the delegation.
TASK_HEADER_FIELDS = ("ask", "link", "requester", "details")
REPO_ITEM_ID = re.compile(r"^(?:review|mypr|ghissue):([^/#]+/[^#]+)#\d+$")
FYI_CLASS = "fyi"
# Outreach: the addressee, subject, options, refs and answer are one line each; details may run on.
MAX_LINE_LENGTH = 500
MAX_DETAILS_LENGTH = 4000
# Where an outreach message goes, and what its --ref is. A new channel is one more row here, plus
# its row in the send/reply table of orchestrator/plugin/commands/chores.md.
CHANNELS = {
    "slack": "a Slack message or thread reply; ref: its permalink",
    "email": "an email; ref: its message id, or subject and sent time",
    "jira": "a Jira comment; ref: the comment URL",
    "github": "a GitHub PR or issue comment; ref: the comment URL",
    "confluence": "a Confluence page comment; ref: the comment URL",
    "other": "anything else, e.g. said in a meeting; ref: where and when",
}
# info-ack: they confirm they saw it (a reply is expected); fyi: one-way, done once sent.
PURPOSES = ("question", "decision", "clarification", "approval", "review", "info-ack", "fyi")
ONE_WAY_PURPOSE = "fyi"
REVIEW_PURPOSE = "review"  # a review request: owed no more once its PR merged or closed
# The one-line separator of --field options=... on upsert, as the outreach listing shows them.
OPTIONS_SEPARATOR = "|"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400
WEEKEND_START = 5  # date.weekday(): Monday is 0, Saturday 5
# Slack permalinks: .../archives/<channel>/p<ts without the dot>[?thread_ts=<ts>&cid=<channel>]
SLACK_PERMALINK = re.compile(r"/archives/(?P<channel>[A-Z0-9]+)/p(?P<ts>\d{7,})")
SLACK_THREAD_TS = re.compile(r"[?&]thread_ts=(?P<ts>\d+\.\d+)")
SLACK_TS_DECIMALS = 6
DM_CHANNEL_PREFIX = "D"
# --at: when a message went out or a reply came, if earlier than its recording. A date and a time
# (no seconds needed); without an offset it is local time.
GIVEN_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
CLOCK_TIME = re.compile(r"^\d{1,2}:\d{2}$")  # --since 10:40: today, local time
GIVEN_TIME_HELP = "an ISO 8601 time with an offset (2026-10-15T12:20+01:00) or local 'YYYY-MM-DD HH:MM'"
# Where a message or reply time came from, kept in the history entry next to it (time_from).
TIME_FROM_OPTION = "--at"
TIME_FROM_SLACK = "slack ref"

Item = dict[str, Any]
Ledger = dict[str, Any]


@dataclass(frozen=True)
class ScanWindow:
    """The morning lookback and the cap after a long absence, in hours."""

    lookback_hours: float = DEFAULT_LOOKBACK_HOURS
    max_lookback_hours: float = DEFAULT_MAX_LOOKBACK_HOURS


@dataclass
class Store:
    """Where the ledger lives and which repositories it tracks. Set by configure(): main() takes
    them from the environment (`bosun ledger` passes them)."""

    state_dir: Path = Path()
    mask: str = ""
    # BOSUN_TICKET_STATUS_MAP and BOSUN_TICKET_STALE_HOURS (tickets).
    status_map: str = ""
    stale_hours: float = DEFAULT_TICKET_STALE_HOURS
    # BOSUN_EVENTS_ENABLED: append events for the orchestrator (lib/events.py); and the tmux
    # window the command runs in, if any (`bosun ledger` finds it), as their origin.
    events_enabled: bool = False
    caller_window: str = ""
    # How far back a chores run's Slack scan reaches (configure_scan).
    scan: ScanWindow = ScanWindow()
    # BOSUN_CONFIG_DIR: where the site's workstream rules live (workstreams.json); unset: none.
    config_dir: Path | None = None
    # BOSUN_TICKET_OWNED_PROJECTS (tickets.parse_owned): empty, every project is ours.
    owned_projects: frozenset[str] = frozenset()
    # BOSUN_ORCHESTRATOR_DIR: whose transcripts resolve-carried-out reads; unset: none.
    orchestrator_dir: str = ""

    @property
    def rules_file(self) -> Path | None:
        return self.config_dir / workstreams.RULES_FILE if self.config_dir else None

    @property
    def ledger_file(self) -> Path:
        return self.state_dir / "ledger.json"

    @property
    def view(self) -> Path:
        return self.state_dir / "ledger.md"

    @property
    def lock(self) -> Path:
        return self.state_dir / "ledger.lock"

    @property
    def backup(self) -> Path:
        """The previous version of ledger.json, kept by every write."""
        return self.state_dir / "ledger.json.bak"


STORE = Store()
# A repo window's live Claude status, by window name (tests replace it).
STATUS_OF: activity.StatusOf = claude_state.live_status


def configure(
    state_dir: str | os.PathLike[str],
    mask: str = "",
    status_map: str = "",
    stale_hours: float = DEFAULT_TICKET_STALE_HOURS,
    *,
    events_enabled: bool = False,
    caller_window: str = "",
) -> None:
    """Point the ledger at <state_dir> and set the repo mask, ticket and event settings, and the
    default scan window and no site config dir (used by main and by tests)."""
    STORE.config_dir = None
    STORE.owned_projects = frozenset()
    STORE.orchestrator_dir = ""
    STORE.state_dir = Path(state_dir)
    STORE.mask = mask
    STORE.status_map = status_map
    STORE.stale_hours = stale_hours
    STORE.events_enabled = events_enabled
    STORE.caller_window = caller_window
    STORE.scan = ScanWindow()


def configure_site(config_dir: str | os.PathLike[str] | None) -> None:
    """Set the site's config dir (BOSUN_CONFIG_DIR), where workstreams.json lives."""
    STORE.config_dir = Path(config_dir) if config_dir else None


def configure_tickets(owned_projects: str, orchestrator_dir: str = "") -> None:
    """Set the projects we own (BOSUN_TICKET_OWNED_PROJECTS) and the orchestrator's folder
    (BOSUN_ORCHESTRATOR_DIR, where resolve-carried-out finds its transcripts)."""
    STORE.owned_projects = tickets.parse_owned(owned_projects)
    STORE.orchestrator_dir = orchestrator_dir


def configure_scan(lookback_hours: float, max_lookback_hours: float) -> None:
    """Set the Slack scan window (BOSUN_SLACK_LOOKBACK_HOURS, ..._MAX_LOOKBACK_HOURS)."""
    STORE.scan = ScanWindow(lookback_hours, max_lookback_hours)


def emit(kind: str, item: str = "", window: str = "", summary: str = "", detail: str = "") -> None:
    """With events on, queue an event for the orchestrator; a failure never fails the command.
    <detail> is what the orchestrator needs besides the summary to act on the event's line."""
    if not STORE.events_enabled:
        return
    try:
        event = event_model.new_event(
            kind,
            item=item,
            window=window,
            origin=STORE.caller_window,
            summary=summary,
            detail=detail,
        )
        events.append(STORE.state_dir, event)
    except (OSError, event_model.EventsError) as exc:
        print(f"ledger: warning: could not queue the {kind} event for {item}: {exc}", file=sys.stderr)


# A re-review must point at a request made after my last review.
REREVIEW_EVIDENCE = (
    "a new, explicit request (e.g. GitHub re-requested review at <time>, or Slack <permalink> after "
    "the last review). An old message still in the scan window is not evidence."
)
USER_EVIDENCE = "user:"
REPLY_EVIDENCE = (
    "a reference to the reply on its channel (Slack permalink, email message id, comment URL), "
    "or, if I told you, 'user: <what I said>'"
)

# kind -> id prefix, the Slack class whose messages link to it, initial state, terminal states,
# allowed transitions (from -> set of to), the transitions that must carry --evidence (and what
# counts), fields a state needs, states entered only with a pending proposal, the "escape"
# state that is reachable from every other state, and the terminal states that record a fact
# (a PR merged or closed) and so are allowed while outreach is open (`facts`).
KINDS: dict[str, dict[str, Any]] = {
    "review-request": {
        "description": "Someone asked me to review their PR.",
        "id_format": "review:<owner>/<repo>#<number>",
        "source_class": "pr-review",
        "initial": "requested",
        "terminal": {"closed", "dismissed"},
        "facts": {"closed"},
        "transitions": {
            "requested": {"delegated", "reviewed", "closed"},
            # back to requested when handing it to a repo session failed
            "delegated": {"requested", "reviewed", "closed"},
            "reviewed": {"rereview-requested", "closed"},
            "rereview-requested": {"delegated", "reviewed", "closed"},
            "dismissed": {"requested"},
        },
        "evidence_required": {("reviewed", "rereview-requested")},
        "evidence": REREVIEW_EVIDENCE,
        "work_done": {"reviewed"},
    },
    "td-review": {
        "description": "Someone asked me to review a tech design (usually a Confluence page).",
        "id_format": "td:<confluence-page-id>",
        "source_class": "td-review",
        "initial": "requested",
        "terminal": {"closed", "dismissed"},
        "transitions": {
            "requested": {"prepared", "commented", "closed"},
            "prepared": {"commented", "closed"},
            "commented": {"rereview-requested", "closed"},
            "rereview-requested": {"prepared", "commented", "closed"},
            "dismissed": {"requested"},
        },
        "evidence_required": {("commented", "rereview-requested")},
        "evidence": REREVIEW_EVIDENCE,
        "work_done": {"commented"},
    },
    "my-pr": {
        "description": "My own open PR, tracked until merged or closed.",
        "id_format": "mypr:<owner>/<repo>#<number>",
        "source_class": "my-pr",
        "initial": "awaiting-review",
        "terminal": {"merged", "closed", "dismissed"},
        "facts": {"merged", "closed"},
        "transitions": {
            "awaiting-review": {"changes-requested", "approved", "merged", "closed"},
            "changes-requested": {"addressing", "awaiting-review", "approved", "merged", "closed"},
            "addressing": {"awaiting-review", "changes-requested", "approved", "merged", "closed"},
            "approved": {"changes-requested", "awaiting-review", "merged", "closed"},
            "dismissed": {"awaiting-review"},
        },
        "evidence_required": set(),
        "work_done": {"awaiting-review", "approved"},
    },
    "mention": {
        "description": "Any other mention that may need my attention.",
        "id_format": "slack:<channel-id>/<message-ts>",
        "source_class": "mention",
        "initial": "new",
        "terminal": {"done", "dismissed"},
        "transitions": {
            "new": {"surfaced", "done"},
            "surfaced": {"done"},
            "dismissed": {"new"},
        },
        "evidence_required": set(),
        "work_done": set(),
    },
    "outreach": {
        "description": (
            "Something sent to someone outside this machine that we wait on: a question, decision, "
            "clarification, approval, review or acknowledgement, by Slack, email, a Jira/GitHub/"
            "Confluence comment, or other. Create it with `ledger reach-out` (alias `ask`); record "
            "each message with `ledger sent`, the reply with `ledger replied`. A one-way message "
            "(purpose fyi) waits on nobody: `ledger sent` moves it on to applied."
        ),
        "id_format": "out:<parent-id>/<n>, or out:<YYYY-MM-DD>/<n> for outreach that belongs to no item",
        "source_class": "outreach",
        "initial": "draft",
        "terminal": {"applied", "no-reply", "dropped"},
        "escape": "dropped",
        "transitions": {
            # proposed: the outgoing message is a proposal awaiting my approval (`ledger propose`
            # moves a draft there; rejecting or withdrawing the proposal moves it back)
            "draft": {"proposed", "sent", "replied"},
            "proposed": {"draft", "sent", "replied"},
            # no-reply: given up after the last nudge (my call); a late reply still counts.
            # applied: only a one-way message (purpose fyi), which needs no reply
            "sent": {"replied", "no-reply", "applied"},
            # back to sent: the reply raised a follow-up in the same conversation
            "replied": {"sent", "applied"},
            "no-reply": {"replied"},
        },
        "evidence_required": {(state, "replied") for state in ("draft", "proposed", "sent", "no-reply")},
        "evidence": REPLY_EVIDENCE,
        "required_fields": {"sent": ("messages",), "replied": ("answer",)},
        "requires_proposal": {"proposed"},
        "work_done": set(),
    },
    "learning": {
        "description": (
            "A change `bosun learn` proposes from what it saw in recent sessions (a memory note, "
            "a rule, a setup change, outreach), with its evidence; created with the change as its "
            "pending proposal. Close it only with `bosun learn apply|done|reject <id>`."
        ),
        "id_format": "learn:<YYYY-MM-DD>/<n>",
        "source_class": "learning",
        "initial": "proposed",
        "terminal": {"applied", "rejected", "dismissed"},
        "transitions": {"proposed": {"applied", "rejected"}, "rejected": {"proposed"}},
        "evidence_required": set(),
        "work_done": set(),
    },
    "task": {
        "description": (
            "Work we decided to do that has no artifact of its own yet: what a colleague asked of us "
            "in a reply or mention, what I told you to do, a follow-up a repo session found. Create "
            "it with `ledger spawn <from-id>` (provenance: spawned_from). Only my word accepts it; "
            "`bosun delegate` moves an accepted task to delegated (as new-work), and its PR is "
            "linked with `ledger link <task> produces <mypr>`. Done when what it produced landed, or "
            "its report answered the ask."
        ),
        "id_format": "task:<YYYY-MM-DD>/<n>",
        "source_class": "task",
        "initial": "proposed",
        "terminal": {"done", "declined", "dropped"},
        "escape": "dropped",
        "transitions": {
            "proposed": {"accepted", "declined"},
            # accepted -> delegated: `bosun delegate` (record-delegation) does it
            "accepted": {"delegated", "done"},
            # back to accepted: taken back from the window, to hand over again
            "delegated": {"accepted", "done"},
            "declined": {"proposed"},
        },
        "evidence_required": {("proposed", "accepted")},
        "evidence": "my approval, as 'user: <what I said>' (someone else's message never accepts work)",
        "evidence_from_me": {"accepted"},
        # entering accepted carries out the pending proposal; declined rejects it
        "resolves_proposal": {"accepted": "done", "declined": "rejected"},
        "work_done": set(),
    },
    "ticket": {
        "description": (
            "An item in an external tracker (Jira, GitHub issues), mirrored read-only: its state is "
            "the tracker's status category, recorded only by `ledger ticket-sync` from what the "
            "orchestrator read there (never `transition`). Work items point at it with `ledger link "
            "<id> tracks <ticket-id>`; where they disagree, `ledger tickets --mismatch` names the "
            "change to propose. Bosun never changes the ticket itself."
        ),
        "id_format": "<system>:<key>: jira:<PROJECT>-<n>, ghissue:<owner>/<repo>#<n>",
        "source_class": "ticket",
        "initial": tickets.UNSYNCED,
        "terminal": set(tickets.CLOSED),
        "escape": "cancelled",
        "facts": set(tickets.CLOSED),
        "mirror": True,
        "transitions": {
            state: set(tickets.CATEGORIES) - {state} for state in (tickets.UNSYNCED, *tickets.CATEGORIES)
        },
        "evidence_required": set(),
        "work_done": set(),
    },
    "workstream": {
        "description": (
            "A time-bound effort I drive (e.g. 'Widget search'), grouping items for "
            "stand-up summaries and stats. Created with `ledger ws add <slug> --title ...`; an item "
            "joins one with `ledger link <id> part_of <ws>` (one per item; tag the root, what came "
            "from it inherits). Not a Jira epic (a workstream may name one with --epic) and not a "
            "product area (it is part of one, --area). `ledger ws set-state` pauses or finishes it."
        ),
        "id_format": "ws:<slug>",
        "source_class": "workstream",
        "initial": workstreams.ACTIVE,
        "terminal": {workstreams.DONE},
        "escape": workstreams.DONE,
        "transitions": {
            workstreams.ACTIVE: {workstreams.PAUSED, workstreams.DONE},
            workstreams.PAUSED: {workstreams.ACTIVE, workstreams.DONE},
            workstreams.DONE: {workstreams.ACTIVE},
        },
        "evidence_required": set(),
        "work_done": set(),
    },
}
OUTREACH_KIND = "outreach"
WORKSTREAM_KIND = "workstream"
LEARNING_KIND = "learning"
TASK_KIND = "task"
TICKET_KIND = "ticket"
TASK_ID = re.compile(r"^task:\d{4}-\d{2}-\d{2}/[1-9]\d*$")
# A report result that is a GitHub PR: the PR a task (or other work) produced.
PR_URL = re.compile(r"https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/pull/(?P<number>\d+)(?:[/?#\s]|$)")
# A drop note that names what took the item over, recorded as a link too: its work went on as part
# of the other item (folded_into), or it was the same item twice (duplicate_of).
MERGED_INTO = re.compile(r"\b(?P<how>merged into|folded into|duplicate of)\s+'?(?P<id>[^\s']+)")
NOTE_RELATIONS = {
    "merged into": lineage.FOLDED_INTO,
    "folded into": lineage.FOLDED_INTO,
    "duplicate of": lineage.DUPLICATE_OF,
}
# The delegate command in a proposal: the work it stood for.
DELEGATE_PROPOSAL = "bosun delegate"
# Kinds whose report results name their own PR or review, not new work.
OWN_PR_KINDS = ("my-pr", "review-request")
# out:<scope>/<n>: the scope is the parent's id, or the day it was created for outreach without one
# (no item id looks like a date: every one starts with its kind's prefix and a colon).
OUTREACH_ID = re.compile(r"^out:(?P<scope>.+)/(?P<number>[1-9]\d*)$")
PROPOSAL_OUTCOMES = ("done", "rejected", "withdrawn")
SOURCE_CLASSES = (*(spec["source_class"] for spec in KINDS.values()), FYI_CLASS)


def id_prefix(kind: str) -> str:
    return str(KINDS[kind]["id_format"]).split(":", 1)[0] + ":"


class LedgerError(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_given_time(value: str, option: str = TIME_FROM_OPTION) -> str:
    """A time given on the command line, as stored (UTC, like now()); refuses garbage and the future."""
    text = value.strip()
    try:
        if not GIVEN_TIME.match(text):
            raise ValueError(text)
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LedgerError(f"{option} takes {GIVEN_TIME_HELP}, not {value!r}") from exc
    moment = moment.astimezone(UTC)  # a naive time is local time
    if moment > datetime.now(UTC):
        raise LedgerError(f"{option} {value!r} is in the future; it records when something already happened")
    return moment.isoformat(timespec="seconds")


def slack_time(ref: str) -> str | None:
    """When a Slack message was posted, from its permalink's own ts (p<ts>, not thread_ts); never
    later than now (clock skew)."""
    message = slack_message(ref)
    if message is None:
        return None
    return min(datetime.fromtimestamp(message[2], UTC), datetime.now(UTC)).isoformat(timespec="seconds")


def event_time(given: str | None, ref: str) -> tuple[str, dict[str, str]]:
    """When a message went out or a reply came: --at, else a Slack ref's own time, else now; with
    the history fields that say where a time not taken at recording came from."""
    if given:
        moment = parse_given_time(given)
        return moment, {"happened_at": moment, "time_from": TIME_FROM_OPTION}
    from_ref = slack_time(ref)
    if from_ref:
        return from_ref, {"happened_at": from_ref, "time_from": TIME_FROM_SLACK}
    return now(), {}


def time_hint(provenance: dict[str, str]) -> str:
    """The outcome's note on a time not taken at recording, e.g. "; at 2026-10-15T11:20:59+00:00 (--at)"."""
    return f"; at {provenance['happened_at']} ({provenance['time_from']})" if provenance else ""


def parse_fields(pairs: list[str] | None) -> dict[str, str]:
    fields = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise LedgerError(f"--field expects key=value, got {pair!r}")
        fields[key] = value
    return fields


@contextlib.contextmanager
def locked_ledger(write: bool) -> Iterator[Ledger]:
    STORE.state_dir.mkdir(parents=True, exist_ok=True)
    with STORE.lock.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        ledger = load()
        yield ledger
        if write:
            save(ledger)


def read_ledger_file() -> Any:
    """The parsed ledger file, {} if there is none yet. A missing file next to a backup is an
    error, not a fresh start, so a lost file never silently empties the ledger."""
    restore = f"restore it from {STORE.backup} (the previous version) or move it aside"
    try:
        return json.loads(STORE.ledger_file.read_text())
    except FileNotFoundError:
        if STORE.backup.exists():
            raise LedgerError(
                f"{STORE.ledger_file} is missing but {STORE.backup} exists; restore it"
            ) from None
        return {}
    except json.JSONDecodeError as exc:
        raise LedgerError(f"{STORE.ledger_file} is not valid JSON ({exc}); {restore}") from exc


def load() -> Ledger:
    ledger = read_ledger_file()
    if not isinstance(ledger, dict) or not all(
        isinstance(ledger.get(key, {}), dict) for key in ("items", "sources", "runs")
    ):
        raise LedgerError(
            f"{STORE.ledger_file} is not a ledger (unexpected structure); restore it or move it aside"
        )
    ledger.setdefault("version", 1)
    ledger.setdefault("items", {})
    ledger.setdefault("sources", {})
    ledger.setdefault("runs", {})
    return ledger


def write_atomically(path: Path, text: str, backup: Path | None = None) -> None:
    """Write <text> to a temporary file, flush it to disk, keep the current file as <backup> (a
    hard link, so there is never a moment without <path>), then rename it into place."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if backup and path.exists():
        backup.unlink(missing_ok=True)
        os.link(path, backup)
    tmp.replace(path)


def save(ledger: Ledger) -> None:
    write_atomically(STORE.ledger_file, json.dumps(ledger, indent=2, sort_keys=True), STORE.backup)
    write_atomically(STORE.view, render(ledger))


def check_repo_mask(item_id: str) -> None:
    match = REPO_ITEM_ID.match(item_id)
    if match and not repo_mask.matches(match.group(1), STORE.mask):
        raise LedgerError(
            f"{item_id} is outside BOSUN_REPO_MASK ({STORE.mask}); it is not tracked. "
            "Record the Slack message as fyi and move on."
        )


def get_item(ledger: Ledger, item_id: str) -> Item:
    item: Item | None = ledger["items"].get(item_id)
    if item is None:
        raise LedgerError(f"no item {item_id!r}; create it with: bosun ledger upsert {item_id} --kind <kind>")
    return item


def is_open(item: Item) -> bool:
    return item["state"] not in KINDS[item["kind"]]["terminal"]


def record(item: Item, event: str, note: str = "", **extra: object) -> None:
    entry: dict[str, object] = {"at": now(), "event": event}
    if note:
        entry["note"] = note
    entry.update({k: v for k, v in extra.items() if v})
    item["history"].append(entry)
    trim_history(item)
    item["updated_at"] = entry["at"]


def trim_history(item: Item) -> None:
    """Keep at most MAX_HISTORY entries: the first ("created"), a marker counting what was
    dropped, and the newest ones."""
    history = item["history"]
    if len(history) <= MAX_HISTORY:
        return
    marker = history[1] if history[1].get("event") == "history-trimmed" else None
    keep = history[-(MAX_HISTORY - 2) :]
    dropped = len(history) - 1 - (1 if marker else 0) - len(keep) + int(marker["count"] if marker else 0)
    item["history"] = [history[0], {"at": keep[0]["at"], "event": "history-trimmed", "count": dropped}, *keep]


def escape_state(kind: str) -> str:
    """The state reachable from every other state of <kind> (dismissed; dropped for outreach)."""
    return str(KINDS[kind].get("escape", "dismissed"))


def allowed_targets(kind: str, state: str) -> set[str]:
    spec = KINDS[kind]
    targets = set(spec["transitions"].get(state, set()))
    if state != escape_state(kind):
        targets.add(escape_state(kind))
    return targets


def is_outreach(item: Item) -> bool:
    return bool(item["kind"] == OUTREACH_KIND)


def open_outreach(ledger: Ledger, parent_id: str) -> list[Item]:
    """The outreach <parent_id> waits on: not yet applied, given up or dropped; oldest first."""
    return outreach_by_parent(ledger["items"].values()).get(parent_id, [])


def outreach_by_parent(items: Iterable[Item]) -> dict[str, list[Item]]:
    """Open outreach grouped by the item it belongs to, oldest first."""
    grouped: dict[str, list[Item]] = {}
    for item in sorted(items, key=lambda i: str(i["created_at"])):
        if is_outreach(item) and is_open(item) and item["fields"].get("parent"):
            grouped.setdefault(item["fields"]["parent"], []).append(item)
    return grouped


def age(since: str) -> str:
    """How long ago <since> was, coarsely: 5m, 3h, 2d."""
    seconds = max(0, int((datetime.now(UTC) - parse_time(since)).total_seconds()))
    for unit, size in (("d", SECONDS_PER_DAY), ("h", SECONDS_PER_HOUR)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds // SECONDS_PER_MINUTE}m"


def business_days_since(since: str, today: date | None = None) -> int:
    """Weekdays after <since>'s date up to and including today (sent on Friday: 1 on Monday)."""
    start, end = parse_time(since).date(), today or datetime.now(UTC).date()
    days = (start + timedelta(days=offset) for offset in range(1, (end - start).days + 1))
    return sum(1 for day in days if day.weekday() < WEEKEND_START)


def last_message_at(messages: list[dict[str, Any]]) -> str:
    """When the latest message went out (by its time, not the order it was recorded in)."""
    return max((str(message["at"]) for message in messages), key=parse_time)


def outreach_status(item: Item) -> str:
    """One phrase for where an outreach stands, e.g. "slack to Dana, 2d, nudged 1x"."""
    fields, state = item["fields"], item["state"]
    how = f"{fields.get('channel', '?')} to {fields.get('to', '?')}"
    messages = fields.get("messages") or []
    if state == "sent" and messages:
        nudges = len(messages) - 1
        expected = f", expected by {fields['expect_by']}" if fields.get("expect_by") else ""
        return (
            f"{how}, {age(last_message_at(messages))}" + (f", nudged {nudges}x" if nudges else "") + expected
        )
    phrases = {
        "draft": f"draft, {how}",
        "proposed": f"{how}: message awaits approval",
        "replied": f"{how}: replied, not applied yet",
        "no-reply": f"{how}: no reply, given up",
    }
    return phrases.get(state, f"{how}: {state}")


# ---- commands -------------------------------------------------------------------------------


def new_item(args: argparse.Namespace, fields: dict[str, str]) -> Item:
    """A fresh item of args.kind, after checking its id format and the repo mask."""
    if args.kind == OUTREACH_KIND:
        raise LedgerError(
            "create outreach with: bosun ledger reach-out [<parent-id>] --channel <channel> "
            "--to <who> --purpose <purpose> --subject <text>"
        )
    check_new_id(args.kind, args.id)
    check_repo_mask(args.id)
    return fresh_item(args.id, args.kind, args.title or "", args.url or "", fields)


def check_new_id(kind: str, item_id: str) -> None:
    """Refuse an id that does not fit its kind's format."""
    if kind == TICKET_KIND:
        tickets.parse_id(item_id)
        return
    if kind == WORKSTREAM_KIND and (
        not item_id.startswith(workstreams.PREFIX) or workstreams.ws_id(item_id) != item_id
    ):
        raise LedgerError(f"workstream ids look like ws:<slug>, not {item_id!r}")
    prefix = id_prefix(kind)
    if (
        not item_id.startswith(prefix)
        or len(item_id) == len(prefix)
        or (kind == TASK_KIND and not TASK_ID.match(item_id))
    ):
        raise LedgerError(f"{kind} ids look like {KINDS[kind]['id_format']}, not {item_id!r}")


def fresh_item(item_id: str, kind: str, title: str, url: str, fields: dict[str, Any], note: str = "") -> Item:
    """A new item in its kind's initial state, with its "created" history entry."""
    if kind == TICKET_KIND:
        ticket = tickets.parse_id(item_id)
        fields = {"system": ticket.system, "key": ticket.key, "project": ticket.project, **fields}
    item: Item = {
        "id": item_id,
        "kind": kind,
        "state": KINDS[kind]["initial"],
        "title": title,
        "url": url,
        "fields": fields,
        "proposal": None,
        "created_at": now(),
        "history": [],
    }
    record(item, "created", note, state=item["state"])
    return item


def update_item(item: Item, args: argparse.Namespace, fields: dict[str, Any]) -> str:
    """Apply new fields, title and url to an existing item; returns "updated" or "unchanged"."""
    if args.kind and item["kind"] != args.kind:
        raise LedgerError(f"{args.id} is a {item['kind']}, not a {args.kind}")
    changed = {k: v for k, v in fields.items() if item["fields"].get(k) != v}
    for attribute in ("title", "url"):
        value = getattr(args, attribute)
        if value and value != item[attribute]:
            item[attribute] = changed[attribute] = value
    item["fields"].update(fields)
    if changed:
        record(item, "updated", fields=changed)
    return "updated" if changed else "unchanged"


def cmd_upsert(args: argparse.Namespace) -> str:
    if args.kind and args.kind not in KINDS:
        raise LedgerError(f"unknown kind {args.kind!r}; kinds: {', '.join(KINDS)}")
    fields = parse_fields(args.field)
    hint = ""
    with locked_ledger(write=True) as ledger:
        item = ledger["items"].get(args.id)
        if item is None:
            if not args.kind:
                raise LedgerError(
                    f"no item {args.id!r}; give --kind to create it (kinds: {', '.join(KINDS)})"
                )
            item = ledger["items"][args.id] = new_item(args, fields)
            outcome = "created"
        elif is_outreach(item):
            outcome = update_item(item, args, outreach_upsert_fields(fields, args))
            hint = one_way_hint(item)
        else:
            outcome = update_item(item, args, fields)
    return f"{outcome}: {args.id} [{item['kind']}] state={item['state']}{hint}"


def check_transition(item: Item, target: str, evidence: str | None, fields: dict[str, Any]) -> None:
    """Refuse a state change the kind does not allow, or one missing its evidence, fields or
    pending proposal."""
    spec, current = KINDS[item["kind"]], item["state"]
    if spec.get("mirror"):
        raise LedgerError(
            f"{item['id']} mirrors its tracker; its state changes only with what you read there: "
            f"bosun ledger ticket-sync '{item['id']}' --status '<status as the tracker shows it>'"
        )
    targets = allowed_targets(item["kind"], current)
    if target not in targets:
        raise LedgerError(
            f"{item['id']}: {current} -> {target} is not allowed for {item['kind']}; "
            f"allowed: {', '.join(sorted(targets))}"
        )
    if (current, target) in spec["evidence_required"] and not evidence:
        raise LedgerError(f"{item['id']}: {current} -> {target} needs --evidence: {spec['evidence']}")
    if target in spec.get("evidence_from_me", ()) and not (evidence or "").startswith(USER_EVIDENCE):
        raise LedgerError(
            f"{item['id']}: {target} needs my approval as --evidence '{USER_EVIDENCE} <what I said>'; "
            "a colleague's message never accepts work: propose it to me first (bosun ledger propose)"
        )
    if is_outreach(item):
        check_one_way(item, target)
    check_state_needs(item, target, fields)


def check_state_needs(item: Item, target: str, fields: dict[str, Any]) -> None:
    """Refuse to enter a state without the fields it needs, or the pending proposal it stands for."""
    spec = KINDS[item["kind"]]
    missing = [
        name
        for name in spec.get("required_fields", {}).get(target, ())
        if not fields.get(name) and not item["fields"].get(name)
    ]
    if missing:
        command = OUTREACH_COMMANDS.get(target) if is_outreach(item) else None
        raise LedgerError(
            f"{item['id']}: {target} needs {', '.join(f'--field {name}=...' for name in missing)}"
            + (f"; use: bosun ledger {command}" if command else "")
        )
    if target in spec.get("requires_proposal", ()) and not item.get("proposal"):
        raise LedgerError(
            f"{item['id']}: {target} means a message awaits my approval; record it with "
            f"bosun ledger propose '{item['id']}' '<the exact message and where it goes>', "
            f"which moves the item to {target}"
        )


def transition_item(
    item: Item, target: str, note: str, evidence: str | None, fields: dict[str, Any], **extra: str
) -> str:
    """Validate and apply a state change, recorded in history (plus <extra>); returns the outcome line."""
    current = item["state"]
    if target == current:
        return f"unchanged: {item['id']} already {current}"
    check_transition(item, target, evidence, fields)
    item["state"] = target
    item["fields"].update(fields)
    record(item, "transition", note, **{"from": current, "to": target, "evidence": evidence}, **extra)
    settle_proposal(item, target)
    return f"ok: {item['id']} {current} -> {target}"


def settle_proposal(item: Item, target: str) -> None:
    """A state that carries out (or rejects) the pending proposal closes it as such; a terminal
    state withdraws it. The action is kept in the history."""
    proposal = item.get("proposal")
    if not proposal:
        return
    outcome = KINDS[item["kind"]].get("resolves_proposal", {}).get(target)
    if outcome:
        item["proposal"] = None
        record(item, f"proposal-{outcome}", f"item moved to {target}", action=proposal["action"])
    elif target in KINDS[item["kind"]]["terminal"]:
        item["proposal"] = None
        record(item, "proposal-withdrawn", "item reached a terminal state", action=proposal["action"])


def cmd_transition(args: argparse.Namespace) -> str:
    fields = parse_fields(args.field)
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        note = check_open_outreach(ledger, item, args.state, args.note, args.force_open_outreach)
        outcome = transition_item(item, args.state, note, args.evidence, fields)
        outcome += close_review_outreach(ledger, item, args.state)
        return outcome + link_merged_into(ledger, item, args.state, args.note) + applied_hint(ledger, item)


def check_open_outreach(ledger: Ledger, item: Item, target: str, note: str, force: bool) -> str:
    """Refuse to finish an item (a terminal state) while outreach it waits on, or an item it is
    blocked_by, is open, unless forced or the state records a fact (a PR merged or closed);
    returns the transition note (then naming what was left open)."""
    if is_outreach(item) or target not in KINDS[item["kind"]]["terminal"]:
        return note
    waiting = open_outreach(ledger, item["id"])
    blockers = lineage.open_blockers(ledger["items"], Index.of(ledger["items"]), item["id"], is_open)
    if not waiting and not blockers:
        return note
    if force or target in KINDS[item["kind"]].get("facts", set()):
        return f"{note} [finished while waiting on: {', '.join(o['id'] for o in [*waiting, *blockers])}]"
    raise LedgerError(
        " ".join(filter(None, [waiting_message(item, waiting), blocked_message(item, blockers)]))
    )


def close_review_outreach(ledger: Ledger, item: Item, target: str) -> str:
    """When <item> reaches a fact state (a PR merged or closed), drop its open review requests:
    nobody owes that review any more (a pending proposal is withdrawn with it; a reply already in
    stays in the history). Other outreach stays open: a question may still matter. Returns the
    outcome lines."""
    if target not in KINDS[item["kind"]].get("facts", set()):
        return ""
    lines = []
    for outreach in open_outreach(ledger, item["id"]):
        if outreach["fields"].get("purpose") != REVIEW_PURPOSE:
            continue
        note = f"{item['id']} is {target}: the review it asked for is no longer owed"
        lines.append(transition_item(outreach, KINDS[OUTREACH_KIND]["escape"], note, None, {}))
    return "".join(f"\n{line}" for line in lines)


def waiting_message(item: Item, waiting: list[Item]) -> str:
    if not waiting:
        return ""
    names = ", ".join(f"{o['id']} ({outreach_status(o)})" for o in waiting)
    return (
        f"{item['id']} is waiting on: {names}. Hand a reply over (bosun delegate --update "
        "'<out-id>', or tell me and transition it to applied), or drop outreach that no longer "
        "matters (bosun ledger transition '<out-id>' dropped --note '<why>'). To finish "
        f"{item['id']} anyway, add --force-open-outreach; the note says why."
    )


def blocked_message(item: Item, blockers: list[Item]) -> str:
    if not blockers:
        return ""
    names = ", ".join(f"{b['id']} ({b['kind']}: {b['state']})" for b in blockers)
    return (
        f"{item['id']} is blocked by: {names}. Finish or drop it first, or, if it no longer blocks: "
        f"bosun ledger unlink '{item['id']}' blocked_by '<id>'. To finish {item['id']} anyway, add "
        "--force-open-outreach; the note says why."
    )


def note_link(note: str) -> tuple[str, str] | None:
    """What a drop note says took the item over, as (phrase, id): 'merged into <id>', 'folded into
    <id>' or 'duplicate of <id>' (NOTE_RELATIONS maps the phrase to its relation)."""
    match = MERGED_INTO.search(note)
    return (match["how"], match["id"].rstrip(".,;)")) if match else None


def link_merged_into(ledger: Ledger, item: Item, target: str, note: str) -> str:
    """Dropped with 'merged into <id>' or 'folded into <id>' (folded_into), or 'duplicate of <id>'
    (duplicate_of): also record the link."""
    named = note_link(note)
    if target != escape_state(item["kind"]) or not named:
        return ""
    rel, other = NOTE_RELATIONS[named[0]], named[1]
    new = Link(item["id"], rel, other, at=now())
    if other not in ledger["items"] or other == item["id"]:
        return f"\nnote: {other} is not a ledger item, so no {rel} link was recorded"
    try:
        if not lineage.check_link(ledger["items"], new):
            return ""
    except LineageError as exc:
        return f"\nnote: no {rel} link recorded: {exc}"
    lineage.add_link(item, new)
    record(item, "linked", f"{rel} {other}")
    return f"; linked {item['id']} {rel} {other}"


def needs_work_hint(ledger: Ledger, item: Item) -> bool:
    """Applied outreach with a reply, no task spawned from it, and no window holding its parent
    (a window that holds the parent got the reply as an update)."""
    if not is_outreach(item) or item["state"] != "applied" or not item["fields"].get("answer"):
        return False
    parent = ledger["items"].get(item["fields"].get("parent") or "") or {}
    held = bool(parent.get("delegation")) and is_open(parent)
    return not held and not Index.of(ledger["items"]).into(item["id"], lineage.SPAWNED_FROM)


def applied_hint(ledger: Ledger, item: Item) -> str:
    """An outreach closed after a reply: if the reply asked us to do something (or a delegate
    proposal stood for it), the work needs a task of its own, not just this note."""
    if not needs_work_hint(ledger, item):
        return ""
    proposed_work = any(DELEGATE_PROPOSAL in str(h.get("note", "")) for h in item["history"])
    lead = (
        "a delegate command was proposed for its reply"
        if proposed_work
        else "if its reply asked us to do something"
    )
    return (
        f"\nnext: {lead}, record that work so it has an id: bosun ledger spawn '{item['id']}' "
        f"--title '<one line>' --ask '<one line>' --via '{item['fields'].get('reply_ref', '<reply ref>')}'"
        " (and link a PR that already exists: bosun ledger link '<task id>' produces 'mypr:...')"
    )


def check_one_line(option: str, value: str, limit: int) -> None:
    if not value.strip() or "\n" in value or len(value) > limit:
        raise LedgerError(
            f"{option} must be one non-empty line of at most {limit} characters; put the detail in "
            "the PR, the review or a report file, and pass its URL or path with --result"
        )


def check_report(args: argparse.Namespace) -> None:
    check_one_line("--summary", args.summary, MAX_SUMMARY_LENGTH)
    for result in args.result or []:
        check_one_line("--result", result, MAX_RESULT_LENGTH)
    if args.open is not None:
        check_one_line("--open", args.open, MAX_SUMMARY_LENGTH)
    if args.evidence and not args.state:
        raise LedgerError("--evidence goes with --state")


def cmd_report(args: argparse.Namespace) -> str:
    """Record a repo session's outcome as the item's latest report (and in its history); with
    --state, also make that transition."""
    check_report(args)
    fields = parse_fields(args.field)
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        moved = ""
        if args.state:
            note = check_open_outreach(ledger, item, args.state, args.summary, args.force_open_outreach)
            moved = transition_item(item, args.state, note, args.evidence, fields)
            moved += close_review_outreach(ledger, item, args.state)
        item["fields"].update(fields)
        window = (item.get("delegation") or {}).get("window")
        report = {"at": now(), "summary": args.summary, "results": args.result or [], "open": args.open or ""}
        ci = {"ci": args.ci} if args.ci else {}
        item["report"] = {**report, **ci, "window": window}
        record(
            item, "report", args.summary, results=report["results"], open=report["open"], window=window, **ci
        )
    emit("report", args.id, window or "", args.summary, report_detail(report, args.ci))
    return f"ok: report recorded on {args.id}" + (f"; {moved}" if moved else "")


def report_detail(report: Mapping[str, Any], ci: str | None) -> str:
    """What a report's event carries besides its summary: open points, results, CI."""
    parts = [f"open: {report['open']}"] if report["open"] else []
    parts += [f"result: {result}" for result in report["results"]]
    return "; ".join(parts + ([f"ci: {ci}"] if ci else []))


def parse_since(value: str) -> datetime:
    """An ISO time (naive: UTC), or HH:MM today in local time (as an events summary shows it)."""
    try:
        if CLOCK_TIME.match(value.strip()):
            hours, minutes = (int(part) for part in value.strip().split(":"))
            return datetime.now().astimezone().replace(hour=hours, minute=minutes, second=0, microsecond=0)
        moment = parse_time(value)
    except ValueError as exc:
        raise LedgerError(
            f"--since expects an ISO time like 2026-10-16T05:00:00+00:00, or HH:MM today, not {value!r}"
        ) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def report_lines(item: Item, index: Index | None = None) -> list[str]:
    report = item["report"]
    origin = f" from {report['window']}" if report.get("window") else ""
    lines = [
        f"{item['id']}  [{item['kind']}: {item['state']}] reported {report['at']}{origin}",
        f"  summary: {report['summary']}",
        *(f"  result:  {result}" for result in report["results"]),
        *([f"  ci:      {report['ci']}"] if report.get("ci") else []),
    ]
    if report.get("open"):
        lines.append(f"  open:    {report['open']}")
    return lines + (produced_pr_lines(item, index) + settle_lines(item, index) if index else [])


def produced_prs(item: Item) -> list[tuple[str, str]]:
    """(mypr id, URL) of each GitHub PR among the report's results, for work that is not itself
    a PR or a review."""
    if item["kind"] in OWN_PR_KINDS or not item.get("report"):
        return []
    found = (PR_URL.search(result) for result in item["report"]["results"])
    return [(f"mypr:{m['repo']}#{m['number']}", m.group(0).rstrip("/?# ")) for m in found if m]


def produced_pr_lines(item: Item, index: Index) -> list[str]:
    """For a PR in the report that is not linked yet (either way: it produced the PR, or the PR was
    spawned from it): the commands that record and link it, without re-creating a recorded PR."""
    linked = {link.target for link in index.out(item["id"], lineage.PRODUCES)}
    linked |= {link.source for link in index.into(item["id"], lineage.SPAWNED_FROM)}
    return [
        "  next:    "
        + (
            ""
            if index.has(pr_id)
            else f"bosun ledger upsert '{pr_id}' --kind my-pr --url '{url}' --title '<PR title>' && "
        )
        + f"bosun ledger link '{item['id']}' produces '{pr_id}'"
        for pr_id, url in produced_prs(item)
        if pr_id not in linked
    ]


def settle_lines(item: Item, index: Index) -> list[str]:
    """For a delegated task whose latest report produced nothing and leaves nothing open: the
    command that closes it (policy: settle a task's state when you act on its report)."""
    report = item.get("report") or {}
    if item["kind"] != "task" or item["state"] != "delegated" or not report or report.get("open"):
        return []
    if produced_prs(item) or index.out(item["id"], lineage.PRODUCES):
        return []
    note = f"report {report['at']} answered the ask"
    command = f"bosun ledger transition '{item['id']}' done --note '{note}'"
    return [f"  next:    the report answered the ask and leaves nothing open; close it: {command}"]


def cmd_reports(args: argparse.Namespace) -> str:
    """The latest report of each open item or, with --since, every report filed since then and
    what I said in a repo window about the item (including items a report itself closed)."""
    with locked_ledger(write=False) as ledger:
        items, said = ledger["items"], ledger.get("said", [])
        index = Index.of(items)
    if args.since:
        return "\n".join(reports_since(items, said, index, parse_since(args.since))) or "(no reports)"
    reported = [item for item in items.values() if item.get("report") and is_open(item)]
    chosen = sorted(reported, key=lambda item: str(item["report"]["at"]))
    return "\n".join(line for item in chosen for line in report_lines(item, index)) or "(no reports)"


def reports_since(
    items: dict[str, Item], said: list[dict[str, Any]], index: Index, since: datetime
) -> list[str]:
    """Every report since <since>, and every user-said entry, grouped by item: each item's
    entries in order, the items by their latest entry (the most recent last)."""
    timeline: dict[str, list[tuple[str, list[str]]]] = {}
    for item_id, at, block in [*report_blocks(items, since), *said_blocks(items, said, since)]:
        timeline.setdefault(item_id, []).append((at, block))
    ordered = sorted(timeline.items(), key=lambda pair: max(parse_time(at) for at, _ in pair[1]))
    lines: list[str] = []
    for item_id, entries in ordered:
        for _, block in sorted(entries, key=lambda pair: parse_time(pair[0])):
            lines += block
        lines += produced_pr_lines(items[item_id], index) + settle_lines(items[item_id], index)
    return lines


Block = tuple[str, str, list[str]]  # item id, when, lines


def report_blocks(items: dict[str, Item], since: datetime) -> list[Block]:
    return [
        (item["id"], entry["at"], report_entry_lines(item, entry))
        for item in items.values()
        for entry in item["history"]
        if entry["event"] == "report" and parse_time(entry["at"]) >= since
    ]


def said_blocks(items: dict[str, Item], said: list[dict[str, Any]], since: datetime) -> list[Block]:
    """What I said since <since>, under the item(s) it is recorded on (`ledger said <id>` also
    finds it under the other items the window held)."""
    recent = [entry for entry in said if parse_time(entry["at"]) >= since]
    return [
        (item_id, entry["at"], [said_line(items[item_id], entry)])
        for entry in recent
        for item_id in entry["items"]
        if item_id in items
    ]


def report_entry_lines(item: Item, entry: dict[str, Any]) -> list[str]:
    """One report from the item's history, shaped like report_lines()."""
    origin = f" from {entry['window']}" if entry.get("window") else ""
    lines = [
        f"{item['id']}  [{item['kind']}: {item['state']}] reported {entry['at']}{origin}",
        f"  summary: {entry.get('note', '')}",
        *(f"  result:  {result}" for result in entry.get("results") or []),
        *([f"  ci:      {entry['ci']}"] if entry.get("ci") else []),
    ]
    return lines + ([f"  open:    {entry['open']}"] if entry.get("open") else [])


def said_line(item: Item, entry: dict[str, Any]) -> str:
    """What I typed in a repo window, recorded on <item>."""
    how = f" (the window also held {', '.join(entry['also_held'])})" if entry.get("also_held") else ""
    return (
        f"{item['id']}  [{item['kind']}: {item['state']}] user said {entry['at']} in {entry['window']}{how}: "
        f"{json.dumps(activity.excerpt(entry['text']))}"
    )


def active_delegation(item: Item) -> dict[str, Any]:
    """The item's delegation, if a repo session is (still) working on it."""
    delegation: dict[str, Any] | None = item.get("delegation")
    if not delegation:
        raise LedgerError(
            f"{item['id']} was never handed to a repo session with bosun delegate, so there is "
            "no window to update. Delegate it first. If it was handed over before delegations "
            f"were recorded: bosun ledger record-delegation {item['id']} --window <window> --task <type>"
        )
    if not is_open(item):
        raise LedgerError(f"{item['id']} is {item['state']}; a finished item takes no updates")
    return delegation


def awaiting_report(item: Item) -> bool:
    """Whether the latest hand-over (delegated, update, moved) has had neither a report nor a
    transition to a state that shows the work done (`work_done` or terminal) since. History order,
    not timestamps: a report and an update can share a second. A round trip such as addressing ->
    changes-requested -> addressing does not end it: new comments go to the same window."""
    spec = KINDS[item["kind"]]
    finished = spec["work_done"] | spec["terminal"]
    for entry in reversed(item["history"]):
        if entry["event"] == "report" or (entry["event"] == "transition" and entry.get("to") in finished):
            return False
        if entry["event"] in HANDOVER_EVENTS:
            return True
    return False


def cmd_delegation(args: argparse.Namespace) -> str:
    """Where the item was handed over, whether that hand-over awaits a report, and whether its
    window is working on it now (its window is free only when none of its items is working)."""
    with locked_ledger(write=False) as ledger:
        item = get_item(ledger, args.id)
        delegation = active_delegation(item)
        working = is_working(item, ledger["items"])
    return json.dumps(
        {
            "id": args.id,
            "state": item["state"],
            "awaiting_report": awaiting_report(item),
            "working": working,
            "window_status": STATUS_OF(delegation["window"]) or "not running",
            **delegation,
        }
    )


def cmd_record_delegation(args: argparse.Namespace) -> str:
    header = {name: getattr(args, name) for name in TASK_HEADER_FIELDS if getattr(args, name)}
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        check_delegable(item)
        item["delegation"] = {"window": args.window, "task": args.task, "at": now(), "updates": 0, **header}
        record(item, "delegated", args.ask, window=args.window, task=args.task)
        moved = ""
        if item["kind"] == TASK_KIND and item["state"] == "accepted":
            moved = "; " + transition_item(item, "delegated", f"handed to {args.window}", None, {})
    return f"ok: {args.id} delegated to {args.window} ({args.task}){moved}"


def check_delegable(item: Item) -> None:
    """A task is handed over only once I accepted it; outreach is never the work itself."""
    if item["kind"] == TASK_KIND and item["state"] not in ("accepted", "delegated"):
        raise LedgerError(
            f"{item['id']} is {item['state']}; only an accepted task is handed over. Once I approve it: "
            f"bosun ledger transition '{item['id']}' accepted --note '<...>' --evidence "
            f"'{USER_EVIDENCE} <what I said>'"
        )
    if is_outreach(item):
        raise LedgerError(
            f"{item['id']} is outreach, not a work item. The work its reply asks for is a task: "
            f"bosun ledger spawn '{item['id']}' --title '<one line>' --ask '<one line>' --via "
            "'<reply ref>', then delegate that task. A reply for the window holding the parent goes "
            f"with bosun delegate --update '{item['id']}'"
        )


def cmd_record_move(args: argparse.Namespace) -> str:
    """The item's task now lives in another window (delivered there by `delegate --update
    --window`); --notified says the old window was told to stop."""
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        delegation = active_delegation(item)
        old = delegation["window"]
        if args.window == old:
            raise LedgerError(f"{args.id} is already delegated to {old}")
        delegation.update(window=args.window, at=now(), updates=0, moved_from=old)
        delegation.pop("last_update_at", None)
        record(
            item,
            "moved",
            args.text,
            **{"from": old, "to": args.window, "old_window_told": str(args.notified).lower()},
        )
    told = "told to stop" if args.notified else "NOT told to stop"
    return f"ok: {args.id} moved from {old} to {args.window} (the old window was {told})"


def cmd_record_update(args: argparse.Namespace) -> str:
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        delegation = active_delegation(item)
        delegation["updates"] = int(delegation.get("updates", 0)) + 1
        delegation["last_update_at"] = now()
        record(item, "update", args.text, window=delegation["window"])
    return f"ok: update {delegation['updates']} recorded on {args.id}"


# ---- what I said in a repo window, and what a window works on (lib/activity.py) ---------------

MAX_SAID_ENTRIES = 1000
DIRECT_TASK = "direct"  # the task of an item a window took on without a hand-over


def add_user_said(window: str, text: str, session: str = "") -> dict[str, Any]:
    """Record a prompt I typed in <window> (its UserPromptSubmit hook calls this) on the item(s)
    it is about; returns the entry. The text is kept verbatim up to MAX_SAID_LENGTH, redacted."""
    kept = redact.redact(activity.unwrap(text))[: activity.MAX_SAID_LENGTH]
    with locked_ledger(write=True) as ledger:
        holding = activity.held(ledger["items"].values(), window, is_open)
        items, also = activity.attribute(kept, holding)
        entry = {"at": now(), "window": window, "text": kept, "items": items, "also_held": also}
        entry.update({"session": session} if session else {})
        said = ledger.setdefault("said", [])
        said.append(entry)
        del said[:-MAX_SAID_ENTRIES]
        for item_id in items:
            record(ledger["items"][item_id], "user-said", activity.excerpt(kept), window=window)
    emit("user-said", ", ".join(items), window, kept)
    return entry


def said_lines(entry: dict[str, Any]) -> list[str]:
    on = ", ".join(entry["items"]) or "(no item held there)"
    also = f" (the window also held {', '.join(entry['also_held'])})" if entry.get("also_held") else ""
    return [
        f"{entry['at']} in {entry['window']} on {on}{also}:",
        *(f"  {line}" for line in entry["text"].splitlines()),
    ]


def cmd_said(args: argparse.Namespace) -> str:
    """What I typed in repo windows: about an item (also where the window held it next to
    another), in a window, since a time; oldest first, verbatim."""
    since = parse_since(args.since) if args.since else None
    with locked_ledger(write=False) as ledger:
        said = ledger.get("said", [])
    lines = [line for entry in said if said_matches(entry, args, since) for line in said_lines(entry)]
    return "\n".join(lines) or "(nothing I said in a repo window matches)"


def said_matches(entry: dict[str, Any], args: argparse.Namespace, since: datetime | None) -> bool:
    about = not args.id or args.id in activity.said_items(entry)
    where = not args.window or entry["window"] == args.window
    return about and where and (since is None or parse_time(entry["at"]) >= since)


def working_window(item: Item, given: str | None) -> str:
    """The window that says it works on <item>: --window, else the one holding it, else the
    caller's; never another window than the one holding it."""
    if not is_open(item):
        raise LedgerError(f"{item['id']} is {item['state']}; a finished item is not worked on")
    holder = (item.get("delegation") or {}).get("window", "")
    window = given or holder or STORE.caller_window
    if not window:
        raise LedgerError("give the window you work in: --window <window>")
    if holder and holder != window:
        raise LedgerError(
            f"{item['id']} is held by {holder}; moving it is the orchestrator's call "
            f"(bosun delegate --update '{item['id']}' --window '{window}', with my approval)"
        )
    return str(window)


def cmd_working(args: argparse.Namespace) -> str:
    """A window says it works on the item now (its active item). An item no window holds yet
    (work I asked for directly in that window) gets the window recorded, without a hand-over."""
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        window = working_window(item, args.window)
        delegation = item.get("delegation")
        if not delegation:
            item["delegation"] = {"window": window, "task": DIRECT_TASK, "at": now(), "updates": 0}
        record(item, activity.WORKING_MARK, args.note or "", window=window)
    taken = "" if delegation else f"; {window} now holds it (no hand-over: report it when done)"
    return f"ok: {window} is working on {args.id}{taken}"


def cmd_idle(args: argparse.Namespace) -> str:
    """The window put the item aside: it is not its active item any more, even while busy."""
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        delegation = active_delegation(item)
        record(item, activity.IDLE_MARK, args.note or "", window=delegation["window"])
    return f"ok: {delegation['window']} is not working on {args.id}"


def cmd_note(args: argparse.Namespace) -> str:
    with locked_ledger(write=True) as ledger:
        record(get_item(ledger, args.id), "note", args.text)
    return f"ok: noted on {args.id}"


STDIN = "-"
SPEC_OPTIONS = ("send", "run", "tool")
SPEC_PARTS = ("channel", "thread", "text", "args_json")


def read_stdin(what: str) -> str:
    text = sys.stdin.read().strip()
    if not text:
        raise LedgerError(f"{what} - read nothing from stdin; pass it in a quoted heredoc")
    return text


def check_spec_options(args: argparse.Namespace) -> None:
    """At most one action spec, its required parts, and at most one value read from stdin."""
    given = [name for name in SPEC_OPTIONS if getattr(args, name)]
    if len(given) > 1:
        raise LedgerError("give one action spec: --send, --run or --tool")
    if [args.action, args.text, args.args_json].count(STDIN) > 1:
        raise LedgerError("only one of the action, --text and --args-json can be read from stdin (-)")
    if given:
        check_spec_parts(args)
    elif any(getattr(args, name) for name in SPEC_PARTS):
        raise LedgerError("--channel, --thread and --text go with --send slack; --args-json with --tool")
    elif not args.action:
        raise LedgerError("say what is proposed: an action, or an action spec (--send, --run, --tool)")


def check_spec_parts(args: argparse.Namespace) -> None:
    if args.send and not (args.channel and args.text):
        raise LedgerError("--send slack needs --channel <channel id> and --text <exact text | ->")
    if args.tool and not args.args_json:
        raise LedgerError("--tool needs --args-json <the call's arguments as a JSON object | ->")


def tool_arguments(value: str) -> dict[str, Any]:
    raw = read_stdin("--args-json") if value == STDIN else value
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"--args-json is not valid JSON: {exc}") from exc
    if not isinstance(arguments, dict):
        raise LedgerError("--args-json must be a JSON object: the tool call's arguments")
    return arguments


def proposal_spec(args: argparse.Namespace) -> dict[str, Any] | None:
    """The exact outward call a proposal stands for (lib/approvals.py), from propose's options."""
    check_spec_options(args)
    if args.send:
        text = read_stdin("--text") if args.text == STDIN else args.text
        return approvals.slack_spec(args.channel, text, args.thread or "")
    if args.run:
        return approvals.run_spec(args.run)
    if args.tool:
        return approvals.mcp_spec(args.tool, tool_arguments(args.args_json))
    return None


def check_spec_fits(item: Item, spec: dict[str, Any] | None) -> None:
    channel = item["fields"].get("channel")
    if spec and spec["type"] == approvals.SLACK and is_outreach(item) and channel != approvals.SLACK:
        raise LedgerError(f"{item['id']} goes out by {channel}, not Slack; fix its channel first (upsert)")


def same_pending(existing: dict[str, Any] | None, action: str, spec: dict[str, Any] | None) -> bool:
    """Whether <existing> already proposes this, and was not used up (approved and carried out)."""
    if not existing or existing["action"] != action or existing.get("spec") != spec:
        return False
    return not ((existing.get("approval") or {}).get("used_at") or existing.get("auto_approved"))


def set_proposal(item: Item, action: str, spec: dict[str, Any] | None) -> str:
    existing = item.get("proposal")
    if existing:
        record(item, "proposal-replaced", existing["action"])
    item["proposal"] = {"action": action, "proposed_at": now(), **({"spec": spec} if spec else {})}
    record(item, "proposed", action)
    if is_outreach(item) and item["state"] == "draft":
        return "; " + transition_item(item, "proposed", "outgoing message proposed", None, {})
    return ""


def cmd_propose(args: argparse.Namespace) -> str:
    spec = proposal_spec(args)
    if args.action == STDIN:
        args.action = read_stdin("propose")
    action = args.action or approvals.describe(spec or {})
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        if not is_open(item):
            raise LedgerError(f"{args.id} is {item['state']}; nothing to propose")
        check_spec_fits(item, spec)
        existing = item.get("proposal")
        if existing and same_pending(existing, action, spec):
            return f"unchanged: {args.id} already has this proposal pending since {existing['proposed_at']}"
        moved = set_proposal(item, action, spec)
    then = f". Once I approve it: bosun ledger approve '{args.id}' --quote '<my exact words>'" if spec else ""
    return f"ok: proposal pending on {args.id}{moved}{then}"


MAX_QUOTE_LENGTH = 500


def cmd_approve(args: argparse.Namespace) -> str:
    """Record my approval of the pending proposal's action spec, quoting my words; the guard lets
    exactly that call through once (lib/approvals.py)."""
    quote = approvals.normalize(args.quote)
    if not quote or len(quote) > MAX_QUOTE_LENGTH:
        raise LedgerError(
            f"--quote takes my exact words of approval as I typed them (1 to {MAX_QUOTE_LENGTH} characters)"
        )
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        proposal = item.get("proposal")
        if not proposal or not proposal.get("spec"):
            raise LedgerError(
                f"{args.id} has no pending proposal with an action spec; propose the exact call first: "
                f"bosun ledger propose '{args.id}' --send slack --channel <id> [--thread <ts>] --text - "
                "<<'EOF' (or --run '<command>', or --tool <tool> --args-json -)"
            )
        if (proposal.get("approval") or {}).get("used_at"):
            raise LedgerError(f"{args.id}'s approval was already used; propose it again for another call")
        proposal["approval"] = {"at": now(), "quote": quote, "hash": approvals.spec_hash(proposal["spec"])}
        record(item, "approved", quote, action=proposal["action"])
    return f"ok: {args.id} approved ('{quote}'); make exactly the proposed call now"


APPROVAL_USED = "approval-used"
AUTO_APPROVED = approvals.AUTO_APPROVED


APPROVAL_CARRIED_OUT = "approval-carried-out"
APPROVAL_FAILED = "approval-failed"
CARRIED_OUT_NOTE = "carried out by the approved call ({tool})"
# Kinds whose proposal only their own command closes (resolve_proposal refuses them): the call's
# success marks it carried out instead.
KEEPS_PROPOSAL_OPEN = {OUTREACH_KIND: "ledger sent", LEARNING_KIND: "bosun learn"}
FAILED_NOTE = "{reason}; approvals are single-use: propose it again and get a new approval"


def use_approval(
    item: Item,
    verdict: approvals.Verdict,
    call: approvals.Call,
    context: approvals.CallContext | None = None,
) -> None:
    """The guard let <call> through for <item>: an approval is used up; a standing class is noted,
    on the proposal it carried out if any (`ledger approvals` lists both, the check-in the
    standing ones). Either way the proposal's record keeps the call's hash and <context> (session,
    transcript), so the call's result can settle it (settle_call)."""
    proposal = item.get("proposal")
    used = {"call": approvals.call_hash(call), **(context or approvals.CallContext()).record()}
    if verdict.outcome == approvals.APPROVED and proposal:
        proposal["approval"].update(used_at=now(), **used)
        record(item, APPROVAL_USED, proposal["approval"]["quote"], action=proposal["action"], tool=call.tool)
        return
    carried_out = bool(
        proposal and approvals.call_matches(proposal.get("spec") or {}, call.tool, call.tool_input)
    )
    if proposal and carried_out:
        proposal["auto_approved"] = {"at": now(), "class": verdict.cls, **used}
    action = proposal["action"] if proposal and carried_out else approvals.describe(approvals.call_spec(call))
    record(item, AUTO_APPROVED, verdict.cls, action=action, tool=call.tool, comment=verdict.comment)


def settle_call(
    item: Item, call: approvals.Call, outcome: approvals.Outcome, context: approvals.CallContext
) -> str:
    """The call that used <item>'s approval ended (approvals.carried_out_by found it). Success
    closes the proposal as done; outreach keeps it for `ledger sent` (which records the message
    ref and resolves it), marked carried out so it no longer waits on me. Failure leaves it open
    and records approval-failed: it needs a new proposal and approval. Unknown: nothing."""
    proposal = item["proposal"]
    grant = approvals.used_grant(proposal)
    if outcome.ok is None or grant is None:
        return ""
    ids = {"tool": call.tool, "tool_use_id": context.tool_use_id}
    if not outcome.ok:
        grant["failed"] = {"at": now(), "reason": outcome.reason, **ids}
        record(
            item, APPROVAL_FAILED, FAILED_NOTE.format(reason=outcome.reason), action=proposal["action"], **ids
        )
        return f"{item['id']}: approval-failed"
    note = CARRIED_OUT_NOTE.format(tool=call.tool)
    closer = KEEPS_PROPOSAL_OPEN.get(item["kind"])
    if closer:
        grant["carried_out"] = {"at": now(), **ids}
        record(
            item,
            APPROVAL_CARRIED_OUT,
            f"{note}; {closer} closes the proposal",
            action=proposal["action"],
            **ids,
        )
        return f"{item['id']}: carried out ({closer} closes the proposal)"
    item["proposal"] = None
    record(item, "proposal-done", note, action=proposal["action"], **ids)
    return f"{item['id']}: proposal done"


def approval_rows(items: dict[str, Item], kinds: tuple[str, ...], since: datetime | None) -> list[str]:
    found = [
        (entry["at"], item["id"], entry)
        for item in items.values()
        for entry in item["history"]
        if entry["event"] in kinds and (since is None or parse_time(entry["at"]) >= since)
    ]
    rows = sorted(found, key=lambda row: (row[0], row[1]))
    return [
        f"{at}  {item_id}  {entry['event']} ({entry.get('note', '')}) {entry.get('tool', '')}: "
        f"{activity.excerpt(entry.get('action', ''))}"
        for at, item_id, entry in rows
    ]


def cmd_approvals(args: argparse.Namespace) -> str:
    """Calls the guard let through (approved in chat, or under a standing class), newest last."""
    since = parse_since(args.since) if args.since else None
    kinds = (AUTO_APPROVED,) if args.auto else (APPROVAL_USED, AUTO_APPROVED)
    with locked_ledger(write=False) as ledger:
        lines = approval_rows(ledger["items"], kinds, since)
    return "\n".join(lines) or "no calls approved by the guard" + (
        f" since {args.since}" if args.since else ""
    )


def cmd_resolve(args: argparse.Namespace) -> str:
    return resolve_proposal(args.id, args.outcome, args.note)


def resolve_proposal(item_id: str, outcome: str, note: str, *, by_learn: bool = False) -> str:
    """Close the pending proposal on <item_id>. A learning proposal only through `bosun learn`
    (<by_learn>), which also applies the change and closes the item, so the two cannot drift."""
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, item_id)
        proposal = item.get("proposal")
        if not proposal:
            raise LedgerError(f"{item_id} has no pending proposal")
        if item["kind"] == LEARNING_KIND and not by_learn:
            raise LedgerError(
                f"{item_id} is a learning proposal: close it with bosun learn apply '{item_id}' "
                f"(or done '{item_id}' for a change made by hand, reject '{item_id}' --note '<why>')"
            )
        if is_outreach(item) and outcome == "done":
            raise LedgerError(
                f"{item_id} is outreach: once its message is sent, record it with bosun ledger "
                f"sent '{item_id}' --ref '<permalink or message id>', which resolves the proposal"
            )
        item["proposal"] = None
        record(item, f"proposal-{outcome}", note, action=proposal["action"])
        moved = ""
        if is_outreach(item) and item["state"] == "proposed":
            moved = "; " + transition_item(item, "draft", f"message {outcome}", None, {})
    return f"ok: proposal on {item_id} {outcome}{moved}"


# ---- approved calls carried out before the PostToolUse hook (one-time cleanup) ----------------

# The transcript's tool_use comes before the PermissionRequest hook uses the approval; its time
# and ours (whole seconds) differ by a few seconds.
EVIDENCE_WINDOW = timedelta(minutes=5)


@dataclass(frozen=True)
class Unsettled:
    """A pending proposal whose approval was used, with no result recorded yet."""

    item_id: str
    spec: dict[str, Any]
    used_at: str
    transcript: str


def unsettled(items: dict[str, Item]) -> list[Unsettled]:
    found = []
    for item, proposal in approvals.pending_specs(items):
        grant = approvals.used_grant(proposal)
        if grant and not grant.get("carried_out") and not grant.get("failed"):
            found.append(
                Unsettled(
                    item["id"],
                    proposal["spec"],
                    approvals.grant_time(grant),
                    str(grant.get("transcript") or ""),
                )
            )
    return sorted(found, key=lambda u: (u.used_at, u.item_id))


def evidence_paths(candidate: Unsettled, given: list[str]) -> list[Path]:
    """Where to look for the call: --transcript paths, else the transcript recorded at use, else
    the orchestrator's transcripts written since shortly before the use."""
    if given:
        return [Path(path) for path in given]
    if candidate.transcript:
        return [Path(candidate.transcript)]
    if not STORE.orchestrator_dir:
        return []
    since = (parse_time(candidate.used_at) - EVIDENCE_WINDOW).timestamp()
    return [t.path for t in claude_state.transcripts_since(STORE.orchestrator_dir, since)]


def call_evidence(candidate: Unsettled, given: list[str]) -> claude_state.CallResult | None:
    """The call the approval let through, as the transcript shows it with its result: the exact
    proposed call nearest the use, within EVIDENCE_WINDOW; None when no such call has a result."""
    used = parse_time(candidate.used_at)
    found = [
        call
        for path in evidence_paths(candidate, given)
        if path.is_file()
        for call in claude_state.call_results(
            path,
            used - EVIDENCE_WINDOW,
            used + EVIDENCE_WINDOW,
            lambda name, tool_input: approvals.call_matches(candidate.spec, name, tool_input),
        )
        if call.failed is not None
    ]
    return min(found, key=lambda call: abs((call.at - used).total_seconds()), default=None)


def settle_from_transcript(ledger: Ledger, candidate: Unsettled, call: claude_state.CallResult) -> str:
    """Settle the proposal by the transcript's result, if it is still the same unsettled one."""
    item = ledger["items"].get(candidate.item_id)
    still = item and is_open(item) and any(u == candidate for u in unsettled({candidate.item_id: item}))
    if not item or not still:
        return f"{candidate.item_id}: changed since; left alone"
    outcome = approvals.Outcome(not call.failed, approvals.short_reason(call.error) if call.failed else "")
    context = approvals.CallContext(tool_use_id=call.tool_use_id)
    return settle_call(item, approvals.Call(call.name, call.tool_input), outcome, context)


def evidence_line(candidate: Unsettled, call: claude_state.CallResult | None, dry_run: bool) -> str:
    if call is None:
        return (
            f"{candidate.item_id}: approval used at {candidate.used_at}, no result found in a "
            f"transcript; resolve it by hand: bosun ledger resolve '{candidate.item_id}' "
            "done|withdrawn --note '<why>'"
        )
    how = "failed" if call.failed else "succeeded"
    would = "would settle" if dry_run else "settling"
    return f"{candidate.item_id}: the approved call ({call.name}) {how} at {call.at.isoformat()}; {would} it"


def cmd_resolve_carried_out(args: argparse.Namespace) -> str:
    """Settle the proposals whose approval was used before the PostToolUse hook existed (or while
    it did not run), by the call's result in the transcript; see the usage above."""
    with locked_ledger(write=False) as ledger:
        candidates = unsettled(ledger["items"])
    if not candidates:
        return "no pending proposal has a used approval waiting for its result"
    found = [(candidate, call_evidence(candidate, args.transcript or [])) for candidate in candidates]
    lines = [evidence_line(candidate, call, args.dry_run) for candidate, call in found]
    settled = [(candidate, call) for candidate, call in found if call is not None]
    if not args.dry_run and settled:
        lines += settle_all(settled)
    verb = "would settle" if args.dry_run else "settled"
    return "\n".join(
        [*lines, f"({len(settled)} {verb}, {len(found) - len(settled)} left for you to resolve)"]
    )


def settle_all(settled: list[tuple[Unsettled, claude_state.CallResult]]) -> list[str]:
    with locked_ledger(write=True) as ledger:
        return [settle_from_transcript(ledger, candidate, call) for candidate, call in settled]


# ---- outreach ---------------------------------------------------------------------------------

# The command that moves an outreach item into a state that needs fields.
OUTREACH_COMMANDS = {
    "sent": "sent '<out-id>' --ref '<permalink or message id of what was sent>' [--at '<when, if earlier>']",
    "replied": (
        "replied '<out-id>' --evidence '<reference to the reply>' --summary '<the answer>' "
        "[--at '<when, if earlier>']"
    ),
}


def check_line(option: str, value: str) -> None:
    if not value.strip() or "\n" in value or len(value) > MAX_LINE_LENGTH:
        raise LedgerError(
            f"{option} must be one non-empty line of at most {MAX_LINE_LENGTH} characters; "
            "longer context goes in --details"
        )


def read_details(details: str | None) -> str:
    """--details as given, or from stdin for "-"."""
    text = (sys.stdin.read() if details == "-" else details or "").strip()
    if len(text) > MAX_DETAILS_LENGTH:
        raise LedgerError(f"--details is limited to {MAX_DETAILS_LENGTH} characters; link a document instead")
    return text


def check_expect_by(value: str | None) -> None:
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise LedgerError(f"--expect-by takes a date like 2026-10-20, not {value!r}") from exc


def get_outreach(ledger: Ledger, outreach_id: str) -> Item:
    prefixed = f"{id_prefix(OUTREACH_KIND)}{outreach_id}"
    if outreach_id not in ledger["items"] and prefixed in ledger["items"]:
        raise LedgerError(f"no item {outreach_id!r}; outreach ids start with out: ({prefixed!r})")
    item = get_item(ledger, outreach_id)
    if not is_outreach(item):
        raise LedgerError(f"{outreach_id} is a {item['kind']}, not an outreach item")
    return item


def outreach_upsert_fields(fields: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    """Changes to outreach, e.g. to keep it in line with the message actually sent: a known channel
    or purpose, one-line recipient and subject, options as one line ("a | b"; empty: none), and the
    subject and title kept the same (either one sets both)."""
    for name, known in (("channel", tuple(CHANNELS)), ("purpose", PURPOSES)):
        if name in fields and fields[name] not in known:
            raise LedgerError(f"unknown outreach {name} {fields[name]!r}; one of: {', '.join(known)}")
    changed: dict[str, Any] = dict(fields)
    if args.title and "subject" not in changed:
        changed["subject"] = args.title
    if "subject" in changed:
        args.title = changed["subject"]
    for name in ("to", "subject"):
        if name in changed:
            check_line(f"--field {name}", changed[name])
    if "options" in changed:
        changed["options"] = parse_options(fields["options"])
    return changed


def parse_options(value: str) -> list[str]:
    """--field options='a | b' as the list of options; empty: none."""
    options = [option.strip() for option in value.split(OPTIONS_SEPARATOR) if option.strip()]
    for option in options:
        check_line("--field options", option)
    return options


def is_one_way(item: Item) -> bool:
    return bool(item["fields"].get("purpose") == ONE_WAY_PURPOSE)


def one_way_hint(item: Item) -> str:
    """After a retag to fyi: a one-way message already sent waits on nobody, so it can be closed."""
    if not is_one_way(item) or item["state"] != "sent":
        return ""
    return (
        f"\nnext: {item['id']} is one-way ({ONE_WAY_PURPOSE}) and already sent; close it: bosun "
        f"ledger transition '{item['id']}' applied --note 'one-way message sent'"
    )


def check_one_way(item: Item, target: str) -> None:
    """sent -> applied skips the reply, so only a one-way message (purpose fyi) may take it."""
    if item["state"] == "sent" and target == "applied" and not is_one_way(item):
        raise LedgerError(
            f"{item['id']}: sent -> applied is only for one-way messages (purpose {ONE_WAY_PURPOSE}); "
            f"record the reply first: bosun ledger {OUTREACH_COMMANDS['replied']}, or retag a "
            f"message that needs no reply: bosun ledger upsert '{item['id']}' --field "
            f"purpose={ONE_WAY_PURPOSE}"
        )


def check_outreach_parent(parent: Item, purpose: str) -> None:
    """Outreach belongs to open work items. A one-way note (fyi) may also follow a fact, such as
    "merged and deployed" on a merged PR: it waits on nothing, so it never holds the parent."""
    if is_outreach(parent):
        owner = parent["fields"].get("parent")
        raise LedgerError(
            f"{parent['id']} is itself outreach; reach out for the work item it belongs to"
            + (f" ({owner})" if owner else ", or without a parent")
        )
    after_fact = purpose == ONE_WAY_PURPOSE and parent["state"] in KINDS[parent["kind"]].get("facts", ())
    if not is_open(parent) and not after_fact:
        raise LedgerError(f"{parent['id']} is {parent['state']}; outreach belongs to open work items")


def next_outreach_id(ledger: Ledger, scope: str) -> str:
    """out:<scope>/<n>: the next number for a parent id, or for a day (outreach without a parent)."""
    numbers = [
        int(match["number"])
        for item_id in ledger["items"]
        if (match := OUTREACH_ID.match(item_id)) and match["scope"] == scope
    ]
    return f"out:{scope}/{max(numbers, default=0) + 1}"


def new_outreach(outreach_id: str, args: argparse.Namespace, details: str) -> Item:
    fields: dict[str, Any] = {
        "channel": args.channel,
        "purpose": args.purpose,
        "to": args.to,
        "subject": args.subject,
        "messages": [],
    }
    optional = {
        "parent": args.parent,
        "options": args.option,
        "details": details,
        "expect_by": args.expect_by,
    }
    fields.update({name: value for name, value in optional.items() if value})
    item: Item = {
        "id": outreach_id,
        "kind": OUTREACH_KIND,
        "state": KINDS[OUTREACH_KIND]["initial"],
        "title": args.subject,
        "url": "",
        "fields": fields,
        "proposal": None,
        "created_at": now(),
        "history": [],
    }
    record(item, "created", f"{args.purpose} by {args.channel} to {args.to}: {args.subject}", state="draft")
    return item


def same_outreach(item: Item, args: argparse.Namespace) -> bool:
    fields = item["fields"]
    return (
        is_outreach(item)
        and is_open(item)
        and (
            fields.get("parent"),
            fields["channel"],
            fields["to"],
            fields["subject"],
        )
        == (args.parent, args.channel, args.to, args.subject)
    )


def check_reach_out(args: argparse.Namespace) -> str:
    """Validate the one-line values and the date; returns the details."""
    check_line("--to", args.to)
    check_line("--subject", args.subject)
    for option in args.option or []:
        check_line("--option", option)
    check_expect_by(args.expect_by)
    return read_details(args.details)


def cmd_reach_out(args: argparse.Namespace) -> str:
    """Record an outreach in draft, for a parent item or on its own; prints its id first."""
    details = check_reach_out(args)
    with locked_ledger(write=True) as ledger:
        if args.parent:
            parent = get_item(ledger, args.parent)
            check_outreach_parent(parent, args.purpose)
        existing = next((item for item in ledger["items"].values() if same_outreach(item, args)), None)
        if existing:
            return (
                f"{existing['id']}\nunchanged: {existing['id']} already exists ({outreach_status(existing)})"
            )
        overlapping = overlapping_outreach(ledger, args)
        outreach_id = next_outreach_id(ledger, args.parent or date.today().isoformat())
        ledger["items"][outreach_id] = new_outreach(outreach_id, args, details)
        if args.parent:
            record(parent, "outreach", f"{outreach_id}: {args.channel} to {args.to}: {args.subject}")
    emit(
        "reach-out",
        outreach_id,
        summary=f"{args.purpose} by {args.channel} to {args.to}: {args.subject}",
        detail=details,
    )
    if args.purpose == ONE_WAY_PURPOSE:
        then = "; it needs no reply: once sent, it is done"
    else:
        then = f"; hold the work on {args.parent} that depends on the reply" if args.parent else ""
    return (
        f"{outreach_id}\ncreated: {outreach_id} [outreach] state=draft ({args.purpose} by {args.channel} "
        f"to {args.to}). Next, the orchestrator proposes the exact message: bosun ledger propose "
        f"'{outreach_id}' '<channel, where, the exact text>'{then}."
    ) + overlap_warning(outreach_id, args.parent, overlapping)


def same_person(one: str, other: str) -> bool:
    """Whether two recipients name the same person, loosely: "Dana" and "Dana (product)", "Marco"
    and "Marco Rossi" (one's words start the other's, ignoring case and parentheses)."""
    shorter, longer = sorted(
        (re.sub(r"\([^)]*\)", " ", name).lower().split() for name in (one, other)), key=len
    )
    return bool(shorter) and longer[: len(shorter)] == shorter


def overlapping_outreach(ledger: Ledger, args: argparse.Namespace) -> list[Item]:
    """Open outreach of the same parent to the same person as a new draft, oldest first. One-way
    messages (fyi) on either side do not count: they wait on nothing, so they neither compete for
    a reply nor add a wait clock."""
    if not args.parent or args.purpose == ONE_WAY_PURPOSE:
        return []
    return [
        o
        for o in open_outreach(ledger, args.parent)
        if not is_one_way(o) and same_person(o["fields"]["to"], args.to)
    ]


def overlap_warning(outreach_id: str, parent: str | None, overlapping: list[Item]) -> str:
    """A new draft next to open outreach of the same parent to the same person: one conversation,
    usually. A warning, not a refusal: it may be a separate question."""
    if not overlapping:
        return ""
    names = "; ".join(
        f"{o['id']} ({o['fields']['purpose']}, {o['state']}: {o['fields']['subject']})" for o in overlapping
    )
    return (
        f"\nwarning: {parent} already has open outreach to the same person: {names}. If this adds to "
        "that conversation, keep one item: the orchestrator says it there as a follow-up recorded on "
        "the existing item (bosun ledger sent '<existing id>' --ref '<message ref>') and folds this "
        f"draft into it (bosun ledger fold '{outreach_id}' --into '<existing id>'). If it asks exactly "
        f"what that one asks, drop it as a duplicate (bosun ledger transition '{outreach_id}' dropped "
        "--note 'duplicate of <existing id>'). Keep both only for a genuinely separate question."
    )


def cmd_sent(args: argparse.Namespace) -> str:
    """One message went out for one or more outreach items (a combined message, e.g. one nudge
    for several questions, counts on each): see record_message. All or none are recorded."""
    check_line("--ref", args.ref)
    at, provenance = event_time(args.at, args.ref)
    ids = list(dict.fromkeys(args.ids))
    note = args.note or (f"one message for {', '.join(ids)}" if len(ids) > 1 else "")
    with locked_ledger(write=True) as ledger:
        items = [get_outreach(ledger, outreach_id) for outreach_id in ids]
        return "\n".join(record_message(item, args, note, (at, provenance)) for item in items)


def record_message(item: Item, args: argparse.Namespace, note: str, when: tuple[str, dict[str, str]]) -> str:
    """A message for the outreach went out: the first one (draft or proposed -> sent) or, once
    sent, a follow-up (a nudge). Resolves the pending proposal for it. A ref already recorded is
    unchanged, unless --at corrects its time. A one-way message (fyi) is then done (applied)."""
    at, provenance = when
    messages = item["fields"].setdefault("messages", [])
    recorded = next((message for message in messages if message["ref"] == args.ref), None)
    if recorded:
        return correct_message_time(item, recorded, provenance if args.at else {})
    if item["state"] not in ("draft", "proposed", "sent"):
        raise LedgerError(
            f"{item['id']} is {item['state']}; only outreach waiting to be sent or answered takes messages"
        )
    if item.get("proposal"):
        proposal, item["proposal"] = item["proposal"], None
        record(item, "proposal-done", note, action=proposal["action"])
    message = {"ref": args.ref, "channel": args.channel or item["fields"]["channel"], "at": at}
    if item["state"] == "sent":
        messages.append(message)
        record(item, "follow-up", note, ref=args.ref, **provenance)
        outcome = f"ok: follow-up {len(messages) - 1} recorded on {item['id']}"
    else:
        outcome = transition_item(
            item, "sent", note or "message sent", None, {"messages": [*messages, message]}, **provenance
        )
    return outcome + finish_one_way(item) + time_hint(provenance)


def finish_one_way(item: Item) -> str:
    """A one-way message (fyi) that went out waits on nobody: sent -> applied."""
    if not is_one_way(item) or item["state"] != "sent":
        return ""
    transition_item(item, "applied", "one-way message sent: no reply expected", None, {})
    return f" -> applied ({ONE_WAY_PURPOSE}: one-way, no reply expected)"


def correct_message_time(item: Item, message: dict[str, Any], given: dict[str, str]) -> str:
    """A message recorded before: --at (<given>) corrects its time, e.g. one recorded late."""
    if not given or given["happened_at"] == message["at"]:
        return f"unchanged: {item['id']} already recorded {message['ref']} (at {message['at']})"
    previous, message["at"] = message["at"], given["happened_at"]
    record(item, "message-time-corrected", f"was {previous}", ref=message["ref"], **given)
    return f"ok: {item['id']}: {message['ref']} now at {message['at']} (was {previous})"


def cmd_replied(args: argparse.Namespace) -> str:
    """Record the reply (with a reference to it as evidence) and say where it goes next."""
    check_line("--summary", args.summary)
    check_line("--evidence", args.evidence)
    at, provenance = event_time(args.at, args.evidence)
    with locked_ledger(write=True) as ledger:
        item = get_outreach(ledger, args.id)
        fields = {"answer": args.summary, "reply_ref": args.evidence, "replied_at": at}
        if item["state"] == "replied":
            item["fields"].update(fields)
            record(item, "reply-updated", args.summary, evidence=args.evidence, **provenance)
            outcome = f"ok: reply on {args.id} updated"
        else:
            if item.get("proposal"):
                item["proposal"] = None
                record(item, "proposal-withdrawn", "replied before the message was sent")
            outcome = transition_item(item, "replied", args.summary, args.evidence, fields, **provenance)
        return f"{outcome}{time_hint(provenance)}\n{handover_hint(ledger, item)}"


def handover_hint(ledger: Ledger, item: Item) -> str:
    """Where the reply goes next: the window holding the parent, or me."""
    applied = f"bosun ledger transition '{item['id']}' applied --note '<what follows from it>'"
    mine = f"tell me the reply (if I gave it to you, I know it already), then: {applied}"
    parent_id = item["fields"].get("parent")
    if not parent_id:
        return f"next: {item['id']} belongs to no work item; {mine}"
    parent = ledger["items"].get(parent_id) or {}
    window = (parent.get("delegation") or {}).get("window") if parent and is_open(parent) else None
    if window:
        return (
            f"next: window {window} holds {parent_id}; hand the reply over with bosun delegate "
            f"--update '{item['id']}' (if you are that window, apply it yourself, then: {applied})"
        )
    return f"next: {parent_id} is not with a repo session; {mine}"


def message_lines(item: Item) -> list[str]:
    """The messages sent (the first, then nudges) and, while waiting, for how long."""
    messages = item["fields"].get("messages") or []
    lines = [
        f"  {'sent:    ' if number == 0 else f'nudge {number}:'} {message['ref']} "
        f"({message['channel']}, {message['at']})"
        for number, message in enumerate(messages)
    ]
    if messages and item["state"] == "sent":
        waited = business_days_since(last_message_at(messages))
        lines.append(f"  waiting:  {waited} business day(s) since the last message")
    return lines


def outreach_lines(item: Item, items: dict[str, Item]) -> list[str]:
    fields = item["fields"]
    parent_id = fields.get("parent")
    parent = items.get(parent_id or "")
    where = (
        f"for {parent_id}{delegation_marker(parent) if parent else ' (missing)'}"
        if parent_id
        else "no parent item"
    )
    lines = [
        f"{item['id']}  [{item['state']}] {fields['purpose']}: {outreach_status(item)}; {where}",
        f"  subject:  {fields['subject']}",
    ]
    if fields.get("options"):
        lines.append("  options:  " + " | ".join(fields["options"]))
    lines += message_lines(item)
    if fields.get("answer"):
        lines.append(f"  reply:    {fields['answer']} ({fields['reply_ref']})")
    if item.get("proposal"):
        lines.append(f"  PENDING:  {item['proposal']['action']}")
    return lines


def in_flight(items: dict[str, Item], window: str) -> list[Item]:
    """The open items handed to <window> whose work is not done: awaiting a report since the latest
    hand-over, or what the window is working on now."""
    held = activity.held(items.values(), window, is_open)
    return [item for item in held if awaiting_report(item) or is_working(item, items)]


def cmd_in_flight(args: argparse.Namespace) -> str:
    """The ids of the items <window> holds with work in flight, one per line (nothing if none)."""
    with locked_ledger(write=False) as ledger:
        items = ledger["items"]
    return "\n".join(item["id"] for item in in_flight(items, args.window))


def window_rows(items: dict[str, Item], window: str) -> list[str]:
    """One list row per open item handed to <window>, the ones in flight first."""
    view = View.of(items)
    flying = {item["id"] for item in in_flight(items, window)}
    held = sorted(activity.held(items.values(), window, is_open), key=lambda item: item["id"] not in flying)
    return [shorten(item_row(item, view)) for item in held]


def pending_approval_rows(items: dict[str, Item]) -> list[str]:
    """One row per proposal waiting for my approval: the item and the proposed action."""
    pending = [item for item in items.values() if approvals.needs_you(item.get("proposal"))]
    return [shorten(f"{item['id']}: {item['proposal']['action']}") for item in pending]


def held_counts(items: dict[str, Item]) -> list[str]:
    """'<window> <n>' for each window that holds open items, busiest first."""
    counts: dict[str, int] = {}
    for item in items.values():
        window = (item.get("delegation") or {}).get("window")
        if window and is_open(item):
            counts[window] = counts.get(window, 0) + 1
    return [f"{window} {count}" for window, count in sorted(counts.items(), key=lambda pair: -pair[1])]


def shorten(line: str, limit: int = COMPACT_ROW_CHARS) -> str:
    return line if len(line) <= limit else line[: limit - 3] + "..."


def cmd_compact_context(args: argparse.Namespace) -> str:
    """The block a session gets back after a compaction (lib/compact_context.py)."""
    with locked_ledger(write=False) as ledger:
        items = ledger["items"]
    if args.orchestrator:
        block = compact_context.orchestrator_block(pending_approval_rows(items), held_counts(items))
    else:
        block = compact_context.window_block(args.window, window_rows(items, args.window))
    return block.render(args.max_chars)


def cmd_outreach(args: argparse.Namespace) -> str:
    with locked_ledger(write=False) as ledger:
        items = ledger["items"]
    chosen = sorted(
        (
            item
            for item in items.values()
            if is_outreach(item)
            and (not args.open or is_open(item))
            and (not args.parent or item["fields"].get("parent") == args.parent)
        ),
        key=lambda item: str(item["created_at"]),
    )
    return "\n".join(line for item in chosen for line in outreach_lines(item, items)) or "(no outreach)"


def slack_message(permalink: str) -> tuple[str, str, float] | None:
    """(channel, thread ts, message ts) of a Slack permalink; a top-level message is its own thread."""
    found = SLACK_PERMALINK.search(permalink)
    if not found:
        return None
    digits = found["ts"]
    ts = f"{digits[:-SLACK_TS_DECIMALS]}.{digits[-SLACK_TS_DECIMALS:]}"
    thread = SLACK_THREAD_TS.search(permalink)
    return found["channel"], thread["ts"] if thread else ts, float(ts)


def same_conversation(message: tuple[str, str, float], sent: tuple[str, str, float]) -> bool:
    """In the thread we wrote in or, in a DM, any later message."""
    if message[0] != sent[0]:
        return False
    return message[1] == sent[1] or (message[0].startswith(DM_CHANNEL_PREFIX) and message[2] > sent[2])


def in_conversation(message: tuple[str, str, float], sent: dict[str, Any]) -> bool:
    """Whether a Slack message is in the conversation of a message we sent (on Slack)."""
    ours = slack_message(sent["ref"]) if sent["channel"] == "slack" else None
    return bool(ours and same_conversation(message, ours))


def outreach_for_message(ledger: Ledger, permalink: str) -> list[tuple[Item, str]]:
    """Every open Slack outreach whose thread (or DM) a Slack message is in, with the time of its
    latest message there; the latest first (on a tie, the oldest item first)."""
    message = slack_message(permalink)
    if message is None:
        return []
    found = []
    for item in (i for i in ledger["items"].values() if is_outreach(i) and is_open(i)):
        times = [
            str(sent["at"]) for sent in item["fields"].get("messages", []) if in_conversation(message, sent)
        ]
        if times:
            found.append((item, max(times, key=parse_time)))
    return sorted(found, key=lambda match: (-parse_time(match[1]).timestamp(), str(match[0]["created_at"])))


def thread_hint(matches: list[tuple[Item, str]]) -> str:
    """What `seen` says about a new message in the conversation of open outreach: the one item, or
    all of them when several share it (I pick the one it answers)."""
    if len(matches) == 1:
        oid, fields = matches[0][0]["id"], matches[0][0]["fields"]
        return (
            f"not processed yet; it is in the conversation of {oid} ({fields['purpose']} to "
            f"{fields['to']}: {fields['subject']})\n"
            f"If it answers: bosun ledger replied '{oid}' --evidence '<this permalink>' --summary "
            f"'<the answer>'. Either way record it: bosun ledger record-source '<this permalink>' "
            f"--class outreach --item '{oid}'"
        )
    head = (
        f"not processed yet; it is in a conversation shared by {len(matches)} open outreach items "
        "(latest message first). Read it and pick the one it answers; ask me when unclear:"
    )
    rows = [
        f"  {item['id']} [{item['state']}] {item['fields']['purpose']} to {item['fields']['to']}: "
        f"{item['fields']['subject']} (last message {at})"
        for item, at in matches
    ]
    tail = (
        "If it answers one: bosun ledger replied '<that out-id>' --evidence '<this permalink>' "
        "--summary '<the answer>' (one call per item it answers, each with its own summary). Either "
        "way record it: bosun ledger record-source '<this permalink>' --class outreach --item "
        "'<the out-id it answers, else the first>'"
    )
    return "\n".join([head, *rows, tail])


def scan_start(name: str, runs: dict[str, str], moment: datetime) -> tuple[datetime, str]:
    """Where the Slack scan of a <name> run at <moment> starts, and why. The last completed scan
    ended at the later of the previous check-in and morning runs; a check-in starts there (minus
    some overlap; `ledger seen` makes overlap harmless), a morning at the earlier of that and the
    lookback, so a failed morning or a day away loses nothing. Never earlier than the maximum
    lookback, so a long absence does not scan weeks."""
    window = STORE.scan
    lookback = moment - timedelta(hours=window.lookback_hours)
    earlier = [parse_time(runs[run]) for run in SCAN_RUNS if runs.get(run)]
    if not earlier:
        start, why = lookback, f"no earlier run: the last {window.lookback_hours:g}h"
    else:
        last = max(earlier) - CHECKIN_OVERLAP
        start, why = last, "the last scan, with some overlap"
        if name != "check-in" and lookback < last:
            start, why = lookback, f"the last {window.lookback_hours:g}h (the last scan was more recent)"
    cap = moment - timedelta(hours=window.max_lookback_hours)
    if start < cap:
        start, why = cap, f"capped at {window.max_lookback_hours:g}h (the last scan was longer ago)"
    return start, why


def cmd_mark_run(args: argparse.Namespace) -> str:
    """Record a chores run, and print where its Slack scan starts (scan_start)."""
    with locked_ledger(write=True) as ledger:
        runs = dict(ledger["runs"])
        ledger["runs"][args.name] = now()
    this_run = ledger["runs"][args.name]
    start, why = scan_start(args.name, runs, parse_time(this_run))
    return (
        f"previous {args.name} run: {runs.get(args.name) or 'never'}; this run: {this_run}\n"
        f"scan from: {start.isoformat(timespec='seconds')} ({why})"
    )


def processed_source(ledger: Ledger, key: str) -> dict[str, Any] | None:
    """The record of a processed source: by its key or, for a Slack permalink, by any recorded
    permalink of the same message (channel and ts; the query part, e.g. thread_ts, may differ)."""
    found: dict[str, Any] | None = ledger["sources"].get(key)
    message = slack_message(key) if found is None else None
    if message is None:
        return found
    for other, source in ledger["sources"].items():
        theirs = slack_message(other)
        if theirs and theirs[0] == message[0] and theirs[2] == message[2]:
            return dict(source)
    return None


def cmd_seen(args: argparse.Namespace) -> tuple[str, int]:
    with locked_ledger(write=False) as ledger:
        source = processed_source(ledger, args.key)
        waiting = [] if source else outreach_for_message(ledger, args.key)
    if source is None:
        return (thread_hint(waiting) if waiting else ""), 1
    return json.dumps(source), 0


def cmd_record_source(args: argparse.Namespace) -> str:
    with locked_ledger(write=True) as ledger:
        if args.item:
            item = ledger["items"].get(args.item)
            if item is None:
                raise LedgerError(f"no item {args.item!r}; upsert it first")
            if args.cls != KINDS[item["kind"]]["source_class"]:
                raise LedgerError(
                    f"{args.item} is a {item['kind']}; its messages are class "
                    f"{KINDS[item['kind']]['source_class']}, not {args.cls}"
                )
        elif args.cls != FYI_CLASS:
            raise LedgerError(f"class {args.cls} needs --item; only {FYI_CLASS} sources stand alone")
        previous = ledger["sources"].get(args.key)
        ledger["sources"][args.key] = {
            "classification": args.cls,
            "item": args.item or None,
            "seen_at": previous["seen_at"] if previous else now(),
        }
        if args.item:
            record(ledger["items"][args.item], "source", args.key, classification=args.cls)
    return f"{'updated' if previous else 'recorded'}: {args.key} -> {args.item or '-'} ({args.cls})"


def linked_to_open(items: dict[str, Item]) -> set[str]:
    """Items linked to or from an open item: kept by prune, so live lineage stays whole (belonging
    to an open workstream keeps nothing: workstreams themselves are never pruned)."""
    index = Index.of(items)
    return {
        end
        for item_id, item in items.items()
        if is_open(item)
        for link in [*index.out(item_id), *index.into(item_id)]
        if link.rel != lineage.PART_OF
        for end in (link.source, link.target)
    }


def prune_said(ledger: Ledger, cutoff: datetime) -> None:
    if "said" in ledger:
        ledger["said"] = [entry for entry in ledger["said"] if parse_time(entry["at"]) >= cutoff]


def prunable(item: Item, cutoff: datetime) -> bool:
    """Finished before <cutoff>; never a workstream (few, and the key of every summary)."""
    finished = not is_open(item) and parse_time(item["updated_at"]) < cutoff
    return finished and item["kind"] != WORKSTREAM_KIND


def cmd_prune(args: argparse.Namespace) -> str:
    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    with locked_ledger(write=True) as ledger:
        linked = linked_to_open(ledger["items"])
        stale_items = [
            item_id
            for item_id, item in ledger["items"].items()
            if prunable(item, cutoff) and item_id not in linked
        ]
        for item_id in stale_items:
            del ledger["items"][item_id]
        stale_sources = [
            key
            for key, source in ledger["sources"].items()
            if parse_time(source["seen_at"]) < cutoff and source.get("item") not in ledger["items"]
        ]
        for key in stale_sources:
            del ledger["sources"][key]
        prune_said(ledger, cutoff)
    return f"pruned {len(stale_items)} item(s) and {len(stale_sources)} source(s) older than {args.days} days"


def cmd_diff(args: argparse.Namespace) -> str:
    """Items added, or with a new state or proposal, compared with an earlier copy of the ledger."""
    try:
        before = json.loads(Path(args.before).read_text()).get("items", {})
    except (OSError, json.JSONDecodeError):
        before = {}
    with locked_ledger(write=False) as ledger:
        after = ledger["items"]
    lines = [
        line for item_id, item in sorted(after.items()) for line in diff_lines(item, before.get(item_id))
    ]
    lines.append(f"({len(lines)} change(s); full view: {STORE.view})")
    return "\n".join(lines)


def diff_lines(item: Item, old: Item | None) -> list[str]:
    """What changed in <item> since <old>: added (+), state or proposal (~), delegated or updated
    (>), reported (*)."""
    proposal = f"  [proposed: {item['proposal']['action']}]" if item.get("proposal") else ""
    lines = []
    if old is None:
        lines.append(f"+ {item['id']} [{item['kind']}: {item['state']}] {item.get('title', '')}{proposal}")
    elif old["state"] != item["state"] or old.get("proposal") != item.get("proposal"):
        lines.append(f"~ {item['id']} {old['state']} -> {item['state']}{proposal}")
    return lines + handover_diff_lines(item, old or {})


def handover_diff_lines(item: Item, old: Item) -> list[str]:
    """Delegation (>) and report (*) changes in <item> since <old>."""
    lines = []
    delegation = item.get("delegation")
    if delegation and delegation != old.get("delegation"):
        updates = f"{delegation['updates']} update(s)"
        lines.append(f"> {item['id']} delegated to {delegation['window']} ({updates})")
    report = item.get("report")
    if report and report != old.get("report"):
        lines.append(f"* {item['id']} reported: {report['summary']}")
    return lines


def delegation_marker(item: Item, items: dict[str, Item] | None = None) -> str:
    """For an open item handed to a repo session: ' @<window>', with ' (awaiting report)' while
    awaiting_report(), or ' (reported)' when its latest report or transition is a report; given
    all <items>, also ' (working)' while its window works on it (see lib/activity.py)."""
    delegation = item.get("delegation")
    if not delegation or not is_open(item):
        return ""
    words = [handover_word(item), "working" if items is not None and is_working(item, items) else ""]
    shown = ", ".join(word for word in words if word)
    return f" @{delegation['window']}" + (f" ({shown})" if shown else "")


def handover_word(item: Item) -> str:
    """'awaiting report', 'reported' (its latest report or transition is a report), or ''."""
    if awaiting_report(item):
        return "awaiting report"
    latest = next(
        (h["event"] for h in reversed(item["history"]) if h["event"] in ("report", "transition")), ""
    )
    return "reported" if latest == "report" else ""


def is_working(item: Item, items: dict[str, Item]) -> bool:
    window = (item.get("delegation") or {}).get("window", "")
    return activity.is_working(item, activity.held(items.values(), window, is_open), STATUS_OF)


@dataclass
class View:
    """What a listing needs besides the item: open outreach by parent, and the links."""

    items: dict[str, Item]
    waiting: dict[str, list[Item]]
    index: Index

    @classmethod
    def of(cls, items: dict[str, Item]) -> View:
        return cls(items, outreach_by_parent(items.values()), Index.of(items))


def waiting_marker(item: Item, view: View) -> str:
    """For outreach: where it stands. For an item with open outreach: ' | waiting on <out-id>
    (<channel> to <who>, <age>), ...'; with open items it is blocked_by: ' | blocked by <id>'."""
    if is_outreach(item):
        return f" ({outreach_status(item)})" if is_open(item) else ""
    waiting = view.waiting.get(item["id"], [])
    blockers = lineage.open_blockers(view.items, view.index, item["id"], is_open)
    marker = (
        " | waiting on " + ", ".join(f"{o['id']} ({outreach_status(o)})" for o in waiting) if waiting else ""
    )
    if blockers:
        marker += " | blocked by " + ", ".join(f"{b['id']} ({b['state']})" for b in blockers)
    return marker


def pending_marker(proposal: dict[str, Any] | None) -> str:
    """' | PENDING: <action>' for a proposal awaiting my approval; one I approved, or whose
    approved call failed, says so: ' | PENDING (carried out, closing): <action>'."""
    if not proposal:
        return ""
    plain = approvals.needs_you(proposal) and not (approvals.used_grant(proposal) or {}).get("failed")
    label = "" if plain else f" ({approvals.proposal_label(proposal)})"
    return f" | PENDING{label}: {proposal['action']}"


def item_row(item: Item, view: View) -> str:
    pending = pending_marker(item.get("proposal"))
    title = f" - {item['title']}" if item["title"] else ""
    return (
        f"{item['id']}  [{item['kind']}: {item['state']}]{delegation_marker(item, view.items)}{title}"
        f"{waiting_marker(item, view)}{lineage.list_hint(view.index, item['id'])}{pending}"
    )


def listed(item: Item, args: argparse.Namespace) -> bool:
    """Whether <item> passes the --open, --kind and --proposals filters."""
    return (
        (not args.open or is_open(item))
        and (not args.kind or item["kind"] == args.kind)
        and (not args.proposals or bool(item.get("proposal")))
    )


def cmd_list(args: argparse.Namespace) -> str:
    with locked_ledger(write=False) as ledger:
        view = View.of(ledger["items"])
    in_ws = ws_filter(view.items, args.ws)
    items = sorted(view.items.values(), key=lambda i: (i["kind"], i["created_at"]))
    return "\n".join(item_row(i, view) for i in items if listed(i, args) and in_ws(i["id"])) or "(no items)"


def ws_filter(items: dict[str, Item], name: str | None) -> Callable[[str], bool]:
    """`list --ws`: items resolved into the workstream or one under it; 'unassigned': in no
    workstream (workstreams and tickets are in none either way)."""
    if not name:
        return lambda _item_id: True
    resolver = resolver_for(items)
    if name == workstreams.UNASSIGNED_NAME:
        unassigned = set(resolver.unassigned())
        return lambda item_id: item_id in unassigned
    wanted = workstreams.family(items, workstreams.find(items, name))
    return lambda item_id: resolver.resolve(item_id).ws in wanted


def cmd_show(args: argparse.Namespace) -> str:
    """The item as stored, plus what is derived from the rest of the ledger (under "derived"):
    links to it, its lineage tree and hints, and what I said about it in a repo window."""
    with locked_ledger(write=False) as ledger:
        item = get_item(ledger, args.id)
        derived = derived_view(ledger["items"], args.id)
        said = [e for e in ledger.get("said", []) if args.id in activity.said_items(e)]
        derived.update({"user_said": said} if said else {})
    return json.dumps({**item, **({"derived": derived} if derived else {})}, indent=2)


def derived_view(items: dict[str, Item], item_id: str) -> dict[str, Any]:
    index = Index.of(items)
    tree = lineage.TreeView(items, index, item_id, delegation_marker).lines()
    derived: dict[str, Any] = {
        "workstream": workstream_view(items, index, item_id),
        "computed_links": [link.outward() for link in index.out(item_id) if link.computed],
        "linked_from": [link.inward() for link in index.into(item_id)],
        "lineage": tree if len(tree) >= MIN_TREE_LINES else [],
        "hints": item_hints(items, index, item_id),
    }
    return {key: value for key, value in derived.items() if value}


def workstream_view(items: dict[str, Item], index: Index, item_id: str) -> dict[str, Any]:
    """`show`: the item's resolved workstream and how it was found; for a workstream, its parent,
    the workstreams under it and how many items belong to it."""
    item = items[item_id]
    if item["kind"] == TICKET_KIND:
        return {}
    resolver = resolver_for(items, index)
    if item["kind"] != WORKSTREAM_KIND:
        return resolver.resolve(item_id).as_dict()
    members = resolver.members(workstreams.family(items, item_id))
    shown = {
        "parent": workstreams.parent(items, item_id),
        "children": workstreams.children(items, item_id),
        "items": len(members),
        "open_items": sum(1 for m in members if is_open(items[m])),
    }
    return {key: value for key, value in shown.items() if value}


def describe_kind(kind: str, spec: dict[str, Any]) -> list[str]:
    terminal = ", ".join(sorted(spec["terminal"]))
    done = ", ".join(sorted(spec["work_done"] | spec["terminal"]))
    return [
        f"{kind}: {spec['description']} (initial: {spec['initial']}; terminal: {terminal})",
        f"  ids: {spec['id_format']}; Slack messages about it: --class {spec['source_class']}",
        *(f"  {state} -> {', '.join(sorted(allowed_targets(kind, state)))}" for state in spec["transitions"]),
        *requirement_lines(spec),
        f"  a hand-over to a repo session ends with its report, or on reaching: {done}",
    ]


def requirement_lines(spec: dict[str, Any]) -> list[str]:
    """What entering a state needs: evidence, fields, a pending proposal."""
    lines = []
    for target in sorted({to for _, to in spec["evidence_required"]}):
        sources = ", ".join(sorted(src for src, to in spec["evidence_required"] if to == target))
        lines.append(f"  -> {target} (from {sources}) requires --evidence: {spec['evidence']}")
    for state, names in spec.get("required_fields", {}).items():
        command = f" (set by: ledger {OUTREACH_COMMANDS[state]})" if state in OUTREACH_COMMANDS else ""
        lines.append(f"  {state} needs {', '.join(names)}{command}")
    lines += [
        f"  {state}: entered when you propose its message (ledger propose), left when it is sent "
        "(ledger sent) or the proposal is rejected"
        for state in sorted(spec.get("requires_proposal", ()))
    ]
    return lines


def cmd_kinds(_args: argparse.Namespace) -> str:
    return "\n".join(line for kind, spec in KINDS.items() for line in describe_kind(kind, spec))


def render_pending(open_items: list[Item]) -> list[str]:
    """Proposals awaiting my approval; then those I approved (not made yet, or closing), if any."""
    with_proposal = [i for i in open_items if i.get("proposal")]
    pending = [
        f"- **{i['id']}**: {i['proposal']['action']} (since {i['proposal']['proposed_at']})"
        + (
            ""
            if approvals.proposal_label(i["proposal"]) == approvals.PROPOSAL_STATUSES[approvals.NEEDS_YOU]
            else f" ({approvals.proposal_label(i['proposal'])})"
        )
        for i in with_proposal
        if approvals.needs_you(i["proposal"])
    ]
    approved = [
        f"- **{i['id']}**: {i['proposal']['action']} ({approvals.proposal_label(i['proposal'])})"
        for i in with_proposal
        if not approvals.needs_you(i["proposal"])
    ]
    lines = ["## Awaiting your approval", "", *(pending or ["- none"])]
    return [*lines, "", "## Approved", "", *approved] if approved else lines


def render_item(item: Item, view: View) -> str:
    link = f" ([link]({item['url']}))" if item["url"] else ""
    last = item["history"][-1]
    report = f"\n  - report: {item['report']['summary']}" if item.get("report") else ""
    return (
        f"- `{item['state']}` **{item['id']}**{delegation_marker(item)} {item['title']}{link}"
        f"{waiting_marker(item, view)}{lineage.list_hint(view.index, item['id'])} - last: "
        f"{last['event']} {last['at']}{report}"
    )


def render_kinds(open_items: list[Item], view: View) -> list[str]:
    lines = []
    for kind in KINDS:
        rows = sorted((i for i in open_items if i["kind"] == kind), key=lambda i: i["created_at"])
        if rows:
            lines += ["", f"## {kind}", "", *(render_item(item, view) for item in rows)]
    return lines


def render(ledger: Ledger) -> str:
    """The readable view, ledger.md."""
    items = list(ledger["items"].values())
    open_items = [i for i in items if is_open(i)]
    finished = len(items) - len(open_items)
    runs = ", ".join(f"{name} {at}" for name, at in sorted(ledger["runs"].items())) or "none"
    footer = (
        f"_{finished} finished item(s), {len(ledger['sources'])} processed source(s). Last runs: {runs}._"
    )
    return "\n".join(
        [
            "# Bosun ledger",
            "",
            f"_Generated {now()}. Do not edit; use `bosun ledger`._",
            "",
            *render_pending(open_items),
            *render_kinds(open_items, View.of(ledger["items"])),
            "",
            footer,
            "",
        ]
    )


def cmd_render(_args: argparse.Namespace) -> str:
    with locked_ledger(write=True):
        pass
    return f"wrote {STORE.view}"


# ---- tasks and links ----------------------------------------------------------------------------

MIN_TREE_LINES = 2  # a tree of the item alone says nothing
TASK_FIELDS = ("ask", "requester", "repo", "area", "done_when")


def check_spawn(args: argparse.Namespace) -> None:
    for option, value in (("--title", args.title), ("--ask", args.ask)):
        check_line(option, value)
    for option, value in (
        ("--via", args.via),
        ("--requester", args.requester),
        ("--done-when", args.done_when),
    ):
        if value is not None:
            check_line(option, value)
    if not args.source and not args.via:
        raise LedgerError(
            "a task spawned from no item needs --via: where it came from, e.g. --via 'user: <what I said>'"
        )
    if args.blocks and not args.source:
        raise LedgerError("--blocks says the task blocks the item it was spawned from; give that item")


def next_task_id(items: dict[str, Item]) -> str:
    prefix = f"{id_prefix(TASK_KIND)}{date.today().isoformat()}/"
    numbers = [int(i[len(prefix) :]) for i in items if i.startswith(prefix) and i[len(prefix) :].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1}"


def add_checked_link(items: dict[str, Item], new: Link, note: str = "") -> bool:
    """Validate and store a link on its source, recorded in its history; False if it existed."""
    if not lineage.check_link(items, new):
        return False
    lineage.add_link(items[new.source], new)
    record(items[new.source], "linked", note or f"{new.rel} {new.target}", via=new.via)
    return True


def cmd_spawn(args: argparse.Namespace) -> str:
    """A proposed task spawned from an item (or from what I said); prints its id first."""
    check_spawn(args)
    # Without a source item, where it came from (what I said) stays on the task itself.
    fields = {name: getattr(args, name) for name in TASK_FIELDS if getattr(args, name)}
    fields.update({} if args.source else {"via": args.via})
    with locked_ledger(write=True) as ledger:
        items = ledger["items"]
        source = get_item(ledger, args.source) if args.source else None
        similar = lineage.similar_tasks(items, Index.of(items), args.source, args.title, is_open)
        same = next((t for t in similar if t["title"].strip().lower() == args.title.strip().lower()), None)
        if same:
            return f"{same['id']}\nunchanged: {same['id']} already exists ({same['state']}): {same['title']}"
        cited = same_origin_tasks(items, args, similar)
        task_id = next_task_id(items)
        items[task_id] = fresh_item(task_id, TASK_KIND, args.title, "", fields, f"ask: {args.ask}")
        if source:
            link_spawned(items, task_id, source, args)
    emit_spawn(task_id, args)
    return spawn_output(task_id, args, similar) + same_origin_warning(task_id, cited)


# A task closed this recently still counts when a new one comes from the same source.
RECENTLY_CLOSED_DAYS = 14


def ref_keys(ref: str) -> set[str]:
    """How a reference is compared across tasks: a Slack permalink by its channel and thread (and
    the message itself), another URL without its query, nothing for free text."""
    message, found = slack_message(ref), SLACK_PERMALINK.search(ref)
    if message and found:
        digits = found["ts"]
        ts = f"{digits[:-SLACK_TS_DECIMALS]}.{digits[-SLACK_TS_DECIMALS:]}"
        return {f"slack:{message[0]}/{message[1]}", f"slack:{message[0]}/{ts}"}
    if ref.startswith(("http://", "https://")):
        return {"url:" + ref.split("?", 1)[0].rstrip("/")}
    return set()


def origin_keys(items: dict[str, Item], origins: list[tuple[str, str]]) -> set[str]:
    """What tasks came from, comparable: each (source item, via) as the item, and the Slack threads
    or URLs of the via and of the source's own links (a mention's id is its Slack message)."""
    keys: set[str] = set()
    for source_id, via in origins:
        source = items.get(source_id) if source_id else None
        refs = [via]
        if source:
            keys.add(f"item:{source_id}")
            fields = source["fields"]
            refs += [
                source.get("url") or "",
                fields.get("request_permalink", ""),
                fields.get("reply_ref", ""),
            ]
        if source_id.startswith(id_prefix("mention")):
            keys.add(source_id)  # slack:<channel>/<ts>, like a permalink's message key
        keys.update(key for ref in refs if ref for key in ref_keys(ref))
    return keys


def task_origins(index: Index, task: Item) -> list[tuple[str, str]]:
    found = [(link.target, link.via) for link in index.out(task["id"], lineage.SPAWNED_FROM)]
    return found + ([("", str(task["fields"]["via"]))] if task["fields"].get("via") else [])


def emit_spawn(task_id: str, args: argparse.Namespace) -> None:
    emit(
        "spawn", task_id, summary=f"{args.title} (from {args.source or args.via})", detail=f"ask: {args.ask}"
    )


def same_origin_tasks(items: dict[str, Item], args: argparse.Namespace, known: list[Item]) -> list[Item]:
    """Open or recently closed tasks that came from the same source item, Slack thread or URL as
    the task <args> would spawn (other than the <known> similar ones)."""
    index = Index.of(items)
    wanted = origin_keys(items, [(args.source or "", args.via or "")])
    found = [task for task in recent_tasks(items) if wanted & origin_keys(items, task_origins(index, task))]
    return [task for task in found if task not in known]


def recent_tasks(items: dict[str, Item]) -> list[Item]:
    cutoff = datetime.now(UTC) - timedelta(days=RECENTLY_CLOSED_DAYS)
    tasks = (item for item in items.values() if item["kind"] == TASK_KIND)
    return [task for task in tasks if is_open(task) or parse_time(task["updated_at"]) >= cutoff]


def same_origin_warning(task_id: str, cited: list[Item]) -> str:
    if not cited:
        return ""
    names = "; ".join(f"{t['id']} ({t['state']}{delegation_marker(t)}: {t['title']})" for t in cited)
    return (
        f"\nwarning: a task already comes from the same source or thread: {names}. If it is the same "
        f"work, keep that one: bosun ledger transition '{task_id}' dropped --note 'duplicate of "
        "<kept id>' (a task held by another window stays there; name it in your report)"
    )


def link_spawned(items: dict[str, Item], task_id: str, source: Item, args: argparse.Namespace) -> None:
    """The new task points at its source (spawned_from); the source records it, and with
    --blocks waits for it (blocked_by)."""
    add_checked_link(items, Link(task_id, lineage.SPAWNED_FROM, source["id"], args.via or "", now()))
    record(source, "spawned", f"{task_id}: {args.title}")
    if args.blocks:
        add_checked_link(items, Link(source["id"], lineage.BLOCKED_BY, task_id, at=now()))


def spawn_output(task_id: str, args: argparse.Namespace, similar: list[Item]) -> str:
    origin = f", spawned from {args.source}" if args.source else ""
    blocks = f"; {args.source} is not finished while it is open (blocked_by)" if args.blocks else ""
    if (args.via or "").startswith(USER_EVIDENCE):
        then = (
            f"my instruction is the approval: bosun ledger transition '{task_id}' accepted --note "
            f"'<what I asked>' --evidence '{args.via}', then hand it over with bosun delegate "
            f"new-work --id '{task_id}' ... (which moves it to delegated)"
        )
    else:
        then = (
            f"the orchestrator proposes it to me (bosun ledger propose '{task_id}' '<accept it, and "
            f"the exact delegate command>'); once I say yes: bosun ledger transition '{task_id}' "
            "accepted --note '<...>' --evidence 'user: <what I said>'. A repo session that found it: "
            "do not start it; name it in your report"
        )
    warning = ""
    if similar:
        names = "; ".join(f"{t['id']} ({t['state']}: {t['title']})" for t in similar)
        warning = (
            f"\nwarning: similar open task(s) from the same source: {names}. If it is the same work, keep "
            f"one: bosun ledger transition '{task_id}' dropped --note 'duplicate of <kept id>'"
        )
    return (
        f"{task_id}\ncreated: {task_id} [task] state=proposed{origin}{blocks}. Not accepted work yet.\n"
        f"next: {then}.{warning}"
    )


def ensure_ticket(ledger: Ledger, ticket_id: str) -> str:
    """A ticket named by a tracks link is created (unsynced) if it is new; returns a note."""
    if ticket_id in ledger["items"]:
        return ""
    tickets.parse_id(ticket_id)
    check_repo_mask(ticket_id)
    ledger["items"][ticket_id] = fresh_item(ticket_id, TICKET_KIND, "", "", {})
    return (
        f"; created {ticket_id} [ticket: {tickets.UNSYNCED}]: read it and record what it shows with "
        f"bosun ledger ticket-sync '{ticket_id}' --status '<status>'"
    )


def cmd_link(args: argparse.Namespace) -> str:
    lineage.check_relation(args.rel)
    if args.via is not None:
        check_line("--via", args.via)
    with locked_ledger(write=True) as ledger:
        get_item(ledger, args.id)
        other, moved = link_target(ledger["items"], args)
        created = ensure_ticket(ledger, other) if args.rel == lineage.TRACKS else ""
        new = Link(args.id, args.rel, other, args.via or "", now())
        if not add_checked_link(ledger["items"], new):
            return f"unchanged: {args.id} already {args.rel} {other}"
        hints = item_hints(ledger["items"], Index.of(ledger["items"]), args.id)
        if args.rel == lineage.PART_OF:
            resolver = resolver_for(ledger["items"])
            hints += [finding.message for finding in workstreams.item_findings(resolver, args.id)]
    return f"ok: {args.id} {args.rel} {other}{created}{moved}" + "".join(f"\nhint: {hint}" for hint in hints)


def link_target(items: dict[str, Item], args: argparse.Namespace) -> tuple[str, str]:
    """The link's target (for part_of, the workstream a slug or alias names) and, with --move, the
    workstream the item leaves."""
    if args.rel != lineage.PART_OF:
        if args.move:
            raise LedgerError(f"--move is for {lineage.PART_OF}: an item's one workstream")
        return args.other, ""
    other = workstreams.find(items, args.other)
    return other, move_out(items[args.id], other) if args.move else ""


def move_out(item: Item, target: str) -> str:
    """`link --move`: the item leaves the workstream it was part of (recorded), for <target>."""
    left = [ws for ws in lineage.stored_part_of(item) if ws != target]
    for ws in left:
        lineage.remove_link(item, lineage.PART_OF, ws)
        record(item, "unlinked", f"{lineage.PART_OF} {ws} (moved to {target})")
    return f" (moved from {', '.join(left)})" if left else ""


def cmd_unlink(args: argparse.Namespace) -> str:
    lineage.check_relation(args.rel)
    with locked_ledger(write=True) as ledger:
        item = get_item(ledger, args.id)
        if args.rel == lineage.PART_OF:
            args.other = workstreams.find(ledger["items"], args.other)
        if not lineage.remove_link(item, args.rel, args.other):
            computed = any(
                link.computed and link.target == args.other
                for link in Index.of(ledger["items"]).out(args.id, args.rel)
            )
            if computed:
                raise LedgerError(
                    f"{args.other} is outreach for {args.id} (its parent field); it stops blocking once "
                    f"it is applied or dropped: bosun ledger transition '{args.other}' dropped "
                    "--note '<why>'"
                )
            return f"unchanged: {args.id} has no link {args.rel} {args.other}"
        record(item, "unlinked", f"{args.rel} {args.other}")
    return f"ok: {args.id} no longer {args.rel} {args.other}"


# ---- folding one item into another ---------------------------------------------------------------


def is_folded(item: Item, target_id: str) -> bool:
    """Already folded into <target_id>: closed in its escape state with the link stored."""
    return item["state"] == escape_state(item["kind"]) and any(
        (entry["rel"], entry["id"]) == (lineage.FOLDED_INTO, target_id) for entry in item.get("links") or []
    )


def check_fold(item: Item, target: Item, force_closed_target: bool) -> None:
    """Refuse a fold that is none: into itself, a learning proposal or a ticket (each closes its own
    way), an item already finished, or a finished target (unless --force-closed-target)."""
    item_id, target_id = item["id"], target["id"]
    if item_id == target_id:
        raise LedgerError(f"{item_id} cannot be folded into itself")
    if item["kind"] == LEARNING_KIND:
        raise LedgerError(
            f"{item_id} is a learning proposal: close it with bosun learn reject '{item_id}' --note "
            f"'folded into {target_id}'"
        )
    if KINDS[item["kind"]].get("mirror"):
        raise LedgerError(f"{item_id} mirrors its tracker; fold the work item that tracks it instead")
    if not is_open(item):
        raise LedgerError(
            f"{item_id} is already {item['state']}. To record where its work went: bosun ledger "
            f"link '{item_id}' {lineage.FOLDED_INTO} '{target_id}'"
        )
    if not is_open(target) and not force_closed_target:
        raise LedgerError(
            f"{target_id} is {target['state']}, so nothing carries {item_id}'s work on. Fold it into "
            f"open work; if {target_id} already carried it (e.g. it landed in its PR), add "
            "--force-closed-target"
        )


def withdraw_for_fold(item: Item, note: str) -> str:
    """Withdraw <item>'s pending proposal: what it proposed for the item is neither done on it nor
    rejected; the work goes on under the other item. Returns what was withdrawn."""
    proposal = item.get("proposal")
    if not proposal:
        return ""
    item["proposal"] = None
    record(item, "proposal-withdrawn", note, action=proposal["action"])
    return f"\nproposal withdrawn: {proposal['action']}"


def fold_update_hint(item: Item, target: Item) -> str:
    """For a target held by a repo session: the update that carries the folded item's ask there."""
    delegation = target.get("delegation")
    if not delegation or not is_open(target):
        return ""
    ask = item["fields"].get("ask") or (item.get("delegation") or {}).get("ask") or ""
    text = f"Also carry {item['id']} here (folded into {target['id']}): {item['title'] or item['id']}" + (
        f". Ask: {ask}" if ask else ""
    )
    return (
        f"\nnext: {target['id']} is with window {delegation['window']}; carry the work there: "
        f"bosun delegate --update '{target['id']}' --details {shlex.quote(text)}"
    )


def fold_window_hint(item: Item, target: Item) -> str:
    """The folded item's own window, if it had one other than the target's: it should stop."""
    window = (item.get("delegation") or {}).get("window")
    if not window or window == (target.get("delegation") or {}).get("window"):
        return ""
    return (
        f"\nnote: {item['id']} was handed to window {window}; if it still works on it, tell it to stop: "
        f"bosun send --id '{item['id']}' {window} 'folded into {target['id']}: stop work on it'"
    )


def cmd_fold(args: argparse.Namespace) -> str:
    """<id>'s work goes on as part of <into>: link folded_into, withdraw <id>'s pending proposal,
    close <id> in its kind's escape state. Idempotent."""
    with locked_ledger(write=True) as ledger:
        item, target = get_item(ledger, args.id), get_item(ledger, args.into)
        if is_folded(item, args.into):
            return f"unchanged: {args.id} already folded into {args.into} ({item['state']})"
        check_fold(item, target, args.force_closed_target)
        escape = escape_state(item["kind"])
        what = f"folded into {args.into}" + (f": {args.note}" if args.note else "")
        note = check_open_outreach(ledger, item, escape, what, args.force_open_outreach)
        add_checked_link(ledger["items"], Link(args.id, lineage.FOLDED_INTO, args.into, at=now()))
        withdrawn = withdraw_for_fold(item, what)
        outcome = transition_item(item, escape, note, None, {})
    return (
        f"ok: {args.id} {lineage.FOLDED_INTO} {args.into}; {outcome.removeprefix('ok: ')}{withdrawn}"
        f"{fold_update_hint(item, target)}{fold_window_hint(item, target)}"
    )


def cmd_tree(args: argparse.Namespace) -> str:
    with locked_ledger(write=False) as ledger:
        items = ledger["items"]
        get_item(ledger, args.id)
        index = Index.of(items)
        view = lineage.TreeView(items, index, args.id, delegation_marker)
        lines = view.lines()
        hints = [hint for node in view.shown for hint in item_hints(items, index, node)]
    return "\n".join([*lines, *(f"hint: {hint}" for hint in dict.fromkeys(hints))])


def task_hints(items: dict[str, Item], index: Index, item: Item) -> list[str]:
    """A task whose products all landed is done; a report's PR that is not linked yet."""
    hints = [line.strip().removeprefix("next:").strip() for line in produced_pr_lines(item, index)]
    if item["kind"] == TASK_KIND and is_open(item):
        hints += landed_task_hint(items, index, item["id"])
    return hints


def landed_task_hint(items: dict[str, Item], index: Index, task_id: str) -> list[str]:
    produced = [items[link.target] for link in index.out(task_id, lineage.PRODUCES) if link.target in items]
    landed = [p for p in produced if p["state"] in tickets.LANDED_STATES.get(p["kind"], set())]
    if not landed or any(is_open(p) for p in produced):
        return []
    return [f"all {task_id} produced has landed ({landed[0]['id']}): transition it to done"]


def tracked_tickets(items: dict[str, Item], index: Index, item_id: str) -> list[str]:
    """Tickets that track <item_id>, or the item that produced it."""
    sources = [item_id, *(link.source for link in index.into(item_id, lineage.PRODUCES))]
    return list(dict.fromkeys(link.target for s in sources for link in index.out(s, lineage.TRACKS)))


def linked_work(items: dict[str, Item], index: Index, ticket_id: str) -> list[Item]:
    """The items that track a ticket, and what they produced."""
    trackers = [link.source for link in index.into(ticket_id, lineage.TRACKS)]
    produced = [link.target for t in trackers for link in index.out(t, lineage.PRODUCES)]
    return [items[i] for i in dict.fromkeys([*trackers, *produced]) if i in items]


def ticket_hints(items: dict[str, Item], index: Index, ticket_id: str) -> tickets.Hints:
    """What to propose for a ticket (and what to re-read); another team's mismatches as FYI."""
    stale = tickets.Staleness(timedelta(hours=STORE.stale_hours), datetime.now(UTC))
    work = linked_work(items, index, ticket_id)
    return tickets.ticket_hints(items[ticket_id], work, is_open, stale, STORE.owned_projects)


def item_hints(items: dict[str, Item], index: Index, item_id: str) -> list[str]:
    """Derived hints for an item: its task hints, and those of the tickets tracking it."""
    item = items.get(item_id)
    if item is None:
        return []
    if item["kind"] == TICKET_KIND:
        return ticket_hints(items, index, item_id).lines()
    found = [t for t in tracked_tickets(items, index, item_id) if t in items]
    return task_hints(items, index, item) + [
        hint for t in found for hint in ticket_hints(items, index, t).lines()
    ]


# ---- migration -----------------------------------------------------------------------------------

ID_TOKEN = re.compile(r"[^\s,;()'\"]+")


def origin_target(items: dict[str, Item], item_id: str, origin: str) -> str | None:
    """The one ledger item an `origin` field names (the whole value, or a single id in it)."""
    if origin.strip() in items:
        return origin.strip()
    found = {token.rstrip(".") for token in ID_TOKEN.findall(origin)} & set(items)
    found.discard(item_id)
    return found.pop() if len(found) == 1 else None


def migrate_origin(items: dict[str, Item], item_id: str, origin: str) -> str:
    target = origin_target(items, item_id, origin)
    if target is None or target == item_id:
        return f"left: {item_id} origin {origin!r} names no single ledger item (link it by hand)"
    via = "" if origin.strip() == target else origin.strip()
    try:
        add_checked_link(
            items, Link(item_id, lineage.SPAWNED_FROM, target, via, now()), "migrated from origin"
        )
    except LineageError as exc:
        return f"left: {item_id} origin {origin!r}: {exc}"
    del items[item_id]["fields"]["origin"]
    record(items[item_id], "links-migrated", f"field origin {origin!r} -> {lineage.SPAWNED_FROM} {target}")
    return f"{item_id}: origin -> {lineage.SPAWNED_FROM} {target}"


MIGRATED_FROM_NOTE = "migrated from note"


def merged_notes(item: Item) -> list[tuple[str, str]]:
    """What the notes of transitions to the escape state say took the item over, as (phrase, id)."""
    escape = escape_state(item["kind"])
    notes = (
        str(h.get("note", ""))
        for h in item["history"]
        if h.get("event") == "transition" and h.get("to") == escape
    )
    return [named for note in notes if (named := note_link(note))]


def merged_duplicate(item: Item, other: str, named: list[tuple[str, str]]) -> bool:
    """Whether <item>'s duplicate_of <other> was recorded from a drop note that said 'merged into
    <other>', from before such notes recorded folded_into: the link was recorded by a drop or by
    this migration (its "linked" entry), and no drop note calls it a duplicate."""
    stored = any(
        (entry["rel"], entry["id"]) == (lineage.DUPLICATE_OF, other) for entry in item.get("links") or []
    )
    recorded = any(
        h.get("event") == "linked"
        and h.get("note") in (f"{lineage.DUPLICATE_OF} {other}", MIGRATED_FROM_NOTE)
        for h in item["history"]
    )
    return stored and recorded and ("merged into", other) in named and ("duplicate of", other) not in named


def refold_duplicate(items: dict[str, Item], item_id: str, other: str) -> str:
    """Turn a duplicate_of <other> recorded from a 'merged into' note into folded_into <other>."""
    new = Link(item_id, lineage.FOLDED_INTO, other, at=now())
    try:
        missing = lineage.check_link(items, new)
    except LineageError as exc:
        return f"left: {item_id} {lineage.DUPLICATE_OF} {other}: {exc}"
    item = items[item_id]
    lineage.remove_link(item, lineage.DUPLICATE_OF, other)
    if missing:
        lineage.add_link(item, new)
    change = f"{lineage.DUPLICATE_OF} {other} -> {lineage.FOLDED_INTO} {other}"
    record(item, "links-migrated", f"{change} (its drop note said 'merged into')")
    return f"{item_id}: 'merged into' note: {change}"


def migrate_merged(items: dict[str, Item], item_id: str) -> list[str]:
    changes = []
    named = merged_notes(items[item_id])
    for how, other in named:
        if other not in items or other == item_id:
            continue
        if merged_duplicate(items[item_id], other, named):
            changes.append(refold_duplicate(items, item_id, other))
            continue
        rel = NOTE_RELATIONS[how]
        try:
            added = add_checked_link(items, Link(item_id, rel, other, at=now()), MIGRATED_FROM_NOTE)
        except LineageError as exc:
            changes.append(f"left: {item_id} {how} {other}: {exc}")
            continue
        if added:
            changes.append(f"{item_id}: '{how}' note -> {rel} {other}")
    return changes


def cmd_migrate_links(args: argparse.Namespace) -> str:
    """Move ad hoc provenance into links: fields.origin -> spawned_from; drop notes 'merged into'
    or 'folded into' -> folded_into, 'duplicate of' -> duplicate_of; and duplicate_of links
    recorded from a 'merged into' note (before such notes meant folded_into) -> folded_into.
    Idempotent; --dry-run writes nothing."""
    with locked_ledger(write=not args.dry_run) as ledger:
        items = ledger["items"]
        changes = []
        for item_id in sorted(items):
            origin = items[item_id]["fields"].get("origin")
            if origin:
                changes.append(migrate_origin(items, item_id, str(origin)))
            changes += migrate_merged(items, item_id)
    if not changes:
        return "nothing to migrate"
    done = "dry run: nothing written" if args.dry_run else f"written; the previous ledger is {STORE.backup}"
    return "\n".join([*changes, f"({len(changes)} change(s); {done})"])


# ---- tickets -------------------------------------------------------------------------------------


def ticket_changes(item: Item, args: argparse.Namespace, category: str) -> dict[str, Any]:
    wanted = {"state": category, "external_status": args.status, "title": args.title, "url": args.url}
    wanted["assignee"] = args.assignee
    if args.parent:
        parent = tickets.ticket_ref(args.parent, item["fields"]["system"])
        if parent == item["id"]:
            raise LedgerError(f"{item['id']} cannot be its own parent")
        wanted[workstreams.PARENT_TICKET_FIELD] = parent
    current = {**item["fields"], "state": item["state"], "title": item["title"], "url": item["url"]}
    return {key: value for key, value in wanted.items() if value is not None and current.get(key) != value}


def apply_ticket_sync(item: Item, changes: dict[str, Any]) -> str:
    """Store what the tracker shows; history only on a change. synced_at is stamped either way."""
    before = item["state"]
    for key, value in changes.items():
        if key in ("state", "title", "url"):
            item[key] = value
        else:
            item["fields"][key] = value
    item["fields"]["status_category"] = item["state"]
    item["fields"]["synced_at"] = now()
    if not changes:
        status = item["fields"].get("external_status")
        return f"unchanged: {item['id']} {item['state']} ('{status}'); synced_at stamped"
    record(
        item,
        "synced",
        f"{before} -> {item['state']}",
        **{"from": before, "to": item["state"]},
        fields=changes,
    )
    return f"ok: {item['id']} {before} -> {item['state']} ('{item['fields']['external_status']}')"


def cmd_ticket_sync(args: argparse.Namespace) -> str:
    ticket = tickets.parse_id(args.id)
    for option, value in (("--status", args.status), ("--title", args.title), ("--assignee", args.assignee)):
        if value is not None:
            check_line(option, value)
    category = tickets.categorize(
        ticket, args.status, args.category, tickets.parse_overrides(STORE.status_map)
    )
    with locked_ledger(write=True) as ledger:
        ensure_ticket(ledger, args.id)
        item = ledger["items"][args.id]
        if item["kind"] != TICKET_KIND:
            raise LedgerError(f"{args.id} is a {item['kind']}, not a ticket")
        outcome = apply_ticket_sync(item, ticket_changes(item, args, category))
        hints = ticket_hints(ledger["items"], Index.of(ledger["items"]), args.id)
    return outcome + "".join(
        [*(f"\nhint: {hint}" for hint in hints.propose), *(f"\nfyi: {hint}" for hint in hints.fyi)]
    )


def ticket_lines(ticket: Item, work: list[Item], hints: tickets.Hints) -> list[str]:
    fields = ticket["fields"]
    synced = f"synced {age(fields['synced_at'])} ago" if fields.get("synced_at") else "never synced"
    status = f" '{fields['external_status']}'," if fields.get("external_status") else ""
    title = f" - {ticket['title']}" if ticket["title"] else ""
    url = f" ({ticket['url']})" if ticket["url"] else ""
    backend = tickets.BACKENDS[fields["system"]]
    return [
        f"{ticket['id']}  [{ticket['state']}]{status} {synced}{title}{url}",
        *(f"  tracked by: {w['id']} [{w['kind']}: {w['state']}]" for w in work),
        f"  read:  {backend.read}",
        *(f"  hint:  {hint}" for hint in hints.propose),
        *(f"  fyi:   {hint}" for hint in hints.fyi),
    ]


def ticket_rows(items: dict[str, Item], index: Index, ticket: Item, args: argparse.Namespace) -> list[str]:
    """One ticket's lines, if it is listed: open or tracking open work (or --all), and with
    --mismatch only when there is something to propose (or, for another team's ticket, an FYI)."""
    work = linked_work(items, index, ticket["id"])
    if not args.all and not (is_open(ticket) or any(is_open(w) for w in work)):
        return []
    hints = ticket_hints(items, index, ticket["id"])
    return ticket_lines(ticket, work, hints) if hints.lines() or not args.mismatch else []


def cmd_tickets(args: argparse.Namespace) -> str:
    """Tickets of open work (or --all), how to read each, and where they disagree with our work."""
    with locked_ledger(write=False) as ledger:
        items = ledger["items"]
        index = Index.of(items)
        ordered = sorted((i for i in items.values() if i["kind"] == TICKET_KIND), key=lambda i: i["id"])
        rows = [line for ticket in ordered for line in ticket_rows(items, index, ticket, args)]
    return "\n".join(rows) or "(no tickets)"


# ---- workstreams ----------------------------------------------------------------------------------


def read_rules(items: dict[str, Item]) -> workstreams.RuleSet:
    """The site's workstream rules ($BOSUN_CONFIG_DIR/workstreams.json; none if it is missing),
    with each workstream's own channel."""
    path = STORE.rules_file
    data = None
    if path and path.exists():
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError(f"{path} is not valid JSON ({exc}); fix it or move it aside") from exc
    return workstreams.parse_rules(items, data)


def resolver_for(items: dict[str, Item], index: Index | None = None) -> workstreams.Resolver:
    return workstreams.Resolver(items, read_rules(items), index)


def check_day(option: str, value: str | None) -> None:
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise LedgerError(f"{option} takes a date YYYY-MM-DD, not {value!r}") from exc


def ws_given_fields(args: argparse.Namespace) -> dict[str, Any]:
    """The fields `ws add` was given (only those, so a repeat changes nothing else)."""
    for option in ("started", "ended", "target"):
        check_day(f"--{option}", getattr(args, option))
    for option, value in (("--title", args.title), ("--area", args.area)):
        if value is not None:
            check_line(option, value)
    fields: dict[str, Any] = {
        "epic": tickets.ticket_ref(args.epic, "jira") if args.epic else None,
        "channels": sorted(set(args.channel)) if args.channel else None,
        "repos": sorted(set(args.repo)) if args.repo else None,
        "started": args.started,
        "ended": args.ended,
        "target": args.target,
        "area": args.area,
    }
    return {key: value for key, value in fields.items() if value is not None}


def add_aliases(items: dict[str, Item], ws: str, names: Iterable[str]) -> list[str]:
    """Add aliases to <ws>, refusing one another workstream answers to; returns those added."""
    item, added = items[ws], []
    for name in names:
        check_line("--alias", name)
        owner = workstreams.alias_owner(items, name)
        if owner and owner != ws:
            raise LedgerError(f"{owner} already answers to {name!r}; an alias names one workstream")
        if name.casefold() not in {a.casefold() for a in workstreams.aliases(item)} and owner != ws:
            item["fields"].setdefault("aliases", []).append(name)
            added.append(name)
    return added


def create_or_update_ws(
    items: dict[str, Item], ws: str, args: argparse.Namespace, fields: dict[str, Any]
) -> tuple[Item, str]:
    """A new workstream (it needs a title; without --started its rules reach back to any date), or
    the given fields and title applied to the existing one."""
    item = items.get(ws)
    if item is None:
        if not args.title:
            raise LedgerError(f"a new workstream needs --title: bosun ledger ws add '{ws}' --title '<what>'")
        item = items[ws] = fresh_item(ws, WORKSTREAM_KIND, args.title, "", fields)
        return item, "created"
    if item["kind"] != WORKSTREAM_KIND:
        raise LedgerError(f"{ws} is a {item['kind']}, not a workstream")
    args.id, args.kind, args.url, args.field = ws, None, None, None
    return item, update_item(item, args, fields)


def ws_add(args: argparse.Namespace) -> str:
    """Create a workstream, or update the fields given; idempotent."""
    ws = workstreams.ws_id(args.name or "")
    fields = ws_given_fields(args)
    with locked_ledger(write=True) as ledger:
        items = ledger["items"]
        item, outcome = create_or_update_ws(items, ws, args, fields)
        added = add_aliases(items, ws, args.alias or [])
        parented = ws_parent(items, ws, args.parent) if args.parent else ""
        if (added or parented) and outcome == "unchanged":
            outcome = "updated"
    aliases = f"; aliases added: {', '.join(added)}" if added else ""
    return f"{outcome}: {ws} [workstream: {item['state']}] {item['title']}{aliases}{parented}"


def ws_parent(items: dict[str, Item], ws: str, name: str) -> str:
    target = workstreams.find(items, name)
    if add_checked_link(items, Link(ws, lineage.PART_OF, target, at=now())):
        return f"; part_of {target}"
    return ""


def ws_counts_line(resolver: workstreams.Resolver, ws: str) -> str:
    members = resolver.members({ws})
    rollup = resolver.members(workstreams.family(resolver.items, ws))
    bases = workstreams.basis_counts(resolver, members)
    how = ", ".join(f"{basis} {count}" for basis, count in bases.items() if count)
    total = (
        f"; with the workstreams under it: {len(rollup)} item(s), {open_count(resolver.items, rollup)} open"
        if len(rollup) != len(members)
        else ""
    )
    return f"{len(members)} item(s) ({how or 'none'}), {open_count(resolver.items, members)} open{total}"


def open_count(items: Mapping[str, Item], ids: Iterable[str]) -> int:
    return sum(1 for item_id in ids if is_open(items[item_id]))


def ws_list_lines(resolver: workstreams.Resolver, show_all: bool) -> list[str]:
    items, lines = resolver.items, []
    for top in workstreams.top_level(items):
        for ws in [top, *workstreams.children(items, top)]:
            item = items[ws]
            if item["state"] == workstreams.DONE and not show_all:
                continue
            indent = "  " * (workstreams.depth(items, ws) - 1)
            lines.append(f"{indent}{ws}  [{item['state']}] {item['title']} | {ws_counts_line(resolver, ws)}")
    unassigned = resolver.unassigned()
    lines.append(
        f"unassigned: {len(unassigned)} item(s), {sum(1 for i in unassigned if is_open(items[i]))} open"
    )
    return lines


def ws_list(args: argparse.Namespace) -> str:
    with locked_ledger(write=False) as ledger:
        resolver = resolver_for(ledger["items"])
    return "\n".join(ws_list_lines(resolver, args.all))


def ws_show(args: argparse.Namespace) -> str:
    with locked_ledger(write=False) as ledger:
        items = ledger["items"]
        ws = workstreams.find(items, args.name or "")
        resolver = resolver_for(items)
    item, fields = items[ws], items[ws]["fields"]
    shown = [f"{key}: {', '.join(v) if isinstance(v, list) else v}" for key, v in sorted(fields.items()) if v]
    above, below = workstreams.parent(items, ws), workstreams.children(items, ws)
    return "\n".join(
        [
            f"{ws}  [{item['state']}] {item['title']}",
            *(f"  {line}" for line in shown),
            *([f"  part of: {above}"] if above else []),
            *([f"  holds: {', '.join(below)}"] if below else []),
            f"  items: {ws_counts_line(resolver, ws)}",
            *ws_member_lines(resolver, ws),
        ]
    )


def ws_member_lines(resolver: workstreams.Resolver, ws: str) -> list[str]:
    """`ws show`: its items by kind (open of all), and the ones tagged explicitly."""
    items, members = resolver.items, resolver.members({ws})
    by_kind: dict[str, list[str]] = {}
    for member in members:
        by_kind.setdefault(items[member]["kind"], []).append(member)
    lines = [
        f"  {kind}: {len(ids)} ({sum(1 for i in ids if is_open(items[i]))} open)"
        for kind, ids in sorted(by_kind.items())
    ]
    return lines + [f"  tagged: {i}" for i in members if resolver.resolve(i).basis == workstreams.EXPLICIT]


def ws_set_state(args: argparse.Namespace) -> str:
    """Pause, finish or reopen a workstream; finishing records its end date if it has none."""
    if not args.value or not args.note:
        raise LedgerError("usage: bosun ledger ws set-state <ws> active|paused|done --note '<why>'")
    with locked_ledger(write=True) as ledger:
        ws = workstreams.find(ledger["items"], args.name or "")
        item = ledger["items"][ws]
        ended = (
            {"ended": date.today().isoformat()}
            if args.value == workstreams.DONE and not item["fields"].get("ended")
            else {}
        )
        return transition_item(item, args.value, args.note, None, ended)


def ws_alias(args: argparse.Namespace) -> str:
    if not args.value:
        raise LedgerError("usage: bosun ledger ws alias <ws> <alias> [--remove]")
    with locked_ledger(write=True) as ledger:
        items = ledger["items"]
        ws = workstreams.find(items, args.name or "")
        if args.remove:
            kept = [a for a in workstreams.aliases(items[ws]) if a.casefold() != args.value.casefold()]
            if len(kept) == len(workstreams.aliases(items[ws])):
                return f"unchanged: {ws} has no alias {args.value!r}"
            items[ws]["fields"]["aliases"] = kept
            record(items[ws], "updated", f"alias removed: {args.value}")
            return f"ok: {ws} no longer answers to {args.value!r}"
        if not add_aliases(items, ws, [args.value]):
            return f"unchanged: {ws} already answers to {args.value!r}"
        record(items[ws], "updated", f"alias added: {args.value}")
    return f"ok: {ws} also answers to {args.value!r}"


WS_ACTIONS: dict[str, Callable[[argparse.Namespace], str]] = {
    "add": ws_add,
    "list": ws_list,
    "show": ws_show,
    "set-state": ws_set_state,
    "alias": ws_alias,
}


def cmd_ws(args: argparse.Namespace) -> str:
    if args.action not in ("list",) and not args.name:
        raise LedgerError(f"usage: bosun ledger ws {args.action} <ws> ...")
    return WS_ACTIONS[args.action](args)


def cmd_assign(args: argparse.Namespace) -> str:
    """Turn epic and rule resolutions into explicit part_of links on the topmost items (dry run
    unless --apply); uncertain cases are printed as proposals, never applied."""
    if args.apply and args.dry_run:
        raise LedgerError("give --dry-run or --apply, not both")
    with locked_ledger(write=args.apply) as ledger:
        items = ledger["items"]
        only = workstreams.family(items, workstreams.find(items, args.ws)) if args.ws else None
        changes, uncertain = workstreams.assign_plan(resolver_for(items), only)
        applied = [change for change in changes if args.apply and apply_assignment(items, change)]
    return "\n".join(assign_lines(changes, uncertain, len(applied) if args.apply else None))


def assign_lines(
    changes: list[workstreams.Change], uncertain: list[workstreams.Finding], applied: int | None
) -> list[str]:
    """What `assign` did or would do (<applied>: None for a dry run), and what it proposes."""
    verb = "would assign" if applied is None else "assigned"
    lines = [f"{verb}: {change.line()}" for change in changes]
    lines += [f"propose: {finding.item}: {finding.message}" for finding in uncertain]
    tail = (
        "dry run: nothing written (--apply records them)"
        if applied is None
        else f"{applied} link(s) recorded"
    )
    return [*lines, f"({len(changes)} change(s), {len(uncertain)} proposal(s); {tail})"]


def apply_assignment(items: dict[str, Item], change: workstreams.Change) -> bool:
    try:
        return add_checked_link(
            items,
            Link(change.item, lineage.PART_OF, change.ws, at=now()),
            f"assigned: {change.basis}: {change.why}",
        )
    except LineageError as exc:
        print(f"ledger: skipped {change.item}: {exc}", file=sys.stderr)
        return False


def summary_since(value: str | None) -> datetime:
    """--since: a date (local midnight), an ISO time, HH:MM today; default: this week's Monday."""
    local_now = datetime.now().astimezone()
    if not value:
        return ws_summary.week_start(local_now)
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        return parse_since(value)
    return datetime(day.year, day.month, day.day, tzinfo=local_now.tzinfo)


def cmd_summary(args: argparse.Namespace) -> str:
    since = summary_since(args.since)
    with locked_ledger(write=False) as ledger:
        resolver = resolver_for(ledger["items"])
        if args.ws and args.ws != workstreams.UNASSIGNED_NAME:
            found = [
                ws_summary.workstream_summary(
                    resolver, workstreams.find(resolver.items, args.ws), since, is_open
                )
            ]
        elif args.ws:
            found = [ws_summary.unassigned_summary(resolver, since, is_open)]
        else:
            found = ws_summary.all_summaries(resolver, since, is_open)
    return ws_summary.render_all(found, args.markdown)


def cmd_lint(args: argparse.Namespace) -> tuple[str, int]:
    """What is doubtful in the ledger's workstreams (findings; proposals never applied)."""
    with locked_ledger(write=False) as ledger:
        resolver = resolver_for(ledger["items"])
        findings = workstreams.lint(resolver, is_open, datetime.now(UTC))
    code = 1 if args.strict and findings else 0
    if args.json:
        return json.dumps([f.as_dict() for f in findings], indent=2), code
    return "\n".join(f.line() for f in findings) or "(no findings)", code


Handler = Callable[[argparse.Namespace], "str | tuple[str, int]"]
Arguments = list[tuple[tuple[str, ...], dict[str, Any]]]
REACH_OUT_ARGUMENTS: Arguments = [
    (("parent",), {"nargs": "?"}),
    (("--channel",), {"choices": tuple(CHANNELS), "default": "slack"}),
    (("--to",), {"required": True}),
    (("--purpose",), {"choices": PURPOSES, "default": "question"}),
    (("--subject", "--question"), {"dest": "subject", "required": True}),
    (("--option",), {"action": "append"}),
    (("--details",), {}),
    (("--expect-by",), {}),
]
# subcommand -> (handler, arguments as (flags, add_argument options))
COMMANDS: dict[str, tuple[Handler, Arguments]] = {
    "list": (
        cmd_list,
        [
            (("--open",), {"action": "store_true"}),
            (("--kind",), {}),
            (("--proposals",), {"action": "store_true"}),
            (("--ws",), {}),
        ],
    ),
    "show": (cmd_show, [(("id",), {})]),
    "upsert": (
        cmd_upsert,
        [
            (("id",), {}),
            (("--kind",), {}),
            (("--title",), {}),
            (("--url",), {}),
            (("--field",), {"action": "append"}),
        ],
    ),
    "transition": (
        cmd_transition,
        [
            (("id",), {}),
            (("state",), {}),
            (("--note",), {"required": True}),
            (("--evidence",), {}),
            (("--field",), {"action": "append"}),
            (
                ("--force-open-outreach", "--force-blocked"),
                {"action": "store_true", "dest": "force_open_outreach"},
            ),
        ],
    ),
    "note": (cmd_note, [(("id",), {}), (("text",), {})]),
    "report": (
        cmd_report,
        [
            (("id",), {}),
            (("--summary",), {"required": True}),
            (("--result",), {"action": "append"}),
            (("--open",), {}),
            (("--ci",), {"choices": CI_STATES}),
            (("--state",), {}),
            (("--evidence",), {}),
            (("--field",), {"action": "append"}),
            (
                ("--force-open-outreach", "--force-blocked"),
                {"action": "store_true", "dest": "force_open_outreach"},
            ),
        ],
    ),
    "reports": (cmd_reports, [(("--since",), {})]),
    "delegation": (cmd_delegation, [(("id",), {})]),
    "said": (
        cmd_said,
        [(("id",), {"nargs": "?"}), (("--since",), {}), (("--window",), {})],
    ),
    "working": (cmd_working, [(("id",), {}), (("--window",), {}), (("--note",), {})]),
    "idle": (cmd_idle, [(("id",), {}), (("--note",), {})]),
    "record-delegation": (
        cmd_record_delegation,
        [
            (("id",), {}),
            (("--window",), {"required": True}),
            (("--task",), {"required": True}),
            (("--ask",), {"default": ""}),
            (("--link",), {}),
            (("--requester",), {}),
            (("--details",), {}),
        ],
    ),
    "record-update": (cmd_record_update, [(("id",), {}), (("--text",), {"required": True})]),
    "record-move": (
        cmd_record_move,
        [
            (("id",), {}),
            (("--window",), {"required": True}),
            (("--text",), {"required": True}),
            (("--notified",), {"action": "store_true"}),
        ],
    ),
    "propose": (
        cmd_propose,
        [
            (("id",), {}),
            (("action",), {"nargs": "?"}),
            (("--send",), {"choices": (approvals.SLACK,)}),
            (("--channel",), {}),
            (("--thread",), {}),
            (("--text",), {}),
            (("--run",), {}),
            (("--tool",), {}),
            (("--args-json",), {}),
        ],
    ),
    "approve": (cmd_approve, [(("id",), {}), (("--quote",), {"required": True})]),
    "approvals": (cmd_approvals, [(("--since",), {}), (("--auto",), {"action": "store_true"})]),
    "resolve": (
        cmd_resolve,
        [(("id",), {}), (("outcome",), {"choices": PROPOSAL_OUTCOMES}), (("--note",), {"default": ""})],
    ),
    "resolve-carried-out": (
        cmd_resolve_carried_out,
        [(("--dry-run",), {"action": "store_true"}), (("--transcript",), {"action": "append"})],
    ),
    "reach-out": (cmd_reach_out, REACH_OUT_ARGUMENTS),
    # The same command, for prompts that read "ask Dana ...".
    "ask": (cmd_reach_out, REACH_OUT_ARGUMENTS),
    "sent": (
        cmd_sent,
        [
            (("ids",), {"nargs": "+", "metavar": "id"}),
            (("--ref",), {"required": True}),
            (("--channel",), {"choices": tuple(CHANNELS)}),
            (("--note",), {"default": ""}),
            (("--at",), {}),
        ],
    ),
    "replied": (
        cmd_replied,
        [
            (("id",), {}),
            (("--evidence",), {"required": True}),
            (("--summary",), {"required": True}),
            (("--at",), {}),
        ],
    ),
    "outreach": (cmd_outreach, [(("--open",), {"action": "store_true"}), (("--parent",), {})]),
    "in-flight": (cmd_in_flight, [(("window",), {})]),
    "compact-context": (
        cmd_compact_context,
        [
            (("--window",), {"default": ""}),
            (("--orchestrator",), {"action": "store_true"}),
            (("--max-chars",), {"type": int, "default": compact_context.DEFAULT_MAX_CHARS}),
        ],
    ),
    "mark-run": (cmd_mark_run, [(("name",), {})]),
    "seen": (cmd_seen, [(("key",), {})]),
    "record-source": (
        cmd_record_source,
        [
            (("key",), {}),
            (("--class",), {"dest": "cls", "required": True, "choices": SOURCE_CLASSES}),
            (("--item",), {}),
        ],
    ),
    "spawn": (
        cmd_spawn,
        [
            (("source",), {"nargs": "?"}),
            (("--title",), {"required": True}),
            (("--ask",), {"required": True}),
            (("--via",), {}),
            (("--requester",), {}),
            (("--repo",), {}),
            (("--area",), {}),
            (("--done-when",), {}),
            (("--blocks",), {"action": "store_true"}),
        ],
    ),
    "link": (
        cmd_link,
        [
            (("id",), {}),
            (("rel",), {}),
            (("other",), {}),
            (("--via",), {}),
            (("--move",), {"action": "store_true"}),
        ],
    ),
    "unlink": (cmd_unlink, [(("id",), {}), (("rel",), {}), (("other",), {})]),
    "fold": (
        cmd_fold,
        [
            (("id",), {}),
            (("--into",), {"required": True}),
            (("--note",), {"default": ""}),
            (("--force-closed-target",), {"action": "store_true"}),
            (
                ("--force-open-outreach", "--force-blocked"),
                {"action": "store_true", "dest": "force_open_outreach"},
            ),
        ],
    ),
    "tree": (cmd_tree, [(("id",), {})]),
    "migrate-links": (cmd_migrate_links, [(("--dry-run",), {"action": "store_true"})]),
    "ticket-sync": (
        cmd_ticket_sync,
        [
            (("id",), {}),
            (("--status",), {"required": True}),
            (("--category",), {}),
            (("--title",), {}),
            (("--url",), {}),
            (("--assignee",), {}),
            (("--parent", "--epic"), {"dest": "parent"}),
        ],
    ),
    "tickets": (
        cmd_tickets,
        [(("--all",), {"action": "store_true"}), (("--mismatch",), {"action": "store_true"})],
    ),
    "ws": (
        cmd_ws,
        [
            (("action",), {"choices": tuple(WS_ACTIONS)}),
            (("name",), {"nargs": "?"}),
            (("value",), {"nargs": "?"}),
            (("--title",), {}),
            (("--epic",), {}),
            (("--channel",), {"action": "append"}),
            (("--repo",), {"action": "append"}),
            (("--started",), {}),
            (("--ended",), {}),
            (("--target",), {}),
            (("--area",), {}),
            (("--parent",), {}),
            (("--alias",), {"action": "append"}),
            (("--note",), {}),
            (("--remove",), {"action": "store_true"}),
            (("--all",), {"action": "store_true"}),
        ],
    ),
    "assign": (
        cmd_assign,
        [
            (("--dry-run",), {"action": "store_true"}),
            (("--apply",), {"action": "store_true"}),
            (("--ws",), {}),
        ],
    ),
    "summary": (
        cmd_summary,
        [
            (("--ws",), {}),
            (("--all",), {"action": "store_true"}),
            (("--since",), {}),
            (("--markdown",), {"action": "store_true"}),
        ],
    ),
    "lint": (
        cmd_lint,
        [(("--json",), {"action": "store_true"}), (("--strict",), {"action": "store_true"})],
    ),
    "kinds": (cmd_kinds, []),
    "prune": (cmd_prune, [(("--days",), {"type": int, "default": DEFAULT_PRUNE_DAYS})]),
    "render": (cmd_render, []),
    "diff": (cmd_diff, [(("before",), {})]),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bosun ledger", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (handler, arguments) in COMMANDS.items():
        command = sub.add_parser(name)
        for flags, options in arguments:
            command.add_argument(*flags, **options)
        command.set_defaults(func=handler)
    return parser


def configure_from_environment() -> None:
    """The settings `bosun ledger` passes (load_config does not export them)."""
    configure(
        os.environ["BOSUN_STATE_DIR"],
        os.environ.get("BOSUN_REPO_MASK", ""),
        os.environ.get("BOSUN_TICKET_STATUS_MAP", ""),
        hours_setting("BOSUN_TICKET_STALE_HOURS", DEFAULT_TICKET_STALE_HOURS),
        events_enabled=os.environ.get("BOSUN_EVENTS_ENABLED") == "1",
        caller_window=os.environ.get("BOSUN_CALLER_WINDOW", ""),
    )
    configure_site(os.environ.get("BOSUN_CONFIG_DIR") or None)
    configure_tickets(os.environ.get(tickets.OWNED_SETTING, ""), os.environ.get("BOSUN_ORCHESTRATOR_DIR", ""))
    configure_scan(
        hours_setting("BOSUN_SLACK_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS),
        hours_setting("BOSUN_SLACK_MAX_LOOKBACK_HOURS", DEFAULT_MAX_LOOKBACK_HOURS),
    )


def hours_setting(name: str, default: float) -> float:
    value = os.environ.get(name) or str(default)
    try:
        return float(value)
    except ValueError as exc:
        raise LedgerError(f"{name} must be a number of hours, not {value!r}") from exc


def main() -> int:
    args = build_parser().parse_args()
    if not os.environ.get("BOSUN_STATE_DIR"):
        print("ledger: BOSUN_STATE_DIR is not set; run it as `bosun ledger`", file=sys.stderr)
        return 2
    handler: Handler = args.func
    try:
        configure_from_environment()
        result = handler(args)
    except (LedgerError, LineageError, TicketError, WorkstreamError) as exc:
        print(f"ledger: {exc}", file=sys.stderr)
        return 1
    output, code = result if isinstance(result, tuple) else (result, 0)
    if output:
        print(output)
    return code


if __name__ == "__main__":
    sys.exit(main())
