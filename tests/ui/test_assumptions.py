import pytest

from obb_terminal.domain.models import Assumptions, MethodWeights
from obb_terminal.ui.valuation.assumptions import AssumptionsBox

DEFAULTS = Assumptions(0.09, 0.15, 0.025, 0.03)


@pytest.fixture
def box(qtbot):
    widget = AssumptionsBox()
    qtbot.addWidget(widget)
    widget.set_defaults(DEFAULTS)
    widget.set_values(DEFAULTS)
    return widget


def test_set_values_shows_percent_and_does_not_emit(qtbot, box):
    with qtbot.assertNotEmitted(box.changed, wait=200):
        box.set_values(DEFAULTS)
    assert box.spins["discount_rate"].value() == pytest.approx(9.0)
    assert box.spins["w_dcf"].value() == pytest.approx(35.0)
    assert box.spins["s_upside"].value() == pytest.approx(30.0)
    assert box.values() == DEFAULTS


def test_edit_emits_debounced_assumptions(qtbot, box):
    with qtbot.waitSignal(box.changed, timeout=1000) as blocker:
        box.spins["discount_rate"].setValue(12.0)
        box.spins["w_dividend"].setValue(0.0)
    a = blocker.args[0]
    assert a.discount_rate == pytest.approx(0.12)
    assert a.method_weights == MethodWeights(0.35, 0.30, 0.20, 0.0)


def test_reset_restores_defaults_and_emits(qtbot, box):
    box.spins["growth_1_5"].setValue(1.0)
    with qtbot.waitSignal(box.changed, timeout=1000) as blocker:
        box.reset_button.click()
    assert blocker.args[0] == DEFAULTS
    assert box.spins["growth_1_5"].value() == pytest.approx(15.0)


def test_spins_do_not_track_keyboard(box):
    assert all(not spin.keyboardTracking() for spin in box.spins.values())
    assert len(box.spins) == 13 + 8  # stock fields + ETF fields (10-year, coverage, 6 weights)


def test_cancel_pending_drops_debounced_edit(qtbot, box):
    with qtbot.assertNotEmitted(box.changed, wait=600):
        box.spins["discount_rate"].setValue(12.0)
        box.cancel_pending()
