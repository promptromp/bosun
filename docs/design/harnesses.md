# Agents: Claude Code and Codex

bosun runs coding agents in tmux windows and needs to know a few things about each: how to start and
resume it, whether it is busy, whether it can take a message, what it is allowed to do. An agent is called
a *harness* in the code. There are two today.

| | Claude Code | Codex |
|---|---|---|
| Repo sessions | Yes | Yes |
| The orchestrator | Yes | Not yet: it stands on Claude Code's hooks, plugins and permission settings |
| Chosen by | The default | `BOSUN_HARNESS=codex`, or `harness: codex` on a window in `sessions.yml` |
| Unattended flags | `BOSUN_CLAUDE_FLAGS`: `--permission-mode auto --chrome` | `BOSUN_CODEX_FLAGS`: `--no-daemon --approve-for-me` |
| Model | `BOSUN_MODEL`, or the window's `model:` | `BOSUN_CODEX_MODEL` (empty: Codex's own default), or the window's `model:` |
| Its files, read through | `lib/claude_state.py` | `lib/codex_state.py` |

A setup can mix them window by window.

## How it is built

Shared code never asks which agent it has. Each layer has one place per agent, and one table or flow over them:

| Layer | Shared | Per agent |
|---|---|---|
| Checking a machine | `lib/preflight.sh`, the flow | `lib/harnesses/<harness>.sh`, a profile |
| Starting a window | `libexec/bosun-launch`, the flow: resolve the session, refuse a second copy, start or resume | `lib/launch/<harness>.sh`: `build_agent_args`, `first_prompt`, `run_new`, `run_resumed` |
| A running session | `lib/agent_state.py`: the `Agent` interface and the `AGENTS` table. `lib/agents.sh` is its shell side | `lib/claude_state.py`, `lib/codex_state.py`, each a module with `Agent`'s functions (`mypy` checks they match) |
| Reading a screen | `lib/pane_input.py`: `typed_text` | A reader per agent in `BOX_READERS` |

`window_specs` is the one source of which agent a window runs: `window_harness` and `harnesses_in_use` read it.

A running agent's record has the fields of Claude Code's running-session record, plus `harness`, so what goes
by a window's status (`send`, `sessions`, check-ins, the watchdog) works for both without knowing which it
has. Adapters share `lib/agent_files.py`, and neither imports the other.

What only one agent has stays out of the interface and is called by name: Claude Code's login checks
(`lib/login.py`), its bypass warning, and the transcripts Impact and learning read.

## What Codex needed

All of this was found by running real Codex 0.166.0 on a real Mac, and none of it is a public API.

**`--no-daemon`.** By default the terminal attaches to a shared background daemon, which holds the session
file. With this flag each process holds its own, which is how bosun tells which session runs in a window.

**`--approve-for-me`, not a bare sandbox.** Under `--sandbox workspace-write` a session cannot read the
Keychain, so a stored GitHub login is out of reach. This flag keeps the sandbox and adds an automatic
reviewer that decides when a command may go past it, with no prompt. It is the closest thing to Claude Code's
auto mode. On top of that, `lib/launch/codex.sh` adds what a task always needs:

- the repo's `.git` as a writable root, since Codex keeps it read-only and nothing could be committed
- bosun's state and log folders as writable roots, for `bosun ledger report`
- network access, for `git` and `gh`

**A first prompt, always.** Codex writes no session file until its first prompt. A Codex with no file looks
just like one stopped at a screen, so `bosun launch` gives a new session a short prompt if it has nothing
to do yet. That costs one small request, once per session.

**Trust lives in `config.toml`.** `[projects."<dir>"]` with `trust_level = "trusted"`, which `bosun trust`
writes. Passing it with `-c` does not skip the screen. On that screen Enter accepts, unlike Claude Code's,
where Enter exits, so `send` must never reach it (below).

**Names are bosun's.** Codex has no way to name a session from outside, so bosun keeps a table in
`codex-sessions.json`, filled in by what Codex runs at the end of a turn (`notify=`, `bosun hook
codex-turn-end`). That report has two traps, both found only by running the real thing:

- It also fires for Codex's hidden helpers, such as the one that writes a session's title, each under an
  id of its own. A thread only counts if it has a session file that came from the terminal.
- `input-messages` lists every prompt of the session so far, on every turn. So bosun counts what it has
  seen, per session, or old messages would come back as things you just said.

**A turn** is `task_started` to `task_complete` or `turn_aborted` in the session file. The reviewer has
session files of its own, marked as such.

**Questions are on the screen only.** A session asking for approval still reads as busy, because the file
does not record the question, and Enter would answer it. So `bosun send` reads the screen first and refuses
(`lib/pane_input.py`). With the default flags Codex does not ask, so this is a guard, not a usual path.

## What Codex windows do not have yet

| Feature | On Codex |
|---|---|
| The orchestrator | Claude Code only. Without Claude Code, set `BOSUN_ORCHESTRATOR=""` to run windows alone |
| Remote Control from your phone | No such thing: drive the window from a terminal |
| A revoked login | Not reported. `codex login status` only says a key is there, and a failed request shows on the screen but not in the session file |
| What you said in a window | Recorded when the turn ends, not when you press Return, because that is when Codex reports it |
| The watchdog's `waiting`, `spend-limit` and `context-size` | `waiting` cannot fire with the default flags. The other two read Claude Code's transcripts |
| Impact stats and daily learning | Read Claude Code's transcripts, so Codex sessions are not counted. `lib/learn_digest.py`'s `Harness` protocol is where an adapter goes |
| Compact-context and signal hooks | Claude Code only. Codex has hooks, but each new one stops at a review screen until a person accepts it, which is why bosun uses `notify=` |
| A real GitHub login inside a session | Not tested: the runs so far used a stand-in `gh`. A real one is expected to go through the reviewer, since the sandbox blocks the Keychain |

## Adding another

1. A profile in `lib/harnesses/<harness>.sh`, so preflight can check it (`tests/unit/test_preflight.py`
   checks it has every function).
2. A `lib/<harness>_state.py` with the functions of `Agent`, and a line in `AGENTS` (`lib/agent_state.py`).
   `mypy` checks it has them all.
3. `lib/launch/<harness>.sh`, with the functions of `lib/launch/codex.sh`.
4. A stand-in in `tests/sandbox/`, a section of `tests/sandbox/fresh_user.sh`, and a run with the real thing:
   `tests/sandbox.sh mac live <harness>`. The stand-in only behaves the way you already believed: every trap
   above was found by the real run, so teach the stand-in each one afterwards.
5. The docs: this note, [Setup](../setup.md#running-repo-sessions-on-codex), the README's Status section,
   [Known limits](../limits.md) and `ROADMAP.md`.
