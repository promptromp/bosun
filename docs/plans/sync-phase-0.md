# Sync, phase 0: groundwork Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

Status: planned (2026-11-03).

**Goal:** Both stores keep a per-machine journal of changes that rebuilds `ledger.json` and the
knowledge base exactly, with ids two Macs can never mint alike, so phase 1 only has to move
journal files.

**Architecture:** A pure change-log module (envelope, canonical bytes, ids, hybrid clock, order)
and a journal module (files, sequence numbers, transactions) are shared by both stores. The ledger
turns each write into changes by comparing the document before and after at `locked_ledger`, the
one place every write passes; the knowledge base journals the records it already appends. Pure
rebuild modules turn a journal back into the files, checking the rules a merge cannot keep and
returning conflict records. The journal is the commit point: the files record the journal position
they reflect and are rebuilt when it differs.

**Tech Stack:** bash and stdlib-only Python 3.13 (`hashlib`, `hmac`, `json`, `secrets`, `fcntl`);
unittest-style tests run by pytest; the smoke suite.

**Spec:** [docs/design/sync.md](../design/sync.md) (decisions 1, 3 and 7; Shape; Phases, row 0).

## Global Constraints

- No third-party runtime dependency; stdlib Python 3.13 only. Shell stays bash 3.2 compatible.
- The checks in `CLAUDE.md` pass: ruff (McCabe <= 10), mypy --strict, complexipy (<= 15), lizard
  (cyclomatic <= 10, <= 60 lines), shellcheck, typos, links, `mkdocs build --strict`. Split a
  function rather than raise a limit.
- Fictional data only in tests and fixtures (acme, placeholders); no real names or ids.
- Canonical bytes: `json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`;
  a record holding a float is refused.
- Clock text: `f"{ms:013d}.{counter:04d}"`; changes sort by `(hlc, replica, id)`.
- Id suffix: six lowercase Crockford base32 characters (`0123456789abcdefghjkmnpqrstvwxyz`).
- Unknown field paths are class `local` (fail closed).
- New files in the state dir get rows in `STATE_FILES` (`lib/uninstall_plan.py`); the smoke suite
  fails otherwise.
- Every doc a task affects changes in the same task (README, `docs/`, `CLAUDE.md`).
- Existing ids (`task:2026-10-15/1`, `f12`) stay valid; new ones use the new form.
- No config keys in this phase: nothing here is optional.
- Never run `install.sh`, `bosun init --recommended`, `get.sh` or `bosun uninstall` on this Mac.

## Review Focus

1. **The first write on an existing ledger** (old numeric ids, trimmed histories, pruned items,
   approvals with quotes, `said`): the genesis snapshot must rebuild it byte for byte. Pinned in
   Task 5 over the acme fixture.
2. **A crash between the journal append and the file rewrite:** the next write must rebuild, with
   the change present once. Pinned in Task 5.
3. **Twenty concurrent writers** (the smoke suite's existing case): contiguous sequence numbers, no
   lost or doubled change, `ledger validate --journal` clean. Pinned in Task 5.
4. **The wall clock going backwards** (NTP, a manual change): the hybrid clock still increases.
   Pinned in Task 1.
5. **A command that edits a history entry in place:** `ledger validate --journal` must report the
   difference, never pass silently. Pinned in Task 6.

## File structure

| File | Responsibility |
|---|---|
| `lib/change_log.py` (new, pure) | Envelope, canonical bytes, change ids, hybrid clock, order, id suffixes |
| `lib/journal.py` (new) | Replica identity; a stream's journal files; transactions under the caller's lock |
| `lib/ledger_changes.py` (new, pure) | The field table, `diff`, `rebuild` with conflicts, history trimming (moved from `ledger.py`) |
| `lib/knowledge_changes.py` (new, pure) | Knowledge edits from a `KB`, and the knowledge rebuild |
| `lib/ledger.py` | `locked_ledger` journals; ids minted; `ledger validate` |
| `lib/knowledge.py`, `lib/knowledge_model.py`, `lib/knowledge_store.py` | Journal on save; minted fact ids; `prev` on updates; keyed forgotten hash; `kg validate` |
| `lib/learn.py` | The change lives on the `learn:` item; ids from the finding |
| `lib/console_kb.py` | Reads through `knowledge.load` |
| `lib/uninstall_plan.py` | `journal`, `replica.json` rows |
| `tests/unit/test_change_log.py`, `test_journal.py`, `test_ledger_changes.py`, `test_knowledge_changes.py` (new) | Their modules |

Part A (Tasks 1-2) is shared. Part B (Tasks 3-7) is the ledger and can ship as one pull request.
Part C (Tasks 8-11) is the knowledge base and can ship as a second.

---

## Part A: the change log

### Task 1: Change log primitives

**Files:**
- Create: `lib/change_log.py`
- Test: `tests/unit/test_change_log.py`

(mypy and the complexity hooks already cover all of `lib/` and `tests/unit/`.)

**Interfaces:**
- Produces:
  - `ENVELOPE_VERSION = 1`; `Change = dict[str, Any]`; `class ChangeLogError(Exception)`
  - `@dataclass(frozen=True) class Edit(op: str, target: str, body: dict[str, Any], local: bool)`:
    a change before the journal gives it an envelope
  - `@dataclass(frozen=True) class Conflict(change: str, target: str, kind: str, detail: str)`:
    a change the rebuild kept but did not apply; `kind` one of `"state"`, `"link"`, `"field"`,
    `"deleted"`
  - `canonical(record: Mapping[str, Any]) -> bytes`
  - `change_id(record: Mapping[str, Any]) -> str` (SHA-256 hex of `canonical` of the record
    without its `id`)
  - `tick(last: str, now_ms: int) -> str`
  - `observe(last: str, seen: str, now_ms: int, max_skew_ms: int) -> tuple[str, bool]` (the new
    clock, and whether `seen` was too far ahead to adopt)
  - `order_key(change: Mapping[str, Any]) -> tuple[str, str, str]`
  - `in_order(changes: Iterable[Change]) -> list[Change]` (deduplicated by `id`, sorted)
  - `mint_suffix(replica: str, seed: str, n: int = 0) -> str`

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_change_log.py`:
  - `test_canonical_is_sorted_and_compact`: `canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()`
  - `test_canonical_refuses_floats`: `canonical({"x": 1.5})` raises `ChangeLogError`; so does a
    nested float
  - `test_change_id_ignores_its_own_id`: `change_id({"a": 1}) == change_id({"a": 1, "id": "x"})`
    and is 64 hex characters
  - `test_tick_advances_with_wall_time`: `tick("0000000001000.0003", 2000) == "0000000002000.0000"`
  - `test_tick_survives_a_clock_going_back`: `tick("0000000005000.0001", 4000) == "0000000005000.0002"`
  - `test_observe_moves_past_what_it_saw`: `observe("0000000001000.0000", "0000000003000.0004",
    2000, 600_000) == ("0000000003000.0005", False)`
  - `test_observe_flags_a_far_future_clock`: a `seen` 11 minutes ahead with `max_skew_ms=600_000`
    returns `(tick(last, now_ms), True)`
  - `test_in_order_sorts_and_deduplicates`: three changes given shuffled with one duplicated come
    back once each, by `(hlc, replica, id)`
  - `test_mint_suffix_is_stable_and_short`: same arguments give the same six characters from the
    Crockford alphabet; changing any argument changes it

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_change_log.py -q`
  Expected: errors importing `change_log`.

- [ ] **Step 3: Implement `lib/change_log.py`.** The clock follows Kulkarni et al.'s hybrid
  logical clock; a counter past 9999 moves to the next millisecond. `observe`:

  ```python
  def observe(last: str, seen: str, now_ms: int, max_skew_ms: int) -> tuple[str, bool]:
      seen_ms, seen_counter = parse_clock(seen)
      if seen_ms - now_ms > max_skew_ms:
          return tick(last, now_ms), True
      last_ms, last_counter = parse_clock(last)
      top = max(last_ms, seen_ms, now_ms)
      if top == last_ms == seen_ms:
          counter = max(last_counter, seen_counter) + 1
      elif top == last_ms:
          counter = last_counter + 1
      elif top == seen_ms:
          counter = seen_counter + 1
      else:
          counter = 0
      return clock_text(top, counter), False
  ```

  `mint_suffix`: the first 30 bits of `sha256(f"{replica}|{seed}|{n}")`, as six base32 characters.

- [ ] **Step 4: Run the tests to see them pass**

  Run: `uv run pytest tests/unit/test_change_log.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/change_log.py tests/unit/test_change_log.py pyproject.toml .pre-commit-config.yaml
  git commit -m "Change log: canonical records, ids, a hybrid clock and one order"
  ```

### Task 2: Journals and transactions

**Files:**
- Create: `lib/journal.py`
- Test: `tests/unit/test_journal.py`
- Modify: `lib/uninstall_plan.py` (`("journal", LEDGER)`, `("replica.json", LEDGER)` in
  `STATE_FILES`), `tests/unit/test_uninstall_plan.py` if it counts rows

**Interfaces:**
- Consumes: Task 1's `canonical`, `change_id`, `tick`, `in_order`, `mint_suffix`, `ENVELOPE_VERSION`.
- Produces:
  - `REPLICA_FILE = "replica.json"`, `LOCAL = "local"`, `class JournalError(Exception)`
  - `replica_id(state_dir: Path) -> str`: 32 hex characters, created once with `secrets`
  - `@dataclass class Journal(root: Path, stream: str, replica: str, rules: int, now_ms:
    Callable[[], int] = wall_ms)` with:
    - `changes() -> list[Change]`: every `*.jsonl` under `root`, in order
    - `position() -> dict[str, int]`: the highest `seq` per file stem (replica ids and `"local"`)
    - `begin(cmd: str) -> Transaction`
  - `class Transaction` with:
    - `add(op: str, target: str, body: dict[str, Any], *, local: bool = False) -> None`
    - `mint() -> str`: `mint_suffix(replica, f"{private_next}/{local_next}", n)`, `n` counting
      mints in this transaction
    - `commit() -> list[Change]`: nothing when empty; else one `tick` for the transaction, then
      each change gets `v`, `stream`, `replica`, `seq` (its file's next), `hlc`, `tx` (the
      transaction's clock), `cmd`, `rules`, `op`, `target`, `body`, `local` (only when true), and
      `id` last; appended as canonical lines with one `os.write` and `fsync` per file

  Files: `<root>/<replica>.jsonl` (private changes) and `<root>/local.jsonl` (each with its own
  sequence). The caller holds the store's lock; `Journal` takes none.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_journal.py`:
  - `test_replica_id_is_made_once`: two calls on one dir return the same 32-hex id; the file is in
    the dir
  - `test_commit_numbers_each_file_from_one`: a transaction with two private changes and one local
    change gives `seq` 1, 2 in `<replica>.jsonl` and 1 in `local.jsonl`; `position()` is
    `{replica: 2, "local": 1}`
  - `test_an_empty_transaction_writes_nothing`: no file is created
  - `test_every_change_carries_the_envelope_and_a_valid_id`: each line's `id == change_id(line)`,
    and `v`, `stream`, `rules`, `cmd`, `tx` are as given
  - `test_changes_come_back_in_order_across_files`: two journals (two replicas) under one root read
    back by `(hlc, replica, id)`
  - `test_mint_is_unique_and_repeatable`: two mints in one transaction differ; a second journal in
    a copy of the same dir mints the same first suffix
  - `test_a_torn_last_line_is_reported`: a file ending in half a JSON line raises `JournalError`
    naming the file and line

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_journal.py -q`
  Expected: errors importing `journal`.

- [ ] **Step 3: Implement `lib/journal.py`** and the two `STATE_FILES` rows.

- [ ] **Step 4: Run the tests, and the uninstall tests**

  Run: `uv run pytest tests/unit/test_journal.py tests/unit/test_uninstall_plan.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/journal.py tests/unit/test_journal.py lib/uninstall_plan.py tests/unit/test_uninstall_plan.py
  git commit -m "Journal: per-replica change files with sequence numbers and transactions"
  ```

---

## Part B: the ledger

### Task 3: The field table and `diff`

**Files:**
- Create: `lib/ledger_changes.py`
- Test: `tests/unit/test_ledger_changes.py`
- Modify: `lib/ledger.py` (move `MAX_HISTORY` and `trim_history` into `ledger_changes`; `ledger`
  imports them)

**Interfaces:**
- Produces:
  - Classes `LOCAL, SEALED, PRIVATE, TEAM = "local", "sealed", "private", "team"`
  - Rules `SET, STATE, HISTORY, LIST, LINKS = "set", "state", "history", "list", "links"`
  - `@dataclass(frozen=True) class FieldRule(pattern: tuple[str, ...], rule: str, klass: str,
    important: bool = False)`; `*` matches one path segment
  - `FIELDS: tuple[FieldRule, ...]`
  - `classify(path: tuple[str, ...]) -> FieldRule` (first match; else `FieldRule(path, SET, LOCAL)`)
  - `diff(before: Ledger, after: Ledger) -> list[change_log.Edit]`
  - `MAX_HISTORY`, `trim_history(item)` (moved unchanged)

  Ops and bodies (paths relative to the item, as lists):

  | op | target | body |
  |---|---|---|
  | `item.create` | item id | `{"item": <item without its local leaves>}` |
  | `item.delete` | item id | `{}` |
  | `item.set` | item id | `{"path": [...], "value": v, "prev": p}` (a missing leaf is `null`) |
  | `item.state` | item id | `{"from": s, "to": t}` |
  | `item.append` | item id | `{"path": [...], "entry": e}` |
  | `item.drop` | item id | `{"path": [...], "entry": e}` (only for `LIST` rules) |
  | `item.link` / `item.unlink` | item id | `{"rel", "id", "at", "via"?}` |
  | `map.put` / `map.delete` | `sources` or `runs` | `{"key", "value"?, "prev"?}` |
  | `said.append` / `said.drop` | `said` | `{"entry": e}` (always local) |

  The table, in this order:

  | Pattern | Rule | Class | Important |
  |---|---|---|---|
  | `items.*.state` | `STATE` | private | yes |
  | `items.*.history` | `HISTORY` | sealed | |
  | `items.*.links` | `LINKS` | private | |
  | `items.*.title`, `items.*.fields.subject`, `items.*.report.summary` | `SET` | sealed | |
  | `items.*.fields.messages` | `LIST` | sealed | |
  | `items.*.fields.requester`, `items.*.fields.to`, `items.*.fields.answer`, `items.*.fields.details` | `SET` | sealed | |
  | `items.*.proposal.proposed_at`, `items.*.proposal.approval.used_at` | `SET` | private | yes |
  | `items.*.proposal.approval.quote`, `.session`, `.tool_use_id`, `.transcript` | `SET` | local | |
  | `items.*.proposal`, `items.*.proposal.approval`, `items.*.delegation`, `items.*.report` (the whole value, when it appears or goes) | `SET` | private | |
  | `items.*.proposal.*`, `items.*.proposal.approval.*` | `SET` | private | |
  | `items.*.fields.key`, `items.*.fields.system` | `SET` | private | yes |
  | `items.*.kind`, `items.*.url`, `items.*.created_at`, `items.*.updated_at`, `items.*.fields.*`, `items.*.delegation.*`, `items.*.report.*` | `SET` | private | |
  | `sources.*`, `runs.*` | `SET` | private | |
  | `said` | `LIST` | local | |

  Comparison rules: dicts are compared leaf by leaf; a dict that appears, disappears or turns into
  a non-dict is one `item.set` of the whole value at that path; lists follow their rule, and a list
  with no rule is one `item.set`. `HISTORY`: entries in `after` not in `before` (compared as
  canonical bytes) are appended in order; entries that disappeared, and `history-trimmed` markers,
  are ignored. `LINKS`: by `(rel, id, at)`. A leaf whose class is `local` goes in its own
  `item.set` with `local=True`, never in a non-local body, including inside a whole value set at a
  container path. The top-level `journal` key (Task 5) is not diffed. History entries are `sealed`
  whole for now; phase 1's gate may strip only their notes.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_ledger_changes.py`:
  - `test_no_change_no_edits`: `diff(x, deepcopy(x)) == []`
  - `test_a_new_item_is_created_without_its_local_leaves`
  - `test_a_field_change_records_its_prev`: `fields.repo` `"a"` to `"b"` gives
    `Edit("item.set", id, {"path": ["fields", "repo"], "value": "b", "prev": "a"}, False)`
  - `test_a_state_change_records_from_and_to`
  - `test_history_appends_and_ignores_trimming`: an item trimmed by `trim_history` plus one new
    entry gives exactly one `item.append`
  - `test_links_and_unlinks_are_their_own_edits`
  - `test_an_approval_quote_stays_local`: approving a proposal gives a private
    `proposal.approval.used_at`-free set and a local edit for `quote`
  - `test_prune_is_a_delete`; `test_said_is_local`; `test_sources_and_runs_are_map_edits`
  - `test_unknown_field_is_local`: a new `items.*.mystery` leaf gives a local edit
  - `test_every_fixture_field_is_classified`: every leaf path of
    `tests/fixtures/console/acme-ledger.json` matches a `FIELDS` row, not the fallback

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_ledger_changes.py -q`
  Expected: errors importing `ledger_changes`.

- [ ] **Step 3: Implement `FIELDS`, `classify` and `diff`;** move `MAX_HISTORY` and
  `trim_history`.

- [ ] **Step 4: Run the tests and the ledger's existing tests**

  Run: `uv run pytest tests/unit/test_ledger_changes.py tests/unit/test_ledger.py tests/unit/test_ledger_protocol.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/ledger_changes.py lib/ledger.py tests/unit/test_ledger_changes.py
  git commit -m "Ledger changes: a field table and the diff of a write"
  ```

### Task 4: Rebuilding the ledger, with conflicts

**Files:**
- Modify: `lib/ledger_changes.py`, `lib/ledger.py` (a `RULES` value built from `KINDS` and
  `lineage`)
- Test: `tests/unit/test_ledger_changes.py`

**Interfaces:**
- Consumes: Task 1's `in_order` and `Conflict`; Task 3's ops and `trim_history`.
- Produces:
  - `@dataclass(frozen=True) class Rules(allowed: Callable[[str, str, str], bool], link_problem:
    Callable[[Items, Link], str | None])`
  - `@dataclass class Rebuilt(ledger: Ledger, conflicts: list[change_log.Conflict])`
  - `rebuild(changes: Iterable[Change], rules: Rules) -> Rebuilt`
  - `state_hash(ledger: Ledger) -> str` (SHA-256 of `canonical` of the ledger without `journal`)
  - In `lib/ledger.py`: `RULES = ledger_changes.Rules(allowed=..., link_problem=...)`, where
    `allowed(kind, from, to)` is `to in allowed_targets(kind, from)` and `link_problem` wraps
    `lineage.check_link` (its `LineageError` message, or `None`)

  Rebuild rules: apply `in_order(changes)` to an empty ledger
  (`{"version": 1, "items": {}, "sources": {}, "runs": {}, "said": []}`), then `trim_history` every
  item. `item.state` applies when `from` equals the current state and `rules.allowed` holds, else a
  `state` conflict. `item.set` on an important path applies when `prev` equals the current value,
  else a `field` conflict; other sets always apply (last in order wins). `item.link` applies unless
  `rules.link_problem` returns a reason (a `link` conflict); `item.unlink` removes the link with
  that `(rel, id, at)` only. Any change to a deleted item, other than another delete, is a
  `deleted` conflict. `item.create` of an existing id merges: fields the item lacks are added,
  histories are unioned in order.

- [ ] **Step 1: Write the failing tests:**
  - `test_rebuild_of_diffs_reproduces_the_ledger`: run every `cmd_*` the protocol tests use on a
    scratch ledger, diff each write, rebuild: equal to the final `ledger.json` (without `journal`)
  - `test_order_of_delivery_does_not_matter`: the same changes shuffled and duplicated give the
    same `state_hash`
  - `test_two_transitions_from_one_state`: replicas A and B move `review:acme/api#7` from
    `requested` to `delegated` and to `closed`; one applies, the other is a `state` conflict, the
    same on either delivery order
  - `test_a_cycle_made_on_two_machines_is_refused`: A links x `spawned_from` y, B links y
    `spawned_from` x; the later one is a `link` conflict
  - `test_an_approval_used_twice`: two `used_at` sets with `prev` `null`; the second is a `field`
    conflict
  - `test_delete_wins`: a transition after a delete is a `deleted` conflict and the item stays gone
  - `test_a_concurrent_unlink_keeps_a_re_add`: an unlink of `(rel, id, at1)` leaves a link added
    with `at2`

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_ledger_changes.py -q`
  Expected: the new tests fail.

- [ ] **Step 3: Implement `rebuild`, `state_hash` and `ledger.RULES`.**

- [ ] **Step 4: Run the tests**

  Run: `uv run pytest tests/unit/test_ledger_changes.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/ledger_changes.py lib/ledger.py tests/unit/test_ledger_changes.py
  git commit -m "Ledger changes: rebuild in one order, conflicts for the rules a merge cannot keep"
  ```

### Task 5: Every ledger write goes through the journal

**Files:**
- Modify: `lib/ledger.py` (`Store`, `locked_ledger`, `main`), `lib/guard.py`, `lib/hook.py`,
  `lib/review_watch.py` (their command names), `tests/smoke.sh`, `docs/setup.md` (the state folder
  list), `CLAUDE.md` (where things live)
- Test: `tests/unit/test_ledger_protocol.py`, `tests/unit/test_corpus.py`

**Interfaces:**
- Consumes: Task 2's `Journal`, `replica_id`; Task 3's `diff`; Task 4's `rebuild`, `RULES`.
- Produces:
  - `Store.journal_dir` (`state_dir / "journal" / "ledger"`), `Store.command: str` (set by `main`
    to `f"ledger {args.command}"`; by the guard, the hook and review-watch to their names)
  - `ledger.json` gains the top-level key `"journal"`: the `position()` it reflects
  - `RULES_VERSION = 1` in `lib/ledger.py`, passed as the journal's `rules`
  - `current_transaction() -> journal.Transaction` (valid inside `locked_ledger(write=True)`)

  `locked_ledger(write)`, under the existing lock:
  1. Open the journal. If it is empty and `ledger.json` has items: commit a genesis transaction
     (`cmd` `"genesis"`) with an `item.create` per item (history included), a `map.put` per
     source and run, and a local `said.append` per entry.
  2. If `ledger.json`'s `"journal"` differs from `position()`: rebuild, save, and use the rebuilt
     ledger.
  3. Copy the ledger, begin a transaction, yield.
  4. On write: add `diff(copy, ledger)`, commit, set `ledger["journal"] = position()`, `save` (as
     today, even with no edits).

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_ledger_protocol.py`:
  - `test_the_journal_rebuilds_the_ledger_after_every_command`: after the setUp commands and a
    delegation, report and transition, `rebuild(journal.changes(), RULES).ledger` equals
    `ledger.json` without `journal`
  - `test_genesis_reproduces_an_existing_ledger`: copy the acme fixture in as `ledger.json`, run
    `ledger list`, then `ledger render`; the rebuild equals the fixture
  - `test_a_crash_after_the_journal_is_repaired`: commit a transaction by hand without saving; the
    next command's ledger has that change once, and `"journal"` equals `position()`
  - `test_concurrent_writers_keep_contiguous_sequences`: the existing 20-writer test also checks
    `seq` runs 1..n in `<replica>.jsonl` with no gaps and the rebuild equals the file
  - In `tests/unit/test_corpus.py`: after each replay scenario, the rebuild equals `ledger.json`

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_ledger_protocol.py tests/unit/test_corpus.py -q`
  Expected: the new tests fail.

- [ ] **Step 3: Implement it in `locked_ledger`.** Commands, readers and `render` do not change.

- [ ] **Step 4: Measure a synthetic year.** A throwaway script (not committed) writes 100 items
  with 500 changes each through `locked_ledger` and times `rebuild`. Record the time in the open
  question "Rebuild cost" in `docs/design/sync.md`. If it exceeds one second, note snapshots as
  needed in phase 1.

- [ ] **Step 5: Add smoke checks:** after the smoke suite's ledger section, the journal exists
  under `$BOSUN_STATE_DIR/journal/ledger/` and `bosun ledger validate --journal` (Task 6) prints
  nothing wrong. Update `docs/setup.md` (`journal/`, `replica.json` in the state folder list) and
  `CLAUDE.md` (the change log, journal and ledger-changes modules under "Where things live").

- [ ] **Step 6: Run everything**

  Run: `uv run pytest tests/unit -q && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 7: Commit**

  ```bash
  git add lib/ledger.py lib/guard.py lib/hook.py lib/review_watch.py tests/ docs/setup.md CLAUDE.md
  git commit -m "Ledger: every write goes through a journal that rebuilds ledger.json"
  ```

### Task 6: `bosun ledger validate`

**Files:**
- Modify: `lib/ledger.py` (`cmd_validate`, its line in the module's usage), `docs/orchestrator.md`
  (next to `ledger lint`)
- Test: `tests/unit/test_ledger.py`, `tests/smoke.sh`

**Interfaces:**
- Consumes: Task 4's `rebuild`, `RULES`; Task 5's journal.
- Produces: `cmd_validate(args) -> tuple[str, int]`; options `--journal`, `--json`.

  Without `--journal` it checks the current ledger against every rule: a state that is not one of
  its kind's states, a link `lineage.check_link` would refuse, more than one `part_of`, a link to
  an unknown item. With `--journal` it also rebuilds and reports each conflict and each item where
  the rebuild and `ledger.json` differ (the first differing path). Exit 0 when clean, 1 otherwise.

- [ ] **Step 1: Write the failing tests:**
  - `test_validate_passes_a_clean_ledger`
  - `test_validate_finds_a_bad_state`: set an item's state by hand to `"nonsense"`; exit 1, the
    item named
  - `test_validate_journal_finds_an_in_place_edit`: inside `locked_ledger(write=True)`, edit the
    newest history entry's note in place; `validate --journal` exits 1 and names the item and
    `history`
  - `test_validate_journal_lists_conflicts`: two hand-made conflicting transitions in the journal
    are listed

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_ledger.py -k validate -q`
  Expected: fail (no `validate` command).

- [ ] **Step 3: Implement `cmd_validate`,** add it to `COMMANDS` and to the usage text; one line in
  `docs/orchestrator.md` beside `ledger lint`.

- [ ] **Step 4: Run the tests and the smoke suite**

  Run: `uv run pytest tests/unit/test_ledger.py -q && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/ledger.py tests/unit/test_ledger.py tests/smoke.sh docs/orchestrator.md
  git commit -m "ledger validate: check every rule, and the journal against ledger.json"
  ```

### Task 7: Ids two Macs never mint alike

**Files:**
- Modify: `lib/ledger.py` (`next_task_id`, `next_outreach_id`, `TASK_ID`, `OUTREACH_ID`, the
  `id_format` of task, outreach and learning kinds), `lib/learn.py` (`next_id`, `record_proposal`,
  `pending_change`, `sync_statuses`, `State`), `tests/fixtures/console/make-acme-ledger.sh` and
  the fixture, the tests that name task or outreach ids, `docs/orchestrator.md`, `docs/learning.md`
- Test: `tests/unit/test_ledger.py`, `tests/unit/test_outreach.py`, `tests/unit/test_learn.py`

**Interfaces:**
- Consumes: Task 1's `mint_suffix`; Task 5's `current_transaction()`; Task 2's `replica_id`.
- Produces:
  - `next_task_id(items)` returns `f"task:{utc_date}/{current_transaction().mint()}"`
  - `next_outreach_id(ledger, scope)` returns `f"out:{scope}/{current_transaction().mint()}"`,
    with the scope's date (no parent) in UTC
  - `TASK_ID = re.compile(r"^task:\d{4}-\d{2}-\d{2}/([1-9]\d*|[0-9a-hjkmnp-tv-z]{6})$")`, and the
    same alternative in `OUTREACH_ID`
  - `learn.next_id(today, key, run, replica)` returns
    `f"learn:{today:%Y-%m-%d}/{mint_suffix(replica, f'{key}/{run}')}"`
  - The `learn:` item carries the change: fields `key`, `target`, `run` (as today) plus `text` and
    `window`; `pending_change` builds the change from the item, resolving `dir` from the window
    with `target_dir`; `State.proposals` goes. `State.load` moves a legacy `proposals` map onto
    its items once (`--field text=... --field window=...`) and drops it.

- [ ] **Step 1: Write the failing tests:**
  - `test_spawn_mints_a_hash_id`: `spawn` prints an id matching
    `^task:\d{4}-\d{2}-\d{2}/[0-9a-hjkmnp-tv-z]{6}$`
  - `test_old_task_ids_still_parse`: `upsert task:2026-10-15/1 --kind task` is accepted
  - `test_two_replicas_mint_different_ids`: two state dirs with different `replica.json` spawn the
    same task first; the ids differ
  - `test_a_pruned_id_is_never_minted_again`: spawn, finish, prune, spawn: a different id
  - `test_reach_out_mints_a_hash_id` (outreach, with and without a parent)
  - `test_learn_id_comes_from_the_finding`: same key and run give the same id; another run gives
    another
  - `test_learn_apply_reads_the_change_from_the_item`: `state.json` without `proposals`; `apply`
    writes the memory note into the window's folder
  - `test_legacy_learn_proposals_move_onto_items`

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_ledger.py tests/unit/test_outreach.py tests/unit/test_learn.py -q`
  Expected: the new tests fail.

- [ ] **Step 3: Implement the ids and the learn change on the item.** Update
  `make-acme-ledger.sh` to write a fixed `replica.json` into its scratch state dir and to take
  task and outreach ids from the commands' output; rebuild the fixture with it; update the tests
  that name its ids. Update the id lines in `docs/orchestrator.md` (`task:<YYYY-MM-DD>/<id>`,
  `out:<parent-id>/<id>`) and `docs/learning.md`.

- [ ] **Step 4: Run everything**

  Run: `uv run pytest tests/unit -q && tests/js_check.sh && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/ledger.py lib/learn.py tests/ docs/orchestrator.md docs/learning.md
  git commit -m "Ids from the replica and journal position, so two Macs never mint alike"
  ```

---

## Part C: the knowledge base

### Task 8: A keyed hash for forgotten people

**Files:**
- Modify: `lib/knowledge_model.py` (`hash_id`, `forgotten_hashes`, `check_new_entity`, `forget`),
  `lib/knowledge.py` (load the key), `lib/knowledge_store.py` (`Paths.secret`, `GIT_IGNORE`),
  `docs/design/knowledge.md` (Privacy)
- Test: `tests/unit/test_knowledge.py`

**Interfaces:**
- Produces:
  - `Paths.secret` = `root / "secret.key"`: 32 random bytes as hex, mode 0600, created on the first
    write, git-ignored
  - `KB.secret: bytes` (empty when there is no key yet)
  - `hash_id(entity_id: str, secret: bytes) -> str`: HMAC-SHA256, first 16 hex characters
  - New forgotten records carry `"hash_kind": "hmac-sha256"`; a record without `hash_kind` is
    compared as `sha256(entity_id)[:16]`, as today

- [ ] **Step 1: Write the failing tests:**
  - `test_forget_records_a_keyed_hash`: the record's hash is not `sha256("person/dana")[:16]`, and
    `hash_kind` is set
  - `test_a_forgotten_person_needs_allow_forgotten_again` (new-style record)
  - `test_an_old_forgotten_record_still_blocks_re_adding` (record without `hash_kind`)
  - `test_the_key_is_private_and_not_in_git`: mode 0600; absent from `git ls-files`

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_knowledge.py -k forget -q`

- [ ] **Step 3: Implement it;** one sentence in the design note's Privacy section.

- [ ] **Step 4: Run the tests**

  Run: `uv run pytest tests/unit/test_knowledge.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/knowledge*.py tests/unit/test_knowledge.py docs/design/knowledge.md
  git commit -m "Knowledge: forgotten people as a keyed hash, not a guessable one"
  ```

### Task 9: The knowledge journal, minted fact ids, `prev` on updates

**Files:**
- Create: `lib/knowledge_changes.py`, `tests/unit/test_knowledge_changes.py`
- Modify: `lib/knowledge.py` (`load`, `save`, `opened`, `Settings`), `lib/knowledge_model.py`
  (`KB.mint`, `next_fact_id`, one `update_record` builder used by every update),
  `docs/knowledge.md` (fact ids)

**Interfaces:**
- Consumes: Task 1's `Edit`; Task 2's `Journal`, `replica_id`.
- Produces:
  - `KG_RULES_VERSION = 1` in `lib/knowledge.py`; the journal at `root / "journal"`, stream `"kg"`,
    replica from `replica_id(Path(SETTINGS.state_dir))`
  - `KB.mint: Callable[[], str]`; `next_fact_id(kb)` returns `f"f-{kb.mint()}"`
  - `update_record(kb, fact, event, changes, **extra) -> Record`: today's update record plus
    `"prev": {k: fact.get(k) for k in changes}`
  - `knowledge_changes.edits(before_entities, kb) -> list[Edit]`: `fact.add` and `fact.update`
    (body: the record) for `kb.new_records`; `entity.put` (body: the entity) and `entity.delete`
    from comparing entities; local when the fact's subject or object, or the entity, is a
    `person/` id
  - `view.json` in `root`: `{"journal": <position>}`, written after the files; `save` journals
    first

  A rewrite (`kg forget`, the retention purge) only removes person records, which are local:
  `save` rewrites `local.jsonl` without them and refuses a rewrite that would remove a private
  record. The genesis transaction journals the existing entities and `facts.jsonl` records.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_new_fact_gets_a_minted_id`: `f-` and six base32 characters; existing `f12` facts keep
    their ids
  - `test_updates_carry_what_they_replaced`: confirming a proposed fact writes `prev.status ==
    "proposed"`
  - `test_person_facts_go_to_the_local_journal`; `test_product_facts_go_to_the_replica_journal`
  - `test_forget_rewrites_only_the_local_journal`: the person's records are gone from
    `local.jsonl` and from `git log -p --all` after the history restart, as the existing test
    checks for `facts.jsonl`
  - `test_genesis_journals_an_existing_knowledge_base`: the acme import, then a write; the journal
    holds every entity and record once

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_knowledge_changes.py tests/unit/test_knowledge.py -q`

- [ ] **Step 3: Implement it.** Update `docs/knowledge.md` where it shows fact ids.

- [ ] **Step 4: Run the tests**

  Run: `uv run pytest tests/unit -q`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/knowledge*.py tests/unit/test_knowledge*.py docs/knowledge.md
  git commit -m "Knowledge: a journal of every change, minted fact ids, and what an update replaced"
  ```

### Task 10: Rebuilding the knowledge base, and `kg validate`

**Files:**
- Modify: `lib/knowledge_changes.py`, `lib/knowledge.py` (`opened` rebuilds when `view.json`
  differs from the journal position; `cmd_validate`), `lib/knowledge_model.py` (`fold_all` takes
  records in the given order and holds updates for a fact not seen yet), `docs/knowledge.md`
  (`kg validate` next to `kg lint`)
- Test: `tests/unit/test_knowledge_changes.py`, `tests/smoke.sh`

**Interfaces:**
- Consumes: Task 1's `in_order`; Task 9's journal and edits.
- Produces:
  - `@dataclass class RebuiltKB(entities: dict[str, Record], records: list[Record], conflicts:
    list[change_log.Conflict], pending: list[str])`
  - `rebuild(changes: Iterable[Change]) -> RebuiltKB`: records in order; an update whose fact has
    not been added is held and applied when the add comes; one still held at the end is listed in
    `pending`; an update whose `prev.status` differs from the current status is a `field` conflict
    and not applied
  - `bosun kg validate [--json]`: the rebuild against the files, conflicts and pending updates;
    exit 0 when clean

- [ ] **Step 1: Write the failing tests:**
  - `test_rebuild_reproduces_the_files_after_every_kg_command`: entities and the folded facts
    match the files after `add`, `propose`, `confirm`, `reject`, `supersede`, `forget`, `expire`
  - `test_an_early_update_waits_for_its_fact`: an update ordered before its add is applied after it
  - `test_confirm_and_reject_at_once_is_a_conflict`: two updates from `proposed`, one to
    `confirmed`, one to `rejected`; the later is a conflict
  - `test_order_of_delivery_does_not_matter` (shuffled, duplicated)
  - `test_a_crash_before_the_files_is_repaired`: a journaled transaction without the files; the
    next write rebuilds

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest tests/unit/test_knowledge_changes.py -q`

- [ ] **Step 3: Implement it;** add `kg validate` to the CLI, its usage, `docs/knowledge.md` and a
  smoke check after the smoke suite's knowledge section.

- [ ] **Step 4: Run everything**

  Run: `uv run pytest tests/unit -q && tests/smoke.sh`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/knowledge*.py tests/ docs/knowledge.md
  git commit -m "Knowledge: rebuild from the journal in one order, and kg validate"
  ```

### Task 11: The console reads through `knowledge.load`, and phase 0 is done

**Files:**
- Modify: `lib/console_kb.py` (`read_kb` calls `knowledge.load` with identifiers left out),
  `lib/knowledge.py` (`load(p, *, identifiers: bool = True)`), `docs/design/sync.md` (status:
  phase 0 built; the rebuild cost measured in Task 5), `ROADMAP.md` (section 6: phase 0 done),
  `CLAUDE.md` (the knowledge-changes module)
- Test: `tests/unit/test_console_knowledge.py`, `tests/unit/test_console_kg_views.py`

**Interfaces:**
- Consumes: Task 9's `load`.
- Produces: `knowledge.load(p, *, identifiers=True)`; the console passes `identifiers=False` and
  never reads `identifiers.jsonl` or `forgotten.jsonl`, as today.

- [ ] **Step 1: Write the failing test** `test_console_reads_no_identifiers`: with an
  unreadable `identifiers.jsonl` (mode 000), the console's knowledge view still loads.

- [ ] **Step 2: Run it to see it fail**

  Run: `uv run pytest tests/unit/test_console_knowledge.py -k identifiers -q`

- [ ] **Step 3: Implement it;** update the design note's status, the roadmap and `CLAUDE.md`.

- [ ] **Step 4: Run every check**

  Run: `uv run pytest tests/unit -q && tests/js_check.sh && tests/smoke.sh && uv run pre-commit run --all-files`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add lib/console_kb.py lib/knowledge.py tests/unit docs/design/sync.md ROADMAP.md CLAUDE.md
  git commit -m "Console reads knowledge through knowledge.load; sync phase 0 done"
  ```
