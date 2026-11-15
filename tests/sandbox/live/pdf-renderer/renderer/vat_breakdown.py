"""The VAT breakdown at the foot of an invoice: net and VAT per rate."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def vat_breakdown(lines: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Per VAT rate (as `0.19`), the net amount and the VAT of the lines at that rate, to the cent.

    A line is `price`, `quantity` and `vat_rate`; VAT is rounded per line, as the exporter rounds it, so the
    breakdown adds up to the invoice total.
    """
    rows: dict[str, dict[str, Decimal]] = {}
    for line in lines:
        rate = Decimal(str(line["vat_rate"]))
        net = (Decimal(str(line["price"])) * Decimal(str(line["quantity"]))).quantize(Decimal("0.01"))
        vat = (net * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        row = rows.setdefault(str(rate), {"net": Decimal("0.00"), "vat": Decimal("0.00")})
        row["net"] += net
        row["vat"] += vat
    return {rate: {key: str(amount) for key, amount in row.items()} for rate, row in sorted(rows.items())}
