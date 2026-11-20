"""TODAY, the morning brief: what needs a decision across the watchlist, most urgent first; under
it how far each name is from fair value and a small value map; beside them the three benchmarks
and the latest headlines on your symbols."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.data.market import DataError
from obb_terminal.domain.models import NewsItem, Quote
from obb_terminal.journal.benchmarks import BENCHMARK
from obb_terminal.ui.desk.common import (
    NO_SYMBOLS,
    TableCell,
    fill_table,
    heading,
    label,
    loading_text,
    panel,
    table,
)
from obb_terminal.ui.desk.data import DeskSnapshot
from obb_terminal.ui.desk.distance import DistanceBars, distance_rows
from obb_terminal.ui.desk.value_map import MapWidget
from obb_terminal.ui.desk.views import (
    ACTION_TEXT,
    BriefAction,
    BriefItem,
    Urgency,
    brief_items,
    brief_title,
    fair_value_gaps,
    value_points,
)
from obb_terminal.ui.fmt import DASH, fmt_num, fmt_pct
from obb_terminal.ui.theme import set_dir
from obb_terminal.ui.worker import Loader

MARKET_REFRESH_S = 300.0
NEWS_PER_SYMBOL = 3
MAX_NEWS_SYMBOLS = 10
MAX_HEADLINES = 12
ACTION_WIDTH = 140
URL_ROLE = Qt.ItemDataRole.UserRole
BENCHMARK_NAMES = {"SPY": "S&P 500 (SPY)", "^FTAS": "FTSE All-Share", "^BVSP": "Bovespa"}
HEADERS = ("Symbol", "What needs a look", "Why", "Last", "")
_TONE = {Urgency.THESIS: "down", Urgency.TARGET: "up"}
DISTANCE_NOTE = ("Centre = today's price; the tick is fair value. Bars stop at ±40%. "
                 "Double-click a row, or press Enter on it, to open it.")


def _open_in_browser(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def _chart_panel(title: str, chart: QWidget, footer: QWidget) -> QWidget:
    """A panel whose chart takes the spare height, with a footer line under it."""
    frame = panel(heading(title), chart, footer, stretch_last=True)
    frame.layout().setStretchFactor(footer, 0)
    frame.layout().setStretchFactor(chart, 1)
    return frame


class TodayPage(QWidget):
    openValuation = Signal(str)
    openCallBook = Signal()
    openWatchlist = Signal()
    openValueMap = Signal()

    def __init__(self, loader: Loader, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._loader = loader
        self._active = False
        self._symbols: list[str] = []
        self._news: dict[str, list[NewsItem]] = {}
        self._fetched_at: float | None = None
        self.open_url: Callable[[str], None] = _open_in_browser
        self._timer = QTimer(self)
        self._timer.setInterval(int(MARKET_REFRESH_S * 1000))
        self._timer.timeout.connect(self._fetch_market)

        self.title = label("Loading your watchlist…", "DeskTitle", wrap=True)
        self.note = label("What needs a look across your watchlist, most urgent first.", "Secondary", wrap=True)
        self.queue = table(HEADERS, "What needs a look", stretch=2)
        self.queue.setSelectionMode(self.queue.SelectionMode.NoSelection)
        self.queue.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the row buttons take the focus
        self.queue.verticalHeader().setDefaultSectionSize(40)
        header = self.queue.horizontalHeader()
        header.setSectionResizeMode(len(HEADERS) - 1, QHeaderView.ResizeMode.Fixed)
        self.queue.setColumnWidth(len(HEADERS) - 1, ACTION_WIDTH)
        self.add_button = QPushButton("Open watchlist")
        self.add_button.clicked.connect(self.openWatchlist)
        self.add_button.hide()

        self.benchmarks: dict[str, tuple] = {}
        bench = QWidget()
        grid = QGridLayout(bench)
        grid.setContentsMargins(0, 0, 0, 0)
        for row, symbol in enumerate(BENCHMARK.values()):
            last, change = label(DASH, "Value"), label(DASH, "Value")
            last.setAlignment(Qt.AlignmentFlag.AlignRight)
            change.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(label(BENCHMARK_NAMES[symbol]), row, 0)
            grid.addWidget(last, row, 1)
            grid.addWidget(change, row, 2)
            self.benchmarks[symbol] = (last, change)
        grid.setColumnStretch(0, 1)

        self.headlines = QListWidget()
        self.headlines.setWordWrap(True)
        self.headlines.setAccessibleName("Headlines on your symbols")
        self.headlines.itemActivated.connect(self._open_item)
        self.headlines.itemClicked.connect(self._open_item)

        self.distance = DistanceBars()
        self.distance.rowActivated.connect(self.openValuation)
        self.map = MapWidget(compact=True)
        self.map.pointActivated.connect(self.openValuation)
        self.map.pointClicked.connect(self.map.set_selected)
        self.map_button = QPushButton("Open full value map")
        self.map_button.clicked.connect(self.openValueMap)
        map_foot = QHBoxLayout()
        map_foot.addStretch(1)
        map_foot.addWidget(self.map_button)
        map_footer = QWidget()
        map_footer.setLayout(map_foot)
        map_foot.setContentsMargins(0, 0, 0, 0)

        side = QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(panel(heading("BENCHMARKS"), bench))
        side.addWidget(panel(heading("NEWS ON YOUR SYMBOLS"), self.headlines, stretch_last=True), 1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        queue = panel(self.title, self.note, self.add_button, self.queue, stretch_last=True)
        lower = QHBoxLayout()
        lower.setSpacing(10)
        lower.addWidget(_chart_panel("DISTANCE TO FAIR VALUE", self.distance,
                                     label(DISTANCE_NOTE, "Secondary", wrap=True)), 1)
        lower.addWidget(_chart_panel("VALUE MAP", self.map, map_footer), 1)
        main = QVBoxLayout()
        main.setSpacing(10)
        main.addWidget(queue, 2)
        main.addLayout(lower, 3)
        layout.addLayout(main, 5)
        layout.addLayout(side, 2)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self._timer.start()
            self._refresh_market()
        else:
            self._timer.stop()

    def show_snapshot(self, snapshot: DeskSnapshot) -> None:
        self._symbols = [e.symbol for e in snapshot.entries]
        items = brief_items(snapshot)
        empty = not snapshot.entries
        self.add_button.setVisible(empty)
        waiting = loading_text(snapshot)
        self.title.setText(NO_SYMBOLS if empty else waiting if waiting and not items else brief_title(items))
        self.note.setText(waiting or "What needs a look across your watchlist, most urgent first.")
        self._show_items(items)
        points, _left_out = value_points(snapshot)
        self.distance.set_rows(distance_rows(fair_value_gaps(snapshot)))
        self.map.set_points(points)
        self._fetch_missing_news()

    # --- the queue --------------------------------------------------------------------------

    def _show_items(self, items: list[BriefItem]) -> None:
        rows = [[
            TableCell(i.symbol, mono=True),
            TableCell(i.what, tone=_TONE.get(i.urgency)),
            TableCell(i.why, tone="muted"),
            TableCell(i.last, right=True, mono=True),
            TableCell(""),
        ] for i in items]
        fill_table(self.queue, rows, [i.symbol for i in items])
        for r, item in enumerate(items):
            button = QPushButton(ACTION_TEXT[item.action])
            button.setAccessibleName(f"{ACTION_TEXT[item.action]} for {item.symbol}")
            button.clicked.connect(partial(self._run, item))
            self.queue.setCellWidget(r, len(HEADERS) - 1, button)

    def _run(self, item: BriefItem, _checked: bool = False) -> None:
        if item.action is BriefAction.VALUATION:
            self.openValuation.emit(item.symbol)
        elif item.action is BriefAction.CALL_BOOK:
            self.openCallBook.emit()
        else:
            self.openWatchlist.emit()

    # --- benchmarks and headlines -----------------------------------------------------------

    def _refresh_market(self) -> None:
        """On showing the page: fetch unless the last fetch is still recent."""
        if self._fetched_at is None or time.monotonic() - self._fetched_at >= MARKET_REFRESH_S:
            self._fetch_market()

    def _fetch_market(self) -> None:
        self._fetched_at = time.monotonic()
        self._news.clear()
        for symbol in self.benchmarks:
            self._loader.submit(
                f"desk:quote:{symbol}", lambda md, s=symbol: md.quote(s),
                partial(self._on_quote, symbol), partial(self._on_quote_failed, symbol), scoped=False,
            )
        self._fetch_missing_news()

    def _fetch_missing_news(self) -> None:
        if not self._active:
            return
        for symbol in self._symbols[:MAX_NEWS_SYMBOLS]:
            if symbol not in self._news:
                self._news[symbol] = []  # asked for; a failure simply shows no headlines
                self._loader.submit(
                    f"desk:news:{symbol}", lambda md, s=symbol: md.news(s, NEWS_PER_SYMBOL),
                    partial(self._on_news, symbol), self._on_news_failed, scoped=False,
                )

    def _on_quote(self, symbol: str, quote: Quote) -> None:
        last, change = self.benchmarks[symbol]
        last.setText(fmt_num(quote.last))
        change.setText(fmt_pct(quote.change_pct, signed=True))
        pct = quote.change_pct
        set_dir(change, "flat" if not pct else "up" if pct > 0 else "down")

    def _on_quote_failed(self, symbol: str, _err: DataError) -> None:
        last, change = self.benchmarks[symbol]
        last.setText(DASH)
        change.setText(DASH)

    def _on_news(self, symbol: str, items: list[NewsItem]) -> None:
        self._news[symbol] = items
        self._show_headlines()

    def _on_news_failed(self, _err: DataError) -> None:
        self._show_headlines()

    def _show_headlines(self) -> None:
        stories: dict[str, tuple[NewsItem, list[str]]] = {}  # one story can name several symbols
        for symbol, items in self._news.items():
            if symbol in self._symbols:
                for news in items:
                    stories.setdefault(news.title, (news, []))[1].append(symbol)
        newest = sorted(stories.values(), key=lambda pair: pair[0].published, reverse=True)
        self.headlines.clear()
        for news, symbols in newest[:MAX_HEADLINES]:
            item = QListWidgetItem(f"{', '.join(symbols)}  {news.title}")
            item.setData(URL_ROLE, news.url)
            item.setToolTip(f"{news.published:%Y-%m-%d %H:%M}")
            self.headlines.addItem(item)

    def _open_item(self, item: QListWidgetItem) -> None:
        url = str(item.data(URL_ROLE) or "")
        if url.startswith(("https://", "http://")):
            self.open_url(url)
