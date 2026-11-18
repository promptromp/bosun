# Updating and uninstalling

How to move bosun to a newer version while your agents keep working, how to try a branch, how to go back to the
version before, and how to take bosun off your Mac again. An install that [`get.sh`](../get.sh) made updates with `bosun update`; a
checkout of your own updates with `git pull`.

## Updating

```bash
bosun update --check     # is there a newer release? Changes nothing
bosun update             # shows the plan, and asks
bosun update --dry-run   # only shows the plan
bosun update --yes       # goes ahead without asking
bosun update --back      # back to the version the last update came from
bosun update --branch x  # follow the branch x from now on, and update to its head
```

Running the install line again does the same as `bosun update`. The menu bar app's Check for Updates... runs the
same check, and `bosun update --skip <version>` stops a release being offered again.

An update moves bosun to the newest release (or, on the `main` [channel](#update-channels), to the head of its
branch) while your agents keep working:

1. It gets the new version into a folder of its own, beside the one you run.
2. That version shows its plan: the commits, the migrations your data needs, the prompts that changed, what each
   window gets, the background jobs to reload, and anything that stops it. In a terminal it asks first; without
   one, it stops at the plan.
3. It rehearses the migrations on a copy of your data.
4. It holds bosun's own background work (the check-in loop, the watchdog, learning, the morning job) and pauses
   writes for a moment. It backs up your ledger, ontology and settings (`backups/` in the state folder, the last
   3 kept), migrates them, switches `~/.local/share/bosun` to the new version and checks each store. Writes that
   arrive meanwhile wait, and land on the new version.
5. If any of that fails, it puts everything back as it was and says what failed.

Before it hands over to the new version, the version you run checks that the new one would not read your data in an
older format, or under older rules, than your data is in (a revert on `main`, or a branch from before a migration),
and refuses if it would: going back past a migration is [`bosun update --back`](#going-back)'s.

How it works: [design note](design/update.md).

### What happens to the windows

| The window | What the update does |
|---|---|
| Nothing it was started with changed (most updates) | Leaves it alone: its next `bosun` command runs the new version |
| Only the orchestrator's prompts changed (`policy.md`, the task files) | Asks the orchestrator to re-read them once it is idle |
| Something it was started with changed (its settings, hooks, plugin) | Restarts it on the new version once it is idle, in the same conversation. If only background work runs there, it first asks the window to list that work, and passes the list back after the restart, as a restart ends it |
| Busy, or someone typing in it | Waits until it is done |
| Waiting for your answer (a permission prompt or a question) | Never touches it until you answer. If only such windows are left, the update stops waiting and leaves them to the check-in loop (below) |
| One bosun did not start (a shell or a Claude you opened in its tmux session, outside `sessions.yml`) | Leaves it as it is, and says so in the plan |

Each window gets a prompt to check for anything it missed and carry on, and answers with its status. The update
waits for every answer and says which windows it is waiting for. Ctrl-C stops that wait, and the check-in loop
brings the rest along once each is idle. A window opened while you answer, or during the update, is brought
along too; one opened after it runs the new version from its next `bosun` command, and keeps what it was started
with.

A window waiting for your answer never ends that wait by itself, so once every window the update still waits for
is one of those, the update stops waiting on its own and says so, for example:

```text
Stopped waiting: every window still waited for waits for your answer.
  orchestrator waits for your answer at its prompt; once you answer, the check-in loop restarts it for the update
```

Answer it when you get to it: the check-in loop (or `bosun update advance`, if no loop runs) brings it along once
its turn is over. Meanwhile no update is running: the menu bar app offers Check for Updates again and says what
the window still gets ("orchestrator restarts for the update once you answer it"), and the next update is not
held up by it. It takes such a window over (its plan lists it under "Still owed from the last update"; one whose
launch the newest version no longer changes is listed as left alone instead), finishes
the last update's summary, which says the window was carried into the next update, and its own summary says what
came of it.

A window whose restart did not start (its launch refused, or its agent exited at once) is not waited for: the
summary says it did not start, why, and what to do (fix that, then `bosun refresh <window>`). A window whose
launch the new version would refuse is not restarted at all, even one the last update still owed: it keeps
running (one whose agent had exited stays as it is), and the plan and the summary say it would not start on the
new version, why, and what to do.

With a plugin list (the orchestrator has one by default: none of yours), your own Claude Code plugins count among
what a window is started with: installing or enabling one (`claude plugin install`, `~/.claude/settings.json`)
changes what its launch switches off, so the next update restarts the orchestrator, even an update that changes
nothing else.

Tasks already handed to a repo window keep the text they had. The summary lists them, so you can send a
`bosun delegate --update` where a changed rule matters.

### When it is done

It ends with a summary: the commits, the migrations, the config it changed and its new keys, anything you have
to change by hand in your own files, the prompts that changed, the jobs reloaded, what came of each window,
anything that fails now that did not before, and where the backup is. The summary is saved as `summary.md` in the
backup's folder, and its last line is how to go back (`bosun update --back`, [Going back](#going-back)). `bosun doctor`
lists what the last update left for you to do.

An update cut short (Ctrl-C, a crash, a power cut) is put back, or finished, by the next `bosun update`.

**Your config.** A new key needs nothing: its default applies. A key that was renamed or removed is changed in
`bosun.env` for you, and so is a key whose new default would change what your install did: the update writes it
with the value that keeps your install as it was, under a comment saying why. Either way it first keeps the file
as `bosun.env.pre-update-<time>`, and the plan shows the change before you say yes.

<details class="note" markdown>
<summary>The keys an update has written so far</summary>

- **`BOSUN_ORCHESTRATOR_CONNECTORS="all"`:** an install from before
  [connectors](configuration.md#connectors-what-each-window-may-reach) keeps every connector for its orchestrator, and
  `bosun doctor` warns until you name the ones it needs. Repo windows start without connectors from then on, a
  repo's own `.mcp.json` servers included: give one back with `BOSUN_CONNECTORS`, or `connectors:` in its
  `sessions.yml` entry. A checkout you update with `git pull` gets no such change: set the key yourself, or the
  orchestrator starts with none.
- **`BOSUN_ORCHESTRATOR_PLUGINS="all"`:** an install from before
  [plugin lists](configuration.md#plugins-which-of-yours-each-window-loads) keeps loading every Claude Code plugin of
  yours in its orchestrator, as before. A plugin's hook or skill can stop its unattended runs at a question, so name
  the ones it needs in `BOSUN_ORCHESTRATOR_PLUGINS`, or set it empty for none; `bosun doctor` says what it loads.
  A checkout you update with `git pull` gets no such change: set the key yourself, or the orchestrator loads none
  of your plugins. An install with no `bosun.env` to write it to gets none either, and the update's plan and
  summary say so.
- **`BOSUN_UPDATE_CHANNEL="main"`:** an install that was updating before releases existed goes on following the
  head of `main`, and the plan and summary say how to follow the releases instead.
- **`BOSUN_MENU_APP`, `BOSUN_CONSOLE_ALWAYS` and `BOSUN_START_AT_LOGIN`, all `"0"`:** an install from before the
  menu bar app stays as it was: no app, no console job, no start at login.

A Claude Code window that a settings file would give servers its list does not name
([Connectors](configuration.md#connectors-what-each-window-may-reach)) would not start on the new version, so the
update leaves it running; the plan and the summary say why, as does `bosun doctor`. Name those servers in its
list, or remove them from that file, then `bosun refresh <window>`.

</details>

### Update channels

| `BOSUN_UPDATE_CHANNEL` | Updates to |
|---|---|
| `releases` (the default for a new install) | The newest dated release, a tag `v<YYYY.MM.DD>` |
| `main` | The head of the branch the install follows (`main`, or the `BOSUN_BRANCH` it was installed from) |

The Settings window's "Updates from" sets it, and so does `bosun settings set BOSUN_UPDATE_CHANNEL=main`.

**Trying a branch** (a pull request's, before it merges):

```bash
bosun update --branch try-x            # follow try-x from now on, and update to its head
bosun update --branch try-x --dry-run  # only show the plan; nothing is written
bosun update --branch main             # follow main again
```

`--branch` checks first that the origin has the branch, and changes nothing if it has not. Then it sets what the
install follows (`bosun.branch` in `~/.local/share/bosun-versions/bosun.git`) and, if the channel is not `main` yet,
sets `BOSUN_UPDATE_CHANNEL=main` (through `bosun settings set`, which keeps a copy of `bosun.env`), and updates to the
branch's head as any update does: it shows the plan and asks. Say no, and the install still follows the branch, at the
version it has; the next `bosun update` goes to the branch's head. Once the branch has merged and its origin deleted
it, `bosun update` cannot fetch it and says to run `bosun update --branch main`. A branch whose code is older than
your data (one made before a migration your data went through) is refused, pointing at `--back`. To follow the
releases again: `bosun settings set BOSUN_UPDATE_CHANNEL=releases`.

A local branch you have not pushed is the install line's: `curl ... | BOSUN_REPO=<path to your checkout>
BOSUN_BRANCH=<branch> sh`. That points `bosun.git`'s origin at your checkout until you run the install line again
with `BOSUN_REPO` set back (the address the README's install line uses).

### The first update of an older install

An install from before versions side by side is a checkout in `~/.local/share/bosun`, and its bosun has no
`bosun update`. Running the install line again converts it, once:

1. The new version's `bosun update` shows its plan and asks.
2. It waits for a quiet moment: every window idle with nothing running, and nobody typing in the session for
   `BOSUN_ACTIVE_CLIENT_MINUTES`. It says what it waits for, and Ctrl-C there changes nothing.
3. It swaps the checkout for the link in one step, keeps the checkout as
   `~/.local/share/bosun-versions/legacy-<its commit>`, and restarts each of bosun's own windows (the
   orchestrator, and those in `sessions.yml`) on the new version.

If anything fails, the old checkout runs again, as it was, with the background work it stopped started again;
after a crash, run the install line again, which does that. It will not convert a checkout with anything of yours
in it: changes, a stash, commits not pushed, another worktree, a file git ignores. Take that out first. The update
after it deletes the kept checkout, unless something of yours is in it by then. How the conversion works, step by
step: [design/update.md](design/update.md#getsh-on-an-existing-install).

## Going back

```bash
bosun update --back            # shows the plan, and asks
bosun update --back --dry-run  # only shows the plan
bosun update --back --yes      # goes back without asking
```

It goes back to the version the last finished update came from: one step, never further. A second `--back` is
refused; `bosun update` goes forward again. The version you run now does it (it knows what its update changed), and
fetches nothing; if the older version's folder is gone, it adds it again from `bosun.git`.

What you wrote since the update stays wherever the older version can read it. Each part is decided on its own:

| Part | Kept | Restored from the update's backup |
|---|---|---|
| The ledger, the ontology | When both versions read it alike (the same format and rules version) | When its format or rules version differs: a migration ran, or the rules changed. What was written to it since is not in the backup; going back takes a backup of its own first, which keeps it |
| `bosun.env` | When the update did not write it | The update's own changes are taken back (a renamed key gets its old name back, a value it added goes), where they still read as it wrote them; a line you changed since stays as you made it, and the plan says so. The file is kept first as `bosun.env.pre-back-<time>` |
| Everything else (the events queue, signals, your own files) | Always | Never |

The plan says which applies, and what restoring would lose (how many changes were written since, when the last was,
and the ledger items they touch), before it asks. With nothing to restore it only switches back, and writes never
pause. The windows come along as for an update ([What happens to the windows](#what-happens-to-the-windows)), and it
ends with a summary ("bosun went back: ..."), saved as `back-summary.md` beside its backup (or, when it took none,
beside the backup of the update it went back over). The version it left is recorded as skipped, so `bosun update
--check` and the menu bar app no longer offer it; a newer one is offered as usual, and `bosun update` still goes to it
if you ask, its plan saying `--back` left it.

`--back --dry-run` changes nothing, even when the older version's folder is gone: it reads what it can of that
version from `bosun.git` and says what it cannot tell until going back adds the folder again. After going back to a
version from before `--back`, that version's `bosun uninstall --purge` does not know `bosun.env.pre-back-<time>` and
leaves the config folder with it: delete it by hand.

It is refused when no update has finished, after a back, or when the version before is the checkout an older `get.sh`
made (go back by hand, below). A restore needs the update's backup: never past a `kg forget`, which deletes every
update backup there is, since restoring one would bring the person back. When nothing needs restoring, a back after a
forget still works.

<details class="note" markdown>
<summary>Going back by hand, when bosun update itself is what broke</summary>

You need the version the update came from and the backup it took, both named in its summary:

1. **Stop what writes:** `bosun quit --stop-now` (or `bosun stop`), and unload the watchdog, learning and console
   jobs: `launchctl bootout gui/$(id -u)/com.bosun.morning.watchdog`, and the same for `.learn` and `.console` if
   you have them (with your `BOSUN_LAUNCHD_LABEL` if you set one).
2. **Find the older version's folder** in `~/.local/share/bosun-versions`. An update keeps the one it came from
   until the next update. After the first update, the version before it is the checkout it kept,
   `legacy-<its first 12 characters>`. If the folder is gone, add it again from its commit: the backup's
   `manifest.json` names the whole commit as `from` (the summary's first line shows only 7 characters, which
   `git --git-dir ~/.local/share/bosun-versions/bosun.git rev-parse <those 7>` turns into the whole one), then
   run `git --git-dir ~/.local/share/bosun-versions/bosun.git worktree add --detach
   ~/.local/share/bosun-versions/<its first 12 characters> <commit>`.
3. **Put the backup back:** each entry in its `state/` folder over the one in `~/.local/state/bosun`, and each in
   its `config/` folder over the one in `~/.config/bosun`, replacing the newer one whole (its `manifest.json` lists
   every file). Anything written since the update is not in it.
4. **Point the link at the older version** (`ln -sfn ~/.local/share/bosun-versions/<folder>
   ~/.local/share/bosun`), run its `~/.local/share/bosun/install.sh` to load its jobs again, and `bosun start`.

`bosun update` would take you forward again, to the newest release, or on the `main` channel to the head of the
branch (from the checkout kept from before versions, which has no `bosun update`, the install line does).

</details>

## Uninstall

```bash
bosun uninstall             # says what goes and what stays, then asks
bosun uninstall --dry-run   # only says
```

Run it from a terminal outside bosun's windows, since it stops them. It changes nothing until you have answered
every question, and Return always takes the safe answer: at "Are you sure?" it means no, and at "Keep your
ledger?" it means keep. Ctrl-C at any question leaves everything as it was.

- **What goes:** your windows (stopped), bosun's background jobs, the menu bar app, the repeating wake bosun set,
  the folder trust bosun gave your agents, the memory notes [learning](learning.md) wrote, the empty worktrees
  folders it made beside your repositories, the `bosun` command and the installed versions.
- **What it asks about:** your ledger and learning history, and your ontology. Return keeps them.
- **What stays:** your config in `~/.config/bosun` (`--purge` deletes it too), your repos, branches and pull
  requests, your conversations, your own notes and agent settings, and anything it does not know is bosun's.

If something could not be removed it says what to run, and running it again finishes the job. Install again with
the same line, and bosun picks up what you kept. How it decides: [design note](design/uninstall.md).

<details class="note" markdown>
<summary>Everything it removes, and what it leaves</summary>

| What | What happens |
|---|---|
| Your windows | Stopped, so any work in them stops. It says which are running first, and waits up to 15 seconds for what they started to end; anything still running then (a background command a Codex window started, say) it names and leaves running |
| The launchd jobs (morning, watchdog, learning, console, `.now`, the login job, and the app's Open at Login job) | Unloaded and deleted, before the windows stop, so the watchdog cannot bring them back |
| The menu bar app, `~/Applications/bosun.app` | Asked to quit if it runs, then deleted. The `~/Applications` folder stays |
| The repeating wake | Cancelled with `sudo pmset repeat cancel`, if it is the one bosun set. `pmset` can only cancel every repeating event at once, so another app's wake stays, and it says so |
| Folder trust | Taken back for the folders bosun marked, in Claude Code and Codex. A folder you had accepted yourself stays trusted. It goes by bosun's record of what it changed (`agent-changes.json`); trust from an install older than `bosun uninstall` is not on it, and stays |
| The plugin bosun installed into `agy` | Uninstalled with `agy plugin uninstall`, with what that leaves behind (`plugin_data/bosun/`). Your own `agy` setup is not touched |
| Memory notes from [`bosun learn`](learning.md#learning) | Deleted, with their line in `MEMORY.md`. Your own notes stay |
| The worktrees folders bosun made beside your repositories (`exporter.worktrees` beside `exporter`) | Removed while empty. One with a worktree in it stays, and it says where, since a worktree may hold work that is not committed or pushed. A folder of that name that was there before bosun stays |
| `~/.local/bin/bosun` and bosun's working files | Deleted |
| What `get.sh` installed: `~/.local/share/bosun-versions` and the link `~/.local/share/bosun` | Deleted, last, unless anything of yours is in them: changes, a stash, commits not pushed, a worktree elsewhere, a file git ignores. Then they all stay, link included |
| A checkout of your own you run bosun from | Stays. One that an older `get.sh` made, from before versions side by side, is deleted last, unless anything of yours is in it |
| Your ledger and learning history, your ontology | It asks, and Return keeps them |
| The backups `bosun update` took | Deleted when either the ledger or the ontology is, as each backup holds both. Kept only when you keep both |
| `~/.config/bosun`: your config, the backups `bosun init` and `bosun update` made of it | Stay, and it says where. `--purge` deletes them too, but leaves `forbidden-terms.txt`, which is for working on bosun, not running it |
| Anything else in those folders | Stays, and it names it. It deletes only what it knows is bosun's, and a folder you pointed `BOSUN_STATE_DIR` or `BOSUN_LOG_DIR` at that also holds other files stays whole |

- `/bosun:chores` and bosun's hooks need no removing: bosun passes them to each window it starts, so neither
  Claude Code nor Codex has them installed.
- `replica.json`, this Mac's id in both journals, goes with the ledger, so keeping the ontology alone gives its
  next write a new id.
- For scripts, `--yes` asks nothing and keeps your records; `--delete-ledger`, `--delete-knowledge` and `--purge`
  say otherwise.
- If you deleted bosun's folders by hand, the jobs and the wake are still there. Run the install line again, then
  `bosun uninstall`.
- A first update of an older install that you declined, or that failed before the switch, leaves
  `~/.local/share/bosun-versions` beside the checkout, which the checkout's own `bosun uninstall` does not know
  about. Delete it by hand once nothing of yours is in it: `rm -rf ~/.local/share/bosun-versions`.

</details>
