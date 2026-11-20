import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from obb_terminal.alerts.engine import BUSY, SAVE_FAILED, AlertEngine
from obb_terminal.alerts.store import TargetsRepo
from obb_terminal.domain.targets import AlertState, EventKind, Heat, Indicator
from tests.fakes import FakeNotifier, make_valuation_inputs

T0 = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 13, 14, 15, tzinfo=UTC)


def obs(symbol="AAPL", *, price=None, at=T0):
    i = make_valuation_inputs(symbol)
    return replace(i, price=i.price if price is None else price, fetched_at=at)


@pytest.fixture
def repo(tmp_path):
    return TargetsRepo(tmp_path / "targets.json")


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def engine(repo, notifier):
    return AlertEngine(repo, notifier, clock=lambda: NOW)


def test_hit_fires_and_notifies_once(engine, notifier):
    engine.set_target("AAPL", Indicator.PRICE, 400.0, True)  # above 336.64: hit now
    first = engine.apply([obs()])
    assert [e.kind for e in first.events] == [EventKind.TARGET_HIT]
    assert first.notified == 1 and first.error is None
    assert notifier.bodies() == ["AAPL: Price ≤ 400 hit — price 336.64 USD (buy-at 400.00)"]
    target = first.file.get("AAPL").targets[0]
    assert (target.state, target.fired_at) == (AlertState.FIRED, NOW)
    assert first.rows[0].cells[0].target.state is AlertState.FIRED  # rows reflect the commit
    second = engine.apply([obs(at=T0 + timedelta(minutes=15))])
    assert second.events == () and len(notifier.sent) == 1


def test_rearms_after_two_percent_and_fires_again(engine, notifier):
    engine.set_target("AAPL", Indicator.PRICE, 300.0, True)
    engine.apply([obs(price=300.0)])
    engine.apply([obs(price=306.0, at=T0 + timedelta(minutes=1))])  # exactly 2%: stays fired
    assert engine.snapshot().file.get("AAPL").targets[0].state is AlertState.FIRED
    engine.apply([obs(price=306.01, at=T0 + timedelta(minutes=2))])
    assert engine.snapshot().file.get("AAPL").targets[0].state is AlertState.ARMED
    engine.apply([obs(price=299.0, at=T0 + timedelta(minutes=3))])
    assert len([b for b in notifier.bodies() if "Price ≤ 300" in b]) == 2


def test_stale_observation_makes_no_transition(engine, notifier):
    engine.set_target("AAPL", Indicator.PRICE, 300.0, True)
    engine.apply([obs(price=350.0, at=T0)])
    assert engine.snapshot().file.get("AAPL").observed_at == T0
    result = engine.apply([obs(price=290.0, at=T0)])  # same fetch (a cache hit)
    assert result.events == () and notifier.sent == []
    assert result.rows[0].cells[0].hit  # still evaluated for display


def test_edit_clears_observed_at_so_cached_data_fires_at_once(engine, notifier):
    engine.set_target("AAPL", Indicator.PRICE, 300.0, True)
    engine.apply([obs(at=T0)])
    engine.set_target("AAPL", Indicator.PRICE, 370.0, True)  # now above the price
    assert engine.snapshot().file.get("AAPL").observed_at is None
    result = engine.apply([obs(at=T0)])  # same cached observation
    assert [e.kind for e in result.events] == [EventKind.TARGET_HIT]


def test_symbols_without_targets_are_evaluated_but_not_written(engine, repo):
    result = engine.apply([obs("SHEL.L")])
    assert result.rows[0].symbol == "SHEL.L" and result.rows[0].cells[0].heat is Heat.NA
    assert not repo.path.exists()


def test_two_engines_notify_exactly_once(repo):
    notifiers = [FakeNotifier(), FakeNotifier()]
    engines = [AlertEngine(repo, n, clock=lambda: NOW) for n in notifiers]
    engines[0].set_target("AAPL", Indicator.PRICE, 400.0, True)
    barrier = threading.Barrier(2)

    def run(engine, at):
        barrier.wait(2)
        engine.apply([obs(at=at)])

    threads = [threading.Thread(target=run, args=(e, T0 + timedelta(seconds=n)))
               for n, e in enumerate(engines)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    bodies = notifiers[0].bodies() + notifiers[1].bodies()
    assert bodies == ["AAPL: Price ≤ 400 hit — price 336.64 USD (buy-at 400.00)"]


def test_busy_lock_returns_display_rows_without_events(engine, repo, notifier):
    engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    entered, release = threading.Event(), threading.Event()

    def holder():
        with repo.transaction():
            entered.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert entered.wait(2)
        result = engine.apply([obs()], lock_timeout_s=0.1)
    finally:
        release.set()
        thread.join(2)
    assert (result.error, result.busy, result.events, notifier.sent) == (BUSY, True, (), [])
    assert result.rows[0].cells[0].hit
    assert engine.snapshot().file.get("AAPL").targets[0].state is AlertState.ARMED


def test_notifier_failure_still_commits(repo):
    engine = AlertEngine(repo, FakeNotifier(accept=False), clock=lambda: NOW)
    engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    result = engine.apply([obs()])
    assert len(result.events) == 1 and result.notified == 0
    assert engine.snapshot().file.get("AAPL").targets[0].state is AlertState.FIRED


def test_write_failure_gives_no_events(engine, notifier, monkeypatch):
    engine.set_target("AAPL", Indicator.PRICE, 400.0, True)

    def broken(path, text):
        raise OSError("disk full")

    monkeypatch.setattr("obb_terminal.alerts.store.atomic_write_text", broken)
    result = engine.apply([obs()])
    assert (result.error, result.events, notifier.sent) == (SAVE_FAILED, (), [])
    assert len(result.rows) == 1


def test_set_remove_and_clear(engine, repo):
    with pytest.raises(ValueError):
        engine.set_target("AAPL", Indicator.PE, -1.0, True)
    assert not repo.path.exists()
    engine.set_target("AAPL", Indicator.PE, 25.0, True)
    engine.set_target("AAPL", Indicator.PB, 2.0, False)
    f = engine.remove_target("AAPL", Indicator.PE)
    assert [t.indicator for t in f.get("AAPL").targets] == [Indicator.PB]
    assert engine.clear_targets("AAPL").get("AAPL") is None
    assert engine.snapshot().file.symbols == ()


def test_thesis_break_fires_once_through_the_engine(engine, notifier):
    i = obs(price=100.0)
    engine.set_thesis_break("AAPL", 100.5)
    result = engine.apply([i])
    assert [e.kind for e in result.events] == [EventKind.THESIS_BROKEN]
    assert len(notifier.sent) == 1 and "thesis-break level 100.5 hit" in notifier.sent[0][1]
    assert engine.apply([obs(price=99.0, at=T0 + timedelta(minutes=5))]).events == ()


def test_apply_call_sets_price_target_and_thesis_in_one_write(engine, repo):
    engine.set_target("AAPL", Indicator.PRICE, 50.0, False)
    f = engine.apply_call("AAPL", price_level=310.0, thesis_level=255.0)
    st = f.get("AAPL")
    assert st.target(Indicator.PRICE).level == 310.0 and st.target(Indicator.PRICE).enabled
    assert st.thesis.level == 255.0 and st.thesis.state is AlertState.ARMED
    assert engine.snapshot().file == f


def test_apply_call_with_only_one_part_leaves_the_other(engine):
    engine.set_target("AAPL", Indicator.PRICE, 50.0, True)
    f = engine.apply_call("AAPL", price_level=None, thesis_level=40.0)
    assert f.get("AAPL").target(Indicator.PRICE).level == 50.0 and f.get("AAPL").thesis.level == 40.0
    with pytest.raises(ValueError):
        engine.apply_call("AAPL", price_level=None, thesis_level=None)


def test_removing_the_only_thesis_break_removes_the_symbol(engine):
    engine.set_thesis_break("AAPL", 40.0)
    assert engine.set_thesis_break("AAPL", None).get("AAPL") is None


def test_clear_targets_keeps_the_thesis_break(engine):
    engine.set_target("AAPL", Indicator.PE, 25.0, True)
    engine.set_thesis_break("AAPL", 40.0)
    st = engine.clear_targets("AAPL").get("AAPL")
    assert st.targets == () and st.thesis.level == 40.0
