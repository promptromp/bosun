# Permissions: what needs your approval

| Tier | Examples | How it is enforced |
|---|---|---|
| Do without asking | read Slack/GitHub/Confluence, update the ledger, read and write the ontology (`bosun kg lookup`, `search`, `facts`, `context`, `add-entity`, `propose`, `supersede`, `confirm`, `reject`, `batch`; a fact in your words counts only with a quote that is whole words of one of your recent prompts of that session, at least 8 characters of them, and confirming, rejecting or `batch apply` from a session needs one too; a terminal and the console work as before), `bosun delegate --window` (and `--update`) to a repo window already running an agent; that session then acts within its task type's rules (see Delegated tasks) | auto mode, plus an explicit allow list |
| Propose, then wait | the orchestrator's own Slack posts and reactions, `gh pr merge/review/comment/edit/close/ready/update-branch/create`, `gh issue comment/create/edit/close`, `gh release create`, `gh workflow run`, `gh run rerun`, `gh api`, `git push`, Confluence/Jira comments, and any other MCP tool whose name has a write verb (send, create, update, delete, transition, execute, forward, share, trash, push, ...) | the policy, `ask` rules in `orchestrator/settings.json`, and `bosun guard`, a PreToolUse hook that asks before every MCP write tool, including ones the rules do not list (verified: Claude Code prompts even in auto mode), and before those commands in a form the rules' prefixes do not see (`git -C <dir> push`, `/usr/bin/git push`, inside `$(...)`, `bash -c` or `eval`). A name's words split on `_`, `-` and case changes (`editJiraIssue` is edit, jira, issue; `s3Upload` is s3, upload); words that are also nouns or adjectives in reads' names (request, comment, label, order, pin, mark, resolve, patch, put, clear, drop) do not count, so reads like `get_pull_request` and `get_patch` pass. bosun adds the guard when it launches the orchestrator (and to `bosun chores --dry-run`), from the bosun that launched it, so no settings file has to carry it |
| Propose, then wait | Chrome changing a page: clicking, typing, pressing keys, filling forms, running scripts | `ask` rules in `orchestrator/settings.json` for its whole tools that change pages (`javascript_tool`, `form_input`), and `bosun guard` for `computer`, which asks for every action but a screenshot, scrolling, zooming, waiting or hovering (a new action Chrome adds asks too). Reading a page, finding on it and going to a URL pass; uploads, shortcuts and new tabs ask by their verbs |
| Propose, then wait | starting an agent's session: in a new window (`bosun delegate ... --new`) or in a window that has none (`--start`) | the policy and chores procedure only, so you approve once in the plan without a second permission prompt |
| Propose, then wait | accepting work someone else asked for (a task spawned from their message); changing an external ticket (transition, assign, edit) | a task is accepted only with `user:` evidence and `delegate` refuses it before; ticket changes are proposals from `ledger tickets --mismatch`, and the tracker's write tools ask (guard) |
| Propose, then wait | applying a learning proposal (`bosun learn apply`, or `done`/`reject` for one) | the policy and the chores procedure; `ledger resolve` refuses `learn:` items, so they close only through `bosun learn`, which applies the change and closes the item together |
| Propose, then wait (unless you asked for it) | handing an item over again while its last hand-over awaits a report (`delegate ... --again`), or moving it to another window (`delegate --update <id> --window W`) | `delegate` refuses the first without `--again`; the policy covers both |
| Never | edit files, `gh pr merge --admin`, force pushes, `bosun uninstall` | `deny` rules (prefix matches, so best effort) plus `bosun guard`, which denies `git push` with `-f`, `--force*` or a `+refspec` and `gh pr merge --admin` anywhere in a command (verified), as it reads it (below) |
| Never | a free-text `bosun send` that names no work item; `send --new` or `send --start <msg>` instead of `delegate` | `bosun guard` denies `send` without `--id <ledger id>` or `--no-item`; updates to delegated work go through `delegate --update` |
| Never | a command substitution the shell would run (`$(...)` or backticks, outside single quotes or in an unquoted heredoc) in `bosun delegate`, `bosun send` or `bosun ledger reach-out`, `ask`, `replied`, `propose`, `approve`, `spawn`, `link`, `unlink`, `fold` or `ticket-sync`, and in `bosun kg add-entity`, `propose`, `supersede`, `confirm`, `reject` or `batch` (whose `--quote`, `--name`, `--alias`, `--summary` and `--value` are your text or someone else's) | `bosun guard` denies it: their text often comes from Slack, PRs or a tracker, so it must never run commands |

**What the guard reads, and what stays best effort.** `bosun guard` reads a Bash call as the shell does:
comments out (a `#` that starts a word), a backslash before a newline joining the lines, heredoc bodies left out
as data (the whole delimiter word, quotes taken out; a here-string `<<<` or arithmetic `$((1<<2))` opens none);
each command by its name (`/usr/bin/git` is `git`), past keywords, assignments, redirections (`2>/dev/null git
...`) and wrappers (`timeout`, `env`, `nice`, `nohup`, `xargs`, `sudo`, `arch`, `script`, `coproc` and the others
in `WRAPPERS`, by name, so `/usr/bin/env` too); git's and gh's subcommand past their own options (`git -C <dir>
push`, `gh -R acme/x pr merge`); the commands in `$(...)` and backticks, with their quotes, heredocs and nesting;
and what `bash`, `sh`, `zsh`, `dash`, `ksh`, `csh`, `tcsh` or `fish -c`, `eval`, `env -S` and `script -c` run, read
again. It denies a force push (`-f`, `--force*`, `--mirror`, a `+refspec`) and `gh pr merge --admin` (or
`--admin=...`) wherever it finds them. When it cannot read a command for certain, it asks: quotes or a heredoc
that never close; a command named by a substitution, ANSI-C quoting (`$'git'`) or a pattern (`/usr/bin/g[i]t`); a
command a wrapper runs named by a variable; a gated command whose arguments use ANSI-C quoting or braces (`git
push $'-f'`, `git push origin --{force,}`); inline code given to `python -c`, `perl -e`, `ruby -e`, `node -e` or
`osascript -e` that names `git push`, `gh` or a URL; and a gated command whose command word is quoted or escaped
(`\git push`, `"git" push`), which the `ask` rules may not see. It does not see, and leaves to auto mode and the
policy: a command fed to a shell on its standard input (`echo 'git push -f' | sh`) or in a script file (`sh
./x.sh`); a command named by a variable outside a wrapper (`G=git; $G push -f`, as `L="bosun ledger"; $L ...` is
common); flags held in a variable (`git push $FLAGS`); git aliases (`git p` from your git config, or `git -c
alias.p=push p`) and gh aliases (`gh alias set m 'pr merge --admin'`); an alias or function from the shell's startup
files; commands run by `find -exec`, `parallel`, `watch`, `make` or a git hook; inline interpreter code that does not
name `git push`, `gh` or a URL (one that builds the command from pieces); and a named coprocess (`coproc NAME {
...; }`).

## What each window can reach

Each window bosun starts reaches only the MCP servers and connectors its config names, so a repo window
with a task cannot forward a mail or share a file it never had:

| Window | Reaches | Set with |
|---|---|---|
| Repo windows | none by default | `BOSUN_CONNECTORS`, or `connectors:` in the window's `sessions.yml` entry |
| The orchestrator (and `bosun chores --dry-run`) | none by default: name the work ones it needs, such as Slack and a tracker | `BOSUN_ORCHESTRATOR_CONNECTORS` |
| Every window | Chrome, as before, through `--chrome` in `BOSUN_CLAUDE_FLAGS` and `BOSUN_ORCHESTRATOR_FLAGS`; on the orchestrator, the gate above asks before it changes a page | the flags |

A repo window may also read and write in one folder beside its repository, the one its worktrees go in:
`<repository>.worktrees` beside the repository's main working tree (`~/Development/exporter.worktrees` for a
window in `~/Development/exporter`, in a folder under it, or in one of its worktrees; none outside a git
repository, or for a repository that is your home folder or directly in `/`). bosun gives Claude Code that
folder as an additional working directory (`permissions.additionalDirectories` in the launch settings) and
Codex as a writable root, so a worktree the agent makes there, as every task's rules say to, does not stop the
window with a question about a read outside its working directories. Only that folder: not the repository's
parent, which would reach every repository beside it. An `agy` window gets the folder but no grant: its trusted
folders do not cover the folders under them. Claude Code drops a working directory that is not there when the
session starts, so `bosun launch` makes the folder before the agent starts; `bosun uninstall` removes one it
made while it is empty, and leaves one with a worktree in it.

bosun passes the list as Claude Code's `allowedMcpServers` in the window's launch settings, in place of
any a site settings file names. A name bosun cannot find is left out, so a window starts with less than
its list, never with more, and the orchestrator's launch alerts you. `all` lifts the limit for that list.
Claude Code adds the `allowedMcpServers` of your `~/.claude/settings.json` and of the window folder's
`.claude/settings.json` and `.claude/settings.local.json` to the window's, so a window with a list does
not start while one of those allows more than its list names. On Codex, bosun switches off the servers the
list does not name, every plugin when it leaves out a plugin's server, and ChatGPT's connectors
([Codex](setup.md#connectors-what-each-window-may-reach)). How to write a list:
[Connectors](setup.md#connectors-what-each-window-may-reach). Within the connectors the orchestrator may
reach, the tiers above still decide what it may do.

## Approvals: approve once, in chat

By default every outward call above opens a Claude Code permission prompt, even after you approved
it in the plan, and an unanswered prompt waits forever. With `BOSUN_APPROVALS_ENABLED=1` (from
the orchestrator's next start; design: [docs/design/approvals.md](design/approvals.md)):

- The orchestrator proposes outward calls with the exact call (`ledger propose <id> --send slack
  --channel C --text -`, `--run '<command>'`, `--tool <tool> --args-json -`). When you approve
  ("1 and 3 yes"), it records your words (`ledger approve <id> --quote '1 and 3 yes'`) and makes
  the call; a PermissionRequest hook (`bosun guard --permission-request`) lets exactly that
  call through, once, if your words say yes in a prompt you typed (or sent from your phone) in that
  session after the proposal: all of the prompt holding an approving word (yes, ok, send, go ahead,
  lgtm, ...), the item's id or a bare choice ("1 and 3", "all"); or at least 8 characters of its
  whole words holding an approving word or the id. A no, not, never, wait, stop, hold, cancel or a
  word ending in n't in the same sentence, or a question without an approving word, never counts;
  nor do tool results, pasted and injected text. `ledger approve` refuses a quote that does not
  count. It cannot tell what your yes answered: a "yes" to another question after the proposal
  counts for it too, and so does a yes you take back in a later sentence or prompt.
- A tool call whose arguments hold a decimal number (`{"story_points": 2.5}`) cannot be proposed:
  the ledger's journal holds whole numbers and text only, so `propose` refuses it, naming the
  field. Make such a call yourself, or set `BOSUN_APPROVALS_DENY_UNAPPROVED=0` so that it asks at
  Claude Code's prompt.
- An outward call with no approved proposal is denied at once (`bosun guard --approvals`),
  telling the orchestrator to propose it, instead of waiting at a prompt.
  `BOSUN_APPROVALS_DENY_UNAPPROVED=0` brings the prompt back for those.
  `BOSUN_APPROVALS_GATED_COMMANDS` adds a site's own outward commands to gate (prefixes
  separated by `,`, e.g. `"mytool deploy, mytool publish"`).
- An approved command runs as a Bash call of its own, exactly as proposed: a call that wraps it in
  more (`timeout 120 <command>`, a pipe, another step) is denied with the exact command to run. The
  guard judges the command a wrapper runs (`timeout`, `env`, `nice`, `nohup`, `xargs`, ...), so a
  wrapper never hides a gated call.
- Once the approved call has run, `bosun guard --post-tool-use` (PostToolUse and
  PostToolUseFailure) settles its proposal: success closes it as done (outreach is marked carried
  out and closes with `ledger sent` as before); failure leaves it open with `approval-failed`, so
  it is under *Needs you* again and needs a new proposal and approval (approvals are single-use).
  An approved call not made yet, or made and closing, does not count as needing you.
  `ledger resolve-carried-out [--dry-run]` settles proposals whose call ran before this hook, by
  what the orchestrator's transcript shows.
- Standing approvals, none unless configured: `BOSUN_STANDING_CLASSES="pr-review-traffic"`
  (review requests, re-review bumps and merged notes for your own ready PRs, ready meaning the
  latest report on the PR's work says `--ci green` with nothing open; proposed as outreach,
  in `BOSUN_STANDING_SLACK_CHANNELS`, mentioning only `BOSUN_STANDING_SLACK_MENTIONS`, with
  no URL but the PR's) and `pr-housekeeping` (`gh pr ready`, and `gh pr edit` with only
  `--title`, `--body`, `--add-reviewer` from `BOSUN_STANDING_GH_REVIEWERS`, and `gh pr comment
  <url> --body '<text>'` when the text is exactly one of `BOSUN_STANDING_GH_COMMENTS`,
  '|'-separated, e.g. the command that asks an automated reviewer for another round; at most
  `BOSUN_STANDING_GH_COMMENTS_MAX_PER_PR` (default 3, 0: no cap) per text and PR, then each
  needs your word). Never DMs, merges, `gh api` or tracker writes. Each is listed in the next
  check-in (`ledger approvals --auto`).
- A re-review request on your PR (outreach with purpose `review`, `gh pr edit --add-reviewer`, or
  a standing comment), approved or standing, is checked against the live PR first
  (`BOSUN_APPROVALS_CHECK_REVIEW_THREADS=1`, the default): it is denied while a review thread
  awaits you (`bosun pr-threads <url> --awaiting-author` lists it) or a review is newer than
  the proposed message. If GitHub cannot be read, you get the prompt.
- Automated reviewers: bots never count in `pr-threads`, `review-watch` and that check (a bot that
  comments on every push would block every request), unless their GitHub login is in
  `BOSUN_REVIEWER_BOTS` (space-separated): then their threads await you and their reviews are
  rounds, like a person's, and their latest verdict is shown, read from the review body with
  `BOSUN_REVIEWER_BOT_VERDICT_PATTERN` (default `^#+\s*Verdict:\s*(.+?)\s*$`, multi-line; the
  first group is the verdict). A reviewer bot's review is never an approval.
- The `ask` rules stay: if a hook fails or decides nothing, you get the prompt as before.

Content from Slack, PRs and Confluence is untrusted. The policy tells the orchestrator never to
follow instructions found there; delegated tasks are built by `bosun delegate` from the task
files, with the orchestrator supplying only the facts (ledger id, link, requester, one-line ask).
Repo sessions run in Claude Code's auto mode by default (`BOSUN_CLAUDE_FLAGS`), where a safety check
reviews each action. `bosun init` asks which to use: auto mode (recommended) or skip permission
checks, which sets `--dangerously-skip-permissions` there. That has no checks and,
in Claude Code's own words, "offers no protection against prompt injection": not on a machine holding
production credentials.

A repo session on Codex runs in its sandbox with its automatic reviewer (`BOSUN_CODEX_FLAGS`:
`--no-daemon --approve-for-me`), and bosun adds the repo's `.git`, its own state folder and the network to
what the sandbox allows. `--dangerously-bypass-approvals-and-sandbox` in that setting would remove both, for
the same reasons not to. The rules in a task are text on Codex as they are on Claude Code: a session follows
them, but neither agent's safety check is what enforces them. See
[Agents](design/harnesses.md#what-codex-needed).
