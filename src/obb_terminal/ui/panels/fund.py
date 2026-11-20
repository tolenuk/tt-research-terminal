"""Fund view for ETFs and FIIs: TTM yield, size, and dividend history."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.data.market import MarketData
from obb_terminal.domain.models import FundInfo
from obb_terminal.ui.fmt import fmt_big, fmt_pct
from obb_terminal.ui.panels.base import Panel
from obb_terminal.ui.theme import style_table
from obb_terminal.ui.worker import Loader


class FundPanel(Panel):
    name = "fund"
    holdingsRequested = Signal()

    def __init__(self, loader: Loader, parent: QWidget | None = None) -> None:
        super().__init__(loader, "FUND · DIVIDENDS", parent)
        self.holdings_button = QPushButton("Holdings")
        self.holdings_button.setObjectName("PanelSwitch")
        self.holdings_button.setAccessibleName("Show the fund's holdings")
        self.holdings_button.clicked.connect(self.holdingsRequested)
        self.holdings_button.hide()  # ETFs only: FIIs have no holdings view
        self.header.addWidget(self.holdings_button)

    def set_holdings_switch(self, visible: bool) -> None:
        self.holdings_button.setVisible(visible)

    def build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        self.summary = QLabel("")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.empty_note = QLabel("No dividends recorded")
        self.empty_note.setObjectName("Muted")
        self.empty_note.hide()
        self.table = QTableWidget(0, 2)
        style_table(self.table)
        self.table.setHorizontalHeaderLabels(["Ex-date", "Amount"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        layout.addWidget(self.summary)
        layout.addWidget(self.empty_note)
        layout.addWidget(self.table, 1)
        return content

    def make_fetch(self, symbol: str) -> Callable[[MarketData], FundInfo]:
        return lambda md: md.fund_info(symbol)

    def render(self, info: FundInfo) -> None:
        ccy = info.currency.value
        parts = [f"TTM yield {fmt_pct(info.ttm_yield)}"]
        if info.total_assets is not None:
            parts.append(f"Total assets {fmt_big(info.total_assets)} {ccy}")
        if info.expense_ratio is not None:
            parts.append(f"Expense ratio {fmt_pct(info.expense_ratio)}")
        self.summary.setText("   ·   ".join(parts))
        self.empty_note.setVisible(not info.dividends)
        self.table.setRowCount(len(info.dividends))
        for i, dividend in enumerate(info.dividends):
            self.table.setItem(i, 0, QTableWidgetItem(dividend.ex_date.isoformat()))
            amount = QTableWidgetItem(f"{dividend.amount:,.4f} {ccy}")
            amount.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 1, amount)
        self.table.resizeColumnsToContents()
