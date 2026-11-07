#!/bin/bash
# Usage: tests/sandbox/update_patch.sh [--failing] <copy of bosun>
#
# Makes "the next version" of bosun out of a copy of it, for the update's tests (tests/smoke.sh,
# "bosun update"): the ledger's format goes to 2, with the migration that takes it there, one line
# of the orchestrator's policy changes, and the format's digest is recorded under 2
# (tests/unit/test_store_formats.py). Only the copy changes; commit it to have the new version.
#
# The migration records the field `update_patch` in ledger.json, stamped as a write saves it, so the
# ledger still passes its own check. On the real data, never on a rehearsal's copy, it first sleeps
# UPDATE_PATCH_SLEEP seconds (default 0): bosun itself has no such pause, so this is how a test acts
# while writes are paused.
#   --failing  the migration changes ledger.json, then fails, on the real data only (the rehearsal's
#              copy passes): a failure inside the freeze, which the update puts back
set -euo pipefail

usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; }
FAILING=0
if [ "${1:-}" = "--failing" ]; then
  FAILING=1
  shift
fi
case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
  "")
    usage >&2
    exit 2
    ;;
esac
TREE="$1"
[ -f "$TREE/lib/ledger.py" ] || {
  echo "update_patch.sh: $TREE is not a copy of bosun" >&2
  exit 2
}

python3 - "$TREE" "$FAILING" <<'PY'
import sys
from pathlib import Path

tree, failing = Path(sys.argv[1]), sys.argv[2] == "1"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit(f"update_patch.sh: {path} does not hold {old!r} once")
    path.write_text(text.replace(old, new))


replace_once(tree / "lib" / "ledger.py", "\nLEDGER_FORMAT = 1\n", "\nLEDGER_FORMAT = 2\n")

fail = (
    '    if not rehearsal:\n'
    '        raise MigrationError("update_patch: the migration of the ledger failed on purpose")\n'
    if failing
    else ""
)
migration = f'''def _update_patch(ctx: Context) -> int:
    """tests/sandbox/update_patch.sh: the ledger from format 1 to 2, the field update_patch added."""
    import json as _json
    import os as _os
    import time as _time

    rehearsal = ctx.state_dir.parent.name == "rehearsal"
    if not rehearsal:
        _time.sleep(float(_os.environ.get("UPDATE_PATCH_SLEEP") or 0))
    path = ctx.state_dir / "ledger.json"
    data = _json.loads(path.read_text())
    saved = ledger.ledger_changes.JOURNAL in data and not ledger.edited_outside(data)
    data["update_patch"] = "migrated"
    if saved:
        ledger.stamp(data, ledger.saved_position(data))
    path.write_text(_json.dumps(data, indent=2, sort_keys=True))
{fail}    return len(data.get("items", {{}}))


REGISTRY: tuple[Migration, ...] = (Migration("ledger", 1, 2, _update_patch, "{{n}} items migrated"),)'''
replace_once(tree / "lib" / "migrations.py", "REGISTRY: tuple[Migration, ...] = ()", migration)

policy = tree / "orchestrator" / "policy.md"
lines = policy.read_text().splitlines(keepends=True)
lines[0] = lines[0].rstrip("\n") + " (the next version)\n"
policy.write_text("".join(lines))

sys.path.insert(0, str(tree / "lib"))
import ledger  # noqa: E402  (the patched copy's)

digests = tree / "tests" / "unit" / "test_store_formats.py"
text = digests.read_text()
start = text.index('    "ledger": {\n')
end = text.index("    },\n", start)
entry = f'        2: "{ledger.format_digest()}",  # pragma: allowlist secret\n'
digests.write_text(text[:end] + entry + text[end:])
PY
