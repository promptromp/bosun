# Design note: work-item lineage and external trackers

Status: phases 1-2 built (2026-10-16), folding (2026-10-20) and workstreams (phase 4,
2026-10-21) built; from phase 3, proposals with the exact tracker call are built with approvals. Tracked in
[ROADMAP.md](../../ROADMAP.md#3-ledger-as-the-protocol-between-sessions).

## Why

The ledger is a task queue organized by where work comes from: review requests, tech designs, my
PRs, mentions, outreach, learning proposals. Each kind has its own state machine, delegations,
reports and proposals. Two gaps showed up in real use:

- **Work with no artifact of its own had no kind.** A colleague answers my outreach with "yes
  please fix that too", I say "go ahead and do it", a repo session finds a follow-up, learning
  finds something to change. Such work was recorded as a `mention` it never was, delegated under
  an outreach id, or not recorded at all; work started from my direct instruction had no ledger
  id.
- **Items could not point at each other.** Provenance lived in prose: an outreach `parent` field,
  a free-form `--field origin=...` on a PR, "merged into <id>" notes. External tickets (Jira)
  were not in the ledger at all, so a ticket and the PR it tracks drifted apart unnoticed.

A typical chain (fictional): my PR on acme's exporter -> outreach to Marco offering to fix the
same bug in the other callers -> Marco replies "yes please" -> that becomes a task, delegated as
new work -> the task produces a new PR; meanwhile a Jira ticket, EXP-123, tracks the original PR.
A variant: the repo session opens the new PR at my direct request, before the orchestrator's
proposal to delegate it (under the outreach id) is approved; without a task kind, the orchestrator
could only close the outreach with a note and withdraw the proposal, and the new PR had no ledger
item at all.

## What exists

| Concept | Before | Now |
|---|---|---|
| Asked of me | `review:`, `td:`, `slack:` (mention) | unchanged |
| My own artifacts | `mypr:` | unchanged |
| Waiting on others | `out:` with a `parent` field | unchanged; `parent` also reads as `blocked_by` |
| Work we decided to do | a mention or outreach id, or nothing | `task:<YYYY-MM-DD>/<n>` |
| Changes to bosun | `learn:` | unchanged (a finding with work behind it can spawn a task) |
| External trackers | none | `ticket` (`jira:EXP-123`, `ghissue:acme/exporter#7`) |
| Provenance, duplicates | notes, `origin` field, "merged into" | typed links |

A mention stays what it is: a message that may need my attention. When it asks us to do
something, the work is a task spawned from it, so the mention can be answered and closed while the
work goes on under its own id.

## Shape

**Tasks.** Fields: title, ask, requester, repo or area (optional), done_when (optional); `via` on
a task with no source item. States: `proposed` -> `accepted` -> `delegated` -> `done`; `declined`
(from proposed, and back to proposed if reconsidered); `dropped` from anywhere. Rules in code:

- `accepted` needs `--evidence 'user: <what I said>'`: someone else's message never accepts work.
  Entering it carries out the pending proposal (`proposal-done`); `declined` rejects it.
- `bosun delegate` refuses a task that is not accepted, and refuses an outreach id as the work
  item (the reply's work is a task; a reply for the window holding the parent goes through
  `delegate --update <out-id>`). Recording the delegation moves an accepted task to `delegated`.
- A report ends the hand-over as for every kind (`work_done` is empty). The task stays
  `delegated` while its PR is reviewed; `done` once what it produced landed (`ledger tree` says
  so) or, with nothing produced, once the report answered the ask.

**Links.** Stored once, on the item they start from: `links: [{rel, id, via?, at}]`. The inverse
is computed on read (`ledger show` under `derived`, `tree`, `list`).

| Relation | Inverse | Meaning | Cycles |
|---|---|---|---|
| `spawned_from` | `spawned` | provenance; `via`: the message that asked, or `user: <what I said>` | refused (with `produces`) |
| `produces` | `produced_by` | a task's PR, or other artifact | refused (with `spawned_from`) |
| `blocked_by` | `blocks` | not finished while the other is open | refused |
| `duplicate_of` | `duplicates` | the same item twice; the other carries it on | refused (with `folded_into`) |
| `folded_into` | `absorbs` | its work goes on as part of the other (e.g. in that task's PR) | refused (with `duplicate_of`) |
| `tracks` | `tracked_by` | the external ticket for this item | n/a (tickets link nowhere) |
| `part_of` | `has_part` | the item's one workstream; a workstream's parent workstream ([workstreams.md](workstreams.md)) | refused; one per item, two levels of workstreams |
| `relates_to` | `relates_to` | loosely related | allowed |

`part_of` is not lineage. It is how a workstream spreads along lineage: only the root is tagged,
and what was spawned from it, produced by it, sent as its outreach or folded into it inherits the
workstream on read (see [workstreams.md](workstreams.md#resolution-computed-on-read-never-copied)).
`prune` does not keep an item because it belongs to an open workstream, and never prunes a
workstream.

Links refuse unknown ids (a `tracks` target that is a valid ticket id is created, unsynced),
self-links, and cycles within a family. An outreach under its parent counts in the lineage family
too, because the tree shows it there.

**Outreach `parent`.** Kept as a field and read as a computed `blocked_by`, rather than migrated
to a stored link. It is the least risky path: the field also scopes the outreach id
(`out:<parent>/<n>`), and `delegate --update <out-id>`, the open-outreach guard, the learning
checks and the corpus fixtures all read it. Migrating would touch each of them for no change in
behaviour. `unlink` refuses it and says how outreach stops blocking (applied or dropped).

**The blocker guard.** The open-outreach guard now also counts open items the item is stored
`blocked_by`, with the same exemptions: a fact (a PR merged or closed) goes through, and
`--force-open-outreach` (alias `--force-blocked`) overrides with a note. A spawned task blocks its
source only with `spawn --blocks`.

**Commands.** `ledger spawn [<from-id>] --title --ask [--via] [--requester] [--repo] [--area]
[--done-when] [--blocks]` (an exact repeat is unchanged; a similar open task from the same source
is a warning), `link`, `unlink`, `tree`, `migrate-links [--dry-run]`, `ticket-sync`, `tickets
[--all] [--mismatch]`. `list` shows `from <id>`, `-> <id>` and `tracks <id>`; `reports` prints the
commands that record and link a PR a report names; dropping an item with "merged into <id>" or
"folded into <id>" records `folded_into`, with "duplicate of <id>" `duplicate_of`; closing a
replied outreach that no task carries, while no window holds its parent, prints the spawn command;
`prune` keeps finished items linked to open ones.

**Folding.** "Merged into" used to record `duplicate_of`, which lost the difference between the
same item recorded twice and an item whose work went on inside another (a small task done in
another task's PR). `ledger fold <id> --into <other> [--note]` records the second for posterity:
`folded_into`, then closes `<id>` in its kind's escape state (`dropped`; `dismissed` for kinds
without one), since the item did not finish on its own and the link, not the state, says where
the work went. A pending proposal on `<id>` is withdrawn (not `done`: nothing was carried out on
the item; not `rejected`: I did not say no), which is also what any terminal state does to it;
the fold names it. The open-outreach and blocker guards apply as for `transition`; a finished
target is refused unless `--force-closed-target` (the work already landed with it); learning
proposals and tickets close their own way. If the target is with a window, `fold` prints the
`delegate --update` that carries the folded item's title and ask there, and does not send it.
Repeating it is unchanged. `migrate-links` turns a `duplicate_of` recorded from a "merged into"
note (its `linked` entry, and the drop note) into `folded_into`.

## External trackers

Two ways to bring a tracker in:

- **(a) a field on the work item** (`--field jira=EXP-123`): cheap, but a ticket shared by two
  PRs is two copies, its state has nowhere to live, and nothing can compare it with our work.
- **(b) a ticket item, linked with `tracks`**: one item per ticket, holding what the tracker
  shows, so the ledger can say where the ticket and our work disagree.

We chose (b). A `ticket` item mirrors the tracker read-only: id `<system>:<key>`, fields system,
key, project, url, title, `external_status` (raw), `status_category`, `synced_at`, assignee. Its
state is the category: `unsynced`, then `todo`, `in-progress`, `in-review`, `done` or
`cancelled` (the last two are closed). `transition` refuses tickets; only `ledger ticket-sync`
changes them, from what the orchestrator read (the CLI cannot call MCP tools). It records history
only on a change and stamps `synced_at` either way.

**Backends** (`BACKENDS` in `lib/tickets.py`, like `CHANNELS` for outreach): the key format, how
the orchestrator reads it (Jira: the site's read tool, e.g. an Atlassian MCP get-issue tool;
GitHub issues: `gh issue view`), how an approved change is made, the tracker's own status
categories (Jira's statusCategory) and built-in status names. `linear:` and others are a row
each.

**Status mapping.** In order: the site's overrides, per project then per system, from
`BOSUN_TICKET_STATUS_MAP` in `bosun.env` (`jira/EXP:Ready for Review=in-review; jira:On Hold=todo`;
one key fits the existing config convention, and `ledger.py` has no YAML parser); the built-in
status names; `--category` (ours, or the tracker's own); otherwise `ticket-sync` refuses and says
which of the three to use.

**Mismatch hints** (derived, never acted on): the work that tracks a ticket, and what that work
produced, against the ticket's category:

- ticket done or cancelled while that work is open: propose closing the work (or asking whether
  the ticket closed by mistake);
- all that work closed and some of it landed (PR merged, task done) while the ticket is open:
  propose transitioning the ticket to done;
- a PR in review, or a task in progress, while the ticket is `todo`: propose moving it on;
- never synced, or synced longer ago than `BOSUN_TICKET_STALE_HOURS` while the work is open.

They show in `ledger tree`, `show`, `link` and `ticket-sync` output, and in `ledger tickets
--mismatch`; learning sees the first three. A ticket in a project another team owns (not in
`BOSUN_TICKET_OWNED_PROJECTS`, when that is set) gets an FYI line instead of a proposal: not
ours to move.

## Guardrails

- Only my word accepts a task (`user:` evidence, enforced). A colleague's request becomes a
  proposed task and a tier-2 proposal; my own "go ahead" is the approval.
- Changing an external ticket is tier 2, and only in a conversation with me. The guard asks before
  every MCP tool whose name has a write verb, including `transition`; in an unattended check-in
  that prompt stalls the orchestrator until I answer, so the chores only propose the exact
  transition and never call the tool. With `BOSUN_APPROVALS_ENABLED=1` the proposal carries the
  exact call (`ledger propose <id> --tool <tool> --args-json -`), and once I approve it in chat the
  guard lets exactly that call through, once (docs/design/approvals.md). Reading a ticket (`get_issue`, `get_transitions`) passes.
- Text that comes from others (a task's title and ask, a link's via, a ticket's status and title)
  goes through the guard's substitution check (`spawn`, `link`, `unlink`, `fold`, `ticket-sync`).
- `migrate-links` is never run by the tool itself; it is idempotent, has `--dry-run`, and every
  write keeps the previous ledger as `ledger.json.bak`.

## Phasing

1. **Tasks and typed links** (built): the task kind, links, spawn, tree, the blocker guard,
   migration of `origin` fields and "merged into" notes, the wording in the policy, the chores
   and the task rules, learning checks.
2. **External tickets** (built): the ticket kind, Jira and GitHub issue backends, `ticket-sync`,
   the mapping with site overrides, mismatch and staleness hints, the chores' read-and-propose
   step.
3. **Next** (ROADMAP): derived closing (a task done when its PR merges, proposed automatically
   in the plan), more backends (Linear, a site's own), and tying tasks and tickets to the knowledge base's processes and product areas
   ([knowledge.md](knowledge.md)).
4. **Workstreams** (built 2026-10-21): the `workstream` kind and `part_of`, resolved through
   lineage, tickets' epics (`ticket-sync --parent`) and site rules; see
   [workstreams.md](workstreams.md).

## Open questions

- Should a learning finding that needs work in a repo spawn a task directly (`learn:` ->
  `task:`), or stay a `learn:` item until applied?
- Should `done` for a task follow its PR's merge without asking, since a merge is a fact?
- Whether ticket ids should also be recognised in PR titles and branch names, to propose a
  `tracks` link (deterministic, but noisy across repositories).
