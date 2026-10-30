# Console

`bosun console` serves a local web page over the ledger, the sessions, bosun's own health
and the knowledge base, read-only except for knowledge review decisions (design: [docs/design/console.md](design/console.md)). It runs in the
foreground until Ctrl-C and prints a URL such as `http://127.0.0.1:8765/?t=<token>`; open it (or
pass `--open`). The top bar shows how many things need you on every view; the page follows the system's
light or dark mode and works down to a narrow window.

- **Ledger** (`#/ledger`): every item with its kind, state, window, what it waits on and when it
  changed, filtered by kind, group, window, person (requester or outreach recipient) and update
  time, searched over ids, titles, fields, notes and reports, and sorted by update, creation or
  kind. The filters live in the URL, so a view can be bookmarked. Groups: *needs me* (a pending
  proposal, outreach whose message awaits approval, a proposed task, a ticket that disagrees
  with our work), *waiting on someone* (my review or PR with the other side, outreach sent, open
  outreach or blockers), *open* and *closed*; the default shows every open item, as `ledger list
  --open` does.
- **Needs you**, above the list: windows waiting at a prompt, each pending proposal with its
  exact action, proposed outreach, and the items a ticket mismatch asks you to decide on (one
  entry per item, as the *needs me* group counts them, with the tickets behind it).
- **Item page** (`#/item/<id>`): fields, delegation, latest report, outreach (to whom, what went
  out, nudges, reply, how long it waited), the pending proposal, what you said about it in a repo
  window, the lineage tree and links (duplicates, folded into, blocked by, tracks, and their
  inverses) that go to other items, every web link in the item labelled GitHub, Jira,
  Confluence, Slack or Web, and the history. Only `http(s)` links are clickable; they open in a
  new tab.
- **Sessions** (`#/sessions`): a card per window (`bosun sessions --json`): its kind, folder,
  model and effort, its Claude's status (idle, busy, shell, waiting) and for how long, the items it
  holds, its active item and whether it works on it now, its last report, its login verdict, and
  the `tmux attach` command with a copy button. Below, **Health**: the watchdog's checks (as
  `watchdog --status` shows them; nothing is alerted or recorded), today's morning run and the
  last chores runs, the events dispatcher (deliveries today against `BOSUN_EVENTS_MAX_PER_DAY`,
  events pending), the Slack watcher (last run, spend today against its daily cap, failures), the
  login (the last login check, windows answering only with login errors) and the feature flags
  (events, Slack watcher, learning: on or off). It never starts Claude, so whether the stored
  login works right now is left to `bosun status`. The view reloads every 15 s while visible.
- **Stats** (`#/stats?days=7|30|90`): from the ledger's history, items opened and closed per day
  by kind, my PRs merged and the median time to merge, review rounds done and their turnaround,
  tasks spawned, accepted and done, hand-overs per window and the median time to the report,
  outreach sent, replied, unanswered and waiting with the median reply time and nudges per reply,
  the people reached out to and worked for (top 10; names stay local), and bosun itself: the
  last chores runs, event deliveries, Slack-watch and learning runs and their spend per day. Each
  metric is defined in the design note. Charts are inline SVG, each with a text summary and its
  numbers in a data table.
- **Improvements** (`#/learning`): learning's runs, live and dry (when, outcome, findings, proposals,
  cost) with a link to each run's report, rendered in the page; every `learn:` proposal with its
  finding, evidence, reason, the exact change and where it goes, and when and how it was applied
  or rejected; and the findings learning remembers, with how often each was seen.
- **Knowledge Graph** (`#/knowledge`): the knowledge base (`bosun kg`) in three tabs. The
  **Briefing** (the home): facts captured per day, the confirmed share, coverage per card, what
  changed this week (before -> after) and the new facts, today's review as a card stack (Y/N/S
  keys or drag, Z undoes), and the most connected entities. The **Constellation**
  (`?view=constellation`): the whole knowledge base as a force-laid graph with hover, a facts
  panel, search, card chips and an "as of" slider (at most 400 entities). **Raw data**
  (`?view=raw`): four cards, People, Process, Product and Systems, with entity counts, confirmed
  and proposed facts and what changed last, and a search over names, aliases and fact text with a
  type filter. A disabled Metro tab marks the map still on the roadmap.
  An entity's page (`#/knowledge/<key>`) shows its current facts by predicate with who stands
  behind each (user, stakeholder, observed, inferred), its status, dates and sources (web links
  and ledger items link; a memory note links to the Config view when
  `BOSUN_CONSOLE_MEMORY_DIRS` lists its folder; a session shows its name; anything else is
  text), the facts that no longer hold (greyed, with what
  replaced them), the facts that point to it, related ledger items (a person as requester or
  outreach recipient, a repo by its items), a process's steps as an ordered flow (actor, gates,
  entry points, systems), and a small graph of what lies within two links. A person's page shows
  only work facts and how to have them deleted (`bosun kg forget`); restricted identifiers are
  never shown. The review (`#/knowledge/review`) is the batch `kg batch` uses, lettered the same
  way: confirm or reject a group or a fact, skip a fact, or confirm a group except the facts you
  tick. Decisions are recorded as yours, from the console, with the time, through the same code
  as `kg batch apply`; once every fact of the batch `kg batch` printed is decided, it is marked
  answered there too. The view reloads when the knowledge base changes (the review only while
  nothing is ticked). Systems groups the system, service and environment types. Without a
  knowledge base the view says how to start one.
- **Config** (`#/library`): the files that steer bosun, read-only: the repo's policy, chores,
  task types and prompts, and your site overlay from `$BOSUN_CONFIG_DIR` (policy, task types,
  `sessions.yml`, the orchestrator settings, `bosun.env`). Each task type says whether it is
  the one `bosun delegate` uses or a site file replaces it. Config values whose names contain
  TOKEN, SECRET, KEY or PASSWORD, and webhook URLs, are masked; site files are also redacted like
  learning's digest. Markdown is rendered (its front matter as a table). Claude Code memory notes
  appear only if you list their folders in `BOSUN_CONSOLE_MEMORY_DIRS` (colon-separated).
- The open view refreshes when the ledger changes (checked every 15 s).

- It listens on `127.0.0.1` only, on `BOSUN_CONSOLE_PORT` (8765) or `--port N`; `--port 0`
  picks a free port. A port in use is an error, not a silent fallback.
- The token is new on every start. The first visit swaps it for an HttpOnly, SameSite=Strict
  cookie and drops it from the address bar; the API answers 401 without the cookie, so other
  local processes and pages cannot read the ledger by finding the port. Restarting the console
  signs the browser out: open the new URL.
- Read-only: GET and HEAD only, a strict Content-Security-Policy, and only its own files are
  served. The one exception is a knowledge review decision (`POST /api/knowledge/review`): local
  data, never outward-facing, and only with the cookie, this page's Origin, a JSON body and a
  per-start CSRF token that only the review page can read. Config files, learning reports and
  knowledge entities only by an opaque key from a list it builds itself
  (no path from a request ever reaches the filesystem). It reads `ledger.json` as a snapshot (never its lock), re-read when the file changes.
  It reads the windows the way `bosun sessions --json` and the watchdog do (list them, read
  Claude Code's session records), at most every 10 s; it never sends keys to a pane, never reads
  a pane's text and never starts Claude.
- It logs only refused requests (to stderr), never ledger content or item ids.

## Impact

The console's **Impact** view (`#/impact`) asks what bosun changed, not what it did (design:
[docs/design/impact.md](design/impact.md)). It compares weeks since the start
(`BOSUN_IMPACT_START`, else the ledger's first item) with my own baseline before it
(`BOSUN_IMPACT_BASELINE_FROM`, else 13 weeks earlier), shown as a band: PRs merged per week and
time to merge, reviews given per week and time to my first review, agent busy hours against my
touch time (leverage, week by week and before the start against since where transcripts from before
exist), what learning has produced (runs, findings that came back, proposals, the knowledge base's
growth), cost per week and per merged PR, and where agent time went per window, with the PRs behind
each week as links. Estimates (my touch time: `BOSUN_IMPACT_PROMPT_MINUTES` per
prompt I typed; cost at list prices) carry an "estimate" badge and a range, every number says how
many weeks it rests on, and fewer than four weeks since the start is marked early data. Other people
are never named. The view reads the snapshot `bosun impact collect` writes to
`$BOSUN_STATE_DIR/impact/` (GitHub reads only, through `gh`, over `BOSUN_IMPACT_REPOS` or the
ledger's repos; transcripts through the same reader as the rest of bosun, cached per file); run it
by hand, or daily from the morning run with `BOSUN_IMPACT_COLLECT=1`.

Its **Overhead** panel shows what bosun itself costs. Every turn's tokens count against what
started it: my requests, tasks handed to a window and subagents are work; check-ins, events
summaries, background-task notifications, resume prompts, messages between sessions, prompts Claude
Code sends on a schedule (a session's own cron or wake-up) and the prompt openings in
`BOSUN_IMPACT_OVERHEAD_PREFIXES` (a site's own scheduled prompts, `|`-separated) are overhead. The panel shows the overhead share per week, the cost per check-in, notification and
resume, and the context each window's turns re-read (a long conversation makes every turn there
expensive). Guardrails beside it (tasks reported and done, hand-over to report time, approval
failures, outreach answered, events delivered and waiting, check-ins sent and skipped) show the
work did not get worse when the cost fell; they, the context per window and cost per turn are on
**Bosun internals** (`#/impact/internals`, linked from the page). `BOSUN_IMPACT_OVERHEAD_FIXED_AT` (a date) adds a
before/after table there for the week on each side. The watchdog's `context-size` and `overhead-share`
checks warn when either grows.

Its **Cost per task** panel shows what each ledger item cost, its subagents included: by outcome
(done, merged, dropped, open), task type and kind, and the most expensive items next to the PR they
produced. A turn counts for the item its hand-over names, else the item the window reported on
next, else the window's active item, as long as that window's last ledger event is within
`BOSUN_IMPACT_ATTRIBUTION_HOURS` (8); the rest stays unattributed per window (the
orchestrator's coordination, work outside the ledger). `BOSUN_IMPACT_TASKS_TOP` (15) sets how
many items it lists. A model missing from the price table is priced as Opus and named on the panel.
