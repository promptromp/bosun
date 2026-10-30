"""`bosun init`'s wizard: one screen per question, one key per answer, a preview of each choice.

The questions only turn the recommended answers into yours: lib/init_plan.py still decides what they
mean, and nothing changes until you accept the plan on the last screen. The terminal is behind `Ui`
(lib/init_terminal.py) and the machine behind `Machine` (lib/init.py), so this file only needs a list
of keys to test. Design: docs/design/init.md.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import awake
import init_plan
from init_plan import Answers, Facts, Plan, Repo, RepoUse


ENTER = "\n"
BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
MAX_TOGGLES = 9  # one digit each
BAR_START = "00"
RULER = "00          06          12          18          24"
CHUNKS_PER_DAY = 48
CUSTOM = "custom"


class Quit(Exception):
    """You pressed q, or the input ended: nothing was changed."""


class Restart(Exception):
    """You pressed r: back to the first question."""


class Ui(Protocol):
    def show(self, text: str) -> None: ...  # a new screen
    def say(self, text: str) -> None: ...  # more on this screen
    def key(self) -> str: ...  # one key, lowercase; ENTER for Return, "" at the end of the input
    def line(self, prompt: str) -> str: ...  # a line of text


@dataclass(frozen=True)
class Machine:
    """What the wizard needs from outside it."""

    plan: Callable[[Answers], Plan]
    repo_use: Callable[[str], RepoUse | None]  # the folder you typed; None if it is not one


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    changes: Mapping[str, Any] = field(default_factory=dict)
    preview: tuple[str, ...] = ()
    recommended: bool = False


def bold(text: str) -> str:
    return f"{BOLD}{text}{RESET}"


def dim(text: str) -> str:
    return f"{DIM}{text}{RESET}"


def header(step: int, steps: int, title: str, intro: Sequence[str] = ()) -> list[str]:
    return [dim(f"bosun setup, step {step} of {steps}"), "", bold(title), *intro, ""]


def preview_lines(option: Option) -> list[str]:
    indent = "     "
    return [
        wrapped
        for line in option.preview
        for wrapped in textwrap.wrap(
            line, init_plan.LINE_WIDTH, initial_indent=indent, subsequent_indent=indent, drop_whitespace=False
        )
    ]


def option_lines(option: Option) -> list[str]:
    label = f"{option.label} (recommended)" if option.recommended else option.label
    return [bold(f"({option.key})  {label}"), *preview_lines(option), ""]


def footer(*keys: str) -> list[str]:
    return [dim("   ".join((*keys, "(r) restart", "(q) quit and change nothing")))]


def pressed(ui: Ui, allowed: Sequence[str]) -> str:
    """The next key that means something here; q and r end the question."""
    while True:
        key = ui.key()
        if key in ("", "q"):
            raise Quit
        if key == "r":
            raise Restart
        if key in allowed:
            return key


def choose(ui: Ui, top: Sequence[str], options: Sequence[Option], keep_screen: bool = False) -> Option:
    """Ask for one of <options>; Return takes the recommended one."""
    by_key = {option.key: option for option in options}
    recommended = next((option for option in options if option.recommended), None)
    if recommended:
        by_key[ENTER] = recommended
    lines = [*top, *(line for option in options for line in option_lines(option))]
    lines += footer("(enter) the recommended one") if recommended else footer()
    (ui.say if keep_screen else ui.show)("\n".join(lines))
    return by_key[pressed(ui, list(by_key))]


# ---- the repos -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoChoice:
    directory: str  # as sessions.yml says it: ~/Development/api
    prompts: int = 0
    use: RepoUse | None = None
    repo: Repo | None = None  # the entry it already has, with its options


def repo_choices(facts: Facts, answers: Answers) -> list[RepoChoice]:
    """What is chosen now, then the rest of what was found, most used first."""
    uses = {init_plan.tilde(use.dir, facts.home): use for use in facts.repos}
    choices = [
        RepoChoice(r.dir, uses[r.dir].prompts if r.dir in uses else 0, uses.get(r.dir), r)
        for r in answers.repos
    ]
    chosen = {r.dir for r in answers.repos}
    others = [use for directory, use in uses.items() if directory not in chosen]
    others.sort(key=lambda use: -use.prompts)  # stable: ties stay newest first
    choices += [RepoChoice(init_plan.tilde(use.dir, facts.home), use.prompts, use) for use in others]
    return choices[:MAX_TOGGLES]


def repo_screen(top: Sequence[str], choices: Sequence[RepoChoice], chosen: set[str]) -> str:
    width = max((len(choice.directory) for choice in choices), default=0)
    lines = [*top]
    for number, choice in enumerate(choices, 1):
        mark = "x" if choice.directory in chosen else " "
        used = f"{choice.prompts} prompts" if choice.prompts else ""
        lines.append(f"  [{mark}] ({number})  {choice.directory:<{width}}  {dim(used)}".rstrip())
    if not choices:
        lines.append(dim("  None found. Add one, or carry on and add them to sessions.yml later."))
    lines.append("")
    return "\n".join([*lines, *footer("(1-9) switch", "(a) add a folder", "(enter) next")])


def add_repo(ui: Ui, machine: Machine, facts: Facts, choices: list[RepoChoice], chosen: set[str]) -> None:
    typed = ui.line("Folder: ").strip()
    use = machine.repo_use(typed) if typed else None
    if use is None:
        return
    directory = init_plan.tilde(use.dir, facts.home)
    if all(choice.directory != directory for choice in choices):
        choices.append(RepoChoice(directory, use.prompts, use))
    chosen.add(directory)


def chosen_repos(choices: Sequence[RepoChoice], chosen: set[str]) -> tuple[Repo, ...]:
    taken = set(init_plan.RESERVED_WINDOWS) | {
        c.repo.name for c in choices if c.repo and c.directory in chosen
    }
    return tuple(
        choice.repo or Repo(init_plan.window_name(choice.directory, taken), choice.directory)
        for choice in choices
        if choice.directory in chosen
    )


def ask_repos(ui: Ui, facts: Facts, answers: Answers, machine: Machine, top: Sequence[str]) -> Answers:
    choices = repo_choices(facts, answers)
    chosen = {r.dir for r in answers.repos}
    while True:
        ui.show(repo_screen(top, choices, chosen))
        digits = [str(n) for n in range(1, min(len(choices), MAX_TOGGLES) + 1)]
        key = pressed(ui, [*digits, "a", ENTER])
        if key == ENTER:
            break
        if key == "a":
            add_repo(ui, machine, facts, choices, chosen)
            continue
        chosen.symmetric_difference_update({choices[int(key) - 1].directory})
    repos = chosen_repos(choices, chosen)
    uses = [c.use for c in choices if c.directory in chosen and c.use]
    return replace(
        answers,
        repos=repos,
        repos_dir=init_plan.recommended_repos_dir(facts, uses),
        repo_mask=init_plan.recommended_mask(facts, uses),
    )


# ---- the schedule --------------------------------------------------------------------------------


def day_bar(start: str, until: str) -> list[str]:
    """The awake window on a day's ruler, half an hour to a character."""
    first = init_plan.minutes(start) // init_plan.HALF_HOUR
    last = init_plan.minutes(until) // init_plan.HALF_HOUR
    span = (last - first) % CHUNKS_PER_DAY or CHUNKS_PER_DAY
    awake = {(first + n) % CHUNKS_PER_DAY for n in range(span)}
    return [RULER, "".join("█" if chunk in awake else "·" for chunk in range(CHUNKS_PER_DAY))]


def schedule_option(key: str, days: str, start: str, until: str, recommended: bool = False) -> Option:
    label = f"{init_plan.describe_days(days)}, {start} to {until}"
    return Option(
        key, label, {"days": days, "start": start, "until": until}, tuple(day_bar(start, until)), recommended
    )


def schedule_options(answers: Answers) -> list[Option]:
    """The recommended schedule, the same hours on weekdays, bosun's defaults, or your own."""
    candidates = [
        (answers.days, answers.start, answers.until),
        (init_plan.DEFAULT_DAYS, answers.start, answers.until),
        (init_plan.DEFAULT_DAYS, init_plan.DEFAULT_START, init_plan.DEFAULT_UNTIL),
    ]
    options: list[Option] = []
    for days, start, until in dict.fromkeys(candidates):
        key = str(len(options) + 1)
        options.append(schedule_option(key, days, start, until, recommended=not options))
    options.append(Option(str(len(options) + 1), "Something else", {CUSTOM: True}))
    return options


def typed_schedule(ui: Ui, answers: Answers) -> Answers:
    """Ask for each part, keeping the current one on Return, until the three make sense together."""
    current = answers
    while True:
        days = ui.line(f"Days, from {awake.DAY_LETTERS} (R is Thursday, U Sunday) [{current.days}]: ").strip()
        start = ui.line(f"Start, HH:MM [{current.start}]: ").strip()
        until = ui.line(f"Awake until, HH:MM [{current.until}]: ").strip()
        current = replace(
            current, days=days or current.days, start=start or current.start, until=until or current.until
        )
        problems = init_plan.schedule_problems(current)
        if not problems:
            return current
        ui.show("\n".join([*problems, ""]))
        current = answers


def ask_schedule(ui: Ui, facts: Facts, answers: Answers, top: Sequence[str]) -> Answers:
    hours = init_plan.usual_hours(facts)
    said = textwrap.wrap(hours[:1].upper() + hours[1:], init_plan.LINE_WIDTH)
    picked = choose(ui, [*top[:-1], *(dim(line) for line in said), ""], schedule_options(answers))
    if picked.changes.get(CUSTOM):
        return typed_schedule(ui, answers)
    return replace(answers, **picked.changes)


# ---- how proactive, which model, the wake, AWS -----------------------------------------------------

FEATURES_ON = {"events": True, "approvals": True, "learning": True}
FEATURES_OFF = {"events": False, "approvals": False, "learning": False, "slack_watch": False}
PROACTIVE_PREVIEW = (
    "Reports from your sessions reach the orchestrator as they happen, not at the next check-in.",
    "Anything going out under your name waits for your yes to that exact call, in one line.",
    "Each evening a learning run proposes what to fix, quoting its evidence (at most $2 a run).",
)
SLACK_PREVIEW = "A small model also looks for new Slack mentions every 15 minutes (at most $1.50 a day)."
QUIET_PREVIEW = (
    "Sessions come up each morning and the orchestrator runs its chores and hourly check-ins.",
    "Outward actions stop at a permission prompt, which waits until you look. No learning.",
)


def feature_options(facts: Facts) -> list[Option]:
    options = []
    if facts.slack_plugin:
        options.append(
            Option(
                "1",
                "Proactive, with the Slack watcher",
                {**FEATURES_ON, "slack_watch": True},
                (*PROACTIVE_PREVIEW, SLACK_PREVIEW),
                recommended=True,
            )
        )
    plain = {**FEATURES_ON, "slack_watch": False}
    options.append(
        Option(str(len(options) + 1), "Proactive", plain, PROACTIVE_PREVIEW, not facts.slack_plugin)
    )
    options.append(
        Option(str(len(options) + 1), "Keep sessions running, and little else", FEATURES_OFF, QUIET_PREVIEW)
    )
    return options


def ask_features(ui: Ui, facts: Facts, answers: Answers, top: Sequence[str]) -> Answers:
    intro = [*top[:-1]]
    if not facts.slack_plugin:
        intro.append(
            dim("The Slack watcher is not offered: the Slack plugin for Claude Code is not installed.")
        )
    picked = choose(ui, [*intro, ""], feature_options(facts))
    return replace(answers, **picked.changes)


def model_options(answers: Answers) -> list[Option]:
    known = (
        ("opus", "The strongest, and the one that uses your plan's limits fastest."),
        ("sonnet", "Cheaper, and enough for most of the work in a repo."),
    )
    names = [name for name, _ in known]
    options = [
        Option(str(n), name, {"model": name}, (text,), recommended=name == answers.model)
        for n, (name, text) in enumerate(known, 1)
    ]
    if answers.model not in names:
        keep = Option("3", f"{answers.model}, as now", {"model": answers.model}, recommended=True)
        options.append(keep)
    return options


def ask_model(ui: Ui, answers: Answers, top: Sequence[str]) -> Answers:
    picked = choose(ui, top, model_options(answers))
    return replace(answers, **picked.changes)


ALWAYS_ASKED = 5  # repos, schedule, features, model, wake
CLAUDE_PREVIEW = ("One Claude Code session per repo, and the orchestrator runs on it too.",)
CODEX_PREVIEW = (
    "One Codex session per repo, in its sandbox, with its reviewer deciding when something may go past it.",
    "The orchestrator stays on Claude Code. Costs and what you said in a window show less for Codex so far.",
)
AGENT_CHOICES = {init_plan.CLAUDE: ("Claude Code", CLAUDE_PREVIEW), init_plan.CODEX: ("Codex", CODEX_PREVIEW)}
AUTO_PREVIEW = (
    "A safety check reviews each action and blocks risky ones.",
    "Nothing to accept first, and it uses slightly more tokens.",
    "If it blocks several in a row, the session waits at a prompt, and the watchdog tells you.",
)
BYPASS_PREVIEW = (
    "No safety checks at all, and Claude's docs say it offers no protection against prompt injection.",
    "You also accept its warning once before the first start, or the sessions stop there.",
)


def permission_options(answers: Answers) -> list[Option]:
    return [
        Option(
            "1",
            "Auto mode",
            {"permissions": init_plan.AUTO},
            AUTO_PREVIEW,
            answers.permissions == init_plan.AUTO,
        ),
        Option(
            "2",
            "Skip permission checks",
            {"permissions": init_plan.BYPASS},
            BYPASS_PREVIEW,
            answers.permissions == init_plan.BYPASS,
        ),
    ]


def ask_permissions(ui: Ui, answers: Answers, top: Sequence[str]) -> Answers:
    picked = choose(ui, top, permission_options(answers))
    return replace(answers, **picked.changes)


def wake_options(facts: Facts, answers: Answers) -> list[Option]:
    command = init_plan.wake_command(facts, replace(answers, wake=True))
    yes = ["The Mac wakes itself for the morning run."]
    if command:
        yes += [f"Runs: {command}", "That needs your password once, after you accept the plan."]
    if facts.has_battery:
        yes.append("On a laptop, a scheduled wake needs the charger connected and the lid open.")
    if facts.repeating_wake:
        yes.append(f"pmset keeps only one repeating event, so this replaces: {facts.repeating_wake}")
    no = ("The morning run then happens the next time the Mac is awake after its start time.",)
    return [
        Option("y", "Yes, wake the Mac on schedule", {"wake": True}, tuple(yes), recommended=answers.wake),
        Option("n", "No", {"wake": False}, no, recommended=not answers.wake),
    ]


def ask_wake(ui: Ui, facts: Facts, answers: Answers, top: Sequence[str]) -> Answers:
    picked = choose(ui, top, wake_options(facts, answers))
    return replace(answers, **picked.changes)


def ask_aws(ui: Ui, facts: Facts, answers: Answers, top: Sequence[str]) -> Answers:
    """Which of the SSO profiles found should each morning log in to."""
    profiles = facts.aws_sso_profiles[:MAX_TOGGLES]
    chosen = set(answers.aws_sso_profiles)
    while True:
        lines = [*top]
        for number, profile in enumerate(profiles, 1):
            lines.append(f"  [{'x' if profile in chosen else ' '}] ({number})  {profile}")
        ui.show("\n".join([*lines, "", *footer("(1-9) switch", "(enter) next")]))
        key = pressed(ui, [*(str(n) for n in range(1, len(profiles) + 1)), ENTER])
        if key == ENTER:
            return replace(answers, aws_sso_profiles=tuple(p for p in profiles if p in chosen))
        chosen.symmetric_difference_update({profiles[int(key) - 1]})


# ---- the whole conversation ----------------------------------------------------------------------


def has_a_choice_of_agents(facts: Facts) -> bool:
    """Claude Code runs the orchestrator, so it is always there to pick."""
    return any(agent != init_plan.CLAUDE for agent in facts.ready_agents)


def harness_options(facts: Facts, answers: Answers) -> list[Option]:
    offered = [a for a in AGENT_CHOICES if a == init_plan.CLAUDE or a in facts.ready_agents]
    return [
        Option(str(n), AGENT_CHOICES[a][0], {"harness": a}, AGENT_CHOICES[a][1], answers.harness == a)
        for n, a in enumerate(offered, 1)
    ]


def ask_harness(ui: Ui, facts: Facts, answers: Answers, top: Sequence[str]) -> Answers:
    return replace(answers, **choose(ui, top, harness_options(facts, answers)).changes)


def total_steps(facts: Facts, answers: Answers) -> int:
    return (
        ALWAYS_ASKED
        + has_a_choice_of_agents(facts)
        + (answers.harness == init_plan.CLAUDE)
        + bool(facts.aws_sso_profiles)
    )


def ask_all(ui: Ui, facts: Facts, answers: Answers, machine: Machine) -> Answers:
    step = 0

    def top(title: str, intro: Sequence[str] = ()) -> Sequence[str]:
        nonlocal step
        step += 1
        return header(step, total_steps(facts, answers), title, intro)

    windows_intro = ("Each gets a window: one session that keeps running in that repo.",)
    answers = ask_repos(ui, facts, answers, machine, top("Which repos should get a window?", windows_intro))
    answers = ask_schedule(ui, facts, answers, top("When should bosun run?"))
    answers = ask_features(ui, facts, answers, top("How proactive should it be?"))
    if has_a_choice_of_agents(facts):
        answers = ask_harness(ui, facts, answers, top("Which agent should run the repo sessions?"))
    on_claude = answers.harness == init_plan.CLAUDE
    who = "sessions" if on_claude else "orchestrator"
    answers = ask_model(ui, answers, top(f"Which model should the {who} use?"))
    if on_claude:
        answers = ask_permissions(ui, answers, top("How should repo sessions handle permissions?"))
    answers = ask_wake(ui, facts, answers, top("Should the Mac wake by itself?"))
    if facts.aws_sso_profiles:
        answers = ask_aws(ui, facts, answers, top("Which AWS SSO profiles should each morning log in to?"))
    return answers


def accepted(ui: Ui, facts: Facts, answers: Answers, plan: Plan, config_dir: str) -> bool:
    """The plan, as `bosun init` prints it. Blocked, it can only be restarted or left."""
    text = init_plan.render_plan(plan, answers, facts, config_dir)
    keys = ("(y) do it",) if not plan.blockers else ()
    ui.show("\n".join([text, *footer(*keys)]))
    return pressed(ui, ["y"] if not plan.blockers else []) == "y"


def run(ui: Ui, facts: Facts, recommended: Answers, machine: Machine, config_dir: str) -> Answers | None:
    """Your answers once you accept the plan, or None if you quit."""
    while True:
        try:
            answers = ask_all(ui, facts, recommended, machine)
            if accepted(ui, facts, answers, machine.plan(answers), config_dir):
                return answers
        except Restart:
            continue
        except Quit:
            return None


# ---- before and after ----------------------------------------------------------------------------

START_TEXT = (
    "One window per repo, plus the orchestrator, which starts its morning chores right away: it reads "
    "your pull requests and Slack, records them, and asks you before anything goes out."
)
START_PREVIEW = (START_TEXT,)


def ask_start(ui: Ui) -> bool:
    top = ["", bold("bosun is set up. Start the windows now?"), ""]
    options = [
        Option("y", "Yes, start them", preview=START_PREVIEW, recommended=True),
        Option("n", "Not now", preview=("They start at the scheduled time, or with `bosun start`.",)),
    ]
    try:
        return choose(ui, top, options, keep_screen=True).key == "y"  # what install.sh said stays visible
    except (Quit, Restart):
        return False
