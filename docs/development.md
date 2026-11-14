# Development

`uv sync && uv run pre-commit install` once after cloning. Commits then run ruff, mypy --strict,
complexity limits (complexipy, lizard, and a shell checker in `tools/`), shellcheck, shfmt, hadolint for
the Dockerfile, actionlint and zizmor for the workflows, spelling, the links between the docs, a build of
the docs site, a secrets scan (detect-secrets) and the unit tests the change can reach; pushes also run
the smoke suite ([Running the tests](#running-the-tests)). A
forbidden-terms check (`tools/forbidden_terms.py`) refuses staged files and commit messages that
contain a term from `~/.config/bosun/forbidden-terms.txt` (one case-insensitive regex
per line, `#` comments), a list you keep outside the repo of names that must never be committed:
your organization, products, repos, colleagues, internal hosts and ids. Without the file it
passes. `uv run pre-commit run --all-files` checks everything by hand. See `CLAUDE.md` for the
limits and conventions.

On GitHub, every pull request to `main` runs the same checks (`.github/workflows/ci.yml`) and
a gitleaks scan of the new commits (the smoke suite on macOS is off in CI until the feature-complete
MVP; the pre-push hook still runs the sections the change can reach), and `main` only takes pull requests
that pass them, through GitHub's merge queue: once a pull request's checks pass, `gh pr merge --squash`
(or the Merge button) adds it to the queue, which builds it on top of the latest `main` and the pull
requests ahead of it, runs the same checks again on that commit (CI listens for `merge_group` as well
as `pull_request`) and merges it when they pass, so nobody updates a branch by hand. Dependabot's patch
and minor updates of the GitHub Actions, the sandbox's Docker base image, the pre-commit hooks and the
uv development dependencies get auto-merge turned on by `.github/workflows/dependabot-auto-merge.yml`,
which only adds them to the queue once their checks pass; majors and production dependencies wait for
a person. CI runs every unit test on Linux (Python 3.13 and 3.14; on macOS too once the smoke job is back), and
keeps a floor under their coverage, measured through `sys.monitoring` (`COVERAGE_CORE=sysmon`) because 3.13's
default, `sys.settrace`, made every test about a quarter slower. The menu bar app has its own job, `app`: when a pull request or merge group changes nothing under `app/` (or the two tools that feed it, `tools/contract_samples.py` and `tools/swift_coverage.py`, the workflow, `setup-uv`, `uv.lock` or `pyproject.toml`), it is skipped (a cheap Linux job, `changes`, looks first, so no macOS runner starts, and a skipped job counts as passing for the required check); otherwise it runs the Swift tests with warnings as errors, the coverage floor, SwiftLint, `swift format lint --strict` and an ad hoc signed build. For the forbidden-terms check to run there too, store your list as the
`FORBIDDEN_TERMS` Actions secret with `gh secret set FORBIDDEN_TERMS <
~/.config/bosun/forbidden-terms.txt`; the logs never print what it matched.

Issues and pull requests are welcome: see [CONTRIBUTING.md](../CONTRIBUTING.md) and the
[code of conduct](../CODE_OF_CONDUCT.md). Report security problems privately, as
[SECURITY.md](../SECURITY.md) describes.

## Running the tests

| What | Command | When it runs by itself | Time on a 12-core Mac |
|---|---|---|---|
| The unit tests a change can reach | `uv run pytest -n auto --impacted` | every commit (the `unit-tests` hook) | seconds to a minute |
| Every unit test | `uv run pytest -n auto` | CI, on Linux (with coverage) and on macOS | about a minute |
| The end-to-end smoke suite | `tests/smoke.sh` | before every push, but only the sections the change can reach (`--pre-push`, the build-out setting: skipped if this exact tree already passed a whole run here in the last 24 hours); CI's macOS job is off until the MVP | about 18 minutes in full |
| The sections a change reaches | `tests/smoke.sh --changed <git ref>` | by the pre-push hook, against `origin/main` | what those sections take |
| Every unit test with a plain `python3`, then the smoke suite | `tests/smoke.sh --with-unit` | never | 8 more minutes |
| One area of the smoke suite | `tests/smoke.sh --only <id>[,<id>...]` (ids: `tests/smoke.sh --list`) | never | seconds to a few minutes |
| Only the sections that failed last time | `tests/smoke.sh --last-failed` (`--lf`) | never | what those sections take |
| Stop at the first failing check | `tests/smoke.sh -x` (`--exitfirst`), with any of the above | never | up to that check |

The smoke suite is in sections, each begun by a `section <id> "<name>"` line. `--list` prints them. `--only`
runs the shared setup and the sections you name, plus any they `needs` (the list shows those), in the order of
the file, and its summary says it was a partial run: it is for iterating on the area you changed, and never
instead of the whole suite before you commit; CI (when its smoke job is on) runs the whole suite, and the pre-push hook the sections the change reaches (below). `--with-unit` and
`--only` combine.

To cut the waiting while you iterate: `-x` stops at the first failing check, after its `FAIL` line, and the
summary says in which section and which check; the run's temporary folders and tmux servers are cleaned up as
always, and the exit status is 1. `--last-failed` reruns only the sections whose last result was a failure,
and what they `needs`, through the same machinery as `--only` (it says which it reran). Every run records each
section it ran as pass or fail in `tests/.smoke-cache/sections`, a git-ignored file in your checkout, and a
partial run changes only the sections it ran, so a section you did not run keeps its earlier result, as in
pytest's cache. With none recorded as failed, `--last-failed` says so in one line and runs nothing (exit 0).
It combines with `-x` and `--with-unit`, not with `--only`. The record is best effort: a run never fails over
it. The loop is `--only` or `--last-failed`, with `-x`, then one full run with none of them before you
commit.

The pre-push hook runs `tests/smoke.sh --pre-push`, which skips the suite, with a line saying so, when the tree
of the commit you push (pre-commit's `PRE_COMMIT_TO_REF`) already passed a whole run in this checkout less than
24 hours ago. Otherwise it runs only the sections the change can reach, which is the build-out setting: a push
need not wait for the whole 18 minutes, and the whole suite on every push comes back once the project is past
its release (CI runs it again at the MVP, when its smoke job is turned on with the repository variable
`CI_SMOKE`). The choice is described in [Which sections a push runs](#which-sections-a-push-runs) below. A whole run (none of `--only`, `--last-failed` or `-x`) that ends with no failure records, in
`tests/.smoke-cache/passes`, the tree it tested: the files on disk before the first section runs, uncommitted
changes included (what `git add -A` stages, taken in a copy of the index, so yours is not touched). It records
nothing unless the tree is the same at the end of the run: a test that writes a file into the repository, or
changes a tracked one, is a bug in the test, and the run names the file instead of quietly defeating the skip.
Git-ignored paths (caches, `__pycache__`, `tests/.smoke-cache/`) never count. An untracked file that is not
ignored counts at the start, on purpose: it may have influenced the run, the commit you push lacks it, so its
tree differs and the whole suite runs. In doubt the suite runs: another tree, a record older than a day, a
partial or failed run, an unreadable record, git failing; the record can make a run longer, never a failing
tree pass. `BOSUN_SMOKE_NO_CACHE=1 git push` makes the hook ignore the record and run the sections the change reaches (this is the knob, not
pre-commit's `SKIP`, which this repository does not use). With several refs in one push pre-commit hands the
hook only the first one that is not a deletion, and a branch deletion runs nothing. CI never uses the record:
when its smoke job runs, it runs `tests/smoke.sh` in full.

### Which sections a push runs

`tests/smoke.sh --changed <git ref>` works out the files that changed since the merge-base of that ref and
`HEAD`: committed since, staged, unstaged and untracked (not ignored) ones, since what is tested is the tree on
disk. `--pre-push` does this against `origin/main` (`BOSUN_SMOKE_BASE` names another ref), after the tree check
above. The answer is one of three, printed with the files that led to it:

- **the whole suite**, when any changed file is on the select-everything list, is used by `lib/common.sh` (which
  every command sources: the modules it names under `lib/`, and what imports those), or is covered by no section
  and no `ignore` rule (a file nothing says is safe), when `origin/main` or the merge-base cannot be found, or when
  the selection itself fails for any reason;
- **nothing**, when every changed file is ignored (the docs, which their own hooks check; the settings of the
  checks themselves; the unit and sandbox suites), saying so in one line;
- **the sections that cover a changed file**, plus the ones they `needs`, plus the shared setup and the
  `always_run` parts, as `--only` runs them.

A section says what it runs or reads in a clause on its line: `section app "bosun app ..." covers
'libexec/bosun-app,lib/app_*.py,templates/launchd-app.plist'`. A glob is matched against the path from the
repository root, whole: `*` stays in a folder, `**` goes through any number of them. What depends on a covered
file is followed by `tools/smoke_select.py`: a module that imports a changed module, and the commands and
scripts that run a changed file (one step; a script that names `lib/ledger.py`, or runs `bosun ledger`). So a
section lists the files it runs directly, and a change to a library module reaches the section through the
command that runs it. `tests/smoke_selection.txt` holds the two lists that need no section: `all <glob>` (the
whole suite: `lib/common.sh`, `bin/bosun`, the suite itself and its stand-ins, what every window's launch loads)
and `ignore <glob>`. `tests/unit/test_smoke_selection.py` fails on a file that is none of those, naming it and
saying what to add; the same for a section without `covers`.

`BOSUN_SMOKE_FULL=1 git push` forces the whole suite (even for a tree that passed it: the pass record is not
consulted then either; `BOSUN_SMOKE_NO_CACHE=1` only ignores the record, and still runs just the sections the change reaches). A partial run never records the tree as passed: only a whole one does.

A section either sets up what it uses itself or says `needs <id>`, and
`tests/unit/test_smoke_sections.py` fails on a section without a marker, a taken id or a `needs` that names
nothing above it. Every section begins by checking that the tools that reach outside the run
(`osascript`, `aws`, `launchctl`, `pmset`, `sudo`, `open`, `caffeinate`, `codesign`, `ditto`, `spctl`) are
stand-ins in the run's own folder, and the run stops if one would be the real one. The run's HOME and those
stand-ins exist before the first section, and a section that points bosun at another HOME gets them there too.

Suites of several checkouts (git worktrees, parallel agents) can run on one Mac at the same time. A run keeps its
HOME, state, config, tmux sockets (`bosun-smoke-<pid>`), stand-ins and the install under test in its own temp folder;
`tests/unit/test_smoke_state.py` checks one thing about that: no script the suite runs (`tests/smoke.sh`, the update
patch, the stand-ins) names a `/tmp/<name>` path of its own. A shared port, socket, label or file under the real home is
not checked, only avoided by construction. What the runs cannot help sharing is the drive: macOS's `F_FULLFSYNC`,
which the journal asks for on every write, waits for the whole drive's cache, and with three writers beside it the
update section took five times as long to reach the step its migration wait is for (those waits are 120 s).
So the suite sets `BOSUN_PLAIN_FSYNC=1`, and `lib/journal.py` then flushes with a plain `fsync`, as the unit tests do.
It is for tests only: with it set, a write to either store can be lost to a power cut, so never set it in real use;
`bosun doctor` warns while it is set, and the Mac VM's runs and every real run keep the full flush.

`-n auto` runs the tests in parallel, one worker per core (pytest-xdist). `--impacted`
(pytest-impacted) runs only the tests that what git has uncommitted (staged or not, and new files)
can reach, so `uv run pre-commit run --all-files` selects the same tests as the commit. The plugin
follows the Python imports from `lib/` (an import root, like pytest's `pythonpath`) and between the
test files and the helpers of `tests/unit`; what it cannot see (a test that runs
`bin/bosun`, reads a docs page, or a file `bosun` reads as it runs) is a rule in `pyproject.toml`'s
`impacted_map` and `impacted_invalidate_all`. A rule may select more tests than it needs, never fewer:
when unsure, it runs every test (`conftest.py`, the fixtures and what bosun reads as it runs do). A
rule's `**` matches any depth, as the plugin reads it. Two checks keep the rules honest: `tests/unit/test_impacted_rules.py`
runs in every selection and fails on a file no rule covers or a module whose importers would not be
selected, and `tests/unit/impacted_guard.py` fails any test that reads or runs a file whose change
would not select it, naming the line to add. `uv run impacted-tests --verbose` prints what the
current changes select, without running it.

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
  from the docs goes to GitHub). Its masthead is two generated pictures, by day and by night;
  `docs/assets/home/masthead.txt` says how they were made. Its sections are HTML blocks with Markdown inside (`md_in_html`), and
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
| What the menu bar app (`bosun.app`) does: every state of its menu, every dialog and choice of its Quit and update, Open at Login across a restart, one copy at a time, what macOS says to a quit by Apple Event | `tests/sandbox.sh mac app` | the real app, built here and driven by its test hooks on a clone's desktop session, with launchd, a reboot and the Trash; `--from release` the published, notarized one | about 30 minutes |
| What an update does to an install in use, its windows, its ledger and knowledge base, and what real use does to them: their journals, ids, writes, privacy | `tests/sandbox.sh mac work` | a week of work through the first update's conversion and a mid-afternoon update on a year of data, on real macOS: launchd, a moving clock, racing writers, writes cut short, files changed outside the journal, going back | 16 minutes |
| How an agent behaves: launching it, its screens and files, a task handed to it, an update's prompts, which MCP servers a window reaches | `tests/sandbox.sh mac live claude`, `... live codex` | the real agents from nothing, through setup, sessions, an update and connectors | about 10 minutes and a dollar each |
| How Antigravity (`agy`) behaves under bosun: its hooks and plugin, its screen, its dialogs, its login | `tests/sandbox.sh mac live agy` | the real `agy` in a VM with a window, where you sign in; then windows started by launchd, Esc, a reviewer's dialog, the plugin's approvals, a slow hook, connectors, uninstall | about 25 minutes, you at the start, and a personal account's free quota |
| The wake itself | `bosun wake-test`, on real hardware | a real sleep and a real wake | 5 minutes |

The first also runs in CI (the second's unit and smoke suites run there natively, once the smoke job is back), and so does the third on
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
fire (the morning job, and the one `bosun morning --now --background` kickstarts, whose tmux server must have a
responsible process that is not the caller's: `launchctl procinfo`, which needs root there, so the test tries
`sudo -n`), the console's job (`BOSUN_CONSOLE_ALWAYS=1`: started by `bosun start`'s marker, no process after a
`bosun quit`), `bosun quit` with what the watchdog and the morning job do after it, `bosun update` from one release
to the next (the install is on the default `releases` channel, its origin `~/origin.git` a repository with a release
tag, and the next release is tagged there by the test), the real `pmset`, `plutil` and bash 3.2. A Tart VM has
Gatekeeper's assessments off, which the test turns on for its `spctl` check.

```bash
brew install openai/tools/tart
tests/sandbox.sh mac setup                # once: pulls the image (6 minutes) and prepares the bases (4)
tests/sandbox.sh mac run                  # on a developer's Mac: Homebrew is already there
tests/sandbox.sh mac run --from-nothing   # on a new Mac: no Homebrew, no Apple tools
tests/sandbox.sh mac work                 # a week of work, through two updates: see below
tests/sandbox.sh mac app                  # bosun.app driven through every state and choice, and a restart
tests/sandbox.sh mac live codex           # the real agents: see below
tests/sandbox.sh mac live agy             # Antigravity: you sign in in the VM's window, see below
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

### The menu bar app: `tests/sandbox.sh mac app [--from build|release]`

`tests/app_e2e.sh` in a clone with its desktop session (the status item needs one; a clone started with
`--no-graphics` still has it: the auto-login, the window server, the Dock and Finder). The app is started with
`--test-hooks <dir>`, so it writes its menu to `menu.json` after each render, takes clicks and answers to its dialogs
from `actions/<n>.json`, and logs each command it runs and each address it would open to `log.jsonl`; the checks
read those, `bosun status --json`, `ps` and `launchctl`. It runs: the menu green with the windows, yellow with a
problem and back, Open Console, an update to a newer release tagged in the clone's origin (offer, plan, the
update waiting for a busy agent, done), Quit with the defaults, the app opened again starting bosun, Quit with a
busy agent (Let Them Finish with a 0-minute wait, then Stop It Now), Quit keeping the icon and Start, a second copy
exiting, Open at Login, a restart of the Mac with the app opened by its login job (`--at-login`, bosun not started),
the app moved to the Trash (the watchdog's `app-missing`) and an uninstall that leaves nothing named bosun.
It also asks the app to quit with an Apple Event, as `bosun app update` and `bosun app remove` do, from the login
and from an update the app started, and reports whether macOS prompts for Automation.

`--from build` (the default) builds the app here with `app/build.sh --identity -` (nothing is signed with a real
identity, and the app is never started on your Mac) and copies it in; it is placed in `~/Applications` as
`bosun app install` would, since `bosun init` cannot offer an app that is not the publisher's. `--from release` has
`bosun init` install the published, notarized app and checks Gatekeeper accepts it: it needs a release and a Team ID
in `app/publisher.json`.

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

**`live agy`** is different in how it starts. Antigravity's login is a browser sign-in whose result is a
Keychain item that `agy` reads only in the desktop session, never over ssh. So the clone boots with its
window (not headless), Google's own installer puts `agy` in it (`curl -fsSL
https://antigravity.google/cli/install.sh | bash`), and the run stops and tells you what to do: open Terminal
in the VM's window, run `~/.local/bin/agy`, sign in with Google (a personal account's free quota), go through
its first-run screens and quit it. It then looks every 10 seconds, for up to 15 minutes, for the Keychain item
(asked for without reading it) and `onboardingComplete`, and goes on by itself. Your sign-in stays in the
clone, which is deleted at the end, and no image is ever saved with it. The Anthropic key goes in as for the
others, since the orchestrator stays on Claude Code; no OpenAI key.

What it then checks (`tests/sandbox/live_agy.sh`) is the Testing list of the
[design note](design/antigravity.md#testing): windows started by bosun's launchd job in the desktop session,
never by `bosun start` over ssh (the installer's and doctor's checks go through `launchctl asuser` for the
same reason), named, busy then idle from the hooks, refreshed and resumed on their conversation; Esc in a busy
window reading idle with no hook; a push to `main` stopping at `agy`'s own dialog, read as `waiting`, which
`bosun send` refuses; `--remote-control` and `SSH_AUTH_SOCK` not stopping the window; the plugin's approvals
(a feature branch's push and a `gh` command of its list answered with `BypassSandbox`, an `ls` with nothing),
read from a copy of the plugin that records each hook's payload and answer (`tests/sandbox/live/agy-hook-record`,
installed for those parts only, since macOS 27.2 shows no process's environment to `ps`); a hook slower than
its timeout; connectors with none and with a dummy server; the morning's `agy update`; and `bosun uninstall`,
after which the file tree of `~/.gemini` may differ from before only by `agy`'s conversations and logs. A few
facts are recorded, not judged, and printed at the end (what `SessionStart` carries, the footer right after a
hook, whether `run_command` has a working-directory argument, what the sandbox lets a nested `.git` do,
whether `safe.bareRepository=explicit` and the window's git configuration hold). It costs a few cents of the
Anthropic key (the orchestrator's start) and the Google account's free quota.

**The keys.** It reads two API keys from your Keychain, one for Anthropic, and for `live codex` one for OpenAI
(`live agy` needs only the Anthropic one; the sign-in is its own).
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

## Building the app

The menu bar app is a Swift package in `app/`, so it needs a Swift toolchain (Xcode's or the command line tools').
`app/build.sh --identity - --out <dir>` builds `bosun.app` (ad hoc signed, which is for developing the app only) and
zips it; `bosun app install --build` runs it and installs the result. The commit hooks run `swift format lint`,
SwiftLint and `swift test` with warnings as errors, only for a commit that touches `app/`, and skip with one line
off a Mac (the app is AppKit; CI's Linux `lint` job has a `swift` that cannot build it) or without Swift; CI's macOS
`app` job runs them, the coverage floor (`tools/swift_coverage.py`) and an ad hoc build. SwiftPM keeps its dependencies' clones and
downloads in one cache for the whole Mac, which the hooks of two checkouts running at once twice failed
on (`'swiftlintplugins': Couldn't check out revision`; a rerun passed), so `tools/swift_hook.sh` runs `swift package resolve` holding a
lock beside that cache (retried once, only for that checkout error) and the check then finds its dependencies in the
checkout's own `.build`; a warm checkout pays about a third of a second. CI's `app` job runs `swift` directly, on a runner with a cache of its own. While
iterating, `cd app && swift test --filter <suite>`. The app talks to bosun only through its `--json` commands, so a
change to what one prints needs `uv run python tools/contract_samples.py` to write the fixtures the Swift tests
decode. What the real app does on a real desktop is `tests/sandbox.sh mac app` (see
[Which sandbox for what](#which-sandbox-for-what)).

## Releasing bosun.app

The menu bar app is released apart from bosun: a tag `app-v<version>` on `main`, where `<version>` is what
`app/VERSION` says, starts `.github/workflows/release-app.yml`. It builds the app, signs it with your Developer
ID in a keychain that exists for that run only, has Apple notarize it, staples the ticket, and attaches
`bosun-app-<version>.zip` to a GitHub release. Before it publishes anything it puts the final zip through the install's own code
(`lib/app_install.py verify-archive`: the archive's entries, the code requirement, Gatekeeper's `source=Notarized
Developer ID` and the bundle id), so a release the install would refuse never goes out. You need an Apple Developer Program membership, a
*Developer ID Application* certificate and an App Store Connect API key (Users and Access, Integrations, with the
Developer role). The values below come from your own files and your own terminal: never paste a certificate, a
key or a password into a chat, an issue or a pull request.

Once, per repository. `<owner>/<repo>` is where you publish. A workflow run's logs are public, and the workflow prints none of these values.

**A. From Apple.** About ten minutes. If another app of yours already signs this way, reuse its `.p12` and `.p8` and skip steps 2 and 3.

1. **Find the certificate and the Team ID.** Run:

    ```sh
    security find-identity -v -p codesigning
    ```

    Look for `"Developer ID Application: <name> (<team id>)"`. The whole quoted text is `MACOS_SIGN_IDENTITY`, and the ten characters in the parentheses are the Team ID. If there is no such line, create the certificate:

   1. In Keychain Access, open Certificate Assistant › Request a Certificate From a Certificate Authority. Choose "Saved to disk".
   2. On developer.apple.com, open Certificates, add a **Developer ID Application** certificate, and upload that request.
   3. Download the `.cer`, double-click it, and run the command again.

2. **Export the certificate with its private key as a `.p12`.**

   1. In Keychain Access, open the login keychain and choose My Certificates.
   2. Expand the "Developer ID Application" entry to check that a private key sits under it.
   3. Choose Export, then Personal Information Exchange (.p12).
   4. Choose an export password. It becomes `MACOS_CERTIFICATE_PASSWORD`.

3. **Create an App Store Connect API key for notarization.**

   1. In App Store Connect, open Users and Access › Integrations › App Store Connect API › Team Keys, and press +.
   2. Give the key the Developer access.
   3. Download `AuthKey_<key id>.p8`. Apple lets you download it only once.
   4. Note the key's id (`APPLE_API_KEY_ID`) and the Issuer ID at the top of the page (`APPLE_API_ISSUER_ID`).

**B. On GitHub.** About three minutes, with `gh` signed in to an account that administers the repository.

1. **Create the `release` environment.** Only release runs can read its secrets, and only after you approve each run:

    ```sh
    ME=$(gh api user --jq .id)
    gh api -X PUT repos/<owner>/<repo>/environments/release --input - <<EOF
    {"reviewers":[{"type":"User","id":$ME}],"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
    EOF
    gh api -X POST repos/<owner>/<repo>/environments/release/deployment-branch-policies -f name='app-v*' -f type=tag
    ```

    The required reviewer is the real control. A tag on a commit that is not on `main` runs that commit's copy of the workflow. The workflow stops such a tag, but only in a step of that same copy, so read what you approve.

2. **Protect the tags.** Only administrators may create `app-v*` tags, and nobody may move or delete them:

    ```sh
    gh api -X POST repos/<owner>/<repo>/rulesets --input - <<'EOF'
    {"name":"app release tags","target":"tag","enforcement":"active",
     "conditions":{"ref_name":{"include":["refs/tags/app-v*"],"exclude":[]}},
     "rules":[{"type":"creation"},{"type":"update"},{"type":"deletion"}],
     "bypass_actors":[{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"always"}]}
    EOF
    ```

3. **Set the six secrets.** A `gh secret set` without `--body` or a pipe asks for the value without showing it, so nothing goes into your shell history:

    ```sh
    R=<owner>/<repo>
    base64 -i ~/path/to/developer-id.p12 | gh secret set MACOS_CERTIFICATE --env release -R $R
    gh secret set MACOS_CERTIFICATE_PASSWORD --env release -R $R   # the .p12's export password
    gh secret set MACOS_SIGN_IDENTITY --env release -R $R          # Developer ID Application: <name> (<team id>)
    gh secret set APPLE_API_KEY --env release -R $R < ~/path/to/AuthKey_<key id>.p8
    gh secret set APPLE_API_KEY_ID --env release -R $R             # the key's id
    gh secret set APPLE_API_ISSUER_ID --env release -R $R          # the Issuer ID
    gh secret list --env release -R $R                             # lists the six names
    ```

4. **Decide what to do with the `.p12` and `.p8` files.** Once the secrets are set, you can delete both. GitHub never shows a secret again, so each file is only useful for setting a secret again, for example in a fork or after rotating a key.

   - **The `.p12`:** keep the certificate and its private key in your login keychain, and you can export it again at any time.
   - **The `.p8`:** without it, you make a new key and revoke the old one, which takes a few minutes.

   If you want a backup, keep both in a password manager. Never keep them in the repository or a synced folder.

5. **Put the Team ID into `app/publisher.json` as `team_id`, through a pull request.** It is public, since every signed app carries it. The same file names the bundle identifier and the repository whose releases `bosun app install` downloads from, so check `bundle_id` and `releases` while you are there. Until `team_id` is filled in, `bosun init` does not offer the app, and the release workflow stops at its first check.

Then, for each release:

1. Set `app/VERSION` (`major.minor.patch`) in a pull request and merge it. The first release is `0.1.0`.
2. Tag the merged commit on `main` and push the tag:

    ```sh
    git switch main && git pull
    git tag -a app-v0.1.0 -m "bosun.app 0.1.0"
    git push origin app-v0.1.0
    ```

3. Approve the run in the Actions tab (if you set a required reviewer). It stops at once if the tag is not
   `app-v` plus `app/VERSION`, if the commit is not on `main`, or if `team_id` is missing. Notarization takes a few
   minutes; when Apple does not accept the app, the job prints Apple's log and fails, and nothing is published.
4. On a Mac of yours, `bosun app install` downloads the release and accepts it only if it passes the same
   checks. To release again, raise `app/VERSION` and tag again: never move or reuse a tag.

To swap the publisher (a fork, another team) and keep the bundle identifier, edit `team_id` and `releases` in
`app/publisher.json`, replace the six secrets and cut a release: the next `bosun update` installs the newly signed
app. A new `bundle_id` takes more. To the version that has it, the installed app is not the publisher's (its bundle
id is another), and `bosun update`, `bosun app install`, `remove` and `uninstall` never replace or remove an app
they cannot say is the publisher's: they leave it and say why. So on each Mac, run `bosun app remove` before
updating to that version (the old version still knows the app), or delete `~/Applications/bosun.app` by hand after
it; then `bosun app install` installs the new app, which macOS sees as a new one, and `bosun app login on` turns
Open at Login on again.

## Testing without waiting for the schedule

Work up the levels; each one exercises more of the real chain.

1. **Unit and integration:** `uv run pytest -n auto`, then `tests/smoke.sh`. The first runs the
   Python unit tests in `tests/unit/` (ledger and outreach, delegate, Claude Code state, repo mask,
   permission guard, the watchdog's checks with an injected clock, learning's digest, ledger checks
   and proposals, and replays of orchestrator and repo-session interactions, rewritten as fiction in
   the acme domain, from `tests/fixtures/corpus/`); the second the end-to-end checks,
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
