# Orchestrator

The `orchestrator` window runs an interactive Claude session with Remote Control, so you can
answer it from your phone. Unlike the repo windows it:

- **starts a fresh conversation every day** (`orchestrator-YYYY-MM-DD`). `refresh` restarts it once
  it is idle the next morning. Continuity lives in the ledger, not the chat history.
- runs in its **own permission setup**: `BOSUN_ORCHESTRATOR_FLAGS` (default
  `--chrome --permission-mode auto`) plus `orchestrator/settings.json`, never
  `--dangerously-skip-permissions`.
- loads `orchestrator/policy.md` as an appended system prompt and the `orchestrator/plugin`
  commands. The main one is `/bosun:chores morning|check-in`.
- must run in a folder Claude Code **already trusts** (`BOSUN_ORCHESTRATOR_DIR`, default your
  repos dir). A trust prompt would block an unattended start; `bosun doctor` checks this.

## Delegated tasks and folder trust

The orchestrator hands work to repo sessions with `bosun delegate <type> --id ... --ask ...`.
The task text is built from `orchestrator/tasks/`: one file per task type with what the repo
session may and may never do and what the orchestrator does afterwards, plus `_common.md` with
the rules every task shares. The orchestrator only supplies the facts (ledger ids, link,
requester, a one-line ask, and your instructions in `--details`), so the rules cannot drift.
`bosun delegate --list` shows the types:

| Task | The repo session may | It never |
|---|---|---|
| Address review comments on my PR | push new commits to the PR branch; reply in the review threads it addressed (every thread awaiting the author in a fresh `bosun pr-threads --awaiting-author`, any round) | resolve threads, re-request review, merge, post in Slack, reuse an earlier draft or file for a "review addressed" message |
| Investigate or answer a question | write findings to a local markdown file | post anything |
| New work for me | work in a git worktree, push a new branch, open a draft PR | request reviewers, mark the PR ready, merge, post in Slack |
| Review someone else's PR | post one review with inline comments; APPROVE if nothing blocks merging, otherwise COMMENT; skip a PR that is merged, closed or already approved by someone else, unless the ask names something specific | request changes, push to the PR branch, merge, post in Slack |

Asking for review stays with you: the orchestrator proposes marking the PR ready (a draft does not
notify code owners) and the Slack message, with the exact text. No task merges, force-pushes,
pushes to the default branch or posts in Slack, Jira or Confluence. The orchestrator delegates
`review` and `fix-my-pr` tasks on its own; other work (for example a Slack ask for a code change)
only once you ask or approve, and so does handing an item over again before its window reported,
or moving it to another window. Each session reports with `bosun ledger report` (see Ledger).
These rules live in the task text: a repo session follows them, but neither auto mode nor
`--dangerously-skip-permissions` technically prevents it from doing more.
To change a rule, edit the task file; `tests/smoke.sh` checks that the ledger steps are valid and
that this table matches.

**Site overlays.** To adapt the orchestrator to your team without forking, put files in the config
directory: `policy.md` is appended to the orchestrator policy; `tasks/<type>.md` adds a task type
(same format as `orchestrator/tasks/`; a file named like a built-in type replaces it); and
`tasks/_common.md` adds rules to every task (the built-in shared rules always stay).
`bosun doctor` lists the overlays in use.

Claude Code shows a folder trust prompt the first time it opens a repository (trusting a parent
folder does not cover it) and has no setting or flag to skip it; only headless `-p` runs skip it.
An unattended start would stall there. `bosun trust <dir>` records the same entry Claude
writes when you accept the prompt (`hasTrustDialogAccepted` in `~/.claude.json`, an internal
format) for git repos inside the repo mask, and `bosun send --new|--start` (and so
`delegate --new|--start`) call it for their folder (`BOSUN_TRUST_MASKED_REPOS=1`). Outside the mask they fail at once with the command to
run. `bosun doctor` checks every session folder.

## Chores

`orchestrator/plugin/commands/chores.md` holds the whole procedure:

0. **Orient:** record the run (`ledger mark-run`, which also prints where the Slack scan starts),
   read `bosun context`, and the reports repo sessions filed since the scan window began.
1. **Slack:** mentions and DMs in the scan window: since the last run for check-ins; in the
   morning the last `BOSUN_SLACK_LOOKBACK_HOURS` (24), or from the last scan if that was
   longer ago (a failed morning, days away), but at most `BOSUN_SLACK_MAX_LOOKBACK_HOURS` (96)
   back. Each message is checked against the ledger (`seen`) and classified as `pr-review`,
   `td-review`, `my-pr`, `mention` or `fyi`.
2. **Review requests:** derive state from GitHub. If you already reviewed, mark it `reviewed`.
   Otherwise hand it to the repo window, which posts the review with inline comments and approves
   when nothing blocks merging, or propose opening a window for it.
3. **Tech design reviews:** read the Confluence page, draft comments, and propose posting them.
4. **Your open PRs** (found with `bosun my-prs`; see the repo mask and window below), checked through `gh` and
   `bosun pr-threads`:
   - changes requested: the repo session pushes fixes and answers every thread that awaits you
     (`pr-threads --awaiting-author`, any round); once that lists nothing, the orchestrator
     proposes re-requesting review, with the thread counts per round
   - approved and green: propose a squash-merge
   - waiting too long: propose a nudge in the original review-request thread
5. **Outreach** (what we wait on from other people): propose the message for new drafts, check
   sent ones for a reply on their channel, hand replies to the window holding the work, propose
   nudges. See [Waiting on someone](#waiting-on-someone-outreach).
6. **Mentions:** new asks are surfaced under FYI with a one-line summary, with a draft reply
   proposed when one fits. A mention that asks us to do something becomes a proposed task spawned
   from it, with its `delegate new-work` / `investigate` command; nothing is delegated before you
   say so.
7. **Tasks and tickets:** proposed tasks wait for your yes (see
   [Tasks and links](#work-we-take-on-tasks-and-links)); accepted ones are handed over, delegated
   ones followed, and their PRs linked. Tickets of open work are read with the tracker's read
   tools and recorded; where a ticket and the work disagree, the exact transition is proposed
   (see [External tickets](#external-tickets)).
8. **Report:** group the reply as *Needs your approval* (numbered, with exact text; also earlier
   proposals you have not answered and `learn:` proposals, see [Learning](learning.md#learning)), *Handled
   automatically* and *FYI*. You answer "1 and 3 yes, 2 skip". On a Monday morning it also offers
   a stand-up draft per active workstream (see [Workstreams](#workstreams-what-an-item-is-for)).

**Scope.** `BOSUN_REPO_MASK` limits the orchestrator to matching repositories. It is a
space-separated list of `owner/repo` glob patterns, for example `my-org/*` or
`my-org other-org/one-repo` (a bare owner means all its repos; empty means everything). It is
enforced in code, not just the prompt:
- `bosun my-prs` filters by it
- the ledger refuses `review:` and `mypr:` items outside it
- `bosun context` only maps matching repos

**PR window.** `bosun my-prs` discovers your open PRs with activity within
`BOSUN_MY_PR_WINDOW` (default `48h`). Pass `--since 7d`, `--since 2w` or `--since 2026-09-01`
for a wider sweep, and `--by created` to filter by creation time instead of activity. You can also
just ask the orchestrator ("check my PRs from the last week"). PRs already in the ledger stay
tracked until merged or closed, whatever the window.

Each day's new orchestrator conversation starts with `/bosun:chores morning`; with
`BOSUN_ORCHESTRATOR_MORNING_CHORES=0` it starts idle instead, and the chores run only when you
or a check-in ask for them.

The `checkin` window sends `/bosun:chores check-in` every `BOSUN_CHECKIN_MINUTES`. It only
does so when the orchestrator has been idle for 5 minutes and you have not typed in tmux recently,
so it never interrupts a conversation. `shell` counts as idle (the turn is over and only a
background command still runs, such as a wait for a review to settle): the check-in goes in ahead
of that command's completion notice, and the 5 minutes count from the end of the turn. The first
check-in comes one interval after the window starts, since the orchestrator begins the day with
the morning chores. A check-in held back only by timing (the orchestrator busy or idle too briefly,
or you typing) stays due: the loop retries it every tick (`BOSUN_EVENTS_INTERVAL_SECONDS`, or
every few minutes with events off) until it goes out, logging the skip once, so a busy morning
never pushes it a whole interval later. Events keep flowing while it waits.
A check-in also goes out only when something is new since the last one (`lib/checkin_gate.py`, no
Claude call): events not yet delivered, Slack messages the watcher found (or a watcher that is off,
failing or stalled, since the check-in then scans Slack itself), a repo session's report, an outreach
whose wait crossed a business day or whose `expect_by` passed, or a new watchdog alert. Otherwise it
logs `skip: nothing new since HH:MM`, at most `BOSUN_CHECKIN_MAX_QUIET_HOURS` (4; 0 turns the
gate off) in a row, so the chores still run. Every check-in makes the orchestrator re-read its
whole conversation, which is why quiet hours skip it.

A permission prompt or question left open in the orchestrator pauses the check-ins until you answer
it. The [watchdog](watchdog.md#watchdog) alerts you when it, or any other Claude window, has waited
`BOSUN_ALERT_WAITING_MINUTES` (30 by default).

## Ledger: idempotency and state

`bosun ledger` (`lib/ledger.py`) is the only way the orchestrator, and the repo sessions it
delegates to, record state. It keeps:

- **items** with stable ids: `review:<owner>/<repo>#<n>`, `td:<page-id>`, `mypr:<owner>/<repo>#<n>`,
  `slack:<channel>/<ts>`, outreach, `out:<parent-id>/<n>` (see below), learning proposals,
  `learn:<YYYY-MM-DD>/<n>` (see [Learning](learning.md#learning)), tasks, `task:<YYYY-MM-DD>/<n>`, and
  external tickets, `jira:<KEY>-<n>` or `ghissue:<owner>/<repo>#<n>` (see
  [Tasks and links](#work-we-take-on-tasks-and-links)), and workstreams, `ws:<slug>` (see
  [Workstreams](#workstreams-what-an-item-is-for)). Each kind has a state machine
  (`bosun ledger kinds`); invalid transitions are rejected.
- **links** between items (`spawned_from`, `produces`, `blocked_by`, `duplicate_of`, `folded_into`,
  `tracks`, `part_of`, `relates_to`), stored once and read both ways.
- **sources**: every processed Slack message (by permalink). A message still in tomorrow's scan
  window is recognised and skipped.
- **proposals**: actions awaiting your approval, which survive the daily restart.
- **runs**: when each chores mode last ran, which sets the check-in scan window.
- **delegations and reports**: `bosun delegate` records which window works on an item (and
  the task's header, so the task can be sent again), and `delegate --update` sends that window
  addenda framed with the item's id. Repo sessions record their outcome with
  `bosun ledger report <id> --summary ... --result <URL or path> [--open ...] [--state ...]`,
  which the orchestrator reads with `ledger reports`, so no step depends on chat history or on a
  window's screen. A hand-over ends with a report, or when the item reaches a state that shows
  the work done (`work_done` in `ledger kinds`: `reviewed`, `commented`, `awaiting-review`,
  `approved`, or a terminal state). Until then `ledger list` shows `@<window> (awaiting report)`,
  `delegate` refuses the item without `--again`, and `delegate --update <id> --window <window>`
  can move it: the new window gets the whole task, the old one a stop notice by id (if it cannot
  take it, the move is still made and the history says so). A report ends the hand-over, not the
  work: `ledger list` shows `(working)` while the window's Claude is mid-turn on the item (its
  active item: the latest hand-over, report, `ledger working` or something I said there), and
  `ledger delegation` has `"working"`; a window is free only when none of its items is working.
- **what I said in repo windows** (`said`), with [events](events.md#events-from-repo-sessions) on: each
  prompt I type there, on the item it is about, read with `ledger said [<id>]`.

The re-review rule is enforced. `reviewed` -> `rereview-requested` (and `commented` ->
`rereview-requested` for tech designs) requires `--evidence` of a new, explicit request after your
last review: GitHub re-requesting you, or a new Slack message asking again. Nudges follow the
chores procedure (the prompt, not code): only for your non-draft PR in `awaiting-review`, never
once it is `approved`.
Writes take a file lock and replace `ledger.json` atomically, keeping the previous version as
`ledger.json.bak`; `ledger.md` is regenerated after each one. Each item keeps its last 200
history entries.

## Waiting on someone: outreach

Work often stops on someone else: a product decision, another team's contract, a vendor's file
format, a reporter's environment. Such an ask is an **outreach** item, so it does not live only in
chat, a PR description or a Slack reply:

| Field | |
|---|---|
| id | `out:<parent-id>/<n>` for the work item it blocks, or `out:<YYYY-MM-DD>/<n>` when it belongs to none (no item id looks like a date: each starts with its kind's prefix) |
| `channel` | `slack`, `email`, `jira`, `github`, `confluence` or `other` (one table in `lib/ledger.py`) |
| `purpose` | `question`, `decision`, `clarification`, `approval`, `review`, `info-ack` (they confirm they saw it) or `fyi` (one-way: no reply expected, so it is done once sent) |
| `to`, `subject`, `options`, `details`, `expect_by` | whom, what (one line), the choices, context, and an optional date |
| `messages` | every message sent for it, the first and each follow-up: ref (permalink, comment URL or message id), channel, and when it was sent (`--at`, else a Slack permalink's own time, else when recorded) |
| `answer`, `reply_ref` | the reply in one line, and the reference to it (the evidence) |

`draft` (recorded) -> `proposed` (the outgoing message awaits my approval: `ledger propose` moves
it there, rejecting the proposal moves it back) -> `sent` (`ledger sent --ref`; later calls add
follow-ups, counted as nudges; several ids record one message on each, e.g. one nudge for several
questions; an `fyi` item goes straight on to `applied`) -> `replied` (`ledger replied --evidence --summary`) -> `applied`
(`bosun delegate --update <out-id>` hands the reply to the window holding the parent, or it was
surfaced to me). `no-reply` gives up after the last nudge; `dropped` is the way out from any state
(`ledger fold <out-id> --into <other-id>` when its questions go into another message,
`--note 'duplicate of <other-id>'` when it asks exactly what another asks).

- **Parents show it and stay open.** `ledger list` shows `waiting on out:.../1 (slack to Dana, 2d)`
  on the parent, and the ledger refuses to finish a parent (any terminal state, also through
  `report --state`) while its outreach is open, unless `--force-open-outreach` (the note says why).
  A PR that merged or closed is a fact, not a choice, so those states go through and the note
  names the outreach still open. Its open review requests (purpose `review`) are dropped with it,
  since nobody owes that review any more; other outreach (a question) stays open.
- **Replies are matched, not re-classified.** `ledger seen <permalink>` names the open outreach
  whose Slack thread (or DM) a new message is in (all of them, latest message first, when several
  share it, so the orchestrator picks the one it answers), so the reply is recorded on it
  (`--class outreach`) instead of becoming a new mention. Other channels: the chores read the
  ticket, PR or page comments, or ask me (email); see step 5 of the chores.
- **One conversation, one item.** `ledger reach-out` warns (it does not refuse) when the parent
  already has open outreach to the same person, so an addition goes out as a follow-up on that
  item instead of a second one waiting in the same thread. `ledger upsert <out-id> --field
  subject=... --field options='a | b'` keeps the record in line with the message actually sent.
- **Nudges are proposals.** After `nudge_after_business_days` without a reply, and below
  `max_nudges`, the chores propose a follow-up in the same conversation; nothing is sent on its own.
  A PR waiting on its reviewers has no fixed count (`max_review_nudges`, 0 = no cap): it is nudged
  each business day while the next move is the reviewers', after a fresh check of the PR, its
  threads, the conversation and the ledger, at most once per reviewer per day. After
  `review_escalate_after_nudges` unanswered nudges the chores also propose another reviewer.
  The watchdog does not alert on outreach.
- **Times are when it happened, not when it was recorded.** The wait counts from the latest
  message's time. `ledger sent` and `ledger replied` take it from `--at` (ISO 8601 with an offset,
  or local `YYYY-MM-DD HH:MM`; never in the future), else from a Slack permalink's own ts, else
  now; `sent --at` with a ref already recorded corrects that message's time. The history entry
  keeps the recording time and says where the other came from (`happened_at`, `time_from`).
- **Free text.** You never type these commands. Tell the orchestrator "ask Dana whether we can drop
  the shared password", "email the vendor to clarify the format and wait for them", "ping Priya
  again", "Marco answered in the thread, go ahead with b"; the policy maps each to the steps above.
  Repo sessions record outreach for decisions they cannot make (`orchestrator/tasks/_common.md`)
  and never send it; the orchestrator proposes the message. Questions for you are not outreach:
  repo sessions ask you directly when you are at the window, or put the decision in their report.

## Work we take on: tasks and links

Work we decide to do that has no artifact of its own yet is a **task**: what a colleague asks of
us in a reply or a mention ("yes please, fix the other callers too", "could you also ..."), what
you tell the orchestrator to do, a follow-up a repo session finds. It is spawned from the item it
came from, so its origin is a link rather than a note, and it is handed over under its own id.
Design and phasing: [docs/design/lineage.md](design/lineage.md).

| State | |
|---|---|
| `proposed` | `ledger spawn [<from-id>] --title --ask [--via <permalink or 'user: ...'>] [--requester] [--repo] [--blocks]` records it; an exact repeat is unchanged, a similar open task from the same source is a warning |
| `accepted` | only on your word: `--evidence 'user: <what you said>'` is required, so a colleague's message never accepts work; it closes the task's pending proposal |
| `delegated` | `bosun delegate new-work --id <task id>` (it refuses a task you did not accept, and an outreach id as the work item) |
| `done` | what it produced landed (its PR merged; `ledger tree` says so), or its report answered the ask |
| `declined`, `dropped` | you said no; or it went away: `ledger fold <id> --into <other-id>` when its work goes on as part of another item, `--note 'duplicate of <id>'` when it was the same item twice (both record the link) |

**Links** are stored once, on the item they start from, and read both ways (`ledger show` lists
them under `derived`, with the lineage tree and hints): `spawned_from` (provenance, with `via`:
the message that asked, or `user: ...`), `produces` (a task's PR), `blocked_by` (the item is not
finished while the other is open, like an outreach parent), `duplicate_of` (the same item twice),
`folded_into` (its work goes on as part of the other, e.g. in that task's PR; read back as
`absorbs`), `tracks` (an external ticket), `part_of` (the item's one workstream, see
[Workstreams](#workstreams-what-an-item-is-for)) and `relates_to`. `ledger link` refuses unknown ids,
self-links and cycles; `ledger tree <id>` shows where an item came from and what came of it, with
states; `ledger list` shows `from <id>`, `-> <id>`, `tracks <id>`, `folded into <id>` and
`absorbs <id>`. An outreach's `parent` stays a field and reads as
a computed `blocked_by`. `ledger reports` prints the commands that record a PR a report names and
link it to the work. `ledger migrate-links [--dry-run]` moves ad hoc provenance into links once
(an `origin` field naming an item, "merged into <id>" and "duplicate of <id>" drop notes, and a
`duplicate_of` recorded from a "merged into" note, which becomes `folded_into`); it is idempotent.

`ledger fold <id> --into <other-id> [--note ...]` records that an item's work went into another
(a small task done in another task's PR, an outreach draft said as a follow-up on an existing
conversation): it links `folded_into`, withdraws the item's pending proposal (it was neither
carried out on the item nor rejected), and closes the item in its kind's way out (`dropped`;
`dismissed` for kinds without it) with the note `folded into <other-id>: ...`. It refuses an item
with open outreach or blockers as `transition` does (`--force-open-outreach`), and a finished
target unless `--force-closed-target` (the work already landed with it). If the target is with a
repo window it prints, and does not run, the `bosun delegate --update` that carries the folded
item's title and ask there. A drop note "merged into <id>" or "folded into <id>" records
`folded_into` too. `prune` keeps a folded item while its target is open.

Tell the orchestrator "go ahead and do it" about an outreach reply or a mention and it spawns the
task with your words as `via`, accepts it and delegates it; a colleague's request ends up as a
proposed task under *Needs your approval*. Repo sessions spawn follow-ups they find and never
start them.

## External tickets

A Jira issue or GitHub issue that tracks our work is a **ticket** item (`jira:EXP-123`,
`ghissue:acme/exporter#7`), a read-only mirror: tell the orchestrator "this PR is for EXP-123" and
it runs `ledger link <pr> tracks jira:EXP-123`, which creates the ticket. The orchestrator reads
it with the tracker's read tools and records what it shows with `ledger ticket-sync <id> --status
'<status>' [--category ...] [--title ...] [--url ...] [--assignee ...]`; bosun never
transitions a ticket itself. Its state is a category (`todo`, `in-progress`, `in-review`, `done`,
`cancelled`; `unsynced` before the first read), from your overrides, the built-in status names or
`--category` (ours, or the tracker's own, such as Jira's statusCategory). Status names your
tracker uses that the built-in names miss go in `bosun.env`:

    BOSUN_TICKET_STATUS_MAP="jira:Ready for Review=in-review; jira/EXP:On Hold=todo"
    BOSUN_TICKET_STALE_HOURS="24"   # a ticket of open work read longer ago is flagged
    BOSUN_TICKET_OWNED_PROJECTS="EXP acme/exporter"   # empty: every project is yours

`ledger tickets [--all] [--mismatch]` lists the tickets of open work, how to read each, and where
a ticket and the work disagree: closed while the work is open, still open after the PR merged,
`todo` while the PR is in review, never or long ago synced. Each is a proposal under *Needs your
approval* with the exact transition, because a tracker write tool asks for permission and would
stall an unattended check-in. A ticket outside `BOSUN_TICKET_OWNED_PROJECTS` (another team's,
such as an infrastructure request in their intake state) is theirs to move: its mismatches print as
`fyi:` lines, never as proposals, and the console does not count them under *Needs you*.

## Workstreams: what an item is for

A **workstream** (`ws:<slug>`) is a time-bound effort you drive, such as "Widget search". It
groups ledger items for a weekly stand-up, stats and, later, the knowledge graph. It is not a Jira
epic (it may name one) and not a product area (it is part of one). Design, research and examples:
[docs/design/workstreams.md](design/workstreams.md).

- `ledger ws add <slug> --title ... [--epic WID-100] [--channel <Slack channel id>] [--repo ...]
  [--started/--ended/--target <date>] [--area <knowledge entity>] [--parent <ws>] [--alias ...]`
  creates one or updates its fields. `ws list`, `ws show`, `ws set-state <ws> active|paused|done
  --note ...` and `ws alias` cover the rest. Any `<ws>` argument takes the id, the slug or an
  alias. A workstream can sit under one other, two levels at most.
- `ledger link <id> part_of <ws>` puts an item in its one workstream; `--move` moves it. Tag the
  root: what was spawned from it, what it produced, its outreach and what was folded into it
  inherit the workstream. Nothing is copied.
- Each item resolves in this order: its own `part_of`, then inherited, then a tracked ticket whose
  epic is the workstream's (`ticket-sync ... --parent <key>` records a ticket's epic or parent),
  then your rules in `$BOSUN_CONFIG_DIR/workstreams.json` (repo globs, title words, origin
  Slack channel, kinds; see `examples/generic/workstreams.json`) and the workstream's own channel,
  then unassigned. `ledger show <id>` says which, and how.
- `ledger assign [--dry-run|--apply] [--ws W]` turns epic and rule matches into explicit links on
  the topmost items, printing each change and why. Ambiguous cases are printed as proposals and
  never applied.
- `ledger summary --ws <ws> [--since 2026-10-19] [--markdown]` drafts a stand-up from the ledger:
  shipped, in review (and waiting on whom), waiting on others, decisions, blocked, next. `--all`
  does it per workstream, then the unassigned. The default since is this Monday.
- `ledger list --ws <ws|unassigned>` filters; `ledger lint` flags evidence that disagrees, a child
  tagged differently from its parent, redundant tags and a growing unassigned count.

## Your own MCP servers: a site settings file

`orchestrator/settings.json` only names tools every install can have: the Slack plugin, the
claude.ai Slack connector, `gh` and `git`. Your own MCP servers (issue tracker, wiki, CI,
metrics) are still covered, because `bosun guard` asks before any MCP tool whose name has a
write verb and auto mode decides on the rest. To let the orchestrator use their read tools without
a prompt, or to name their write tools explicitly, keep a settings file of your own outside the
repo:

1. `cp orchestrator/settings.json ~/.config/bosun/orchestrator-settings.json`
2. Add the read tools to `permissions.allow`, for example `"mcp__my-tracker__search_issues"` and
   `"mcp__my-wiki__get_page"` (a bare `"mcp__my-metrics"` allows every tool of a read-only
   server), and any write tools to `permissions.ask`.
3. Point bosun at it in `bosun.env`:
   `BOSUN_ORCHESTRATOR_SETTINGS="$BOSUN_CONFIG_DIR/orchestrator-settings.json"`.

The file replaces `orchestrator/settings.json` rather than adding to it, so keep its rules and the
`hooks` block that runs `bosun guard`. `bosun doctor` fails without that hook and warns
about any rule of `orchestrator/settings.json` the file lacks (for example one added after you
copied it). The orchestrator picks the file up at its next launch.
