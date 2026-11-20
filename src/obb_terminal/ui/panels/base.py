"""Panel base: header + EMPTY/LOADING/READY/ERROR pages, one in-flight load keyed by `name`."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.data.market import DataError, MarketData, NoData, SymbolNotFound
from obb_terminal.domain.models import Profile
from obb_terminal.ui.worker import Loader


class PanelState(Enum):
    EMPTY = "empty"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


def error_message(err: DataError, symbol: str | None) -> str:
    if isinstance(err, NoData | SymbolNotFound):
        return f"No data available for {symbol}"
    return err.user_message


class _Title(QLabel):
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class Panel(QFrame):
    maximiseRequested = Signal()
    loaded = Signal(object)

    name = "panel"
    timeout_s: float | None = None  # Loader backstop for this panel's fetch; None = Loader default

    def __init__(self, loader: Loader, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._loader = loader
        self.symbol: str | None = None
        self.profile: Profile | None = None
        self.state = PanelState.EMPTY

        self.title = _Title(title)
        self.title.setObjectName("PanelTitle")
        self.title.setToolTip("Double-click to maximise")
        self.title.doubleClicked.connect(self.maximiseRequested)
        self.header_frame = QFrame()
        self.header_frame.setObjectName("PanelHeader")
        self.header = QHBoxLayout(self.header_frame)
        self.header.setContentsMargins(0, 0, 0, 0)
        self.header.setSpacing(4)
        self.header.addWidget(self.title, 0, Qt.AlignmentFlag.AlignBottom)
        self.header.addStretch(1)

        empty = QLabel("No symbol")
        empty.setObjectName("Muted")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading = QLabel("Loading…")
        loading.setObjectName("Muted")
        loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label = QLabel()
        self._error_label.setObjectName("PanelError")
        self._error_label.setTextFormat(Qt.TextFormat.PlainText)
        self._error_label.setWordWrap(True)
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.retry_button = QPushButton("Retry")
        self.retry_button.setAccessibleName(f"Retry {title}")
        self.retry_button.clicked.connect(self.retry)
        error_page = QWidget()
        error_layout = QVBoxLayout(error_page)
        error_layout.addStretch(1)
        error_layout.addWidget(self._error_label)
        error_layout.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignHCenter)
        error_layout.addStretch(1)

        self._pages: dict[PanelState, QWidget] = {
            PanelState.EMPTY: empty,
            PanelState.LOADING: loading,
            PanelState.READY: self.build_content(),
            PanelState.ERROR: error_page,
        }
        self._stack = QStackedWidget()
        for page in self._pages.values():
            self._stack.addWidget(page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self.header_frame)
        layout.addWidget(self._stack, 1)
        self._set_state(PanelState.EMPTY)

    # --- subclass hooks -------------------------------------------------------------------

    def build_content(self) -> QWidget:
        raise NotImplementedError

    def make_fetch(self, symbol: str) -> Callable[[MarketData], Any]:
        raise NotImplementedError

    def render(self, data: Any) -> None:
        raise NotImplementedError

    # --- public API -----------------------------------------------------------------------

    @property
    def error_text(self) -> str:
        return self._error_label.text()

    def load(self, symbol: str, profile: Profile | None) -> None:
        self.symbol, self.profile = symbol, profile
        self._set_state(PanelState.LOADING)
        self._submit(silent=False)

    def reload(self) -> None:
        if self.symbol is not None:
            self.load(self.symbol, self.profile)

    def retry(self) -> None:
        self.reload()

    def refresh(self) -> None:
        if self.symbol is None or self._loader.is_pending(self.name):
            return
        self._submit(silent=True)

    def clear(self) -> None:
        self.symbol, self.profile = None, None
        self._set_state(PanelState.EMPTY)

    # --- internals ------------------------------------------------------------------------

    def _set_state(self, state: PanelState) -> None:
        self.state = state
        self._stack.setCurrentWidget(self._pages[state])

    def _submit(self, *, silent: bool) -> None:
        assert self.symbol is not None
        self._loader.submit(
            self.name,
            self.make_fetch(self.symbol),
            self._on_ok,
            lambda err: self._on_err(err, silent),
            timeout_s=self.timeout_s,
        )

    def _on_ok(self, data: Any) -> None:
        self.render(data)
        self._set_state(PanelState.READY)
        self.loaded.emit(data)

    def _on_err(self, err: DataError, silent: bool) -> None:
        if silent and self.state is PanelState.READY:
            return
        self._error_label.setText(error_message(err, self.symbol))
        self._set_state(PanelState.ERROR)
