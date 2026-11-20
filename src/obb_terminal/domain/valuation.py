"""Fair-value methods and their blend. Pure functions: they never raise and never fetch."""

from __future__ import annotations

import math
from dataclasses import asdict

from obb_terminal.domain.dividends import cagr, ttm_total
from obb_terminal.domain.models import (
    GBP_TEN_YEAR_DEFAULT,
    AssetType,
    Assumptions,
    Currency,
    EtfAssumptions,
    EtfScoreWeights,
    Method,
    MethodResult,
    MethodWeights,
    ScoreWeights,
    Valuation,
    ValuationInputs,
    ValuationLabel,
)

DISCOUNT_RATE = {Currency.USD: 0.09, Currency.GBP: 0.095, Currency.BRL: 0.14}
FII_DISCOUNT_RATE = 0.12
TERMINAL_GROWTH = {Currency.USD: 0.025, Currency.GBP: 0.025, Currency.BRL: 0.04}
DEFAULT_GROWTH = 0.05
MAX_GROWTH = 0.15
DEFAULT_DIVIDEND_GROWTH = 0.03
MIN_TERMINAL_SPREAD = 0.01
MIN_DIVIDEND_SPREAD = 0.02
LABEL_BAND = 0.15
_EPS = 1e-9

NO_FCF = "no free cash flow data"
FCF_NOT_POSITIVE = "free cash flow not positive"
NO_SHARES = "shares outstanding unavailable"
NO_FX = "currency conversion unavailable"
TERMINAL_SPREAD = "discount rate must exceed terminal growth by 1%"
NO_COVERAGE = "no analyst coverage"
FEW_ANALYSTS = "fewer than 3 analysts"
EARNINGS_NOT_POSITIVE = "earnings not positive"
BOOK_NOT_POSITIVE = "book value not positive"
NO_RECENT_DIVIDENDS = "no dividends in last 12 months"
DIVIDEND_SPREAD = "discount rate must exceed dividend growth by 2%"
ETF_NOT_APPLICABLE = "Fair value not applicable to ETFs"
NO_METHOD = "No method has enough data"
ZERO_WEIGHTS = "All method weights are zero"

_Result = tuple[float | None, str | None]
ETF_SCORE_KEYS = ("upside", "vs_bonds", "cost", "diversification", "income", "momentum")


def default_assumptions(inputs: ValuationInputs) -> Assumptions:
    fii = inputs.asset_type is AssetType.FII
    rate = FII_DISCOUNT_RATE if fii else DISCOUNT_RATE[inputs.currency]
    growth = inputs.revenue_growth
    growth = DEFAULT_GROWTH if growth is None else min(max(growth, 0.0), MAX_GROWTH)
    if fii:
        dividend_growth = DEFAULT_DIVIDEND_GROWTH
    else:
        history = cagr(inputs.dividends, inputs.as_of)
        dividend_growth = (
            DEFAULT_DIVIDEND_GROWTH
            if history is None
            else min(max(history, 0.0), rate - MIN_DIVIDEND_SPREAD)
        )
    if fii:
        weights = MethodWeights(dcf=0.0, analyst=0.0, multiple=0.0, dividend=1.0)
    elif inputs.asset_type is AssetType.ETF:
        weights = MethodWeights(dcf=0.0, analyst=0.0, multiple=0.0, dividend=0.0)
    else:
        weights = MethodWeights()
    ten_year = inputs.etf.ten_year if inputs.etf is not None else None
    return Assumptions(
        discount_rate=rate,
        growth_1_5=growth,
        terminal_growth=TERMINAL_GROWTH[inputs.currency],
        dividend_growth=dividend_growth,
        method_weights=weights,
        score_weights=ScoreWeights(),
        etf=EtfAssumptions(bond_yield=GBP_TEN_YEAR_DEFAULT if ten_year is None else ten_year),
    )


def eps(inputs: ValuationInputs) -> float | None:
    """Latest annual earnings per share in the trading currency."""
    if inputs.net_income is None or not inputs.shares_outstanding or inputs.fx_rate is None:
        return None
    return inputs.net_income / inputs.shares_outstanding * inputs.fx_rate


def bvps(inputs: ValuationInputs) -> float | None:
    """Book value per share in the trading currency."""
    if inputs.book_value_ps is None or inputs.fx_rate is None:
        return None
    return inputs.book_value_ps * inputs.fx_rate


def fcf_per_share(inputs: ValuationInputs) -> float | None:
    """Mean of the last (up to) 3 annual free cash flows per share, in the trading currency."""
    recent = inputs.fcf_history[:3]
    if not recent or not inputs.shares_outstanding or inputs.fx_rate is None:
        return None
    return sum(recent) / len(recent) / inputs.shares_outstanding * inputs.fx_rate


def dcf_value(base: float, rate: float, growth: float, terminal: float) -> float:
    """Ten-year DCF per share: years 1-5 at `growth`, 6-10 fading linearly to `terminal`."""
    cash, present = base, 0.0
    for year in range(1, 11):
        g = growth if year <= 5 else growth - (growth - terminal) * (year - 5) / 5
        cash *= 1 + g
        present += cash / (1 + rate) ** year
    terminal_value = cash * (1 + terminal) / (rate - terminal)
    return present + terminal_value / (1 + rate) ** 10


def _dcf(inputs: ValuationInputs, a: Assumptions) -> _Result:
    if not inputs.fcf_history:
        return None, NO_FCF
    if not inputs.shares_outstanding:
        return None, NO_SHARES
    if inputs.fx_rate is None:
        return None, NO_FX
    base = fcf_per_share(inputs)
    if base is None or base <= 0:
        return None, FCF_NOT_POSITIVE
    if a.discount_rate - a.terminal_growth < MIN_TERMINAL_SPREAD - _EPS:
        return None, TERMINAL_SPREAD
    return dcf_value(base, a.discount_rate, a.growth_1_5, a.terminal_growth), None


def _analyst(inputs: ValuationInputs) -> _Result:
    consensus = inputs.analyst
    if consensus is None or consensus.target_consensus is None:
        return None, NO_COVERAGE
    if (consensus.number_of_analysts or 0) < 3:
        return None, FEW_ANALYSTS
    return consensus.target_consensus, None


def _multiple(inputs: ValuationInputs) -> _Result:
    if inputs.fx_rate is None:
        return None, NO_FX
    earnings = eps(inputs)
    if earnings is None or earnings <= 0:
        return None, EARNINGS_NOT_POSITIVE
    book = bvps(inputs)
    if book is None or book <= 0:
        return None, BOOK_NOT_POSITIVE
    return math.sqrt(22.5 * earnings * book), None


def _dividend(inputs: ValuationInputs, a: Assumptions) -> _Result:
    ttm = ttm_total(inputs.dividends, inputs.as_of)
    if ttm <= 0:
        return None, NO_RECENT_DIVIDENDS
    if a.discount_rate - a.dividend_growth < MIN_DIVIDEND_SPREAD - _EPS:
        return None, DIVIDEND_SPREAD
    return ttm * (1 + a.dividend_growth) / (a.discount_rate - a.dividend_growth), None


def label_for(margin: float) -> ValuationLabel:
    if margin >= LABEL_BAND:
        return ValuationLabel.UNDERVALUED
    if margin <= -LABEL_BAND:
        return ValuationLabel.OVERVALUED
    return ValuationLabel.FAIR


def value(inputs: ValuationInputs, a: Assumptions) -> Valuation:
    if inputs.asset_type is AssetType.ETF and inputs.etf is not None:
        from obb_terminal.domain.etf import value_etf

        return value_etf(inputs, a)
    if inputs.asset_type is AssetType.ETF:
        return Valuation(
            False, inputs.currency, inputs.price, (), None, None, None, ETF_NOT_APPLICABLE
        )
    w = a.method_weights
    raw: list[tuple[Method, _Result, float]] = [
        (Method.DCF, _dcf(inputs, a), max(w.dcf, 0.0)),
        (Method.ANALYST, _analyst(inputs), max(w.analyst, 0.0)),
        (Method.MULTIPLE, _multiple(inputs), max(w.multiple, 0.0)),
        (Method.DIVIDEND, _dividend(inputs, a), max(w.dividend, 0.0)),
    ]
    total = sum(weight for _, (v, _), weight in raw if v is not None and weight > 0)
    methods = tuple(
        MethodResult(
            method, v, reason, weight / total if v is not None and weight > 0 and total else 0.0
        )
        for method, (v, reason), weight in raw
    )
    if all(m.value is None for m in methods):
        return Valuation(True, inputs.currency, inputs.price, methods, None, None, None, NO_METHOD)
    if total <= 0:
        return Valuation(
            True, inputs.currency, inputs.price, methods, None, None, None, ZERO_WEIGHTS
        )
    blended = sum(m.value * m.weight for m in methods if m.value is not None and m.weight > 0)
    margin = (blended - inputs.price) / inputs.price
    return Valuation(
        True, inputs.currency, inputs.price, methods, blended, margin, label_for(margin), None
    )


def assumptions_to_dict(a: Assumptions) -> dict:
    return asdict(a)


def assumptions_from_dict(data: dict) -> Assumptions:
    """Inverse of assumptions_to_dict. Raises KeyError/TypeError/ValueError on bad input."""
    mw, sw = data["method_weights"], data["score_weights"]
    return Assumptions(
        discount_rate=float(data["discount_rate"]),
        growth_1_5=float(data["growth_1_5"]),
        terminal_growth=float(data["terminal_growth"]),
        dividend_growth=float(data["dividend_growth"]),
        method_weights=MethodWeights(**{k: float(mw[k]) for k in ("dcf", "analyst", "multiple", "dividend")}),
        score_weights=ScoreWeights(
            **{k: float(sw[k]) for k in ("upside", "analyst", "quality", "value_growth", "momentum")}
        ),
        etf=_etf_assumptions_from_dict(data.get("etf")),
    )


def _etf_assumptions_from_dict(data: dict | None) -> EtfAssumptions:
    """Reports saved before ETF support have no "etf" key: they get the defaults."""
    if data is None:
        return EtfAssumptions()
    sw = data["score_weights"]
    return EtfAssumptions(
        bond_yield=float(data["bond_yield"]),
        min_coverage=float(data["min_coverage"]),
        score_weights=EtfScoreWeights(**{k: float(sw[k]) for k in ETF_SCORE_KEYS}),
    )
