# Development

`uv sync && uv run pre-commit install` once after cloning. Commits then run ruff, mypy --strict,
complexity limits (complexipy, lizard, and a shell checker in `tools/`), shellcheck, shfmt, hadolint for
the Dockerfile, actionlint and zizmor for the workflows, spelling, the links between the docs, a secrets
scan (detect-secrets) and the unit tests; pushes also run the smoke suite. A
forbidden-terms check (`tools/forbidden_terms.py`) refuses staged files and commit messages that
contain a term from `~/.config/bosun/forbidden-terms.txt` (one case-insensitive regex
per line, `#` comments), a list you keep outside the repo of names that must never be committed:
your organization, products, repos, colleagues, internal hosts and ids. Without the file it
passes. `uv run pre-commit run --all-files` checks everything by hand. See `CLAUDE.md` for the
limits and conventions.

On GitHub, every pull request to `main` runs the same checks (`.github/workflows/ci.yml`), the
smoke suite on macOS and a gitleaks scan of the new commits, and `main` only takes pull requests
that pass them. The unit tests there also keep a floor under coverage. For the forbidden-terms check to run there too, store your list as the
`FORBIDDEN_TERMS` Actions secret with `gh secret set FORBIDDEN_TERMS <
~/.config/bosun/forbidden-terms.txt`; the logs never print what it matched.

Issues and pull requests are welcome: see [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[code of conduct](../CODE_OF_CONDUCT.md). Report security problems privately, as
[SECURITY.md](../SECURITY.md) describes.

## Which sandbox for what

**Never test an install on your own Mac.** `install.sh`, `bosun init --recommended` and `get.sh` change
launchd, `pmset` and your config for real. Use the cheapest of these that can show what you changed:

| What you changed | Run | What it can prove | Time |
|---|---|---|---|
| Logic, config, the wizard | `tests/sandbox.sh` | bosun's logic, on a machine with nothing installed, through a real terminal | 1 minute |
| Anything else in bosun | `tests/sandbox.sh all` | also the unit tests and the smoke suite | 3 minutes |
| `get.sh`, `install.sh`, launchd, `pmset`, anything macOS does its own way | `tests/sandbox.sh mac run` | the real thing on real macOS, except sleep | 75 seconds |
| What a new Mac lacks: Homebrew, Apple's tools | `tests/sandbox.sh mac run --from-nothing` | the same from a Mac with nothing on it | 4 minutes |
| How an agent behaves: launching it, its screens and files, a task handed to it | `tests/sandbox.sh mac live claude`, `... live codex` | the real agents from nothing, through setup and sessions | 10 minutes, about a dollar |
| The wake itself | `bosun wake-test`, on real hardware | a real sleep and a real wake | 5 minutes |

The first two also run in CI, and so does the third on a clean GitHub macOS runner, whenever the install
could have changed (`.github/workflows/install.yml`). The sandboxes' scripts install for real, so they
refuse to run anywhere else.

### Docker: `tests/sandbox.sh`

A throwaway Linux container with a read-only copy of your checkout, and no network for the first-day
scenario. It needs Docker.

```bash
tests/sandbox.sh          # a new user's first day: the wizard, a morning, re-runs, uninstall
tests/sandbox.sh all      # also the unit tests and the smoke suite
tests/sandbox.sh shell    # that new user's machine, to try things by hand; it goes away on exit
```

Docker on a Mac is Linux, so `launchctl`, `pmset`, `caffeinate` and the like are stand-ins
(`tests/sandbox/macos/`). It proves bosun's logic, never launchd, a real wake or the Keychain.

### A real Mac: `tests/sandbox.sh mac`

macOS in a [Tart](https://tart.run) VM on your Mac (Apple silicon). A run clones a base image, boots it,
runs `tests/install_e2e.sh` in it and deletes the clone: the real installer, real launchd jobs that really
fire, the real `pmset`, `plutil` and bash 3.2.

```bash
brew install openai/tools/tart
tests/sandbox.sh mac setup                # once: pulls the image (6 minutes) and prepares the bases (4)
tests/sandbox.sh mac run                  # on a developer's Mac: Homebrew is already there
tests/sandbox.sh mac run --from-nothing   # on a new Mac: no Homebrew, no Apple tools
tests/sandbox.sh mac live codex           # the real agents: see below
tests/sandbox.sh mac shell                # a window, to try the wizard as a new user would
tests/sandbox.sh mac clean                # delete the base images (`tart prune` removes the pulled one)
```

If Homebrew refuses the tap, it wants to be told you trust it. These two are enough, not the whole tap:
`brew trust --formula openai/tools/tart openai/tools/softnet`.

- **It costs disk once.** The image is about 29 GB to download and takes about 33 GB; a clone takes no
  time and no space until it writes, so keep 10 GB or so free for a run. `du` double-counts clones:
  trust `df`.
- **It is a stranger's Mac, not yours.** The login is `admin`, password `admin`, with passwordless `sudo`
  and ssh on. Never put a real token in one by hand, and never push an image you logged in to. `live` is the
  one exception, and how it handles its keys is below.
- **It cannot sleep.** `pmset sleepnow` fails in a macOS guest (`error 0xe00002e2`), and `tart suspend` is
  no stand-in: launchd did not catch up on a job that fell due while the VM was frozen. To check a job's
  *time*, move the clock past it instead (`sudo systemsetup -setusingnetworktime off`, then `sudo date`):
  launchd fires on time. Only `bosun wake-test` on real hardware proves the wake.
- **It only runs here.** GitHub's macOS runners cannot nest a macOS VM, so CI uses the runner itself.
- **Wait for the login.** ssh answers before the auto-login finishes, and until then launchd has no
  `gui/<uid>` domain and `launchctl bootstrap` fails with error 125. `mac.sh` waits for it; do the same
  if you script a VM by hand.

### The real agents: `tests/sandbox.sh mac live`

The stand-ins only behave the way you already believed. Every surprising thing about Codex in
[Agents](design/harnesses.md#what-codex-needed) was found by running the real one, and so was the first-run
stall on Claude Code. So after a change in how bosun talks to an agent, or an update of one, run this:

```bash
tests/sandbox.sh mac live claude
tests/sandbox.sh mac live codex
BOSUN_SANDBOX_CODEX_MODEL=<a cheap model> tests/sandbox.sh mac live codex
```

On a clone of the developer's Mac it does what a new user does, following only what the installer prints:
it stops for what is missing, runs the line it gave, logs in, goes through Claude Code's first screens, and
installs. Then real windows, a restart that must come back on the same session, and a real task: the real
orchestrator hands a fix to the real repo session, which must report to the ledger, push a branch whose
tests pass and open a pull request, and the orchestrator must link it. `live codex` runs the repo window on
Codex and the orchestrator on Claude Code. GitHub is a stand-in and the remote a local bare repo, so nothing
leaves the Mac but requests to the agents' APIs. It is not in CI, because it costs money and needs keys.

**The keys.** It reads two API keys from your Keychain, one for Anthropic, and for `live codex` one for OpenAI.
Use keys made for this, with a spend limit. Copy each to the clipboard and store it like this, because
`security add-generic-password -w` with no value silently cuts what you type at 128 characters and a key is
longer. It also keeps the key out of the process list, and clears the clipboard:

```bash
printf 'add-generic-password -U -a "%s" -s bosun-sandbox-anthropic -w "%s"\n' "$USER" "$(pbpaste)" | security -i; pbcopy </dev/null
printf 'add-generic-password -U -a "%s" -s bosun-sandbox-openai -w "%s"\n' "$USER" "$(pbpaste)" | security -i; pbcopy </dev/null
```

A key only ever goes through a pipe: never a variable, an argument or the screen. In the clone it is a `0600`
file that `apiKeyHelper` reads, or Codex's own login. What comes back through ssh has anything shaped like a
key masked, and the clone is deleted at the end, even on failure. The clone holds no ssh key, no GitHub
token and no shared folder of yours, and an image with a key in it is never saved.

Tart is OpenAI's since it acquired Cirrus Labs in 2026. The repository's licence is FSL-1.1-ALv2, which
allows internal use and becomes Apache 2.0 after two years (the licensing page on tart.run still shows the
older terms). It is only a test tool here: nothing in bosun needs it or ships it.

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
