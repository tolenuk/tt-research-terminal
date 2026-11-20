from PySide6.QtCore import Qt

from obb_terminal.domain.models import Currency, FundInfo
from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.panels.fund import FundPanel
from tests.fakes import make_profile


def test_renders_dividends_and_ttm(qtbot, loader):
    panel = FundPanel(loader)
    qtbot.addWidget(panel)
    panel.load("HGLG11.SA", make_profile("HGLG11.SA"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    assert "TTM yield 5.45%" in panel.summary.text()
    assert panel.table.rowCount() == 3
    assert panel.table.item(0, 0).text() == "2026-09-01"
    assert panel.table.item(0, 1).text() == "1.1000 BRL"
    assert panel.empty_note.isHidden()


def test_no_dividends_note(qtbot, loader, fake_md):
    fake_md.results["fund_info"] = lambda s: FundInfo(s, Currency.BRL, (), 0.0, None, None)
    panel = FundPanel(loader)
    qtbot.addWidget(panel)
    panel.load("BOVA11.SA", None)
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    assert not panel.empty_note.isHidden()
    assert panel.table.rowCount() == 0


def test_summary_is_plain_text(qtbot, loader):
    panel = FundPanel(loader)
    qtbot.addWidget(panel)
    assert panel.summary.textFormat() is Qt.TextFormat.PlainText
