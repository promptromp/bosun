# Contributing to bosun

Thanks for helping. bosun is a small project, so for anything bigger than a fix, open an issue
first to agree on the approach.

## Bugs and feature requests

Use the issue templates. bosun works with your Slack, GitHub and tracker data: before you paste
logs, `bosun doctor` output or ledger items, replace names, ids, channels, repos and hosts with
placeholders. Report vulnerabilities privately as [SECURITY.md](SECURITY.md) describes, not in an
issue.

## Setting up

The [requirements](docs/setup.md#requirements) are macOS, Homebrew, tmux, jq, Python 3.13 or newer, the GitHub
CLI and Claude Code (Codex is optional); for development also [uv](https://docs.astral.sh/uv/). Fork the repository,
then:

```bash
git clone https://github.com/YOUR_USERNAME/bosun.git
cd bosun
uv sync && uv run pre-commit install
```

Commits then run ruff, mypy --strict, the complexity limits, shellcheck, shfmt, hadolint, actionlint,
zizmor, spelling and link checks, a build of the [docs site](docs/development.md#the-docs-site), the
secrets scans and the unit tests the change can reach; pushes also run the smoke suite (unless this exact tree
passed it here in the last 24 hours).

## Making a change

[CLAUDE.md](CLAUDE.md) is the maintainer's guide, for people and coding agents alike: where things
live, a checklist for each kind of change (a command, a config key, a ledger kind, a watchdog
check and more), the limits and the gotchas (macOS ships bash 3.2; tmux has its own). Read the
parts your change touches.

- **Tests.** Add a unit test in `tests/unit/` for a change to `lib/*.py`, and a check in
  `tests/smoke.sh` for a command's behaviour. Before you push, run `uv run pytest -n auto` (every unit
  test, in parallel), `tests/smoke.sh` (end to end, hermetic: its own HOME, tmux socket and a fake
  agent; while iterating, `tests/smoke.sh --only <id>` runs one area, `--list` shows them) and `uv run pre-commit run --all-files`; [Running the tests](docs/development.md#running-the-tests)
  says what runs when. Never test an install on your own Mac:
  [the sandboxes](docs/development.md#which-sandbox-for-what) are throwaway machines.
- **Wording that Claude acts on** (`orchestrator/policy.md`, the task files, the prompts) is
  tested against the real Claude by `tests/llm/`. Those runs cost money, so CI does not run them:
  say in the pull request that you changed such wording, and they will be run before merging.
- **Placeholders only.** No real names of people, organizations, products, repos or channels, and
  no real ids, emails or tokens, in code, tests, fixtures, docs or commit messages: use `acme`,
  `example.com` and zero-filled Slack ids. Keep your own list of terms that must never be
  committed in `~/.config/bosun/forbidden-terms.txt`, and the forbidden-terms hook refuses them.
- **Limits.** When a complexity limit trips, split the function rather than raise the limit.

## Pull requests

Open them against `main`, one topic each, saying what changed, why, and how you tested it. CI runs
the hooks, the unit tests on Python 3.13 and 3.14, the menu bar app's build and tests (quick when `app/` is
untouched), gitleaks and CodeQL (the macOS smoke suite is off until the MVP; the pre-push hook still runs it);
`main` takes a pull request once those pass and its review threads are resolved, through the merge
queue: `gh pr merge --squash` (or the Merge button) adds it to the queue, which runs the checks again
on the pull request on top of the latest `main` and merges it (squashed) when they pass. You never
have to update your branch to `main` for that.

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## License

By contributing to bosun, you agree that your contributions will be licensed under the same
license as the project.
