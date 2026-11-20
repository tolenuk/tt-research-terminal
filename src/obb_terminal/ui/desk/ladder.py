"""ENTRY LADDER: today's price in the centre of every row, with your levels at their distance."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.ui.desk.common import NO_SYMBOLS, label, loading_text, panel
from obb_terminal.ui.desk.data import DeskSnapshot
from obb_terminal.ui.desk.views import LadderRow, ladder_rows
from obb_terminal.ui.fmt import fmt_num
from obb_terminal.ui.theme import TOKENS, mono_font

SPAN = 0.40  # a track shows -40% to +40% around the price; further levels sit on its edge
TRACK_HEIGHT = 58
SWATCH = 12
KEY = (("amber", "Price now"), ("text", "Your buy level"), ("sel_bg", "AI entry zone"),
       ("down", "Thesis break"), ("up", "Fair value"))
PLAIN_TEXT = Qt.TextFormat.PlainText


def track_x(fraction: float, width: float) -> float:
    return (max(-SPAN, min(SPAN, fraction)) + SPAN) / (2 * SPAN) * width


def _pct(fraction: float) -> str:
    return f"{fraction * 100:+.0f}%"


def describe(row: LadderRow) -> str:
    parts = [f"{row.symbol}, price {row.price:,.2f} {row.currency}"]
    if row.buy is not None:
        parts.append(f"your buy level {_pct(row.buy)}")
    if row.zone is not None:
        parts.append(f"AI entry zone {_pct(row.zone[0])} to {_pct(row.zone[1])}")
    if row.thesis is not None:
        parts.append(f"thesis break {_pct(row.thesis)}")
    if row.fair is not None:
        parts.append(f"fair value {_pct(row.fair)}")
    return ", ".join(parts)


class LadderTrack(QWidget):
    def __init__(self, row: LadderRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = row
        self.setFixedHeight(TRACK_HEIGHT)
        self.setMinimumWidth(320)
        self.setAccessibleName(describe(row))
        self.setToolTip(describe(row))

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, mid = float(self.width()), TRACK_HEIGHT / 2
        row = self.row
        painter.setPen(QPen(QColor(TOKENS["rule"]), 1))
        painter.drawLine(QPointF(0, mid), QPointF(width, mid))
        if row.zone is not None:
            low, high = track_x(row.zone[0], width), track_x(row.zone[1], width)
            painter.fillRect(QRectF(low, mid - 8, max(2.0, high - low), 16), QColor(TOKENS["sel_bg"]))
            painter.setPen(QPen(QColor(TOKENS["amber_dim"]), 1))
            painter.drawRect(QRectF(low, mid - 8, max(2.0, high - low), 16))
        painter.setFont(mono_font(9))
        if row.thesis is not None:
            self._mark(painter, row.thesis, "down", None, width, mid)
        if row.fair is not None:
            self._mark(painter, row.fair, "up", _pct(row.fair), width, mid, below=True)
        if row.buy is not None:
            self._mark(painter, row.buy, "text", _pct(row.buy), width, mid)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TOKENS["amber"]))
        painter.drawEllipse(QPointF(width / 2, mid), 6, 6)
        painter.end()

    def _mark(self, painter: QPainter, at: float, tone: str, text: str | None, width: float, mid: float,
              *, below: bool = False) -> None:
        x = track_x(at, width)
        colour = QColor(TOKENS[tone])
        painter.setPen(QPen(colour, 2))
        painter.drawLine(QPointF(x, mid - 10), QPointF(x, mid + 10))
        if text:
            box = QRectF(min(max(x - 30, 0), width - 60), mid + 11 if below else mid - 26, 60, 15)
            painter.setPen(QColor(TOKENS["muted"]))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


class EntryLadderPage(QWidget):
    openValuation = Signal(str)
    setTargetRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = label("How far is each stock from your price?", "DeskTitle", wrap=True)
        self.note = label("Today's price sits in the centre. Levels beyond 40% sit on the edge of the line.",
                          "Secondary", wrap=True)
        self.rows: list[LadderRow] = []
        self.buttons: dict[str, QPushButton] = {}
        key = QHBoxLayout()
        key.setSpacing(16)
        for tone, text in KEY:
            swatch = QFrame()
            swatch.setFixedSize(SWATCH, SWATCH)
            swatch.setStyleSheet(f"background-color: {TOKENS[tone]}; border: 1px solid {TOKENS['amber_dim']};")
            key.addWidget(swatch)
            key.addWidget(label(text, "Secondary"))
        key.addStretch(1)
        key_row = QWidget()
        key_row.setLayout(key)
        key.setContentsMargins(0, 0, 0, 0)

        self._holder = QWidget()
        self._grid = QGridLayout(self._holder)
        self._grid.setColumnStretch(2, 1)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(2)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self._holder)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(panel(self.title, self.note, key_row, self.scroll, stretch_last=True))

    def show_snapshot(self, snapshot: DeskSnapshot) -> None:
        rows = ladder_rows(snapshot)
        waiting = loading_text(snapshot)
        if not snapshot.entries:
            self.note.setText(NO_SYMBOLS)
        else:
            self.note.setText(waiting or "Today's price sits in the centre. Levels beyond 40% sit on the edge of the line.")
        if rows != self.rows:
            self.rows = rows
            self._rebuild()

    def _rebuild(self) -> None:
        while self._grid.count():
            widget = self._grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.buttons = {}
        for col, text in ((0, "Symbol"), (1, "Last"), (2, "−40%  ·  price now  ·  +40%"), (3, "To your level")):
            head = label(text, "Secondary")
            if col == 2:
                head.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._grid.addWidget(head, 0, col)
        for r, row in enumerate(self.rows, start=1):
            self._add_row(r, row)
        self._grid.setRowStretch(len(self.rows) + 1, 1)

    def _add_row(self, r: int, row: LadderRow) -> None:
        symbol = label(row.symbol, "Symbol")
        last = label(fmt_num(row.price), "Value")
        last.setToolTip(row.currency)
        distance = label("No target" if row.buy is None else _pct(row.buy), "Secondary" if row.buy is None else "Value")
        button = QPushButton("Open" if row.has_target else "Set target")
        button.setAccessibleName(f"{button.text()} for {row.symbol}")
        slot = self.openValuation if row.has_target else self.setTargetRequested
        button.clicked.connect(partial(self._emit, slot, row.symbol))
        self.buttons[row.symbol] = button
        for col, widget in enumerate((symbol, last, LadderTrack(row), distance, button)):
            self._grid.addWidget(widget, r, col)

    @staticmethod
    def _emit(signal, symbol: str, _checked: bool = False) -> None:
        signal.emit(symbol)
