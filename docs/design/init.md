# Installing: `bosun init`

Installing used to mean cloning, running `install.sh`, hand-editing three files (`bosun.env` alone has
over 130 settings), re-running and pasting a `sudo` line. Now it is one command, `curl ... | sh`, which
ends in a short wizard that starts from what the Mac already shows and shows its plan before it changes
anything.

## How it works

| Part | File | What it does |
|---|---|---|
| Bootstrap | `get.sh` | Plain `sh`, because it runs before anything is installed: Homebrew and what is missing, then bosun itself. |
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
  a login is only offered for a tool that is installed and logged out.
- Re-runnable, starting from your config, and usable without a terminal (Powerlevel10k has no such
  mode, and it is what lets everything here be tested).

`get.sh` follows oh-my-zsh: one line, an unattended mode, plain `sh`. Behind a pipe its standard input is
the script, so it hands `bosun init` the terminal from `/dev/tty`. All of it is in functions with `main`
last, so a download cut short does nothing.

## What it recommends

On a first run your own history beats a generic default. A re-run starts from your config instead.

| Answer | From |
|---|---|
| Repos | The ones you typed the most Claude Code prompts in over 30 days (20 or more, at most 6). Worktrees count for their main checkout; scripted runs (`claude -p`) do not count. With no history, up to 3 git repos from the usual folders, newest first. |
| Days | Weekdays you worked on at least 60% of since your first prompt. |
| Hours | Half an hour before your median first prompt, to after your median last one, at least 4 hours. Work before 04:00 belongs to the day before. With under 5 active days: 08:00 to 20:00, Monday to Friday. |
| Features | Events, approvals and learning on. The Slack watcher only if the Slack plugin is installed. |
| Model | `opus` on Max, Team and Enterprise plans, else `sonnet`. |
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

The plan also says that repo sessions run with `--dangerously-skip-permissions`: that is the
default that lets an unattended session keep going, and you should know about it on day one.

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

## What comes next

- Morning scenarios on a real Mac: "Monday 08:00, tmux survived, one window at a prompt, the login
  expired".
- A Homebrew tap, once there are releases to pin it to.
