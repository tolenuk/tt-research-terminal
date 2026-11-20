from dataclasses import replace

import pytest

from obb_terminal.domain.targets import Indicator, Target
from obb_terminal.ui.watchlist_tab.editor import TargetEditor
from tests.fakes import make_valuation_inputs


@pytest.fixture
def aapl():
    return make_valuation_inputs("AAPL")


def test_new_target_defaults_ten_percent_cheaper_and_previews(qtbot, aapl):
    editor = TargetEditor("AAPL", Indicator.PE, aapl, None)
    qtbot.addWidget(editor)
    assert editor.level.value() == pytest.approx(39.48)  # 43.86 x 0.9, rounded
    assert not editor.delete_button.isEnabled() and editor.enabled.isChecked()
    editor.level.setValue(25.0)
    assert editor.preview.text() == (
        "Current P/E 43.86x · price 336.64 USD\nBuy at 191.87 USD · ↓ 43.0%")


def test_yield_is_edited_in_percent_and_saved_as_fraction(qtbot):
    hglg = make_valuation_inputs("HGLG11.SA")
    editor = TargetEditor("HGLG11.SA", Indicator.DIV_YIELD, hglg, Target(Indicator.DIV_YIELD, 0.08))
    qtbot.addWidget(editor)
    assert editor.level.value() == pytest.approx(8.0) and editor.level.suffix() == " %"
    assert editor.preview.text().endswith("✓ hit")
    with qtbot.waitSignal(editor.saved) as signal:
        editor.level.setValue(9.5)
        editor.enabled.setChecked(False)
        editor.buttons.accepted.emit()
    assert signal.args == ["HGLG11.SA", Indicator.DIV_YIELD, pytest.approx(0.095), False]


def test_delete_and_na_preview(qtbot):
    hglg = make_valuation_inputs("HGLG11.SA")
    editor = TargetEditor("HGLG11.SA", Indicator.EV_EBITDA, hglg, Target(Indicator.EV_EBITDA, 10.0))
    qtbot.addWidget(editor)
    assert editor.preview.text() == "Current EV/EBITDA: n/a — no EBITDA"
    with qtbot.waitSignal(editor.deleted) as signal:
        editor.delete_button.click()
    assert signal.args == ["HGLG11.SA", Indicator.EV_EBITDA]


def test_without_data_preview_waits_then_updates(qtbot, aapl):
    editor = TargetEditor("AAPL", Indicator.PRICE, None, None)
    qtbot.addWidget(editor)
    assert editor.preview.text().startswith("No data yet")
    assert editor.level.value() == 1.0
    editor.level.setValue(300.0)
    editor.set_inputs(replace(aapl, price=290.0))
    assert editor.preview.text() == "Current Price 290.00 · price 290.00 USD\nBuy at 300.00 USD · ✓ hit"
