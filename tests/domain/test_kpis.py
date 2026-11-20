from dataclasses import replace
from datetime import date

import pytest

from obb_terminal.domain.kpis import kpis
from obb_terminal.domain.models import AssetType, Currency, KpiFormat, Mark, ValuationInputs


def make(**changes) -> ValuationInputs:
    base = ValuationInputs(
        symbol="TEST", name="Test plc", asset_type=AssetType.EQUITY, currency=Currency.USD,
        as_of=date(2026, 10, 9), price=100.0, shares_outstanding=1.0, fx_rate=1.0,
    )
    return replace(base, **changes)


def by_key(inputs):
    return {k.key: k for k in kpis(inputs)}


def test_twelve_kpis_in_order_all_missing_without_data():
    rows = kpis(make())
    assert [k.label for k in rows] == [
        "P/E", "Forward P/E", "PEG", "EV/EBITDA", "P/B", "ROE", "Operating margin",
        "Debt/Equity", "Current ratio", "Revenue growth", "Dividend yield", "Payout ratio",
    ]
    assert all(k.mark is Mark.MISSING and k.value is None for k in rows)


def test_pe_and_pb_computed_from_eps_and_bvps():
    k = by_key(make(net_income=5.0, book_value_ps=50.0))
    assert k["pe"].value == pytest.approx(20.0) and k["pe"].mark is Mark.NEUTRAL
    assert k["pb"].value == pytest.approx(2.0) and k["pb"].fmt is KpiFormat.MULTIPLE


def test_pe_negative_earnings_note():
    pe = by_key(make(net_income=-5.0))["pe"]
    assert (pe.value, pe.mark, pe.note) == (None, Mark.MISSING, "negative earnings")


def test_pe_uses_fx_for_cross_currency():
    k = by_key(make(net_income=10.0, fx_rate=0.5, book_value_ps=40.0))  # EPS 5, BVPS 20
    assert k["pe"].value == pytest.approx(20.0)
    assert k["pb"].value == pytest.approx(5.0)


def test_yahoo_note_on_cross_currency_ratios():
    k = by_key(make(forward_pe=9.0, yahoo_cross_currency=True))
    assert k["forward_pe"].note == "Yahoo"
    assert k["peg"].note == "Yahoo" and k["ev_ebitda"].note == "Yahoo"
    assert k["pe"].note is None


@pytest.mark.parametrize(
    ("field", "key", "good", "neutral", "weak"),
    [
        ("forward_pe", "forward_pe", 15, 20, 30),
        ("peg_ratio", "peg", 1, 1.5, 2),
        ("ev_to_ebitda", "ev_ebitda", 10, 15, 20),
        ("return_on_equity", "roe", 0.15, 0.10, 0.05),
        ("operating_margin", "operating_margin", 0.20, 0.10, 0.05),
        ("debt_to_equity", "debt_to_equity", 0.5, 1.0, 2.0),
        ("current_ratio", "current_ratio", 1.5, 1.2, 1.0),
        ("revenue_growth", "revenue_growth", 0.10, 0.05, 0.0),
        ("payout_ratio", "payout_ratio", 0.60, 0.80, 1.0),
    ],
)
def test_thresholds_inclusive(field, key, good, neutral, weak):
    assert by_key(make(**{field: good}))[key].mark is Mark.GOOD
    assert by_key(make(**{field: neutral}))[key].mark is Mark.NEUTRAL
    assert by_key(make(**{field: weak}))[key].mark is Mark.WEAK


def test_negative_multiple_is_weak():
    assert by_key(make(forward_pe=-4.0))["forward_pe"].mark is Mark.WEAK


@pytest.mark.parametrize(("dy", "mark"), [(0.03, Mark.GOOD), (0.01, Mark.NEUTRAL), (0.0, Mark.NEUTRAL), (None, Mark.MISSING)])
def test_dividend_yield_marks(dy, mark):
    assert by_key(make(dividend_yield=dy))["dividend_yield"].mark is mark


def test_negative_debt_to_equity_is_weak_with_note():
    de = by_key(make(debt_to_equity=-1.5))["debt_to_equity"]
    assert (de.value, de.mark, de.note) == (-1.5, Mark.WEAK, "negative equity")


def test_pb_negative_book_value_is_missing_with_note():
    pb = by_key(make(book_value_ps=-5.0))["pb"]
    assert (pb.value, pb.mark, pb.note) == (None, Mark.MISSING, "negative book value")


def test_negative_payout_is_weak_with_note():
    po = by_key(make(payout_ratio=-0.4))["payout_ratio"]
    assert (po.value, po.mark, po.note) == (-0.4, Mark.WEAK, "loss-making")
