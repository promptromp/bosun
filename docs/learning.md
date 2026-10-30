# Learning

`bosun learn` looks back over recent work and proposes what should change, so the same
mishaps do not happen twice. Design and phasing: [docs/design/learning.md](design/learning.md).

1. **Digest** (no Claude, free): for every bosun window (`bosun sessions`), including the
   orchestrator's daily conversations, it reads the window's Claude Code transcripts (not yet
   [Codex's](design/harnesses.md#what-codex-windows-do-not-have-yet)) in the time
   window and keeps what tells a story: who sent each prompt (you or bosun, and whether it
   arrived while the session was busy), permission prompts and how long they waited, refused and
   failed tool calls, guard denials, questions to you, the `bosun` commands run and the end of
   each turn. Then the ledger's history, the bosun logs (repeated lines collapsed), the
   watchdog's alerts, deterministic ledger checks (overlapping outreach, `info-ack` outreach still
   waiting for a confirmation, which maybe should be `fyi`, sensitive subjects addressed to a
   channel, an item handed to two windows; and lineage: a PR a report names with no item or link,
   outreach closed after a delegate proposal with no task, tasks proposed for days, tickets out of
   step with their work), the instructions and memory in force, and what
   earlier runs found. Tokens, keys, passwords and email addresses are redacted; other sessions in
   the same folders are not read, and `BOSUN_LEARN_EXCLUDE_SESSIONS` lists session ids it must
   never read.
2. **Analysis**: one headless Claude with **no tools and no MCP servers**, its prompt from
   `orchestrator/learn.md` (plus `$BOSUN_CONFIG_DIR/learn.md`), returns findings (stall,
   mishap, duplicate, repeated correction, gap, prompt gap, setup, outreach, memory, ledger
   hygiene), each with evidence quoted from the digest. Findings whose quotes are not in the
   digest are dropped.
3. **Proposals**, never applied on their own: each new finding with a change (at most
   `BOSUN_LEARN_MAX_PROPOSALS` a run) becomes a `learn:<YYYY-MM-DD>/<n>` ledger item whose
   pending proposal is the exact change, so the orchestrator lists it under *Needs your approval*.
   On your yes it runs `bosun learn apply <id>`: a memory note for a window's folder, or a rule
   appended to `$BOSUN_CONFIG_DIR/policy.md` or `tasks/_common.md` (the previous file is kept
   in `learn/backups/`). Changes to this repo, `sessions.yml` or outreach you make yourself, then
   `learn done <id>`; `learn reject <id>` declines one for good. Findings are remembered by key, so a
   later run counts a known problem again instead of proposing it twice.

It runs by hand any time (`--dry-run` writes nothing but its report; `--collect-only` only the
digest). With `BOSUN_LEARN_ENABLED=1`, `install.sh` loads a launchd agent that runs it daily at
`BOSUN_LEARN_AT` on `BOSUN_DAYS` (window: since the last run, at most `BOSUN_LEARN_HOURS`),
and the watchdog's `learn` check alerts when that run is missing, failed, crashed or runs past
`BOSUN_LEARN_TIMEOUT_MINUTES`. Model, effort and a per-run budget: `BOSUN_LEARN_MODEL`,
`BOSUN_LEARN_EFFORT`, `BOSUN_LEARN_MAX_BUDGET_USD` (a run estimated over it is not
started). The digest is capped at `BOSUN_LEARN_MAX_DIGEST_KB` (400); over it, the least telling
parts are left out, and the digest says which.
