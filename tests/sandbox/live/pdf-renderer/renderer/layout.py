"""The page layout of an invoice, as plain text columns (the PDF engine takes this text)."""

from __future__ import annotations

from typing import Any

from renderer.vat_breakdown import vat_breakdown


WIDTH = 48


def line_row(description: str, amount: str) -> str:
    """A description on the left and its amount on the right, `WIDTH` characters wide."""
    room = WIDTH - len(amount) - 1
    return f"{description[:room]:<{room}} {amount}"


def render(invoice: dict[str, Any]) -> str:
    """The invoice as a page: heading, one row per line, then the VAT breakdown."""
    page = [f"Invoice {invoice['number']}".center(WIDTH), f"Customer: {invoice['customer']}", ""]
    page.extend(
        line_row(f"{line['quantity']} x {line['description']}", f"{line['price'] * line['quantity']:.2f}")
        for line in invoice["lines"]
    )
    page.append("-" * WIDTH)
    for rate, row in vat_breakdown(invoice["lines"]).items():
        page.append(line_row(f"VAT {float(rate):.0%} on {row['net']}", row["vat"]))
    return "\n".join(page)
