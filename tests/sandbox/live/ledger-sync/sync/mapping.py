"""How an exported invoice becomes a ledger entry."""

from __future__ import annotations

from typing import Any


# The ledger account of each country's sales.
ACCOUNTS = {"DE": "4000", "FR": "4010", "GB": "4020", "NL": "4030", "US": "4040"}


def to_entry(invoice: dict[str, Any]) -> dict[str, Any]:
    """The ledger entry of an invoice or credit note: whole cents, a credit note negative."""
    cents = sum(round(line["price"] * 100) * line["quantity"] for line in invoice["lines"])
    return {
        "reference": invoice["number"],
        "account": ACCOUNTS[invoice["country"]],
        "cents": cents,
        "memo": f"credit for {invoice['credits']}" if invoice.get("kind") == "credit_note" else "invoice",
    }
