import threading

from PySide6.QtCore import Qt

from obb_terminal.ui.ticker_bar import TickerBar
from tests.fakes import make_profile, make_quote


def make(qtbot, loader):
    bar = TickerBar(loader, debounce_ms=100)
    qtbot.addWidget(bar)
    bar.show()
    return bar


def test_search_is_debounced_and_fills_model(qtbot, loader, fake_md):
    bar = make(qtbot, loader)
    qtbot.keyClicks(bar.edit, "Petro")
    qtbot.waitUntil(lambda: bar.model.rowCount() == 2, timeout=2000)
    qtbot.wait(200)
    assert [c[1] for c in fake_md.calls_to("search")] == ["Petro"]
    index = bar.model.index(0)
    assert bar.model.data(index, Qt.ItemDataRole.UserRole) == "PETR4.SA"
    assert "Petrobras" in bar.model.data(index, Qt.ItemDataRole.DisplayRole)


def test_single_character_does_not_search(qtbot, loader, fake_md):
    bar = make(qtbot, loader)
    qtbot.keyClicks(bar.edit, "P")
    qtbot.wait(250)
    assert fake_md.calls_to("search") == []


def test_choose_emits_symbol(qtbot, loader):
    bar = make(qtbot, loader)
    with qtbot.waitSignal(bar.symbolRequested, timeout=1000) as blocker:
        bar.choose("PETR4.SA")
    assert blocker.args == ["PETR4.SA"]
    assert bar.edit.text() == "PETR4.SA"


def test_enter_on_raw_symbol_uppercases(qtbot, loader):
    bar = make(qtbot, loader)
    bar.edit.setText("aapl")
    with qtbot.waitSignal(bar.symbolRequested, timeout=1000) as blocker:
        qtbot.keyClick(bar.edit, Qt.Key.Key_Return)
    assert blocker.args == ["AAPL"]


def test_header_shows_profile_and_gbp_quote(qtbot, loader):
    bar = make(qtbot, loader)
    bar.set_profile(make_profile("SHEL.L"))
    bar.set_quote(make_quote("SHEL.L"))
    assert bar.symbol_label.text() == "SHEL.L"
    assert bar.name_label.text() == "Shell plc  ·  LSE  ·  Equity"
    assert bar.price_label.text() == "27.10 GBP"
    assert bar.change_label.property("dir") == "up"
    bar.set_profile(make_profile("SPY"))
    assert bar.name_label.text().endswith("·  ETF")


def test_inline_error_cleared_on_edit(qtbot, loader):
    bar = make(qtbot, loader)
    bar.show_error("Unknown symbol: ZZZZ")
    assert not bar.error_label.isHidden()
    qtbot.keyClicks(bar.edit, "A")
    assert bar.error_label.isHidden()


def test_new_profile_clears_previous_symbols_price(qtbot, loader):
    bar = make(qtbot, loader)
    bar.set_profile(make_profile("SHEL.L"))
    bar.set_quote(make_quote("SHEL.L"))
    bar.set_profile(make_profile("AAPL"))
    assert bar.price_label.text() == ""
    assert bar.change_label.text() == ""
    assert bar.change_label.property("dir") == "flat"


def start_gated_search(qtbot, bar, fake_md, text="Petro"):
    gate = threading.Event()
    fake_md.gates["search"] = gate
    qtbot.keyClicks(bar.edit, text)
    qtbot.waitUntil(lambda: len(fake_md.calls_to("search")) == 1, timeout=2000)
    return gate


def test_late_search_result_after_choose_is_ignored(qtbot, loader, fake_md):
    bar = make(qtbot, loader)
    gate = start_gated_search(qtbot, bar, fake_md)
    bar.choose("PETR4.SA")
    gate.set()
    qtbot.wait(300)
    assert not bar.completer.popup().isVisible()
    assert bar.model.rowCount() == 0


def test_late_search_result_after_enter_is_ignored(qtbot, loader, fake_md):
    bar = make(qtbot, loader)
    gate = start_gated_search(qtbot, bar, fake_md, "AAPL")
    with qtbot.waitSignal(bar.symbolRequested, timeout=1000):
        qtbot.keyClick(bar.edit, Qt.Key.Key_Return)
    gate.set()
    qtbot.wait(300)
    assert not bar.completer.popup().isVisible()
    assert bar.model.rowCount() == 0


def test_empty_matches_hide_the_popup(qtbot, loader, fake_md):
    bar = make(qtbot, loader)
    qtbot.keyClicks(bar.edit, "Petro")
    qtbot.waitUntil(lambda: bar.model.rowCount() == 2, timeout=2000)
    hidden = []
    popup = bar.completer.popup()
    popup.hide = lambda: hidden.append(True)  # spy: popup visibility is platform-dependent
    fake_md.results["search"] = lambda q, limit: []
    bar._on_edited(bar.edit.text())
    qtbot.waitUntil(lambda: len(fake_md.calls_to("search")) == 2, timeout=2000)
    qtbot.waitUntil(lambda: bar.model.rowCount() == 0, timeout=2000)
    assert hidden == [True]


def test_remote_and_user_text_is_rendered_as_plain_text(qtbot, loader):
    bar = make(qtbot, loader)
    for label in (bar.name_label, bar.error_label, bar.price_label, bar.change_label):
        assert label.textFormat() is Qt.TextFormat.PlainText
