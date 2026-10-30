# Plan: Impact stats, overhead and guardrails

Design: [docs/design/impact.md](../design/impact.md), "Overhead and guardrails". This file is the
build plan; tick items as they land so a later session can resume.

## Scope

Measure what bosun itself costs and whether fixing it hurts the work: classify what started
every turn, keep the context each turn re-read, add guardrails from bosun's own state, show
both on the Impact page with a before/after table around a fix date, and warn from the watchdog.
The fixes themselves (fewer resume prompts, check-ins skipped when nothing changed, bounded and
one-shot waits, fresh conversations for idle repo sessions) are separate changes; this work only
measures them.

## Files

| File | What |
|---|---|
| `lib/impact_overhead.py` | pure: the classes and `Classifier`, per-call accounting (`add_call`), the Overhead model (last 7 days against the 7 before, weeks, contexts per window, the before/after table), the guardrails model, `from_snapshot` for the console and the watchdog |
| `lib/impact_guardrails.py` | I/O: per-day guardrail rows from the ledger's histories, the events queue and delivery record, the check-in logs |
| `lib/claude_state.py` | `ACTIVITY_INJECTED` (a notification or another session's message starts a turn), `context_run` and `reply_context` (the watchdog's context-size check) |
| `lib/impact_collect.py` | triggers per window and day in `summarize`, the cache keyed by the classifier, the guardrail rows and the fix date in the snapshot, `BOSUN_IMPACT_OVERHEAD_PREFIXES`, `BOSUN_IMPACT_OVERHEAD_FIXED_AT`, `BOSUN_LOG_DIR` |
| `libexec/bosun-impact` | passes the new settings; Usage header |
| `lib/console_server.py` | `/api/impact` gains `overhead` |
| `console/app.js` | the Overhead section, the before/after table, contexts per window, cost per turn by week, the guardrails table |
| `lib/watchdog.py`, `libexec/bosun-watchdog` | `context-size` and `overhead-share` checks, `BOSUN_CONTEXT_WARN_TOKENS`, `BOSUN_OVERHEAD_WARN_SHARE` |
| `lib/common.sh`, `examples/generic/bosun.env` | the four config keys; the watchdog settings list |
| `README.md`, `ROADMAP.md`, `docs/design/impact.md` | the panel, the watchdog rows, the roadmap item, the design section |
| tests | `tests/unit/test_impact_overhead.py` (new), `test_impact_collect.py`, `test_watchdog.py`, `tests/js/app_check.js` and `api_models.py` |

## Steps

- [x] 1. Classification and per-day triggers in the collector; the cache keyed by the classifier.
- [x] 2. Guardrail rows (ledger, events, check-in logs) in the snapshot.
- [x] 3. The Overhead model and guardrails model; `/api/impact`; the page sections; JS checks.
- [x] 4. The watchdog checks, config keys, README and design note.
- [x] 5. Unit, JS and smoke suites; pre-commit.
- [x] 6. A real collect, the per-class table for the last 14 days against the saved baseline
      (`~/.local/state/bosun/overhead-baseline/2026-10-27-7d.txt`, outside the repo), a
      screenshot of `#/impact`.
- [ ] 7. When the fixes roll out: set `BOSUN_IMPACT_OVERHEAD_FIXED_AT` to that day, collect daily,
      read the before/after table after a week (cost down, guardrails holding).

## Comparing daily

`bosun impact collect` (or the morning run with `BOSUN_IMPACT_COLLECT=1`), then the Impact
page's Overhead section. Without the console:

    python3 -c 'import json,sys,datetime as d; sys.path.insert(0,"lib"); import impact_overhead as o;
    s=json.load(open(sys.argv[1])); m=o.from_snapshot(s, d.datetime.now(d.UTC).astimezone(), d.datetime.now().astimezone().tzinfo);
    print(json.dumps({k: m[k] for k in ("last","previous","fix")}, indent=1))' ~/.local/state/bosun/impact/snapshot.json
