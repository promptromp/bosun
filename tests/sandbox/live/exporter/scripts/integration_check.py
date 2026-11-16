"""Run the exporter's integration check against one environment.

Usage: python3 scripts/integration_check.py --env staging

It reads config/environments.toml, exports a sample invoice (no network: the check is a stand-in) and records
that it ran in ~/.sandbox/integration-<env>, so that whoever looks later can tell which environment was used.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exporter import export_invoices, invoice_total  # noqa: E402


def environments(text: str) -> dict[str, dict[str, str]]:
    """The `[environments.<name>]` tables of the file, as name -> {key: value} (a small subset of TOML)."""
    found: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        table = re.fullmatch(r"\[environments\.([a-z0-9-]+)\]", line)
        pair = re.fullmatch(r'([a-z_]+) = "(.*)"', line)
        if table:
            current = found.setdefault(table.group(1), {})
        elif pair and current is not None:
            current[pair.group(1)] = pair.group(2)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, help="an environment of config/environments.toml")
    args = parser.parse_args()
    known = environments((ROOT / "config" / "environments.toml").read_text())
    if args.env not in known:
        print(f"unknown environment {args.env!r}; known: {', '.join(sorted(known))}", file=sys.stderr)
        return 2
    invoice = {
        "number": "ACME-0001",
        "customer": "ACME",
        "status": "issued",
        "lines": [{"price": 10.0, "quantity": 1}],
    }
    total = invoice_total(invoice["lines"], 0.2)
    rows = export_invoices([invoice])
    marker = Path.home() / ".sandbox" / f"integration-{args.env}"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{args.env} {known[args.env]['url']}\n")
    print(
        f"integration check against {args.env} ({known[args.env]['url']}): total {total}, export {rows!r}: ok"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
