from dataclasses import replace
from datetime import date

import pytest

from obb_terminal.domain.etf import (
    LOW_COVERAGE,
    NEEDS_EQUITY,
    NO_FUND,
    ROLLUP_PENDING,
    coverage,
    etf_kind,
    holding_value,
    rollup_margin,
)
from obb_terminal.domain.kpis import kpis
from obb_terminal.domain.models import (
    AssetType,
    Component,
    Currency,
    Dividend,
    EtfAssumptions,
    EtfData,
    EtfKind,
    EtfScoreWeights,
    FundProfile,
    Holding,
    HoldingValue,
    Mark,
    Method,
    ValuationInputs,
    ValuationLabel,
)
from obb_terminal.domain.scoring import score
from obb_terminal.domain.valuation import (
    ETF_NOT_APPLICABLE,
    assumptions_from_dict,
    assumptions_to_dict,
    default_assumptions,
    value,
)

TODAY = date(2026, 11, 17)

SPY_PROFILE = FundProfile(
    symbol="SPY", category="Large Blend", family="State Street", expense_ratio=0.000945,
    category_expense_ratio=0.007218, turnover=0.03,
    holdings=(Holding("AAPL", "Apple Inc", 0.077), Holding("NVDA", "NVIDIA Corp", 0.076),
              Holding("MSFT", "Microsoft Corp", 0.059), Holding("AMZN", "Amazon.com Inc", 0.036)),
    sectors=(("technology", 0.38), ("financial_services", 0.12), ("healthcare", 0.10),
             ("industrials", 0.08)),
    stock_position=0.9994, bond_position=0.0, cash_position=0.0006, other_position=0.0,
    pe=24.0, pb=5.08, ps=3.44, pcf=18.2,
)
AGG_PROFILE = FundProfile(
    symbol="AGG", category="Intermediate Core Bond", expense_ratio=0.0003,
    stock_position=0.0, bond_position=0.993,
    bond_ratings=(("aa", 0.7415), ("a", 0.1193), ("bbb", 0.1161), ("aaa", 0.0231)),
)
GLD_PROFILE = FundProfile(symbol="GLD", category="Commodities Focused", expense_ratio=0.004,
                          stock_position=0.0, bond_position=0.0)

VALUED = (HoldingValue("AAPL", 0.077, 0.10), HoldingValue("NVDA", 0.076, -0.20),
          HoldingValue("MSFT", 0.059, 0.30), HoldingValue("AMZN", 0.036, None, "no fair value"))


def make(profile=SPY_PROFILE, holding_values=VALUED, ten_year=0.051, **changes) -> ValuationInputs:
    base = ValuationInputs(
        symbol=profile.symbol, name="Test ETF", asset_type=AssetType.ETF, currency=Currency.USD,
        as_of=TODAY, price=100.0, ma_200d=95.0,
        dividends=(Dividend(date(2026, 9, 1), 0.5), Dividend(date(2026, 6, 1), 0.5)),
        etf=EtfData(profile=profile, ten_year=ten_year, holding_values=holding_values),
    )
    return replace(base, **changes)


def comp(s, key):
    return next(c for c in s.components if c.key is key)


def keys(s):
    return {c.key for c in s.components}


# --- kind ----------------------------------------------------------------------------------


def test_kind_rules():
    assert etf_kind(SPY_PROFILE) is EtfKind.EQUITY
    assert etf_kind(AGG_PROFILE) is EtfKind.BOND
    assert etf_kind(GLD_PROFILE) is EtfKind.OTHER
    assert etf_kind(replace(SPY_PROFILE, pe=None)) is EtfKind.OTHER  # stocks but no look-through
    assert etf_kind(replace(SPY_PROFILE, stock_position=0.6)) is EtfKind.OTHER
    assert etf_kind(FundProfile("BOVA11.SA", gaps=("no fund data",))) is EtfKind.OTHER


# --- roll-up -------------------------------------------------------------------------------


def test_rollup_weighted_mean_of_valued_holdings():
    m = rollup_margin(VALUED)
    expected = (0.077 * 0.10 + 0.076 * -0.20 + 0.059 * 0.30) / (0.077 + 0.076 + 0.059)
    assert m == pytest.approx(expected)
    assert coverage(VALUED) == pytest.approx(0.212)


def test_rollup_none_without_valued_holdings():
    assert rollup_margin(()) is None
    assert rollup_margin((HoldingValue("X", 0.5, None, "no data"),)) is None
    assert coverage(()) == 0.0


def test_holding_value_from_a_valuation():
    hv = holding_value(Holding("AAPL", "Apple", 0.07), margin=0.12, reason=None)
    assert hv == HoldingValue("AAPL", 0.07, 0.12, None)


def test_value_equity_etf_is_the_rollup():
    inputs = make()
    v = value(inputs, default_assumptions(inputs))
    m = rollup_margin(VALUED)
    assert v.applicable and v.margin == pytest.approx(m)
    assert v.blended == pytest.approx(100.0 * (1 + m))
    assert [(r.method, r.weight) for r in v.methods] == [(Method.LOOK_THROUGH, 1.0)]
    assert v.label is ValuationLabel.FAIR


def test_value_waits_for_the_rollup():
    inputs = make(holding_values=None)
    v = value(inputs, default_assumptions(inputs))
    assert v.blended is None and v.reason == ROLLUP_PENDING


def test_value_needs_minimum_coverage():
    thin = (HoldingValue("AAPL", 0.10, 0.1), HoldingValue("NVDA", 0.08, None, "no data"))
    inputs = make(holding_values=thin)
    a = default_assumptions(inputs)
    assert value(inputs, a).reason == LOW_COVERAGE
    looser = replace(a, etf=replace(a.etf, min_coverage=0.05))
    assert value(inputs, looser).margin == pytest.approx(0.1)


def test_value_bond_and_other_have_no_fair_value():
    for profile in (AGG_PROFILE, GLD_PROFILE):
        inputs = make(profile=profile, holding_values=())
        v = value(inputs, default_assumptions(inputs))
        assert v.blended is None and v.reason == NEEDS_EQUITY


def test_value_without_fund_data_says_so():
    inputs = make(profile=FundProfile("BOVA11.SA", gaps=("no fund data",)), holding_values=())
    assert value(inputs, default_assumptions(inputs)).reason == NO_FUND


def test_etf_without_look_through_keeps_old_behaviour():
    inputs = replace(make(), etf=None)
    v = value(inputs, default_assumptions(inputs))
    assert not v.applicable and v.reason == ETF_NOT_APPLICABLE


# --- assumptions ---------------------------------------------------------------------------


def test_default_bond_yield_uses_market_yield_or_the_default():
    assert default_assumptions(make()).etf.bond_yield == pytest.approx(0.051)
    assert default_assumptions(make(ten_year=None)).etf.bond_yield == pytest.approx(0.045)


def test_assumptions_round_trip_with_etf_and_old_dicts():
    a = default_assumptions(make())
    assert assumptions_from_dict(assumptions_to_dict(a)) == a
    old = assumptions_to_dict(a)
    del old["etf"]
    assert assumptions_from_dict(old).etf == EtfAssumptions()


# --- score ---------------------------------------------------------------------------------


def run(inputs, a=None):
    a = a or default_assumptions(inputs)
    return score(inputs, value(inputs, a), a)


def test_equity_etf_components():
    s = run(make())
    assert keys(s) == {Component.UPSIDE, Component.VS_BONDS, Component.COST,
                       Component.DIVERSIFICATION, Component.MOMENTUM}
    assert s.value is not None
    assert sum(c.weight for c in s.components) == pytest.approx(1.0)


def test_vs_bonds_ramp():
    # earnings yield 1/24 = 4.17% vs 5.10%: gap -0.93 pt -> just above the -1 pt floor
    gap = 1 / 24 - 0.051
    assert comp(run(make()), Component.VS_BONDS).value == pytest.approx((gap + 0.01) / 0.05 * 100)
    cheap = make(profile=replace(SPY_PROFILE, pe=10.0), ten_year=0.04)  # 10% - 4% = +6 pts
    assert comp(run(cheap), Component.VS_BONDS).value == 100.0


def test_cost_against_category_and_absolute():
    s = run(make())  # 0.0945% vs 0.7218%: ratio 0.13 -> 100
    assert comp(s, Component.COST).value == 100.0
    pricey = replace(SPY_PROFILE, expense_ratio=0.0072, category_expense_ratio=0.0072)  # ratio 1
    assert comp(run(make(profile=pricey)), Component.COST).value == pytest.approx(50.0)
    no_avg = replace(SPY_PROFILE, expense_ratio=0.01, category_expense_ratio=None)
    assert comp(run(make(profile=no_avg)), Component.COST).value == 0.0


def test_diversification_ramp():
    four = run(make())  # top weight 0.248 -> 100
    assert comp(four, Component.DIVERSIFICATION).value == 100.0
    heavy = replace(SPY_PROFILE, holdings=(Holding("A", "A", 0.425),))
    assert comp(run(make(profile=heavy)), Component.DIVERSIFICATION).value == pytest.approx(50.0)


def test_bond_etf_components_and_income():
    inputs = make(profile=AGG_PROFILE, holding_values=(), ten_year=0.04, price=100.0,
                  dividends=(Dividend(date(2026, 10, 1), 4.0),))
    s = run(inputs)
    assert keys(s) == {Component.INCOME, Component.COST, Component.MOMENTUM}
    # 4% yield vs 4% bonds: gap 0 -> (0 + 0.02) / 0.03
    assert comp(s, Component.INCOME).value == pytest.approx(200 / 3)
    assert s.value is not None


def test_other_etf_components():
    s = run(make(profile=GLD_PROFILE, holding_values=()))
    assert keys(s) == {Component.COST, Component.DIVERSIFICATION, Component.MOMENTUM}
    assert comp(s, Component.DIVERSIFICATION).value is None


def test_pending_rollup_still_scores_the_rest():
    s = run(make(holding_values=None))
    up = comp(s, Component.UPSIDE)
    assert up.value is None and up.reason == ROLLUP_PENDING
    assert s.value is not None


def test_etf_score_weights_are_editable():
    inputs = make()
    a = default_assumptions(inputs)
    only_cost = replace(a, etf=replace(a.etf, score_weights=EtfScoreWeights(
        upside=0, vs_bonds=0, cost=1, diversification=1, income=0, momentum=0)))
    s = run(inputs, only_cost)
    assert s.value == pytest.approx(100.0)


# --- KPIs ----------------------------------------------------------------------------------


def test_equity_etf_kpis():
    rows = {k.key: k for k in kpis(make())}
    assert len(rows) == 12
    assert rows["pe"].value == 24.0 and rows["pe"].mark is Mark.NEUTRAL
    assert rows["earnings_yield"].value == pytest.approx(1 / 24)
    assert rows["gap_vs_10y"].value == pytest.approx(1 / 24 - 0.051)
    assert rows["expense_ratio"].mark is Mark.GOOD and "0.72%" in rows["expense_ratio"].note
    assert rows["top10_weight"].value == pytest.approx(0.248)
    assert rows["largest_sector"].value == 0.38 and rows["largest_sector"].note == "Technology"
    assert rows["ttm_yield"].value == pytest.approx(0.01)


def test_bond_etf_kpis_show_credit_quality():
    rows = {k.key: k for k in kpis(make(profile=AGG_PROFILE, holding_values=()))}
    assert not {"pe", "earnings_yield", "gap_vs_10y", "pb", "ps", "pcf", "largest_sector"} & set(rows)
    assert rows["investment_grade"].value == pytest.approx(1.0)
    assert rows["aaa_aa"].value == pytest.approx(0.7646)
    assert rows["yield_gap"].value == pytest.approx(0.01 - 0.051)  # TTM 1% vs 5.1%
    assert rows["yield_gap"].mark is Mark.WEAK
    assert len(rows) == 8


def test_plain_etf_without_look_through_keeps_stock_kpis():
    assert {k.key for k in kpis(replace(make(), etf=None))} >= {"pe", "roe"}
