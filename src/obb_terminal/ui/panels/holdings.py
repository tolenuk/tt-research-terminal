"""ETF look-through on Overview: top holdings, sector (or credit-rating) weights and asset mix."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.data.market import MarketData
from obb_terminal.domain.etf import rating_label, sector_label
from obb_terminal.domain.models import FundProfile
from obb_terminal.ui.fmt import fmt_pct
from obb_terminal.ui.panels.base import Panel
from obb_terminal.ui.theme import TOKENS, style_table
from obb_terminal.ui.worker import Loader

NO_HOLDINGS_TEXT = "Yahoo has no holdings for this fund"
BAR_ALPHA = 70
WEIGHT_ROLE = Qt.ItemDataRole.UserRole + 1  # a row's weight as a fraction of the largest
MIX_COLOURS = ("amber", "amber_dim", "muted", "scroll")


class WeightBarDelegate(QStyledItemDelegate):
    """Paints a thin bar under the cell's text, as long as the row's share of the largest row."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex | QPersistentModelIndex) -> None:
        share = index.data(WEIGHT_ROLE)
        if isinstance(share, float) and share > 0:
            r: QRect = option.rect  # type: ignore[attr-defined]
            width = max(2.0, (r.width() - 4) * min(share, 1.0))
            colour = QColor(TOKENS["amber_dim"])
            colour.setAlpha(BAR_ALPHA)
            painter.save()
            painter.fillRect(QRectF(r.left() + 2, r.top() + 3, width, r.height() - 6), colour)
            painter.restore()
        super().paint(painter, option, index)  # the figure stays readable on top of its bar


class MixBar(QWidget):
    """One stacked bar of the asset mix, the percentages written beside it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.parts: list[tuple[str, float]] = []
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Asset mix")

    def set_parts(self, parts: Sequence[tuple[str, float]]) -> None:
        self.parts = [(name, w) for name, w in parts if w > 0]
        self.setAccessibleDescription(", ".join(f"{n} {w * 100:.1f}%" for n, w in self.parts))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(TOKENS["raised"]))
        total = sum(w for _, w in self.parts) or 1.0
        x = 0.0
        for n, (_, w) in enumerate(self.parts):
            width = self.width() * w / total
            painter.fillRect(QRectF(x, 0, width, self.height()), QColor(TOKENS[MIX_COLOURS[n % len(MIX_COLOURS)]]))
            x += width
        painter.end()


def _table(headers: Sequence[str], name: str) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    style_table(table)
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(22)
    table.setAccessibleName(name)
    table.setItemDelegateForColumn(len(headers) - 1, WeightBarDelegate(table))
    return table


def _weight_item(weight: float, largest: float) -> QTableWidgetItem:
    item = QTableWidgetItem(fmt_pct(weight))
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    item.setData(WEIGHT_ROLE, weight / largest if largest > 0 else 0.0)
    return item


def _summary(p: FundProfile) -> str:
    parts = [x for x in (p.category, p.family) if x]
    if p.expense_ratio is not None:
        avg = f" vs {fmt_pct(p.category_expense_ratio)} avg" if p.category_expense_ratio else ""
        parts.append(f"Expense {fmt_pct(p.expense_ratio)}{avg}")
    if p.turnover is not None:
        parts.append(f"Turnover {fmt_pct(p.turnover)}")
    return " · ".join(parts)


class HoldingsPanel(Panel):
    name = "holdings"
    holdingActivated = Signal(str)
    dividendsRequested = Signal()

    def __init__(self, loader: Loader, parent: QWidget | None = None) -> None:
        super().__init__(loader, "HOLDINGS", parent)
        self.dividends_button = QPushButton("Dividends")
        self.dividends_button.setObjectName("PanelSwitch")
        self.dividends_button.setAccessibleName("Show the fund's dividends")
        self.dividends_button.clicked.connect(self.dividendsRequested)
        self.header.addWidget(self.dividends_button)

    def build_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)
        self.summary = QLabel("")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.empty_note = QLabel(NO_HOLDINGS_TEXT)
        self.empty_note.setObjectName("Muted")
        self.empty_note.hide()
        self.mix = MixBar()
        self.mix_text = QLabel("")
        self.mix_text.setObjectName("Muted")
        self.mix_text.setTextFormat(Qt.TextFormat.PlainText)

        self.holdings = _table(("Symbol", "Name", "Weight"), "Top holdings")
        self.holdings.setToolTip("Double-click a holding to open it")
        self.holdings.cellDoubleClicked.connect(self._activate)
        header = self.holdings.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.coverage = QLabel("")
        self.coverage.setObjectName("Muted")
        left = QVBoxLayout()
        top_heading = QLabel("TOP HOLDINGS")
        top_heading.setObjectName("SectionHeading")
        left.addWidget(top_heading)
        left.addWidget(self.holdings, 1)
        left.addWidget(self.coverage)

        self.sector_heading = QLabel("SECTORS")
        self.sector_heading.setObjectName("SectionHeading")
        self.sectors = _table(("Sector", "Weight"), "Sector weights")
        self.sectors.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        right = QVBoxLayout()
        right.addWidget(self.sector_heading)
        right.addWidget(self.sectors, 1)
        right.addWidget(self.mix)
        right.addWidget(self.mix_text)

        tables = QHBoxLayout()
        tables.addLayout(left, 3)
        tables.addLayout(right, 2)
        layout.addWidget(self.summary)
        layout.addWidget(self.empty_note)
        layout.addLayout(tables, 1)
        return content

    def make_fetch(self, symbol: str) -> Callable[[MarketData], FundProfile]:
        return lambda md: md.fund_profile(symbol)

    def render(self, p: FundProfile) -> None:
        self.summary.setText(_summary(p))
        empty = not p.holdings and not p.sectors and not p.bond_ratings
        self.empty_note.setVisible(empty)
        largest = p.holdings[0].weight if p.holdings else 0.0
        self.holdings.setRowCount(len(p.holdings))
        for row, h in enumerate(p.holdings):
            self.holdings.setItem(row, 0, QTableWidgetItem(h.symbol))
            self.holdings.setItem(row, 1, QTableWidgetItem(h.name))
            self.holdings.setItem(row, 2, _weight_item(h.weight, largest))
        self.holdings.resizeColumnToContents(0)
        top = p.top_weight
        self.coverage.setText("" if top is None else f"Top {len(p.holdings)} = {top * 100:.1f}% of the fund")
        bonds = bool(p.bond_ratings) and not p.sectors
        self.sector_heading.setText("CREDIT RATING" if bonds else "SECTORS")
        self.sectors.setHorizontalHeaderLabels(["Rating" if bonds else "Sector", "Weight"])
        rows = [(rating_label(k) if bonds else sector_label(k), w) for k, w in (p.bond_ratings if bonds else p.sectors)]
        biggest = rows[0][1] if rows else 0.0
        self.sectors.setRowCount(len(rows))
        for row, (label, weight) in enumerate(rows):
            self.sectors.setItem(row, 0, QTableWidgetItem(label))
            self.sectors.setItem(row, 1, _weight_item(weight, biggest))
        mix = [(name, w) for name, w in (("Stocks", p.stock_position), ("Bonds", p.bond_position),
                                         ("Cash", p.cash_position), ("Other", p.other_position))
               if w is not None]
        self.mix.set_parts(mix)
        self.mix.setVisible(bool(mix))
        self.mix_text.setText("   ".join(f"{name} {w * 100:.1f}%" for name, w in mix if w > 0.0005))

    def _activate(self, row: int, _col: int) -> None:
        item = self.holdings.item(row, 0)
        if item is not None and item.text():
            self.holdingActivated.emit(item.text())
