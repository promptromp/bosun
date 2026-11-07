# Uninstalling: `bosun uninstall`

Bosun reaches further into a machine than most tools: scheduled jobs, a wake that needs `sudo`, entries in other
programs' config, a running tmux server. Taking all that off should not need a list from the docs, and should
never cost anyone something they wanted to keep. What it does for you is in the [setup guide](../setup.md#uninstall);
this note is why.

## What others do

| Tool | How | Worth taking | Worth avoiding |
|---|---|---|---|
| oh-my-zsh | A script: `[y/N]`, then removes | Moves your `.zshrc` aside rather than deleting it; defaults to no | Changes your shell back before it asks |
| Homebrew | `uninstall.sh`, `--dry-run`, `--force` | Lists first; at the end, lists what it could not delete | |
| rustup | `self uninstall`, `-y` | One command, from the tool itself | |
| mise | `implode`, `--dry-run`, `--yes` | Config stays unless `--config`; says what it leaves to you | A question per path |
| Determinate's Nix installer | A receipt of the install plan, played in reverse | Undoes what it recorded doing, and not what it finds | |
| k3s | Install writes the uninstaller | It is always there, matching what was installed | |
| apt | `remove` keeps config, `purge` does not | The two words, and what they promise | |
| uv, Ollama | Steps in the docs | | Everything is left to you |

## The rules

- **Nothing changes until every question is answered.** It says what goes and what stays, asks, and only
  then starts. Ctrl-C at any question leaves everything as it was. Unlike oh-my-zsh, nothing happens before the
  question.
- **The safe answer takes no effort.** Enter is no at "Are you sure?" and keep at "Keep your ledger?". `--yes`
  also keeps your records: a script that wants them gone has to say so. A kept file is an `rm` away, and a deleted
  ledger cannot be got back.
- **It only deletes what it knows is bosun's, from a folder that is bosun's.** `STATE_FILES`, `CONFIG_FILES` and
  `LOG_FILES` in `lib/uninstall_plan.py` list what bosun writes, and what each one is. A name that is not listed
  stays, and the plan names it. `BOSUN_STATE_DIR` can be set to anything, and `logs` or `*.log` is a name other
  programs use too, so a folder is only bosun's own when its name says bosun or everything in it is listed;
  any other folder is left whole, and the plan says why. A folder that holds your home folder or bosun's
  checkout is never touched, compared as folders and not as names, since on macOS `/users/me` is `/Users/me`.
  The line it prints to delete what stays is only offered for a folder of bosun's own, and every path it
  prints is quoted for the shell.
- **It undoes what bosun did, not what it finds.** A folder you trusted yourself looks exactly like one bosun
  marked, so `lib/agent_changes.py` keeps a record of the changes, as the Nix installer's receipt does, and only a
  change is recorded: bosun marking a folder that was already trusted did nothing. The same goes for memory notes:
  one you named `bosun-something` is still yours. Losing the record only means that those changes stay.
- **Your work is not bosun's to delete.** The checkout goes only if `get.sh` made it (it sets `bosun.managed` in
  the checkout's git config) and git shows nothing in it of yours: no uncommitted changes, no stash, no commit
  that is not on a remote (on any branch, or none), no other worktree, and nothing git ignores but the caches
  running bosun makes (a `.env`, a key, a state folder someone pointed inside it). A git command that fails
  counts as work, not as clean, and git is never let look in a folder above the one it checks. A checkout you
  cloned to work on stays.
- **Versions side by side go whole, or not at all.** What `get.sh` installs now is `~/.local/share/bosun-versions`
  (`bosun.git`, which it marks with `bosun.managed`, and a folder per version, each a worktree of it), the
  checkout from before versions that the first update kept there (`legacy-*`), and the link
  `~/.local/share/bosun`. Each is checked as a checkout is: every version's files and a commit made in it,
  the repository for a stash, a branch or tag on no remote (but the branch it follows, which is bosun's) or a
  worktree outside the folder, anything in the folder that bosun does not put there, and the old checkout on
  its own (its mark, and its work). Anything of yours in any of them keeps all of it, the link too, and the
  plan says which folder holds what. They go last, and the link only once every folder has gone. A
  temporary link a switch cut short left beside the install (`.bosun.link-*`) is no part of that: it goes
  earlier, with `~/.local/bin/bosun`, whether the install stays or not. The update's prune keeps a
  checkout from before versions, or an older version, by the same check (`lib/checkout_work.py`).
- **Records are records whatever shape they are in.** A knowledge folder with anything in it, a ledger or a
  backup of one with items, or either of them unreadable, is something it asks about. A copy you made by
  hand, like `ledger.json.before-my-edit`, is not a file it knows, so it stays. `replica.json` serves both
  stores' journals but goes with the ledger, so a knowledge base kept without it carries on under a new id
  from its next write. The update's backups (`backups/`, class `BACKUPS`) each hold both stores, every
  person and `secret.key` included, so they go when either store is deleted and stay only when both are kept.
- **`pmset` decides what is safe.** `pmset repeat cancel` cancels every repeating event, so it only runs when the one
  event there is bosun's wake: at bosun's time to the minute (`1:01AM` is not `11:01AM`), and on bosun's days
  where `pmset` has a word for them (every day, weekdays, weekends). Otherwise it says what it found and leaves
  it to you.
  It does not put back an event that bosun's wake once replaced: the wizard only does that after asking.
- **It reports what it finds, not what it did.** It looks again at the end, and what is still there is what it
  reports, with the command to run. A failed step, whatever the error, does not stop the others, and running it
  again finishes the job. The checkout goes last and only if nothing else is left, because that is what a second
  run needs. An agent's config it cannot read counts as still holding the trust, and the record of a change is
  only forgotten once the agent no longer shows it.
- **A launchd job is bosun's by its label, or by being exactly what install.sh renders.** That is a job that
  runs the install's own `bin/bosun`: the checkout's, or with versions side by side the link's, a version
  folder's or the old checkout's, which older plists name. Your own job that runs `bosun status` is not.

## What a review found

An independent review, before this first shipped, found nine ways the first version broke these rules, each
reproduced in a scratch home: a checkout with a stash or ignored files deleted, `rm -rf ~` suggested for a state
folder set to home, the home guard passed by `/users/me`, records with no lines deleted under `--yes`, `*.log`
taking other programs' logs, crashes on a hand-edited record, your own launchd job taken, trust reported gone
from a config it could not read, and `1:01AM` matching `11:01AM`. Each has a test now, and each of the rules
above has a deliberately broken copy that the unit tests must fail.

## The order

The wake first, as the one step that may ask for something; the rest then runs unattended. The jobs before the
windows, because the watchdog would bring those back. Then the agents' files, the link and the working files.
Stopping the windows writes a file of its own, so it looks again before deleting anything. The checkout last,
since this runs from it: it is all in memory by then. With versions side by side that is the checkout kept from
before versions, the versions folder, then the link, so that the line it gives to run again works until the end.

## What it refuses

- **Inside bosun's tmux server** (but for `--dry-run`), which it would stop halfway through its own work. That
  also stops an agent in one of bosun's windows, and the orchestrator's settings deny the command as well.
- **With no terminal and neither `--yes` nor `--dry-run`:** it has nobody to ask.
- **When `~/.local/bin/bosun` runs another checkout:** it says to uninstall with that one, rather than take the
  jobs from under it.

## What needs no removing

Bosun passes its plugin (`/bosun:chores`), hooks and settings to each window as it starts it (`--plugin-dir`,
`--settings`, Codex's `-c`), so nothing of them is ever installed in `~/.claude` or `~/.codex`. It never writes
Claude Code's `settings.json` or your shell's startup files. `bosun wake-test`'s one-off `pmset` wake expires by
itself. The Keychain items in [development.md](../development.md) are a contributor's, made by hand.

Your conversations stay in the agents' own folders. Bosun's names for them go with its working files.

## Testing

A wrong uninstall costs someone their data or leaves their Mac waking at night, so each layer has to be able to
catch what the one before cannot.

| Layer | What it proves | How |
|---|---|---|
| Unit tests | Every answer, refusal and failure, on a fake Mac that fails on any command it does not know. Before and after, the whole scratch home is compared | `tests/unit/test_uninstall.py`, `test_uninstall_plan.py` |
| Smoke suite | The real command, over a real install's plists. **It also fails on any file a whole run left in the state folder that the tables do not list,** which is how a new file does not get forgotten | `tests/smoke.sh` |
| Docker sandbox | A real terminal: Enter, no, Ctrl-C at each question; keeping and not keeping; installing again from what was kept; the machine's files compared with what it held before | `the_uninstall` in `tests/sandbox/fresh_user.sh` |
| Real Mac, GitHub macOS runner | Real launchd, `pmset` and `sudo`: jobs launchd can no longer run, a wake that is not bosun's left alone, trust you gave kept, back again from what was kept, and nothing named bosun left in the home folder | `uninstall` in `tests/install_e2e.sh` |
| Real agents | After real windows on Claude Code and Codex: no process left, `/bosun:chores` no longer offered (and shown to be offered with the plugin, so the check can fail), both agents still working | `off_again` in `tests/sandbox/live.sh`, through `tests/sandbox.sh mac live` |
