# Design note: Antigravity windows, and agents that say what they can do

bosun runs repo windows on Claude Code or Codex. This adds Google's Antigravity CLI (`agy`) as a third, and
first makes adding an agent what [Agents](harnesses.md) says it is: a module per agent, with no agent's name
in shared code. Status: proposed, 2026-11-10. Part 1 built (pull request to follow); Part 2 not started.

## Why

- **I asked for Antigravity** next to Claude Code and Codex, on my personal Google account. Since Google I/O
  (2026-05-19) `agy` is a terminal coding agent of its own (v1.5.3 on 2026-11-07), and it took over from Gemini
  CLI for personal accounts on 2026-06-18. Gemini CLI now serves only paid API keys, Vertex AI and enterprise
  accounts, so it is not the one to add.
- **It tests the framework.** Codex arrived in one pull request of about 80 files. A third agent should cost
  an adapter and a real run, not another sweep through shared code. Today about eight places in shared code
  still name `claude` or `codex` (below), against the design note's own rule.
- **What else is next** (research, 2026-11-10): OpenCode, then GitHub Copilot CLI. Both are on the
  roadmap; neither is in this change.

## Goals

1. **Agents describe themselves.** Each agent module declares what it is and what it can do; shared code
   asks that, never an agent's name.
2. **A repo window can run on `agy`**, unattended in its `auto` mode, as a Codex window can: started and
   resumed under its bosun name, its status known, its folder trusted, its connectors held to its list, and
   brought along by `bosun update`.
3. **Nothing changes for Claude Code and Codex windows.** The first part is a refactor with no change in
   behaviour, and its pull request says so.

## Non-goals

- The orchestrator stays on Claude Code. It stands on `--plugin-dir`, `--append-system-prompt`, a settings
  file per launch and blocking hooks; `agy` takes plugins, rules and hooks only globally, and has no system
  prompt flag.
- The headless jobs (the login check, learning, the Slack watcher) stay on Claude Code. They need a budget
  cap, tool and MCP isolation and a cost in the result, which `agy -p` does not give.
- Transcript-based features on `agy` windows: Impact stats, daily learning, the watchdog's `context-size` and
  `spend-limit`. `agy`'s transcript store is undocumented (SQLite since 1.0.4); this leaves them out, as for
  Codex.
- Business accounts. `auto` is offered to personal accounts only; a business account would need
  `--dangerously-skip-permissions`, which this does not offer.
- OpenCode, Copilot CLI, and a generic ACP adapter.

## Anti-goals

- **Never more than a window's list.** An `agy` window that cannot be held to its connectors does not start.
- **Never a blank conversation under a window's name.** `agy --conversation <id>` with an id it does not know
  starts a fresh one without failing, so bosun checks the conversation is there before it resumes.
- **Nothing of the user's Antigravity setup changed behind their back.** `agy`'s MCP servers, hooks, plugins
  and rules live in `~/.gemini/config/`, which the Antigravity app and IDE read too. bosun writes there only
  what it records (`lib/agent_changes.py`) and `bosun uninstall` takes back.
- **No status guessed.** A signal an agent lacks reads as unknown, never as idle.

## Part 1: agents that say what they can do

Each agent module (`lib/claude_state.py`, `lib/codex_state.py`, `lib/agy_state.py`) gains a `PROFILE`, one
frozen dataclass declared next to its `HARNESS`:

- **Its config keys.** The binary, the window flags and the default model: `BOSUN_CLAUDE_BIN`,
  `BOSUN_CLAUDE_FLAGS`, `BOSUN_MODEL` for Claude Code (their names stay); `BOSUN_CODEX_BIN`,
  `BOSUN_CODEX_FLAGS`, `BOSUN_CODEX_MODEL` for Codex; `BOSUN_AGY_BIN`, `BOSUN_AGY_FLAGS`, `BOSUN_AGY_MODEL` for
  `agy`. Their defaults stay in `lib/common.sh`, as every key's do.
- **What it can do**, each a value shared code reads instead of a name:
  - how a window's name maps to a session: the agent names it (Claude Code), or bosun keeps a table (Codex,
    `agy`);
  - which statuses it can report: `idle` and `busy` always; `waiting` and `shell` only where it has a signal;
  - Remote Control, autocompact, and per-task subagents (the `SUBAGENT_HARNESS` check today);
  - how a window's connectors are held: by the agent at launch (Claude Code's allow list, Codex's switches),
    or by refusing to start a window the agent cannot hold to its list (`agy`);
  - the hook command, if any, that bosun installs or names at launch, and the state files it keeps (for
    `STATE_FILES` and the update's `IN_FLIGHT`).

The shell reads a profile through one lookup per command (`agent_state profile`), not a Python start per
question. The places that name an agent today then read the profile:

| Today | Becomes |
|---|---|
| `harness_bin`, `harness_default_model` (`lib/agents.sh`): a `case` on the name | the profile's keys |
| `agent_pid_starting`, and the `BOSUN_*_BIN` passed by name to `agent_state` and preflight | every agent's binary, from `AGENTS` |
| the `default_model` hash in `window_specs`'s Ruby (`lib/common.sh`) | the profile's model key, handed in |
| `init_plan`'s `CLAUDE`/`CODEX` check and `OTHER_AGENTS` | `AGENTS` |
| `connectors.py`'s `claude` and `codex` subcommands, `DOCTOR_LINES` | one subcommand that asks the window's agent module |
| `SUBAGENT_HARNESS` (`lib/delegate.py`) | the profile's subagents |
| `pane_input`'s `question` and `rejected`, applied to every window | each agent's own screen readers |
| `codex-turn-end` in `lib/hook.py` and `libexec/bosun-hook` | `bosun hook turn-end --harness <h>`, the module's handler (`codex-turn-end` stays as an alias for windows started before) |

What only Claude Code has (its login checks, the headless jobs, the transcripts Impact and learning read)
stays named, as the note already allows, since no interface describes it yet.

A unit test fails when shared code compares a harness id string, so the rule holds after this change.

## Part 2: `agy` windows

Each item below says what bosun does, and what the real run in the macOS VM has to prove, since `agy`'s
docs leave it open.

- **Installing and checking.** `lib/harnesses/agy.sh`, a preflight profile: `agy --version` against a minimum
  version (1.4.7, when every hook began to carry `lastUserInput`; the VM run confirms it), and the login with
  no model call: the Keychain item `agy` keeps after a browser sign-in. `agy`'s own installer
  (`curl -fsSL https://antigravity.google/cli/install.sh | bash`) is what preflight tells a person to run; bosun
  installs nothing.
- **First-run screens.** A person runs `agy` once (theme, terms), as for Claude Code; preflight checks the
  settings key that marks it done, which the VM run finds.
- **Starting.** `agy --prompt-interactive=<prompt> --mode auto --model <m> --effort <e>`, plus:
  - `--add-dir` for bosun's state and log folders and the repo's git common dir, since `auto`'s sandbox keeps
    `.git` read-only, as Codex's did. `auto`'s reviewer decides on a command that must leave the sandbox (the
    network, for `git push` and `gh`). If the VM run shows it stops them, bosun's plugin (below) approves
    exactly `git`, `gh` and `bosun` commands with `PreToolUse`, inside bosun's windows only; nothing in `agy`'s
    global settings changes (my decision, 2026-11-10);
  - `AGY_CLI_DISABLE_AUTO_UPDATE=true`, so `agy` never updates itself under a running window (it releases
    several times a week). The version a window started on is kept with its launch (`lib/launch_record.py`).
    The morning run updates it instead, once, with `agy update` before it starts or
    refreshes the windows, and `bosun refresh` restarts an idle window whose version no longer matches the
    one installed; `bosun doctor` reports an `agy` older than its newest release. My call: `agy` keeps
    itself current anyway, and this only moves when to a quiet moment;
  - `--remote-control`, as Claude Code windows get theirs;
  - a marker that the process is bosun's and which window it is, which `launch_settings` reads back from
    `ps`. `agy` has no flag to name a session, so the marker is an environment variable and a documented flag
    such as `--log-file <state>/agy/<window>.log`; the VM run settles which works.
  - Effort above `high` is passed as `high`. `/model` is never pasted into a window, since `agy` saves it for
    every later session, as Claude Code does.
- **Names and resuming.** bosun keeps `agy-sessions.json`: a window's name and folder to its conversation id,
  written by the `SessionStart` hook. A launch resumes with `--conversation <id>` only after checking that the
  conversation is there (where `agy` keeps it is for the VM run to find), and never uses `-c` ("the latest in
  this folder"), which two windows in one repo would share. If nothing on disk says so (`agy` may ask its
  servers), the check is afterwards: the window's `SessionStart` must report the id it was resumed with, or
  bosun stops it at once, before its first prompt, and says so.
- **Status.** Busy on `PreInvocation`, idle on `Stop`, from bosun's hooks; `shell` only if the VM run shows a
  `Stop` with `fullyIdle` false means what `shell` means. No `waiting`: no hook reports it, and the statusline
  feed that does would replace the footer in my own `agy` sessions (my decision, 2026-11-10: leave it). So, as
  on Codex, the watchdog's `waiting` never fires for an `agy` window, which rarely asks in `auto`, and
  `bosun send` reads the screen first. The record is built as `codex_state.build_record` builds Codex's, keyed
  by pid. What you said in the window is recorded at the first `PreInvocation` that carries a new
  `lastUserInput`.
- **bosun's hooks, as an `agy` plugin.** `agy` reads hooks only from a workspace file (which would dirty the
  repo), the global `hooks.json` (the app's too) or a plugin. So bosun installs a plugin of its own with
  `agy plugin install`, once a window runs on `agy` (`bosun init`, and `bosun update` when one starts to),
  never otherwise. Its hooks return at once outside a bosun window (the marker above), so my own `agy`
  sessions only start a process that ends at once. It is recorded in `lib/agent_changes.py`; `bosun
  uninstall` takes it back. If `agy` asks a person to approve a new hook, as Codex did, preflight says so
  before any window starts.
- **Trust.** `trustedWorkspaces` in `agy`'s settings, written by `bosun trust` for both the folder as given and
  its resolved path.
- **Connectors.** `agy` takes no MCP limit per launch, and switching servers off in the shared config would
  race between windows and change the app's setup. So before a launch the adapter lists every server `agy`
  would start there (global, the workspace's, plugins'), and refuses the window unless each is on its list
  (or the list is `all`); a listing that fails refuses too, as on Codex. bosun's own plugin brings no server.
  `bosun doctor` names the servers that block a window. In practice a repo window with no connectors starts
  on `agy` only if `agy` has no MCP servers set up at all, the app's included.
- **Reading its screen.** An input-box reader and a question reader for `pane_input`, from real captures: idle
  shows "? for shortcuts", busy "esc to cancel", a dialog "↑/↓ Navigate". `bosun send` refuses a window at a
  question, as for Codex.
- **Going without.** No transcripts features, autocompact or per-task subagents; `bosun resolve` reads names
  from `agy-sessions.json`. [Agents](harnesses.md) gets an `agy` column in what windows lack.

## Risk

Google has said nothing about another program driving `agy` unattended, and a forum thread appeals an `agy`
account suspended under its terms. My call: go ahead. The setup guide says so where it explains how to run a
window on `agy`.

## Testing

- **Unit:** each profile and the lookup, the agent-name test, `lib/agy_state.py` against recorded hook
  payloads, the session table, the connector refusal, and the screen readers against
  real captures.
- **A stand-in** (`tests/sandbox/agy-stand-in`), teaching it each fact the real run finds, as
  `codex-stand-in` was taught Codex's; a smoke section and a first-day section in `tests/sandbox/fresh_user.sh`.
- **The real thing:** `tests/sandbox.sh mac live agy`, about a dollar a run. The login is `agy`'s own: I sign
  in inside the throwaway VM, whose clone is deleted after the run, and no image is saved with a login in it.
  It must prove:
  - a prompt that starts with `-`;
  - a bracketed paste and Enter;
  - trust in both path forms;
  - whether plugin hooks wait for approval;
  - `--conversation` after a restart, and with an unknown id;
  - the Keychain when tmux was started by launchd;
  - whether `auto`'s reviewer lets `git push` and `gh` through, and the plugin's approval if not;
  - what a `Stop` with `fullyIdle` false means;
  - the onboarding key, and `agy --version`'s format;
  - the connector refusal with a server set up, and none.

## What stops the second pull request

Part 1 merges on its own. Part 2 merges only if the VM run shows each anti-goal above holds: a window
resumes its own conversation (or is stopped before its first prompt), its connectors hold, its status is
never guessed, and nothing of the user's Antigravity setup changes but what bosun records. A finding that
breaks one stops Part 2 until it has a fix. These stop it too, since unattended windows cannot work without
them: the marker reaches bosun's hooks, and the Keychain login works in a tmux started by launchd. Anything
else the run finds that `agy` lacks ships as a documented gap, in [Agents](harnesses.md) and
[Known limits](../limits.md).

## Two pull requests

1. **Agents that say what they can do:** Part 1 alone, behaviour unchanged, all suites green as they are.
2. **`agy` windows:** Part 2, the stand-in, the live run, and the docs: this note, [Agents](harnesses.md),
   [Setup](../setup.md), the README's Status section, [Known limits](../limits.md), and `ROADMAP.md` (with
   OpenCode and Copilot CLI as the next agents).

## Left to the builder

- The profile's exact fields and names, and how the shell caches it.
- Where `agy-sessions.json`'s writes lock, and its format (Codex's is the model).
- The wording of every refusal, in the style of the existing ones.
