"""`bosun update --back`: back to the version the last finished update came from, one step, never further
(docs/design/update.md, "Going back, and trying a branch").

This runs in the version installed, which knows the formats and rules its data is in and holds the record
of the update that brought it: the wrapper (libexec/bosun-update) fetches nothing and runs this. A back is
an update in reverse, from the version installed to the older one, through lib/update.py's own steps and
record (update.json, kind BACK, at the update's own steps only, as the older version reads it once it is
installed): an update cut short carried on first, the plan (update_back_plan: which update it goes back over,
what each store and bosun.env need, what restoring loses), the go-ahead, the record (`previous` is the update
gone over), a rehearsal (the backup checked against its manifest), the hold, and:

- with something to restore, the freeze: the back's own backup of everything, each store the older version
  cannot read restored from that update's backup, bosun.env's changes taken back (migrations.revert_config),
  the switch, `checked`; a failure puts the back's own backup back (update.freeze, put_back_held);
- with nothing to restore, `checked` at once: both versions read the stores as they are, and the release
  switches (update._lead_install).

Then the release, the windows brought along and the summary, as an update's; once it is done, the version it
left recorded as skipped (update_lookup.skip_left), so `--check` and the menu bar app no longer offer it.

Also `target-check <folder> <name>`, which the wrapper runs before it hands over to any version: whether that
version would read the data older than it is (update_facts.older_than_data), pointing at going back.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import install_paths
import knowledge
import knowledge_store
import ledger
import migrations
import update
import update_back_plan
import update_backups
import update_check
import update_facts
import update_gate
import update_plan
import update_state
import versions
from journal import Journal, JournalError
from update import Output, Said
from update_back_plan import BackPart, ConfigPart, StorePart
from update_facts import Folders, UpdateRefused, Versions, folders
from update_plan import Plan, VersionFacts
from update_state import UpdateState


ROOT = Path(__file__).resolve().parents[1]  # the version installed, which goes back
BACK_AHEAD = "bosun update --back --yes goes back without asking"
QUESTION = "Go back now?"
TARGET_CHECK = "target-check"
MISMATCH = "that update's backup ({backup}) is of another update ({found}): going back is by hand ({guide})"


def _journals(store: str, state_dir: Path, config_dir: Path) -> Journal:
    """<store>'s journal under the state folder <state_dir> and the config folder <config_dir> (the live
    ones, or a backup's state/ and config/)."""
    if store == "ledger":
        return ledger.journal_at(state_dir / "journal" / ledger.LEDGER_STREAM)
    return knowledge.open_journal(knowledge_store.Paths(config_dir / update_gate.KNOWLEDGE_DIR))


# ---- the facts ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Older:
    """The version a back goes back to: its folder; where its files are read (the folder, bosun.git at its
    commit for a dry run whose folder is gone, None when bosun.git lacks that commit too); whether its folder
    is there, so its own commands can be asked; and what a plan cannot tell without it."""

    root: Path
    source: update_facts.Source | None
    present: bool
    unknown: tuple[str, ...] = ()


@dataclass(frozen=True)
class Facts:
    """What a back is made from: the update it goes back over, the older version, its own part of the plan,
    bosun.env as it was before that update (None: not needed, or not there), the plan."""

    over: UpdateState
    older: Older
    back: BackPart
    before_env: str | None
    plan: Plan


def _over(state_dir: Path) -> UpdateState:
    """The update to go back over (update_back_plan.gone_over); UpdateRefused saying why there is none."""
    found = update_back_plan.gone_over(update_state.read(state_dir))
    if isinstance(found, str):
        raise UpdateRefused(found)
    return found


def _commit_of(folder: Path) -> str | None:
    """The commit checked out in <folder>; None where it is no checkout git can read (a link moved by
    hand)."""
    try:
        return versions.commit_of(folder)
    except RuntimeError:
        return None


def _installed_is(over: UpdateState) -> None:
    """UpdateRefused unless the install leads to the version <over> installed."""
    installed = update.installed(ROOT)
    if _commit_of(installed) != over.to_commit:
        raise UpdateRefused(
            update_back_plan.MOVED.format(installed=installed, to=update_back_plan.short(over.to_commit))
        )


# A fetch over the network: no terminal to ask in (a private https origin never prompts while update.lock is
# held), a transfer slower than 1 KB/s for 30 seconds given up on (versions.SLOW, as the wrapper's own fetch),
# and never longer than FETCH_SECONDS.
NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0"}
FETCH_SECONDS = 120


def _repo_of(folder: Path) -> Path:
    return folder.parent / install_paths.REPO


def _fetched(repo: Path, commit: str) -> None:
    """<commit> fetched into bosun.git (<repo>) from its origin; UpdateRefused saying why it could not be."""
    Output.say(f"Fetching {update_back_plan.short(commit)}, which bosun.git no longer has...")
    try:
        done = versions.git(
            *versions.SLOW,
            "--git-dir",
            str(repo),
            "fetch",
            "--quiet",
            "--no-tags",
            "origin",
            commit,
            env=NO_PROMPT,
            timeout=FETCH_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateRefused(f"could not fetch {commit}: it took over {FETCH_SECONDS} s") from exc
    if done.returncode != 0:
        lines = [line.strip() for line in done.stderr.splitlines() if line.strip()]
        why = update_check.strip_credentials(lines[-1]) if lines else f"git exited {done.returncode}"
        raise UpdateRefused(f"could not fetch {commit} from the origin: {why}")


def _add_again(folder: Path, commit: str) -> None:
    """The version at <commit> added again as <folder> from bosun.git (a worktree, as an update makes one),
    its commit fetched from the origin first if bosun.git no longer has it; UpdateRefused if it cannot be."""
    repo = _repo_of(folder)
    if not versions.has_commit(repo, commit):
        _fetched(repo, commit)
    try:
        done = versions.git(
            "--git-dir",
            str(repo),
            "worktree",
            "add",
            "--quiet",
            "--detach",
            str(folder),
            commit,
            timeout=versions.LOCAL_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateRefused(f"could not add {folder} again: git took too long") from exc
    if done.returncode != 0:
        raise UpdateRefused(f"could not add {folder} again at {commit}: {done.stderr.strip()}")
    Output.say(f"Added {folder} again, the version the last update came from")


def _unseen(older: Path, commit: str) -> Older:
    """For a dry run, the version at <commit> whose folder <older> is gone, never added again: read from
    bosun.git at that commit, or nothing when bosun.git lacks it (a back fetches it first)."""
    repo = _repo_of(older)
    short = update_back_plan.short(commit)
    if versions.has_commit(repo, commit):
        note = update_back_plan.NOT_ADDED.format(folder=older, commit=short)
        return Older(older, update_facts.Committed(repo, commit), False, (note,))
    return Older(older, None, False, (update_back_plan.NOT_FETCHED.format(folder=older, commit=short),))


def _older(over: UpdateState, dry_run: bool) -> Older:
    """The version <over> came from: its folder, added again if it is gone (a dry run adds none, and reads
    what it can of it from bosun.git: _unseen); UpdateRefused for a folder that is not that version."""
    older = Path(over.from_root)
    if not older.is_dir():
        if dry_run:
            return _unseen(older, over.from_commit)
        _add_again(older, over.from_commit)
    if _commit_of(older) != over.from_commit:
        raise UpdateRefused(
            f"{older} is not a checkout of {over.from_commit}; move it aside and run it again"
        )
    return Older(older, update_facts.Folder(older), True)


def _manifest(over: UpdateState) -> tuple[update_backups.Manifest | None, str]:
    """The manifest of <over>'s backup (None: it is gone, or is another update's), and why it is not that
    update's ("" when it is, or is gone)."""
    backup = Path(over.backup) if over.backup else None
    if backup is None or not backup.is_dir():
        return None, ""
    manifest = update_backups.read_manifest(backup)
    if (manifest.from_commit, manifest.to_commit) != (over.from_commit, over.to_commit):
        found = (
            f"{update_back_plan.short(manifest.from_commit)} -> {update_back_plan.short(manifest.to_commit)}"
        )
        return None, MISMATCH.format(backup=backup, found=found, guide=update_back_plan.GUIDE)
    return manifest, ""


def _written(store: str, where: Folders, backup: Path) -> update_back_plan.Since:
    """What <store> had written since <backup> (update_back_plan.since)."""
    try:
        now = _journals(store, where.state_dir, where.config_dir).changes()
        then = _journals(store, backup / "state", backup / "config").position()
    except JournalError as exc:
        raise UpdateRefused(
            f"the {store}'s journal cannot be read to say what restoring loses: {exc}"
        ) from exc
    return update_back_plan.since(now, then, items=store == "ledger")


def _stores(
    older: Older, where: Folders, over: UpdateState, manifest: update_backups.Manifest | None
) -> list[StorePart]:
    """Each store as the back finds it, with what was written since that update's backup where it is
    restored; none when the older version cannot be read (a dry run that would fetch it)."""
    if older.source is None:
        return []
    now = update_facts.store_formats(where.state_dir, where.config_dir)[0]
    then = update_facts.formats_in(older.source)
    rules_now, rules_then = update_facts.rules(ROOT), update_facts.rules_in(older.source)
    found = []
    for store in migrations.FORMATS:
        backup_format = manifest.formats.get(store, 0) if manifest is not None else None
        part = StorePart(
            store, now.get(store, 0), then[store], rules_now[store], rules_then[store], backup_format
        )
        if part.restore and manifest is not None:
            part = dataclasses.replace(part, written=_written(store, where, Path(over.backup)))
        found.append(part)
    return found


def _env_before(over: UpdateState, config_dir: Path, there: bool) -> str | None:
    """bosun.env as it was before <over> wrote it: the copy in its backup (<there>: that backup is <over>'s),
    or the copy it kept beside bosun.env (bosun.env.pre-update-<time>, named in its config_changed), which
    a kg forget leaves; None when neither is there."""
    copies = [Path(over.backup) / "config" / migrations.CONFIG_FILE] if there else []
    kept = update_back_plan.config_copy(over.config_changed)
    copies += [config_dir / kept] if kept else []
    found = next((copy for copy in copies if copy.is_file()), None)
    return found.read_bytes().decode("utf-8", "surrogateescape") if found is not None else None


def _config(over: UpdateState, config_dir: Path, there: bool) -> tuple[ConfigPart, str | None]:
    """bosun.env as the back finds it (whether that update wrote it, and what taking its changes back does),
    and bosun.env as it was before that update."""
    env = config_dir / migrations.CONFIG_FILE
    if not over.config_changed or not env.is_file():
        return ConfigPart(), None
    before = _env_before(over, config_dir, there)
    if before is None:
        return ConfigPart(changed=True, backup_there=False), None
    now = env.read_bytes().decode("utf-8", "surrogateescape")
    reverted = migrations.revert_config(now, before, update_plan.from_record(over.plan).keys_added)
    return ConfigPart(True, reverted.said, reverted.left), before


def _older_facts(older: Older, over: UpdateState, now: VersionFacts) -> VersionFacts:
    """The older version's facts, read from where its files are (no code of it runs here); a store whose
    format its source does not say is taken at <now>'s (the data's), and what cannot be read at all (a dry
    run that would fetch it) as <now>'s, so the plan says no change of it."""
    if older.source is None:
        return dataclasses.replace(now, commit=over.from_commit)
    then = update_facts.formats_in(older.source)
    return VersionFacts(
        commit=over.from_commit,
        formats={store: then.get(store) or found for store, found in now.formats.items()},
        rules=update_facts.rules_in(older.source),
        keys=update_facts.keys_in(older.source),
        prompts=update_facts.prompts_in(older.source),
    )


def _plan_unseen(versions_: Versions, where: Folders) -> Plan:
    """A dry run's plan when the older version's folder is gone: what the data and the prompts tell, and what
    stops it of the stores'; nothing of the windows, the jobs or what it needs, which only its own commands
    can say (its `unknown` says so)."""
    cut_short, unreadable = update_facts.deletion_cut_short(where.state_dir, where.config_dir)
    plan = update_plan.make_plan(
        versions_.old,
        versions_.new,
        commits=(),
        windows=(),
        jobs=(),
        renames=(),
        user_changes=(),
        in_flight=update_facts.in_flight(where.state_dir, where.config_dir),
        missing=(),
        deletion_cut_short=cut_short,
        console=update_facts.console_running(where.state_dir),
        steps=lambda *_: (),
    )
    found = (
        *update_facts.store_formats(where.state_dir, where.config_dir)[1],
        *unreadable,
        *update_facts.dangling_links(where.state_dir, where.config_dir),
    )
    return dataclasses.replace(plan, blockers=(*plan.blockers, *found))


def _plan(older: Older, over: UpdateState, back: BackPart, where: Folders) -> Plan:
    """The back's plan: an update's from the version installed to the older one (update_facts.plan_from),
    with the data as it will be once restored, so no migration; nothing written to bosun.env but what
    taking that update's changes back does; and its own part, and what stops it. With the older folder gone
    (a dry run), what can be told without it (_plan_unseen)."""
    old = update_facts.live_facts(where.state_dir, where.config_dir, ROOT)
    restored = {part.store: part.format_then for part in back.restored()}
    old = dataclasses.replace(old, formats={**old.formats, **restored})
    versions_ = Versions(old, _older_facts(older, over, old), ROOT, older.root)
    if older.present:
        plan = update_facts.plan_from(
            versions_, where.state_dir, where.config_dir, kept={}, renames=(), user_changes=()
        )
    else:
        plan = _plan_unseen(versions_, where)
    found = (*plan.blockers, *back.blockers())
    return dataclasses.replace(
        plan, keys_added=(), back=back.as_record(), blockers=found, unknown=older.unknown
    )


def facts(where: Folders, dry_run: bool = False) -> Facts:
    """What a back is made from, gathered: UpdateRefused where there is nothing to go back over, the install
    leads elsewhere, or the version to go back to cannot be had. A dry run adds no folder."""
    over = _over(where.state_dir)
    _installed_is(over)
    older = _older(over, dry_run)
    manifest, mismatch = _manifest(over)
    stores = tuple(_stores(older, where, over, manifest))
    config, before = _config(over, where.config_dir, manifest is not None)
    back = BackPart(over.id, over.started_at, over.backup, stores, config, over.user_changes, mismatch)
    return Facts(over, older, back, before, _plan(older, over, back, where))


# ---- the steps ----------------------------------------------------------------------------------------


def _restore(part: StorePart, backup: Path, where: Folders) -> str:
    """<part>'s store restored from that update's <backup> (what a failed freeze puts back of it), and
    its line."""
    update_backups.restore(backup, where.state_dir, where.config_dir, update_backups.STORE_PARTS[part.store])
    Output.say(f"  restored the {part.store} from the update's backup")
    return part.done_line()


def _config_taken_back(found: Facts, config_dir: Path, now: datetime) -> tuple[str, ...]:
    """That update's changes to bosun.env taken back (migrations.revert_written_config), the file kept first
    as bosun.env.pre-back-<now>; the summary's line, or none."""
    if not found.back.config.takes_back or found.before_env is None:
        return ()
    added = update_plan.from_record(found.over.plan).keys_added
    reverted = migrations.revert_written_config(config_dir, now, found.before_env, added)
    if not reverted.said:
        return ()
    Output.say("  took the update's changes to bosun.env back")
    copy = migrations.back_copy(config_dir, now).name
    return (f"{migrations.CONFIG_FILE}: {'; '.join(reverted.said)} (before: {copy})",)


def _restored(state: UpdateState, found: Facts, where: Folders) -> UpdateState:
    """The steps under the stores' locks (update.freeze), each recorded before it starts: `marked`;
    `backed-up`, the back's own backup of everything, named with what a put back restores; `migrated`, each
    store restored and bosun.env's changes taken back; `switched`, the link to the older version (with what
    they did); `checked` (the restore read every file it put back against the manifest)."""
    now = datetime.now(UTC)
    state = update._step(where.state_dir, state, "marked")
    state = update._step(where.state_dir, state, "backed-up")
    formats = migrations.store_formats(migrations.Context(where.state_dir, where.config_dir))
    backup = update_backups.take(
        where.state_dir, where.config_dir, state.from_commit, state.to_commit, formats, now
    )
    restorable = found.back.restorable()
    state = update._record(
        where.state_dir, dataclasses.replace(state, backup=str(backup), restorable=restorable)
    )
    state = update._step(where.state_dir, state, "migrated")
    restored = tuple(_restore(part, Path(found.over.backup), where) for part in found.back.restored())
    config = _config_taken_back(found, where.config_dir, now)
    state = update._switch(where.state_dir, state, restored=restored, config_changed=config)
    return update._step(where.state_dir, state, "checked")


def _rehearsed(state: UpdateState, found: Facts, where: Folders) -> UpdateState:
    """The rehearsal: every file of that update's backup a restore puts back checked against its manifest;
    `rehearsed`, or `failed` with what it found. Nothing is migrated, so no copy is made."""
    parts = [prefix for part in found.back.restored() for prefix in update_backups.STORE_PARTS[part.store]]
    if parts:
        Output.say("Checking the backup it restores from...")
        try:
            update_backups.check(Path(found.over.backup), parts)
        except update_backups.BackupError as exc:
            return update._step(where.state_dir, state, "failed", error=f"the rehearsal failed: {exc}")
    return update._step(where.state_dir, state, "rehearsed")


def _switched(state: UpdateState, found: Facts, where: Folders) -> UpdateState:
    """The hold, then with something to restore the freeze (_restored), else `checked` at once, as both
    versions read the stores as they are (the release switches); where it stopped otherwise."""
    hold_until = int(time.time()) + update_state.HOLD_SECONDS
    state = update._step(where.state_dir, state, "holding", hold_until=hold_until)
    if not found.back.restores:
        Output.say("Holding bosun's background work; nothing to restore, so writes go on.")
        return update._step(where.state_dir, state, "checked")
    Output.say("Holding bosun's background work; pausing writes to back up, restore, switch...")
    state = update.freeze(
        state,
        found.plan,
        where.state_dir,
        where.config_dir,
        frozen=lambda begun: _restored(begun, found, where),
    )
    if state.step == "checked":
        Output.say(f"Writes went on after {state.freeze_ms} ms.")
    return state


def carried_out(found: Facts, where: Folders) -> UpdateState:
    """Recorded from `planned` on (kind BACK, its `previous` the update gone over): the rehearsal, the hold,
    the freeze or none, the release, the windows brought along; or where it stopped."""
    Output.say("Asking bosun doctor of the older version what fails before going back...")
    before = update._doctor_fails(found.older.root)
    started = update.begun(
        found.plan, ROOT, found.older.root, before, where.state_dir, kind=update_state.BACK
    )
    state = update._runs_as(where.state_dir, update._record(where.state_dir, started))
    state = _rehearsed(state, found, where)
    if state.step == "failed":
        return state
    state = _switched(state, found, where)
    if state.step != "checked":
        return state
    Output.say("Releasing...")
    state = update.release(state)
    Output.say("Bringing the agents along...")
    return update._windows_brought_along(state)


# ---- the command --------------------------------------------------------------------------------------


def _show(found: Facts, as_json: bool) -> None:
    if not as_json:
        Output.say(update_back_plan.render(found.plan, found.back).rstrip("\n"))


def _back(where: Folders, args: argparse.Namespace, said: Said) -> int:
    """Under update.lock: the plan, shown; then, said yes to, carried out and summarized."""
    found = facts(where)
    said.plan = found.plan
    _show(found, args.json)
    if found.plan.blockers or not update._go_ahead(args.yes, BACK_AHEAD, QUESTION):
        return update.BLOCKED if found.plan.blockers else 0
    state = carried_out(found, where)
    if state.step not in update.ENDED:
        return update._failed(state)
    update.skip_left(where.state_dir, state)
    update._say_summary(where.state_dir, state, said)
    return 0


def _dry_run(where: Folders, args: argparse.Namespace, said: Said) -> int:
    """The plan alone; an update cut short is said first, never carried on here."""
    update._said_recorded(where.state_dir)
    found = facts(where, dry_run=True)
    said.plan = found.plan
    _show(found, args.json)
    Output.say("A dry run: nothing has changed.")
    return update.BLOCKED if found.plan.blockers else 0


def run(args: argparse.Namespace, said: Said) -> int:
    """`bosun update --back`: a dry run, or an update cut short carried on first and then, under update.lock
    (an update running is refused), the back."""
    where = folders()
    if args.dry_run:
        return _dry_run(where, args, said)
    with update_state.marking(where.state_dir, update_state.RUN_MARKER):
        if not update._carried_on(where.state_dir, said):
            return 0
        with update_state.one_update(where.state_dir, update_state.lock_wait(where.state_dir)):
            return _back(where, args, said)


def _said(args: argparse.Namespace, said: Said) -> int:
    """run, with what stops it said in one line (exit 1)."""
    try:
        return run(args, said)
    except update_state.RecordUnreadable as exc:
        print(f"bosun update: {update._unreadable(exc)}", file=sys.stderr)
    except (UpdateRefused, update_state.UpdateRunning) as exc:
        print(f"bosun update: {exc}", file=sys.stderr)
    except update_backups.BackupError as exc:
        print(f"bosun update: {exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print(
            "\nbosun update: stopped; run bosun update again: it carries on, or puts back, what it started",
            file=sys.stderr,
        )
    return 1


def target_check(argv: Sequence[str]) -> int:
    """`target-check <folder> <name>`: whether the version in <folder> (called <name>) reads the data older
    than it is, a line on stderr for it (exit 1), else nothing (exit 0)."""
    folder, name = Path(argv[0]), argv[1]
    where = folders()
    problems = update_facts.older_than_data(folder, name, where.state_dir, where.config_dir, ROOT)
    for line in problems:
        print(f"bosun update: {line}", file=sys.stderr)
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bosun update --back", allow_abbrev=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str]) -> int:
    """The command, and with --json one object on stdout on every path, as `bosun update` prints it."""
    if argv and argv[0] == TARGET_CHECK:
        return target_check(argv[1:])
    args = build_parser().parse_args(list(argv))
    Output.to_stderr = args.json
    said = Said()
    code = _said(args, said)
    if args.json:
        print(json.dumps(update.json_object(said.plan, update._record_now(), said.summary), indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
