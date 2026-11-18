"""What each file bosun writes in its state, config and log folders is, by name: the file tables. Its kind
decides what `bosun uninstall` does with a file (lib/uninstall_plan.py) and whether an update's backup holds
it (lib/update_backups.py). The tables are a module of their own, which both import, so that an update, and
every write to a store through its gate, never loads the uninstall's plan. Pure. Design:
docs/design/uninstall.md.

A file bosun writes in one of these folders needs a row here, or uninstall leaves it behind (the smoke suite
fails on a file in the state folder that the table does not list).
"""

from __future__ import annotations

import fnmatch

import agent_state
import log_files


# What a file is decides what happens to it.
WORKING = "working"  # bosun's own: it goes
LEDGER = "ledger"  # your records: you decide
KNOWLEDGE = "knowledge"  # your records: you decide
BACKUPS = (
    "backups"  # the update's backups: each holds the ledger and the knowledge base, so they go with either
)
SETTINGS = "settings"  # what you configured: it stays, unless --purge
OURS_TO_LEAVE = "leave"  # in bosun's folder, but not part of running it: it always stays
UNKNOWN = "unknown"  # not listed below: it always stays


def agent_state_rows() -> tuple[tuple[str, str], ...]:
    """A row for each file an agent keeps in the state folder, and for a file there, the pattern its
    atomic write leaves when cut short."""
    rows: list[tuple[str, str]] = []
    for agent in agent_state.AGENTS.values():
        for name in agent.PROFILE.state_files:
            rows.append((name, WORKING))
            if "/" not in name:
                rows.append((f".{name}.*", WORKING))
    return tuple(rows)


STATE_FILES = (
    ("ledger.json", LEDGER),
    ("journal", LEDGER),  # each machine's changes to the ledger: your records
    # This machine's id in both stores' journals (the ledger's, the knowledge base's), classed with the
    # ledger: --delete-ledger deletes it even when the knowledge base stays, which then carries on as
    # a new replica from its next write.
    ("replica.json", LEDGER),
    ("ledger.json.bak", LEDGER),
    ("ledger.md", LEDGER),
    ("learn", LEDGER),  # what learning found and what you decided, which a later run goes by
    # The update's backups (lib/update_backups.py): each holds the whole ledger and knowledge base, so they
    # go when either is deleted (wanted_gone). A deletion of a person deletes them all.
    # A back's own backup is one of them, and so is the summary it saves beside the update it went back over
    # (back-summary.md, in that update's backup folder).
    ("backups", BACKUPS),
    ("ledger.json.tmp", WORKING),  # what a write cut short left
    (".replica.json.*.tmp", WORKING),  # what making replica.json left when it was cut short
    ("ledger.lock", WORKING),
    ("update.json", WORKING),  # the update's record (docs/design/update.md)
    ("update.json.tmp", WORKING),
    ("update.lock", WORKING),
    ("update-skip.json", WORKING),  # the release `--skip` said not to offer
    (".update-skip.json.tmp", WORKING),
    ("quit.json", WORKING),  # bosun is stopped until started (lib/quit_state.py)
    (".quit.json.*", WORKING),  # what writing it left, cut short
    ("login-reload", WORKING),  # a reload of the jobs, so the login job's run is not taken for a login
    ("loaded-*", WORKING),  # when its morning, learning and watchdog jobs were last loaded (reload_job)
    # the update's notes, own-work mark and prompt files, and who holds update.lock (`run`, `turn` and
    # their lock files `run.lock`, `turn.lock`: lib/update_state.py)
    ("update", WORKING),
    (".*.restoring", WORKING),  # what an update's restore was writing, cut short (lib/update_backups.py)
    ("agent-changes.json", WORKING),
    ("agent-setup.lock", WORKING),  # held while an agent's one-time setup is made
    ("agent-changes.lock", WORKING),  # held while agent-changes.json is written (lib/agent_changes.py)
    (".agent-changes.json.*", WORKING),
    ("alerted-*", WORKING),  # what the check-in loop left, before the watchdog
    ("alerts", WORKING),
    ("connectors.json", WORKING),  # the connectors' URLs the last launch found (lib/connectors_claude.py)
    (".connectors.json.*", WORKING),  # what a write cut short left
    ("console", WORKING),  # its token, its running lock with its port, the job's `wanted` marker
    ("conversations", WORKING),
    ("dry-runs", WORKING),
    ("events", WORKING),
    ("impact", WORKING),
    ("keep-awake-*.pid", WORKING),
    ("keep-awake-*.json", WORKING),  # when the hold ends, and its pid (bosun status --json)
    (".keep-awake-*.json.*", WORKING),  # what writing it left, cut short
    # what each window was launched with (lib/launch_record.py), and a launch's last error (<window>.error)
    ("launches", WORKING),
    ("logs", WORKING),
    ("login.json", WORKING),
    ("login.json.tmp", WORKING),
    ("login.lock", WORKING),
    ("prompts", WORKING),
    ("signals", WORKING),
    ("slack-watch", WORKING),
    ("stopped-*", WORKING),
    ("started-*", WORKING),  # when `bosun start` last made the session (the watchdog's gate on install day)
    ("wake-test.env", WORKING),
    ("watchdog.json", WORKING),
    ("watchdog.json.tmp", WORKING),
    ("watchdog.lock", WORKING),
    ("windows.lock", WORKING),
    ("init.lock", WORKING),
    (".DS_Store", WORKING),  # Finder's, if you ever looked in the folder
    *agent_state_rows(),  # the files each agent keeps here (its profile)
)
CONFIG_FILES = (
    ("knowledge", KNOWLEDGE),
    ("bosun.env", SETTINGS),
    ("sessions.yml", SETTINGS),
    ("setup.sh", SETTINGS),
    ("policy.md", SETTINGS),
    ("learn.md", SETTINGS),
    ("tasks", SETTINGS),
    ("workstreams.json", SETTINGS),
    ("*.pre-init-*", SETTINGS),  # what `bosun init` backed up
    ("bosun.env.pre-update-*", SETTINGS),  # what `bosun update` backed up
    ("bosun.env.pre-back-*", SETTINGS),  # what `bosun update --back` kept before it took changes back
    (".bosun.env.*.tmp", WORKING),  # what `bosun update` was writing, cut short (bosun.env, its copy)
    (".*.restoring", WORKING),  # what an update's restore was writing, cut short (lib/update_backups.py)
    ("forbidden-terms.txt", OURS_TO_LEAVE),  # guards commits to this repo; bosun runs without it
    (".DS_Store", WORKING),
)
# A log folder of its own (BOSUN_LOG_DIR outside the state folder): bosun's log names, not every *.log.
LOG_FILES = tuple(
    (pattern, WORKING)
    for pattern in (
        # A day's file, `<writer>-YYYY-MM-DD.log`, of bosun's own writers only (lib/log_files.py's WRITERS,
        # what the morning run prunes): another program's day files in a shared folder stay
        *(f"{writer}-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].log" for writer in log_files.WRITERS),
        *log_files.agent_logs(),  # what the agents write there (agy's logs, say: each one's profile)
        "hook-*.log*",
        "launchd.out",
        "launchd.err",
        "*.launchd.out",
        "*.launchd.err",
        ".DS_Store",
    )
)


def kind_of(name: str, table: tuple[tuple[str, str], ...]) -> str:
    return next((kind for pattern, kind in table if fnmatch.fnmatchcase(name, pattern)), UNKNOWN)
