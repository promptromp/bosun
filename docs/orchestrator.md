# Orchestrator

The orchestrator is the Claude Code window that runs your chores. It reads Slack and your pull requests, records
each piece of work in the [ledger](ledger.md), hands tasks to the repo windows, follows up with the people you are
waiting on, and asks you before anything goes out under your name. This page says what it does each day, what it
hands to repo windows, and how to steer it.

## How it differs from a repo window

- **A fresh conversation every day** (`orchestrator-YYYY-MM-DD`): `refresh` restarts it once it is idle the next
  morning. What carries over between days lives in the ledger, not in the chat.
- **Its own permissions**: `BOSUN_ORCHESTRATOR_FLAGS` (default `--chrome --permission-mode auto`) plus
  `orchestrator/settings.json`, never `--dangerously-skip-permissions`. See [Permissions](permissions.md).
- **Its own instructions**: `orchestrator/policy.md` as an appended system prompt, and the `/bosun:chores morning|
  check-in` command. With `BOSUN_KNOWLEDGE_ENABLED=1` the prompt ends with the
  [ontology's rules](knowledge.md#in-the-agents-hands): look a fact up before asking you, and record what you state
  as standing. `bosun chores --dry-run` gets them too.
- **Only the connectors you name** in `BOSUN_ORCHESTRATOR_CONNECTORS` (empty: none), such as `claude.ai Slack` and a
  tracker ([Connectors](configuration.md#connectors-what-each-window-may-reach)). A name it cannot find at launch is
  left out, and `bosun alert` tells you which.
- **A folder Claude Code already trusts** (`BOSUN_ORCHESTRATOR_DIR`, default your repos folder), since a trust prompt
  would stop an unattended start; `bosun doctor` checks it.

It may hand tasks to repo windows whatever agent runs in them, Claude Code, [Codex](configuration.md#running-repo-sessions-on-codex)
or Antigravity.

<details class="note" markdown>
<summary>What it may read of bosun itself</summary>

The orchestrator may read bosun's install folder and its versions folder, and only read them: `Read(...)` rules
are added to its settings' own `allow` list (a site's settings file keeps its rules), as `bosun update` asks it to
re-read files there and Claude Code asks first before a read outside its working directories.

</details>

## The chores

Each day's conversation starts with `/bosun:chores morning`, and the check-ins run `/bosun:chores check-in`. The
procedure is `orchestrator/plugin/commands/chores.md`, in the same steps:

- **0. Orient:** record the run (`ledger mark-run`, which also prints where the Slack scan starts), read `bosun
  context`, and the reports repo windows filed since the scan window began.
- **1. Slack:** mentions and DMs in the scan window. Each message is checked against the ledger (`seen`) and
  classified as `pr-review`, `td-review`, `my-pr`, `mention` or `fyi`.
- **2. Review requests:** it reads their state on GitHub. If you already reviewed, it marks them `reviewed`;
  otherwise it hands them to the repo window, which posts the review with inline comments and approves when
  nothing blocks merging, or proposes opening a window for it.
- **3. Tech design reviews:** it reads the Confluence page, drafts comments, and proposes posting them.
- **4. Your open pull requests** (found with `bosun my-prs`, checked with `gh` and `bosun pr-threads`):
  - changes requested: the repo window pushes fixes and answers every thread that waits for you; once none is
    left, the orchestrator proposes re-requesting review, with the thread counts per round
  - approved and green: it proposes a squash-merge
  - waiting too long: it proposes a nudge in the original review-request thread
- **5. Outreach** (what we wait on from other people): it proposes the message for new drafts, checks sent ones for
  a reply, hands replies to the window holding the work, and proposes nudges. See
  [Waiting on someone](#waiting-on-someone-outreach).
- **6. Mentions:** new asks appear under FYI with a one-line summary, and a draft reply when one fits. A mention that
  asks us to do something becomes a proposed task, with the command that would hand it over; nothing is handed
  over before you say so.
- **7. Tasks and tickets:** proposed tasks wait for your yes ([Tasks and links](#work-we-take-on-tasks-and-links));
  accepted ones are handed over, and their pull requests linked. Tickets of open work are read with the tracker's
  tools, and where a ticket and the work disagree, the exact change is proposed
  ([External tickets](#external-tickets)).
- **8. Report:** the reply is grouped as *Needs your approval* (numbered, with exact text, including earlier proposals
  you have not answered and [learning](learning.md#learning)'s), *Handled automatically* and *FYI*. You answer "1
  and 3 yes, 2 skip". On a Monday morning it also offers a stand-up draft per active
  [workstream](#workstreams-what-an-item-is-for).

With `BOSUN_ORCHESTRATOR_MORNING_CHORES=0` the day's conversation starts idle instead, and the chores run only when
you or a check-in ask for them.

<details class="note" markdown>
<summary>The Slack scan window</summary>

A check-in scans Slack since the last run. A morning scans the last `BOSUN_SLACK_LOOKBACK_HOURS` (24), or from the
last scan if that was longer ago (a failed morning, days away), but at most `BOSUN_SLACK_MAX_LOOKBACK_HOURS` (96)
back. Changes requested on your pull requests are answered thread by thread: every thread that waits for the author
in a fresh `bosun pr-threads --awaiting-author`, any round.

</details>

### Which repositories and pull requests

**The repo mask.** `BOSUN_REPO_MASK` limits the orchestrator to matching repositories: a space-separated list of
`owner/repo` glob patterns, such as `my-org/*` or `my-org other-org/one-repo` (a bare owner means all its repos;
empty means every repo). It is enforced in code, not just the prompt: `bosun my-prs` filters by it, the ledger
refuses `review:`, `mypr:` and `ghissue:` items outside it, and `bosun context` only maps matching repos.

**Your pull requests.** `bosun my-prs` finds your open pull requests with activity within `BOSUN_MY_PR_WINDOW`
(default `48h`). Pass `--since 7d`, `--since 2w` or `--since 2026-09-01` for a wider sweep, and `--by created` to go
by creation time instead of activity. Drafts are skipped unless you pass `--include-drafts`. You can also just ask
the orchestrator ("check my PRs from the last week"). Pull requests already in the ledger stay tracked until merged
or closed, whatever the window.

### Check-ins

The `checkin` window sends `/bosun:chores check-in` every `BOSUN_CHECKIN_MINUTES` (60), but only when the
orchestrator has been idle for 5 minutes and you have not typed in tmux recently, so it never interrupts a
conversation, and only when something is new since the last one: events not yet delivered, Slack messages the
watcher found, a repo window's report, an outreach whose wait crossed a business day, or a new watchdog alert.
Otherwise it logs `skip: nothing new since HH:MM`, at most `BOSUN_CHECKIN_MAX_QUIET_HOURS` (4) in a row, so the
chores still run. Every check-in makes the orchestrator re-read its whole conversation, which is why quiet hours
skip it.

A permission prompt or question left open in the orchestrator pauses the check-ins until you answer it. The
[watchdog](watchdog.md#watchdog) alerts you when it, or any other Claude window, has waited
`BOSUN_ALERT_WAITING_MINUTES` (30).

<details class="note" markdown>
<summary>More on when a check-in goes out</summary>

- `shell` counts as idle (the turn is over and only a background command still runs, such as a wait for a review
  to settle): the check-in goes in ahead of that command's completion notice, and the 5 minutes count from the end
  of the turn.
- The first check-in comes one interval after the window starts, since the orchestrator begins the day with the
  morning chores.
- A check-in held back only by timing (the orchestrator busy or idle too briefly, or you typing) stays due: the loop
  retries it every tick (`BOSUN_EVENTS_INTERVAL_SECONDS`, or every few minutes with events off) until it goes out,
  logging the skip once, so a busy morning never pushes it a whole interval later. Events keep flowing while it
  waits.
- What counts as new is decided by `lib/checkin_gate.py`, with no Claude call: besides the above, a Slack watcher
  that is off, failing or stalled counts (the check-in then scans Slack itself), and so does an outreach whose
  `expect_by` passed. `BOSUN_CHECKIN_MAX_QUIET_HOURS=0` turns the gate off.

</details>

## Delegated tasks and folder trust

The orchestrator hands work to repo windows with `bosun delegate <type> --id ... --ask ...`. The task's text is
built from `orchestrator/tasks/`: one file per task type, with what the repo window may and may never do and what
the orchestrator does afterwards, plus `_common.md` with the rules every task shares. The orchestrator supplies only
the facts (the ledger id, the link, who asked, a one-line ask, and your instructions in `--details`), so the rules
cannot drift. `bosun delegate --list` shows the types:

| Task | The repo session may | It never |
|---|---|---|
| Address review comments on my PR | push new commits to the PR branch; reply in the review threads it addressed (every thread awaiting the author in a fresh `bosun pr-threads --awaiting-author`, any round) | resolve threads, re-request review, merge, post in Slack, reuse an earlier draft or file for a "review addressed" message |
| Investigate or answer a question | write findings to a local markdown file | post anything |
| New work for me | work in a git worktree, push a new branch, open a draft PR | request reviewers, mark the PR ready, merge, post in Slack |
| Review someone else's PR | post one review with inline comments; APPROVE if nothing blocks merging, otherwise COMMENT; skip a PR that is merged, closed or already approved by someone else, unless the ask names something specific | request changes, push to the PR branch, merge, post in Slack |

Asking for review stays with you: the orchestrator proposes marking the pull request ready (a draft does not notify
code owners) and the Slack message, with the exact text. No task merges, force-pushes, pushes to the default branch
or posts in Slack, Jira or Confluence. The orchestrator hands over `review` and `fix-my-pr` tasks on its own; other
work (a Slack ask for a code change, say) only once you ask or approve, and so does handing an item over again
before its window reported, or moving it to another window. Each window reports with `bosun ledger report`.

These rules live in the task's text: a repo window follows them, but neither auto mode nor
`--dangerously-skip-permissions` technically stops it from doing more. To change a rule, edit the task file;
`tests/smoke.sh` checks that its ledger steps are valid and that this table matches.

**Your own rules.** To adapt the orchestrator to your team without forking, put files in your config folder:
`policy.md` is appended to the orchestrator's policy; `tasks/<type>.md` adds a task type (in the format of
`orchestrator/tasks/`; a file named like a built-in type replaces it); and `tasks/_common.md` adds rules to every
task (the built-in shared rules always stay). `bosun doctor` lists the ones in use.

**Folder trust.** Claude Code shows a trust prompt the first time it opens a repository (trusting a parent folder
does not cover it), and has no setting or flag to skip it; only headless `-p` runs skip it. An unattended start
would stop there. `bosun trust <dir>` records the same entry each agent in use writes when you accept the prompt
(Claude Code: `hasTrustDialogAccepted` in `~/.claude.json`; Codex: `trust_level` in `~/.codex/config.toml`; both
internal formats), for git repos inside the repo mask. `bosun send --new|--start` (and so `delegate --new|--start`)
call it for their folder (`BOSUN_TRUST_MASKED_REPOS=1`); outside the mask they fail at once with the command to
run. `bosun doctor` checks every window's folder.

## Waiting on someone: outreach

Work often stops on someone else: a product decision, another team's contract, a vendor's file format, a
reporter's environment. Such an ask is an **outreach** item, so it does not live only in chat, a pull request
description or a Slack reply.

You never type its commands. Tell the orchestrator "ask Dana whether we can drop the shared password", "email the
vendor to clarify the format and wait for them", "ping Priya again", "Marco answered in the thread, go ahead with b",
and the policy turns each into the steps below. Repo windows record outreach for decisions they cannot make, and
never send it; the orchestrator proposes the message. Questions for you are not outreach: repo windows ask you
directly when you are at the window, or put the decision in their report.

| Field | |
|---|---|
| id | `out:<parent-id>/<id>` for the work item it blocks, or `out:<YYYY-MM-DD>/<id>` (the UTC day) when it belongs to none; `reach-out` prints it first. Older ids end in a number (`out:<parent-id>/1`) and stay valid |
| `channel` | `slack`, `email`, `jira`, `github`, `confluence` or `other` |
| `purpose` | `question`, `decision`, `clarification`, `approval`, `review`, `info-ack` (they confirm they saw it) or `fyi` (one-way: no reply expected, so it is done once sent) |
| `to`, `subject`, `options`, `details`, `expect_by` | whom, what (one line), the choices, context, and an optional date |
| `messages` | every message sent for it, the first and each follow-up: its reference (permalink, comment URL or message id), channel, and when it was sent |
| `answer`, `reply_ref` | the reply in one line, and the reference to it (the evidence) |

Its states: `draft` (recorded), `proposed` (the message waits for your approval), `sent` (each later message is a
follow-up, counted as a nudge; an `fyi` item goes straight on to `applied`), `replied` (with the reply as evidence),
`applied` (the reply reached the window holding the work, or you). `no-reply` gives up after the last nudge, and
`dropped` is the way out from any state. [The ledger](ledger.md#each-item-moves-through-states) draws it.

- **The work waiting on it stays open.** `ledger list` shows `waiting on out:.../7k2m9q (slack to Dana, 2d)` on the
  parent, and the ledger refuses to finish a parent while its outreach is open, unless `--force-open-outreach` with a
  note saying why. A pull request that merged or closed is a fact, not a choice, so those states go through, and the
  note names the outreach still open; its open review requests are dropped with it, since nobody owes that review
  any more.
- **Replies are matched, not classified again.** `ledger seen <permalink>` names the open outreach whose Slack
  thread (or DM) a new message is in, so the reply is recorded on it instead of becoming a new mention. On other
  channels the chores read the ticket, pull request or page comments, or ask you (email).
- **Nudges are proposals.** After `BOSUN_NUDGE_AFTER_BUSINESS_DAYS` (1) without a reply, and below
  `BOSUN_MAX_NUDGES` (2), the chores propose a follow-up in the same conversation; nothing is sent on its own. The
  watchdog does not alert on outreach.

<details class="note" markdown>
<summary>The commands, review nudges, times and duplicates</summary>

- **The commands.** `ledger propose` moves an outreach to `proposed` (rejecting the proposal moves it back), `ledger
  sent --ref` records a message (several ids record one message on each, such as one nudge for several questions),
  `ledger replied --evidence --summary` the reply, and `bosun delegate --update <out-id>` hands the reply to the
  window holding the parent and marks it `applied`. `ledger fold <out-id> --into <other-id>` drops it when its
  questions go into another message; `--note 'duplicate of <other-id>'` when it asks exactly what another asks. The
  channels are one table in `lib/ledger.py`. No item id looks like a date: each starts with its kind's prefix.
- **Matching replies.** When several open outreach items share a thread, `ledger seen` names all of them, latest
  message first, so the orchestrator picks the one the reply answers (`--class outreach`).
- **One conversation, one item.** `ledger reach-out` warns (it does not refuse) when the parent already has open
  outreach to the same person, so an addition goes out as a follow-up on that item instead of a second one waiting
  in the same thread. `ledger upsert <out-id> --field subject=... --field options='a | b'` keeps the record in line
  with the message actually sent.
- **Pull requests waiting on reviewers** have no fixed count of nudges (`BOSUN_MAX_REVIEW_NUDGES`, 0 = no cap): one
  is nudged each business day while the next move is the reviewers', after a fresh check of the pull request, its
  threads, the conversation and the ledger, at most once per reviewer per day. After
  `BOSUN_REVIEW_ESCALATE_AFTER_NUDGES` (3) unanswered nudges the chores also propose another reviewer. `bosun context`
  prints these limits.
- **Times are when it happened, not when it was recorded.** The wait counts from the latest message's time. `ledger
  sent` and `ledger replied` take it from `--at` (ISO 8601 with an offset, or local `YYYY-MM-DD HH:MM`; never in the
  future), else from a Slack permalink's own time, else now; `sent --at` with a reference already recorded corrects
  that message's time. The history entry keeps the recording time and says where the other came from
  (`happened_at`, `time_from`).

</details>

## Work we take on: tasks and links

Work we decide to do that has no pull request or page of its own yet is a **task**: what a colleague asks of us in a
reply or a mention ("yes please, fix the other callers too"), what you tell the orchestrator to do, a follow-up a
repo window finds. It is spawned from the item it came from, so its origin is a link rather than a note, and it is
handed over under its own id.

| State | |
|---|---|
| `proposed` | `ledger spawn [<from-id>] --title --ask [--via <permalink or 'user: ...'>] [--requester] [--repo] [--blocks]` records it; an exact repeat is unchanged, and a similar open task from the same source is a warning |
| `accepted` | only on your word: `--evidence 'user: <what you said>'` is required, so a colleague's message never accepts work; it closes the task's pending proposal |
| `delegated` | `bosun delegate new-work --id <task id>` (it refuses a task you did not accept, and an outreach id as the work item) |
| `done` | what it produced landed (its pull request merged; `ledger tree` says so), or its report answered the ask |
| `declined`, `dropped` | you said no; or it went away: `ledger fold <id> --into <other-id>` when its work goes on as part of another item, `--note 'duplicate of <id>'` when it was the same item twice (both record the link) |

Tell the orchestrator "go ahead and do it" about an outreach reply or a mention, and it spawns the task with your
words as `via`, accepts it and hands it over. A colleague's request ends up as a proposed task under *Needs your
approval*. Repo windows spawn the follow-ups they find, and never start them.

**Links** are stored once, on the item they start from, and read both ways: `spawned_from`, `produces`,
`blocked_by`, `duplicate_of`, `folded_into`, `tracks`, `part_of` and `relates_to`
([The ledger](ledger.md#links-where-work-came-from-and-what-came-of-it) says what each means). `ledger link` refuses
unknown ids, links to the item itself and cycles; `ledger tree <id>` shows where an item came from and what came of
it, with states. Design: [design/lineage.md](design/lineage.md).

<details class="note" markdown>
<summary>Folding, migrating links, and what <code>ledger list</code> shows</summary>

- **`ledger fold <id> --into <other-id> [--note ...]`** records that an item's work went into another (a small task
  done in another task's pull request, an outreach draft said as a follow-up on an existing conversation): it links
  `folded_into`, withdraws the item's pending proposal (it was neither carried out on the item nor rejected), and
  closes the item in its kind's way out (`dropped`; `dismissed` for kinds without it) with the note `folded into
  <other-id>: ...`. It refuses an item with open outreach or blockers as `transition` does (`--force-open-outreach`),
  and a finished target unless `--force-closed-target` (the work already landed with it). If the target is with a
  repo window, it prints, and does not run, the `bosun delegate --update` that carries the folded item's title and
  ask there. A drop note "merged into <id>" or "folded into <id>" records `folded_into` too. `prune` keeps a folded
  item while its target is open, and with it what the folded item links to: prune never leaves a kept item with a
  link to a pruned one.
- **What lists show.** `ledger show` lists links under `derived`, with the lineage tree and hints; `ledger list`
  shows `from <id>`, `-> <id>`, `tracks <id>`, `folded into <id>` and `absorbs <id>` (`folded_into` read back). An
  outreach's `parent` stays a field and reads as a computed `blocked_by`. `ledger reports` prints the commands that
  record a pull request a report names and link it to the work.
- **`ledger migrate-links [--dry-run]`** moves ad hoc provenance into links once (an `origin` field naming an item,
  "merged into <id>" and "duplicate of <id>" drop notes, and a `duplicate_of` recorded from a "merged into" note,
  which becomes `folded_into`); it is safe to run again.

</details>

## External tickets

A Jira issue or GitHub issue that tracks our work is a **ticket** item (`jira:EXP-123`, `ghissue:acme/exporter#7`),
a read-only mirror. Tell the orchestrator "this PR is for EXP-123" and it runs `ledger link <pr> tracks
jira:EXP-123`, which creates the ticket. The orchestrator reads it with the tracker's read tools and records what it
shows with `ledger ticket-sync <id> --status '<status>' [--category ...] [--title ...] [--url ...] [--assignee ...]`;
bosun never changes a ticket itself.

A ticket's state is a category: `todo`, `in-progress`, `in-review`, `done` or `cancelled` (`unsynced` before the
first read), from your overrides, the built-in status names, or `--category`. `ledger tickets [--all] [--mismatch]`
lists the tickets of open work and where a ticket and the work disagree: closed while the work is open, still open
after the pull request merged, `todo` while the pull request is in review, never or long ago synced. Each is a
proposal under *Needs your approval* with the exact transition, because a tracker's write tool asks for permission
and would stall an unattended check-in. A ticket outside your own projects (another team's, such as an
infrastructure request in their intake state) is theirs to move: its mismatches print as `fyi:` lines, never as
proposals, and the console does not count them under *Needs you*.

Status names your tracker uses that the built-in names miss, and the projects that are yours, go in `bosun.env`:

```sh
BOSUN_TICKET_STATUS_MAP="jira:Ready for Review=in-review; jira/EXP:On Hold=todo"
BOSUN_TICKET_STALE_HOURS="24"                      # a ticket of open work read longer ago is flagged
BOSUN_TICKET_OWNED_PROJECTS="EXP acme/exporter"    # empty: every project is yours
```

## Workstreams: what an item is for

A **workstream** (`ws:<slug>`) is a time-bound effort you drive, such as "Widget search". It groups ledger items for a
weekly stand-up, stats and, later, the ontology. It is not a Jira epic (it may name one) and not a product area (it
is part of one). Design, research and examples: [design/workstreams.md](design/workstreams.md).

- **Make one:** `ledger ws add <slug> --title ... [--epic WID-100] [--channel <Slack channel id>] [--repo ...]
  [--started/--ended/--target <date>] [--area <ontology entity>] [--parent <ws>] [--alias ...]` creates one or updates
  its fields. `ws list`, `ws show`, `ws set-state <ws> active|paused|done --note ...` and `ws alias` cover the rest.
  Any `<ws>` argument takes the id, the slug or an alias. A workstream can sit under one other, two levels at most.
- **Put work in it:** `ledger link <id> part_of <ws>` puts an item in its one workstream; `--move` moves it. Tag the
  root: what was spawned from it, what it produced, its outreach and what was folded into it inherit the workstream.
  Nothing is copied.
- **Draft the stand-up:** `ledger summary --ws <ws> [--since 2026-10-19] [--markdown]` drafts it from the ledger:
  shipped, in review (and waiting on whom), waiting on others, decisions, blocked, next. `--all` does it per
  workstream, then the unassigned. The default since is this Monday.

<details class="note" markdown>
<summary>How an item finds its workstream, and the checks</summary>

- **Resolution order:** an item's own `part_of`, then one it inherits, then a tracked ticket whose epic is the
  workstream's (`ticket-sync ... --parent <key>` records a ticket's epic or parent), then your rules in
  `$BOSUN_CONFIG_DIR/workstreams.json` (repo globs, title words, origin Slack channel, kinds; see
  `examples/generic/workstreams.json`) and the workstream's own channel, then unassigned. `ledger show <id>` says
  which, and how.
- **`ledger assign [--dry-run|--apply] [--ws W]`** turns epic and rule matches into explicit links on the topmost
  items, printing each change and why. Ambiguous cases are printed as proposals and never applied.
- **`ledger list --ws <ws|unassigned>`** filters; **`ledger lint`** flags evidence that disagrees, a child tagged
  differently from its parent, redundant tags and a growing unassigned count.
- **`ledger validate`** checks every rule of the whole ledger: [The ledger](ledger.md#reference).

</details>

## Your own MCP servers: a site settings file

The orchestrator reaches only the servers and connectors `BOSUN_ORCHESTRATOR_CONNECTORS` names, so name yours there
first (`"claude.ai Slack, plugin:acme-tracker:tracker, acme-wiki"`). A settings file cannot widen that, as bosun's
list replaces any `allowedMcpServers` it holds (with `all` there is no list of bosun's, and the file's applies;
`bosun doctor` says which).

`orchestrator/settings.json` only names tools every install can have: the Slack plugin, the claude.ai Slack
connector, `gh` and `git`. Your own MCP servers (issue tracker, wiki, CI, metrics) are still covered, because `bosun
guard` asks before any MCP tool whose name has a write verb, and auto mode decides on the rest. To let the
orchestrator use their read tools without a prompt, or to name their write tools explicitly, keep a settings file of
your own:

1. Copy bosun's: `cp ~/.local/share/bosun/orchestrator/settings.json ~/.config/bosun/orchestrator-settings.json`
   (from a checkout of your own: its `orchestrator/settings.json`).
2. Add the read tools to `permissions.allow`, such as `"mcp__my-tracker__search_issues"` and
   `"mcp__my-wiki__get_page"` (a bare `"mcp__my-metrics"` allows every tool of a read-only server), and any write
   tools to `permissions.ask`.
3. Point bosun at it in `bosun.env`: `BOSUN_ORCHESTRATOR_SETTINGS="$BOSUN_CONFIG_DIR/orchestrator-settings.json"`.

The file replaces `orchestrator/settings.json` rather than adding to it, so keep its rules; `bosun doctor` warns
about any rule of bosun's file that yours lacks (one added after you copied it, say). The orchestrator picks the file
up at its next launch.

<details class="note" markdown>
<summary>The guard's hook block in an old copy</summary>

The file needs no `hooks` block for `bosun guard`: bosun adds the guard at launch, from the bosun that launches the
window, whatever the settings file. A file copied before that still has the block, which runs the guard twice, and
the two need not be the same version (a checkout's guard and the installed one's), so they may not decide alike;
drop it. `bosun doctor` warns about that block.

</details>

## After an update: the windows brought along

Once [`bosun update`](updating.md) has switched to the new version, it brings each window along. In most updates
nothing a window was launched with changed, and it is left alone: the next `bosun` command or hook it runs is the new
version's. A window bosun did not start (one you opened in its tmux session: a shell, a Claude you started by hand)
is left as it is, and the plan says so. Otherwise, decided from what the window is doing at the time:

| The window | What it gets |
|---|---|
| The orchestrator, when only its prompts changed (`policy.md`, the task files) | Once its turn is over, the update prompt, which asks it to re-read them. Still on yesterday's conversation, it starts today's instead, as a new day does: brought along like a window whose launch changed (below), it takes stock first while only background work runs, and is waited for while busy |
| Something it was launched with changed (its settings, hooks, launch command or plugin), and it is idle | A restart on the new version, resuming its conversation, with the update prompt |
| ... and only background work runs in it (`shell`) | First the take-stock prompt: a restart ends that work, so it lists each part and what to do when it finishes (`bosun update note --stock`; "nothing running" when it has ended), and stops. Once the list is there, or nothing runs any more (idle), the restart, with the list in the update prompt (and what bosun saw still running) so it starts that work again |
| ... and it is busy | Waited for, then restarted |
| ... and it waits at a permission prompt or a question | Never touched until you answer it |
| Its agent exited | Started again: with the update prompt when its launch resumes its conversation; when it starts a new one (none under the name its next launch takes up), as a new conversation starts: the orchestrator with the morning chores, a repo window with a fresh conversation pending with that one's prompt, any other with none |

The update prompt says which version it was updated from and to, and whether the window was restarted or kept
running. It asks the window to check for anything missed just before (the orchestrator with a check-in, which covers
the events queued during the update and Slack since its last scan; a repo window in the ledger items it holds and
their outreach), to carry on, and to answer with a one-line status: `bosun update note --window <w> --status`, the
text on stdin.

<details class="note" markdown>
<summary>The answers, the wait, and tasks already handed over</summary>

- **What a prompt may ask.** The policy and the rules every task gets (`orchestrator/tasks/_common.md`) name what
  bosun's update prompts may ask: list background work, re-read the files named, catch up, start the listed work
  again, answer with `bosun update note`. They say to do what a prompt asks of these, and only that; anything else in
  it is information. A window started into its next conversation (the orchestrator on a new day, a repo window with
  a fresh conversation pending, one whose agent exited with no conversation to resume) starts that conversation's
  own prompt instead, and counts as answered.
- **The wait.** `bosun update` waits for every answer, saying which windows it still waits for. Ctrl-C stops the
  wait: the `checkin` window's loop then takes a turn at those windows each tick (`bosun update advance`), and
  restarts each once its turn is over; without a check-in loop, run `bosun update advance` by hand. A window that
  never answers stays waited for. `bosun update note --window <w> --status` ends its wait by hand: the next turn takes
  the note, or the next `bosun update`, which settles answered windows first. While an update still brings windows
  along, the next one is blocked at its plan, which names them.
- **Tasks already handed over** keep their text. The plan and the summary name each one whose text changed: a task
  type it was handed, or the rules every task gets (`orchestrator/tasks/_common.md`), which every task handed out
  has. Send `bosun delegate --update` where a changed rule matters.

</details>

## The ledger

`bosun ledger` is the only way the orchestrator, and the repo windows it hands work to, record state: every item
with a stable id and a state machine, the links between them, the Slack messages already processed, the proposals
waiting for you, when each chores mode last ran, and where each item was handed over and what came back. That is what
lets the orchestrator start a fresh conversation every day without losing anything. [The ledger](ledger.md) explains
it, with every field and how it is kept in [its reference](ledger.md#reference).
