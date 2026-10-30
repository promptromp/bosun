---
description: Scan Slack mentions, review requests and my open PRs; update the ledger; propose actions
argument-hint: morning | check-in
---
Run the bosun chores in **$ARGUMENTS** mode, following the orchestrator policy.

## 0. Orient

1. `bosun ledger mark-run $ARGUMENTS`. It prints when this kind of run last happened and the
   `scan from` time.
2. `bosun context`. It shows config (scan lookback, nudge rules, my GitHub login), tmux windows
   with Claude status, the `owner/repo` -> directory map, open ledger items and pending proposals.
3. Scan window: from the `scan from` time that `mark-run` printed, until now, in both modes. In
   `morning` mode it reaches back to the last scan when that was more than `slack_lookback_hours`
   ago (a failed morning, days away), up to a maximum. It includes some overlap; the `ledger seen`
   check below makes overlap harmless.
4. `bosun ledger reports --since <start of the scan window>`: every report repo sessions filed
   on delegated items since then, and what I said in their windows (`user said`). Act on each
   report in the step for its item below, as its task type's followup says (`bosun delegate
   --list`), and bring anything under `open:` to me unless I already answered it in the window. A
   report that names a new PR has a `next:` line: run it (it records the `mypr:` item and links
   it to the work that opened it). Then settle the task's state as the policy says (a task's PR):
   a finished report with nothing produced and nothing open makes the task `done`.

## 1. Slack

Identify me with the Slack read-user-profile tool (no user id). Search Slack (public, private,
DMs, group DMs) within the scan window for messages that mention me by @-mention or by name, or
that are direct messages to me. Read the thread when the ask is unclear. (An `[bosun events]`
prompt with Slack lines runs this step for exactly the permalinks it lists, with no search.)

For each message:
- `bosun ledger seen <permalink>`. If it is already processed, skip it. If it names an
  outreach item, the message is in the conversation where we asked: handle it in step 5 with that
  item (`--class outreach --item <out-id>`), not as a new mention. If it names several (they share
  the thread), read the message and pick the one it answers; ask me when unclear.
- Classify:
  - `pr-review`: asks me to review or approve someone else's PR
  - `td-review`: asks me to review a tech design (usually a Confluence page)
  - `my-pr`: about one of my own PRs, e.g. a reviewer replying
  - `mention`: needs a reply or action from me that is none of the above
  - `fyi`: no action needed (bots, CI, announcements, already resolved in thread)
- Upsert the matching item (`bosun ledger kinds` shows each kind's id format and Slack class),
  then `bosun ledger record-source <permalink> --class <class> --item <id>`. For `fyi`, record
  the source without `--item`. The ledger rejects ids and classes that don't match the kind.
- For `pr-review` and `td-review`, store `--field requester=<name>` and `--field request_permalink=<permalink>`.
  For a `my-pr` review-request message I posted, store `--field review_thread=<permalink>` so
  nudges can go to that thread.

## 2. Review requests (`review:` items)

For every open `review:` item, check GitHub:
`gh pr view <url> --json state,author,title,headRefOid,reviews,reviewRequests,reviewDecision,latestReviews,mergedAt,closedAt`

- Merged or closed: transition to `closed`.
- Already approved by someone else (`reviewDecision` is `APPROVED`, or `latestReviews` has an
  `APPROVED` review when the repo requires none), and the item is `requested` or
  `rereview-requested`: don't delegate. Transition to `dismissed` with the note
  "already approved by <login>", unless the request explicitly asks me to look at something
  specific in the PR (a file, a change, a question). A blanket or automatic review request
  (CODEOWNERS, a team) is not specific. In that case delegate as below and put the specific part
  in `--ask`.
- A `dismissed` review item goes back to `requested` only on a new, unseen Slack message or
  PR comment that asks me for something specific in it; pass it in `--ask` as above.
- I have submitted a review (any review authored by my GitHub login) and the item is `requested`
  or `delegated`: transition to `reviewed` with
  `--field reviewed_at=<time> --field reviewed_sha=<commit>`.
- `reviewed`, and I am in `reviewRequests` again (GitHub re-request after my review) or a new,
  unseen Slack message after `reviewed_at` explicitly asks again: transition to
  `rereview-requested` with `--evidence`.
- `requested` or `rereview-requested`: hand it over with `bosun delegate review` (for a
  re-review, `--details 'focus on changes since <reviewed_sha>'`). The session posts the review
  itself and transitions the item to `reviewed`. If a window already runs Claude in that repo,
  transition to `delegated` first, then delegate (`--window`); if delegate fails, transition back
  to `requested` with the error as the note. Otherwise propose the `--start` (a `no-agent` window
  in that repo) or `--new` command (`ledger propose`), and do the same once I approve. A re-review
  after `reviewed` is a new hand-over and needs no `--again`. If delegate refuses because the
  item still awaits a report from its window, send what changed with `delegate --update`, or
  propose handing it over again (`--again`) or moving it (`--update <id> --window <window>`).

## 3. Tech design reviews (`td:` items)

For `requested` or `rereview-requested` items, read the page and its comments with the Confluence
tools. Draft concise review comments yourself, then transition to `prepared` with a note that
summarizes them. Propose posting them (`ledger propose`), with the exact comment text in the plan.
When I approve and they are posted, transition to `commented` with `--field commented_at=<time>`.

## 4. My open PRs (`mypr:` items)

Find my open PRs with `bosun my-prs`, which applies the repo mask, skips drafts and uses the
`my_pr_window` from the context (PRs with activity in the last 48h by default). If I ask for a
wider sweep, pass a window, e.g. `bosun my-prs --since 7d`, or `--by created` for PRs opened
in the window. Don't search GitHub for them another way. Upsert a `mypr:` item for each, then also
re-check every open `mypr:` item already in the ledger, whatever its age (it may have merged or
gone quiet and need a nudge). Then run `bosun review-watch`: it lists each review on an item in
`addressing`, `awaiting-review` or `approved` that came after the item's last hand-over (or
report) and still needs acting on, with its next step (for `addressing`: the threads go to the
window with `bosun delegate --update`). For each:

- `gh pr view <url> --json state,reviewDecision,latestReviews,reviewRequests,mergeable,mergeStateStatus,statusCheckRollup,commits,updatedAt`
- `bosun pr-threads <url>` lists unresolved review threads, which of them await me (my reply
  is not the last comment) and the threads per review round; `--awaiting-author` lists only those.
  An automated reviewer the site relies on (`(automated)`, with its latest verdict) counts like a
  person: its threads are review comments to address. Its review is never an approval.
- If a background reviewer wait you started for the PR is still running, list it as "waiting for
  the review to settle" and don't act on it yet.
- Map to a state and act:
  - merged or closed: `merged` / `closed`.
  - `addressing` (a repo session is fixing review comments): leave it while the session works.
    When its report says it pushed, run `bosun pr-threads <url> --awaiting-author`: threads it
    lists, or a review newer than the report, go to the window with `bosun delegate --update`
    (policy: Saying my review comments are addressed). Only when it lists nothing, propose
    re-requesting review (GitHub, and the `review_thread` Slack thread if known) with the exact
    message, written from that output with the thread counts per round: a Slack message is
    outreach on the PR (`--purpose review`), proposed with `--send slack` (policy: Outward calls).
    Once that is done, transition to `awaiting-review` with `--field last_push_at=<time of the
    pushed commit>`.
  - a review requesting changes newer than `last_push_at` (any, if it is not set), or threads that
    await me (`pr-threads --awaiting-author`; a thread I answered does not count): `changes-requested`. If a
    window already runs Claude in that repo, transition to `addressing`, then
    `bosun delegate fix-my-pr --window <window>` (back to `changes-requested` if it fails).
    Otherwise propose the `--start` or `--new` command for it. New review comments on an item
    that is still `addressing` go to its window with `bosun delegate --update`, not a second
    task (delegate refuses that until the session reported or the item reached
    `awaiting-review`).
  - `APPROVED` by a person (not an automated reviewer's review), checks passing and mergeable:
    `approved`. Propose a squash-merge with the exact
    command (`bosun ledger propose '<mypr id>' --run 'gh pr merge <url> --squash'`, or
    `... --squash --auto` where the repository allows auto-merge). If `mergeStateStatus` is
    `BEHIND`, it is still `approved`, but first hand the PR to its repo window to take the base
    branch and run the merge once it reports green checks on the new head (policy: merges).
    After a merge, a "merged" note to the reviewers is `fyi` outreach on the PR, proposed the same
    way.
  - otherwise: `awaiting-review`.
- Nudge rule: a PR waiting on its reviewers is nudged every `nudge_after_business_days` business
  days until the next move is mine, with no fixed count (`max_review_nudges`: 0 means no cap).
  Only in `awaiting-review`: never once approved, never for a draft PR, never while the next move
  is mine. Nudge when there has been no review activity for at least `nudge_after_business_days`
  business days since the latest of: PR opened, last push, last review activity, and
  `last_nudged_at`.
  - Check the live state right before proposing, and again right before sending, never from
    memory or an earlier draft:
    - `gh pr view` again: still open, not a draft, not approved, the reviewer still pending, no
      review since the last nudge, and checks not failing (red checks are mine to fix first).
    - `bosun pr-threads <url> --awaiting-author` lists nothing. A thread or question that
      waits on me means the next move is mine: no nudge.
    - The `review_thread` Slack thread and the PR's comments since the last message: no reply
      from the reviewer (a reply, such as "I'll look tomorrow", resets the clock: wait until the
      time they gave, or one business day), and no request from me still unanswered there.
    - The ledger: no open outreach to the same reviewer about this PR that is proposed or sent
      within the last business day, from this item or a combined message
      (`ledger outreach --open`). Reuse that item or conversation; never start a second one.
    If anything changed since the draft (a new push, review, reply, merge), drop or rewrite it.
  - One nudge per reviewer per business day: combine their waiting PRs into one message.
  - After `review_escalate_after_nudges` nudges with no response at all, keep nudging and also
    propose asking another reviewer or the team channel.
  - Propose a short, friendly reply in the `review_thread` Slack thread if known (otherwise a DM to
    the pending reviewers). Include the exact message text. After I approve and it is sent, update
    `--field last_nudged_at=<time> --field nudge_count=<n+1>`.

## 5. Outreach (`out:` items: what we wait on from other people)

`bosun ledger outreach --open` lists them with their messages and how long we have waited.
Repo sessions add `draft` items, so expect new ones on every run.

Before proposing drafts, compare them with each other and with open or sent outreach for the same
parent, recipient or thread:
- A draft that asks exactly what another item asks (the same question twice): keep one and drop
  the other as a duplicate,
  `ledger transition '<out-id>' dropped --note 'duplicate of <kept id>'`.
- A draft whose questions go into another message (two drafts combined into one, or an addition
  to a conversation that already has sent outreach): one message on the kept item, and fold the
  draft into it, `ledger fold '<out-id>' --into '<kept id>'`. An addition to sent outreach is
  proposed there as a follow-up, recorded on the existing item once sent (`ledger sent '<existing
  id>' --ref ...`), unless it is a genuinely separate question.
- When you change a message so it no longer matches its item (e.g. remove a topic another item
  covers), update the record: `ledger upsert '<out-id>' --field subject='...'
  [--field options='a | b']`.
- One message covering several items (a combined question or nudge) is recorded on all of them:
  `ledger sent '<out-id>' '<out-id>' ... --ref ...`.
- `fyi` needs no reply: once sent it is done (`ledger sent` moves it to `applied`).

For each, by state:

- `draft`: draft the message (short, friendly, the question and options; in English unless the
  thread is not) and propose it as the policy says (`ledger propose`, then `ledger sent` once I
  approve and it is sent). Show the channel, recipient and where next to the text, so I can change
  any of them; a change is recorded and re-proposed as the policy says, never sent unapproved. If
  you cannot tell whom to address (a role or team), ask me in the proposal. If you find it was
  already sent (by me or a repo session), record that message with `ledger sent`, with `--at '<when
  it was sent>'` unless its ref is a Slack permalink, so the wait counts from then.
- `proposed`: still waits for my approval; list it again.
- `sent`: check for a reply on its channel (table below). A reply: `ledger replied`, then
  `bosun delegate --update '<out-id>'` to the window holding the parent. Otherwise (no parent,
  or no window holds it) put the reply under **FYI** and transition to `applied` with the note
  "surfaced to the user". A reply that asks us to do something new ("yes please, fix that too")
  is a task: `bosun ledger spawn '<out-id>' --title '...' --ask '...' --via '<reply
  permalink>' --requester '<who>'`, proposed for my approval with the delegate command (step 7),
  and the outreach goes to `applied` with the note "carried by <task id>". Never close outreach
  whose reply led to work (a PR, a delegate proposal) with only a note: the work gets a task. No reply: the nudge rule of step 4 applies, counted from the last
  message (`waiting: N business day(s)`), with fewer than `max_nudges` follow-ups so far
  (outreach with `--purpose review` follows the review nudge rule of step 4 instead, with no fixed
  count): propose a short follow-up in the same conversation, never send it on your own. Before
  proposing it, re-read the conversation and the parent's live state: a reply, a request from me
  still unanswered there, or a parent that moved on (merged, closed, answered elsewhere) means no
  nudge. After the last nudge, propose giving up (`no-reply`) or asking someone else. Mention a
  passed `expect_by`.
- `replied` (not applied yet): hand it over as above. A reply that answers only part of it: as the
  policy says, hand on the answered part and move it back to `sent` with the rest as its subject.

| Channel | Sending, once I approve | Checking for a reply |
|---|---|---|
| slack | the Slack send tool, in the thread or DM, exactly as proposed with `--send slack`; `--ref`: the sent message's permalink | read the thread of each slack ref (a DM: later messages); step 1's `ledger seen` also flags messages in those threads |
| github | `gh pr comment` / `gh issue comment`; `--ref`: the comment URL | `gh pr view <url> --json comments,reviews` or `gh issue view <url> --comments`, and `bosun pr-threads` |
| jira, confluence | the site's comment tool, if configured; `--ref`: the comment URL | read the issue or page comments with the site's read tools |
| email | an email tool, if one is configured; otherwise "you send this" | an email read tool, if configured; otherwise ask me whether a reply came |
| other | I do it ("you send this") | ask me |

## 6. Mentions (`slack:` items)

New `mention` items: transition to `surfaced` and list them under FYI with a one-line summary and
the permalink. If a reply is obvious, offer a draft as a proposal. If it asks us to do something
(a code change or an investigation in a repo, "could you also ..."), that is a task: `bosun
ledger spawn '<mention id>' --title '...' --ask '...' --via '<permalink>' --requester '<who>'`,
proposed with its `delegate new-work` or `investigate --id '<task id>'` command (step 7). Never
delegate under the mention's id, or before I say so. Once I answered, the mention is `done` with a
note naming the task. A mention that is done but still waits on open outreach stays `surfaced`:
the ledger refuses `done` until that outreach is applied or dropped.

## 7. Tasks (`task:` items) and tickets

`bosun ledger list --open --kind task` lists the work we took on or were asked to take on:
- `proposed`: under **Needs your approval**, with the exact delegate command (recorded once with
  `ledger propose '<task id>' '...'`). On my yes: `ledger transition '<task id>' accepted --note
  '...' --evidence 'user: <my answer>'`, then delegate it as step 2 does (`--window` to a window
  running an agent in its repo; otherwise propose `--start` or `--new`). On my no: `declined`.
- `accepted`: hand it over the same way.
- `delegated`: follow it like any delegation (its report, `delegate --update`). Its PR: upsert the
  `mypr:` item and `ledger link '<task id>' produces 'mypr:...'`. Once all it produced landed
  (`ledger tree '<task id>'` says so): `done`.
- A PR of mine from step 4 with no `from` in `ledger list` that carries out a reply or a delegate
  proposal (pending or withdrawn): link it as the policy says under "Work that started without a
  hand-over".

`bosun ledger tickets` lists the tickets of open work and how to read each. Read each with the
tracker's read tools only, then record it: `ledger ticket-sync '<ticket id>' --status '<status
name>' --category '<status category>' [--title ...] [--url ...] [--parent '<its epic or parent
key>']`. `bosun ledger tickets
--mismatch` then names each change to propose: put it under **Needs your approval** with the exact
transition (`ledger propose` on the item it is about); its `fyi:` lines (another team's tickets)
go under FYI only. Never transition, comment on or assign a
ticket during the chores; only after I approved that exact change.

Workstreams: for each root item recorded in this run (a new PR of mine, a task or outreach with no
source), set its workstream as the policy says (`ledger link '<id>' part_of '<ws>'` when it is
clear, a proposal when it is not). In `morning` mode, run `bosun ledger lint` and list its
`propose:` and `warn:` lines briefly under FYI.

## 8. Report

Present the plan as the policy describes. Pending proposals from earlier runs that I have not
answered are listed again under **Needs your approval** (not those `ledger list` marks approved
or carried out; one marked as failed needs a new proposal), each as a short recap I can act on
without scrolling back (see the policy's "Presenting a plan"). Before you list a
decision for me, check `bosun ledger said '<id>'`: never ask what I already answered in a
repo window. Learning proposals
(`learn:` items, which `bosun learn` records with the exact change) are listed there too, with
that change (`bosun learn --status` lists the waiting ones). On my yes: `bosun learn apply '<id>'` (for a change I make myself:
`bosun learn done '<id>'`); on my no: `bosun learn reject '<id>' --note '<why>'`. Never
apply one I have not approved in this conversation. Under **Handled automatically**, list each
call the guard let through under a standing approval since the start of the scan window
(`bosun ledger approvals --auto --since <start of the scan window>`: one line each, item and
what went out). In `morning` mode, end
FYI with one line per outreach still `sent` ("waiting on <who> since <age>: <subject>"). On a
Monday `morning`, also offer a stand-up draft for each active workstream (`bosun ledger ws
list`), from `bosun ledger summary --ws '<ws>' --since <the previous Monday> --markdown`: show
it as a draft under **Needs your approval** (record a proposal with the exact Slack call if I
should post it). In `check-in` mode,
if nothing changed and nothing is pending, reply only: "Check-in: nothing new."
