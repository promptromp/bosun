# Design note: sync and sharing (ledger and knowledge)

Status: accepted (2026-11-03); nothing built yet. Phase 0 is planned in
[docs/plans/sync-phase-0.md](../plans/sync-phase-0.md). Tracked in
[ROADMAP.md](../../ROADMAP.md#6-sync-and-sharing).

## Why

Everything bosun knows lives on one Mac: the ledger in `$BOSUN_STATE_DIR/ledger.json` and the
knowledge base in `$BOSUN_CONFIG_DIR/knowledge/`. Losing the Mac loses both, and a second Mac
starts from nothing. The README already says what comes next: "A ledger and knowledge base shared
across a team is next."

Two goals, in this order:

1. **Sync and restore my own state** across my machines, and onto a new one. This is the MVP.
2. **Collaborate across people and their agents**, soon after. Two examples:
   - a colleague's agent benefits from what my knowledge graph has learned;
   - a colleague's agent sees that my ledger already has a review of a PR, so we don't both do work
     that only one of us has to do.

Non-goals:

- Real-time co-editing of one item by several people.
- A server of our own, until a phase needs one (see [Phases](#phases)).
- Syncing what only makes sense on one Mac: tmux windows, agent sessions, transcripts.
- Sharing raw prompts, transcripts or chat messages.

Anti-goals (a failure even if everything else works):

- Personal data on a host where it can no longer be deleted.
- A merge that quietly breaks the ledger's rules (a state change no kind allows, a lineage cycle, a
  single-use approval used twice).
- GitHub details leaking into the framework: switching backend must be a config change plus a
  one-time copy.
- A colleague's fact steering an outward action (a post, a merge, a ticket change) without review.

## What the research says (summary)

A review of the code and of current prior art (2026-11) found:

- **Syncing whole files through git fails.** Beads, an issue tracker for coding agents, kept its
  issues as JSONL in git from late 2025, then spent four months patching: a field-level merge
  driver, 30-day tombstones, removed links coming back after union merges, a driver dropping
  unknown fields. In February 2026 it moved to Dolt and deleted about 11,000 lines of sync code.
  Its docs state why: an import "cannot infer that records absent from an export were deleted".
- **Syncing changes works.** git-bug keeps each entity as a log of operations in git, applies them
  in one order everywhere (a logical clock, ties broken by hash), uses wall time only for display,
  rejects clocks that jump too far, and since v0.11 (2026-09) keeps operations it does not
  recognize instead of dropping them.
- **CRDT libraries cost more than they give here.** Automerge, Yjs and Loro are maintained, but
  their Python bindings are native extensions, the data becomes opaque binary, and none of them
  can enforce "valid state change", "no cycles" or "one current value". What they offer for a
  field, one deterministic winner with the losers still readable, takes a few dozen lines.
- **Local-first sync engines** (Zero, Electric, PowerSync, Triplit) need a hosted database, a sync
  service and JavaScript or native clients. Designs to borrow from, not dependencies.
- **Merging cannot keep every rule.** A rule survives coordination-free merging only if merging two
  valid states always gives a valid state (Bailis et al., "invariant confluence"). "This link
  exists" passes; "each state change was valid from the one before", "no cycles", "one `part_of`"
  and "consecutive ids" fail. Those must be checked when the merged data is rebuilt, and a loser
  shown as a conflict, as git-bug, Jujutsu and Automerge do.
- **No agent-memory product shares knowledge between people.** Graphiti, mem0, Letta, LangGraph's
  store and CrewAI "share" by letting several agents write one store, separated by scope keys that
  enforce nothing; shared mutable memory loses updates. Claude Code splits it the way bosun should:
  reviewed CLAUDE.md is shared through version control, auto memory is machine-local.
- **Publishing is the model for sharing.** Nanopublications (signed, content-addressed assertions
  with provenance, retracted but never deleted) and AT Protocol (one signed repository per
  person, a revision cursor, additive-only schemas) show how to publish facts others can import.
- **Imported facts are an attack channel.** Memory-poisoning attacks succeed at high rates
  (AgentPoison over 80% with under 0.1% of memory poisoned; MINJA 98% injection through queries
  alone). Content screening and trust-weighted ranking did not help in 2026 studies; provenance
  recorded at write time and enforced as a hard gate did.
- **Anything pushed to GitHub is effectively permanent.** Rewritten history survives in clones,
  forks, cached views and pull-request refs, and GitHub Support does not remove data that is not a
  secret.

## What exists

| Today | Effect on sync | Change |
|---|---|---|
| Fact log append-only, folded on read; facts closed, never deleted | Helps: already a change log | A common record envelope; fold in one global order |
| Item ids from PRs, Slack messages, tickets | Helps: the same item gets the same id everywhere | None |
| `task:`, `out:`, `learn:` and `f<N>` ids counted up per machine (`max + 1`, local date) | Two machines create the same id for different things | Hash-based ids ([Ids](#ids)) |
| `ledger.json` rewritten whole under one `flock`, even when nothing changed | Any two machines collide on one file | A change log per machine; the file rebuilt from it |
| Rules checked when a command writes, never on load | A merge breaks them unnoticed | Checked again in the rebuild; conflicts shown |
| `prune`, `unlink` and history trimming leave no trace | A delete cannot reach another machine | Deletes become changes; trimming only in the rebuilt file |
| Shareable, machine-local and personal fields in the same records | Personal data would leak on sync | One table that classifies every field ([Which data goes where](#which-data-goes-where)) |
| The knowledge base's local git repo pushes `identifiers.jsonl`; `kg forget` deletes `.git` | Pushing it leaks plaintext forever | A separate sync checkout that holds only what the gate lets out |
| `forgotten.jsonl` keeps `sha256(entity id)`, reversible by guessing `person/<name>` | Leaks who was forgotten if it ever leaves | A keyed hash with a local secret |
| `learn/state.json` holds the change behind each `learn:` item | A `learn:` item synced without it cannot be applied | Keep the change on the item |
| Pure models (`knowledge_model`, `lineage`); most access through the `bosun ledger` and `bosun kg` CLIs | Helps: few call sites change | Keep |

Some readers open `ledger.json` directly (the console, the check-in gate, the impact collector,
uninstall, the chores dry run) and the console has its own knowledge loader
(`console_kb.read_kb`). They keep working because the rebuilt files keep their shape; the console's
loader moves behind `knowledge.load` anyway.

## Decisions (2026-11-03)

1. **Ship changes, not files.** Each store becomes a log of changes, one per machine; every machine
   rebuilds the ledger and knowledge base from all the logs in the same order. (Proposed.)
2. **Encryption is a pluggable step**, its own port next to storage. It starts on (`age`) and can
   be turned off per scope: where the backend encrypts at rest itself (S3 with KMS), or where we
   want the data readable for review on GitHub. (Decided by the user.)
3. **Personal data stays on the Mac** through the collaboration phases: person facts, colleagues'
   ids, my verbatim prompts, approvals. One table decides what goes where, and a field it does not
   know stays local. We expect to iterate on this table. (Decided by the user.)
4. **One active orchestrator per person**, held by a lease. I can still work in repo windows on any
   of my Macs; the orchestrator does not hand work to another Mac's windows. (The lease decided by
   the user; what it gates is proposed in [The sync engine](#the-sync-engine).)
5. **A dedicated state repository**, used the way Terraform uses a state bucket, never mixed with
   code. One per organization, shared by its members, each writing only under their own prefix.
   (Decided by the user; the layout is proposed in [Scopes](#scopes-and-the-state-repository).)
6. **MVP first, then the smallest collaboration.** Phase 1 is sync and restore for one person;
   phase 2 is the team board; knowledge sharing follows. (Order decided by the user; the board
   before knowledge is proposed.)
7. **Free text is `sealed`.** Titles, notes, history notes, report summaries and outreach
   subjects can name colleagues, and on a store that cannot delete (GitHub) plain text would stay
   readable forever. So with encryption off, my scope carries ids, states, links and times but no
   words; with encryption on (the default), everything in the class allows. Rejected: allowing
   plain free text after an acknowledgment in config (set once, forgotten, permanent), and
   allowing plain text only in the team scope (stricter than needed). (Decided by the user.)

## Shape

### Changes, not files

Each store is a **stream** (`ledger`, `kg`). Each machine is a **replica** with a random 128-bit
id, created once and kept in the state dir. A replica appends its own changes to its own log and
never edits another replica's. Every change carries one envelope:

| Field | What it is |
|---|---|
| `v` | Format version of the envelope |
| `stream` | `ledger` or `kg` |
| `replica` | The replica that made the change |
| `seq` | Per replica and stream, from 1 with no gaps, so a missing change is noticed |
| `hlc` | Hybrid logical clock: wall time in ms plus a counter, fixed width so it sorts as a string |
| `tx` | The transaction: every change made under one write lock shares it |
| `cmd` | The command that made it (`ledger transition`, `kg confirm`), so intent is not lost |
| `rules` | Version of the rules it was checked against (`KINDS`, `RELATIONS`, the predicate list): a number bumped by hand when a change to them would rebuild old changes differently, as `orchestrator/learn.md` bumps its `version` |
| `op`, `target`, `body` | What changed: an operation, the item, link, entity or fact, and its data |
| `id` | SHA-256 of the record's canonical bytes without `id` |

**Canonical bytes:** `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`,
one record per line, LF endings. That matches RFC 8785 for strings and integers only, so a record
may not hold floats; the writer refuses them.

**Order:** every replica sorts all changes by `(hlc, replica, id)`. A Lamport clock alone would sort
yesterday's offline change after today's, which the stand-up summary should not do; wall time alone
lets a wrong clock win. A hybrid clock is wall time that still respects causality. Two guards:
each replica advances its clock past every clock it pulls, and a change whose wall time is more
than `BOSUN_SYNC_MAX_SKEW_MINUTES` (default 10) ahead of local time is flagged and does not drag the
local clock forward, so a Mac whose clock says 2030 cannot win every contest.

**The files stay.** `ledger.json`, `ledger.md` and the knowledge files are still written, in their
current shape, now rebuilt from the logs. Commands, the guard, hooks, the console, task files and
the policy keep reading them. The property every test checks: the files equal the rebuild of the
logs.

**The journal is the commit point.** A write appends its changes to the journal (with `fsync`)
first, then rewrites the files, which record the journal position they reflect (the highest `seq`
per replica). A crash between the two leaves the files behind the journal; the next load sees the
positions differ and rebuilds the files from the journal. Pulling another Mac's changes in phase 1
is the same step.

### Rebuilding, and what a merge cannot decide

Merging is the union of all changes, deduplicated by `id`. Rebuilding applies them in order and
checks, in the same way on every machine, each rule a merge cannot keep. A change that breaks one
is kept in the log, has no effect, and becomes a **conflict record**:

| Data | Merge rule | A conflict when |
|---|---|---|
| The changes themselves | Union by `id` | never |
| Item fields (`title`, `url`, `fields.*`) | Last writer wins per field; losing values stay in the history | the field is marked important (a ticket key, an owner) |
| Item state | A state change carries its `from`; the first valid one in order applies | a later one whose `from` no longer holds |
| History entries, outreach messages, processed Slack sources | Grow-only set, shown in order | never |
| Lineage links | Add wins over a concurrent unlink; inverses computed as today | an add would close a cycle, or give an item a second `part_of` |
| Proposals and approvals | One slot per item; an approval's `used_at` set once | two proposals in one slot; an approval used twice |
| Item deletion (`prune`) | A tombstone; delete wins | a change arrives for a deleted item |
| Fact assertions | One per source; a fact is the group sharing subject, predicate and object | never |
| Single-valued predicates | Keep every confirmed value | more than one current confirmed value |
| Review decisions | Last writer wins per fact and reviewer | confirm and reject by different reviewers at once |
| Entities | Last writer wins per field; aliases unioned; delete wins | a proposed merge of two entities |

Conflicts show where open work already shows: a "needs you" entry in the console and in chores,
and `bosun ledger conflicts` and `bosun kg conflicts`. They are resolved with ordinary commands,
which write ordinary changes. A new `bosun ledger validate` checks the rebuilt ledger against
every rule, so a damaged ledger is found by a command rather than by a person.

Two checks guard the rebuild itself. Each replica publishes a hash of its rebuilt state with its
log, so two machines that disagree after reading the same changes show up in `bosun doctor`. And
two versions guard against Macs running different bosun versions, because Beads found that clones
on different schemas could no longer merge:

- **A newer envelope format (`v`)** than this bosun understands: it keeps working locally, stops
  pushing, and says to update bosun.
- **A newer rules version** than this bosun knows: it applies those changes without re-checking
  them, since the newer bosun already checked them under rules this one lacks (usually a new state
  or relation), and `bosun doctor` says to update. Once updated, it rebuilds the same as the
  others. Changes checked under older rules are always re-checked under the current ones, so an
  update never needs every Mac to move at once.

**Existing data** enters the journal once, as a snapshot change per item, entity and fact, when a
Mac first turns on the journal (phase 0). History before that is whatever the files still hold:
trimmed history and pruned items are gone already.

### Where the ledger's changes come from

The ledger has 29 places that write, all through one function: `locked_ledger` loads the
document, hands it to the command and saves it. That function copies the document first and
compares afterwards, turning each write into one transaction of field-level changes. A table maps
each field path to its merge rule and its class ([Which data goes where](#which-data-goes-where)),
in the table style of `KINDS`, `RELATIONS` and `CHECKS`. A state change becomes a change with its
`from`; added history entries, links and messages become additions; a removed link becomes an
unlink. A write that changed nothing adds nothing to the journal (the files are still rewritten,
since `ledger render` relies on that). The command name goes on the transaction.

The alternative, an explicit change emitted by each command, keeps more intent but spreads sync
across 29 places. The comparison keeps it in one, and the command name recovers most of the
intent. If a case needs more (for example `spawn`'s de-duplication by title), that command can
emit an explicit change through the same journal later; nothing in this design prevents mixing.

Three ledger behaviours change meaning:

- **History trimming** (200 entries per item) happens only in the rebuilt file. The log keeps
  everything.
- **`prune`** writes tombstones, so a pruned item disappears from every machine.
- **`learn:` items** keep their change on the item instead of in `learn/state.json`, so a synced
  `learn:` item can still be applied.

### Knowledge changes

The knowledge graph needs less:

- `add` and `update` records gain the envelope.
- Entity changes become changes in the same log; `entities.jsonl` becomes a rebuilt file.
- The rebuild sorts by the global order, not by position in the file, and an update for a fact that
  has not arrived yet waits instead of being dropped.
- Each source's assertion stays its own record, so one agent never overwrites another's.
- A single-valued predicate keeps every confirmed value, so a disagreement opens a review item.
  That matches today's rule that an automatic confirmation never closes a confirmed value it
  contradicts.
- Validity windows stay data, compared with "now" only when reading, so the rebuild depends only
  on the log.

The local git history of `knowledge/` stays as it is for now. It is local, it never syncs, and
`kg forget` can keep restarting it. Once the log exists it duplicates the log's history, so a later
change may retire it ([Open questions](#open-questions)).

### Ids

| Kind | Today | Proposed |
|---|---|---|
| Items from outside references (`review:`, `mypr:`, `slack:`, `jira:`, `ghissue:`, `td:`) | From the reference | Unchanged |
| Workstreams | `ws:<slug>`, chosen by a person | Unchanged |
| Tasks | `task:<local date>/<max + 1>` | `task:<UTC date>/<short hash>` |
| Outreach | `out:<parent or local date>/<max + 1>` | `out:<parent or UTC date>/<short hash>` |
| Learning | `learn:<local date>/<max + 1>` | `learn:<UTC date>/<short hash>` |
| Facts | `f<max + 1>` | `f-<short hash>` |
| Entities | `<type>/<slug>` | Unchanged within one person; across people, see [Knowledge sharing](#phase-3-knowledge-sharing) |

The short hash is six base32 characters of a hash of the replica id and the journal position the
id is made at, so it is unique to its Mac by construction, never reused (not even after a `prune`),
the same on every rebuild of a fixture, and short enough to type. Two Macs collide only by a hash
collision, about one in a billion for a pair of ids. Existing ids stay as they are: only the first
Mac has them.

A new Mac joins by restoring from the state repository and starting with no ledger of its own. A
Mac that already has its own ledger cannot join in phase 1: merging two ledgers that grew apart
(renaming clashing generated ids, and deciding between two histories of the same `review:` item)
is a separate piece of work, left until someone needs it ([Open questions](#open-questions)).

### Which data goes where

Every field belongs to one class. The class decides how far it may travel:

| Class | Leaves this Mac | Examples |
|---|---|---|
| `local` | Never | `said` (my verbatim prompts); an approval's quote, session, transcript path and tool call; `identifiers.jsonl`; `forgotten.jsonl`; person entities and every fact naming a person; `batch.json` |
| `sealed` | Only to my own scope, and only when the data is encrypted (by the codec, or by a store that declares encryption at rest) | free text (titles, notes, history notes, report summaries, outreach subjects and bodies); colleagues' names in `requester` and `to`; Slack permalinks |
| `private` | To my own scope | item ids, kinds, states, links, history events, reports; the window in a delegation or report, tagged with its replica; process and product facts |
| `team` | Also into a published projection | item id, kind, state and owner for the team board; confirmed, non-personal facts a share spec selects |

The rules that keep this clean:

- **One table per stream** (field paths for the ledger; entity type, predicate and sensitivity for
  knowledge). Moving a field to another class is a change to one row.
- **Unknown means `local`.** A new field that nobody classified stays on the Mac, so forgetting
  the table fails safe.
- **The table is total.** A test walks every field of the fixture ledger and knowledge base and
  fails if one is unclassified, so a new field forces a decision in the same pull request.
- **The gate checks again** before anything is encoded, and what it checks depends on the scope:
  - every scope: the class filter, the values in `identifiers.jsonl` (people's ids),
    `lib/redact.py` (keys, tokens, emails) and gitleaks if it is installed;
  - the team scope also: the site's forbidden terms and the patterns of Slack, code-host and
    tracker ids. The private scope is my own data, full of my org's repo and channel names, so
    those checks would stop every push there.
- **A change the gate stops is held, not lost, and does not stop the rest.** It is sealed as a
  placeholder with the same `seq` and no body, so the sequence has no gap; the real change stays in
  the local journal and shows as a "needs you" entry until I fix the data or move the field's
  class. Other Macs see that a change was held and skip it.
- **Turning encryption off changes nothing by hand**: `sealed` fields simply stop leaving.
- **Windows are not personal, just local.** A delegation's window stays in the record with its
  replica, so the orchestrator on one Mac sees that an item is held by a window on another Mac and
  does not delegate it again. `send`, the watchdog and the console act only on their own Mac's
  windows.

Where we expect to iterate:

- **Restore loses `local` data.** A restored Mac has no prompts in `said`, no person facts and no
  identifiers. Person facts are the first candidate for `sealed`, once deletion can reach a remote
  (key epochs, phase 4 in [Phases](#phases)).
- **Titles can hold names.** A PR title or a Slack excerpt can name a colleague, so free text is
  `sealed` and stays out of the team board. Whether some titles (a PR's, already visible to the
  team on GitHub) could be shared needs a closer look before any title goes into a team projection.
- **Item ids can be sensitive.** `slack:<channel>/<ts>` names a channel; it is `private`, and Slack
  items stay out of the team board.

### Scopes and the state repository

Data travels in two scopes:

- **Private:** my own replicas. Full fidelity within the classes above. Only my replicas write
  it, and with encryption on, only I can read it.
- **Team:** projections I publish for my organization (the board, shared facts). Only I write my
  part; my teammates read it. Nobody writes into anyone else's.

The default deployment is one state repository per organization, holding both scopes:

```text
format.json                                       plaintext: format version, minimum bosun version
roster/                                           phase 2: members, their signing and encryption keys
members/<member>/private/ledger/log/<replica>/<yyyy-mm>/<first>-<last>-<hash8>.jsonl[.age]
members/<member>/private/ledger/heads/<replica>.json[.age]   highest seq and rebuild hash
members/<member>/private/kg/log/...                          same layout
members/<member>/private/lease.json[.age]                    which replica runs the orchestrator
members/<member>/team/board/...                              phase 2
members/<member>/team/kg/...                                 phase 3
```

Each replica writes only its own files (the lease is the one shared file, written with a
compare-and-swap). So a git merge never has a conflict to resolve, and no merge driver or
`merge=union` is needed; drivers live in each clone's own config and forges do not run them.
Segments are sealed and never appended to, because an encrypted file cannot be appended to and
object stores cannot append at all. Month folders keep each folder under GitHub's recommended
3,000 entries.

**Two refinements on the shared repository:**

- **Prefixes rather than branches.** On GitHub a branch is not an access boundary: anyone with
  write access can push to any branch unless a ruleset says otherwise, and per-member rulesets
  were not verified. Branches would also multiply fetches. Ownership is enforced when reading, by
  signatures: a file under `members/<member>/` that the member's key did not sign is ignored and
  reported. Branches remain an option later if GitHub can enforce who writes where.
- **The private scope can live elsewhere.** The code knows two scopes, each with its own remote,
  codec and signer, so a person can keep their private scope in a personal repository or a bucket
  and publish only the team scope to the organization's repository. With both in one repository,
  each member clones only their own private prefix and everyone's team prefix (sparse checkout).

Limits to keep in mind: GitHub recommends at most about six pushes a minute per repository,
shared by every writer, so pushes batch on the check-in loop; a large team outgrows one repository
before it outgrows the design, and moves to object storage.

### Ports and adapters

Three ports, each a `Protocol` with a table of implementations in the style of the `AGENTS` table
(`lib/agent_state.py`):

**Store** (moves bytes; knows nothing of streams or encryption):

```python
class Conflict(Exception): ...  # a precondition failed: HTTP 412, non-fast-forward push, other bytes already there
class NotFound(Exception): ...

@dataclass(frozen=True)
class Blob:
    key: str              # "members/me/private/ledger/log/<replica>/2026-11/<first>-<last>-<hash8>.jsonl.age"
    size: int | None
    version: str | None   # ETag, GCS generation, git blob id or mtime: opaque, used only by cas

class RemoteStore(Protocol):
    name: str
    caps: frozenset[str]  # a subset of {"cas", "durable_delete", "encrypted_at_rest"}

    def list(self, prefix: str) -> Iterator[Blob]: ...
    def get(self, key: str) -> tuple[bytes, Blob]: ...       # NotFound
    def put_new(self, key: str, data: bytes) -> Blob: ...    # create only; the same bytes already there is success
    def put(self, key: str, data: bytes) -> Blob: ...        # overwrite; only keys one replica owns (its head)
    def delete(self, key: str) -> None: ...                  # absent is success
    def cas(self, key: str, data: bytes, expected: str | None) -> Blob: ...  # only with "cas"
    def refresh(self) -> None: ...  # make others' writes visible (git: fetch; no-op over HTTP)
    def flush(self) -> None: ...    # make own writes visible (git: commit and push; no-op over HTTP)
```

**Codec** (turns plaintext into what is stored, and back): `name`, `suffix`, `encrypts`,
`encode(data, recipients)`, `decode(data)`. Implementations: `plain` (identity) and `age` (the
`age` CLI as a subprocess, the identity in the Keychain). The suffix is part of each file's name,
so switching codec never rewrites history and a store can hold both.

**Signer:** `sign(data, namespace)` and `verify(data, signature, namespace, signer)`.
Implementations: `ssh` (`ssh-keygen -Y sign` and `-Y verify`, which ship with macOS) and `none`
(tests and a plain local folder). Trust starts from the member's public key held locally, never
from the remote it protects: device keys found on the remote count only when the member key signed
them (see Keys in [The sync engine](#the-sync-engine)). A segment is signed before it is encoded,
because `age` does not prove who sent a file.

Each scope is configured with a remote, a codec and a signer (names proposed):

```bash
BOSUN_SYNC_REMOTE=""        # empty: sync off. git:<url> | dir:<path> | later s3://<bucket>/<prefix>, exec:<name>
BOSUN_SYNC_MEMBER=""        # my prefix under members/
BOSUN_SYNC_CODEC="age"      # age | plain
BOSUN_SYNC_SIGNER="ssh"     # ssh | none
BOSUN_SYNC_MAX_SKEW_MINUTES="10"
BOSUN_SYNC_PRIMARY=""       # the lease holder's replica, only on a store without compare-and-swap
BOSUN_TEAM_REMOTE=""        # empty: the same as BOSUN_SYNC_REMOTE (phase 2)
BOSUN_TEAM_CODEC="age"      # age to the roster | plain, for review on GitHub (phase 2)
```

The backends:

| Backend | Create-only write | Compare-and-swap | Real delete | Needs |
|---|---|---|---|---|
| Local folder (`dir:`; tests, and Dropbox, iCloud or Syncthing folders) | exclusive create | `flock` plus rename (not on synced folders) | yes (synced folders: best effort) | nothing |
| GitHub through a `git` clone (`git:`, the MVP) | a new path only one replica writes; a rejected push means fetch and retry | a fast-forward push | no: history, forks and caches keep it | `git` (`gh` only for setup) |
| S3, R2, MinIO (phase 4) | `If-None-Match: *` | `If-Match: <ETag>` | yes | the AWS CLI for SSO credentials; request signing fits in stdlib Python |
| A program (`exec:`, phase 4) | the program's answer | the program's answer | the program's answer | `bosun-store-<name>` speaking the verbs as JSON lines, as git-annex special remotes do |
| A server (phase 4, only if needed) | insert with a key | update on a version | yes | a running service |

A store without `cas` loses only what needs it (the lease); a store without `durable_delete` never
receives `sealed` data unless the codec encrypts. The GitHub adapter is a full clone (never
shallow) of the state repository under `$BOSUN_STATE_DIR/sync/`, whose `.gitignore` is `*` with
exceptions for the files the layout allows, so the plaintext knowledge directory has no path to
GitHub.

### The sync engine

`bosun sync` runs on the check-in loop and by hand. It is pure over a journal, a codec, a signer
and a store, so tests run it against two folders.

- **Push:** collect this replica's changes not yet sent; run the gate; seal them into one segment;
  sign; encode; `put_new`; rewrite this replica's head; `flush`.
- **Pull:** `refresh`; list each stream's logs; fetch the segments not seen yet; decode; verify
  the signature against the local allowed signers, the hash in the name and the continuity of
  `seq`; append to the local journal; rebuild; report new conflicts.
- **Lease:** one replica holds the orchestrator lease, so two Macs never both send the same
  reminder or record the same review twice. It splits the work into two kinds:

  | Runs only on the lease holder | Runs on every Mac |
  |---|---|
  | The orchestrator window (`bosun start` and the morning job skip it elsewhere and say where it runs) | Repo windows, `send` and the events dispatcher for this Mac's windows |
  | Chores, check-ins and nudges | The watchdog, for this Mac's windows and jobs |
  | `slack-watch`, `review-watch`, `ticket-sync`, learning | `bosun sync` |

  The lease is renewed on each sync. A lease that lapses (its holder asleep or gone) is never taken
  over on its own: `bosun doctor` and the watchdog say so, and I move it with `bosun sync lease
  take`. On a store without compare-and-swap (a synced folder), the holder is a replica named in
  config (`BOSUN_SYNC_PRIMARY`). The lease is checked by `start` and the check-in loop, so the
  orchestrator and its jobs simply do not run elsewhere; nothing else needs to enforce it.
  Approvals already bind to a local session's transcript, so one approval cannot be spent on two
  Macs.

- **Keys:** two kinds, so that adding a Mac and restoring are the same step.
  - A **member key** (an SSH key) is my identity. It signs each Mac's device key; together with the
    `age` identity it makes up my recovery copy, kept outside bosun (a password manager) and in each
    Mac's Keychain.
  - A **device key** per Mac signs that Mac's segments. Readers trust a segment whose device key the
    member key signed. A lost Mac is revoked by listing its device key as revoked, signed by the
    member key.
- **Restore** (and adding a Mac): on a new Mac, `bosun init` offers to restore from a state
  repository. It asks for the remote, the member name and the recovery copy, puts the keys in the
  Keychain, creates a replica and a device key signed by the member key, pulls and rebuilds. No
  other Mac has to approve it, and a restore after losing every Mac works the same way.
- **Doctor:** the replica id, the last push and pull, flagged clocks, conflicts, and a rebuild hash
  that differs from another replica's.

The local journal lives next to each store (`$BOSUN_STATE_DIR/journal/` for the ledger,
`$BOSUN_CONFIG_DIR/knowledge/journal/` for knowledge): one file per replica, plus a `local` file
with its own sequence for `local`-class changes, which is never sealed.

## Collaboration

### Phase 2: the team board

The smallest useful collaboration, and the one with the least risk: who is on what.

- **What I publish:** for my open items whose ids come from shared references (`review:`, `mypr:`,
  `ghissue:`, `jira:`, `td:`): the id, kind, state, my member name and when it last changed. No
  titles, no notes, no Slack items, at first.
- **What my agent reads:** each teammate's board, matched to my items by id, which converges
  because these ids come from the references. `bosun ledger show` and `list` say "also open for
  <teammate> (in review)"; `bosun team board` lists everyone's; chores sees it before delegating.
- **What it changes:** before starting a review a teammate is already on, the orchestrator says so
  and asks, unless the PR requested both of us. It never blocks. This is wording the orchestrator
  acts on, so it gets free-text cases in `tests/llm/`.
- **Trust:** a board entry is data shown to a person or agent, never an instruction. Entries are
  signed by their member and checked against the roster.

Each person's ledger stays their own: teammates read my board, never write my items. Two people's
`review:` items for one PR stay two items, one in each ledger.

### Phase 3: knowledge sharing

- **What I publish:** a projection of confirmed, non-personal facts that a share spec selects (by
  entity type, predicate or workstream), with sources reduced to their kind and a shareable
  reference, and private Slack permalinks removed. Each fact is a self-contained record whose id is
  the hash of its content, signed, and encrypted to the current roster or plain for review.
- **What my agent does with it:** each teammate's facts land in their own partition with a new
  untrusted source kind, `peer`. They arrive as proposals and:
  - never confirm themselves, and never count as a second trusted source (two peers agreeing is not
    independent evidence);
  - never supersede a fact whose authority is my own words;
  - pass a contradiction check before the repeated-fact rule runs, so a contradicting fact is not
    absorbed as another observation;
  - are limited per peer, and a whole bundle can be undone by its id.
- **The gate on actions:** a turn whose context included peer facts needs explicit approval for
  any gated outward action (`lib/guard.py`), so an imported "announcements go to #general" cannot
  redirect a post.
- **Entities across people:** the same person or product in two graphs is matched only through a
  reviewed `same_as` link that does not chain.
- **Before it ships:** the privacy or legal review the knowledge design already requires before any
  team-shared version.

Later, beyond these phases: agent-to-agent messages (an inbox per member) for handing work over,
and workstreams shared by several people.

## Security and privacy

| Threat | Control | Tool | Phase |
|---|---|---|---|
| The host, or anyone with access to the repository, reads the data | `age` codec on by default; opaque names; fixed commit author and message | `age` CLI (new) | 1 |
| A stolen sync credential plants records | Every segment signed by a device key the member key signed; anything else is hostile, not a proposal | `ssh-keygen -Y` | 1 |
| Personal data or secrets pushed by mistake | The class table and the gate inside `bosun sync`, which holds what it stops | `lib/redact.py`, forbidden terms, optional gitleaks | 1 |
| A lost Mac | A credential and device key per Mac, each revocable alone; the recovery copy (member key and `age` identity) restores onto a new one | Keychain | 1 |
| A deletion request after sync | Personal data never syncs; key epochs later allow crypto-shredding | `age` | 1, then 4 |
| A teammate's agent poisons my graph | `peer` source kind, quarantine, no auto-confirm, the gate on actions | `lib/guard.py` | 3 |
| A former teammate keeps reading | Team data encrypted only to the current roster; removal stops future data only | `age -R` | 2 |
| A forged roster change | Roster changes signed by an admin; fingerprints confirmed on first use | `ssh-keygen -Y` | 2 |

Credentials: each Mac gets its own, kept in the Keychain and handed to git through a credential
helper, never in argv or the remote URL. For one person: a deploy key or a fine-grained token
limited to the state repository. For a team: per-member fine-grained tokens owned by the
organization, or a GitHub App. Never the `gh` login's token, which reaches every repository. The
organization owns the repository, so team data sits under the employer's agreement.

## Where things will live

Pure modules hold the logic; thin modules do the I/O.

- `lib/change_log.py` (pure): the envelope, canonical bytes, ids, the hybrid clock and the order.
- `lib/journal.py`: a stream's local journal files, under the store's existing lock.
- `lib/ledger_changes.py` (pure): the field table, `diff(before, after)` into changes, and the
  rebuild with its conflict records.
- `lib/knowledge_changes.py` (pure): the same for knowledge, sharing `knowledge_model`'s fold.
- `lib/sync_plan.py` (pure): what to push and pull, from the journal and a store listing.
- `lib/sync.py` and `libexec/bosun-sync`: the command.
- `lib/remote_store.py`: the port, `BACKENDS` and the contract every adapter passes;
  `lib/remote_dir.py`, `lib/remote_git.py`, later `remote_s3.py` and `remote_exec.py`.
- `lib/codecs.py`: the port, `CODECS`, `plain`; `lib/codec_age.py`.
- `lib/signers.py`: the port, `SIGNERS`, `none` and `ssh`.
- Phase 2: `lib/team_board.py` (pure). Phase 3: `lib/share.py` (pure: the share spec and the
  projection).

Outside these: the two store seams (`locked_ledger`; `knowledge.opened` and `save`), the four id
generators, `console_kb.read_kb` behind `knowledge.load`, `bosun doctor`, `bosun init` (an answer
for the remote and the restore), `STATE_FILES` and `CONFIG_FILES` for the journals and the sync
checkout (uninstall never touches the remote, and says so), and the config keys in
`lib/common.sh` and `examples/generic/bosun.env`.

## Testing

- **One contract suite for every store:** create-only writes, the same bytes written twice,
  deletes of what is absent, listing after writing, `cas` where declared. It runs against the
  folder adapter in the unit tests and against a local bare git repository in the smoke suite, so
  no test reaches the network. Beads kept the same kind of suite as its answer to backend drift.
- **Rebuild properties:** the same changes delivered shuffled or duplicated give the same rebuild
  hash; two replicas that edit one item offline produce the same conflict records on both sides.
- **The corpus:** after every command in the replayed corpus (`tests/fixtures/corpus/`), the
  rebuild of the journal equals the saved `ledger.json`.
- **The class table is total** over the fixture ledger and knowledge base.
- **Two machines:** two state dirs syncing through a bare repository in the smoke suite; the
  Docker sandbox for a new user restoring; the Mac VM for the Keychain and the real `age` and
  `ssh-keygen`. `age` gets a stand-in in the smoke suite's fake tools, as `gh` and `launchctl` do.

## Phases

| Phase | Scope | Built | New dependencies | Done when |
|---|---|---|---|---|
| 0: Groundwork | one Mac, no remote | The envelope, hash-based ids, the clock, journals and rebuilt files, rebuild with conflicts, the class table, `ledger validate`, `learn:` changes on the item, the keyed hash in `forgotten.jsonl` | none | Rebuilding the journals reproduces `ledger.json` and the knowledge base over the corpus; the same changes delivered shuffled or duplicated rebuild the same |
| 1: Sync and restore | one person, one or more Macs, the state repository | The three ports with the folder store, `plain` codec, `none` signer and their contract suite; the git store, the `age` codec, the `ssh` signer with member and device keys, the gate, the lease, `bosun sync`, restore in `bosun init`, doctor lines | the `age` CLI | Two Macs work offline, sync and show the same ledger and knowledge; a new Mac restores; the host holds only `.age` files |
| 2: Team board | a small team, one shared repository | The roster, the team scope, the board projection and its reading, the orchestrator's wording | none | A teammate's agent sees my open review and says so before starting its own |
| 3: Knowledge sharing | the same team | The share spec, signed fact bundles, peer partitions, the `peer` source kind, quarantine, the gate on actions, the export log, signed retractions | none | Privacy or legal sign-off; an imported fact cannot reach a context pack or an outward action without review |
| 4: More backends, a server only if needed | any | S3-compatible and `exec:` stores; key epochs, so `sealed` can include person facts; a server with live push if a team outgrows a repository | the AWS CLI for S3 | The contract suite passes on every store; switching store is a config change plus a copy |

Each phase stands on its own: stopping after any of them leaves a working bosun. The S3 store can
come any time after phase 1, because the port exists from then on.

The ROADMAP's SQLite ledger (P2) changes meaning: if it comes, it is a local query copy rebuilt
from the journal, never what travels.

## Rejected alternatives

- **Add a remote to the knowledge base's git repo and mark `facts.jsonl` `merge=union`.** It
  would push `identifiers.jsonl` in plaintext forever, does nothing for `entities.jsonl` or the
  ledger, and repeats Beads' bug where union merges brought deleted links back.
- **Sync `ledger.json` as a file**, with or without a merge driver: every concurrent change
  conflicts, and a snapshot cannot carry a delete.
- **A CRDT library** (Automerge, Yjs, Loro): native packages, opaque files, and none of bosun's
  rules.
- **Dolt:** the only ready-made store that merges field by field over GitHub, but a large Go binary
  that replaces bosun's store and still knows nothing of its state machines.
- **A local-first sync engine** (Zero, Electric, PowerSync): a hosted database and service, and no
  Python client.
- **git-crypt, transcrypt, git-remote-gcrypt or sops** instead of a codec: they leak names and
  sizes, cannot revoke, re-upload the whole history on each push, or break git merges.
- **One git ref per item** (git-bug's layout): GitHub recommends at most 5,000 branches per
  repository.
- **One shared, mutable knowledge store for the team** (the agent-memory products' model): lost
  updates, no provenance, and every member trusted with every write.

## Open questions

- **Rebuild cost:** nobody has measured a rebuild over months of changes. Phase 0 measures one over
  a synthetic year before deciding whether snapshots are needed.
- **Joining with an existing ledger:** a Mac that already has its own ledger cannot join in phase 1.
  If someone needs it: rename its clashing generated ids, and decide how two histories of the same
  `review:` item combine (one wins, both kept, or a conflict per item).
- **Team credentials:** per-member fine-grained tokens or a GitHub App; and whether GitHub rulesets
  can restrict pushes by path or branch per member (not verified).
- **`age` details:** whether it can read an identity from stdin rather than a temporary file, and
  whether the Secure Enclave plugin (Touch ID on decrypt) suits an unattended check-in loop.
- **The knowledge base's local git history:** keep it, or retire it once the journal holds the
  history.
- **When person facts become `sealed`:** after key epochs make deletion reach the remote.
- **Several bosun versions in one team:** how long a replica keeps writing when a teammate runs a
  newer format.
