# Design note: Antigravity windows, and agents that say what they can do

bosun runs repo windows on Claude Code or Codex. This adds Google's Antigravity CLI (`agy`) as a third, and
first makes adding an agent what [Agents](harnesses.md) says it is: a module per agent, with no agent's name
in shared code. Status: approved, 2026-11-10. Part 1 built (#36, plan `docs/plans/agent-profiles.md`). Part 2
amended after a run of the real `agy` in the macOS VM (2026-11-11), and approved the same day; plan
`docs/plans/antigravity.md`.

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

A run of the real `agy` 1.5.5 in the macOS VM (2026-11-11) settled most of what its docs left open. Each
item says what bosun does and what the run showed; what is still open is under Testing. Three findings change
the note's first version, each marked below: the status of an `agy` window also comes from its screen,
bosun's plugin is installed by the first launch that needs it, and its approval lets commands past the
sandbox.

- **Installing and checking.** `lib/harnesses/agy.sh`, a preflight profile. `agy --version` prints the bare
  version (`1.5.5`), checked against a minimum of 1.5.5, the version the run saw. The login is checked with no
  model call: the Keychain item whose service is `gemini`, looked up without reading it. `agy`'s own installer
  (`curl -fsSL https://antigravity.google/cli/install.sh | bash`) puts `agy` in `~/.local/bin` and does not add
  that to PATH; preflight says both. bosun installs nothing.
- **First-run screens.** A person runs `agy` once to sign in and finish its first-run screens; preflight checks
  `~/.gemini/antigravity-cli/cache/onboarding.json` for `onboardingComplete`.
- **The login reaches only a desktop session.** `agy` reads its login from the Keychain only when neither
  `SSH_CLIENT` nor `SSH_CONNECTION` is set (otherwise it looks for a login file it never wrote), and over ssh
  the Keychain is locked anyway. bosun's tmux server is started by launchd in the desktop session, where the
  run showed the login works. The launch unsets those two variables, in case a tmux server inherited them from
  an ssh session, and preflight says that a check run over ssh cannot see the login.
- **Starting.** `agy --prompt-interactive=<prompt> --settings <json> --log-file <log> --model <m> --effort <e>
  --remote-control`:
  - `auto` comes from `--settings '{"agentMode":"auto"}'`. `--mode auto` is dropped at startup with only a line
    in `agy`'s log ("auto mode is unavailable, falling back to default"), which would leave the window stopping
    at every command for a person. `--settings` holds for this session only and is never saved.
  - No `--add-dir`. `agy` runs the agent's commands in the first of its workspace folders in sorted order, so a
    state folder could take over from the repo. `auto`'s sandbox makes `.git` read-only and has no network, but
    a command it stops is retried past the sandbox, which `auto`'s reviewer passes (a commit landed so), and
    bosun's plugin lets exactly `git`, `gh` and `bosun` commands past it (below).
  - `AGY_CLI_DISABLE_AUTO_UPDATE=true`, which the run showed it honours. bosun records the version a window
    started on. The morning run updates `agy` once with `agy update` before it starts or refreshes the
    windows, and `bosun refresh` restarts an idle window that runs an older version than the one installed,
    as it does for Claude Code. My call: `agy` keeps itself current anyway, and this only moves when to a
    quiet moment.
  - `--remote-control`, as Claude Code windows get theirs. The run did not try it; the live run shows it.
  - The marker: `--log-file <bosun's log folder>/agy-<window>.log` among the flags before the first prompt,
    which `ps` shows and which keeps `agy` from adding a log of its own under `~/.gemini` at every start; bosun
    finds its own `agy` processes by it, and by nothing in a process's environment, which macOS 27.2 keeps from
    `ps` even for a user's own processes. `BOSUN_WINDOW=<window>` is in `agy`'s environment too, which reaches
    every hook (`agy` passes its environment through): that is for the hooks, never read from the process. The
    launch writes the window's tmux pane and server socket into the window's status file.
  - Model and effort: `agy models` lists names with the effort in them (`gemini-3.8-flash-low`). bosun passes
    the base name and `--effort`, and an effort above `high` as `high`: `xhigh` silently switched a model to
    another. `/model` is never pasted into a window, since `agy` saves it for every later session, as Claude
    Code does.
  - The first prompt is passed with `=`, so one that starts with `-` is no flag (seen).
- **Names and resuming.** bosun keeps `agy-sessions.json`: a window's name and folder to its conversation id,
  written by its `SessionStart` hook, which fires at a new conversation's first prompt and never on a resume.
  A launch resumes with `--conversation <id>` only if `~/.gemini/antigravity-cli/conversations/<id>.db` is
  there, and never uses `-c` ("the latest in this folder"), which two windows in one repo would share. A resuming launch also records that conversation in the window's status file, idle, since `agy` fires no hook until a prompt and a resume may have none: the window is a session at once. A new conversation is none until its first hook names it. `agy`
  starts a fresh conversation under an unknown id without failing, so the launch also tells bosun's hooks the
  id it resumed: the window's first `PreInvocation` stops the turn before its model call if `agy` reports
  another id, and bosun says so in the launch log and alerts.
- **Status.** From bosun's hooks: busy at `PreInvocation`, idle at `Stop`, and `shell` at a `Stop` with
  `fullyIdle` false (a background command still runs; when it ends, `agy` starts a turn by itself). Each
  window's status and when it changed are a file in bosun's state folder. Esc cancels a turn with no hook at
  all, so a busy status alone could stay busy for good. So while the hooks say busy, the adapter reads the
  window's screen (the launch recorded its tmux pane in the status file): `agy`'s idle footer over an empty box means
  the turn ended, and the window is idle from then; a dialog ("↑/↓ Navigate") means it waits for a person, and
  the window is `waiting`. `auto`'s reviewer does ask, for a push to `main`. Both are what the screen shows,
  not a guess, and only a busy status is checked, so an idle window costs nothing. (Changed: the first version
  had no `waiting` for `agy` windows.) What you said in the window is recorded at the first `PreInvocation`
  that carries a new `lastUserInput`; a turn `agy` starts by itself repeats the last one, which is not new.
- **bosun's hooks, as an `agy` plugin.** `--settings` refuses hooks, and otherwise `agy` reads them only from a
  workspace file (which would dirty the repo), the global `hooks.json` (the app's too) or a plugin. So bosun
  installs a plugin of its own, `bosun`, with `agy plugin install`, when a window first starts on `agy` (in
  `bosun launch`, under a lock) and again when the plugin's files change; never otherwise. It installs without
  asking anyone, and its hooks run without approval. Each hook is one line: without `BOSUN_HOOK_CMD` in its
  environment (any `agy` session bosun did not start) it exits at once and prints nothing; in a bosun window it
  runs bosun's hook. Printing nothing matters: `agy` takes an empty answer as no opinion, but `{}` or a failing
  hook as a refusal of the tool. The plugin is recorded in `lib/agent_changes.py`, and `bosun uninstall` runs
  `agy plugin uninstall` and removes what that leaves behind (`plugin_data/bosun/`, and `import_manifest.json`
  if the plugin made it). (Changed: the first version installed it at `bosun init` and `bosun update`; the
  first launch on `agy` covers both, and windows added later.)
- **What bosun's plugin approves.** Its `PreToolUse` hook answers `auto_approve` with `BypassSandbox` for a
  `run_command` whose every command is `git`, `gh` or `bosun` with a subcommand of that program's allow-list
  (`GIT`, `GH` and `BOSUN` in `lib/agy_state.py`: no global option before it, no `cd`, no prefix, no glob, run in
  the payload's own workspace, and per subcommand the options and repositories it may name), in bosun windows
  only, and nothing for every other command. (Changed: the first version took any `git`, `gh` or `bosun`
  command past `cd` and refused a list of dangerous options, which two reviews showed could not be completed.) `allow` is not enough: it still
  shows `agy`'s dialog, and the command still runs in the sandbox with no network. (Changed: the first version
  said "approves".) `agy`'s reviewer can still ask, and the window is then `waiting`. Nothing in `agy`'s
  global settings changes (my decision, 2026-11-10).
- **Trust.** Each launch passes the folder, as given, in `--settings` (`trustedWorkspaces`), so the trust
  screen never shows and `agy`'s settings are never written. `agy` compares the folder as given, not its
  resolved path, and a trusted folder does not cover the folders under it. `bosun trust` has nothing to do for
  `agy`.
- **Connectors.** `agy` takes no MCP limit per launch, and switching servers off in the shared config would
  race between windows and change the app's setup. So before a launch the adapter lists every server `agy`
  would start there: the global `~/.gemini/config/mcp_config.json`, each `.agents/mcp_config.json` from the
  repo up to its root, and the plugins' servers from `agy plugin list --json` (`<plugin>_<server>`); `agy mcp
  list` shows only the first. It refuses the window unless each is on its list (or the list is `all`), and a
  listing that fails refuses too. bosun's own plugin brings no server. `bosun doctor` names the servers that
  block a window. In practice a repo window with no connectors starts on `agy` only if `agy` has no MCP servers
  set up at all, the app's included.
- **Reading its screen.** Readers for `pane_input`, from the run's captures. The box is the line starting with
  `>` between two grey rules, and text in it blanks the footer's hint. A question is a dialog ("↑/↓ Navigate",
  whose footer also says "esc to cancel", so it is checked before busy), the trust screen ("Do you trust the
  contents of this project?") or the sign-in screen ("You are currently not signed in"). `bosun send` refuses a
  window at a question, as for Codex. One Enter submits a paste; a paste while busy is queued by `agy` and sent
  when the turn ends.
- **What shared code gains**, each asked of the profile, as Part 1 set out: an agent's own hooks (a field
  beside `TurnEnd`, which has Codex's shape), a step a launch takes once per machine (the plugin), the command
  that updates an agent (`agy update`), and the uninstall and backup tables' rows for an agent's state files,
  which now come from its profile instead of naming Codex's.
- **Going without.** No transcripts features, autocompact or per-task subagents, and no hook when a window
  exits; `bosun resolve` reads names from `agy-sessions.json`. [Agents](harnesses.md) gets an `agy` column in
  what windows lack.

## Risk

Google has said nothing about another program driving `agy` unattended, and a forum thread appeals an `agy`
account suspended under its terms. My call: go ahead. The setup guide says so where it explains how to run a
window on `agy`.

## Testing

- **Unit:** each profile and the lookup, the agent-name test, `lib/agy_state.py` against the payloads the VM
  run recorded, the session table, the resume guard, the status from hooks and screen, the connector refusal,
  the plugin's approval, and the screen readers against the run's captures.
- **A stand-in** (`tests/sandbox/agy-stand-in`), taught each fact the run found, as `codex-stand-in` was taught
  Codex's; a smoke section and a first-day section in `tests/sandbox/fresh_user.sh`.
- **The real thing:** `tests/sandbox.sh mac live agy`, on a personal account's free quota. The login is
  `agy`'s own: I sign in inside the throwaway VM, whose clone is deleted after the run, and no image is saved
  with a login in it. The discovery run (2026-11-11) already showed: a prompt that starts with `-`; a bracketed
  paste and Enter; trust as given and resolved; that plugin hooks need no approval; `--conversation` after a
  restart and with an unknown id; the Keychain in a tmux started by launchd; the reviewer and the sandbox for
  `git push` and the network; what a `Stop` with `fullyIdle` false means; the onboarding key and
  `agy --version`'s format. The live run proves bosun's side of each, and what the discovery did not see:
  - a window started, resumed and refreshed by bosun on `agy`, end to end;
  - Esc in a busy window read as idle, and a reviewer's dialog read as `waiting`;
  - `--remote-control` starting;
  - `SSH_AUTH_SOCK`, which launchd sets in a desktop session, not hiding the login;
  - a `gh` and a `git push` command approved by the plugin, and every other command left alone;
  - a hook slower than `agy`'s timeout;
  - the plugin's install and uninstall leaving nothing else behind;
  - the connector refusal with a server set up, and with none;
  - the morning `agy update` with automatic updates off.

## What stops the second pull request

Part 1 merges on its own. Part 2 merges only if the VM run shows each anti-goal above holds: a window
resumes its own conversation (or is stopped before its first prompt), its connectors hold, its status is
never guessed (the hooks and, while they say busy, the screen), and nothing of the user's Antigravity setup changes but what bosun records. A finding that
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
