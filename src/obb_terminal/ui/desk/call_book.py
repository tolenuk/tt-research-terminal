"""CALL BOOK: every saved AI call, whether its thesis still holds, and how it did at each horizon."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout, QWidget

from obb_terminal.ui.desk.common import TableCell, fill_table, heading, label, panel, table
from obb_terminal.ui.desk.data import DeskSnapshot
from obb_terminal.ui.desk.views import CallRow, call_progress, call_rows
from obb_terminal.ui.track_record.view import Cell

HEADERS = ("Symbol", "Called", "Source", "Action", "Entry zone", "Break", "Target", "Thesis", "20 days", "60 days", "120 days")
NO_JOURNAL = "The journal is off, so there are no saved calls to show."
JOURNAL_ERROR = "The journal could not be read. See the log."
NO_CALLS = "No AI calls yet. Run the AI analyst on the Valuation tab and its call appears here."
NOTE = "Each call is scored against its local index at 20, 60 and 120 trading days. ✓ beat it by 2% or more."
GATE_NOTE = "Hit rates stay hidden on Track Record until 30 calls are scored, because a smaller sample would flatter or punish by chance."
_THESIS_TONE = {"Broken": "down", "Alive": "up"}


def _horizon(cell: Cell) -> TableCell:
    text, tip = cell
    tone = "up" if text.endswith("✓") else "down" if text.endswith("✗") else "muted" if text in ("pending", "n/a") else None
    return TableCell(text, tip, tone=tone, right=True, mono=True)


def _cells(row: CallRow) -> list[TableCell]:
    return [
        TableCell(row.symbol, mono=True),
        TableCell(row.day.isoformat(), mono=True),
        TableCell(row.kind),
        TableCell(row.action),
        TableCell(row.zone, right=True, mono=True),
        TableCell(row.thesis_break, right=True, mono=True),
        TableCell(row.target, right=True, mono=True),
        TableCell(row.thesis, row.thesis_tip, tone=_THESIS_TONE.get(row.thesis)),
        *(_horizon(c) for c in row.horizons),
    ]


class CallBookPage(QWidget):
    openValuation = Signal(str)
    openTrackRecord = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = label("Was the AI right, and is the thesis still alive?", "DeskTitle", wrap=True)
        self.note = label(NOTE, "Secondary", wrap=True)
        self.table = table(HEADERS, "AI calls", stretch=3)
        self.table.cellDoubleClicked.connect(self._activate)
        self.table.cellActivated.connect(self._activate)

        self._progress: list[tuple[QWidget, QProgressBar]] = []
        side = QVBoxLayout()
        side.setSpacing(8)
        bars = QWidget()
        bar_layout = QVBoxLayout(bars)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        for _ in range(2):
            text, bar = label("", "Body"), QProgressBar()
            bar.setObjectName("ScoreBar")
            bar.setTextVisible(False)
            bar_layout.addWidget(text)
            bar_layout.addWidget(bar)
            self._progress.append((text, bar))
        self.track_button = QPushButton("Open track record")
        self.track_button.clicked.connect(self.openTrackRecord)
        evidence = panel(heading("EVIDENCE SO FAR"), bars, label(GATE_NOTE, "Secondary", wrap=True), self.track_button)
        side.addWidget(evidence, 1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(panel(self.title, self.note, self.table, stretch_last=True), 4)
        layout.addLayout(side, 1)

    def show_snapshot(self, snapshot: DeskSnapshot) -> None:
        rows = call_rows(snapshot)
        fill_table(self.table, [_cells(r) for r in rows], [r.symbol for r in rows])
        if snapshot.journal_error:
            self.note.setText(JOURNAL_ERROR)
        elif not snapshot.journal_on:
            self.note.setText(NO_JOURNAL)
        else:
            self.note.setText(NOTE if rows else NO_CALLS)
        for (text, bar), (name, done, need) in zip(self._progress, call_progress(snapshot), strict=True):
            text.setText(f"{name}: {done} of {need} scored at 20 days")
            bar.setRange(0, need)
            bar.setValue(min(done, need))
            bar.setAccessibleName(text.text())

    def _activate(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            self.openValuation.emit(item.text())
