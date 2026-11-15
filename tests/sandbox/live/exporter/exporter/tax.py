"""Tax rates by country, and how a tax amount is rounded."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


# The standard rate of the countries the exporter has customers in.
RATES: dict[str, str] = {"DE": "0.19", "FR": "0.20", "GB": "0.20", "NL": "0.21", "US": "0.00"}


def rate_for(country: str) -> float:
    """The tax rate of a two-letter country code; a country with no rate is an error, never zero."""
    try:
        return float(RATES[country.upper()])
    except KeyError:
        raise ValueError(f"no tax rate for {country!r}") from None


def round_tax(amount: str) -> str:
    """A tax amount to the cent, half up (0.125 is 0.13), as a string so that no float rounds it again."""
    return str(Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
