# Updating bosun

Status: built and run in the macOS VM. The design is in `bosun update` (`lib/update*.py`, behind
`libexec/bosun-update`), in `get.sh`'s side-by-side versions and its first-run conversion of a checkout an
older `get.sh` made (`convert` in `lib/update.py`), and in the summary (`lib/update_summary.py`). What the
runs on a real Mac showed, and what they changed, is under [Settled in the VM](#settled-in-the-vm); what
was decided while building it, under "Settled while building it" and "Decided while building it". The
plan: [docs/plans/update.md](../plans/update.md). Going back with a command (`bosun update --back`) and trying a
branch (`bosun update --branch`) are under [Going back, and trying a branch](#going-back-and-trying-a-branch): built,
with unit and smoke tests, and their VM runs still to come.

## Why

Before `bosun update`, updating meant running `get.sh` again. It ran `git pull --ff-only` in
`~/.local/share/bosun`, in place, then started `bosun init`. All the while, the repo sessions, the
orchestrator, the check-in loop and the launchd jobs kept running from that folder. Nothing was backed up
and no data was migrated. Nothing checked the result, and nothing said what changed. A command that
started mid-pull could import half old and half new code.

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

- **A command to go back to an older version** (the first version: a failed update put everything back
  itself, and going back later was the manual path decided in sync.md, which the summary pointed to).
  `bosun update --back` came later, one step back ([Going back, and trying a
  branch](#going-back-and-trying-a-branch)); the manual path stays, for when the newer version's own update is
  what broke.
- **Releases, tags or channels** (the first version: it updated to the head of the branch the install tracks,
  `main` or `BOSUN_BRANCH`, as `get.sh` did then; releases came with the menu bar app, see
  [Channels](#channels)). Each data format carries its own version, so migrations do not depend on release
  numbers.
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
- **The file tables.** `STATE_FILES` and `CONFIG_FILES` in `lib/file_tables.py` list every file
  bosun keeps, and class each as working, ledger, knowledge or settings. The smoke suite fails on a
  state file the tables do not list, so they can serve as the backup's list too.
- **What `init` and `install.sh` already do.** `init` keeps a `*.pre-init-*` copy of each config file
  it writes. `install.sh` links `bosun` into `~/.local/bin`, then renders the launchd jobs (filling
  in the install path, `__BOSUN_ROOT__`) and reloads them.
- **Format versions before this work:**
  - the journal envelope's `v` (`change_log.ENVELOPE_VERSION`, 1);
  - `ledger.json`'s `version` (1);
  - `RULES_VERSION` and `KG_RULES_VERSION`, each pinned by a digest test.

  The knowledge base's files had no format version (Part A gave it one, in `knowledge/format.json`).

- **Rolling out by hand** (`CLAUDE.md`, "Rolling a change out to running sessions"). The orchestrator
  reads `policy.md` once per conversation; to pick up a change it is sent "re-read". Tasks already
  handed to repo sessions keep their text (`delegate --update`).

## Shape

### Versions side by side, switched in one step

Each version gets its own folder, `~/.local/share/bosun-versions/<commit>/` (named for the first 12
characters of its commit): a git worktree of one repository there, `bosun.git`. `~/.local/share/bosun`
becomes a link to the current one. Every path already written into launchd jobs, hooks and the
`~/.local/bin/bosun` link stays the same and goes through that link.

The switch replaces the link in one rename, so a command sees either the old version or the new one,
never a mix. A version's folder is never changed once in use, so whatever runs from it finishes on
the code it started with. The previous version's folder stays until the next update; older ones are
deleted, unless one holds something of yours (a change in its files, a commit made in it), which the
summary says. One a running process still uses (it has the folder open, or names it in its
command line, as a long-running `bosun console` does) is left to a later update, and with no `lsof` or
`ps` to tell, every one stays.

**Two paths, kept apart.** A process loads its code from the version folder it started in. Bash and
Python alike resolve that folder once, at the start, so a command that runs across the switch stays
on one version. Anything that outlives the process gets the install link written into it instead:
hook commands, launch commands, the launchd plists and the `~/.local/bin/bosun` link. Before Part A,
three places resolved all the way to the real folder and wrote that path out:

- `bin/bosun`, with `readlink -f`;
- `lib/init.py`, which runs `install.sh` from `Path(__file__).resolve()`;
- `install.sh`, which fills in `__BOSUN_ROOT__` and makes the `bosun` link from its own location.

Each still finds its own folder that way, to run from it, and what it writes out is the link's path
now (`lib/install_paths.py`, and `BOSUN_INSTALL` / `BOSUN_CMD` in `lib/common.sh`).

A session keeps its launch command and hook commands until it restarts. So each hook entry point has
to accept the command lines the previous version wrote. A test runs the previous version's launch
settings against the new hooks. Where an update cannot keep this, the plan marks those windows for a
restart.

### Old code that writes after the switch

The freeze makes writers wait, but it does not keep old code out. Some writers load the old code
before the switch and write after it:

- a `bosun ledger` command an agent started just before the switch;
- the detached child of the UserPromptSubmit hook, and of Codex's `turn-end` hook (`codex-turn-end` in windows started before);
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
- the `checkin` window's loop is bosun's own, a long-running script that keeps the code it started
  with, so the update's release restarts it, every time.

With that check in place, a migration may rewrite the files derived from a journal. The journals
themselves still keep what was recorded: old envelopes are read as new ones. A version without the
check gets no protection from it, which is why the first update converts at a quiet moment
([below](#getsh-on-an-existing-install)).

### The steps

1. **Plan** (changes nothing). Fetch what the channel follows (the newest release's tag on `releases`, the head
   of the tracked branch on `main`: [Channels](#channels)) and check out the new version's folder. The
   new version's own code compares the two folders and lists:
   - the commits;
   - each store's format before and after, and the migrations in between;
   - the rules versions;
   - config keys added, renamed or removed, with what each added one means, and the value written to
     `bosun.env` for a key whose default would change what the install did;
   - the prompts that changed: `policy.md`, the ontology's rules (`knowledge.md`), `chores.md`, the task
     files, `learn.md` and `slack-watch.md`, counted and named;
   - which windows need a restart and which only need to re-read something (see below);
   - which launchd jobs need reloading;
   - what the new version needs that is missing (its `lib/preflight.sh`);
   - a `kg forget` cut short, which stops the update until it is finished (see Backups).

   As built, these stop it at the plan too: a store whose format cannot be read, a link among what a
   backup copies that leads nowhere, a launchd job the new version cannot render, and versions that are
   not side by side. It lists the windows the last update still brings along, which this one takes over
   (below), and a console (bosun's background job, which the update restarts itself, or one
   started by hand, which it does not), the handed tasks that keep their old text, and what to change by hand in my own files.

   It shows the plan and asks first, as `init` and `uninstall` do. `--yes` skips the question;
   `--dry-run`, or no terminal to ask in, stops here.

2. **Rehearse.** Copy what a backup holds (below) into `update/rehearsal` in the state folder (APFS
   clones, `cp -c`). Run the new version's migrations and checks on the copy. If they fail, stop:
   nothing has changed, and it says why in one line.
3. **Hold bosun's own background work.** The check-in loop, the watchdog, learning, the Slack
   watcher, the events dispatcher and the morning job each check for an update hold before they
   start a run. While it is set, they skip the run, log that, and catch up on their next one; the
   morning job instead waits for the hold to lift, as its next run would be the next morning. The
   agents keep working.
4. **Freeze, briefly.** Take both stores' write locks, the ledger's and then the knowledge base's.
   Anything that writes to them waits (the other working files a backup holds, such as the events
   queue, are not locked, and a failed freeze does not put them back: an event queued meanwhile is
   never undone). While the locks are held:
   1. set the update marker, recording each step as it goes;
   2. back up;
   3. migrate;
   4. switch the link;
   5. run each store's own check (`ledger validate`, `kg validate`) on the real data;
   6. clear the marker.

   If any of these fails, restore the backup, switch the link back, release the locks, and report
   what failed.
5. **Release.** Make sure the install leads to the new version (a no-op after the freeze switched it), reload
   the launchd jobs whose plist changed and unload, deleting their plists, those the version updated from lists
   and the new one does not (`job_labels`; each would run a command the new version lacks), restart the
   check-in loop while the hold
   is still on (it stops at once, even mid-tick, and the hold keeps a tick from starting meanwhile),
   bring the menu bar app to the version the new one expects (`bosun app update` of the new version, which
   goes by the app being there, not by `BOSUN_MENU_APP`, and does nothing with none; never to an app older than
   the one installed, which is left as it is; a refusal or failure is a
   finding in the summary and stops nothing), restart the
   console's launchd job if it is loaded and wanted (`console/wanted` is there; `launchctl kickstart -k`, so it
   serves the new code; a job that is not loaded is never started), lift the hold, delete the old backups and version folders, and run the new version's `bosun
   doctor`. A finding at this point is reported, not rolled back, since writes may have landed since
   the freeze.
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
  versions. The last three are kept, by name, always counting the backup of the update that prunes them:
  taken after the clock went back, its name sorts first, and a second backup in the same second gets
  `-2` after its name rather than failing. The backups folder has a row in `STATE_FILES`, of a class of its
  own (`BACKUPS`): `bosun uninstall` deletes it with either store, as each backup holds both.
- **Restoring.** The update itself restores from a backup when its checks fail. In the first
  version, restoring later was by hand; the summary and `docs/setup.md` said how. `bosun update --back`
  restores what the older version cannot read ([below](#going-back-and-trying-a-branch)), and by hand
  stays the fallback.
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
- **Config.** A new key needs nothing written, since the defaults in `lib/common.sh` apply. Two kinds
  of change are written to `bosun.env`, keeping one `bosun.env.pre-update-<time>` copy for both
  (`YYYYmmdd-HHMMSS`, in UTC); a `bosun.env` that is a link stays one, and its target changes:
  - a key renamed or removed (`CONFIG_RENAMES` in `lib/migrations.py`) is renamed, or its line removed;
  - a key the update adds whose default would change what an older install did (`CONFIG_ADDED`, an
    entry with a kept value) is appended with that value, under a comment with its note, when the
    plan's `keys_added` holds it and the file does not set it. The first is
    `BOSUN_ORCHESTRATOR_CONNECTORS="all"` ([connectors](connectors.md#existing-installs)).

  Each `CONFIG_ADDED` entry carries a note: what the plan shows beside the key, a line of its own in
  the summary under Config, and what `bosun doctor` adds to the key while `bosun.env` does not set it. A
  kept key's note says the install keeps what it did, so it is said only where its value was written; it
  also carries an unset note, for while it is at its default: with no `bosun.env` to write it to, the
  plan and the summary say that one (`unwritten` in the record; an older record has none), and so does
  `bosun doctor`, which warns whenever `bosun.env` does not set such a key (`migrations.py unset`, also
  for a checkout updated with `git pull`, which no update ran for).
  The plan lists the kept values it will write (`kept` in its record; an older record has none), and
  compares each window's launch with them given, so a window that keeps what it had is no launch
  change. Whether an update may change `bosun.env` (keys renamed, or a kept key among those added) is
  one condition for writing it and for putting it back after a failed freeze. Without a `bosun.env`
  nothing is written. The update never edits my own files: `sessions.yml`, `setup.sh`, `policy.md`,
  `learn.md`, `tasks/` and `workstreams.json`. If their format has to change, the summary says what to
  change and `bosun doctor` points to it.

### Bringing the agents along

Each window gets one of three treatments, decided from the plan and the window's signals:

- **Nothing it was launched with changed.** It is left alone. This is the case for most updates. The
  next bosun command or hook it runs uses the new version.
- **Only the orchestrator's prompts changed** (`policy.md`, `knowledge.md`, the task files). Once it is idle, it
  gets the update prompt, which asks it to re-read those files; `knowledge.md` only with
  `BOSUN_KNOWLEDGE_ENABLED=1`, as its policy holds the ontology's rules only then (with the key off, the update
  that brings them lists them among the changed prompts and asks nothing of the orchestrator for them). A
  repo window whose system prompt holds the ontology's rules (`BOSUN_KNOWLEDGE_ENABLED=1`, a Claude window)
  cannot re-read it: their file is part of its fingerprint (below), so a change to it restarts the window. Tasks already handed to repo sessions keep
  their text, as today; the summary lists them, so I can send `delegate --update` if a rule matters:
  each item handed a task type whose file changed (`delegate review,fix` hands both; a type the site
  replaces in its own `tasks/` is the site's text, which an update never changes), and every item
  handed out when `orchestrator/tasks/_common.md` changed, as every task gets it (a site's
  `_common.md` only adds to it).
  `chores.md` is the orchestrator's plugin. A running Claude does not pick up a changed command file
  (the VM showed it, [below](#settled-in-the-vm)), so a change to it is a launch input.
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
    the update's record keeps the windows still waited for and what was done to each, and `bosun
    launch` records what each window was launched with (below);
  - *waiting at a permission prompt or a question:* never restarted until it is answered. Such a window
    never ends its wait by itself, so once every window the update still waits for waits for me, the
    update's own wait ends as Ctrl-C ends it: it names each ("orchestrator waits for your answer at its
    prompt; once you answer, the check-in loop restarts it for the update") and leaves them to the
    check-in loop, which brings each along once it is answered and its turn is over. A busy window
    among them keeps the wait going, as it ends by itself. So an update the menu bar app started, with
    no terminal to press Ctrl-C in, never hangs on a prompt;
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

- the agent's arguments, built for a placeholder conversation name, with the text after
  `--append-system-prompt` left out: a new day's or a fresh conversation's name is no launch input, the
  policy is re-read, not restarted for, and a repo window's ontology rules count through their file;
- the content of the files under each argument that is a path inside the install (the plugin, a
  settings file), read through the install link, as the agent reads them, and of the file whose text a
  repo window's prompt holds (`orchestrator/knowledge.md`, with the key on; with it off, nothing is added and
  the fingerprint is what it was before the rules).

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
old ones, so for the plan's list of the windows that need a restart the new version's `launch
--fingerprint` reads the files under the install from its own folder.

The **update prompt** says bosun was updated from one version to the other, and whether the session
was restarted or kept running. It asks the session to check for anything missed from just before:

- for the orchestrator, a check-in, covering the events queued during the hold and Slack since its
  last scan;
- for a repo session, its item in the ledger and any replies.

Then it continues what it was doing, including any background work it listed. The exact wording gets
settled with the real Claude and Codex in the VM, and with free-text test cases.

The update is done when every window that needed something has answered with its status. `bosun
update` ends there, with the summary, or once only windows waiting for me are left (above), which the
check-in loop then finishes.

### The summary

The update prints the summary when it ends and saves it as `summary.md` next to the backup; `--json`
prints it as JSON instead (as `summary` in its one object, beside the plan and the record). For
example (fictional windows):

```text
bosun updated: 95545b0 -> c41b186 (23 commits) in 2m 41s
Data
  ledger      format 1 -> 2 (412 items migrated); rules 1 -> 2
  knowledge   format 1, unchanged
Config
  bosun.env   BOSUN_OLD_KEY is now BOSUN_NEW_KEY (before: bosun.env.pre-update-20261104-153207)
  3 new keys, at their defaults (bosun doctor lists them)
Prompts changed: 4 (learn.md, policy.md, tasks/_common.md, tasks/review.md)
Jobs reloaded: watchdog
Agents
  orchestrator  asked to re-read policy.md, tasks/_common.md, tasks/review.md; checked in (2 events from the update)
  api           restarted; resumed: waiting on CI for PR 12
  web           kept running (nothing it was started with changed)
Backup: ~/.local/state/bosun/backups/20261104-153207-95545b0-c41b186 (3.1 MB)
Going back: bosun update --back (docs/updating.md#going-back)
```

Settled while building it:

- **The same however it ends.** Each step keeps in the update's record what the summary needs as it
  learns it: the plan said yes to when it starts, each migration's line and what changed in
  `bosun.env` at the switch, the jobs reloaded in the release. So an update carried on by the next
  `bosun update`, or finished by the check-in loop, says what one that ran in one go would; a record
  an older bosun wrote, which kept none of it, says less, never something false.
- **When.** It is said when the update ends, also when Ctrl-C, or only windows waiting for me, stop the
  wait (the windows still waited for say so), and saved as `summary.md` once the update is done, by
  whichever run finishes it: the update itself, the next `bosun update` (which takes over the windows
  still waited for: their line says "carried into the next update", and the next summary says what came
  of each, "owed by the last update too"), or the check-in loop's last turn.
- **Each window's line** is what was done to it, as the record keeps it (the orchestrator's re-read
  naming the files it was asked to re-read), then its own answer; a window left alone says it kept
  running, and the `checkin` window is not listed. A window whose restart did not start says so, with the
  launch's own reason and what to do ("did not start: <why>; fix that, then bosun refresh <window>"). The
  prompts are named in the plan's order.
- **What else it can say,** each only when there is something: what to change by hand in my own
  files, under Config; the tasks handed out that keep their old text, with the `bosun delegate
  --update` for each; and, under "Found after the switch", what the update itself found (a job not
  reloaded, the check-in loop not restarted) and each `bosun doctor` FAIL, with its detail, that a
  doctor run while planning (before the hold, its FAIL lines kept in the record) did not show. Those
  it did show are counted ("2 already failing before the update"), and so are doctor's warnings ("3
  warnings: run bosun doctor"): they are no news of the update. A backup that a `kg forget` has
  deleted since says so instead of its path ("deleted by a kg forget since; going back past it is no
  longer possible"); a forget deletes a backup whole even when a summary is being saved into it. A
  failed update names the backup it took. The backup's size is that of its files.
- **`bosun doctor`** lists what the last update left to me, from its record: the changes to make in my
  own files, and the keys it added that `bosun.env` does not set, with the value each has now and its
  note (a kept key's only where it holds: written).

### Channels

Added with the menu bar app ([menu-bar.md](menu-bar.md)); the rest of this note is unchanged by it. `BOSUN_UPDATE_CHANNEL`
says what `bosun update` moves to, and is the one setting:

- `releases` (the default): the newest release, a tag `v<YYYY.MM.DD>` (a second one that day `.2`, then `.3`),
  found by `git ls-remote --tags` (`lib/update_check.py`'s tag rule, with the git side in `lib/update_lookup.py`:
  read-only, no version folder, no lock). The release is fetched into a remote ref of `bosun.git`
  (`refs/remotes/origin/release`), never into `refs/tags`, which `bosun uninstall` would count as work of yours,
  and the commit it points at is resolved there, locally (`refs/remotes/origin/release^{commit}`, as `get.sh`
  does with its own ref): an origin whose `git ls-remote` prints an annotated tag's own object and no peeled
  `^{}` line (a git bundle, or a mirror made from one) is read as rightly as GitHub, and every comparison, the
  commit the update checks out and the one `--check` prints are that commit. The folder is made as for a
  branch's head. The version is the same folder named by commit; the release's
  name is only what `--check` and the summary call it. An install past the newest release (on `main` after it) says
  so and changes nothing.
- `main`: the head of the branch the install tracks (`bosun.branch`, `main` by default), as before releases. An
  install made with `BOSUN_BRANCH` is on it (`get.sh` tells `init`, which writes it), and so is an install that
  was updating before releases existed: `BOSUN_UPDATE_CHANNEL="main"` is a `CONFIG_ADDED` key with that kept value,
  which the first update writes to `bosun.env` with a note in the plan and the summary saying how to follow the
  releases instead. Behaviour for an existing install does not change.

`bosun update --check [--json]` is the same lookup with nothing changed (what the app's "Check for Updates..." runs),
and `--skip <version>` records a release not to be offered again (`update-skip.json`; it does not stop an update you
run). A first install takes the newest release too (`get.sh`'s own `newest_release`, an `sh` twin of the tag rule
that `tests/unit/test_get_sh.py` holds to the Python one); a repository with no release yet gets the head of `main`.
The VM runs (`tests/install_e2e.sh`, on the default channel against a bare repository with release tags: first release
installed, next one tagged, `--check --json`, `--skip`, `--yes`; `tests/work_e2e.sh`, on the channel its conversion
keeps) check both.

### `get.sh` on an existing install

The bosun already installed may not have `update` yet. `get.sh` is always fetched fresh from the
branch, so on an existing install it hands over to `bosun update`: the install's own when it is a link
to a version, so that its channel decides what it moves to, and the new version's, from the head of the
branch, when it is a checkout from before versions. A first install uses the new layout from the start.

**The first update converts, at a quiet moment.** The version it converts from may have none of the
following (a checkout from before Part A has none; one of Part A has the hold, the write check and a
check-in loop that honours the hold, but records no launches):

- the hold;
- the write check;
- a `refresh` that knows bosun's version;
- a check-in loop that honours the hold.

So the conversion does not try to meet the mid-afternoon bar; nobody else runs bosun yet. It waits
until every window is idle with nothing running and nobody has typed for a while. Then it stops
bosun's own jobs and swaps the checkout for the link in one step: macOS's `renamex_np` with
`RENAME_SWAP`, so the path never goes missing. Finally it restarts the `checkin` window and brings the
agents along as below. The VM is to test this conversion on its own, and the mid-afternoon update
between two versions that both have `update` (not run yet).

Settled while building it:

- **`get.sh`** looks at the install path first, and refuses what it does not know. On an install `init`
  has set up, a link to a version gets the install's own `bosun update` and fetches nothing; a checkout
  gets the branch fetched into `bosun.git` (cloned bare beside it first), the new version's folder and
  that version's `bosun update --from` the checkout. An install `init`
  never finished gets `init` again, as it is, since only `bosun update` switches versions. The cases:
  [init.md](init.md#the-wizard).
- **Which update converts** is decided by the version updated from, once an update cut short is carried
  on: a checkout from before versions, at the install path or kept as `legacy-*` (where a conversion put
  back leads the link). `convert` refuses one with anything of yours in it, as `bosun uninstall` checks a
  checkout, since the update after it deletes the checkout.
- **The quiet moment:** every window's agent idle, or none running (the `checkin` window aside, whose
  loop it stops), and nobody typing in a client of the session within `BOSUN_ACTIVE_CLIENT_MINUTES`. It
  waits once you said yes, saying what it waits for each time that changes, and plans again when it is
  quiet. Ctrl-C there changes nothing. The doctor run and the rehearsal take a while after it, and the old
  check-in loop could prompt a window meanwhile, so once bosun's own work is stopped it checks again, and
  waits again if it has to (with the loop stopped, that settles soon), before anything is frozen. That
  wait lasts two minutes at the most (`QUIET_AFTER_STOP_SECONDS`), as a busy window would otherwise keep
  bosun's own work off for as long as its turn runs: past it, it says so, starts that work again, waits
  for a quiet moment with everything running, and plans again, as after the first wait.
- **bosun's own work** is stopped after the rehearsal, where the hold begins: the watchdog's and
  learning's launchd jobs unloaded, and the check-in loop's process ended. The morning job is left, as it
  runs once a morning. Before anything stops, a mark of what runs (the jobs loaded, and whether the loop)
  goes into a file of its own, `update/own-work.json` in the state folder, not into `update.json`: an
  older version's write gate, putting a freeze back, rewrites the record with only the fields it knows.
  However the conversion ends short of the switch, a put back included, the work the mark names starts
  again as it was, once, and the mark goes: each job loaded from its plist as it is on disk (`start_jobs`:
  nothing rendered, so the old code runs under the plists it had, and the morning job, or one you unloaded,
  is left alone), and the loop restarted by the new version's `refresh --checkin-loop`, whose pane runs the
  install path's `bosun`, which leads to the old code again by then (the checkout, or `legacy-*` after the
  swap); a loop that runs by then (one never stopped, or started since by `bosun start` or the morning
  job) is left to its tick, as a restart would stop it mid-tick. After a crash, the next update does it,
  once it has put the freeze back, whoever wrote the record since, and also with no record at all (moved
  aside, or deleted). That next update comes from running the install line again, as the bosun installed
  then is the old checkout, which has no `bosun update`; what the update says on a failure or a Ctrl-C
  names the install line wherever the installed bosun is a checkout from before versions. Past the
  switch, the release reloads the jobs and restarts the loop, and removes the mark.
- **The swap is one call:** the link is made at the legacy folder's name and exchanged with the checkout
  (Linux's `renameat2` with `RENAME_EXCHANGE` does the same), so the checkout is never under another name,
  nor the install path missing. A link a swap cut short left there is replaced.
- **From the switch on, the record names the legacy folder** as the version updated from, in the record
  write of `switched`, so a put back leads the link there; before the swap, a checkout is never made a
  link. The update after the conversion is an ordinary one, from a version folder. Its prune deletes the
  legacy checkout unless something of yours is in it, or the installer did not make it, and an older
  version unless a change or a commit of its own is in it (lib/checkout_work.py, shared with uninstall),
  which its summary says. The record names the legacy folder once, and the swap puts the checkout there.
- **Temporary links** that a switch cut short leaves beside the install (`.bosun.link-*`, links only) are
  swept with the prune, once older than the update that sweeps them.

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

### Going back, and trying a branch

Designed 2026-11-18, for an install that follows `main` and takes every merge (the user's decision of that day
is under [Decided by the user](#decided-my-calls-and-what-is-left-to-the-builder)), and built the same day
(`lib/update_back.py`, the command; `lib/update_back_plan.py`, its decisions, pure; `migrations.revert_config`).

`bosun update --back` returns to the version the last finished update came from, one step, never further.
`bosun update --branch <name>` makes the install follow a branch of its origin and updates to its head. Both
take `--dry-run`, `--yes` and `--json` as `bosun update` does: in a terminal they show their plan and ask
first, without one they stop at the plan, and `--json` prints the update's one object. Run inside one of
bosun's windows, both are refused as an update is, but for a dry run.

**Going back is an update in reverse, run by the version installed.** That version knows the formats and
rules its data is in, and holds the record of the update that brought it, so `--back` hands over to no
other version: the wrapper fetches nothing and runs its own `lib/update_back.py`. It carries out an update
from the version installed to the one the last update came from, through the update's own machinery:
`update.lock` and the run marker, an update cut short carried on or put back first, the hold, the freeze
only when it restores, the one-step switch, the put back of a failed freeze, the release, the windows
brought along and the summary. Its record is `update.json`, at the update's own steps only, with a field
`kind: "back"`: after the switch the older version is the installed one, and it reads the record (its
write gate, its status, its `bosun doctor`), so a step it did not know would refuse every write ([The record
only grows](#cut-short)). Every version since versions side by side knows the steps a back uses.

**Which update it goes back over** (`lib/update_back_plan.py`, pure, from the record):

- the record at `done`, or at `agents` with windows still waited for, which the back takes over, as the
  next update does;
- an update recorded `failed` or `restored` left bosun where it was, so the back goes back over the update
  before it: each update's record now keeps the last finished one's from when it started (`previous`), so a
  rehearsal that failed the day after never takes the way back away;
- a back that failed or was put back: the update it tried to go back over (its own `previous`, which is that
  update), so `--back` can be run again;
- a back at `done` or `agents`: refused, so it never swings back and forth: "bosun already went back from
  c41b186 to 95545b0 on 2026-11-18; bosun update goes forward again";
- no record, or none that finished: refused, "no update has finished here: there is nothing to go back to".

Refused too, before anything changes: the install not leading to the version that update installed (the
link moved by hand: going back is then by hand), and a version to go back to that is a checkout from before
versions (`legacy-*`, which has no hold or write gate: by hand, as [the guide](../updating.md#going-back)
says).

**Where the facts come from.** The record of the update gone over: both versions' folders and commits, its backup,
its migrations (`migrated`) and what it wrote to `bosun.env` (`config_changed`, the plan's `renames` and `kept`).
The backup's manifest, whose `from` and `to` must be the record's commits. Each version's stores' formats and
rules versions, read from its source, as the plan reads the rules versions (`update_facts.source_formats`: no code
of the older version runs in the back's process). The older version's folder stays until the next update; if it is
gone, the back adds it again from `bosun.git` at its whole commit (`git worktree add --detach`, as the guide's
path by hand does), fetching that commit from the origin first if `bosun.git` no longer has it (no terminal to ask
in, a transfer slower than 1 KB/s for 30 seconds given up on, as the wrapper's fetch, and two minutes at the most,
as `update.lock` is held). A dry run adds no folder: it reads that version's formats, rules, keys and prompts from
`bosun.git` at its commit (`git show`, `update_facts.Committed`), and its plan's `unknown` says what it cannot tell
until going back adds the folder (what each window gets, the jobs, what that version needs of this Mac); with the
commit not in `bosun.git` either, it says that going back fetches it first, and that until then it cannot tell that
version's formats and rules, so what is restored. A backup whose manifest names another update is said once, and
only where something needs that backup.

**What it restores, part by part.** What was written since the update stays wherever the older version can
read it, each part decided on its own:

- *A store* (the ledger, the knowledge base) is restored from the update's backup when its format on disk is
  not the older version's (a migration ran) or its rules version differs (`RULES_VERSION`,
  `KG_RULES_VERSION`). Nothing refuses a journal under newer rules: the older version's rebuild checks every
  change under its own rules and keeps what they refuse as a conflict, unapplied
  ([sync](sync.md#decisions-2026-11-04)), so items written since could rebuild otherwise; hence the restore.
  It puts back what a failed freeze puts back of that store (`update_backups.FROZEN`: the ledger's files,
  journal and `replica.json`; the knowledge folder), never the working files (the events queue, signals, the
  Slack watcher's state), which stay as they are, as a failed freeze leaves them.
- *`bosun.env`*, when the update wrote it (keys renamed, kept values added), has the update's own changes
  taken back, line by line, where they still read as it wrote them: a renamed key gets its old name back, a
  removed key's lines come back from the backup's copy, and a kept value goes with the comment above it.
  A line changed since is left as it is, and the plan says what the older version reads instead. With
  nothing changed since, the result is the backup's copy (a unit test holds that undoing what
  `migrations.write_config` wrote gives back what it read). The file is kept first as
  `bosun.env.pre-back-<time>`, as an update keeps `.pre-update-`. Keys added at their defaults need nothing:
  the older version does not read them.
- A part that needs neither is kept, with what was written since. With nothing to restore, a back only
  switches versions and brings the windows along.

**What the plan says before it asks.** A back's plan is made as an update's is, from the version installed
to the older one (each window compared with the older version's `launch --fingerprint`, its jobs, its
preflight, the prompts of both), with the data as it will be once restored, so no migration and no
store-newer blocker. Its own part, `back`, says:

- which update it goes back over ("the update of 2026-11-18 14:02, 95545b0 -> c41b186");
- for each store and `bosun.env`, kept ("format 2 and rules 1 in both: kept, with what was written since"),
  or restored and why ("format 2 -> 1: restored from that update's backup");
- for each store restored, what restoring loses: how many changes were written since the backup (the
  journal's position now against the backup's, file by file), the time of the last, and for the ledger the
  items they touch ("12 changes since 14:02, the last at 15:40, to a12, a15, d3"; the knowledge base's as a
  count only, as its changes can name people); or "nothing written since: restoring loses nothing". They
  are not deleted: the back takes a backup first (below), but going forward again does not bring them back;
- `bosun.env`'s lines taken back, and those left as they were changed;
- what the update asked me to change in my own files, if anything: the older version reads them as before
  that change, so undo it if I made it;
- the windows, the jobs to reload and to unload, the prompts that differ, the console, the handed tasks and
  what stops it, as an update's plan.

**The steps.** An update's, with the data's part changed:

1. The plan and the go-ahead ("Go back now? [y/N]").
2. Rehearse: every file of the backup that a restore puts back checked against its manifest (as
   `update_backups.restore` checks before it writes), and `bosun.env`'s change worked out; `rehearsed`.
   Nothing is migrated, so no copy is made.
3. Hold bosun's own background work (`holding`).
4. With a part to restore, the freeze, under both stores' locks: `marked`; `backed-up`, a backup of the back's
   own of everything a backup holds (`<time>-<newer>-<older>`), naming what a put back restores (the parts it
   is about to change); `migrated`, each store restored from the update's backup and `bosun.env` taken back
   (`restored` and `config_changed` in the record); `switched`, the link to the older version; `checked`
   (the restore reads every file it put back against the update's manifest; the older version's `bosun
   doctor` in the release checks the rest). A failure puts the back's own backup back and the link at the
   newer version (`update_gate.put_back_held`, unchanged). `update.freeze` takes the steps it runs under the
   locks: an update's migrate, or a back's restore.
   With nothing to restore there is nothing to freeze, as both versions read the stores as they are: the back
   records `checked`, and the release switches.
5. The release, as an update's. Three of its parts came with the back and apply to every update ([the
   steps](#the-steps), 5): it begins by making sure the install leads to the version updated to (a no-op after a
   freeze switched it), so a back cut short anywhere from `checked` is carried on by the next `bosun update` like
   an update; the launchd jobs that run a bosun command (`job_kinds`: `bosun`) which the version updated from lists
   and the one updated to does not are unloaded and their plists deleted (a newer version's job would run a command
   the older one lacks), never the menu bar app's Open at Login job (`app`: it runs the app's own binary, and no
   reload makes it again), and none at all when either version cannot say what it lists; and the menu
   bar app is brought along only when the version updated to wants an app at least as new as the one installed. So
   a back leaves the app as it is: it reads only what `bosun ... --json` prints, whose fields only grow, and takes
   one an older bosun does not send as its default (as it does `starting`); a menu item whose command the older
   bosun lacks fails with that command's error, and the next update brings the app to what its version wants.
6. Bring the windows along, as an update does, by its table: restarted once idle where the launch differs
   from what the older version would launch, a re-read where only the orchestrator's prompts differ, taking
   stock first where only background work runs, the ones waiting for me handed to the check-in loop, and the
   next update taking over those still owed. The prompts are the update's, word for word ("bosun was updated
   from c41b186 to 95545b0; ..."): a window restarted on the older version runs its policy, which knows them by
   how they start.
7. The summary.

**The record, the summary and the next update.**

- A back's record is `update.json` with `kind: "back"`, `previous` (the record gone over: every record keeps
  the last finished one from when it started, as `carried` holds one, its own records left out), `restored`
  (what each store restored, as the step learnt it) and the usual fields, from the newer version to the older.
  A release's `unloaded` keeps the jobs it unloaded. All new fields are optional, and old code ignores them.
- Its summary: "bosun went back: c41b186 -> 95545b0 in 31s, over the update of 2026-11-18 14:02"; Data, each
  store kept or restored, with what restoring lost and the backup that keeps it; Config, `bosun.env` taken
  back and its copy; the prompts that differ; the jobs reloaded and unloaded; Agents; what was found after
  the switch; the backup; and "Going forward again: bosun update". It is saved as `back-summary.md`, never
  `summary.md`: beside the back's own backup, or, when it restored nothing and took none, beside the backup of
  the update it went back over. A back that leaves windows to the check-in loop (one waits for me) saves it
  then too, with a line naming the windows still brought along, as that loop runs the installed version, the
  older one, which may know no back: such a loop finishes the back as an update, and saves an update's summary
  as `summary.md` in the back's own backup (an "updated" headline, the guide's way back), or none when the back
  took no backup; neither replaces `back-summary.md`. A newer run that finishes it saves `back-summary.md` again,
  whole. `bosun doctor` lists nothing for it: a back's record asks no change of my files and adds no key. An
  update's summary ends "Going back: bosun update --back (docs/updating.md#going-back)".
- Once the back is done it records the version it left as skipped (`update-skip.json`, as `--skip` does,
  under the name `--check` gives it on the channel: its release tag, or on `main` its short commit), so
  `--check` and the menu bar app no longer offer it, and offer a newer one as usual. The older version reads
  that file as it is.
- The next `bosun update` goes forward again, to the newest the channel has, which can be the version just
  left: a skip never stops an update you run, and its plan says it in a line ("bosun update --back left this
  version on 2026-11-18 15:42"), so going forward to it again is never a surprise.
- To the menu bar app a back is an update like any other: "An update is running" while it runs, and its
  record as the status's `update.last`.

**`bosun update --branch <name>`**, in the wrapper of the version installed:

1. The name checked (`git check-ref-format --branch`), then the origin asked (`git ls-remote --heads`): a
   branch it does not have, or an origin it cannot reach, is refused before anything changes.
2. As on the `main` channel: the head fetched into `bosun.git`, and its version folder made.
3. The target checked against the data, before handing over. This is no branch's own: every update runs it, so a
   revert on `main` that takes a format back is caught the same way. The target's stores' formats and rules
   versions (from its source) are compared with the data's (its formats on disk, the installed version's rules
   versions): a store the target would read in an older format, or under older rules, is refused, with nothing
   changed but the folder (which a dry run makes too): "the head of try-x (abc1234) has the ledger at format 1,
   and the data is at format 2: going back past a migration or a rules change is bosun update --back's, to the
   version the last update came from". The target's own plan already stops on a store newer than it knows
   (`update_plan.GOING_BACK`, in every version since the plan), but says "not supported" and knows nothing of
   rules; this check says what to do. (`update_facts.older_than_data`, which the wrapper runs as
   `lib/update_back.py target-check`.)
4. Unless `--dry-run`: `bosun.branch` set in `bosun.git`, and, where the channel is not `main`,
   `BOSUN_UPDATE_CHANNEL=main` written by `bosun settings set` (its writer and its `bosun.env.pre-init-<stamp>`
   copy), said in a line: "bosun follows <branch> from now on (bosun update --branch main follows main
   again)".
5. Handed over to the branch's version, whose plan asks as any update's does. Said no to, or blocked, the
   install still follows the branch, at the version it had.

`--branch` with `--back`, `--check` or `--skip` is refused. A branch deleted on the origin once it merged:
the next `bosun update` cannot fetch it, and says to run `bosun update --branch main`. Following a branch
never changes the origin: a local branch not pushed is the install line's
(`BOSUN_REPO=<path> BOSUN_BRANCH=<branch>`), which points `bosun.git`'s origin at that path until it is set
again, and the guide says so.

**The contract.** `--back --json` and `--branch --json` print the update's object (`contract` 1), which only
grows: the plan gains `back` (null for an update), `unload` and `left_by_back`, the record `kind`, `previous`,
`restored` and `unloaded`,
the summary its back's part, and `tools/contract_samples.py` writes the fixtures again. The app runs neither,
and `menu_status.CONTRACT` does not change.

**What a back cannot make right**, accepted:

- A window the back waits for (busy, or waiting for me) runs its hooks, which the newer version wrote, on
  the older one until it restarts: a hook whose command line the older version does not take fails. No older
  version can be taught a newer one's lines; the windows whose launch differs are the ones restarted.
- A process of the newer version still running (a hook's detached child, a console started by hand) that
  writes to a store the back restored finds it older than its code. The write gate runs it again through the
  install link, as for a newer store, where the link leads to another version than the writer's own; it
  refuses as before only where it does not (a checkout run on data not yet migrated). A write under the
  newer rules into a store restored for its rules alone is kept, as a conflict if the older rules refuse it.
- The working files written since (events, signals) are kept, and nothing versions them: the older version
  reads what it can of them.
- One store restored and the other kept can leave a fact written since that cites a ledger item now gone.
- The older version's `bosun uninstall`, from before backs, does not know `bosun.env.pre-back-<time>` (its file
  table lacks the row), so `--purge` leaves the config folder with that file in it, and says so as a file it does
  not know; delete it by hand, or uninstall from a version that knows it.
- A back finished by an older version's check-in loop (above) saves an update's summary of it as `summary.md`
  in the back's own backup, which says "updated"; the back's own `back-summary.md` beside it is the one to read.

**Testing.** Unit tests (`tests/unit/test_update_back_plan.py`, `test_update_back.py`, and the general changes
in `test_update.py`, `test_update_gate.py`, `test_update_facts.py`, `test_update_lookup.py`, `test_migrations.py`):
which update a back goes back over, for every shape of record; each refusal (nothing finished, a second back, the
install led elsewhere, a `legacy-*` version, a restore whose backup is gone); each part's decision over format,
rules and `bosun.env`; what restoring loses; `bosun.env` taken back with and without changes since; a back that
keeps everything (no freeze), one that restores a store, one whose restore fails while writes are paused (put
back) and one whose backup is found damaged (the rehearsal fails); the plan's and summary's text; the target
checked against the data; the release's link, jobs to unload and app rule; the write gate both ways and for a
checkout; a skipped head of `main`. Smoke sections, on installs of their own made from the update's origin (the
`update-upstream` section's versions): `update-back` (over the format change from v1 to v2, v1's folder removed first, which the dry run does
not add again and reads from `bosun.git`, and going back does: the ledger restored, the write made since in the
back's own backup, `bosun.env` taken back, a second `--back` refused, the version left skipped, forward again with
the plan's line; with the backup gone, blocked; over v2 to v3, which changes nothing the data is read by: a ledger
and a knowledge write kept, no freeze and no backup, the summary beside the update's backup) and `update-branch`
(an unknown branch and a bad name refused with nothing changed, `--json`'s object, a dry run that writes nothing,
the plan said no to while the install follows the branch, the update to its head, a branch older in format
refused pointing at `--back` (one older in rules goes through the same target check, in the unit tests), `--branch
main`). A back cut short at each step is not killed in
the smoke suite: what carries it on is the update's own (`resume`, and the release that leads the install), unit
tested. In the VM, `tests/work_e2e.sh` keeps its path by hand (the fallback when the newer version's own update is
what broke) and adds `bosun update --back` twice: over the update to the next version (format 2: the ledger
restored, a ledger write made since in the back's backup, an ontology write kept), and over an update to the
version after (`tests/sandbox/update_patch.sh --again`: the same format), which restores nothing and keeps both
writes. `tests/install_e2e.sh` (`mac run`) does `--branch` to a branch it pushes to its local origin, `--branch
main`, and the releases again.

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
  - an idle, a `shell` and a busy window, and one waiting at a prompt: the update ends its wait once only
    that one is left, the status calls no update running, a second update takes the window over, and a
    tick restarts it once it is answered;
  - a restart whose launch is refused: refresh exits 5 with the launch's own error;
  - a hook and a ledger write arriving during the freeze: they wait, and nothing is lost;
  - a migration that fails: everything is back as before;
  - a `kill -9` at each step: the next run finishes or undoes it;
  - a `kg forget` after an update deletes its backup and says so; one cut short is finished,
    backups included, by the next write; and an update stops while one is cut short;
  - stand-ins for launchd.
- **The VM** (`tests/sandbox.sh mac work`; what it settled: [Settled in the VM](#settled-in-the-vm)).
  The release converted to this checkout, then a working day and `bosun update` to the next version
  mid-afternoon, while sessions are busy, a background wait is running and stand-in Slack events arrive
  during the hold. It checks that:
  - no event, write or record was lost;
  - each window resumed, with its background work started again;
  - the summary is right;
  - the backup restores, and the previous version runs on it;
  - after a person is forgotten, no backup or rehearsal copy holds their id.

  It also measures the freeze on a year of data.

- **`tests/sandbox.sh mac live claude|codex`** tries the take-stock and update prompts with the real
  agents (about a dollar a run), and whether a running Claude picks up a changed command file, with
  free-text cases for the prompts in `tests/llm/`. On Claude Code the orchestrator puts a question to me
  (its question tool) just before the update: the update ends without waiting for it, says so, the status
  calls no update running, and once the question is answered the check-in loop brings it along and the
  record reaches `done`.

### Settled in the VM

What the runs on a real Mac showed (`tests/sandbox.sh mac work`, `mac live claude|codex`; docs/development.md
says what each does). Run on 2026-11-08, on macOS in Tart, with Claude Code 2.1.308 and the Codex Homebrew
installed that day.

- **The freeze is short.** A year of acme work (`tests/sandbox/acme_year.py`, through bosun's own
  commands) leaves 6 907 ledger writes in a journal of 14 375 changes (6.3 MB), `ledger.json` at 308 KB,
  1 259 knowledge writes (1 824 changes, `facts.jsonl` 831 KB) and its git history. On that, the
  mid-afternoon update paused writes for 612 to 1 267 ms in six runs (`freeze_ms`; the most with the smoke
  suite running on the host beside the VM), far under the 10 s that the post-call approval hooks allow, so
  their fallback (saving their record for the next write) is not built. The conversion of the release, with
  little data, paused them for 154 ms. From its start to its last window's answer, the mid-afternoon update
  took 39 to 48 s and the conversion 18 to 24 s in those runs, as the first line of each summary says
  ("bosun updated: … in 43s"); with the real agents (`mac live`), 27 and 33 s on Claude Code and 24 s on
  Codex by their summaries (40 to 41 s by the scenario's own clock, which looks every 10 s).
- **Restoring fits a hook's budget.** On that year's backup, restore's own check of every file against its
  manifest (`_verify`) takes 288 ms (389 files, 24 MB), 214 ms (290 files, 20 MB), 339 ms (521 files,
  29 MB) and 127 ms (62 files, 12 MB), and flushing each file of the backup to disk 22 to 29 ms (taking a
  backup does not flush its clones; this is what it would cost). A whole restore into a copy of the
  stores, flushing the drive (`F_FULLFSYNC`) after each file it put back, took 603 ms, 2 899 ms and, with
  the host busy, 7 531 ms (257, 314 and 413 files put back or deleted), against the 8 s of a recovering
  approval hook: nearly all of it those flushes, about 10 ms a file on the host too. Restore now hands
  each file and its folder to the drive (`fsync`) and flushes the state and config folders' drives, and
  any other it wrote to, once each at its end, before the restore is recorded (one cut short before that
  is redone by the next, which puts back whatever does not read as the backup has it, and flushes even
  when that is nothing): 443 ms for 623 files put back or deleted, and 198 ms for
  28 put back and 124 deleted, counted apart in the last run.
- **A read-only file is put back as one.** The first measurement failed: git keeps its objects and
  commit-graph files read-only, and `restore` opened its copy for writing to flush it. It now copies the
  file with its mode and times (`copy2`), then hands it to the drive through a read-only descriptor, so
  they are on disk with its content once restore flushes the drive (`_put_back`, with a unit test). git's
  maintenance can rewrite such a file after a knowledge write let go of the lock, so a put back can meet
  one.
- **A write after a cut short rebuilds the whole journal.** Once a cut leaves `ledger.json` behind its
  journal, the next write rebuilds it from every change (214 to 455 ms on that year), as `ledger validate
  --journal` does; nothing is lost. Fifty writes of each store were cut at random from their start to past
  a write and that rebuild (0 to 657 ms): 27 of the ledger's and 26 of the knowledge base's printed their
  id first, and each of those was in its store after the next write, as was every approval the journal
  held. A long-lived install pays the rebuild once per crash. (A first run cut only in the first 300 ms,
  which on a year of data is before any write prints its id.)
- **launchd for real.** During the conversion the watchdog's and learning's jobs are really unloaded and
  loaded again afterwards (`launchctl print` all along). A conversion whose migration fails while writes
  are paused loads them again from their plists as they were on disk, byte for byte.
- **The check-in loop.** The conversion ends the loop in its real `checkin` window with SIGTERM and the
  release starts it again on the new version (`ps` shows the version folder's `bosun-checkin --loop`).
  After a failed conversion is put back, the loop started again runs the old code: the checkout's own
  `bosun-checkin`.
- **The swap.** `RENAME_SWAP` exchanges the checkout and the link in one call on APFS: the checkout is the
  same folder (inode) as `legacy-<12>`, the install path was never missing in 8.6 million looks while it ran,
  and no `.bosun.link-*` is left; one planted an hour before the next update is swept by its prune.
- **The upgrade from the release converts.** A release installed by its own `get.sh` is a checkout, set
  up by its `install.sh` link; this checkout's `get.sh` run again takes the conversion path, and each of
  bosun's windows on the release (no launch records) is restarted as one whose launch changed, in its own
  conversation, and answers. Its summary names the release, the backup and where the checkout is kept.
- **A window opened by hand** (a plain shell in bosun's tmux session) is left alone by the conversion and
  by the mid-afternoon update: the same pane, no `bosun launch` in it, not in the record, and the
  summary says so.
- **Nothing is lost mid-afternoon.** With a ledger write, a knowledge write, a typed prompt (the
  UserPromptSubmit hook) and a Stop hook arriving every second, and one of each the moment writes are
  paused (54 of each in all, some beginning at `backed-up`, `switched` and `checked`): each is there once,
  the typed prompts as events too; both journals and the fact log begin as they did; nothing bosun said
  to a window is recorded as mine. Each hook command of the previous version's launch settings (20, with
  the approval hooks on), given its own event's payload, runs on the new one. Slack messages that came during the hold are queued by the watcher's next run, each once.
- **The table holds.** The orchestrator kept running and re-read `policy.md`; the idle window was
  restarted; the window waiting on a background `sleep` took stock, was restarted and started the `sleep`
  again from its note; the busy one was waited for and restarted once its turn ended.
- **Going back works as [the guide](../updating.md#going-back) says**, its commands unchanged: the older
  version's folder (or `worktree add` of its whole commit, from the summary's 7 characters), the backup
  put back entry by entry, the link led back, `install.sh`, `bosun start`; the backup, put in a scratch
  folder, also passes the previous version's `ledger validate --journal`. `bosun update` then takes it
  forward again, migrating again.
- **A forget reaches the backups.** After the updates, a person forgotten leaves nothing in any backup or
  rehearsal copy (`grep -r` for their id, name and fact), and the forget says how many backups it deleted.
- **git keeps tokens out of its errors.** git 2.54 (Apple Git-157) prints a failed clone or fetch of
  `https://0000tok@example.invalid/...` without the user and password, and so does `get.sh`, which shows
  URLs the same way; `get.sh` needs no filter.
- **A running Claude does not pick up a changed command file.** A Claude started with `--plugin-dir`
  through a link answered its command from the first file after the link was moved to another folder (in
  each of seven live runs, Claude Code 2.1.308), and again after the file in the folder it started from
  (`v1`) was changed in place (in the last three: fix round 1's `mac live claude` and `mac live codex` and
  fix round 2's `mac live claude`; the four before changed the other folder's file, which shows
  nothing). It reads its plugin's commands once, at start; so a change to `chores.md` stays a launch
  input, which restarts the orchestrator. Its writes there are none: a version folder, while Claude runs
  with its plugin from it, reads clean as an update's prune (`version_work`) and `bosun uninstall`
  (`versions_work`) read it, and uninstall deleted every version once it waited for the windows' last
  hooks (below). (`checkout_work`, for a checkout from before versions, names the shared `bosun.git` as
  another worktree of a version, so it is not what a version is checked with.)
- **The prompts' words work with a real Claude.** The take-stock prompt (`TAKE_STOCK_PROMPT` in
  `lib/update_agents.py`: "bosun will restart this session for an update (… -> …), which ends your
  background work. List each background task still running and what you would do when it finishes, and
  save the list with `bosun update note --window <w> --stock` (the list on stdin; if nothing runs any more,
  save "nothing running"). Then stop and wait; start nothing new.") led a repo session waiting on a
  background command to save a list naming it and what to do when it ends. After the restart, the update
  prompt (`UPDATE_PROMPT`: "bosun was updated from … to …; this session was restarted on the new version.
  Check for anything missed just before: … Before the restart you listed this background work; start
  again what still needs doing: … Then continue what you were doing, and send a one-line status with
  `bosun update note --window <w> --status` (on stdin).") led it to check its ledger item, start the
  command again and say so in its status. The words are left as they are. A session judges what still
  needs doing: a command it was told was only a placeholder it listed as such and did not start again.
- **Codex keeps background work its session file does not show.** A Codex repo window asked for a
  background command kept it running after its turn while its status read idle (its session file records
  only idle or busy, [harnesses](harnesses.md)). So an update leaves a Codex window running when nothing
  it was launched with changed, as here, but one whose launch changed would be restarted at once, ending
  that work without taking stock. Asked by hand in the update's own words, Codex listed the command with
  `--stock` and, restarted with the update prompt, started it again and answered with `--status`.
- **The orchestrator may read the install.** The re-read prompt names the changed
  files in the install (`~/.local/share/bosun/orchestrator/policy.md`), and Claude Code asks "Allow this
  read outside the working directories?" before it reads there, even in auto mode: the orchestrator stopped
  at that question and the update waited for it (it never answers for me). The orchestrator may now read
  the install and its versions folder, and only read them: `Read(//<path>/**)` rules added to its settings'
  own allow list (`install_read_settings` in `lib/common.sh`; the merge adds to a settings file's
  permission lists and never replaces them). Not working directories (`--add-dir` or
  `additionalDirectories`), which would let it edit there, and whose path inside the install would put
  every file of it in the launch's fingerprint. With the rules alone, the next run's orchestrator re-read
  `policy.md` through the link and answered, with no question, and the update took 27 s.
- **Uninstall waits for the windows' last hooks.** In three of four runs, `bosun uninstall` kept the
  install and said to run it again: after it stopped the windows, `logs/` and `signals/` came back in the
  state folder once deleted. A run listing the processes and the files while it stopped them showed the
  writer: each Claude Code, ending, starts its async hooks in a session of their own about 1.3 s after its
  window stopped, `bosun hook signal --window <w>` and its python, which append the window's
  `signals/<w>.jsonl` (`lib/signals.py`) and log to `logs/hook-<date>.log` (the folder made again by
  `load_config`). Uninstall now waits for them ([uninstall](uninstall.md#the-order)); with the real Claude
  it removed the install and every version, and nothing came back after the deletion. That run also showed
  Claude Code starting `caffeinate -i -t 300` as it ends, in its window's session, and leaving it running:
  waited for, it held uninstall 15 s and was named as a step not done, so a process an agent starts in its
  window's session as it ends is not waited for. The next `mac live claude` showed it with a real Claude:
  as the windows stopped, the exporter's Claude started `caffeinate -i -t 300` in its window's session
  after 1.3 s, which was not waited for, and each Claude its `bosun hook signal` in a session of its own
  after 1.5 s, which were; the files went at 2.2 s and none came back, with nothing said but "removed".
  In the run with a Codex repo window, uninstall waited out the 15 s for that window's background `sleep
  300`, which Codex had started in a session of its own and which outlived the window, and named it as a
  step not done; then it removed the install and every version. What a window started and still runs at
  the bound is now said as a note instead ("still running, started in the exporter window: sleep 300 (pid
  16856); bosun did not stop it."), and the uninstall ends as it does.
- **A reinstall's `init` adds its newer keys.** An update never writes a new key (its default applies),
  so an install from an older release lacks the keys a newer `init` writes; `bosun uninstall` then the
  install line adds them to `bosun.env` (at their defaults, under "Added by `bosun init`", the file as it
  was kept as `.pre-init-*`). Nothing else of the settings changes.

## Decided, my calls, and what is left to the builder

Decided by the user:

- backup before anything changes, a check after, and everything put back on failure;
- a migration with each format change;
- going back means installing the older version and restoring the backup, and is not supported to
  versions older than today;
- going back keeps what was written since the update where it is safe (2026-11-18, refining the line
  above): `bosun update --back` returns to the version the last finished update came from, one step, never
  further, and restores the backup only for what that update changed that the older version cannot read (a
  store's format or rules version, keys it added or renamed in `bosun.env`); otherwise it switches back and
  keeps everything. Its plan says which, and what restoring would lose, before it asks. `--back` and
  `--branch` take `--dry-run`, `--yes` and `--json` as `bosun update` does;
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
- the update waits for busy windows, and Ctrl-C hands them to the check-in loop, as it does by itself
  once only windows waiting for me are left; the next update takes over the windows the last one still
  waits for;
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
- restoring done by the update itself only, with no `--restore` command in the first version (since:
  `bosun update --back`, [Going back, and trying a branch](#going-back-and-trying-a-branch), whose own calls
  are in that section);
- updating to the head of the tracked branch (releases came later: [Channels](#channels));
- the summary also saved next to the backup.

Decided while building it:

- **The hold** is the record's step: from `holding` up to `released`, and only while the update holds
  `update.lock` and within the hour. bosun's own background work asks before each run and skips it; a
  run already going finishes, its writes waiting on the freeze like any other's.
- **The update's record**, `update.json`: the update's id, both versions' folders and commits, its
  step, when it started, when its hold ends, the backup and what of it a put-back restores, the
  windows still to bring along, when the update's own run handed them to the check-in loop
  (`handed_over`), the last update's record whose windows it took over (`carried`) and which of them it
  took (`taken_over`), the changes to my own
  files, how long writes were paused
  (`freeze_ms`, from the stores' locks taken to their release), the error, and the findings after the switch (`bosun doctor`'s, and a job that
  could not be reloaded); and for the summary, the plan, the migrations that ran, what changed in
  `bosun.env`, the jobs reloaded and what `bosun doctor` failed at while planning. Each step is recorded before it starts, and the backup once it is taken, so
  no step past `backed-up` is recorded without it.
- **The launchd jobs:** a job is reloaded only where its rendered plist differs or it is not loaded,
  and each rendered plist is checked before the job is touched. One that cannot be rendered, or renders
  invalid, blocks the plan, and in the release it is a finding while the installed job stays loaded. The job
  that runs `bosun morning --now` (`<label>.now`, `templates/launchd-now.plist`) is one of them, always wanted:
  an update from a version without it loads it, and its `ProgramArguments` name the install link like the
  others'. The update itself is not run through a job like it: it reloads changed plists, which could boot the
  job it runs in out, so the menu bar app starts `bosun update --yes` as a detached child instead
  ([menu-bar.md](menu-bar.md)). An update whose release changes the `.now` plist boots out a `.now` run that is
  waiting on the update's hold, as it does for the morning job; that run is not carried on, and Start again starts it.
- **The plan before the switch** asks the new version: its `launch --fingerprint` reads the files
  under the install from its own folder. One of bosun's windows with no launch record counts as changed.
- **A window whose launch the new version would refuse**: its fingerprint reports a refusal (exit 4, from
  `launch_refused`: a plugin list over an `installed_plugins.json` that does not parse, a settings scope that
  would widen its connectors, an agy window given a plugin list), and its last stderr line is the reason that
  launch would record; any other failure of the fingerprint (a crash, a timeout) counts as changed only. A restart
  would stop a working agent and leave it unable to start, so the window is left running (`leave`, with the
  reason in the plan's `refused`), and the plan and the summary say "left running, as it would not start on
  the new version: <why>; fix that, then bosun refresh <window>" ("not restarted, as ..." for one whose agent
  had exited: the plan's `stopped`). The app reads it as `leave`. A takeover never restarts one the last update
  still waits for, whatever it began (`take_over`'s `refused`): its notes stay, it is not listed as owed, and the
  last update's summary marks it with the same line.
- **A window bosun did not start**: one `bosun sessions` lists outside the window list (`extra`)
  with no launch recorded, as a shell or a Claude opened by hand in bosun's tmux session, is left as it
  is, whatever it does, and the plan says so ("not started by bosun: left as it is", the summary "left
  as it is (bosun did not start it)"). Restarting one would replace your terminal, or your own Claude,
  with a bosun launch in a conversation it never had. `refresh --update-restart` refuses one, as `launch
  --fingerprint` does. A window `send --new` opened has a launch recorded, and is compared as bosun's;
  in a conversion, one an older version opened has none, so it is left running too, which is safe: its
  hooks take the older command lines.
- **The rehearsal's copy** is taken under both stores' shared locks, so it is whole; writes wait only
  for the clones.
- **A store's check** after the migrations fails the update on an error (writes refused, or a
  rebuild that would lose data); a warning is no reason to put the data back.
- **Bringing the agents along** is the record's step `agents`, after `released`: `lib/update_agents.py`
  decides, over the windows' statuses and an Actions interface, its only file the update's record (each
  thing done recorded as it is done); `lib/update_windows.py` carries it out (bosun's commands, the wait
  and Ctrl-C), and `lib/update_notes.py` keeps the notes. Every window the plan gives
  something to do is pending (not those left alone, and not the `checkin` window, whose loop the release
  restarted), with the kind the plan lists for it (never guessed from a later listing), until it answers
  with `bosun update note --window <w> --status`. A turn decides again from each window's status then, by
  the table above, and does each thing once: the record keeps each window's kind, the plan's treatment
  and what was done to it
  (`windows`), and the files the orchestrator re-reads (`reread`), written as each is done. So the
  update's own wait (a turn every 5 s) and, once it handed them over, the check-in loop's turn (`bosun
  update advance`, each tick while windows are pending), which take `update.lock` in turn, never repeat
  one; the loop's turn does nothing while an update runs. Once the update's own run handed them over, it
  records when (`handed_over`). Who holds `update.lock` is never guessed from the record: each holder says
  so in a marker of its own, its pid in `update/run` (an update, from before it waits for the lock until it
  ends, and its update's id once its record has one) or `update/turn` (the check-in loop's turn, written
  before it tries the lock). A marker is owned by the process that holds an exclusive lock on its lock file
  beside it (`update/run.lock`, `update/turn.lock`) for the marker's whole life, and is live only while that
  lock is held: the system drops it when its owner dies, where the pid a marker names can be any process's
  after a crash and a restart. So of two at once only one owns it, a stale one (its lock free) is replaced, it
  is removed as it ends while its lock is still held, and the lock files stay (one removed while another
  process opens it would let two owners lock apart). The turn's marker counts as the holder only with no
  other update's marker live. An update runs (`update_state.update_runs`) while its marker is live, or while the lock is held with
  no live turn marker (a writer putting back a freeze); so the status calls neither the loop's turn an
  update running nor a takeover planning before it writes its record none. The record's pending windows are
  the running update's own wait only when the marker names its id; a record at `agents` whose update is not
  running is shown as handed over, whatever `handed_over` says. The next
  update waits for the loop's turn (`TURN_SECONDS`), which gives way after the window it is on once the
  update's marker is there, leaving the rest pending for it to take over; if it still times out, its
  refusal says the check-in loop's turn holds the lock.
- **The turn's cases.** A restart that refresh leaves running (busy again, someone typing: exit 3) or that
  fails otherwise (a split window, an agent that would not stop) is tried at the next turn; a prompt `bosun
  send` could not deliver is sent again. A restart whose launch failed is final: once refresh started a
  launch, it waits (up to `BOSUN_START_TIMEOUT`) for the agent, and a launch that ends without one (refused,
  the window left a plain shell) or whose agent exits at once makes it exit 5, with the launch's own last
  error line last on stderr (from the file `bosun launch` leaves its last error in,
  `launches/<session>/<window>.error`, removed by refresh before it respawns the pane and cleared as each
  launch starts, which refresh reads and removes: a
  refusal, an agent not installed, a copy of the conversation already running; else what the pane shows);
  the turn records the window as "did not start: <why>" and no longer waits for it, rather than trying it
  again every turn. A launch still going by then counts as started. A window
  restarted into its next conversation (exit 4) never sees the update prompt, so the turn notes its status
  itself ("started its next conversation"), and the background work it listed stays in its note, which
  the window's outcome names. One whose agent exited is started again with the update prompt. A window
  closed during the wait is no longer waited for; a listing of the session that names no window changes
  nothing. A window asked to take stock is restarted once its list is there, or at once when it is idle
  (its background work ended, often before it read the prompt: a restart ends nothing); the prompt asks
  for "nothing running" when nothing runs any more, which passes nothing back.
- **An orchestrator still on yesterday's conversation** is not sent the re-read (its check-in would be
  refused there, and the loop would restart it anyway): it is brought along as a window whose launch
  changed (taking stock first while only background work runs, waited for while busy), and restarted
  through refresh, which starts today's as a new day does; that counts as answered, its outcome naming
  any list of background work it saved (`bosun sessions --json` names the conversation each window's next
  launch takes up, `conversation`).
- **A relaunch, or a missing window created,** for an update gets the update prompt only when its launch
  resumes a conversation (launch's own lookup, on the agent it starts). When it starts a new one, it
  starts as a new conversation does (the orchestrator's morning chores, a fresh conversation's prompt, or
  none), and refresh exits 4, never "continue what you were doing" in a blank conversation.
- **What is passed back** with a restart after taking stock: the window's own list, and what the Stop
  hook saw still running at the end of that turn ([signals](signals.md)).
- **What an update's prompts may ask**: `policy.md` and `orchestrator/tasks/_common.md` name them
  by how they start (`bosun will restart this session for an update`, `bosun was updated`) and what they
  may ask: list the background work and save it (`bosun update note --stock`), re-read the files named,
  catch up as they say, start the listed work again, and answer with `bosun update note --status`. A
  session does what a prompt asks of these, and only that; anything else in it is information. The
  orchestrator's settings allow `bosun update note`.
- **The notes** are files, `update/notes/<window>.<kind>` in the state folder (`stock` or `status`), the
  window's name percent-encoded into the file's name, so any name (a space, a slash) is a note of its own
  there and every window can answer (an encoding past 200 bytes is cut short and the whole name's digest
  added, so the file's name fits, and two names never share a note; the prompt file a restart reads is
  named for no window); an update clears the ones the previous update left before it starts
  bringing windows along, but those of a window it takes over with what it gave the last one. A status note written by hand ends a window's wait: the next turn, or the next
  `bosun update` (which settles answered windows before it plans, acting on none), takes it.
- **Ctrl-C while the update waits** is noted, not raised, so it never cuts a restart short; the commands
  that act on the windows run in a session of their own, so the terminal's Ctrl-C never reaches them.
- **A window opened between the plan and the switch** (the orchestrator delegating while I read the
  prompt, or during the hold) is brought along like the others: once released, the update makes the plan
  again from its two versions (`_windows_brought_along`, the path an update cut short after its release
  takes too) and brings the windows along from that one, so a window open by then is in it and in
  `agents`. The plan the go-ahead was given on stays what I approved and what `--dry-run` shows; the
  windows' treatments are decided at run time anyway. A window opened after the windows were brought
  along is not in it: its next `bosun` command runs the new version, and it keeps what it was started
  with.
- **An update cut short** after its release (at `checked` or `released`) brings the windows along from a
  plan made again from its two versions, saying what came of each; Ctrl-C there ends the command. One
  cut short at `agents` leaves the windows still waited for to the check-in loop (or to `bosun update
  advance` by hand, when no loop runs).
- **The next update takes over the windows the last one still waits for,** rather than being blocked by
  them (one left at a permission prompt would otherwise hold every later update until answered). Windows
  answered by a note are settled first, as before. Its plan lists those it will take over ("Still owed
  from the last update, which this one takes over"), by the same decision the takeover makes over the
  plan's treatments (`update_agents.taken_or_left`), and in one line those it will leave alone ("Also owed
  by the last update, and left alone, as ..."), so it never promises a restart it will not make; its record
  keeps the last one's (`carried`). Once it switched,
  each of them joins its own bringing-along, decided from its status and the new plan, which compares what
  it was launched with (its launch record, still from before the last update) with the newest version: a
  window owed a restart restarts when that differs (unless the newest version reverted it: then leaving it
  alone is right, as a running agent runs bosun's commands through the install link); what it already gave
  is kept (asked to take stock, the list it saved, which is passed back at its restart), unless the last
  update had restarted it already, which the new update's restart replaces; one the new plan leaves alone
  still gets what the last update began, or its answer is still waited for; an orchestrator owed a re-read
  re-reads both updates' files. A window the last update asked to take stock, which the new plan only asks
  to re-read, never gets its saved list back: accepted, as nothing restarts it, so its background work was
  never ended. The new record keeps which windows it took over (`taken_over`: each of the last one's
  windows it brings along, one the last had restarted and it restarts afresh too), and the last update's
  summary is then finished and saved next to its backup: each of those carried into the next update, one
  closed since closed, and any other "left alone by the next update: nothing it was launched with differs
  from the newest version". The new summary says "owed by the last update too" of those it took over. An
  update that fails, or is put back, before that gives them back: the last record is written again as it
  was (`update_state.carried_back`, by the next `bosun update` or the check-in loop's turn), so they stay
  the loop's; so does one killed before its freeze (`planned`, `rehearsed`, `holding`) once nothing holds
  the lock and no update's marker is live, which the loop's turn records `failed` first. An update that
  finds such a record when it holds the lock (the loop having given way to its marker) ends it `failed`
  itself before it writes its own, and takes over the windows it carried. `update.lock` is
  still taken in turn by the update and the loop's `bosun update advance`.

Left to the builder:

- upcasting or rewriting, case by case;
- the update prompt's exact words, settled in the VM ([above](#settled-in-the-vm));
- saving hooks' records to disk while a lock is busy, if the measured freeze needs it: it does not
  (612 to 1 267 ms on a year of data).

Later, each a pull request of its own:

- **Readers during a freeze.** Writes check the store before they change it (`lib/update_gate.py`), but
  readers do not (`locked_ledger(write=False)`, then `load()`): old code still running after the switch,
  `review-watch`'s targets, the guard's read for `--approvals` or a running console, reading a ledger a
  migration reshaped, fails with an error instead of running again through the install link as a refused
  write does. Gate them too, at the first migration that reshapes a store.
- **A conversion restart under a changed `BOSUN_HARNESS`.** A release's windows have no launch records,
  so a conversion restarts each with the agent the config names now; if `BOSUN_HARNESS` changed since the
  window started, it lands in a blank conversation of the other agent, with the update prompt. Take the
  running agent's (`agent_session_field <pid> harness`) when nothing is recorded (`libexec/bosun-refresh`).
- **`lib/update.py` in its steps and its conversion.** Over 1 100 lines; split into `update_steps`,
  `update_convert` and `update`, in a pull request of its own after this one, with no change in what
  they do.
- **Kept stores on a fresh install.** `bosun uninstall` keeps the ledger and the knowledge base; an
  install of a newer version over them refuses their writes until they are migrated, and `bosun update`
  there finds nothing to update. `init` should run the migrations of kept stores, with a backup, before
  the first start.
- **Codex background work.** Bosun cannot see a Codex window's background commands, so a Codex window
  whose launch changed is restarted without taking stock (see [Settled in the VM](#settled-in-the-vm)).
  It needs Codex to say what runs.

## Docs to change when it is built

Changed with the build and the runs in the VM:

- `docs/setup.md` (updating and going back, and that a forget deletes the backups);
- `docs/knowledge.md` and the deletion bullet of `docs/design/knowledge.md` (a deletion deletes
  the update backups);
- `docs/commands.md`;
- `README.md`;
- the `get.sh` header;
- the file tables in `lib/uninstall_plan.py` (now `lib/file_tables.py`);
- `docs/development.md` (the VM scenario);
- `CLAUDE.md`: a new common change, "a data format change", which needs a version bump, a migration
  and a fixture.
