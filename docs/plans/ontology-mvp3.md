# Ontology MVP 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the agents use the ontology and keep it true. A delegated brief carries the facts for its repo and area.
The orchestrator looks before it asks. Every Claude window records the standing facts and rules you state, in your
own words, confirmed when those words check out against its own transcript. And a realistic fictional world (three
repos, about thirty facts) proves all of it in the macOS VM with the real agents.

**Architecture:** one config key, `BOSUN_KNOWLEDGE_ENABLED` (default `0`, recommended by `bosun init` with the other
proactive parts), switches the first two of these on; the third is always on:

1. `bosun delegate` appends a context pack (`lib/knowledge_query.py`) to each new brief.
2. One rules file, `orchestrator/knowledge.md`, is appended to the orchestrator's system prompt, and to the
   system prompt of every repo window whose agent can check your words (Claude).
3. `kg` gains a trustworthy notion of "the session that ran me": the nearest ancestor process with an agent
   session record. A quote of your words is checked against that session's transcript, and only recent prompts
   count. From inside a session, confirming and rejecting need such a quote too (Decision 8 of the design).

**Tech stack:** Bash 3.2 and Python 3.13 (stdlib), Claude Code's session records and transcripts through
`lib/claude_state.py`, unittest/pytest, `tests/smoke.sh`, `tests/llm/free_text.py`, the Tart VM
(`tests/sandbox.sh mac live claude`).

**Spec:** `docs/design/knowledge.md` (accepted; MVP 3 in "How it gets in", "How it is used", Decisions 3 and 8, and the
phasing table). The calls this plan makes where the design is silent are listed under "My calls" and go into the
design note in Task 8.

## My calls (where the design is silent)

1. **One key for packs and the rules,** named like the other proactive parts (`BOSUN_EVENTS_ENABLED`,
   `BOSUN_LEARN_ENABLED`). Its default is `0`, so briefs and system prompts stay as they were (no `CONFIG_ADDED`
   entry). `bosun init` recommends `1`. The quote check (3 below) is always on, whatever the key says: the safer
   reading of Decision 8. The design's "packs become the default once measured" stays open.
2. **"The session that ran me"** is found by walking up from `bosun kg`'s own process to the nearest ancestor that an
   agent module recognizes as a session (`record(pid)`), through the `AGENTS` table. Inside such a session:
   - `--transcript` may be left out;
   - one naming another session's transcript is refused;
   - a quote counts only from a genuine prompt of the last 24 hours (`QUOTE_MAX_AGE`).

   With no agent ancestor (a terminal), it behaves as today, but for that 24-hour window, which applies to a
   `--transcript` too.
3. **Confirm, reject and `batch apply` from inside a session** need `--from-user --quote` that verifies. With no
   session around them they work as today, and so does the console's review.
4. **A pack leaves out people's own profile facts.** It keeps facts that link a person to the target ("Dana reviews
   the invoice exporter") and drops a person's own facts: timezone, working hours, preferences, title. This matches
   the home page example's rule (`PERSONAL` in `tools/docs_examples.py`). Identifiers and unconfirmed facts stay out,
   as the design says.
5. **The standing rules reach windows of agents that can check your words:** `Profile.verifies_quotes`, true for
   Claude only. Codex and agy windows still get packs, which travel inside the brief, but are not asked to capture,
   since bosun cannot check their transcripts and Codex's sandbox cannot write the knowledge base.
6. **The fictional world reconciles the names already in use.** `repo/invoice-exporter` gains the strong alias
   `acme/exporter`, the slug the ledger, the VM and the LLM world use. New people get Example/Sample/Placeholder
   surnames and `@example.com` addresses.

## Global Constraints

- Nothing real: no real names, ids, emails, hosts or tokens anywhere (CLAUDE.md "Publishing"). People are `Dana
  Example`, `Lee Sample`, `Marco Placeholder`, `Lena Placeholder`, `Priya Placeholder`, `Sam Placeholder`; mail
  `<first>@example.com`; Slack ids zero-filled.
- With `BOSUN_KNOWLEDGE_ENABLED=0`, a brief, a window's launch command and the orchestrator's system prompt are byte
  for byte what they are on main.
- Packs: at most `kq.DEFAULT_PACK_TOKENS` (1500) per brief in all; confirmed, current facts only; never identifiers.
- Quotes: 1 to `MAX_QUOTE_LENGTH` (500) characters, genuine prompts only (`approvals.is_genuine`), newer than
  `QUOTE_MAX_AGE` (24 hours).
- Shared code never names an agent (`tests/unit/test_agent_names.py`). Agent facts go in the agent's module and its
  `Profile`.
- Bash 3.2 (CLAUDE.md "Gotchas"). Complexity limits as in CLAUDE.md, and split a function rather than raise a limit.
- Wording Claude acts on (`orchestrator/knowledge.md`) is checked with `tests/llm/free_text.py` (Task 6).
- Every user-facing change goes in its guide in the same pull request (Task 8 collects the overview docs).

## Review Focus

1. **A quote that is not mine, or not now.** A window passes another session's transcript, or quotes a prompt from
   last week, or quotes text bosun pasted (a brief, a `bosun send`): none may confirm anything. Tested in Task 1.
2. **A pack that says too much.** A person's timezone or preferences, an identifier, or an unconfirmed fact in a
   brief: tested in Task 2 against a knowledge base that holds all three.
3. **A pack twice, or a hand-over that no longer looks like one.** `delegate --update ... --window` (a move)
   re-renders the brief, and a pack must not start the message (`HANDOVER_OPENINGS`). Tested in Task 2.
4. **The key off changes nothing.** Briefs, launch arguments and the orchestrator's policy are identical with the key
   unset or `0`. Tested in Tasks 2 and 3.
5. **A one-off instruction recorded as a standing rule.** "For this PR, skip the changelog" must not become a fact;
   "from now on, CSV exports never include drafts" must. An LLM case each in Task 6.

---

### Task 1: The session that ran `kg`, and quotes that must be its own and recent

**Files:**

- Modify: `lib/agent_files.py` (`Profile.verifies_quotes: bool = False`), `lib/claude_state.py` (`PROFILE`:
  `verifies_quotes=True`; `session_transcript(record) -> Path | None`), `lib/codex_state.py`, `lib/agy_state.py`
  (`session_transcript` returning `None`, to satisfy the `Agent` protocol), `lib/agent_state.py` (the protocol gains
  `session_transcript`; new `calling_session(pid: int | None = None) -> CallingSession | None`)
- Modify: `lib/knowledge.py` (`user_source`, `quote_verified`, confirm/reject/batch apply), `libexec/bosun-kg`
  (its `# Usage:` lines)
- Test: `tests/unit/test_agent_state.py` (or the file that tests `AGENTS`), `tests/unit/test_knowledge.py`

**Interfaces:**

- Produces: `agent_state.CallingSession(agent: str, pid: int, transcript: Path | None, verifies_quotes: bool)`;
  `agent_state.calling_session(pid=None)` walks `ps -o ppid=` from `pid` (default `os.getppid()`) to the first
  ancestor some module's `record(pid)` recognizes (stale records skipped, as `claude_state.is_stale`), at most 64
  steps; `None` when none.
- Produces: `knowledge.QUOTE_MAX_AGE = timedelta(hours=24)`; `knowledge.session_or_refuse(args) -> Path`, the
  transcript a `--from-user` quote is checked in.

- [ ] **Step 1: failing tests** in `tests/unit/test_knowledge.py` (a fake `calling_session` patched in, transcripts
  written as the existing `AutoConfirmTest` writes them):
  - `test_inside_a_session_the_quote_is_checked_in_its_own_transcript`: no `--transcript`, a typed prompt holds the
    quote, so the fact is confirmed with `source.ref == <that session id>`.
  - `test_inside_a_session_another_transcript_is_refused`: `--transcript <other.jsonl>` raises a `KnowledgeError`
    whose text names "not this session's transcript".
  - `test_a_quote_older_than_a_day_stays_proposed`: the prompt is 25 hours old, so the fact stays proposed and the
    note is printed.
  - `test_outside_a_session_the_transcript_is_still_required`: no ancestor, no `--transcript`, so today's error.
  - `test_confirming_from_a_session_needs_my_words`: `kg confirm f-x` inside a session without `--from-user` is
    refused ("from a session, confirm with --from-user --quote"); with a verified quote it confirms; reject and
    `batch apply` the same.
  - `test_confirming_from_a_terminal_is_unchanged`: no ancestor, so `kg confirm f-x` confirms as before.
  - `test_a_session_of_an_agent_that_cannot_check_quotes_cannot_confirm`: `verifies_quotes=False`, so confirm is
    refused even with `--from-user` ("bosun cannot check your words in <display_name>: confirm from a terminal or
    the console").
- [ ] **Step 2:** `uv run pytest tests/unit/test_knowledge.py -k "session or terminal or older" -q` fails with an
  `AttributeError` on `calling_session`.
- [ ] **Step 3:** build `calling_session` and `session_transcript` in the agent modules (Claude:
  `transcript_path(record["cwd"], record["sessionId"])`). Then in `lib/knowledge.py`:
  - `user_source` uses `session_or_refuse`.
  - `quote_verified` keeps only prompts with `at >= CLOCK() - QUOTE_MAX_AGE`.
  - `confirm`, `reject` and `batch apply` gain `--from-user --quote` and call one guard,
    `require_my_words(args)`, before `km.review`.
  - `supersede --from-user` already refuses an unverified quote; it now uses the same session.
- [ ] **Step 4:** the tests pass, and so do `uv run pytest tests/unit/test_knowledge.py tests/unit/test_agent_names.py
  -q`.
- [ ] **Step 5:** `tests/smoke.sh --only knowledge` passes (it confirms and rejects from no session). Commit
  `kg: a quote of my words is checked in the session that ran kg, recent only; confirming from a session needs one`.

### Task 2: Context packs in briefs

**Files:**

- Modify: `lib/knowledge_query.py` (`pack_targets`, `pack_facts`, `context_pack`), `lib/knowledge.py`
  (`cmd_context` takes several `--for`), `libexec/bosun-kg` usage
- Modify: `lib/delegate.py` (`render`, `check_ids` keeps the items, `render_moved_task`), `libexec/bosun-delegate`
  (passes `BOSUN_KNOWLEDGE_ENABLED`), `lib/common.sh` (`set_knowledge_defaults`: `BOSUN_KNOWLEDGE_ENABLED="0"`),
  `examples/generic/bosun.env` (the same, with a one-line comment)
- Test: `tests/unit/test_knowledge.py` (`ContextTest`), `tests/unit/test_delegate.py` (`RenderTest`, `MoveTest`), and
  the smoke `delegate` section

**Interfaces:**

- Produces: `kq.context_pack(kb: KB, targets: Sequence[str], max_tokens: int = DEFAULT_PACK_TOKENS) -> str` (one
  header per resolved target, one shared budget; a single `str` still accepted, for `kg context`'s callers).
- Produces: `delegate.pack_targets_of(items: Mapping[str, Record]) -> list[str]`: each item's repos
  (`workstreams.item_repos`), then its `fields.area`, then its workstream's `area` (the resolution `workstreams`
  already makes), in that order, without duplicates.
- Produces: `delegate.knowledge_pack(targets: list[str]) -> str` (`""` when the key is off, there are no targets, or
  nothing is confirmed), placed in `render` after the task bodies and before `common_rules()`.

- [ ] **Step 1: failing tests:**
  - `ContextTest.test_owner_slash_repo_finds_the_repo`: `--for acme/exporter` resolves through the alias, and
    `acme/pdf-renderer` without an alias resolves through `repo/pdf-renderer`.
  - `ContextTest.test_a_pack_keeps_who_reviews_but_not_their_profile`: the pack holds "Dana Example reviews invoice
    exporter" and not Dana's timezone, working hours or preferred channel, nor any identifier.
  - `ContextTest.test_several_targets_share_one_budget`: repo plus area stays within 1500 tokens.
  - `RenderTest.test_a_brief_carries_the_pack_for_its_repo_and_area` (key on, an item with `fields.repo` and
    `fields.area`): the pack sits after the task text and before the common rules, and its fact ids appear.
  - `RenderTest.test_with_the_key_off_the_brief_is_unchanged`: byte-identical to `render` without packs.
  - `RenderTest.test_no_confirmed_facts_no_section`: nothing confirmed for the target, so no pack section.
  - `MoveTest.test_a_moved_task_does_not_get_the_pack_again`.
  - `RenderTest.test_the_brief_still_opens_as_a_hand_over`: `HANDOVER_OPENINGS` still recognizes it.
- [ ] **Step 2:** they fail.
- [ ] **Step 3:** build it. The pack section's first line is `What bosun knows about this work (its ontology; cite a
  fact's [id] when you rely on it, and tell me if one is wrong):`. `knowledge_pack` loads the knowledge base
  read-only through `knowledge.load` (a shared lock) and never fails a hand-over: on a knowledge error it adds one
  line `(the ontology could not be read: <reason>)` and goes on.
- [ ] **Step 4:** the tests pass, then `uv run pytest tests/unit/test_delegate.py tests/unit/test_knowledge.py -q`
  passes.
- [ ] **Step 5:** extend the smoke `delegate` section: with the key on and the fixture knowledge base imported and
  confirmed, `bosun delegate new-work --id <task> --ask ... --print` shows the pack for `acme/exporter`, and with the
  key off it does not. Run `tests/smoke.sh --only delegate,knowledge`, then commit `delegate: a brief carries what
  the ontology says about its repo and area (BOSUN_KNOWLEDGE_ENABLED)`.

### Task 3: The standing rules, for the orchestrator and Claude's repo windows

**Files:**

- Create: `orchestrator/knowledge.md`
- Modify: `lib/common.sh` (`orchestrator_policy` appends it under `# The ontology` when the key is on; new
  `window_knowledge_prompt` prints it for a repo window), `lib/launch/claude.sh` (`build_agent_args`, repo branch:
  `--append-system-prompt "$(window_knowledge_prompt)"` after the model and effort arguments, only when the key is on
  and the profile verifies quotes), `orchestrator/settings.json` (allow `Bash(bosun kg lookup:*)`, `search`,
  `facts`, `context`, `propose`, `supersede`, `confirm`, `reject`, `batch:*`), `lib/guard.py` (the substitution
  check covers `kg propose|supersede|confirm|reject|batch`, whose `--quote` is my text)
- Test: `tests/unit/test_guard.py` (`TextCommandTest`), the smoke `orchestrator` section (or the one that checks
  launch arguments, `send-new-start`)

**What `orchestrator/knowledge.md` must say** (its wording is checked by Task 6's cases):

- **Look before asking.** Before asking me who reviews or owns something, where to ask, or how a process goes, run
  `bosun kg lookup <entity>` or `bosun kg search "<words>"`. A confirmed fact answers it: act on it and cite its
  `[id]`. Ask only when there is nothing, or only unconfirmed facts.
- **Record what I state as standing.** A fact or rule about how things work here, in my exact words: `bosun kg
  propose <subject> <predicate> [<object>] [--value "<text>"] --from-user --quote "<my words>"`. Add the entity first
  if it is new (`kg add-entity`). Never a one-off instruction for the task at hand, never what someone else said,
  never an inference.
- **Corrections.** "That's wrong" or "not anymore" closes or replaces the fact: `kg supersede <id> ... --from-user
  --quote`.
- **Confirming.** Confirm or reject waiting facts only when I name them, with my words.
- **Say it in one line.** Tell me what you recorded (`recorded: <fact> [id], confirmed`), or that it stays proposed
  and why.

- [ ] **Step 1: failing tests:**
  - `TextCommandTest.test_a_substitution_in_a_kg_quote_is_refused`: `bosun kg propose x rule --value v --from-user
    --quote "$(cat f)"` is denied, as for `delegate`.
  - Smoke: with the key on, a repo window's launch command carries the rules and the orchestrator's policy ends with
    them; with the key off, neither does (the checks compare the launch command recorded by `lib/launch_record.py`).
- [ ] **Step 2:** they fail.
- [ ] **Step 3:** build it. Keep `orchestrator/knowledge.md` under about 40 lines.
- [ ] **Step 4:** the unit tests pass, and `tests/smoke.sh --only orchestrator,send-new-start` passes.
- [ ] **Step 5:** commit `The ontology's standing rules: the orchestrator looks before asking, Claude windows record
  what I state (BOSUN_KNOWLEDGE_ENABLED)`.

### Task 4: `bosun init` recommends it

**Files:**

- Modify: `lib/init_plan.py` (an `Answers` field `knowledge`, recommended `True`, its key in `env_groups` next to
  `BOSUN_LEARN_ENABLED`), `lib/init_wizard.py` (the "How proactive should it be?" question lists it: "The ontology in
  your agents' hands: briefs carry what it knows, windows record what you state"), `docs/design/init.md` (its table)
- Test: `tests/unit/test_init_plan.py`, `tests/unit/test_init_wizard.py`

- [ ] **Step 1: failing tests:** the recommended answers set `BOSUN_KNOWLEDGE_ENABLED=1`; answering no writes `0`;
  re-running init on a config that has `0` keeps `0`.
- [ ] **Step 2:** they fail. **Step 3:** build it. **Step 4:** they pass, and so does `tests/sandbox.sh init` (the Docker
  walk, about a minute; update its answers if it pins the question's lines).
- [ ] **Step 5:** commit `init: recommend the ontology in the agents' hands`.

### Task 5: A realistic fictional world for the VM

**Files:**

- Modify: `tests/sandbox/live/exporter/` (keep `exporter.py`'s planted quantity bug and its failing test, which
  `live.sh`'s hand-over fixes)
- Create:
  - `tests/sandbox/live/pdf-renderer/` and `tests/sandbox/live/ledger-sync/`
  - `tests/sandbox/live/knowledge.jsonl`
  - `tests/sandbox/live/history.sh` (each repo's scripted commits)
- Modify: `tests/sandbox/live.sh` (`the_repo` loops over the repos, one bare remote each; a `seed_knowledge` step),
  `tests/sandbox/live/gh` (the repo from the working copy's `origin`, not hard-coded), `tests/sandbox/mac.sh` (only if
  a new file lies outside `tests/sandbox/live/`)
- Test: `tests/unit/test_live_world.py` (new): the knowledge file imports into a scratch config with no refusals; the
  facts the scenarios need are confirmed afterwards; every repo's tests pass but the planted one. It needs a line in
  `pyproject.toml`'s `impacted_map`, which `impacted_guard` names.

**The repos** (plain Python, unittest, no dependencies; each with `README.md`, `CODEOWNERS` and 4 to 8 commits by
different fictional authors):

- **`acme/exporter`** (window `exporter`):
  - `exporter/totals.py`: per-line rounding, the caveat.
  - `exporter/tax.py`: rates by country, and rounding.
  - `exporter/credit_notes.py`: negative amounts, the invoice's numbering sequence per customer.
  - `exporter/csv_export.py`: drafts excluded today, though nothing enforces it.
  - `scripts/integration_check.py --env <name>`: reads `config/environments.toml` and writes
    `~/.sandbox/integration-<env>` when it runs.
  - `config/environments.toml`: `staging`, plus `staging-2` marked retired.
  - `RUNBOOK.md`: merge, smoke, staging, canary, then rollout by the deploy bot, announced in #deploys; the hotfix
    path.
  - `CODEOWNERS`: `* @acme/billing`, `exporter/tax.py @dana-example`.
- **`acme/pdf-renderer`:** `renderer/layout.py`, `renderer/vat_breakdown.py`, tests, `CODEOWNERS` (`*
  @marco-placeholder`), and a README noting the exporter calls `render(invoice)`.
- **`acme/ledger-sync`:** `sync/client.py` (retry with backoff on 429), `sync/mapping.py`, tests, `CODEOWNERS` (`*
  @acme/platform`), and a `RUNBOOK.md` (sync paused during the month-end freeze).

**`knowledge.jsonl`** (about 30 facts, imported after `tests/fixtures/knowledge/acme-import.jsonl`, which stays
untouched because the smoke suite counts it). The import's auto-confirm settles some facts. The high-risk ones (`owns`,
`approves`, `gated_by`, `rule`, `status`, `states`) are confirmed by `seed_knowledge` with `kg confirm` from a terminal:

- **Entities and aliases:** `repo/invoice-exporter` gains the alias `acme/exporter`. New entities: `repo/ledger-sync`
  (alias `acme/ledger-sync`), `team/platform`, `person/marco` (Marco Placeholder), `person/lena` (Lena Placeholder),
  `person/priya` (Priya Placeholder), `feature/csv-export`, `feature/credit-notes`, `service/sftp-upload`,
  `environment/staging`, `process/month-end-freeze`, `process/review`, `gate/canary`.
- **People:**
  - Marco is `member_of` platform, `owns` and `reviews` `repo/pdf-renderer`, and `prefers_channel` `channel/acme-prs`.
  - Lena's `role` is "QA sign-off for releases", and the release process has her as its sign-off `actor`.
  - Priya `reviews` `service/sftp-upload`.
  - The billing team `owns` `repo/invoice-exporter`; the platform team `owns` `repo/ledger-sync`.
- **Process:**
  - The release is `gated_by` the smoke tests, and the canary is its step 3, before the rollout.
  - The review process has its `entry_point` in `channel/acme-prs`, and the `rule` "A change to exporter/tax.py
    needs Dana Example's review".
  - The month-end freeze has the `rule` "No exporter or ledger-sync release on the last business day of the month".
- **Product:**
  - The exporter's tax-rounding `rule` ("A change to tax rounding needs a test with a credit note and a
    multi-currency invoice"), now backed by `ledger:` and `report:` sources.
  - Credit notes `lives_in` the exporter, with the `caveat` "Credit notes are negative and reuse the invoice
    sequence per customer".
  - CSV export `lives_in` the exporter (no rule yet: scenario 4 states it).
  - The SFTP upload has the `rule` "per-customer keys only; never a shared password".
  - Ledger-sync has the `caveat` "the ledger API answers 429 under load; retry with backoff" and `depends_on` the
    exporter.
  - Staging has the `purpose` "where every release and test run goes before production".

- [ ] **Step 1:** write `tests/unit/test_live_world.py`. It fails, since the files are missing.
- [ ] **Step 2:** write the repos, `history.sh` and `knowledge.jsonl`. The test passes.
- [ ] **Step 3:** `live.sh`: `the_repo` builds all three, and `ready` passes all three in `bosun init --answers`
  (three windows). `seed_knowledge` imports both files with `--apply --auto-confirm`, then confirms the listed facts.
  The `gh` stand-in names the repo from `git remote get-url origin`.
- [ ] **Step 4:** `tests/sandbox.sh mac live claude` (about 10 minutes, a dollar) still passes all its checks, with
  three windows now.
- [ ] **Step 5:** commit `Sandbox: a fictional acme world for the VM, three repos with history and an ontology`.

### Task 5a: Bounded output and what it costs

Added 2026-11-15 at the user's request ("pay attention to efficiency: context use, input and output tokens, how the
ontology is queried"). Measured on the fixture knowledge base:

- The rules file is about 500 tokens, in the system prompt of the orchestrator and of every Claude repo window.
- `kg lookup` takes 90 to 200 tokens there, but nothing bounds it: it lists every confirmed fact, every link and
  every fact waiting for review.
- `--json` is about twice the text.
- Packs are bounded at 1500.

**Files:**

- Modify: `lib/knowledge_query.py` (`lookup`, `search`), `lib/knowledge.py` (their parsers: `--max-tokens` for lookup),
  `libexec/bosun-kg` usage, `orchestrator/knowledge.md`, `lib/delegate.py` (one log line per pack)
- Test: `tests/unit/test_knowledge.py` (`QueryTest`), `tests/unit/test_delegate.py`

**Interfaces:**

- Produces: `kq.LOOKUP_TOKENS = 800`, `kq.LOOKUP_PENDING = 5`, `kq.SEARCH_HITS = 10`.
- Produces: `kq.lookup(kb, entity_id, max_tokens=LOOKUP_TOKENS) -> str`. It lists confirmed facts and links within the
  budget, then `({n} more: kg facts {entity})`. Waiting facts are listed at most `LOOKUP_PENDING`, then `(+{n} more
  waiting for review)`. `kg lookup --json` stays complete, for scripts.

- [ ] **Step 1: failing tests:**
  - `QueryTest.test_a_lookup_stays_within_its_budget`: an entity with 200 confirmed facts prints at most 800 tokens
    and ends with the "more" line.
  - `QueryTest.test_a_lookup_lists_five_waiting_facts_and_counts_the_rest`.
  - `QueryTest.test_lookup_json_is_complete`.
  - `QueryTest.test_search_lists_at_most_ten_hits`.
  - `PackTest.test_a_pack_is_logged_with_its_size`: one line naming the targets, the facts and the estimated tokens,
    in the log the delegate already writes to (or `$BOSUN_LOG_DIR/delegate.log`, through the existing helper).
- [ ] **Step 2:** they fail.
- [ ] **Step 3:** build it.
  - **Rules file:** tighten `orchestrator/knowledge.md` to at most about 300 tokens (`estimate_tokens`), keeping its
    five points.
  - **Two new lines in it:**
    - "A brief's ontology section already holds what is known for its repo: look up only what it lacks."
    - "Read the text output; never `--json`."
  - **Add a unit test** that the file stays within 300 tokens.
- [ ] **Step 4:** run `uv run pytest -n auto tests/unit/test_knowledge.py tests/unit/test_delegate.py </dev/null`, then
  `timeout 590 tests/smoke.sh --only knowledge,delegate,orchestrator`.
- [ ] **Step 5:** commit `kg: bounded lookups and searches, a shorter rules file, each pack's size logged`.

**Measured afterwards:**

- **Task 6** reports each case's cost and turns (`free_text.py` already prints them), with the K cases run once more
  with the key off as a baseline.
- **Task 7** reports each brief's pack size and the number of `bosun kg` calls per window, from the transcripts.

### Task 6: The real-Claude cases

**Files:**

- Modify: `tests/llm/world.py` (seed the knowledge base from both files; a knowledge snapshot for `Outcome`; the repo
  copied from `tests/sandbox/live/exporter` instead of `REPO_FILES`, if that keeps the existing cases green),
  `tests/llm/free_text.py` (`REPO_ALLOWED` gains `Bash(bosun kg lookup:*)`, `search`, `facts`, `context`, `propose`,
  `supersede`; the orchestrator's system prompt gains `orchestrator/knowledge.md`, as with the key on),
  `tests/llm/cases.py`

**The cases** (assert on the calls Claude made, which the world's `bosun` wrapper logs, and on the ledger. A
`--from-user` write fails in the rehearsal, which has no session transcript, so the call's arguments are what is
checked):

- **K1, routing (orchestrator):** "who should review a change to the pdf renderer, and where do I ask?" Passes if a
  `kg lookup|search|context` call came before the reply, no outreach or proposal was made, and the reply names Marco
  and #acme-prs.
- **K2, a standing rule (orchestrator):** "from now on, CSV exports never include draft invoices". Passes if `kg
  propose` names `feature/csv-export rule` with `--from-user` and a `--quote` that is a substring of what was said.
- **K3, the same in a repo window:** said mid-task in the exporter window.
- **K4, a one-off is not recorded:** "for this PR, skip the changelog". Passes if no `kg propose` call was made.
- **K5, a correction (orchestrator):** "Lee doesn't review the exporter anymore". Passes if a `kg supersede` of
  Lee's `reviews` fact was made with `--close` (or `--by`), `--from-user` and the quote.
- **K6, a pack used (repo window):** a brief for "round VAT per line in exporter/tax.py" with the pack. Passes if the
  agent's plan or edits mention a credit-note and a multi-currency test (the rule's content), checked in its
  written files or its report.

- [ ] **Step 1:** add the cases and the world's seeding, then `uv run python tests/llm/free_text.py --only K --runs 2`
  (real Claude, about $3 to $6). Adjust the wording of `orchestrator/knowledge.md`, never the cases' expectations,
  until every case passes both runs.
- [ ] **Step 2:** run `uv run python tests/llm/free_text.py --runs 1` over all cases once (the policy grew).
- [ ] **Step 3:** commit `LLM cases: the ontology's lookups, captures, corrections and packs`.

### Task 7: The VM proves it end to end

**Files:**

- Modify: `tests/sandbox/live.sh` (a step `ontology`, run by `mac live claude` after `go`, with the key on via
  `bosun settings` or `bosun.env`), `docs/development.md` ("The real agents": what the step checks, its time and cost)

**The scenarios** (each a `check` in `live.sh`'s style; sent to windows as a person types, never through `bosun
send`, so they count as genuine prompts):

1. **A pack the task needs:** ask the orchestrator to have the exporter window round VAT per invoice line in
   `exporter/tax.py`, and say "go ahead" when asked.
   - The delegation's brief (the repo window's first prompt in its transcript) carries the tax-rounding rule's fact
     id.
   - The pushed branch adds a test naming a credit note and one naming a second currency.
2. **The retired environment:** ask the exporter window to run the integration check before opening its PR.
   - `~/.sandbox/integration-staging` exists and `~/.sandbox/integration-staging-2` does not.
3. **Routing:** ask the orchestrator who should review a pdf renderer change and where.
   - Its transcript has a `bosun kg` lookup before the reply, the reply names Marco and #acme-prs, and the ledger
     has no new outreach item.
4. **Capture:** type "From now on, CSV exports never include draft invoices." into the exporter window.
   - `bosun kg facts feature/csv-export --json` has a confirmed `rule` whose source is `user` with that session's id.

- [ ] **Step 1:** write the step. **Step 2:** `tests/sandbox.sh mac live claude` passes every check (about 25 minutes,
  a few dollars). Record its counts in the pull request.
- [ ] **Step 3:** if a repo window's auto mode blocks `bosun kg propose`, add exactly those `Bash(...)` rules to
  `repo_session_settings` (`lib/common.sh`). Note it in `docs/permissions.md`, and rerun.
- [ ] **Step 4:** commit `VM: the ontology end to end, packs, a retired environment, routing and capture`.

### Task 8: Docs and the record

**Files:**

- Modify: `docs/knowledge.md` (a section "In the agents' hands": the key, packs, lookups, capture, corrections,
  quotes and sessions), `docs/design/knowledge.md` (status: MVP 3 built; "My calls" above as decisions; the
  phasing row), `docs/orchestrator.md` (the ontology rules beside the policy), `docs/permissions.md` (the `kg`
  allow rules; confirming from a session), `docs/commands.md` (`kg`'s row: `--from-user` without `--transcript`),
  `docs/setup.md` (init's question), `ROADMAP.md` (section 4: MVP 3 done; next: measurement with learning's
  knowledge pass, Codex's and agy's transcripts), `CLAUDE.md` (where the rules file is, and `calling_session`),
  `README.md` and `docs/index.md` (check the claims "every agent starts from" and "takes bearings before each task"
  now hold, and say they need the key where they would otherwise mislead)

- [ ] **Step 1:** write them. `uv run pre-commit run --all-files` passes, including the strict docs build and the link
  check.
- [ ] **Step 2:** `tests/smoke.sh` runs the whole suite once, as the merge queue will, and passes.
- [ ] **Step 3:** commit `Docs: the ontology in the agents' hands (MVP 3)`. One pull request for the branch.
