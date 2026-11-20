from PySide6.QtCore import Qt

from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.panels.news import NewsPanel
from tests.fakes import make_profile


def test_lists_headlines_and_opens_on_click(qtbot, loader):
    panel = NewsPanel(loader)
    qtbot.addWidget(panel)
    opened = []
    panel.open_url = opened.append
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    assert panel.list.count() == 2
    assert panel.list.item(0).text() == "2026-10-09 20:00  AAPL headline one"
    panel.list.itemClicked.emit(panel.list.item(0))
    assert opened == ["https://finance.yahoo.com/news/one.html"]


def test_ignores_non_http_urls(qtbot, loader):
    panel = NewsPanel(loader)
    qtbot.addWidget(panel)
    opened = []
    panel.open_url = opened.append
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    item = panel.list.item(0)
    item.setData(Qt.ItemDataRole.UserRole, "file:///etc/passwd")
    panel.list.itemClicked.emit(item)
    assert opened == []
