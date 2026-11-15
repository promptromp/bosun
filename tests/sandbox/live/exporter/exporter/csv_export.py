"""CSV export of invoices for the customer's accounting system."""

from __future__ import annotations

from typing import Any


def as_csv(lines: list[dict[str, float]]) -> str:
    """One `price,quantity` row per line."""
    return "\n".join(f"{line['price']},{line['quantity']}" for line in lines)


def export_invoices(invoices: list[dict[str, Any]]) -> str:
    """One `number,customer,rows` row per invoice that has been issued. Drafts are left out here."""
    issued = [invoice for invoice in invoices if invoice.get("status") != "draft"]
    return "\n".join(
        f"{invoice['number']},{invoice['customer']},{len(invoice['lines'])}" for invoice in issued
    )
