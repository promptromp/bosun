# Connectors: what each window may reach

Status: accepted 2026-11-09. A window bosun starts gets only the connectors its config names: none for a
repo window, the work ones I chose for the orchestrator. The orchestrator's gate covers what it still
missed, and an unattended install grants nothing nobody saw.

## Why

A window bosun starts is an agent that runs for hours, mostly unwatched. Until now it got every connector
the agent has on this Mac:

- **Every MCP server and connector.** A Claude Code window loaded my claude.ai connectors (Gmail, Google
  Drive, Calendar and the rest), every plugin's server and every server in `~/.claude.json`. A repo window
  only needs `git`, `gh` and its repo, and the orchestrator needs Slack and maybe a tracker or a wiki.
- **Repo windows have little in the way.** They run in auto mode, whose safety check is all that stands
  between a task and "forward this mail" or "share this file". The orchestrator may hand `review` and
  `fix-my-pr` tasks to a running window without asking.
- **The orchestrator's gate has holes.** `lib/guard.py` asks before an MCP tool whose name holds a write
  verb, but its verbs miss forward, share, trash, label, spam, respond and push. It splits names on `_`
  and `-` only, so a tracker's `editJiraIssue` or `createConfluencePage` is one word and goes through
  unasked, and the approvals' tracker families (`jira_`, `confluence_`) miss those names too. Chrome's
  clicking, typing, form filling and scripting have no verb at all.
- **The gate came from the installed bosun.** `orchestrator/settings.json` names
  `"$HOME/.local/bin/bosun" guard`, so `bosun chores --dry-run` from a checkout rehearses with another
  version's guard, or with none where bosun is not installed (a hook that is not found fails, and Claude
  Code goes on). A site that replaces the settings file (`BOSUN_ORCHESTRATOR_SETTINGS`) drops the gate
  unless it copies the hook.
- **Setup granted without asking.** The recommended setup ticks up to six repos I have used the agent in,
  and `get.sh --unattended` carries it out: those folders are trusted and get windows, and nobody saw
  which.

## Goals, non-goals, anti-goals

- **Goals:** none by default, a few by name, the same names for every window. A window that cannot find
  an allowed connector starts without it and says so, rather than starting with everything. `bosun
  doctor` shows what each window gets. Existing installs keep working after the update.
- **Non-goals:** Chrome. Repo windows use it to check pages, so it stays on through `--chrome` as before;
  tightening it is for later. Per-tool rules inside an allowed connector are the gate's job, not this one's.
  Codex's web search stays as Codex sets it.
- **Anti-goals:** an orchestrator that quietly stops seeing Slack after an update; a list of what to block,
  which a new connector would get past; anything that edits Claude Code's or Codex's own files.

## What the agents do (measured)

Claude Code 2.1.319, 2026-11-09, on a Mac signed in to claude.ai, with `claude -p --output-format
stream-json --verbose` and the session's `init` message (its `mcp_servers` and `tools`), every tool call
refused:

| Launch | What the session got |
|---|---|
| nothing extra | every local and plugin server; claude.ai connectors too in an interactive window (in `-p` only with `ENABLE_CLAUDEAI_MCP_SERVERS=true`) |
| `--settings '{"allowedMcpServers": []}'` | no server at all; Chrome still there with `--chrome` |
| `allowedMcpServers: [{"serverName": "<local name>"}]` | that server only |
| `allowedMcpServers: [{"serverUrl": "<connector URL or pattern>"}]` | that claude.ai connector only (an exact URL and `https://host/*` both work) |
| `allowedMcpServers: [{"serverCommand": [...]}]` | that stdio server only (a plugin's, by its command) |
| `allowedMcpServers: [{"serverName": "claude.ai Gmail"}]`, or a plugin's name | nothing: a name in an allow list may only hold letters, digits, `-` and `_`, and the connectors are reported "blocked by enterprise policy" |
| `deniedMcpServers: [{"serverName": "claude.ai Gmail"}]` | everything but Gmail; a deny beats an allow of the same server |
| `permissions.deny: ["mcp__*"]` | no MCP tools, but every server still connects |
| `--strict-mcp-config` | no server (Chrome stays with `--chrome`) |
| `disableClaudeAiConnectors: true` | no claude.ai connector; local and plugin servers stay |

A connector's URL is in no file Claude Code keeps, but `claude mcp list` prints every server with its URL
or command and its health, claude.ai connectors included (with `ENABLE_CLAUDEAI_MCP_SERVERS=true`), in
about 4 seconds; it connects to each to check it. Allow lists from every settings scope add up, so an
allow list in my own `~/.claude/settings.json` would widen a window's; deny lists add up too.

Codex 0.167.0, from its docs and source (to be confirmed in the VM): servers come from `[mcp_servers.<name>]`
in `~/.codex/config.toml`, a trusted project's `.codex/config.toml`, profiles and plugins; ChatGPT's
connectors are one built-in server, `codex_apps`, present with a ChatGPT login. A launch turns one server
off with `-c mcp_servers.<name>.enabled=false` (`-c mcp_servers={}` removes nothing: `-c` tables merge),
and the connectors with `--disable apps`. `codex mcp list --json` lists the configured and plugin servers
by name (not `codex_apps`), with each stdio server's environment in plain text, so a caller keeps the
names only. bosun already launches Codex with `--no-daemon`, without which a shared background server may
ignore the overrides.

## The design

### Saying what a window may reach

Two keys and a per-window override:

- `BOSUN_CONNECTORS`: the repo windows'. Default empty.
- `BOSUN_ORCHESTRATOR_CONNECTORS`: the orchestrator's. Default empty; `bosun init` writes what I choose.
- `connectors:` in a `sessions.yml` entry: that window's, instead of `BOSUN_CONNECTORS`.

A value is a comma-separated list. Each entry is a server's name as `/mcp` and `claude mcp list` show it
(`claude.ai Slack`, `plugin:slack:slack`, `jira`), or a URL pattern (`https://mcp.example.com/*`). Empty
means none; `all` means no limit, as before this version. Names are matched exactly, case included, after
trimming spaces.

### Claude Code

At launch, `build_agent_args` adds `allowedMcpServers` to the window's `--settings` (merged with the
rest, as the approvals and compaction hooks are), from the window's list:

| Entry | Becomes |
|---|---|
| a name of letters, digits, `-`, `_` | `{"serverName": <name>}` |
| `plugin:<plugin>:<server>` | that server's `url` (`serverUrl`) or `command` and `args` (`serverCommand`, with `${CLAUDE_PLUGIN_ROOT}` expanded to the plugin's folder), found by the server's name in the installed plugin's `.mcp.json`, which may hold several (`lib/claude_state.py` already reads it for the Slack watcher, one server only) |
| `claude.ai <Name>` | its URL from `claude mcp list`, run with `ENABLE_CLAUDEAI_MCP_SERVERS=true` in the window's folder, only when the list holds such a name |
| a URL pattern | `{"serverUrl": <pattern>}` |
| `all` | no `allowedMcpServers` key |

An empty list is `allowedMcpServers: []`. A plugin and a claude.ai connector at the same URL are one
server to Claude Code (it loads one of them), so allowing either lets that one in.

**When a name is not found.** An entry that resolves to nothing is left out, and the launch logs one line
naming it: the window starts with less, never with more. `claude mcp list` is read only through
`lib/claude_state.py`, like every other Claude Code internal; its parsing is pinned by a unit test on its
text, and a run that fails or times out (30 s) is told apart from a name it does not show.

**When the lookup fails.** At the morning wake the network may not be up yet, and an orchestrator started
without `claude.ai Slack` would miss Slack all day. So each lookup keeps what it resolved in the state
folder (`connectors.json`: each `claude.ai` name and its URL, a row in `STATE_FILES`); a lookup that fails
uses what was kept, and a name with nothing kept is left out, logged, and raised with `bosun alert` for
the orchestrator. A connector's URL rarely changes, so what was kept is almost always right.

**The fingerprint.** `refresh` and `bosun update` compare a window's launch fingerprint, a digest of its
arguments, with the one recorded at its launch. The resolved `allowedMcpServers` is part of the arguments,
so a list that changes, or a server that moves, counts as a launch change. `launch --fingerprint` never runs
the lookup (an update takes one for every window): it resolves from what was kept. A launch looks up once,
however many times it builds its arguments.

`bosun chores --dry-run` builds the orchestrator's list the same way, and runs with
`ENABLE_CLAUDEAI_MCP_SERVERS=true` when that list names a claude.ai connector or is `all`, so that the
rehearsal reaches what the window does. The headless calls that already limit their servers (learning,
the Slack watcher, the login check) do not change.

### Codex

At launch, `lib/launch/codex.sh` lists the servers (`codex mcp list --json`, names only, never logged
whole) and adds `-c mcp_servers.<name>.enabled=false` for each one the list does not name, the name
quoted as a TOML key. `--disable apps` unless the list is `all`. URL patterns and `claude.ai` names mean
nothing to Codex: doctor says so for a Codex window. A listing that fails keeps the window from starting,
and the launch says why: without it, bosun cannot tell which servers to switch off. The facts above
marked "to be confirmed" are checked in the VM before this lands, and `lib/codex_state.py`'s note and
`docs/design/harnesses.md` record them. One that does not hold gets another way that the VM shows works
(`--disable plugins` for a plugin's server, say); failing that, a Codex window with a server it cannot
switch off does not start.

### Existing installs

A new key needs nothing on an update: its default applies. That is right for `BOSUN_CONNECTORS` (repo
windows get none) and wrong for the orchestrator, whose chores would stop seeing Slack. So
`lib/migrations.py` gets a second kind of config change beside the renames: a key an update adds with
the value that keeps what an older install did (`BOSUN_ORCHESTRATOR_CONNECTORS="all"`). The update writes
it to `bosun.env` when the plan's `keys_added` holds that key and the file does not set it, with the same
backup a rename gets, and its summary says it in a line with what to do: name the connectors and drop `all`.
`bosun doctor` warns while the orchestrator's list is `all`. The summary also says, in a line of its own,
that repo windows now start without connectors (a repo's own `.mcp.json` servers included), and how to
give one back (`BOSUN_CONNECTORS`, or `connectors:` in its `sessions.yml` entry). Each added key with
something to say carries its line in the registry, beside the kept value.

### Seeing it

`bosun doctor` prints a line per window: the list, what it resolved to, and every allowed name it could
not find. It warns when my own Claude Code settings hold an `allowedMcpServers` (it widens every window's).

### Setup

- **Which connectors.** `bosun init` asks which connectors the orchestrator may use, from what `claude mcp
  list` shows (`lib/init_detect.py`, which already reads Claude Code through `lib/claude_state.py`). One
  screen like the repos', an answer `orchestrator_connectors` in `Answers` (its check, the key in
  `env_groups`, the table in `docs/design/init.md`). On a first run nothing is ticked: guessing which are
  work tools from their names would be a list of words to keep up, and wrong for someone. I tick what the
  orchestrator needs (Slack, a tracker). A re-run starts from what `bosun.env` says, as for repos: `all`
  is one line, ticked (a server ticked unticks it), and a name that `claude mcp list` no longer shows
  stays, marked as not found. Repo windows get none, and it says where to add some.
- **Which repos.** The recommendation ticks the repo I use most; the others are listed, unticked, up to
  the screen's limit. A re-run keeps what `sessions.yml` holds, as now.
- **Unattended.** `get.sh --unattended` on a first install runs `bosun init --recommended --unattended`:
  the recommended setup without repo windows or connectors, since nobody chose them, and a last line
  saying the orchestrator has no connectors and how to add them and windows (`bosun init`). The rest of
  the recommendation stays as it is. A re-run, or an update, is unchanged. A script that wants windows
  says which with `--answers`.

### The orchestrator's gate

- **Verbs.** `WRITE_VERBS` gains: accept, checkout, decline, fork, forward, grant, pay, purchase, push,
  react, reopen, respond, restore, revoke, share, spam, trash, unlabel, unpin, unstar, untrash. Words
  that are also nouns (request, comment, label, order, pin, mark, resolve) are left out: as verbs they
  would stop the orchestrator at a GitHub server's `get_pull_request`, and a rule for where in a name a
  word counts would be one more thing to keep up. The corpus fixtures' (`tests/fixtures/corpus/`) guard
  decisions are checked against the new list.
- **Names.** A tool's name splits on `_`, `-` and lower-to-upper case changes (`editJiraIssue`: edit,
  jira, issue). The approvals' tracker families match `jira` or `confluence` as a word of the name, not
  as its prefix.
- **Chrome.** `javascript_tool` and `form_input` get Claude Code's own `ask` rules in
  `orchestrator/settings.json`, the idiomatic place for a whole tool. `computer` needs its input looked
  at (a settings rule cannot ask by input for an MCP tool), so the guard lets it pass only with an action
  that changes nothing: a screenshot, scrolling, zooming, waiting or hovering. Any other action, one Chrome
  adds later included, asks. Reading the page and finding on it pass, and the verbs already cover
  uploads, shortcuts and new tabs. Going to a URL passes: a URL can carry data out, but so can `curl` and
  WebFetch, which the gate does not ask about either.
- **Why words and not the tools' own hints.** MCP tools can say they are read-only (`readOnlyHint`), but
  Claude Code uses that only to run calls side by side, and passes no hint to hooks or permission rules
  (its docs, 2026-11-09). If it ever does, the guard should go by it instead.
- **From the launch.** The guard's PreToolUse hook is added at launch (`guard_hook_settings` in
  `lib/common.sh`, naming `$BOSUN_CMD`), for the orchestrator window and `bosun chores --dry-run` alike,
  and leaves `orchestrator/settings.json`. A site's own settings file that still has the old hook runs
  the guard twice, which decides the same; the docs say to drop it.
- **Not taken: asking about any tool with no known verb.** It would have the orchestrator stop at a
  prompt for every read tool whose name has no read word, and an unattended orchestrator at a prompt is
  stalled until the watchdog sees it. The allow list is now what keeps the reach small; the verbs
  catch what is reached.

### Two roadmap bugs on the way

- **Business days by local date.** `ledger.business_days_since` counts UTC dates, so a message sent just
  after local midnight east of UTC is counted on the day before. It counts dates in the local zone (and
  takes the zone, for its tests).
- **`--help` before the config.** `bosun <cmd> --help` (or `-h`) prints the command's `# Usage:` header
  without reading the config, so it works with no config, a broken one, or one I would rather it did not
  read.

## Testing

| Layer | What it shows |
|---|---|
| Unit | Each entry's resolution, `claude mcp list`'s text parsed (placeholder servers), an unresolved name left out, a failed lookup falling back to what was kept (and the alert when nothing was), the Codex arguments and a failed listing refusing the start, the kept config value written once and only when the update adds the key, the init recommendations, its re-run and `--unattended`, the gate's new verbs (and the noun-like words left out), camel case and Chrome actions, business days in a zone east and west of UTC |
| Smoke | A launch's arguments for a window with none, a few, and `all` (the fake `claude` answers `mcp list`); `--fingerprint` running no lookup; the guard hook in the orchestrator's launch and in `chores --dry-run`; `--help` with no config; doctor's lines |
| VM, `mac live` | Real Claude Code and Codex windows with local stdio test servers (one allowed, one not, one a project's, one a plugin's named through `${CLAUDE_PLUGIN_ROOT}`): each window lists only what it may reach; the Codex facts above; an orchestrator that had everything keeps it after an update and doctor warns |
| This Mac | One interactive Claude Code window with an allow list naming one claude.ai connector, since only a real claude.ai login has them; it shows that one and none of the others |

## Later

- Chrome for repo windows: off unless a window's list names `chrome`.
- Codex's connectors one by one (`apps._default.enabled=false` and `apps.<id>.enabled=true`).
- The Slack watcher and the login check could take their server from the same resolution.
