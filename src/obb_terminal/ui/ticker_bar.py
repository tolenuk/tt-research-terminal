"""Ticker entry with debounced company-name search, plus the security header."""

from __future__ import annotations

import logging

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QLabel, QLineEdit, QWidget

from obb_terminal.data.market import DataError
from obb_terminal.domain.models import AssetType, Profile, Quote, SymbolMatch
from obb_terminal.ui.fmt import direction, fmt_change, fmt_price
from obb_terminal.ui.theme import set_dir
from obb_terminal.ui.worker import Loader

log = logging.getLogger(__name__)


class SearchModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._matches: list[SymbolMatch] = []

    def set_matches(self, matches: list[SymbolMatch]) -> None:
        self.beginResetModel()
        self._matches = list(matches)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._matches)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        match = self._matches[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            kind = match.asset_type.value.upper() if match.asset_type else (match.quote_type or "")
            return f"{match.symbol:<11} {match.name[:40]:<40} {match.exchange or '':<4} {kind}"
        if role == Qt.ItemDataRole.UserRole:
            return match.symbol
        return None


class TickerBar(QWidget):
    symbolRequested = Signal(str)

    def __init__(self, loader: Loader, parent: QWidget | None = None, *, debounce_ms: int = 300) -> None:
        super().__init__(parent)
        self._loader = loader
        # Bumped per submitted search and on choose/Enter; older results are ignored.
        self._search_seq = 0

        self.edit = QLineEdit()
        self.edit.setObjectName("TickerEdit")
        self.edit.setPlaceholderText("Search ticker or company — e.g. AAPL, SHEL.L, Petrobras")
        self.edit.setAccessibleName("Ticker search")
        self.model = SearchModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setCompletionRole(Qt.ItemDataRole.UserRole)
        self.completer.setWidget(self.edit)
        self.completer.activated[str].connect(self.choose)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(debounce_ms)
        self._debounce.timeout.connect(self._search)
        self.edit.textEdited.connect(self._on_edited)
        self.edit.returnPressed.connect(self._on_return)

        self.error_label = QLabel("")
        self.error_label.setObjectName("InlineError")
        self.error_label.setAccessibleName("Ticker error")
        self.error_label.hide()
        self.symbol_label = QLabel("")
        self.symbol_label.setObjectName("SecuritySymbol")
        self.name_label = QLabel("")
        self.name_label.setObjectName("SecurityName")
        self.price_label = QLabel("")
        self.price_label.setObjectName("Price")
        self.change_label = QLabel("")
        # Names and typed symbols come from Yahoo / the user: never interpret them as markup.
        for label in (self.error_label, self.symbol_label, self.name_label, self.price_label, self.change_label):
            label.setTextFormat(Qt.TextFormat.PlainText)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self.edit, 2)
        layout.addWidget(self.error_label)
        layout.addStretch(1)
        # Ticker and name sit beside the price, so it always reads as that security's price.
        layout.addWidget(self.symbol_label)
        layout.addWidget(self.name_label)
        layout.addWidget(self.price_label)
        layout.addWidget(self.change_label)

    def _on_edited(self, _text: str) -> None:
        self.clear_error()
        self._debounce.start()

    def _search(self) -> None:
        query = self.edit.text().strip()
        if len(query) < 2:
            return
        self._search_seq += 1
        seq = self._search_seq
        self._loader.submit(
            "search",
            lambda md: md.search(query),
            lambda matches: self._on_matches(matches, seq),
            self._on_search_error,
            scoped=False,
        )

    def _cancel_search(self) -> None:
        self._debounce.stop()
        self._search_seq += 1

    def _on_matches(self, matches: list[SymbolMatch], seq: int | None = None) -> None:
        if seq is not None and seq != self._search_seq:
            return
        self.model.set_matches(matches)
        if matches:
            self.completer.complete()
        else:
            self.completer.popup().hide()

    def _on_search_error(self, err: DataError) -> None:
        log.info("Search failed: %s", err.user_message)

    def _on_return(self) -> None:
        if self.completer.popup().isVisible():
            return
        text = self.edit.text().strip().upper()
        if text:
            self._cancel_search()
            self.symbolRequested.emit(text)

    def choose(self, symbol: str) -> None:
        self._cancel_search()
        self.completer.popup().hide()
        self.edit.setText(symbol)
        self.symbolRequested.emit(symbol)

    def set_profile(self, profile: Profile) -> None:
        self.symbol_label.setText(profile.symbol)
        kind = "Equity" if profile.asset_type is AssetType.EQUITY else profile.asset_type.value.upper()
        self.name_label.setText(f"{profile.name}  ·  {profile.exchange or '—'}  ·  {kind}")
        # The previous symbol's price must never sit under the new name.
        self.price_label.clear()
        self.change_label.clear()
        set_dir(self.change_label, "flat")

    def set_quote(self, quote: Quote) -> None:
        self.price_label.setText(fmt_price(quote.last, quote.currency.value))
        self.change_label.setText(fmt_change(quote.change, quote.change_pct))
        set_dir(self.change_label, direction(quote.change))

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.hide()
