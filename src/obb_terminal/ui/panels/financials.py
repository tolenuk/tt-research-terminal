"""Income / Balance / Cash Flow statements, annual or quarterly (equities only)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QPushButton,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.data.market import MarketData
from obb_terminal.domain.models import FinancialTable, Period, Statement
from obb_terminal.ui.fmt import fmt_big
from obb_terminal.ui.panels.base import Panel
from obb_terminal.ui.theme import style_table
from obb_terminal.ui.worker import Loader

STATEMENTS = (Statement.INCOME, Statement.BALANCE, Statement.CASH_FLOW)
TAB_LABELS = ("Income", "Balance", "Cash Flow")


class FinancialsPanel(Panel):
    name = "financials"

    def __init__(self, loader: Loader, parent: QWidget | None = None) -> None:
        self._statement = Statement.INCOME
        self._period = Period.ANNUAL
        super().__init__(loader, "FINANCIALS", parent)
        self.tabs = QTabBar()
        for label in TAB_LABELS:
            self.tabs.addTab(label)
        self.tabs.currentChanged.connect(self._on_tab)
        self.quarterly_button = QPushButton("Quarterly")
        self.quarterly_button.setCheckable(True)
        self.quarterly_button.setAccessibleName("Show quarterly statements")
        self.quarterly_button.toggled.connect(self._on_period)
        self.header.addWidget(self.tabs)
        self.header.addWidget(self.quarterly_button)

    def build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        self.currency_label = QLabel("")
        self.currency_label.setTextFormat(Qt.TextFormat.PlainText)
        self.currency_label.setObjectName("Muted")
        self.table = QTableWidget()
        style_table(self.table)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.currency_label)
        layout.addWidget(self.table, 1)
        return content

    def _on_tab(self, index: int) -> None:
        self._statement = STATEMENTS[index]
        self.reload()

    def _on_period(self, quarterly: bool) -> None:
        self._period = Period.QUARTER if quarterly else Period.ANNUAL
        self.reload()

    def make_fetch(self, symbol: str) -> Callable[[MarketData], FinancialTable]:
        statement, period = self._statement, self._period
        return lambda md: md.financials(symbol, statement, period)

    def render(self, table: FinancialTable) -> None:
        self.currency_label.setText(f"Reported in {table.currency}" if table.currency else "")
        t = self.table
        t.clear()
        t.setColumnCount(len(table.periods))
        t.setRowCount(len(table.rows))
        t.setHorizontalHeaderLabels([p.isoformat() for p in table.periods])
        t.setVerticalHeaderLabels([row.label for row in table.rows])
        for i, row in enumerate(table.rows):
            for j, value in enumerate(row.values):
                item = QTableWidgetItem(fmt_big(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(i, j, item)
        t.resizeColumnsToContents()
