from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def alerts(monkeypatch, tmp_path):
    """MainWindow's default alert services, made safe: a temp targets file, a recording
    notifier and a fake installer (UI tests never run notify-send or systemctl)."""
    from obb_terminal.alerts.engine import AlertEngine
    from obb_terminal.alerts.store import TargetsRepo
    from obb_terminal.ui.watchlist_tab import AlertServices
    from tests.fakes import FakeInstaller, FakeNotifier

    notifier, installer = FakeNotifier(), FakeInstaller()
    services = AlertServices(
        AlertEngine(TargetsRepo(tmp_path / "alerts" / "targets.json"), notifier), installer
    )
    monkeypatch.setattr("obb_terminal.ui.main_window.default_alert_services", lambda: services)
    return SimpleNamespace(services=services, notifier=notifier, installer=installer)


@pytest.fixture(autouse=True)
def screener(monkeypatch, tmp_path):
    """MainWindow's TOP 20 sources, made safe: canned snapshots, never Fundamentus or Yahoo."""
    from datetime import UTC, datetime

    from obb_terminal.domain.cheap_screen import Market, MarketSnapshot, ScreenStock
    from obb_terminal.ui.screener import ScreenerSource

    petr = ScreenStock(
        ticker="PETR4",
        name="PETROBRAS",
        price=44.7,
        pl=4.1,
        pvp=1.14,
        ev_ebit=3.34,
        ebit_margin=0.45,
        roic=0.21,
        liquidity=2.7e9,
    )
    fake = SimpleNamespace(calls=0)

    def fetch():
        fake.calls += 1
        return MarketSnapshot((petr,), frozenset(), datetime.now(UTC))

    sources = {
        m: ScreenerSource(fetch, tmp_path / "screener" / f"{m.value.lower()}.json") for m in Market
    }
    monkeypatch.setattr("obb_terminal.ui.main_window.default_screener_sources", lambda: sources)
    return fake
