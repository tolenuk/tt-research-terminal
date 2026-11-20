"""The 12-row KPI table: value plus a good / neutral / weak mark."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from obb_terminal.domain.models import Kpi, Mark
from obb_terminal.ui.fmt import DASH, fmt_kpi
from obb_terminal.ui.theme import TOKENS, fit_rows, style_table

MARK_TEXT = {Mark.GOOD: "▲ good", Mark.NEUTRAL: "■ neutral", Mark.WEAK: "▼ weak", Mark.MISSING: f"{DASH} n/a"}
MARK_COLOUR = {Mark.GOOD: "up", Mark.NEUTRAL: "amber_dim", Mark.WEAK: "down", Mark.MISSING: "amber_dim"}
NOTES = {
    "Yahoo": "From Yahoo: mixes trading-currency prices with reporting-currency figures",
    "negative earnings": "Earnings are negative, so the ratio is not meaningful",
}


class KpiTable(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 3, parent)
        style_table(self)
        self.setHorizontalHeaderLabels(["KPI", "Value", "Mark"])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.setAccessibleName("Key performance indicators")

    def show_kpis(self, kpis: list[Kpi]) -> None:
        self.setRowCount(len(kpis))
        for row, k in enumerate(kpis):
            label = QTableWidgetItem(k.label)
            value = QTableWidgetItem(fmt_kpi(k))
            value.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if k.note:
                value.setToolTip(NOTES.get(k.note, k.note))
            mark = QTableWidgetItem(MARK_TEXT[k.mark])
            mark.setForeground(QColor(TOKENS[MARK_COLOUR[k.mark]]))
            for col, item in enumerate((label, value, mark)):
                self.setItem(row, col, item)
        self.resizeColumnsToContents()
        fit_rows(self)
