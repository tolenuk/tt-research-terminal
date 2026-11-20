"""Dividend-derived metrics."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from obb_terminal.domain.models import Dividend


def ttm_total(dividends: Iterable[Dividend], today: date) -> float:
    """Sum of dividends with ex-date in the 365 days up to and including `today`."""
    cutoff = today - timedelta(days=365)
    return sum(d.amount for d in dividends if cutoff < d.ex_date <= today)


def ttm_yield(dividends: Iterable[Dividend], price: float | None, today: date) -> float | None:
    """Sum of dividends with ex-date in the last 365 days, divided by price (a fraction)."""
    if not price:
        return None
    return ttm_total(dividends, today) / price


def cagr(dividends: Iterable[Dividend], today: date, years: int = 5) -> float | None:
    """Annual growth of the trailing-12-month total over `years` years.

    None when the history does not reach back `years` years or either total is not positive.
    """
    divs = list(dividends)
    start = today - timedelta(days=365 * years)
    if not divs or min(d.ex_date for d in divs) > start:
        return None
    then, now = ttm_total(divs, start), ttm_total(divs, today)
    if then <= 0 or now <= 0:
        return None
    return (now / then) ** (1 / years) - 1
