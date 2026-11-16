"""Invoice totals.

The caveat everyone meets once: tax is rounded per line, not per invoice, so a total can differ by a cent
from the subtotal times (1 + rate). Finance compares exported amounts to the cent.
"""

from __future__ import annotations


def invoice_total(lines: list[dict[str, float]], tax_rate: float) -> float:
    """The total of an invoice's lines, each `price` times `quantity`, with tax added line by line."""
    return round(sum(round(line["price"] * (1 + tax_rate), 2) for line in lines), 2)
