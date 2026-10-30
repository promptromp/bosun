# Design note: approvals for outward calls

Status: built (2026-10-21), off by default (`BOSUN_APPROVALS_ENABLED=0`). Tracked in
[ROADMAP.md](../../ROADMAP.md#approvals-for-outward-calls).

## Why

The orchestrator's settings have `ask` rules for every outward tool: Slack sends, reactions and
canvases, Jira and Confluence writes, `gh pr merge|ready|edit|comment|review|close|update-branch`, `gh api`,
`git push`. An `ask` rule beats auto mode, so each such call opens a Claude Code permission prompt.
In the first week of use that was over a hundred prompts, nearly all a second approval of something
I had already approved in chat ("1 and 3 yes"). And a prompt waits forever: two approved re-review
messages sat at their prompt overnight and kept the morning restart from running.

## Claude Code behaviour this relies on (verified on 2.1.306, headless)

- A PreToolUse hook's `allow` does not override a settings `ask` (or `deny`) rule; it only replaces
  the default prompt when no rule matches. A PreToolUse `deny` always wins.
- A **PermissionRequest** hook answering `{"hookSpecificOutput": {"hookEventName":
  "PermissionRequest", "decision": {"behavior": "allow"}}}` approves a call an `ask` rule (or a
  PreToolUse `ask`) would prompt for, with no prompt. `"behavior": "deny"` with a `message` denies
  it, and the model sees the message.
- Both hooks get `session_id`, `transcript_path`, `cwd`, `permission_mode`, `tool_name` and
  `tool_input`. PreToolUse also gets `tool_use_id`; **PermissionRequest does not** (2.1.306).
- A PreToolUse `ask` (as the plain guard gives MCP writes) still leads to the PermissionRequest
  hook. With several PreToolUse hooks, `deny` from any one wins (documented behaviour).
- Unanswered prompts wait indefinitely; team-plan mods do not bypass `ask` rules.

## Shape

The `ask` rules stay, as the backstop: if a hook fails, times out or gives no decision, the prompt
appears as before. With the flag on, `bosun launch` adds these hooks to the orchestrator (on
top of its settings file, whose own `bosun guard` hook stays):

| Hook | Mode | Decides |
|---|---|---|
| PreToolUse | `bosun guard --approvals` | a gated call that carries out no approved proposal and no standing class: **deny**, with how to propose it. Else no decision |
| PermissionRequest | `bosun guard --permission-request` | a gated call that carries out an approved proposal, or a standing class: **allow**, and use the approval up. Else no decision (the prompt) |
| PostToolUse, PostToolUseFailure | `bosun guard --post-tool-use` | nothing: settles the proposal the call used (section 4) |

The gated calls are one table in `lib/guard.py` (`GATED_MCP`, `GATED_MCP_FAMILIES`,
`GATED_COMMANDS`), the same list as the `ask` rules; a site adds its own command prefixes with
`BOSUN_APPROVALS_GATED_COMMANDS` (and an `ask` rule for each). Bash commands are matched by
their leading words anywhere in a compound command (`gh pr view 1 && gh pr comment ...` is gated).
CI build tools (`restart_build`, `create_build`, `promote_build`, `stop_build`) are gated too, so
an approved re-run goes through the PermissionRequest hook instead of the plain guard's prompt.

### Flag off means no change

With `BOSUN_APPROVALS_ENABLED=0` the launch command is byte for byte what it was (no extra
settings), and both new hook modes check the flag again on every call and decide nothing, so
turning it off works at once. The plain `bosun guard` (the settings file's PreToolUse hook) is
untouched. `ledger propose` without the new options and every other ledger command behave as
before.

## 1. Approved in chat: no second prompt

A proposal can carry an **action spec**, the exact call it stands for:

    bosun ledger propose '<id>' --send slack --channel C0000000001 [--thread 1700000000.000100] --text - <<'EOF'
    <the exact message>
    EOF
    bosun ledger propose '<id>' --run 'gh pr merge https://github.com/acme/exporter/pull/7 --squash'
    bosun ledger propose '<id>' --tool jira_transition_issue --args-json - <<'EOF'
    {"issue_key": "EXP-12", "transition_id": "31"}
    EOF

The action line (shown in listings) defaults to a description of the spec. Free-text proposals
still work; they just never let a call through.

When I approve, the orchestrator records it with my words:

    bosun ledger approve '<id>' --quote '1 and 3 yes'

stored on the proposal with its time and a hash of the spec. The PermissionRequest hook allows a
call only when all of these hold:

- **The call matches an approved, unused proposal exactly.** Slack: the same channel, the same
  thread (or none), no `reply_broadcast`, and the same text after collapsing runs of whitespace
  (case and punctuation count). A command: the same string, trimmed. Another MCP tool: the same
  tool name (under any server) and equal arguments.
- **The approval is for this version.** Its hash matches the spec; a new proposal drops it.
- **My words are mine.** The quote appears verbatim (whitespace-normalized, case-insensitive) in a
  prompt of the calling session's transcript (`transcript_path`) made between the proposal and the
  approval (plus two minutes). Only prompts the session received from its user count, read through
  `claude_state.delivered_prompts`: typed, sent from the phone (Remote Control), queued while busy,
  or delivered into a running turn. Remote Control prompts appear in the transcript as ordinary
  user turns (verified on 2026-10-20 transcripts: a `queue-operation` enqueue, then a `user`
  entry with the text as a string), so they count like typed ones; no special case. Never tool results, meta entries, the summary after `/compact`
  or task notifications; and of those prompts, never what `activity.is_mine` rejects (bosun's
  own prompts: `[bosun ...]`, `Ledger item ...`, check-in and drain prompts, slash commands,
  text `bosun send` pasted) nor anything that opens like a tag (`<cross-session-message>`,
  `<agent-message>`, ...).
- **Once.** Allowing it marks the approval used (`approval-used` in the item's history). A retry,
  or the same call again, needs a new proposal and approval. This holds even if the call then
  fails (Slack down, a merge conflict): a used approval never lets a second call through. Decided
  on 2026-10-21 to keep it that way; the orchestrator proposes the retry and I approve it again.
  The failure is recorded (`approval-failed`, section 4), so the proposal is back under *Needs
  you*.

So a forged approval needs my words in my prompts: text from Slack, a PR or another session that
reaches the orchestrator as a tool result or an injected message never counts.

## 2. Standing approvals

`BOSUN_STANDING_CLASSES` lists the classes a site allows without a chat approval (none by
default). Each allowed call is recorded (`auto-approved`) and the chores list them under **Handled
automatically** (`bosun ledger approvals --auto --since <scan start>`).

**`pr-review-traffic`**: a Slack message (`slack_send_message`, not scheduled, not broadcast) when

- its channel is in `BOSUN_STANDING_SLACK_CHANNELS`, never a DM (`D...` channels and user ids
  are dropped from the list);
- it mentions only people in `BOSUN_STANDING_SLACK_MENTIONS`, and no group (`<!here>`,
  `<!subteam^...>`, `@channel`);
- it has at most one URL, and that is the parent PR's (`https://github.com/<repo>/pull/<n>`);
- it is exactly the proposed message (spec) of an open outreach item, not yet sent under a standing
  approval, with channel slack and purpose `review`, `info-ack` or `fyi`;
- its parent is my PR (`mypr:`) in the ledger, and either it is **ready** or, for `fyi` only, it is
  merged. `fyi` outreach may now be recorded on a merged PR for this.

**Ready** (`approvals.pr_ready`): the PR is open, and the latest report on its work after the
last hand-over says CI is green and leaves nothing open. "Its work" is the PR item and any item
that `produces` it (the `task:` a new-work window reported on). If a hand-over (`delegate`,
`--update`, a move) is newer than every report, it is not ready until the window reports again. The
ledger state alone (`awaiting-review`) is not evidence: the ledger cannot see CI, and the ready
check does not call GitHub. The repo window records CI with its report: `bosun ledger report <id> ... --ci
green|red|pending|none` (fix-my-pr and new-work tell it to wait for `gh pr checks --watch` first;
`_common.md` says what each value means). `ledger reports` shows it as a `ci:` line.

**`pr-housekeeping`**: one plain command (no chains, pipes, redirections, substitutions or
variables) on my open, ready PR, given by URL:

- `gh pr ready <url>` (no flags), on a ready PR as above (CI green);
- `gh pr edit <url>` with only `--title`, `--body` and `--add-reviewer` from
  `BOSUN_STANDING_GH_REVIEWERS`;
- `gh pr comment <url> --body '<text>'` (or `-b`, or `--body=<text>`, and nothing else) when
  `<text>` is exactly one of `BOSUN_STANDING_GH_COMMENTS` ('|'-separated; empty: none), e.g.
  the command that asks an automated reviewer for another round. At most
  `BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR` (default 3; 0: no cap) comments with that text go
  out on one PR under the standing approval, counted from the item's `auto-approved` history
  entries, which record the text (`comment`); past the cap each needs my word, so a loop that keeps
  asking for another round stops at a person. Such a comment is a re-review request, so the
  live-PR check below applies to it.

No class covers DMs, canvases, reactions, merges, closes, other comments, reviews, `gh api`, `git
push` or tracker writes: those need my word every time.

## 3. Never hang on an unapproved outward call

The PreToolUse hook denies a gated call that matches neither, with a reason that says why (no
proposal has exactly this action; proposed but not approved; approval used; quote not found) and
what to do: propose the exact call, list it under **Needs your approval**, and after I approve run
`ledger approve --quote` and retry the same call. A Bash call that holds an approved command with
more around it (`timeout 120 <command>`, a pipe, another step) is denied with the exact command to
run as a call of its own. The guard judges the command a wrapper runs (`timeout`, `env`, `nice`,
`nohup`, `xargs` and the others in `WRAPPERS`, `lib/guard.py`); before 2026-10-23 a wrapper hid it,
so such calls skipped the guard and waited at Claude Code's prompt. The orchestrator gets the denial
at once instead of waiting at a prompt until I come back. `BOSUN_APPROVALS_DENY_UNAPPROVED=0` restores the old
prompt for such calls (read on every call, no restart needed).

## 4. After the call: close what was carried out

Before this, nothing closed a proposal once its approved call had run: outreach closed with `ledger
sent`, merges by hand, but four Jira comment proposals whose calls had succeeded stayed pending for
hours, and the console counted them as waiting on me. `bosun guard --post-tool-use` runs on
PostToolUse and PostToolUseFailure (same matcher, flag and gated-call table as the other hooks).

**What the events carry** (verified on 2.1.306, headless, a scratch hook dumping its input, a
gated Bash command and a stdio MCP test server):

- PostToolUse fires **only on success**: `session_id`, `transcript_path`, `cwd`, `prompt_id`,
  `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`, `tool_use_id`, `duration_ms`,
  `tool_response`; MCP calls add `mcp_server` (`name`, `source`). `tool_response` for Bash is an
  object (`stdout`, `stderr`, `interrupted`, `isImage`, `noOutputExpected`; no exit code); for an
  MCP tool it is the result's content list (`[{"type": "text", "text": ...}]`).
- A failure fires **PostToolUseFailure** instead, with no `tool_response` but `error` (a Bash exit
  1: `"Exit code 1"`; an MCP result with `isError: true`: its text) and `is_interrupt`. A matcher
  for PostToolUse alone never sees failures.

**Matching the call.** PermissionRequest has no `tool_use_id`, so using an approval records, on it
(or on `auto_approved`), the call's hash (`approvals.call_hash`: the command for Bash, the tool and
arguments otherwise), the session and the transcript path. The post hook settles the open item whose
used, unsettled approval has the same hash, the same session (when both are known), and was used
at most `CALL_WINDOW` (30 minutes) before; if two items proposed the same call, the earliest use.
Nothing else is touched: no other item, no proposal without a used approval, nothing once settled.

**Settling it** (`ledger.settle_call`):

- success: the proposal is resolved `done`, "carried out by the approved call (<tool>)". Outreach
  (and `learn:` items) keep their own closing command: the approval is marked `carried_out`
  (`approval-carried-out` in the history) and `ledger sent` records the ref and resolves it, as
  before; no double close.
- failure (PostToolUseFailure, or a response with `is_error`, a non-zero exit code or
  `interrupted`): the proposal stays open, the approval is marked `failed` and the history gets
  `approval-failed` with the reason and that a new approval is needed.
- a Bash call sent to the background only started: nothing is recorded.

The hook reads the ledger first and takes the write lock only on a match (checking again under
it), always exits 0 with no output, and with the flag off does nothing.

**Where a proposal stands for me** (`approvals.proposal_status`, used by the console's *Needs you*
and Ledger groups, `ledger list` and `ledger.md`):

| Proposal | Status | Needs me? |
|---|---|---|
| no approval, or one for an earlier version | awaiting your approval | yes |
| approved, not used | approved, not yet made (bosun makes the call) | no |
| used, no result yet, or carried out (outreach waiting for `sent`) | carried out, closing | no |
| used, then `approval-failed` | the approved call failed; needs a new approval | yes |

An outreach item in `proposed` waits on me only while its proposal does.

## 5. A re-review request must match the live PR

A message asking a reviewer for another look says their review is addressed. Once, such a bump was
written after the first review round, carried into a proposal after a second round had arrived
(three new threads), regenerated from the same old text the next morning and sent: approved and
standing approvals alike only check that the call is the proposed one, and the ledger cannot see a
review that came after the last report. So with `BOSUN_APPROVALS_CHECK_REVIEW_THREADS=1` (the
default; read on every call) the hooks check the live PR before such a call goes out:

- **Which calls:** an allowed call (approved in chat or standing) that carries out the proposal of
  outreach with purpose `review` about a PR (its parent `mypr:` item, else a PR URL in its url,
  subject or details), `gh pr edit ... --add-reviewer` on a `mypr:` item, or `gh pr comment` on a
  `mypr:` item with one of the standing comment texts (`BOSUN_STANDING_GH_COMMENTS`). Nothing
  else touches the network.
- **The check** (`guard.check_bump`, over `lib/pr_threads.py`, read-only GraphQL through `gh`
  within `BUMP_CHECK_SECONDS`, 20 s, under the hooks' 30 s timeout): no review thread awaits the
  author (unresolved, last comment not by the PR's author), and no review by someone else was
  submitted after the proposal was recorded (`proposed_at`; the author's own thread replies are
  reviews too and do not count). Bots never count either way (GraphQL type Bot, or a login ending
  in `[bot]`): a review or AI bot that comments on every push would otherwise deny every
  legitimate bump, so its threads never await the author and its reviews are never newer. The
  exception is a reviewer bot the site relies on (`BOSUN_REVIEWER_BOTS`, its logins): its
  threads await the author and its reviews can be newer, like a person's, so a re-review request
  waits until its comments are answered. Its review is never an approval (`pr_threads.approvals`).
- **It disagrees:** `--approvals` denies the call, naming the threads (up to three URLs), the
  newer review and the threads per round, and what to do: run `bosun pr-threads <url>
  --awaiting-author`, hand those threads to the PR's window, then write and propose the message
  again from that output. `--permission-request` decides nothing, so the approval is not used up.
- **GitHub cannot be read** (gh fails, times out, or is missing): `--approvals` answers `ask` with
  the error and `--permission-request` decides nothing, so Claude Code's prompt asks me. This
  follows how the hooks fail elsewhere (a hook that cannot decide leaves the prompt), and neither
  sends the message unchecked nor blocks it until GitHub is back.
- `ledger sent` is not checked: it records a message that already went out.

The PermissionRequest hook reads the PR again rather than trusting the PreToolUse result: the two
hooks share no state, and a review can land in between. That is one more read-only GraphQL call
per re-review request, about a second.

**Cleanup.** `ledger resolve-carried-out [--dry-run] [--transcript PATH ...]` settles proposals
whose approval was used without a result recorded (calls made before this hook): it looks for the
exact proposed call within five minutes of the use in the transcript recorded at use, else the
given paths, else the orchestrator's transcripts written since, and settles it by the tool
result's `is_error`. Without such evidence it lists the item for resolving by hand and changes
nothing.

## Repo sessions

Repo sessions run with `BOSUN_CLAUDE_FLAGS` (by default `--permission-mode auto`). Auto mode prompts
only after blocking several actions in a row, and bypass mode never does, so they rarely hang on these
calls (a week of real transcripts in bypass mode had no permission prompt in a repo session other than
questions; the watchdog's `waiting` check reports the rest). A site that runs them in a prompting
mode could get the same pair of hooks through `repo_session_settings`, behind the same flag; the
quote check would then read the repo window's own transcript, and `pasted_hashes` for that window.
Not wired.

## Cost

Each gated call runs the hook three times (PreToolUse, PermissionRequest, and PostToolUse or
PostToolUseFailure), and with the flag on the PreToolUse and post hooks run for every Bash and MCP
call: 0.15 to 0.4 s each, mostly `load_config`; the post hook reads the ledger only for a gated call. The quote
check reads the session's transcript (tens of MB late in a day) only for a call that matches an
approved proposal.

## Decisions (2026-10-21) and what to revisit

- **CI evidence comes from the window's report** (`--ci green`), not from the hook calling GitHub:
  the hook stays fast, offline and deterministic over the ledger, and the window that pushed is the
  one that watched the checks. The one exception is the re-review check (section 5): what it needs
  (a review newer than the report) is by definition not in the ledger, so it reads the PR, only
  for re-review requests, and asks me when it cannot.
- **The standing classes stay as strict as described above.** The replay of a week of history
  showed what they leave to me: re-review bumps sent while the item showed a newer hand-over than
  its report, and notes about other people's PRs in the PR channel. Revisit after a week of real
  `bosun ledger approvals --auto` data, before widening anything.
- **Phone approvals need no special case** (Remote Control prompts are ordinary user turns, above).
- **An approval is used even if its call fails** (above, "Once"); the failure puts the proposal
  back under *Needs you* (section 4).
