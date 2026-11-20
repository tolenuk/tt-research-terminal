from dataclasses import replace
from datetime import date

import pytest

from obb_terminal.domain.models import (
    AnalystConsensus,
    AssetType,
    Assumptions,
    Currency,
    Dividend,
    Method,
    MethodWeights,
    ValuationInputs,
    ValuationLabel,
)
from obb_terminal.domain.valuation import (
    assumptions_from_dict,
    assumptions_to_dict,
    bvps,
    dcf_value,
    default_assumptions,
    eps,
    fcf_per_share,
    value,
)

TODAY = date(2026, 10, 9)
A = Assumptions(discount_rate=0.10, growth_1_5=0.10, terminal_growth=0.03, dividend_growth=0.04)


def make(**changes) -> ValuationInputs:
    base = ValuationInputs(
        symbol="TEST", name="Test plc", asset_type=AssetType.EQUITY, currency=Currency.USD,
        as_of=TODAY, price=120.0, shares_outstanding=1.0, reporting_currency="USD", fx_rate=1.0,
    )
    return replace(base, **changes)


def consensus(target=150.0, n=5) -> AnalystConsensus:
    return AnalystConsensus(target, None, 100.0, 200.0, "buy", 2.0, n)


def method(v, m):
    return next(r for r in v.methods if r.method is m)


# --- Appendix C reference values -----------------------------------------------------------


def test_dcf_reference_value():
    assert dcf_value(10.0, 0.10, 0.10, 0.03) == pytest.approx(216.7659, abs=1e-4)


def test_dcf_cross_currency_uses_mean_of_three_and_fx():
    inputs = make(fcf_history=(110.0, 100.0, 90.0, 80.0), shares_outstanding=10.0, fx_rate=0.75)
    assert fcf_per_share(inputs) == pytest.approx(7.5)
    assert method(value(inputs, A), Method.DCF).value == pytest.approx(162.5744, abs=1e-4)


def test_dcf_equals_gordon_when_growth_equals_terminal():
    a = replace(A, growth_1_5=0.03)
    assert dcf_value(10.0, 0.10, 0.03, 0.03) == pytest.approx(147.1429, abs=1e-4)
    assert method(value(make(fcf_history=(10.0,)), a), Method.DCF).value == pytest.approx(147.1429, abs=1e-4)


def test_graham_number():
    inputs = make(net_income=2.0, book_value_ps=20.0)
    assert eps(inputs) == 2.0 and bvps(inputs) == 20.0
    assert method(value(inputs, A), Method.MULTIPLE).value == pytest.approx(30.0)


def test_dividend_gordon():
    inputs = make(dividends=(Dividend(date(2026, 6, 1), 0.5), Dividend(date(2026, 1, 5), 0.5)))
    a = replace(A, discount_rate=0.09, dividend_growth=0.04)
    assert method(value(inputs, a), Method.DIVIDEND).value == pytest.approx(20.8)


def test_blend_renormalises_and_labels():
    inputs = make(fcf_history=(10.0,), analyst=consensus(150.0), net_income=2.0, book_value_ps=20.0)
    v = value(inputs, A)
    assert v.applicable and v.reason is None
    assert v.blended == pytest.approx(149.2566, abs=1e-4)
    assert v.margin == pytest.approx(0.2438, abs=1e-4)
    assert v.label is ValuationLabel.UNDERVALUED
    weights = {r.method: r.weight for r in v.methods}
    assert weights[Method.DCF] == pytest.approx(0.35 / 0.85)
    assert weights[Method.DIVIDEND] == 0.0
    assert method(v, Method.DIVIDEND).reason == "no dividends in last 12 months"


# --- reasons -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({}, "no free cash flow data"),
        ({"fcf_history": (-5.0, 1.0)}, "free cash flow not positive"),
        ({"fcf_history": (10.0,), "shares_outstanding": None}, "shares outstanding unavailable"),
        ({"fcf_history": (10.0,), "fx_rate": None}, "currency conversion unavailable"),
    ],
)
def test_dcf_reasons(changes, reason):
    assert method(value(make(**changes), A), Method.DCF).reason == reason


def test_dcf_needs_terminal_spread():
    a = replace(A, discount_rate=0.035)
    assert method(value(make(fcf_history=(10.0,)), a), Method.DCF).reason == (
        "discount rate must exceed terminal growth by 1%"
    )
    ok = replace(A, discount_rate=0.04)  # exactly tg + 1%: allowed
    assert method(value(make(fcf_history=(10.0,)), ok), Method.DCF).value is not None


@pytest.mark.parametrize(
    ("analyst", "reason"),
    [(None, "no analyst coverage"), (consensus(None), "no analyst coverage"), (consensus(150.0, 2), "fewer than 3 analysts")],
)
def test_analyst_reasons(analyst, reason):
    assert method(value(make(analyst=analyst), A), Method.ANALYST).reason == reason


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"net_income": -1.0, "book_value_ps": 20.0}, "earnings not positive"),
        ({"net_income": None, "book_value_ps": 20.0}, "earnings not positive"),
        ({"net_income": 2.0, "book_value_ps": -3.0}, "book value not positive"),
        ({"net_income": 2.0, "book_value_ps": 20.0, "fx_rate": None}, "currency conversion unavailable"),
    ],
)
def test_multiple_reasons(changes, reason):
    assert method(value(make(**changes), A), Method.MULTIPLE).reason == reason


def test_dividend_needs_spread():
    inputs = make(dividends=(Dividend(date(2026, 6, 1), 1.0),))
    a = replace(A, discount_rate=0.05, dividend_growth=0.04)
    assert method(value(inputs, a), Method.DIVIDEND).reason == (
        "discount rate must exceed dividend growth by 2%"
    )


def test_etf_not_applicable():
    v = value(make(asset_type=AssetType.ETF), A)
    assert (v.applicable, v.methods, v.blended, v.reason) == (False, (), None, "Fair value not applicable to ETFs")


def test_no_method_has_data():
    v = value(make(), A)
    assert v.blended is None and v.label is None
    assert v.reason == "No method has enough data"
    assert len(v.methods) == 4


def test_zero_and_negative_weights_leave_method_out_but_show_it():
    inputs = make(fcf_history=(10.0,), analyst=consensus(150.0))
    a = replace(A, method_weights=MethodWeights(dcf=0.0, analyst=-1.0, multiple=0.2, dividend=0.15))
    v = value(inputs, a)
    assert v.blended is None and v.reason == "All method weights are zero"
    assert method(v, Method.DCF).value == pytest.approx(216.7659, abs=1e-4)
    a2 = replace(A, method_weights=MethodWeights(dcf=0.0, analyst=0.3, multiple=0.2, dividend=0.15))
    v2 = value(inputs, a2)
    assert v2.blended == pytest.approx(150.0)
    assert method(v2, Method.DCF).weight == 0.0


@pytest.mark.parametrize(
    ("blended", "label"),
    [(115.0, ValuationLabel.UNDERVALUED), (114.0, ValuationLabel.FAIR),
     (85.0, ValuationLabel.OVERVALUED), (86.0, ValuationLabel.FAIR)],
)
def test_margin_label_boundaries(blended, label):
    inputs = make(price=100.0, analyst=consensus(blended))
    assert value(inputs, A).label is label


# --- defaults ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("currency", "rate", "terminal"),
    [(Currency.USD, 0.09, 0.025), (Currency.GBP, 0.095, 0.025), (Currency.BRL, 0.14, 0.04)],
)
def test_default_rates_per_market(currency, rate, terminal):
    a = default_assumptions(make(currency=currency))
    assert (a.discount_rate, a.terminal_growth) == (rate, terminal)
    assert a.method_weights == MethodWeights()


@pytest.mark.parametrize(("growth", "expected"), [(None, 0.05), (0.164, 0.15), (-0.2, 0.0), (0.08, 0.08)])
def test_default_growth_clamped(growth, expected):
    assert default_assumptions(make(revenue_growth=growth)).growth_1_5 == expected


def test_default_dividend_growth_from_cagr_and_clamped():
    rising = (Dividend(date(2026, 6, 1), 2.0), Dividend(date(2021, 6, 1), 1.0))  # CAGR 14.87%
    assert default_assumptions(make(dividends=rising)).dividend_growth == pytest.approx(0.07)  # 9% - 2%
    gentle = (Dividend(date(2026, 6, 1), 1.1), Dividend(date(2021, 6, 1), 1.0))
    assert default_assumptions(make(dividends=gentle)).dividend_growth == pytest.approx(1.1 ** 0.2 - 1)
    falling = (Dividend(date(2026, 6, 1), 0.5), Dividend(date(2021, 6, 1), 1.0))
    assert default_assumptions(make(dividends=falling)).dividend_growth == 0.0
    assert default_assumptions(make()).dividend_growth == 0.03


def test_default_fii_and_etf():
    fii = default_assumptions(make(asset_type=AssetType.FII, currency=Currency.BRL))
    assert (fii.discount_rate, fii.dividend_growth, fii.terminal_growth) == (0.12, 0.03, 0.04)
    assert fii.method_weights == MethodWeights(0.0, 0.0, 0.0, 1.0)
    etf = default_assumptions(make(asset_type=AssetType.ETF))
    assert etf.method_weights == MethodWeights(0.0, 0.0, 0.0, 0.0)


def test_assumptions_round_trip():
    assert assumptions_from_dict(assumptions_to_dict(A)) == A
