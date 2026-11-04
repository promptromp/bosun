# Design note: organizational knowledge (People, Process, Product)

Status: accepted (2026-10-21); MVP 1 built (`bosun kg`, see [Implementation](#implementation-mvp-1));
MVP 2 built (the console's Knowledge view, see [Console](#console-knowledge-view-knowledge-mvp-2)),
with the Briefing and Constellation tabs (see [the page](#the-knowledge-graph-page-briefing-constellation-raw-data-2026-10-21)).
Replaces the earlier ontology note (2026-10-16). Tracked in
[ROADMAP.md](../../ROADMAP.md#4-organizational-knowledge-people-process-product).

## Why

A week of real use showed that the most expensive mistakes come from knowledge that was missing,
not from bad code. A survey of the week's sessions found about a hundred reusable facts and a
dozen episodes where a missing fact cost time or caused an error. For example, the orchestrator
asked again which channel deploys are announced in, a day after it was told, and it pinged
reviewers on PRs that were already approved. An expired-looking login was really a leftover
credential, twice. A task was started against an environment that had been retired. Most of these
facts were stated once, in the user's own words, and then lost when the conversation ended.

## What the research says (summary)

- **Graph retrieval helps on multi-hop and temporal questions, not on simple lookups.** Plain
  files plus good search are hard to beat (in one published agent-memory benchmark, grep-style file
  memory scored ahead of a graph-memory variant). Corpus-wide graph indexing is the wrong tool at
  this size.
- **What works in agent memory** (temporal knowledge graphs, memory layers for agents):
  - keep raw episodes as the source of truth;
  - store atomic facts that link to their source;
  - give facts two timelines: when they were true, and when the system learned or retired them;
  - when facts conflict, close the old one, never delete it;
  - match entities against aliases first, with an LLM only suggesting merges;
  - write only through fixed operations.
- **Context injection can hurt.** A study of repository context files found that general context
  raised cost by about 20% without improving success, while specific, non-obvious conventions
  helped. So deliver small task-specific packs and on-demand lookups, never an always-loaded
  profile.
- **People data is personal data** (GDPR, CCPA). Keep it to work-operational facts, recorded the
  way you could show them to the person, with retention limits and delete-on-request. Evaluative
  or inferred-behaviour attributes are out.

## Shape

**Storage (site-specific, never in the repo):** `$BOSUN_CONFIG_DIR/knowledge/`, a local git
repository so every change has history.

- `entities.jsonl`: one entity per line, with these fields:
  - `id`, for example `person/dana`
  - `type`
  - `name`
  - `aliases`, split into strong (true synonyms) and weak (related terms), following SKOS
  - `summary` (never for a person)
- `facts.jsonl`, append-only: an `add` record per fact, then an `update` record per change, which
  also says what the fields it sets held before (`prev`). Each fact has an id (`f-` and six
  characters, minted from the journal so that two Macs never make the same one; `f12` for facts
  from before), a subject, a predicate, and either an object (another entity) or a value. It also
  carries:
  - `sources`: each a kind and a reference (see below)
  - `authority`: user said, stakeholder said, observed, or inferred
  - `observed_at`, `observations`
  - `valid_from`, `valid_to`, `superseded_by`
  - `status`: proposed, confirmed, rejected or expired
  - `last_confirmed_at`
  - `sensitivity`: internal, or personal for anything naming a person
  - `prevents_rework`: whether not knowing it caused rework
- `identifiers.jsonl` (restricted, mode 0600): chat member ids, tracker account ids, code-host
  logins, needed for mentions and routing. Never shown in context packs.
- `journal/`: every change to entities and facts, which each write appends to before it writes the
  files above, one transaction per command ([Sync and sharing](sync.md#knowledge-changes)):
  `<replica>.jsonl` for this Mac's changes, `local.jsonl` for those that never leave this Mac:
  those naming a person (a person entity, a fact whose subject or object is one), and every record
  of a fact I stated (one whose sources hold my quote or name one of my sessions), whole, from the
  write that brings my words on; its records written before then stay private.
  `view.json` records the journal position the files reflect and a digest of what they hold. Both
  are in the local git history like the other files; what a write cut short left
  (`<file>.torn-<time>`) is not.
- `entities.jsonl` and `facts.jsonl` are the journal's rebuild (`knowledge_changes.rebuild`): its
  changes in the one order, an entity put whole or deleted, the fact log's records kept in that
  order and folded as the files are (an update that comes before its fact's add waits for it).
  An update that finds the fact's status no longer what it left (`prev`: a confirm and a reject of
  one proposal at once) is a conflict and not applied. A write that finds the files behind the
  journal (a write cut short) goes on from the rebuild and saves it; a reader uses it in memory and
  writes nothing. `view.json` records a digest of each data file: when each file is either as
  recorded or as the rebuild makes it, a write was cut short somewhere between the journal and
  `view.json` (written last), and the rebuild finishes it even at the same position. A deletion
  (`kg forget`, the retention purge) also commits a local `deletion` marker holding its
  `forgotten.jsonl` record (the keyed hash and counts), so the position moves and a deletion cut
  short before the files is finished too, its record written by the write that finishes it; a
  write writes `identifiers.jsonl` and `forgotten.jsonl` before the data files, and one that
  finishes a deletion drops the identifiers of entities gone. A file that is neither is a change
  made outside the journal (by
  hand, a git checkout or revert of files here, an older bosun): the next write adopts it as one
  `adopt` transaction (entities made, changed or removed, and records the fact log gained), then
  goes on from the rebuild. A record removed cannot be adopted (the fact log only grows) and comes
  back; one edited in place comes back beside its adopted copy, and an edited add takes the fact
  over. The write says so, and first commits the files as edited to the git history when it is
  on and the write does not start it again (a history it restarts holds what it deletes, so then
  nothing keeps them, and the line says so); the line is said by the save, which knows that. A
  write cut short and then a hand edit before the next write adopts that write's records a
  second time (accepted: rare, visible). A person deleted on request is never adopted. Forgotten
  is `forgotten.jsonl` with the records the journal's deletion markers carry (a write first puts
  back one the file lacks), checked by a plain or keyed hash everywhere: adding, adopting, and the
  key check. One gone from the files whom a forgotten record matches (an older bosun's
  `kg forget`, a hand removal) is forgotten in the journal too, as `kg forget` does it: its
  marker first, then the placeholders, over the whole journal. One in files restored from before
  a forget, with no `--allow-forgotten` add in the journal, is left out (one line, not naming
  them), the files as edited are not committed, and the rebuild drops them. While the key of the
  forgotten cannot be read, no person entity is adopted at all (one line saying why). A history
  restart is a debt: a write takes it on when it finishes a deletion cut short (its marker after
  the saved position; the placeholders for the marker's hashed ids are put in again, and what
  writes cut short left of `local.jsonl` is deleted, as the deletion does), applies a
  forget made outside the journal, or drops or refuses a person. It pays it once, at the save,
  after the files and before `view.json`, by deleting `.git` whatever `BOSUN_KNOWLEDGE_GIT` says;
  the save's commit starts a new history. Finishing a `kg forget` cut short takes the key: while
  it cannot be read, the write is refused whole, naming the key (fail closed, as adding a person),
  and the first write once it is back finishes the forget. A reader shows restored files until the
  next write; an older bosun's retention purge is redone by the next `kg expire`, as is a purge cut
  short between its marker and its placeholders (its marker holds no hash to finish it by). Two
  more double faults are accepted as rare: a write cut short while it pays a restart owed for
  refusing a person can leave an older bosun's commit of that person in the history until the
  next `kg forget` (that debt has no marker); and `view.json` restored together with data files
  from before a forget, or removed, makes the next write treat every marker after the saved
  position as unfinished, so a later `--allow-forgotten` re-add is forgotten again (the safe
  direction).
  `kg validate` reports damage, a position that does not match, a rebuild that differs,
  conflicts, updates waiting for their fact and an outside edit; `bosun doctor` runs it
  (`lib/validation.py`, shared with the ledger's validate).
- When the journal is damaged, or behind `view.json` (a journal file lost), a write refuses and
  writes nothing. The way out, as for the ledger: move `journal/` and the state folder's
  `replica.json` aside together; the next write starts a new journal from the files (genesis) as a
  new replica, so no mint seed repeats. `replica.json` is shared by the ledger and the knowledge
  base, so this gives the Mac a new id in both journals, which both carry on under (the other
  store's old replica file stays in its journal).
- Derived views (entity pages, indexes) are rebuilt from the log and never hand-edited.

**Schema (generic, in the repo).** A closed set of types and predicates in
`lib/knowledge_schema.py`; a site can extend it with `knowledge/schema.json` (new types and
predicates, never about a person):

| Axis | Entity types | Predicates |
|---|---|---|
| People | person, team | `member_of`, `role`, `title`, `timezone`, `stated_working_hours`, `prefers_channel`, `stated_preference`, `reviews` (area or repo), `owns`, `approves` (for example hotfixes), `alias_of`, `distinct_from` |
| Process | process, step, gate, channel, system | `has_step` (ordered), `actor`, `gated_by`, `entry_point` (for example a channel), `uses`, `posts_to`, `escalates_to`, `purpose`, `rule`, `caveat` |
| Product | repo, service, environment, feature, decision, principle, lesson | `depends_on`, `replicates_from`, `status` (for example retired), `decided` (dated), `supersedes`, `partially_reversed_by`, `states`, `learned_from` (episode), `about`, `part_of`, `lives_in` |

Single-valued (a second current value is a contradiction): `title`, `timezone`,
`stated_working_hours`, `alias_of`, `entry_point`, `purpose`, `replicates_from`, `status`,
`decided`, `states`. High-risk (never confirmed by sources alone): `owns`, `approves`,
`gated_by`, `escalates_to`, `rule`, `status`, `decided`, `supersedes`, `partially_reversed_by`,
`states`.

Examples, in the fictional acme world:

- **The release process:** merge to main, deploy to staging, canary checks (smoke tests, error
  rate), then a gradual rollout to production. Deploys are announced in `#deploys`.
- **The hotfix process:** the entry point is the `#deploys` channel. Only the user approves a
  hotfix; agents prepare the change and its notes.
- **Environments:** `environment/staging-2` is `status: retired`, and "a retired environment takes
  no new work" is a lesson linked to it.

## How it grows (capture)

1. **From the user's own words, the most valuable source.** Today you run
   `bosun kg propose ... --from-user --quote '<their words>' --transcript <session>` by hand.
   Confirmation reuses the approvals check: if the quote appears in a genuine user prompt in that
   transcript, the fact is confirmed. Otherwise it stays proposed. Planned for MVP 3: when the
   user states a standing fact or rule in any window ("deploys are announced in #deploys"), the
   session runs that command itself. No prompt or task rule asks for it yet.
2. **From the daily learning run** (planned, Later). `bosun learn` already reads the week's
   sessions and the ledger, but it has no knowledge pass yet. That pass would propose facts with
   sources (reports, ledger items, permalinks), and flag facts that were rediscovered or caused
   rework. Nothing from chat, PR, ticket or document text is ever confirmed automatically, because
   it is untrusted.
3. **Seed import.** A survey's facts and the existing memory notes are imported as proposals
   (`kg import`, a dry run first), for one review pass.
4. **Maintenance (lint).** It flags:
   - stale facts (not re-observed in `BOSUN_KNOWLEDGE_STALE_DAYS`);
   - contradictions (two current values for a single-valued predicate);
   - orphan entities;
   - alias collisions (two people sharing a first name, unless a `distinct_from` says so);
   - merge suggestions (near-identical names of one type).

   Person facts not re-observed for 12 months (`RETENTION_DAYS`) are reported by `kg lint` and
   deleted by `kg expire`, which you run by hand; nothing runs it on a schedule yet.
   `kg forget person/<id>` deletes a person on request.

## How it is used (retrieval)

- **CLI:**
  - `kg lookup <entity>` (current facts with sources)
  - `kg facts <subject> [--predicate] [--as-of]`
  - `kg search <text>`
  - `kg neighbors <entity> [--hops 2]`
- **Context packs:** `kg context --for <repo|process|topic>` returns at most about 1.5k tokens of
  cited, current, confirmed facts. `bosun delegate` appends the pack for the task's repo and
  area to each brief (behind a flag, MVP 3). The orchestrator policy gains one rule: check
  `kg lookup` before asking the user about routing, reviewers, channels or process.
- **Measurement** (planned, with the learning run's knowledge pass): the learning run counts
  rediscovery and rework episodes per week. Packs stay only if that count falls, and pack size and
  cost are tracked.

## Console: Knowledge view (`#/knowledge`, MVP 2)

- **Browse by axis** (People, Process, Product, Systems), with search and type filters, and counts
  of proposed facts waiting for review.
- **Entity page:**
  - current facts with authority badges, dates and source links;
  - superseded facts, greyed out, with what replaced them;
  - backlinks;
  - related ledger items (as requester or recipient, by repo or area).
- **Process pages** render as an ordered flow: steps, each step's actor, gates, entry points and
  systems.
- **Small neighbourhood graph** (one or two hops around the entity), hand-written SVG layout, no
  dependencies. Never the whole-graph "hairball".
- **Review queue:** the grouped batch below, confirmable and rejectable from the console (local
  data, not outward-facing). Written for a reviewer: each fact in plain words from its predicate's
  sentence (the schema keeps one per predicate; a site predicate may give its own in schema.json as
  `"sentence": "... {target}"`, else it reads "<name>: <target>"), one block per entity with the
  facts that only place it as its subtitle, sources named in words, and what is already known about
  the entity (docs/design/console.md, section 6).

Built (2026-10-21) as described in docs/design/console.md, section 6: `lib/console_knowledge.py`
(the models), `lib/console_kg_graph.py` (the graph's walk and layout) and `lib/console_kb.py` (the
reads and the one write). The cards are People, Process, Product and a fourth, Systems: the
system, service and environment types (which the schema files under Process and Product), kept as
one card by decision of 2026-10-21. Security notes:

- **What the console reads:** entities, facts and the site schema, under the shared lock, through
  `knowledge.load` (the journal's rebuild in memory where the files are behind it, never saved);
  never `identifiers.jsonl` (restricted), `forgotten.jsonl` or `secret.key`. About a person it shows only whitelisted
  predicates and never a summary, with the note "Work-operational facts only. Delete on request:
  bosun kg forget person/<slug>". Entities are addressed by opaque keys, never by id or path.
- **What it writes:** only review decisions on facts waiting in today's batch (the batch `kg
  batch` saved today, or the one it would print now), through `knowledge.opened` and
  `km.review`, as `kg batch apply` does: the same lock, the same supersede-on-confirm rule for
  single-valued predicates, one commit per decision. Each decided fact records `reviewed_by:
  {"kind": "user", "ref": "console", "at": ...}`. Defer writes nothing. Once every fact of the
  saved batch is decided, the console marks `batch.json` answered as `kg batch apply` does.
- **Who can write:** only the console's own page. `POST /api/knowledge/review` needs the session
  cookie (HttpOnly, SameSite=Strict), an Origin naming the console, JSON, and a per-start CSRF
  token in a header that only the page can read; every other console endpoint stays GET-only.
  Like the CLI in MVP 1 (decision 8), the console does not ask for a verified quote: whoever holds
  the console's URL token is taken to be you, as whoever runs `kg confirm` is.

### The Knowledge Graph page: Briefing, Constellation, Raw data (2026-10-21)

Four treatments of the page were prototyped on invented acme data and kept for reference:
[docs/design/prototypes/knowledge-treatments/](prototypes/knowledge-treatments/README.md)
(Constellation, Metro map, Briefing, Radial explorer). Decision: the Briefing is the page's home,
the Constellation is a second tab, and the overview and entity browser above stay as a third, "Raw
data"; the Metro map needs work on realistic data first (ROADMAP.md, "Knowledge Graph: Metro map
treatment") and shows as a disabled tab. Built in `lib/console_kg_views.py` (pure) as described in
docs/design/console.md, section 6. Its measures:

- **Captured:** a fact's capture day is the local day it was first recorded (`recorded_at`;
  `observed_at` moves with each re-observation). The headline counts facts captured per day over
  the last 30 days, whatever their status now, and the running total.
- **Confirmed share:** confirmed facts among the current facts (confirmed or proposed, not closed).
- **Coverage** of a card (People, Process, Product, Systems): the share of its entities that take
  part in at least one current, confirmed fact, as its subject or its object. A step confirmed as
  part of a process counts, so does a repo a confirmed fact points to; an entity with only proposed
  facts does not. It says how much of what we hold you have vouched for, not how complete it is.
- **This week:** the last 7 local days. New facts: current facts captured then. Changes, shown
  before -> after: a fact closed by a replacement then, and a `supersedes` or
  `partially_reversed_by` link between decisions captured then (the older decision's `decided`
  value -> the newer one's).
- **Degree** (most connected, node size): how many distinct entities an entity shares a current
  link with.
- **Rediscovery saved** is not shown as a number: it needs the learning run to count rediscovery
  and rework episodes (Measurement, above), which it does not do yet. The Briefing shows a labelled
  placeholder in its place.

## Privacy

- **People predicates are a whitelist.** Only factual, work-operational predicates: role, team,
  title, timezone, stated working hours, channels they prefer, areas they review or own, and
  preferences they stated about work ("prefers questions as a table"). Anything else about a
  person is refused, and a site's schema cannot add to the list.
- **Evaluative and inferred attributes are out:** no "strengths", no activity patterns inferred
  from message timestamps, no personality, mood, performance, health or personal life. A fact
  with authority `inferred` that names a person is refused; `stated_*` predicates take only what
  the person (or the user) said.
- **Every person fact is written so it could be shown to that person.** A person entity has no
  free-text summary.
- **A forgotten person is kept as a keyed hash** (`secret.key`, never leaves the Mac), so nobody can
  guess who was forgotten; records from before the key (no `hash_kind`) stay guessable, since their ids
  are unknown. If the key is lost for good, move `forgotten.jsonl`, `journal/` (whose deletion
  markers hold the same records) and `replica.json` aside together: those people can then be
  added again (a choice made knowingly) and the next write makes a new key.
- **Machine-local storage**, within the user's account, never in the repo, with the retention and
  delete-on-request rules above. The design should be confirmed with the privacy or legal team
  before any team-shared version.

## Curation without the spam

Review has to be rare, grouped and optional. Nothing about it may interrupt a session or arrive as
a stream of questions.

- **Most facts never reach you.** They are confirmed automatically when they are:
  - your own words, with the quote verified in the session transcript;
  - low-risk facts that name no person, backed by two trusted sources of different kinds (for
    example a ledger record and a report);
  - person facts read from a structured profile source (title, timezone) or recorded from your
    own words.
- **What remains goes to one daily batch.** It is grouped by entity and axis ("Release process: 4
  facts", "Reviewers for repo X: 3 facts"), with one decision per group and per-fact exceptions.
  `kg batch` prints it, and the console shows it as its review queue; nothing puts it in the
  morning summary. Reply in a single
  line, for example "confirm all except 2b": 2b is deferred (it stays proposed for a later batch);
  only an explicit "reject 2b" rejects.
- **The batch is small:** at most 10 groups, 8 facts a group and 40 facts in all
  (`BOSUN_KNOWLEDGE_BATCH_CAP`, `BOSUN_KNOWLEDGE_GROUP_FACTS`,
  `BOSUN_KNOWLEDGE_BATCH_FACTS`). Facts that would have prevented rework come first; the rest
  wait for a later batch.
- **Silence is fine.** Unreviewed proposals stay out of context packs, the console marks them
  unconfirmed, and they expire after 30 days unless something observes them again. Nothing waits
  on you.
- **A seed import of about a hundred facts is spread over several daily batches** (the survey's
  seed: 30 groups over 6 batches of at most 40 facts), every fact a proposal until reviewed.
- **Corrections are cheap.** `kg supersede <id> --value X --from-user --quote ...` closes the fact,
  with your words as its source. A session doing that when you say "That's wrong: X" in any window
  comes with the capture above (MVP 3).

## Decisions (2026-10-21)

1. People attributes are factual only: areas reviewed or owned, stated timezone and working hours,
   stated work preferences. No evaluative notes and no activity inferred from timestamps.
2. The console may confirm and reject knowledge facts directly (local data, not outward-facing),
   through the grouped batch above.
3. Context packs start behind a flag and become the default once measured.
4. The surveyed facts are imported as proposals: `kg import` applies no auto-confirm rule unless
   `--auto-confirm` is given.
5. `knowledge/` lives under a local git repository for history.
6. Batches stay painless: 8 facts a group, 40 a batch, 10 groups; "except" defers, it never
   rejects.
7. `ticket` text stays untrusted, and a site's schema can never add a predicate about a person.
8. Confirming or rejecting from a session (`kg confirm`, `kg reject`, `kg batch apply`) will
   require a verified quote of your words with MVP 3, as `kg propose --from-user` does now; in
   MVP 1 any caller of the CLI can confirm.

## Phasing

| Step | Contents |
|---|---|
| MVP 1 (built) | Schema and storage, `kg` CLI (add-entity, identify, propose, confirm, reject, supersede, lookup, facts, search, neighbors, context, batch, import, lint, stats, schema, forget, expire), seed import as proposed |
| MVP 2 (built) | Console Knowledge view: browser, entity pages, process flows, review queue (confirm, reject, defer), neighbourhood graph |
| MVP 2b (built) | The Knowledge Graph page's tabs: Briefing (headline, this week, review card stack, most connected) and Constellation; Metro map prototyped only |
| MVP 3 | Context packs in delegate briefs (flag), the orchestrator's lookup rule, capture from the user's own words in every window, a verified quote for confirm and reject from sessions |
| Later | Knowledge pass in the learning run, console curation beyond review (supersede, edit aliases), embeddings search, document import, a team-shared variant |

## Implementation (MVP 1)

- **Code:** `lib/knowledge_schema.py` (types, predicates, the whitelist, source kinds, site
  extension; pure), `lib/knowledge_store.py` (files, lock, atomic writes and appends, git),
  `lib/knowledge_model.py` (the fact log folded into facts, resolution, every write operation and
  the auto-confirm rules; pure over a `KB` value, so a dry run is an operation never saved),
  `lib/knowledge_review.py` (batch, decisions, lint), `lib/knowledge_query.py` (lookup, facts,
  search, neighbours, context packs), `lib/knowledge_changes.py` (a write's changes for the
  journal, local where they name a person or hold my words; a knowledge base from before as one genesis
  transaction; the digest `view.json` records; the rebuild, what an outside edit adopts, and where
  the rebuild and the files differ; pure), `lib/knowledge.py` + `libexec/bosun-kg` (the CLI, and
  the write boundary: the journal first, then the files; reads caught up with the journal;
  `kg validate`).
- **Source kinds:** trusted `user` (counts only once the quote is verified), `ledger`, `report`,
  `memory` (a Claude Code memory note), `transcript` (a session on this machine), `profile` (a
  structured directory field); untrusted `slack`, `pr`, `ticket`, `doc`. "Two independent
  sources" means two trusted kinds.
- **The fact log** holds `add` and `update` records; folding them gives each fact and its history.
  Proposing the same fact again (same subject, predicate and object, or value ignoring case and
  spacing) re-observes it: newer `observed_at`, one more observation, any new source, and a
  revived expiry. A rejected fact stays rejected unless your verified words bring it back.
  Confirming a single-valued fact closes the value it replaces; an automatic confirmation never
  does (it waits for review instead).
- **Batch groups:** a fact belongs to its subject's anchor (a step to its process, a decision or
  lesson to what it is `about`, a feature to what it is `part_of` or `lives_in`), or for
  `reviews`, `owns` and `approves` of an area, to that area. Groups of fewer than five facts fold
  into one group per axis ("Process: other facts"); a group is split into parts of at most
  `BOSUN_KNOWLEDGE_GROUP_FACTS` (8, at most 26: one letter each). A batch takes groups in
  priority order (rework-preventing, most observed, largest, oldest) until 10 groups or 40 facts.
  In a decision, facts after `except` and under `skip` stay proposed; later clauses win.
  `kg batch apply` answers the batch last printed (`batch.json`) and says how many facts it left
  proposed.
- **Deletion:** `kg forget` and the retention purge rewrite `facts.jsonl` without the facts, keep
  only a keyed hash of the id and the counts in `forgotten.jsonl`, and restart the git history, since
  old commits would otherwise still hold the data. In the journal they rewrite only `local.jsonl`,
  where everything naming a person is: each of its changes becomes a placeholder that keeps its
  place (sequence number, clock, transaction) and nothing of what it held, so the sequence and the
  ids minted from it never go back. What writes cut short left of `local.jsonl` (its torn tails,
  a rewrite's unfinished copy) is deleted too, whatever it held. The private journal is never
  rewritten, so a deletion that would need it is refused. A forgotten id cannot be added again without
  `--allow-forgotten`. The hash is an HMAC-SHA256 under `secret.key` (made on the first write,
  mode 0600, ignored by the history, never leaves the Mac), so guessing `person/<name>` finds
  nobody; records from before the key (no `hash_kind`) stay plain sha256 and guessable, since
  their ids are unknown and cannot be re-keyed. With keyed records and no readable key, adding a
  person is refused and names the key's path: restore it, a new key would make every record
  unmatchable. `kg forget` is refused the same way, and so is a write that has to finish a
  `kg forget` cut short (until the key is back). If the key is lost for good, move
  `forgotten.jsonl`, `journal/` (whose deletion markers hold the same records) and `replica.json`
  aside together: those people can then be added again (a choice made knowingly), and the next
  write makes a new key.
- **Import** reads JSON or JSON lines (`{"type": "entity", ...}` and `{"type": "fact", ...}`), is
  a dry run unless `--apply`, records everything as proposals (`--auto-confirm`: apply the rules
  above), and reports refusals by reason (People whitelist, schema, unresolved) and how many
  daily batches the proposals take. `tools/survey_to_kg.py` turns
  a survey's markdown tables plus a site mapping file (kept outside the repo) into such a file.
