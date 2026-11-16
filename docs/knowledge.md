# Ontology

`bosun kg` keeps bosun's ontology of your organization, what it should know about it: people
(work facts only), processes and the product, as atomic facts with sources, so a fact stated once
is not lost when the conversation ends (design: [docs/design/knowledge.md](design/knowledge.md)). It
lives in `$BOSUN_CONFIG_DIR/knowledge/`, never in this repo, with its own git history (one
commit per change; `BOSUN_KNOWLEDGE_GIT=0` turns it off, and it is skipped if git is missing). A lock
that a git command cut short left there (the index's, `HEAD`'s or a branch's, older than 30 seconds)
is removed by the next write, with a note on stderr for each, so the history goes on recording.
That git runs none of your own git hooks, templates or file watcher, and nothing of it is still
running when a command returns (its housekeeping runs inside the commit; a git command past 30
seconds is stopped with all it started, and the write warns). A `.git` there that is no repository
at all (no `HEAD`, `objects` or `refs` in it) is deleted by the next write, which starts the history
again and says so in one line; one that git cannot read right now (a typo in your git config, a
newer git's repository format) is left as it is, and each write warns until git reads it again.
A problem with the history never fails a write, which is saved by then: it is a warning.
Every change goes first into a journal there, one transaction per command: `journal/<replica>.jsonl`
for this Mac's changes, and `journal/local.jsonl` for those that never leave this Mac: whatever
names a person, and every fact you stated (one with your quote, `--from-user`, or a `transcript`
source); `view.json` records how far the files reflect it ([Sync and sharing](design/sync.md)).

The files are the journal's rebuild. A write cut short anywhere between the journal and `view.json`
is finished by the next write (a `kg forget` too), and a command that only reads uses the rebuild
without saving it. A knowledge file left with a line that does not parse (a power cut or a full disk
during a write) is read the same way, with a line on stderr naming it, and the next write writes it
again; only with no journal to rebuild it from does it stop every command, naming the line to fix or
remove.

A change made to the files outside the journal (by hand, a `git checkout` or `git revert` of files
in the knowledge folder, or a bosun from before the journal; `view.json` records a digest of each
file, so this is told apart from a write cut short) is adopted by the next write: it goes into the
journal as one transaction named `adopt`, with a line on stderr, and the write goes on from the
journal's rebuild. What the journal cannot take comes back, since the fact log only grows: a record
removed by hand comes back, and one edited in place comes back beside its edited copy, which is
adopted too; an edited `add` record takes the fact over (its status is the edited record's again,
and the updates before it no longer show). Close a fact with `kg supersede` instead. That write says
so in one line, and the knowledge folder's git history keeps the files as they were edited (unless
`BOSUN_KNOWLEDGE_GIT=0`, or the write starts the history again because it deletes: then nothing
keeps them, as that history holds what it deletes, and the line says so).

A person deleted on request is never taken back. One gone from the files whom a forgotten record
matches (an older bosun's `kg forget`, or removed by hand) is forgotten in the journal too: their
changes become placeholders. One found in files restored from before a `kg forget` is left out and
goes from the files; the write says so without naming them, and `kg add-entity <id> --allow-forgotten`
adds them again if they asked.

Every deletion of a person (`kg forget`, `kg expire` when it deletes a person fact, one made outside
the journal) also deletes every backup `bosun update` took (`backups/` in the state folder). A backup
holds the whole knowledge base, `forgotten.jsonl` and the journal included, so restoring one from
before the deletion would bring the person back with no trace of it. The line says how many went
("kg: deleted 2 update backups taken before this; going back past it is no longer possible"), never
whom. A deletion cut short deletes them when the next write finishes it.

If a write that deletes the git history (it holds what a person deleted on request named) cannot
delete all of `.git`, it stops there and names it: delete it, and the next write finishes. While
`secret.key` is missing over keyed records, no person is taken in from changed files at all, as
adding one is refused then; the write says why, and they go from the files, so restore those again
once the key is back. A write that has to finish a `kg forget` cut short while that key is missing
is refused whole, naming the key, and the first write once it is back finishes the forget.

A command that only reads shows restored files as they are until the next write, since reads never
write; and person facts an older bosun's retention purge took out come back from the journal until
the next `kg expire` purges them again. Checking out an older commit of the whole folder takes the
journal back with it, and what came after is gone
([Sync and sharing](design/sync.md#open-questions)). Going back to the release before the journal
is not supported: once a fact has a minted id (`f-3k9x2a`), that release's `kg batch` and `kg
lookup` stop on it ([Sync and sharing](design/sync.md#decisions-2026-11-04)). How cut writes,
outside edits, who is forgotten and the history restarts work, and the rare double faults accepted,
is in [Sync and sharing](design/sync.md#knowledge-changes).

`kg validate` compares the files with the journal (`--json` for scripts; exit 1 on any finding), and
`bosun doctor` runs it: damage, a position that does not match or a rebuild that differs fails,
since writes are refused or data is at risk; a conflict, an update waiting for its fact, an outside
edit the next write adopts or a file that does not parse, which the next write writes again, warns.

If a command says the knowledge base's journal is damaged, or behind `view.json` (a journal file
deleted, a partial restore), nothing was written, and the files are still whole. To go on from
them, move `knowledge/journal/` aside (keep it): the next write starts a new journal from the
files, as a new replica, so that no fact id is minted twice. The state folder's `replica.json` may
stay: a journal that starts again (moved aside, emptied or removed) always gets a new id, made by
that write in place of the old one. `replica.json` is this Mac's id in the ledger's journal too, so
this gives the Mac a new id in both journals, which both carry on under; the ledger's old replica
file stays in its journal ([Configuration](configuration.md#where-bosun-keeps-things)). A write cut short as it starts the new
journal is finished by the next write, as any other.

```sh
bosun kg add-entity process/release --name "Release process" --alias releases
bosun kg add-entity gate/canary-checks --name "Canary checks"
bosun kg propose releases has_step canary-checks --order 3 --source ledger:mypr:acme/api#7 --source report:task:2026-10-01/1
bosun kg propose releases rule --value "a deploy rolls out to production gradually once the canary checks pass" \
  --from-user --quote "it rolls out gradually once the canary checks pass" --transcript <session.jsonl>
bosun kg batch                          # the day's proposals, grouped and numbered
bosun kg batch apply 'confirm all except 2b'
bosun kg context --for releases         # a small cited pack of confirmed facts
```

- **Entities** are `<type>/<slug>` on three axes: people (`person`, `team`), process (`process`,
  `step`, `gate`, `channel`, `system`) and product (`repo`, `service`, `environment`, `feature`,
  `decision`, `principle`, `lesson`). A name or strong alias works anywhere an id does; weak
  aliases are related terms for search. Near-identical names are reported as merge suggestions.
- **Facts** use a closed set of predicates (`bosun kg schema`); a site adds its own in
  `knowledge/schema.json`. About a person only work facts are kept (role, team, title, timezone,
  stated working hours, preferred channels, what they review, own or approve, stated work
  preferences); anything else is refused, as is anything inferred about a person. A new fact's id
  is `f-` and six characters (`f-3k9x2a`), minted from the journal so that two Macs never make the
  same one; facts from before keep theirs (`f12`), and both work wherever a fact id is asked for.
- **Confirmation without the spam:** a fact is confirmed at once when the quote of your words is
  in a prompt you typed in that session's transcript (tool results and pasted text never count),
  when two trusted sources of different kinds back a low-risk fact that names no person, or when a
  profile source gives a title or timezone. Slack, PR, ticket and document text never confirms
  anything. The rest waits for `kg batch`: one list, grouped by entity and axis, rework-preventing
  groups first, at most 10 groups, 8 facts a group and 40 facts a day
  (`BOSUN_KNOWLEDGE_BATCH_CAP`, `_GROUP_FACTS`, `_BATCH_FACTS`), answered in one line;
  "confirm all except 2b" leaves 2b proposed, only "reject 2b" rejects it. The same batch can
  be answered in the [console](console.md#console)'s review page (`#/knowledge/review`).
  Unreviewed proposals expire after 30 days unless something observes them again.
- **Two timelines:** facts are never edited or deleted when they change: `kg supersede` closes
  one (`valid_to`, `superseded_by`), and `kg facts <entity> --as-of <date>` shows what held then.
- **Maintenance:** `kg lint` reports contradictions, stale facts (`BOSUN_KNOWLEDGE_STALE_DAYS`),
  orphans, alias collisions and expired proposals; `kg validate` checks the files against the
  journal they are the rebuild of (above); person facts not re-observed for 12 months
  are deleted by `kg expire`, and `kg forget person/<slug>` deletes a person on request (with the
  git history, which restarts, and their changes in `journal/local.jsonl`, each left as a
  placeholder that keeps its place in the sequence and nothing of what it held; what writes cut
  short left of that file, `local.jsonl.torn-*`, goes too, and so does what they left in the
  knowledge folder, `*.tmp`, such as a `kg batch`'s `batch.json.tmp`; the history never holds a torn
  file). What it keeps is a keyed hash of the id, under `secret.key` in the
  knowledge folder (made on the first write, readable only by you, not in the history, never
  leaving this Mac). Once such records exist, `kg forget` and adding a person need that key; if
  it is missing the command refuses and names its path, so restore it from a backup. If it is
  gone for good, move `forgotten.jsonl` and `knowledge/journal/` aside, and `secret.key`, if it is
  there, all together (the journal's deletion markers hold the same records, and the next write
  starts a new journal from the files, as a new replica, as for a damaged one; the state folder's
  `replica.json` may stay): the people in them can then be added again (your choice, knowingly),
  and the next write makes a new key. A `secret.key` that is not a key (empty, the wrong length, unreadable)
  while no such record needs it is moved aside by the next write, kept as
  `secret.key.aside-<time>` (not in the history), and made again, with a line on stderr.
- **Who and where in one lookup:** `kg lookup "<name>"` lists what links to an entity, and says where
  each person or team that reviews, owns or approves it prefers to be asked, on their `reviews` line
  (else their first `owns` or `approves` line): their own confirmed `prefers_channel` facts with their
  ids (`f-0ahnvb Marco Placeholder reviews pdf renderer [...]; prefers to be asked in #acme-prs
  [f-dm2y00]`), never their team's. A lookup of a person lists their own preference among their facts,
  as before.
- **Search by words:** `kg search` matches the whole text first, as one phrase, then by its words:
  each of three characters or more and not one of a few such as "who", "should" or "the", at the
  start of a word (so "review" finds "reviews" and "reviewer", and "renderer?" finds the renderer), in
  the same names, aliases, related terms, summaries and fact values, but never an id's type
  (`person/`). A word in a name, a strong alias or the id counts most, and more when the name's words
  come in a row ("pdf renderer"); a word only in a related term, the summary or a fact counts less. Every
  word match comes after the whole-text matches, and the reason says which words matched. So "Who
  should review a change to the pdf renderer, and where do I ask them?" finds `repo/pdf-renderer`
  first. A context pack's topic, and the console's search, still match the whole text only, so a pack
  never pulls in what is only loosely related.
- **Bounded reads:** `kg lookup` prints at most 800 tokens (`--max-tokens N`; the rest is
  `(N more: kg lookup <entity> --max-tokens 3200)`), where to ask included, and five waiting facts,
  then a count; `kg search` shows 10 hits (`--limit`); `--json` is complete. `bosun delegate` logs
  each pack's size, one line in `delegate-<date>.log` of the log folder.
- **Context packs:** `kg context --for <entity|repo|topic> [--for ...] [--max-tokens N]` prints at
  most about 1.5k tokens of confirmed, current facts, each citing its `[id]` and source, with one
  section per target and one budget for all. A repo can be named as the ledger does
  (`acme/invoice-exporter`), by id, name or alias. A pack names who reviews or owns the work and
  leaves a person's own profile (timezone, hours, preferences, title) out unless the person is the
  target, which only naming them makes them: a topic's search leaves people out, even one whose fact
  matches it; identifiers and unconfirmed facts never appear. With `BOSUN_KNOWLEDGE_ENABLED=1` in
  `bosun.env` (default `0`), `bosun delegate` adds the pack for the task's repos, its area and
  its workstream's area to the brief, after the task's rules, under "What bosun knows about this
  work", and adds nothing when the knowledge base holds no confirmed fact about them. A task moved
  to another window does not get it again. A knowledge base that cannot be read costs the brief one
  line saying so, never the hand-over.
- **Seeding:** `kg import <file>` reads entities and facts as JSON lines, records every fact as a
  proposal (`--auto-confirm` applies the rules above), is a dry run unless `--apply`, and says how
  many daily batches the review takes; `tools/survey_to_kg.py` converts a survey's markdown tables, with a mapping file kept
  next to the survey, into such a file. `bosun doctor` shows the counts and runs `kg validate`.

## In the agents' hands

One key, `BOSUN_KNOWLEDGE_ENABLED` (default `0`, so briefs and system prompts stay as they were until you set
it; `bosun init` recommends `1`), switches on the packs and the rules below. The check of your words is not part
of it: it is always on.

- **Packs in briefs.** `bosun delegate` adds "What bosun knows about this work": the pack for the task's repo and
  area (at most 1500 tokens in all, its first line included). A repo is found by its slug, alias or last path
  segment. It never fails a hand-over, and each pack's size is logged to `delegate-<date>.log` in
  `$BOSUN_LOG_DIR`.
- **Look before asking.** The orchestrator's system prompt, and every Claude repo window's, end with
  `orchestrator/knowledge.md` (about 300 tokens): look a fact up before asking you (one lookup of a name says who
  reviews, owns or approves it and where to ask each), record what you state as standing in your exact words,
  supersede it when you correct it, confirm or reject a waiting fact only when you name it, and say in one line
  what was recorded or why it stays proposed. Codex and Antigravity windows get
  packs in their briefs but not these rules: bosun cannot check their transcripts, so a fact from them would
  never be confirmed.

**Words that count** (always on, whatever the key says). Inside an agent session, `--from-user --quote` needs no
`--transcript`: `kg` finds the session that ran it by walking up its process tree to an agent's session record. A
quote is checked against that session only (another session's transcript is refused), and only genuine prompts of
the last 24 hours count, from a terminal with `--transcript` too. Text bosun pasted into the window (a brief, a
`bosun send`) is never your words. `kg confirm`, `reject`, `batch apply`, and `supersede --close` or `--by`, run
from a session, need `--from-user --quote` that verifies, at least 8 characters, typed after the facts they decide
on were proposed; for `batch apply` the quote holds each group number the decision names (or "all", when it says
all). The quote is kept in the decision's note, on this Mac only. A correction with `--value` or `--object` needs a
source: your words, checked the same way, or a `--source`. From a terminal, and in the console's review, they work
as before.

`kg lookup` prints at most 800 tokens (`--max-tokens`), five waiting facts and a count, and says how to see
more; `kg search` shows 10 hits; `--json` is complete. Your own profile facts and identifiers never reach a
pack. The check of your words guards against a session's mistakes, not against a hostile agent, which can
escape the process-tree walk.
