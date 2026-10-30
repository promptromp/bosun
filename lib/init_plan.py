"""What `bosun init` recommends and what it would change: pure, over facts and answers.

lib/init_detect.py finds the facts (read-only), this module turns them into recommended answers and
answers into a plan, and lib/init.py prints the plan and carries it out. Design: docs/design/init.md.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timedelta
from typing import Any

from awake import DAY_LETTERS


ENV_FILE, SESSIONS_FILE, SETUP_FILE = "bosun.env", "sessions.yml", "setup.sh"
CONFIG_FILES = (ENV_FILE, SESSIONS_FILE, SETUP_FILE)
REQUIRED_TOOLS = ("tmux", "ruby", "python3", "jq", "claude")
RESERVED_WINDOWS = frozenset({"orchestrator", "checkin"})  # lib/common.sh's window_specs adds them

DEFAULT_DAYS, DEFAULT_START, DEFAULT_UNTIL = "MTWRF", "08:00", "20:00"
DEFAULT_MODEL, DEFAULT_REPOS_DIR = "opus", "~/Development"
DEFAULT_WAKE_DELAY_MINUTES = 1
MINUTES_PER_DAY = 24 * 60
HALF_HOUR = 30
WORKDAY_BEGINS = 4 * 60  # a prompt before 04:00 belongs to the day before
MIN_ACTIVE_DAYS = 5  # with less history a schedule would be a guess
ACTIVE_WEEKDAY_SHARE = 0.6  # a weekday counts if you worked on this share of them
READY_BEFORE = HALF_HOUR  # sessions are up this long before your usual first prompt
EARLIEST_START, LATEST_START = 5 * 60, 11 * 60
MIN_AWAKE = 4 * 60
LEARN_BEFORE_END = 90  # the learning run is this long before the window ends (18:30 for 20:00)
MAX_REPOS = 6  # each is a window, so a Claude that resumes and a context to keep small
MAX_UNUSED_REPOS = 3  # when there is no history to go by
MIN_PROMPTS = 20
OPUS_PLANS = frozenset({"max", "team", "enterprise"})
SKIP_PERMISSIONS_NOTE = (
    "Repo sessions start with --dangerously-skip-permissions (BOSUN_CLAUDE_FLAGS), so an unattended "
    "session does not stall at a prompt. Change it in bosun.env if you prefer prompts."
)


# ---- facts ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoUse:
    dir: str  # absolute
    prompts: int = 0  # in the history window; 0: found by looking in the usual folders
    owner: str = ""  # its GitHub owner, if origin is on GitHub


@dataclass(frozen=True)
class DayUse:
    day: str  # ISO date of the working day
    first: int  # minutes after that day's midnight; past 24:00 after midnight
    last: int


@dataclass(frozen=True)
class Facts:
    home: str
    today: str
    history_days: int = 30
    repos: tuple[RepoUse, ...] = ()
    days: tuple[DayUse, ...] = ()
    missing_tools: tuple[str, ...] = ()
    has_launchd: bool = True  # bosun runs on macOS
    gh_logged_in: bool = False
    claude_logged_in: bool = False
    claude_plan: str = ""
    aws_sso_profiles: tuple[str, ...] = ()
    slack_plugin: bool = False
    has_battery: bool = False
    repeating_wake: str = ""  # the repeating event pmset has now
    files: Mapping[str, str] = field(default_factory=dict)  # the config files that exist, by name
    env: Mapping[str, str] = field(default_factory=dict)  # what bosun.env sets now
    sessions: tuple[Mapping[str, Any], ...] = ()  # what sessions.yml lists now


# ---- answers -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Repo:
    name: str
    dir: str
    options: Mapping[str, Any] = field(default_factory=dict)  # sessions.yml's model, effort, autocompact


@dataclass(frozen=True)
class Answers:
    repos: tuple[Repo, ...] = ()
    days: str = DEFAULT_DAYS
    start: str = DEFAULT_START
    until: str = DEFAULT_UNTIL
    events: bool = True
    approvals: bool = True
    learning: bool = True
    slack_watch: bool = False
    model: str = DEFAULT_MODEL
    repo_mask: str = ""
    repos_dir: str = DEFAULT_REPOS_DIR
    aws_sso_profiles: tuple[str, ...] = ()
    wake: bool = True
    trust: bool = True


class AnswersError(Exception):
    """An answers file that is not what `bosun init --print-answers` prints."""


TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
WINDOW_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]*$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
BOOL_ANSWERS = ("events", "approvals", "learning", "slack_watch", "wake", "trust")
TEXT_ANSWERS = ("days", "start", "until", "model", "repo_mask", "repos_dir")


def answers_to_json(answers: Answers) -> dict[str, Any]:
    data: dict[str, Any] = {f.name: getattr(answers, f.name) for f in fields(Answers)}
    data["repos"] = [{"name": r.name, "dir": r.dir, **r.options} for r in answers.repos]
    data["aws_sso_profiles"] = list(answers.aws_sso_profiles)
    return data


def repo_from_json(item: object, taken: set[str]) -> Repo:
    if not isinstance(item, dict) or not isinstance(item.get("dir"), str):
        raise AnswersError(f"repos: {item!r} needs at least a dir")
    fields_left = dict(item)
    directory, name = fields_left.pop("dir"), fields_left.pop("name", None)
    if name is not None and not isinstance(name, str):
        raise AnswersError(f"repos: the name of {directory} must be text")
    return Repo(name or window_name(directory, taken), directory, fields_left)


def answers_from_json(data: object, base: Answers) -> Answers:
    """<base> with what <data> sets, so a file only needs the answers it changes."""
    if not isinstance(data, dict):
        raise AnswersError("the answers must be a JSON object")
    unknown = sorted(set(data) - {f.name for f in fields(Answers)})
    if unknown:
        raise AnswersError(f"unknown answers: {', '.join(unknown)}")
    changes: dict[str, Any] = {}
    for key, value in data.items():
        changes[key] = checked_answer(key, value)
    return replace(base, **changes)


def checked_repos(value: object) -> tuple[Repo, ...]:
    if not isinstance(value, list):
        raise AnswersError("repos must be a list")
    taken: set[str] = set(RESERVED_WINDOWS)
    return tuple(repo_from_json(item, taken) for item in value)


def checked_profiles(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(profile, str) for profile in value):
        raise AnswersError("aws_sso_profiles must be a list of names")
    return tuple(value)


def checked_answer(key: str, value: object) -> object:
    if key == "repos":
        return checked_repos(value)
    if key == "aws_sso_profiles":
        return checked_profiles(value)
    if key in BOOL_ANSWERS and not isinstance(value, bool):
        raise AnswersError(f"{key} must be true or false")
    if key in TEXT_ANSWERS and not isinstance(value, str):
        raise AnswersError(f"{key} must be text")
    return value


def schedule_problems(answers: Answers) -> list[str]:
    problems = []
    letters = answers.days
    if not letters or set(letters) - set(DAY_LETTERS) or len(set(letters)) != len(letters):
        problems.append(f"days: '{letters}' must use each of {DAY_LETTERS} at most once")
    times = (("start", answers.start), ("until", answers.until))
    problems += [f"{key}: '{value}' must be HH:MM, 24-hour" for key, value in times if not TIME.match(value)]
    if answers.start == answers.until:
        problems.append("until: the same as start would keep the Mac awake around the clock")
    return problems


def repo_problems(repos: Sequence[Repo]) -> list[str]:
    problems = []
    names = Counter(r.name for r in repos)
    for repo in repos:
        if not WINDOW_NAME.match(repo.name):
            problems.append(
                f"repos: '{repo.name}' must be letters, digits, - or _ (tmux targets a window by it)"
            )
        if repo.name in RESERVED_WINDOWS:
            problems.append(f"repos: '{repo.name}' is a window bosun adds itself")
        if names[repo.name] > 1:
            problems.append(f"repos: '{repo.name}' is listed more than once")
    return problems


def validate(answers: Answers) -> list[str]:
    """Why these answers cannot be used, if they cannot."""
    problems = schedule_problems(answers) + repo_problems(answers.repos)
    if answers.slack_watch and not answers.events:
        problems.append("slack_watch: the Slack watcher needs events on")
    if not MODEL_NAME.match(answers.model):
        problems.append(f"model: '{answers.model}' is not a model name")
    texts = [answers.repo_mask, answers.repos_dir, *answers.aws_sso_profiles, *(r.dir for r in answers.repos)]
    if any(CONTROL.search(text) for text in texts):
        problems.append("a repo, folder or profile contains a control character")
    return problems


# ---- recommendations -----------------------------------------------------------------------------


def minutes(clock_time: str) -> int:
    hours, mins = clock_time.split(":")
    return int(hours) * 60 + int(mins)


def clock(total: int) -> str:
    total %= MINUTES_PER_DAY
    return f"{total // 60:02d}:{total % 60:02d}"


def working_days(times: Iterable[datetime]) -> tuple[DayUse, ...]:
    """Per working day, its first and last moment in <times> (local times). Work past midnight belongs
    to the day it started, so a late night does not look like the next day's early start."""
    moments: dict[date, list[int]] = {}
    for at in times:
        day = (at - timedelta(minutes=WORKDAY_BEGINS)).date()
        moment = at.hour * 60 + at.minute + (MINUTES_PER_DAY if at.date() != day else 0)
        moments.setdefault(day, []).append(moment)
    return tuple(DayUse(day.isoformat(), min(m), max(m)) for day, m in sorted(moments.items()))


def active_weekdays(days: Sequence[DayUse], today: date) -> str:
    """The weekdays (pmset's letters) you worked on most of since your first day in <days>."""
    dates = [date.fromisoformat(d.day) for d in days]
    possible = Counter((today - timedelta(days=n)).weekday() for n in range((today - min(dates)).days + 1))
    worked = Counter(d.weekday() for d in dates)
    return "".join(
        letter
        for number, letter in enumerate(DAY_LETTERS)
        if possible[number] and worked[number] >= ACTIVE_WEEKDAY_SHARE * possible[number]
    )


def recommended_hours(days: Sequence[DayUse]) -> tuple[str, str]:
    """Up half an hour before your usual first prompt, awake until after your usual last one."""
    first = int(statistics.median(d.first for d in days))
    last = int(statistics.median(d.last for d in days))
    ready = (first - READY_BEFORE) // HALF_HOUR * HALF_HOUR
    start = min(max(ready, EARLIEST_START), LATEST_START)
    end = -(-last // HALF_HOUR) * HALF_HOUR  # rounded up
    until = min(max(end, start + MIN_AWAKE), start + MINUTES_PER_DAY - HALF_HOUR)
    return clock(start), clock(until)


def recommended_schedule(facts: Facts) -> tuple[str, str, str]:
    """(days, start, until): on a first run what your history shows, if it shows enough; on a re-run
    what bosun.env says, and bosun's defaults for what it does not."""
    days, start, until = DEFAULT_DAYS, DEFAULT_START, DEFAULT_UNTIL
    if not facts.env and len(facts.days) >= MIN_ACTIVE_DAYS:
        days = active_weekdays(facts.days, date.fromisoformat(facts.today)) or DEFAULT_DAYS
        start, until = recommended_hours(facts.days)
    env = facts.env
    return (
        env.get("BOSUN_DAYS", days),
        env.get("BOSUN_START_TIME", start),
        env.get("BOSUN_AWAKE_UNTIL", until),
    )


def tilde(path: str, home: str) -> str:
    if path == home:
        return "~"
    if path.startswith(home.rstrip("/") + "/"):
        return "~" + path[len(home.rstrip("/")) :]
    return path


def window_name(directory: str, taken: set[str]) -> str:
    """A tmux window name for <directory> that is not in <taken>, which it joins."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", posixpath.basename(directory.rstrip("/"))).strip("-_") or "repo"
    name, suffix = base, 2
    while name in taken:
        name, suffix = f"{base}-{suffix}", suffix + 1
    taken.add(name)
    return name


def recommended_uses(repos: Sequence[RepoUse]) -> list[RepoUse]:
    ranked = sorted(repos, key=lambda r: -r.prompts)  # stable: ties stay newest first
    busy = [r for r in ranked if r.prompts >= MIN_PROMPTS]
    return busy[:MAX_REPOS] if busy else ranked[:MAX_UNUSED_REPOS]


def existing_repos(facts: Facts) -> tuple[Repo, ...]:
    repos = []
    for entry in facts.sessions:
        fields_left = dict(entry)
        name, directory = str(fields_left.pop("name", "")), str(fields_left.pop("dir", ""))
        repos.append(Repo(name, tilde(directory, facts.home), fields_left))
    return tuple(repos)


def recommended_repos(facts: Facts) -> tuple[Repo, ...]:
    if SESSIONS_FILE in facts.files:
        return existing_repos(facts)
    taken: set[str] = set(RESERVED_WINDOWS)
    return tuple(
        Repo(window_name(use.dir, taken), tilde(use.dir, facts.home)) for use in recommended_uses(facts.repos)
    )


def recommended_repos_dir(facts: Facts, uses: Sequence[RepoUse]) -> str:
    """The folder your repos share, for the orchestrator to run in (not your home folder)."""
    if facts.env:
        return facts.env.get("BOSUN_REPOS_DIR", DEFAULT_REPOS_DIR).replace("$HOME", "~", 1)
    parents = [posixpath.dirname(use.dir) for use in uses]
    if not parents:
        return DEFAULT_REPOS_DIR
    shared = posixpath.commonpath(parents)
    useful = shared not in ("/", facts.home) and shared.startswith(facts.home)
    return tilde(shared if useful else parents[0], facts.home)


def recommended_mask(facts: Facts, uses: Sequence[RepoUse]) -> str:
    if facts.env:
        return facts.env.get("BOSUN_REPO_MASK", "")
    return " ".join(sorted({use.owner for use in uses if use.owner}))


def recommended_model(facts: Facts) -> str:
    if facts.env:
        return facts.env.get("BOSUN_MODEL") or DEFAULT_MODEL
    return "opus" if facts.claude_plan in OPUS_PLANS else "sonnet"


def recommend(facts: Facts) -> Answers:
    """The recommended setup: a first run from what the Mac shows, a re-run from the config it has."""
    uses = recommended_uses(facts.repos)
    days, start, until = recommended_schedule(facts)

    def on(key: str, recommended: bool) -> bool:
        """On a re-run a switch bosun.env leaves out is at bosun's default: off."""
        return facts.env.get(key, "1" if recommended and not facts.env else "0") == "1"

    events = on("BOSUN_EVENTS_ENABLED", True)
    return Answers(
        repos=recommended_repos(facts),
        days=days,
        start=start,
        until=until,
        events=events,
        approvals=on("BOSUN_APPROVALS_ENABLED", True),
        learning=on("BOSUN_LEARN_ENABLED", True),
        slack_watch=on("BOSUN_SLACK_WATCH_ENABLED", facts.slack_plugin and events),
        model=recommended_model(facts),
        repo_mask=recommended_mask(facts, uses),
        repos_dir=recommended_repos_dir(facts, uses),
        # With more than one profile, which one the mornings should log in to is your call.
        aws_sso_profiles=facts.aws_sso_profiles if len(facts.aws_sso_profiles) == 1 else (),
        # pmset keeps only one repeating event: never replace someone else's by default.
        wake=not facts.repeating_wake or bool(facts.env),
    )


# ---- the config files ----------------------------------------------------------------------------


def env_path(path: str) -> str:
    return "$HOME" + path[1:] if path == "~" or path.startswith("~/") else path


def env_value(value: str) -> str:
    """<value> as a double-quoted shell word; only a leading $HOME expands."""
    prefix = "$HOME" if value == "$HOME" or value.startswith("$HOME/") else ""
    rest = value[len(prefix) :]
    return '"' + prefix + re.sub(r'([\\"`$])', r"\\\1", rest) + '"'


def learn_at(answers: Answers) -> str:
    start, until = minutes(answers.start), minutes(answers.until)
    end = until if until > start else until + MINUTES_PER_DAY
    return clock(max(end - LEARN_BEFORE_END, start))


def env_groups(answers: Answers) -> list[tuple[str, list[tuple[str, str]]]]:
    flag = {True: "1", False: "0"}
    return [
        (
            "Schedule",
            [
                ("BOSUN_DAYS", answers.days),
                ("BOSUN_START_TIME", answers.start),
                ("BOSUN_AWAKE_UNTIL", answers.until),
            ],
        ),
        (
            "Models",
            [
                ("BOSUN_MODEL", answers.model),
                ("BOSUN_ORCHESTRATOR_MODEL", answers.model),
                ("BOSUN_LEARN_MODEL", answers.model),
            ],
        ),
        (
            "Where the repos are, and which are in scope",
            [("BOSUN_REPOS_DIR", env_path(answers.repos_dir)), ("BOSUN_REPO_MASK", answers.repo_mask)],
        ),
        (
            "Features",
            [
                ("BOSUN_EVENTS_ENABLED", flag[answers.events]),
                ("BOSUN_SLACK_WATCH_ENABLED", flag[answers.slack_watch]),
                ("BOSUN_APPROVALS_ENABLED", flag[answers.approvals]),
                ("BOSUN_LEARN_ENABLED", flag[answers.learning]),
                ("BOSUN_LEARN_AT", learn_at(answers)),
            ],
        ),
    ]


ENV_HEADER = """\
# bosun configuration: only what differs from the defaults (every setting and what it means:
# examples/generic/bosun.env). Written by `bosun init`. Edit it freely: a re-run updates these
# settings and keeps anything you add. Run ./install.sh after changing the schedule.
"""


def render_env(answers: Answers) -> str:
    parts = [ENV_HEADER]
    for title, settings in env_groups(answers):
        lines = "".join(f"{key}={env_value(value)}\n" for key, value in settings)
        parts.append(f"\n# {title}\n{lines}")
    return "".join(parts)


ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(BOSUN_[A-Z0-9_]+)=(.*)$")


def shell_value(raw: str) -> str | None:
    """The word <raw> assigns, with $HOME and the like left as written; None if it is not one word."""
    try:
        words = shlex.split(raw, comments=True)
    except ValueError:
        return None
    return words[0] if len(words) == 1 else "" if not words else None


def parse_env(text: str) -> dict[str, str]:
    """The settings an env file assigns; a later line wins, as in the shell."""
    found = {}
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        value = shell_value(match.group(2)) if match else None
        if match and value is not None:
            found[match.group(1)] = value
    return found


def merged_line(line: str, match: re.Match[str] | None, wanted: Mapping[str, str]) -> str:
    """<line>, unless it assigns a setting in <wanted> something else."""
    if not match or match.group(1) not in wanted:
        return line
    key = match.group(1)
    return line if shell_value(match.group(2)) == wanted[key] else f"{key}={env_value(wanted[key])}"


def settings_to_keep(facts: Facts, answers: Answers) -> frozenset[str]:
    """Settings that follow from an answer that did not change. A re-run leaves them alone, so one you
    set apart from it stays yours; changing the answer moves them along."""
    env = facts.env
    kept: set[str] = set()
    if env.get("BOSUN_MODEL", DEFAULT_MODEL) == answers.model:
        kept |= {"BOSUN_ORCHESTRATOR_MODEL", "BOSUN_LEARN_MODEL"}
    schedule = (env.get("BOSUN_START_TIME", DEFAULT_START), env.get("BOSUN_AWAKE_UNTIL", DEFAULT_UNTIL))
    if schedule == (answers.start, answers.until):
        kept.add("BOSUN_LEARN_AT")
    return frozenset(kept)


def merge_env(text: str, answers: Answers, keep: frozenset[str] = frozenset()) -> str:
    """<text> with the answers' settings changed, every other line as it was, and the ones it does
    not have yet added at the end. Settings in <keep> are left as they are, or left out."""
    wanted = {key: value for _, settings in env_groups(answers) for key, value in settings if key not in keep}
    missing = dict(wanted)
    lines = []
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if match and match.group(1) in wanted:
            missing.pop(match.group(1), None)
        lines.append(merged_line(line, match, wanted))
    if missing:
        lines += ["", "# Added by `bosun init`", *(f"{k}={env_value(v)}" for k, v in missing.items())]
    return "\n".join(lines) + "\n"


PLAIN_SCALAR = re.compile(r"^(?:~/)?[A-Za-z][A-Za-z0-9_./-]*$")
YAML_WORDS = frozenset({"true", "false", "yes", "no", "on", "off", "null"})


def yaml_scalar(value: object) -> str:
    """<value> for sessions.yml. Quoted text is JSON, which is also valid YAML."""
    text = str(value)
    if isinstance(value, str) and PLAIN_SCALAR.match(text) and text.lower() not in YAML_WORDS:
        return text
    return json.dumps(value)


SESSIONS_HEADER = """\
# One tmux window per entry, written by `bosun init`. `name` is the Claude session name: the most
# recent session with exactly this name in `dir` is resumed; if none exists, a new one is created.
# Optional per window: `model`, `effort` and `autocompact` (defaults: bosun.env).
"""


def render_sessions(answers: Answers) -> str:
    if not answers.repos:
        return SESSIONS_HEADER + "sessions: []\n"
    lines = ["sessions:"]
    for repo in answers.repos:
        lines += [f"  - name: {yaml_scalar(repo.name)}", f"    dir: {yaml_scalar(repo.dir)}"]
        lines += [f"    {key}: {yaml_scalar(value)}" for key, value in repo.options.items()]
    return SESSIONS_HEADER + "\n".join(lines) + "\n"


SETUP_HEADER = """\
# shellcheck shell=bash
# Setup steps run by `bosun morning` after the network is up (and by `bosun setup`). Written once by
# `bosun init`, which never changes it again. Helpers (see lib/setup-helpers.sh):
#   ensure_claude_login               Claude Code is logged in and its login works (one tiny request)
#   ensure_aws_sso <profile>          skip if credentials work, else `aws sso login`
#   run_step "<description>" cmd ...  run any command with the login timeout
# A failed step is logged and reported, and the remaining steps still run.

"""


def render_setup(answers: Answers) -> str:
    steps = ["ensure_claude_login", *(f"ensure_aws_sso {shlex.quote(p)}" for p in answers.aws_sso_profiles)]
    body = "".join(f"  {step}\n" for step in steps)
    return f"{SETUP_HEADER}setup_steps() {{\n{body}}}\n"


# ---- the plan ------------------------------------------------------------------------------------

CREATE, UPDATE, SAME, KEEP, REPLACE, REFUSE = "create", "update", "same", "keep", "replace", "refuse"
CHANGING = frozenset({CREATE, UPDATE, REPLACE})


@dataclass(frozen=True)
class FileChange:
    name: str
    action: str  # create, update (merged), same, keep, replace (--force, backed up), refuse
    text: str  # what it would contain
    detail: str


@dataclass(frozen=True)
class DirState:
    """What the folders in the answers look like now (lib/init.py checks; this module does no I/O)."""

    missing: tuple[str, ...] = ()
    untrusted: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    files: tuple[FileChange, ...]
    trust: tuple[str, ...]
    wake_command: str
    reasons: tuple[str, ...]
    notes: tuple[str, ...]
    blockers: tuple[str, ...]


def env_change(facts: Facts, answers: Answers) -> FileChange:
    count = sum(len(settings) for _, settings in env_groups(answers))
    current = facts.files.get(ENV_FILE)
    if current is None:
        return FileChange(
            ENV_FILE, CREATE, render_env(answers), f"{count} settings; the rest keep their defaults"
        )
    merged = merge_env(current, answers, settings_to_keep(facts, answers))
    if merged == current:
        return FileChange(ENV_FILE, SAME, current, "already says this")
    return FileChange(ENV_FILE, UPDATE, merged, "these settings change; everything else in it stays")


def same_sessions(facts: Facts, answers: Answers) -> bool:
    return existing_repos(facts) == answers.repos


def sessions_change(facts: Facts, answers: Answers, force: bool) -> FileChange:
    text = render_sessions(answers)
    count = f"{len(answers.repos)} window{'s' if len(answers.repos) != 1 else ''}"
    if SESSIONS_FILE not in facts.files:
        return FileChange(SESSIONS_FILE, CREATE, text, count)
    if same_sessions(facts, answers):
        return FileChange(SESSIONS_FILE, SAME, facts.files[SESSIONS_FILE], "already lists these windows")
    if force:
        return FileChange(SESSIONS_FILE, REPLACE, text, f"{count}; the old file is kept as a backup")
    return FileChange(SESSIONS_FILE, REFUSE, text, "lists other windows; --force replaces it, with a backup")


def setup_change(facts: Facts, answers: Answers) -> FileChange:
    text = render_setup(answers)
    steps = "checks the Claude Code login each morning"
    if answers.aws_sso_profiles:
        steps += f" and logs in to AWS ({', '.join(answers.aws_sso_profiles)})"
    if SETUP_FILE not in facts.files:
        return FileChange(SETUP_FILE, CREATE, text, steps)
    return FileChange(SETUP_FILE, KEEP, facts.files[SETUP_FILE], "yours; bosun init leaves it alone")


def pmset_label(clock_time: str) -> str:
    """How `pmset -g sched` prints a time: 8:01AM."""
    hours, mins = (int(part) for part in clock_time.split(":"))
    return f"{hours % 12 or 12}:{mins:02d}{'AM' if hours < 12 else 'PM'}"  # noqa: PLR2004 (noon)


def wake_command(facts: Facts, answers: Answers) -> str:
    """The sudo line for the wake, or nothing if it is not wanted or already set for this schedule."""
    if not answers.wake:
        return ""
    delay = int(facts.env.get("BOSUN_WAKE_DELAY_MINUTES", DEFAULT_WAKE_DELAY_MINUTES))
    wake_at = clock(minutes(answers.start) + delay)
    current = facts.env.get("BOSUN_DAYS") == answers.days and pmset_label(wake_at) in facts.repeating_wake
    return "" if current else f"sudo pmset repeat wakeorpoweron {answers.days} {wake_at}:00"


def windows_reason(facts: Facts, answers: Answers) -> list[str]:
    if not answers.repos or SESSIONS_FILE in facts.files:
        return []
    if any(use.prompts for use in facts.repos):
        return [f"Windows: the repos you used Claude Code in most over the last {facts.history_days} days."]
    return ["Windows: the git repos found in your usual folders, newest first."]


def schedule_reason(facts: Facts) -> list[str]:
    if len(facts.days) < MIN_ACTIVE_DAYS or "BOSUN_START_TIME" in facts.env:
        return []
    first = clock(int(statistics.median(d.first for d in facts.days)))
    last = clock(int(statistics.median(d.last for d in facts.days)))
    reason = (
        f"Schedule: your usual first prompt is {first} and your last {last}, over {len(facts.days)} active "
        "days. Sessions are ready half an hour before you start."
    )
    return [reason]


def reasons(facts: Facts, answers: Answers) -> tuple[str, ...]:
    found = windows_reason(facts, answers) + schedule_reason(facts)
    if facts.env:
        found.append("Everything else: what your config says now.")
    return tuple(found)


def login_notes(facts: Facts) -> list[str]:
    notes = []
    if not facts.claude_logged_in:
        notes.append("Claude Code is not logged in: run `claude auth login`.")
    if not facts.gh_logged_in:
        notes.append("The GitHub CLI is not logged in, which the orchestrator needs: run `gh auth login`.")
    return notes


def wake_notes(facts: Facts, answers: Answers) -> list[str]:
    if answers.wake:
        return [
            "On a laptop, a scheduled wake needs the charger connected and the lid open."
        ] * facts.has_battery
    if facts.repeating_wake:
        kept = (
            f"pmset already has a repeating event ({facts.repeating_wake}) and keeps only one, so it stays. "
            "Answer wake: true to replace it."
        )
        return [kept]
    return []


def setup_notes(facts: Facts, answers: Answers) -> list[str]:
    current = facts.files.get(SETUP_FILE)
    if current is None:
        return []
    lacking = [profile for profile in answers.aws_sso_profiles if profile not in current]
    return [
        f"setup.sh is yours, so add this to it yourself: ensure_aws_sso {shlex.quote(p)}" for p in lacking
    ]


def feature_notes(facts: Facts, answers: Answers) -> list[str]:
    found = setup_notes(facts, answers)
    if answers.events and not answers.slack_watch and not facts.slack_plugin:
        found.append("The Slack watcher stays off: the Slack plugin for Claude Code is not installed.")
    if len(facts.aws_sso_profiles) > 1 and not answers.aws_sso_profiles:
        found.append(
            f"{len(facts.aws_sso_profiles)} AWS SSO profiles found: name the ones to log in to each "
            "morning in aws_sso_profiles (or with ensure_aws_sso in setup.sh)."
        )
    return found


def notes(facts: Facts, answers: Answers) -> tuple[str, ...]:
    first_run = [SKIP_PERMISSIONS_NOTE] if ENV_FILE not in facts.files else []
    return tuple(login_notes(facts) + feature_notes(facts, answers) + wake_notes(facts, answers) + first_run)


def blockers(facts: Facts, state: DirState, files: Sequence[FileChange]) -> tuple[str, ...]:
    found = [] if facts.has_launchd else ["bosun runs on macOS: it schedules with launchd and pmset."]
    if facts.missing_tools:
        found.append(
            f"Missing: {', '.join(facts.missing_tools)}. `brew install tmux jq python` covers most; Claude "
            "Code is at https://claude.com/claude-code."
        )
    found += [f"No such folder: {directory}" for directory in state.missing]
    found += [f"{change.name} {change.detail}" for change in files if change.action == REFUSE]
    return tuple(found)


def build_plan(facts: Facts, answers: Answers, state: DirState, force: bool = False) -> Plan:
    files = (
        env_change(facts, answers),
        sessions_change(facts, answers, force),
        setup_change(facts, answers),
    )
    return Plan(
        files=files,
        trust=state.untrusted if answers.trust else (),
        wake_command=wake_command(facts, answers),
        reasons=reasons(facts, answers),
        notes=notes(facts, answers),
        blockers=blockers(facts, state, files),
    )


# ---- printing the plan ---------------------------------------------------------------------------

DAY_NAMES = dict(zip(DAY_LETTERS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), strict=True))


def describe_days(days: str) -> str:
    """'MTWRF' as 'Mon-Fri', 'MWF' as 'Mon, Wed, Fri', in the week's order."""
    numbers = sorted(DAY_LETTERS.index(letter) for letter in days if letter in DAY_LETTERS)
    names = [DAY_NAMES[DAY_LETTERS[number]] for number in numbers]
    consecutive = len(numbers) > 2 and numbers == list(range(numbers[0], numbers[-1] + 1))  # noqa: PLR2004
    return f"{names[0]}-{names[-1]}" if consecutive else ", ".join(names)


def describe_features(answers: Answers) -> str:
    switches = (
        ("events", answers.events),
        ("approvals", answers.approvals),
        ("learning", answers.learning),
        ("Slack watcher", answers.slack_watch),
    )
    on = [name for name, value in switches if value]
    off = [name for name, value in switches if not value]
    return "; ".join(
        part for part in (f"{', '.join(on)} on" if on else "", f"{', '.join(off)} off" if off else "") if part
    )


def summary_lines(answers: Answers) -> list[str]:
    windows = ", ".join(r.name for r in answers.repos) or "none yet (add them to sessions.yml)"
    return [
        f"  Windows   {windows} (plus the orchestrator and its check-in loop)",
        f"  Schedule  {describe_days(answers.days)}, {answers.start} to {answers.until}",
        f"  Features  {describe_features(answers)}",
        f"  Model     {answers.model}",
    ]


def step_lines(plan: Plan, home: str) -> list[str]:
    lines = []
    if plan.trust:
        lines.append(f"  bosun trust --any {' '.join(tilde(d, home) for d in plan.trust)}")
    with_wake = " --with-wake" if plan.wake_command else ""
    lines.append(f"  ./install.sh{with_wake}   links `bosun`, loads its launchd jobs, runs `bosun doctor`")
    if plan.wake_command:
        lines.append(f"  {plan.wake_command}   wakes the Mac on schedule; pmset needs admin rights")
    return lines


def section(title: str, lines: Sequence[str]) -> list[str]:
    return ["", title, *lines] if lines else []


def render_plan(plan: Plan, answers: Answers, facts: Facts, config_dir: str) -> str:
    files = [f"  {c.action:<8} {c.name:<13} {c.detail}" for c in plan.files]
    lines = [
        "bosun init: the plan. Nothing has changed yet.",
        "",
        *summary_lines(answers),
        *section("Why", [f"  - {reason}" for reason in plan.reasons]),
        *section(f"Files in {tilde(config_dir, facts.home)}", files),
        *section("Then", step_lines(plan, facts.home)),
        *section("Worth knowing", [f"  - {note}" for note in plan.notes]),
        *section("Blocked", [f"  - {blocker}" for blocker in plan.blockers]),
    ]
    return "\n".join(lines) + "\n"
