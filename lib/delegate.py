#!/usr/bin/env python3
"""Hand a task to a repo session, built from the task-type files in orchestrator/tasks/.

Usage: bosun delegate --list [--markdown]
       bosun delegate <type>[,<type>...] --id <ledger id> [--id ...] --ask "<one line>"
                          [--link <url>] [--requester <who>] [--details "<text>" | --details -]
                          (--print | --window <window> | --new <window> <dir> | --start <window>)
                          [--again]
       bosun delegate --update <ledger id> (--details "<text>" | --details -)
                          [--window <window> | --print]
       bosun delegate --update <out-id> [--details ...] [--print]

The task text is the header (ledger ids, link, requester, ask, details), then the block of each
type in the order given, then the rules every task shares (orchestrator/tasks/_common.md). The
orchestrator never writes the rules itself, so they cannot drift from the files. Once the task is
delivered, the ledger records the window and the header on each item (`bosun ledger
delegation <id>`).

  --again      hand over an item whose last hand-over still awaits a report. Without it
               delegate refuses, says where and when the item went, and points to --update. A
               hand-over ends with the session's report, or once the item reaches a state that
               shows the work done (`bosun ledger kinds`: e.g. reviewed, awaiting-review), so
               a re-review or the next fix round after a push needs no --again.
  --update     add to or correct a task already handed over: the text goes to the window the
               ledger recorded for the item, framed as "Update to ledger item <id>", and is kept
               in the item's history. Refused if the item was never delegated or is finished;
               fails if that window is gone or not taking input.
  --update <id> --window <window>
               move the item to another window that runs Claude: it gets the whole task again
               (from the header the ledger kept), led by the move and the update text. Nothing
               changes if it does not take it. Then the old window is told, by id, to stop
               (best effort: a gone or stuck window is reported and recorded, not fatal), and
               the ledger records the move (from, to, whether the old window was told).
  --update <out-id>
               hand the reply to an outreach item (`bosun ledger replied`) to the window
               working on its parent item, as an update to the parent; the reply text comes from
               the ledger (--details adds to it). Once delivered, the outreach becomes applied.
               Refused for outreach without a reply, without a parent, or whose parent no
               window holds: then tell me the reply and transition it to applied.

Sites can add task types in $BOSUN_CONFIG_DIR/tasks/<type>.md (a file named like a built-in
type replaces it) and add shared rules in $BOSUN_CONFIG_DIR/tasks/_common.md.

  --list       the task types: what the repo session may and may never do, and what the
               orchestrator does afterwards (--markdown: the rows of the task table in docs/orchestrator.md)
  --details -  read the details from stdin (use a quoted heredoc: --details - <<'EOF')
  --model, --effort  override the task type's model and effort (front matter `model`/`effort`;
               "session" means the session's own). A new window starts with them; a running or
               resumed session runs the task in a subagent with them.
  --print      print the task text only
  --window     paste it into a window already running Claude (bosun send <window>)
  --new        open a new window with a fresh session (bosun send --new)
  --start      start Claude in an existing window that has none (bosun send --start)

Every --id must exist in the ledger and be of a kind the type takes (e.g. `review` takes
review-request items).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "orchestrator" / "tasks"
COMMON_FILE = TASKS_DIR / "_common.md"
COMMON_NAME = "_common.md"


def site_tasks_dir() -> Path | None:
    """$BOSUN_CONFIG_DIR/tasks: the site's own task types (`bosun delegate` passes it)."""
    config_dir = os.environ.get("BOSUN_CONFIG_DIR")
    return Path(config_dir) / "tasks" if config_dir else None


BOSUN = str(ROOT / "bin" / "bosun")
SUBAGENT_HARNESS = "claude"  # the agent whose Agent tool subagent_preamble names
FRONT_MATTER_FIELDS = ("title", "kinds", "may", "never", "followup")
ANY_KIND = "any"
# Front-matter `model` and `effort`: a model or effort level for the task, or "session" (the
# default) to do it in the session itself, at the session's own model and effort.
SESSION = "session"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
MAX_ASK_LENGTH = 300
# `--update <out-id>` hands an outreach reply to the window holding the outreach's parent item.
OUTREACH_PREFIX = "out:"
OUTREACH_KIND = "outreach"
# Tasks (ledger kind task) are handed over once I accepted them; record-delegation moves them on.
TASK_KIND = "task"
TASK_DELEGABLE = ("accepted", "delegated")
STATE_CHANGE = " -> "  # in the ledger's output when recording the delegation moved the item on
TELL_ME = "tell me the reply, then: bosun ledger transition '<out-id>' applied --note 'told the user'"


class DelegateError(Exception):
    pass


Task = dict[str, str]


def split_front_matter(path: Path) -> tuple[Task, str]:
    """The `key: value` lines between the leading `---` markers, and the body after them."""
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise DelegateError(f"{path}: missing front matter")
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields, "\n".join(lines[end + 1 :]).strip()


def parse_task_file(path: Path) -> Task:
    """A task type: its front matter plus `name` and `body`, with model and effort defaulted."""
    fields, body = split_front_matter(path)
    missing = [field for field in FRONT_MATTER_FIELDS if not fields.get(field)]
    if missing or not body:
        raise DelegateError(f"{path}: missing {', '.join(missing) or 'body'}")
    task = {"model": SESSION, "effort": SESSION, **fields, "name": path.stem, "body": body}
    if task["effort"] not in (*EFFORT_LEVELS, SESSION):
        raise DelegateError(f"{path}: effort must be one of {', '.join(EFFORT_LEVELS)} or {SESSION}")
    return task


def task_model_effort(chosen: list[Task], model: str | None, effort: str | None) -> tuple[str, str]:
    """Model and effort for the task: the overrides, else the types' defaults. Combined types run
    in the session as soon as one of them needs it; otherwise the highest effort wins."""
    if effort is None:
        levels = [task["effort"] for task in chosen]
        effort = SESSION if SESSION in levels else max(levels, key=EFFORT_LEVELS.index)
    if model is None:
        models = [task["model"] for task in chosen if task["model"] != SESSION]
        model = models[0] if models and SESSION not in [t["effort"] for t in chosen] else SESSION
    return model, effort


def subagent_preamble(model: str, effort: str) -> str:
    """For a session that is already running: do the task in a subagent with the task's model and
    effort, so the session's own setting (and its long context) stays out of it."""
    settings = [f'effort "{effort}"'] if effort != SESSION else []
    settings += [f'model "{model}"'] if model != SESSION else []
    return (
        f"Run this task in a subagent: make one Agent tool call with {' and '.join(settings)}, passing "
        "everything below the line as its prompt, and wait for it. Then reply here with a one-line "
        "summary of its result. This keeps the task at its own model and effort without changing "
        f"this session's settings. {SUBAGENT_LIMITS}\n{SUBAGENT_WAITS}\n\n---"
    )


# What a subagent cannot do, said up front so it does not fail and retry on it.
SUBAGENT_LIMITS = (
    "Start the subagent without worktree isolation when the task works in an existing branch "
    "worktree or checkout it names (isolation refuses commands that leave its own copy). Tell it "
    "that it cannot call ReadNotifications (main conversation only) and that it returns the "
    "content of any findings or report file as text, for you to write."
)
# How a subagent waits: each time it idles on a background command, Claude Code wakes the parent
# session, which then re-reads its whole conversation; a foreground wait wakes nobody.
SUBAGENT_WAITS = (
    "Tell it to wait for tests, eval runs or CI in the foreground, with one blocking Bash call of at "
    "most 9 minutes at a time (`timeout 540 bash -c 'until <check>; do sleep 60; done'`, the call's "
    "timeout set to 600000 ms), repeated within the task's 2-hour limit, and never to end its turn "
    "while it waits: each time a subagent idles on a background command, Claude Code wakes this "
    "session to say so."
)


def task_files(directories: Iterable[Path | None]) -> dict[str, Path]:
    """Each task type's file, by name: a later directory's file replaces an earlier one's of the same
    name (the site's replaces the repo's). Files starting with `_` (shared rules) are no types.
    The console's Library shows the same resolution as its "effective" badge."""
    files: dict[str, Path] = {}
    for directory in directories:
        if directory and directory.is_dir():
            files.update(
                {path.stem: path for path in sorted(directory.glob("*.md")) if not path.name.startswith("_")}
            )
    return files


def load_task_types() -> dict[str, Task]:
    """The repo's task types, then the site's: a site file with the same name replaces the repo's."""
    return {name: parse_task_file(path) for name, path in task_files((TASKS_DIR, site_tasks_dir())).items()}


def common_rules() -> str:
    """The rules every task gets: the repo's, plus the site's _common.md (added, never replacing)."""
    rules = [COMMON_FILE.read_text().strip()]
    site = site_tasks_dir()
    if site and (site / COMMON_NAME).is_file():
        rules.append((site / COMMON_NAME).read_text().strip())
    return "\n".join(rules)


def list_types(types: dict[str, Task], markdown: bool) -> str:
    if markdown:
        return "\n".join(f"| {t['title']} | {t['may']} | {t['never']} |" for t in types.values())
    blocks = []
    for name, t in types.items():
        blocks.append(
            f"{name}: {t['title']} (ledger kinds: {t['kinds']}; effort: {t['effort']}; model: {t['model']})\n"
            f"  may:      {t['may']}\n"
            f"  never:    {t['never']}\n"
            f"  then you: {t['followup']}"
        )
    return "\n".join(blocks)


def ledger(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run `bosun ledger <arguments>`, capturing its output."""
    return subprocess.run([BOSUN, "ledger", *arguments], check=False, capture_output=True, text=True)


def ledger_item(item_id: str) -> dict[str, Any]:
    """The ledger item <item_id>; fails if it does not exist."""
    shown = ledger("show", item_id)
    if shown.returncode != 0:
        raise DelegateError(f"no ledger item {item_id}; upsert it first")
    item: dict[str, Any] = json.loads(shown.stdout)
    return item


def check_work_item(item: dict[str, Any]) -> None:
    """Outreach is never the work itself, and a task is handed over only once I accepted it."""
    item_id, state = item["id"], item["state"]
    if item["kind"] == OUTREACH_KIND:
        raise DelegateError(
            f"{item_id} is outreach, not a work item. The work its reply asks for is a task: "
            f"bosun ledger spawn '{item_id}' --title '<one line>' --ask '<one line>' --via "
            f"'<reply ref>', then delegate the task. A reply for the window holding the parent: "
            f"bosun delegate --update '{item_id}'"
        )
    if item["kind"] == TASK_KIND and state not in TASK_DELEGABLE:
        raise DelegateError(
            f"{item_id} is a {state} task; only an accepted task is handed over. Propose it to me; on "
            f"my yes: bosun ledger transition '{item_id}' accepted --note '<...>' --evidence "
            "'user: <what I said>'"
        )


def check_ids(ids: list[str], chosen: list[Task]) -> None:
    for item_id in ids:
        item = ledger_item(item_id)
        check_work_item(item)
        kind = str(item["kind"])
        for task in chosen:
            kinds = task["kinds"].split()
            if ANY_KIND not in kinds and kind not in kinds:
                raise DelegateError(f"{task['name']} tasks take {task['kinds']} items; {item_id} is a {kind}")


def active_delegation(item_id: str) -> dict[str, Any] | None:
    """The item's delegation (`bosun ledger delegation`) if it is open and handed over."""
    found = ledger("delegation", item_id)
    delegation: dict[str, Any] | None = json.loads(found.stdout) if found.returncode == 0 else None
    return delegation


def check_not_awaiting(ids: list[str]) -> None:
    """Refuse to hand an item over again while its last hand-over awaits a report (--again
    overrides). After a report, or once the item reached a state that shows the work done (a
    re-review, the next fix round after a push), a new hand-over is fine."""
    for item_id in ids:
        delegation = active_delegation(item_id)
        if delegation and delegation.get("awaiting_report"):
            raise DelegateError(
                f"{item_id} was handed to window {delegation['window']} at {delegation['at']} "
                f"({delegation['task']}) and has not reported since. To add to or "
                f"correct that task: bosun delegate --update '{item_id}' --details - <<'EOF' "
                "(add --window <window> to move it to another window). To hand it over again anyway, "
                "which needs my approval unless I asked for it: add --again."
            )


def render(args: argparse.Namespace, chosen: list[Task], preamble: str = "", lead: str = "") -> str:
    """The task: the subagent preamble, a lead paragraph (for a moved task), the header (ledger
    ids, link, requester, ask, details), each type's rules, then the shared rules."""
    header = [f"Ledger id{'s' if len(args.id) > 1 else ''}: {', '.join(args.id)}"]
    if args.link:
        header.append(f"Link: {args.link}")
    if args.requester:
        header.append(f"Requester: {args.requester}")
    header.append(f"Ask: {args.ask}")
    if args.details:
        header.append(f"Details: {args.details}")
    parts = (
        ([preamble] if preamble else [])
        + ([lead] if lead else [])
        + ["\n".join(header)]
        + [task["body"] for task in chosen]
        + [common_rules()]
    )
    return "\n\n".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bosun delegate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("types", nargs="?", help="task type(s), comma-separated")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--update", metavar="LEDGER_ID", help="send an update to the item's window")
    parser.add_argument(
        "--again", action="store_true", help="hand over an item whose last hand-over awaits a report"
    )
    parser.add_argument("--ask")
    parser.add_argument("--link")
    parser.add_argument("--requester")
    parser.add_argument("--details")
    parser.add_argument("--model", help="override the task type's model (only when the user asks)")
    parser.add_argument(
        "--effort", choices=EFFORT_LEVELS, help="override the task type's effort (only when the user asks)"
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--print", action="store_true")
    target.add_argument("--window")
    target.add_argument("--new", nargs=2, metavar=("WINDOW", "DIR"))
    target.add_argument("--start")
    return parser


def chosen_types(names_arg: str | None, types: dict[str, Task]) -> list[Task]:
    """The task types named in "<type>[,<type>...]", in order."""
    if not names_arg:
        raise DelegateError("give a task type; see bosun delegate --list")
    names = [name.strip() for name in names_arg.split(",") if name.strip()]
    unknown = [name for name in names if name not in types]
    if unknown:
        raise DelegateError(f"unknown task type {', '.join(unknown)}; types: {', '.join(types)}")
    return [types[name] for name in names]


def check_request(args: argparse.Namespace) -> None:
    if not args.id:
        raise DelegateError("give at least one --id")
    if not args.ask or "\n" in args.ask or len(args.ask) > MAX_ASK_LENGTH:
        raise DelegateError(f"--ask must be one line of at most {MAX_ASK_LENGTH} characters")
    if not (args.print or args.window or args.new or args.start):
        raise DelegateError("say where: --print, --window, --new or --start")


def send(*arguments: str) -> int:
    return subprocess.run([BOSUN, "send", *arguments], env=os.environ, check=False).returncode


def window_harness(window: str) -> str:
    """Which agent runs <window>: the one running there, else the one configured for it."""
    result = subprocess.run([BOSUN, "sessions", "--json"], check=False, capture_output=True, text=True)
    try:
        windows = json.loads(result.stdout)
    except ValueError:
        return SUBAGENT_HARNESS
    entry: dict[str, Any] = next((w for w in windows if w.get("name") == window), {})
    return str((entry.get("agent") or {}).get("harness") or entry.get("harness") or SUBAGENT_HARNESS)


def can_use_subagents(args: argparse.Namespace) -> bool:
    """The preamble names Claude Code's Agent tool, so another agent runs the task at its own settings.
    --print shows what a window on Claude Code would get."""
    target = args.window or args.start
    return not target or window_harness(target) == SUBAGENT_HARNESS


def hand_over(args: argparse.Namespace, chosen: list[Task]) -> int:
    """Print the task or send it to its target, at the task's model and effort."""
    model, effort = task_model_effort(chosen, args.model, args.effort)
    if args.new:
        # A window opened for this task starts at the task's model and effort.
        launch = [
            *(["--model", model] if model != SESSION else []),
            *(["--effort", effort] if effort != SESSION else []),
        ]
        return send("--new", *launch, args.new[0], args.new[1], render(args, chosen))
    # A running (or resumed) session keeps its own settings; the task runs in a subagent, where it can.
    wants_own_settings = (model, effort) != (SESSION, SESSION)
    preamble = subagent_preamble(model, effort) if wants_own_settings and can_use_subagents(args) else ""
    task = render(args, chosen, preamble)
    if args.print:
        print(task)
        return 0
    return send(args.window, task) if args.window else send("--start", args.start, task)


def target_window(args: argparse.Namespace) -> str | None:
    """The window the task went to (None for --print)."""
    return str(args.window or args.start or (args.new[0] if args.new else "")) or None


def record_delegation(args: argparse.Namespace) -> None:
    """Record on each item which window now works on it. The task is already delivered, so a
    failure here is reported but does not fail the delegation (a retry would deliver it twice)."""
    window = target_window(args)
    if window is None:
        return
    # The header is kept so that a move (--update --window) can send the whole task again.
    header = [
        argument
        for name in ("link", "requester", "details")
        if getattr(args, name)
        for argument in (f"--{name}", getattr(args, name))
    ]
    for item_id in args.id:
        recorded = ledger(
            "record-delegation", item_id, "--window", window, "--task", args.types, "--ask", args.ask, *header
        )
        if recorded.returncode != 0:
            print(
                f"bosun delegate: delivered, but the ledger did not record the window for {item_id}: "
                f"{recorded.stderr.strip()}",
                file=sys.stderr,
            )
        elif STATE_CHANGE in recorded.stdout:
            # e.g. a task: accepted -> delegated
            print(recorded.stdout.strip())


# How the prompts this module renders open (a hand-over, an update, a moved task, the subagent
# preamble before any of them).
HANDOVER_OPENINGS = ("Ledger id", "Ledger item", "Update to ledger item", "Run this task in a subagent")
# The ledger ids a hand-over names, from the formats below (`render`'s header, `render_update`,
# the lead of a moved task); lib/impact_attribution.py reads them back from transcripts.
HANDOVER_IDS = (
    re.compile(r"^Ledger ids?: (?P<ids>.+)$", re.MULTILINE),
    re.compile(r"^Update to ledger item (?P<ids>\S+) \(the "),
    re.compile(r"^Ledger item (?P<ids>\S+) moves to this window", re.MULTILINE),
)
IDS_SEPARATOR = ", "


def handover_ids(text: str) -> list[str]:
    """The ledger ids a hand-over prompt names, in order; [] for any other prompt."""
    if not text.startswith(HANDOVER_OPENINGS):
        return []
    for pattern in HANDOVER_IDS:
        match = pattern.search(text)
        if match:
            return [i for i in match.group("ids").strip().split(IDS_SEPARATOR) if i]
    return []


def render_update(item_id: str, delegation: dict[str, str], details: str) -> str:
    return (
        f"Update to ledger item {item_id} (the {delegation['task']} task handed to this window at "
        f"{delegation['at']}):\n\n{details}\n\n"
        f"This adds to your task for {item_id}; the rest of that task still applies. If a subagent "
        f"is working on {item_id}, pass this update on to it. Report the outcome for {item_id} "
        "with `bosun ledger report` as the task says."
    )


def check_update_request(args: argparse.Namespace) -> None:
    others = [args.types, args.id, args.ask, args.link, args.requester, args.model, args.effort, args.again]
    if any(others) or args.new or args.start:
        raise DelegateError(
            "--update takes only --details, --window (to move the item there) and --print; "
            "the item's window comes from the ledger"
        )
    if not args.details and not args.update.startswith(OUTREACH_PREFIX):
        raise DelegateError("--update needs the update text: --details '<text>' or --details - <<'EOF'")


def render_moved_task(item_id: str, delegation: dict[str, Any], details: str) -> str:
    """The whole task again, from what the ledger kept of the hand-over, led by the move and the
    update, for a window that has never seen it."""
    chosen = chosen_types(delegation["task"], load_task_types())
    header = argparse.Namespace(
        id=[item_id],
        link=delegation.get("link"),
        requester=delegation.get("requester"),
        ask=delegation.get("ask") or f"(not recorded; see bosun ledger show {item_id})",
        details=delegation.get("details"),
    )
    model, effort = task_model_effort(chosen, None, None)
    preamble = subagent_preamble(model, effort) if (model, effort) != (SESSION, SESSION) else ""
    lead = (
        f"Ledger item {item_id} moves to this window from window {delegation['window']}, where its "
        f"task was handed over at {delegation['at']}; that window is told to stop. First check "
        f"what was done there (bosun ledger show {item_id}: history, reports, notes; any "
        f"branch or PR) and continue from it.\n\nUpdate: {details}"
    )
    return render(header, chosen, preamble, lead)


def stop_notice(item_id: str, window: str) -> str:
    return (
        f"Ledger item {item_id} has moved to window {window}. Stop working on it here. If you made "
        f"progress, record where it stands: bosun ledger note {item_id} '<what is done; branch "
        "or PR>'. Do not report on it; the other window will."
    )


def move(args: argparse.Namespace, delegation: dict[str, Any], details: str) -> int:
    """Send the whole task to another window, then tell the old one to stop (best effort: a
    window is often moved away from because it is gone or stuck), and record the move."""
    item_id, old, new = args.update, delegation["window"], args.window
    if new == old:
        raise DelegateError(f"{item_id} is already delegated to {old}; drop --window to update it there")
    text = render_moved_task(item_id, delegation, details)
    if send(new, text) != 0:
        raise DelegateError(f"the task was not delivered to {new} (see above); {item_id} stays with {old}")
    told = send("--id", item_id, old, stop_notice(item_id, new)) == 0
    recorded = ledger(
        "record-move", item_id, "--window", new, "--text", details, *(["--notified"] if told else [])
    )
    print(recorded.stdout.strip() or recorded.stderr.strip())
    if not told:
        print(
            f"bosun delegate: window {old} was not told to stop (see above); check that it has, "
            f"or tell it: bosun send --id '{item_id}' {old} -",
            file=sys.stderr,
        )
    return 0


def send_update(args: argparse.Namespace) -> int:
    """Deliver an update to the window the item was delegated to (or move the item to --window),
    and record it."""
    check_update_request(args)
    if args.update.startswith(OUTREACH_PREFIX):
        return send_reply(args)
    found = ledger("delegation", args.update)
    if found.returncode != 0:
        raise DelegateError(found.stderr.strip().removeprefix("ledger: ") or f"no ledger item {args.update}")
    delegation = json.loads(found.stdout)
    details = read_details(args.details)
    if args.window:
        return move(args, delegation, details)
    text = render_update(args.update, delegation, details)
    if args.print:
        print(text)
        return 0
    if send(delegation["window"], text) != 0:
        raise DelegateError(
            f"the update was not delivered (see above). {args.update} was handed to window "
            f"{delegation['window']} at {delegation['at']}; if that window is gone or runs another "
            "session, hand the task over again with bosun delegate <type> --id ..."
        )
    recorded = ledger("record-update", args.update, "--text", details)
    print(recorded.stdout.strip() or recorded.stderr.strip())
    return 0


def replied_outreach(outreach_id: str) -> dict[str, Any]:
    """The outreach item, which must have a reply and a parent work item."""
    shown = ledger("show", outreach_id)
    if shown.returncode != 0:
        raise DelegateError(f"no ledger item {outreach_id}")
    item: dict[str, Any] = json.loads(shown.stdout)
    if item["state"] != "replied":
        raise DelegateError(
            f"{outreach_id} is {item['state']}; only outreach with a reply is handed over "
            f"(record it first: bosun ledger replied '{outreach_id}' --evidence ... --summary ...)"
        )
    if not item["fields"].get("parent"):
        raise DelegateError(f"{outreach_id} belongs to no work item, so no window holds it; {TELL_ME}")
    return item


def render_reply(item: dict[str, Any]) -> str:
    """The reply, from the ledger, for the window working on the parent."""
    fields = item["fields"]
    options = f"\nOptions were: {' | '.join(fields['options'])}" if fields.get("options") else ""
    return (
        f"Reply to {item['id']} ({fields['purpose']} by {fields['channel']} to {fields['to']}: "
        f"{fields['subject']}){options}\n"
        f"Reply: {fields['answer']} (reference: {fields['reply_ref']})\n"
        f"Continue the work on {fields['parent']} that waited on it."
    )


def send_reply(args: argparse.Namespace) -> int:
    """Hand an outreach reply to the window that holds its parent item, then mark it applied."""
    if args.window:
        raise DelegateError(
            "a reply goes to the window holding its parent; to move the parent, use --update <parent-id>"
        )
    item = replied_outreach(args.update)
    parent = item["fields"]["parent"]
    found = ledger("delegation", parent)
    if found.returncode != 0:
        reason = found.stderr.strip().removeprefix("ledger: ")
        raise DelegateError(f"no window holds {parent} ({reason}); {TELL_ME}")
    delegation = json.loads(found.stdout)
    text = render_reply(item) + (f"\n\n{read_details(args.details)}" if args.details else "")
    message = render_update(parent, delegation, text)
    if args.print:
        print(message)
        return 0
    if send(delegation["window"], message) != 0:
        raise DelegateError(
            f"the reply was not delivered to {delegation['window']} (see above); {args.update} stays replied"
        )
    note = f"reply handed to window {delegation['window']} (update to {parent})"
    for done in (
        ledger("record-update", parent, "--text", text),
        ledger("transition", args.update, "applied", "--note", note),
    ):
        print(done.stdout.strip() or done.stderr.strip())
    return 0


def read_details(details: str) -> str:
    """--details as given, or from stdin for "-" (call it once: stdin is read to the end)."""
    if details != "-":
        return details
    text = sys.stdin.read().strip()
    if not text:
        raise DelegateError("--details - read nothing from stdin; pass the text in a quoted heredoc")
    return text


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    types = load_task_types()
    if args.list:
        print(list_types(types, args.markdown))
        return 0
    if args.update:
        return send_update(args)
    chosen = chosen_types(args.types, types)
    check_request(args)
    if args.details:
        args.details = read_details(args.details)
    check_ids(args.id, chosen)
    if target_window(args) and not args.again:
        check_not_awaiting(args.id)
    code = hand_over(args, chosen)
    if code == 0:
        record_delegation(args)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except DelegateError as error:
        print(f"bosun delegate: {error}", file=sys.stderr)
        sys.exit(1)
