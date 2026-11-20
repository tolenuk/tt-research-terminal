"""A table header with a band of group labels above the column titles."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QHeaderView, QWidget

from obb_terminal.ui.theme import TOKENS

LABEL_PAD = 8  # lines up with the cells' text
GROUP_GAP = 4  # between the rules of neighbouring groups
TITLE_POINT_SIZE = 9
ARROW_GAP = 4  # between a column's title and its sort arrow
ARROW_ROOM = 12  # reserved in every column, so that sorting does not change the widths


TEXT_ALIGNMENT = Qt.ItemDataRole.TextAlignmentRole


def _arrow_font(base: QFont) -> QFont:
    font = QFont(base)
    font.setPointSize(TITLE_POINT_SIZE - 3)
    return font


class GroupedHeader(QHeaderView):
    def __init__(self, groups: Sequence[tuple[str, Sequence[int]]], parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._groups = tuple(groups)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _title_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSize(TITLE_POINT_SIZE)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def group_band(self) -> int:
        """The height of the strip that holds the group labels."""
        return QFontMetrics(self._title_font()).height() + 6

    def sectionSizeFromContents(self, logical_index: int) -> QSize:  # noqa: N802
        """The title and room for its arrow: the style's own answer is far wider than the text."""
        text = str(self.model().headerData(logical_index, self.orientation()))
        width = QFontMetrics(self._title_font()).horizontalAdvance(text) + LABEL_PAD * 2 + ARROW_ROOM
        return QSize(width, super().sectionSizeFromContents(logical_index).height())

    def sizeHint(self) -> QSize:  # noqa: N802
        size = super().sizeHint()
        return QSize(size.width(), size.height() + self.group_band())

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int) -> None:  # noqa: N802
        title = rect.adjusted(0, self.group_band(), 0, 0)
        super().paintSection(painter, title, logical_index)
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logical_index:
            self._paint_sort_arrow(painter, title, logical_index)

    def _paint_sort_arrow(self, painter: QPainter, title: QRect, logical_index: int) -> None:
        """Qt's arrow lands on right-aligned titles, so it is drawn just left of them instead."""
        font = self._title_font()
        text = str(self.model().headerData(logical_index, self.orientation()))
        align = Qt.AlignmentFlag(self.model().headerData(logical_index, self.orientation(), TEXT_ALIGNMENT))
        width = QFontMetrics(font).horizontalAdvance(text)
        ascending = self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
        if align & Qt.AlignmentFlag.AlignLeft:
            x = title.left() + LABEL_PAD + width + ARROW_GAP
        else:
            x = title.right() - LABEL_PAD - width - ARROW_GAP - ARROW_ROOM + 2
        painter.save()
        painter.setFont(_arrow_font(font))
        painter.setPen(QColor(TOKENS["amber"]))
        painter.drawText(
            QRect(x, title.top(), ARROW_ROOM, title.height()),
            Qt.AlignmentFlag.AlignCenter,
            "▲" if ascending else "▼",
        )
        painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        band = self.group_band()
        painter = QPainter(self.viewport())
        painter.setFont(self._title_font())
        for name, columns in self._groups:
            if not name:
                continue
            left = self.sectionViewportPosition(columns[0])
            width = sum(self.sectionSize(c) for c in columns)
            painter.setPen(QColor(TOKENS["amber_dim"]))
            painter.drawText(
                QRect(left + LABEL_PAD, 0, max(width - LABEL_PAD, 0), band - 1),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                name,
            )
            painter.setPen(QColor(TOKENS["rule"]))
            painter.drawLine(left + GROUP_GAP, band - 1, left + width - GROUP_GAP, band - 1)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.position().y() < self.group_band():  # a label, not a column title: no sorting
            event.accept()
            return
        super().mousePressEvent(event)
