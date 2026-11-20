from PySide6.QtCore import Qt

from obb_terminal.domain.models import Period, Statement
from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.panels.financials import FinancialsPanel
from tests.fakes import make_profile


def ready(qtbot, loader):
    panel = FinancialsPanel(loader)
    qtbot.addWidget(panel)
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    return panel


def test_renders_table(qtbot, loader):
    panel = ready(qtbot, loader)
    t = panel.table
    assert (t.rowCount(), t.columnCount()) == (2, 2)
    assert t.horizontalHeaderItem(0).text() == "2025-09-30"
    assert t.verticalHeaderItem(0).text() == "Total revenue"
    assert t.item(0, 0).text() == "416.16B"
    assert t.item(1, 1).text() == "—"
    assert panel.currency_label.text() == "Reported in USD"


def test_tab_and_period_change_refetch(qtbot, loader, fake_md):
    panel = ready(qtbot, loader)
    panel.tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: fake_md.calls_to("financials")[-1][2] is Statement.BALANCE, timeout=2000)
    qtbot.mouseClick(panel.quarterly_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: fake_md.calls_to("financials")[-1][3] is Period.QUARTER, timeout=2000)
    assert fake_md.calls_to("financials")[-1][2] is Statement.BALANCE


def test_currency_label_is_plain_text(qtbot, loader):
    panel = FinancialsPanel(loader)
    qtbot.addWidget(panel)
    assert panel.currency_label.textFormat() is Qt.TextFormat.PlainText
