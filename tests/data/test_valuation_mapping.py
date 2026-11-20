from datetime import UTC, date, datetime

import pytest

from obb_terminal.data.market import NoData
from obb_terminal.data.openbb_mapping import (
    _KM_SCALE,
    fx_pair,
    reporting_currency,
    to_consensus,
    to_fx_rate,
    to_valuation_inputs,
)
from obb_terminal.domain.models import AssetType, Currency
from tests.data import valuation_samples as v

TODAY = date(2026, 10, 10)


def build(symbol, *, quote, info=(), km=(), cf=(), income=(), targets=(), divs=(), fx=1.0, last=None, gaps=()):
    return to_valuation_inputs(
        symbol, quote_rows=list(quote), info_rows=list(info), km_rows=list(km),
        cash_flow_rows=list(cf), income_rows=list(income), target_rows=list(targets),
        dividend_rows=list(divs), fx_rate=fx, fallback_last=last, today=TODAY, gaps=tuple(gaps),
    )


def test_aapl_full_mapping():
    i = build("AAPL", quote=v.AAPL_QUOTE, info=v.AAPL_INFO, km=v.AAPL_KEY_METRICS, cf=v.AAPL_CASH_FLOW,
              income=v.AAPL_INCOME, targets=v.AAPL_TARGETS, divs=v.AAPL_DIVIDENDS)
    assert (i.asset_type, i.currency, i.price, i.as_of) == (AssetType.EQUITY, Currency.USD, 336.64, TODAY)
    assert i.ma_200d == pytest.approx(290.74344)
    assert i.shares_outstanding == 14594180000
    assert i.reporting_currency == "USD" and i.fx_rate == 1.0 and not i.yahoo_cross_currency
    assert i.fcf_history == (98767000000.0, 108807000000.0, 99584000000.0, 111443000000.0)
    assert i.net_income == 112010000000.0
    assert i.book_value_ps == 7.36
    assert i.debt_to_equity == pytest.approx(0.78445)
    assert i.dividend_yield == pytest.approx(0.0032)
    assert i.price_return_1y == pytest.approx(0.35928285)
    assert i.return_on_equity == pytest.approx(1.4875101)
    assert i.ev_to_ebitda == pytest.approx(29.382)
    assert i.analyst.target_consensus == pytest.approx(328.09384)
    assert i.analyst.number_of_analysts == 39
    assert [d.amount for d in i.dividends] == [0.27, 0.27, 0.26, 0.26]  # newest first


def test_shel_scales_pence_and_flags_cross_currency():
    i = build("SHEL.L", quote=v.SHEL_QUOTE, info=v.SHEL_INFO, km=v.SHEL_KEY_METRICS, cf=v.SHEL_CASH_FLOW,
              income=v.SHEL_INCOME, targets=v.SHEL_TARGETS, divs=v.SHEL_DIVIDENDS, fx=0.75564)
    assert i.currency is Currency.GBP
    assert i.price == pytest.approx(37.935)
    assert i.ma_200d == pytest.approx(32.02075)
    assert i.reporting_currency == "USD" and i.fx_rate == 0.75564 and i.yahoo_cross_currency
    assert i.book_value_ps == pytest.approx(24.561403)  # stays in USD (REPORTING)
    assert i.analyst.target_consensus == pytest.approx(40.22854)
    assert i.analyst.target_low == pytest.approx(34.869006)
    assert i.dividends[0].amount == pytest.approx(0.2892)


def test_hglg_fii_without_targets():
    i = build("HGLG11.SA", quote=v.HGLG_QUOTE, info=v.HGLG_INFO, km=v.HGLG_KEY_METRICS, cf=v.HGLG_CASH_FLOW,
              income=v.HGLG_INCOME, divs=v.HGLG_DIVIDENDS)
    assert i.asset_type is AssetType.FII
    assert i.analyst is None
    assert i.debt_to_equity is None and i.forward_pe is None
    assert i.fcf_history == (327032000.0, 276745000.0, 231668000.0)


def test_spy_etf_falls_back_to_last_close_and_minimal_fields():
    i = build("SPY", quote=v.SPY_QUOTE, info=v.SPY_INFO, last=777.0, gaps=("km",))
    assert (i.asset_type, i.price, i.reporting_currency) == (AssetType.ETF, 777.0, None)
    assert i.fcf_history == () and i.net_income is None and i.analyst is None
    assert i.gaps == ("km",)


def test_missing_price_is_no_data():
    with pytest.raises(NoData):
        build("SPY", quote=v.SPY_QUOTE)
    with pytest.raises(NoData):
        build("SPY", quote=[])


@pytest.mark.parametrize("bad", [0.0, -1.5, float("nan"), float("inf")])
def test_non_positive_or_non_finite_price_is_no_data(bad):
    quote = [{**v.AAPL_QUOTE[0], "last_price": bad}]
    with pytest.raises(NoData):
        build("AAPL", quote=quote, info=v.AAPL_INFO)
    with pytest.raises(NoData):  # same rule for the last-close fallback
        build("SPY", quote=v.SPY_QUOTE, info=v.SPY_INFO, last=bad)


def test_km_scale_covers_percentage_fields():
    assert _KM_SCALE["debt_to_equity"] == ("debt_to_equity", 0.01)
    assert _KM_SCALE["dividend_yield"] == ("dividend_yield", 0.01)
    assert _KM_SCALE["price_return_1y"] == ("price_return_1y", 1.0)


def test_reporting_currency_and_fx_pair():
    assert reporting_currency(v.SHEL_KEY_METRICS) == "USD"
    assert reporting_currency([]) is None
    assert fx_pair("USD", Currency.GBP) == ("USDGBP", 1.0)
    assert fx_pair("USD", Currency.USD) == (None, 1.0)
    assert fx_pair(None, Currency.BRL) == (None, 1.0)
    assert fx_pair("GBp", Currency.GBP) == (None, 0.01)
    assert fx_pair("GBp", Currency.USD) == ("GBPUSD", 0.01)


def test_fx_rate_takes_last_close_by_date():
    assert to_fx_rate(v.USDGBP) == pytest.approx(0.7556399703025818)
    assert to_fx_rate([]) is None
    assert to_fx_rate([{"date": "2026-10-10", "close": 0.0}]) is None
    assert to_fx_rate([{"date": "2026-10-10", "close": float("nan")}]) is None


def test_consensus_without_row_currency_uses_quote_factor():
    c = to_consensus([{"target_consensus": 4000.0, "number_of_analysts": 5}], 0.01)
    assert c.target_consensus == pytest.approx(40.0)
    assert to_consensus([], 1.0) is None


# --- v1.2 watchlist fields ------------------------------------------------------------------


def build_v12(symbol, *, quote, income=(), balance=None, fetched_at=None, fx=1.0):
    return to_valuation_inputs(
        symbol, quote_rows=list(quote), info_rows=[], km_rows=[], cash_flow_rows=[],
        income_rows=list(income), target_rows=[], dividend_rows=[], fx_rate=fx,
        fallback_last=None, today=TODAY, balance_rows=balance, fetched_at=fetched_at,
    )


def test_ebitda_debt_and_cash_are_reporting_currency():
    i = build_v12("SHEL.L", quote=v.SHEL_QUOTE, income=v.SHEL_INCOME_EBITDA, balance=v.SHEL_BALANCE, fx=0.7557)
    assert i.ebitda == 56664000000.0  # newest annual, not converted
    assert (i.total_debt, i.cash) == (75643000000.0, 29630000000.0)


def test_missing_balance_sheet_gives_none():
    i = build_v12("AAPL", quote=v.AAPL_QUOTE, income=v.AAPL_INCOME_EBITDA, balance=None)
    assert (i.total_debt, i.cash) == (None, None)
    assert build_v12("AAPL", quote=v.AAPL_QUOTE, balance=[]).total_debt is None


def test_balance_row_without_debt_key_means_zero_debt():
    i = build_v12("HGLG11.SA", quote=v.HGLG_QUOTE, balance=v.HGLG_BALANCE)
    assert (i.total_debt, i.cash) == (0.0, 12053000.0)


def test_no_ebitda_key_gives_none():
    assert build_v12("AAPL", quote=v.AAPL_QUOTE, income=v.AAPL_INCOME).ebitda is None


def test_year_range_scaled_from_pence_and_fetched_at_kept():
    at = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)
    i = build_v12("SHEL.L", quote=v.SHEL_QUOTE_RANGE, fetched_at=at)
    assert (i.year_high, i.year_low) == (pytest.approx(40.62), pytest.approx(25.885))
    assert i.fetched_at == at
    assert build_v12("AAPL", quote=v.AAPL_QUOTE).year_high is None
