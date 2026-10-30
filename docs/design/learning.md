# Design note: continuous learning

Status: MVP built (2026-10-16), off by default (`BOSUN_LEARN_ENABLED=0`). Tracked in
[ROADMAP.md](../../ROADMAP.md#5-continuous-learning).

## Why

Bosun's mishaps are found by accident today. In its first days the orchestrator sat at an
unanswered permission prompt for hours; repo sessions stopped at prompts nobody saw; a message
pasted into a busy session went missing; a rehearsal ran the installed CLI instead of the checkout;
outreach was recorded with the wrong send time; a repo session filed the same question twice, once
in a public channel. Each was noticed by the user, fixed by hand, and its lesson lived on only in a
chat or a commit message. The user also corrects the same behaviour more than once, because nothing
carries a correction from one conversation to the next.

A learning loop looks back over a recent window, across the orchestrator and the repo sessions,
cross-references what happened with the ledger, and turns patterns, gaps and mishaps into concrete,
reviewable changes: a memory note, a rule in the instructions, a setup change, a message to someone.
The instructions (policy, chores, task rules, memory) become living objects that improve from
evidence rather than static framework files.

## What exists (inputs)

- **Transcripts** of every Claude Code session (`~/.claude/projects/<folder>/<session>.jsonl`, with
  subagents beside them): prompts and where they came from, tool calls and results, the user's
  answers to permission prompts (`permissionDecision.source` `user_*`), rejections and what the
  user said, `queue-operation` entries for messages that arrived while the session was busy, hook
  decisions (the guard), compactions and warnings. All of it is a Claude Code internal, read only
  in `lib/claude_state.py`.
- **The ledger** (`ledger.json`): every work item with its full history (transitions, delegations,
  reports, proposals and their outcome, outreach messages, corrections).
- **Logs** (`logs/morning-*`, `checkin-*`) and the **watchdog's** alerts (`watchdog.json`).
- **Live windows**: `bosun sessions --json` (new) lists every configured or open window with
  its Claude session id, status and transcript path.
- **What is in force**: `orchestrator/policy.md`, `chores.md`, the task rules, the site overlays,
  and the orchestrator folder's Claude Code memory index.

## Shape

**Two stages, deterministic first.**

1. `lib/learn_digest.py` builds a compact, redacted **digest** of the window. Per session it keeps
   what tells a story and drops the rest (file reads, diffs, ordinary shell output): who sent each
   prompt (the user, or bosun: check-ins, delegations, drain prompts; and whether it arrived
   while the session was busy, into a running turn, or never), permission prompts with how long
   they waited, rejections with what the user said, guard denials, questions to the user, failed
   tool calls, every `bosun` command, and the end of each turn. A session with more than
   `BOSUN_LEARN_MAX_EVENTS` keeps the most telling ones (prompts and permission events before
   errors before replies). Then the ledger's history in the window, the open items,
   **deterministic ledger checks** (`lib/learn_checks.py`: open outreach that overlaps other
   outreach by parent, recipient and subject or by Slack conversation; outreach that expects no
   reply but waits for one; a security-sensitive subject addressed to a channel rather than a DM;
   an item handed to a second window before the first reported; and lineage, see
   [lineage.md](lineage.md): a reported PR with no item or link, outreach closed after a delegate
   proposal with no task, tasks proposed for days, ticket mismatches), the logs with repeated lines
   collapsed, the watchdog's alerts, the findings of earlier runs, and the instructions and memory
   in force, and the commits to the bosun checkout in the window (so a problem already fixed
   needs no proposal). On three and a half real days the digest was about 240 KB; collecting it
   takes under two seconds. A busier stretch is larger (three days in October 2026: 1.1 MB, $4.05
   for one call), so the digest has a cap, `BOSUN_LEARN_MAX_DIGEST_KB` (400, which fits the
   default budget). Over it, the digest leaves out, step by step and only as far as needed:
   routine ledger history (an item filed or linked), ordinary log lines (errors, failures, skips,
   denials and alerts stay), the tail of long ledger history lines, older ledger history a quarter
   of the window at a time, and only then events per session (the most telling and newest stay),
   since the sessions tell the most; a last resort cuts the end of the activity.
   The digest says at the top what it left out. Before the call, `lib/learn.py` estimates its cost
   from the prompt's size (`CHARS_PER_TOKEN`, `INPUT_USD_PER_MTOK`, measured on that run) and does
   not call Claude when the estimate exceeds `BOSUN_LEARN_MAX_BUDGET_USD`, since Claude Code
   checks `--max-budget-usd` only after a turn. The run's line shows the cost, the estimate and
   the budget.
2. `lib/learn.py` gives the digest to **one headless Claude** (`claude -p`) with the prompt in
   `orchestrator/learn.md` (versioned in the repo, generic) plus `$BOSUN_CONFIG_DIR/learn.md`.
   It runs with no tools (`--tools ""`), no MCP servers (`--strict-mcp-config`), `dontAsk` and no
   permission prompts, no session file, a budget cap and a JSON schema for its answer. It returns
   **findings**: category (stall, mishap, duplicate, repeated correction, gap, prompt gap, setup,
   outreach, memory, ledger hygiene), severity, a stable kebab-case key, a summary, evidence
   (source, ref, time and a verbatim quote of a digest line) and optionally one **proposal**
   (target and exact text).

**Grounding.** Every evidence quote is checked against the digest (whitespace aside; an ellipsis
may join two parts of one line). Findings with no quote found are dropped and listed in the
report. This is the main guard against invented findings, and it is cheap.

**De-duplication.** `learn/state.json` remembers every finding by key: first and last seen, times
seen, and what happened to it (reported, proposed, applied, rejected, synced from the ledger). The
digest lists them so the analysis reuses keys; a known key is counted, never proposed again once
proposed, applied or rejected.

**Proposals flow through the ledger.** A new ledger kind, `learning`, with ids
`learn:<YYYY-MM-DD>/<n>`, starts in `proposed` with the exact change as its pending proposal, so the
orchestrator lists it under *Needs your approval* at the next check-in (one sentence in
`chores.md`). The user answers there like for any other proposal. Then:

| Target | `bosun learn apply <id>` does | Why this way |
|---|---|---|
| `memory` | writes a Claude Code memory note for the named window's folder (default: the orchestrator's) and adds it to `MEMORY.md` | facts and preferences; Claude Code memory is the harness's own mechanism |
| `policy` | appends the rule to `$BOSUN_CONFIG_DIR/policy.md` under a marker naming the id | the site overlay: no repo change, applies at the orchestrator's next conversation |
| `task-rule` | appends it to `$BOSUN_CONFIG_DIR/tasks/_common.md` | every delegated task gets it; a site file named like a task type would replace that type, so only `_common.md` is appended to |
| `repo-change`, `setup`, `outreach`, `ledger` | nothing: the user makes the change, then `learn done <id>` | repo changes must stay generic and go through review; `sessions.yml` and outreach are the user's call |

`learn reject <id>` declines a proposal for good. The applied text comes from `learn/state.json`,
written when the proposal was made, not from ledger fields that any session could edit; the path is
computed by code from the target, never taken from the model. Before a file changes, its previous
version goes to `learn/backups/`. Applying is idempotent (the marker is checked).

**Runtime.** `bosun learn` runs once: the window starts where the last successful run ended,
at most `BOSUN_LEARN_HOURS` back (`--since`/`--until` override); `--dry-run` writes only its
run folder; `--collect-only` makes no Claude call; `--status` shows the schedule, the last runs, the
remembered findings and what waits for approval. With `BOSUN_LEARN_ENABLED=1`, `install.sh`
loads a launchd agent (`templates/launchd-learn.plist`) that runs `learn --scheduled` daily at
`BOSUN_LEARN_AT` on `BOSUN_DAYS` (once a day; a run missed during sleep happens on wake).
A run holds a lock, marks itself running (pid and start) in `learn/state.json`, and records its
outcome however it ends.

**The watchdog's `learn` check** (only when enabled) alerts once per problem: a run that is still
going after `BOSUN_LEARN_TIMEOUT_MINUTES` (stalled), one marked running whose process is gone
(crashed), a failed last run, no run an hour after `BOSUN_LEARN_AT` on a scheduled day
(missing, confirmed on two watchdog runs), and an unreadable state file. Learn's own `ERROR` log
lines are left to this check, so they do not alert twice.

## MVP cut, and why

In: the digest with the session adapter, ledger, logs and deterministic checks; one analysis call
with grounding; de-duplication; proposals as ledger items; apply for memory, site policy and task
rules; the launchd agent and the watchdog check; unit and smoke tests; an eval with planted issues;
a read-only dogfood run on real data.

Deferred (ROADMAP), each for a reason:

- **Repo patches applied by `learn`.** Changes to generic instructions need the publishing rules
  (no names) and a human commit; the proposal describes the change instead.
- **Applying setup and outreach changes.** `sessions.yml` edits and messages to people are the
  user's call; outreach already has its own proposal flow once the user asks for it.
- **Reading raw transcript excerpts on demand.** A tool-less analysis cannot be steered into
  reading or doing anything; giving it a sandboxed Read over excerpts is the next step if digests
  prove too thin. (Claude Code's `dontAsk` mode still allowed reads outside the working folder in
  a probe, so a sandbox needs explicit deny rules.)
- **Trend views across runs** (a problem's frequency over weeks) and **automatic retirement** of
  rules that no longer fire.
- **Other harnesses.** The digest reads sessions through a `Harness` protocol (transcripts,
  events, subagents, memory folder); Claude Code is the only adapter. A second one plugs in there,
  and `memory` proposals would then go to that harness's memory mechanism.

## Safety and privacy

- **Untrusted input.** Transcripts quote Slack messages, PR text and tool output written by others.
  The analysis has no tools, so text in the digest can at most shape findings; every proposal is a
  ledger proposal the user reads in full before anything changes, and `apply` writes only text the
  learner recorded, to a path code chooses. The prompt says the digest is data, not instructions.
  Nothing is applied automatically, deliberately, for the same reason as the knowledge note: an
  automatic write would let anyone's message plant a "lesson" that steers later sessions.
- **No second permission prompt** on `learn apply`: like `delegate --new`, it is approval-only by
  policy (the chores step), because an extra prompt may not be answerable from the phone and an
  unanswered prompt is exactly the stall this feature looks for. Open question below.
- **Data minimisation.** Only sessions of bosun windows are read; other conversations in the
  same folders are skipped, and `BOSUN_LEARN_EXCLUDE_SESSIONS` excludes ids outright. The digest
  keeps short excerpts (prompts to 600 characters, other lines to 300) and redacts tokens, keys,
  passwords, signed URL queries and email addresses. The analysis call sends it to the same model
  provider the sessions already use; no other service sees it.
- **Retention.** Run folders (digest, prompt, raw result, report) are deleted after 14 days;
  `state.json` keeps keys, titles and counts, not excerpts.
- **People.** Memory notes are work-relevant only (how someone prefers to work with the user, never
  judgements); the prompt says so, and the user reviews each note.

## Test harness

- **Unit tests** (`tests/unit/test_learn*.py`, `test_transcript_events.py`,
  `test_watchdog_learn.py`) run against a fictional world (`tests/llm/learn_world.py`: acme's
  invoice exporter, Monday to Wednesday, three repo windows and the orchestrator's daily
  conversations), checking the digest line by line, the ledger checks, grounding, de-duplication,
  proposals, apply/done/reject, the schedule and the watchdog check; the analysis is a fake
  `claude`.
- **Smoke** (`tests/smoke.sh`): `sessions --json`, `learn --collect-only`, a run with a fake
  analysis that proposes, applies and does not re-propose, the launchd template, and install /
  uninstall with and without `BOSUN_LEARN_ENABLED`.
- **Eval** (`tests/llm/learn_eval.py`, real Claude, not run by hooks): the same world with eleven
  planted issues (overnight permission prompt, repeated guard denial, duplicate hand-over, a
  correction made twice, a report never recorded, an unanswered question with no nudge, an idle
  delegated window, outreach drafts never proposed, near-duplicate outreach, a follow-up recorded
  as new outreach, a sensitive subject addressed to a public channel) next to clean noise. Scored
  for recall of the planted issues, precision (findings that match one), dropped (ungrounded)
  findings, and the shape of the proposals. First results (opus, medium effort, two runs with the final
  scoring): recall 100% both times, precision 91% and 100% (the one unmatched finding was true: a
  Slack reply sent without a ledger record), no ungrounded findings, five well-formed proposals
  per run, about $0.33 a run.

**Dogfood (read-only, three and a half real days, three dry runs, about $1.05 each).** Every
run found the overnight permission-prompt stall (with the check-in log and the morning refresh as
evidence) and the user's repeated corrections (a review verdict the user had said not to use, a
fix task scoped to one reviewer). After the prompt named them (version 2), it also found the
repo-session permission prompt on deleting files and the outreach backfilled with the wrong send
time (and said the fix was already in place). On the live duplicate-outreach case it reported the
follow-up recorded as a new item in a thread that already had sent outreach, and FYIs recorded as
outreach that would wait for replies, and judged correctly that the near-duplicate pair had already
been merged and that the public channel was the user's own choice. New, useful findings: work a
repo session did at the user's direct request never reached the ledger, so the orchestrator
proposed messages already sent; subagents kept using blocked `sleep` chains; a PR waited on two
people for days with no outreach item. No finding was dropped for missing evidence. Misses: a
message lost in a busy session (the digest shows deliveries into running turns, none lost in the
window), and a rehearsal that ran the installed CLI (headless rehearsals keep no transcript). One
stale proposal: an alert for long prompts, which the watchdog already has (the analysis does not
see the watchdog's checks yet).

## Phasing

1. **MVP (this):** daily digest and analysis, proposals for approval, apply for memory and site
   rules, watchdog check. Enable after a week of manual runs (`bosun learn --dry-run`).
2. **Closing the loop:** after a change is applied, check in later runs whether its finding stops
   recurring, and report changes that did not help (keys ending in `-again`).
3. **Richer evidence:** sandboxed on-demand reads of transcript excerpts; PR and Slack outcomes
   (merged, reverted, replied) linked to ledger items.
4. **Living instructions:** proposals as diffs to the generic prompts, reviewed as PRs, and
   per-task-type statistics (how often a task type needs correction).

Next small steps: give the analysis the watchdog's check list and the command list, so it does not
propose what exists; a deterministic check for one message recorded on one of several outreach
items it answers.

## Open questions

- Should `learn apply` also require a Claude Code permission prompt, once phone approvals are
  verified to work through Remote Control?
- Daily cadence or after every N check-ins? Daily is cheap and matches the user's rhythm; patterns
  across days rely on the remembered findings.
- Should repeated findings escalate on their own (a third sighting of an applied rule's problem
  proposes a `repo-change` for hard enforcement)?
- Where memory for a shared orchestrator folder should live when the user's own sessions use the
  same folder (today the orchestrator's memory is that folder's Claude Code memory).
