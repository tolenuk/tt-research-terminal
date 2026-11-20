"""TODAY's distance-to-fair-value bars."""

from PySide6.QtCore import QPointF, Qt

from obb_terminal.ui.desk.distance import ROW_H, DistanceBars, DistanceRow, bar_x, distance_rows


def point(symbol: str, upside: float) -> tuple[str, float]:
    return symbol, upside


def test_rows_put_the_names_nearest_fair_value_first():
    rows = distance_rows([point("AAPL", -0.45), point("SHEL.L", 0.63), point("HGLG11.SA", 0.02), point("PETR4.SA", 0.10)])
    assert [r.symbol for r in rows] == ["HGLG11.SA", "PETR4.SA", "AAPL", "SHEL.L"]


def test_rows_are_capped_and_ties_keep_symbol_order():
    rows = distance_rows([point(s, 0.05) for s in ("C", "A", "B")], limit=2)
    assert [r.symbol for r in rows] == ["A", "B"]


def test_bar_puts_price_in_the_centre_and_holds_far_values_at_the_edge():
    assert bar_x(0.0, 100, 200) == 200
    assert bar_x(0.40, 100, 200) == 300
    assert bar_x(2.5, 100, 200) == 300
    assert bar_x(-0.9, 100, 200) == 100


def test_double_click_on_a_row_asks_to_open_it(qtbot):
    bars = DistanceBars()
    qtbot.addWidget(bars)
    bars.resize(400, 200)
    bars.set_rows([DistanceRow("AAPL", -0.45), DistanceRow("SPY", 0.1)])
    assert "AAPL: fair value -45% from price" in bars.accessibleDescription()
    assert bars.row_at(QPointF(10, ROW_H * 1.5)) == "SPY"
    assert bars.row_at(QPointF(10, ROW_H * 5)) is None
    with qtbot.waitSignal(bars.rowActivated) as blocker:
        qtbot.mouseDClick(bars, Qt.MouseButton.LeftButton, pos=QPointF(20, ROW_H / 2).toPoint())
    assert blocker.args == ["AAPL"]
    assert not bars.grab().isNull()


def test_empty_bars_say_why_instead_of_staying_blank(qtbot):
    bars = DistanceBars()
    qtbot.addWidget(bars)
    bars.resize(300, 120)
    bars.set_rows([])
    assert bars.accessibleDescription() == "No stocks with a fair value"
    assert not bars.grab().isNull()


def test_keyboard_moves_between_rows_and_enter_opens_one(qtbot):
    bars = DistanceBars()
    qtbot.addWidget(bars)
    bars.set_rows([DistanceRow("AAPL", 0.1), DistanceRow("SPY", 0.2), DistanceRow("MSFT", 0.3)])
    assert bars.focusPolicy() == Qt.FocusPolicy.StrongFocus
    qtbot.keyClick(bars, Qt.Key.Key_Down)
    qtbot.keyClick(bars, Qt.Key.Key_Down)
    qtbot.keyClick(bars, Qt.Key.Key_Down)  # held on the last row
    with qtbot.waitSignal(bars.rowActivated) as blocker:
        qtbot.keyClick(bars, Qt.Key.Key_Return)
    assert blocker.args == ["MSFT"]
    qtbot.keyClick(bars, Qt.Key.Key_Up)
    assert bars.current == 1


def test_bars_ask_for_room_for_every_row(qtbot):
    bars = DistanceBars()
    qtbot.addWidget(bars)
    bars.set_rows([DistanceRow(f"S{i}", 0.01 * i) for i in range(7)])
    assert bars.minimumHeight() == 7 * ROW_H
    bars.set_rows([])
    assert bars.minimumHeight() == 3 * ROW_H
