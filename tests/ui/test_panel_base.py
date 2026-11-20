import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from obb_terminal.data.market import NoData, SourceUnavailable
from obb_terminal.ui.panels.base import Panel, PanelState
from tests.fakes import make_profile, make_quote


class DummyPanel(Panel):
    name = "dummy"

    def build_content(self):
        self.content = QLabel()
        return self.content

    def make_fetch(self, symbol):
        return lambda md: md.quote(symbol)

    def render(self, quote):
        self.content.setText(f"{quote.last:.2f}")


def make(qtbot, loader):
    panel = DummyPanel(loader, "DUMMY")
    qtbot.addWidget(panel)
    return panel


def test_starts_empty(qtbot, loader):
    assert make(qtbot, loader).state is PanelState.EMPTY


def test_load_renders_and_emits_loaded(qtbot, loader):
    panel = make(qtbot, loader)
    with qtbot.waitSignal(panel.loaded, timeout=2000) as blocker:
        panel.load("AAPL", make_profile("AAPL"))
    assert panel.state is PanelState.READY
    assert panel.content.text() == "100.00"
    assert blocker.args[0].symbol == "AAPL"


def test_no_data_shows_message_and_retry_recovers(qtbot, loader, fake_md):
    panel = make(qtbot, loader)
    fake_md.results["quote"] = NoData("x")
    panel.load("HGLG11.SA", make_profile("HGLG11.SA"))
    qtbot.waitUntil(lambda: panel.state is PanelState.ERROR, timeout=2000)
    assert panel.error_text == "No data available for HGLG11.SA"
    fake_md.results["quote"] = lambda s: make_quote(s)
    qtbot.mouseClick(panel.retry_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)


def test_other_errors_show_user_message(qtbot, loader, fake_md):
    panel = make(qtbot, loader)
    fake_md.results["quote"] = SourceUnavailable("Source unavailable (offline?)", reason="network")
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.ERROR, timeout=2000)
    assert panel.error_text == "Source unavailable (offline?)"


def test_silent_refresh_failure_keeps_content(qtbot, loader, fake_md):
    panel = make(qtbot, loader)
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    fake_md.results["quote"] = NoData("x")
    panel.refresh()
    qtbot.waitUntil(lambda: not loader.is_pending("dummy"), timeout=2000)
    assert panel.state is PanelState.READY
    assert panel.content.text() == "100.00"


def test_refresh_skipped_while_pending(qtbot, loader, fake_md):
    panel = make(qtbot, loader)
    gate = threading.Event()
    fake_md.gates["quote"] = gate
    panel.load("AAPL", make_profile("AAPL"))
    panel.refresh()
    gate.set()
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    assert len(fake_md.calls_to("quote")) == 1


def test_double_click_title_requests_maximise(qtbot, loader):
    panel = make(qtbot, loader)
    with qtbot.waitSignal(panel.maximiseRequested, timeout=1000):
        qtbot.mouseDClick(panel.title, Qt.MouseButton.LeftButton)


def test_clear_returns_to_empty(qtbot, loader):
    panel = make(qtbot, loader)
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    panel.clear()
    assert panel.state is PanelState.EMPTY
    assert panel.symbol is None


def test_error_text_is_plain_text(qtbot, loader):
    assert make(qtbot, loader)._error_label.textFormat() is Qt.TextFormat.PlainText


class SlowPanel(DummyPanel):
    name = "slow"
    timeout_s = 0.2


def test_panel_timeout_is_forwarded_to_loader(qtbot, loader, fake_md):
    fake_md.gates["quote"] = threading.Event()
    panel = SlowPanel(loader, "SLOW")
    qtbot.addWidget(panel)
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.ERROR, timeout=700)  # Loader default is 1.0 s
    assert panel.error_text == "Timed out"
