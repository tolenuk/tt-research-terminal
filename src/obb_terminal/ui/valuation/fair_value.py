"""Fair value: the methods table, the blend, the margin label and a price-vs-value gauge."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.domain.etf import ROLLUP_PENDING, coverage
from obb_terminal.domain.models import (
    METHOD_LABELS,
    AnalystConsensus,
    HoldingValue,
    Valuation,
    ValuationLabel,
)
from obb_terminal.ui.fmt import DASH, fmt_num, fmt_pct, fmt_price
from obb_terminal.ui.theme import TOKENS, fit_rows, set_dir, style_table

ROLLUP_HEADERS = ("Holding", "Weight", "Its margin")
ROLLUP_CAVEAT = "Assumes the rest of the fund is valued like its top holdings."
GAUGE_HEIGHT = 58  # room for a caption above and below the bar
_LABEL_DIR = {ValuationLabel.UNDERVALUED: "up", ValuationLabel.OVERVALUED: "down", ValuationLabel.FAIR: "flat"}


def gauge_scale(
    price: float, fair: float | None, low: float | None, high: float | None
) -> tuple[float, float]:
    """Axis range covering every known value with 10% padding (never below zero)."""
    values = [x for x in (price, fair, low, high) if x is not None and x > 0]
    if not values:
        return 0.0, 1.0
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.1 or hi * 0.1
    return max(0.0, lo - pad), hi + pad


def method_text(value: float | None, reason: str | None, currency: str) -> str:
    return fmt_price(value, currency) if value is not None else f"n/a — {reason or 'no data'}"


def gauge_description(
    price: float | None, fair: float | None, low: float | None, high: float | None, currency: str
) -> str:
    """A full-sentence description of the gauge (with currency and a direction word)."""
    if price is None:
        return "No price available"
    p = fmt_price(price, currency)
    if fair is None:
        text = f"Price {p}; fair value not available"
    else:
        f = fmt_price(fair, currency)
        direction = "below" if price < fair else "above" if price > fair else "at"
        text = f"Price {p} is {direction} fair value {f}"
    if low is not None and high is not None:
        text += f"; analyst range {low:,.2f}–{fmt_price(high, currency)}"
    return text


class ValueGauge(QWidget):
    """A horizontal bar: analyst low-high band, a fair-value tick and a price marker."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(GAUGE_HEIGHT)
        self.setAccessibleName("Price versus fair value gauge")
        self.price: float | None = None
        self.fair: float | None = None
        self.low: float | None = None
        self.high: float | None = None
        self.captions = True  # off while rendering the PDF image, which prints the values as text

    def set_values(
        self,
        price: float | None,
        fair: float | None,
        low: float | None,
        high: float | None,
        currency: str = "",
    ) -> None:
        self.price, self.fair, self.low, self.high = price, fair, low, high
        text = gauge_description(price, fair, low, high, currency)
        self.setToolTip(text)
        self.setAccessibleDescription(text)
        self.update()

    def png_bytes(self) -> bytes | None:
        """The rendered gauge as PNG bytes (for the PDF report); None when empty."""
        if self.price is None:
            return None
        self.captions = False
        try:
            image = self.grab().toImage()
        finally:
            self.captions = True
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = image.save(buffer, "PNG")
        buffer.close()
        return bytes(buffer.data()) if ok else None

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(TOKENS["bg"]))
        if self.price is None:
            painter.end()
            return
        lo, hi = gauge_scale(self.price, self.fair, self.low, self.high)
        margin = 8.0
        width = max(1.0, self.width() - 2 * margin)
        mid = self.height() / 2
        line = painter.fontMetrics().height()

        def x(v: float) -> float:
            return margin + min(1.0, max(0.0, (v - lo) / (hi - lo))) * width

        painter.fillRect(QRectF(margin, mid - 3, width, 6), QColor(TOKENS["border"]))
        if self.low is not None and self.high is not None:
            painter.fillRect(QRectF(x(self.low), mid - 5, x(self.high) - x(self.low), 10), QColor(TOKENS["sel_bg"]))
        if self.fair is not None:
            painter.setPen(QPen(QColor(TOKENS["amber"]), 3))
            painter.drawLine(QPointF(x(self.fair), mid - 8), QPointF(x(self.fair), mid + 8))
        colour = TOKENS["up"] if self.fair is not None and self.fair >= self.price else TOKENS["down"]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour if self.fair is not None else TOKENS["focus"]))
        painter.drawEllipse(QPointF(x(self.price), mid), 6, 6)
        # Name each mark: fair value above the bar, price below (the analyst band is in the tooltip).
        if not self.captions:
            painter.end()
            return
        if self.fair is not None:
            self._caption(painter, x(self.fair), mid - 10 - line, f"Fair value {fmt_num(self.fair)}", TOKENS["amber"])
        self._caption(painter, x(self.price), mid + 9, f"Price {fmt_num(self.price)}",
                      colour if self.fair is not None else TOKENS["focus"])
        painter.end()

    def _caption(self, painter: QPainter, centre: float, top: float, text: str, colour: str) -> None:
        """Text centred on a mark, slid inward so it never runs off either end."""
        w = painter.fontMetrics().horizontalAdvance(text) + 4
        left = min(max(0.0, centre - w / 2), max(0.0, self.width() - w))
        painter.setPen(QColor(colour))
        painter.drawText(QRectF(left, top, w, painter.fontMetrics().height()), Qt.AlignmentFlag.AlignCenter, text)


class FairValueView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.heading = QLabel("FAIR VALUE")
        self.heading.setObjectName("SectionHeading")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.na_label = QLabel("")
        self.na_label.setObjectName("Muted")
        self.na_label.setTextFormat(Qt.TextFormat.PlainText)
        self.na_label.setWordWrap(True)
        self.table = QTableWidget(0, 3)
        style_table(self.table)
        self.table.setHorizontalHeaderLabels(["Method", "Value", "Weight"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().hide()
        self.table.setAccessibleName("Fair value methods")
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.blended_label = QLabel(DASH)
        self.blended_label.setObjectName("Blended")
        self.blended_label.setTextFormat(Qt.TextFormat.PlainText)
        self.margin_label = QLabel("")
        self.margin_label.setTextFormat(Qt.TextFormat.PlainText)
        self.gauge = ValueGauge()
        # Two halves side by side (holdings 1-5 | 6-10), so ten rows take the height of five.
        self.rollup_table = QTableWidget(0, 2 * len(ROLLUP_HEADERS))
        style_table(self.rollup_table)
        self.rollup_table.setHorizontalHeaderLabels(list(ROLLUP_HEADERS) * 2)
        self.rollup_rows: tuple[HoldingValue, ...] = ()
        self.rollup_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rollup_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.rollup_table.verticalHeader().hide()
        self.rollup_table.setAccessibleName("Top-holdings roll-up")
        for col in (2, 5):
            self.rollup_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.rollup_table.hide()
        self.rollup_note = QLabel("")
        self.rollup_note.setObjectName("Muted")
        self.rollup_note.setTextFormat(Qt.TextFormat.PlainText)
        self.rollup_note.setWordWrap(True)
        self.rollup_note.hide()
        self._progress: tuple[int, int] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.heading)
        layout.addWidget(self.na_label)
        layout.addWidget(self.table)  # sized to its rows by fit_rows; spare height goes below
        layout.addWidget(self.blended_label)
        layout.addWidget(self.margin_label)
        layout.addWidget(self.gauge)
        layout.addWidget(self.rollup_note)
        layout.addWidget(self.rollup_table)
        layout.addStretch(1)

    def show_valuation(self, v: Valuation, analyst: AnalystConsensus | None = None) -> None:
        ccy = v.currency.value
        self.table.setRowCount(len(v.methods))
        for row, m in enumerate(v.methods):
            self.table.setItem(row, 0, QTableWidgetItem(METHOD_LABELS[m.method]))
            value_item = QTableWidgetItem(method_text(m.value, m.reason, ccy))
            if m.value is None:
                value_item.setForeground(QColor(TOKENS["amber_dim"]))
            self.table.setItem(row, 1, value_item)
            self.table.setItem(row, 2, QTableWidgetItem(fmt_pct(m.weight) if m.weight > 0 else DASH))
        self.table.resizeColumnToContents(0)
        fit_rows(self.table)
        self.table.setAccessibleDescription(
            "; ".join(
                f"{METHOD_LABELS[m.method]} {method_text(m.value, m.reason, ccy)}"
                + (f", weight {fmt_pct(m.weight)}" if m.weight > 0 else "")
                for m in v.methods
            )
            or "No fair value methods"
        )
        self.table.setVisible(bool(v.methods))
        reason = v.reason
        if reason == ROLLUP_PENDING and self._progress is not None:
            reason = f"Valuing holdings… {self._progress[0]} of {self._progress[1]}"
        self.na_label.setText(reason or "")
        self.na_label.setVisible(reason is not None)
        self.blended_label.setText(
            f"Fair value {fmt_price(v.blended, ccy)}" if v.blended is not None else f"Fair value {DASH}"
        )
        if v.margin is not None and v.label is not None:
            self.margin_label.setText(f"{fmt_pct(v.margin, signed=True)} margin of safety · {v.label.value}")
            set_dir(self.margin_label, _LABEL_DIR[v.label])
        else:
            self.margin_label.setText("")
            set_dir(self.margin_label, "flat")
        low = analyst.target_low if analyst else None
        high = analyst.target_high if analyst else None
        self.gauge.set_values(v.price if v.applicable else None, v.blended, low, high, ccy)
        self.gauge.setVisible(v.applicable)

    def set_rollup_progress(self, progress: tuple[int, int] | None) -> None:
        """(done, total) while holdings are being valued; None otherwise. Used by the next
        show_valuation."""
        self._progress = progress

    def show_rollup(self, rows: tuple[HoldingValue, ...] | None, total: int = 0) -> None:
        """The roll-up table (rows valued so far) and its coverage line; None hides both."""
        if rows is None:
            self.rollup_table.hide()
            self.rollup_note.hide()
            return
        self.rollup_rows = rows
        half = (max(len(rows), total) + 1) // 2
        self.rollup_table.clearContents()
        self.rollup_table.setRowCount(min(len(rows), half))
        for n, hv in enumerate(rows):
            row, base = (n, 0) if n < half else (n - half, len(ROLLUP_HEADERS))
            self.rollup_table.setItem(row, base, QTableWidgetItem(hv.symbol))
            weight = QTableWidgetItem(fmt_pct(hv.weight))
            weight.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.rollup_table.setItem(row, base + 1, weight)
            margin = QTableWidgetItem(
                fmt_pct(hv.margin, signed=True) if hv.margin is not None else f"n/a — {hv.reason or 'no data'}"
            )
            if hv.margin is None or hv.transient:
                margin.setForeground(QColor(TOKENS["amber_dim"]))
            if hv.transient and hv.margin is not None:
                margin.setText(f"{margin.text()} *")  # valued on incomplete data
            margin.setToolTip(margin.text() + (" (incomplete data)" if hv.transient else ""))
            self.rollup_table.setItem(row, base + 2, margin)
        for col in (0, 3):
            self.rollup_table.resizeColumnToContents(col)
        fit_rows(self.rollup_table)
        self.rollup_table.setAccessibleDescription(
            "; ".join(f"{hv.symbol} {fmt_pct(hv.weight)}: "
                      + (fmt_pct(hv.margin, signed=True) if hv.margin is not None else f"n/a, {hv.reason}")
                      for hv in rows) or "No holdings valued yet")
        valued = sum(hv.margin is not None for hv in rows)
        unsettled = sum(hv.transient for hv in rows)
        provisional = (
            f" Provisional: {unsettled} holding{'s' if unsettled != 1 else ''} had incomplete or failed "
            "data, so this is not saved and the next load tries again."
            if unsettled else ""
        )
        self.rollup_note.setText(
            f"Valued {valued} of {total or len(rows)} holdings, {coverage(rows) * 100:.1f}% of the fund. "
            + ROLLUP_CAVEAT + provisional
        )
        self.rollup_table.setVisible(bool(rows))
        self.rollup_note.show()
