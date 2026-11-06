# Updating bosun

Status: approved 2026-11-05. Part A, the groundwork, is built: the two paths, the stores' formats, the
update's record and hold, backups, version folders, the write gate and the migration registry. The
`bosun update` command is not built yet. The plan: [docs/plans/update.md](../plans/update.md).

## Why

Today, updating means running `get.sh` again. It runs `git pull --ff-only` in `~/.local/share/bosun`,
in place, then starts `bosun init`. All the while, the repo sessions, the orchestrator, the check-in
loop and the launchd jobs keep running from that folder. Nothing is backed up and no data is migrated.
Nothing checks the result, and nothing says what changed. A command that starts mid-pull can import
half old and half new code.

Since sync phase 0, data formats change between versions: a journal's envelope, a store's rules
version. [Sync and sharing](sync.md#decisions-2026-11-04) decided what an update owes them:

- a format change ships with its migration;
- the update backs the data up before it changes anything;
- going back means installing the older version and restoring that backup. Going back to versions
  older than today is not supported.

## What I want

In my words, 2026-11-04:

- I run it mid-afternoon. Repo sessions are working, often with subagents and background tasks, and
  the orchestrator checks in with them and watches Slack and other sources.
- Nothing is lost midway. After the update, the agents check for anything missed from just before.
- The ledger, the knowledge base and all other stored state come through it. They are backed up
  before anything is replaced and checked after.
- When it is done, every agent continues what it was doing before, if anything. A restart ends an
  agent's background tasks, so before one, they are listed or the agent takes stock itself.
- It ends with a short summary: the migrations it ran, version bumps, config files changed, numbers
  such as how many prompts changed, and where the backups are.
- It is tried for real in the macOS VM, with unit and integration tests. How restarts work exactly
  gets settled by testing there.

## Goals, non-goals and anti-goals

The goal is `bosun update` (with `--dry-run`) doing the above. `get.sh` on an existing install hands
over to it.

Not in the first version:

- **A command to go back to an older version.** A failed update puts everything back itself. Going
  back later is the manual path decided in sync.md, which the summary points to.
- **Releases, tags or channels.** It updates to the head of the branch the install tracks (`main`, or
  `BOSUN_BRANCH`), as `get.sh` does today. Each data format carries its own version, so migrations do
  not depend on release numbers.
- **Updating Claude Code or Codex themselves.** `bosun refresh` already restarts idle sessions on a
  new agent version.
- **More than one Mac.** That comes with sync phase 1.

Any of these makes the update a failure, even if it otherwise works:

- an event, a write to the ledger or the knowledge base, a hook's record or a running task lost
  because of the update;
- a busy agent interrupted, or an agent left stopped;
- an install left half old and half new, or a command that starts mid-update seeing a mix;
- data changed before a backup exists, or a backup that cannot be restored;
- a person deleted on request coming back from a backup;
- anything of mine in the config folder (`bosun.env`, `sessions.yml`, `policy.md`, `tasks/`)
  changed without a copy from before and a line in the summary.

## What exists to build on

- **`bosun refresh`** restarts a session only when it is idle, and only once it has been idle for
  `BOSUN_RESTART_MIN_IDLE_SECONDS`; it leaves busy ones alone. It resumes the same conversation.
  It sends `BOSUN_DRAIN_PROMPT` (finish whatever was cut off, or else reply with a one-line status),
  but only to the orchestrator, to a `shell` window, and to a window that holds unfinished
  handed-over work.
- **Signals** ([signals.md](signals.md)): the Stop hook records the background tasks a turn left
  running. A window whose only work left is background work shows as `shell`.
- **The check-in loop** interrupts the orchestrator only when that is safe. A check-in it holds back
  stays due.
- **Catching up after a pause.** The Slack watcher lists activity since its last scan, and the
  events queue is files in the state folder ([events.md](events.md)). So a run that is skipped
  catches up on the next one. This is to be checked in the VM.
- **The store locks.** `locked_ledger` and the knowledge base's `locked` wait on an `flock` with no
  timeout, so a writer waits rather than fails while the lock is held.
- **The file tables.** `STATE_FILES` and `CONFIG_FILES` in `lib/uninstall_plan.py` list every file
  bosun keeps, and class each as working, ledger, knowledge or settings. The smoke suite fails on a
  state file the tables do not list, so they can serve as the backup's list too.
- **What `init` and `install.sh` already do.** `init` keeps a `*.pre-init-*` copy of each config file
  it writes. `install.sh` links `bosun` into `~/.local/bin`, then renders the launchd jobs (filling
  in the install path, `__BOSUN_ROOT__`) and reloads them.
- **Format versions today:**
  - the journal envelope's `v` (`change_log.ENVELOPE_VERSION`, 1);
  - `ledger.json`'s `version` (1);
  - `RULES_VERSION` and `KG_RULES_VERSION`, each pinned by a digest test.

  The knowledge base's files have no format version.

- **Rolling out by hand** (`CLAUDE.md`, "Rolling a change out to running sessions"). The orchestrator
  reads `policy.md` once per conversation; to pick up a change it is sent "re-read". Tasks already
  handed to repo sessions keep their text (`delegate --update`).

## Shape

### Versions side by side, switched in one step

Each version gets its own folder, `~/.local/share/bosun-versions/<commit>/`: a git worktree of one
repository there. `~/.local/share/bosun` becomes a link to the current one. Every path already
written into launchd jobs, hooks and the `~/.local/bin/bosun` link stays the same and goes through
that link.

The switch replaces the link in one rename, so a command sees either the old version or the new one,
never a mix. A version's folder is never changed once in use, so whatever runs from it finishes on
the code it started with. The previous version's folder stays until the next update; older ones are
deleted.

**Two paths, kept apart.** A process loads its code from the version folder it started in. Bash and
Python alike resolve that folder once, at the start, so a command that runs across the switch stays
on one version. Anything that outlives the process gets the install link written into it instead:
hook commands, launch commands, the launchd plists and the `~/.local/bin/bosun` link. Today three
places resolve all the way to the real folder and write that path out:

- `bin/bosun`, with `readlink -f`;
- `lib/init.py`, which runs `install.sh` from `Path(__file__).resolve()`;
- `install.sh`, which fills in `__BOSUN_ROOT__` and makes the `bosun` link from its own location.

All three have to write the link's path instead.

A session keeps its launch command and hook commands until it restarts. So each hook entry point has
to accept the command lines the previous version wrote. A test runs the previous version's launch
settings against the new hooks. Where an update cannot keep this, the plan marks those windows for a
restart.

### Old code that writes after the switch

The freeze makes writers wait, but it does not keep old code out. Some writers load the old code
before the switch and write after it:

- a `bosun ledger` command an agent started just before the switch;
- the detached child of the UserPromptSubmit hook, and of Codex's `codex-turn-end` hook;
- the approval hooks;
- a running `bosun console`, which saves review decisions.

So **every write checks the store before it changes it**, once it holds the lock
(`lib/update_gate.py`). It checks two things, in this order: an update marker saying a freeze is in
progress, before it reads anything of the store, since a store the freeze changed part way may not
even read as one; then the store's format version, before the store's shape, which a newer format
may change. If a freeze is on, or the store's format is not the writer's, nothing is written:

- a command or a hook's child runs itself again through the install link, with the same arguments
  and the same stdin, so an agent's call just works on the new version. It does so once: the run
  again carries `BOSUN_RERUN=1`, and a refusal there is said in one line, never run again;
- a freeze whose update still runs is waited out for a few seconds, then the same. If that update
  died meanwhile, the freeze is put back first ([below](#cut-short)), so the run again can write;
- a store older than the writer's code (a newer checkout run on data not yet migrated) is refused in
  one line: run `bosun update` to migrate it;
- the console refuses with a message to restart it, and the plan lists a running console;
- bosun's own background writers handle a refusal too. `bosun review-watch` runs itself again like
  a command, since what it records it records once. `bosun learn` fails the run, or the apply, in one
  line and leaves it to the next one. It checks before it proposes or applies anything, so a refusal
  normally changes nothing; a freeze that starts in between can still refuse a later write, and the
  next run picks it up;
- the `checkin` window's loop is bosun's own, so the update restarts it when its code changed.

With that check in place, a migration may rewrite the files derived from a journal. The journals
themselves still keep what was recorded: old envelopes are read as new ones. A version without the
check gets no protection from it, which is why the first update converts at a quiet moment
([below](#getsh-on-an-existing-install)).

### The steps

1. **Plan** (changes nothing). Fetch the tracked branch and check out the new version's folder. The
   new version's own code compares the two folders and lists:
   - the commits;
   - each store's format before and after, and the migrations in between;
   - the rules versions;
   - config keys added, renamed or removed;
   - the prompts that changed: `policy.md`, `chores.md`, the task files, `learn.md` and
     `slack-watch.md`, counted and named;
   - which windows need a restart and which only need to re-read something (see below);
   - which launchd jobs need reloading;
   - what the new version needs that is missing (its `lib/preflight.sh`);
   - a `kg forget` cut short, which stops the update until it is finished (see Backups).

   It shows the plan and asks first, as `init` and `uninstall` do. `--yes` skips the question, and
   `--dry-run` stops here.

2. **Rehearse.** Copy the config and state folders (APFS clones, `cp -c`). Run the new version's
   migrations and checks on the copy. If they fail, stop: nothing has changed, and the summary says
   why.
3. **Hold bosun's own background work.** The check-in loop, the watchdog, learning, the Slack
   watcher, the events dispatcher and the morning job each check for an update hold before they
   start a run. While it is set, they skip the run, log that, and catch up on their next one. The
   agents keep working.
4. **Freeze, briefly.** Take the stores' write locks: the ledger, the knowledge base and the other
   state locks. Anything that writes waits. While the locks are held:
   1. set the update marker, recording each step as it goes;
   2. back up;
   3. migrate;
   4. switch the link;
   5. run each store's own check (`ledger validate`, `kg validate`) on the real data;
   6. clear the marker.

   If any of these fails, restore the backup, switch the link back, release the locks, and report
   what failed.
5. **Release.** Reload the launchd jobs whose plist changed, lift the hold, and run the new
   version's `bosun doctor`. A finding at this point is reported, not rolled back, since writes may
   have landed since the freeze.
6. **Bring the agents along** (below).
7. **Summarize** (below).

The freeze has to be shorter than the timeouts of the hooks that wait on these locks. Claude Code
kills a hook that runs longer:

- the approval hooks write to the ledger: PreToolUse and PermissionRequest have 30 s, PostToolUse
  and PostToolUseFailure 10 s;
- the SessionStart hook after a compaction reads the ledger under a shared lock, which the freeze's
  exclusive lock blocks, and has 15 s.

The UserPromptSubmit hook detaches its child, which simply waits out the freeze. The signal hooks
take no lock. So the freeze must stay under 10 s. It gets measured in the VM on a year of data. If
it cannot stay under 10 s, the post-call approval hooks must save their record to disk for the next
write to take in. Measure before building that.

### Backups

- **What.** Everything the file tables class as ledger, knowledge or settings, plus the working files
  that carry work in flight: the events queue, signals, the Slack watcher's state, `login.json`,
  `codex-sessions.json` and `agent-changes.json`. (`replica.json` is already classed as ledger.)
  Logs, dry runs and earlier backups are left out.
- **Where.** `~/.local/state/bosun/backups/<time>-<from>-<to>/`, made with APFS clones so they cost
  little. Each has a manifest listing both versions, every file with its digest, and the format
  versions. The last three are kept. The backups folder needs a row in `STATE_FILES`.
- **Restoring.** The update itself restores from a backup when its checks fail. In the first
  version, restoring later is by hand; the summary and `docs/setup.md` say how.
- **A forget reaches them.** `kg forget` promises that nothing of the person is left, but a backup
  holds the whole knowledge base as it was, `forgotten.jsonl`, the journal and `secret.key`
  included. Restoring one from before the forget would bring the person back with no trace of the
  forget, which the adopt check in [the knowledge guide](../knowledge.md) cannot catch, since that
  check reads the very records the restore replaces. So every deletion of a person deletes every
  update backup there is, whole, and says so in its line ("deleted 2 update backups taken before
  this; going back past it is no longer possible"), without naming the person. Every backup there
  is was taken before the deletion, as the freeze holds the knowledge lock while it backs up.
  - Every deletion means each one that ends in `forget_in_journal` (`lib/knowledge.py`): `kg
    forget`, `kg expire` when it deletes a person fact, and a person removed outside the journal
    whom a forgotten record matches. Deleting the backups is a step of that last step, so a
    deletion cut short is finished by the next write, backups included. The backups' own module
    does the deleting; the knowledge code only calls it.
  - The rehearsal copy is deleted when the rehearsal ends, so it never outlives the update.
  - A `kg forget` cut short holds the person in the files until the next knowledge write. The plan
    checks for one, and the update stops there and says so (any `kg` write finishes it), so no
    backup or rehearsal copy is taken of it.
  - When the ledger gets a forget (open for phase 1 in [sync.md](sync.md)), it does the same.

### Migrations

- Each store gets a format version where it has none (the knowledge base). A test per store pins a
  digest of its format to its version, the way the rules digests are pinned, so a format change
  cannot land without a version bump and a migration.
- One registry, `lib/migrations.py`, lists each migration in order: its store, its from and to
  versions, the function that does it, and the line it adds to the summary. The same registry takes
  the rules changes that alter what earlier changes mean
  ([sync.md](sync.md#decisions-2026-11-04)).
- Each migration:
  - runs on the rehearsal copy first;
  - can run twice with the same result;
  - is tested from a fixture in the old format;
  - leaves the store passing its own check.
- **Journals are append-only.** A migration reads old envelopes as new ones (upcasting) rather than
  rewriting what was recorded, and rebuilds the files derived from the journal. So whatever a store
  rebuilds from its journal is in the code's format (a `ledger.json` made from the rebuild records it).
- **Config.** A new key needs nothing written, since the defaults in `lib/common.sh` apply. A key
  that is renamed or removed is edited in `bosun.env`, keeping a `bosun.env.pre-update-<time>` copy
  (`YYYYmmdd-HHMMSS`, in UTC). The update never edits my own files: `sessions.yml`, `setup.sh`,
  `policy.md`, `learn.md`, `tasks/` and `workstreams.json`. If their format has to change, the
  summary says what to change and `bosun doctor` points to it.

### Bringing the agents along

Each window gets one of three treatments, decided from the plan and the window's signals:

- **Nothing it was launched with changed.** It is left alone. This is the case for most updates. The
  next bosun command or hook it runs uses the new version.
- **Only the orchestrator's prompts changed** (`policy.md`, the task files). Once it is idle, it gets
  the update prompt, which asks it to re-read those files. Tasks already handed to repo sessions keep
  their text, as today; the summary lists them, so I can send `delegate --update` if a rule matters.
  `chores.md` is the orchestrator's plugin. Whether a running session picks up a changed command
  file is still to be checked in the VM; until then, a change to it counts as a launch input.
- **Something it was launched with changed:** its settings, its hooks, the launch command or the
  plugin. It is restarted on the new version through refresh's path, and what happens depends on
  what it is doing:
  - *idle, nothing running:* restart it, resume the conversation, and send the update prompt;
  - *`shell`, only background work running:* a restart would end that work, so first ask it to take
    stock. The message says that bosun will restart it for an update, asks it to list its background
    work and what it would do when each part finishes, and to stop and wait. Keep the reply and the
    signals' list, restart it, and pass the list back in the update prompt so it starts that work
    again;
  - *busy:* wait until it is idle. The update keeps waiting and shows which windows it is waiting
    for. Ctrl-C leaves them to the check-in loop, which restarts each one once it is idle. For that,
    `refresh` learns which bosun version each window was launched on;
  - *waiting at a permission prompt or a question:* never restarted until it is answered;
  - *someone typing in it* (within `BOSUN_SEND_TYPING_MINUTES`, or unsent text in its input box):
    left until they are done, as `send` waits.

  A window whose next launch takes up another conversation than the one it runs starts that one as it
  would have, never with "continue what you were doing" in a blank conversation: the orchestrator
  still on yesterday's starts today's with the morning chores, as on a new day, and a repo window
  with a fresh conversation pending starts it with the fresh conversation's prompt.

**What a window was launched with.** `bosun launch` records it, per tmux session and window, in
`launches/<session>/<window>.json` in the state folder (`lib/launch_record.py`): bosun's commit, the
agent and the model and effort it runs with, the window's directory and any `--model` / `--effort` it
was given, and a fingerprint in two parts:

- the agent's arguments, built for a placeholder conversation name, with the policy's text left out:
  a new day's or a fresh conversation's name is no launch input, and the policy is re-read, not
  restarted for;
- the content of the files under each argument that is a path inside the install (the plugin, a
  settings file), read through the install link, as the agent reads them.

`bosun launch --fingerprint <window>` gives the fingerprint a launch would record now, and `bosun
refresh --update-restart` restarts a window through refresh's path. Both start from the record:

- every window keeps the agent it was launched on. A restart onto another agent would land in a blank
  conversation mid-work, so if `harness:` in `sessions.yml` or `BOSUN_HARNESS` changed since, the
  update restarts it on the recorded agent (`launch --harness`), with the model and effort it ran
  with, since the configured ones are meant for the other agent, and the change does not count as one
  in its fingerprint. The new agent takes over at the next restart that is not for an update
  (`refresh`, `start`);
- a window outside the window list (opened by `send --new`) keeps the directory and the `--model` /
  `--effort` its launch recorded, so it carries on as it was started. The two fingerprints are compared after the switch, in
this step, when the link leads to the new version's files. Before the switch it still leads to the
old ones, so the plan's list of the windows that need a restart has to get the new files another
way.

The **update prompt** says bosun was updated from one version to the other, and whether the session
was restarted or kept running. It asks the session to check for anything missed from just before:

- for the orchestrator, a check-in, covering the events queued during the hold and Slack since its
  last scan;
- for a repo session, its item in the ledger and any replies.

Then it continues what it was doing, including any background work it listed. The exact wording gets
settled with the real Claude and Codex in the VM, and with free-text test cases.

The update is done when every window that needed something has answered with its status. `bosun
update` ends there, with the summary.

### The summary

The update prints the summary when it ends and saves it as `summary.md` next to the backup; `--json`
prints it as JSON instead. For example (fictional windows):

```text
bosun updated: 95545b0 -> c41b186 (23 commits) in 2m 41s
Data
  ledger      format 1 -> 2 (412 items migrated); rules 1 -> 2
  knowledge   format 1, unchanged
Config
  bosun.env   BOSUN_OLD_KEY is now BOSUN_NEW_KEY (before: bosun.env.pre-update-20261104-153207)
  3 new keys, at their defaults (bosun doctor lists them)
Prompts changed: 4 (policy.md, tasks/review.md, tasks/_common.md, learn.md)
Jobs reloaded: watchdog
Agents
  orchestrator  re-read policy.md; checked in (2 events from the update)
  api           restarted when idle; resumed: waiting on CI for PR 12
  web           busy; kept running (nothing it was started with changed)
Backup: ~/.local/state/bosun/backups/20261104-153207-95545b0-c41b186 (3.1 MB)
Going back: docs/setup.md#going-back
```

### `get.sh` on an existing install

The bosun already installed may not have `update` yet. `get.sh` is always fetched fresh from the
branch, so on an existing install it gets the new version into its own folder and runs that version's
`bosun update`. A first install uses the new layout from the start.

**The first update converts, at a quiet moment.** The version it converts from has none of the
following:

- the hold;
- the write check;
- a `refresh` that knows bosun's version;
- a check-in loop that honours the hold.

So the conversion does not try to meet the mid-afternoon bar; nobody else runs bosun yet. It waits
until every window is idle with nothing running and nobody has typed for a while. Then it stops
bosun's own jobs and swaps the checkout for the link in one step: macOS's `renamex_np` with
`RENAME_SWAP`, so the path never goes missing. Finally it restarts the `checkin` window and brings the
agents along as below. The VM tests this conversion on its own, and tests the mid-afternoon update
between two versions that both have `update`.

### Cut short

- **Each step records where it is,** in the update marker in the state folder (a row in
  `STATE_FILES`). An update can be cut short by a power cut, Ctrl-C or a crash; what happens next
  depends on where it stopped:
  - before the freeze: nothing changed but the hold, which is lifted;
  - inside the freeze: the backup is the truth. A dead process releases its locks, but the writers
    still find the marker and write nothing. So the next bosun command that would write finds the
    update's process gone, and restores the backup and the link itself, under the locks. Nothing
    has been written since the backup, so nothing is lost. `bosun update` does the same if it runs
    first;
  - after the checks passed: the update rolls forward. The next `bosun update` lifts the hold and
    brings the agents along.
- **The hold lapses** at once when its update has died, as it counts only while the update holds its
  lock, and after an hour in any case, so an update that died never stops the check-in loop. The
  watchdog and `bosun doctor` report an update that stopped inside its hold.
- **One update at a time:** a second one is refused while the first runs.
- **The record only grows.** Old code reads a newer version's record: a hook or a command started
  before the switch, the console. So its steps are only ever added, never renamed or removed, and a
  new field is optional; a field old code does not know is ignored. A step it does not know is a
  newer bosun's: the write is refused and runs again through the install link, whose code knows it,
  and the hold counts as on. So a new step is recorded only inside the freeze or after the switch;
  progress before the switch goes in a field, or the installed old code would refuse every write. A record that is there but cannot be read refuses every write in one
  line, since it may record a freeze: `bosun update` comes first (it resumes an update in progress and
  puts back one cut short), and the file is moved aside only if `bosun update` cannot read it either.

## Testing

- **Unit tests:**
  - the plan, which is pure: two version folders in, the migrations, prompts changed and windows to
    restart out;
  - the registry, with a fixture per migration;
  - the format digests;
  - the summary;
  - the decision for each window, for every pairing of its state (idle, shell, busy, waiting) with
    what changed.
- **Smoke tests**, hermetic, with fake Claude windows. An update between two local commits with:
  - an idle, a `shell` and a busy window;
  - a hook and a ledger write arriving during the freeze: they wait, and nothing is lost;
  - a migration that fails: everything is back as before;
  - a `kill -9` at each step: the next run finishes or undoes it;
  - a `kg forget` after an update deletes its backup and says so; one cut short is finished,
    backups included, by the next write; and an update stops while one is cut short;
  - stand-ins for launchd.
- **The VM** (`tests/sandbox.sh mac work`). A working day on the previous version, then `bosun
  update` to this checkout mid-afternoon, while sessions are busy, a background wait is running and
  stand-in Slack events arrive during the hold. It checks that:
  - no event, write or record was lost;
  - each window resumed, with its background work started again;
  - the summary is right;
  - the backup restores, and the previous version runs on it;
  - after a person is forgotten, no backup or rehearsal copy holds their id.

  It also measures the freeze on a year of data.

- **`tests/sandbox.sh mac live claude|codex`** tries the take-stock and update prompts with the real
  agents (about a dollar a run), with free-text cases for them in `tests/llm/`.

## Decided, my calls, and what is left to the builder

Decided by the user:

- backup before anything changes, a check after, and everything put back on failure;
- a migration with each format change;
- going back means installing the older version and restoring the backup, and is not supported to
  versions older than today;
- nothing lost during the update, and the agents check for anything they missed;
- each agent continues what it was doing, its background work included;
- a `kg forget` deletes the update backups taken before it, so it leaves nothing, at the cost of
  going back past it;
- the summary's contents;
- tried for real in the VM.

My calls, which the user can overturn:

- versions side by side, with `~/.local/share/bosun` as the link that switches;
- each process keeps the version it started on, and only the link is written into anything that
  outlives the process;
- every write checks the store's format and the update marker, and on a newer store it runs itself
  again on the new version;
- after a crash inside the freeze, the backup is the truth, and the first writer restores it;
- the first update, which converts the layout, waits for a quiet moment;
- a hold on bosun's own background work, plus a short freeze under the stores' locks;
- agents left alone unless something they were launched with changed;
- the update waits for busy windows, and Ctrl-C hands them to the check-in loop;
- a window's launch inputs are its arguments (the conversation's name and the policy's text left out)
  and the files under the install they name, read through the link; the commit, the agent, the model
  and effort, the directory and the overrides are recorded with them, and they are compared after the
  switch;
- an update restarts a window on the agent it was launched on; a changed agent takes over at the next
  restart not for an update;
- three backups kept, in the state folder;
- a forget deletes each backup whole, not only its knowledge part, since a backup is restored as
  one moment;
- every deletion of a person does it (`kg expire` and a removal outside the journal too), at the
  deletion's last step, so a deletion cut short finishes it;
- the update stops while a `kg forget` is cut short;
- restoring done by the update itself only, with no `--restore` command in the first version;
- updating to the head of the tracked branch;
- the summary also saved next to the backup.

Left to the builder:

- how the hold works;
- upcasting or rewriting, case by case;
- the update prompt's exact words, settled in the VM;
- saving hooks' records to disk while a lock is busy, if the measured freeze needs it;
- the update state file's format.

## Docs to change when it is built

- `docs/setup.md` (updating and going back, and that a forget deletes the backups);
- `docs/knowledge.md` and the deletion bullet of `docs/design/knowledge.md` (a deletion deletes
  the update backups);
- `docs/commands.md`;
- `README.md`;
- the `get.sh` header;
- the file tables in `lib/uninstall_plan.py`;
- `docs/development.md` (the VM scenario);
- `CLAUDE.md`: a new common change, "a data format change", which needs a version bump, a migration
  and a fixture.
