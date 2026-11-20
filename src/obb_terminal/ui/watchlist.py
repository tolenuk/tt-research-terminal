"""Horizontal watchlist strip with periodic price refresh and a one-month sparkline per chip.

When the chips overflow the strip it rolls like a ticker tape: a copy of the whole set trails
the real one and the view scrolls left, jumping back by one set width so the loop is seamless.
Hovering pauses it so a chip can be clicked."""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from obb_terminal.data.market import DataError
from obb_terminal.domain.models import PriceSeries, Quote, Range
from obb_terminal.ui.fmt import ARROWS, direction, fmt_pct
from obb_terminal.ui.theme import TOKENS
from obb_terminal.ui.worker import Loader

log = logging.getLogger(__name__)

_COLOURS = {"up": "up", "down": "down", "flat": "amber"}
SPARK_ROLE = Qt.ItemDataRole.UserRole + 1
COPY_ROLE = Qt.ItemDataRole.UserRole + 2  # trailing copy that makes the roll seamless
SPARK_RANGE = Range.M1
SPARK_SIZE = QSize(56, 16)
SPARK_GAP = 10  # between the chip text and its sparkline
ROLL_TICK_MS = 30  # ~33 px/s with ROLL_STEP_PX = 1
FADE_PX = 36  # width of the fade at each end while rolling
ROLL_STEP_PX = 1


def sparkline_points(closes: tuple[float, ...], box: QRectF) -> list[QPointF]:
    """Closes spread evenly across `box`, highest at the top; fewer than two give no line."""
    if len(closes) < 2:
        return []
    low, high = min(closes), max(closes)
    span = (high - low) or 1.0
    step = box.width() / (len(closes) - 1)
    return [
        QPointF(box.left() + i * step, box.bottom() - (c - low) / span * box.height())
        for i, c in enumerate(closes)
    ]


class SparklineDelegate(QStyledItemDelegate):
    """Paints the chip as usual, then the sparkline in room reserved at its right end."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        if index.data(SPARK_ROLE):
            size.setWidth(size.width() + SPARK_SIZE.width() + SPARK_GAP)
        return size

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().paint(painter, option, index)
        closes = index.data(SPARK_ROLE)
        if not closes:
            return
        rect = option.rect
        box = QRectF(
            rect.right() - 12 - SPARK_SIZE.width(),
            rect.center().y() - SPARK_SIZE.height() / 2,
            SPARK_SIZE.width(),
            SPARK_SIZE.height(),
        )
        points = sparkline_points(closes, box)
        colour = index.data(Qt.ItemDataRole.ForegroundRole)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(colour.color() if colour is not None else QColor(TOKENS["amber"]), 1.5))
        painter.drawPolyline(points)
        painter.restore()


class EdgeFade(QWidget):
    """A strip of background fading to clear, laid over one end of the rolling chips so they slide
    in and out of view instead of being cut mid-word. It never takes the mouse."""

    def __init__(self, parent: QWidget, *, left: bool) -> None:
        super().__init__(parent)
        self._left = left
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        solid, clear = QColor(TOKENS["bg"]), QColor(TOKENS["bg"])
        clear.setAlpha(0)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0, solid if self._left else clear)
        gradient.setColorAt(1, clear if self._left else solid)
        painter = QPainter(self)
        painter.fillRect(self.rect(), gradient)
        painter.end()


class WatchlistStrip(QWidget):
    symbolActivated = Signal(str)
    changed = Signal(list)
    quoteUpdated = Signal(object)  # Quote
    trendUpdated = Signal(str, object)  # symbol, one month of closes

    def __init__(
        self,
        loader: Loader,
        symbols: list[str],
        parent: QWidget | None = None,
        *,
        refresh_ms: int = 60_000,
    ) -> None:
        super().__init__(parent)
        self._loader = loader
        self._quotes: dict[str, Quote] = {}
        self._sparks: dict[str, tuple[float, ...]] = {}
        self._current: str | None = None
        self._in_flight = 0

        self.list = QListWidget()
        self.list.setObjectName("WatchStrip")
        self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(False)
        self.list.setFixedHeight(44)
        self.list.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.viewport().installEventFilter(self)
        self.list.setItemDelegate(SparklineDelegate(self.list))
        self.list.setAccessibleName("Watchlist")
        self.list.itemClicked.connect(
            lambda item: self.symbolActivated.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        delete = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.list)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self._remove_selected)
        self.fades = (EdgeFade(self.list, left=True), EdgeFade(self.list, left=False))
        self.add_button = QPushButton("+ Add")
        self.add_button.setToolTip("Add current symbol to watchlist")
        self.add_button.setAccessibleName("Add current symbol to watchlist")
        self.add_button.clicked.connect(self.add_current)
        self.remove_button = QPushButton("− Remove")
        self.remove_button.setToolTip("Remove selected symbol (Delete)")
        self.remove_button.setAccessibleName("Remove selected symbol from watchlist")
        self.remove_button.clicked.connect(self._remove_selected)

        title = QLabel("WATCH")
        title.setObjectName("Secondary")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.add_button)
        layout.addWidget(self.remove_button)

        for symbol in symbols:
            self._add_item(symbol)
        self._update_buttons()

        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._roll_timer = QTimer(self)
        self._roll_timer.setInterval(ROLL_TICK_MS)
        self._roll_timer.timeout.connect(self._roll)
        self._roll_timer.start()
        loader.engineReady.connect(self.refresh)
        if loader.ready:
            QTimer.singleShot(0, self.refresh)

    def symbols(self) -> list[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self._real_items()]

    def set_symbols(self, symbols: list[str]) -> None:
        """Show exactly `symbols` (the window's list); never emits `changed`."""
        if symbols == self.symbols():
            return
        known = set(self.symbols())
        self.list.clear()
        for symbol in symbols:
            self._add_item(symbol)
            self._render(symbol)
        self._quotes = {s: q for s, q in self._quotes.items() if s in symbols}
        self._sparks = {s: c for s, c in self._sparks.items() if s in symbols}
        self._update_buttons()
        self._sync_loop()
        for symbol in symbols:
            if symbol not in known:
                self._fetch(symbol)

    def set_current(self, symbol: str | None) -> None:
        self._current = symbol
        self._update_buttons()

    def add_current(self) -> None:
        if not self._current or self._current in self.symbols():
            return
        self._add_item(self._current)
        self._update_buttons()
        self._sync_loop()
        self.changed.emit(self.symbols())
        self._fetch(self._current)

    def remove(self, symbol: str) -> None:
        item = self._item(symbol)
        if item is None:
            return
        self.list.takeItem(self.list.row(item))
        self._quotes.pop(symbol, None)
        self._sparks.pop(symbol, None)
        self._update_buttons()
        self._sync_loop()
        self.changed.emit(self.symbols())

    def sparkline(self, symbol: str) -> tuple[float, ...] | None:
        return self._sparks.get(symbol)

    def refresh(self) -> None:
        if self._in_flight:
            return
        for symbol in self.symbols():
            self._fetch(symbol)

    def is_rolling(self) -> bool:
        return self.list.count() > len(self._real_items())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.list.viewport() and event.type() == QEvent.Type.Resize:
            self._place_fades()
            QTimer.singleShot(0, self._sync_loop)
        return super().eventFilter(watched, event)

    # --- rolling --------------------------------------------------------------------------

    def _place_fades(self) -> None:
        width, height = min(FADE_PX, self.list.width() // 4), self.list.height()
        left, right = self.fades
        left.setGeometry(0, 0, width, height)
        right.setGeometry(self.list.width() - width, 0, width, height)
        rolling = self.is_rolling()
        for fade in self.fades:
            fade.setVisible(rolling)
            fade.raise_()

    def _real_items(self) -> list[QListWidgetItem]:
        items = (self.list.item(i) for i in range(self.list.count()))
        return [item for item in items if not item.data(COPY_ROLE)]

    def _loop_span(self) -> int:
        """Pixels from the first real chip to its copy: one full set, gap included."""
        if not self.is_rolling():
            return 0
        reals = len(self._real_items())
        first = self.list.visualItemRect(self.list.item(0))
        copy = self.list.visualItemRect(self.list.item(reals))
        return copy.left() - first.left()

    def _sync_loop(self) -> None:
        """Add the trailing copy when the real chips overflow, drop it when they fit."""
        reals = self._real_items()
        if self.is_rolling():
            self.list.doItemsLayout()
            overflow = self._loop_span() > self.list.viewport().width()
        else:
            self.list.doItemsLayout()
            overflow = self.list.horizontalScrollBar().maximum() > 0
        copies = [self.list.item(i) for i in range(len(reals), self.list.count())]
        if overflow and len(copies) == len(reals):
            for real, copy in zip(reals, copies, strict=True):
                self._copy_into(real, copy)
            return
        while self.list.count() > len(reals):
            self.list.takeItem(self.list.count() - 1)
        if overflow:
            for real in reals:
                copy = QListWidgetItem()
                copy.setData(COPY_ROLE, True)
                self._copy_into(real, copy)
                self.list.addItem(copy)
        else:
            self.list.horizontalScrollBar().setValue(0)
        self._place_fades()

    @staticmethod
    def _copy_into(real: QListWidgetItem, copy: QListWidgetItem) -> None:
        copy.setText(real.text())
        copy.setForeground(real.foreground())
        copy.setData(Qt.ItemDataRole.UserRole, real.data(Qt.ItemDataRole.UserRole))
        copy.setData(SPARK_ROLE, real.data(SPARK_ROLE))

    def _roll(self) -> None:
        if not self.is_rolling() or self.list.underMouse():
            return
        span = self._loop_span()
        if span <= 0:
            return
        bar = self.list.horizontalScrollBar()
        bar.setValue((bar.value() + ROLL_STEP_PX) % span)

    # --- internals ------------------------------------------------------------------------

    def _remove_selected(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.remove(item.data(Qt.ItemDataRole.UserRole))

    def _fetch(self, symbol: str) -> None:
        self._fetch_spark(symbol)
        key = f"watch:{symbol}"
        if self._loader.is_pending(key):
            return
        self._in_flight += 1
        self._loader.submit(
            key,
            lambda md: md.quote(symbol),
            self._on_quote,
            lambda err, s=symbol: self._on_error(s, err),
            scoped=False,
        )

    def _fetch_spark(self, symbol: str) -> None:
        """One month of closes, fetched once per symbol: the shape barely moves intraday."""
        key = f"spark:{symbol}"
        if symbol in self._sparks or self._loader.is_pending(key):
            return
        self._loader.submit(
            key,
            lambda md: md.history(symbol, SPARK_RANGE),
            self._on_spark,
            lambda err, s=symbol: log.info("Sparkline failed for %s: %s", s, err.user_message),
            scoped=False,
        )

    def _on_spark(self, series: PriceSeries) -> None:
        if self._item(series.symbol) is None:  # removed while loading
            return
        _xs, closes = series.arrays()
        self._sparks[series.symbol] = tuple(closes)
        self._render(series.symbol)
        self.trendUpdated.emit(series.symbol, self._sparks[series.symbol])

    def _on_quote(self, quote: Quote) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        self._quotes[quote.symbol] = quote
        self._render(quote.symbol)
        self.quoteUpdated.emit(quote)

    def _on_error(self, symbol: str, err: DataError) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        log.info("Watchlist quote failed for %s: %s", symbol, err.user_message)

    def _item(self, symbol: str) -> QListWidgetItem | None:
        for item in self._real_items():
            if item.data(Qt.ItemDataRole.UserRole) == symbol:
                return item
        return None

    def _add_item(self, symbol: str) -> None:
        item = QListWidgetItem(symbol)
        item.setData(Qt.ItemDataRole.UserRole, symbol)
        self.list.insertItem(len(self._real_items()), item)  # ahead of any rolling copies

    def _render(self, symbol: str) -> None:
        item = self._item(symbol)
        if item is None:
            return
        item.setData(SPARK_ROLE, self._sparks.get(symbol))
        quote = self._quotes.get(symbol)
        if quote is None:
            self._sync_loop()
            return
        d = direction(quote.change)
        item.setText(
            f"{symbol}  {quote.last:,.2f}  {ARROWS[d]} {fmt_pct(quote.change_pct, signed=True)}"
        )
        item.setForeground(QColor(TOKENS[_COLOURS[d]]))
        self._sync_loop()

    def _update_buttons(self) -> None:
        self.add_button.setEnabled(bool(self._current) and self._current not in self.symbols())
        self.remove_button.setEnabled(bool(self._real_items()))
