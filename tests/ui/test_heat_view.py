import pytest
from PySide6.QtCore import Qt

from obb_terminal.domain.targets import Indicator, evaluate
from obb_terminal.ui.watchlist_tab.model import (
    Col,
    HeatMatrixModel,
    HeatSortProxy,
    RowState,
    RowView,
)
from obb_terminal.ui.watchlist_tab.view import HeatMatrixView
from tests.fakes import make_valuation_inputs


@pytest.fixture
def view(qtbot):
    model = HeatMatrixModel()
    model.set_symbols(["AAPL", "SHEL.L"])
    for symbol in ("AAPL", "SHEL.L"):
        inputs = make_valuation_inputs(symbol)
        model.set_row(RowView(symbol, RowState.READY, evaluate(inputs, None), inputs))
    proxy = HeatSortProxy()
    proxy.setSourceModel(model)
    v = HeatMatrixView()
    v.setModel(proxy)
    v.sortByColumn(Col.SYMBOL, Qt.SortOrder.AscendingOrder)
    v.resize(1600, 300)
    qtbot.addWidget(v)
    v.show()
    v._model = model  # keep the source model alive with the view
    return v


def centre(view, row, col):
    return view.visualRect(view.model().index(row, col)).center()


def test_click_indicator_cell_requests_edit(qtbot, view):
    with qtbot.waitSignal(view.editRequested) as signal:
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=centre(view, 1, Col.PE))
    assert signal.args == ["SHEL.L", Indicator.PE]


def test_double_click_other_cell_activates(qtbot, view):
    pos = centre(view, 0, Col.PRICE)
    with qtbot.waitSignal(view.activateRequested) as signal:
        # QAbstractItemView only reports a double-click on the index it saw pressed first.
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert signal.args == ["AAPL"]


def test_keyboard_paths(qtbot, view):
    view.setFocus()
    view.select_cell("AAPL", Col.EV_EBITDA)
    with qtbot.waitSignal(view.editRequested) as edit:
        qtbot.keyClick(view, Qt.Key.Key_Return)
    assert edit.args == ["AAPL", Indicator.EV_EBITDA]
    with qtbot.waitSignal(view.deleteRequested) as delete:
        qtbot.keyClick(view, Qt.Key.Key_Delete)
    assert delete.args == ["AAPL", Indicator.EV_EBITDA]
    with qtbot.waitSignal(view.menuRequested) as menu:
        qtbot.keyClick(view, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
    assert menu.args[0].column() == Col.EV_EBITDA
    view.select_cell("SHEL.L", Col.SYMBOL)
    with qtbot.waitSignal(view.activateRequested) as activate:
        qtbot.keyClick(view, Qt.Key.Key_Enter)
    assert activate.args == ["SHEL.L"]


def test_view_is_named_for_assistive_tech(view):
    assert view.accessibleName() == "Watchlist buy targets" and view.objectName() == "HeatMatrix"


def test_signals_carry_indicator_enums_not_strings(qtbot, view):
    # Qt hands a StrEnum back from the model as a plain str; the view must restore the enum.
    with qtbot.waitSignal(view.editRequested) as signal:
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=centre(view, 0, Col.DIV_YIELD))
    assert type(signal.args[1]) is Indicator
    view.setFocus()
    view.select_cell("AAPL", Col.PB)
    with qtbot.waitSignal(view.deleteRequested) as delete:
        qtbot.keyClick(view, Qt.Key.Key_Delete)
    assert type(delete.args[1]) is Indicator
