# Installing: `bosun init`

Before this, installing meant cloning, running `install.sh`, hand-editing three files (`bosun.env`
alone has over 130 settings), re-running and pasting a `sudo` line. `bosun init` replaces the
editing with what the Mac already shows, and shows its plan before it changes anything. The target
is one command, `curl ... | sh`, that ends in a short wizard: see [What comes next](#what-comes-next).

## How it works

Three parts, split the way the rest of bosun is:

| Part | File | What it does |
|---|---|---|
| Detect | `lib/init_detect.py` | Read-only: the facts. Claude Code's files are read through `lib/claude_state.py`. |
| Plan | `lib/init_plan.py` | Pure: facts to recommended answers, answers to file contents and a plan. |
| Apply | `lib/init.py` | The CLI. Writes the config with backups, trusts the folders, runs `install.sh`. |

`install.sh` stays the one place that knows launchd, `pmset` and `bosun doctor`, so `init` only
writes what `install.sh` reads. Answers are JSON (`--print-answers`), and a file only needs what it
changes. That is what makes this testable, and what the wizard will fill in.

**On its own `bosun init` changes nothing:** it prints the plan. `--recommended` or `--answers`
carries it out.

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
| Sandbox | A new user's first day from nothing, end to end, with no network | `tests/sandbox.sh` |

Docker on a Mac runs Linux, so **the sandbox cannot prove anything about launchd, a real wake or the
Keychain**. `tests/sandbox/macos/` holds stand-ins that record what they are asked and keep the state
that `bosun doctor` reads. `bosun wake-test` remains the proof of a real wake, on real hardware.

## What comes next

1. **This step:** the core, `--answers`, and the Docker sandbox.
2. The wizard (one keypress per question, a preview of each choice, `(recommended)` labels, restart
   and quit keys, as Powerlevel10k does), a `curl ... | sh` bootstrap in plain `sh` (it runs before
   Python 3.13 exists), and a job that runs it all on a clean GitHub macOS runner.
3. A real macOS VM (Tart) for what Docker cannot show, and morning scenarios: "Monday 08:00, tmux
   survived, one window at a prompt, the login expired".
