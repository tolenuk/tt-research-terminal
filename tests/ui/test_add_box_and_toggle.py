import threading
from datetime import UTC, datetime

import pytest
from PySide6.QtCore import Qt

from obb_terminal.alerts.store import AlertsStatus
from obb_terminal.alerts.systemd import InstallError, TimerStatus
from obb_terminal.ui.jobs import SerialJobs
from obb_terminal.ui.watchlist_tab.add_box import AddSymbolBox
from obb_terminal.ui.watchlist_tab.alerts_bar import AlertsToggle, status_text
from tests.fakes import FakeInstaller

LAST = AlertsStatus(datetime(2026, 10, 13, 13, 15, tzinfo=UTC), True, 4, 0, 0, 1)


# --- AddSymbolBox ---------------------------------------------------------------------------


@pytest.fixture
def box(qtbot, loader):
    b = AddSymbolBox(loader, debounce_ms=10)
    qtbot.addWidget(b)
    b.show()
    return b


def test_enter_validates_then_emits_and_clears(qtbot, box, fake_md):
    box.edit.setText("shel.l")
    with qtbot.waitSignal(box.symbolChosen) as signal:
        qtbot.keyClick(box.edit, Qt.Key.Key_Return)
    assert signal.args == ["SHEL.L"] and box.edit.text() == ""
    assert fake_md.calls_to("profile") == [("profile", "SHEL.L")]


def test_unknown_and_malformed_symbols_show_errors(qtbot, box, fake_md):
    box.submit("NOPE")
    qtbot.waitUntil(lambda: box.error_label.isVisible(), timeout=2000)
    assert box.error_label.text() == "Unknown symbol: NOPE"
    box.submit("AAPL,MSFT")
    assert box.error_label.text() == "Unknown symbol: AAPL,MSFT"
    assert fake_md.calls_to("profile") == [("profile", "NOPE")]


def test_name_search_offers_matches(qtbot, box, fake_md):
    qtbot.keyClicks(box.edit, "petro")
    qtbot.waitUntil(lambda: box.model.rowCount() == 2, timeout=2000)
    assert fake_md.calls_to("search")[-1][1] == "petro"
    with qtbot.waitSignal(box.symbolChosen) as signal:
        box.completer.activated[str].emit("PETR4.SA")
    assert signal.args == ["PETR4.SA"]


# --- AlertsToggle ---------------------------------------------------------------------------


@pytest.fixture
def jobs():
    j = SerialJobs()
    yield j
    j.shutdown()


def test_status_text():
    assert status_text(TimerStatus(False, False, False, None)) == "Background alerts: off"
    assert status_text(TimerStatus(True, True, True, None)) == "Background alerts: on — no check yet"
    local = LAST.last_run_at.astimezone().strftime("%H:%M")
    assert status_text(TimerStatus(True, True, True, LAST)) == f"Background alerts: on — last check {local} ✓"
    failed = AlertsStatus(LAST.last_run_at, False, 1, 1, 0, 0, "offline")
    assert status_text(TimerStatus(True, True, True, failed)).endswith("✗ offline")
    assert status_text(TimerStatus(False, False, False, None, "systemctl not found")) == (
        "Background alerts: error — systemctl not found")


def test_toggle_reads_status_then_installs_and_uninstalls(qtbot, jobs):
    installer = FakeInstaller()
    toggle = AlertsToggle(jobs, installer)
    qtbot.addWidget(toggle)
    qtbot.waitUntil(lambda: toggle.button.isEnabled(), timeout=2000)
    assert not toggle.button.isChecked() and toggle.status() == "Background alerts: off"
    toggle.button.click()
    qtbot.waitUntil(lambda: toggle.status() == "Background alerts: on — no check yet", timeout=2000)
    assert toggle.button.isChecked() and installer.calls == ["status", "install", "status"]
    toggle.button.click()
    qtbot.waitUntil(lambda: toggle.status() == "Background alerts: off", timeout=2000)
    assert installer.calls[-2:] == ["uninstall", "status"]


def test_toggle_is_disabled_while_working_and_reverts_on_error(qtbot, jobs):
    installer = FakeInstaller()
    toggle = AlertsToggle(jobs, installer)
    qtbot.addWidget(toggle)
    qtbot.waitUntil(lambda: toggle.button.isEnabled(), timeout=2000)
    installer.error = InstallError("systemd user session unavailable")
    installer.gate = threading.Event()
    toggle.button.click()
    assert not toggle.button.isEnabled() and toggle.status() == "Background alerts: turning on…"
    installer.gate.set()
    qtbot.waitUntil(lambda: toggle.button.isEnabled(), timeout=2000)
    assert not toggle.button.isChecked()
    assert toggle.status() == "Background alerts: error — systemd user session unavailable"
