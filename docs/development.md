# Development

`uv sync && uv run pre-commit install` once after cloning. Commits then run ruff, mypy --strict,
complexity limits (complexipy, lizard, and a shell checker in `tools/`), shellcheck, shfmt, a
secrets scan (detect-secrets) and the unit tests; pushes also run the smoke suite. A
forbidden-terms check (`tools/forbidden_terms.py`) refuses staged files and commit messages that
contain a term from `~/.config/bosun/forbidden-terms.txt` (one case-insensitive regex
per line, `#` comments), a list you keep outside the repo of names that must never be committed:
your organization, products, repos, colleagues, internal hosts and ids. Without the file it
passes. `uv run pre-commit run --all-files` checks everything by hand. See `CLAUDE.md` for the
limits and conventions.

On GitHub, every pull request to `main` runs the same checks (`.github/workflows/ci.yml`), the
smoke suite on macOS and a gitleaks scan of the new commits, and `main` only takes pull requests
that pass them. For the forbidden-terms check to run there too, store your list as the
`FORBIDDEN_TERMS` Actions secret with `gh secret set FORBIDDEN_TERMS <
~/.config/bosun/forbidden-terms.txt`; the logs never print what it matched.

Issues and pull requests are welcome: see [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[code of conduct](../CODE_OF_CONDUCT.md). Report security problems privately, as
[SECURITY.md](../SECURITY.md) describes.

## Testing without waiting for the schedule

Work up the levels; each one exercises more of the real chain.

1. **Unit and integration:** `tests/smoke.sh`. It runs the Python unit tests in `tests/unit/`
   (ledger and outreach, delegate, Claude Code state, repo mask, permission guard, the watchdog's
   checks with an injected clock, learning's digest, ledger checks and proposals, and replays of
   real orchestrator and repo-session interactions, anonymized and moved to a fictional domain,
   from `tests/fixtures/corpus/`) and then end-to-end checks,
   including setup with a fake `aws`, the morning dry run, and install/uninstall with fake
   `launchctl` and `pmset` under a test label. Hermetic: temp HOME, fixture sessions, fake Claude
   processes with fake status records for the refresh, orchestrator and check-in rules, ledger
   state machines and concurrency, a fake `claude` that registers itself for `send --new` and
   `--start` (long, multi-line prompts arrive intact; failures are reported), and a fake `claude`
   for the chores dry run, and one for the learning analysis. Isolated tmux socket; no Claude
   calls, no launchd or pmset changes.
2. **Config sanity:** `bosun doctor`, then `bosun morning --dry-run`. Once the session
   exists, the dry run also prints the refresh action for each window. `bosun watchdog
   --status` and `--dry-run` show what the watchdog sees and would alert, without sending.
3. **Session start, now:** `bosun start`, then `bosun attach`. Preview the
   steady-state path with `bosun refresh --dry-run`, then run it for real.
4. **Through launchd, now:** `launchctl kickstart gui/$(id -u)/com.bosun.morning`, then
   `bosun status`.
5. **Real sleep and wake:** `bosun wake-test 3` loads a temporary launchd job due in 2 minutes,
   schedules a one-off wake in 3 minutes (the same order as production) and puts the Mac to sleep,
   retrying if a touch or notification cancels the sleep. Scheduling the wake needs
   admin rights: `sudo` in a terminal, or the macOS password dialog when there is no terminal
   (e.g. Claude Code's `!` prefix). The wake is scheduled first, so a cancelled prompt changes
   nothing. **Keep your hands off the keyboard and trackpad during the 20-second countdown**: any
   touch as the Mac goes to sleep cancels the sleep. The test detects this and tells you. Afterwards run
   `bosun wake-test --report` to see the Wake/Sleep events, the keep-awake assertion, the
   morning log and the test session's windows, then `--cleanup`. By default the test windows only
   print the `claude` command they would run. `--full` also runs setup and launches Claude for real.
6. **Orchestrator chores, no side effects:** `bosun chores morning --dry-run` (or `check-in`).
   It runs the real procedure headless against real Slack, GitHub and Confluence reads, and
   prints the plan, the ledger changes and any blocked tool calls. The safeguards: a scratch
   copy of the ledger (`--fresh` starts empty), `bosun send` only logs, and outward-facing
   tools are denied (by headless mode and by name). It uses the checkout you run it from, also
   for the `bosun` calls Claude makes, so it rehearses a branch before you install it. To
   check idempotency, run it again with `--continue`, which starts from the previous rehearsal's
   ledger. Nothing already processed should be processed again.
7. **What I say, translated:** `uv run python tests/llm/free_text.py [--runs 2] [--only O1,R4]`
   runs the real, headless Claude (the orchestrator with its policy, settings, model and effort;
   repo sessions with their real task text) on things I say in my own words ("ask Dana whether
   ...", "ping Priya again", "Marco answered, go ahead with b", and things that must not become
   outreach) and checks the resulting ledger. It runs with no MCP servers (`--with-mcp` loads
   yours, read tools only). Each run has a scratch world: scratch HOME, ledger
   and tmux server, sends only logged, outward tools denied. It costs money (about $0.3 per case
   and run, for each case in `tests/llm/cases.py`), so neither the smoke suite nor the hooks run
   it; run it after changing `policy.md`, `chores.md` or the task files.
8. **What learning finds:** `uv run python tests/llm/learn_eval.py [--runs 2]` builds a
   fictional world (acme's invoice exporter, three days) with planted problems (an overnight
   permission prompt, a repeated guard denial, a duplicate hand-over, a correction made twice, a
   missing report, an unanswered question with no nudge, an idle delegated window, drafts never
   proposed, duplicate and mis-scoped outreach) and checks that `bosun learn`'s analysis finds
   them (recall), invents nothing (precision) and proposes well-formed changes. Real Claude, no MCP
   servers; about $0.35 per run with opus. Run it after changing `orchestrator/learn.md`.
9. **Orchestrator for real:** `bosun start`, attach, and watch the `orchestrator` window run
   `/bosun:chores morning`. Nothing outward-facing happens until you approve it there or from
   your phone. `bosun chores check-in` triggers a check-in on demand.
