"""Add a symbol to the watchlist by ticker or company name (same search as the ticker bar)."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QLabel, QLineEdit, QWidget

from obb_terminal.data.market import DataError, SymbolNotFound
from obb_terminal.domain.models import Profile, SymbolMatch
from obb_terminal.store import SYMBOL_RE
from obb_terminal.ui.ticker_bar import SearchModel
from obb_terminal.ui.worker import Loader

log = logging.getLogger(__name__)


class AddSymbolBox(QWidget):
    symbolChosen = Signal(str)  # a symbol confirmed to exist

    def __init__(self, loader: Loader, parent: QWidget | None = None, *, debounce_ms: int = 300) -> None:
        super().__init__(parent)
        self._loader = loader
        self._search_seq = 0  # bumped per search and on submit; older results are ignored

        self.edit = QLineEdit()
        self.edit.setObjectName("SymbolEntry")  # typed text is not a figure: no amber
        self.edit.setPlaceholderText("Add symbol — e.g. AAPL, SHEL.L, Petrobras")
        self.edit.setAccessibleName("Add symbol to watchlist")
        self.model = SearchModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setCompletionRole(Qt.ItemDataRole.UserRole)
        self.completer.setWidget(self.edit)
        self.completer.activated[str].connect(self.submit)
        self.error_label = QLabel("")
        self.error_label.setObjectName("InlineError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setAccessibleName("Add symbol error")
        self.error_label.hide()

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(debounce_ms)
        self._debounce.timeout.connect(self._search)
        self.edit.textEdited.connect(self._on_edited)
        self.edit.returnPressed.connect(self._on_return)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.error_label)

    def submit(self, text: str) -> None:
        """Validate `text` as a symbol (a profile lookup) and emit symbolChosen if it exists."""
        self._debounce.stop()
        self._search_seq += 1
        self.completer.popup().hide()
        symbol = text.strip().upper()
        if not symbol:
            return
        if not SYMBOL_RE.match(symbol):
            self._show_error(f"Unknown symbol: {symbol}")
            return
        self._loader.submit(
            "wladd", lambda md: md.profile(symbol), self._on_profile,
            lambda err, s=symbol: self._on_profile_error(s, err), scoped=False,
        )

    # --- internals ------------------------------------------------------------------------

    def _on_edited(self, _text: str) -> None:
        self.error_label.hide()
        self._debounce.start()

    def _on_return(self) -> None:
        if not self.completer.popup().isVisible():
            self.submit(self.edit.text())

    def _search(self) -> None:
        query = self.edit.text().strip()
        if len(query) < 2:
            return
        self._search_seq += 1
        seq = self._search_seq
        self._loader.submit(
            "wlsearch", lambda md: md.search(query),
            lambda matches: self._on_matches(matches, seq), self._on_search_error, scoped=False,
        )

    def _on_matches(self, matches: list[SymbolMatch], seq: int) -> None:
        if seq != self._search_seq:
            return
        self.model.set_matches(matches)
        if matches:
            self.completer.complete()
        else:
            self.completer.popup().hide()

    def _on_search_error(self, err: DataError) -> None:
        log.info("Watchlist search failed: %s", err.user_message)

    def _on_profile(self, profile: Profile) -> None:
        self.edit.clear()
        self.error_label.hide()
        self.symbolChosen.emit(profile.symbol)

    def _on_profile_error(self, symbol: str, err: DataError) -> None:
        self._show_error(f"Unknown symbol: {symbol}" if isinstance(err, SymbolNotFound) else err.user_message)

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()
