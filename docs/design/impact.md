# Impact stats: what bosun changes

Status: accepted 2026-10-26; Phase A built, in review (see the checklist at the end). Plan for the phase
being built: [docs/plans/impact-phase-a.md](../plans/impact-phase-a.md). Roadmap entry:
[ROADMAP.md](../../ROADMAP.md), "Impact (productivity) stats".

## Goal

The console's Stats view counts what happened (items opened and closed, tasks, outreach). It does
not say whether any of it made me more productive. After one or two weeks of real use the question
is: what did bosun change? The Impact view answers it with numbers I can defend: delivery
against my own baseline from before bosun, how much agent work each hour of mine buys, how
fast reviews get done, what it costs, and later what the learning and the knowledge base saved.

## Honesty principles

- **Measured and estimated look different.** A measured number (PRs merged, agent busy time) is
  plain. An estimate (my touch time, time saved) carries an "estimate" badge, shows a range when
  it has one, and names the assumption behind it (a config key) in its tooltip.
- **Every number shows its sample.** Weeks in the baseline, weeks since the start, items behind a
  median. With fewer than `EARLY_WEEKS` (4) weeks since the start the page says "early data".
- **A baseline, not a guess.** "Before" is my own history from GitHub over the baseline window
  (`BOSUN_IMPACT_BASELINE_FROM` to the start), shown as a band (the middle half of the
  baseline weeks) with its mean, never a single cherry-picked week.
- **Confounders are named, not hidden.** A heavy project week, holidays and a changed team load
  move these numbers as much as bosun does. The page says so next to the comparison, and the
  weekly chart lets me see a single outlier week for what it is.
- **Drill down to the items.** Every count opens the list behind it (PR links, ledger items), so a
  number can be checked by hand.
- **No vanity metrics.** Lines of code are collected (for context) but never shown as output.
- **People are counts only.** The page never names a colleague: reviews given are counted, with
  links to the PRs, but the author is stored only as "mine" or "someone else's".

## Data sources

| Source | What it gives | How it is read |
|---|---|---|
| GitHub (`gh`, read only) | my merged PRs per repo: created, merged, size, URL; reviews I gave on other people's PRs: PR created, my first review | `bosun impact collect`, a daily snapshot |
| Claude Code transcripts (`lib/claude_state.py`) | per session and day: agent busy time, the prompts I typed, tokens by type and model | the same collector, cached per transcript (size, mtime) |
| The ledger | the start date (its first item), my PRs and reviews as bosun saw them, approvals | read by the console as today |
| Deploys (Phase B) | when a merged change reached production | `bosun impact collect`, from the site's deploy announcements |
| Learning and knowledge events (Phase C) | finding classes, recurrences, knowledge and memory use | new events, recorded from Phase C on |

Repos come from `BOSUN_IMPACT_REPOS` (owner/name, comma-separated); empty: the repos of the
ledger's `mypr:` and `review:` items. Nothing in the repo names a site's repos.

## Metric definitions (Phase A)

Weeks are ISO weeks (Monday to Sunday) in local time. The **start** is `BOSUN_IMPACT_START`
(a date), else the day of the ledger's first item. **Baseline weeks** are the full weeks from
`BOSUN_IMPACT_BASELINE_FROM` (else 13 weeks before the start) up to the week before the start;
**since weeks** are the weeks from the start's week on, the current week included and marked
partial.

- **PRs merged per week:** my PRs (author = the `gh` user) whose `mergedAt` falls in the week, per
  repo. The tile compares the mean of the since weeks (the partial week left out) with the
  baseline mean and band (25th to 75th percentile of the baseline weeks).
- **Time to merge:** median hours from `createdAt` to `mergedAt` of the PRs merged in the
  baseline, and of those merged since the start.
- **Reviews given per week:** reviews I submitted on PRs authored by someone else (one per PR and
  day at most, so a burst of comments is one review), by `submitted_at`. **Time to first review:**
  median hours from the PR's `createdAt` to my first review of it (the time a review was
  requested is not in the REST API; noted as a proxy).
- **Agent busy time:** for each transcript of an bosun window (and the subagent transcripts
  under it), the timestamps of assistant entries and tool results in order; every gap of at most
  `BUSY_GAP_SECONDS` (300) between consecutive ones counts as busy time, a longer gap as idle.
  Summed per window and day; parallel sessions add up (agent-hours, not wall-clock hours).
- **My prompts:** prompts delivered to those sessions that I typed (`claude_state.delivered_prompts`
  filtered with `activity.is_mine`: not bosun's pasted check-ins, events or drain prompt, not
  slash commands).
- **My touch time (estimate):** my prompts times `BOSUN_IMPACT_PROMPT_MINUTES` (2): reading
  what the session said and answering. A range of half to double that is shown.
- **Leverage (estimate):** agent busy hours divided by my touch hours, per week.
- **Tokens and cost (estimate):** each assistant message's `usage` (input, output, cache writes,
  cache reads), counted once per message id, priced with `PRICES` in `lib/impact.py` per model
  (input, output and cache-read list prices per million tokens, matched on the model id, most
  specific first; first-party API rates, so an estimate, not a bill: a subscription or a partner
  platform bills differently). Cache writes are priced by lifetime: 1.25x input for the 5-minute
  cache, 2x for the 1-hour cache (`usage.cache_creation.ephemeral_1h_input_tokens`, kept as
  `cache_write_1h`; a transcript without that breakdown counts every write as 5-minute). Per week,
  per window, and per merged PR since the start.
- **Where time went:** agent busy hours since the start, per window.

## Collector

`bosun impact collect [--since DATE] [--dry-run]` (`lib/impact_collect.py`, the only module
with I/O for this; the metrics are pure in `lib/impact.py`):

- writes `$BOSUN_STATE_DIR/impact/snapshot.json` (replaced atomically, idempotent): the
  window, the start, the repos, my PRs, my reviews, and per-session daily activity;
- keeps a per-transcript cache (`impact/transcripts.json`: path, size, mtime, the day buckets) so
  a daily run re-reads only the transcripts that changed;
- refetches only PRs updated since the last run for reviews;
- `--dry-run` prints what it would collect (counts per source) and writes nothing;
- runs daily from the morning run when `BOSUN_IMPACT_COLLECT=1` (default 0 until reviewed), and
  by hand any time. GitHub reads only; nothing outward-facing; nothing leaves the machine.

## The page (`#/impact`)

- **Headline tiles:** PRs merged per week (since vs baseline, with the change), time to merge,
  reviews given per week, leverage (estimate), cost per week (estimate). Each with its sample
  size.
- **Before and after:** weekly PRs merged as bars, the baseline band and mean as a shaded strip, a
  dashed line at the start; reviews given per week below it in the same layout.
- **Your time and agent time** (2026-10-29): one headline per phase, "for every hour you spent
  prompting, agents worked about X hours (range)", with agent hours and my touch hours a week, since
  the start and before it. Each phase counts only its full weeks that have prompts: before the
  start that is my own Claude Code use in the same folders, and only the weeks whose transcripts
  still exist (Claude Code removes old ones), so the page says how many weeks of how many the
  before figure rests on, or that there is none. Below: agent busy hours and my touch hours per
  week as paired bars with a legend, and the week's leverage. Leverage compares like with like (my
  prompting time against agent time); it does not count my time outside Claude, so it is not
  output per hour of my day.
- **Where time went:** agent busy hours per window since the start.
- **Cost:** per week, and per merged PR since the start.
- **Flow** (Phase B): asked, task, PR, merged, in prod, with the median time at each step.
- **What learning has produced** (2026-10-29, `lib/impact_learning.py`): learning's runs (live
  and dry) with their findings and cost, the `learn:` proposals by state, findings that came back
  in a later run (by finding key, from the reports under the learn state directory), and the
  knowledge base's size and weekly growth (facts shown, by status; entities). Time saved is not
  estimated: uses of a fact or a memory note are not logged yet, and the panel says so. Phase C
  adds that instrumentation (fixes shipped, recurrences since each fix, knowledge answers served,
  hours saved as a labelled range).
- **Bosun internals** (`#/impact/internals`, linked at the bottom): what we watch while
  developing bosun, kept off the Impact page: the before/after table for
  `BOSUN_IMPACT_OVERHEAD_FIXED_AT`, context re-read per window, cost per turn by week and the
  guardrails. The Overhead card itself (share, cost per check-in and notification, the weekly chart)
  stays on the Impact page, as part of what bosun costs.
- **Details:** the PRs and reviews behind each week, as links.

## Overhead and guardrails (2026-10-27)

**Why:** check-ins, events summaries, background-task notifications and resume prompts are turns
bosun starts on its own, and each re-reads the session's whole conversation. A first count over
7 days (2026-10-21 to 10-27, list prices) put about half the spend there: the cost of a turn
follows the context it re-reads (typically 250-440k tokens), not how often it fires. The panel makes
that visible and shows whether fixes help, and the guardrails show that the work did not get worse
when the cost fell.

**What started a turn** (`lib/impact_overhead.py`, `Classifier`): the last prompt or injected message
before a call, by its opening; tool results do not start a turn, the summary after `/compact` and
meta entries (a slash command's expanded text) are skipped, except the meta prompts Claude Code
sends on a schedule. The rules carry a version (`RULES_VERSION`) in the cache key.

| Class | Opening | Counts as |
|---|---|---|
| `work` | anything else I typed | work |
| `handover` | `Ledger id`, `Ledger item`, `Update to ledger item`, `Run this task in a subagent` (`lib/activity.py`'s bosun prefixes) | work |
| `subagent` | every call in a subagent's transcript (its prompt comes from its parent) | work |
| `check-in` | `/bosun:chores` (check-in and morning runs) | overhead |
| `events` | `[bosun` (the events summary) | overhead |
| `notification` | `<task-notification` (Claude Code's background-task notice) | overhead |
| `resume` | the opening of `BOSUN_DRAIN_PROMPT` or the watchdog's spend-limit resume prompt | overhead |
| `cross-session` | another session's message (`<cross-session-message`, `[Cross-session`, ...) | overhead |
| `scheduled` | a prompt Claude Code sent on a schedule (a meta entry from source `system`: a session's own cron or wake-up), or the openings in `BOSUN_IMPACT_OVERHEAD_PREFIXES` (`|`-separated; a site's own scheduled prompts) | overhead |

**Per window, day and class** the snapshot keeps the turns, the tokens by model (priced with the
same `PRICES` as the rest of the view) and, for each turn, the context its first call re-read (input
plus cache reads plus cache writes). The per-transcript cache records the classifier's key, so a
changed prefix list re-reads every transcript once.

**The panel:**
- Overhead share and cost per day over the last 7 days against the 7 before (today counts).
- Cost per turn of the overhead that fires most: check-in, notification, resume, events.
- Cost per week, stacked by class (the last 8 weeks with turns).
- Context re-read per turn, per window: median and p90 in thousands of tokens, the latest week
  against the one before, with the number of turns.
- With `BOSUN_IMPACT_OVERHEAD_FIXED_AT`: a before/after table, the 7 days before against the
  days since (at most 7, only the days that passed, as daily averages), with the guardrails over the
  same days.

**Guardrails** (`lib/impact_guardrails.py`, per local day since the start, from bosun's own
state; the panel shows them per week and around the fix):

| Guardrail | Source |
|---|---|
| tasks reported, tasks done | `report` history events (any item); `task` items moved to `done` |
| hand-over to report (h, median) | from an item's latest `delegated` event to its next `report` |
| approval failures | `approval-failed` history events |
| outreach replied / dropped | outreach moved to `replied` or `dropped` |
| events queued / delivered, delay (min, median) | the events queue and its delivery record |
| events pending at day end (week: max) | queued by the day's end and not delivered by then |
| check-ins sent / skipped as nothing new / skipped otherwise | the check-in log: `[send] delivered` lines, `[checkin] skip:` lines whose reason says nothing new (or changed), other skips |

Corrections I type ("no", "actually") were left out: telling a correction from a follow-up by its
words is too unreliable to be a guardrail.

**Watchdog:** `context-size` warns once per conversation when a window's replies have re-read
`BOSUN_CONTEXT_WARN_TOKENS` (300000) or more for a day (a smaller reply, as after `/compact`,
ends the run); `overhead-share` warns once a week when overhead is over `BOSUN_OVERHEAD_WARN_SHARE`
(0.5) of the last 7 days, from a snapshot at most 3 days old.

## Cost per task (2026-10-28)

**Why:** subagents carry about two thirds of all spend, so overhead is the smaller lever. Before
tuning effort, models or subagent use, the question is which kinds of work are costly for what
they deliver.

**Attribution** (`lib/impact_attribution.py`, pure). A turn is what one prompt or injected message
set off (`lib/impact_turns.py`, the same turns the Overhead panel counts). Each turn goes to one
ledger item, by the first rule that applies:

| Rule | When | Source |
|---|---|---|
| hand-over | the turn's prompt is a hand-over bosun pasted; the first known id it names | `delegate.handover_ids`, next to the formats `delegate.py` renders |
| report | the window's next ledger event after the turn is a `report`, within the horizon | the items' histories (`window` on each event) |
| active | the window's latest ledger event at or before the turn (hand-over, update, move, something I said there, `working`, report), within the horizon, unless it was `idle` or the item had closed by then | the same, as `lib/activity.py` defines a window's active item |
| unattributed | none of the above | kept per window |

The horizon is `BOSUN_IMPACT_ATTRIBUTION_HOURS` (8). A subagent's tokens go to the item of its
parent session's turn when it started (its first call); a subagent that started before any parent
turn uses its own start. The rules only read what bosun itself writes (hand-over prompts, the
ledger's histories), so a setup that does not use the ledger gets everything unattributed, per
window, rather than a wrong guess.

**Collected** (`lib/impact_collect.py`): the per-transcript cache (version 3) keeps each turn's
start, the ids its hand-over names and its tokens by model, so attribution re-runs against the
current ledger without re-reading unchanged transcripts. The snapshot's `tasks` holds a row per
item since the start (kind, task type, state, outcome, the PR it is or produced, turns, windows,
first and last day, tokens of its own turns and of its subagents), the unattributed rest per
window and tokens per rule. Fresh conversations (`<window>-<date>-<n>`) count for their window.

**Outcome:** `open` while the item's kind does not end in its state; `merged`; `dropped` for states
that mean the work was given up (`dropped`, `declined`, `dismissed`, `cancelled`, `rejected`,
`no-reply`, and a closed `my-pr`); `done` for every other closed state.

**The panel** (`lib/impact_tasks.py`, pure): cost since the start, the attributed share (and how
much each rule claimed), the subagents' share; cost, median per item and subagent share by
outcome, task type and kind; the `BOSUN_IMPACT_TASKS_TOP` (15) most expensive items with their
kind, outcome, PR and turns; the unattributed rest per window. Prices are the shared `PRICES`; a
model not in the table is priced as `FALLBACK_MODEL` and named on the panel and in the collector's
summary line.

## Decisions (2026-10-26)

1. Phase A first, from data that already exists (GitHub history, transcripts, the ledger).
2. The baseline runs from a configured date (default 13 weeks before the start) to the start,
   over the configured repos.
3. People are counts only: no colleague is named on the page or stored in the snapshot.
4. Time saved is shown only as a calibrated, clearly labelled range (Phase C), never as a bare
   number; measured numbers stay separate.

## Open questions

- Production lead time (Phase B): does a change reach production when its deploy is announced,
  or when the gradual rollout completes? Decide from real deploys.
- Calibration (Phase C): minutes per avoided recurrence and per knowledge answer, set by me after
  a week of looking at the events.
- Should the weekly summary (Phase D) go into the Monday standup draft automatically?

## Checklist (resume here)

- [x] Design note, roadmap entry and Phase A plan (2026-10-26).
- [x] Phase A: collector, `/api/impact`, the Impact page, tests, a real run (see the plan); merged (2026-10-27).
- [x] Phase B0: cost per task: each session and subagent turn's cost attributed to the ledger item
      it worked on; cost per item, task type and outcome; the most expensive items and types
      against what they produced (added 2026-10-27; built 2026-10-28, plan:
      [docs/plans/impact-cost-per-task.md](../plans/impact-cost-per-task.md)).
- [x] Page rework (2026-10-29): "Your time and agent time" with leverage before and since,
      "What learning has produced" from what learning and the knowledge base already record, and
      the overhead diagnostics moved to `#/impact/internals`.
- [ ] Phase B: production lead time and deploy frequency from deploy announcements; review
      rounds, CI first pass, reverts; the flow funnel.
- [ ] Phase C: learning finding classes and recurrence counts, knowledge and memory use events,
      calibrated time-saved ranges (collect from launch, show after 2 to 4 weeks).
- [ ] Phase D: polish, a weekly summary for the Monday standup draft, maybe a monthly report.
- [x] Overhead and guardrails: turn triggers, the Overhead panel, guardrails and the two watchdog
      checks (2026-10-27; plan: [docs/plans/impact-overhead.md](../plans/impact-overhead.md)).
- [ ] Overhead fixes measured: set `BOSUN_IMPACT_OVERHEAD_FIXED_AT` when they roll out and read
      the before/after table after a week.
