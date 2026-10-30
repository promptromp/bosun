# Installing: `bosun init`

Installing used to mean cloning, running `install.sh`, hand-editing three files (`bosun.env` alone has
over 130 settings), re-running and pasting a `sudo` line. Now it is one command, `curl ... | sh`, which
ends in a short wizard that starts from what the Mac already shows and shows its plan before it changes
anything.

## How it works

| Part | File | What it does |
|---|---|---|
| Bootstrap | `get.sh` | Plain `sh`, because it runs before anything is known to be there: only gets bosun and starts `init`. |
| Preflight | `lib/preflight.sh`, `lib/platforms/`, `lib/harnesses/` | Plain `sh`: what bosun needs, and what to do about anything missing. Changes nothing. |
| Detect | `lib/init_detect.py` | Read-only: the facts. Claude Code's files are read through `lib/claude_state.py`. |
| Plan | `lib/init_plan.py` | Pure: facts to recommended answers, answers to file contents and a plan. |
| Wizard | `lib/init_wizard.py` | Pure over a `Ui`: the questions, from recommended answers to yours. |
| Terminal | `lib/init_terminal.py` | A key at a time, a screen at a time, and the terminal put back. |
| Apply | `lib/init.py` | The CLI. Writes the config with backups, trusts the folders, runs `install.sh`. |

`install.sh` stays the one place that knows launchd, `pmset` and `bosun doctor`, so `init` only
writes what `install.sh` reads. Answers are JSON (`--print-answers`), and a file only needs what it
changes: the wizard only fills those in, so `--answers` and the wizard cannot disagree.

**Nothing changes until you accept the plan.** In a terminal `bosun init` asks its questions and then
shows the plan. Without a terminal, or with `--dry-run`, it only prints the plan; `--recommended` or
`--answers` carry one out without asking.

## The wizard

Taken from what makes oh-my-zsh and Powerlevel10k pleasant, with what they lack added:

- One key per answer, no Return. Return takes the recommended answer, so a new user can lean on it.
- A preview under each choice: the awake window drawn on a day's ruler, what a level of proactivity
  will actually do, the exact `sudo` line.
- `(recommended)` on the answer it would pick, and the reason from your own history above it.
- `r` starts again and `q` leaves with nothing changed, on every screen, as does Ctrl-C.
- It asks only what it cannot find out: the AWS question only appears if there are SSO profiles, and
  which agent to use only if Codex is ready to run.
- Re-runnable, starting from your config, and usable without a terminal (Powerlevel10k has no such
  mode, and it is what lets everything here be tested).

`get.sh` follows oh-my-zsh: one line, an unattended mode, plain `sh`. Behind a pipe its standard input is
the script, so it hands `bosun init` the terminal from `/dev/tty`. All of it is in functions with `main`
last, so a download cut short does nothing.

## What it recommends

On a first run your own history beats a generic default. A re-run starts from your config instead.

| Answer | From |
|---|---|
| Repos | The ones you typed the most prompts in over 30 days, in Claude Code and Codex (20 or more, at most 6). Worktrees count for their main checkout; scripted runs (`claude -p`) do not count. With no history, up to 3 git repos from the usual folders, newest first. |
| Days | Weekdays you worked on at least 60% of since your first prompt. |
| Hours | Half an hour before your median first prompt, to after your median last one, at least 4 hours. Work before 04:00 belongs to the day before. With under 5 active days: 08:00 to 20:00, Monday to Friday. |
| Features | Events, approvals and learning on. The Slack watcher only if the Slack plugin is installed. |
| Agent | Only asked once Codex is installed and logged in: what `BOSUN_HARNESS` asks for, else your config, else Codex if you typed more prompts there, else Claude Code. |
| Model | `opus` on Max, Team and Enterprise plans, else `sonnet`. On Codex this is for the orchestrator, and repo sessions use `BOSUN_CODEX_MODEL`. |
| Repo mask | The GitHub owners of the chosen repos. |
| Orchestrator folder | The folder the repos share, never your home folder. |
| AWS SSO | The profile, if there is exactly one; with more, the choice is yours. |
| Wake | Yes, unless `pmset` already has a repeating event that bosun did not set: it keeps only one, and it is not ours to replace by default. |

It only reads where and when prompts were typed, never what they say.

## What it never does

- **Overwrite what is yours.** `bosun.env` is merged: the settings it manages change, every other
  line stays, and the old file is kept as `bosun.env.pre-init-<time>`. Settings that follow from an
  answer (the orchestrator's model, the learning time) only move when that answer does, so one you
  set apart from it stays yours. A `sessions.yml` that lists other windows needs `--force`, also with
  a backup. `setup.sh` is code: once it exists it is never changed.
- **Change your setup on a re-run.** A re-run goes by your config and bosun's defaults, not by your
  history: a switch `bosun.env` leaves out stays off. To change something, answer it.
- **Use `sudo` quietly.** One command, `pmset repeat`, named in the plan with its reason, and only
  when the wake is not already set for this schedule. Without a terminal to ask for a password, it
  prints the line for you to run.
- **Print your config.** `--detect` says which settings exist, not what they say: people paste its
  output into issues, and `bosun.env` can hold a webhook.
- **Replace another repeating wake,** or start anything before the plan has been shown.

It asks how repo sessions handle permissions, and recommends Claude Code's auto mode: a safety check
reviews each action, and there is no warning to accept before the first start. If auto mode blocks
several actions in a row the session waits at a prompt, and the watchdog says so. The other answer,
`--dangerously-skip-permissions`, has no checks at all, and its warning has to be accepted once by
hand: its cursor starts on "No, exit", so a window nobody is watching stops there.

## What it needs first

`bosun init` does not install anything for you. It used to offer to, and that made it the one piece of
this that had to know every OS's package manager, and it could still not do the parts that need a
person: logging in, and the screens Claude Code shows the first time. So it finds out what is missing,
says all of it at once, with the line to run or the page to go to, and exits. You do those, and run the
same line again.

`lib/preflight.sh` does this in layers, so that another OS or agent is one more file, not a change to
the flow:

1. **The OS picks a platform profile**, `lib/platforms/<os>.sh`: its package manager, the line that
   installs from it, how a tool's package is named, and what to say when any of that is missing.
2. **Each agent in use picks a harness profile**, `lib/harnesses/<harness>.sh`: how to tell it is installed,
   new enough, logged in, and past whatever it asks a person the first time. Those are Claude Code for
   the orchestrator, `BOSUN_HARNESS`, and the `harness:` of any window.
3. **The flow** in `lib/preflight.sh` knows neither. It checks everything and prints a list, then
   numbered next steps. Exit status 0 means all there, 1 something to do, 2 no profile for this OS or
   harness.

It is plain `sh` because it runs before Python 3.13 is known to be there, and the harness's checks that
need Python wait until it is. `bosun init` and `bosun doctor` both start with it, so `doctor` cannot say
"all checks passed" over a machine that would stall on its first morning.

**To add an OS:** add `lib/platforms/<os>.sh` with the same functions as `macos.sh`, map its `uname -s`
in `load_profiles`, and let `get.sh`'s `check_os` through. `tests/unit/test_preflight.py` checks that a
profile has them all. That covers this check only. What else assumes macOS: `install.sh` and the
launchd templates, `pmset` and the wake, `caffeinate` in the keep-awake, and `osascript` for alerts.

**To add an agent:** see [Agents](harnesses.md#adding-another). There are two so far, and a profile is
only the first step.

**A window can still stop at a screen nobody knew about.** So the watchdog's `stalled-start` check does
not look at the screen: a Claude that has run for five minutes without writing its session record has
not reached a session. It alerts and leaves the answer to you. bosun never answers these screens with a
keypress, since several of them default to exit, and a key sent to a screen it misread can agree to
something nobody saw.

## Testing

`bosun init` changes a real machine, so it is tested where it cannot harm one.

| Layer | What it proves | How |
|---|---|---|
| Unit tests | Recommendations, merging, quoting, refusals | `tests/unit/test_init_plan.py`, `test_init.py` |
| Smoke suite | The real command on macOS: plists through the real `plutil`, config that loads | `tests/smoke.sh`, in CI on macOS |
| Docker sandbox | A new user's first day from nothing, through a real terminal, with no network | `tests/sandbox.sh` |
| Real Mac | The real installer, launchd jobs that really fire, `pmset`, `plutil` | `tests/sandbox.sh mac`, and a GitHub macOS runner in CI |
| Real hardware | A real sleep and a real wake | `bosun wake-test` |

Docker on a Mac runs Linux, so `tests/sandbox/macos/` holds stand-ins that record what they are asked
and keep the state `bosun doctor` reads. A macOS guest cannot sleep, so only real hardware proves a wake.
How to choose and set them up: [docs/development.md](../development.md#which-sandbox-for-what).

On Codex the same first day runs in the Docker sandbox on a stand-in (`the_first_day_on_codex`), and for
real with `tests/sandbox.sh mac live codex`.

## What comes next

- Morning scenarios on a real Mac: "Monday 08:00, tmux survived, one window at a prompt, the login
  expired".
- A Homebrew tap, once there are releases to pin it to.
