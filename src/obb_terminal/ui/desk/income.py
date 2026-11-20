"""INCOME: twelve months of dividends per share, the trailing yield and your yield target."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

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
from obb_terminal.ui.desk.views import MONTHS, IncomeRow, income_months, income_rows, next_ex_dates
from obb_terminal.ui.fmt import DASH

MAX_NEXT = 6
NOTE = "A year of dividends per share by ex-dividend month, in each stock's trading currency."
PER_SHARE = "The app doesn't know what you own, so these are per share, not money received."
NEXT_NOTE = "Estimated from last year's ex-dividend months, not from announced dates."


def _month(month: date, months: tuple[date, ...]) -> str:
    """The month's name; the two ends are the same month, so they also carry the year."""
    return f"{month:%b %y}" if month in (months[0], months[-1]) else f"{month:%b}"


def _amount(value: float | None) -> str:
    return "" if value is None else f"{value:,.2f}"


def _target(row: IncomeRow) -> TableCell:
    if row.target is None:
        return TableCell(DASH, "No yield target set", tone="muted", right=True, mono=True)
    if row.target_hit:
        return TableCell(f"Hit {row.target * 100:.1f}%", tone="up", right=True, mono=True)
    return TableCell(f"{row.target * 100:.1f}%", right=True, mono=True)


def _cells(row: IncomeRow) -> list[TableCell]:
    yield_text = DASH if row.ttm_yield is None else f"{row.ttm_yield * 100:.1f}%"
    return [
        TableCell(row.symbol, f"{row.currency}, {row.asset_type.value}", mono=True),
        *(TableCell(_amount(v), fill="sel_bg" if v is not None else None, right=True, mono=True) for v in row.paid),
        TableCell(yield_text, "Dividends with ex-dates in the last 365 days, divided by the price", right=True, mono=True),
        _target(row),
    ]


class IncomePage(QWidget):
    openOverview = Signal(str)
    setYieldTarget = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = label("When does each name pay, and is the yield worth it?", "DeskTitle", wrap=True)
        self.note = label(NOTE, "Secondary", wrap=True)
        self.table = table(["Symbol", *[""] * MONTHS, "12m yield", "Target"], "Dividends by month", stretch=0)
        self.table.cellDoubleClicked.connect(self._activate)
        self.table.cellActivated.connect(self._activate)
        self.table.itemSelectionChanged.connect(self._update_button)
        self.next_list = label("", "Body", wrap=True)
        self.next_list.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.target_button = QPushButton("Set a yield target")
        self.target_button.setAccessibleName("Set a yield target for the selected symbol")
        self.target_button.clicked.connect(self._set_target)

        side = QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(panel(heading("NEXT EX-DIVIDEND"), self.next_list, label(NEXT_NOTE, "Secondary", wrap=True)), 1)
        side.addWidget(panel(heading("PER SHARE FOR NOW"), label(PER_SHARE, "Secondary", wrap=True), self.target_button))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(panel(self.title, self.note, self.table, stretch_last=True), 4)
        layout.addLayout(side, 1)
        self._update_button()

    def show_snapshot(self, snapshot: DeskSnapshot) -> None:
        months = income_months(snapshot.today)
        self.table.setHorizontalHeaderLabels(["Symbol", *[_month(m, months) for m in months], "12m yield", "Target"])
        rows = income_rows(snapshot)
        fill_table(self.table, [_cells(r) for r in rows], [r.symbol for r in rows])
        upcoming = next_ex_dates(snapshot, snapshot.today)[:MAX_NEXT]
        self.next_list.setText("\n".join(f"{s}  {d:%b %Y}" for s, d in upcoming) or "No dividends in the past year.")
        waiting = loading_text(snapshot)
        self.note.setText(NO_SYMBOLS if not snapshot.entries else waiting or NOTE)
        self._update_button()

    def _update_button(self) -> None:
        self.target_button.setEnabled(selected_symbol(self.table) is not None)

    def _set_target(self) -> None:
        symbol = selected_symbol(self.table)
        if symbol:
            self.setYieldTarget.emit(symbol)

    def _activate(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            self.openOverview.emit(item.text())
