"""The 12-row KPI table with good / neutral / weak marks (spec §4)."""

from __future__ import annotations

from obb_terminal.domain.models import Kpi, KpiFormat, Mark, ValuationInputs
from obb_terminal.domain.valuation import bvps, eps

YAHOO_NOTE = "Yahoo"
NEGATIVE_EARNINGS = "negative earnings"
NEGATIVE_EQUITY = "negative equity"
NEGATIVE_BOOK = "negative book value"
LOSS_MAKING = "loss-making"


def _lower_better(value: float | None, good: float, weak: float) -> Mark:
    if value is None:
        return Mark.MISSING
    if value <= good:
        return Mark.GOOD
    if value >= weak:
        return Mark.WEAK
    return Mark.NEUTRAL


def _multiple_mark(value: float | None, good: float, weak: float) -> Mark:
    """Valuation multiples: a value <= 0 means losses or shrinking earnings, so it is weak."""
    if value is not None and value <= 0:
        return Mark.WEAK
    return _lower_better(value, good, weak)


def _higher_better(value: float | None, good: float, weak: float) -> Mark:
    if value is None:
        return Mark.MISSING
    if value >= good:
        return Mark.GOOD
    if value <= weak:
        return Mark.WEAK
    return Mark.NEUTRAL


def _price_over(price: float, per_share: float | None) -> tuple[float | None, str | None]:
    if per_share is None:
        return None, None
    if per_share <= 0:
        return None, NEGATIVE_EARNINGS
    return price / per_share, None


def kpis(inputs: ValuationInputs, bond_yield: float | None = None) -> list[Kpi]:
    """`bond_yield` (ETFs only) defaults to the inputs' 10-year yield or the default."""
    if inputs.etf is not None:
        from obb_terminal.domain.etf import kpis_etf
        from obb_terminal.domain.valuation import default_assumptions

        y = bond_yield if bond_yield is not None else default_assumptions(inputs).etf.bond_yield
        return kpis_etf(inputs, y)
    i = inputs
    yahoo = YAHOO_NOTE if i.yahoo_cross_currency else None
    pe, pe_note = _price_over(i.price, eps(i))
    book = bvps(i)
    pb = i.price / book if book is not None and book > 0 else None
    pb_note = NEGATIVE_BOOK if book is not None and book <= 0 else None
    de = i.debt_to_equity
    de_negative = de is not None and de < 0
    payout = i.payout_ratio
    payout_negative = payout is not None and payout < 0
    dividend_mark = (
        Mark.MISSING if i.dividend_yield is None
        else Mark.GOOD if i.dividend_yield >= 0.03
        else Mark.NEUTRAL
    )
    return [
        Kpi("pe", "P/E", pe, KpiFormat.MULTIPLE, _multiple_mark(pe, 15, 30), pe_note),
        Kpi("forward_pe", "Forward P/E", i.forward_pe, KpiFormat.MULTIPLE, _multiple_mark(i.forward_pe, 15, 30), yahoo),
        Kpi("peg", "PEG", i.peg_ratio, KpiFormat.MULTIPLE, _multiple_mark(i.peg_ratio, 1, 2), yahoo),
        Kpi("ev_ebitda", "EV/EBITDA", i.ev_to_ebitda, KpiFormat.MULTIPLE, _multiple_mark(i.ev_to_ebitda, 10, 20), yahoo),
        Kpi("pb", "P/B", pb, KpiFormat.MULTIPLE, _multiple_mark(pb, 1.5, 5), pb_note),
        Kpi("roe", "ROE", i.return_on_equity, KpiFormat.PERCENT, _higher_better(i.return_on_equity, 0.15, 0.05)),
        Kpi("operating_margin", "Operating margin", i.operating_margin, KpiFormat.PERCENT, _higher_better(i.operating_margin, 0.20, 0.05)),
        Kpi("debt_to_equity", "Debt/Equity", i.debt_to_equity, KpiFormat.RATIO,
            Mark.WEAK if de_negative else _lower_better(i.debt_to_equity, 0.5, 2.0),
            NEGATIVE_EQUITY if de_negative else None),
        Kpi("current_ratio", "Current ratio", i.current_ratio, KpiFormat.RATIO, _higher_better(i.current_ratio, 1.5, 1.0)),
        Kpi("revenue_growth", "Revenue growth", i.revenue_growth, KpiFormat.PERCENT, _higher_better(i.revenue_growth, 0.10, 0.0)),
        Kpi("dividend_yield", "Dividend yield", i.dividend_yield, KpiFormat.PERCENT, dividend_mark),
        Kpi("payout_ratio", "Payout ratio", i.payout_ratio, KpiFormat.PERCENT,
            Mark.WEAK if payout_negative else _lower_better(i.payout_ratio, 0.60, 1.0),
            LOSS_MAKING if payout_negative else None),
    ]
