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
| What an agent is and can do | `lib/agent_state.py profiles` prints one tab-separated line per agent, which `lib/agents.sh` reads (`profile_field`, `known_harnesses`, `agent_bin_env`, `harness_bin`, `harness_default_model`) | A `Profile` in each adapter (`PROFILE`, `lib/agent_files.py`): its config keys, whether its name for a session comes from the agent, which statuses it reports, Remote Control, autocompact, per-task subagents, its state files, and how `bosun init` shows it |
| Reading a screen | `lib/pane_input.py` asks the `AGENTS` table; `lib/screen_text.py` has the helpers readers share | A `Screen` in its profile: `typed`, `question`, `rejected` (`lib/agent_files.py`) |
| Holding a window to its connectors | `lib/connectors.py`: reading a list, the report's helpers, and `doctor`, which imports the module the window's profile names | `lib/connectors_<harness>.py`, named by `Profile.connectors`: `main` (its launch command line) and `doctor_line` |
| The end of a turn | `lib/hook.py`'s `turn-end --harness <agent>`: the update gate, events, the log | A `TurnEnd` in its profile, or none (`lib/agent_files.py`): `report` (what its JSON says: `TurnReport`, or why it is ignored) and `remember` (the name its session goes by) |

`window_specs` is the one source of which agent a window runs: `window_harness` and `harnesses_in_use` read it.

`bosun launch` refuses an agent that has no profile (`no agent '<h>': bosun knows claude, codex`). Delegation
asks the profile whether a window can take a per-task subagent, and `bosun init`'s agent question and plan
take each agent's name, preview and notes from its profile.

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

**What a window reaches.** A Codex window starts with the MCP servers its connectors list names, as a
Claude Code window does ([connectors](connectors.md)). At launch `lib/launch/codex.sh` has
`lib/connectors_codex.py` list the servers Codex would start in that folder (`codex mcp list --json`, names
only: a stdio server's entry holds its environment in plain text), once with plugins and once with
`--disable plugins`, so a server only in the first is a plugin's. It adds one `-c
mcp_servers={<name>={enabled=false},...}` for every configured server the list does not name, each name a
bare TOML key or a TOML basic string; `--disable plugins` when the list does not name one of the plugins'
servers, since a plugin's server is not an `mcp_servers` table (naming one there stops Codex) and nothing
Codex prints says which plugin it is from, so every plugin goes, a plugin server the list names too; and
`--disable apps` to switch ChatGPT's connectors off; nothing for `all`. One inline table, because Codex splits a `-c` path at every dot, quotes included (`config/src/overrides.rs`), so
`-c mcp_servers."a.b".enabled=false` names a server `"a` and Codex exits at start (`invalid transport`); a
table in the value is read as TOML and merges into the configuration. The Mac VM (`tests/sandbox.sh mac live
codex`, Codex 0.166.0) showed it for a user's server, a project's and one named with a dot, new and after
`codex resume` (`/mcp` shows each `disabled (0 tools)`), and that `--disable apps` is taken
([connectors](connectors.md#what-the-vm-showed)). Codex's built-in servers (`codex_worktrees`, and
`codex_apps` without `--disable apps`) are not in the listing and stay on, as Claude Code's Chrome and
computer-use do: they are local, and a list cannot name them. A listing that fails, or arguments of any other
shape than `[-c mcp_servers={...}] [--disable plugins] --disable apps`, keep the window from starting: bosun cannot tell which servers to
switch off, and it would rather not start the window than start it with all of them. A launch looks up
once, however many times it builds its arguments: one lookup of two listings (with plugins, and with
`--disable plugins`). `launch --fingerprint` lists too, since the fingerprint holds
these arguments; when that fails during an update, the window reads as changed and its restart refuses to
start it.

**No update screen.** Once Codex has seen a newer version of itself, a start shows "Update available" and
waits for a key (seen in the Mac VM on 0.166.0), which stalls an unattended start, and the Enter that `bosun
send` presses would answer it. The launch passes `-c check_for_update_on_startup=false`, the key Codex
documents for centrally managed updates; Codex is updated the way it was installed (`brew upgrade`).

**A first prompt, always.** Codex writes no session file until its first prompt. A Codex with no file looks
just like one stopped at a screen, so `bosun launch` gives a new session a short prompt if it has nothing
to do yet. That costs one small request, once per session.

**Trust lives in `config.toml`.** `[projects."<dir>"]` with `trust_level = "trusted"`, which `bosun trust`
writes. Passing it with `-c` does not skip the screen. On that screen Enter accepts, unlike Claude Code's,
where Enter exits, so `send` must never reach it (below).

**Names are bosun's.** Codex has no way to name a session from outside, so bosun keeps a table in
`codex-sessions.json`, filled in by what Codex runs at the end of a turn (`notify=`, `bosun hook
turn-end --harness codex`; `codex-turn-end` is the same, kept for windows started before). That report has two traps, both found only by running the real thing:

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
| The orchestrator | Claude Code only, and it must be installed. `BOSUN_ORCHESTRATOR=""` runs windows with no orchestrator and stops preflight asking for it, but `bosun init`, `install.sh` and `bosun doctor` still require the `claude` command |
| Remote Control from your phone | No such thing: drive the window from a terminal |
| A revoked login | Not reported. `codex login status` only says a key is there, and a failed request shows on the screen but not in the session file |
| What you said in a window | Recorded when the turn ends, not when you press Return, because that is when Codex reports it |
| The watchdog's `waiting`, `spend-limit` and `context-size` | `waiting` never fires, whatever the flags: the session file records only idle or busy, so a question reads as busy. Only `bosun send` reads a question, from the screen (above). The other two read Claude Code's transcripts |
| Impact stats and daily learning | Read Claude Code's transcripts, so Codex sessions are not counted. `lib/learn_digest.py`'s `Harness` protocol is where an adapter goes |
| Autocompact | Claude Code only: `BOSUN_AUTOCOMPACT_TOKENS` and `autocompact:` do nothing on a Codex window |
| `shell`, `idle-bg` and the watchdog's `long-shell` | Codex's session file records only idle or busy, so a background command left running does not show |
| Per-task model and effort on a running session | A task's settings reach a running Claude Code session through a subagent, an instruction that names its Agent tool. A running Codex session gets the task at its own settings. A window opened for the task (`--new`) does start at them |
| `bosun resolve` | Claude Code's names only. A Codex window's are in `codex-sessions.json` |
| Compact-context and signal hooks | Claude Code only. Codex has hooks, but each new one stops at a review screen until a person accepts it, which is why bosun uses `notify=` |
| A real GitHub login inside a session | Not tested: the runs so far used a stand-in `gh`. A real one is expected to go through the reviewer, since the sandbox blocks the Keychain |

## Adding another

1. A profile in `lib/harnesses/<harness>.sh`, so preflight can check it (`tests/unit/test_preflight.py`
   checks it has every function).
2. A `lib/<harness>_state.py` with the functions of `Agent` and a `PROFILE` (`Profile` in `lib/agent_files.py`:
   say what the agent is and can do, with a `Screen` and, if it reports its turns, a `TurnEnd`), and a line in
   `AGENTS` (`lib/agent_state.py`). `mypy` checks it has them all.
3. `lib/launch/<harness>.sh`, with the functions of `lib/launch/codex.sh`.
   Its `lib/connectors_<harness>.py` (`main`, `doctor_line`; `Profile.connectors` names it) holds a window to
   its list of connectors.
4. A stand-in in `tests/sandbox/`, a section of `tests/sandbox/fresh_user.sh`, and a run with the real thing:
   `tests/sandbox.sh mac live <harness>`. The stand-in only behaves the way you already believed: every trap
   above was found by the real run, so teach the stand-in each one afterwards.
5. `tests/unit/test_agent_names.py` must still pass: it fails on an agent's id as a string constant in shared
   Python, or as a case arm or comparison in shared shell. Its `ALLOWED` lists the files that hold what only
   Claude Code has; a new agent adds nothing there.
6. The docs: this note, [Setup](../setup.md#running-repo-sessions-on-codex), the README's Status section,
   [Known limits](../limits.md) and `ROADMAP.md`.
