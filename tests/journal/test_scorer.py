import threading
from datetime import UTC, date, datetime, timedelta

import pytest

from obb_terminal.data.market import NoData
from obb_terminal.domain.models import Currency, PricePoint, PriceSeries, Range
from obb_terminal.journal.models import OutcomeStatus
from obb_terminal.journal.scorer import LAST_SCORED, run_scoring, scoring_due
from obb_terminal.journal.store import Journal
from tests.fakes import FakeMarketData
from tests.journal.samples import DAY0, NOW, snap, weekdays

TODAY = date(2026, 10, 27)


def series(symbol, closes, start=DAY0):
    points = tuple(
        PricePoint(datetime(d.year, d.month, d.day, tzinfo=UTC), c) for d, c in zip(weekdays(start, len(closes)), closes, strict=True)
    )
    return PriceSeries(symbol, Range.Y1, Currency.USD, points)


class Market(FakeMarketData):
    def __init__(self, data, fail=(), gate=None):
        super().__init__()
        self.data, self.fail, self.gate = data, set(fail), gate
        self.lock = threading.Lock()
        self.asked = []
        self.results["history"] = self._history

    def _history(self, symbol, range_):
        with self.lock:
            self.asked.append((symbol, range_))
        if self.gate is not None:
            self.gate.wait(5)
        if symbol in self.fail:
            raise NoData("no data", symbol)
        return self.data[symbol]


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "journal.sqlite3")


def test_scores_due_snapshots_against_the_benchmark(journal):
    journal.record(snap(symbol="AAPL", benchmark="SPY"))
    # 130 bars: start 100, +20 bars -> 120; benchmark 50 -> 55 over the same span
    stock = series("AAPL", [100.0] + [100.0 + i for i in range(1, 130)])
    bench = series("SPY", [50.0 + 0.25 * i for i in range(130)])
    md = Market({"AAPL": stock, "SPY": bench})
    result = run_scoring(md, journal, TODAY, now=NOW)
    assert result.error is None and result.failed == ()
    assert result.scored == 3 and result.pending == 0
    [(_, outcomes)] = journal.rows()
    o20 = outcomes[20]
    assert o20.status is OutcomeStatus.DONE and o20.start_price == 100.0 and o20.end_price == 120.0
    assert o20.bench_start == 50.0 and o20.bench_end == 55.0
    assert o20.excess == pytest.approx(0.20 - 0.10)
    assert sorted(outcomes) == [20, 60, 120]
    assert sorted(a for a in md.asked) == [("AAPL", Range.Y1), ("SPY", Range.Y1)]
    assert journal.get_meta(LAST_SCORED) == "2026-10-27"
    assert journal.due(TODAY) == []


def test_short_history_leaves_horizons_pending(journal):
    journal.record(snap(symbol="AAPL"))
    md = Market({"AAPL": series("AAPL", [100.0] * 30), "SPY": series("SPY", [1.0] * 30)})
    result = run_scoring(md, journal, TODAY, now=NOW)
    assert (result.scored, result.pending) == (1, 2)
    assert [hs for _, hs in journal.due(TODAY)] == [(60, 120)]


def test_failed_fetch_skips_its_snapshots_and_is_retried(journal):
    journal.record(snap(symbol="AAPL"))
    journal.record(snap(symbol="MSFT"))
    data = {s: series(s, [100.0] * 130) for s in ("AAPL", "MSFT", "SPY")}
    result = run_scoring(Market(data, fail={"MSFT"}), journal, TODAY, now=NOW)
    assert result.failed == ("MSFT",) and result.scored == 3
    assert [s.symbol for s, _ in journal.due(TODAY)] == ["MSFT"]


def test_benchmark_failure_skips_every_snapshot_using_it(journal):
    journal.record(snap(symbol="AAPL"))
    data = {s: series(s, [100.0] * 130) for s in ("AAPL", "SPY")}
    result = run_scoring(Market(data, fail={"SPY"}), journal, TODAY, now=NOW)
    assert result.failed == ("SPY",) and result.scored == 0


def test_nothing_due_fetches_nothing(journal):
    journal.record(snap(day=TODAY - timedelta(days=3)))
    md = Market({})
    result = run_scoring(md, journal, TODAY, now=NOW)
    assert (result.scored, result.pending, md.asked) == (0, 0, [])
    assert journal.get_meta(LAST_SCORED) == "2026-10-27"


def test_budget_abandons_slow_fetches(journal):
    journal.record(snap(symbol="AAPL"))
    gate = threading.Event()
    md = Market({"AAPL": series("AAPL", [1.0] * 130), "SPY": series("SPY", [1.0] * 130)}, gate=gate)
    try:
        result = run_scoring(md, journal, TODAY, now=NOW, budget_s=0.2, margin_s=0.1)
    finally:
        gate.set()
    assert result.scored == 0 and set(result.skipped) == {"AAPL", "SPY"}


def test_scoring_due_once_a_day(journal):
    assert scoring_due(journal, TODAY)
    journal.set_meta(LAST_SCORED, TODAY.isoformat())
    assert not scoring_due(journal, TODAY)
    assert scoring_due(journal, TODAY + timedelta(days=1))


def test_journal_errors_are_reported_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    result = run_scoring(Market({}), Journal(blocker / "j.sqlite3"), TODAY, now=NOW)
    assert result.error is not None
    assert not scoring_due(Journal(blocker / "j.sqlite3"), TODAY)


def test_todays_unfinished_bar_is_never_an_end_bar(journal):
    journal.record(snap(symbol="AAPL"))
    n = 21  # exactly enough for the 20-day horizon only if today's bar counted
    today = weekdays(DAY0, n)[-1]
    md = Market({"AAPL": series("AAPL", [100.0] * n), "SPY": series("SPY", [1.0] * n)})
    result = run_scoring(md, journal, today, now=NOW)
    assert result.scored == 0
    [(_, outcomes)] = journal.rows()
    assert outcomes == {}


def test_failed_runs_do_not_mark_the_day_scored(journal):
    journal.record(snap(symbol="AAPL"))
    run_scoring(Market({}, fail={"AAPL", "SPY"}), journal, TODAY, now=NOW)
    assert scoring_due(journal, TODAY)
