"""The Impact view's metrics (/api/impact): what bosun changed, from the snapshot
`bosun impact collect` writes (lib/impact_collect.py). Design, with every definition:
docs/design/impact.md.

Pure over a loaded snapshot; the time and the time zone are passed in. Weeks are ISO weeks
(Monday to Sunday) in local time. Baseline weeks are the full weeks from the baseline's first
Monday up to the start's week; since weeks run from the start's week on, the current week marked
partial and left out of the means.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any


SNAPSHOT_VERSION = 1
EARLY_WEEKS = 4  # fewer full weeks since the start than this: the page says "early data"
STALE_DAYS = 2  # a snapshot older than this: the page says when it was collected
DEFAULT_BASELINE_WEEKS = 13
DEFAULT_PROMPT_MINUTES = 2.0
LEVERAGE_KEYS = ("estimate", "low", "high")  # a week's leverage, without its parts
TOUCH_LOW, TOUCH_HIGH = 0.5, 2.0  # my touch time's range: half to double the assumed minutes
SECONDS_PER_HOUR = 3600
DAYS_PER_WEEK = 7
PER_MILLION = 1_000_000
# List prices in USD per million tokens (input, output, cache read), matched on the model id, most
# specific first: an estimate, not a bill (first-party API rates as of 2026-10; a subscription or
# a partner platform bills differently). Cache writes cost CACHE_WRITE x input. Haiku 5.5 is the
# rate for prompts up to 100K tokens. An unknown model is priced like FALLBACK_MODEL and counted
# in `unpriced_models`.
PRICES = (
    ("fable", (10.0, 50.0, 0.25)),
    ("mythos", (10.0, 50.0, 0.25)),
    ("opus-5-5", (4.0, 20.0, 0.20)),
    ("opus", (5.0, 25.0, 0.50)),
    ("sonnet-5", (2.0, 10.0, 0.20)),
    ("sonnet", (3.0, 15.0, 0.30)),
    ("haiku-5-5", (0.10, 0.50, 0.01)),
    ("haiku", (1.0, 5.0, 0.10)),
)
FALLBACK_MODEL = "opus"
CACHE_WRITE = 1.25
TOKEN_KINDS = ("input", "output", "cache_write", "cache_read")
BASELINE, SINCE = "baseline", "since"
QUARTER, THREE_QUARTERS = 0.25, 0.75


# ---- small helpers -----------------------------------------------------------------------------


def parse_time(value: Any) -> datetime | None:
    """An ISO time (a trailing Z or an offset; none counts as UTC), else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def parse_day(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def median(values: Iterable[float]) -> float | None:
    found = list(values)
    return statistics.median(found) if found else None


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear interpolation between the closest ranks; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def hours(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() / SECONDS_PER_HOUR


def rounded(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


# ---- cost --------------------------------------------------------------------------------------


def family(model: str) -> str | None:
    """The PRICES entry a model id matches, if any."""
    return next((name for name, _ in PRICES if name in model.lower()), None)


def usage_cost(model: str, usage: Mapping[str, Any]) -> float:
    """USD at list prices for one model's token counts (TOKEN_KINDS)."""
    price_in, price_out, price_cache_read = dict(PRICES)[family(model) or FALLBACK_MODEL]
    tokens = {kind: float(usage.get(kind) or 0) for kind in TOKEN_KINDS}
    total = (
        tokens["input"] * price_in
        + tokens["output"] * price_out
        + tokens["cache_write"] * price_in * CACHE_WRITE
        + tokens["cache_read"] * price_cache_read
    )
    return total / PER_MILLION


def row_cost(row: Mapping[str, Any]) -> float:
    return sum(usage_cost(model, usage) for model, usage in (row.get("usage") or {}).items())


def merge_usage(total: dict[str, dict[str, int]], usage: Mapping[str, Mapping[str, Any]]) -> None:
    """Add tokens by model (TOKEN_KINDS each) into <total>."""
    for model, counts in usage.items():
        mine = total.setdefault(model, dict.fromkeys(TOKEN_KINDS, 0))
        for kind in TOKEN_KINDS:
            mine[kind] += int(counts.get(kind) or 0)


def unpriced_models(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Models that used tokens but are not in PRICES."""
    return sorted(
        {
            model
            for row in rows
            for model, usage in (row.get("usage") or {}).items()
            if family(model) is None and any(usage.get(kind) for kind in TOKEN_KINDS)
        }
    )


# ---- the weeks ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """The weeks shown: baseline weeks, then since weeks; `today` marks the partial week."""

    baseline_from: date
    start: date
    today: date

    @classmethod
    def of(cls, snapshot: Mapping[str, Any], now: datetime, zone: tzinfo) -> Frame:
        today = now.astimezone(zone).date()
        start = parse_day(snapshot.get("start")) or today
        first = parse_day(snapshot.get("baseline_from")) or start - timedelta(weeks=DEFAULT_BASELINE_WEEKS)
        return cls(first, start, today)

    def weeks(self) -> list[date]:
        first = monday(self.baseline_from)
        if first < self.baseline_from:
            first += timedelta(days=DAYS_PER_WEEK)  # a partial first week is left out
        found, week = [], first
        while week <= monday(self.today):
            found.append(week)
            week += timedelta(days=DAYS_PER_WEEK)
        return found

    def phase(self, week: date) -> str:
        return SINCE if week >= monday(self.start) else BASELINE

    def partial(self, week: date) -> bool:
        return week == monday(self.today)

    def week_of(self, moment: datetime | None, zone: tzinfo) -> date | None:
        return None if moment is None else monday(moment.astimezone(zone).date())


# ---- PRs, reviews, activity --------------------------------------------------------------------


@dataclass
class Week:
    start: date
    phase: str
    partial: bool
    prs: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    busy_seconds: float = 0.0
    prompts: int = 0
    cost: float = 0.0


def pr_merged_week(pr: Mapping[str, Any], frame: Frame, zone: tzinfo) -> date | None:
    return frame.week_of(parse_time(pr.get("merged")), zone)


def review_days(pr: Mapping[str, Any], zone: tzinfo) -> list[datetime]:
    """My reviews of one PR, one per local day (a burst of comments is one review), oldest first."""
    seen: dict[date, datetime] = {}
    for value in pr.get("reviews") or []:
        moment = parse_time(value)
        if moment is not None:
            day = moment.astimezone(zone).date()
            seen[day] = min(seen.get(day, moment), moment)
    return sorted(seen.values())


def add_prs(weeks: dict[date, Week], snapshot: Mapping[str, Any], frame: Frame, zone: tzinfo) -> None:
    for pr in snapshot.get("prs") or []:
        week = pr_merged_week(pr, frame, zone)
        if week in weeks:
            weeks[week].prs.append(pr)


def add_reviews(weeks: dict[date, Week], snapshot: Mapping[str, Any], frame: Frame, zone: tzinfo) -> None:
    for pr in snapshot.get("reviews") or []:
        for moment in review_days(pr, zone):
            week = frame.week_of(moment, zone)
            if week in weeks:
                weeks[week].reviews.append({**pr, "at": moment.isoformat()})


def add_activity(weeks: dict[date, Week], snapshot: Mapping[str, Any]) -> None:
    for row in snapshot.get("activity") or []:
        day = parse_day(row.get("day"))
        week = monday(day) if day else None
        if week in weeks:
            weeks[week].busy_seconds += float(row.get("busy_seconds") or 0)
            weeks[week].prompts += int(row.get("prompts") or 0)
            weeks[week].cost += row_cost(row)


def fill_weeks(snapshot: Mapping[str, Any], frame: Frame, zone: tzinfo) -> list[Week]:
    weeks = {w: Week(w, frame.phase(w), frame.partial(w), [], []) for w in frame.weeks()}
    add_prs(weeks, snapshot, frame, zone)
    add_reviews(weeks, snapshot, frame, zone)
    add_activity(weeks, snapshot)
    return [weeks[w] for w in sorted(weeks)]


def full(weeks: list[Week], phase: str) -> list[Week]:
    return [w for w in weeks if w.phase == phase and not w.partial]


# ---- the comparison tiles ----------------------------------------------------------------------


def compare(weeks: list[Week], measure: Any) -> dict[str, Any]:
    """A weekly measure's baseline (mean, middle half, weeks) against the since weeks (mean,
    weeks; the partial week only when there is no full one yet)."""
    before = [float(measure(w)) for w in full(weeks, BASELINE)]
    since_weeks = full(weeks, SINCE) or [w for w in weeks if w.phase == SINCE]
    after = [float(measure(w)) for w in since_weeks]
    base, now = mean(before), mean(after)
    change = (now / base - 1) if base and now is not None else None
    return {
        "baseline": {
            "mean": rounded(base),
            "p25": rounded(percentile(before, QUARTER)),
            "p75": rounded(percentile(before, THREE_QUARTERS)),
            "weeks": len(before),
        },
        "since": {"mean": rounded(now), "weeks": len(after), "partial_only": not full(weeks, SINCE)},
        "change": rounded(change, 3),
    }


def merge_hours(weeks: list[Week], phase: str) -> dict[str, Any]:
    values = [
        h
        for w in weeks
        if w.phase == phase
        for pr in w.prs
        if (h := hours(parse_time(pr.get("created")), parse_time(pr.get("merged")))) is not None
    ]
    return {"median": rounded(median(values)), "n": len(values)}


def first_review_hours(snapshot: Mapping[str, Any], frame: Frame, zone: tzinfo, phase: str) -> dict[str, Any]:
    values = []
    for pr in snapshot.get("reviews") or []:
        days = review_days(pr, zone)
        week = frame.week_of(days[0], zone) if days else None
        if week is None or week < monday(frame.baseline_from) or frame.phase(week) != phase:
            continue
        value = hours(parse_time(pr.get("pr_created")), days[0])
        if value is not None:
            values.append(value)
    return {"median": rounded(median(values)), "n": len(values)}


def touch_hours(prompts: int, minutes: float) -> dict[str, float]:
    base = prompts * minutes / 60
    return {
        "estimate": round(base, 1),
        "low": round(base * TOUCH_LOW, 1),
        "high": round(base * TOUCH_HIGH, 1),
    }


def leverage(busy_seconds: float, prompts: int, minutes: float) -> dict[str, Any]:
    """Agent busy hours per hour of my touch time (an estimate), with the range the touch-time
    range gives; None without prompts."""
    busy = busy_seconds / SECONDS_PER_HOUR
    touch = touch_hours(prompts, minutes)
    if not touch["estimate"]:
        return {"estimate": None, "low": None, "high": None, "agent_hours": round(busy, 1), "touch": touch}
    return {
        "estimate": round(busy / touch["estimate"], 1),
        "low": round(busy / touch["high"], 1),
        "high": round(busy / touch["low"], 1),
        "agent_hours": round(busy, 1),
        "touch": touch,
    }


def since_totals(weeks: list[Week], minutes: float) -> dict[str, Any]:
    since = [w for w in weeks if w.phase == SINCE]
    busy = sum(w.busy_seconds for w in since)
    prompts = sum(w.prompts for w in since)
    cost = sum(w.cost for w in since)
    merged = sum(len(w.prs) for w in since)
    return {
        "leverage": leverage(busy, prompts, minutes),
        "prompts": prompts,
        "cost": round(cost, 2),
        "cost_per_week": rounded(cost / len(since), 2) if since else None,
        "merged": merged,
        "cost_per_pr": rounded(cost / merged, 2) if merged else None,
    }


def phase_leverage(weeks: list[Week], phase: str, minutes: float) -> dict[str, Any]:
    """Leverage over the phase's full weeks that have any prompts (weeks with no transcripts say
    nothing about it), and how many of its full weeks those are."""
    whole = full(weeks, phase)
    covered = [w for w in whole if w.prompts]
    totals = leverage(sum(w.busy_seconds for w in covered), sum(w.prompts for w in covered), minutes)
    return {
        **totals,
        "agent_per_week": rounded(totals["agent_hours"] / len(covered)) if covered else None,
        "touch_per_week": rounded(totals["touch"]["estimate"] / len(covered)) if covered else None,
        "weeks": len(covered),
        "of_weeks": len(whole),
        "first_week": covered[0].start.isoformat() if covered else None,
    }


def leverage_compare(weeks: list[Week], minutes: float) -> dict[str, Any]:
    """Leverage before the start and since, each over its full weeks with transcripts."""
    return {phase: phase_leverage(weeks, phase, minutes) for phase in (BASELINE, SINCE)}


def by_window(snapshot: Mapping[str, Any], frame: Frame) -> list[dict[str, Any]]:
    """Agent busy hours, my prompts and cost per window since the start, busiest first."""
    totals: dict[str, dict[str, float]] = {}
    for row in snapshot.get("activity") or []:
        day = parse_day(row.get("day"))
        if day is None or day < monday(frame.start):
            continue
        entry = totals.setdefault(
            str(row.get("window") or "unnamed"), {"busy": 0.0, "prompts": 0.0, "cost": 0.0}
        )
        entry["busy"] += float(row.get("busy_seconds") or 0)
        entry["prompts"] += int(row.get("prompts") or 0)
        entry["cost"] += row_cost(row)
    rows = [
        {
            "window": name,
            "agent_hours": round(v["busy"] / SECONDS_PER_HOUR, 1),
            "prompts": int(v["prompts"]),
            "cost": round(v["cost"], 2),
        }
        for name, v in totals.items()
    ]
    return sorted(rows, key=lambda r: (-r["agent_hours"], r["window"]))


# ---- the page model ----------------------------------------------------------------------------


def link(pr: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "url": pr.get("url"),
        "title": pr.get("title") or f"{pr.get('repo')}#{pr.get('number')}",
        "repo": pr.get("repo"),
    }


def week_leverage(week: Week, minutes: float) -> dict[str, Any]:
    whole = leverage(week.busy_seconds, week.prompts, minutes)
    return {key: whole[key] for key in LEVERAGE_KEYS}


def week_view(week: Week, minutes: float) -> dict[str, Any]:
    by_repo: dict[str, int] = {}
    for pr in week.prs:
        by_repo[str(pr.get("repo"))] = by_repo.get(str(pr.get("repo")), 0) + 1
    return {
        "week": week.start.isoformat(),
        "phase": week.phase,
        "partial": week.partial,
        "prs": len(week.prs),
        "prs_by_repo": by_repo,
        "pr_links": [link(pr) for pr in sorted(week.prs, key=lambda p: str(p.get("merged")))],
        "reviews": len(week.reviews),
        "review_links": [link(pr) for pr in week.reviews],
        "agent_hours": round(week.busy_seconds / SECONDS_PER_HOUR, 1),
        "prompts": week.prompts,
        "touch_hours": touch_hours(week.prompts, minutes)["estimate"],
        "leverage": week_leverage(week, minutes),
        "cost": round(week.cost, 2),
    }


def freshness(snapshot: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    collected = parse_time(snapshot.get("collected_at"))
    age = (now - collected).days if collected else None
    return {"collected_at": snapshot.get("collected_at"), "stale": age is None or age >= STALE_DAYS}


def impact_model(snapshot: Mapping[str, Any] | None, now: datetime, zone: tzinfo) -> dict[str, Any]:
    """The whole Impact view; `exists` False until a snapshot was collected."""
    if not snapshot:
        return {"exists": False}
    frame = Frame.of(snapshot, now, zone)
    minutes = float((snapshot.get("settings") or {}).get("prompt_minutes") or DEFAULT_PROMPT_MINUTES)
    weeks = fill_weeks(snapshot, frame, zone)
    return {
        "exists": True,
        **freshness(snapshot, now),
        "start": frame.start.isoformat(),
        "baseline_from": frame.baseline_from.isoformat(),
        "repos": list(snapshot.get("repos") or []),
        "early": len(full(weeks, SINCE)) < EARLY_WEEKS,
        "prompt_minutes": minutes,
        "prs": {
            **compare(weeks, lambda w: len(w.prs)),
            "merge_hours": {BASELINE: merge_hours(weeks, BASELINE), SINCE: merge_hours(weeks, SINCE)},
        },
        "reviews": {
            **compare(weeks, lambda w: len(w.reviews)),
            "first_review_hours": {
                BASELINE: first_review_hours(snapshot, frame, zone, BASELINE),
                SINCE: first_review_hours(snapshot, frame, zone, SINCE),
            },
        },
        "since": since_totals(weeks, minutes),
        "leverage": leverage_compare(weeks, minutes),
        "windows": by_window(snapshot, frame),
        "unpriced_models": unpriced_models(snapshot.get("activity") or []),
        "weeks": [week_view(w, minutes) for w in weeks],
    }
