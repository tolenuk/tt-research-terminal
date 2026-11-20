"""The heat-matrix table view: keyboard and mouse gestures become symbol-level signals."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QCursor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from obb_terminal.ui.watchlist_tab.delegate import WatchlistDelegate
from obb_terminal.ui.watchlist_tab.header import GroupedHeader
from obb_terminal.ui.watchlist_tab.model import COLUMN_GROUPS, Col, SymbolRole, indicator_at

ROW_HEIGHT = 48  # two text lines per cell


class HeatMatrixView(QTableView):
    editRequested = Signal(str, object)  # symbol, Indicator
    deleteRequested = Signal(str, object)  # symbol, Indicator: remove that target
    activateRequested = Signal(str)  # symbol: open it in OVERVIEW
    menuRequested = Signal(QModelIndex, QPoint)  # index, global position

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeatMatrix")
        self.setAccessibleName("Watchlist buy targets")
        self._hovered_row = -1
        self.setHorizontalHeader(GroupedHeader(COLUMN_GROUPS, self))
        self.setItemDelegate(WatchlistDelegate(self))
        self.setShowGrid(False)
        self.setMouseTracking(True)
        self.setSortingEnabled(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setWordWrap(False)
        self.setTabKeyNavigation(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Columns hug their contents and spare width stays to the right, so the groups sit side by
        # side instead of the last column stretching away from the rest.
        self.horizontalHeader().setStretchLastSection(False)
        self.clicked.connect(self._on_clicked)
        self.doubleClicked.connect(self._on_double_clicked)

    def hovered_row(self) -> int:
        return self._hovered_row

    def clear_hover(self) -> None:
        self._set_hovered_row(-1)

    def _set_hovered_row(self, row: int) -> None:
        if row != self._hovered_row:
            self._hovered_row = row
            self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._set_hovered_row(self.rowAt(event.position().toPoint().y()))
        super().mouseMoveEvent(event)

    def _pointer(self) -> QPoint:
        return QCursor.pos()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        """A scroll moves rows under a pointer that has not moved."""
        super().scrollContentsBy(dx, dy)
        if self._hovered_row != -1:
            pos = self.viewport().mapFromGlobal(self._pointer())
            self._set_hovered_row(self.rowAt(pos.y()) if self.viewport().rect().contains(pos) else -1)

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Leave:
            self.clear_hover()
        return super().viewportEvent(event)

    def _on_clicked(self, index: QModelIndex) -> None:
        indicator = indicator_at(index)
        if indicator is not None:
            self.editRequested.emit(index.data(SymbolRole), indicator)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if indicator_at(index) is None:
            self.activateRequested.emit(index.data(SymbolRole))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        index = self.currentIndex()
        key, mods = event.key(), event.modifiers()
        if index.isValid():
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                indicator = indicator_at(index)
                if indicator is not None:
                    self.editRequested.emit(index.data(SymbolRole), indicator)
                else:
                    self.activateRequested.emit(index.data(SymbolRole))
                return
            if key == Qt.Key.Key_Delete and indicator_at(index) is not None:
                self.deleteRequested.emit(index.data(SymbolRole), indicator_at(index))
                return
            if key == Qt.Key.Key_Menu or (
                key == Qt.Key.Key_F10 and mods & Qt.KeyboardModifier.ShiftModifier
            ):
                self.menuRequested.emit(index, self.viewport().mapToGlobal(self.visualRect(index).center()))
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        if event.reason() != QContextMenuEvent.Reason.Mouse:
            index = self.currentIndex()
            pos = self.viewport().mapToGlobal(self.visualRect(index).center())
        else:
            index = self.indexAt(self.viewport().mapFromGlobal(event.globalPos()))
            pos = event.globalPos()
        if index.isValid():
            self.menuRequested.emit(index, pos)

    def select_cell(self, symbol: str, col: Col) -> None:
        model = self.model()
        for r in range(model.rowCount()):
            index = model.index(r, col)
            if index.data(SymbolRole) == symbol:
                self.setCurrentIndex(index)
                return
