"""Close-price line chart with range buttons and a snapping crosshair."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QButtonGroup, QLabel, QPushButton, QWidget

from obb_terminal.data.market import MarketData
from obb_terminal.domain.models import PriceSeries, Range
from obb_terminal.ui.panels.base import Panel
from obb_terminal.ui.theme import TOKENS
from obb_terminal.ui.worker import Loader

RANGE_ORDER = (Range.D1, Range.M1, Range.M6, Range.Y1, Range.Y5, Range.ALL)
FILL_ALPHA = 0.10  # amber wash under the price line


def snap_index(xs: np.ndarray, x: float) -> int:
    i = int(np.searchsorted(xs, x))
    if i <= 0:
        return 0
    if i >= len(xs):
        return len(xs) - 1
    return i if xs[i] - x < x - xs[i - 1] else i - 1


def crosshair_text(ts: float, price: float, currency: str) -> str:
    at = datetime.fromtimestamp(ts, UTC)
    stamp = at.strftime("%Y-%m-%d") if (at.hour, at.minute) == (0, 0) else at.strftime("%Y-%m-%d %H:%M")
    return f"{stamp}  {price:,.2f} {currency}"


class ChartPanel(Panel):
    name = "chart"

    def __init__(self, loader: Loader, parent: QWidget | None = None) -> None:
        self._range = Range.Y1
        self.series: PriceSeries | None = None
        self._xs = np.array([], dtype=float)
        self._ys = np.array([], dtype=float)
        self._currency = ""
        super().__init__(loader, "PRICE CHART", parent)

        self.cross_label = QLabel("")
        self.cross_label.setObjectName("Muted")
        self.header.addWidget(self.cross_label)
        self.range_buttons: dict[Range, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for r in RANGE_ORDER:
            button = QPushButton(r.value)
            button.setCheckable(True)
            button.setChecked(r is self._range)
            button.setAccessibleName(f"Chart range {r.value}")
            button.clicked.connect(lambda _checked=False, r=r: self.set_range(r))
            group.addButton(button)
            self.header.addWidget(button)
            self.range_buttons[r] = button

    def build_content(self) -> QWidget:
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        item = self.plot.getPlotItem()
        item.setMenuEnabled(False)
        item.hideButtons()
        item.showGrid(x=False, y=True, alpha=0.08)
        for axis in ("left", "bottom"):
            item.getAxis(axis).setPen(pg.mkPen(TOKENS["rule"]))
            item.getAxis(axis).setTextPen(pg.mkPen(TOKENS["muted"]))
        self.plot.setMouseEnabled(x=False, y=False)
        fill = QColor(TOKENS["amber"])
        fill.setAlphaF(FILL_ALPHA)
        self.curve = self.plot.plot([], [], pen=pg.mkPen(TOKENS["amber"], width=2), brush=fill)
        dash = pg.mkPen(TOKENS["amber_dim"], style=Qt.PenStyle.DashLine)
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=dash)
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=dash)
        for line in (self.vline, self.hline):
            line.hide()
            self.plot.addItem(line, ignoreBounds=True)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse)
        return self.plot

    def set_range(self, r: Range) -> None:
        self._range = r
        self.range_buttons[r].setChecked(True)
        self.reload()

    def make_fetch(self, symbol: str) -> Callable[[MarketData], Any]:
        r = self._range
        return lambda md: md.history(symbol, r)

    def render(self, series: PriceSeries) -> None:
        self.series = series
        xs, ys = series.arrays()
        self._xs = np.asarray(xs, dtype=float)
        self._ys = np.asarray(ys, dtype=float)
        self._currency = series.currency.value
        self.curve.setData(self._xs, self._ys)
        # Shade down to the lowest close, not to zero, so the fill never squashes the y range.
        self.curve.setFillLevel(float(self._ys.min()) if self._ys.size else None)
        self.plot.getPlotItem().enableAutoRange()
        self._hide_crosshair()

    def _hide_crosshair(self) -> None:
        self.vline.hide()
        self.hline.hide()
        self.cross_label.setText("")

    def _on_mouse(self, pos: QPointF) -> None:
        item = self.plot.getPlotItem()
        if self._xs.size == 0 or not item.sceneBoundingRect().contains(pos):
            self._hide_crosshair()
            return
        point = item.vb.mapSceneToView(pos)
        i = snap_index(self._xs, point.x())
        self.vline.setPos(self._xs[i])
        self.hline.setPos(self._ys[i])
        self.vline.show()
        self.hline.show()
        self.cross_label.setText(crosshair_text(self._xs[i], self._ys[i], self._currency))
