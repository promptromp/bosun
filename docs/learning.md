# Learning

`bosun learn` looks back over the day's work and proposes what should change, so the same mishap does not happen
twice: a memory note for a window, a rule for the orchestrator or the task rules, a setup change. Nothing is applied
until you approve it. It is off in bosun's own defaults, and `bosun init` recommends turning it on. Design and
phasing: [design/learning.md](design/learning.md).

## Turning it on

With `BOSUN_LEARN_ENABLED=1`, `install.sh` loads a launchd job that runs it on your days at `BOSUN_LEARN_AT`
(18:30), over what happened since the last run (at most `BOSUN_LEARN_HOURS`, 24). On a new install, `bosun init`
sets this when you choose "Proactive". It skips while bosun is stopped by `bosun quit`.

You can run it by hand any time: `bosun learn`; `--dry-run` writes only its run folder (the digest, the prompt, the
raw result, `stderr.log` and the report) and no ledger items, state or log; `--collect-only` writes only the digest
(no Claude call, no cost).

Run folders older than 14 days, dry runs' too, are deleted when a run starts: one you type, a dry run,
`--collect-only`, or the scheduled one even when it is not due. Not one skipped because an update is holding bosun's
background work, nor a scheduled one while bosun is stopped by `bosun quit`.

## What a run does

1. **A digest** (no Claude, free): from every bosun window's transcripts, the ledger's history, bosun's logs and the
   watchdog's alerts, it keeps what tells a story: who sent each prompt, permission prompts and how long they
   waited, refused and failed tool calls, guard denials, questions to you, the `bosun` commands run, and the end of
   each turn. Deterministic checks of the ledger are added, and the instructions and memory in force, and what
   earlier runs found.
2. **An analysis:** one headless Claude with no tools and no MCP servers reads the digest and returns findings
   (a stall, a mishap, a duplicate message, a correction you repeated, a missing rule, a setup or outreach idea, a
   memory note), each with evidence quoted from the digest. A finding whose quote is not in the digest is dropped.
3. **Proposals:** each new finding with a change becomes a `learn:<YYYY-MM-DD>/<id>` ledger item holding the exact
   change, and the orchestrator lists it under *Needs your approval*. Findings are remembered, so a later run counts
   a known problem again instead of proposing it twice.

The run's report is in `learn/runs/<time>/report.md` in the state folder, and the console's
[Improvements view](console.md#console) shows every run, proposal and remembered finding.

## Approving what it proposes

Say yes to a proposal, and the orchestrator runs `bosun learn apply <id>`, which makes the change:

- **a memory note** for a window's folder (as `bosun sessions` shows it now), which a Claude Code window reads; or
- **a rule** appended to your `policy.md` or `tasks/_common.md` in the config folder (the file before is kept in
  `learn/backups/` in the state folder).

Changes to bosun's own code, `sessions.yml` or `bosun.env`, outreach, and ledger corrections are yours to make (or
to tell the orchestrator); then `bosun learn done <id>` closes the proposal. `bosun learn reject <id>` declines one
for good: that finding is never proposed again.

`apply` makes the change only if the item and its proposal are still exactly what learning proposed. It keeps
digests of both in its own state, so only the Mac that proposed a change can apply it; anywhere else, or after an
edit, it refuses, and you reject the proposal instead.

## What it reads, and what it never reads

- It reads only bosun's own windows (`bosun sessions`), including the orchestrator's daily conversations. Other
  sessions in the same folders are not read, and `BOSUN_LEARN_EXCLUDE_SESSIONS` lists session ids it must never
  read.
- Tokens, keys, passwords and email addresses are redacted from the digest.
- It reads Claude Code's transcripts only, not yet [Codex's or Antigravity's](design/harnesses.md#what-windows-do-not-have-yet),
  and a memory note it writes is not read by a Codex window.

## Settings

| Key | Default | What it sets |
|---|---|---|
| `BOSUN_LEARN_ENABLED` | `0` | The daily run, and the watchdog's `learn` check |
| `BOSUN_LEARN_AT` | `18:30` | When it runs, on `BOSUN_DAYS` |
| `BOSUN_LEARN_HOURS` | `24` | How far back a run looks, at most |
| `BOSUN_LEARN_MODEL`, `BOSUN_LEARN_EFFORT` | `opus`, `medium` | The analysis's model and effort |
| `BOSUN_LEARN_MAX_BUDGET_USD` | `2` | A run estimated over it is not started |
| `BOSUN_LEARN_MAX_PROPOSALS` | `5` | Proposals per run, at most |
| `BOSUN_LEARN_MAX_DIGEST_KB` | `400` | The digest's size; over it, the least telling parts are left out, and the digest says which |
| `BOSUN_LEARN_TIMEOUT_MINUTES` | `30` | The watchdog's `learn` check alerts when a run is missing an hour after `BOSUN_LEARN_AT`, failed, crashed, or runs past this |
| `BOSUN_LEARN_EXCLUDE_SESSIONS` | empty | Session ids it must never read |

Your own instructions for the analysis go in `learn.md` in the config folder; bosun's are
`orchestrator/learn.md`.

<details class="note" markdown>
<summary>Everything the digest holds</summary>

For every bosun window, including the orchestrator's daily conversations, it reads the window's Claude Code
transcripts in the time window and keeps who sent each prompt (you or bosun, and whether it arrived while the
session was busy), permission prompts and how long they waited, refused and failed tool calls, guard denials,
questions to you, the `bosun` commands run and the end of each turn. Then the ledger's history, the bosun logs
(repeated lines collapsed), the watchdog's alerts, deterministic ledger checks (overlapping outreach, `info-ack`
outreach still waiting for a confirmation, which maybe should be `fyi`, sensitive subjects addressed to a channel, an
item handed to two windows; and lineage: a pull request a report names with no item or link, outreach closed after a
delegate proposal with no task, tasks proposed for days, tickets out of step with their work), the instructions and
memory in force, and what earlier runs found. The item a proposal makes holds the exact change in its fields
(`target`, `text` and, for a memory note, `window`), so another Mac sees it with the ledger, and has it as its pending
proposal. Its id comes from the finding and the run, so one run never proposes a finding twice.

</details>
