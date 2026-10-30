"""A small invoice exporter, entirely fictional: the project the live tests hand work on."""

from __future__ import annotations


def invoice_total(lines: list[dict[str, float]], tax_rate: float) -> float:
    """The total of an invoice's lines, each `price` times `quantity`, with tax added."""
    subtotal = sum(line["price"] for line in lines)
    return round(subtotal * (1 + tax_rate), 2)


def as_csv(lines: list[dict[str, float]]) -> str:
    """One `price,quantity` row per line."""
    return "\n".join(f"{line['price']},{line['quantity']}" for line in lines)
