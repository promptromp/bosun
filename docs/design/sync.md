# Design note: sync and sharing (ledger and knowledge)

Status: accepted (2026-11-03). Phase 0 is built, for both stores. Every ledger write goes through
a journal of changes per Mac that `ledger.json` is the rebuild of, with hash-based ids, the class
table and `bosun ledger validate --journal` (also in `bosun doctor`). For the knowledge base,
every write goes through a journal of its own, with minted fact ids and updates that say what they
replaced, and its files are that journal's rebuild, with `bosun kg validate` (also in `bosun
doctor`) ([Knowledge changes](#knowledge-changes)). The console reads the knowledge base through
the same `knowledge.load`, with the identifiers, the forgotten records and the key left out.
Planned in [docs/plans/sync-phase-0.md](../plans/sync-phase-0.md). Tracked in
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
| `learn/state.json` holds the change behind each `learn:` item | A `learn:` item synced without it cannot show what it would change | Keep the change on the item; only the Mac whose learn proposed it applies it |
| Pure models (`knowledge_model`, `lineage`); most access through the `bosun ledger` and `bosun kg` CLIs | Helps: few call sites change | Keep |

Some readers open `ledger.json` directly (the console, the check-in gate, the impact collector,
uninstall, the chores dry run) and the console has its own knowledge loader
(`console_kb.read_kb`). They keep working because the rebuilt files keep their shape; the console's
knowledge loader now goes through `knowledge.load` (identifiers left out), as built in phase 0.

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

## Decisions (2026-11-04)

- **Going back to an older release is not supported yet.** Nobody else runs bosun, so a release
  need not read what a later one wrote: the one before the knowledge journal stops on a minted
  fact id. From here on, a change to a data format ships with its migration, and `bosun update`
  (being designed) backs the data up before it changes anything, so that going back means
  installing the older release and restoring that backup. (Decided by the user.)
- **A fact I stated travels in phase 1, without my words.** Phase 1 splits my quote and my
  session off such a fact: they stay `local`, and the fact is classed like any other (`private`
  unless it names a person). A record that closes a product fact in favour of one I stated
  (`superseded_by`) then names a fact the other Mac has. (Decided by the user.)
- **Learning's evidence quotes stay `sealed`.** A `learn:` proposal's quotes of my prompts and
  transcripts travel only to my own scope and only encrypted; with encryption off they stay on
  the Mac. They do not get a field of their own. (Decided by the user.)
- **The rebuild trusts what was checked when it was written.** The bosun that wrote a change
  checked it, under its own rules, against the state it applied to. So the rebuild checks a change
  again only where it lands on changes its writer had not seen (two Macs writing at once), and
  there under today's rules: one they refuse is kept as a conflict, never lost. No release keeps
  older rule tables; a rules change that alters what earlier changes mean (a state renamed or
  dropped) ships with a migration, as a format change does. Telling the two cases apart needs
  what each transaction was built on, which phase 1 adds anyway ([Open
  questions](#open-questions)); until then all of one Mac's changes land where they were written.
  Rejected: checking each change under the rules version it was recorded with, which keeps every
  old rule table in every release and replays a rule's bugs after they are fixed. (Proposed; the
  user asked for a recommendation.)

## Shape

### Changes, not files

Each store is a **stream** (`ledger`, `kg`). Each machine is a **replica** with a random 128-bit
id, created once and kept in the state dir, and made anew when a store's journal starts again
(below). A replica appends its own changes to its own log and never edits another replica's. Every
change carries one envelope:

| Field | What it is |
|---|---|
| `v` | Format version of the envelope |
| `stream` | `ledger` or `kg` |
| `replica` | The replica that made the change |
| `seq` | Per replica and stream, from 1 with no gaps, so a missing change is noticed |
| `hlc` | Hybrid logical clock: wall time in ms plus a counter, fixed width so it sorts as a string |
| `tx` | The transaction: every change made under one write lock shares it |
| `of` | How many lines the transaction has in this change's file, so a transaction cut short is noticed; a line in the `local` file also has `private_of`, how many it has in the replica's file (local lines never leave the Mac) |
| `cmd` | The command that made it (`ledger transition`, `kg confirm`), so intent is not lost |
| `rules` | Version of the rules it was checked against (`KINDS`, `RELATIONS`, the predicate list): a number bumped by hand when a change to them would rebuild old changes differently, as `orchestrator/learn.md` bumps its `version` |
| `op`, `target`, `body` | What changed: an operation, the item, link, entity or fact, and its data |
| `id` | SHA-256 of the record's canonical bytes without `id` |

**Canonical bytes:** `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`,
one record per line, LF endings. That matches RFC 8785 for strings and integers only, so a record
may hold no floats, and no text that is not valid Unicode (a lone surrogate, which is what Python
makes of command-line bytes that are not UTF-8). A write that would journal one is refused before
anything is written, naming the item and the field. Data that never went through the journal (a
ledger from before it, or one changed outside it) has each such value converted once instead, a
float to its text and a lone surrogate to U+FFFD, with a note on stderr; the file's own keys
beside the records (`version`) silently take the value the rebuild gives. So a tool call whose
arguments hold a decimal number cannot be proposed for approval
([Approvals](../permissions.md#approvals-approve-once-in-chat)).

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

**The journal is the commit point.** A write appends its changes to the journal first, every byte
of them (a short write is finished, or the write fails) and flushed to the disk itself (on macOS
`F_FULLFSYNC`: a plain `fsync` there can leave the append in the drive's cache while a later
rename reaches the disk). Then it rewrites the files, which record the journal position they
reflect (the highest `seq` per file) and a digest of what they hold. A file replaced whole (in both
stores, and `local.jsonl` when a delete on request rewrites it) is flushed, renamed into place,
and then its folder is flushed too (`journal.replace_durably`): a rename is an entry in the folder,
which a power cut could otherwise lose while keeping what was written after it. A crash between the
journal and the files leaves the files behind the journal; the next write sees the positions differ
and rebuilds the files from the journal (a command that only reads uses the rebuild without saving
it). Pulling
another Mac's changes in phase 1 is the same step. A transaction counts once all its lines are
present: a commit writes the `local` file first, then the replica's, and every line says how many
its transaction has there (`of`, and from the `local` file `private_of`). A write cut short (a
power cut mid-way, a signal between the two files, a line without its newline) leaves an
unfinished transaction at the end of a file. Readers leave it out, as if it were not there, and
the next write moves it aside (`<file>.torn-<time>`, kept) and says so. Each file is numbered
from `seq` 1 with no gap; reading one with a gap or a repeat is damage, named by file, line and
the missing `seq`.

For the ledger this is `locked_ledger`. A reader (shared lock) never writes: it rebuilds in memory
where the file is behind the journal, and on any other damage warns once on stderr and keeps the
last whole `ledger.json`, so the guard keeps working. A writer uses the rebuild in memory, and the
command's one save writes it, so `ledger.json.bak` always holds the ledger as it was before the
write; copying it (or any older `ledger.json`) back does not undo a write, because the journal
brings it forward again. A write whose rebuild keeps changes it cannot apply says how many on
stderr (with one Mac each is a bug or damage), and `bosun doctor` runs `bosun ledger validate
--journal`, failing on what refuses writes or puts data at risk and warning on the rest. A journal that is behind the file (a journal file deleted, a partial restore) is never
rebuilt from, because that would lose what it lost from the file too. A writer refuses then, and
so does a write that meets other damage where it reads (every write reads the end of each file,
and a rebuild reads all of it), with the file and line named. The way out is the same for both:
move the journal folder aside, and the next write starts a new journal from `ledger.json`.

**A journal that starts again is a new replica.** A journal starts again when it holds no line at
all while the store's files record the position of the journal they were saved from: moved aside,
emptied or removed by hand (`change_log.starts_again`, decided once per write, once it has read the
files: `take_replica` in each store). The write that starts it makes a new id and records it in
`replica.json` in place of the old one, whole and flushed before anything is minted from it
(`journal.new_replica_id`), so that no id is minted twice from the same `seq`: the files, and any
copy of the old journal kept aside, hold the ids minted from the old one. A write refused before
then (a file it cannot read) makes none. So `replica.json` may stay where it is; moving it aside as
well comes to the same. A store whose files record no position (new, or from before the journal) is
not starting again: its journal starts under the id `replica.json` holds, or one made then, once
(a gap phase 1 must close: [Open questions](#open-questions)). `replica.json` is this Mac's id in
both stores' journals (the ledger's and the knowledge base's), so a new id is new in both, which
both carry on under: the other store's old replica file stays in its journal, and its next change
goes to a file of the new id. Right after the new id, and before anything is journaled, the same
write saves the files without the old position, as files from before the journal (`ledger.json`
without its `journal` key, `view.json` with an empty stamp). The old position names `seq`s the new journal never had, which reads as lines
it lost: kept until the files record the new journal, it would make a write cut short between the
new journal's first transaction (genesis) and the files refuse every write after it. Without it,
that cut is finished by the next write like any other, the files being behind the journal. A cut
between the new id and that save makes one more id at the next write, from which nothing was ever
minted. The knowledge base's way out is the same, with `knowledge/journal/`
([Knowledge](../knowledge.md)).

**A change made outside the journal is adopted.** `ledger.json` can still be changed by something
that does not journal: by hand, or by an older bosun (after a downgrade, or a checkout's bosun on
the same state folder), which writes every top-level key back as it found it, the journal
position included. So the file also records the digest of what it holds (`journal.digest`: the
SHA-256 of its canonical bytes without the `journal` key). A write that finds what the file holds
no longer matching its digest puts the difference in the journal first, as one transaction named
`adopt` (the diff from what the file held when it was saved to what it holds now, with what a
write cut short adopted already counted), says so on stderr, and always goes on from the
journal's rebuild, so that `ledger.json` stays the rebuild. The file is what someone outside the
journal meant, and refusing would stop every write. Adopted changes follow the same rules as any
other: a field is set, an item made or deleted, a history entry added. What the journal cannot
hold is undone by that write, which says so in one line (how many parts, and that
`ledger.json.bak` keeps the file as edited until the next write): history only grows, so an entry removed by hand comes
back, and one edited in place comes back beside its edited copy. A file saved before digests is
trusted once and gets one at its next save. The knowledge base does the same with the digest in
`view.json` ([Knowledge changes](#knowledge-changes)).

### Rebuilding, and what a merge cannot decide

Merging is the union of all changes, deduplicated by `id`. Rebuilding applies them in order and
checks, in the same way on every machine, each rule a merge cannot keep. A change that breaks one
is kept in the log, has no effect, and becomes a **conflict record**:

| Data | Merge rule | A conflict when |
|---|---|---|
| The changes themselves | Union by `id` | never |
| Item creation | The same id made on two Macs (one PR, one Slack message) is merged: what the item lacks is added, histories are unioned by time; the state and important fields (a proposal whole, never blended) stay as the earlier one made them | the two differ in state or an important field, or the later one's link would break a lineage rule (checked once its write's items are all made) |
| Item fields (`title`, `url`, `fields.*`) | Last writer wins per field; losing values stay in the journal | the field is marked important (a ticket's system, a learning finding's key) |
| Item state | A state change carries its `from`; the first valid one in order applies | a later one whose `from` no longer holds, or a move its kind does not allow |
| History entries | Grow-only, shown in order | never |
| Outreach messages | Added at the end; a corrected time drops the message and adds it again; dropping one already gone does nothing | never |
| Processed Slack sources, runs | Last writer wins per key; `prune` deletes old sources | never |
| Lineage links | Add wins over a concurrent unlink; inverses computed as today | an add would close a cycle, or give an item a second `part_of` |
| Proposals and approvals | One slot per item; an approval's `used_at` set once | two proposals in one slot; an approval used twice |
| Item deletion (`prune`) | A tombstone; delete wins; the Mac that deleted it may make it again (a pruned ticket tracked again) | a change other than a delete arrives for a deleted item, or another Mac makes it again |
| Fact assertions | One per source; a fact is the group sharing subject, predicate and object | never |
| Single-valued predicates | Keep every confirmed value | more than one current confirmed value |
| Review decisions | Last writer wins per fact and reviewer | confirm and reject by different reviewers at once |
| Entities | Last writer wins per field; aliases unioned; delete wins | a proposed merge of two entities |

A change this bosun cannot apply, an operation it does not know (from a newer version) or a body
it cannot read, is kept the same way and shows as a conflict, as git-bug keeps operations it does
not recognize; so is a change for an item that was never made (`missing`).

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
  others. Today the rebuild re-checks every change under the current rules, which holds only while
  no rules change removes a move: before one does, or phase 1, it re-checks only a change that
  lands on changes its writer had not seen ([Decisions (2026-11-04)](#decisions-2026-11-04)).

**Existing data** enters the journal once, as a snapshot change per item, entity and fact, when a
Mac first turns on the journal (phase 0). History before that is whatever the files still hold:
trimmed history and pruned items are gone already. For the ledger, the first write's genesis
transaction (command `genesis`) is the diff from the empty ledger: a create per item, by id, with
its local leaves split off as for any new item, a put per source and run, and what I said. Its
rebuild is the file again, old numeric ids, trimmed histories and approvals included. The file is
saved at its new position straight away, with its own content, so that a position it brought
from elsewhere (a copy, such as the chores dry run's) ends there even if the command fails.

### Where the ledger's changes come from

The ledger has 29 places that write, all through one function: `locked_ledger` loads the
document, hands it to the command and saves it. That function copies the document first and
compares afterwards, turning each write into one transaction of field-level changes. A table maps
each field path to its merge rule and its class ([Which data goes where](#which-data-goes-where)),
in the table style of `KINDS`, `RELATIONS` and `CHECKS`. A state change becomes a change with its
`from`, one per transition the write made (a one-way message is sent, then applied, in one
write), so the rebuild checks each against its kind. Added history entries, links and messages
become additions; a removed link becomes an unlink; a message whose time was corrected is dropped
and added again; a pruned source is deleted from its map; a field that was removed travels as a
set marked `unset`, so the rebuild removes the key rather than setting it to null. A write that
changed nothing adds nothing to the journal (the files are still rewritten, since `ledger render`
relies on that). The command name goes on the transaction.

The alternative, an explicit change emitted by each command, keeps more intent but spreads sync
across 29 places. The comparison keeps it in one, and the command name recovers most of the
intent. If a case needs more (for example `spawn`'s de-duplication by title), that command can
emit an explicit change through the same journal later; nothing in this design prevents mixing.

Three ledger behaviours change meaning:

- **History trimming** (200 entries per item) happens only in the rebuilt file. The log keeps
  everything.
- **`prune`** writes tombstones, so a pruned item disappears from every machine.
- **`learn:` items** keep their change on the item instead of in `learn/state.json`, so a synced
  `learn:` item shows what it would change. Only the Mac whose learn proposed it can apply it:
  apply compares the item and its proposal with digests in that learn's own state
  ([learning](learning.md)).

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

Built so far: the journal sits in `knowledge/journal/`, under the knowledge base's own lock, and
each write (`knowledge.opened`) is one transaction named by its command (`kg propose`, `kg batch
apply`, `console review`). `lib/knowledge_changes.py` turns a write into its changes: each record
it appended to the fact log, as it is (`fact.add`, `fact.update`), and each entity it made, changed
or removed (`entity.put` with the whole entity, `entity.delete`). An update record says what the
fields it sets held before (`prev`), so the rebuild can tell when two updates crossed (a confirm and
a reject of the same proposal). A change that names a person (a person entity, every record of a
fact whose subject or object is one) is `local`, and so is every record of a fact I stated, whole,
from the write that brings my words on (one whose sources hold my quote or name one of my sessions;
its records written before then stay `private`: [Where we expect to
iterate](#which-data-goes-where)); the rest is `private`. A new fact's id is minted
from the transaction (`f-` and six characters); facts from before keep theirs (`f12`), and a
minted id that already names a fact is minted again. The journal is written first, then the files,
then `view.json`: the position the files reflect and the digest of what they hold (the entities
and the fact log), as `ledger.json`'s `journal` key does. A knowledge base from before the journal
enters it as one transaction (`genesis`) holding every entity and record, with what the journal
cannot hold converted once, as for the ledger.

The files are the journal's rebuild (`knowledge_changes.rebuild`), in the one order: an entity is
put whole or deleted, and the fact log keeps its records in that order, folded as the files are
read (`knowledge_model.Fold`), so an update that comes before its fact's add waits for it and one
whose add never comes is listed as pending. An update whose `prev.status` is not the fact's status
where it is applied (a confirm and a reject of one proposal at once) is a `field` conflict and not
applied; an op or a body this bosun cannot apply is kept the same way. The write path does what the
ledger's does, in the same order: it refuses a journal behind `view.json`, sets an unfinished
transaction aside, puts a knowledge base from before the journal in it (genesis), adopts a change
made to the files outside it as one `adopt` transaction, and goes on from the journal's rebuild
where the files are behind it or were changed outside it, saving the files whole. `view.json`
records a digest of each data file, which tells the two apart: a file holding neither what
`view.json` recorded nor what the rebuild makes was changed outside the journal (by hand, a git
checkout or revert of files in the knowledge folder, an older bosun); when each file is one or the
other, a write was cut short somewhere between the journal and `view.json` (written last), and the
rebuild finishes it, even at the same position. Adopting such files would hold that write's
records twice, or bring back a person a `kg forget` cut short had already placeholdered. The fact
log's append goes through the journal's own write and flush (`write_all`, `flush_to_disk`), and
`forgotten.jsonl` is written whole, but a power cut or a full disk during an append can still leave
a data file ending in a line that does not parse: the journal holds every record, so such a file is
one the rebuild replaces (a write saves it, a reader uses it in memory and says so on stderr, and
`kg validate` warns, `unreadable`), and a `forgotten.jsonl` that does not parse is written again
from its other lines with the records the deletion markers carry. Only where no journal can rebuild
it (none yet, one that cannot be read, or one behind `view.json`) does such a file stop every
command, naming the line. A deletion
commits a local `deletion` marker (no target, skipped by the rebuild) with its transaction, before
it rewrites what it removed into placeholders; the marker holds the write's `forgotten.jsonl`
records (a keyed hash and counts, nothing of the person). So the position moves, and a deletion
cut short anywhere is finished by the next write, which puts the placeholders in again for the
marker's hashed ids and deletes what writes cut short left of `local.jsonl`, as the deletion
does, and the knowledge folder's `*.tmp` (a `kg batch` cut short before its rename leaves
`batch.json.tmp`, which names people). A keyed record takes the key to finish: while `secret.key`
cannot be read, that write is refused whole, naming it, and the first write once it is back
finishes the deletion. The marker also holds the ids of the facts its write removed (ids only), so
a retention purge cut short between its marker and its placeholders is finished the same way,
although its record holds no hash. Who is forgotten is `forgotten.jsonl`
together with the records every deletion marker carries, and a write first writes a marker's
record that `forgotten.jsonl` lacks, before anything is taken in: a forget is whole through the
journal, and the way out when the key is lost for good moves the journal folder aside with
`forgotten.jsonl`, and `secret.key`, if it is there (`replica.json` may stay: the journal starts
again as a new replica either way). The key is made as
`replica.json` is (whole and flushed, then linked to its name), before anything hashed with it is
journaled; with no keyed record anywhere, a `secret.key` that is not a key (empty, the wrong length,
unreadable) is moved aside, kept and ignored by the history, and made again, with a line on stderr.
The identifiers and
`forgotten.jsonl`, which the journal does not hold, are written before the data files, and a write
that finishes a deletion drops the identifiers of entities gone. A history restart is a debt: a
write that finishes a deletion cut short, applies one made outside the journal, or drops or
refuses a person deletes the knowledge folder's `.git` once its files are final and before
`view.json` (whatever `BOSUN_KNOWLEDGE_GIT` says), and its commit starts a new history, so that
no cut leaves the person in it. A `.git` that is still there after the delete stops the write
there, naming it (fail closed: a deletion cut short is then finished by the next write). What an
outside change cannot express (a record removed: the fact log only grows) comes back; a record
edited in place comes back beside its edited copy, which is adopted, so an edited `add` takes the
fact over (as an edited history entry comes back in the ledger). That write says so in one line, and first commits the files as
edited to the knowledge folder's git history, which keeps them (with the history off, nothing
does, nor when the write starts the history again, as it holds what the write deletes; the save
says the line, as only it knows). A write cut short and then a hand edit before the next write adopts that write's records a
second time (a second `add` takes its fact over): accepted, as rare and visible. So are two more
double faults: a write cut short while it pays a restart owed for refusing a person (a debt with
no marker) can leave an older bosun's commit of that person in the history until the next
`kg forget`; and `view.json` restored with data files from before a forget, or removed, makes
every marker after the saved position unfinished, so a later `--allow-forgotten` re-add is
forgotten again (the safe direction). And when one data file does not parse, the other one edited
by hand before the next write is replaced by the rebuild too, not adopted (the write names only the
file that does not parse). One more, in both stores: when a journal that starts again has its
genesis cut short, the files record no position and no digest until the next write
([Changes, not files](#changes-not-files)), so a hand edit made to them in that window is not seen
as one: the rebuild replaces it without a word. The ledger keeps the edited file in
`ledger.json.bak` until the write after that; the knowledge base does not keep it. Adopt never brings
in or keeps a person deleted on request: one gone from the files whom a forgotten record matches
(a plain or a keyed hash: an older bosun's `kg forget`, or a hand removal) is forgotten in the
journal too, as `kg forget` does it (its marker, then the placeholders, over the whole journal,
and the history restart owed), and one in files restored from before a forget, with no
`--allow-forgotten` add in the journal, is not adopted (one line, without naming them), the files
as edited are not committed, and the rebuild drops them. While the key of the forgotten cannot be
read (keyed records and no `secret.key`), the forgotten check cannot run, so adopt takes in no
person entity at all, as adding one is refused then, and says why. A reader shows such files as
they are until the next write (reads never write), and person facts an older bosun's retention
purge took out come back from the journal until the next `kg expire`. A write that rebuilds with conflicts or
pending updates says how many on stderr. A command that only reads uses the rebuild in memory and
writes nothing. `bosun kg validate` compares the files as saved with the journal's rebuild, with
the ledger's findings and severities (`lib/validation.py` holds what both stores share: the
finding, its severity and line, and the decisions from the position, the digest and the
comparison), and `bosun doctor` runs it.

A delete on request (`kg forget`) and the retention purge rewrite only `local.jsonl`, where all
that names a person is: each of its changes becomes a placeholder that keeps its envelope's place
(`v`, `stream`, `replica`, `seq`, `hlc`, `tx`, `of`, `local`, `private_of`) with op `forgotten`, an id of its
own and no target or body. So the local sequence and the ids minted from it never go back, and its
transaction stays whole; the rebuild skips placeholders. The private journal is never rewritten:
a deletion that would have to remove a change from it is refused. What writes cut short left of
`local.jsonl` goes too, whatever it held (its `.torn-<time>` tails, never committed and not
readable enough to pick one person out, and a rewrite's unfinished copy), as does what they left
in the knowledge folder (`*.tmp`), and the git history never holds any torn file.

The local git history of `knowledge/` stays as it is for now, and holds the journal and `view.json`
like the other files. It is local, it never syncs, and `kg forget` keeps restarting it, which also
takes the person's changes out of earlier commits. Once the log exists it duplicates the log's
history, so a later change may retire it ([Open questions](#open-questions)).

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
the same on every rebuild of a fixture whose writes change the same fields (the console fixture's
build gives each write a second of its own, since a write in the same second leaves `updated_at`
as it was), and short enough to type. Two Macs collide only by a hash collision, about one in a
billion for a pair of ids; one that names an item already is minted again. A learning id hashes
the replica, the finding's key and the run instead, so the same finding in the same run is the
same item. Existing ids stay as they are: only the first Mac has them.

A new Mac joins by restoring from the state repository and starting with no ledger of its own. A
Mac that already has its own ledger cannot join in phase 1: merging two ledgers that grew apart
(renaming clashing generated ids, and deciding between two histories of the same `review:` item)
is a separate piece of work, left until someone needs it ([Open questions](#open-questions)).

### Which data goes where

Every field belongs to one class. The class decides how far it may travel:

| Class | Leaves this Mac | Examples |
|---|---|---|
| `local` | Never | `said` (my verbatim prompts); an approval's quote; a call's session, transcript path and tool call id, wherever an approval, a standing approval or the call's outcome (`failed`, `carried_out`) keeps them; the history entries that quote me (`approved`, `approval-used`, `user-said`) or keep a call's context; my words as a link's or a task's `via` or a transition's evidence (`user: ...`), with the link or history entry that holds them; `identifiers.jsonl`; `forgotten.jsonl` and `secret.key`, the key of its hashes; person entities and every fact naming a person; every record of a fact I stated (one whose sources hold my quote or name one of my sessions), whole, from the write that brings my words on (its records written before then stay `private`); `batch.json` |
| `sealed` | Only to my own scope, and only when the data is encrypted (by the codec, or by a store that declares encryption at rest) | a learning finding's key (a slug the model wrote, which can name a colleague); free text: titles, URLs, notes, history notes, report summaries, open points and results, outreach subjects, details, options and answers, a task's ask, done-when and `via` (unless it quotes me), a delegation's ask, details and link, a proposal's action and its exact call (`spec`), a failed call's reason, a workstream's aliases; colleagues' names in `requester`, `to` and a ticket's `assignee`; Slack permalinks (a reply's or request's ref, processed sources) |
| `private` | To my own scope | item ids, kinds, states, links, history events, a proposal's times, a report's time and CI status; repo, ticket and system names; the window in a delegation or report, tagged with its replica; process and product facts |
| `team` | Also into a published projection | item id, kind, state and owner for the team board; confirmed, non-personal facts a share spec selects |

The rules that keep this clean:

- **One table per stream** (field paths for the ledger; entity type, predicate and sensitivity for
  knowledge). Moving a field to another class is a change to one row.
- **Unknown means `local`.** A new field that nobody classified stays on the Mac, so forgetting
  the table fails safe.
- **The table is total.** Every key is named, with no wildcard below an item, so an unnamed key is
  `local`. A test walks every field of the fixture ledger, of the ledgers that real commands write,
  and of the knowledge base, and fails if one is unclassified, so a new field forces a decision in
  the same pull request.
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
- **Links and history notes can quote me.** A link's or a task's `via` and a transition's evidence
  that quote me (`user: <what I said>`) are `local`, as the history entries that quote me are: the
  link or history entry stays on the Mac whole, so another Mac misses that link, and a task's `via`
  stays as a field of its own. Other history notes (a task's ask, a note) can still hold my words
  and stay `sealed`, so phase 1's gate strips them when encryption is off.
- **Learn's evidence quotes.** A `learn:` proposal's action holds evidence quoted from my prompts
  or a session transcript, and so do its `proposed` and `proposal-*` history entries; they are
  `sealed`, and stay so ([Decisions (2026-11-04)](#decisions-2026-11-04)): with encryption off,
  they stay on the Mac.
- **A fact I stated stays on the Mac.** A knowledge fact whose sources hold my words (a `user`
  source carries my quote and the session it was checked in) or name one of my sessions (a
  `transcript` source) is `local`, every record of it and whole, from the write that brings my
  words on, as the ledger keeps a link whose `via` quotes me. So another Mac misses facts that
  are often the most valuable, such as a rule I stated about a process. Phase 1 splits the quote
  and the session off, so that the fact itself travels and my words stay here ([Decisions
  (2026-11-04)](#decisions-2026-11-04)).
- **Nothing removes a history entry.** The ledger's journal keeps every change, so a note typed by
  mistake (a colleague's name, something private) stays in the journal even after a hand edit
  (which the next write undoes) or a `prune` (which deletes the item, not its earlier changes).
  Today the only way out is to move the journal aside, which starts it again from `ledger.json`.
  A ledger forget is open for phase 1, before any of it leaves the Mac: as planned for the
  knowledge base's `kg forget`, a forgotten change would keep its place in the sequence and lose
  its body.

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
- `lib/validation.py` (pure): what both stores' `validate` finds, alike: the finding, its
  severity and line, and the journal against the files.
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

- **Rebuild cost (measured 2026-11-03, phase 0):** a synthetic year written through
  `locked_ledger` (100 items, about 500 changes each: a note and a field per round, 52,436 changes,
  a 20.7 MB journal for a 3.0 MB `ledger.json`) takes 0.18 s to read and order and 1.09 s to
  rebuild on an Apple-silicon Mac (Python 3.15). An ordinary write never rebuilds: only the
  commands after a crash until one writes, and in phase 1 each pull. That is over a second, so
  phase 1 needs snapshots: a rebuilt state saved with the position it reflects, and only the
  changes after it applied. The write itself costs more with the journal (the copy and the diff):
  about 3 ms instead of 1 ms at the fixture's size, and 40 ms instead of 15 ms for a 3 MB ledger,
  against about 80 ms to start the command. Since 2026-11-03 each write also hashes the ledger twice
  (to notice a change made outside the journal, and to record its digest), about 15 ms each for a
  3 MB ledger and under 1 ms at the fixture's size, and flushes the journal and `ledger.json` to the
  disk itself (`F_FULLFSYNC`, about 5 ms each on an Apple-silicon Mac). Since 2026-11-04 each
  replaced file (`ledger.json`, `ledger.md`) also flushes its folder, so that the rename itself
  survives a power cut: about 9 ms each.
- **Knowledge rebuild cost (measured 2026-11-04, phase 0):** 500 processes and 500 steps with
  20,000 `has_step` facts imported in one transaction (21,000 changes, a 12.2 MB journal for a 6.6
  MB `facts.jsonl`) take 0.36 s to read and order and 0.25 s to rebuild on an Apple-silicon Mac
  (Python 3.15). A reader pays it only where the files are behind the journal (a write cut short);
  the same remark as for the ledger holds for phase 1. A write reads the journal once and passes
  that read on, and skips looking for unfinished changes when `view.json` is at the journal's
  position; it rebuilds only where the files are not as saved. Measured 2026-11-04 at 6,000 facts
  (a 5.3 MB journal, a 3.6 MB `facts.jsonl`, the history off): an ordinary write read the journal
  three times (0.12 s of 0.25 s) and now reads it once (0.04 s of 0.19 s); a write after a hand
  edit read it 15 times and rebuilt 7 times (1.12 s of 1.66 s) and now reads it once and rebuilds
  twice, the journal before and after it adopts (0.19 s of 0.69 s). Both still grow with the
  journal, which phase 1's snapshots answer.
- **Rules versions in the rebuild (both stores):** every change records `rules`, but each store's
  rebuild checks it against today's rules: the ledger's `KINDS` and `RELATIONS`, the knowledge
  base's ops (the ones its dispatch table applies and the ones it skips) and the fields an update
  must find as it left them (`knowledge_changes.APPLIED`, `SKIPPED` and `CHECKED`). A change to
  them that removes a move, tightens a link rule or checks another field
  would turn a Mac's own valid history into conflicts, and a write that rebuilds after a crash
  would revert those states. Before the next such change, or phase 1, the rebuild must re-check
  only a change that lands on changes its writer had not seen ([Decisions
  (2026-11-04)](#decisions-2026-11-04)), and a change that alters what earlier changes mean needs
  its migration. A unit test per store pins a
  digest of its rule tables to its version (`RULES_VERSION` in `lib/ledger.py`, `KG_RULES_VERSION`
  in `lib/knowledge.py`), so such a change cannot land unnoticed.
- **One replica id on two Macs:** a Migration Assistant or Time Machine restore onto a second Mac
  copies `replica.json`, and both Macs then write as one replica. Phase 1 must notice it (two
  journals with the same `seq` and different changes) rather than merge them.
- **A store started anew under the old replica id:** a journal starts again under a new id only
  when the store's files still record a position ([Changes, not files](#changes-not-files)). A
  store whose journal and files are both gone, while `replica.json` is kept, looks new: its next
  write starts its journal under the old id at `seq` 1 in each file, and so mints the ids of the
  store it replaces. A documented command gets there: `bosun uninstall --delete-knowledge` keeps
  `replica.json`, which is classed with the ledger (`lib/uninstall_plan.py`, [Setup](../setup.md)),
  so a knowledge base made again after it mints the deleted one's fact ids. Harmless in phase 0,
  where nothing holds them any more. Phase 1 must handle it, as the old journal may already be in
  the state repository: one way is for `replica.json` to record which streams have started under
  its id, so that a stream starting under an id it used before gets a new one.
- **Causal context for a change after a delete:** an item that another Mac deleted and this one
  makes again stays deleted, because the rebuild cannot yet tell a Mac that saw the delete from one
  that made the item at the same time. Phase 1 adds what each transaction was built on (the
  journal position it read) so that one that saw the delete can start the item again, and so that
  the rebuild knows which changes landed on changes their writer had not seen.
- **Joining with an existing ledger:** a Mac that already has its own ledger cannot join in phase 1.
  If someone needs it: rename its clashing generated ids, and decide how two histories of the same
  `review:` item combine (one wins, both kept, or a conflict per item).
- **Team credentials:** per-member fine-grained tokens or a GitHub App; and whether GitHub rulesets
  can restrict pushes by path or branch per member (not verified).
- **`age` details:** whether it can read an identity from stdin rather than a temporary file, and
  whether the Secure Enclave plugin (Touch ID on decrypt) suits an unattended check-in loop.
- **The knowledge base's local git history:** keep it, or retire it once the journal holds the
  history.
- **A note on a product fact is `private` (phase 1).** A knowledge record's free-text `note`
  (`--note`, or an import's `note`) on a fact that names no person and holds none of my words is
  classed with the rest of the record, `private`, while the ledger keeps its notes `sealed`.
  Phase 1 decides the knowledge base's class table at that level (fields, not whole records).
- **A git checkout or revert in the knowledge folder moves the journal back:** the journal is in
  that history, so a `git checkout` or `git revert` there takes it back with the files. The changes
  made after that point are lost, and the local `seq` and the seeds of minted ids go back with it,
  so a fact id could be minted again (one that names a fact still there is minted anew, but the id
  of a fact deleted since can come back). In phase 0 nothing else holds those ids. Before phase 1
  syncs facts, the journal needs a history that git cannot rewind, or a check that refuses a
  journal that went back.
- **When person facts become `sealed`:** after key epochs make deletion reach the remote.
- **A simpler forget, phase 1's starting point.** Phase 0's forget holds at every cut point tried,
  but "nothing of a forgotten person survives" is spread over thirteen places in three modules, and
  three of its decisions hang on `view.json`'s saved position and the adopted base, which is why
  double faults remain (a restart owed for refusing a person, and `view.json` restored with older
  files). Phase 1 has to rework forget anyway, so that it travels to other Macs; it starts from this
  design (the final review of phase 0's knowledge journal), with the same guarantees and fewer gaps:
  - **One ordered status per hashed id.** A forget marker and an allow marker (journaled by
    `--allow-forgotten`) are both local, and the later one in journal order wins. Who is forgotten
    then never depends on the saved position, which closes the restored-`view.json` case.
  - **One idempotent scrub at write start.** For every person whose status is forgotten, their
    changes become placeholders in `local.jsonl`, they are dropped from the files in memory, and
    the leftovers are deleted. This replaces finishing a cut forget, applying one made outside the
    journal, and the forgotten half of what adopt refuses and holds back. The retention purge's
    marker lists its fact ids (as it now does), so the scrub finishes that too.
  - **The history restart as a durable debt.** Recorded before anything else is touched (a local
    marker, or an ignored `restart.owed` file), paid at the save, and cleared after the new
    history's first commit. This closes the restart owed for refusing a person, and the gap a cut
    between deleting `.git` and `view.json` leaves.
- **Several bosun versions in one team:** how long a replica keeps writing when a teammate runs a
  newer format.
