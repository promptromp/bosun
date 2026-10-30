# Maintaining bosun

Notes for changing this repo. They point to where things live; the files themselves are the source
of truth, so don't copy rules into this file.

## Where things live

- `bin/bosun` dispatches `bosun <cmd>` to `libexec/bosun-<cmd>`. Each command's
  `# Usage:` header is its `--help`.
- `lib/common.sh`: config defaults (`load_config`) and shared helpers: the window list
  (`window_specs`: orchestrator, sessions.yml entries, checkin), tmux, Claude status, the one
  launch path (`build_launch_command`, used by `start`, `refresh` and `send`), and the launchd
  labels and plist rendering (from `templates/launchd.plist`, `launchd-watchdog.plist`,
  `launchd-learn.plist`). `lib/setup-helpers.sh`: the helpers a site's `setup.sh` calls
  (`run_step`, `ensure_aws_sso`, `ensure_claude_login`). `lib/ledger.py`: item kinds, id formats, Slack classes, state
  machines and the states that end a hand-over to a repo session (`work_done`), plus delegations
  and reports, outreach (what we wait on from other people: its channels table, reply
  matching, the parent guard), tasks, and the commands for links and tickets. `lib/lineage.py`:
  typed links between items (`RELATIONS`, validation, inverse, tree), pure over the items;
  `lib/tickets.py`: external tickets (`BACKENDS`, ids, status mapping, mismatch hints); design:
  `docs/design/lineage.md`. `lib/workstreams.py` (pure): which workstream an item belongs to
  (resolution order, inheritance through lineage, ticket epics, the site's rules in
  `$BOSUN_CONFIG_DIR/workstreams.json`), lint and `assign`'s plan; `lib/ws_summary.py` (pure):
  the stand-up summary; design: `docs/design/workstreams.md`. `lib/similar.py` (pure): loose
  word-overlap similarity for near-duplicate hints (`ledger spawn`, learning's outreach overlap,
  which held item a prompt is about).
  `lib/delegate.py`: task rendering. `lib/guard.py`: the orchestrator's PreToolUse permission hook,
  and with `BOSUN_APPROVALS_ENABLED` its approval hooks (`--approvals`, `--permission-request`,
  `--post-tool-use`; the gated outward calls are one table there, `GATED_*`). `lib/approvals.py` (pure): action specs
  and exact matching, whose prompts count for an approval's quote, the standing classes
  (`STANDING_CLASSES`), matching a call's result to the approval it used and where a proposal
  stands for me (`proposal_status`); `ledger propose --send|--run|--tool`, `approve`, `approvals` store and list
  them; `orchestrator_extra_settings` (`lib/common.sh`) adds the hooks at launch. Design:
  `docs/design/approvals.md`. `lib/pr_threads.py`: a PR's review threads and rounds, read-only
  (`bosun pr-threads`; which threads await the author), also used by the guard's re-review
  check (`check_bump`, `BOSUN_APPROVALS_CHECK_REVIEW_THREADS`) and by `lib/review_watch.py`
  (`bosun review-watch`: new reviews on my PRs after their last hand-over, recorded as
  `review-arrived` and queued as `review` events; design: `docs/design/events.md`).
  `lib/claude_state.py`: everything read or written in Claude Code's own files (transcripts,
  running-session records, folder trust, error replies, `claude auth status`, the login check
  request); bash reaches them only through it. `lib/login.py`: whether the login works and a
  window gets real replies (signals combined, the check-in gate, `login.json`). `lib/orphans.py`:
  the helpers a pane's shell leaves behind (`bosun orphans`). `lib/repo_mask.py`:
  the one repo-mask implementation. `lib/watchdog.py`: the watchdog's table of checks (`CHECKS`),
  awake window, de-dup state and alert sinks; `libexec/bosun-watchdog` only collects the live
  facts (tmux windows, the Claude in each, the check-in loop, launchd) for it.
- Events (design: `docs/design/events.md`, behind `BOSUN_EVENTS_ENABLED`): `lib/activity.py`
  (pure: what I said in a window and its attribution, a window's active item, `working`, which
  prompts are mine), `lib/hook.py` + `libexec/bosun-hook` (the repo sessions'
  UserPromptSubmit hook, installed by `bosun launch` through `repo_session_settings`),
  `lib/events.py` + `libexec/bosun-events` (the queue and the dispatcher the check-in loop
  runs), `lib/redact.py` (shared with learning). The Slack watcher (behind
  `BOSUN_SLACK_WATCH_ENABLED`): `lib/slack_watch.py` + `libexec/bosun-slack-watch` (the
  read-only headless call, checking its JSON, the `slack` events), its prompt
  `orchestrator/slack-watch.md`; `lib/awake.py` is the awake window it shares with the watchdog.
- Learning (`bosun learn`, design: `docs/design/learning.md`; `lib/learn*.py`):
  `lib/learn_digest.py` builds the digest (sessions through a harness adapter, ledger, logs),
  `lib/learn_checks.py` the deterministic ledger hints, `lib/learn.py` the analysis call,
  grounding, de-dup state, `learn:` proposals and `apply`; its prompt is `orchestrator/learn.md`,
  its eval `tests/llm/learn_eval.py` over the fictional world in `tests/llm/learn_world.py`. Its
  launchd agent comes from `templates/launchd-learn.plist`. `bosun sessions --json` lists
  windows.
- Knowledge (`bosun kg`, design: `docs/design/knowledge.md`; data only in
  `$BOSUN_CONFIG_DIR/knowledge/`, never here): `lib/knowledge_schema.py` (pure: entity types,
  `PREDICATE_LIST` with what each connects, single-valued and risk, the People whitelist, source
  kinds and their trust, a site's `schema.json`), `lib/knowledge_model.py` (pure over a `KB`: the
  fact log folded, resolution, the write operations and the auto-confirm rules),
  `lib/knowledge_review.py` (pure: batch groups and decisions, lint), `lib/knowledge_query.py`
  (pure: lookup, search, neighbours, context packs), `lib/knowledge_store.py` (files, lock, git),
  `lib/knowledge.py` + `libexec/bosun-kg` (the CLI and import). Quotes of my words are checked
  with `lib/approvals.py`'s genuine-prompt test. `tools/survey_to_kg.py` converts a survey into an
  import file; the fixture knowledge base is `tests/fixtures/knowledge/acme-import.jsonl`.
- Console (`bosun console`, design: `docs/design/console.md`): `lib/console_server.py` (the
  HTTP server and its request checks: bind address, Host, token cookie, static allowlist,
  headers), `lib/console_data.py` (ledger snapshots, `/api/version`, what the views share),
  `lib/console_ledger.py` (pure: the Ledger list, item page and "needs you"),
  `lib/console_live.py` (the only I/O for Sessions and health: `sessions --json`, `watchdog
  --facts`, the watchdog's `status_data`, state files; cached) and `lib/console_sessions.py`
  (pure: window cards, waiting windows, health), `lib/console_stats.py` (pure: the Stats metrics,
  defined in the design note) over what `lib/console_state.py` reads of the events, Slack watcher
  and learning state, `lib/console_learn.py` (pure: the Learning view), `lib/console_library.py`
  (pure: Library entries, config masking, front matter, effective task types from
  `delegate.task_files`) and `lib/console_files.py` (the Library's file list from fixed roots,
  addressed by opaque keys, and the learning reports'), `lib/console_knowledge.py` (pure: the
  Knowledge view's cards, search, entity pages, process flows, review batch and decisions),
  `lib/console_kg_graph.py` (pure: the neighbourhood graph's walk and layout),
  `lib/console_kg_views.py` (pure: the Knowledge Graph page's Briefing and Constellation; the
  approved prototype of its treatments is `docs/design/prototypes/knowledge-treatments/`) and
  `lib/console_kb.py` (its only I/O: reads the knowledge base, never identifiers; saves a review
  decision, the console's one write, `POST /api/knowledge/review`, guarded in `console_server.py`'s
  `write_refusal`), `lib/console_kg_sources.py` (readable fact sources: a memory note's title and
  Library link, a session's name; cached), the page in `console/` (`index.html`, `app.js`,
  `app.css`; no build step; its design tokens are the custom properties at the top of `app.css`).
  `tests/js_check.sh` checks `app.js` with node if it is installed (a fake DOM in
  `tests/js/app_check.js`, rendering views from `tests/js/api_models.py`; no npm packages).
  Its fixture ledger is `tests/fixtures/console/acme-ledger.json`, rebuilt with the real ledger
  commands by `make-acme-ledger.sh` there (its build day is pinned, so the ids stay).
- `orchestrator/policy.md` (appended to the orchestrator's system prompt),
  `orchestrator/plugin/commands/chores.md` (the chores procedure), `orchestrator/settings.json`
  (the orchestrator's own permissions, generic; a site replaces it with
  `BOSUN_ORCHESTRATOR_SETTINGS`; repo sessions use `BOSUN_CLAUDE_FLAGS`).
- `orchestrator/tasks/<type>.md`: one file per delegated task type (front matter: what the repo
  session may and never does, what the orchestrator does afterwards; body: the rules sent to the
  session). `_common.md`: rules every task gets. Sites can add or replace task types and add shared
  rules in `$BOSUN_CONFIG_DIR/tasks/`, and append to the policy with
  `$BOSUN_CONFIG_DIR/policy.md`; keep team- or person-specific procedure there, not here.
- Docs: `README.md` is the front page (what bosun is, a quick start, the guide list), kept short;
  the user guides are `docs/*.md` (index: `docs/README.md`), one per part; design notes are in
  `docs/design/`, plans in `docs/plans/`. A user-facing change goes in its part's guide.

## Common changes

- **A task rule** ("open normal PRs, not drafts", "reply in the Slack thread after a review"):
  edit `orchestrator/tasks/<type>.md`, both the body and the front-matter summary. If it adds or
  changes a ledger step, check the state exists (`bosun ledger kinds`). If the orchestrator's
  part changes (what it proposes afterwards, when it may delegate), also update `followup:`, and
  `policy.md` or `chores.md` only where they name that step. Paste the new
  `bosun delegate --list --markdown` rows into the task table in `docs/orchestrator.md`.
- **A new task type:** add `orchestrator/tasks/<type>.md`; mention it where `policy.md` lists the
  types and where `chores.md` should use it.
- **Wording the orchestrator or repo sessions act on** (`policy.md`, `chores.md`, task files):
  run the real-Claude free-text tests for the cases it touches (`tests/llm/free_text.py --only
  ...`, see Tooling) and add a case for a new phrasing. For `orchestrator/learn.md`: bump its
  `version`, then run `tests/llm/learn_eval.py`. For `orchestrator/slack-watch.md`: bump its
  `version`, then check a real `bosun slack-watch --dry-run` (read-only; it prints the searches
  it ran and the counts per kind).
- **An outreach channel:** a row in `CHANNELS` (`lib/ledger.py`) and in the send/reply table of
  `chores.md` step 5.
- **An outreach purpose:** `PURPOSES` (`lib/ledger.py`) and the `reach-out` help there, the
  purpose lists in `policy.md` (with what each means) and `orchestrator/tasks/_common.md`, and the
  outreach table in `docs/orchestrator.md`.
- **A watchdog check:** a row in `CHECKS` (`lib/watchdog.py`), a line in
  `libexec/bosun-watchdog`'s usage (smoke checks they match), and the table in `docs/watchdog.md`.
- **A ledger kind:** an entry in `KINDS` (`lib/ledger.py`) and the list of item ids in `docs/orchestrator.md`.
- **A link relation:** a row in `RELATIONS` (`lib/lineage.py`, with its inverse and the family in
  which cycles are refused), the lineage section of `docs/orchestrator.md` and the relation list in `policy.md`.
- **A workstream rule criterion** (beyond repo, title, channel, kind): `RULE_KEYS`, a test in
  `RULE_TESTS` (`lib/workstreams.py`), the rules section of `docs/design/workstreams.md`, and
  `examples/generic/workstreams.json` if it helps. Sites' rules stay in their config dir.
- **A ticket backend** (another tracker): a row in `BACKENDS` (`lib/tickets.py`: key format, how
  to read it, how an approved change is made, its status categories and names), the tickets
  section of `docs/orchestrator.md`, and a test in `tests/unit/test_tickets.py`. Site status names belong in
  `BOSUN_TICKET_STATUS_MAP`, not in the table.
- **An outward tool to gate** (approvals): a row in `GATED_MCP` / `GATED_COMMANDS` (`lib/guard.py`)
  and its `ask` rule in `orchestrator/settings.json`; a standing class: `STANDING_CLASSES` and its
  check in `lib/approvals.py`, its config keys, the design note and `docs/permissions.md`.
- **A knowledge predicate or entity type:** an entry in `PREDICATE_LIST` or `ENTITY_TYPES`
  (`lib/knowledge_schema.py`), the table and the single-valued and high-risk lists in
  `docs/design/knowledge.md`, and a test in `tests/unit/test_knowledge_schema.py`. A predicate
  about a person widens the People whitelist: only factual, work-operational ones, and say why in
  the commit. Site-specific predicates belong in `$BOSUN_CONFIG_DIR/knowledge/schema.json`.
- **A config key:** default in `lib/common.sh` and the same value in
  `examples/generic/bosun.env` (tests check they match); any other example only lists
  overrides. The guide in `docs/` if users should know.
- **A command:** `libexec/bosun-<cmd>` with a `# Usage:` header (`usage` prints it for
  `--help`; `die_usage` reports usage errors with exit 2), a line in `bin/bosun`'s usage, the
  table in `docs/commands.md`, and smoke tests.

## Tooling and checks

One-time setup: `uv sync && uv run pre-commit install` (commit, commit-msg and pre-push hooks).
All tools come from the uv-locked dev group in `pyproject.toml`, so `uv run <tool>`, the hooks
and CI agree on versions. On commit (`.pre-commit-config.yaml`; run all with `uv run pre-commit run --all-files`):

- Python: ruff (lint incl. McCabe complexity <= 10, isort, bugbear, security; format), mypy
  --strict, complexipy (cognitive complexity <= 15), lizard (cyclomatic <= 10, <= 60 lines).
- Shell: shellcheck (`-S style`, follows `source` via `.shellcheckrc`), shfmt (2-space indent,
  indented `case`, from `.editorconfig`), and `tools/shell_complexity.py` (functions: branch
  count <= 15 and <= 60 lines; script bodies <= 25 and <= 120 lines), since the Python tools do
  not read shell.
- Hygiene: whitespace, line endings, JSON/TOML/YAML syntax, shebangs vs executable bits,
  credentials and private keys, and `uv.lock` in sync.
- Publishing: detect-secrets against the audited `.secrets.baseline` (after a reviewed false
  positive: `uv run detect-secrets scan --baseline .secrets.baseline`, then `uv run
  detect-secrets audit .secrets.baseline`), and `tools/forbidden_terms.py` on staged files and
  commit messages (see Publishing below).
- The unit tests; the smoke suite runs on push.

CI (`.github/workflows/ci.yml`) runs the same on every pull request to `main`: all the hooks
(`lint`; the forbidden-terms list comes from the `FORBIDDEN_TERMS` Actions secret, and the pull
request's title, description and commit messages are checked too), the unit tests on Python 3.13
to 3.15 (`test (<version>)`), the smoke suite on macOS (`smoke`) and gitleaks over the pull
request's commits (`gitleaks`; a made-up value in a test carries a `gitleaks:allow` comment). The
logs are public, so neither check prints what it matched. `main` takes changes only through a pull
request with those checks green and up to date with `main`, CodeQL's results clean (GitHub's
default setup; no error alerts, no high or critical security alerts) and every review thread
resolved (the ruleset also asks for a Copilot review on each push, which takes effect once
Copilot code review is enabled for the organization); squashed or rebased: the ruleset in
`.github/rulesets/main.json`. A new or renamed job goes in its list too, applied with `gh api -X PUT
repos/{owner}/{repo}/rulesets/<id> --input .github/rulesets/main.json`. Dependabot
(`.github/dependabot.yml`) opens monthly grouped updates of the actions, `uv.lock` and the hook
revisions, and security updates as advisories come out.

When a limit trips, split the function rather than raising the limit. Suppress a finding only
with an inline comment that says why (as for the jq and GraphQL strings). The formatters rewrite
files in place; that is fine for this repo's only long-running script (`checkin --loop`, whose
loop bash has already parsed), but for hand edits to `libexec/` keep to atomic replacement.

Always run `tests/smoke.sh` (hermetic: temp HOME, own tmux socket, fake Claude). It runs the Python
unit tests in `tests/unit/` first (add one there for any change to `lib/*.py` or
`bosun-resolve`), then end-to-end checks, including that task files only use valid ledger
transitions and that the task table in `docs/orchestrator.md` matches them. Then `bosun doctor`. In `tests/smoke.sh`,
use `fails_with "<expected error>" cmd` for refusals (a crash must not count as a pass) and
`wait_for <seconds> cmd` instead of fixed sleeps; external tools (aws, gh, launchctl, pmset,
osascript) are faked in the temp HOME's `~/.local/bin`, and the install test uses its own launchd
label and refuses to run unless the fakes come first on PATH.

`tests/llm/` holds tests with the real, headless Claude: what I say in my own words, and the
ledger state it must lead to (`cases.py`), each run in a scratch world (`world.py`: scratch HOME,
state and tmux server, an `bosun` wrapper that pins them and send dry-run, outward tools
denied, no MCP servers unless `--with-mcp`). They cost money, so no hook runs them: `uv run python tests/llm/free_text.py [--runs 2]
[--only <case ids>]` after changing wording Claude acts on. `tests/llm/learn_eval.py [--runs 2]`
scores learning's real analysis on a fictional world with planted problems (`learn_world.py`);
run it after changing `orchestrator/learn.md` or what the digest shows. Fictional data only, as
for the corpus.

`tests/fixtures/corpus/` holds two days of orchestrator commands with the guard's decision, and
replay scenarios run by `tests/unit/test_corpus.py`, all in the fictional acme invoice-exporter
domain. When a change alters a guard decision or a ledger step, update the fixture and say why in
the commit. A fixture modelled on real use is rewritten as fiction, not anonymized by swapping
words: a new story with the same command shapes, placeholders only (no names, Slack/GitHub ids or
emails), and nothing of the real product, its repos, code or processes.

## Publishing: keeping the repo generic

The repo is meant to be shareable, so nothing in it may identify an organization or a person.

- Site-specific content lives only in the user's config dir (`$BOSUN_CONFIG_DIR`, default
  `~/.config/bosun/`): `bosun.env`, `sessions.yml`, `setup.sh`, `policy.md`,
  `tasks/`, an orchestrator settings file with the site's MCP servers
  (`BOSUN_ORCHESTRATOR_SETTINGS`), and the forbidden-terms list. `examples/` holds generic
  examples only.
- Never commit real names (people, organizations, products, repos, channels, hosts), ids (Slack,
  GitHub, Jira, AWS accounts or profiles), emails or tokens, also not in tests, fixtures, docs or
  commit messages. Use placeholders (`acme`, `my-org`, `example.com`, `/Users/me`, zero-filled
  Slack ids) and clearly fictional names.
- The `forbidden-terms` hook enforces this locally from `$BOSUN_CONFIG_DIR/forbidden-terms.txt`
  (one case-insensitive regex per line, `#` comments), so the list itself never enters the repo;
  without the file it passes. Add a term there whenever something site-specific nearly slipped in.
  To check the whole tree: `git ls-files | xargs uv run python tools/forbidden_terms.py`.

## Rolling a change out to running sessions

- The orchestrator reads `policy.md` once per conversation, and starts a new one each morning. For
  a same-day change: `bosun send orchestrator "re-read <file>: <what changed>"`.
- Tasks already handed to repo sessions keep their old text; send them a short update
  (`bosun delegate --update <id>`, or `bosun send --id <id> <window>` if the ledger has no
  delegation recorded for the item).
- **Replace `libexec/` scripts atomically** (write a temp file, then `mv` over the original); never
  edit them in place while the session runs. Bash reads a script as it executes, so a long-running
  script edited in place resumes at its old byte offset in the new text. Once this made two windows
  start blank sessions under their real names. `launch` now `exec`s Claude, so no launcher
  stays behind, but the rule still holds for `checkin --loop` and any future long-running script.

## Gotchas

- `/bin/bash` on macOS is 3.2: no `mapfile`, `${x@Q}`, associative arrays or `wait -n`; empty arrays
  break `"${a[@]}"` under `set -u`; an assignment with escaped quotes inside `"$(...)"` (`"$(X="{\"a\":
  1}" f)"`) is misparsed, so assign first. The smoke suite's `bash -c` checks run whichever `bash`
  comes first on PATH: Homebrew's 5.x on many Macs, 3.2 in CI.
- Never type long commands into a pane with `send-keys`: the terminal's line limit (1024 bytes on
  macOS) drops the rest and the Enter. Run commands as the pane's own command (`new-window` /
  `respawn-pane -- argv`, see `build_launch_command`); tmux refuses commands over about 16 KB, so
  pass large text in a file. To prompt a running Claude, use `load-buffer` + `paste-buffer -p`.
- Target tmux windows by id (`@N`): `display-message -t <unknown name>` silently falls back to the
  current window.
- `load_config` does not export the config, on purpose: exported values were inherited by Claude
  sessions and the tmux server and then masked later edits to `bosun.env`. Pass a setting to a
  child process explicitly (as `bosun-ledger` does for `ledger.py`); only real environment
  overrides are exported.
- Model and effort: launch passes `--model` and the effort as `--settings '{"effortLevel":...}'`
  (before the orchestrator's long `--append-system-prompt`, so `claude_launch_model_effort` can read
  them back from `ps`). Not `--effort` or `CLAUDE_CODE_EFFORT_LEVEL`: those beat `/effort`, so you
  could not change effort inside the session. Never paste `/model` or `/effort` into a session:
  both save the choice to `~/.claude/settings.json` for every future session. Per-task effort for a
  running session goes through a subagent (the Agent tool takes `model` and `effort`).
- Only paste into a Claude whose status `claude_takes_input` accepts. `waiting` means a permission
  prompt or question is open, and the Enter that `send` presses would answer it.
- tmux windows inherit the environment of whatever started the tmux server, hence the dedicated
  server (`BOSUN_TMUX_SOCKET`). Static `AWS_*` credentials leaked that way override SSO
  profiles.
- A pane's launch shell (`<shell> -lic '... launch <window> ; exec <shell> -l'`) is interactive but
  never shows a prompt, so helpers the user's startup files fork off (a prompt theme's git-status
  daemon) outlive it, adopted by launchd. `lib/orphans.py` finds them by exact command line and
  terminal; `launch`, `checkin --loop`, `relaunch_in_window` and `stop` stop them. Never kill
  processes by a pattern match: the tmux server's own command line contains the launch string.
- Claude Code internals this relies on (not public APIs) are all in `lib/claude_state.py`: session
  records in `~/.claude/sessions/<pid>.json`, transcript `custom-title` entries, error replies
  (`isApiErrorMessage`, `error`, `apiErrorStatus`), `claude auth status --json`, and
  `projects[<dir>].hasTrustDialogAccepted` in `~/.claude.json` (trust is per git repo since 2.1.232;
  no flag skips the prompt). After a Claude Code update, check with `bosun doctor` and
  `bosun refresh --dry-run`.
- When testing outward-facing features, never post to shared Slack channels; DM yourself.
