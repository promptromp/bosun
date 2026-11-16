"""Credit notes: an invoice reversed, as negative amounts.

A credit note takes the next number of its customer's invoice sequence, so a customer's documents
(invoices and credit notes alike) are numbered 1, 2, 3 in the order they were made.
"""

from __future__ import annotations

from typing import Any


def next_number(customer: str, existing: list[str]) -> str:
    """The next document number of <customer>, from the numbers already issued (`ACME-0001`, ...)."""
    prefix = f"{customer}-"
    taken = [int(number[len(prefix) :]) for number in existing if number.startswith(prefix)]
    return f"{prefix}{max(taken, default=0) + 1:04d}"


def credit_note(invoice: dict[str, Any], existing: list[str]) -> dict[str, Any]:
    """The credit note that reverses <invoice>: its lines negated, numbered in the customer's sequence."""
    lines = [{**line, "price": -line["price"]} for line in invoice["lines"]]
    return {
        "number": next_number(invoice["customer"], existing),
        "customer": invoice["customer"],
        "kind": "credit_note",
        "credits": invoice["number"],
        "status": "issued",
        "lines": lines,
    }
