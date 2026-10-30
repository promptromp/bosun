#!/bin/bash
# A new user's machine, all fictional: nothing of bosun installed, three repos of the acme company,
# a month of Claude Code history in them (Monday to Friday, 09:10 to 18:40, most of it in api), and
# stand-ins for Claude Code and the GitHub CLI, both logged in.
set -uo pipefail
[ -e /etc/bosun-sandbox ] || {
  echo "$0 only runs in tests/sandbox.sh's container: it would change this machine" >&2
  exit 1
}

identity=(-c user.name=sandbox -c user.email=sandbox@example.com)
make_repo() { # <name> <origin>
  local path="$HOME/Development/$1"
  git init -q "$path"
  git -C "$path" remote add origin "$2"
  git -C "$path" "${identity[@]}" commit -q --allow-empty -m "first"
}
make_repo api git@github.com:acme/api.git
make_repo web https://github.com/acme/web.git
make_repo toy git@gitlab.example.com:me/toy.git

mkdir -p "$HOME/.local/bin"
cp "$(dirname "$0")/claude-stand-in" "$HOME/.local/bin/claude"
cp "$(dirname "$0")/gh-stand-in" "$HOME/.local/bin/gh"
chmod +x "$HOME/.local/bin/claude" "$HOME/.local/bin/gh"
echo '{"hasCompletedOnboarding": true}' >"$HOME/.claude.json" # a person went through the first-run screens

python3 - <<'HISTORY'
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

home = Path.home()
now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
for repo, per_day in (("api", 30), ("web", 2), ("toy", 0)):
    cwd = home / "Development" / repo
    folder = home / ".claude" / "projects" / str(cwd).replace("/", "-")
    folder.mkdir(parents=True)
    lines = []
    for ago in range(1, 29):
        day = (now - timedelta(days=ago)).replace(hour=9, minute=10)
        if day.weekday() >= 5:
            continue
        step = timedelta(minutes=(9 * 60 + 30) / max(per_day - 1, 1))
        for n in range(per_day):
            entry = {"type": "user", "cwd": str(cwd), "timestamp": (day + n * step).isoformat(),
                     "entrypoint": "cli", "message": {"content": "a fictional prompt"}}
            lines.append(json.dumps(entry, separators=(",", ":")))
    (folder / "session.jsonl").write_text("\n".join(lines) + "\n")
HISTORY
