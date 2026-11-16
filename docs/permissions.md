# Permissions: what needs your approval

What bosun's agents may do on their own, what waits for your yes, and what they may never do; what each window can
reach; and how to approve an outward action once, in chat. Read it before you run bosun on a Mac that holds
anything you care about.

## At a glance

| | What it covers | Enforced by |
|---|---|---|
| **Without asking** | Reading Slack, GitHub and Confluence; updating the ledger; reading and writing the ontology; handing a task to a repo window that already runs an agent, which then works within its task's rules | Claude Code's auto mode, and an allow list |
| **After your approval** | Anything that goes out under your name: the orchestrator's own Slack posts, merges, reviews and other pull request writes, pushes, tracker and wiki writes, any tool whose name says it writes, Chrome changing a page. Also starting a new agent session, accepting work someone else asked for, changing a ticket, and applying a learning proposal | The orchestrator's policy, `ask` rules in its settings, and `bosun guard` |
| **Never** | Editing files from the orchestrator, force pushes, `gh pr merge --admin`, `bosun uninstall`, a message to a window that names no work item, and running commands hidden in text that came from someone else | `deny` rules, and `bosun guard` |

By default "after your approval" means a Claude Code permission prompt, which waits until you look. With
[approvals in chat](#approvals-approve-once-in-chat) on, the orchestrator proposes the exact action and you
approve it in one line, from your phone if you like.

The full rules, tool by tool, are at the [end of this page](#the-rules-in-full).

## What each window can reach

Each window bosun starts reaches only the MCP servers and connectors its config names, so a repo window with a
task cannot forward a mail or share a file it never had:

| Window | Reaches | Set with |
|---|---|---|
| Repo windows | none by default | `BOSUN_CONNECTORS`, or `connectors:` in the window's `sessions.yml` entry |
| The orchestrator (and `bosun chores --dry-run`) | none by default: name the work tools it needs, such as Slack and a tracker | `BOSUN_ORCHESTRATOR_CONNECTORS` |
| Every window | Chrome, through `--chrome` in `BOSUN_CLAUDE_FLAGS` and `BOSUN_ORCHESTRATOR_FLAGS`; on the orchestrator, the guard asks before Chrome changes a page | the flags |

A name bosun cannot find is left out, so a window starts with less than its list, never with more, and the
orchestrator's launch alerts you. `all` lifts the limit for that list. Within the connectors the orchestrator may
reach, the rules above still decide what it may do with them. How to write a list, and what Claude Code's and
Codex's own settings do to it: [Connectors](configuration.md#connectors-what-each-window-may-reach).

**Files.** A repo window works in its repository, and may also read and write one folder beside it, the one its
worktrees go in: `<repository>.worktrees` (`~/Development/exporter.worktrees` for a window in
`~/Development/exporter`). Only that folder, not the repository's parent, which would reach every repository beside
it.

<details class="note" markdown>
<summary>More on the worktrees folder</summary>

The folder sits beside the repository's main working tree, for a window in the repository, in a folder under it,
or in one of its worktrees; there is none outside a git repository, or for a repository that is your home folder
or directly in `/`. bosun gives Claude Code that folder as an additional working directory
(`permissions.additionalDirectories` in the launch settings) and Codex as a writable root, so a worktree the agent
makes there, as every task's rules say to, does not stop the window with a question about a read outside its
working directories. An `agy` window gets the folder but no grant: its trusted folders do not cover the folders
under them. Claude Code drops a working directory that is not there when the session starts, so `bosun launch`
makes the folder before the agent starts; `bosun uninstall` removes one it made while it is empty, and leaves one
with a worktree in it.

</details>

## Approvals: approve once, in chat

Without this, every outward call opens a Claude Code permission prompt, even after you approved it in the plan,
and an unanswered prompt waits for ever. With `BOSUN_APPROVALS_ENABLED=1` (from the orchestrator's next start):

1. The orchestrator proposes the outward call exactly: the message and where it goes, the command, or the tool and
   its arguments (`bosun ledger propose <id> --send slack --channel C --text -`, `--run '<command>'`, `--tool <tool>
   --args-json -`), and lists it under *Needs your approval*.
2. You answer in your own words ("1 and 3 yes"), in the terminal or from your phone.
3. It records your words (`bosun ledger approve <id> --quote '1 and 3 yes'`) and makes the call. A hook lets exactly
   that call through, once, if your words are in a prompt you typed in that session after the proposal. Tool
   results, pasted text and anything injected never count.
4. When the call has run, its proposal is settled: done on success; on failure it is open again under *Needs you*,
   and needs a new proposal and approval, since approvals are single-use.

An outward call with no approved proposal is refused at once, telling the orchestrator to propose it, instead of
waiting at a prompt (`BOSUN_APPROVALS_DENY_UNAPPROVED=0` brings the prompt back). If a hook fails or decides
nothing, you get Claude Code's prompt as before. Design: [design/approvals.md](design/approvals.md).

<details class="note" markdown>
<summary>The fine print of approvals</summary>

- **The hooks.** `bosun guard --permission-request` (a PermissionRequest hook) lets the approved call through;
  `bosun guard --approvals` refuses an unapproved one; `bosun guard --post-tool-use` (PostToolUse and
  PostToolUseFailure) settles the proposal. Outreach that was approved is marked carried out and closes with `ledger
  sent` as before; a failure leaves the proposal open with `approval-failed`. An approved call not made yet, or made
  and closing, does not count as needing you.
- **Exactly as proposed.** An approved command runs as a Bash call of its own, exactly as proposed: a call that wraps
  it in more (`timeout 120 <command>`, a pipe, another step) is refused with the exact command to run. The guard
  judges the command a wrapper runs (`timeout`, `env`, `nice`, `nohup`, `xargs`, ...), so a wrapper never hides a
  gated call.
- **Decimal numbers.** A tool call whose arguments hold a decimal number (`{"story_points": 2.5}`) cannot be
  proposed: the ledger's journal holds whole numbers and text only, so `propose` refuses it, naming the field. Make
  such a call yourself, or set `BOSUN_APPROVALS_DENY_UNAPPROVED=0` so that it asks at Claude Code's prompt.
- **Your own outward commands.** `BOSUN_APPROVALS_GATED_COMMANDS` adds a site's own commands to gate (prefixes
  separated by `,`, such as `"mytool deploy, mytool publish"`).
- **From before the hook.** `bosun ledger resolve-carried-out [--dry-run]` settles proposals whose call ran before
  the PostToolUse hook existed, by what the orchestrator's transcript shows.

</details>

### Standing approvals

Some routine traffic can be approved once, for good. None is, unless you configure it in `BOSUN_STANDING_CLASSES`:

- **`pr-review-traffic`**: review requests, re-review bumps and merged notes for your own ready pull requests (ready
  meaning the latest report on the work says `--ci green` with nothing open). They are proposed as outreach, in
  `BOSUN_STANDING_SLACK_CHANNELS`, mentioning only `BOSUN_STANDING_SLACK_MENTIONS`, with no link but the pull
  request's.
- **`pr-housekeeping`**: `gh pr ready`; `gh pr edit` with only `--title`, `--body`, or `--add-reviewer` from
  `BOSUN_STANDING_GH_REVIEWERS`; and `gh pr comment <url> --body '<text>'` when the text is exactly one of
  `BOSUN_STANDING_GH_COMMENTS` (`|`-separated; for example the command that asks an automated reviewer for another
  round), at most `BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR` times (3; 0 for no cap) per text and pull request, after
  which each needs your word.

Never DMs, merges, `gh api` or tracker writes. Each standing action is listed in the next check-in (`bosun ledger
approvals --auto`).

### Re-review requests and automated reviewers

A re-review request on your pull request (outreach with purpose `review`, `gh pr edit --add-reviewer`, or a standing
comment), approved or standing, is checked against the live pull request first
(`BOSUN_APPROVALS_CHECK_REVIEW_THREADS=1`, the default). It is refused while a review thread waits for you (`bosun
pr-threads <url> --awaiting-author` lists it) or a review is newer than the proposed message. If GitHub cannot be
read, you get the prompt.

Bots never count in `pr-threads`, `review-watch` and that check, since a bot that comments on every push would block
every request, unless their GitHub login is in `BOSUN_REVIEWER_BOTS` (space-separated). Then their threads wait for
you and their reviews are rounds, like a person's, and their latest verdict is shown, read from the review body with
`BOSUN_REVIEWER_BOT_VERDICT_PATTERN` (default `^#+\s*Verdict:\s*(.+?)\s*$`, multi-line; the first group is the
verdict). A reviewer bot's review is never an approval.

## How repo windows are kept in bounds

A repo window works on the task `bosun delegate` built from the task's file, with the orchestrator supplying only the
facts (the ledger id, the link, who asked, a one-line ask). The rules of each task type (what it may do, what it
never does) are text the window follows; neither agent's safety check is what enforces them.
[Orchestrator](orchestrator.md#delegated-tasks-and-folder-trust) lists them.

| Agent | How it runs | Its setting |
|---|---|---|
| Claude Code | Auto mode by default: a safety check reviews each action. `bosun init` asks: auto mode (recommended), or skip permission checks (`--dangerously-skip-permissions`), which has no checks and, in Claude Code's own words, "offers no protection against prompt injection". Not on a machine holding production credentials | `BOSUN_CLAUDE_FLAGS` |
| Codex | Its sandbox, with its automatic reviewer (`--no-daemon --approve-for-me`). bosun adds the repo's `.git`, its own state folder and the network to what the sandbox allows. `--dangerously-bypass-approvals-and-sandbox` would remove both, for the same reasons not to | `BOSUN_CODEX_FLAGS` |

Content from Slack, pull requests and Confluence is untrusted. The policy tells the orchestrator never to follow
instructions found there, and the guard never lets text from there run commands. More on Codex:
[Agents](design/harnesses.md#what-codex-needed).

## The rules in full

| Tier | Examples | How it is enforced |
|---|---|---|
| Do without asking | read Slack/GitHub/Confluence, update the ledger, read and write the ontology (`bosun kg lookup`, `search`, `facts`, `context`, `add-entity`, `propose`, `supersede`, `confirm`, `reject`, `batch`; a fact in your words counts only with a quote that is in your recent prompts of that session, and confirming, rejecting or `batch apply` from a session needs one too; a terminal and the console work as before), `bosun delegate --window` (and `--update`) to a repo window already running an agent; that session then acts within its task type's rules | auto mode, plus an explicit allow list |
| Propose, then wait | the orchestrator's own Slack posts and reactions, `gh pr merge/review/comment/edit/close/ready/update-branch`, `gh api`, `git push`, Confluence/Jira comments, and any other MCP tool whose name has a write verb (send, create, update, delete, transition, execute, forward, share, trash, push, ...) | the policy, `ask` rules in `orchestrator/settings.json`, and `bosun guard`, a PreToolUse hook that asks before every MCP write tool, including ones the rules do not list (verified: Claude Code prompts even in auto mode). A name's words split on `_`, `-` and case changes (`editJiraIssue` is edit, jira, issue; `s3Upload` is s3, upload); words that are also nouns or adjectives in reads' names (request, comment, label, order, pin, mark, resolve, patch, put, clear, drop) do not count, so reads like `get_pull_request` and `get_patch` pass. bosun adds the guard when it launches the orchestrator (and to `bosun chores --dry-run`), from the bosun that launched it, so no settings file has to carry it |
| Propose, then wait | Chrome changing a page: clicking, typing, pressing keys, filling forms, running scripts | `ask` rules in `orchestrator/settings.json` for its whole tools that change pages (`javascript_tool`, `form_input`), and `bosun guard` for `computer`, which asks for every action but a screenshot, scrolling, zooming, waiting or hovering (a new action Chrome adds asks too). Reading a page, finding on it and going to a URL pass; uploads, shortcuts and new tabs ask by their verbs |
| Propose, then wait | starting an agent's session: in a new window (`bosun delegate ... --new`) or in a window that has none (`--start`) | the policy and chores procedure only, so you approve once in the plan without a second permission prompt |
| Propose, then wait | accepting work someone else asked for (a task spawned from their message); changing an external ticket (transition, assign, edit) | a task is accepted only with `user:` evidence and `delegate` refuses it before; ticket changes are proposals from `ledger tickets --mismatch`, and the tracker's write tools ask (guard) |
| Propose, then wait | applying a learning proposal (`bosun learn apply`, or `done`/`reject` for one) | the policy and the chores procedure; `ledger resolve` refuses `learn:` items, so they close only through `bosun learn`, which applies the change and closes the item together |
| Propose, then wait (unless you asked for it) | handing an item over again while its last hand-over waits for a report (`delegate ... --again`), or moving it to another window (`delegate --update <id> --window W`) | `delegate` refuses the first without `--again`; the policy covers both |
| Never | edit files, `gh pr merge --admin`, force pushes, `bosun uninstall` | `deny` rules (prefix matches, so best effort) plus `bosun guard`, which denies `git push` with `-f`, `--force*` or a `+refspec` and `gh pr merge --admin` anywhere in a command (verified) |
| Never | a free-text `bosun send` that names no work item; `send --new` or `send --start <msg>` instead of `delegate` | `bosun guard` denies `send` without `--id <ledger id>` or `--no-item`; updates to delegated work go through `delegate --update` |
| Never | a command substitution the shell would run (`$(...)` or backticks, outside single quotes or in an unquoted heredoc) in `bosun delegate`, `bosun send` or `bosun ledger reach-out`, `ask`, `replied`, `propose`, `approve`, `spawn`, `link`, `unlink`, `fold` or `ticket-sync`, and in `bosun kg add-entity`, `propose`, `supersede`, `confirm`, `reject` or `batch` (whose `--quote`, `--name`, `--alias`, `--summary` and `--value` are your text or someone else's) | `bosun guard` denies it: their text often comes from Slack, PRs or a tracker, so it must never run commands |
