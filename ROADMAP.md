# Roadmap

Plans, open questions and what is done so far. Each entry says why it matters and what we already
know, so whoever picks it up does not start from scratch.

## Architecture building blocks (2026-10-16)

The five parts bosun needs, and where each stands.

### 1. Local watchdog

**Why:** something has to notice when bosun itself fails. Early on the orchestrator sat at a
permission prompt for hours, unnoticed; every check-in skipped it.

**Done (2026-10-16):** `bosun watchdog`, its own launchd agent every 5 minutes, independent
of tmux. One table of checks, each with a de-duplication key: morning run missing or failed, any
window waiting at a prompt, new ERROR lines in the logs, session or check-in loop missing during
awake hours, items stuck in `delegated`, and (with `BOSUN_LEARN_ENABLED=1`) a daily learning
run that is missing, failed, crashed or stalled. Alerts only during scheduled awake hours, once per key,
through `bosun alert`. Checks added since, 17 in all now (`docs/watchdog.md`): among them `events` and
`slack-watch` (with those features on), `login-expired` (below), `long-shell` (2026-10-21: a background
command keeping a window in shell) and `stalled-start` (2026-10-30: an agent stopped at a screen); `bosun orphans` finds the helpers a pane's launch shell leaves behind, and `launch`,
`stop` and the check-in loop stop them. Follow-ups: see "Watchdog follow-ups" and "Urgent watchdog alerts
outside the awake window" below.

**Done: expired Claude Code login.** Once the login was revoked; for more than a day every
check-in and the morning run got only "please run /login" error replies, the
check-in loop logged them as delivered and nothing alerted. Now the transcript's structured error
fields (backed by `claude auth status` and a tiny login check request) drive a morning setup step
(`ensure_claude_login`), a check-in gate that stops sending to a session that only gets login
errors, the watchdog's `login-expired` check, and `bosun status` / `doctor` (`docs/setup.md`,
"Claude Code login").

### 2. Cloud watchdog (track only)

**Why:** a machine-local watchdog cannot report that the machine is off, asleep or offline.

**Idea:** a dead-man's switch. The local watchdog sends a heartbeat each cycle during the
scheduled awake window, with a small status summary (last morning run, stuck windows, open alerts).
A remote service that knows the schedule alerts when heartbeats stop, or when a heartbeat reports
a problem.

**Notes:** a hosted heartbeat service (healthchecks.io-style) would work without building a server,
but it is a third-party vendor receiving data from the machine: check it against your
organization's compliance and vendor-review process first, and keep the payload free of work
content either way.

### 3. Ledger as the protocol between sessions

**Why:** long-lived sessions drift into "textual mud" when work items are discussed in free text.
Every message about a work item should reference its ledger id, and outcomes should be recorded as
structured ledger fields, not only as chat.

**Done (2026-10-16):** stable validated ids, per-kind state machines with evidence rules,
per-item history, idempotent Slack processing. `delegate` records the window on each item and
refuses a second hand-over while the first awaits a report (`--again` overrides); `delegate
--update <id>` sends addenda framed with the id, and `--update <id> --window <window>` moves an
item. Repo sessions report outcomes with `ledger report` (summary, results, open points), read
with `ledger reports`. `send -` reads stdin; the guard requires `--id <ledger id>` or `--no-item`
on the orchestrator's sends. Writes take an exclusive `flock`, replace the file atomically and
keep a backup. Two days of interactions, rewritten as fiction in a made-up domain, are replayed
in `tests/fixtures/corpus/`.

**Done (2026-10-16): outreach**, what we wait on from other people. Real sessions left product
decisions, another team's contract question and a reply that asked a stakeholder back only in
chat, PR text and Slack, and closed the mentions that started the work. Now each is an `out:` item
(channel, purpose, to, subject, options, every message sent, the reply as evidence) on the item it
blocks, which shows `waiting on ...` and cannot be finished while it is open. The orchestrator
proposes and sends the messages, matches Slack thread replies (`ledger seen`), proposes nudges and
hands replies to the window holding the work (`delegate --update <out-id>`); repo sessions record
outreach instead of asking in chat. The policy maps free text ("ask Dana ...", "ping Priya
again", "Marco answered, go with b") to these steps, checked with the real Claude in
`tests/llm/` (17 cases at first, all passing on two runs each; 36 now). Follow-ups: see "Outreach follow-ups".
Same day, after overlapping drafts in a live session: one-way messages (`fyi`, done once sent),
one message recorded on several items (`sent <id> <id> ...`), a warning on a second open item to
the same person, every item sharing a thread named by `seen`, and the chores merging overlaps.

**Done (2026-10-16): lineage and external tickets.** Work with no artifact of its own (a
colleague's "yes please fix", my "go ahead", a follow-up a session found) was delegated under an
outreach or mention id, or not recorded; once a session opened a PR at my direct request before
the delegation proposal was approved, and the PR got no ledger item at all. Now: a `task` kind
(proposed, accepted only with `user:` evidence, delegated, done), typed links stored once and read
both ways (`spawned_from`, `produces`, `blocked_by`, `duplicate_of`, `folded_into`, `tracks`, `relates_to`),
`ledger spawn/link/unlink/tree/migrate-links`, delegate refusing outreach ids and unaccepted tasks,
and a read-only `ticket` kind for Jira and GitHub issues (`ticket-sync`, a status mapping with
site overrides, `tickets --mismatch` hints that are only ever proposals). Design note:
[docs/design/lineage.md](docs/design/lineage.md).

**Done (2026-10-19), phase 1, off by default: events from repo sessions.** The orchestrator
learned what happened only by polling, hourly. It re-asked me decisions I had given in a repo
window (a message typed while a session is busy is stored as a `queued_command` attachment, so
reading the transcript missed it), `reports --since` showed only the latest report, a report made
a window look free while it kept working, and sessions spawned tasks another task already held.
Now: `reports --since` lists every report; `spawn` warns about a task from the same source or
Slack thread; with `BOSUN_EVENTS_ENABLED=1` a `UserPromptSubmit` hook records what I type in a
repo window (`ledger said`), and the check-in loop pushes reports, what I said, drafts, follow-ups
and windows waiting at a prompt to the idle orchestrator as one debounced summary; `working` shows
which item a window is on. Peer messaging (Claude Code's `SendMessage` between sessions) was
evaluated as the transport and not used: across permission modes it holds every message at an
approval dialog. Design note: [docs/design/events.md](docs/design/events.md).

**Done (2026-10-20): Slack activity pushed, and no Slack gap after a missed morning.** A morning
scanned a fixed 24 hours, so a failed morning run or a day away lost older messages; it now scans
from the last scan when that was longer ago, capped at `BOSUN_SLACK_MAX_LOOKBACK_HOURS` (96).
Between check-ins, with `BOSUN_SLACK_WATCH_ENABLED=1`, the check-in loop runs `bosun
slack-watch` every 15 minutes in the awake window: a headless haiku that Claude Code lets read Slack
and nothing else finds new mentions, DMs and replies in my threads (`is:thread with:<@me>`), code
checks the JSON and drops what the ledger has seen, and each new message becomes an event for the
orchestrator, which runs chores step 1 for it. About $0.02 a quiet call, capped per call and per
day; the watchdog's `slack-watch` check covers stalls, failures and the budget. Design: "Slack
watcher" in [docs/design/events.md](docs/design/events.md#slack-watcher).

**Done (2026-10-21): workstreams.** Nothing said which effort an item served, so a weekly
stand-up, stats per effort and the knowledge graph had to guess. Now: a `workstream` kind
(`ws:<slug>`, active, paused or done, with an epic, channels, repos, dates, an area and aliases),
one `part_of` per item (two levels of workstreams at most), and resolution on read: explicit,
then inherited through lineage, then a tracked ticket's epic (`ticket-sync --parent`), then site
rules in `workstreams.json`, else unassigned. Ambiguity leaves an item unassigned with the
candidates, as a proposal. Commands: `ws`, `assign --dry-run|--apply`, `summary` (a stand-up
draft), `lint`, `list --ws`; the console API filters by workstream and stats roll up per
workstream. Design and research: [docs/design/workstreams.md](docs/design/workstreams.md).
**Next:** the console's workstream filter and stats in the page; saved, dated status updates with
a health rating; context packs per workstream once the knowledge graph has them.

**Next (Slack):** a real push instead of polling: a Slack app or a cloud trigger that receives
Slack's events API and wakes the orchestrator, which needs a host that is always on (see the cloud
watchdog), and the claude.ai Slack connector as a second source for the watcher.

**Next (events, phase 2):** the window's active item in a waiting event; `refresh` restarting idle
repo sessions whose prompt hook does not match the flag; `user:` evidence taken from a `said`
entry by reference; events for other PR state changes from GitHub (merged, approved).

**Done (2026-10-21): reviews on my PRs.** `bosun pr-threads` reads a PR's review threads and
rounds (read-only; which threads await the author; bots never count), and `bosun review-watch`
raises new reviews on my PRs after their last hand-over as `review-arrived` and, with events on,
`review` events (the check-in loop runs it every `BOSUN_REVIEW_WATCH_MINUTES`).

**Next (lineage, phase 3):**
- **Derived closing.** A task whose PR merged, or a ticket closed while its work is open, is a
  hint today; the plan could carry the exact transition automatically, and a merged task could
  close without asking (a merge is a fact).
- **More backends:** Linear and a site's own tracker (one row in `BACKENDS`), and matching ticket
  keys in PR titles and branch names to propose `tracks` links.
- **Write-sync proposals.** A mismatch proposal that carries the exact tracker call (the issue's
  transition id from the read tools), so an approved change runs as recorded and is synced back.
- **Learning beyond the deterministic checks:** replies on outreach whose text asks for new work
  (left to the analysis, which reads the digest), and findings that should spawn a task.
- **Knowledge tie-in:** tasks and tickets linked to processes and product areas
  ([knowledge.md](docs/design/knowledge.md)).

**Open:** repo sessions started without a hand-over still never got the task rules (see "Repo
sessions started without a hand-over" under "Outreach follow-ups"; the chores now link the PRs
they open); a session waiting at a question still blocks every send to its window.

**P2:** move the store to SQLite when queries, history size or syncing with the cloud watchdog
need it.

### 4. Organizational knowledge (People, Process, Product)

**Why:** the most expensive mistakes of a week of use came from missing knowledge (which checks
a deploy waits on, reviewers who had already approved, a retired environment), mostly facts I had
stated once and that were lost when the conversation ended.

**Done (2026-10-21), MVP 1:** `bosun kg`, a knowledge base in
`$BOSUN_CONFIG_DIR/knowledge/` with its own git history: entities with strong and weak
aliases, an append-only log of atomic facts with sources, authority and two timelines (valid and
recorded), a closed schema whose People predicates are a whitelist, auto-confirmation only for my
verified words, two trusted sources or a profile field, one capped daily review batch answered in
a line ("confirm all except 2b"), lookup, search, neighbours, small cited context packs, lint
(contradictions, staleness, collisions, retention), delete on request, and a dry-run-first
import (with a converter for a survey's tables). Design note:
[docs/design/knowledge.md](docs/design/knowledge.md).

**Done (2026-10-21), MVP 2:** the console's Knowledge view (`#/knowledge`): four axis cards with
search, entity pages (facts with authority, status, dates and sources; history; backlinks;
related ledger items), process flows, a neighbourhood graph, and the review queue, which may
confirm, reject or defer facts (the console's only write, behind the cookie, an Origin check and a
CSRF token).

**Done (2026-10-21), the Knowledge Graph page's tabs:** four treatments prototyped on invented
data ([docs/design/prototypes/knowledge-treatments/](docs/design/prototypes/knowledge-treatments/README.md));
the **Briefing** is the page's home (facts captured per day, the confirmed share, coverage per
card, this week's changes before -> after and new facts, today's review as a card stack with keys,
drag and undo, the most connected with mini ego graphs; "rediscovery saved" a placeholder until
learning counts rework), the **Constellation** a second tab (the whole knowledge base on a canvas
force layout, hover, facts panel, search, chips, an "as of" slider; capped at 400 entities), and
the overview and entity browser a third, **Raw data**. The Metro map is the next item.

**Next:** MVP 3, context packs in delegate briefs behind a flag, the orchestrator's
"check `kg lookup` first" rule, and capture of my own words in every window; later, a knowledge
pass in the learning run (counting rediscovery and rework episodes) and a team-shared variant
after a privacy review.

### 4a. Knowledge Graph: Metro map treatment

**Why:** in the prototype of the Knowledge Graph page
([docs/design/prototypes/knowledge-treatments/](docs/design/prototypes/knowledge-treatments/README.md),
treatment B) the processes drawn as metro lines were the clearest picture of how work moves: each
process a line through its steps, the steps, gates, systems and people that several processes share
as interchanges, and gates as marked stations. It answers "what does a deploy go through, and what
else touches that gate" at a glance, which neither the Briefing nor the Constellation does.
Decision of 2026-10-21: keep it, but prototype it more on realistic data first; the console shows
it as a disabled tab.

**What the prototype showed, and the hard part:** its layout is hand-tuned (every station placed on
a grid by hand, seven lines over invented data). An automatic metro layout of real process data is
the work: ordered `has_step` chains of different lengths, stations shared by several lines in
different orders, steps without an order, processes that only `use` a system, and enough lines to
crowd the map. It has to stay readable at up to about 10 lines (more: pick the busiest, or one map
per area), keep lines straight with 45-degree bends, and stay stable as facts are added.

**Plan:** export the real knowledge base's processes (counts and shape only, on this machine) to
measure them: lines, stations, shared stations, step orders. Then prototype automatic layouts in the
same throwaway page: a longest-chain trunk with the other lines placed by shared stations, ordered
layers with crossing reduction, then an octilinear routing pass, and compare them on that real
shape and on a larger invented one. Build the tab only once one layout holds up.

### 5. Continuous learning

**Why:** mishaps (an overnight permission prompt, a message lost in a busy session, duplicate
outreach, the user correcting the same thing twice) were found by accident and their lessons lived
only in chat.

**Done (2026-10-16), MVP, off by default:** `bosun learn` builds a redacted digest of each
bosun window's sessions, the ledger, the logs and the watchdog's alerts, plus deterministic
ledger checks (overlapping or mis-scoped outreach, a second hand-over), and gives it to one
headless Claude with no tools; findings must quote the digest. New proposals become `learn:` ledger
items that wait for approval; `learn apply` writes a memory note or a site rule, other changes are
made by hand. `bosun sessions --json` lists the windows; a launchd agent runs it daily with
`BOSUN_LEARN_ENABLED=1`, and the watchdog's `learn` check alerts when that run is missing,
failed, crashed or stalled. Design note: [docs/design/learning.md](docs/design/learning.md).

**Done (2026-10-24): a bounded digest.** A three-day run sent a 1.1 MB digest and cost twice its
budget, since Claude Code checks `--max-budget-usd` only after a turn. The digest is now capped
(`BOSUN_LEARN_MAX_DIGEST_KB`, leaving out routine history first and saying what it left out),
and a run whose estimated cost exceeds the budget is refused before the call.

**Next:** give the analysis the watchdog's checks and the command list (it re-proposed an alert the
watchdog has); check whether applied changes stop their finding from recurring; sandboxed on-demand
reads of transcript excerpts; proposals as diffs to the generic prompts (reviewed as PRs); trends
across runs; a second harness adapter. Open: whether `learn apply` should also need a permission
prompt once phone approvals are verified.

## Features

### Other agents and other operating systems

Repo sessions run on Claude Code and Codex, and the installer's checks are split by OS and by agent
(`docs/design/init.md`, `docs/design/harnesses.md`).

**Next on Codex:** the orchestrator (a system prompt, chores without a plugin, the guard and approvals on its
own hooks and sandbox; hooks stop at a review screen, so this needs thought); noticing a revoked login;
its sessions in the Impact stats and the learning digest (`Harness` in `lib/learn_digest.py`); a real GitHub
login inside a session, which so far has only been run with a stand-in `gh`; per-task effort on a running
session; running without Claude Code at all (`bosun init`, `install.sh` and `bosun doctor` still need it).

**Next OS: Linux.** `lib/platforms/linux.sh` is the easy part. What else assumes macOS: `install.sh` and the
launchd templates, `pmset` and the wake, `caffeinate`, `osascript` alerts, `dscl`, `date -j` and `lsof`'s flags.

### Impact (productivity) stats

**Why:** after a week or two of real use the question is no longer what bosun did but what it
changed: do I deliver more, faster, for how many of my own hours and at what cost, and what did
the learning and the knowledge base save? The Stats view counts activity; it cannot answer that.

**What we know:** design in [docs/design/impact.md](docs/design/impact.md) (accepted 2026-10-26):
honesty principles (measured and estimated look different, sample sizes everywhere, my own
baseline from before bosun as a band, confounders named, drill-down to the items, people as
counts only), every metric's definition, a daily read-only collector and an Impact page. Merged PRs
per week and time to merge can move apart, so the page must show both.

**Phases:**
- **A.** `bosun impact collect` (a GitHub and transcript snapshot) and the first Impact page:
  PRs merged per week against the baseline, time to merge, reviews given, agent busy time against
  my touch time (leverage, an estimate), tokens and cost per week and per merged PR, where agent
  time went. Plan: [docs/plans/impact-phase-a.md](docs/plans/impact-phase-a.md).
- **B0. Cost per task** (next, ahead of B; added 2026-10-27). Subagents carry about two thirds of
  all spend, so overhead is the smaller lever: attribute every session and subagent turn's cost to
  the ledger item it worked on (the item a hand-over or report names, else the window's active
  item), and show cost per item, per task type and per outcome (done, merged, dropped), with the
  most expensive items and types against what they produced. This shows which kinds of work are
  costly for what they deliver, before any tuning (effort, model, subagent use) is chosen.
- **B.** Lead time from merge to production and deploy frequency, from the site's deploy
  announcements; quality next to speed (review rounds per PR, CI passing on the first push, reverts
  and follow-up fixes); the flow funnel from ask to production with the median time at each step.
- **C.** Instrumentation for what learning and knowledge save: a class on each learn finding and
  recurrences of that class before and after its fix, events for each use of the knowledge base or
  a memory note, and time saved as a calibrated range. Collect from C's launch; show after two to
  four weeks.
- **D.** Polish, a weekly summary for the Monday standup draft, maybe a monthly report.
- **Overhead.** What bosun itself costs (check-ins, events, notifications, resumes, messages
  between sessions) against the work, the context each window re-reads, guardrails that show the
  work holds, and watchdog warnings; then measure the overhead fixes before and after. Plan:
  [docs/plans/impact-overhead.md](docs/plans/impact-overhead.md).

**Status:** Phase A merged (2026-10-27, daily collection on); overhead panel and guardrails merged
(2026-10-27); overhead fixes merged (2026-10-27/28). B0, cost per task, built (2026-10-28, plan:
[docs/plans/impact-cost-per-task.md](docs/plans/impact-cost-per-task.md)). Next: Phase B.

### Approvals for outward calls

**Why:** every outward call of the orchestrator opened a permission prompt after I had approved it
in chat (over a hundred in six days), and an unanswered prompt blocked everything, once overnight.

**Built (2026-10-21, `BOSUN_APPROVALS_ENABLED`, design: [approvals.md](docs/design/approvals.md)):**
proposals carry the exact call; `ledger approve --quote` with my words lets exactly that call
through once (a PermissionRequest hook checks my words in the session's own prompts); unapproved
outward calls are denied at once instead of waiting; standing classes `pr-review-traffic` and
`pr-housekeeping` for routine traffic on my own PRs, listed in the check-in. Since then
(2026-10-21 to 10-23): a PostToolUse hook closes the proposal an approved call carried out (a
failure records `approval-failed`); a re-review request is checked against the live PR first (no
thread awaiting me, no newer review; bot reviews never count); the guard judges the command a
wrapper (`timeout`, `env`, `xargs`, ...) runs, and denies an approved command with more around it.

A PR counts as ready only on a report with `--ci green` and nothing open after its last hand-over.

**Next:**
- After a week of `ledger approvals --auto` data, revisit the standing classes (kept strict for
  now): re-review bumps after an addendum, "reviewed" notes on other people's PRs.
- The same hooks for repo sessions that run in a prompting mode (`repo_session_settings`), if a
  site needs it; with `--dangerously-skip-permissions` they never prompt.

### Local web console

**Why:** the ledger, the state directory, the logs and the prompt files are only reachable through
`bosun ledger`, tmux panes and asking the orchestrator. That works for one item at a time and
poorly for questions across many: what is open and who it waits on, what last week produced,
which learnings were applied, what the policy says right now.

**What we know:** design in [docs/design/console.md](docs/design/console.md) (accepted
2026-10-20). A read-only, local-only (`127.0.0.1`, a token) page served by `bosun
console` with stdlib `http.server` and one static page, no build step and no new dependencies.
It reads the ledger as a snapshot (never its lock) and never touches the panes. Views: Ledger,
Sessions, Stats, Learning, Library; a knowledge graph is phase 2. Write actions are out of the
MVP; later ones only queue an instruction to the orchestrator. (Since built: the Knowledge view,
whose review decisions are the console's one direct write, local data only; Learning and Library
are now titled Improvements and Config.)

**Steps:** (1) skeleton and security, (2) ledger list, item page and "needs you", (3) sessions and
health, (4) stats, (5) learning and library, (6) polish; then the graph, code-volume stats and
site-file editing.

**Status:** steps 1 to 3 built (2026-10-20): `bosun console` with the security rules, the
shell page and `/api/version`; the Ledger list (state groups, filters, search, sorting, in the URL),
the "needs you" strip and the item page (lineage, links out, outreach, said, history), with
`/api/items`, `/api/items/<id>` and `/api/needs-you`; the Sessions view (a card per window) and the
health panel (watchdog checks, morning and chores runs, events, Slack watcher, login, flags), with
`/api/sessions` and `/api/health`, and windows waiting at a prompt in "needs you". The page got a
small design system (light and dark, state colours, a sticky top bar with the "Needs you" count).
Step 4 (stats) built: `/api/stats?days=7|30|90` with each metric defined in the design note, inline
SVG charts, and a node-based check of `app.js` in a fake DOM (skipped without node). Step 5
(learning and library) built: `/api/learn` (runs, `learn:` items, findings remembered, reports by
key), `/api/library` (repo and site files by opaque key, config masked, effective task types,
memory notes opt-in) and a safe Markdown renderer. The Knowledge view (knowledge MVP 2) built: its
review queue is the console's one write. The Knowledge Graph page got its tabs (2026-10-21):
Briefing (the home), Constellation and Raw data, with the Metro map on the roadmap (4a). Next: step
6, polish.

### Confluence mentions for tech-design reviews

**Why:** the chores find tech-design (TD) review requests only through Slack. Jira and Confluence
notifications reach Slack as app DMs whose text the Slack search tools do not return, so a request
made only by @-mentioning someone on a Confluence page or comment is missed.

**Idea:** in step 3 of `orchestrator/plugin/commands/chores.md`, also search Confluence for pages
and comments in the scan window that mention me (CQL along the lines of
`mention = currentUser() AND lastmodified >= now("-24h")`), and upsert `td:<page-id>` items for
them like the Slack path does.

**To check first:** that the Confluence MCP server's search tool accepts that CQL with the
configured credentials, and how to get my account id if `currentUser()` is not supported. Add the
search tool to the `allow` list of the site settings file (`BOSUN_ORCHESTRATOR_SETTINGS`).

### Outreach follow-ups

**Why:** outreach (`out:` items) shipped with Slack fully handled and the other channels handled
by procedure, not code.

**Open:**
- **Reply detection beyond Slack.** Only Slack replies are matched in code (`ledger seen` finds the
  thread or DM of a sent message). For GitHub and Jira the chores read the comments after the ref;
  Confluence likewise; for email they ask me. Next: match comment URLs (GitHub, Jira) the way
  Slack threads are matched, and read and send email through an email MCP server if one is
  configured (then `email` stops meaning "you send this").
- **Moving outreach to another item.** Work often starts as a mention and continues as a PR
  (`mypr:`). Outreach stays on the mention, which therefore cannot be closed until it is answered;
  a `ledger reparent <out-id> <item>` would move it. (The work itself now moves on as a task
  spawned from the mention, which helps; the outreach's parent is still fixed, since it scopes its
  id.)
- **Repo sessions started without a hand-over** are invisible to the ledger and never got the
  task rules, so they do not know outreach. A short rule in the drain prompt, or a site `CLAUDE.md`, would cover them.
- **One reply for several items.** `ledger sent` takes several ids (one message, recorded on
  each); `ledger replied` does not, since each item needs its own one-line answer. Today: one
  `replied` per item with the same `--evidence`; `record-source` links the permalink to one of
  them. Worth a multi-id form only if that turns out common.
- **Overlap by thread.** `reach-out` warns on the same parent and person; a draft has no thread
  until its message is proposed, so drafts meant for the same thread under different parents are
  only caught by the chores' comparison (step 5).
- **Follow-ups are counted as nudges.** Every message after the first (`ledger sent` on an item
  already sent) counts towards `max_nudges`, also a follow-up that adds to the question rather than
  chasing a reply, so it uses up the nudges. Record which messages are nudges and count only those.
- **Expected-by dates.** `expect_by` is shown, not acted on. If wanted, the chores could propose a
  nudge once it passes, whatever the business-day rule says.
- **More free-text cases:** ambiguous parents (several items fit), the last nudge turning into a
  proposal to give up (`no-reply`), and replies that answer only part of the question.
- **The dry run's guard** comes from the installed `bosun` (the settings' hook names
  `$HOME/.local/bin/bosun`), not from the checkout being rehearsed.
- **When things happened, beyond outreach.** `ledger sent` and `replied` take `--at` (and a
  Slack ref's own time). Other facts are recorded hours late too: two days of real sessions had
  PR merges, approvals and change requests recorded 1 to 16 hours after GitHub's time, and
  messages posted by a repo session or by me recorded at the next check-in. They only live in the
  note today, and nothing computes on them; a `transition --at` (kept as `happened_at` in the
  history entry) is worth it once a report or a rule reads state times.
- **Business days by local date.** `business_days_since` counts UTC dates, so a message sent just
  after local midnight east of UTC (or just before it, west) is counted on the neighbouring day.

### Unattended AWS SSO browser approval

**Why:** `ensure_aws_sso` skips the login while credentials work, and profiles on a shared
`[sso-session]` refresh silently. But when the Identity Center session itself expires, `aws sso
login` opens a browser page that needs a click. Unattended, the morning step times out after
`BOSUN_LOGIN_TIMEOUT` and posts a macOS notification.

**Options considered:**
- **Approve from the phone:** run `aws sso login --use-device-code --no-browser`, capture the
  verification URL and code, and send them to me (for example a Slack DM to myself, or via the
  orchestrator's Remote Control session). This needs no browser automation, and the approval
  still happens with me present. Preferred.
- **Click the button automatically:** drive Chrome, either through AppleScript JavaScript (needs
  Chrome's "Allow JavaScript from Apple Events", which may be blocked by device management) or
  through a headless Claude `--chrome` session. More moving parts, and the identity-provider login
  in front of it (password, MFA) cannot and should not be automated.

**Status:** punted; it only matters when the Identity Center session expires, which the
shared-session setup made rare. On an early run the session had expired and `aws sso
login` still completed unattended in seconds, because the browser was signed in to the
access portal. A click is needed only when that browser session has expired too.

### Hard enforcement for opening new windows

**Why:** starting a Claude session (`bosun delegate ... --new|--start`, which use
`bosun send --new|--start`) is approval-only by policy, not by a permission rule, to avoid a
second prompt after approving the plan. If the orchestrator ignores
the policy, nothing stops it.

**Idea:** make `send --new` require a matching pending ledger proposal (and resolve it), or cap
new windows per day in `bosun send`.

### Watchdog follow-ups

**Why:** the watchdog (`lib/watchdog.py`) started deliberately narrow, to keep false alarms out.

**Open:**
- `ledger-stuck` watches `delegated` (review requests and tasks), counted from the hand-over or
  the latest report, and skips items waiting on open outreach or tasks whose PRs all wait on
  review. `my-pr` items in `addressing` are the same situation for fix-my-pr tasks; add the state
  to `STUCK_STATES` once the threshold has proved quiet. Better: the ledger knows when any
  hand-over awaits a report (`ledger delegation <id>`: `awaiting_report`, `at`, `window`), for
  every kind; `ledger-stuck` could alert on that instead of on states.
- A second sink (for example a webhook URL) is one more function in `SINKS`.
- A watchdog that crashes cannot alert about itself; `bosun doctor` reports a stale last run,
  and its output is in `watchdog.launchd.err`.

### Urgent watchdog alerts outside the awake window (P2)

**Why:** the watchdog sends alerts only on scheduled days between `BOSUN_START_TIME` and
`BOSUN_AWAKE_UNTIL`; anything it finds outside that window is held until the window opens (for
example 08:00). That keeps nights quiet, but some problems may be worth hearing about at once,
such as a Claude window waiting at a prompt while I am still at the desk in the evening.

**Idea:** a config key naming the checks that alert immediately, whatever the time, for example
`BOSUN_WATCHDOG_ALERT_ANYTIME="waiting,ledger-stuck"` (check names from
`bosun watchdog --list`; empty, the default, keeps today's behaviour). The awake-window test
in `lib/watchdog.py` would skip those checks; de-duplication already keeps them to one alert per
problem. The watchdog only runs while the Mac is awake, so this cannot reach me during sleep.
`login-expired` is the first candidate: an expired Claude Code login found in the evening or
on a weekend now alerts only when the window opens (with the morning run's own failure), so
the time until then is lost.

**Status:** not urgent; recorded only.

### Closed lid and API stalls (P2)

**Why:** with the lid closed and no external display, macOS sleeps whatever `caffeinate` asserts,
and only wakes briefly in the background (dark wake). Over one weekend the hourly check-ins kept running in
those brief wakes, and several orchestrator replies in one night ended in "No response from API"
after hanging for minutes each. The
morning run's SSO login then timed out because no browser approval was possible. Nothing failed
loudly: the watchdog saw the orchestrator as `busy`, not stalled, and `bosun doctor` checks the
lid only when run by hand.

**Idea:**
- Check-ins skip while the lid is closed (`ioreg -r -k AppleClamshellState`) or the network is
  unreachable, and log why, instead of starting a turn that hangs on the API.
- A watchdog check: lid closed while sessions run, alerting once per closure, so I know bosun is
  effectively paused (held until the awake window like other alerts).
- A watchdog check for API stalls: a streak of API errors in a window's transcript (read through
  `lib/claude_state.py`), or a window `busy` far longer than a check-in normally takes.
- The morning run skips the SSO browser login when the lid is closed, and says so in its alert.

**Status:** recorded only.

### Request usage credits when a spend cap stops the sessions (P2)

**Why:** a spend cap stops every session and headless call at once: the orchestrator, the repo
windows, background agents and the Slack watcher all wait until someone notices. If your
organization approves `/usage-credits` requests automatically, bosun could ask once per episode
instead of waiting for someone to notice.

**What we know (research 2026-10-21):**
- `/usage-credits` asks for confirmation in a dialog, then notifies the admins. It sends nothing in
  print mode (`claude -p`) or from Remote Control ("run it in an interactive session"), and there
  is no CLI subcommand or API for it. Asking again while a request is pending replies "already
  sent" instead of sending a duplicate.
- Detection: the transcript's synthetic error reply carries `apiError: "usage_limit_reached"` with
  `quotaLimits.rateLimitType: "overage"` (and `overageDisabledReason`), which tells a spend cap
  apart from session or weekly limits that reset on their own. The first normal reply afterwards
  ends the episode.
- Route: a short-lived, dedicated tmux window running plain interactive `claude` (no MCP, no
  Remote Control, a pre-trusted scratch folder): paste `/usage-credits`, press the confirm key only
  on an exact match of the dialog text, read the result line, close the window. Never in a working
  window.

**Idea:** off by default; one request per episode (none while one is pending or within N hours,
only in the awake window, never a retry after an unexpected screen); check-ins and events pause
while capped, like the login gate; a watchdog check alerts "capped at X, credits requested at Y",
and again if still capped later. Needs one supervised real run (with my consent) to record the
dialog's text and key before it can be switched on.

**Status:** researched; deferred as P2 (2026-10-21).

### SQLite ledger (P2)

**Why:** `ledger.json` is rewritten whole on every write under one file lock. That is safe
(verified: 20 concurrent writers of mixed commands lose nothing) and fast at today's size (about
60 KB for 20 items after two days), but every write costs a full rewrite plus a backup link, and
history, reports and sources only grow until `prune`.

**Idea:** move items, history, sources and runs to SQLite (`$BOSUN_STATE_DIR/ledger.db`, WAL
mode), keep the CLI and `ledger.md` as they are, and make `ledger show` / `diff` read the same
shapes. History becomes an append-only table, so trimming (now: the last 200 entries per item)
and per-event queries (`reports --since`) are plain SQL.

**When:** only if writes contend (check-ins and several repo sessions reporting at once), the file
passes a few MB, or we need queries the JSON shape makes awkward.

## Code review backlog (2026-10-15)

From a three-part review (correctness, architecture, tests and docs). Ids are stable; mark items
`[x]` with the commit that fixed them. `[ ]` open, `[~]` partly done.

### A. Safety fixes

Done 2026-10-15; covered by new checks in `tests/smoke.sh`.

- [x] **A1. Never paste into a session that is waiting for you.** Claude Code has a `waiting` status
  (permission prompt or question). `refresh` drained everything not `busy`, and `send` checked no
  status, so the morning drain could press Enter on a pending "post to Slack?" / "merge?" prompt.
  Deliver only to `idle`, `busy` (queued) or `shell`; refuse `waiting` and unknown statuses.
- [x] **A2. One paste buffer per send, and check the paste.** All sends shared one tmux buffer, so
  overlapping sends could deliver the wrong text; paste failures were reported as "delivered" and
  Enter was still pressed.
- [x] **A3. Never relaunch into a split window.** With two panes, the "is anything else running?"
  check failed silently (awk error on a two-line pid list) and `respawn-pane -k` could kill the
  other pane's process. Refuse windows with more than one pane; target the pane explicitly.
- [x] **A4. Reject unknown arguments everywhere.** `stop`, `setup`, `attach`, `status` ignored them:
  `bosun stop --dry-run` closed the session; `setup --dryrun` ran real logins.
- [x] **A5. Stop leaking a frozen config into sessions.** `load_config` exported every setting; Claude
  sessions and the tmux server inherited them, and nested commands treated them as overrides that
  beat `bosun.env`. Edits were ignored until a restart, tests run from inside a session read the
  real `sessions.yml`, and multi-line values were cut to one line.
- [x] **A6. Session lookup must fail closed.** A resolver crash looked like "not found", so `launch`
  started a blank session under the real name; a stale session record with a reused pid made
  `launch` refuse forever.
- [x] **A7. Relaunch in the manifest directory,** not wherever the leftover shell was `cd`'d.
- [x] **A8. The morning run reports window failures** instead of "Sessions ready".
- [x] **A9. Prompts starting with `-`** are parsed as Claude options; pass `--` before the prompt.

### B. Orchestrator logic

B1-B5 done 2026-10-15. B5 also added `bosun guard`, a PreToolUse hook that asks before any
MCP write tool and denies force pushes and `--admin` merges (verified end to end with Claude Code).

- [x] **B1. New-day rollover for the orchestrator** independent of the version-restart path: today
  it stays on yesterday's conversation (with the generic drain prompt) when busy, briefly idle,
  `--drain-only`, or skipped by `--unless-active`.
- [x] **B2. A way out of `addressing`:** after fixes are pushed and re-review is requested, move to
  `awaiting-review` so nudges can fire. Transition to `delegated` before delegating (race).
- [x] **B3. `ledger mark-run` prints the check-in scan start** (later of last check-in and morning
  run), so `chores.md` step 0 is computable.
- [x] **B4. Validate ledger ids against kinds and Slack classes** (`upsert review:x#1 --kind mention`
  succeeds today); `delegate` checks the kind, not only the prefix; `ledger kinds` prints id formats.
- [x] **B5. Permission coverage:** add missing write tools to the `ask` list (Confluence create and
  section update, Jira create/update/edit comment, Slack create conversation, claude.ai Slack canvas
  tools) and have `doctor` flag write-verb MCP tools that are not covered; describe `deny` rules as
  prefix matches (best effort) in the README.
- [x] **B6. Quote `delegate` arguments safely:** the policy shows double quotes around Slack-derived
  text, so `$(...)` would run; use single quotes or a stdin/file option for free text.

### C. Tests

C1 done 2026-10-15: `tests/unit/` (58 tests), run by `tests/smoke.sh`.

- [x] **C1. Python unit tests** for `ledger.py` (proposal lifecycle, dismissed/reopen, evidence rules,
  bad fields, corrupt file), `delegate.py` (render, id checks, malformed task files) and
  `bosun-resolve` (renamed sessions, stale registry, unreadable transcripts).
- [x] **C2. Bash function tests:** schedule maths (`shift_time` past midnight, `seconds_until`,
  launchd calendar for all days/Sunday), the "you are typing" guards, `repo_slug_of_dir` URL forms.
- [x] **C3. Untested paths:** (all but a real, non-dry-run morning run, which would start caffeinate) real paste delivery into a running Claude, `trust` preserving the rest
  of `~/.claude.json` (and mode 600), morning/setup/keep-awake with fake `aws`/`curl`/`caffeinate`,
  `start --restart` and `ON_EXISTING` modes, `chores` option errors, `delegate --window/--start`,
  `pr-threads`, `install.sh`/`uninstall.sh` with fake `launchctl`/`pmset`.
- [~] **C4. Smoke-suite hygiene:** (done except independent sections: they still share state set up by earlier ones) show output of failed checks; make "should fail" checks assert the
  error text; poll instead of fixed sleeps; independent sections; two-way README table check; fix
  the possibly vacuous "restart relaunches in the same window" check; compile `delegate.py`.

### D. Structure and docs

- [x] **D1. One window spec** (name, dir, kind) used by the template, `refresh`, `morning --dry-run`
  and `doctor`; `refresh` recreates a missing `checkin` window; one launch path (consider dropping
  tmuxinator for `new-session` + `build_launch_command`); one env passthrough list.
- [x] **D2. One module for Claude Code internals** (sessions registry, transcripts, trust) and one
  repo-mask implementation, instead of copies in bash and Python; `chores --dry-run` diffs the
  ledger through `ledger.py`, not by reading its JSON.
- [x] **D3. Shared `--help` helper** that prints the leading comment block (seven commands print
  the wrong lines today); consistent exit codes (2 for usage errors).
- [x] **D4. Config docs:** the generic example lists every key (12 missing); other examples
  hold only overrides; rename `BOSUN_DRY_RUN` (setup) to a defaulted, consistent name.
- [x] **D5. Doc fixes:** README ledger commands, `resolve <dir> --list` form, `gh` in
  requirements, `install.sh --with-wake`, restart on any version difference, nudge rule is
  prompt-only; `chores.md` vs `review.md` on `delegated`; `send` help on busy delivery; policy on
  trust errors; `LOGIN_TIMEOUT` comment. (The examples' "label" comment is part of D4.)
- [x] **D6. Dead code:** (the tmuxinator copy went with D1; tmuxinator is no longer used) the review `prepared` state, the unused `~/.config/tmuxinator` copy, the
  duplicated keep-awake pidfile path, `ledger.py`'s own `STATE_DIR` default.
- [x] **D7. Site overlays:** let the config dir add task types and an extra policy file, so a second
  user or team does not need to fork.

## To verify

- **Messages to a busy session:** once a message sent with `bosun send` while the
  session was busy and you were also typing to it (from the phone or tmux) never reached the
  conversation. Another message sent while busy, with nobody typing, arrived mid-turn. Reproduce
  with two senders; if it is a race with the input box, make `send` wait for `idle`, or check the
  transcript afterwards and retry.

- **Phone approvals:** whether permission prompts raised by the orchestrator's `ask` rules can be
  answered from the Claude mobile app through Remote Control. Approving in chat works either way;
  with approvals off, the hard gate depends on this (with `BOSUN_APPROVALS_ENABLED=1` the guard
  is the gate, and a prompt appears only when a hook fails or decides nothing).
- **Remote Control after sleep:** whether a session reconnects after a long sleep without a
  restart. Sessions that `bosun refresh` restarts get a fresh connection.
