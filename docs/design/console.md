# Design note: bosun console (web UI)

Status: accepted (2026-10-20); step 1 (skeleton and security), step 2 (Ledger), step 3
(Sessions and health, with the visual refresh), step 4 (Stats) and step 5 (Learning and Library)
built; the Knowledge view (section 6, the knowledge note's MVP 2) built 2026-10-21, with the
console's only write: a knowledge review decision. Tracked in
[ROADMAP.md](../../ROADMAP.md#local-web-console).

## Why

Everything bosun knows lives in the ledger, the state directory, the logs and a handful of
prompt files, and today the only ways in are `bosun ledger list/show/tree`, tmux panes and
asking the orchestrator. That is fine for one item at a time and poor for the questions that span
many: what is open and who is it waiting on, what did the last week produce, which learnings were
applied, what exactly does the orchestrator's policy say right now. A local, read-only web console
answers those without adding load to the sessions.

## Goals and non-goals (MVP)

Goals:
- Browse and search the ledger, follow links between items and out to GitHub, Jira and Slack.
- See the live sessions and bosun's own health on one page.
- A small stats dashboard derived from data we already record.
- See learning runs and `learn:` proposals, and what became of them.
- Read the editorial content (policy, chores, task types, prompts, site overlays, config).

Non-goals (MVP): any write action (approving proposals, sending, editing prompts, ledger changes),
remote/phone access, multi-user. One exception came later, by decision 2 of the knowledge note:
confirming, rejecting or deferring proposed knowledge facts in the Knowledge view's review queue
(section 6), which is local data and never outward-facing.

The read-only line is deliberate: every outward-facing action goes through the orchestrator
conversation and its approval tiers today. A UI button that approves a Slack message would be a
second, unaudited path around that. Actions come later and only through the same commands.

## Constraints

- **No new dependencies.** The tool is bash plus stdlib Python 3.13. The server is
  `http.server.ThreadingHTTPServer`; the front end is one static HTML page with vanilla JS and CSS,
  no build step, no CDN (corporate proxy, offline use, nothing loaded from outside).
- **Generic repo.** No site content in code or fixtures. The console shows whatever the local
  config and state contain; fixtures stay fictional (the acme world).
- **Never disturb live sessions.** Reads only. The ledger is read as a snapshot (it is replaced
  atomically on write; the console never takes the write lock). Session state comes from the same
  readers `bosun sessions --json` and `status` use, which do not touch the panes.
- **Local only.** Binds `127.0.0.1`. Data includes colleagues' names and untrusted Slack excerpts;
  it never leaves the machine.

## Architecture

```
bosun console [--port N] [--open]
  libexec/bosun-console      -> parses options, starts lib/console_server.py
  lib/console_server.py          -> HTTP: static files, JSON API, auth, headers
  lib/console_data.py            -> the ledger snapshot, /api/version, what the views share
  lib/console_ledger.py          -> pure: the Ledger views (reuses ledger, lineage, activity, tickets)
  lib/console_live.py            -> the only I/O of Sessions and health (cached; see section 2)
  lib/console_sessions.py        -> pure: window cards, waiting windows, health
  lib/console_stats.py           -> pure: the Stats metrics (section 3)
  lib/console_state.py           -> reads bosun's own state files: events, Slack watcher, learning
  lib/console_learn.py           -> pure: the Learning view (runs, learn: items, findings)
  lib/console_library.py         -> pure: Library entries, masking, front matter, effective badges
  lib/console_files.py           -> the Library's file list (fixed roots, opaque keys) and reads
  lib/console_knowledge.py       -> pure: the Knowledge view (cards, search, entity pages, flows,
                                    review batch, decisions)
  lib/console_kg_graph.py        -> pure: the neighbourhood graph's walk and radial layout
  lib/console_kg_views.py        -> pure: the Knowledge Graph page's Briefing and Constellation
  lib/console_kb.py              -> the Knowledge view's only I/O: reads the knowledge base (never
                                    identifiers.jsonl), saves a review decision
  lib/console_kg_sources.py      -> readable fact sources: a memory note's title and Library link,
                                    a session's name (cached by file and modification time)
  console/index.html, app.js, app.css   -> hash-routed single page (#/ledger, #/item/<id>, ...)
```

- The view models are pure over loaded data, like `lineage.py` and `activity.py`, so they and the
  stats are unit-tested against fixture ledgers and hand-made facts without a server.
- The server re-reads the ledger when its mtime changes (cheap; about 100 items today, fine to
  thousands), and the page polls `/api/version` every 15 s and refreshes the open view on change.
- Run on demand (`bosun console --open`). An optional `console` window in the tmux session can
  come later if it proves useful.

### Security

- Bound to `127.0.0.1` only; refuses to start on another address (`--host` exists only to be
  refused, so a mistaken bind fails loudly).
- A random token per start, printed in the URL (`http://127.0.0.1:8765/?t=...`), exchanged for an
  `HttpOnly; SameSite=Strict` cookie on first load, then a redirect drops it from the address bar.
  The cookie's name carries the port, since cookies are not kept apart by port. Every API call
  needs it (other local processes and browser tabs cannot read the ledger by guessing the port).
- The token is kept across restarts (before, every restart after a merge signed the open tab
  out with 401s). `lib/console_token.py` stores it in `$BOSUN_STATE_DIR/console/token`,
  written atomically, the file 0600 in a 0700 folder, and reuses it on start. A file that is
  missing, unreadable, malformed or readable by group or others is replaced by a new token
  rather than reused or fixed in place, since a token others could read may already be known.
  `bosun console --new-token` rotates it, and `BOSUN_CONSOLE_PERSIST_TOKEN=0` restores
  a new token per start. The token is printed only in the startup URL, never logged. The CSRF
  token stays per start: a page open across a restart gets a 403 on a review decision until it
  reloads, which it says.
- There is no way to turn the token off (the first sketch had `--no-token` for tests). The tests
  do not need one: the smoke test reads the token from the printed URL like a user does, and the
  unit tests build the server with a known token. A switch that disables the only access control
  is one more thing to leave on by mistake.
- `Host` header must be `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding).
- GET and HEAD only; any other standard method is 405 (methods HTTP does not define get the
  stdlib's 501). The one exception is `POST /api/knowledge/review`, a knowledge review decision
  (section 6); a POST anywhere else, and any other method there, is still 405. A GET never
  writes, whatever its query says.
- **The write's guards**, checked in this order before the body is read: the Host header, the
  cookie (401), an `Origin` header that names this server, `http://127.0.0.1:<port>` or
  `http://localhost:<port>` (403; a missing one is refused too), `Sec-Fetch-Site`, when the browser
  sends it, must be `same-origin` (403), `Content-Type: application/json` (415; an HTML form cannot
  send it, and a cross-site script that sets it needs a CORS preflight the server never answers),
  and the `X-Bosun-CSRF` header equal to the CSRF token (403). That token is random per start,
  different from the cookie's, compared in constant time, and handed out only in the JSON of
  `GET /api/knowledge/review`, which another origin cannot read (no CORS headers, and the cookie is
  `SameSite=Strict`). The body must be a small JSON decision (at most 64 KB, `{"action":
  "confirm"|"reject"|"defer", "facts": ["f12", ...]}`; 413 when larger, 411 without a
  Content-Length, 400 otherwise), and every fact must be
  waiting in today's batch (409), checked under the knowledge base's own lock.
- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'` (plus
  `base-uri`, `form-action` and `frame-ancestors` closed), no inline script or style;
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store`
  on the API. All ledger text is rendered with `textContent`, never `innerHTML` (Slack excerpts, PR
  titles and notes are untrusted).
- Files are served from an allowlist (the static assets, read once at start, and later the
  editorial files listed below); no path from the request reaches the filesystem directly, so
  traversal is just an unknown path (404).
- Only refused requests are logged (method, path without the query, status), never ledger content
  or the token.
- Config is shown through `lib/redact.py` plus a key-name rule (`*TOKEN*`, `*SECRET*`, `*KEY*`,
  webhook URLs masked).

## Views

### 1. Ledger (`#/ledger`, `#/item/<id>`)

**List.** One row per item: id, kind, state, title, window (`@exporter`), waiting-on, updated.
- Filters: kind (task, my-pr, review-request, td-review, mention, outreach, ticket, learning,
  workstream); state group
  (open / waiting on someone / needs me / closed, mapped from each kind's state machine in
  `ledger.py`); window; requester or recipient; updated within.
- Search: substring over id, title, fields, notes and report summaries.
- Sort: updated (default), created, kind.
- Filter state lives in the URL hash, so a filtered view can be bookmarked.

**State groups** (built in step 2, `lib/console_ledger.py`). Every item is in exactly one, checked in
this order:

| Group | When | Derived from |
|---|---|---|
| closed | the state is terminal for its kind | `KINDS[kind]["terminal"]` |
| needs me | a pending proposal awaiting my approval (none yet, or its approved call failed: `approvals.needs_you`); a state only I move on (outreach `proposed`: `requires_proposal`; a task in `proposed`: leaving it needs evidence from me, `evidence_from_me`), unless its proposal is approved and not used yet or carried out (bosun makes or made the call: `approvals.proposal_status`); a mismatch of a ticket in a project I own (`BOSUN_TICKET_OWNED_PROJECTS`; empty: all) | `KINDS`, `approvals`, `tickets.mismatch_hints`, `tickets.owned` |
| waiting on someone | review-request `reviewed` and td-review `commented` (the author), my-pr `awaiting-review` (reviewers), outreach `sent` (the recipient); or the item has open outreach or open items it is `blocked_by` (as `ledger list` shows "waiting on") | `WAITING_STATES` in `console_ledger.py` |
| open | any other open state: with me or with bosun | |

- A ticket mismatch flags the side to change: an open ticket (move it on), or the open work that
  tracks a closed ticket (close it, or ask whether the ticket closed by mistake). The staleness
  hints of `tickets --mismatch` are left out: re-reading a ticket is the orchestrator's chore.
- The list's group filter also takes `active` (every group but closed: the same items as `ledger
  list --open`, and the page's default) and `all`. A unit test checks that every state of every
  kind lands in a group, and that `WAITING_STATES` only names open states of real kinds.
- Filters: `kind`, `group`, `window` (the delegation's), `person` (case-insensitive substring of a
  requester, an outreach recipient, or the recipient of the item's outreach), `since` (updated
  within: `90m`, `24h`, `7d`, `2w`, or an ISO time), `q` (every word must occur, case-insensitive),
  `sort` (`updated`, `created`, `kind`). An unknown value is 400.

**"Needs you" strip** at the top: windows waiting at a prompt, and the items of the *needs me*
group: proposals awaiting my approval (with the exact proposed action), outreach in `proposed`
whose message is not approved yet, and the items a mismatch of a ticket I own asks me to decide on.
This answers "what am I holding up" at a glance.
- Mismatches count per item, as the *needs me* group does: one entry per flagged item (the open
  ticket, or the open work tracking a closed one), with the tickets behind it and their hints. The
  count is of distinct items (an item with a proposal and a mismatch counts once) plus the waiting
  windows, so with no filter it equals the *needs me* group plus those windows.
- The windows come from the Sessions view's facts (status `waiting`, longest first);
  `windows: null` when the session readers failed.
- The top bar shows the same count on every view.

**Item page.**
- Header: id, kind, state, title, external URL (PR, ticket), created and updated times.
- Fields; delegation (window, task type, when, awaiting report or not); latest report (summary,
  result).
- Outreach block for `out:` items: channel, recipient, purpose, subject, sent ref, nudges, reply
  summary, waiting time.
- Lineage: the `tree` rendered as a clickable tree, plus `duplicates`, `absorbs`, `folded into`,
  `blocked by` and `tracks` links as chips.
- External anchors: every URL found in the item (its `url`, Slack permalinks from `sources` and
  history evidence, Jira and GitHub links), labelled by system. Only `http(s)` URLs with a host
  become links (opened in a new tab, `rel="noopener noreferrer"`); anything else, such as a
  `javascript:` URL in a `url` field, is shown as text.
- The page lives at `#/item/<id>` with the id URL-encoded (ids hold `:`, `/` and `#`); the API
  path is `/api/items/<encoded id>`, and refused-request logs mask the id.
- What I said about it (`said`), and the pending proposal.
- History timeline: event, time, note, collapsible.

### 2. Sessions (`#/sessions`)

- One card per window (from `sessions --json`): repo directory, model and effort, Claude status
  (idle, busy, shell, waiting) and for how long, held items, active item and whether it is
  working, last report. The `tmux attach` command can be copied.
- Health panel: the watchdog table (`watchdog --status` as data), last check-in and morning run,
  events dispatcher, Slack watcher (last run, spend today against its cap), login state, enabled
  feature flags.
- Auto-refreshes every 15 s.

**Built in step 3.** `lib/console_live.py` collects the facts; `lib/console_sessions.py` turns them
into `/api/sessions` and `/api/health`.
- Sources, all read-only: `bosun sessions --json` (whose Claude record now carries
  `statusUpdatedAt`, for "for how long"), `bosun watchdog --facts` (new: the facts the
  watchdog collects from tmux, ps and launchd, as JSON), `watchdog.status_data` (the data behind
  `--status`: no alert, no de-dup state, no lock), the events state and queue, the Slack watcher's
  state, `login.json`, today's morning log, the ledger's `runs` (`mark-run`: the last morning and
  check-in chores) and each window's transcript for its login verdict. `bosun console` passes
  the settings these need explicitly (the watchdog's list in `WATCHDOG_SETTINGS`, the tmux socket,
  the two daily limits); flags reach the page as booleans only, never other config values.
- One collection serves both endpoints and "needs you", kept for 10 s; requests that arrive during
  a collection wait for it. A failing reader leaves its part empty with the reason (`error`), the
  rest still shows.
- Left out, because only a side effect could tell: whether the stored login works right now
  (`claude auth status` starts Claude Code, and the login check request costs money; the
  watchdog's login check runs with "not asked" in its place, and the panel shows the last login
  check and the windows' latest replies instead), and a window's visible text (capture-pane).
  The panel lists these under "Not shown here".
- The page reloads the view every 15 s while it is visible (not in a background tab).

### Look and feel (step 3)

One stylesheet with a small design system in CSS custom properties at its top: a spacing scale,
two radii, a neutral palette with one accent, and state colours whose text meets WCAG AA (4.5:1)
on their own background, light and dark through `prefers-color-scheme`. Ledger groups have their
colours (needs me: amber, waiting: blue, open: green, closed: grey), and so do Claude statuses
(idle: green, busy: violet, shell: teal, waiting: amber, no Claude: grey). Cards with subtle
borders and shadows, a sticky top bar with the views and the "Needs you" count, a two-column item
page on wide screens (content, and a side panel with waiting on, delegation, lineage and links
out), focus rings on `:focus-visible`, a skip link, and a layout that drops optional columns on a
narrow window. System fonts only; no animation beyond short colour transitions (off with
`prefers-reduced-motion`). Under the strict CSP nothing is styled from data: bars use width
classes in 10% steps, not style attributes.

### 3. Stats (`#/stats`)

Period: 7, 30 or 90 days. MVP metrics come only from ledger history:
- **Throughput:** items opened and closed per day, by kind (stacked bars).
- **PRs:** my PRs merged, median time from open to merge, review requests done, and review
  turnaround (requested to reviewed).
- **Tasks:** spawned, accepted and done; median time from delegation to report; tasks per window.
- **Outreach:** sent, replied, no-reply, median reply time, nudges per reply.
- **People:** distinct people we reached out to or worked for (outreach `to`, requesters), top 10
  with counts. Names stay local.
- **Bosun itself:** check-ins delivered or skipped, alerts by check, learn and Slack-watch
  spend.

Charts are inline SVG drawn by `app.js`, with no chart library. Phase 1.5 adds code volume
(additions and deletions of merged PRs, fetched once with `gh pr view --json additions,deletions`
and cached in the state directory).

**Built in step 4.** `lib/console_stats.py` (pure) computes `/api/stats?days=N` from the ledger
snapshot and what `lib/console_state.py` reads of bosun's own state files (read-only, through
the writers' own `State` loaders). `days` must be 7, 30 or 90 (default 7; anything else is 400).

Definitions:
- **Period:** the last N local days (the server's time zone), today included, up to now. A day's
  bucket is the local date of the time.
- **A time** is a history entry's `happened_at` (the real time, when `--at` or a Slack ref gave
  one), else its `at`. An ISO time without an offset counts as UTC. A time that does not parse
  leaves that one metric out; `odd_times` counts them (shown on the page), so a bad entry never
  breaks the view.
- **First recorded:** an item's `created_at`, else its first history entry. It is when the ledger
  learned of the item, not when the PR or request was made elsewhere.
- **Opened:** items first recorded in the period, per day and kind. **Closed:** items now in a
  terminal state of their kind (`KINDS[kind]["terminal"]`), on the day they last moved into one
  (falling back to `updated_at` when the history does not say). A closed-then-reopened item is not
  closed; one closed twice counts once, on its last close.
- **My PRs merged:** `my-pr` items now `merged` whose move to `merged` falls in the period;
  **median open to merge:** first recorded to that move.
- **Review rounds done:** moves of a `review-request` or `td-review` item into its kind's
  `work_done` state (`reviewed`, `commented`) in the period; a re-review is a round of its own.
  **Turnaround:** from the round's ask (first recorded, or the move to `rereview-requested`) to
  that move; the median over the rounds done.
- **Tasks:** *spawned* = `task` items first recorded in the period; *accepted* = a `proposed ->
  accepted` move in the period (taking a task back from a window is not one); *done*, *declined*,
  *dropped* = tasks now in that state that moved into it in the period.
- **Hand-overs:** `delegated` and `moved` history events (any kind) in the period, per window
  (`moved` counts for the window it moved to): "tasks per window". **Median hand-over to
  report:** from a hand-over to the next `report` event on the same item, for reports in the
  period.
- **Outreach:** the cohort is outreach whose first message (`fields.messages[].at`) went out in
  the period; one answered before any message was recorded was never sent and is not counted.
  *One-way* = purpose `fyi`. Of the two-way ones: *replied* (an `answer`, or state `replied`),
  *no reply* (state `no-reply`), *waiting* (still `sent`), *dropped*. **Median reply time:** first
  message to `replied_at` (else the last move to `replied`); a reply recorded before its message is
  left out. **Nudges:** follow-up messages (messages after the first); **nudges per reply:** the
  replied items' nudges divided by the replies.
- **People:** *reached out to* = the recipients (`to`) of the outreach cohort; *worked for* = the
  requesters (`fields.requester`, `delegation.requester`) of items first recorded in the period.
  A field naming several people is split at `,`, `;`, and the words `and`, `&`, `+`, `/`; a note
  in parentheses ("Dana (finance)") and a leading `@` are dropped; names count case-insensitively,
  once per item, shown as first spelt. Distinct counts and the top 10. Names stay on the machine.
- **Bosun itself:** the last run of each chores name the ledger records (`runs`: it keeps only
  the latest, so there is no history of check-ins to count); event deliveries in the period by how
  they went out (`send`, or covered by a `check-in`) and the events in them; Slack-watch runs
  (and failed ones) and its daily spend; learning runs, dry runs (from their folders) and failed
  runs, and their cost; spend per day (Slack watcher and learning, stacked). The events and Slack
  watcher state keep 7 days, which the page says.
- Left out: alerts by check (the watchdog keeps only de-dup state, not a history), and code volume
  (phase 1.5, through `gh`).

The page has a period picker (7, 30, 90 days, in the URL: `#/stats?days=30`), summary tiles with
sparklines, stacked bars for opened and closed per day by kind and for spend, and horizontal bars
for hand-overs per window, outreach outcomes and people. The chart helpers in `app.js` (`svg`,
`stackedBars`, `bars`, `sparkline`) build SVG with `createElementNS`: geometry in attributes,
colours from classes (`.series-0` to `.series-7`), no style attributes. Each chart is `role="img"`
with a `<title>` and `<desc>`, has a text summary under it and its numbers in a table behind a
"Data table" toggle.

### 4. Improvements (`#/learning`; called Learning before 2026-10-21)

- Runs: date, dry run or live, finding count, cost, and a link to its `report.md`.
- `learn:` items: finding, evidence, proposed change (target file and diff text), status
  (proposed, applied, done, rejected with reason), and when and where it was applied.
- Findings remembered (the de-dup state), so repeats are visible.

**Built in step 5.** `/api/learn` (`lib/console_learn.py`, pure, over what `lib/console_state.py`
reads): live runs come from `learn/state.json` (`runs`: started, outcome, findings, proposals,
cost, error), dry runs from their folders under `learn/dry-runs/` (they are not in the state: the
time from the folder name, the cost from `result.json`, findings counted in `report.md`). A run's
report gets an opaque key only if its `report.md` is under the learn state folder, symlinks
resolved (a run made with `--out DIR` elsewhere is listed without one); `/api/learn/report/<key>`
returns it and the page renders it as Markdown. Each `learn:` item shows its fields (finding key,
category, target, run), the Change, Why and Evidence lines of its proposal (pending, or the last
`proposed` history entry once settled), the exact change from the state's `proposals` (target,
text, window), and its last move to applied or rejected (when, and the note `learn apply` wrote:
where the change went). Findings remembered: key, title, category, status, times seen, first and
last seen, with counts per status.

### 5. Config (`#/library`; called Library before 2026-10-21)

- A read-only tree of the editorial content:
  - repo: `orchestrator/policy.md`, `plugin/commands/chores.md`, `tasks/*.md` (with their
    front-matter summaries), `learn.md` and `slack-watch.md`;
  - site overlays from `$BOSUN_CONFIG_DIR`: `policy.md`, `tasks/`, `sessions.yml`, the
    orchestrator settings, and `bosun.env` redacted.
- An "effective" badge for each task type shows whether the repo or the site file wins.
- Markdown is rendered with a small safe renderer (headings, lists, code, links), escaped first.
- Optional and off by default: Claude Code memory directories listed in
  `BOSUN_CONSOLE_MEMORY_DIRS`, read-only.
- Later: editing site files, with a diff preview, a `.bak` backup and a log line. Editing repo
  files from the UI stays out, because changes there go through branches and review.

**Built in step 5.** `lib/console_files.py` builds the list of files at request time (repo: the
files above plus `orchestrator/settings.json` when it is the settings in use; site: `policy.md`,
`learn.md`, `tasks/*.md`, `bosun.env` and the files `BOSUN_SESSIONS_FILE` and
`BOSUN_ORCHESTRATOR_SETTINGS` name; memory: the `.md` files of each folder in
`BOSUN_CONSOLE_MEMORY_DIRS`, colon-separated, empty by default). Each file gets an opaque key
(a hash of its root and path); `/api/library/<key>` looks the key up in that list, so no part of a
request becomes a path, and an unknown key is 404. Never listed: the forbidden-terms list and any
file with `.bak` in its name. A file found by listing a folder (`tasks/`, memory) must stay inside
it with symlinks resolved; one that leads out is skipped. Files over 1 MB are listed, not shown.
`lib/console_library.py` (pure) decides how a file is shown:
- config files from the site (`bosun.env`, `sessions.yml`, the settings JSON): `lib/redact.py`,
  then every `NAME=value`, `NAME: value` or `"NAME": "value"` whose name contains TOKEN, SECRET,
  KEY, PASSWORD, PASSWD, CREDENTIAL, COOKIE or PRIVATE gets its value masked (`<masked>`, quotes
  kept; an empty value stays empty), and every webhook URL (`hooks.` or `webhook` in an http(s)
  URL) becomes `<webhook URL>`; other site files and memory notes go through `lib/redact.py`;
  repo files are shown as they are. The page shows a "masked" badge when anything changed.
- Markdown: the front matter (`key: value` lines between `---`, read as `lib/delegate.py` reads
  task files) as a small table, the body through the page's renderer: everything becomes text
  nodes; headings, paragraphs, nested lists, fenced code, quotes, rules, simple pipe tables;
  inline code, strong, emphasis and links, which link only through `safeHref` (console routes
  and http(s); anything else is text with its target).
- Task types: the badge comes from `delegate.task_files`, the resolution `bosun delegate`
  uses (moved there from `load_task_types` for this): *effective* (the file delegate uses),
  *replaced by site* (a repo type a site file of the same name replaces), *added to every task*
  (`_common.md`, both).

### 6. Knowledge (`#/knowledge`, `#/knowledge/<key>`, `#/knowledge/review`)

The knowledge base of `bosun kg` (docs/design/knowledge.md, its MVP 2), built 2026-10-21.
`lib/console_kb.py` reads `$BOSUN_CONFIG_DIR/knowledge/` under the store's shared lock, as `kg
lookup` does: `entities.jsonl`, `facts.jsonl` and `schema.json`, never `identifiers.jsonl`
(restricted) or `forgotten.jsonl`. A knowledge base that does not exist reads as none and nothing
is created; the views then say how to start one (`bosun kg import`, `kg propose`).
`lib/console_knowledge.py` (pure, over a `KB` and the ledger) builds the models.

- **Tabs** (2026-10-21; the treatments were prototyped first, see
  docs/design/prototypes/knowledge-treatments/): `#/knowledge` is the **Briefing**,
  `#/knowledge?view=constellation` the **Constellation** and `#/knowledge?view=raw` the **Raw
  data** (the overview below, its search in the same query: `?view=raw&q=...`), so each is
  bookmarkable. A fourth tab, "Metro (prototype)", is shown disabled with a note: the metro map is
  on the roadmap, not built. Entity pages and the review keep their own routes.
- **Briefing** (`/api/knowledge/briefing`, `lib/console_kg_views.py`; its measures are defined in
  docs/design/knowledge.md): a headline of four tiles (facts captured per day over 30 days as a
  sparkline, with the total and this week's count; the confirmed share of current facts with a
  meter; coverage rings per card; "Rediscovery saved" as a labelled placeholder, "Not measured
  yet", until learning counts rework episodes); "This week we learned", a timeline of the last 7
  days, newest first, each day with its changes as before -> after and its new facts (decisions,
  lessons and principles first, then rules and caveats; 8 shown, the rest behind "Show more", at
  most 60 sent), every entity a link to its page; today's review as a card stack (below); and the
  six most connected entities, each a card linking to its page with a one-hop mini ego graph from
  `console_kg_graph` (at most 12 neighbours).
- **The review card stack** (on the Briefing): today's batch, the same model as the review page
  (`/api/knowledge/review`, which also gives the CSRF token), one decidable fact per card with its
  group, sources, authority and up to three facts already known about its subject (`context` in
  the review model). Y or right arrow confirms, N or left arrow rejects, S or down arrow skips
  (sent as `defer`: it stays proposed), Z takes the last decision back; the keys are bound to the
  stack element, so they work only while it has focus. Dragging the top card right, left or down
  does the same, with stamps; the card follows the pointer through CSS custom properties set from
  script (CSSOM, which the CSP allows; never a style attribute). Decisions go through the existing
  `POST /api/knowledge/review` one fact at a time, so every guard applies; a decision waits 4
  seconds (or until the next one, or until you leave the page) before it is sent, which is what
  lets Z undo it without a write. A progress bar shows each card's result; after the last card the
  stack says the batch is answered, with the counts. A refresh on poll reloads the Briefing only
  while no review is under way.
- **Constellation** (`/api/knowledge/constellation`): every entity, coloured by card from the chart
  palette (the `.axis-<card>` tokens, read from the page's CSS) with a shape per card, sized by
  degree, on a canvas with the prototype's hand-written force layout (a pull to each card's anchor,
  charge between every pair, springs along links, no dependencies), pre-settled before the first
  frame. Hover dims everything but a node and its neighbours; a click opens a slide-in panel with
  its current facts (from its entity page's model) and a link to the page; the search flies to an
  entity; chips hide a card; an "as of" slider over the days entities were first seen hides what
  came later. Entities with only proposed facts are translucent, proposed links dashed. With
  reduced motion nothing drifts, pulses or flies, and the layout is still. Performance: the charge
  is O(n^2) per frame, fine up to a few hundred nodes, so the model keeps at most 400 entities (the
  best connected) and 1600 links and says how many it left out ("see Raw data"). The animation
  stops when you leave the tab. A knowledge base change (its stamp in `/api/version`) never
  redraws the graph under you: a "Reload" chip appears beside the card chips (decided
  2026-10-21), and a click loads the new graph in place, keeping the camera and zoom, the hidden
  cards, the "as of" day (the latest day stays the latest), the open facts panel, and the
  positions of the entities already drawn; new ones start where the layout puts them.
- **Overview** (`/api/knowledge?q=&type=`, the Raw data tab): four cards, People (person, team), Process (process,
  step, gate, channel), Product (repo, feature, decision, principle, lesson) and Systems (system,
  service, environment: the schema files these under Process and Product; the console groups them
  as one card, decided 2026-10-21; a site's own types go by their axis). Each card: its entities (by name, with their confirmed
  and proposed counts), its current confirmed and proposed facts, and the five facts that changed
  last. The search (`q`: names, aliases and related terms through `kq.search`, and fact text) and
  the type filter (`type`, one of the schema's types; anything else is 400) list entities and
  facts. A "Review" card summarises today's batch and links to it.
- **Entity page** (`/api/knowledge/entity/<key>`): entities are addressed by an opaque key (a hash
  of the id) looked up among the entities; anything else, a raw id included, is 404. Header (type,
  card, name, aliases, summary), current facts (confirmed or proposed, not closed) grouped by
  predicate in the schema's order, steps in order; each fact with its authority (user,
  stakeholder, observed, inferred), status, observed and valid-from dates, who reviewed it, and its
  sources: an http(s) reference is a link (through `safeHref`), a `ledger` reference to an item we
  hold links to `#/item/...`, anything else (a Slack ref, a transcript, a `javascript:` string) is
  text; a quote of my words shows whether it was verified. History, collapsed and greyed: facts
  closed (superseded, with what replaced them), rejected or expired. Backlinks: current facts of
  other entities whose object is this one. Related ledger items: for a person, items whose
  requester or outreach recipient (split like the Stats people) is their name, slug, a strong
  alias, or their first name when no other person shares it; for a repo, items whose repo field,
  id (`mypr:`, `review:`, `ghissue:`) or GitHub URL names it (`owner/repo`, or the repo part
  alone). Read-only, newest first, at most 20.
- **Privacy:** about a person only the People whitelist's predicates are ever shown (the schema
  refuses others; the page filters again), a person has no summary, and the page says "Work-
  operational facts only. Delete on request: bosun kg forget person/<slug>".
- **Process flow:** a process with `has_step` facts gets its steps in order (`order`, then the fact
  id), numbered, each with its actors, gates (`gated_by`), systems (`uses`), channels (`posts_to`),
  entry points and rules or caveats, joined by arrows drawn in CSS, one column at any width.
- **Neighbourhood graph** (`lib/console_kg_graph.py`): the entities within two links over current
  facts (confirmed, or proposed: drawn dashed), walked breadth-first in fact-id order, at most 30
  nodes ("+N more not shown"). A radial layout in the model: the first ring evenly around the
  centre, each second-ring node within its parent's slice (alternate ones further out), then
  rounds of pushing apart nodes that sit too close, clamped inside the picture. Deterministic:
  the same facts give the same picture whatever order they were loaded in. The page draws it as
  inline SVG (`role="group"`), nodes coloured by card from the chart palette, labels under them,
  each node a link to its page.
- **Review** (`/api/knowledge/review`): today's batch, from the same model as `kg batch`: the batch
  `kg batch` saved (`batch.json`) if it was made today (local time), is unanswered, names only
  known facts and still has one waiting; otherwise the batch `kg batch` would print now
  (`kr.build_groups`, `kr.select_batch`, the same limits, passed in by `bosun console`), not
  saved: a GET writes nothing. Groups and facts lettered like the CLI (1a, 1b); facts decided since
  the batch was made show their status and no buttons. Written for a reviewer (2026-10-24):
  - **Plain sentences.** Each fact is said in words from its predicate's sentence in the knowledge
    schema ("includes the step X (step 3)", "has a gotcha: ...", "was decided on Oct 15, 2026:
    ..."), sent as parts (`sentence`: words, entity links, the value) so the page links entities and
    clamps a long value with Show more. The same parts drive the card stack, the Briefing, the
    recent list and entity pages; `kg batch` prints the same sentences.
  - **One block per entity** (`blocks`): a group's facts by subject, headed by the entity's type,
    name and summary; facts that only place it (about, part_of, lives_in) are its subtitle, with
    their own small Reject and Skip, unless they are all it has; up to three facts already known
    about it (`known`); and "Confirm block".
  - **Sources in words:** what kind (Memory note, Session, Slack, Ledger, Ticket, ...) and a label:
    a memory note by its name, with its description (once per block) and a Library link when the Library lists its
    folder (`BOSUN_CONSOLE_MEMORY_DIRS`); a session by its name and time, from its transcript's
    title (`lib/console_kg_sources.py`, cached by file and modification time); times as "Oct 18
    18:56". The raw reference stays in the tooltip.
  - **Less noise:** a legend above the batch (Confirm = true and current, Reject = wrong, Skip = not
    sure yet), no "proposed" badge (every fact there is), the fact id in a tooltip, the authority only
    when it is not plain "observed", and "prevents rework" explained in its tooltip.
  The page: "Confirm group", "Confirm all except ticked" (the ticked facts stay proposed, as
  `except` does), "Reject group", per block "Confirm block", and per fact Confirm, Reject and Skip.
  The result shows above the batch, which then reloads.
- **Decisions** (`POST /api/knowledge/review`, guarded as in Security): under the knowledge base's
  exclusive lock (`knowledge.opened`), the facts are checked against today's batch, then confirm
  and reject go through `km.review`, as `kg batch apply` does (a single-valued confirm closes the
  value it replaces), recorded with `reviewed_by: {"kind": "user", "ref": "console", "at": ...}` and
  the note `console`, and saved as one commit in the knowledge directory's git history
  (`kg console review: confirm f3 f7`). Defer writes nothing (the facts stay proposed for a later
  batch). When the decisions have answered every fact of the batch `kg batch` saved (none still
  waits; a deferred fact still does), the console marks `batch.json` answered exactly as `kg batch
  apply` does (`applied_at`, through its `mark_answered`), so the CLI then refuses to answer it
  again and the two agree; until then the CLI can answer the rest, and reports the facts the
  console decided as skipped. A computed batch has no file and marks nothing.
- **Refresh:** `/api/version` carries the knowledge base's own stamp (`knowledge.stamp`: inode,
  mtime and size of `entities.jsonl`, `facts.jsonl` and `batch.json`; a stat each, no read, no
  lock). When it changes, the page reloads the open Knowledge view (and the entity page also when
  the ledger changes, for its related items): the overview's results under the search form, an
  entity page in place (no "Loading", the history kept open), and the review only while nothing
  is ticked and no decision is being sent, so a review in progress is never thrown away.

### Phase 2: knowledge graph (`#/graph`)

- Nodes: ledger items, people (outreach recipients and requesters), repositories, tickets and Slack
  channels.
- Edges: lineage links, "asked by", "reached out to", "in repo", "tracks".
- Shown as a force-directed SVG (a small hand-written layout, or one vendored file), with filters
  by person, repo and time.
- This is the visible seed of the People, Process, Product knowledge base (`docs/design/knowledge.md`):
  its entities and facts become the graph's nodes and edges (its Knowledge view, MVP 2). The
  Knowledge view (section 6) now draws one entity's neighbourhood, and the whole knowledge base in
  its Constellation tab; this graph of the ledger's own items stays for later.

## API (JSON; all GET, except the one POST)

| Path | Returns |
|---|---|
| `/api/version` | ledger mtime and version, state-dir change stamp, and the knowledge base's own stamp (for polling) |
| `/api/items?kind=&group=&window=&person=&q=&since=&sort=&ws=` | list rows |
| `/api/items/<id>` | item detail view model (lineage, anchors, outreach, said) |
| `/api/needs-you` | pending proposals, proposed outreach, waiting windows, mismatches |
| `/api/sessions` | a card per window: status and for how long, held and active items, working, last report, login, attach command |
| `/api/health` | watchdog checks, morning and chores runs, events, slack-watch, login, flags, what is left out |
| `/api/stats?days=` | the metrics above |
| `/api/learn` | runs and `learn:` items |
| `/api/learn/report/<key>` | one run's `report.md` (only reports under the learn state folder) |
| `/api/library` / `/api/library/<key>` | the editorial tree, and one file (redacted where config) |
| `/api/knowledge?q=&type=` | the four axis cards, search results, today's review batch summary |
| `/api/knowledge/entity/<key>` | an entity's page: facts, history, backlinks, related items, flow, graph |
| `/api/knowledge/review` | today's review batch, lettered, and the CSRF token |
| `/api/knowledge/briefing` | the Briefing: captured per day, confirmed share, coverage per card, this week's timeline, the most connected |
| `/api/knowledge/constellation` | every entity (at most 400) with card, degree, first day and coverage, and the links among them |
| `POST /api/knowledge/review` | a review decision: `{"action": "confirm"\|"reject"\|"defer", "facts": [...]}` |

## Testing

- **Unit** (`tests/unit/test_console_*.py`): view models and every stat over fixture ledgers
  in the acme world, including edge cases (an item with no history, cyclic-looking links, outreach
  without a parent, closed parents), and redaction of config keys. The live facts are collected
  with fake commands over a scratch state directory (nothing written, Claude never started), and
  the smoke test reads `/api/sessions`, `/api/health` and the waiting window in "needs you" from
  its own tmux server's fake Claude windows, checking that no file changed and the panes did not.
- **Smoke** (`tests/smoke.sh`): start on a free port with a temp state dir, then check:
  - every endpoint answers with valid JSON;
  - no token gives 401, and a wrong `Host` gives 403;
  - a non-GET method gives 405;
  - a path-traversal request gives 404;
  - the CSP header is present.
- **JS check** (`tests/js_check.sh`, run by the pre-commit hook and the smoke suite): with node on
  the machine (skipped with a note otherwise; no npm packages), `node --check console/app.js`, then
  `tests/js/app_check.js` runs the script in a small fake DOM and renders views from real API
  models (`tests/js/api_models.py`): it fails on text such as `[object HTMLElement]`, `undefined`
  or `NaN`, a `style` or `on*` attribute, a link that is not a console route or http(s), and any
  HTML-parsing API in the source. Added after step 3, whose nested-array bug nothing exercised.
- **Knowledge** (`tests/unit/test_console_knowledge.py`, over the fixture knowledge base
  `tests/fixtures/knowledge/acme-import.jsonl`): overview counts and search, keys, grouping,
  history and backlinks, sources, the person whitelist and note, related ledger items, flow
  order, the graph's cap, hops, bounds and determinism, the review batch (saved or computed,
  lettered as the CLI prints it), decisions, that `identifiers.jsonl` never reaches a model, and
  one decision saved as one commit. The server tests post decisions with every guard missing in
  turn (each refused, nothing written), a forged GET, unknown keys and traversal (404); the smoke
  test does the same against `bosun console`, with a confirm and a defer round trip on a
  scratch knowledge base. The JS check renders every knowledge route from these models, the empty
  states, and clicks "Confirm group" and "Confirm all except ticked" (method, CSRF header, JSON
  body, the ticked fact left out). The Briefing and Constellation (`tests/unit/test_console_kg_views.py`,
  over the fixture grown through a week with a pinned clock): coverage as defined, the confirmed
  share, the captured series, the timeline's days and before -> after pairs, the most connected,
  the Constellation's nodes, edges, days and cap, the same model whatever the load order, and no
  identifier in either. The JS check renders the three tabs from the hash, drives the card stack
  with its keys and a drag (the POSTs, their CSRF header, Z taking a decision back before it is
  sent, defer for a skip, the answered batch), and draws the Constellation into a fake 2D context
  (finite numbers, clean labels, hover, search to the facts panel, chips, reduced motion, stopping
  when you leave), and checks the Reload chip: hidden at first, shown (with nothing fetched or
  redrawn) when the knowledge base changes, and on a click the new graph with the camera and the
  drawn positions kept.
- **XSS check:** a fixture item whose title and notes contain `<script>` and `<img onerror>` must
  come back as text. The smoke test asserts the raw API value, and a manual browser check (Chrome
  automation) confirms nothing executes.
- **Fixture:** `tests/fixtures/console/acme-ledger.json` is built by `make-acme-ledger.sh` next to
  it, with the real `ledger` commands (and the hook for `said`): every kind, the outreach states,
  each lineage relation, pending proposals, ticket mismatches and the XSS strings. Its task ids
  carry the day they were made, so the script pins it: every date of the build day becomes
  2026-10-20, the day the tests name, and a rebuild keeps the ids.
- **Dogfood:** run against a copy of the real ledger, read-only, and compare its counts with
  `ledger list` and `outreach`. Count item ids, not lines: a multi-line proposal continues on
  further lines of `ledger list`.

## Implementation plan

Each step is a separate PR, built in a worktree and smoke-tested before merge.

| # | Step | Contents | Size |
|---|---|---|---|
| 1 | Skeleton and security | `bosun console`, server, token and cookie, Host check, CSP, static serving, `/api/version`, empty shell page with nav; smoke tests for the security rules | S |
| 2 | Ledger | `console_data` list and detail models, filters and search, item page with lineage, anchors and history, the "needs you" strip | M (largest) |
| 3 | Sessions and health | sessions and health models from the existing readers, auto-refresh | S |
| 4 | Stats | metric functions (pure, tested), SVG bar and line helpers | M |
| 5 | Learning and Library | learn runs and items, editorial tree, safe markdown, redaction | S-M |
| 6 | Polish | keyboard search (`/`), URL-state filters, empty and error states, README section, `doctor` check | S |
| P2 | Graph, code stats, site-file editing | as above | later |

Steps 1 to 3 already give a usable console; 4 and 5 can follow in either order.

Nav labels (2026-10-21): Learning became **Improvements** (what bosun proposes to change about
its own prompts and rules), Knowledge became **Knowledge Graph**, and Library became **Config**
(prompts, task types and site overlays). The routes keep their old names, so links still work.

## Decisions (2026-10-20)

1. Runs on demand (`bosun console --open`); no always-on window or launchd agent for now.
2. The Library may show Claude Code memory notes, opt-in by directory
   (`BOSUN_CONSOLE_MEMORY_DIRS`), off by default.
3. Code-volume stats: cached and opt-in, in phase 1.5 after the MVP.
4. No phone or remote access for now.
5. Later write actions only queue an instruction to the orchestrator, which keeps its approval
   flow; the console never runs outward-facing commands itself. Knowledge review decisions are
   the one exception (knowledge note, decision 2, 2026-10-21): they change local data only, go
   through the same code as `kg batch apply`, and are guarded as in Security.
