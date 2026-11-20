from datetime import UTC, date, datetime

import pytest

from obb_terminal.data.market import DataError, NoData, SourceUnavailable, SymbolNotFound
from obb_terminal.data.openbb_mapping import (
    map_exception,
    to_dividends,
    to_financials,
    to_fund_info,
    to_profile,
    to_quote,
    to_series,
    to_stats,
)
from obb_terminal.domain.models import AssetType, Currency, Period, Range, Statement
from tests.data import samples as s


def test_to_quote_scales_pence_to_gbp():
    q = to_quote(s.SHEL_QUOTE, "SHEL.L")
    assert q.currency is Currency.GBP
    assert q.last == pytest.approx(37.935)
    assert q.prev_close == pytest.approx(37.805)
    assert q.change == pytest.approx(0.13)
    assert q.change_pct == pytest.approx(0.13 / 37.805)
    assert q.year_low == pytest.approx(25.53772)
    assert q.volume == 10913950  # never scaled
    assert q.avg_volume == 10045201


def test_to_quote_usd_unscaled():
    q = to_quote(s.AAPL_QUOTE, "AAPL")
    assert q.currency is Currency.USD
    assert q.last == 336.64
    assert q.change == pytest.approx(336.64 - 340.42)


def test_to_quote_uses_fallback_last_when_missing():
    q = to_quote(s.SPY_QUOTE, "SPY", fallback_last=777.0)
    assert q.last == 777.0
    assert q.change == pytest.approx(777.0 - 773.93)


def test_to_quote_without_any_last_raises_no_data():
    with pytest.raises(NoData):
        to_quote(s.SPY_QUOTE, "SPY")


def test_to_quote_empty_rows_raises_no_data():
    with pytest.raises(NoData):
        to_quote([], "ZZZZ.SA")


def test_to_profile_fii():
    p = to_profile(s.HGLG_INFO, s.HGLG_QUOTE, "HGLG11.SA")
    assert p.asset_type is AssetType.FII
    assert p.currency is Currency.BRL
    assert p.exchange == "SAO"


def test_to_profile_bova_is_etf_despite_equity_issue_type():
    assert to_profile(s.BOVA_INFO, [], "BOVA11.SA").asset_type is AssetType.ETF


def test_to_profile_equity_fields():
    p = to_profile(s.AAPL_INFO, s.AAPL_QUOTE, "AAPL")
    assert (p.name, p.asset_type, p.currency) == ("Apple Inc.", AssetType.EQUITY, Currency.USD)
    assert p.sector == "Technology"
    assert p.website == "https://www.apple.com"


def test_to_profile_from_quote_only():
    p = to_profile([], s.SPY_QUOTE, "SPY")
    assert p.asset_type is AssetType.ETF
    assert p.name == "State Street SPDR S&P 500 ETF Trust"


def test_to_profile_both_empty_raises_symbol_not_found():
    with pytest.raises(SymbolNotFound):
        to_profile([], [], "ZZZZ.SA")


def test_to_stats_percent_yield_becomes_fraction_and_market_cap_unscaled():
    st = to_stats(s.SHEL_KEY_METRICS, [], "SHEL.L", Currency.GBP)
    assert st.dividend_yield == pytest.approx(0.0306)
    assert st.market_cap == 215941365760
    assert st.pe_ratio == pytest.approx(11.223372)
    assert st.currency is Currency.GBP


def test_to_stats_falls_back_to_info():
    st = to_stats([], s.SPY_INFO, "SPY", Currency.USD)
    assert st.dividend_yield == pytest.approx(0.0099)
    assert st.market_cap is None


def test_to_stats_both_empty_raises():
    with pytest.raises(NoData):
        to_stats([], [], "BOVA11.SA", Currency.BRL)


def test_to_series_daily_sorted_and_scaled():
    series = to_series(s.SHEL_DAILY, "SHEL.L", Range.M1, "GBp")
    assert series.currency is Currency.GBP
    assert [p.close for p in series.points] == pytest.approx([36.11, 36.50, 37.00])
    assert series.points[0].at == datetime(2026, 9, 25, tzinfo=UTC)


def test_to_series_d1_keeps_only_last_session_wall_time():
    series = to_series(s.SHEL_5M, "SHEL.L", Range.D1, "GBp")
    assert [p.at for p in series.points] == [
        datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
        datetime(2026, 10, 9, 16, 25, tzinfo=UTC),
    ]


def test_to_series_empty_raises():
    with pytest.raises(NoData):
        to_series([], "AAPL", Range.Y1, "USD")


def test_to_financials_newest_first_with_labels():
    t = to_financials(s.AAPL_INCOME, "AAPL", Statement.INCOME, Period.ANNUAL)
    assert t.periods == (date(2025, 9, 30), date(2024, 9, 28))
    assert [r.key for r in t.rows] == ["total_revenue", "net_income"]
    assert t.rows[0].label == "Total revenue"
    assert t.rows[0].values == (416161000000.0, 391035000000.0)
    assert t.currency == "USD"


def test_to_financials_skips_rows_without_period_ending():
    rows = [*s.AAPL_INCOME, {"fiscal_year": 2023, "total_revenue": 1.0}]
    t = to_financials(rows, "AAPL", Statement.INCOME, Period.ANNUAL)
    assert t.periods == (date(2025, 9, 30), date(2024, 9, 28))
    assert t.rows[0].values == (416161000000.0, 391035000000.0)


def test_to_financials_without_any_period_ending_is_no_data():
    with pytest.raises(NoData):
        to_financials([{"total_revenue": 1.0}], "AAPL", Statement.INCOME, Period.ANNUAL)


def test_to_dividends_scaled_newest_first():
    divs = to_dividends(s.SHEL_DIVIDENDS, "GBp")
    assert [d.ex_date for d in divs] == [date(2026, 5, 14), date(2026, 2, 12), date(2025, 5, 15)]
    assert divs[0].amount == pytest.approx(0.275)


def test_to_fund_info_ttm_and_etf_fields():
    divs = to_dividends(s.HGLG_DIVIDENDS, "BRL")
    info = to_fund_info("HGLG11.SA", Currency.BRL, divs, 157.58, [], date(2026, 10, 9))
    assert info.ttm_yield == pytest.approx(2.20 / 157.58)
    assert info.total_assets is None
    spy = to_fund_info("SPY", Currency.USD, (), 777.0, s.SPY_ETF_INFO, date(2026, 10, 9))
    assert spy.total_assets == 809216114688
    assert spy.ttm_yield == 0.0


class EmptyDataError(Exception):
    pass


class YFRateLimitError(Exception):
    pass


class OpenBBError(Exception):
    pass


RequestsConnectionError = type("ConnectionError", (Exception,), {"__module__": "requests.exceptions"})


def test_map_exception_empty_data_to_no_data():
    err = map_exception(EmptyDataError("x"), "AAPL")
    assert isinstance(err, NoData)
    assert err.user_message == "No data available for AAPL"


def test_map_exception_rate_limit():
    err = map_exception(YFRateLimitError("slow down"), "AAPL")
    assert isinstance(err, SourceUnavailable) and err.reason == "rate_limited"


@pytest.mark.parametrize("exc", [OSError("down"), RequestsConnectionError("refused")])
def test_map_exception_network(exc):
    err = map_exception(exc, "AAPL")
    assert isinstance(err, SourceUnavailable) and err.reason == "network"


@pytest.mark.parametrize(
    "message",
    ["BOVA11.SA is not an ETF.", "Error getting data for BOVA11.SA: No dividend data found for BOVA11.SA"],
)
def test_map_exception_openbb_no_data_messages(message):
    assert isinstance(map_exception(OpenBBError(message), "BOVA11.SA"), NoData)


def test_map_exception_unknown_is_plain_data_error():
    err = map_exception(RuntimeError("weird"), "AAPL")
    assert type(err) is DataError
    assert err.user_message == "Unexpected source response"


def test_map_exception_passes_data_errors_through():
    original = NoData("x")
    assert map_exception(original) is original


CURL_OFFLINE = (
    "Error getting data for AAPL -> ConnectionError: Failed to perform, curl: (7) "
    "Failed to connect to query2.finance.yahoo.com port 443 after 0 ms: Could not connect to server."
)


@pytest.mark.parametrize(
    "message",
    [
        CURL_OFFLINE,
        "Error getting data for AAPL -> curl: (6) Could not resolve host: query2.finance.yahoo.com",
        "Error getting data for AAPL -> ConnectionError: Max retries exceeded",
    ],
)
def test_map_exception_openbb_wrapped_network_error_is_offline(message):
    err = map_exception(OpenBBError(message), "AAPL")
    assert isinstance(err, SourceUnavailable) and err.reason == "network"


def test_map_exception_openbb_network_error_wins_over_no_data_wording():
    # "Error getting data … not found" style wording must not hide a connection failure.
    err = map_exception(OpenBBError(CURL_OFFLINE + " Symbol not found"), "AAPL")
    assert isinstance(err, SourceUnavailable) and err.reason == "network"


@pytest.mark.parametrize(
    "message",
    [
        "Error getting data for AAPL -> YFRateLimitError: Too Many Requests. Rate limited.",
        "Error getting data for AAPL -> 429 Too Many Requests",
    ],
)
def test_map_exception_openbb_wrapped_rate_limit(message):
    err = map_exception(OpenBBError(message), "AAPL")
    assert isinstance(err, SourceUnavailable) and err.reason == "rate_limited"
