"""ETF look-through valuation: kind, top-holdings roll-up, ETF Outlook score and KPIs.

Pure functions, like valuation.py: they never raise and never fetch. `valuation.value`,
`scoring.score` and `kpis.kpis` dispatch here when an ETF carries look-through data
(`ValuationInputs.etf`); without it an ETF keeps the old "not applicable" behaviour.
"""

from __future__ import annotations

from collections.abc import Iterable

from obb_terminal.domain.dividends import ttm_yield
from obb_terminal.domain.models import (
    Assumptions,
    Component,
    EtfKind,
    FundProfile,
    Holding,
    HoldingValue,
    Kpi,
    KpiFormat,
    Mark,
    Method,
    MethodResult,
    Score,
    ScoreComponent,
    Valuation,
    ValuationInputs,
)

EQUITY_MIN_STOCK = 0.70
BOND_MIN = 0.70

ROLLUP_PENDING = "holdings not valued yet"
LOW_COVERAGE = "top holdings cover too little of the fund"
NEEDS_EQUITY = "Fair value needs an equity ETF"
NO_FUND = "Yahoo has no holdings for this fund"
NO_EXPENSE = "no expense ratio"
NO_HOLDINGS = "no holdings"
NO_PE = "no look-through P/E"
NO_YIELD = "no dividends in last 12 months"

_SECTOR_LABELS = {
    "realestate": "Real estate",
    "consumer_cyclical": "Consumer cyclical",
    "basic_materials": "Basic materials",
    "consumer_defensive": "Consumer defensive",
    "technology": "Technology",
    "communication_services": "Communication services",
    "financial_services": "Financial services",
    "utilities": "Utilities",
    "industrials": "Industrials",
    "energy": "Energy",
    "healthcare": "Healthcare",
}
_INVESTMENT_GRADE = ("aaa", "aa", "a", "bbb")


_RATING_LABELS = {"us_government": "US government", "below_b": "Below B", "other": "Other"}


def sector_label(key: str) -> str:
    return _SECTOR_LABELS.get(key, key.replace("_", " ").capitalize())


def rating_label(key: str) -> str:
    return _RATING_LABELS.get(key, key.upper())


def etf_kind(profile: FundProfile) -> EtfKind:
    stock, bond = profile.stock_position or 0.0, profile.bond_position or 0.0
    if stock >= EQUITY_MIN_STOCK and profile.pe is not None:
        return EtfKind.EQUITY
    if bond >= BOND_MIN:
        return EtfKind.BOND
    return EtfKind.OTHER


# --- roll-up -------------------------------------------------------------------------------


def holding_value(
    holding: Holding, margin: float | None, reason: str | None, transient: bool = False
) -> HoldingValue:
    return HoldingValue(holding.symbol, holding.weight, margin, None if margin is not None else reason, transient)


def rollup_settled(values: Iterable[HoldingValue]) -> bool:
    """No row failed for a reason that may pass: safe to remember and to journal."""
    return not any(hv.transient for hv in values)


def _valued(values: Iterable[HoldingValue]) -> list[HoldingValue]:
    return [hv for hv in values if hv.margin is not None and hv.weight > 0]


def coverage(values: Iterable[HoldingValue]) -> float:
    """Share of the fund whose holdings have a fair value."""
    return sum(hv.weight for hv in _valued(values))


def rollup_margin(values: Iterable[HoldingValue]) -> float | None:
    """Weight-averaged margin of safety of the valued holdings; None when none is valued."""
    valued = _valued(values)
    total = sum(hv.weight for hv in valued)
    if total <= 0:
        return None
    return sum(hv.weight * hv.margin for hv in valued) / total  # type: ignore[operator]


def value_etf(inputs: ValuationInputs, a: Assumptions) -> Valuation:
    from obb_terminal.domain.valuation import label_for

    assert inputs.etf is not None
    etf = inputs.etf

    def none(reason: str) -> Valuation:
        return Valuation(True, inputs.currency, inputs.price, (), None, None, None, reason)

    if etf_kind(etf.profile) is not EtfKind.EQUITY:
        return none(NO_FUND if etf.profile.gaps and not etf.profile.holdings else NEEDS_EQUITY)
    if etf.holding_values is None:
        return none(ROLLUP_PENDING)
    margin = rollup_margin(etf.holding_values)
    if margin is None or coverage(etf.holding_values) < a.etf.min_coverage:
        return none(LOW_COVERAGE)
    fair = inputs.price * (1 + margin)
    methods = (MethodResult(Method.LOOK_THROUGH, fair, None, 1.0),)
    return Valuation(True, inputs.currency, inputs.price, methods, fair, margin, label_for(margin), None)


# --- Outlook score -------------------------------------------------------------------------

_USED_WEIGHTS: dict[EtfKind, frozenset[str]] = {
    EtfKind.EQUITY: frozenset({"upside", "vs_bonds", "cost", "diversification", "momentum"}),
    EtfKind.BOND: frozenset({"income", "cost", "momentum"}),
    EtfKind.OTHER: frozenset({"cost", "diversification", "momentum"}),
}


def used_weights(kind: EtfKind) -> frozenset[str]:
    """The ETF score weights (EtfScoreWeights fields) an ETF of this kind is scored on."""
    return _USED_WEIGHTS[kind]

_Parts = tuple[tuple[str, float | None], ...]
_Result = tuple[float | None, str | None, _Parts]


def earnings_yield(profile: FundProfile) -> float | None:
    return None if profile.pe is None or profile.pe <= 0 else 1 / profile.pe


def fund_ttm_yield(inputs: ValuationInputs) -> float | None:
    if not inputs.dividends:
        return None
    return ttm_yield(inputs.dividends, inputs.price, inputs.as_of)


def _vs_bonds(profile: FundProfile, bond_yield: float) -> _Result:
    from obb_terminal.domain.scoring import ramp

    ey = earnings_yield(profile)
    if ey is None:
        return None, NO_PE, ()
    gap = ey - bond_yield
    return ramp(gap, -0.01, 0.0, 0.04, 100.0), None, (
        ("Earnings yield", ey), ("10-year yield", bond_yield), ("Gap", gap))


def _cost(profile: FundProfile) -> _Result:
    from obb_terminal.domain.scoring import ramp

    er, cat = profile.expense_ratio, profile.category_expense_ratio
    if er is None:
        return None, NO_EXPENSE, ()
    if cat:
        ratio = er / cat
        return ramp(ratio, 0.5, 100.0, 1.5, 0.0), None, (
            ("Expense ratio", er), ("Category average", cat))
    return ramp(er, 0.0005, 100.0, 0.01, 0.0), None, (("Expense ratio", er),)


def _diversification(profile: FundProfile) -> _Result:
    from obb_terminal.domain.scoring import ramp

    top = profile.top_weight
    if top is None:
        return None, NO_HOLDINGS, ()
    return ramp(top, 0.25, 100.0, 0.60, 0.0), None, (("Top-10 weight", top),)


def _income(inputs: ValuationInputs, bond_yield: float) -> _Result:
    from obb_terminal.domain.scoring import ramp

    y = fund_ttm_yield(inputs)
    if not y:
        return None, NO_YIELD, ()
    gap = y - bond_yield
    return ramp(gap, -0.02, 0.0, 0.01, 100.0), None, (
        ("TTM yield", y), ("10-year yield", bond_yield), ("Gap", gap))


def score_etf(inputs: ValuationInputs, v: Valuation, a: Assumptions) -> Score:
    from obb_terminal.domain.scoring import LABELS, TOO_FEW, _momentum, _upside, band_for

    assert inputs.etf is not None
    profile, w, y = inputs.etf.profile, a.etf.score_weights, a.etf.bond_yield
    kind = etf_kind(profile)
    raw: list[tuple[Component, _Result, float]] = []
    if kind is EtfKind.EQUITY:
        raw += [(Component.UPSIDE, _upside(v), w.upside),
                (Component.VS_BONDS, _vs_bonds(profile, y), w.vs_bonds)]
    if kind is EtfKind.BOND:
        raw.append((Component.INCOME, _income(inputs, y), w.income))
    raw.append((Component.COST, _cost(profile), w.cost))
    if kind is not EtfKind.BOND:
        raw.append((Component.DIVERSIFICATION, _diversification(profile), w.diversification))
    raw.append((Component.MOMENTUM, _momentum(inputs), w.momentum))
    raw = [(key, result, max(weight, 0.0)) for key, result, weight in raw]
    used = [(val, weight) for _, (val, _, _), weight in raw if val is not None and weight > 0]
    total = sum(weight for _, weight in used)
    components = tuple(
        ScoreComponent(
            key, LABELS[key], val,
            weight / total if val is not None and weight > 0 and len(used) >= 2 else 0.0,
            reason, parts,
        )
        for key, (val, reason, parts), weight in raw
    )
    if len(used) < 2:
        return Score(None, None, components, TOO_FEW)
    result = sum(val * weight for val, weight in used) / total
    return Score(result, band_for(result), components, None)


# --- KPIs ----------------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def kpis_etf(inputs: ValuationInputs, bond_yield: float) -> list[Kpi]:
    from obb_terminal.domain.kpis import _higher_better, _lower_better, _multiple_mark

    assert inputs.etf is not None
    p = inputs.etf.profile
    ey = earnings_yield(p)
    gap = None if ey is None else ey - bond_yield
    er_note = None if p.category_expense_ratio is None else f"category avg {_pct(p.category_expense_ratio)}"
    sector = p.sectors[0] if p.sectors else None
    ttm = fund_ttm_yield(inputs)
    bond = etf_kind(p) is EtfKind.BOND
    if bond:  # no earnings to look through: income and credit quality instead
        ratings = dict(p.bond_ratings)
        ig = sum(ratings.get(k, 0.0) for k in _INVESTMENT_GRADE) if ratings else None
        top = sum(ratings.get(k, 0.0) for k in ("aaa", "aa")) if ratings else None
        income_gap = None if not ttm else ttm - bond_yield
        rows = [
            Kpi("yield_gap", "Yield vs 10-year", income_gap, KpiFormat.PERCENT,
                _higher_better(income_gap, 0.005, -0.01), f"10-year {_pct(bond_yield)}"),
            Kpi("investment_grade", "Investment grade", ig, KpiFormat.PERCENT, _higher_better(ig, 0.9, 0.6)),
            Kpi("aaa_aa", "AAA and AA", top, KpiFormat.PERCENT, _higher_better(top, 0.6, 0.3)),
        ]
    else:
        rows = [
            Kpi("pe", "P/E (look-through)", p.pe, KpiFormat.MULTIPLE, _multiple_mark(p.pe, 15, 30)),
            Kpi("earnings_yield", "Earnings yield", ey, KpiFormat.PERCENT, _higher_better(ey, 0.06, 0.03)),
            Kpi("gap_vs_10y", "Gap vs 10-year", gap, KpiFormat.PERCENT, _higher_better(gap, 0.02, -0.01),
                f"10-year {_pct(bond_yield)}"),
            Kpi("pb", "P/B (look-through)", p.pb, KpiFormat.MULTIPLE, _multiple_mark(p.pb, 1.5, 5)),
            Kpi("ps", "P/S (look-through)", p.ps, KpiFormat.MULTIPLE, _multiple_mark(p.ps, 1.5, 4)),
            Kpi("pcf", "P/CF (look-through)", p.pcf, KpiFormat.MULTIPLE, _multiple_mark(p.pcf, 10, 25)),
        ]
    rows += [
        Kpi("expense_ratio", "Expense ratio", p.expense_ratio, KpiFormat.PERCENT,
            _lower_better(p.expense_ratio, 0.002, 0.0075), er_note),
        Kpi("turnover", "Turnover", p.turnover, KpiFormat.PERCENT, _lower_better(p.turnover, 0.25, 1.0)),
        Kpi("top10_weight", "Top-10 weight", p.top_weight, KpiFormat.PERCENT,
            _lower_better(p.top_weight, 0.30, 0.60)),
    ]
    if not bond:
        rows.append(
            Kpi("largest_sector", "Largest sector", None if sector is None else sector[1], KpiFormat.PERCENT,
                Mark.MISSING if sector is None else _lower_better(sector[1], 0.30, 0.50),
                None if sector is None else sector_label(sector[0]))
        )
    rows += [
        Kpi("ttm_yield", "TTM yield", ttm, KpiFormat.PERCENT,
            Mark.MISSING if ttm is None else Mark.GOOD if ttm >= 0.03 else Mark.NEUTRAL),
        Kpi("return_1y", "1-year return", inputs.price_return_1y, KpiFormat.PERCENT,
            _higher_better(inputs.price_return_1y, 0.10, 0.0)),
    ]
    return rows
