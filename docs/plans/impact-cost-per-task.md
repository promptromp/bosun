# Plan: Impact stats, cost per task (Phase B0)

Design: [docs/design/impact.md](../design/impact.md), "Cost per task". This file is the build
plan; tick items as they land so a later session can resume.

## Scope

Subagents carry about two thirds of all spend, so the overhead panel answers the smaller question.
B0 attributes every session turn's cost, a subagent's included, to the ledger item it worked on,
and shows cost per item, per kind and task type, and per outcome, with the most expensive items
next to what they produced. It measures only: tuning effort, models or subagent use is a later,
separate change.

## Attribution

A turn is what one prompt or injected message set off (the same turns the Overhead panel counts).
Each turn goes to one item, by the first rule that applies:

1. **Hand-over**: the turn's prompt is a hand-over bosun pasted (`Ledger id: <id>`,
   `Update to ledger item <id>`, `Ledger item <id> moves to this window`); the first id it names.
2. **Report**: the window's next ledger event after the turn is a `report` on an item, within
   `BOSUN_IMPACT_ATTRIBUTION_HOURS`: the work that led up to a report belongs to it (a task I
   asked for directly in the window and the window reported on).
3. **Active item**: the window's latest ledger event at or before the turn (hand-over, update,
   move, something I said there, `working`, report), within the same hours, unless it was `idle`
   or the item was closed by then.
4. Otherwise **unattributed**, kept per window (the orchestrator's own turns land here: it
   coordinates every item and holds none).

A subagent's calls go to the item of its parent session's turn at the time the subagent started.

## Works for any setup

The framework is general purpose, so B0 reads only what bosun itself defines:

- **Windows** come from the config (`BOSUN_IMPACT_WINDOWS`, from `window_specs`): any number,
  with or without an orchestrator. A session whose title matches no window keeps its title.
- **Items and kinds** come from the ledger; the kinds' closed states from `ledger.KINDS` (an item
  of a kind this version does not know counts as open). The only state names the outcome mapping
  knows are ones `ledger.KINDS` defines.
- **Attribution** uses only framework mechanisms: the hand-over prompts `bosun delegate`
  renders (parsed next to their formats), the `window` on ledger history events, reports,
  `working` and `idle`. A setup that skips them gets its turns unattributed per window, never a
  guess.
- **Subagents** are optional: a session without any is just its own turns.
- **An empty or brand-new ledger** leaves every turn unattributed; with no turns at all the panel
  says there is no data yet.
- **Prices** come from the shared `PRICES`; an unknown model is priced as `FALLBACK_MODEL` and
  named on the panel and in the collector's summary line.
- Tests use a minimal setup (one window, no orchestrator) and fictional data only.

## Layers

| Layer | File | What |
|---|---|---|
| shared, pure | `lib/impact_turns.py` (new) | the turn walk extracted from `impact_collect.summarize` (`Turn`, `new_call`, `turn_calls`) and the cacheable turn records (start, hand-over ids, tokens by model) |
| format owner | `lib/delegate.py` | `handover_ids(text)` next to the hand-over formats it renders |
| pure | `lib/impact_attribution.py` (new) | the per-window timeline from the ledger's histories, the rules above, per-item and unattributed rows for the snapshot |
| I/O | `lib/impact_collect.py` | turn records in the per-transcript cache (version 3), subagents linked to their parent, `tasks` in the snapshot, the setting |
| pure | `lib/impact_tasks.py` (new) | the panel's model: totals and attributed share, by kind, by task type, by outcome, the top items |
| server | `lib/console_server.py` | `/api/impact` gains `tasks` |
| UI | `console/app.js` | the "Cost per task" section on `#/impact` |
| shared | `lib/impact.py` | `merge_usage` (tokens by model, summed), used by the collector, the overhead panel and B0 |

Settings (defaults in `lib/common.sh`, passed by `libexec/bosun-impact`):
`BOSUN_IMPACT_ATTRIBUTION_HOURS` (8) and `BOSUN_IMPACT_TASKS_TOP` (15). Both are recorded in
the snapshot's settings, so the page shows what the numbers rest on.

## Checklist (resume here)

- [x] `impact_turns.py` and `merge_usage`; `summarize` uses them, its tests unchanged.
- [x] `delegate.handover_ids`.
- [x] `impact_attribution.py` with tests (hand-over, report, active, idle and closed, horizon,
      subagent roll-up, unattributed).
- [x] Collector: turn records cached, `tasks` in the snapshot, window names of fresh conversations
      (`<window>-<date>-<n>`).
- [x] `impact_tasks.py` with tests; `/api/impact`; the panel and its JS check.
- [x] Docs: design section, README, `common.sh`, example env, ROADMAP status.
- [x] Full suite, pre-commit, a dry run against real data.
