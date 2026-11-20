"""TODAY's distance-to-fair-value bars: one row per stock, today's price in the centre and a tick
where blended fair value sits, nearest first, so the names closest to fair value read first."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from obb_terminal.ui.theme import TOKENS, mono_font

SPAN = 0.40  # bars run from -40% to +40%; further away sits on the edge
ROW_H = 30
SYMBOL_W = 96
PCT_W = 64
MAX_ROWS = 8
EMPTY_TEXT = ("No symbol with a fair value yet. An ETF gets one after its Valuation page has valued "
              "its holdings.")


@dataclass(frozen=True, slots=True)
class DistanceRow:
    symbol: str
    upside: float  # fair value / price - 1


def distance_rows(gaps: Sequence[tuple[str, float]], limit: int = MAX_ROWS) -> list[DistanceRow]:
    """Nearest to fair value first; ties keep symbol order so the list never jumps about."""
    ranked = sorted(gaps, key=lambda g: (abs(g[1]), g[0]))
    return [DistanceRow(symbol, upside) for symbol, upside in ranked[:limit]]


def bar_x(upside: float, left: float, width: float) -> float:
    """Where fair value sits along a bar of this width; the centre is today's price."""
    clamped = max(-SPAN, min(SPAN, upside))
    return left + width * (clamped + SPAN) / (2 * SPAN)


class DistanceBars(QWidget):
    rowActivated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[DistanceRow] = []
        self.current = 0  # the keyboard's row
        self.setMinimumSize(260, ROW_H * 3)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Distance to fair value")

    def set_rows(self, rows: list[DistanceRow]) -> None:
        self.rows = rows
        self.current = min(self.current, max(0, len(rows) - 1))
        # Tall enough for every row, so none is cut off without a sign that it exists.
        self.setMinimumHeight(ROW_H * max(3, len(rows)))
        self.setAccessibleDescription("; ".join(f"{r.symbol}: fair value {r.upside * 100:+.0f}% from price" for r in rows)
                                      or "No stocks with a fair value")
        self.update()

    def row_at(self, pos: QPointF) -> str | None:
        index = int(pos.y() // ROW_H)
        return self.rows[index].symbol if 0 <= index < len(self.rows) else None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        symbol = self.row_at(event.position())
        if symbol:
            self.rowActivated.emit(symbol)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Up/Down choose a row; Enter or Return opens it."""
        key = event.key()
        if self.rows and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if key == Qt.Key.Key_Up else 1
            self.current = max(0, min(len(self.rows) - 1, self.current + step))
            self.update()
            return
        if self.rows and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.rowActivated.emit(self.rows[self.current].symbol)
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.rows:
            painter.setPen(QColor(TOKENS["muted"]))
            painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, EMPTY_TEXT)
            painter.end()
            return
        painter.setFont(mono_font())
        left = SYMBOL_W
        width = max(1.0, self.width() - SYMBOL_W - PCT_W - 8)
        for i, row in enumerate(self.rows):
            self._draw_row(painter, row, i * ROW_H, left, width)
        if self.hasFocus() and self.rows:
            painter.setPen(QPen(QColor(TOKENS["focus"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(0.5, self.current * ROW_H + 0.5, self.width() - 1, ROW_H - 1))
        painter.end()

    def _draw_row(self, painter: QPainter, row: DistanceRow, top: float, left: float, width: float) -> None:
        mid = top + ROW_H / 2
        text_flags = Qt.AlignmentFlag.AlignVCenter
        painter.setPen(QColor(TOKENS["text"]))
        symbol = painter.fontMetrics().elidedText(row.symbol, Qt.TextElideMode.ElideRight, SYMBOL_W - 8)
        painter.drawText(QRectF(0, top, SYMBOL_W - 8, ROW_H), text_flags | Qt.AlignmentFlag.AlignLeft, symbol)
        painter.setPen(QPen(QColor(TOKENS["rule"]), 2))
        painter.drawLine(QPointF(left, mid), QPointF(left + width, mid))
        centre = left + width / 2
        painter.setPen(QPen(QColor(TOKENS["amber"]), 2))
        painter.drawLine(QPointF(centre, mid - 7), QPointF(centre, mid + 7))
        tone = QColor(TOKENS["up" if row.upside >= 0 else "down"])
        x = bar_x(row.upside, left, width)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tone)
        painter.drawRoundedRect(QRectF(x - 2, mid - 6, 4, 12), 1, 1)
        painter.setPen(tone)
        painter.drawText(QRectF(left + width + 8, top, PCT_W, ROW_H), text_flags | Qt.AlignmentFlag.AlignRight,
                         f"{row.upside * 100:+.0f}%")
        painter.setPen(QPen(QColor(TOKENS["border"]), 1))
        painter.drawLine(QPointF(0, top + ROW_H - 0.5), QPointF(self.width(), top + ROW_H - 0.5))
