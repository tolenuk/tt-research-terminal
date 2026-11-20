import threading
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from obb_terminal.alerts import checker
from obb_terminal.alerts.checker import describe, main, run_check
from obb_terminal.alerts.engine import AlertEngine
from obb_terminal.alerts.store import AlertsStatus, StatusStore, TargetsRepo
from obb_terminal.alerts.systemd import InstallError, TimerStatus
from obb_terminal.data.market import EngineUnavailable, NoData, SourceUnavailable
from obb_terminal.domain.targets import Indicator
from obb_terminal.store import AppState, Store
from tests.fakes import (
    FakeInstaller,
    FakeMarketData,
    FakeNotifier,
    install_standard,
    make_valuation_inputs,
)

NOW = datetime(2026, 10, 13, 14, 15, tzinfo=UTC)


class Env:
    def __init__(self, tmp_path, symbols=("AAPL", "SHEL.L", "PETR4.SA", "HGLG11.SA")):
        self.md = FakeMarketData()
        install_standard(self.md)
        self.notifier = FakeNotifier()
        self.repo = TargetsRepo(tmp_path / "targets.json")
        self.engine = AlertEngine(self.repo, self.notifier, clock=lambda: NOW)
        self.watchlist = Store(tmp_path / "watchlist.json")
        self.watchlist.save(AppState(tuple(symbols)))
        self.status = StatusStore(tmp_path / "alerts-status.json")
        self.waits: list[float] = []

    def factory(self, wait_s):
        self.waits.append(wait_s)
        return self.md

    def run(self, **kw):
        return run_check(self.factory, self.engine, self.watchlist, self.status, now=lambda: NOW, **kw)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_no_enabled_targets_skips_the_engine(env):
    env.engine.set_target("AAPL", Indicator.PE, 25.0, False)
    code, status = env.run()
    assert (code, status) == (0, AlertsStatus(NOW, True, 0, 0, 0, 0))
    assert env.waits == [] and env.status.read() == status


def test_unreadable_targets_are_reported_not_ok(env, monkeypatch):
    env.engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    original = type(env.repo.path).read_text

    def read_text(self, *args, **kwargs):
        if self == env.repo.path:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.read_text", read_text)
    code, status = env.run()
    assert code == 0
    assert status == AlertsStatus(NOW, False, 0, 0, 0, 0, "targets unreadable")
    assert env.waits == [] and env.md.calls_to("valuation_inputs") == []
    assert env.status.read() == status


def test_only_watchlisted_symbols_with_enabled_targets_are_fetched(env):
    env.engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    env.engine.set_target("SHEL.L", Indicator.PE, 1.0, True)
    env.engine.set_target("MSFT", Indicator.PE, 30.0, True)  # not on the watchlist
    code, status = env.run()
    assert sorted(c[1] for c in env.md.calls_to("valuation_inputs")) == ["AAPL", "SHEL.L"]
    assert (code, status.ok, status.checked, status.fired) == (0, True, 2, 1)
    assert env.waits == [120.0]
    assert env.notifier.bodies()[0].startswith("AAPL: Price ≤ 400 hit")


def test_fires_once_across_two_runs(env):
    env.engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    env.run()
    env.md.results["valuation_inputs"] = lambda s: replace(make_valuation_inputs(s), fetched_at=NOW)
    _, second = env.run()
    assert second.fired == 0 and len(env.notifier.sent) == 1


def test_two_concurrent_runs_notify_exactly_once(tmp_path):
    env = Env(tmp_path, symbols=("AAPL",))
    other_notifier = FakeNotifier()
    other = AlertEngine(env.repo, other_notifier, clock=lambda: NOW)
    env.engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    barrier = threading.Barrier(2)
    env.md.results["valuation_inputs"] = lambda s: (barrier.wait(2), make_valuation_inputs(s))[1]
    runs = [threading.Thread(target=run_check, args=(env.factory, eng, env.watchlist, env.status))
            for eng in (env.engine, other)]
    for thread in runs:
        thread.start()
    for thread in runs:
        thread.join(5)
    assert len(env.notifier.sent) + len(other_notifier.sent) == 1  # a single target: one hit, no all-hit


def test_engine_failure_exits_one(env):
    env.engine.set_target("AAPL", Indicator.PE, 25.0, True)

    def broken(wait_s):
        raise EngineUnavailable("Data engine failed to load")

    code, status = run_check(broken, env.engine, env.watchlist, env.status, now=lambda: NOW)
    assert code == 1
    assert status == AlertsStatus(NOW, False, 0, 0, 1, 0, "Data engine unavailable")
    assert env.status.read() == status


def test_offline_and_failures_are_recorded_and_successes_applied(env):
    env.engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    env.engine.set_target("SHEL.L", Indicator.PE, 5.0, True)
    env.engine.set_target("PETR4.SA", Indicator.PE, 5.0, True)
    base = env.md.results["valuation_inputs"]
    env.md.results["valuation_inputs"] = lambda s: {
        "SHEL.L": SourceUnavailable("offline", s, reason="network"),
        "PETR4.SA": NoData("none", s),
    }.get(s) or base(s)
    _, status = env.run()
    assert (status.ok, status.checked, status.failed, status.error, status.fired) == (False, 1, 2, "offline", 1)


def test_budget_skips_unfinished_symbols(env):
    for symbol in ("AAPL", "SHEL.L", "PETR4.SA", "HGLG11.SA"):
        env.engine.set_target(symbol, Indicator.PRICE, 1.0, True)
    gate = threading.Event()
    env.md.gates["valuation_inputs"] = gate
    try:
        code, status = env.run(budget_s=0.5, margin_s=0.2, workers=2)
    finally:
        gate.set()
    assert code == 0 and status.skipped == 4 and status.checked == 0 and not status.ok


def test_fetch_finishing_within_the_margin_is_counted(env):
    env.engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    base = env.md.results["valuation_inputs"]
    env.md.results["valuation_inputs"] = lambda s: (time.sleep(0.3), base(s))[1]
    code, status = env.run(budget_s=0.6, margin_s=0.4, workers=1)
    assert (code, status.checked, status.skipped) == (0, 1, 0)


def test_unexpected_engine_error_still_writes_status(env):
    env.engine.set_target("AAPL", Indicator.PE, 25.0, True)

    def broken(wait_s):
        raise RuntimeError("boom")

    code, status = run_check(broken, env.engine, env.watchlist, env.status, now=lambda: NOW)
    assert code == 1
    assert status == AlertsStatus(NOW, False, 0, 0, 1, 0, "Unexpected error — see log")
    assert env.status.read() == status


def test_unexpected_apply_error_still_writes_status(env):
    env.engine.set_target("AAPL", Indicator.PRICE, 400.0, True)

    def broken(observations):
        raise RuntimeError("boom")

    env.engine.apply = broken
    code, status = env.run()
    assert code == 1
    assert (status.ok, status.checked, status.error) == (False, 1, "Unexpected error — see log")
    assert env.status.read() == status


def test_busy_lock_is_reported(env):
    env.engine.set_target("AAPL", Indicator.PRICE, 400.0, True)
    entered, release = threading.Event(), threading.Event()

    def holder():
        with env.repo.transaction():
            entered.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    entered.wait(2)
    original = env.engine.apply
    env.engine.apply = lambda obs: original(obs, lock_timeout_s=0.1)
    try:
        code, status = env.run()
    finally:
        release.set()
        thread.join(2)
    assert (code, status.error, status.ok, status.fired) == (0, "busy", False, 0)
    assert env.notifier.sent == []


def test_describe():
    assert describe(TimerStatus(False, False, False, None)) == (
        "Background alerts: off (timer inactive)\nLast check: never")
    last = AlertsStatus(NOW, False, 3, 1, 0, 2, "offline")
    text = describe(TimerStatus(True, True, True, last, None))
    assert text.startswith("Background alerts: on (timer active)\nLast check: ")
    assert text.endswith("— problem (offline); 3 checked, 1 failed, 0 skipped, 2 alerts")


@pytest.fixture
def cli(monkeypatch, tmp_path):
    fake = FakeInstaller()
    monkeypatch.setattr(checker, "default_installer", lambda: fake)
    monkeypatch.setattr(checker, "setup_logging", lambda directory: None)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return fake


def test_cli_install_uninstall_status(cli, capsys):
    assert main(["--install"]) == 0 and cli.calls == ["install"]
    assert main(["--status"]) == 0
    assert "Background alerts: on (timer active)" in capsys.readouterr().out
    assert main(["--uninstall"]) == 0 and cli.calls[-1] == "uninstall"
    cli.error = InstallError("systemd user session unavailable")
    assert main(["--install"]) == 1
    assert "obb-terminal-alerts: systemd user session unavailable" in capsys.readouterr().err


def test_cli_rejects_two_actions(cli):
    with pytest.raises(SystemExit) as info:
        main(["--install", "--uninstall"])
    assert info.value.code == 2


def test_cli_check_with_no_targets_needs_no_engine(cli, monkeypatch, tmp_path):
    def no_engine(wait_s):
        raise AssertionError("engine must not start")

    monkeypatch.setattr(checker, "_market_data", no_engine)
    assert main([]) == 0
    assert StatusStore(tmp_path / "data" / "obb-terminal" / "alerts-status.json").read().ok


def test_symbol_with_only_a_thesis_break_is_checked_and_fires(env):
    price = make_valuation_inputs("AAPL").price
    env.engine.set_thesis_break("AAPL", price * 1.01)
    code, status = env.run()
    assert code == 0 and status.checked == 1 and status.fired == 1
    assert "thesis-break level" in env.notifier.sent[0][1]


# --- daily journal scoring (v1.3) ----------------------------------------------------------


def test_daily_scoring_runs_once_and_skips_the_engine_when_nothing_is_due(tmp_path):
    from datetime import date

    from obb_terminal.alerts.checker import run_daily_scoring
    from obb_terminal.journal.scorer import LAST_SCORED
    from obb_terminal.journal.store import Journal

    journal = Journal(tmp_path / "journal.sqlite3")
    made = []

    def factory(wait_s):
        made.append(wait_s)
        return FakeMarketData()

    today = date(2026, 10, 27)
    result = run_daily_scoring(factory, journal, today)
    assert result is not None and result.scored == 0 and made == []
    assert journal.get_meta(LAST_SCORED) == "2026-10-27"
    assert run_daily_scoring(factory, journal, today) is None  # already scored today


def test_daily_scoring_fetches_when_due_and_survives_an_engine_failure(tmp_path):
    from datetime import date

    from obb_terminal.alerts.checker import run_daily_scoring
    from obb_terminal.journal.store import Journal
    from tests.journal.samples import snap

    journal = Journal(tmp_path / "journal.sqlite3")
    journal.record(snap())

    def broken(wait_s):
        raise EngineUnavailable("no engine")

    assert run_daily_scoring(broken, journal, date(2026, 10, 27)) is None
    md = FakeMarketData()
    md.results["history"] = NoData("none", "AAPL")
    result = run_daily_scoring(lambda w: md, journal, date(2026, 10, 27))
    assert result is not None and set(result.failed) == {"AAPL", "SPY"}


def test_shared_factory_starts_the_engine_once():
    from obb_terminal.alerts.checker import shared_factory

    made = []
    factory = shared_factory(lambda wait_s: made.append(wait_s) or object())
    first = factory(10.0)
    assert factory(20.0) is first and made == [10.0]


def test_daily_scoring_gets_only_the_remaining_budget(tmp_path, monkeypatch):
    from datetime import date

    from obb_terminal.alerts import checker as mod
    from obb_terminal.journal.store import Journal
    from tests.journal.samples import snap

    journal = Journal(tmp_path / "journal.sqlite3")
    journal.record(snap())
    budgets = []
    monkeypatch.setattr(mod, "run_scoring", lambda md, j, today, **kw: budgets.append(kw["budget_s"]))
    mod.run_daily_scoring(lambda w: FakeMarketData(), journal, date(2026, 10, 27), budget_s=100.0)
    assert len(budgets) == 1 and 99.0 < budgets[0] <= 100.0  # less the engine start-up
    assert mod.run_daily_scoring(lambda w: FakeMarketData(), journal, date(2026, 10, 27), budget_s=20.0) is None
    assert len(budgets) == 1  # too little time left: skipped
    assert mod.scoring_budget(started=0.0, now=200.0) == mod.RUN_LIMIT_S - mod.RUN_SLACK_S - 200.0


def test_log_handler_shares_the_app_log_on_posix_but_not_on_windows(monkeypatch, tmp_path):
    from logging.handlers import RotatingFileHandler, WatchedFileHandler

    monkeypatch.setattr(checker, "IS_WINDOWS", False)
    posix = checker.log_handler(tmp_path)
    monkeypatch.setattr(checker, "IS_WINDOWS", True)
    windows = checker.log_handler(tmp_path)
    try:
        assert isinstance(posix, WatchedFileHandler) and posix.baseFilename.endswith("obb-terminal.log")
        assert isinstance(windows, RotatingFileHandler)
        assert windows.baseFilename.endswith("obb-terminal-alerts.log")
    finally:
        posix.close()
        windows.close()
