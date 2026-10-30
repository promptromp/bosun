# Plan: Impact stats, Phase A

Design: [docs/design/impact.md](../design/impact.md). This file is the build plan; tick items as
they land so a later session can resume.

## Scope

A daily snapshot of GitHub and the transcripts, pure metrics over it, an `/api/impact` route and an
Impact page with delivery against the baseline, leverage, reviews and cost. No deploy data
(Phase B), no new learning events (Phase C).

## Files

| File | What |
|---|---|
| `lib/impact.py` | pure: weeks, baseline and since split, every Phase A metric, `PRICES`, `impact_model(snapshot, now, tz)` for the console |
| `lib/impact_collect.py` | the only I/O: `gh` calls, transcript reading through `lib/claude_state.py`, the per-transcript cache, writing the snapshot atomically; `main(argv)` for the command |
| `lib/claude_state.py` | `transcript_activity(path)`: per message the time, kind (assistant, tool result, prompt), message id, model and usage; the one reader of Claude Code's files |
| `libexec/bosun-impact` | `bosun impact collect [--since DATE] [--dry-run]`, Usage header, passes the config |
| `bin/bosun` | a usage line |
| `lib/common.sh`, `examples/generic/bosun.env` | `set_impact_defaults`: `BOSUN_IMPACT_REPOS`, `BOSUN_IMPACT_START`, `BOSUN_IMPACT_BASELINE_FROM`, `BOSUN_IMPACT_PROMPT_MINUTES`, `BOSUN_IMPACT_COLLECT` |
| `libexec/bosun-morning` | `bosun impact collect` after the session start when `BOSUN_IMPACT_COLLECT=1` |
| `lib/console_state.py` | `read_impact(state_dir)`: the snapshot, or None |
| `lib/console_server.py` | `/api/impact` |
| `console/index.html`, `console/app.js`, `console/app.css` | the Impact nav entry and page, a weekly chart with the baseline band and the start line |
| `tests/unit/test_impact.py` | every metric on acme fixtures |
| `tests/unit/test_impact_collect.py` | the collector with a fake `gh` and fixture transcripts; cache reuse; dry run writes nothing |
| `tests/js/app_check.js`, `tests/js/api_models.py` | the page renders the model, early-data note, drill-down links |
| `tests/smoke.sh` | `bosun impact collect --dry-run` and a real collect with the fake `gh` |
| `README.md` | Commands table row, a short Impact section under Console |

## Steps

- [x] 1. Design note, roadmap, this plan.
- [x] 2. `lib/impact.py` and its tests.
- [x] 3. `claude_state.transcript_activity`, `lib/impact_collect.py`, the command, config keys, tests.
- [x] 4. `/api/impact` and the page, JS checks.
- [x] 5. Smoke checks, README, pre-commit, unit, JS and smoke suites.
- [x] 6. A real collect on this machine and screenshots of `#/impact`; the numbers sanity-checked
      against `gh` by hand.
- [x] 7. PR #10 (2026-10-27).

## Status (2026-10-27)

All steps done; Phase A is in review as PR #10. After it merges: review the page, then set
`BOSUN_IMPACT_COLLECT=1` and start Phase B. The first real collect takes a few minutes, as it
reads the GitHub review history of every reviewed PR; later runs reuse the per-transcript cache
and the reviews of PRs that did not change. Collecting daily stays off (`BOSUN_IMPACT_COLLECT=0`)
until the page has been reviewed. Prices in `PRICES` are assumed list prices: check them against
the current price list before trusting the cost tiles.

## Notes for the next session

- The console reads the snapshot only; it never calls `gh` or reads transcripts itself.
- The snapshot holds no other person's login: reviews store the PR URL, its created time and my
  review times; authorship is a boolean.
