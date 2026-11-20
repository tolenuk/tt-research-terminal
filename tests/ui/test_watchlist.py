import threading

from PySide6.QtCore import QRectF, Qt

from obb_terminal.data.market import NoData
from obb_terminal.domain.models import Range
from obb_terminal.ui.watchlist import WatchlistStrip, sparkline_points


def make(qtbot, loader, symbols=()):
    strip = WatchlistStrip(loader, list(symbols), refresh_ms=60_000)
    qtbot.addWidget(strip)
    return strip


def test_initial_symbols_refresh_prices(qtbot, loader):
    strip = make(qtbot, loader, ["SHEL.L"])
    qtbot.waitUntil(lambda: "27.10" in strip.list.item(0).text(), timeout=2000)
    assert strip.list.item(0).text().startswith("SHEL.L  27.10  ▲")


def test_add_and_remove_emit_changed(qtbot, loader):
    strip = make(qtbot, loader)
    assert not strip.add_button.isEnabled()
    strip.set_current("AAPL")
    assert strip.add_button.isEnabled()
    with qtbot.waitSignal(strip.changed, timeout=1000) as blocker:
        strip.add_current()
    assert blocker.args == [["AAPL"]]
    assert not strip.add_button.isEnabled()
    with qtbot.waitSignal(strip.changed, timeout=1000) as blocker:
        strip.remove("AAPL")
    assert blocker.args == [[]]


def test_click_activates_symbol(qtbot, loader):
    strip = make(qtbot, loader, ["AAPL", "SPY"])
    with qtbot.waitSignal(strip.symbolActivated, timeout=1000) as blocker:
        strip.list.itemClicked.emit(strip.list.item(1))
    assert blocker.args == ["SPY"]


def test_refresh_skipped_while_running(qtbot, loader, fake_md):
    gate = threading.Event()
    fake_md.gates["quote"] = gate
    strip = make(qtbot, loader, ["AAPL"])
    qtbot.waitUntil(lambda: len(fake_md.calls_to("quote")) == 1, timeout=2000)
    strip.refresh()
    strip.refresh()
    gate.set()
    qtbot.waitUntil(lambda: "100.00" in strip.list.item(0).text(), timeout=2000)
    assert len(fake_md.calls_to("quote")) == 1


def test_set_symbols_is_silent_keeps_prices_and_fetches_only_new(qtbot, loader, fake_md):
    strip = make(qtbot, loader, ["SHEL.L"])
    qtbot.waitUntil(lambda: "27.10" in strip.list.item(0).text(), timeout=2000)
    changed = []
    strip.changed.connect(changed.append)
    strip.set_symbols(["SHEL.L", "PETR4.SA"])
    assert strip.symbols() == ["SHEL.L", "PETR4.SA"]
    assert strip.list.item(0).text().startswith("SHEL.L  27.10")  # known price kept
    qtbot.waitUntil(lambda: "56.00" in strip.list.item(1).text(), timeout=2000)
    quotes = [c[1] for c in fake_md.calls_to("quote")]
    assert quotes.count("SHEL.L") == 1 and quotes.count("PETR4.SA") == 1
    strip.set_symbols(["SHEL.L", "PETR4.SA"])  # unchanged: nothing happens
    strip.set_symbols(["PETR4.SA"])
    assert strip.symbols() == ["PETR4.SA"] and changed == []


def test_sparkline_loads_one_month_of_closes(qtbot, loader, fake_md):
    strip = make(qtbot, loader, ["SHEL.L"])
    qtbot.waitUntil(lambda: strip.sparkline("SHEL.L") == (1.0, 2.0, 3.0), timeout=2000)
    assert [c[2] for c in fake_md.calls_to("history")] == [Range.M1]


def test_sparkline_fetched_once_across_refreshes(qtbot, loader, fake_md):
    strip = make(qtbot, loader, ["AAPL"])
    qtbot.waitUntil(lambda: strip.sparkline("AAPL") is not None, timeout=2000)
    qtbot.waitUntil(lambda: "100.00" in strip.list.item(0).text(), timeout=2000)
    strip.refresh()
    qtbot.waitUntil(lambda: len(fake_md.calls_to("quote")) == 2, timeout=2000)
    assert len(fake_md.calls_to("history")) == 1


def test_sparkline_failure_leaves_chip_without_one(qtbot, loader, fake_md):
    fake_md.results["history"] = NoData("no history")
    strip = make(qtbot, loader, ["AAPL"])
    qtbot.waitUntil(lambda: "100.00" in strip.list.item(0).text(), timeout=2000)
    qtbot.waitUntil(lambda: len(fake_md.calls_to("history")) == 1, timeout=2000)
    qtbot.wait(50)
    assert strip.sparkline("AAPL") is None


def test_sparkline_path_spans_the_box_and_flips_y():
    path = sparkline_points((1.0, 3.0, 2.0), QRectF(0, 0, 40, 10))
    assert [(p.x(), p.y()) for p in path] == [(0.0, 10.0), (20.0, 0.0), (40.0, 5.0)]
    assert sparkline_points((5.0,), QRectF(0, 0, 40, 10)) == []


# --- rolling ticker ---------------------------------------------------------------------

MANY = ["AAPL", "MSFT", "SPY", "SHEL.L", "PETR4.SA", "HGLG11.SA", "NVDA", "AMZN"]


def shown(qtbot, loader, symbols, width):
    strip = make(qtbot, loader, symbols)
    strip.resize(width, 50)
    strip.show()
    qtbot.waitExposed(strip)
    strip.list.doItemsLayout()
    strip._sync_loop()
    return strip


def test_overflowing_strip_loops_with_copies_hidden_from_symbols(qtbot, loader):
    strip = shown(qtbot, loader, MANY, 300)
    assert strip.is_rolling()
    assert strip.list.count() == 2 * len(MANY)  # one copy of the set trails the real one
    assert strip.symbols() == MANY


def test_strip_that_fits_does_not_roll(qtbot, loader):
    strip = shown(qtbot, loader, ["AAPL"], 1200)
    assert not strip.is_rolling()
    assert strip.list.count() == 1


def test_ends_fade_only_while_rolling(qtbot, loader):
    still = shown(qtbot, loader, ["AAPL"], 1200)
    assert not any(f.isVisible() for f in still.fades)
    rolling = shown(qtbot, loader, MANY, 300)
    assert all(f.isVisible() for f in rolling.fades)
    left, right = rolling.fades
    assert left.x() == 0 and right.geometry().right() == rolling.list.width() - 1
    assert left.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert not left.grab().isNull()


def test_strip_buttons_say_what_they_do(qtbot, loader):
    strip = shown(qtbot, loader, ["AAPL"], 1200)
    assert strip.add_button.text() == "+ Add"
    assert strip.remove_button.text() == "− Remove"


def test_roll_advances_and_wraps_seamlessly(qtbot, loader):
    strip = shown(qtbot, loader, MANY, 300)
    bar = strip.list.horizontalScrollBar()
    start = bar.value()
    strip._roll()
    assert bar.value() > start
    span = strip._loop_span()
    bar.setValue(span - 1)
    strip._roll()
    assert 0 <= bar.value() < span  # wrapped back onto the real set


def test_removing_from_a_rolling_strip_drops_its_copy(qtbot, loader):
    strip = shown(qtbot, loader, MANY, 300)
    strip.remove("NVDA")
    strip.list.doItemsLayout()
    strip._sync_loop()
    assert "NVDA" not in strip.symbols()
    names = [strip.list.item(i).data(0x0100) for i in range(strip.list.count())]
    assert "NVDA" not in names


def test_adding_to_a_rolling_strip_keeps_order(qtbot, loader):
    strip = shown(qtbot, loader, MANY, 300)
    strip.set_current("TSLA")
    strip.add_current()
    assert strip.symbols() == [*MANY, "TSLA"]
    assert strip.list.count() == 2 * (len(MANY) + 1)
