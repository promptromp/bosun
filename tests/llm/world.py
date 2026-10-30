"""A scratch world for one real-Claude run: nothing it does can reach the live bosun.

- HOME, config and state dirs of its own. `bosun` on PATH is a wrapper that runs this
  checkout with that HOME, config and state, and BOSUN_SEND_DRY_RUN=1, and logs every call.
- Its own tmux server with the windows the fictional ledger names, each "running Claude" (a sleep
  with a Claude session record in the scratch HOME), so `bosun context` and `send --list` look
  like a live session.
- A fictional git repo (acme/exporter) with no reachable remote, as the repos dir and the repo
  session's working directory.

The real HOME is only used by Claude itself (login, and MCP servers with --with-mcp); its outward
tools are denied.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SESSION = "llmtest"
WINDOWS = ("orchestrator", "exporter", "exporter-tax", "exporter-pdf")
FAKE_CLAUDE_VERSION = "2.1.300"
FAKE_CLAUDE_SECONDS = 7200
REPO_FILES = {
    "exporter/sftp.py": (
        '"""Upload exported invoices to the bank SFTP server."""\n\n'
        "import os\n\n"
        "# One password shared by every customer's upload.\n"
        'SHARED_PASSWORD = os.environ["EXPORT_SFTP_PASSWORD"]\n'
    ),
    "exporter/discounts.py": (
        '"""Discounts on the lines of an exported invoice."""\n\n\n'
        "def apply(lines: list[dict[str, float]]) -> list[dict[str, float]]:\n"
        "    # Each discount is folded into the line it applies to.\n"
        '    return [{**line, "amount": line["amount"] - line.get("discount", 0.0)} for line in lines]\n'
    ),
    "exporter/credit_notes.py": (
        '"""Credit notes, exported like invoices."""\n\n\n'
        "def lines(note: dict[str, list[dict[str, float]]]) -> list[dict[str, float]]:\n"
        '    return [line for line in note["lines"] if "discount" not in line]\n'
    ),
    "README.md": "# exporter\n\nacme's invoice exporter (fictional test fixture).\n",
}
WRAPPER = """#!/bin/bash
# bosun for a scratch world: this checkout, scratch HOME/config/state, sends only logged.
printf '%s\\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")" >>"{calls}"
exec env HOME="{home}" BOSUN_CONFIG_DIR="{config}" BOSUN_STATE_DIR="{state}" \\
  BOSUN_LOG_DIR="{state}/logs" BOSUN_SEND_DRY_RUN=1 "{bosun}" "$@"
"""
ENV_FILE = """BOSUN_SESSION_NAME="{session}"
BOSUN_TMUX_SOCKET="{socket}"
BOSUN_ORCHESTRATOR="orchestrator"
BOSUN_ORCHESTRATOR_DIR="{orchestrator_dir}"
BOSUN_REPOS_DIR="{repos}"
BOSUN_REPO_MASK="acme/*"
BOSUN_CHECKIN_MINUTES="60"
BOSUN_NUDGE_AFTER_BUSINESS_DAYS="1"
BOSUN_MAX_NUDGES="2"
BOSUN_MAX_REVIEW_NUDGES="0"
BOSUN_REVIEW_ESCALATE_AFTER_NUDGES="3"
"""


@dataclass
class World:
    base: Path
    socket: str
    calls_seen: int = 0
    before: dict[str, Any] = field(default_factory=dict)

    @property
    def home(self) -> Path:
        return self.base / "home"

    @property
    def state(self) -> Path:
        return self.base / "state"

    @property
    def config(self) -> Path:
        return self.base / "config"

    @property
    def bin(self) -> Path:
        return self.base / "bin"

    @property
    def repo(self) -> Path:
        return self.base / "repos" / "exporter"

    @property
    def orchestrator_dir(self) -> Path:
        return self.base / "orchestrator"

    @property
    def calls_file(self) -> Path:
        return self.base / "bosun-calls.jsonl"

    def env(self) -> dict[str, str]:
        """The environment for Claude: the real HOME (login), the wrapper first on PATH."""
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BOSUN_", "AWS_"))}
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env.update(
            BOSUN_STATE_DIR=str(self.state),
            BOSUN_SEND_DRY_RUN="1",
            BOSUN_CONFIG_DIR=str(self.config),
        )
        return env

    def bosun(self, *argv: str, stdin: str | None = None) -> str:
        """Run the wrapper (as Claude would), failing loudly."""
        done = subprocess.run(
            [str(self.bin / "bosun"), *argv],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            env=self.env(),
        )
        if done.returncode != 0:
            raise RuntimeError(f"bosun {' '.join(argv)}: {done.stderr or done.stdout}")
        return done.stdout

    def ledger(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.state / "ledger.json").read_text())
        return data

    def backdate_messages(self, outreach_id: str, days: float) -> None:
        """Pretend the messages of <outreach_id> went out <days> ago (seeds only)."""
        data = self.ledger()
        moment = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        for message in data["items"][outreach_id]["fields"]["messages"]:
            message["at"] = moment
        (self.state / "ledger.json").write_text(json.dumps(data, indent=2, sort_keys=True))

    def snapshot(self) -> None:
        """Remember the ledger and the calls so far: what the run changes is measured from here."""
        self.before = self.ledger()
        self.calls_seen = len(self.calls())

    def calls(self) -> list[list[str]]:
        if not self.calls_file.exists():
            return []
        return [json.loads(line) for line in self.calls_file.read_text().splitlines() if line.strip()]

    def new_calls(self) -> list[list[str]]:
        return self.calls()[self.calls_seen :]

    def close(self, keep: bool) -> None:
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], check=False, capture_output=True)
        socket_dir = Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"  # noqa: S108  (tmux's own default)
        (socket_dir / self.socket).unlink(missing_ok=True)
        if not keep:
            shutil.rmtree(self.base, ignore_errors=True)


def build_world(base: Path, socket: str) -> World:
    world = World(base=base, socket=socket)
    for directory in (
        world.home / ".claude" / "sessions",
        world.state,
        world.config,
        world.bin,
        world.orchestrator_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    wrapper = world.bin / "bosun"
    wrapper.write_text(
        WRAPPER.format(
            calls=world.calls_file,
            home=world.home,
            config=world.config,
            state=world.state,
            bosun=ROOT / "bin" / "bosun",
        )
    )
    wrapper.chmod(0o755)
    (world.config / "bosun.env").write_text(
        ENV_FILE.format(
            session=SESSION, socket=socket, orchestrator_dir=world.orchestrator_dir, repos=world.repo.parent
        )
    )
    make_repo(world.repo)
    start_windows(world)
    return world


def git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True)


def make_repo(repo: Path) -> None:
    """A small fictional repo whose origin names acme/exporter but points nowhere reachable."""
    for name, text in REPO_FILES.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "exporter")
    git(repo, "checkout", "-q", "-b", "feature/work-in-progress")
    git(repo, "remote", "add", "origin", "https://github.invalid/acme/exporter.git")


def start_windows(world: World) -> None:
    """One tmux window per fictional window, each with an idle "Claude" (a registered sleep)."""
    for number, name in enumerate(WINDOWS):
        session_record = f'{{"pid":%d,"status":"idle","version":"{FAKE_CLAUDE_VERSION}"}}'
        record = (
            f"sleep {FAKE_CLAUDE_SECONDS} & printf '{session_record}' "
            f'"$!" >"{world.home}/.claude/sessions/$!.json"; wait'
        )
        place = (
            ["new-session", "-d", "-s", SESSION]
            if number == 0
            else ["new-window", "-d", "-t", f"={SESSION}:"]
        )
        subprocess.run(
            ["tmux", "-L", world.socket, *place, "-n", name, "-c", str(world.repo), "bash", "-c", record],
            check=True,
            capture_output=True,
        )
