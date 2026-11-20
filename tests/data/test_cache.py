import threading
from concurrent.futures import Future
from datetime import date

import pytest

from obb_terminal.data import cache as cache_module
from obb_terminal.data.cache import CachedMarketData
from obb_terminal.data.engine import Engine
from obb_terminal.data.factory import build_market_data
from obb_terminal.data.market import NoData
from obb_terminal.domain.models import AssetType, Currency, Range, ValuationInputs


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Inner:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.gate = None
        self.entered = threading.Event()

    def _record(self, *args):
        self.calls.append(args)
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(2)
        if self.fail:
            raise NoData("none")
        return f"value-{len(self.calls)}"

    def quote(self, symbol):
        return self._record("quote", symbol)

    def profile(self, symbol):
        return self._record("profile", symbol)

    def history(self, symbol, range):
        return self._record("history", symbol, range)

    def search(self, query, limit=8):
        return self._record("search", query, limit)

    def valuation_inputs(self, symbol):
        self._record("valuation_inputs", symbol)
        return self.valuation_result


def test_hit_within_ttl_and_miss_after():
    clock, inner = Clock(), Inner()
    cache = CachedMarketData(inner, clock=clock)
    assert cache.profile("AAPL") == "value-1"
    clock.now = 299
    assert cache.profile("AAPL") == "value-1"
    clock.now = 301
    assert cache.profile("AAPL") == "value-2"


def test_quote_ttl_is_60_seconds():
    clock, inner = Clock(), Inner()
    cache = CachedMarketData(inner, clock=clock)
    cache.quote("AAPL")
    clock.now = 59
    cache.quote("AAPL")
    clock.now = 60
    cache.quote("AAPL")
    assert len(inner.calls) == 2


def test_symbols_and_queries_normalised():
    inner = Inner()
    cache = CachedMarketData(inner, clock=Clock())
    cache.quote(" aapl ")
    cache.quote("AAPL")
    cache.search("Petrobras")
    cache.search("PETROBRAS")
    assert inner.calls == [("quote", "AAPL"), ("search", "Petrobras", 8)]


def test_history_keyed_by_range():
    inner = Inner()
    cache = CachedMarketData(inner, clock=Clock())
    cache.history("AAPL", Range.Y1)
    cache.history("AAPL", Range.M1)
    cache.history("AAPL", Range.Y1)
    assert len(inner.calls) == 2


def test_errors_not_cached():
    inner = Inner()
    inner.fail = True
    cache = CachedMarketData(inner, clock=Clock())
    with pytest.raises(NoData):
        cache.quote("AAPL")
    inner.fail = False
    assert cache.quote("AAPL") == "value-2"


def _run_single_flight(monkeypatch, inner, waiters):
    """Start an owner blocked inside inner, then `waiters` callers provably parked on its future."""
    parked = threading.Semaphore(0)

    class SignallingFuture(Future):
        def result(self, timeout=None):
            parked.release()  # caller already holds the in-flight future; about to block on it
            return super().result(timeout)

    monkeypatch.setattr(cache_module, "Future", SignallingFuture)
    inner.gate = threading.Event()
    cache = CachedMarketData(inner, clock=Clock())
    results, errors = [], []

    def call():
        try:
            results.append(cache.quote("AAPL"))
        except Exception as exc:
            errors.append(exc)

    owner = threading.Thread(target=call)
    owner.start()
    assert inner.entered.wait(2)  # owner is inside inner.quote, blocked on the gate
    others = [threading.Thread(target=call) for _ in range(waiters)]
    for t in others:
        t.start()
    for _ in range(waiters):
        assert parked.acquire(timeout=2)  # each waiter is waiting on the shared future
    assert len(cache._inflight) == 1
    assert len(inner.calls) == 1
    inner.gate.set()
    threads = [owner, *others]
    for t in threads:
        t.join(2)
        assert not t.is_alive()
    return cache, results, errors


def test_single_flight_shares_one_call(monkeypatch):
    inner = Inner()
    _, results, errors = _run_single_flight(monkeypatch, inner, waiters=2)
    assert errors == []
    assert results == ["value-1"] * 3
    assert len(inner.calls) == 1


def test_single_flight_waiters_share_error(monkeypatch):
    inner = Inner()
    inner.fail = True
    cache, results, errors = _run_single_flight(monkeypatch, inner, waiters=2)
    assert results == []
    assert len(errors) == 3
    assert all(isinstance(e, NoData) for e in errors)
    assert len(inner.calls) == 1
    assert cache._inflight == {}
    inner.fail = False
    assert cache.quote("AAPL") == "value-2"


def test_maxsize_evicts_oldest():
    inner = Inner()
    cache = CachedMarketData(inner, clock=Clock(), maxsize=2)
    cache.profile("A")
    cache.profile("B")
    cache.profile("C")
    cache.profile("A")
    assert [c[1] for c in inner.calls] == ["A", "B", "C", "A"]


def test_factory_builds_cached_source():
    md = build_market_data(Engine(importer=lambda: {}))
    assert isinstance(md, CachedMarketData)


def _inputs(gaps=()):
    return ValuationInputs("AAPL", "Apple", AssetType.EQUITY, Currency.USD, date(2026, 10, 10), 1.0, gaps=gaps)


def test_invalidate_valuation_drops_only_that_symbols_entry():
    inner = Inner()
    inner.valuation_result = _inputs()
    cache = CachedMarketData(inner, clock=Clock())
    cache.valuation_inputs("AAPL")
    cache.valuation_inputs("MSFT")
    cache.quote("AAPL")
    cache.invalidate_valuation(" aapl ")
    cache.invalidate_valuation("NONE")  # nothing cached: no error
    cache.valuation_inputs("MSFT")
    cache.quote("AAPL")
    assert len(inner.calls) == 3  # MSFT inputs and AAPL quote still cached
    cache.valuation_inputs("AAPL")
    assert inner.calls[-1] == ("valuation_inputs", "AAPL") and len(inner.calls) == 4


@pytest.mark.parametrize(("gaps", "ttl"), [((), 300.0), (("km",), 30.0)])
def test_valuation_inputs_ttl_depends_on_gaps(gaps, ttl):
    clock, inner = Clock(), Inner()
    inner.valuation_result = _inputs(gaps)
    cache = CachedMarketData(inner, clock=clock)
    assert cache.valuation_inputs(" aapl ") is inner.valuation_result
    clock.now = ttl - 1
    cache.valuation_inputs("AAPL")
    assert len(inner.calls) == 1
    clock.now = ttl
    cache.valuation_inputs("AAPL")
    assert inner.calls == [("valuation_inputs", "AAPL")] * 2


def test_fund_profile_cached_hours_and_gaps_briefly():
    from obb_terminal.domain.models import FundProfile

    class Funds:
        def __init__(self, profile):
            self.profile, self.calls = profile, 0

        def fund_profile(self, symbol):
            self.calls += 1
            return self.profile

    clock = Clock()
    full = Funds(FundProfile("SPY", pe=24.0))
    md = CachedMarketData(full, clock=clock)
    md.fund_profile("spy")
    clock.now = cache_module.FUND_PROFILE_TTL - 1
    md.fund_profile("SPY")
    assert full.calls == 1
    clock.now = cache_module.FUND_PROFILE_TTL + 1
    md.fund_profile("SPY")
    assert full.calls == 2
    empty = Funds(FundProfile("BOVA11.SA", gaps=("no fund data",)))
    md, clock.now = CachedMarketData(empty, clock=clock), 0.0
    md.fund_profile("BOVA11.SA")
    clock.now = cache_module.VALUATION_GAPS_TTL + 1
    md.fund_profile("BOVA11.SA")
    assert empty.calls == 2
