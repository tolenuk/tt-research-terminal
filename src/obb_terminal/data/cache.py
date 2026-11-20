"""In-memory TTL cache with single-flight around any MarketData. Errors are never cached."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from typing import Any

from obb_terminal.data.market import MarketData
from obb_terminal.domain.models import (
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

DEFAULT_TTL = 300.0
FUND_PROFILE_TTL = 6 * 3600.0  # the source also memoises it for a day
TTL = {"quote": 60.0, "fund_profile": FUND_PROFILE_TTL}
VALUATION_TTL, VALUATION_GAPS_TTL = 300.0, 30.0

Key = tuple[str, tuple[Hashable, ...]]


def _sym(symbol: str) -> str:
    return symbol.strip().upper()


class CachedMarketData:
    def __init__(
        self, inner: MarketData, clock: Callable[[], float] = time.monotonic, maxsize: int = 512
    ) -> None:
        self._inner = inner
        self._clock = clock
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._entries: OrderedDict[Key, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[Key, Future[Any]] = {}

    def _cached(
        self,
        method: str,
        args: tuple[Hashable, ...],
        call: Callable[[], Any],
        *,
        ttl_for: Callable[[Any], float] | None = None,
    ) -> Any:
        key: Key = (method, args)
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[key] = future
        assert future is not None
        if not owner:
            return future.result()
        try:
            value = call()
        except Exception as exc:
            with self._lock:
                self._inflight.pop(key, None)
            future.set_exception(exc)
            raise
        except BaseException:
            # Interrupt/exit: still release waiters, never leave them hanging.
            with self._lock:
                self._inflight.pop(key, None)
            future.set_exception(RuntimeError("in-flight call aborted"))
            raise
        try:
            with self._lock:
                ttl = ttl_for(value) if ttl_for is not None else TTL.get(method, DEFAULT_TTL)
                self._entries[key] = (now + ttl, value)
                self._entries.move_to_end(key)
                while len(self._entries) > self._maxsize:
                    self._entries.popitem(last=False)
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            future.set_result(value)
        return value

    def invalidate_valuation(self, symbol: str) -> None:
        """Drop a symbol's cached valuation inputs so the next call fetches fresh data."""
        try:
            with self._lock:
                self._entries.pop(("valuation_inputs", (_sym(symbol),)), None)
        except Exception:  # never raises: a bad symbol just has nothing cached
            pass

    def search(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        q = query.strip()
        return self._cached("search", (q.casefold(), limit), lambda: self._inner.search(q, limit))

    def profile(self, symbol: str) -> Profile:
        s = _sym(symbol)
        return self._cached("profile", (s,), lambda: self._inner.profile(s))

    def quote(self, symbol: str) -> Quote:
        s = _sym(symbol)
        return self._cached("quote", (s,), lambda: self._inner.quote(s))

    def stats(self, symbol: str) -> Stats:
        s = _sym(symbol)
        return self._cached("stats", (s,), lambda: self._inner.stats(s))

    def history(self, symbol: str, range: Range) -> PriceSeries:
        s = _sym(symbol)
        return self._cached("history", (s, range.value), lambda: self._inner.history(s, range))

    def financials(self, symbol: str, statement: Statement, period: Period) -> FinancialTable:
        s = _sym(symbol)
        return self._cached(
            "financials",
            (s, statement.value, period.value),
            lambda: self._inner.financials(s, statement, period),
        )

    def fund_info(self, symbol: str) -> FundInfo:
        s = _sym(symbol)
        return self._cached("fund_info", (s,), lambda: self._inner.fund_info(s))

    def news(self, symbol: str, limit: int = 20) -> list[NewsItem]:
        s = _sym(symbol)
        return self._cached("news", (s, limit), lambda: self._inner.news(s, limit))

    def fund_profile(self, symbol: str) -> FundProfile:
        s = _sym(symbol)
        return self._cached("fund_profile", (s,), lambda: self._inner.fund_profile(s), ttl_for=_fund_ttl)

    def valuation_inputs(self, symbol: str) -> ValuationInputs:
        s = _sym(symbol)
        return self._cached(
            "valuation_inputs", (s,), lambda: self._inner.valuation_inputs(s), ttl_for=_valuation_ttl
        )


def _fund_ttl(profile: FundProfile) -> float:
    return VALUATION_GAPS_TTL if profile.gaps else FUND_PROFILE_TTL


def _valuation_ttl(inputs: ValuationInputs) -> float:
    """A partial result (some sub-fetch failed) is retried sooner."""
    return VALUATION_GAPS_TTL if inputs.gaps else VALUATION_TTL
