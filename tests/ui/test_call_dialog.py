from dataclasses import replace

from PySide6.QtWidgets import QDialogButtonBox

from obb_terminal.domain.targets import Indicator, SymbolTargets, Target, ThesisBreak
from obb_terminal.ui.valuation.call_dialog import UseAsTargetsDialog
from tests.fakes import CALL


def make(qtbot, call=CALL, current=None, in_watchlist=True):
    d = UseAsTargetsDialog("AAPL", call, "USD", current, in_watchlist)
    qtbot.addWidget(d)
    return d


def ok_button(d):
    return d.buttons.button(QDialogButtonBox.StandardButton.Ok)


def test_lists_both_levels_with_current_values(qtbot):
    current = SymbolTargets("AAPL", (Target(Indicator.PRICE, 250.0),), thesis=ThesisBreak(200.0))
    d = make(qtbot, current=current)
    assert d.price_box.text() == "Price target ≤ 310.00 USD (now ≤ 250)"
    assert d.thesis_box.text() == "Thesis break at 255.00 USD (now 200.00)"
    assert d.price_box.isChecked() and d.thesis_box.isChecked()
    assert d.add_box.isHidden()
    assert d.choice() == (310.0, 255.0, False)


def test_unticking_and_missing_levels(qtbot):
    d = make(qtbot, call=replace(CALL, thesis_break=None), in_watchlist=False)
    assert d.price_box.text() == "Price target ≤ 310.00 USD (none set)"
    assert not d.thesis_box.isEnabled() and not d.thesis_box.isChecked()
    assert d.thesis_box.text() == "Thesis break: none given"
    assert not d.add_box.isHidden() and d.add_box.isChecked() and not d.add_box.isEnabled()
    assert d.add_box.text() == "Add AAPL to the watchlist (alerts only run for watchlist symbols)"
    assert d.choice() == (310.0, None, True)
    d.price_box.setChecked(False)
    assert not ok_button(d).isEnabled()
    assert d.choice() == (None, None, True)
