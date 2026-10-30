"""The console's Sessions view and health panel: one card per window, the windows waiting at a
prompt (for "needs you"), and bosun's own health. Design: docs/design/console.md (section 2).

Pure over the facts lib/console_live.py collects and a loaded ledger; the time is passed in.
- A card: the window as `bosun sessions --json` lists it (kind, folder, model and effort, its
  Claude's status and for how long), the items it holds, its active item and whether it works on
  it now (lib/activity.py), its last report, its login verdict, and the tmux attach command.
- Health: the watchdog's checks (lib/watchdog.py's status_data), the morning run and the chores
  runs the ledger records, the events dispatcher (deliveries today against its daily limit, events
  pending), the Slack watcher (last run, spend today against its cap, failures), the login (the
  last login check, the windows that need one) and the feature flags, as booleans only.
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import activity
import claude_state
import console_ledger
import login
from console_data import Item, iso_time
from console_live import Facts, Settings


WAITING = "waiting"  # Claude Code's status while a permission prompt or question waits for me
NO_WINDOW = "no window"
NO_CLAUDE = "no claude"
STATUSES = ("idle", "busy", "shell", WAITING)
MS_PER_SECOND = 1000
LEFT_OUT = (
    (
        "Whether Claude Code is logged in right now: claude auth status starts Claude, and the login "
        "check request costs money. The last login check and each window's latest replies are shown."
    ),
    "A window's visible text: reading it means capturing the pane.",
)


# ---- sessions ----------------------------------------------------------------------------------


def attach_command(settings: Settings, window: str = "") -> str:
    """`tmux attach` for the session (and, given one, a window of it), as `bosun attach` runs it."""
    socket = ["-L", settings.tmux_socket] if settings.tmux_socket else []
    target = f"{settings.session}:{window}" if window else settings.session
    return shlex.join(["tmux", *socket, "attach", "-t", target])


def epoch_seconds(epoch_ms: Any) -> float | None:
    """Claude Code's statusUpdatedAt (epoch milliseconds) in seconds; None if it is not a number."""
    if not isinstance(epoch_ms, int | float) or isinstance(epoch_ms, bool):
        return None
    return epoch_ms / MS_PER_SECOND


def claude_model(claude: Mapping[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """The Claude in a window: status, how long in it (and since when), version, conversation name."""
    if not claude:
        return None
    updated = epoch_seconds(claude.get("statusUpdatedAt"))
    return {
        "pid": claude.get("pid"),
        "status": str(claude.get("status") or "unknown"),
        "seconds": None if updated is None else max(0, int(now.timestamp() - updated)),
        "since": iso_time(updated),
        "version": claude.get("version") or "",
        "name": claude.get("name") or "",
    }


def state_of(window: Mapping[str, Any]) -> str:
    """What a card's badge says: the Claude's status, else no claude (a window with a shell) or no
    window (configured, not running)."""
    if not window.get("window"):
        return NO_WINDOW
    claude = window.get("claude") or {}
    return str(claude.get("status") or "unknown") if claude else NO_CLAUDE


def item_summary(item: Item, working: bool = False) -> dict[str, Any]:
    return {
        **{key: item.get(key, "") for key in ("id", "kind", "state", "title")},
        "working": working,
    }


def report_window(item: Item) -> str:
    report = item.get("report") or {}
    return str(report.get("window") or (item.get("delegation") or {}).get("window") or "")


def last_reports(items: Iterable[Item]) -> dict[str, dict[str, Any]]:
    """Each window's latest report (on any item, open or closed): when, on what, and its summary."""
    latest: dict[str, dict[str, Any]] = {}
    for item in items:
        report = item.get("report") or {}
        window, at = report_window(item), str(report.get("at") or "")
        if window and at and at > str(latest.get(window, {}).get("at", "")):
            latest[window] = {
                "at": at,
                "id": item["id"],
                "title": item.get("title", ""),
                "summary": report.get("summary", ""),
            }
    return latest


def held_model(items: Iterable[Item], name: str, status: str | None) -> dict[str, Any]:
    """The open items delegated to window <name>, its active item, and whether it works on it now."""
    held = activity.held(items, name, console_ledger.item_open)
    active = activity.active_item(held)
    working = activity.is_working(active, held, lambda _window: status) if active else False
    return {
        "held": [item_summary(i, working and i is active) for i in held],
        "active": active["id"] if active else None,
        "working": working,
    }


def login_model(verdict: login.Verdict | None) -> dict[str, Any] | None:
    if verdict is None:
        return None
    return {"state": verdict.state, "detail": verdict.detail, "since": iso_or_none(verdict.since)}


def iso_or_none(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if moment else None


def window_card(
    window: Mapping[str, Any], facts: Facts, items: list[Item], reports: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    name = str(window.get("name", ""))
    claude = claude_model(window.get("claude"), now)
    return {
        "name": name,
        "kind": window.get("kind", ""),
        "dir": window.get("dir", ""),
        "model": "" if window.get("model") in (None, "-") else window.get("model"),
        "effort": "" if window.get("effort") in (None, "-") else window.get("effort"),
        "window": bool(window.get("window")),
        "state": state_of(window),
        "claude": claude,
        **held_model(items, name, claude["status"] if claude else None),
        "last_report": reports.get(name),
        "login": login_model(facts.window_logins.get(name)),
        "attach": attach_command(facts.settings, name),
    }


def sessions_model(facts: Facts, data: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """/api/sessions: a card per window, how many windows are in each state, the attach command."""
    items = list((data.get("items") or {}).values())
    reports = last_reports(items)
    cards = [window_card(w, facts, items, reports, now) for w in facts.windows or []]
    counts: dict[str, int] = {}
    for card in cards:
        counts[card["state"]] = counts.get(card["state"], 0) + 1
    return {
        "collected_at": iso_or_none(facts.at),
        "session": facts.settings.session,
        "attach": attach_command(facts.settings),
        "error": facts.errors.get("sessions") if facts.windows is None else None,
        "counts": counts,
        "windows": cards,
    }


def waiting_windows(facts: Facts, now: datetime) -> list[dict[str, Any]] | None:
    """The windows whose Claude waits at a permission prompt or question, longest first; None when
    the windows could not be read."""
    if facts.windows is None:
        return None
    found = []
    for window in facts.windows:
        claude = claude_model(window.get("claude"), now)
        if window.get("window") and claude and claude["status"] == WAITING:
            found.append({"name": window["name"], "since": claude["since"], "seconds": claude["seconds"]})
    return sorted(found, key=lambda w: -(w["seconds"] or 0))


# ---- health ------------------------------------------------------------------------------------


def watchdog_model(facts: Facts) -> dict[str, Any]:
    data = facts.checks
    if data is None:
        return {"error": facts.errors.get("watchdog", "not collected"), "checks": [], "problems": 0}
    checks = [{**c, "ok": not c["findings"]} for c in data["checks"]]
    return {**data, "error": None, "checks": checks, "problems": sum(len(c["findings"]) for c in checks)}


def morning_model(facts: Facts) -> dict[str, Any] | None:
    run = facts.morning
    if run is None:
        return None
    return {
        "started": run.started.isoformat(sep=" "),
        "trigger": run.trigger,
        "outcome": run.outcome,
        "problems": run.problems,
    }


def chores_model(data: Mapping[str, Any]) -> list[dict[str, str]]:
    """The chores runs the ledger records (`ledger mark-run`): the last morning, check-in, ..."""
    runs = data.get("runs") or {}
    return [
        {"name": str(name), "at": str(at)}
        for name, at in sorted(runs.items(), key=lambda r: str(r[1]), reverse=True)
    ]


def events_model(facts: Facts, today: date) -> dict[str, Any]:
    settings, state = facts.settings, facts.events
    return {
        "enabled": settings.events_enabled,
        "error": facts.errors.get("events"),
        "deliveries_today": state.deliveries_on(today) if state else 0,
        "max_per_day": settings.events_max_per_day,
        "pending": facts.events_pending,
        "last_tick": state.last_tick if state else None,
        "last_error": state.last_error if state else None,
    }


def slack_last_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not runs:
        return None
    last = runs[-1]
    keys = ("started", "ended", "outcome", "found", "queued", "cost", "error")
    return {key: last.get(key) for key in keys}


def slack_model(facts: Facts, today: date) -> dict[str, Any]:
    settings, state = facts.settings, facts.slack
    return {
        "enabled": settings.slack_watch_enabled,
        "active": settings.slack_watch_enabled and settings.events_enabled,  # it needs events on
        "error": facts.errors.get("slack_watch"),
        "last_started": state.last_started if state else None,
        "last_run": slack_last_run(state.runs) if state else None,
        "failures": state.failures if state else 0,
        "failing_since": state.failing_since if state else None,
        "last_error": state.last_error if state else None,
        "spend_today": round(state.spent_on(today), 4) if state else 0.0,
        "cap": settings.slack_max_usd_per_day,
        "budget_spent_today": bool(state and state.budget_spent_on == today.isoformat()),
    }


def overall_login(probe: login.Probe | None, needing: list[str]) -> tuple[str, str]:
    """The login as far as it can be told without starting Claude: the last login check, and the
    windows whose latest replies are login errors."""
    failure = login.login_failure(claude_state.AuthStatus(None), probe)
    if failure:
        return failure.state, failure.detail
    if needing:
        return login.NEEDS_LOGIN, f"every recent reply in {', '.join(needing)} is a login error"
    if probe and probe.outcome == claude_state.PROBE_OK:
        return login.OK, f"the last login check, at {login.shown(probe.at)}, worked"
    return login.UNKNOWN, "no login check made yet; no window answers only with login errors"


def login_health(facts: Facts) -> dict[str, Any]:
    probe = facts.login_state.last_probe
    windows = [{"name": name, **(login_model(v) or {})} for name, v in sorted(facts.window_logins.items())]
    state, detail = overall_login(probe, [w["name"] for w in windows if w["state"] == login.NEEDS_LOGIN])
    return {
        "state": state,
        "detail": detail,
        "last_check": {"at": iso_or_none(probe.at), "outcome": probe.outcome} if probe else None,
        "gated": [{"window": w, "since": since} for w, since in sorted(facts.login_state.gated.items())],
        "windows": windows,
    }


def flags_model(settings: Settings) -> dict[str, bool]:
    """The feature flags, on or off; never the values of other settings."""
    return {
        "events": settings.events_enabled,
        "slack_watch": settings.slack_watch_enabled,
        "learn": settings.learn_enabled,
    }


def health_model(facts: Facts, data: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """/api/health: the watchdog's checks, the morning and chores runs, events, Slack watcher,
    login and flags, and what the console leaves out (only a side effect could tell it)."""
    today = now.astimezone().date()
    return {
        "collected_at": iso_or_none(facts.at),
        "flags": flags_model(facts.settings),
        "watchdog": watchdog_model(facts),
        "morning": morning_model(facts),
        "chores": chores_model(data),
        "events": events_model(facts, today),
        "slack_watch": slack_model(facts, today),
        "login": login_health(facts),
        "left_out": list(LEFT_OUT),
    }
