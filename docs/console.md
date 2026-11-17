# Console

`bosun console` serves a local web page over what bosun knows: the ledger, the windows, bosun's own health, its
stats, what learning proposed, the ontology, and what bosun changed for you (Impact). It runs on your Mac only, and
it is read-only but for one thing, the ontology's review. Design: [design/console.md](design/console.md).

## Opening it

- **From the menu bar app:** Open Console opens it, signed in, in your browser.
- **In a terminal:** `bosun console` runs it until Ctrl-C and prints an address such as
  `http://127.0.0.1:8765/?t=<token>`; open it, or pass `--open`. While a console already runs, `bosun console --url`
  prints its address, token included ("the console is not running", exit 1, when none runs).

With `BOSUN_CONSOLE_ALWAYS=1` (which `bosun init` sets with the menu bar app), the console runs as a background job
from `bosun start` until `bosun quit`, so it is there whenever bosun is.

The top bar shows how many things need you, on every view. The page follows the system's light or dark mode, works
down to a narrow window, and refreshes when the ledger changes (checked every 15 seconds).

## The views

| View | Address | What it shows |
|---|---|---|
| Ledger | `#/ledger` | Every item with its kind, state, window, what it waits on and when it changed, filtered, searched and sorted; above it, **Needs you**: what waits for your decision |
| Item | `#/item/<id>` | One item: its fields, hand-over, latest report, outreach, pending proposal, what you said about it, its lineage, links and history |
| Sessions | `#/sessions` | A card per window, with its agent's status and the items it holds; below, **Health**: the watchdog's checks, the morning run, events, the Slack watcher, the login |
| Stats | `#/stats?days=7\|30\|90` | Items opened and closed, pull requests merged, reviews, tasks, hand-overs, outreach, and bosun's own runs and spend |
| Improvements | `#/learning` | Learning's runs, every `learn:` proposal with its evidence and change, and the findings learning remembers |
| Ontology | `#/knowledge` | The ontology: a briefing, a graph of everything, the raw cards and search, each entity's page, and the daily review (`#/knowledge/review`) |
| Config | `#/library` | The files that steer bosun, read-only, with secrets masked |
| Impact | `#/impact` | What bosun changed, week by week, against your own baseline ([below](#impact)) |

<details class="note" markdown>
<summary>Ledger, Needs you, and the item page</summary>

- **Ledger:** every item with its kind, state, window, what it waits on and when it changed, filtered by kind, group,
  window, person (requester or outreach recipient) and update time, searched over ids, titles, fields, notes and
  reports, and sorted by update, creation or kind. The filters live in the address, so a view can be bookmarked.
  Groups: *needs me* (a pending proposal, outreach whose message waits for approval, a proposed task, a ticket that
  disagrees with our work), *waiting on someone* (my review or pull request with the other side, outreach sent, open
  outreach or blockers), *open* and *closed*; the default shows every open item, as `ledger list --open` does.
- **Needs you**, above the list: windows waiting at a prompt, each pending proposal with its exact action, proposed
  outreach, and the items a ticket mismatch asks you to decide on (one entry per item, as the *needs me* group counts
  them, with the tickets behind it).
- **Item page:** fields, hand-over, latest report, outreach (to whom, what went out, nudges, reply, how long it
  waited), the pending proposal, what you said about it in a repo window, the lineage tree and links (duplicates,
  folded into, blocked by, tracks, and their inverses) that go to other items, every web link in the item labelled
  GitHub, Jira, Confluence, Slack or Web, and the history. Only `http(s)` links are clickable; they open in a new
  tab.

</details>

<details class="note" markdown>
<summary>Sessions and Health</summary>

A card per window (`bosun sessions --json`): its kind, folder, model and effort, its agent's status (idle, busy,
shell, waiting) and for how long, the items it holds, its active item and whether it works on it now, its last
report, its login verdict, and the `tmux attach` command with a copy button.

Below, **Health**: the watchdog's checks (as `watchdog --status` shows them; nothing is alerted or recorded), today's
morning run and the last chores runs, the events dispatcher (deliveries today against `BOSUN_EVENTS_MAX_PER_DAY`,
events pending), the Slack watcher (last run, spend today against its daily cap, failures), the login (the last login
check, windows answering only with login errors) and the feature switches (events, Slack watcher, learning: on or
off). It never starts Claude, so whether the stored login works right now is left to `bosun status`. The view
reloads every 15 seconds while visible.

</details>

<details class="note" markdown>
<summary>Stats and Improvements</summary>

- **Stats**, from the ledger's history: items opened and closed per day by kind, your pull requests merged and the
  median time to merge, review rounds done and their turnaround, tasks spawned, accepted and done, hand-overs per
  window and the median time to the report, outreach sent, replied, unanswered and waiting with the median reply
  time and nudges per reply, the people reached out to and worked for (top 10; names stay local), and bosun itself:
  the last chores runs, event deliveries, Slack-watch and learning runs and their spend per day. Each metric is
  defined in the design note. Charts are inline SVG, each with a text summary and its numbers in a data table.
- **Improvements:** learning's runs, live and dry (when, outcome, findings, proposals, cost) with a link to each run's
  report, rendered in the page; every `learn:` proposal with its finding, evidence, reason, the exact change and where
  it goes, and when and how it was applied or rejected; and the findings learning remembers, with how often each was
  seen.

</details>

<details class="note" markdown>
<summary>Ontology</summary>

The ontology in three tabs:

- **Briefing** (the home): facts captured per day, the confirmed share, coverage per card, what changed this week
  (before and after) and the new facts, today's review as a card stack (Y/N/S keys or drag, Z undoes), and the most
  connected entities.
- **Constellation** (`?view=constellation`): the whole ontology as a force-laid graph with hover, a facts panel,
  search, card chips and an "as of" slider (at most 400 entities).
- **Raw data** (`?view=raw`): four cards, People, Process, Product and Systems (the system, service and environment
  types), with entity counts, confirmed and proposed facts and what changed last, and a search over names, aliases
  and fact text with a type filter. A disabled Metro tab marks the map still on the roadmap.

An entity's page (`#/knowledge/<key>`) shows its current facts by predicate with who stands behind each (user,
stakeholder, observed, inferred), its status, dates and sources (web links and ledger items link; a memory note links
to the Config view when `BOSUN_CONSOLE_MEMORY_DIRS` lists its folder; a session shows its name; anything else is
text), the facts that no longer hold (greyed, with what replaced them), the facts that point to it, related ledger
items (a person as requester or outreach recipient, a repo by its items), a process's steps as an ordered flow
(actor, gates, entry points, systems), and a small graph of what lies within two links. A person's page shows only
work facts and how to have them deleted (`bosun kg forget`); restricted identifiers are never shown.

The review (`#/knowledge/review`) is the batch `kg batch` uses, lettered the same way: confirm or reject a group or a
fact, skip a fact, or confirm a group except the facts you tick. Decisions are recorded as yours, from the console,
with the time, through the same code as `kg batch apply`; once every fact of the batch `kg batch` printed is decided,
it is marked answered there too. The view reloads when the ontology changes (the review only while nothing is
ticked). Without an ontology yet, the view says how to start one.

</details>

<details class="note" markdown>
<summary>Config</summary>

The files that steer bosun, read-only: the repo's policy, chores, task types and prompts, and your own from
`$BOSUN_CONFIG_DIR` (policy, task types, `sessions.yml`, the orchestrator settings, `bosun.env`). Each task type says
whether it is the one `bosun delegate` uses or a file of yours replaces it. Config values whose names contain TOKEN,
SECRET, KEY or PASSWORD, and webhook URLs, are masked; your files are also redacted as learning's digest is. Markdown
is rendered (its front matter as a table). Claude Code memory notes appear only if you list their folders in
`BOSUN_CONSOLE_MEMORY_DIRS` (colon-separated).

</details>

## Running it in the background

With `BOSUN_CONSOLE_ALWAYS=1` (default `0`), the console runs as a launchd job, which `install.sh` loads and `bosun
update` renders again and restarts, so it serves the new version. `bosun start` makes `console/wanted` in the state
folder, and the job runs while that file is there: `bosun quit` removes it and stops the console, so a quit leaves no
console running, and the job does not come back (not at a reboot either) until the next `bosun start`. Turned off
again, `install.sh` removes the job.

A `bosun console` you run by hand while the job serves prints the job's address (`--open` opens it) instead of failing
on the port; `--new-token` is refused then, since the running console keeps its token. The job never exits for a
console you run by hand: while yours holds the port, the job waits (it looks again every few seconds, and says so
once) and serves once yours ends. `bosun doctor` has a line for the job: loaded, and serving while `console/wanted` is
there. The job's errors go to `console.launchd.err` in the log folder, never its address.

A console keeps the code it started with. `bosun update`'s plan says to restart one you started by hand (the job is
restarted by the update), and a review decision it can no longer save after an update says so, and to restart it.

## How it stays private

- It listens on `127.0.0.1` only, on `BOSUN_CONSOLE_PORT` (8765) or `--port N`; `--port 0` picks a free port. A port
  in use is an error, not a silent fallback.
- The first visit swaps the address's token for a cookie, and the API answers 401 without it, so other local
  programs and pages cannot read the ledger by finding the port. `bosun console --new-token` makes a new token (signing
  out every tab).
- Read-only: it serves only its own files and answers only reads, apart from the ontology's review decisions, which
  need the cookie, the page's own origin and a token only the review page can read.
- It logs only refused requests, never ledger content or item ids. It never sends keys to a window, reads a window's
  text, or starts an agent.

<details class="note" markdown>
<summary>The token, the requests it takes, and how it reads</summary>

- **The token** becomes an HttpOnly, SameSite=Strict cookie and leaves the address bar. It is kept across restarts in
  `console/token` in the state folder (file 0600, folder 0700), so an open tab stays signed in when the console
  restarts. A stored token that others could read, or a damaged file, is replaced by a new one, and
  `BOSUN_CONSOLE_PERSIST_TOKEN=0` makes it new on every start. A tab left open from before a new token (a reinstall,
  `--new-token`) shows "This link is out of date" and stops polling; open a fresh link from the menu bar icon's Open
  Console, or with `bosun console --url`.
- **Requests:** GET and HEAD only, with a strict Content-Security-Policy. The one exception is a review decision
  (`POST /api/knowledge/review`): local data, never outward-facing, and only with the cookie, this page's Origin, a
  JSON body and a per-start CSRF token that only the review page can read. Config files, learning reports and
  ontology entities are served only by an opaque key from a list the console builds itself: no path from a request
  ever reaches the filesystem.
- **Reading:** it reads `ledger.json` as a snapshot (never its lock), again when the file changes. It reads the
  windows as `bosun sessions --json` and the watchdog do (list them, read each agent's session records), at most
  every 10 seconds. Refused requests are logged to stderr.

</details>

## Impact

The Impact view (`#/impact`) asks what bosun changed, not what it did. It compares the weeks since you started using
bosun with your own baseline before it, shown as a band:

- pull requests merged per week, and time to merge;
- reviews given per week, and time to your first review;
- agent busy hours against your own touch time (leverage), week by week;
- what learning has produced (runs, findings that came back, proposals, the ontology's growth);
- cost per week and per merged pull request, and where agent time went per window, with the pull requests behind
  each week as links.

Estimates (your touch time, at `BOSUN_IMPACT_PROMPT_MINUTES`, 2, per prompt you typed; cost at list prices) carry an
"estimate" badge and a range, every number says how many weeks it rests on, and fewer than four weeks since the start
is marked early data. Other people are never named. Codex and Antigravity windows are not counted yet
([what they lack](design/harnesses.md#what-windows-do-not-have-yet)), so with them leverage and cost come out low.
Design: [design/impact.md](design/impact.md).

The view reads the snapshot `bosun impact collect` writes to `impact/` in the state folder: run it by hand, or daily
from the morning run with `BOSUN_IMPACT_COLLECT=1`.

<details class="note" markdown>
<summary>Where the numbers come from</summary>

The start is `BOSUN_IMPACT_START`, else the ledger's first item; the baseline is from `BOSUN_IMPACT_BASELINE_FROM`,
else 13 weeks earlier. Leverage before the start is shown against since, where transcripts from before exist. The
snapshot reads GitHub only through `gh`, over `BOSUN_IMPACT_REPOS` or the ledger's repos, and transcripts through the
same reader as the rest of bosun, cached per file.

</details>

### Overhead

The **Overhead** panel shows what bosun itself costs. Every turn's tokens count against what started it: your
requests, tasks handed to a window and subagents are work; check-ins, events summaries, background-task notifications,
resume prompts, messages between sessions, prompts Claude Code sends on a schedule (a session's own cron or wake-up)
and the prompt openings in `BOSUN_IMPACT_OVERHEAD_PREFIXES` (a site's own scheduled prompts, `|`-separated) are
overhead.

It shows the overhead share over the last 7 days against the 7 before, the cost per check-in and per notification,
the context a turn re-reads (median and p90; a long conversation makes every turn there expensive), and the cost per
week by what started each turn. The watchdog's `context-size` and `overhead-share` checks warn when either grows.

**Bosun internals** (`#/impact/internals`, linked from the page) has the context per window, the cost per turn by
week (check-in, notification, resume, events), and guardrails (tasks reported and done, hand-over to report time,
approval failures, outreach answered, events delivered and waiting, check-ins sent and skipped) that show the work
did not get worse when the cost fell. `BOSUN_IMPACT_OVERHEAD_FIXED_AT` (a date) adds a before-and-after table there
for the week on each side.

### Cost per task

The **Cost per task** panel shows what each ledger item cost, its subagents included: by outcome (done, merged,
dropped, open), task type and kind, and the most expensive items next to the pull request they produced.
`BOSUN_IMPACT_TASKS_TOP` (15) sets how many items it lists. A model missing from the price table is priced as Opus,
and named on the panel.

A turn counts for the item its hand-over names, else the item the window reported on next, else the window's active
item, as long as that window's last ledger event is within `BOSUN_IMPACT_ATTRIBUTION_HOURS` (8); the rest stays
unattributed per window (the orchestrator's coordination, work outside the ledger).
