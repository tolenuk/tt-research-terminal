from dataclasses import replace
from datetime import date

import pytest

from obb_terminal.domain.models import (
    AnalystConsensus,
    AssetType,
    Assumptions,
    Component,
    Currency,
    ScoreBand,
    ScoreWeights,
    ValuationInputs,
)
from obb_terminal.domain.scoring import band_for, clamp, ramp, score
from obb_terminal.domain.valuation import value

A = Assumptions(discount_rate=0.10, growth_1_5=0.10, terminal_growth=0.03, dividend_growth=0.04)


def make(**changes) -> ValuationInputs:
    base = ValuationInputs(
        symbol="TEST", name="Test plc", asset_type=AssetType.EQUITY, currency=Currency.USD,
        as_of=date(2026, 10, 9), price=120.0, shares_outstanding=1.0, fx_rate=1.0,
    )
    return replace(base, **changes)


def comp(s, key):
    return next(c for c in s.components if c.key is key)


def run(inputs, a=A):
    return score(inputs, value(inputs, a), a)


def test_clamp_and_ramp():
    assert clamp(-5) == 0 and clamp(150) == 100 and clamp(42.5) == 42.5
    assert ramp(0.10, 0.0, 0.0, 0.20, 100.0) == pytest.approx(50.0)
    assert ramp(-1, 0.0, 0.0, 0.20, 100.0) == 0.0
    assert ramp(0.5, 0.0, 0.0, 0.20, 100.0) == 100.0
    assert ramp(1.25, 0.5, 100.0, 2.0, 0.0) == pytest.approx(50.0)


def test_upside_reference_value():
    consensus = AnalystConsensus(150.0, None, 100.0, 200.0, "buy", None, 5)
    inputs = make(fcf_history=(10.0,), analyst=consensus, net_income=2.0, book_value_ps=20.0)
    assert comp(run(inputs), Component.UPSIDE).value == pytest.approx(98.76, abs=0.01)


def test_upside_clamped_and_none_for_etf():
    consensus = AnalystConsensus(400.0, None, None, None, None, None, 5)
    assert comp(run(make(analyst=consensus)), Component.UPSIDE).value == 100.0
    etf = comp(run(make(asset_type=AssetType.ETF)), Component.UPSIDE)
    assert etf.value is None and etf.reason == "Fair value not applicable to ETFs"


def test_momentum_reference_value():
    s = run(make(price=104.0, ma_200d=100.0, price_return_1y=0.10))
    m = comp(s, Component.MOMENTUM)
    assert m.value == pytest.approx(60.0)
    assert dict(m.parts) == pytest.approx({"Price vs 200-day average": 60.0, "1-year return": 60.0})


def test_analyst_component():
    c = AnalystConsensus(132.0, None, None, None, "buy", 2.0, 10)
    a = comp(run(make(analyst=c)), Component.ANALYST)
    assert dict(a.parts) == pytest.approx({"Rating": 75.0, "Target upside": 70.0})
    assert a.value == pytest.approx(72.5)


@pytest.mark.parametrize(
    ("consensus", "reason"),
    [(None, "no analyst coverage"), (AnalystConsensus(130.0, None, None, None, "buy", 2.0, 2), "fewer than 3 analysts")],
)
def test_analyst_component_missing(consensus, reason):
    a = comp(run(make(analyst=consensus)), Component.ANALYST)
    assert (a.value, a.reason) == (None, reason)


@pytest.mark.parametrize(
    ("roe", "margin", "de", "expected"),
    [(0.20, 0.25, 0.5, 100.0), (0.0, 0.0, 2.0, 0.0), (0.10, None, None, 50.0), (0.30, 0.125, 1.25, (100 + 50 + 50) / 3)],
)
def test_quality_component(roe, margin, de, expected):
    q = comp(run(make(return_on_equity=roe, operating_margin=margin, debt_to_equity=de)), Component.QUALITY)
    assert q.value == pytest.approx(expected)


def test_value_growth_component():
    vg = comp(run(make(peg_ratio=2.0, forward_pe=25.0)), Component.VALUE_GROWTH)
    assert vg.value == pytest.approx(50.0)
    negative = comp(run(make(peg_ratio=-1.0, forward_pe=10.0)), Component.VALUE_GROWTH)
    assert negative.value == 100.0 and dict(negative.parts)["PEG"] is None


def test_value_growth_none_for_cross_currency():
    vg = comp(run(make(peg_ratio=1.0, forward_pe=10.0, yahoo_cross_currency=True)), Component.VALUE_GROWTH)
    assert (vg.value, vg.reason) == (None, "Yahoo ratios unreliable for cross-listed shares")


def test_missing_components_report_no_data():
    s = run(make())
    assert comp(s, Component.QUALITY).reason == "no data"
    assert comp(s, Component.MOMENTUM).reason == "no data"


def test_score_needs_two_components():
    s = run(make(ma_200d=100.0))
    assert s.value is None and s.band is None
    assert s.reason == "needs at least 2 components"
    assert comp(s, Component.MOMENTUM).value is not None


def test_score_is_weighted_mean_renormalised():
    inputs = make(price=104.0, ma_200d=100.0, price_return_1y=0.10, return_on_equity=0.10)
    s = run(inputs)  # momentum 60 (w .15), quality 50 (w .20)
    assert s.value == pytest.approx((60 * 0.15 + 50 * 0.20) / 0.35)
    assert comp(s, Component.QUALITY).weight == pytest.approx(0.20 / 0.35)
    assert comp(s, Component.UPSIDE).weight == 0.0
    assert s.band is ScoreBand.MIXED


def test_zero_score_weight_drops_component():
    inputs = make(price=104.0, ma_200d=100.0, price_return_1y=0.10, return_on_equity=0.10)
    a = replace(A, score_weights=ScoreWeights(momentum=0.0))
    s = run(inputs, a)
    assert s.value is None and s.reason == "needs at least 2 components"


@pytest.mark.parametrize(
    ("value_", "band"),
    [(70.0, ScoreBand.FAVOURABLE), (69.99, ScoreBand.MIXED), (40.0, ScoreBand.MIXED), (39.99, ScoreBand.UNFAVOURABLE)],
)
def test_bands(value_, band):
    assert band_for(value_) is band


def test_quality_negative_debt_to_equity_scores_zero():
    q = comp(run(make(debt_to_equity=-1.5)), Component.QUALITY)
    assert q.value == pytest.approx(0.0)
