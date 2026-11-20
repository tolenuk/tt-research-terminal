"""VALUE MAP: every stock's Outlook score against its upside to blended fair value."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from obb_terminal.ui.desk.common import (
    NO_SYMBOLS,
    TableCell,
    fill_table,
    heading,
    label,
    loading_text,
    panel,
    selected_symbol,
    table,
)
from obb_terminal.ui.desk.data import DeskSnapshot
from obb_terminal.ui.desk.views import ValuePoint, value_points
from obb_terminal.ui.fmt import fmt_num
from obb_terminal.ui.theme import TOKENS, mono_font

SPAN = 0.40  # the map shows upside from -40% to +40%; stocks beyond it sit on the edge
LEFT, RIGHT, TOP, BOTTOM = 44, 16, 12, 44
HIT_RADIUS = 14.0
QUADRANTS = (("Good but expensive", 0, 0), ("Good and cheap", 1, 0),
             ("Avoid for now", 0, 1), ("Cheap for a reason?", 1, 1))
HEADERS = ("Symbol", "Last", "Fair value", "Upside", "Outlook")


def map_position(upside: float, outlook: float, width: float, height: float) -> tuple[float, float]:
    """Where a stock sits in a plot of this size; upside beyond the span is held at the edge."""
    x = (max(-SPAN, min(SPAN, upside)) + SPAN) / (2 * SPAN)
    y = 1 - max(0.0, min(100.0, outlook)) / 100
    return x * width, y * height


class MapWidget(QWidget):
    pointClicked = Signal(str)
    pointActivated = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self.points: list[ValuePoint] = []
        self.selected: str | None = None
        self.setMinimumSize(*((260, 200) if compact else (420, 340)))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Value map")

    def set_points(self, points: list[ValuePoint]) -> None:
        self.points = points
        self.setAccessibleDescription("; ".join(
            f"{p.symbol}: upside {p.upside * 100:+.0f}%, Outlook {p.outlook:.0f}" for p in points
        ))
        self.update()

    def set_selected(self, symbol: str | None) -> None:
        self.selected = symbol
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(LEFT, TOP, max(1, self.width() - LEFT - RIGHT), max(1, self.height() - TOP - BOTTOM))

    def _at(self, p: ValuePoint) -> QPointF:
        plot = self._plot()
        x, y = map_position(p.upside, p.outlook, plot.width(), plot.height())
        return QPointF(plot.left() + x, plot.top() + y)

    def point_at(self, pos: QPointF) -> str | None:
        best, best_d = None, HIT_RADIUS
        for p in self.points:
            at = self._at(p)
            d = ((at.x() - pos.x()) ** 2 + (at.y() - pos.y()) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = p.symbol, d
        return best

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        symbol = self.point_at(event.position())
        if symbol:
            self.pointClicked.emit(symbol)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        symbol = self.point_at(event.position())
        if symbol:
            self.pointActivated.emit(symbol)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        self._draw_grid(painter, plot)
        self._draw_points(painter)
        painter.end()

    def _draw_grid(self, painter: QPainter, plot: QRectF) -> None:
        muted = QColor(TOKENS["muted"])
        for score in (0, 25, 75, 100, 50):
            y = plot.top() + plot.height() * (1 - score / 100)
            painter.setPen(QPen(muted if score == 50 else QColor(TOKENS["rule"]), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(muted)
            painter.drawText(QRectF(0, y - 9, LEFT - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(score))
        middle = plot.center().x()
        painter.setPen(QPen(muted, 1))
        painter.drawLine(QPointF(middle, plot.top()), QPointF(middle, plot.bottom()))
        painter.setPen(muted)
        below = plot.bottom() + 4
        painter.drawText(QRectF(plot.left(), below, 80, 18), Qt.AlignmentFlag.AlignLeft, "−40%")
        painter.drawText(QRectF(middle - 60, below, 120, 18), Qt.AlignmentFlag.AlignHCenter, "At fair value")
        painter.drawText(QRectF(plot.right() - 80, below, 80, 18), Qt.AlignmentFlag.AlignRight, "+40%")
        painter.drawText(QRectF(plot.left(), below + 18, plot.width(), 18), Qt.AlignmentFlag.AlignHCenter,
                         "Upside to fair value")
        for text, col, row in QUADRANTS:
            h = Qt.AlignmentFlag.AlignRight if col else Qt.AlignmentFlag.AlignLeft
            v = Qt.AlignmentFlag.AlignBottom if row else Qt.AlignmentFlag.AlignTop
            painter.drawText(plot.adjusted(8, 6, -8, -6), h | v, text)

    def _draw_points(self, painter: QPainter) -> None:
        painter.setFont(mono_font())
        metrics = painter.fontMetrics()
        placed: list[QRectF] = []
        for p in sorted(self.points, key=lambda q: q.symbol == self.selected):
            at = self._at(p)
            chosen = p.symbol == self.selected
            colour = QColor(TOKENS["amber"] if chosen else TOKENS["text"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(at, 7 if chosen else 5, 7 if chosen else 5)
            rect = self._label_rect(at, metrics.horizontalAdvance(p.symbol) + 2, metrics.height(), placed)
            placed.append(rect)
            painter.setPen(colour)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, p.symbol)

    def _label_rect(self, at: QPointF, w: float, h: float, placed: list[QRectF]) -> QRectF:
        """The first spot around the point that stays on the map and clear of earlier labels."""
        gap = 10.0
        spots = [(gap, -h / 2), (-gap - w, -h / 2), (-w / 2, -gap - h), (-w / 2, gap),
                 (gap, -h - 2), (gap, 2), (-gap - w, -h - 2), (-gap - w, 2)]
        # Points that share a spot (two stocks held on the same edge) stack their labels.
        for k in range(1, 5):
            spots += [(-gap - w, -h / 2 + k * h), (-gap - w, -h / 2 - k * h),
                      (gap, -h / 2 + k * h), (gap, -h / 2 - k * h)]
        bounds = QRectF(self.rect())
        fallback = None
        for dx, dy in spots:
            rect = QRectF(at.x() + dx, at.y() + dy, w, h)
            if not bounds.contains(rect):
                continue
            fallback = fallback or rect
            if not any(rect.intersects(other) for other in placed):
                return rect
        return fallback or QRectF(at.x() + gap, at.y() - h / 2, w, h)


def _note(snapshot: DeskSnapshot) -> str:
    note = ("Outlook score against upside to fair value, with default assumptions. "
            "Upside beyond 40% sits on the edge.")
    when = snapshot.data_from
    return note if when is None else f"{note} Data from {when.astimezone():%H:%M}."


class ValueMapPage(QWidget):
    openValuation = Signal(str)
    refreshRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = label("Which of your names are good and cheap?", "DeskTitle", wrap=True)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setAccessibleName("Fetch fresh prices and fair values")
        self.refresh_button.clicked.connect(self.refreshRequested)
        self.note = label("Loading your watchlist…", "Secondary", wrap=True)
        self.map = MapWidget()
        self.table = table(HEADERS, "Stocks ranked by upside", stretch=0)
        self.left_out = label("", "Secondary", wrap=True)

        self.map.pointClicked.connect(self._select)
        self.map.pointActivated.connect(self.openValuation)
        self.table.itemSelectionChanged.connect(lambda: self.map.set_selected(selected_symbol(self.table)))
        self.table.cellDoubleClicked.connect(self._activate_row)
        self.table.cellActivated.connect(self._activate_row)

        side = QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(panel(heading("RANKED BY UPSIDE"), self.table, stretch_last=True), 3)
        side.addWidget(panel(heading("NOT ON THE MAP"), self.left_out), 1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.addWidget(self.title, 1)
        head_layout.addWidget(self.refresh_button)
        layout.addWidget(panel(head, self.note, self.map, stretch_last=True), 7)
        layout.addLayout(side, 4)

    def show_snapshot(self, snapshot: DeskSnapshot) -> None:
        points, left_out = value_points(snapshot)
        self.map.set_points(points)
        fill_table(self.table, [[
            TableCell(p.symbol, mono=True),
            TableCell(fmt_num(p.price), right=True, mono=True),
            TableCell(fmt_num(p.fair_value), f"Blended fair value in {p.currency}", right=True, mono=True),
            TableCell(f"{p.upside * 100:+.0f}%", tone="up" if p.upside > 0 else "down", right=True, mono=True),
            TableCell(f"{p.outlook:.0f}", right=True, mono=True),
        ] for p in points], [p.symbol for p in points])
        self.left_out.setText("\n".join(f"{s}: {why}" for s, why in left_out) or "Every symbol is on the map.")
        waiting = loading_text(snapshot)
        if not snapshot.entries:
            self.note.setText(NO_SYMBOLS)
        else:
            self.note.setText(waiting or _note(snapshot))

    def _select(self, symbol: str) -> None:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.text() == symbol:
                self.table.selectRow(r)
                return

    def _activate_row(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            self.openValuation.emit(item.text())
