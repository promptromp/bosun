# Development

`uv sync && uv run pre-commit install` once after cloning. Commits then run ruff, mypy --strict,
complexity limits (complexipy, lizard, and a shell checker in `tools/`), shellcheck, shfmt, hadolint for
the Dockerfile, actionlint and zizmor for the workflows, spelling, the links between the docs, a build of
the docs site, a secrets scan (detect-secrets) and the unit tests; pushes also run the smoke suite. A
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

## The docs site

The guides, design notes and plans in `docs/`, with `ROADMAP.md`, `CONTRIBUTING.md`, `SECURITY.md` and
`CODE_OF_CONDUCT.md`, are also a website: [promptromp.github.io/bosun](https://promptromp.github.io/bosun/),
built with [MkDocs](https://www.mkdocs.org/) and [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)
from `mkdocs.yml`. `.github/workflows/docs.yml` publishes it each time `main` changes them.

```bash
uv run mkdocs serve            # preview at http://127.0.0.1:8000, rebuilt as you edit
uv run mkdocs build --strict   # what the docs-site hook and CI run
```

Write the Markdown for GitHub, with relative links between files; `tools/mkdocs_hooks.py` makes the root
files pages and points each link at its page on the site, or at the file on GitHub when the site does not
have it. The strict build fails on a page missing from `nav` in `mkdocs.yml`, a broken link or anchor, and
a list that starts on the line after a paragraph: GitHub shows a list there, the site one paragraph, so
put a blank line before it.

The site has its own look on top of Material:

- **The theme** is `theme/` (Material's `custom_dir`): `main.html` loads the fonts, `home.html` lays out
  the home page. The type and colours are `docs/assets/site.css`. Teal is bosun at work and amber is
  what waits for you, wherever they appear; keep it so.
- **The home page** is `docs/index.md`, not `README.md`, which stays GitHub's front page (a link to it
  from the docs goes to GitHub). Its sections are HTML blocks with Markdown inside (`md_in_html`), and
  a heading's `data-toc-label` is its name in the "On this page" rail.
- **Its live examples** are drawn by `docs/assets/home/examples.js` (plain JavaScript, no build step,
  styled by `docs/assets/home/home.css`) from JSON the page carries. `tools/docs_examples.py` makes that
  JSON when the site is built, from bosun's own tables and its fictional acme data: the ledger's
  `KINDS`, the console's fixture ledger and knowledge base, and the console's map walk. So a change to
  those shows on the site, and `tests/unit/test_docs_examples.py` checks the data and that
  `docs/ledger.md`'s state diagrams match `KINDS`. `mkdocs serve` does not reload `tools/`: restart it
  after changing them.

## Which sandbox for what

**Never test an install on your own Mac.** `install.sh`, `bosun init --recommended`, `get.sh`,
`bosun update` and `bosun uninstall` change launchd, `pmset`, the install and your config for real. Use
the cheapest of these that can show what you changed:

| What you changed | Run | What it can prove | Time |
|---|---|---|---|
| Logic, config, the wizard | `tests/sandbox.sh` | bosun's logic, on a machine with nothing installed, through a real terminal | 1 minute |
| Anything else in bosun | `tests/sandbox.sh all` | also the unit tests and the smoke suite | 3 minutes |
| `get.sh`, `install.sh`, launchd, `pmset`, anything macOS does its own way | `tests/sandbox.sh mac run` | the real thing on real macOS, except sleep | 2 minutes |
| What a new Mac lacks: Homebrew, Apple's tools | `tests/sandbox.sh mac run --from-nothing` | the same from a Mac with nothing on it | 4 minutes |
| What an update does to an install in use, its windows, its ledger and knowledge base, and what real use does to them: their journals, ids, writes, privacy | `tests/sandbox.sh mac work` | a week of work through the first update's conversion and a mid-afternoon update on a year of data, on real macOS: launchd, a moving clock, racing writers, writes cut short, files changed outside the journal, going back | 16 minutes |
| How an agent behaves: launching it, its screens and files, a task handed to it, an update's prompts, which MCP servers a window reaches | `tests/sandbox.sh mac live claude`, `... live codex` | the real agents from nothing, through setup, sessions, an update and connectors | about 10 minutes and a dollar each |
| The wake itself | `bosun wake-test`, on real hardware | a real sleep and a real wake | 5 minutes |

The first also runs in CI (the second's unit and smoke suites run there natively), and so does the third on
a clean GitHub macOS runner, whenever the install could have changed (`.github/workflows/install.yml`). The sandboxes' scripts install for real, so they
refuse to run anywhere else.

### Docker: `tests/sandbox.sh`

A throwaway Linux container with a read-only copy of your checkout, and no network for the first-day
scenario. It needs Docker.

```bash
tests/sandbox.sh          # a new user's first day: the wizard, a morning, re-runs, uninstall and back
tests/sandbox.sh unit     # only the unit tests; `smoke` only the smoke suite
tests/sandbox.sh all      # the first day, the unit tests and the smoke suite
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
tests/sandbox.sh mac work                 # a week of work, through two updates: see below
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

### A week of work: `tests/sandbox.sh mac work`

`run` shows a new install. `work` shows what an update does to someone who already uses bosun, and what
real use does to the ledger and the knowledge base: on a clone of the developer's Mac it installs the
release with the piped installer, works three days of the fictional acme week
(`tests/sandbox/acme_week.sh`: review requests, tasks and their links, a workstream, outreach and its
reply, proposals approved, what I typed in a window through the hook, tickets, a history long enough to
be trimmed, prune; in the knowledge base the acme fixture imported, facts added, confirmed, rejected and
corrected, a fact I stated with my quote checked in a session's transcript, and a person deleted on
request), with the clock moved a day between them.

Then the release's windows come up (the orchestrator, three repos, the check-in loop in its checkin
window), with a plain shell opened beside them by hand, and it updates the way a user does, running
`get.sh` again. The release is a checkout from before versions side by side, so that first update converts
it, at a quiet moment. It is tried twice: first to a head whose ledger migration fails while writes are
paused (`tests/sandbox/update_patch.sh --failing`), which must put everything back (the watchdog's and
learning's launchd jobs, really unloaded meanwhile, loaded again from their plists, which stay as they
were; the check-in loop started again on the old code; the ledger byte for byte; no window touched), then
to this checkout for real. That one must swap the checkout for the link in one step on the real APFS disk
(the checkout moved whole to `bosun-versions/legacy-<its commit>`, the install path never missing while a
watcher looks at it, no temporary link left), stop and reload the jobs, end the check-in loop with a real
SIGTERM and start it again on the new code, restart each of bosun's windows in its own conversation as one
whose launch changed (the release recorded none), leave the shell I opened alone, and end with its summary.
Then the first write starts each journal from what was there (or goes on with the ledger's, when the
release journals it already): the same items, entities and facts, old ids working and new ones minted, the
key of the forgotten made and kept out of the knowledge history, nothing naming a person and none of my
words in the knowledge journal file that can leave the Mac. A person deleted on request then leaves
nothing in `local.jsonl`, in a torn file (one is made as a power loss would leave it), in the git history
or in an update's backup.

Two more days follow on the new bosun, then a mid-afternoon update to the next version (this checkout and
`tests/sandbox/update_patch.sh`: the ledger's format 2 with its migration, a line of `policy.md`, the repo
windows' hook settings), pushed to the install's origin, on a year of data that
`tests/sandbox/acme_year.py` writes through the installed bosun's own commands. The Claude stand-in
(`tests/sandbox/claude-stand-in`) behaves where bosun can see it as Claude Code does: its session record,
a transcript a restart resumes, its hooks, and the update's prompts answered with `bosun update note`.
One window is idle, one waits on a background `sleep` (`shell`), one is in a long turn (busy), and a shell
I opened sits beside them. While the update runs, a ledger write, a knowledge write, a prompt I type (the
UserPromptSubmit hook) and a Stop hook arrive every second, one of each the moment writes are paused, and
two Slack messages come in through the watcher's stand-in while bosun's own work is held. It checks that
nothing is lost (what was there, both journals and the fact log as they began, and each write and hook
record once, the typed prompts as events too), that each window is brought along as the update's table
says (the `shell` one's `sleep` started again from its stock note, the busy one waited for), that the
summary names the migration, `policy.md` and the backup, that the backup, put in a scratch folder, passes
the version before's `ledger validate --journal`, that each hook command of that version's launch settings
runs on the next, that the Slack watcher's next run catches up, and that a temporary link a switch cut
short is swept. It measures the freeze (`freeze_ms` in the update's record, under 10 s) and what restoring
the backup costs on that data (a recovering approval hook has 8 s). Then it goes back by hand, exactly as
[Going back](setup.md#going-back) says, forward again with `bosun update`, and a person forgotten after
that must leave nothing in any backup or rehearsal copy (`grep -r`).

Then writers at once (twenty in the ledger, ten in the knowledge base, then ten in each, under one replica
id), the clock going back an hour, fifty writes of each store killed like a power cut at random, from
their start to past a write's time and a rebuild's, which it measures first (the next knowledge write
must also clear the git lock such a cut leaves, so that its history records again), a
hundred approvals (they write both journal files) killed at points swept across their run and the rebuild
from the journal that a write starts with once a cut has left `ledger.json` behind it, which it counts as
torn tails, repairs and writes lost, and the knowledge files changed outside their journal: by hand, by
`git checkout` of the data files and then of the whole folder, and by the release, run from a checkout of
its own, forgetting a person that the next write must not bring back. Then a morning by launchd, the
console's knowledge view with the restricted files unreadable, an uninstall that keeps the ledger, the
knowledge folder, their journals and this Mac's replica id, and a year on, when `kg expire` expires
proposals and deletes person facts past retention. After each, `bosun ledger validate --journal` and
`bosun kg validate` must pass, and whatever a cut command printed before it died, or the journal holds,
must be there. Right after the install, it checks what git and `get.sh` print for a repository URL with a
token in it that cannot be reached: never the token.

```bash
tests/sandbox.sh mac work                   # from the release to this checkout
tests/sandbox.sh mac work --base <commit>   # from another starting point; it must be an ancestor
```

The default release is the last bosun from before versions side by side and the knowledge base's journal
(`95545b0`), installed by its own `get.sh` as a user did then: a checkout, whose ledger journals already (the first days check its journal and minted ids,
and the update that it goes on with them). Another base must be from before both too. One whose ledger
is from before the journal as well makes the first days check that the release writes no journal and
makes ids like `task:<date>/1`, and the update that the journal starts with the first write after it.

It sends two bundles of this repository: the release, and this checkout (also what is not committed) as
its descendant, the head of the branch that `get.sh`, run again, fetches into the install's `bosun.git`
from the checkout's origin (`~/repo.bundle`). It also sends the release's own `get.sh` (`~/release-get.sh`),
which installs the release as it did then, a checkout; the scenario refuses to run without it. Each later head (the failing one, the next version) is
this checkout patched in the VM and bundled over `~/repo.bundle`. The scenario is `tests/work_e2e.sh`; what
it shares with `tests/install_e2e.sh` is in `tests/sandbox/e2e_lib.sh`. It sets the clock with `sudo date`
and puts it back when it ends, also on failure.

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

Then an update with the agents at work. First, whether a running Claude picks up a changed command file of
its plugin: a Claude on the smallest model runs with `--plugin-dir` through a link, as the orchestrator does
through the install link, and its one command is asked for before and after the link is switched to another
folder, and after the file in the folder it started from changes in place. Then the repo window is given a background command
(`sleep 300`), and `bosun update` goes to the next version (this checkout and `tests/sandbox/update_patch.sh`,
bundled in the VM as the head of the install's branch). The orchestrator only re-reads `policy.md`; on Claude
Code the repo window, waiting on its background command, takes stock first, is restarted, and must start the
command again and answer with `bosun update note --status`. Codex records no background work, so the update
leaves a Codex window running, and its take-stock and update prompts are sent by hand, in the update's own
words. Each version folder must read clean while Claude runs with its plugin from it, as an update's prune and
`bosun uninstall` read one.

Then connectors ([design](design/connectors.md#what-the-vm-showed)): local stdio test servers
(`tests/sandbox/mcp_probe_server.py`, which logs what each was asked) are added to the agent's own config
(`probe-allowed`, `probe-blocked`) and to a repo of its own (`probe-project`). Without a limit the agent starts
every one of them; a `probe` window with `BOSUN_CONNECTORS="probe-allowed"` must start `probe-allowed` and
none of the others, and show only it as connected in its own `/mcp`, both when new and after a restart resumes
its session. On Codex a server named with a dot (`probe.dotted`) is then added and the window restarted, with
the same check; then a local plugin with a server of its own (`probe-plugin`, from a marketplace folder the
test makes), which a list that does not name it must keep from starting by switching every plugin off, and
a list that names it must keep. What a window reaches is read from the agent (which servers it started, its `/mcp`), never
from what its model says: a model leaves deferred tools out of a list. This part adds about three minutes and
a few cents.

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
   orchestrator and repo-session interactions, rewritten as fiction in the acme domain,
   from `tests/fixtures/corpus/`) and then end-to-end checks,
   including setup with a fake `aws`, the morning dry run, install/uninstall with fake
   `launchctl` and `pmset` under a test label, and `bosun update` between two local commits (the next
   version made by `tests/sandbox/update_patch.sh`), with fake windows brought along, and the first
   update converting a checkout. Hermetic: temp HOME, fixture sessions, fake Claude
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
