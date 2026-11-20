"""MarketData implementation over OpenBB's yfinance fetchers (called directly, no `obb`)."""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from functools import partial

from obb_terminal.data.deadline import run_with_deadline
from obb_terminal.data.engine import Engine
from obb_terminal.data.market import (
    DataError,
    DataTimeout,
    EngineUnavailable,
    NoData,
    SourceUnavailable,
    no_data,
)
from obb_terminal.data.openbb_mapping import (
    fx_pair,
    map_exception,
    offline,
    reporting_currency,
    to_dividends,
    to_financials,
    to_fund_info,
    to_fx_rate,
    to_profile,
    to_quote,
    to_series,
    to_stats,
    to_valuation_inputs,
)
from obb_terminal.data.yahoo_funds import (
    NoFundData,
    YahooFunds,
    empty_fund_profile,
    to_fund_profile,
)
from obb_terminal.data.yahoo_news import YahooNews
from obb_terminal.data.yahoo_search import YahooSearch
from obb_terminal.domain.currency import normalise
from obb_terminal.domain.models import (
    RANGE_SPEC,
    AssetType,
    Currency,
    EtfData,
    FinancialTable,
    FundInfo,
    FundProfile,
    NewsItem,
    Period,
    PriceSeries,
    Profile,
    Quote,
    Range,
    Statement,
    Stats,
    SymbolMatch,
    ValuationInputs,
)

log = logging.getLogger(__name__)

_STATEMENT_MODELS = {
    Statement.INCOME: "IncomeStatement",
    Statement.BALANCE: "BalanceSheet",
    Statement.CASH_FLOW: "CashFlowStatement",
}

# Phase-1 valuation calls: name -> (OpenBB model, extra params). Only "quote" is required.
_VALUATION_CALLS: dict[str, tuple[str, dict]] = {
    "quote": ("EquityQuote", {}),
    "info": ("EquityInfo", {}),
    "km": ("KeyMetrics", {}),
    "cash_flow": ("CashFlowStatement", {"period": "annual", "limit": 4}),
    "income": ("IncomeStatement", {"period": "annual", "limit": 2}),
    "targets": ("PriceTargetConsensus", {}),
    "dividends": ("HistoricalDividends", {}),
    "balance": ("BalanceSheet", {"period": "annual", "limit": 1}),
}
FX_TTL_S = 3600.0
FUND_TTL_S = 24 * 3600.0  # holdings change monthly
TEN_YEAR_TTL_S = 3600.0
# Market 10-year government yield per trading currency (Yahoo quotes ^TNX in percent).
TEN_YEAR_SYMBOL: dict[Currency, str] = {Currency.USD: "^TNX"}
FUND_GAP = "fund"
TEN_YEAR_GAP = "ten_year"
FUND_REFUSED = "Yahoo refused the fund data"
_REFUSED_WORDS = ("401", "Invalid Crumb", "Unauthorized")


class ReachabilityProbe:
    """Is Yahoo reachable at all? A TCP connect, memoised for `ttl_s`, safe across threads.

    OpenBB's yfinance fetchers swallow connection failures (EquityQuote returns no rows,
    yf.download an empty frame), so "no data" is only trusted once Yahoo is known reachable.
    """

    def __init__(
        self,
        address: tuple[str, int] = ("query2.finance.yahoo.com", 443),
        *,
        timeout_s: float = 3.0,
        ttl_s: float = 10.0,
        connect: Callable[..., socket.socket] = socket.create_connection,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._address = address
        self._timeout_s = timeout_s
        self._ttl_s = ttl_s
        self._connect = connect
        self._clock = clock
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        self._reachable = True

    def __call__(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._checked_at is not None and now - self._checked_at < self._ttl_s:
                return self._reachable
            try:
                self._connect(self._address, self._timeout_s).close()
                self._reachable = True
            except OSError as exc:
                log.info("Yahoo unreachable: %s", exc)
                self._reachable = False
            self._checked_at = self._clock()
            return self._reachable


class _AuthErrorCounter(logging.Handler):
    """Counts Yahoo's refusals. yfinance only logs them, then returns empty results, so an empty
    result alone cannot tell "no such data" from "Yahoo refused to answer"."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "401" in record.getMessage():
            self.count += 1


def _install_auth_error_counter() -> _AuthErrorCounter:
    logger = logging.getLogger("yfinance")
    for handler in logger.handlers:
        if isinstance(handler, _AuthErrorCounter):
            return handler
    counter = _AuthErrorCounter()
    logger.addHandler(counter)
    return counter


class OpenBBSource:
    def __init__(
        self,
        engine: Engine | None,
        search: YahooSearch,
        news: YahooNews,
        *,
        deadline_s: float = 20.0,
        engine_wait_s: float = 120.0,
        today: Callable[[], date] = date.today,
        reachable: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        funds: YahooFunds | None = None,
    ) -> None:
        self._engine = engine
        self._funds = funds or YahooFunds()
        self._fund_memo: dict[str, tuple[float, FundProfile]] = {}
        self._ten_year_memo: dict[str, tuple[float, float]] = {}
        self._now = now
        self._reachable = reachable if reachable is not None else ReachabilityProbe()
        self._search = search
        self._news = news
        self._deadline_s = deadline_s
        self._engine_wait_s = engine_wait_s
        self._today = today
        self._lock = threading.Lock()
        self._currency_codes: dict[str, str] = {}
        self._clock = clock
        self._fx_memo: dict[str, tuple[float, float]] = {}  # pair -> (expires_at, rate)
        self._session_ready = threading.Event()
        self._session_lock = threading.Lock()
        self._auth_errors = _install_auth_error_counter()

    # --- I/O seam -------------------------------------------------------------------------

    def _fetch(self, model: str, params: dict) -> list[dict]:
        if self._engine is None or not self._engine.wait(self._engine_wait_s):
            raise EngineUnavailable("Data engine unavailable")
        fetcher = self._engine.providers()["yfinance"].fetcher_dict[model]

        def run() -> list[dict]:
            result = asyncio.run(fetcher.fetch_data(params, {}))
            result = getattr(result, "result", result)
            if result is None:
                return []
            items = result if isinstance(result, list) else [result]
            return [item.model_dump(mode="json", exclude_none=True) for item in items]

        return run_with_deadline(run, self._deadline_s)

    # --- helpers --------------------------------------------------------------------------

    def _fetch_after_session(self, model: str, params: dict) -> list[dict]:
        """The very first call runs alone: it gets Yahoo's session crumb. Calls that race it are
        refused with a 401, which the fetchers report as "no data" (reproduced 2026-11-02)."""
        if not self._session_ready.is_set():
            with self._session_lock:
                if not self._session_ready.is_set():
                    try:
                        return self._fetch(model, params)
                    finally:
                        self._session_ready.set()
        return self._fetch(model, params)

    def _rows(self, model: str, params: dict) -> list[dict]:
        symbol = params.get("symbol")
        try:
            rows = self._fetch_after_session(model, params)
        except NoData as exc:
            self._raise_if_offline(symbol, exc)
            raise
        except DataError:
            raise
        except Exception as exc:
            err = map_exception(exc, symbol)
            if type(err) is DataError:
                log.error("Unexpected error from %s %s", model, params, exc_info=exc)
            if isinstance(err, NoData):
                self._raise_if_offline(symbol, exc)
            raise err from exc
        if not rows:
            self._raise_if_offline(symbol)
        return rows

    def _raise_if_offline(self, symbol: str | None, cause: BaseException | None = None) -> None:
        """Empty results are ambiguous offline (fetchers swallow connection errors)."""
        if not self._reachable():
            raise offline(symbol) from cause

    def _rows_or_empty(self, model: str, params: dict) -> list[dict]:
        try:
            return self._rows(model, params)
        except NoData:
            return []

    def _mapped[T](self, symbol: str, build: Callable[[], T]) -> T:
        """Run a mapping step; drifted rows become DataError, never a bare exception."""
        try:
            return build()
        except DataError:
            raise
        except Exception as exc:
            log.exception("Unexpected source data for %s", symbol)
            raise DataError("Unexpected source response", symbol) from exc

    def _remember(self, symbol: str, code: str | None) -> None:
        if code:
            with self._lock:
                self._currency_codes[symbol] = code

    def _currency_code(self, symbol: str) -> str:
        with self._lock:
            code = self._currency_codes.get(symbol)
        if code is None:
            self.quote(symbol)
            with self._lock:
                code = self._currency_codes.get(symbol)
        if code is None:
            raise no_data(symbol)
        return code

    def _last_close(self, symbol: str) -> float | None:
        start = (self._today() - timedelta(days=7)).isoformat()
        rows = self._rows_or_empty(
            "EquityHistorical", {"symbol": symbol, "interval": "1d", "start_date": start}
        )
        closes = self._mapped(
            symbol,
            lambda: [
                float(r["close"])
                for r in sorted(rows, key=lambda r: r["date"])
                if r.get("close") is not None
            ],
        )
        return closes[-1] if closes else None

    def _gather(self, calls: dict[str, Callable[[], list[dict]]]) -> dict[str, list[dict] | DataError]:
        """Run each call on its own daemon thread; collect rows or the DataError it raised."""
        results: dict[str, list[dict] | DataError] = {}
        lock = threading.Lock()

        def run(name: str, call: Callable[[], list[dict]]) -> None:
            try:
                value: list[dict] | DataError = call()
            except DataError as exc:
                value = exc
            except Exception as exc:
                log.error("Unexpected error in %s", name, exc_info=exc)
                value = map_exception(exc)
            with lock:
                results[name] = value

        threads = [
            threading.Thread(target=run, args=(name, call), daemon=True, name=f"gather-{name}")
            for name, call in calls.items()
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + self._deadline_s + 10.0
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with lock:
            return {name: results.get(name, DataTimeout("Timed out")) for name in calls}

    def _retry_refused_parts(
        self, got: dict[str, list[dict] | DataError], calls: dict[str, Callable[[], list[dict]]]
    ) -> tuple[str, ...]:
        """Yahoo refused a call during this fetch, so an empty part may not really be empty: ask
        again, one at a time, updating `got`. Returns the parts refused again: unknown, not empty."""
        still: list[str] = []
        for name, call in calls.items():
            result = got[name]
            if not (isinstance(result, NoData) or result == []):
                continue
            before = self._auth_errors.count
            try:
                got[name] = call()
            except DataError as exc:
                got[name] = exc
            empty = isinstance(got[name], NoData) or got[name] == []
            if empty and self._auth_errors.count != before:
                still.append(name)
        return tuple(still)

    def _fx_rate(self, pair: str) -> float | None:
        """Last close of e.g. USDGBP, memoised for an hour. Failures are not memoised."""
        now = self._clock()
        with self._lock:
            hit = self._fx_memo.get(pair)
        if hit is not None and hit[0] > now:
            return hit[1]
        start = (self._today() - timedelta(days=10)).isoformat()
        try:
            rows = self._rows_or_empty(
                "CurrencyHistorical", {"symbol": pair, "interval": "1d", "start_date": start}
            )
            rate = self._mapped(pair, lambda: to_fx_rate(rows))
        except SourceUnavailable as exc:
            if exc.reason == "network":
                raise
            log.warning("FX %s unavailable: %s", pair, exc.user_message)
            return None
        except DataError as exc:
            log.warning("FX %s unavailable: %s", pair, exc.user_message)
            return None
        if rate is None:
            log.warning("FX %s returned no valid rate", pair)
            return None
        with self._lock:
            self._fx_memo[pair] = (now + FX_TTL_S, rate)
        return rate

    def _fetch_fund_profile(self, symbol: str) -> FundProfile:
        """One retry when Yahoo refused the first try (401 / crumb), like valuation parts."""
        for attempt in (1, 2):
            before = self._auth_errors.count
            try:
                raw = run_with_deadline(lambda: self._funds.fetch(symbol), self._deadline_s)
            except NoFundData as exc:
                if self._auth_errors.count == before:
                    self._raise_if_offline(symbol, exc)
                    return empty_fund_profile(symbol)
                if attempt == 2:
                    raise SourceUnavailable(FUND_REFUSED, symbol) from exc
                continue
            except DataError:
                raise
            except Exception as exc:
                refused = self._auth_errors.count != before or any(w in str(exc) for w in _REFUSED_WORDS)
                if refused and attempt == 1:
                    log.info("Fund data for %s refused, retrying once", symbol)
                    continue
                err = map_exception(exc, symbol)
                if isinstance(err, SourceUnavailable | NoData):
                    # curl's HTTPError is an OSError, so a 401/404/500 maps to "offline". Only a
                    # failed reachability probe means offline; anything else is this endpoint.
                    self._raise_if_offline(symbol, exc)
                    log.warning("Fund data for %s failed: %s", symbol, exc)
                    raise SourceUnavailable(FUND_REFUSED, symbol) from exc
                if type(err) is DataError:
                    log.error("Unexpected error fetching fund data for %s", symbol, exc_info=exc)
                raise err from exc
            return self._mapped(symbol, partial(to_fund_profile, symbol, raw))
        raise SourceUnavailable(FUND_REFUSED, symbol)  # pragma: no cover

    def _ten_year(self, currency: Currency) -> float | None:
        """The market 10-year yield as a fraction, memoised for an hour; None when unknown."""
        symbol = TEN_YEAR_SYMBOL.get(currency)
        if symbol is None:
            return None
        now = self._clock()
        with self._lock:
            hit = self._ten_year_memo.get(symbol)
        if hit is not None and hit[0] > now:
            return hit[1]
        try:
            rows = self._rows_or_empty("EquityQuote", {"symbol": symbol})
        except DataError as exc:
            log.warning("10-year yield %s unavailable: %s", symbol, exc.user_message)
            return None
        row = rows[0] if rows else {}
        # An index quote (^TNX) has no last price: the previous close is the latest daily value.
        last = next((row.get(k) for k in ("last_price", "prev_close", "open") if row.get(k) is not None), None)
        try:
            percent = float(last) if last is not None else None
        except (TypeError, ValueError):
            percent = None
        if percent is None or not 0 < percent < 20:
            log.warning("10-year yield %s returned no valid value", symbol)
            return None
        with self._lock:
            self._ten_year_memo[symbol] = (now + TEN_YEAR_TTL_S, percent / 100)
        return percent / 100

    def _with_etf(self, inputs: ValuationInputs) -> ValuationInputs:
        """Attach an ETF's look-through. No fund data (B3 ETFs) keeps the plain inputs; a failed
        fetch is a gap, so the inputs are retried sooner and not journaled."""
        try:
            profile = self.fund_profile(inputs.symbol)
        except SourceUnavailable as exc:
            if exc.reason == "network":
                raise
            log.warning("Fund data failed for %s: %s", inputs.symbol, exc.user_message)
            return replace(inputs, gaps=(*inputs.gaps, FUND_GAP))
        except DataError as exc:
            log.warning("Fund data failed for %s: %s", inputs.symbol, exc.user_message)
            return replace(inputs, gaps=(*inputs.gaps, FUND_GAP))
        if profile.gaps and not profile.holdings and profile.pe is None:
            return inputs
        ten_year = self._ten_year(inputs.currency)
        # A market that has a 10-year quote but failed to give one now: partial data (retried
        # sooner, not journaled against the default yield).
        failed = ten_year is None and inputs.currency in TEN_YEAR_SYMBOL
        gaps = (*inputs.gaps, TEN_YEAR_GAP) if failed else inputs.gaps
        return replace(inputs, etf=EtfData(profile=profile, ten_year=ten_year), gaps=gaps)

    # --- MarketData -----------------------------------------------------------------------

    def search(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        try:
            return run_with_deadline(lambda: self._search.search(query, limit), self._deadline_s)
        except DataError:
            raise
        except Exception as exc:
            raise map_exception(exc) from exc

    def news(self, symbol: str, limit: int = 20) -> list[NewsItem]:
        try:
            return run_with_deadline(lambda: self._news.fetch(symbol, limit), self._deadline_s)
        except DataError:
            raise
        except Exception as exc:
            raise map_exception(exc, symbol) from exc

    def profile(self, symbol: str) -> Profile:
        info = self._rows_or_empty("EquityInfo", {"symbol": symbol})
        quote = self._rows_or_empty("EquityQuote", {"symbol": symbol})
        profile = self._mapped(symbol, lambda: to_profile(info, quote, symbol))
        self._remember(symbol, self._mapped(symbol, lambda: (info or quote)[0].get("currency")))
        return profile

    def quote(self, symbol: str) -> Quote:
        rows = self._rows("EquityQuote", {"symbol": symbol})
        if not rows:
            raise no_data(symbol)
        fallback = self._last_close(symbol) if rows[0].get("last_price") is None else None
        quote = self._mapped(symbol, lambda: to_quote(rows, symbol, fallback))
        self._remember(symbol, rows[0].get("currency"))
        return quote

    def stats(self, symbol: str) -> Stats:
        km = self._rows_or_empty("KeyMetrics", {"symbol": symbol})
        info = [] if km else self._rows_or_empty("EquityInfo", {"symbol": symbol})
        currency, _ = normalise(self._currency_code(symbol))
        return self._mapped(symbol, lambda: to_stats(km, info, symbol, currency))

    def history(self, symbol: str, range: Range) -> PriceSeries:
        interval, days = RANGE_SPEC[range]
        start = (self._today() - timedelta(days=days)).isoformat()
        rows = self._rows(
            "EquityHistorical", {"symbol": symbol, "interval": interval, "start_date": start}
        )
        code = self._currency_code(symbol)
        return self._mapped(symbol, lambda: to_series(rows, symbol, range, code))

    def financials(self, symbol: str, statement: Statement, period: Period) -> FinancialTable:
        rows = self._rows(
            _STATEMENT_MODELS[statement], {"symbol": symbol, "period": period.value, "limit": 5}
        )
        return self._mapped(symbol, lambda: to_financials(rows, symbol, statement, period))

    def fund_info(self, symbol: str) -> FundInfo:
        quote = self.quote(symbol)
        dividend_rows = self._rows_or_empty("HistoricalDividends", {"symbol": symbol})
        code = self._currency_code(symbol)
        dividends = self._mapped(symbol, lambda: to_dividends(dividend_rows, code))
        etf = self._rows_or_empty("EtfInfo", {"symbol": symbol})
        today = self._today()
        return self._mapped(
            symbol, lambda: to_fund_info(symbol, quote.currency, dividends, quote.last, etf, today)
        )

    def fund_profile(self, symbol: str) -> FundProfile:
        """An ETF's look-through data, memoised for a day. No fund data = an empty profile."""
        now = self._clock()
        with self._lock:
            hit = self._fund_memo.get(symbol)
        if hit is not None and hit[0] > now:
            return hit[1]
        profile = self._fetch_fund_profile(symbol)
        with self._lock:
            self._fund_memo[symbol] = (now + FUND_TTL_S, profile)
        return profile

    def valuation_inputs(self, symbol: str) -> ValuationInputs:
        calls = {
            name: partial(self._rows, model, {"symbol": symbol, **extra})
            for name, (model, extra) in _VALUATION_CALLS.items()
        }
        refused_before = self._auth_errors.count
        got = self._gather(calls)
        refused = self._auth_errors.count != refused_before
        still_refused = self._retry_refused_parts(got, calls) if refused else ()
        for result in got.values():
            if isinstance(result, SourceUnavailable) and result.reason == "network":
                raise result
        quote = got["quote"]
        if isinstance(quote, DataError):
            raise quote
        if not quote:
            raise no_data(symbol)
        parts: dict[str, list[dict]] = {}
        gaps: list[str] = []
        for name, result in got.items():
            if name in still_refused:
                log.warning("Valuation part %s refused by Yahoo for %s", name, symbol)
                parts[name] = []
                gaps.append(name)
            elif isinstance(result, NoData):
                parts[name] = []
            elif isinstance(result, DataError):
                log.warning("Valuation part %s failed for %s: %s", name, symbol, result.user_message)
                parts[name] = []
                gaps.append(name)
            else:
                parts[name] = result
        row = quote[0]
        fallback = self._last_close(symbol) if row.get("last_price") is None else None
        trading, _ = self._mapped(symbol, lambda: normalise(row.get("currency")))
        pair, factor = fx_pair(self._mapped(symbol, lambda: reporting_currency(parts["km"])), trading)
        fx: float | None = factor
        if pair is not None:
            rate = self._fx_rate(pair)
            fx = None if rate is None else rate * factor
            if fx is None:
                gaps.append("fx")
        self._remember(symbol, row.get("currency"))
        today = self._today()
        fetched_at = self._now()
        inputs = self._mapped(
            symbol,
            lambda: to_valuation_inputs(
                symbol,
                quote_rows=quote,
                info_rows=parts["info"],
                km_rows=parts["km"],
                cash_flow_rows=parts["cash_flow"],
                income_rows=parts["income"],
                target_rows=parts["targets"],
                dividend_rows=parts["dividends"],
                fx_rate=fx,
                fallback_last=fallback,
                today=today,
                gaps=tuple(gaps),
                balance_rows=parts["balance"],
                fetched_at=fetched_at,
            ),
        )
        return self._with_etf(inputs) if inputs.asset_type is AssetType.ETF else inputs
