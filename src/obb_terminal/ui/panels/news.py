"""Recent headlines; click to open in the default browser (http/https only)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from obb_terminal.data.market import MarketData
from obb_terminal.domain.models import NewsItem
from obb_terminal.ui.panels.base import Panel
from obb_terminal.ui.worker import Loader


def _open_in_browser(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class NewsPanel(Panel):
    name = "news"

    def __init__(self, loader: Loader, parent: QWidget | None = None) -> None:
        self.open_url: Callable[[str], None] = _open_in_browser
        super().__init__(loader, "NEWS", parent)

    def build_content(self) -> QWidget:
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAccessibleName("News headlines")
        self.list.itemClicked.connect(self._on_click)
        return self.list

    def make_fetch(self, symbol: str) -> Callable[[MarketData], list[NewsItem]]:
        return lambda md: md.news(symbol, 20)

    def render(self, items: list[NewsItem]) -> None:
        self.list.clear()
        for news in items:
            row = QListWidgetItem(f"{news.published:%Y-%m-%d %H:%M}  {news.title}")
            row.setData(Qt.ItemDataRole.UserRole, news.url)
            row.setToolTip(news.url)
            self.list.addItem(row)

    def _on_click(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.ItemDataRole.UserRole) or ""
        if url.startswith(("https://", "http://")):
            self.open_url(url)
