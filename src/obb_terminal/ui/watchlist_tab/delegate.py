"""Draws the Watchlist's cells: a main line and a smaller second line, a readiness bar, the
one-month price line, a marker at the edge of targeted cells and a hairline under each row."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from obb_terminal.ui.theme import TOKENS, app_font, mono_font
from obb_terminal.ui.watchlist import sparkline_points
from obb_terminal.ui.watchlist_tab.model import (
    Col,
    EdgeRole,
    ReadinessRole,
    SubtextColourRole,
    SubtextRole,
    TrendRole,
)

PAD_X = 8
PAD_Y = 6
LINE_GAP = 3
EDGE_WIDTH = 2
BAR_HEIGHT = 4
BAR_MAX_WIDTH = 72
TREND_SIZE = QSize(56, 22)
MAX_COLUMN_WIDTH = 150  # company names and error messages are shortened here; the tooltip has them whole


def _font(base: QFont, point_size: int, *, bold: bool = False) -> QFont:
    font = QFont(base)
    font.setPointSize(point_size)
    font.setBold(bold)
    return font


class WatchlistDelegate(QStyledItemDelegate):
    def __init__(self, view: QTableView) -> None:
        super().__init__(view)
        self._view = view
        self._main = mono_font(9)
        self._symbol = _font(mono_font(), 10, bold=True)
        self._sub = mono_font(8)
        self._name = _font(app_font(), 8)

    # --- what goes where ------------------------------------------------------------------

    def _lines(self, index: QModelIndex) -> tuple[str, str | None]:
        main, _, second = str(index.data(Qt.ItemDataRole.DisplayRole) or "").partition("\n")
        # The second line is small, so one space between its parts reads as clearly as two.
        return main, " ".join(second.split()) or index.data(SubtextRole)

    def _fonts(self, col: int) -> tuple[QFont, QFont]:
        return (self._symbol, self._name) if col == Col.SYMBOL else (self._main, self._sub)

    def sizeHint(self, _option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        col = index.column()
        main_font, sub_font = self._fonts(col)
        main_m, sub_m = QFontMetrics(main_font), QFontMetrics(sub_font)
        height = PAD_Y * 2 + main_m.height() + LINE_GAP + sub_m.height()
        if col == Col.TREND:
            return QSize(TREND_SIZE.width() + PAD_X * 2, height)
        main, second = self._lines(index)
        width = max(main_m.horizontalAdvance(main), sub_m.horizontalAdvance(second or ""))
        width += PAD_X * 2 + EDGE_WIDTH
        return QSize(min(width, MAX_COLUMN_WIDTH), height)

    # --- painting -------------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        self._paint_background(painter, option, index)
        closes = index.data(TrendRole)
        if closes:
            self._paint_trend(painter, option.rect, closes, index)
        else:
            self._paint_text(painter, option.rect, index)
        painter.restore()

    def _paint_background(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        rect = option.rect
        if index.row() == getattr(self._view, "hovered_row", lambda: -1)():
            painter.fillRect(rect, QColor(TOKENS["raised"]))
        fill = index.data(Qt.ItemDataRole.BackgroundRole)
        if fill is not None:
            painter.fillRect(rect, fill)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(TOKENS["sel_bg"]))
        edge = index.data(EdgeRole)
        if edge is not None:
            painter.fillRect(QRect(rect.left(), rect.top(), EDGE_WIDTH, rect.height()), edge)
        painter.setPen(QColor(TOKENS["rule"]))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if option.state & QStyle.StateFlag.State_HasFocus:  # where the keyboard is
            painter.setPen(QColor(TOKENS["focus"]))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _paint_text(self, painter: QPainter, rect: QRect, index: QModelIndex) -> None:
        col = index.column()
        main_font, sub_font = self._fonts(col)
        main_m, sub_m = QFontMetrics(main_font), QFontMetrics(sub_font)
        main, second = self._lines(index)
        align = Qt.AlignmentFlag(index.data(Qt.ItemDataRole.TextAlignmentRole))
        inner = rect.adjusted(PAD_X, PAD_Y, -PAD_X, -PAD_Y)
        main_rect = QRect(inner.left(), inner.top(), inner.width(), main_m.height())
        sub_rect = QRect(inner.left(), main_rect.bottom() + 1 + LINE_GAP, inner.width(), sub_m.height())

        painter.setFont(main_font)
        painter.setPen(index.data(Qt.ItemDataRole.ForegroundRole))
        painter.drawText(main_rect, align, main_m.elidedText(main, Qt.TextElideMode.ElideRight, inner.width()))
        fraction = index.data(ReadinessRole)
        if fraction is not None:
            self._paint_bar(painter, sub_rect, fraction, index.data(Qt.ItemDataRole.ForegroundRole))
        elif second:
            painter.setFont(sub_font)
            colour = index.data(SubtextColourRole)
            painter.setPen(colour if colour is not None else QColor(TOKENS["muted"]))
            painter.drawText(sub_rect, align, sub_m.elidedText(second, Qt.TextElideMode.ElideRight, inner.width()))

    @staticmethod
    def _paint_bar(painter: QPainter, line: QRect, fraction: float, colour: QColor) -> None:
        width = min(BAR_MAX_WIDTH, line.width())
        top = line.center().y() - BAR_HEIGHT // 2
        track = QRect(line.right() - width + 1, top, width, BAR_HEIGHT)
        painter.fillRect(track, QColor(TOKENS["scroll"]))
        painter.fillRect(QRect(track.topLeft(), QSize(round(width * fraction), BAR_HEIGHT)), colour)

    @staticmethod
    def _paint_trend(painter: QPainter, rect: QRect, closes: tuple[float, ...], index: QModelIndex) -> None:
        corner = QPoint(rect.right() - PAD_X - TREND_SIZE.width() + 1, rect.center().y() - TREND_SIZE.height() // 2)
        points = sparkline_points(closes, QRectF(QRect(corner, TREND_SIZE)))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(index.data(Qt.ItemDataRole.ForegroundRole), 1.25))
        painter.drawPolyline(points)
