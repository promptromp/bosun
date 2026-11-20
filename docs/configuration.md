# Configuration

How to change what bosun runs once it is set up: its config files, the model and effort of each window, the
connectors each window may reach, repo windows on Codex or Antigravity, and the logins bosun keeps working. A
reference of where bosun keeps things ends the page.

The easy way to change the windows, connectors, agents and models is to run `bosun init` again: it starts from
your config and shows its plan before it changes anything. The schedule and starting at login are also in the menu
bar app's [Settings](menu-bar-app.md#settings), and `bosun settings set` changes them from a terminal. This page is
for everything else, and for what each setting means.

## Your config files

Your configuration lives in `~/.config/bosun/` (`BOSUN_CONFIG_DIR` moves it):

| File | What it holds |
|---|---|
| `bosun.env` | Settings: the schedule, the agents and their flags, models, connectors, and which parts of bosun are on. Every key and its default is in [`examples/generic/bosun.env`](../examples/generic/bosun.env) (on an install, `~/.local/share/bosun/examples/generic/bosun.env`). |
| `sessions.yml` | One entry per repo window: `name` and `dir`, and optionally `harness` (`claude`, `codex` or `agy`), `model`, `effort`, `connectors` and `plugins` for that window, and on Claude Code `autocompact`. The orchestrator is set up in `bosun.env`, not here. |
| `setup.sh` | What the morning run does before the windows start: `setup_steps()`, calling `ensure_claude_login` ([Claude Code login](#claude-code-login)), `ensure_aws_sso <profile>` ([AWS SSO](#aws-sso)), or `run_step "<description>" <command>...` for anything else. |

bosun reads `bosun.env` each time a command runs, so an edit applies at once, also inside running windows. A
setting you give in the environment (`BOSUN_...=value bosun ...`) wins over the file. `bosun doctor` warns about a
key bosun does not read, with the one you most likely meant, and about a word where a number goes. A file bash
cannot read is not read at all: commands stop and say which line ([If you get stuck](setup.md#if-you-get-stuck)).

Your own policy and task rules for the orchestrator also go in this folder: see
[Orchestrator](orchestrator.md#delegated-tasks-and-folder-trust).

### Model and effort

Every window bosun starts gets the model and effort your config names, so what it costs does not depend on what
you last picked by hand. (Claude Code's `/model` and `/effort` save your choice as the default for every future
session, which is how sessions drift to Opus on high effort.)

| Windows | Model | Effort |
|---|---|---|
| Repo windows on Claude Code | `BOSUN_MODEL` (`opus`) | `BOSUN_EFFORT` (`medium`) |
| Repo windows on Codex | `BOSUN_CODEX_MODEL` (empty: Codex's own default) | `BOSUN_EFFORT`, passed as `-c model_reasoning_effort` |
| Repo windows on Antigravity | `BOSUN_AGY_MODEL` (empty: its own default) | `BOSUN_EFFORT`, passed as `--effort` (above `high` as `high`) |
| The orchestrator | `BOSUN_ORCHESTRATOR_MODEL` (`opus`) | `BOSUN_ORCHESTRATOR_EFFORT` (`medium`) |

A window's entry in `sessions.yml` can set its own `model:` and `effort:`. Each delegated task also sets an
effort: reviews and investigations `medium`, fixes on your pull requests and new work the window's own. A window
opened for the task starts at it; a window already running runs the task in a subagent at it, so its own setting
is untouched (Codex, which has no such instruction yet, runs it at the window's own). The orchestrator asks for
`--effort high` only when you ask for more depth.

`bosun send --list` shows what each window was launched with, `bosun refresh` restarts idle windows whose launch
settings no longer match your config, and `bosun doctor` shows the configured values and your saved default.

<details class="note" markdown>
<summary>How they are passed to Claude Code</summary>

The model goes in as `--model`, and the effort as a session-level setting (`--settings '{"effortLevel":...}'`)
rather than `--effort`, so `/effort` inside a session can still change it for that session. Empty values leave
Claude Code's own default.

</details>

### Connectors: what each window may reach

A window bosun starts reaches only the MCP servers and connectors its list names; the rest are off for it.

| Key | Whose list | Default |
|---|---|---|
| `BOSUN_CONNECTORS` | every repo window | empty: none |
| `connectors:` in a `sessions.yml` entry | that window, instead of `BOSUN_CONNECTORS` | (the key's) |
| `BOSUN_ORCHESTRATOR_CONNECTORS` | the orchestrator, and `bosun chores --dry-run` | empty: none; `bosun init` writes what you tick |

A list is comma-separated, or a YAML list in `sessions.yml` (`[]` is none). Each entry is a server's name as
`claude mcp list` (and `/mcp`) shows it, or a URL pattern. `all` means every one, and stands alone:

```sh
BOSUN_ORCHESTRATOR_CONNECTORS="claude.ai Slack, plugin:acme-tracker:tracker"
BOSUN_CONNECTORS="acme-local, https://mcp.example.com/*"
```

```yaml
sessions:
  - name: api
    dir: ~/Development/api
    connectors: [acme-local]
```

The list is read when the window starts, so a change applies at its next start. A name bosun cannot find is left
out, never let in as more: the launch logs a line, and for the orchestrator it also alerts you. On Claude Code,
Chrome is not a connector: it stays on through `--chrome`.

`bosun doctor` has a line for each window: what it reaches, what it only allows by name or by pattern, and each
name it cannot find. It warns while the orchestrator's list is `all`, or while `bosun.env` does not set it at all.

**Settings files that would widen a list.** Claude Code adds the `allowedMcpServers` of your own
`~/.claude/settings.json`, and of the window folder's `.claude/settings.json` and `.claude/settings.local.json`, to
the window's list. So a window with a list (anything but `all`) does not start while one of those files allows a
server its list does not name. The launch says which file and which entries: name them in the window's list, use
`all`, or remove them from that file.

<details class="note" markdown>
<summary>How a list is applied on Claude Code</summary>

At launch bosun turns the list into Claude Code's `allowedMcpServers`, in the window's `--settings`, in place of
any a site settings file names (with `all`, the site file's applies). A server you added yourself goes by its
name, a plugin's by its URL or command, and a claude.ai connector by the URL `claude mcp list` shows for it.
bosun runs that listing (a few seconds) only when it has to, keeps what it found in `connectors.json` in the
state folder, and uses that if a later listing fails (at a morning wake, before the network is up). A name you
added yourself is matched exactly, so a typo allows nothing. `bosun doctor` looks nothing up, so a claude.ai
connector no start has looked up yet shows as not found until the window's next start.
More: [design/connectors.md](design/connectors.md#claude-code).

</details>

<details class="note" markdown>
<summary>How a list is applied on Codex and Antigravity</summary>

On Codex, a list names servers as `codex mcp list` shows them, and bosun switches off the servers it does not
name. Codex can switch a plugin's server off only by switching off every plugin, so to keep any plugin's server,
the list must name every plugin's server: a list that names some but not all of them loses all of them, and every
plugin's skills too. Servers that `BOSUN_CODEX_FLAGS` adds (a `--profile`, a `-c mcp_servers...`) are not switched
off, so add servers in `config.toml` instead. Unless the list is `all`, bosun also passes `--disable apps`, which
is documented to switch off ChatGPT's connectors. Codex's own local servers (`codex_worktrees`) stay on, as Chrome
does on Claude Code. For a Codex window, `bosun doctor` says which names in its list mean nothing to Codex (a URL
pattern, a claude.ai connector). More: [design/connectors.md](design/connectors.md#codex).

An Antigravity window is refused instead of limited: see [below](#running-repo-sessions-on-antigravity-a-preview).

</details>

### Plugins: which of yours each window loads

A Claude Code window loads the plugins you enabled (`enabledPlugins` in your `~/.claude/settings.json`, and in the
window folder's `.claude/settings.json` and `.claude/settings.local.json`): their hooks, skills, commands and
agents. A plugin's hook or skill that asks a question can stop an unattended window, so each window has a list of
the plugins it keeps:

| Key | Whose list | Default |
|---|---|---|
| `BOSUN_PLUGINS` | every repo window | `all`: every one, as without bosun |
| `plugins:` in a `sessions.yml` entry | that window, instead of `BOSUN_PLUGINS` | (the key's) |
| `BOSUN_ORCHESTRATOR_PLUGINS` | the orchestrator, and `bosun chores --dry-run` | empty: none of yours |

A list is written as for connectors (comma-separated, or a YAML list, `[]` for none, `all` alone). Each entry is a
plugin's id as `enabledPlugins` writes it (`acme-helper@acme-market`), or its bare name (`acme-helper`), which
matches it from any marketplace:

```sh
BOSUN_ORCHESTRATOR_PLUGINS="acme-helper@acme-market"
```

```yaml
sessions:
  - name: api
    dir: ~/Development/api
    plugins: []
```

bosun's own orchestrator plugin always loads. A plugin one of whose servers the window's connectors name
(`plugin:acme-slack:slack`) stays on, so a connector never breaks for it, and so does one your orchestrator
settings file (`BOSUN_ORCHESTRATOR_SETTINGS`) enables. The launch switches the others off with a
`false` for each in its `--settings`, which outranks your own, the project's and the local settings, but not managed
settings: a plugin those force on loads anyway, and the launch logs it. A name no plugin has is left out and logged, as is a settings file that does not parse; an
`installed_plugins.json` that does not parse keeps a window with a list from starting.
On Codex a list other than `all` switches every plugin off (Codex cannot keep some and not others); an Antigravity
window with one does not start. `bosun doctor` has a line for each window: all of yours, or none or the ones it
keeps, how many it switches off, and what it keeps for its connectors. An install from before this setting keeps
`all` for the orchestrator (`bosun update` writes it to `bosun.env`). Every headless call bosun makes,
[learning](learning.md)'s analysis, the [Slack watcher](events.md#slack-watcher)'s call, the login check and
[`bosun alert`](watchdog.md)'s Slack DM, loads none of your plugins, whatever these lists say (the watcher and the
alert keep the Slack plugin their Slack server may come from). More:
[design/connectors.md](design/connectors.md#plugins).

## Running repo sessions on Codex

[Codex](https://developers.openai.com/codex/cli) can run the repo windows. The orchestrator stays on Claude Code,
so you need both, and it hands tasks to a Codex window as it does to any other: the same tasks, the same reports
to the ledger.

1. Install Codex 0.166.0 or newer, and log in:

    ```bash
    brew install codex
    codex login
    ```

2. Choose it. `bosun init` asks once Codex is installed and logged in (and recommends it if you use it more than
   Claude Code). Or in `bosun.env`, for every repo window:

    ```bash
    BOSUN_HARNESS="codex"
    BOSUN_CODEX_MODEL=""       # optional; empty: Codex's own default
    ```

    Or for one window, in `sessions.yml`:

    ```yaml
    sessions:
      - name: my-service
        dir: ~/Development/my-service
        harness: codex
    ```

3. Run `bosun trust <dir>` for any folder Codex has not seen, then `bosun doctor`.

**What it may do.** Windows start with `BOSUN_CODEX_FLAGS`, by default `--no-daemon --approve-for-me`: Codex's
sandbox, with an automatic reviewer that decides when a command may go past it, and no prompts. bosun also gives
the sandbox the repo's `.git`, its own state folder and the network, or nothing could be committed, reported or
pushed, and the folder beside the repo its worktrees go in (`<repo>.worktrees`,
[Permissions](permissions.md#what-each-window-can-reach)). Keep `--no-daemon`: bosun needs each session in its own
process. Each window reaches only the MCP servers its list names ([Connectors](#connectors-what-each-window-may-reach)),
but servers these flags add (a `--profile`, a `-c mcp_servers...`) stay on: add servers in `config.toml` instead.

**What differs from a Claude Code window**, among them no Remote Control from your phone, a revoked login that is
not reported, and no Impact stats or learning yet:
[what windows do not have yet](design/harnesses.md#what-windows-do-not-have-yet).

## Running repo sessions on Antigravity (a preview)

Google's Antigravity CLI (`agy`) can run repo windows, as Codex can. The orchestrator stays on Claude Code. This
is a preview: the windows are built and tested against a stand-in, in the smoke suite and in the Docker sandbox,
but the live run on the real `agy` (`tests/sandbox.sh mac live agy`, which needs a person to sign in to `agy`
inside the throwaway VM) has not passed yet. Expect rough edges, and read [the risk](#the-risk) first.

Set it up once:

1. Install `agy` 1.5.5 or newer with Google's installer, `curl -fsSL https://antigravity.google/cli/install.sh |
   bash`. It puts `agy` in `~/.local/bin` and does not add that folder to your PATH, so do that yourself.
2. Sign in in the desktop session, not over ssh: `agy` reads its login from the Keychain only there.
3. Run `agy` once and go through its first-run screens, then leave it.
4. Choose it for the windows you want: `harness: agy` on a window in `sessions.yml`, or `BOSUN_HARNESS=agy` for all
   of them (or `BOSUN_HARNESS=agy` after the pipe of the install line, as for Codex). `bosun preflight` and
   `bosun doctor` check the version, the login and the first-run screens.

What an `agy` window does:

- It runs in `agy`'s `auto` mode, set for that session only, with the repo folder passed as trusted at each start.
  bosun never saves a setting in `agy`. `BOSUN_AGY_FLAGS`, `BOSUN_AGY_MODEL` and `BOSUN_AGY_BIN` are its keys, like
  Codex's.
- When the first `agy` window starts, bosun installs a small plugin of its own into `agy`. It is recorded, and
  `bosun uninstall` takes it back. In a bosun window the plugin lets exactly allow-listed `git`, `gh` and `bosun`
  commands run past `agy`'s sandbox, so that pushes and `gh` work; every other command is left to `agy`. Since
  `agy` 1.5.13 such an approval is final (`agy`'s own reviewer no longer looks at it), so the list leaves out what
  a repo session never does: a push that forces, deletes, names no branch or writes the default branch, and
  `gh pr merge` or `close`. In your own `agy` sessions the plugin does nothing.
- A window is refused when its folder, or one up to its repo's root, has `agy` hooks of its own: an
  `.agents/hooks.json`, a plugin with hooks in `.agents/plugins/`, or an `.agents/plugins.json`. `agy` would run
  them outside its sandbox at every start.
- Repo git hooks do not run in an `agy` window: an approved commit would otherwise run the repo's own hook code
  outside the sandbox. Pre-commit-style checks therefore run in CI only.
- Its status comes from `agy`'s hooks and, while a window is busy, from its screen (Esc, and `agy`'s dialogs).
- A window is refused, saying so, when `agy` would start an MCP server that its connectors list does not name
  ([Connectors](#connectors-what-each-window-may-reach)). A repo window with no connectors starts only if `agy` has
  no MCP servers set up at all, the Antigravity app's included. `bosun doctor` names the servers that block a
  window.
- `bosun morning` updates `agy` once before the windows start, and `bosun refresh` restarts an idle window that
  runs an older version.

**What it goes without:** transcript features (Impact stats and learning), autocompact, per-task subagents, and a
hook when a window exits; see [what windows go without](design/harnesses.md#what-windows-do-not-have-yet).

### The risk

Google has said nothing about another program driving `agy` unattended, and a forum thread appeals an `agy`
account suspended under its terms. Use an account you can afford to lose access to.

## Logins

bosun runs unattended, so the logins it uses must keep working without you: Claude Code's, and any AWS SSO
profiles your repos need. The morning run checks them before the windows start.

### Claude Code login

A Claude Code login can expire or be revoked at any time. Every window then answers every prompt with an error
("Please run /login") and nothing else. bosun notices, and tells you:

- **In the morning,** `ensure_claude_login` in your `setup.sh` (which `bosun init` puts first) checks that the login
  works. It cannot log in by itself (`/login` is interactive): it posts a notification and fails the step, so the
  watchdog's `morning-failed` alert names it. Without an answer either way (no network) it only warns.
- **During the day,** when the orchestrator's latest replies are all login errors, the check-in loop logs
  `<window> needs /login` once and sends it no more check-ins or events (events wait) until the login works again.
- **The watchdog's** `login-expired` check alerts once per outage, for the login and for every Claude window, and
  again if it comes back after recovering. Like every check, it alerts only inside your hours.
- **`bosun status`** shows the login and each window's state, and **`bosun doctor`** fails while Claude Code is
  logged out, the last check failed, or a running window gets only login errors.

To fix it, run `/login` in any Claude window, then talk to it, or run `claude` in a terminal. bosun picks up that
the login works again from the next normal reply, or from its next login check. The login is the Mac's, so that sign
counts for every window.

**No credit.** A window whose latest replies are all "Credit balance too low" (`billing_error`) is `no-credit` in
`bosun status`, fails `bosun doctor`, and alerts through the watchdog's `no-credit` check, once per window and row of
such replies. Its login works, so check-ins and events still go to it, as on a usage limit. The login check request
works without credit, so only a normal reply ends it: its own, or one in any window after the last such reply of
every window, since the credit is the organization's and every window bosun starts uses the same key.
[With an API key](#with-an-api-key-instead-of-a-login) says why it happens, and what to do.

<details class="note" markdown>
<summary>How bosun tells that a login is gone</summary>

It reads, best signal first (`lib/login.py`; the Claude Code formats are in `lib/claude_state.py`):

1. **The transcript's error fields.** A failed request is written as a synthetic reply flagged
   `isApiErrorMessage`, with `error` (`authentication_failed`, `rate_limit`, `server_error`, ...), often the HTTP
   status (`apiErrorStatus`: 401, 429, 529) and a finer `apiError` (`no_response`, `usage_limit_reached`). These
   map to auth, usage-limit, credit (`billing_error`: no credit for an API key), rate-limit, overloaded, network or
   other; only auth counts as a login error. Exact and free, but known only once a prompt has failed.
2. **`claude auth status --json`** (`loggedIn`, `authMethod`): free, but it reads the stored credentials only and
   makes no request (it answers the same with the network cut off), so a token the server revoked still shows
   `loggedIn: true`. It does catch a missing login (`loggedIn: false`, exit 1).
3. **A login check request:** one tiny headless request (`claude -p`, a small model, no tools, settings, MCP
   servers, plugins of yours or transcript, but the API key helper below if you use one; about $0.002). An
   `installed_plugins.json` that does not parse makes no request: the check says it could not tell, and why.
   `ensure_claude_login` makes
   one each morning; the check-in loop makes one while the orchestrator needs a login or the last check failed, at
   most every 30 minutes, to learn when the login works again (a `/login` itself is not written to any
   transcript). A failure before any request (for example settings that could not load without network) gives no
   structured answer and counts as unknown, never as a login error.
4. **Fallback:** a loose reading of the error text (phrases like "please run /login", "token revoked", "401
   Unauthorized"), only for an error reply without structured fields, or, when a window has no transcript yet, its
   last lines. Verdicts from it say "pattern fallback".

After each check-in or event delivery, the loop waits up to 15 seconds for the reply, so a delivery that just
failed is logged as such. While it holds the orchestrator's check-ins and events back for a login, the watchdog's
`events` check does not call the dispatcher stalled. An outage overnight or on a weekend alerts when your hours
begin, together with the morning run's own failure.

**One login for every window.** The login is the Mac's, so once a login check works, or any window gets a normal
reply, after the last login error of every window, `bosun status`, the watchdog and the console count all the windows
as working again, an idle one too, which gets no prompt to show it, and the check-in loop lets the orchestrator go
(while it holds it back, it reads the other windows too). A window that fails again after such a sign starts a new
row: its "since" is its first error after the sign, so the watchdog's `no-credit` alerts again. This assumes that
every window bosun starts uses the same credentials, as it does unless a repository's own `.claude/settings.json`
sets an API key in its `env` or an `apiKeyHelper`: then that window's replies say nothing about the others' login or
credit, and can clear them wrongly until their next prompt fails again.

</details>

### With an API key instead of a login

Claude Code can sign in with an Anthropic API key, billed per request on your Console account, instead of a
claude.ai login. For bosun, give it the key through the `apiKeyHelper` setting in `~/.claude/settings.json`: a
command that prints the key, which every Claude bosun starts reads, in a window, under launchd or headless. From
the Keychain, so the key is in no file, the command is `security find-generic-password -s anthropic-api-key -w`,
after storing the key once with `security add-generic-password -U -a "$USER" -s anthropic-api-key -w` (it asks
for it).

`claude auth status` then says `api_key_helper`, and bosun's login check and the Slack watcher, which load no
settings files of yours, still get that one setting. Not `ANTHROPIC_API_KEY`: an interactive Claude asks once
whether to use a key from the environment, which a window bosun starts would wait at, and launchd's jobs do not
have your shell's environment. With a key, Claude Code has no Remote Control (it needs a claude.ai login), and the
models bosun uses (`BOSUN_MODEL`, `BOSUN_ORCHESTRATOR_MODEL`, `BOSUN_LEARN_MODEL`) cost per request: smaller ones
cost less.

The key's organization needs purchased credit. The API credits a subscription adds to the Console (the
"Promotional credits" of a Max, Ultra or Team plan) cover the Claude API and the Agent SDK but not Claude
Code, so the windows reply "Credit balance too low · Add funds" while `claude -p` and the API still work. The
watchdog's `no-credit` check alerts on such a window, and `bosun status` and `bosun doctor` name it. Two ways out:

1. **Add credit** in the Console's billing
   ([which credits cover what](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)), then
   tell the window to carry on (`bosun send <window> ...`).
2. **Or use your subscription.** An API key (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `apiKeyHelper`)
   outranks a subscription login
   ([authentication precedence](https://code.claude.com/docs/en/authentication#authentication-precedence)), and a
   running window keeps the key it started with, so `/login` alone changes nothing. Remove the API key
   (`ANTHROPIC_API_KEY` in your shell profile, or `apiKeyHelper` in `~/.claude/settings.json`), restart the window
   (`bosun refresh <window>`), then `/login` in it.

### AWS SSO

`ensure_aws_sso <profile>` in your `setup.sh` logs a profile in each morning, so the windows' AWS tools work all
day. It skips the login when the profile's credentials work and its SSO session can still issue new ones. When it
needs a login, it runs `aws sso login`, which opens your browser; if no approval comes, it alerts you and the rest
of the morning goes on ([Known limits](limits.md) has the details).

Point your profiles at a shared `[sso-session]` block: the CLI then renews them silently for as long as the
Identity Center session lasts, and one browser login covers every profile in that session. Profiles that use the
older per-profile `sso_start_url` and `sso_region` keys get no refresh token, so they need the browser every time:

```ini
[profile my-prod-profile]
sso_session = my-sso        # instead of sso_start_url / sso_region
sso_account_id = ...
sso_role_name = admin
region = us-east-1
```

How long the Identity Center session lasts is set by your organization; if it does not survive the night, expect
a browser approval most mornings.

<details class="note" markdown>
<summary>Why it checks the SSO session, not only the credentials</summary>

The AWS CLI caches each profile's role credentials (in `~/.aws/cli/cache`, typically for 12 hours), and that
cache can outlive the Identity Center session: a check that reuses it passes in the morning, and the credentials
then run out a few hours later. So when a profile's `[sso-session]` access token has expired, `ensure_aws_sso`
first drops the cached SSO role credentials. The check then passes only if the session can still issue new ones
(the CLI refreshes the token on the way), and otherwise the login happens at the morning run rather than
mid-morning. Profiles without an `sso_session` are checked as before.

</details>

## Reference

### Where bosun keeps things

| Folder | What is in it |
|---|---|
| `~/.config/bosun/` (`BOSUN_CONFIG_DIR`) | Your [config files](#your-config-files), and the [ontology](knowledge.md) in `knowledge/` |
| `~/.local/state/bosun/` (`BOSUN_STATE_DIR`) | The ledger, logs and bosun's working files (below) |
| `~/.local/state/bosun/logs/` (`BOSUN_LOG_DIR`) | A file per command and day |
| `~/.local/share/bosun` and `~/.local/share/bosun-versions` | The installed versions, and the link to the one in use |

If you move the config or state folder, keep both on a disk that supports hard links, as the Mac's own disk does:
bosun makes `replica.json` and the ontology's `secret.key` with one, so that a file is never half-made or made
twice. On a disk without them (exFAT, some network shares), writes stop and say so.

<details class="note" markdown>
<summary>Every file in the state folder</summary>

| File or folder | What it is |
|---|---|
| `ledger.json`, `ledger.md`, `ledger.json.bak` | The [ledger](ledger.md), its readable view, and the version before the last write. Copying an older `ledger.json` back does not undo a write: the journal brings it forward again ([How it is kept](ledger.md#how-it-is-kept)) |
| `journal/ledger/` | Every change to the ledger, which `ledger.json` is rebuilt from: `<replica>.jsonl` for this Mac's changes, `local.jsonl` for what never leaves it, such as your own words ([Sync and sharing](design/sync.md)) |
| `replica.json` | This Mac's id in the ledger's and the ontology's journals |
| `logs/` | A file per command and day, `<command>-YYYY-MM-DD.log`, with what it said on its screen (`morning-…` has the whole morning run, `launch-…` each window's start), and what launchd captured from each job (`launchd.{out,err}`, `watchdog.launchd.{out,err}`, ...). A day's file stops at 2 MB; each morning run deletes files older than 30 days and cuts launchd's files past 1 MB to their last 256 KB |
| `watchdog.json` | The watchdog's memory of what it already alerted |
| `quit.json`, `stopped-<session>`, `keep-awake-<session>.pid` and `.json`, `login-reload`, `console/wanted`, `update-skip.json` | What starting and stopping leave: a `bosun quit`, a `bosun stop` today, the keep-awake and until when, a reload of the jobs, the console job's switch, and a release `bosun update --skip` passed over |
| `loaded-<morning\|learn\|watchdog>` | When bosun's morning, learning and watchdog jobs were last loaded (a fresh load, or one that moves the job's time or days). The watchdog owes no run that was due before then, and counts a watchdog job that never ran from it |
| `started-<session>` | When `bosun start` last made the session. On a day no morning run is owed, as on install day, the watchdog watches the session from then |
| `login.json` | The last Claude Code login check |
| `codex-sessions.json` | Which Codex session each window's name stands for |
| `connectors.json` | What `claude mcp list` last showed for the connectors a list names, for a launch whose lookup fails |
| `agent-changes.json` (and its locks) | What bosun changed in the agents' own files and the worktrees folders it made, so that `bosun uninstall` takes back only that |
| `signals/` | What Claude Code's hooks reported per window ([Signals](design/signals.md)) |
| `conversations/`, `prompts/`, `launches/` | A repo window's current conversation after a fresh start, each start's first prompt, and what each window was last launched with (so an update can tell which need a restart) |
| `console/token`, `console/running.lock` | The console's access token, and a lock held while a console runs |
| `update.json`, `update.lock`, `update/` | The last `bosun update`'s record, its lock, and the windows' notes to it |
| `backups/<time>-<from>-<to>/` | What `bosun update` backed up before it changed anything, with its `manifest.json` and `summary.md`, the last 3 kept |
| `impact/`, `alerts/`, `events/`, `slack-watch/`, `learn/` | The Impact snapshot, the last Slack alert, the event queue, the Slack watcher's runs, and the learning runs (kept 14 days), each with its feature on |
| `dry-runs/<timestamp>-<n>/` | Chores rehearsals (`<n>` numbers those that start in the same second) |

</details>

### Background jobs

bosun's background work runs as launchd jobs in your login session, named after `BOSUN_LAUNCHD_LABEL`
(`com.bosun.morning` by default). `install.sh`, `bosun init` and `bosun update` load them, and
[`bosun uninstall`](updating.md#uninstall) removes them.

| Job | What it runs, and when |
|---|---|
| `<label>` | `bosun morning`, at your start time on your days |
| `<label>.watchdog` | The [watchdog](watchdog.md), every 5 minutes |
| `<label>.learn` | The daily [learning](learning.md) run, with `BOSUN_LEARN_ENABLED=1` |
| `<label>.console` | The [console](console.md), with `BOSUN_CONSOLE_ALWAYS=1`, from `bosun start` until `bosun quit` |
| `<label>.login` | `bosun morning --at-login` when you log in ([Starting at login](menu-bar-app.md#starting-at-login)), with `BOSUN_START_AT_LOGIN=1` |
| `<label>.now` | No schedule: `bosun morning --now --background` (the menu bar app's Start) starts it, so that launchd, not the caller, is the parent of what the morning run starts |
| `<label>.app` | Opens the menu bar app at login, while Open at Login is on (`bosun app login on`) |

Reloading the jobs (an update, `install.sh`) makes launchd run the login job too; a marker the reload leaves
(`login-reload` in the state folder) tells that run it is not a login.
