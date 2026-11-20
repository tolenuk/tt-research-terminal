"""Currency normalisation. LSE quotes in pence (GBp/GBX) become GBP via a 0.01 price factor."""

from __future__ import annotations

from obb_terminal.data.market import UnsupportedCurrency
from obb_terminal.domain.models import Currency

PENCE = frozenset({"GBp", "GBX", "GBx"})


def normalise(code: str | None) -> tuple[Currency, float]:
    """Return (display currency, factor to apply to price fields)."""
    if code in PENCE:
        return Currency.GBP, 0.01
    try:
        return Currency(code), 1.0
    except ValueError:
        raise UnsupportedCurrency(f"Unsupported currency: {code}") from None
