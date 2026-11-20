import logging
import threading
from datetime import UTC, date, datetime

import pytest

from obb_terminal.data.market import DataError, NoData, SourceUnavailable
from obb_terminal.data.openbb_source import FUND_TTL_S, OpenBBSource
from obb_terminal.data.yahoo_funds import NoFundData, YahooFunds
from obb_terminal.data.yahoo_news import YahooNews
from obb_terminal.data.yahoo_search import YahooSearch
from obb_terminal.domain.models import AssetType, Currency
from tests.data import samples as s
from tests.data import valuation_samples as v


class EmptyDataError(Exception):
    pass


class LockedFetch:
    """Thread-safe fake `_fetch`: `_gather` calls it from several threads at once."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, model, params):
        with self._lock:
            self.calls.append((model, dict(params)))
        result = self.responses.get((model, params.get("symbol")), [])
        if callable(result) and not isinstance(result, BaseException):
            result = result()
        if isinstance(result, BaseException):
            raise result
        return result

    def models(self):
        with self._lock:
            return [m for m, _ in self.calls]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


NOW = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)


def no_funds(symbol):
    raise NoFundData(symbol)


def make_source(responses, *, reachable=True, clock=None, funds=no_funds):
    source = OpenBBSource(
        engine=None,
        search=YahooSearch(lambda q, n: []),
        news=YahooNews(lambda url: s.EMPTY_RSS),
        today=lambda: date(2026, 10, 10),
        reachable=lambda: reachable,
        clock=clock or Clock(),
        now=lambda: NOW,
        funds=YahooFunds(funds),
    )
    fetch = LockedFetch(responses)
    source._fetch = fetch
    return source, fetch


AAPL = {
    ("EquityQuote", "AAPL"): v.AAPL_QUOTE,
    ("EquityInfo", "AAPL"): v.AAPL_INFO,
    ("KeyMetrics", "AAPL"): v.AAPL_KEY_METRICS,
    ("CashFlowStatement", "AAPL"): v.AAPL_CASH_FLOW,
    ("IncomeStatement", "AAPL"): v.AAPL_INCOME,
    ("PriceTargetConsensus", "AAPL"): v.AAPL_TARGETS,
    ("HistoricalDividends", "AAPL"): v.AAPL_DIVIDENDS,
    ("BalanceSheet", "AAPL"): v.AAPL_BALANCE,
}
SHEL = {
    ("EquityQuote", "SHEL.L"): v.SHEL_QUOTE,
    ("EquityInfo", "SHEL.L"): v.SHEL_INFO,
    ("KeyMetrics", "SHEL.L"): v.SHEL_KEY_METRICS,
    ("CashFlowStatement", "SHEL.L"): v.SHEL_CASH_FLOW,
    ("IncomeStatement", "SHEL.L"): v.SHEL_INCOME,
    ("PriceTargetConsensus", "SHEL.L"): v.SHEL_TARGETS,
    ("HistoricalDividends", "SHEL.L"): v.SHEL_DIVIDENDS,
    ("CurrencyHistorical", "USDGBP"): v.USDGBP,
}


def test_aapl_calls_and_params():
    source, fetch = make_source(AAPL)
    inputs = source.valuation_inputs("AAPL")
    assert inputs.price == 336.64 and inputs.fx_rate == 1.0 and inputs.gaps == ()
    assert sorted(fetch.calls) == sorted([
        ("EquityQuote", {"symbol": "AAPL"}),
        ("EquityInfo", {"symbol": "AAPL"}),
        ("KeyMetrics", {"symbol": "AAPL"}),
        ("CashFlowStatement", {"symbol": "AAPL", "period": "annual", "limit": 4}),
        ("IncomeStatement", {"symbol": "AAPL", "period": "annual", "limit": 2}),
        ("PriceTargetConsensus", {"symbol": "AAPL"}),
        ("HistoricalDividends", {"symbol": "AAPL"}),
        ("BalanceSheet", {"symbol": "AAPL", "period": "annual", "limit": 1}),
    ])


def test_phase_one_runs_in_parallel_once_the_session_exists():
    barrier = threading.Barrier(8, timeout=2)

    def waiting(rows):
        return lambda: (barrier.wait(), rows)[1]

    source, _ = make_source({key: waiting(rows) for key, rows in AAPL.items()})
    source._session_ready.set()
    assert source.valuation_inputs("AAPL").analyst is not None  # sequential calls would break the barrier


def test_shel_converts_usd_with_fx_pair():
    source, fetch = make_source(SHEL)
    inputs = source.valuation_inputs("SHEL.L")
    assert inputs.currency is Currency.GBP
    assert inputs.fx_rate == pytest.approx(0.7556399703025818)
    assert ("CurrencyHistorical", {"symbol": "USDGBP", "interval": "1d", "start_date": "2026-09-30"}) in fetch.calls


def test_fx_is_memoised_for_an_hour():
    clock = Clock()
    source, fetch = make_source(SHEL, clock=clock)
    source.valuation_inputs("SHEL.L")
    clock.now = 3599
    source.valuation_inputs("SHEL.L")
    assert fetch.models().count("CurrencyHistorical") == 1
    clock.now = 3601
    source.valuation_inputs("SHEL.L")
    assert fetch.models().count("CurrencyHistorical") == 2


def test_fx_failure_gives_none_with_gap_and_is_not_memoised():
    responses = dict(SHEL)
    responses[("CurrencyHistorical", "USDGBP")] = []
    source, fetch = make_source(responses)
    inputs = source.valuation_inputs("SHEL.L")
    assert inputs.fx_rate is None and "fx" in inputs.gaps
    responses[("CurrencyHistorical", "USDGBP")] = v.USDGBP
    assert source.valuation_inputs("SHEL.L").fx_rate == pytest.approx(0.7556399703025818)


def test_optional_parts_missing_or_failing():
    responses = dict(AAPL)
    responses[("PriceTargetConsensus", "AAPL")] = EmptyDataError("none")
    responses[("KeyMetrics", "AAPL")] = RuntimeError("weird")
    source, _ = make_source(responses)
    inputs = source.valuation_inputs("AAPL")
    assert inputs.analyst is None
    assert inputs.forward_pe is None
    assert inputs.gaps == ("km",)


def test_quote_failure_raises():
    responses = dict(AAPL)
    responses[("EquityQuote", "AAPL")] = []
    source, _ = make_source(responses)
    with pytest.raises(NoData):
        source.valuation_inputs("AAPL")


def test_offline_raises_source_unavailable():
    source, _ = make_source({}, reachable=False)
    with pytest.raises(SourceUnavailable) as info:
        source.valuation_inputs("AAPL")
    assert info.value.reason == "network"


def test_etf_without_last_price_uses_history_close():
    source, fetch = make_source({
        ("EquityQuote", "SPY"): v.SPY_QUOTE,
        ("EquityInfo", "SPY"): v.SPY_INFO,
        ("KeyMetrics", "SPY"): EmptyDataError("none"),
        ("EquityHistorical", "SPY"): v.SPY_DAILY,
    })
    inputs = source.valuation_inputs("SPY")
    assert (inputs.asset_type, inputs.price) == (AssetType.ETF, 777.0)
    assert "EquityHistorical" in fetch.models()


def test_malformed_rows_are_data_error():
    responses = dict(AAPL)
    responses[("CashFlowStatement", "AAPL")] = [{"period_ending": "2025-09-30", "free_cash_flow": "lots"}]
    source, _ = make_source(responses)
    with pytest.raises(DataError) as info:
        source.valuation_inputs("AAPL")
    assert type(info.value) is DataError
    assert info.value.user_message == "Unexpected source response"


def test_balance_sheet_and_ebitda_feed_watchlist_fields():
    responses = dict(SHEL)
    responses[("IncomeStatement", "SHEL.L")] = v.SHEL_INCOME_EBITDA
    responses[("BalanceSheet", "SHEL.L")] = v.SHEL_BALANCE
    responses[("EquityQuote", "SHEL.L")] = v.SHEL_QUOTE_RANGE
    source, _ = make_source(responses)
    inputs = source.valuation_inputs("SHEL.L")
    assert (inputs.ebitda, inputs.total_debt, inputs.cash) == (56664000000.0, 75643000000.0, 29630000000.0)
    assert inputs.year_high == pytest.approx(40.62)
    assert inputs.fetched_at == NOW


def test_missing_balance_sheet_is_not_a_gap():
    responses = dict(AAPL)
    del responses[("BalanceSheet", "AAPL")]  # no rows: NoData, which is not a gap
    source, _ = make_source(responses)
    inputs = source.valuation_inputs("AAPL")
    assert inputs.total_debt is None and inputs.gaps == ()


# --- Yahoo refusing calls with a 401 (fetchers log it, then return nothing) -------------------


def _refused_once(model_rows):
    """Empty and logging a 401 on the first call; the real rows afterwards."""
    calls = []

    def answer():
        calls.append(1)
        if len(calls) == 1:
            logging.getLogger("yfinance").error('HTTP Error 401: {"description":"Invalid Crumb"}')
            return []
        return model_rows

    return answer


def test_a_refused_part_is_asked_for_again_and_counts_as_complete():
    responses = dict(AAPL)
    responses[("PriceTargetConsensus", "AAPL")] = _refused_once(AAPL[("PriceTargetConsensus", "AAPL")])
    source, _ = make_source(responses)
    inputs = source.valuation_inputs("AAPL")
    assert inputs.analyst is not None and inputs.gaps == ()


def test_a_part_refused_twice_is_reported_as_a_gap_not_as_no_data():
    def always_refused():
        logging.getLogger("yfinance").error("HTTP Error 401: Invalid Crumb")
        return []

    responses = dict(AAPL)
    responses[("PriceTargetConsensus", "AAPL")] = always_refused
    source, _ = make_source(responses)
    inputs = source.valuation_inputs("AAPL")
    assert inputs.analyst is None and inputs.gaps == ("targets",)


def test_a_really_empty_part_is_not_retried_or_called_a_gap():
    responses = dict(AAPL)
    responses[("PriceTargetConsensus", "AAPL")] = []
    source, fetch = make_source(responses)
    inputs = source.valuation_inputs("AAPL")
    assert inputs.gaps == () and fetch.models().count("PriceTargetConsensus") == 1


def test_another_kind_of_yfinance_error_does_not_trigger_a_retry():
    def other_error():
        logging.getLogger("yfinance").error("HTTP Error 404: not found")
        return []

    responses = dict(AAPL)
    responses[("PriceTargetConsensus", "AAPL")] = other_error
    source, fetch = make_source(responses)
    assert source.valuation_inputs("AAPL").gaps == ()
    assert fetch.models().count("PriceTargetConsensus") == 1


def test_the_first_call_finishes_before_any_other_starts():
    release, first_running, order = threading.Event(), threading.Event(), []

    def slow_first(model, params):
        if not first_running.is_set():
            first_running.set()
            release.wait(5)
            order.append("first done")
        else:
            order.append("second")
        return [{"x": 1}]

    source, _ = make_source({})
    source._fetch = slow_first
    a = threading.Thread(target=source._rows, args=("EquityQuote", {"symbol": "A"}))
    b = threading.Thread(target=source._rows, args=("EquityQuote", {"symbol": "B"}))
    a.start()
    assert first_running.wait(5)
    b.start()
    b.join(0.2)
    assert b.is_alive() and order == []
    release.set()
    a.join(5)
    b.join(5)
    assert order == ["first done", "second"]


def test_a_failed_first_call_does_not_block_later_ones():
    source, _ = make_source({("EquityQuote", "A"): RuntimeError("boom"), ("EquityQuote", "B"): [{"x": 1}]})
    with pytest.raises(DataError):
        source._rows("EquityQuote", {"symbol": "A"})
    assert source._rows("EquityQuote", {"symbol": "B"}) == [{"x": 1}]


# --- ETF look-through ----------------------------------------------------------------------

SPY = {
    ("EquityQuote", "SPY"): v.SPY_QUOTE,
    ("EquityInfo", "SPY"): v.SPY_INFO,
    ("KeyMetrics", "SPY"): EmptyDataError("none"),
    ("EquityHistorical", "SPY"): v.SPY_DAILY,
    ("EquityQuote", "^TNX"): [{"symbol": "^TNX", "last_price": 5.099, "currency": "USD"}],
}


class Funds:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, symbol):
        self.calls.append(symbol)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if callable(result):
            result = result()
        if isinstance(result, BaseException):
            raise result
        return result


def test_etf_inputs_carry_fund_profile_and_ten_year():
    from tests.data.fund_samples import RAW

    source, fetch = make_source(SPY, funds=Funds(RAW["SPY"]))
    inputs = source.valuation_inputs("SPY")
    assert inputs.etf is not None
    assert inputs.etf.profile.pe == pytest.approx(1 / 0.0416)
    assert inputs.etf.ten_year == pytest.approx(0.05099)
    assert inputs.etf.holding_values is None
    assert "fund" not in inputs.gaps


def test_etf_without_fund_data_keeps_old_inputs():
    source, _ = make_source(SPY)
    inputs = source.valuation_inputs("SPY")
    assert inputs.etf is None and "fund" not in inputs.gaps


def test_fund_fetch_failure_is_a_gap():
    source, _ = make_source(SPY, funds=Funds(RuntimeError("boom")))
    inputs = source.valuation_inputs("SPY")
    assert inputs.etf is None and "fund" in inputs.gaps


def test_equities_never_fetch_fund_data():
    funds = Funds(NoFundData("AAPL"))
    source, fetch = make_source(AAPL, funds=funds)
    source.valuation_inputs("AAPL")
    assert funds.calls == [] and ("EquityQuote", {"symbol": "^TNX"}) not in fetch.calls


def test_ten_year_unavailable_is_none():
    from tests.data.fund_samples import RAW

    responses = dict(SPY)
    responses[("EquityQuote", "^TNX")] = []
    source, _ = make_source(responses, funds=Funds(RAW["SPY"]))
    assert source.valuation_inputs("SPY").etf.ten_year is None


def test_fund_profile_memoised_for_a_day():
    from tests.data.fund_samples import RAW

    clock = Clock()
    funds = Funds(RAW["SPY"])
    source, _ = make_source(SPY, funds=funds, clock=clock)
    assert source.fund_profile("SPY").pe is not None
    source.fund_profile("SPY")
    assert funds.calls == ["SPY"]
    clock.now = FUND_TTL_S + 1
    source.fund_profile("SPY")
    assert funds.calls == ["SPY", "SPY"]


def test_fund_profile_no_fund_data_is_an_empty_profile():
    source, _ = make_source({})
    p = source.fund_profile("BOVA11.SA")
    assert p.holdings == () and p.gaps == ("no fund data",)


def test_fund_profile_retries_once_after_a_refusal():
    from tests.data.fund_samples import RAW

    def refused():
        logging.getLogger("yfinance").error("HTTP Error 401: Invalid Crumb")
        return RuntimeError("401 Unauthorized")

    funds = Funds(refused, RAW["SPY"])
    source, _ = make_source({}, funds=funds)
    assert source.fund_profile("SPY").pe is not None
    assert funds.calls == ["SPY", "SPY"]


def test_fund_profile_failure_raises_data_error_and_is_not_memoised():
    funds = Funds(RuntimeError("boom"))
    source, _ = make_source({}, funds=funds)
    with pytest.raises(DataError):
        source.fund_profile("SPY")
    with pytest.raises(DataError):
        source.fund_profile("SPY")
    assert funds.calls == ["SPY", "SPY"]


def test_ten_year_falls_back_to_previous_close():
    """Yahoo quotes ^TNX as an index: no last_price, only prev_close/open (seen 2026-11-17)."""
    from tests.data.fund_samples import RAW

    responses = dict(SPY)
    responses[("EquityQuote", "^TNX")] = [{"symbol": "^TNX", "asset_type": "INDEX", "open": 5.125,
                                          "prev_close": 5.121, "currency": "USD"}]
    source, _ = make_source(responses, funds=Funds(RAW["SPY"]))
    assert source.valuation_inputs("SPY").etf.ten_year == pytest.approx(0.05121)


@pytest.mark.parametrize("message", ["HTTP Error 401: Unauthorized", "HTTP Error 404: Not Found",
                                     "HTTP Error 500: Server Error"])
def test_fund_http_errors_are_a_gap_not_offline(message):
    from curl_cffi.requests.exceptions import HTTPError

    source, _ = make_source(SPY, funds=Funds(HTTPError(message)))
    inputs = source.valuation_inputs("SPY")
    assert inputs.etf is None and "fund" in inputs.gaps


def test_fund_http_error_while_offline_is_offline():
    from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError

    source, _ = make_source({}, funds=Funds(CurlConnectionError("down")), reachable=False)
    with pytest.raises(SourceUnavailable) as info:
        source.fund_profile("SPY")
    assert info.value.reason == "network"


def test_failed_ten_year_is_a_gap():
    from tests.data.fund_samples import RAW

    responses = dict(SPY)
    responses[("EquityQuote", "^TNX")] = RuntimeError("boom")
    source, _ = make_source(responses, funds=Funds(RAW["SPY"]))
    inputs = source.valuation_inputs("SPY")
    assert inputs.etf.ten_year is None and "ten_year" in inputs.gaps
