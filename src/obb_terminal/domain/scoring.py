"""Outlook score (0-100): a transparent weighted mean of five components. Not a probability."""

from __future__ import annotations

from obb_terminal.domain.models import (
    Assumptions,
    Component,
    Score,
    ScoreBand,
    ScoreComponent,
    Valuation,
    ValuationInputs,
)
from obb_terminal.domain.valuation import FEW_ANALYSTS, NO_COVERAGE

LABELS: dict[Component, str] = {
    Component.UPSIDE: "Upside to fair value",
    Component.ANALYST: "Analyst view",
    Component.QUALITY: "Quality",
    Component.VALUE_GROWTH: "Valuation vs growth",
    Component.MOMENTUM: "Momentum",
    Component.VS_BONDS: "Valuation vs bonds",
    Component.COST: "Cost",
    Component.DIVERSIFICATION: "Diversification",
    Component.INCOME: "Income vs bonds",
}
NO_FAIR_VALUE = "no fair value"
NO_DATA = "no data"
CROSS_CURRENCY = "Yahoo ratios unreliable for cross-listed shares"
TOO_FEW = "needs at least 2 components"

_Parts = tuple[tuple[str, float | None], ...]


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def ramp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """Linear between (x0, y0) and (x1, y1) with x0 < x1; flat outside that range."""
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _mean(parts: _Parts) -> float | None:
    values = [v for _, v in parts if v is not None]
    return sum(values) / len(values) if values else None


def _upside(v: Valuation) -> tuple[float | None, str | None, _Parts]:
    if v.margin is None:
        return None, v.reason or NO_FAIR_VALUE, ()
    return clamp(50 + v.margin * 200), None, (("Margin of safety", v.margin),)


def _analyst(i: ValuationInputs) -> tuple[float | None, str | None, _Parts]:
    c = i.analyst
    if c is None or (c.target_consensus is None and c.recommendation_mean is None):
        return None, NO_COVERAGE, ()
    if (c.number_of_analysts or 0) < 3:
        return None, FEW_ANALYSTS, ()
    rating = None if c.recommendation_mean is None else clamp((5 - c.recommendation_mean) / 4 * 100)
    target = (
        None if c.target_consensus is None else clamp(50 + (c.target_consensus / i.price - 1) * 200)
    )
    parts = (("Rating", rating), ("Target upside", target))
    return _mean(parts), None, parts


def _quality(i: ValuationInputs) -> tuple[float | None, str | None, _Parts]:
    parts = (
        ("ROE", None if i.return_on_equity is None else ramp(i.return_on_equity, 0.0, 0.0, 0.20, 100.0)),
        ("Operating margin", None if i.operating_margin is None else ramp(i.operating_margin, 0.0, 0.0, 0.25, 100.0)),
        ("Debt/Equity", None if i.debt_to_equity is None else 0.0 if i.debt_to_equity < 0 else ramp(i.debt_to_equity, 0.5, 100.0, 2.0, 0.0)),
    )
    mean = _mean(parts)
    return mean, None if mean is not None else NO_DATA, parts


def _value_growth(i: ValuationInputs) -> tuple[float | None, str | None, _Parts]:
    if i.yahoo_cross_currency:
        return None, CROSS_CURRENCY, ()
    # A negative PEG or forward P/E means shrinking or negative earnings: no signal, not "cheap".
    peg = i.peg_ratio if i.peg_ratio is not None and i.peg_ratio > 0 else None
    fpe = i.forward_pe if i.forward_pe is not None and i.forward_pe > 0 else None
    parts = (
        ("PEG", None if peg is None else ramp(peg, 1.0, 100.0, 3.0, 0.0)),
        ("Forward P/E", None if fpe is None else ramp(fpe, 10.0, 100.0, 40.0, 0.0)),
    )
    mean = _mean(parts)
    return mean, None if mean is not None else NO_DATA, parts


def _momentum(i: ValuationInputs) -> tuple[float | None, str | None, _Parts]:
    trend = None if not i.ma_200d else clamp(50 + (i.price / i.ma_200d - 1) * 250)
    year = None if i.price_return_1y is None else clamp(50 + i.price_return_1y * 100)
    parts = (("Price vs 200-day average", trend), ("1-year return", year))
    mean = _mean(parts)
    return mean, None if mean is not None else NO_DATA, parts


def band_for(value: float) -> ScoreBand:
    if value >= 70:
        return ScoreBand.FAVOURABLE
    if value >= 40:
        return ScoreBand.MIXED
    return ScoreBand.UNFAVOURABLE


def score(inputs: ValuationInputs, v: Valuation, a: Assumptions) -> Score:
    if inputs.etf is not None:
        from obb_terminal.domain.etf import score_etf

        return score_etf(inputs, v, a)
    w = a.score_weights
    raw = [
        (Component.UPSIDE, _upside(v), max(w.upside, 0.0)),
        (Component.ANALYST, _analyst(inputs), max(w.analyst, 0.0)),
        (Component.QUALITY, _quality(inputs), max(w.quality, 0.0)),
        (Component.VALUE_GROWTH, _value_growth(inputs), max(w.value_growth, 0.0)),
        (Component.MOMENTUM, _momentum(inputs), max(w.momentum, 0.0)),
    ]
    used = [(value, weight) for _, (value, _, _), weight in raw if value is not None and weight > 0]
    total = sum(weight for _, weight in used)
    components = tuple(
        ScoreComponent(
            key, LABELS[key], value,
            weight / total if value is not None and weight > 0 and len(used) >= 2 else 0.0,
            reason, parts,
        )
        for key, (value, reason, parts), weight in raw
    )
    if len(used) < 2:
        return Score(None, None, components, TOO_FEW)
    result = sum(value * weight for value, weight in used) / total
    return Score(result, band_for(result), components, None)
