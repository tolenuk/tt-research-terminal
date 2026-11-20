"""Shared pieces of the dashboards: panels, labels and read-only tables.

Every label is plain text: symbols, headlines and AI text must never be read as markup.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.ui.desk.data import DeskSnapshot
from obb_terminal.ui.theme import TOKENS, mono_font, style_table

SYMBOL_ROLE = Qt.ItemDataRole.UserRole


@dataclass(frozen=True, slots=True)
class TableCell:
    text: str
    tip: str = ""
    tone: str | None = None  # a theme token for the text
    fill: str | None = None  # a theme token for the background
    right: bool = False
    mono: bool = False


def label(text: str = "", name: str | None = None, *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if name:
        widget.setObjectName(name)
    return widget


def heading(text: str) -> QLabel:
    return label(text, "SectionHeading")


def panel(*widgets: QWidget, stretch_last: bool = False) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch_last and widgets:
        layout.setStretchFactor(widgets[-1], 1)
    elif not stretch_last:
        layout.addStretch(1)
    return frame


def table(headers: Sequence[str], name: str, stretch: int | None = None) -> QTableWidget:
    """A read-only table; `stretch` names the one column that takes the spare width."""
    view = QTableWidget(0, len(headers))
    style_table(view)
    view.setHorizontalHeaderLabels(list(headers))
    view.setAccessibleName(name)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.verticalHeader().hide()
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    if stretch is not None:
        view.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return view


def _item(cell: TableCell) -> QTableWidgetItem:
    item = QTableWidgetItem(cell.text)
    if cell.tip:
        item.setToolTip(cell.tip)
    if cell.tone:
        item.setForeground(QColor(TOKENS[cell.tone]))
    if cell.fill:
        item.setBackground(QColor(TOKENS[cell.fill]))
    if cell.mono:
        item.setFont(mono_font())
    horizontal = Qt.AlignmentFlag.AlignRight if cell.right else Qt.AlignmentFlag.AlignLeft
    item.setTextAlignment(horizontal | Qt.AlignmentFlag.AlignVCenter)
    return item


def fill_table(view: QTableWidget, rows: Sequence[Sequence[TableCell]], symbols: Sequence[str]) -> None:
    """Replace the rows, keeping the selected symbol selected. The first column holds the symbol."""
    selected = selected_symbol(view)
    view.setRowCount(len(rows))
    for r, cells in enumerate(rows):
        for c, cell in enumerate(cells):
            view.setItem(r, c, _item(cell))
        first = view.item(r, 0)
        if first is not None:
            first.setData(SYMBOL_ROLE, symbols[r])
    if selected in symbols:
        view.selectRow(list(symbols).index(selected))


def selected_symbol(view: QTableWidget) -> str | None:
    row = view.currentRow()
    item = view.item(row, 0) if row >= 0 else None
    return None if item is None else item.data(SYMBOL_ROLE)


def loading_text(snapshot: DeskSnapshot) -> str:
    n = snapshot.loading
    return f"Loading {n} of {len(snapshot.entries)} symbols…" if n else ""


NO_SYMBOLS = "Your watchlist is empty. Add a symbol on the Watchlist tab to fill this page."
