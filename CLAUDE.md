# Maintaining bosun

Notes for changing this repo. They point to where things live; the files themselves are the source
of truth, so don't copy rules into this file.

## Where things live

- `bin/bosun` dispatches `bosun <cmd>` to `libexec/bosun-<cmd>`. Each command's
  `# Usage:` header is its `--help`, which `bin/bosun` prints itself (`usage_of`, `lib/common.sh`) without
  running the command, so it needs no config.
- Two paths (`lib/install_paths.py`, design: `docs/design/update.md`): a process runs the code of its own
  folder (`BOSUN_ROOT`, `BOSUN`), and whatever outlives it (hook commands in launch settings, the launch
  command, plists, the `~/.local/bin/bosun` link, `--plugin-dir`, Codex's notify, the orchestrator
  settings default) names the install link (`BOSUN_INSTALL`, `BOSUN_CMD`), which an update points at a
  newer version. In a checkout they are the same. A new place that writes a path out uses the second.
- Updating (design: `docs/design/update.md`): `lib/versions.py` (version folders, the link switched in
  one rename, pruning), `lib/update_state.py` (the update's record and lock, the hold),
  `lib/update_backups.py` (backups, restore, pruning, deleted by a forget), `lib/update_gate.py` (the
  write gate and `guarded`), `lib/migrations.py` (the one registry of the stores' format
  migrations, and the config's renamed keys), `lib/launch_record.py` (what each window was
  launched with, per session: the fingerprint an update compares after the switch, and the agent,
  directory and overrides its restart keeps, through `recorded_restart` in `lib/common.sh`; `bosun
  launch` writes it, `launch --fingerprint` and `refresh --update-restart` use it) and
  `lib/update_plan.py` (pure: the plan, what each window gets, its text). `libexec/bosun-update` (the
  wrapper: fetch the newest release, or on `BOSUN_UPDATE_CHANNEL=main` the branch, into its own version folder,
  hand over to that version; `--check` and `--skip`), `lib/update_check.py` (pure: the release tag rule, which
  `tools/release.py` uses too, and the check's answer) with `lib/update_lookup.py` (its git side: read-only, no
  version folder, no lock) and
  `lib/update_facts.py` (the facts the plan is made from, and what stops it there) and `lib/update.py`
  (the command: the rehearsal, the hold, the freeze and its put-back, the release, an update cut
  short carried on; `update note` and `update advance`), `lib/update_agents.py` (the decisions, its
  only file the update's record: what each window gets turn by turn, the take-stock and update prompts,
  what came of each), `lib/update_windows.py` (carrying them out: bosun's commands, the update's wait
  and its Ctrl-C, which the check-in loop carries on), `lib/update_notes.py` (the windows' note
  files) and `lib/update_summary.py` (pure: the summary it ends with, from what each step kept in the
  record, its text and `--json`, and what the last update left me, which `bosun doctor` lists through
  `lib/update.py after-update`); `reload_changed_jobs` in `lib/common.sh`
  (also install.sh's); its smoke section's next version is `tests/sandbox/update_patch.sh`.
- `lib/common.sh`: config defaults (`load_config`) and shared helpers: the window list
  (`window_specs`: orchestrator, sessions.yml entries, checkin), tmux, the one
  launch path (`build_launch_command`, used by `start`, `refresh` and `send`), and the launchd
  labels and plist rendering (from `templates/launchd.plist`, `launchd-watchdog.plist`,
  `launchd-learn.plist`, `launchd-console.plist`, `launchd-now.plist` and `launchd-app.plist`: one label helper
  each, such as `now_label`, listed once in `job_labels`), and `one_at_a_time` (`lib/run_locked.py`: the rest of a shell command run
  holding a lock that what it starts inherits; `start`, `refresh` and `init` use it). `lib/setup-helpers.sh`: the helpers a site's `setup.sh` calls
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
  `lib/connectors.py`: what a window may reach (its list of MCP servers and connectors), and what every
  agent's module shares (reading a list, the report's helpers and texts); each agent turns it into what that
  agent takes at launch in its own module, which its `Profile.connectors` names: `lib/connectors_claude.py`
  (Claude Code's `allowedMcpServers`, and the URLs and command lines kept from the last lookup in
  `connectors.json`) and `lib/connectors_codex.py` (Codex's launch arguments), each with `main` and
  `doctor_line`; the agents' listings are read through `lib/claude_state.py` (`claude
  mcp list`, plugins' servers, the settings scopes' allow lists a launch refuses to be widened by) and
  `lib/codex_state.py`; design: `docs/design/connectors.md`. The shell side is
  `window_connectors` and `set_connector_settings` (`lib/common.sh`), which `build_agent_args` calls once
  per launch; `bosun doctor`'s line per window is `connectors.py doctor`, which asks the window's agent
  module (`check_connectors`).
  `lib/delegate.py`: task rendering. `lib/guard.py`: the orchestrator's PreToolUse permission hook
  (which `guard_hook_settings` in `lib/common.sh` adds to the orchestrator's launch and to `chores
  --dry-run`), and with `BOSUN_APPROVALS_ENABLED` its approval hooks (`--approvals`, `--permission-request`,
  `--post-tool-use`; the gated outward calls are one table there, `GATED_*`). `lib/approvals.py` (pure): action specs
  and exact matching, whose prompts count for an approval's quote, the standing classes
  (`STANDING_CLASSES`), matching a call's result to the approval it used and where a proposal
  stands for me (`proposal_status`); `ledger propose --send|--run|--tool` and `ledger approve` store
  them and `ledger approvals` lists them; `orchestrator_extra_settings` (`lib/common.sh`) adds the hooks
  at launch. Design:
  `docs/design/approvals.md`. `lib/pr_threads.py`: a PR's review threads and rounds, read-only
  (`bosun pr-threads`; which threads await the author), also used by the guard's re-review
  check (`check_bump`, `BOSUN_APPROVALS_CHECK_REVIEW_THREADS`) and by `lib/review_watch.py`
  (`bosun review-watch`: new reviews on my PRs after their last hand-over, recorded as
  `review-arrived` and queued as `review` events; design: `docs/design/events.md`).
  `lib/claude_state.py`: everything read or written in Claude Code's own files (transcripts,
  running-session records, folder trust, error replies, `claude auth status`, the login check
  request); bash reaches them only through it, but for `bosun doctor` showing your saved default. `lib/signals.py`: Claude Code's own signals,
  recorded by async hooks (`bosun hook signal`: what a waiting window was asked, what a turn
  left running, StopFailure); design: `docs/design/signals.md`. `lib/login.py`: whether the login
  works and a window gets real replies (signals combined, the check-in gate, `login.json`). `lib/orphans.py`:
  the helpers a pane's shell leaves behind (`bosun orphans`). `lib/repo_mask.py`:
  the one repo-mask implementation. `lib/watchdog.py`: the watchdog's table of checks (`CHECKS`),
  awake window, de-dup state and alert sinks; `libexec/bosun-watchdog` only collects the live
  facts (tmux windows, the Claude in each, the check-in loop, launchd) for it.
- Quit (design: `docs/design/menu-bar.md`): `lib/quit.py` (pure: the plan over the windows, its texts, and the run over
  `Ports`: the wait, the second look under the lock, the order of the steps) and `lib/quit_live.py` (the world the
  ports touch: `bosun sessions --json`, the `windows` lock, launchctl and the console job, `bosun stop`; the
  command), which `libexec/bosun-quit` (the command line and settings) runs;
  `lib/quit_state.py` is what it leaves (`quit.json`: since when, whether the morning start is paused), which the
  watchdog and learning's scheduled run skip on, and the morning run when paused (`quit_stopped`, `quit_paused`,
  `quit_clear` in `lib/common.sh`); `bosun start` clears it, `start --unless-quit` (the morning's) never does.
- Sync (design: `docs/design/sync.md`): every ledger write goes through `locked_ledger` into a
  journal that `ledger.json` is the rebuild of, and every knowledge write through
  `knowledge.opened` into one in `knowledge/journal/`. `lib/change_log.py` (pure: the envelope, ids,
  the hybrid clock, the order, and what both stores do alike at their write boundary: the refusal,
  the one-time conversion, the stamp their files record, a deletion's placeholder), `lib/journal.py`
  (a store's journal files, the replica id, transactions, an unfinished transaction set aside, a
  deletion in `local.jsonl`, and the durable writes both stores' files use: every byte, flushed, a
  replace or a new file with its folder flushed), `lib/ledger_changes.py` (pure: the field table, `diff`, `rebuild`),
  `lib/knowledge_changes.py` (pure: a knowledge write's edits, genesis, each file's digest,
  `rebuild`, what an outside edit adopts, never a person deleted on request) and
  `lib/validation.py` (pure: what both stores' `validate` finds: the finding, its severity and
  line, the journal against the files, where a rebuild and a file differ; `bosun doctor` prints
  each line). A change to the knowledge rebuild's rules (`_APPLIERS`, the table its dispatch uses,
  `SKIPPED` or `CHECKED`) trips the rules-digest test in `tests/unit/test_knowledge_changes.py`:
  bump `KG_RULES_VERSION` (`lib/knowledge.py`) and record the new digest under it.
- Setup (design: `docs/design/init.md`): `get.sh` (what `curl | sh` runs: plain POSIX sh, everything in
  functions with `main` last, because people pipe it straight from `main`; a first install's versions side
  by side, taking the newest release (`newest_release`, an sh twin of `lib/update_check.py`'s tag rule, held to
  it by `tests/unit/test_get_sh.py`; `BOSUN_BRANCH` takes a branch's head and `init` follows `main`), and on an
  install `init` set up the hand-over to the install's own `bosun update` (to the new version's, for an older
  checkout, whose first run converts it: `convert` in `lib/update.py`); `lib/checkout_work.py` is what deleting a
  checkout or a version would lose, which uninstall, the conversion and the update's prune go by), `lib/preflight.sh` (what
  bosun needs and what to do about what is missing; plain sh, changes nothing, and `init` and `doctor`
  start with it) with its profiles `lib/platforms/<os>.sh` and `lib/harnesses/<harness>.sh`, `lib/init_detect.py`
  (read-only: what the Mac shows, Claude Code's files through `lib/claude_state.py`), `lib/init_plan.py`
  (pure: recommended answers, the config files' text, the plan), `lib/init_wizard.py` (pure over a `Ui`:
  the questions), `lib/init_terminal.py` (the keys and screens) and `lib/init.py` (the CLI: writes the
  config with backups, trusts the folders, then runs `install.sh`, the one place that sets `pmset`; the
  launchd jobs are rendered and loaded by `lib/common.sh`, which `install.sh` and `bosun update` share).
  `lib/uninstall_detect.py` (read-only: what of bosun is here), `lib/uninstall_plan.py` (pure: the
  tables of what bosun's files are, what goes, what stays, how it reads), `lib/uninstall_macos.py` (launchd
  and `pmset`) and `lib/uninstall.py` (the CLI) take it off again; `lib/agent_changes.py` records what bosun
  changed in the agents' own files, so that only that is undone. Design: `docs/design/uninstall.md`.
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
  (pure: lookup, search, neighbours, context packs), `lib/knowledge_changes.py` (pure: a write's
  changes for the journal, local where they name a person or hold my words, and the rebuild the
  files are), `lib/knowledge_store.py` (files, lock, git), `lib/knowledge.py` + `libexec/bosun-kg`
  (the CLI and import, and the write boundary: the journal first, then the files and `view.json`;
  reads caught up with the journal, `knowledge.load` being the one reader, which the console calls with the identifiers, forgotten records and key left out; `kg validate`). Quotes of my words are checked
  with `lib/approvals.py`'s genuine-prompt test. `tools/survey_to_kg.py` converts a survey into an
  import file; the fixture knowledge base is `tests/fixtures/knowledge/acme-import.jsonl`.
- Agents (design: `docs/design/harnesses.md`): an adapter per agent, `lib/claude_state.py`,
  `lib/codex_state.py` and `lib/agy_state.py`, each a module with the functions of `Agent` in `lib/agent_state.py`, whose `AGENTS`
  table is the one place that lists them (each module's `PROFILE`, a `Profile` in `lib/agent_files.py`, says
  what it is and can do; `lib/agent_state.py profiles` prints them for the shell) and whose `record` the
  watchdog, ledger and events use. They share `lib/agent_files.py`, and neither imports the other.
  `lib/agents.sh` is the shell side: `agent_state`, `window_harness`, `harnesses_in_use`, `agent_*`.
  `libexec/bosun-launch` holds the flow, and `lib/launch/<harness>.sh` what differs in it
  (`build_agent_args`, `first_prompt`, `run_new`, `run_resumed`). `window_specs` says which agent each window
  runs. `bosun hook turn-end --harness <agent>` (`lib/hook.py`) is what an agent runs when a turn ends, by
  its profile's `turn_end` (`codex-turn-end` is its alias for Codex windows started before, whose launch
  command outlives the bosun that wrote it); each agent's profile has a `Screen` (its readers for its input
  box, questions and rejected pastes, with `lib/screen_text.py`'s helpers), which `lib/pane_input.py` asks.
  An agent with hooks of its own (`agy`) names them in its profile: `bosun hook agent --harness <agent> --event
  <event>` runs them, and always exits 0, since the agent takes another exit as a refusal of its tool; an
  agent that needs a one-time step (`agy`'s plugin, from `agents/agy-plugin/`) has `Profile.setup`, made by
  `bosun launch` and recorded in `lib/agent_changes.py`, and `Profile.update_args`, its update, run by `bosun
  morning`. `lib/connectors_agy.py` refuses a window instead of limiting it.
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
  commands by `make-acme-ledger.sh` there (its build day, its replica and one write per second
  are pinned, so the ids stay).
- Status for the menu bar app (`bosun status --json`, design: `docs/design/menu-bar.md`): `lib/menu_status.py`
  (pure: the level, sentence and lines from the watchdog's checks and its gate on a missing session
  (`watchdog.session_gate_kind`), `console_sessions.waiting_in` and the quit record, `snapshot`; its `CONTRACT` only grows) and `lib/menu_status_live.py` (its only I/O, through
  `console_live`'s readers; the command, which always prints the object). The keep-awake end is
  `keep-awake-<session>.json`, written by `libexec/bosun-morning` and removed by `bosun stop`, read by
  `lib/keep_awake.py` (status and quit). The app's Start is `bosun morning --now --background`, which only
  kickstarts the `.now` job (`now_label`, `templates/launchd-now.plist`, which runs `morning --now`) so that launchd
  and not the app is the parent of what the run starts; the morning logic stays in the `--now` path. `bosun update`
  stays a detached child of the app (a job could boot itself out reloading plists).
- The menu bar app's lifecycle (`bosun app`, design: `docs/design/menu-bar.md`): `libexec/bosun-app` (the commands, and
  `login on|off`, which render and load `templates/launchd-app.plist` through `render_app_plist`, `app_label` and
  `reload_job` in `lib/common.sh`; the login job is there while its plist is, which `reload_changed_jobs` keeps current
  and never makes), `lib/app_release.py` (pure: the publisher, the release address, the code requirement the signature
  must meet, which archives are refused, `status`) and `lib/app_install.py` (the live side, every external tool through
  one `Runner`: `verify`, the download, the placing, the sweep of what a killed install left, quit and delete). The
  Team ID is checked with `codesign -R` against the certificates, never read from `codesign`'s text. The publisher is
  `app/publisher.json`, the version `app/VERSION`.
- The menu bar app itself (`app/`, a Swift package; design: `docs/design/menu-bar.md`, plan:
  `docs/plans/menu-bar-app.md`): `BosunMenuCore` is pure (no I/O, no AppKit) and carries the most tests (`Contract.swift`: the JSON of part 1's
  commands, each with `"contract": 1`; `MenuModel.swift`: the menu each state becomes, `FlowState` (one flow at a
  time) and `shouldExitAsSecondCopy`; `UpdateFlow.swift` and `QuitFlow.swift`: the flows as state machines, with the
  `bosun` commands each step runs; `BosunCLI.swift`: the one protocol the app runs bosun through, the commands and
  their timeouts as values, and `FakeBosunCLI`). The tests decode `app/Tests/BosunMenuCoreTests/Fixtures/*.json`,
  which `tools/contract_samples.py` writes from part 1's own pure functions (quit's results from its run over fake
  ports): a change to what `bosun status --json`, `update --check|--dry-run --json`, `quit --json` or `app status
  --json` print fails `tests/unit/test_contract_samples.py` until `uv run python tools/contract_samples.py` writes the
  fixtures again, and the Swift side follows. The app reads nothing of bosun's but what those commands print (the
  contract rule: `bosun ... --json` is its only input, and `menu_status.CONTRACT` only grows: a field is added, never
  renamed or removed, so an older app still reads a newer bosun). `app/` holds `Package.swift`, `Sources/`, `Tests/`,
  `Resources/` (`Info.plist.in`, the icons), `build.sh`, `publisher.json` and `VERSION`; Swift's lint is under Tooling.
  What the AppKit shell needs is split by whether it touches the system. Pure, in the core (so tested, and counted by the
  floor): `AppCoordinator.swift` and `AppCoordinator+Update.swift` (what is chosen, as flows over `AppUI` and
  `ScriptWriting`: the shell's whole surface), `DialogSpecs.swift` (every dialog's words, buttons and boxes, and what
  each answer means to a flow), `Poller.swift` (the timing, on a clock it is given), `Launch.swift` (the arguments, the
  one-copy rule over a start-time reader it is given) and `TestHooks.swift` (the VM suite's hook formats, the menu as
  JSON). `Sources/BosunMenuSystem` (Foundation only, depends on the core) is what touches the system: `ProcessBosunCLI`
  (bosun run for real, with timeouts that stop a command's whole process group), `ProcessStartTime` (sysctl),
  `HookFiles` (the hooks' folder and `LoggingBosunCLI`) and `CommandScriptWriter` (the `.command` files); its tests are
  `BosunMenuSystemTests`. `Sources/BosunMenu` is only AppKit: `MenuRenderer` (a `MenuModel` as an `NSMenu`, updated in
  place), `Dialogs` (a `DialogSpec` as an `NSAlert`), `StatusItemController`, `AppShell` (the `AppUI`), `AppDelegate`,
  `main.swift`. Start is `bosun morning --now --background`, a short command, never a child the app leaves running
  (the Mac's privacy prompts would name the app); only `update --yes` is started detached.
- bosun.app's build (design: `docs/design/menu-bar.md`, "The bundle and its pictures"): `app/build.sh` (the bundle
  from `swift build`, `app/Resources/Info.plist.in`, signed with the bundle id from `app/publisher.json` as its
  identifier, zipped; what `bosun app install --build` runs, ad hoc) and the pictures `tools/app_icons.py` made once
  from `docs/assets/logo.png` (`app/Resources/`; `tests/unit/test_app_icons.py` checks them).
- `orchestrator/policy.md` (appended to the orchestrator's system prompt),
  `orchestrator/plugin/commands/chores.md` (the chores procedure), `orchestrator/settings.json`
  (the orchestrator's own permissions, generic; a site replaces it with
  `BOSUN_ORCHESTRATOR_SETTINGS`; repo sessions use `BOSUN_CLAUDE_FLAGS` or `BOSUN_CODEX_FLAGS`).
- `orchestrator/tasks/<type>.md`: one file per delegated task type (front matter: what the repo
  session may and never does, what the orchestrator does afterwards; body: the rules sent to the
  session). `_common.md`: rules every task gets. Sites can add or replace task types and add shared
  rules in `$BOSUN_CONFIG_DIR/tasks/`, and append to the policy with
  `$BOSUN_CONFIG_DIR/policy.md`; keep team- or person-specific procedure there, not here.
- Docs: `README.md` is the front page (what bosun is, a quick start, the guide list), kept short;
  the user guides are `docs/*.md`, one per part; design notes are in `docs/design/`, plans in
  `docs/plans/`. A user-facing change goes in its part's guide. They are also the docs site
  (`mkdocs.yml`: its `nav` lists every page; `tools/mkdocs_hooks.py` adds the root pages and points
  links at the site; published by `.github/workflows/docs.yml`; guide: `docs/development.md`, "The
  docs site"). The site's home page is `docs/index.md` with `theme/home.html`, its look `theme/` and
  `docs/assets/site.css`, and its live examples `docs/assets/home/examples.js`, fed by
  `tools/docs_examples.py` from bosun's own tables and fixtures. A new page goes in `nav` and the
  guide table of `README.md`.

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
- **A launchd job:** its template in `templates/` (the hook checks it is XML), a label helper listed in `job_labels`,
  `render_job` and `reload_changed_jobs` (`lib/common.sh`), and in the Mac VM (`tests/sandbox/e2e_lib.sh`,
  `tests/install_e2e.sh`) a check that it loads and does what its plist says, and that uninstall removes it
  (`jobs_are_gone`).
- **A watchdog check:** a row in `CHECKS` (`lib/watchdog.py`), a line in
  `libexec/bosun-watchdog`'s usage (smoke checks they match), and the table in `docs/watchdog.md`.
- **A ledger kind:** an entry in `KINDS` (`lib/ledger.py`) and the list of item ids in `docs/orchestrator.md`.
  A change to `KINDS`' transitions trips the rules-digest test (`TestRules` in
  `tests/unit/test_ledger_changes.py`): bump `RULES_VERSION` (`lib/ledger.py`) and record the new
  digest under it. One that removes a move or renames a state first needs the rebuild decision of
  2026-11-04 in `docs/design/sync.md` built (the rebuild re-checking only a change that lands on
  ones its writer had not seen), and a migration for earlier changes.
- **A data format change** (what a store holds on disk: the ledger's field table or maps, the
  knowledge base's files or journal ops, the change envelope): `tests/unit/test_store_formats.py`
  fails on the digest. Bump `ledger.LEDGER_FORMAT` or `knowledge_store.KNOWLEDGE_FORMAT`, add a
  migration in `lib/migrations.py` with a fixture in the old format under
  `tests/fixtures/migrations/`, and record the new digest under the new version. The knowledge
  base's version is in `knowledge/format.json`, the ledger's in `ledger.json`'s `version`.
- **A link relation:** a row in `RELATIONS` (`lib/lineage.py`, with its inverse and the family in
  which cycles are refused), the lineage section of `docs/orchestrator.md` and the relation list in `policy.md`.
  It trips the same rules-digest test: bump `RULES_VERSION` and record the new digest.
- **A workstream rule criterion** (beyond repo, title, channel, kind): `RULE_KEYS`, a test in
  `RULE_TESTS` (`lib/workstreams.py`), the rules section of `docs/design/workstreams.md`, and
  `examples/generic/workstreams.json` if it helps. Sites' rules stay in their config dir.
- **A ticket backend** (another tracker): a row in `BACKENDS` (`lib/tickets.py`: key format, how
  to read it, how an approved change is made, its status categories and names), the tickets
  section of `docs/orchestrator.md`, and a test in `tests/unit/test_tickets.py`. Site status names belong in
  `BOSUN_TICKET_STATUS_MAP`, not in the table.
- **A connector a window should reach** (Slack for the orchestrator, a tracker for a repo window): a
  config value, never code: `BOSUN_ORCHESTRATOR_CONNECTORS`, `BOSUN_CONNECTORS` or `connectors:` in a
  `sessions.yml` entry, in the site's config dir (`bosun init` asks for the orchestrator's). No server's
  name goes in the repo. A new way to name a server, or an agent update that changes how one is switched
  off: that agent's module, `lib/connectors_claude.py` or `lib/connectors_codex.py` (on Codex, `codex_args`
  and its shape check `codex_connector_args_valid` in `lib/launch/codex.sh` change together),
  `docs/design/connectors.md`, and a run of `tests/sandbox.sh mac live` (its connectors part).
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
  overrides. The guide in `docs/` if users should know. Renamed or removed: a row in `CONFIG_RENAMES`
  (`lib/migrations.py`), which `bosun update` applies to `bosun.env`. A new key whose default would
  change what an older install did, or that needs a word in the update's plan and summary: an entry
  in `CONFIG_ADDED` there (its kept value, written to `bosun.env`, its note, and for a kept value the
  unset note that the plan, the summary and `bosun doctor` say while `bosun.env` does not set it). A change to one of my own files
  (`sessions.yml`, `policy.md`, `tasks/`) is never made for me: a row in `USER_FILE_CHANGES` there.
- **Any substantial change** (an agent or OS, install steps, what runs where, a default, a command): every doc
  it affects changes in the same pull request, not later. Grep for what it touches ("Claude Code", "macOS
  only", old flags, install lines) across the README, `docs/`, `ROADMAP.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  `examples/` and `.github/` templates, and say in the pull request which changed.
- **An OS to support:** a profile in `lib/platforms/` with the same functions as the others
  (`tests/unit/test_preflight.py` checks); what else still assumes macOS is in `docs/design/init.md`.
- **An agent to support:** `docs/design/harnesses.md#adding-another`. Shared code asks the `AGENTS` table, the
  agent's `Profile` or a module per agent; an `if` on an agent's name in shared code means something belongs in
  its module or profile, and `tests/unit/test_agent_names.py` fails on it.
- **A Codex fact changes** (after a Codex update): `lib/codex_state.py`, the note at its top,
  `tests/sandbox/codex-stand-in` so the stand-in does it too, and `docs/design/harnesses.md`.
- **An agy fact changes** (after an `agy` update or a live run): `lib/agy_state.py`, its docstring,
  `tests/sandbox/agy-stand-in` so the stand-in does it too, `tests/fixtures/agy/` (the recorded hook payloads,
  screens and listings) and `docs/design/antigravity.md`.
- **A file bosun writes in its state or config folder, or anything it leaves elsewhere:** a row in `STATE_FILES`
  or `CONFIG_FILES` (`lib/uninstall_plan.py`), or a step there, or uninstall leaves it behind. The smoke suite
  fails on a file in the state folder that the table does not list. A change in an agent's own files goes on
  record in `lib/agent_changes.py`.
- **An answer `bosun init` offers:** a field of `Answers` (`lib/init_plan.py`) with its check, what
  it recommends, the key in `env_groups` if it sets one, the table in `docs/design/init.md` and a
  test in `tests/unit/test_init_plan.py`.
- **A macOS tool bosun starts to use:** a stand-in in `tests/sandbox/macos/`, so `tests/sandbox.sh` still
  passes without it, and a check in `tests/install_e2e.sh` for the real one.
- **A new kind of file written by hand:** its linter in `.pre-commit-config.yaml` and `pyproject.toml`'s
  dev group, here under Tooling. If it is glue code in Python, add it to mypy's `files` and to the
  complexity hooks, which do not cover `tests/`. A file in a new place needs a rule for the commit
  hook's test selection (`impacted_map` or `impacted_invalidate_all` in `pyproject.toml`), or
  `tests/unit/test_impacted_rules.py` fails.
- **A unit test that runs bosun's programs, or reads the docs or another repository file:** its line
  in `impacted_map` (`bin/bosun=<test>`, `mkdocs.yml=<test>`, ...); `tests/unit/impacted_guard.py`
  fails the test until it is there and says which. A test importing a helper or another test file of
  `tests/unit` needs nothing: the plugin follows it.
- **A menu item** (bosun.app): the item and its state in `MenuModel.swift` (and its dialog in `DialogSpecs.swift`, if it
  opens one), the `bosun` command it runs in `BosunCLI.swift` (a new command is a command first: `libexec/`, its
  `# Usage:` header, `docs/commands.md`, and a `--json` output if the app reads it, with its `CONTRACT` grown, never
  changed), the contract fixture (`uv run python tools/contract_samples.py`, which `tests/unit/test_contract_samples.py`
  checks), the VM suite's step (`tests/app_e2e.sh`, run with `tests/sandbox.sh mac app`) and the guide (`docs/setup.md`,
  "The menu bar app").
- **A command:** `libexec/bosun-<cmd>` with a `# Usage:` header that holds all of its help
  (`bin/bosun` prints it for `--help`, with no flag handling of the command's own for exactly that;
  `die_usage` reports usage errors with exit 2), a line in `bin/bosun`'s usage, the
  table in `docs/commands.md`, and smoke tests.
- **A command or background job that writes a store** (the ledger, the knowledge base): it runs
  under `update_gate.guarded` (`lib/update_gate.py`), or catches `WriteRefused` and fails its run in
  one line.

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
- Swift (the menu bar app in `app/`; three hooks on `files: ^app/`, so a Python-only commit pays nothing, each
  run only on a Mac and skipping with one line elsewhere or when `swift` is absent, through `tools/swift_hook.sh`:
  CI's Linux `lint` job has a `swift` that cannot build AppKit, and the macOS `app` job runs them): `swift format lint --strict` (the
  toolchain's swift-format, `app/.swift-format`), SwiftLint (the SwiftLintPlugins package's command plugin, its version
  pinned in `app/Package.resolved`; `app/.swiftlint.yml` has the repo's limits: cyclomatic 10, function body 60, type
  body 300, file 400; `force_unwrapping`, `force_try` and `force_cast` errors in Sources, allowed in Tests; no `print`
  in Sources) and `swift test -Xswiftc -warnings-as-errors`. Swift 6 language mode, so strict concurrency. The floor is
  95% of the lines of each of `Sources/BosunMenuCore` and `Sources/BosunMenuSystem`: `cd app && swift test --enable-code-coverage -Xswiftc
  -warnings-as-errors`, then `uv run python tools/swift_coverage.py` (it reads `llvm-cov export -summary-only` and
  names the files under the floor). While iterating run `swift test --filter <suite>`, not the lot. When a limit
  trips, split the function, as for Python.
- Everything else written by hand: hadolint (Dockerfiles), actionlint and zizmor (workflows; actions are
  pinned by commit, with the tag in a comment that Dependabot keeps up to date), the Dependabot config's
  schema, valid XML in the plist templates (Apple's `plutil` is more lenient than XML), `typos`
  (`_typos.toml` lists words it should leave alone), `tools/check_links.py` (relative links and
  anchors between the docs, as GitHub resolves them) and `mkdocs build --strict` (the docs site: every
  page in `nav`, links and anchors on the site, and no list glued to the paragraph above it, which the
  site would show as one paragraph).
- Hygiene: whitespace, line endings, JSON/TOML/YAML syntax, shebangs vs executable bits, symlinks,
  credentials and private keys, `uv.lock` in sync, and no commit directly on `main`.
- Publishing: detect-secrets against the audited `.secrets.baseline` (after a reviewed false
  positive: `uv run detect-secrets scan --baseline .secrets.baseline`, then `uv run
  detect-secrets audit .secrets.baseline`), and `tools/forbidden_terms.py` on staged files and
  commit messages (see Publishing below).
- The unit tests the change can reach, in parallel (`pytest -n auto --impacted`, pytest-impacted
  and pytest-xdist): the change is what git has uncommitted, so `pre-commit run --all-files` selects
  the same. Before a push, the smoke suite (end to end only; skipped if this exact tree passed it here
  in the last 24 hours); in CI, every unit test (its smoke job, when it is on, always runs the whole suite).

Which tests a change reaches: the plugin follows Python imports from `lib/` (an import root, as
pytest's `pythonpath` entry; `impacted_module = "lib"`) and between the test files and the helpers of
`tests/unit`, which has no `__init__.py`; everything else is a rule in `pyproject.toml` (`impacted_map`, `impacted_invalidate_all`), read by
`tests/unit/impacted_rules.py`. A rule may select more than it needs, never less: when unsure, a glob
in `impacted_invalidate_all`. A change to bosun's programs (`lib/*.sh`, `libexec/`, the installers)
or to any `lib/*.py` counts as one to `bin/bosun`, which selects the tests that run or read the
programs; a docs page counts as one to `mkdocs.yml`, which selects the tests that read the docs;
what bosun reads as it runs (`orchestrator/`, `templates/`, `console/`, `examples/`, `agents/`), the
fixtures and `conftest.py` run every test (the plugin leaves a conftest's imports out of its analysis, so
`lib/journal.py` and `lib/change_log.py`, which it imports, select their importers, not every test). A
glob's `**` matches any depth (`docs/**`, `tests/fixtures/**`), `*` one level, both from the right, as
the plugin matches (`pytest_impacted.strategies.matches_any_glob`, which `impacted_rules.matches` calls). `tests/unit/test_impacted_rules.py` runs on every
commit and fails on a file no rule covers, and on a test the plugin would not select for a module
it imports (through another test too: that is a map line); `tests/unit/impacted_guard.py` fails a
test that reads or runs a file whose change would not select it, and says which line to add. The
unit tests flush the journal with fsync, not F_FULLFSYNC (`conftest.py`): in parallel, F_FULLFSYNC
queued every worker behind the drive.

CI (`.github/workflows/ci.yml`) runs the same on every pull request to `main` and on every merge group
of the merge queue (`merge_group`: without it the queue waits forever for checks that never report): all the hooks
(`lint`; the forbidden-terms list comes from the `FORBIDDEN_TERMS` Actions secret, and the pull
request's title, description and commit messages are checked too; for a merge group only the commit
messages between its `base_sha` and `head_sha`), every unit test in parallel on
Python 3.13 to 3.15 with a floor under coverage (`test (<version>)`, never `--impacted`), every unit
test again on macOS and then the end-to-end smoke suite (`smoke`, which is off until the
feature-complete MVP: its job-level `if` in `ci.yml` wants the repository variable `CI_SMOKE` set
to `true`, and it is not a required check in `.github/rulesets/main.json`; to turn it back on,
remove that `if` (or set the variable), re-add `smoke` to the ruleset's required checks and apply
it; the pre-push hook still runs the suite), the menu bar app (`app`, on macOS: `swift test`
with warnings as errors, the coverage floor, SwiftLint, `swift format lint --strict`, and an ad hoc build whose
bundle must verify; a cheap Linux job, `changes`, compares the pull request or merge group with its base, and `app`
`needs` it and is skipped (no macOS runner starts, and a skipped job counts as passing) when nothing under `app/`,
`tools/contract_samples.py`, `tools/swift_coverage.py`, the workflow, `.github/actions/setup-uv/`, `uv.lock` or
`pyproject.toml` changed, so it can be required on every pull request, as `changes` is, so that a failed `changes`
cannot skip `app` past the ruleset), and
gitleaks over the pull request's or merge group's commits (`gitleaks`; a made-up value in a test carries a
`gitleaks:allow` comment). `.github/workflows/install.yml` runs the install for real on a clean macOS
runner and the Docker sandbox, when the install could have changed and weekly; those are not required
checks, because a required check that did not run blocks a merge. `.github/workflows/release-app.yml`, on a tag
`app-v<app/VERSION>`, signs, notarizes and publishes bosun.app (steps for the person: `docs/development.md`,
"Releasing bosun.app"); it checks the signed bundle with `lib/app_release.py requirement` and its final zip
with `lib/app_install.py verify-archive`, which is the install's own code, never a copy of it. `.github/workflows/docs.yml`
publishes the docs site to GitHub Pages from `main` (the repository's Pages source is GitHub Actions). The
logs are public, so neither check prints what it matched. `main` takes changes only through a pull
request with those checks green, CodeQL's results clean (GitHub's default setup; no error alerts, no
high or critical security alerts) and every review thread resolved (the ruleset also asks for a
Copilot review on each push, which takes effect once Copilot code review is enabled for the
organization), and then through the merge queue: `gh pr merge --squash` adds the pull request to the
queue once its checks pass, and the queue builds it on top of the latest `main` and the pull requests
ahead of it, runs the required checks on that merge group's commit and squash-merges it when they
pass (the ruleset's `merge_queue` rule; so `strict_required_status_checks_policy` is off, the queue
being what "up to date with `main`" was for, and nobody updates a branch by hand; CodeQL's default setup does not run on merge groups, and GitHub's
docs say the ruleset's code scanning protection does not apply to them, so CodeQL gates the pull
request only; a merge group's checks may take 60 minutes, `check_response_timeout_minutes`, above the `app` job's
30 and the wait for a macOS runner). The release gate
(`tools/release.py gate`) therefore reads the required checks on main's head commit itself, the merge
group's, and falls back to the pull request's for commits merged before the queue. The rules are
in `.github/rulesets/main.json`. A new or renamed job goes in its list too, applied with `gh api -X PUT
repos/{owner}/{repo}/rulesets/<id> --input .github/rulesets/main.json`. Dependabot
(`.github/dependabot.yml`) opens monthly grouped updates of the actions, `uv.lock` and the hook
revisions, and security updates as advisories come out. `.github/workflows/dependabot-auto-merge.yml`
enables auto-merge (squash) on a Dependabot pull request that is a patch or minor update of a GitHub
Action, the sandbox's Docker base image, a pre-commit hook revision or a uv development dependency; a
major update, a production dependency or anything not opened by Dependabot waits for a person. Auto-merge
adds the pull request to the merge queue once its required checks pass (the repository setting "Allow
auto-merge" must be on). A new ecosystem in `dependabot.yml` goes in that workflow's condition too.

When a limit trips, split the function rather than raising the limit. Suppress a finding only
with an inline comment that says why (as for the jq and GraphQL strings). The formatters rewrite
files in place; that is fine for this repo's only long-running script (`checkin --loop`, whose
loop bash has already parsed), but for hand edits to `libexec/` keep to atomic replacement.

Add a unit test in `tests/unit/` for any change to `lib/*.py` or `bosun-resolve`; the commit hook
runs the ones the change reaches, and `uv run pytest -n auto` runs them all (about a minute). Always
run `tests/smoke.sh` (hermetic: temp HOME, own tmux socket, fake Claude): end-to-end checks,
including that task files only use valid ledger transitions and that the task table in
`docs/orchestrator.md` matches them; `--with-unit` runs every unit test first too, with a plain
`python3 -m unittest`. Then `bosun doctor`. While iterating, run only the area you changed: `tests/smoke.sh --list`
prints the sections' ids, `tests/smoke.sh --only <id>[,<id>...]` runs the shared setup and those (and what they
`needs`) in seconds (a few minutes for a section with prerequisites), and says in its summary that it was a
partial run. While iterating use `--only`, `--last-failed` (`--lf`: only the sections whose last recorded
result, in the git-ignored `tests/.smoke-cache/`, was a failure) and `-x` (stop at the first failing check);
before committing, run the whole suite once with none of them. CI's smoke job, when it is on, runs all of it,
and so does the pre-push hook (`tests/smoke.sh --pre-push`) unless that exact tree (uncommitted and untracked
files included) passed a whole run in this checkout in the last 24 hours (`BOSUN_SMOKE_NO_CACHE=1` forces it;
what the record is and why it can only make a run longer: `docs/development.md`). A test must not write into
the repository: the run then records no pass and names the file. In `tests/smoke.sh`, a new section begins with
`section <id> "<name>" [needs <id>,...]` (unique kebab-case id; `tests/unit/test_smoke_sections.py` fails
otherwise): set up what it uses itself, or say `needs` the section whose state it relies on; a helper or a
constant that more than one section uses goes in the helpers by the fixture HOME, and an export every later
section sees goes after an `always_run` line. Every section starts by checking that `osascript`, `aws`,
`launchctl` and the other tools that reach outside the run resolve to stand-ins in the run's folder, and the run
stops if not (`fakes_first`): a stand-in with a behaviour replaces the plain one in its section and puts it
back, and a section that points bosun at another HOME puts the plain ones there too (`stand_ins_in <home>`, then
`fakes_first <home>`). The temp HOME and the stand-ins exist before the first section, so nothing runs on the real
one. Use `fails_with "<expected error>" cmd` for refusals (a crash must not count as a pass) and
`wait_for <seconds> cmd` instead of fixed sleeps; external tools (aws, gh, launchctl, pmset,
osascript) are faked in the temp HOME's `~/.local/bin`, and the install test uses its own launchd
label and refuses to run unless the fakes come first on PATH.

**Never test an install on the machine you are running on: `install.sh`, `bosun init --recommended`,
`get.sh`, `bosun update` and `bosun uninstall` change launchd, `pmset`, the install and the config for
real.** Use the cheapest sandbox that can show what
you changed (the table and how to set them up: `docs/development.md`, "Which sandbox for what"):

- `tests/sandbox.sh [init|unit|smoke|all|shell]`: a throwaway Linux container with a read-only copy of the
  checkout. `init` walks a new user's first day with no network (`tests/sandbox/fresh_user.sh`, in the
  fictional world of `world.sh`), through a real terminal (`drive.py`). Docker on a Mac is Linux, so
  `tests/sandbox/macos/` holds stand-ins: it proves bosun's logic, never launchd or the Keychain.
- `tests/sandbox.sh mac [setup|run [--from-nothing]|live [claude|codex]|shell|clean]`: a real throwaway Mac in a Tart VM (`live`: the real agents, about a dollar a run)
  (`tests/sandbox/mac.sh`), running `tests/install_e2e.sh`, the same script as the GitHub macOS runner.
  Use it for `get.sh`, `install.sh`, launchd, `pmset` and whatever macOS does its own way. A clone costs
  no time or disk, so make one rather than be careful with one. It has already shown what Docker could
  not: a new Mac has no Apple tools, and launchd has no `gui/<uid>` domain until the auto-login is done.
- `tests/sandbox.sh mac work [--base <ref>]`: in the same VM, a week of acme work in the ledger and the knowledge base (`tests/work_e2e.sh`, its days in `tests/sandbox/acme_week.sh`, what it shares with `install_e2e.sh` in `tests/sandbox/e2e_lib.sh`) on the release (a checkout from before versions), converted to this checkout by `get.sh`, a mid-afternoon `bosun update` to the next version (`tests/sandbox/update_patch.sh`) on a year of data (`tests/sandbox/acme_year.py`), going back and forward again, then the clock moved, writers racing, writes cut short, the knowledge files changed outside their journal, a person forgotten.
- `tests/sandbox.sh mac app [--from build|release]`: in the same VM with its desktop session, bosun.app (built on this Mac with `app/build.sh --identity -`, never started here) driven through every state and choice of its menu by its test hooks, a restart of the Mac between the two parts, the app moved to the Trash and an uninstall (`tests/app_e2e.sh`, which shares the update's origin and the console and status helpers in `tests/sandbox/e2e_lib.sh`; the update's third release asks the app to quit, `tests/sandbox/update_quit_probe.sh`, to see what macOS says to a quit that an update the app started makes). `--from release` has `bosun init` install the notarized app.
- `bosun wake-test` on real hardware: the only proof of a real wake, because a macOS guest cannot sleep and
  `tart suspend` does not make launchd catch up. Check a job's time in the VM by moving its clock.

Those scripts install for real, so they refuse to run outside their machines. A test never reaches a real
tool either: `tests/unit/test_init.py` fails on any command but git and ruby, after one reached a real
`gh auth login`.

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
- One exception: the publisher's Team ID and the app's bundle id (the publisher's reverse-DNS id) in
  `app/publisher.json` are public identifying values the repo may hold, there and nowhere else (`bosun app
  install` checks the downloaded app against them, and they are public in every signed app). Anything else that
  needs one reads it from that file. The file's `releases` names this project's own repository, as get.sh and the
  README do. It does not loosen the forbidden-terms check for anything else.
- The `forbidden-terms` hook enforces this locally from `$BOSUN_CONFIG_DIR/forbidden-terms.txt`
  (one case-insensitive regex per line, `#` comments), so the list itself never enters the repo;
  without the file it passes. Add a term there whenever something site-specific nearly slipped in.
  To check the whole tree: `git ls-files | xargs uv run python tools/forbidden_terms.py`.

## Rolling a change out to running sessions

On an install `get.sh` made, `bosun update` does all of this itself (the re-reads, the restarts, and
the handed tasks it lists for `bosun delegate --update`; `docs/design/update.md`). The steps below are
for a checkout of your own, or a change made by hand:

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
- CI's `lint` runs mypy on Linux, whose type stubs lack macOS-only names (`fcntl.F_FULLFSYNC`): reach
  one through a module constant (`journal.FULL_FSYNC`, `None` elsewhere), and check with `uv run mypy
  --platform linux`.
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
- Model and effort, on Claude Code: launch passes `--model` and the effort as `--settings '{"effortLevel":...}'`
  (before the orchestrator's long `--append-system-prompt`, so `claude_state.launch_settings` can read
  them back from `ps`). Not `--effort` or `CLAUDE_CODE_EFFORT_LEVEL`: those beat `/effort`, so you
  could not change effort inside the session. Never paste `/model` or `/effort` into a session:
  both save the choice to `~/.claude/settings.json` for every future session. Per-task effort for a
  running session goes through a subagent (the Agent tool takes `model` and `effort`).
- Only paste into a Claude whose status `agent_takes_input` accepts. `waiting` means a permission
  prompt or question is open, and the Enter that `send` presses would answer it.
- tmux windows inherit the environment of whatever started the tmux server, hence the dedicated
  server (`BOSUN_TMUX_SOCKET`). Static `AWS_*` credentials leaked that way override SSO
  profiles.
- A pane's launch shell (`<shell> -lic '... launch <window> ; exec <shell> -l'`) is interactive but
  never shows a prompt, so helpers the user's startup files fork off (a prompt theme's git-status
  daemon) outlive it, adopted by launchd. `lib/orphans.py` finds them by exact command line and
  terminal; `launch`, `checkin --loop`, `relaunch_in_window` and `stop` stop them. Never kill
  processes by a pattern match: the tmux server's own command line contains the launch string.
- Codex's report at the end of a turn fires for its hidden helpers too, and lists every prompt of the session
  each time; Enter on its trust screen says yes. Facts like these only come from running the real thing:
  `tests/sandbox.sh mac live codex`. The rest are in `docs/design/harnesses.md`.
- Claude Code internals this relies on (not public APIs) are all in `lib/claude_state.py`: session
  records in `~/.claude/sessions/<pid>.json`, transcript `custom-title` entries, error replies
  (`isApiErrorMessage`, `error`, `apiErrorStatus`), `claude auth status --json`, and
  `projects[<dir>].hasTrustDialogAccepted` in `~/.claude.json` (trust is per git repo since 2.1.232;
  no flag skips the prompt). After a Claude Code update, check with `bosun doctor` and
  `bosun refresh --dry-run`.
- When testing outward-facing features, never post to shared Slack channels; DM yourself.
