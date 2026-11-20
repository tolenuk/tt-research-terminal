"""The TRACK RECORD tab. Reads run on SerialJobs; scoring runs on the Loader (it fetches)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from functools import partial

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.data.market import DataError, MarketData
from obb_terminal.journal.scorer import BUDGET_S, ScoringResult, run_scoring, scoring_due
from obb_terminal.journal.store import Journal
from obb_terminal.ui.jobs import SerialJobs
from obb_terminal.ui.theme import style_table
from obb_terminal.ui.track_record.view import (
    CALLS_HEADERS,
    COMPONENT_HEADERS,
    RECENT_HEADERS,
    Cell,
    TrackRecordView,
    bucket_cells,
    component_cells,
    load_view,
    recent_cells,
    summary_text,
)
from obb_terminal.ui.worker import Loader

log = logging.getLogger(__name__)

JOURNAL_OFF = "Journal off"
SCORE_KEY = "journal:score"
SCORE_TIMEOUT_S = BUDGET_S + 30
NOTE = (
    "Calls are scored against the local index (SPY, FTSE All-Share, Ibovespa) after 20, 60 and 120 "
    "trading days. A hit beats the market by 2% or more in the call's direction. Rates appear once a "
    "bucket has 30 scored calls. Suggested weights are not applied automatically."
)


class TextTableModel(QAbstractTableModel):
    """Read-only rows of (text, sentence) cells, with an optional key per row."""

    def __init__(self, headers: Sequence[str], parent=None) -> None:
        super().__init__(parent)
        self._headers = tuple(headers)
        self._rows: list[list[Cell]] = []
        self._keys: list[str] = []

    def set_rows(self, rows: list[list[Cell]], keys: list[str] | None = None) -> None:
        self.beginResetModel()
        self._rows = rows
        self._keys = keys or [""] * len(rows)
        self.endResetModel()

    def key(self, row: int) -> str:
        return self._keys[row]

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        text, sentence = self._rows[index.row()][index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return text
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleDescriptionRole):
            return sentence
        if role == Qt.ItemDataRole.TextAlignmentRole:
            align = Qt.AlignmentFlag.AlignLeft if index.column() < 2 else Qt.AlignmentFlag.AlignRight
            return int(align | Qt.AlignmentFlag.AlignVCenter)
        return None


def _table(model: TextTableModel, name: str) -> QTableView:
    view = QTableView()
    style_table(view)
    view.setModel(model)
    view.setAccessibleName(name)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.verticalHeader().hide()
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    view.horizontalHeader().setStretchLastSection(True)
    return view


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionHeading")
    return label


def score_task(journal: Journal, md: MarketData) -> ScoringResult:
    return run_scoring(md, journal, date.today())


def result_text(r: ScoringResult) -> str:
    if r.error is not None:
        return f"Scoring failed: {r.error}"
    parts = [f"Scored: {r.scored} outcomes, {r.pending} pending"]
    if r.failed:
        parts.append(f"{len(r.failed)} symbols failed")
    if r.skipped:
        parts.append(f"{len(r.skipped)} skipped (time limit)")
    return "; ".join(parts)


class TrackRecordTab(QWidget):
    symbolActivated = Signal(str)

    def __init__(
        self, loader: Loader, journal: Journal | None, jobs: SerialJobs, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._loader = loader
        self._journal = journal
        self._jobs = jobs
        self._scoring = False

        self.summary = QLabel("")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.score_button = QPushButton("Score now")
        self.score_button.setAccessibleName("Score journal calls whose horizons have passed")
        self.score_button.clicked.connect(self.score_now)
        note = QLabel(NOTE)
        note.setObjectName("Muted")
        note.setWordWrap(True)

        self.calls_model = TextTableModel(CALLS_HEADERS, self)
        self.components_model = TextTableModel(COMPONENT_HEADERS, self)
        self.recent_model = TextTableModel(RECENT_HEADERS, self)
        self.calls_view = _table(self.calls_model, "Calls versus market")
        self.components_view = _table(self.components_model, "Outlook components")
        self.recent_view = _table(self.recent_model, "Recent calls")
        self.recent_view.doubleClicked.connect(self._open)

        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.status)
        top.addWidget(self.score_button)
        middle = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(_heading("CALLS VS MARKET"))
        left.addWidget(self.calls_view)
        right.addWidget(_heading("OUTLOOK COMPONENTS"))
        right.addWidget(self.components_view)
        middle.addLayout(left, 3)
        middle.addLayout(right, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(note)
        layout.addLayout(middle, 2)
        layout.addWidget(_heading("RECENT CALLS"))
        layout.addWidget(self.recent_view, 3)
        self._update_button()

    # --- public API -----------------------------------------------------------------------

    def set_active(self, active: bool) -> None:
        if active:
            self.refresh()

    def refresh(self) -> None:
        if self._journal is None:
            self.summary.setText(JOURNAL_OFF)
            return
        self._jobs.submit(partial(load_view, self._journal), self._show, self._on_load_failed)

    def score_now(self) -> None:
        if self._journal is None or self._scoring:
            return
        self._scoring = True
        self.status.setText("Scoring…")
        self._update_button()
        self._loader.submit(
            SCORE_KEY, partial(score_task, self._journal), self._on_scored, self._on_score_failed,
            scoped=False, timeout_s=SCORE_TIMEOUT_S,
        )

    def score_if_due(self) -> None:
        """Start-up: score once a day in the background."""
        if self._journal is None:
            return
        journal = self._journal
        self._jobs.submit(
            lambda: scoring_due(journal, date.today()),
            lambda due: self.score_now() if due else None,
            lambda exc: log.warning("Could not check whether scoring is due", exc_info=exc),
        )

    # --- internals ------------------------------------------------------------------------

    def _show(self, view: TrackRecordView) -> None:
        self.summary.setText(summary_text(view))
        self.calls_model.set_rows([bucket_cells(b) for b in view.buckets])
        self.components_model.set_rows([component_cells(c) for c in view.components])
        self.recent_model.set_rows([recent_cells(r) for r in view.recent], [r[0].symbol for r in view.recent])

    def _on_load_failed(self, exc: Exception) -> None:
        log.error("Could not read the journal", exc_info=exc)
        self.summary.setText("Journal unreadable — see log")

    def _on_scored(self, result: ScoringResult) -> None:
        self._scoring = False
        self.status.setText(result_text(result))
        self._update_button()
        self.refresh()

    def _on_score_failed(self, err: DataError) -> None:
        self._scoring = False
        self.status.setText(f"Scoring failed: {err.user_message}")
        self._update_button()

    def _update_button(self) -> None:
        self.score_button.setEnabled(self._journal is not None and not self._scoring)

    def _open(self, index: QModelIndex) -> None:
        symbol = self.recent_model.key(index.row())
        if symbol:
            self.symbolActivated.emit(symbol)
