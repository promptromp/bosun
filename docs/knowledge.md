# Ontology

The ontology is what bosun knows about your organization: its people (work facts only), its processes and its
product, kept as small facts, each with its source. A fact stated once is not lost when the conversation ends: the
agents look things up before asking you, and the tasks they are handed carry what is known about the work. You
decide what goes in, in a short daily review. Design: [design/knowledge.md](design/knowledge.md).

It lives in your config folder, in `knowledge/`, never in bosun's repository. `bosun kg` reads and writes it, and
the [console](console.md)'s Ontology view shows it.

## How facts get in

Facts come from three places:

- **Your own words.** Tell an agent "the release rolls out gradually once the canary checks pass", and it records
  that as a standing fact in your words. A fact is confirmed at once when your quote is in a prompt you typed in that
  session ([Words that count](#words-that-count)).
- **Sources bosun has seen:** trusted ones (the ledger, a repo window's or learning run's report, a memory note, a
  session's transcript, a profile field) and untrusted ones (Slack, pull requests, tickets, documents). These become
  proposals. Two trusted sources of different kinds confirm a low-risk fact that names no person, and a profile
  source confirms a title or a timezone; the rest waits for your review.
- **An import** of what your team already wrote down ([Seeding it](#seeding-it)).

Text from Slack, a pull request, a ticket or a document never confirms anything on its own.

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

## The daily review

`bosun kg batch` lists what waits for you as one batch: grouped by entity and axis, the groups that prevent rework
first, at most 10 groups, 8 facts a group and 40 facts a day (`BOSUN_KNOWLEDGE_BATCH_CAP`,
`BOSUN_KNOWLEDGE_GROUP_FACTS`, `BOSUN_KNOWLEDGE_BATCH_FACTS`). You answer it in one line: "confirm all except 2b"
leaves 2b proposed, only "reject 2b" rejects it, and "confirm 1-3" takes a range. The same batch can be answered in
the console's review page (`#/knowledge/review`). A proposal nobody reviews expires after 30 days, unless something
observes it again.

## In the agents' hands

One key, `BOSUN_KNOWLEDGE_ENABLED`, switches on the packs and the rules below. It is `0` in bosun's own defaults, so
briefs and system prompts stay as they were until you set it; `bosun init` recommends `1`. The check of your words is
not part of it: that is always on.

- **Packs in briefs.** `bosun delegate` adds "What bosun knows about this work" to a task, after its rules: the pack
  for the task's repos, its area and its workstream's area (at most 1500 tokens in all, its first line included), and
  nothing when the ontology holds no confirmed fact about them. A repo is found by its slug, alias or last path
  segment. A task moved to another window does not get it again. It never fails a hand-over: an ontology that cannot
  be read costs the brief one line saying so. Each pack's size is logged to `delegate-<date>.log` in the log folder.
- **Look before asking.** The orchestrator's system prompt, and every Claude repo window's, end with
  `orchestrator/knowledge.md` (about 300 tokens): look a fact up before asking you (one lookup of a name says who
  reviews, owns or approves it and where to ask each), record what you state as standing in your exact words,
  supersede it when you correct it, confirm or reject a waiting fact only when you name it, and say in one line what
  was recorded or why it stays proposed. Codex and Antigravity windows get packs in their briefs but not these rules:
  bosun cannot check their transcripts, so a fact from them would never be confirmed.

### Words that count

Always on, whatever the key says:

- **A quote is checked against the session that ran `kg`.** Inside an agent session, `--from-user --quote` needs no
  `--transcript`: `kg` finds its session by walking up its process tree to an agent's session record. Another
  session's transcript is refused, and only genuine prompts of the last 24 hours count (from a terminal with
  `--transcript` too). Text bosun pasted into the window (a brief, a `bosun send`) is never your words.
- **Decisions need your words too.** `kg confirm`, `reject`, `batch apply`, and `supersede --close` or `--by`, run
  from a session, need a `--from-user --quote` that verifies, at least 8 characters, typed after the facts they decide
  on were proposed; for `batch apply` the quote holds each group number the decision names (a range such as 1-3 holds each in it; or
  "all", when it says all). A correction with `--value` or `--object` needs a source: your words, checked the same way, or a `--source`.
- **From a terminal, and in the console's review,** they work as before.

The quote is kept in the decision's note, on this Mac only. The check guards against a session's mistakes, not
against a hostile agent, which can escape the process-tree walk.

## Looking things up

| Command | What it gives |
|---|---|
| `kg lookup "<name>"` | One entity: its facts, what links to it, and where each person or team that reviews, owns or approves it prefers to be asked |
| `kg search "<text>"` | The entities a question or a phrase is about, best first |
| `kg context --for <entity\|repo\|topic>` | A small pack of confirmed, current facts, each citing its id and source: what a task's brief carries |
| `kg facts <entity> --as-of <date>` | What held on a date |
| `kg neighbors <entity>` | What lies one link away |

- **Who and where in one lookup.** `kg lookup` says where each person or team that reviews, owns or approves an
  entity prefers to be asked, on their `reviews` line (else their first `owns` or `approves` line): their own
  confirmed `prefers_channel` facts with their ids (`f-0ahnvb Marco Placeholder reviews pdf renderer [...]; prefers
  to be asked in #acme-prs [f-dm2y00]`), never their team's. A lookup of a person lists their own preference among
  their facts.
- **Search by words.** `kg search` matches the whole text first, as one phrase, then by its words, so a whole
  question works: "Who should review a change to the pdf renderer, and where do I ask them?" finds
  `repo/pdf-renderer` first. Each hit says why it matched.
- **Packs leave people out unless named.** A pack names who reviews or owns the work, but leaves a person's own
  profile (timezone, hours, preferences, title) out unless the person is the target, which only naming them makes
  them: a topic's search leaves people out, even one whose fact matches it. Identifiers and unconfirmed facts never
  appear.

<details class="note" markdown>
<summary>How search ranks words, and how much each read prints</summary>

- **Words.** After the whole-text matches, `kg search` takes each word of three characters or more that is not one of
  a few such as "who", "should" or "the", at the start of a word (so "review" finds "reviews" and "reviewer", and
  "renderer?" finds the renderer), in the same names, aliases, related terms, summaries and fact values, but never an
  id's type (`person/`). A word in a name, a strong alias or the id counts most, and more when the name's words come
  in a row ("pdf renderer"); a word only in a related term, the summary or a fact counts less. Every word match comes
  after the whole-text matches, and the reason says which words matched. A context pack's topic, and the console's
  search, still match the whole text only, so a pack never pulls in what is only loosely related.
- **Bounded reads.** `kg lookup` prints at most 800 tokens (`--max-tokens N`; the rest is `(N more: kg lookup <entity>
  --max-tokens 3200)`), where to ask included, and five waiting facts, then a count; `kg search` shows 10 hits
  (`--limit`); `--json` is complete.
- **Packs.** `kg context --for <entity|repo|topic> [--for ...] [--max-tokens N]` prints at most about 1.5k tokens,
  with one section per target and one budget for all. A repo can be named as the ledger does
  (`acme/invoice-exporter`), by id, name or alias.

</details>

## What goes in: entities and facts

- **Entities** are `<type>/<slug>` on three axes: people (`person`, `team`), process (`process`, `step`, `gate`,
  `channel`, `system`) and product (`repo`, `service`, `environment`, `feature`, `decision`, `principle`, `lesson`).
  A name or strong alias works anywhere an id does; weak aliases are related terms for search. Near-identical names
  are reported as merge suggestions.
- **Facts** use a closed set of predicates (`bosun kg schema`); a site adds its own in `knowledge/schema.json`. A new
  fact's id is `f-` and six characters (`f-3k9x2a`), made so that two Macs never make the same one; facts from before
  keep theirs (`f12`), and both work wherever a fact id is asked for.
- **About a person, only work facts:** role, team, title, timezone, stated working hours, preferred channels, what
  they review, own or approve, and stated work preferences. Anything else is refused, and so is anything inferred
  about a person.
- **Two timelines.** Facts are never edited or deleted when they change: `kg supersede` closes one (`valid_to`,
  `superseded_by`), and `kg facts <entity> --as-of <date>` shows what held then.

## Seeding it

`kg import <file>` reads entities and facts as JSON lines and records every fact as a proposal (`--auto-confirm`
applies the confirmation rules above). It is a dry run unless `--apply`, and says how many daily batches the review
will take. `tools/survey_to_kg.py` turns a survey's Markdown tables, with a mapping file kept next to the survey, into
such a file.

## Keeping it healthy

- **`kg lint`** reports contradictions, stale facts (older than `BOSUN_KNOWLEDGE_STALE_DAYS`, 180), orphans, alias
  collisions and expired proposals.
- **`kg validate`** checks the files against the journal they are rebuilt from (`--json` for scripts; exit 1 on any
  finding). `bosun doctor` runs it and shows the counts: damage, a position that does not match or a rebuild that
  differs fails, since writes are refused or data is at risk; a conflict, an update waiting for its fact, an outside
  edit the next write adopts or a file that does not parse, which the next write writes again, warns.
- **`kg expire`** deletes person facts nobody observed again for 12 months.
- **History.** Every change is a commit in the knowledge folder's own git history (`BOSUN_KNOWLEDGE_GIT=0` turns it
  off; it is skipped without git). A problem with that history never fails a write: it is a warning.

## Deleting a person

`kg forget person/<slug>` deletes a person on request: their facts, their changes in the journal (each left as an
empty placeholder that keeps its place), and the knowledge folder's git history, which starts again. It also deletes
every backup `bosun update` took, since restoring one from before would bring the person back, and says how many went
("kg: deleted 2 update backups taken before this; going back past it is no longer possible"), never whom. A person
deleted on request is never taken back: one found again in files restored from before is left out, and the write
says so without naming them; `kg add-entity <id> --allow-forgotten` adds them again if they asked.

It does not reach their name where it is free text elsewhere (another entity's summary or aliases, a fact's value
or note, a quote): it lists those places as "still named in" (`--dry-run` too). An edit does not take the name out of
them for good: the shared journal and the history keep every earlier text, and a fact's text can only be superseded,
which keeps the old fact.

What it keeps of them is a keyed hash of the id, under `secret.key` in the knowledge folder (made on the first write,
readable only by you, not in the history, and never leaving this Mac). Once such records exist, `kg forget` and adding
a person need that key: if it is missing, the command refuses and names its path, so restore it from a backup.

<details class="note" markdown>
<summary>If <code>secret.key</code> is gone, or not a key</summary>

If the key is gone for good, move `forgotten.jsonl` and `knowledge/journal/` aside, and `secret.key` if it is there,
all together (the journal's deletion markers hold the same records, and the next write starts a new journal from the
files, as a new replica, as for a damaged one; the state folder's `replica.json` may stay). The people in them can
then be added again (your choice, knowingly), and the next write makes a new key.

A `secret.key` that is not a key (empty, the wrong length, unreadable) while no such record needs it is moved aside
by the next write, kept as `secret.key.aside-<time>` (not in the history), and made again, with a line on stderr.
While `secret.key` is missing over keyed records, no person is taken in from changed files at all, as adding one is
refused then; the write says why, and they go from the files, so restore those again once the key is back. A write
that has to finish a `kg forget` cut short while that key is missing is refused whole, naming the key, and the first
write once it is back finishes the forget.

`kg forget` also deletes what writes cut short left of the journal's local file (`local.jsonl.torn-*`) and in the
knowledge folder (`*.tmp`, such as a `kg batch`'s `batch.json.tmp`); the history never holds a torn file. Every
deletion of a person (`kg forget`, `kg expire` when it deletes a person fact, one made outside the journal) deletes
the update backups, and a deletion cut short deletes them when the next write finishes it. If a write that deletes the
git history cannot delete all of `.git`, it stops there and names it: delete it, and the next write finishes.

</details>

## Reference: how it is stored

Every change goes first into a journal in the knowledge folder, one transaction per command:
`journal/<replica>.jsonl` for this Mac's changes, and `journal/local.jsonl` for those that never leave this Mac
(whatever names a person, and every fact you stated: one with your quote, or a `transcript` source). The files are
the journal's rebuild, and `view.json` records how far they reflect it ([Sync and sharing](design/sync.md)).

<details class="note" markdown>
<summary>Writes cut short, outside edits, and the git history</summary>

- **A write cut short** anywhere between the journal and `view.json` is finished by the next write (a `kg forget`
  too), and a command that only reads uses the rebuild without saving it. A knowledge file left with a line that does
  not parse (a power cut or a full disk during a write) is read the same way, with a line on stderr naming it, and the
  next write writes it again; only with no journal to rebuild it from does it stop every command, naming the line to
  fix or remove.
- **A change made to the files outside the journal** (by hand, a `git checkout` or `git revert` of files in the
  knowledge folder, or a bosun from before the journal; `view.json` records a digest of each file, so this is told
  apart from a write cut short) is adopted by the next write: it goes into the journal as one transaction named
  `adopt`, with a line on stderr, and the write goes on from the journal's rebuild. What the journal cannot take comes
  back, since the fact log only grows: a record removed by hand comes back, and one edited in place comes back beside
  its edited copy, which is adopted too; an edited `add` record takes the fact over (its status is the edited
  record's again, and the updates before it no longer show). Close a fact with `kg supersede` instead. That write says
  so in one line, and the knowledge folder's git history keeps the files as they were edited (unless
  `BOSUN_KNOWLEDGE_GIT=0`, or the write starts the history again because it deletes: then nothing keeps them, as that
  history holds what it deletes, and the line says so).
- **A person gone from the files** whom a forgotten record matches (an older bosun's `kg forget`, or removed by hand)
  is forgotten in the journal too: their changes become placeholders.
- **Restored files** are shown as they are by a command that only reads, until the next write, since reads never
  write; and person facts an older bosun's retention purge took out come back from the journal until the next `kg
  expire` purges them again. Checking out an older commit of the whole folder takes the journal back with it, and
  what came after is gone ([Sync and sharing](design/sync.md#open-questions)).
- **The git history.** That git runs none of your own git hooks, templates or file watcher, and nothing of it is still
  running when a command returns (its housekeeping runs inside the commit; a git command past 30 seconds is stopped
  with all it started, and the write warns). A lock that a git command cut short left there (the index's, `HEAD`'s or
  a branch's, older than 30 seconds) is removed by the next write, with a note on stderr for each. A `.git` there that
  is no repository at all (no `HEAD`, `objects` or `refs` in it) is deleted by the next write, which starts the
  history again and says so in one line; one that git cannot read right now (a typo in your git config, a newer git's
  repository format) is left as it is, and each write warns until git reads it again.
- **Going back to the release before the journal** is not supported: once a fact has a minted id (`f-3k9x2a`), that
  release's `kg batch` and `kg lookup` stop on it ([Sync and sharing](design/sync.md#decisions-2026-11-04)).

How cut writes, outside edits, who is forgotten and the history restarts work, and the rare double faults accepted,
is in [Sync and sharing](design/sync.md#knowledge-changes).

</details>

<details class="note" markdown>
<summary>If the journal is damaged</summary>

If a command says the ontology's journal is damaged, or behind `view.json` (a journal file deleted, a partial
restore), nothing was written, and the files are still whole. To go on from them, move `knowledge/journal/` aside
(keep it): the next write starts a new journal from the files, as a new replica, so that no fact id is made twice.
The state folder's `replica.json` may stay: a journal that starts again (moved aside, emptied or removed) always gets
a new id, made by that write in place of the old one. `replica.json` is this Mac's id in the ledger's journal too, so
this gives the Mac a new id in both journals, which both carry on under; the ledger's old replica file stays in its
journal ([Where bosun keeps things](configuration.md#where-bosun-keeps-things)). A write cut short as it starts the
new journal is finished by the next write, as any other.

</details>
