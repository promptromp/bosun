# The ledger

The ledger is bosun's record of every piece of work: each ask, task, pull request, review, question
and ticket. Each one is an item with an id, a state and a history, and links that say where it came
from and what came of it. The orchestrator and the repo agents change it only through bosun's own
commands, and those check every change against the item's rules.

You rarely touch it yourself. You tell the orchestrator what you want in your own words, and the
console and `bosun ledger list` show you where everything stands.

## Why a ledger

- **Nothing drops between days, agents or people.** The orchestrator starts a fresh conversation each
  morning. What was in flight is in the ledger, not in yesterday's chat.
- **Nothing is marked done on a guess.** The moves that matter need evidence: your words, a link to the
  reply, a new review request.
- **Nothing is handled twice.** Every Slack message bosun has processed is recorded by its link, so a
  message still in tomorrow's search is recognised and skipped.
- **Work and the trackers stay in step.** Jira and GitHub issues are mirrored, and bosun tells you where
  a ticket and the work disagree.

## What goes in it

Every item has a kind, and its id says what it is:

| Kind | Its id | What it is |
|---|---|---|
| Review request | `review:<owner>/<repo>#<n>` | Someone asked you to review their pull request |
| Tech design review | `td:<page-id>` | Someone asked you to review a tech design |
| Your pull request | `mypr:<owner>/<repo>#<n>` | Your own pull request, followed until it is merged or closed |
| Mention | `slack:<channel>/<ts>` | Any other Slack mention that may need you |
| Task | `task:<YYYY-MM-DD>/<id>` | Work we decided to do that has no pull request or page of its own yet |
| Outreach | `out:<parent-id>/<id>` | Something we asked someone, and wait on |
| Ticket | `jira:<KEY>-<n>`, `ghissue:<owner>/<repo>#<n>` | An issue in your tracker, mirrored read-only |
| Workstream | `ws:<slug>` | A time-bound effort that groups items, such as "Widget search" |
| Learning | `learn:<YYYY-MM-DD>/<id>` | A fix the evening learning run proposes |

The `<id>` in a task, outreach or learning id is six characters such as `7k2m9q`, made so that two Macs
never make the same one. `bosun ledger kinds` prints every kind with its states and rules.

## Each item moves through states

Every kind has its own state machine, and a move it does not allow is refused. A task, for example:

```mermaid
stateDiagram-v2
  direction LR
  [*] --> proposed
  proposed --> accepted: your word
  proposed --> declined
  declined --> proposed
  accepted --> delegated: handed to an agent
  accepted --> done
  delegated --> accepted: taken back
  delegated --> done
```

A task starts as `proposed`. It is `accepted` only on your word, `delegated` when bosun hands it to the
agent working in that repository, and `done` when what it produced has landed or its report answered
the ask. From any state it can be `dropped`, with a note that says why.

Outreach, something we asked and wait on, has a state machine of its own:

```mermaid
stateDiagram-v2
  direction LR
  state "no-reply" as no_reply
  [*] --> draft
  draft --> proposed: the message waits for you
  draft --> sent
  draft --> replied
  proposed --> draft
  proposed --> sent
  proposed --> replied
  sent --> replied
  sent --> no_reply: given up
  sent --> applied
  replied --> sent: a follow-up
  replied --> applied
  no_reply --> replied: a late reply
```

`applied` means the reply reached the work that waited on it. `no-reply` is giving up after the last
nudge, and a late reply still counts.

## Rules that keep it honest

- **Only your word accepts work.** A task becomes `accepted` only with what you said as its evidence
  (`user: go ahead`). A colleague's message never accepts work: their request becomes a proposal that
  waits for you.
- **A reply needs proof.** Recording a reply needs a reference to it: a Slack link, an email's message id,
  a comment's URL.
- **A re-review needs a new request.** Moving a review you already did back to `rereview-requested`
  needs a request made after your review. An old message that is still in the search doesn't count.
- **Work that waits on someone stays open.** An item can't finish while a question it asked is still
  open, unless the note says why. A pull request that merged or closed is a fact, not a choice, so that
  goes through, and the note names the question still open.
- **Every change is kept.** Each item keeps its history: what moved, when, why and on what evidence.

## Links: where work came from and what came of it

Items are linked, so a piece of work carries its story with it. A link is stored once and read both
ways:

| Link | Means |
|---|---|
| `spawned_from` | This item came from that one, with the message that asked |
| `produces` | This item's work produced that one, such as a task's pull request |
| `tracks` | That ticket tracks this item |
| `blocked_by` | This item can't finish while that one is open |
| `duplicate_of` | The same item twice; the other carries it on |
| `folded_into` | This item's work goes on as part of that one, for example in its pull request |
| `part_of` | This item belongs to that workstream |
| `relates_to` | Loosely related |

Here is one ask and everything that came of it, from the example on the [home page](index.md):

- **A Slack mention:** Priya asks "could you also export credit notes?"
  - **A task,** spawned from her message: export credit notes. You said "go ahead", so it is accepted
    and handed to the exporter's agent.
    - **A pull request** it produced, waiting for review.
    - **A Jira ticket** that tracks it, which says Done. Bosun flags that, since the pull request is
      still open.
    - **A question** to Dana about credit-note numbering. Until her reply is handed over, the task
      can't finish.
  - **Two more tasks** from the same thread: one the same as the first (`duplicate_of`), one small
    enough to be done in the same pull request (`folded_into`). Both are closed, and both point at
    the task that carries them on.

`bosun ledger tree <id>` shows this for any item, with each item's state.

## How new work is spawned

Work that has no pull request or page of its own yet is a task, spawned from the item it came from, so
its origin is a link rather than a note:

- Tell the orchestrator "go ahead and do it" about a reply or a mention, and it spawns the task with
  your words, accepts it and hands it to the right agent.
- A colleague's request becomes a proposed task that waits for you under *Needs your approval*.
- Repo agents spawn the follow-ups they find, and never start them on their own.
- A request made twice is closed as a duplicate of the first, and a small ask done inside a bigger one
  is folded into it. Both keep the link.

## Jira and GitHub issues

A Jira issue or GitHub issue that tracks your work is a ticket item, such as `jira:EXP-123`. Tell the
orchestrator "this pull request is for EXP-123" and it links them. It reads the ticket with your
tracker's own tools and records what it saw. Bosun never changes a ticket itself.

A ticket's state is a category: `todo`, `in-progress`, `in-review`, `done` or `cancelled`. Where a
ticket and the work disagree, bosun proposes the exact change for you to make: a ticket closed while
the work is open, still open after the pull request merged, still `todo` while the pull request is in
review. For another team's projects it only tells you. Status names your tracker uses and the projects
that are yours go in `bosun.env`; the [Orchestrator guide](orchestrator.md#external-tickets) has the
settings.

## Handing work to an agent, and hearing back

`bosun delegate` hands an item to a repo agent and records which window has it. The agent reports back
with `bosun ledger report`: what it did, the pull request or file it produced, and what is still open.
So no step depends on a chat history or on what a window happens to show, and `bosun ledger list`
shows which window has each item until it reports.

## Workstreams

A workstream groups the items of one effort for a weekly stand-up and for stats. Put the first item in
it, and what came from that item follows. `bosun ledger summary --ws <ws>` drafts the stand-up: what
shipped, what is in review and with whom, what waits on others, and what is next.
[Workstreams](orchestrator.md#workstreams-what-an-item-is-for) has the details.

## How it is kept

Every change is first written to a journal, and `ledger.json` is rebuilt from it. Writes take a lock,
and a write cut short is finished by the next one, so a crash never leaves half a change behind.
`bosun ledger validate` checks every rule, and `bosun doctor` runs it for you.
[Sync and sharing](design/sync.md) explains the journal, which is also what will let a team share one
ledger.

## Commands you will see

You rarely type these. The orchestrator and the agents use them, and they are handy for a look:

| Command | Shows |
|---|---|
| `bosun ledger list` | Open items, with their state and what each waits on |
| `bosun ledger show <id>` | One item: its fields, history and links |
| `bosun ledger tree <id>` | Where an item came from and what came of it |
| `bosun ledger kinds` | Every kind, with its states and rules |
| `bosun ledger tickets --mismatch` | Where tickets and the work disagree |
| `bosun ledger summary --ws <ws>` | A stand-up draft for one workstream |
| `bosun ledger validate` | Every rule checked, with what to do about a finding |

[Commands](commands.md#bosun-ledger) lists every subcommand, the [Orchestrator guide](orchestrator.md) says how
outreach, tasks, tickets and workstreams are used, and [Work-item lineage](design/lineage.md) is the design behind
the links.

## Reference

### What the ledger keeps

- **Items** with stable ids: `review:<owner>/<repo>#<n>`, `td:<page-id>`, `mypr:<owner>/<repo>#<n>`,
  `slack:<channel>/<ts>`, outreach `out:<parent-id>/<id>`, learning proposals `learn:<YYYY-MM-DD>/<id>`, tasks
  `task:<YYYY-MM-DD>/<id>`, tickets `jira:<KEY>-<n>` or `ghissue:<owner>/<repo>#<n>`, and workstreams `ws:<slug>`.
  Each kind has a state machine (`bosun ledger kinds`), and a move it does not allow is refused. The date in a task,
  outreach or learning id is the UTC day it was made, and `<id>` is six characters such as `7k2m9q`, hashed from this
  Mac's replica id and where the ledger's journal is (for learning, from the finding and the run), so two Macs never
  make the same id and a pruned one never comes back. Copy ids from what the command printed. Ids made before,
  ending in a number (`task:2026-10-15/1`), stay valid. Each item keeps its last 200 history entries; the journal
  keeps all of them.
- **Links** between items (`spawned_from`, `produces`, `blocked_by`, `duplicate_of`, `folded_into`, `tracks`,
  `part_of`, `relates_to`), stored once and read both ways.
- **Sources:** every processed Slack message, by its link. A message still in tomorrow's scan window is recognised
  and skipped.
- **Proposals:** actions waiting for your approval, which survive the daily restart.
- **Runs:** when each chores mode last ran, which sets the check-in's scan window.
- **Delegations and reports:** `bosun delegate` records which window works on an item (and the task's header, so the
  task can be sent again), and `delegate --update` sends that window addenda framed with the item's id. Repo windows
  record their outcome with `bosun ledger report <id> --summary ... --result <URL or path> [--open ...] [--state
  ...]`, which the orchestrator reads with `ledger reports`, so no step depends on chat history or on a window's
  screen.
- **What you said in repo windows** (`said`), with [events](events.md#events-from-repo-sessions) on: each prompt you
  type there, on the item it is about, read with `ledger said [<id>]`.

<details class="note" markdown>
<summary>When a hand-over ends, and when a window is free</summary>

A hand-over ends with a report, or when the item reaches a state that shows the work done (`work_done` in `ledger
kinds`: `reviewed`, `commented`, `awaiting-review`, `approved`, or a final state). Until then `ledger list` shows
`@<window> (awaiting report)`, `delegate` refuses the item without `--again`, and `delegate --update <id> --window
<window>` can move it: the new window gets the whole task, the old one a stop notice by id (if it cannot take it, the
move is still made and the history says so).

A report ends the hand-over, not the work: `ledger list` shows `(working)` while the window's agent is in the middle
of a turn on the item (its active item: the latest hand-over, report, `ledger working` or something you said there),
and `ledger delegation` has `"working"`. A window is free only when none of its items is working.

</details>

### The re-review rule

`reviewed` to `rereview-requested` (and `commented` to `rereview-requested` for tech designs) requires `--evidence`
of a new, explicit request after your last review: GitHub re-requesting you, or a new Slack message asking again.
Nudges follow the chores procedure (the prompt, not code): only for your non-draft pull request in
`awaiting-review`, never once it is `approved`.

### Checking it: `ledger validate`

`bosun ledger validate [--journal] [--json]` checks every rule (a state its kind does not have, a link lineage would
refuse, a second `part_of`, a link to an unknown item, with the `ledger unlink` that clears it, since only a prune
from before it kept lineage whole leaves one) and exits 1 on any finding. `--journal` also rebuilds the journal and
lists its conflicts and each item where the rebuild and `ledger.json` differ ([Sync and sharing](design/sync.md)),
and says when `ledger.json` was changed outside the journal (the next write adopts it). A ledger from before journals
is not a failure: it prints "no journal yet; the next write starts it" and exits 0. With `--json` each finding has a
`severity`: `error` where writes are refused or a rebuild would lose data (journal damage, a position that does not
match, a rebuild that differs), `warning` for the rest, `note`. `bosun doctor` runs it, and fails on an error but only
warns on a warning.

### Writes, the journal, and repairing it

Writes take a file lock and replace `ledger.json` in one step, keeping the version before as `ledger.json.bak`;
`ledger.md`, the readable view, is made again after each one. Each write first appends what it changed to the journal
(`journal/ledger/` in the state folder), and `ledger.json` is the rebuild of that journal, recording the position it
reflects and a digest of what it holds.

<details class="note" markdown>
<summary>Cut writes, hand edits, damage, and starting the journal again</summary>

- **A write cut short** between the journal and `ledger.json` is completed by the next write (a command that only
  reads shows it without saving), and one cut short inside the journal is set aside (`*.torn-<time>`, kept) and left
  out ([Sync and sharing](design/sync.md#changes-not-files)).
- **Copying `ledger.json.bak`**, or any older `ledger.json`, back does not undo a write: the journal brings it
  forward again. Undo a write with the command that reverses it.
- **A hand edit.** A change made to `ledger.json` by hand, or by an older bosun, is adopted by the next write: it
  goes into the journal as a transaction named `adopt`, with a line on stderr. What the journal cannot hold, such as
  removing a history entry (history only grows), is undone by that write, which says so and keeps the edited file as
  `ledger.json.bak` until the next write.
- **Damage.** Writes stop and say so when a journal file has gone missing or lost lines (the journal is behind
  `ledger.json`), or a line at the end of one, which every write reads, is damaged. Damage further up, or a gap in a
  file's numbering, stops only a write that has to rebuild (after a write cut short or a hand edit); until then `bosun
  ledger validate --journal` and `bosun doctor` report it.
- **Starting the journal again.** Either way, move `journal/ledger` aside, and the next write starts a new journal
  from `ledger.json`, as a new replica. `replica.json` may stay: a journal that starts again (moved aside, emptied or
  removed) always gets a new id, made by that write in place of the old one, so that no id is minted twice.
  `replica.json` is this Mac's id in the ontology's journal too, so the Mac gets a new id in both, which the ontology
  carries on under as well (its old replica file stays in its journal). A write cut short as it starts the new
  journal is finished by the next write, as any other.

</details>
