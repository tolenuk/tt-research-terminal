from datetime import date

import pytest

from obb_terminal.data.market import DataError, NoData, SourceUnavailable, SymbolNotFound
from obb_terminal.data.openbb_source import OpenBBSource, ReachabilityProbe
from obb_terminal.data.yahoo_news import YahooNews
from obb_terminal.data.yahoo_search import YahooSearch
from obb_terminal.domain.models import AssetType, Currency, Period, Range, Statement
from tests.data import samples as s


class EmptyDataError(Exception):
    pass


class OpenBBError(Exception):
    pass


class FakeFetch:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, model, params):
        self.calls.append((model, dict(params)))
        result = self.responses.get((model, params.get("symbol")), [])
        if isinstance(result, BaseException):
            raise result
        return result


def make_source(responses, *, reachable=True):
    source = OpenBBSource(
        engine=None,
        search=YahooSearch(lambda q, n: s.PETROBRAS_SEARCH),
        news=YahooNews(lambda url: s.AAPL_RSS),
        today=lambda: date(2026, 10, 9),
        reachable=lambda: reachable,
    )
    fetch = FakeFetch(responses)
    source._fetch = fetch
    return source, fetch


def models(fetch):
    return [model for model, _ in fetch.calls]


def test_quote_scales_pence():
    source, _ = make_source({("EquityQuote", "SHEL.L"): s.SHEL_QUOTE})
    q = source.quote("SHEL.L")
    assert q.currency is Currency.GBP
    assert q.last == pytest.approx(37.935)


def test_quote_without_last_price_falls_back_to_history_close():
    source, fetch = make_source(
        {("EquityQuote", "SPY"): s.SPY_QUOTE, ("EquityHistorical", "SPY"): s.SPY_DAILY}
    )
    assert source.quote("SPY").last == 777.0
    assert fetch.calls[1] == (
        "EquityHistorical",
        {"symbol": "SPY", "interval": "1d", "start_date": "2026-10-02"},
    )


def test_profile_remembers_currency_for_history():
    source, fetch = make_source(
        {
            ("EquityInfo", "HGLG11.SA"): s.HGLG_INFO,
            ("EquityQuote", "HGLG11.SA"): s.HGLG_QUOTE,
            ("EquityHistorical", "HGLG11.SA"): [{"date": "2026-10-08", "close": 157.0}],
        }
    )
    assert source.profile("HGLG11.SA").asset_type is AssetType.FII
    series = source.history("HGLG11.SA", Range.Y1)
    assert series.currency is Currency.BRL
    assert models(fetch) == ["EquityInfo", "EquityQuote", "EquityHistorical"]
    assert fetch.calls[-1][1] == {"symbol": "HGLG11.SA", "interval": "1d", "start_date": "2025-10-08"}


def test_history_all_fetches_monthly_bars_from_the_full_record():
    source, fetch = make_source(
        {("EquityQuote", "SHEL.L"): s.SHEL_QUOTE, ("EquityHistorical", "SHEL.L"): s.SHEL_DAILY}
    )
    series = source.history("SHEL.L", Range.ALL)
    assert series.range is Range.ALL
    params = fetch.calls[0][1]
    assert params["interval"] == "1M"
    assert params["start_date"] <= "1930-01-01"  # earlier than any listing Yahoo carries


def test_history_fetches_quote_first_when_currency_unknown():
    source, fetch = make_source(
        {("EquityQuote", "SHEL.L"): s.SHEL_QUOTE, ("EquityHistorical", "SHEL.L"): s.SHEL_DAILY}
    )
    series = source.history("SHEL.L", Range.M1)
    assert models(fetch) == ["EquityHistorical", "EquityQuote"]
    assert series.points[0].close == pytest.approx(36.11)


def test_profile_unknown_symbol():
    source, _ = make_source({("EquityInfo", "ZZZZ.SA"): EmptyDataError("none")})
    with pytest.raises(SymbolNotFound):
        source.profile("ZZZZ.SA")


def test_stats_falls_back_to_info_for_etfs():
    source, fetch = make_source(
        {
            ("KeyMetrics", "SPY"): EmptyDataError("none"),
            ("EquityInfo", "SPY"): s.SPY_INFO,
            ("EquityQuote", "SPY"): s.SPY_QUOTE,
            ("EquityHistorical", "SPY"): s.SPY_DAILY,
        }
    )
    stats = source.stats("SPY")
    assert stats.dividend_yield == pytest.approx(0.0099)
    assert stats.currency is Currency.USD


def test_financials_params():
    source, fetch = make_source({("BalanceSheet", "AAPL"): s.AAPL_INCOME})
    table = source.financials("AAPL", Statement.BALANCE, Period.QUARTER)
    assert fetch.calls == [("BalanceSheet", {"symbol": "AAPL", "period": "quarter", "limit": 5})]
    assert table.statement is Statement.BALANCE


def test_fund_info_etf_without_dividends_is_empty_not_error():
    source, _ = make_source(
        {
            ("EquityQuote", "BOVA11.SA"): s.BOVA_QUOTE,
            ("HistoricalDividends", "BOVA11.SA"): OpenBBError(
                "Error getting data for BOVA11.SA: No dividend data found for BOVA11.SA"
            ),
            ("EtfInfo", "BOVA11.SA"): OpenBBError("BOVA11.SA is not an ETF."),
        }
    )
    info = source.fund_info("BOVA11.SA")
    assert info.dividends == ()
    assert info.ttm_yield == 0.0


def test_fund_info_fii_ttm_yield():
    source, _ = make_source(
        {
            ("EquityQuote", "HGLG11.SA"): s.HGLG_QUOTE,
            ("HistoricalDividends", "HGLG11.SA"): s.HGLG_DIVIDENDS,
        }
    )
    info = source.fund_info("HGLG11.SA")
    assert info.ttm_yield == pytest.approx(2.20 / 157.58)
    assert info.dividends[0].ex_date == date(2026, 9, 1)


def test_network_error_maps_to_source_unavailable():
    source, _ = make_source({("EquityQuote", "AAPL"): OSError("no route")})
    with pytest.raises(SourceUnavailable) as info:
        source.quote("AAPL")
    assert info.value.reason == "network"


def test_unknown_quote_is_no_data():
    source, _ = make_source({})
    with pytest.raises(NoData):
        source.quote("ZZZZ.SA")


def test_search_and_news_do_not_touch_openbb():
    source, fetch = make_source({})
    assert [m.symbol for m in source.search("Petrobras")][1] == "PETR4.SA"
    assert [n.title for n in source.news("AAPL")] == ["Newer story", "Older story"]
    assert fetch.calls == []


# --- real offline shapes (C1) -------------------------------------------------------------

CURL_OFFLINE = (
    "Error getting data for AAPL -> ConnectionError: Failed to perform, curl: (7) "
    "Failed to connect to query2.finance.yahoo.com port 443 after 0 ms: Could not connect to server."
)


def assert_offline(call):
    with pytest.raises(SourceUnavailable) as info:
        call()
    assert info.value.reason == "network"


def test_offline_profile_with_wrapped_curl_error_and_empty_quote():
    # EquityInfo raises OpenBBError(curl); EquityQuote swallows the error and returns [].
    source, _ = make_source(
        {("EquityInfo", "AAPL"): OpenBBError(CURL_OFFLINE), ("EquityQuote", "AAPL"): []},
        reachable=False,
    )
    assert_offline(lambda: source.profile("AAPL"))


def test_offline_stats_with_wrapped_curl_error():
    source, _ = make_source({("KeyMetrics", "AAPL"): OpenBBError(CURL_OFFLINE)}, reachable=False)
    assert_offline(lambda: source.stats("AAPL"))


def test_offline_quote_empty_rows_with_unreachable_probe():
    source, _ = make_source({("EquityQuote", "AAPL"): []}, reachable=False)
    assert_offline(lambda: source.quote("AAPL"))


def test_offline_history_empty_frame_with_unreachable_probe():
    source, _ = make_source(
        {("EquityHistorical", "AAPL"): EmptyDataError("empty"), ("EquityQuote", "AAPL"): []},
        reachable=False,
    )
    assert_offline(lambda: source.history("AAPL", Range.Y1))


def test_offline_financials_empty_with_unreachable_probe():
    source, _ = make_source({("IncomeStatement", "AAPL"): EmptyDataError("empty")}, reachable=False)
    assert_offline(lambda: source.financials("AAPL", Statement.INCOME, Period.ANNUAL))


def test_offline_fund_info_with_unreachable_probe():
    source, _ = make_source({("EquityQuote", "SPY"): []}, reachable=False)
    assert_offline(lambda: source.fund_info("SPY"))


def test_genuine_no_data_with_reachable_probe_stays_no_data():
    source, _ = make_source({("EquityHistorical", "ZZZZ.SA"): EmptyDataError("empty")})
    with pytest.raises(NoData):
        source.financials("ZZZZ.SA", Statement.INCOME, Period.ANNUAL)
    with pytest.raises(NoData):
        source.history("ZZZZ.SA", Range.Y1)


def test_probe_not_consulted_when_data_arrives():
    calls = []
    source = OpenBBSource(
        engine=None,
        search=YahooSearch(lambda q, n: []),
        news=YahooNews(lambda url: s.AAPL_RSS),
        reachable=lambda: calls.append(1) or True,
    )
    source._fetch = FakeFetch({("EquityQuote", "SHEL.L"): s.SHEL_QUOTE})
    source.quote("SHEL.L")
    assert calls == []


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_reachability_probe_memoises_result_for_ttl():
    attempts = []

    def connect(address, timeout):
        attempts.append((address, timeout))
        raise OSError("network is unreachable")

    clock = FakeClock()
    probe = ReachabilityProbe(connect=connect, ttl_s=10.0, clock=clock)
    assert probe() is False
    assert probe() is False
    assert attempts == [(("query2.finance.yahoo.com", 443), 3.0)]
    clock.now += 10.5
    assert probe() is False
    assert len(attempts) == 2


def test_reachability_probe_reports_reachable_and_closes_socket():
    closed = []

    class Conn:
        def close(self):
            closed.append(True)

    probe = ReachabilityProbe(connect=lambda address, timeout: Conn(), clock=FakeClock())
    assert probe() is True
    assert closed == [True]


def test_reachability_probe_timeout_counts_as_unreachable():
    def connect(address, timeout):
        raise TimeoutError("timed out")

    assert ReachabilityProbe(connect=connect, clock=FakeClock())() is False


# --- drifted / malformed rows never escape as bare exceptions (I5) ------------------------


def assert_unexpected(call):
    with pytest.raises(DataError) as info:
        call()
    assert type(info.value) is DataError
    assert info.value.user_message == "Unexpected source response"


def test_malformed_quote_row_is_data_error():
    source, _ = make_source({("EquityQuote", "AAPL"): [{"last_price": "n/a", "currency": "USD"}]})
    assert_unexpected(lambda: source.quote("AAPL"))


def test_malformed_fallback_history_row_is_data_error():
    source, _ = make_source(
        {("EquityQuote", "SPY"): s.SPY_QUOTE, ("EquityHistorical", "SPY"): [{"close": 1.0}]}
    )
    assert_unexpected(lambda: source.quote("SPY"))


def test_malformed_profile_row_is_data_error():
    source, _ = make_source({("EquityInfo", "AAPL"): [{"issue_type": 5, "currency": "USD"}]})
    assert_unexpected(lambda: source.profile("AAPL"))


def test_malformed_stats_row_is_data_error():
    source, _ = make_source(
        {("KeyMetrics", "SPY"): [{"market_cap": "big"}], ("EquityQuote", "SPY"): s.SHEL_QUOTE}
    )
    assert_unexpected(lambda: source.stats("SPY"))


def test_malformed_history_row_is_data_error():
    source, _ = make_source(
        {("EquityQuote", "SHEL.L"): s.SHEL_QUOTE, ("EquityHistorical", "SHEL.L"): [{"close": 1.0}]}
    )
    assert_unexpected(lambda: source.history("SHEL.L", Range.Y1))


def test_malformed_financials_row_is_data_error():
    source, _ = make_source(
        {("IncomeStatement", "AAPL"): [{"period_ending": "not-a-date", "total_revenue": 1.0}]}
    )
    assert_unexpected(lambda: source.financials("AAPL", Statement.INCOME, Period.ANNUAL))


def test_malformed_dividend_row_is_data_error():
    source, _ = make_source(
        {
            ("EquityQuote", "HGLG11.SA"): s.HGLG_QUOTE,
            ("HistoricalDividends", "HGLG11.SA"): [{"amount": 1.0}],
        }
    )
    assert_unexpected(lambda: source.fund_info("HGLG11.SA"))
