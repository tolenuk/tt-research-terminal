"""The WATCHLIST tab: buy targets per symbol, the heat matrix, and the background-alerts switch.

Each symbol is fetched on the Loader (`wl:<SYM>`, unscoped) by `fetch_and_apply`, which also
applies alert transitions under the targets lock on the worker thread, so the GUI never waits
on the lock and a hit is notified as soon as it is seen. Target edits run on SerialJobs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.alerts.background import default_installer
from obb_terminal.alerts.engine import SAVE_FAILED, AlertEngine, ApplyResult
from obb_terminal.alerts.notify import default_notifier
from obb_terminal.alerts.store import TargetsRepo
from obb_terminal.alerts.systemd import Installer
from obb_terminal.data.market import DataError, MarketData
from obb_terminal.data.rollup import RollupMemo, attach_cached
from obb_terminal.domain.models import Quote, ValuationInputs
from obb_terminal.domain.targets import (
    LABEL,
    EventKind,
    Indicator,
    SymbolTargets,
    TargetsFile,
    cleared,
    edit,
    evaluate,
    failed_row,
    set_thesis,
)
from obb_terminal.fsutil import LockBusy
from obb_terminal.ui.jobs import SerialJobs
from obb_terminal.ui.watchlist_tab.add_box import AddSymbolBox
from obb_terminal.ui.watchlist_tab.alerts_bar import AlertsToggle
from obb_terminal.ui.watchlist_tab.editor import TargetEditor
from obb_terminal.ui.watchlist_tab.model import (
    Col,
    HeatMatrixModel,
    HeatSortProxy,
    RowState,
    RowView,
    SymbolRole,
    indicator_at,
    summary_text,
)
from obb_terminal.ui.watchlist_tab.view import HeatMatrixView
from obb_terminal.ui.worker import Loader

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 50.0
GAP_RETRY_MS = 45_000  # partial data is fetched again soon, whatever the refresh interval
BACKGROUND_REFRESH_FACTOR = 3  # every 15 minutes by default, like the background checker
BUSY_MESSAGE = "Targets busy — try again"
RECOVERED_MESSAGE = "Targets file was unreadable — started with no targets"
UNREADABLE_MESSAGE = "Targets file unreadable — see log"


@dataclass(frozen=True, slots=True)
class AlertServices:
    engine: AlertEngine
    installer: Installer


def default_alert_services() -> AlertServices:
    return AlertServices(AlertEngine(TargetsRepo(), default_notifier()), default_installer())


def fetch_and_apply(
    engine: AlertEngine, symbol: str, md: MarketData, *, fresh: bool = False,
    rollups: RollupMemo | None = None,
) -> tuple[ValuationInputs, ApplyResult]:
    """Loader task: fetch one symbol (DataError propagates) and apply its alert transitions.
    `fresh` drops the symbol's cached inputs first so the prices are current. An ETF gets the
    top-holdings roll-up the Valuation page already ran, if any (the desk never starts one)."""
    if fresh:
        invalidate = getattr(md, "invalidate_valuation", None)
        if invalidate is not None:
            invalidate(symbol)
    inputs = attach_cached(md.valuation_inputs(symbol), rollups)
    return inputs, engine.apply([inputs])


def _ask_thesis_level(parent: QWidget, symbol: str, current: float | None) -> float | None:
    level, ok = QInputDialog.getDouble(
        parent, "Thesis break", f"{symbol}: alert when the price falls to (trading currency)",
        current or 0.0, 0.0, 1e9, 2,
    )
    return level if ok and level > 0 else None


def alert_message(symbol: str, kinds: list[EventKind]) -> str:
    """Status text for the alerts one refresh fired."""
    parts = []
    buys = sum(1 for k in kinds if k is not EventKind.THESIS_BROKEN)
    if buys:
        parts.append(f"{buys} buy alert{'s' if buys > 1 else ''}")
    if EventKind.THESIS_BROKEN in kinds:
        parts.append("thesis break hit")
    return f"{symbol}: {' and '.join(parts)} — see notifications"


class WatchlistTab(QWidget):
    symbolActivated = Signal(str)  # open in OVERVIEW
    addRequested = Signal(str)
    removeRequested = Signal(str)
    hitCountChanged = Signal(int)
    message = Signal(str)
    dataChanged = Signal()  # a row or the symbol list changed: the dashboards re-read

    def __init__(
        self,
        loader: Loader,
        services: AlertServices,
        jobs: SerialJobs,
        symbols: list[str],
        parent: QWidget | None = None,
        *,
        refresh_ms: int = 300_000,
        max_in_flight: int = 3,
        rollups: RollupMemo | None = None,
    ) -> None:
        super().__init__(parent)
        self._loader = loader
        self._rollups = rollups
        self._engine = services.engine
        self._jobs = jobs
        self._refresh_ms = refresh_ms
        self._max_in_flight = max_in_flight
        self.gap_retry_ms = GAP_RETRY_MS
        self._queue: list[str] = []
        self._in_flight: set[str] = set()
        self._fresh: set[str] = set()  # queued by a refresh: bypass the cache
        self._refetch: set[str] = set()  # saved while in flight: re-fetch right after
        self._inputs: dict[str, ValuationInputs] = {}
        self._last_refresh: float | None = None
        self._active = False
        snapshot = self._engine.snapshot()
        self._targets: TargetsFile = snapshot.file
        self.editor: TargetEditor | None = None
        # (symbol, current level) -> new level or None; replaced in tests
        self.ask_thesis_level = partial(_ask_thesis_level, self)

        self.add_box = AddSymbolBox(loader)
        self.alerts = AlertsToggle(jobs, services.installer)
        self.alerts.failed.connect(self.message)
        self.summary = QLabel()
        self.summary.setObjectName("Secondary")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setAccessibleName("Refresh watchlist prices")
        self.refresh_button.clicked.connect(self.refresh)
        self.model = HeatMatrixModel(self)
        self.model.dataChanged.connect(self._update_summary)
        self.model.modelReset.connect(self._update_summary)
        self.proxy = HeatSortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.view = HeatMatrixView()
        self.view.setModel(self.proxy)
        self.view.sortByColumn(Col.READINESS, Qt.SortOrder.DescendingOrder)
        self.view.editRequested.connect(self.open_editor)
        self.view.deleteRequested.connect(self._delete_target)
        self.view.activateRequested.connect(self.symbolActivated)
        self.view.menuRequested.connect(self._show_menu)
        self.add_box.symbolChosen.connect(self.addRequested)

        top = QHBoxLayout()
        top.addWidget(self.add_box, 2)
        top.addSpacing(12)
        top.addWidget(self.summary)
        top.addStretch(1)
        top.addWidget(self.alerts)
        top.addWidget(self.refresh_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)
        self.set_symbols(symbols)
        if snapshot.recovered:
            QTimer.singleShot(0, self, partial(self.message.emit, RECOVERED_MESSAGE))
        if snapshot.error:
            QTimer.singleShot(0, self, partial(self.message.emit, UNREADABLE_MESSAGE))

    # --- API ------------------------------------------------------------------------------

    def set_symbols(self, symbols: list[str]) -> None:
        added = [s for s in symbols if s not in self.model.symbols()]
        self.model.set_symbols(symbols)
        self._queue = [s for s in self._queue if s in symbols]
        for symbol in added:
            self._show_cached(symbol)
        if self._active and added:
            self._enqueue(added)
        self.hitCountChanged.emit(self.model.hit_count())
        self.dataChanged.emit()

    def set_active(self, active: bool, *, background: bool = False) -> None:
        """`background`: a dashboard reads this data while the tab itself is hidden, so it may
        be open all day and refreshes less often."""
        self._active = active
        if not active:
            self._timer.stop()
            return
        interval = self._refresh_ms * (BACKGROUND_REFRESH_FACTOR if background else 1)
        self._timer.setInterval(interval)
        self._timer.start()
        stale = self._last_refresh is None or (
            (time.monotonic() - self._last_refresh) * 1000 >= interval
        )
        if stale:
            self.refresh()
        else:  # symbols added while hidden were never fetched
            self._enqueue([s for s in self.model.symbols() if self.model.row(s).state is RowState.LOADING])

    def refresh(self) -> None:
        self._last_refresh = time.monotonic()
        self._enqueue(self.model.symbols(), fresh=True)
        self.alerts.refresh_status()

    def open_editor(self, symbol: str, indicator: Indicator) -> None:
        st = self._targets.get(symbol)
        current = st.target(indicator) if st else None
        old = self.editor
        if old is not None:
            old.close()  # its finished() clears self.editor
            old.deleteLater()
        self.editor = TargetEditor(symbol, indicator, self._inputs.get(symbol), current, self)
        self.editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.editor.finished.connect(self._editor_finished)
        self.editor.saved.connect(self.save_target)
        self.editor.deleted.connect(self._delete_target)
        self.editor.open()

    def _editor_finished(self, _result: int) -> None:
        if self.sender() is self.editor:  # a superseded editor's close must not clear the new one
            self.editor = None

    def save_target(self, symbol: str, indicator: Indicator, level: float | None, enabled: bool) -> None:
        """Show the edit at once from cached data, persist it on SerialJobs, then re-apply."""
        try:
            self._show_targets(edit(self._targets.get(symbol), symbol, indicator, level, enabled))
        except ValueError as exc:
            self.message.emit(str(exc))
            return
        self._jobs.submit(
            partial(self._engine.set_target, symbol, indicator, level, enabled),
            partial(self._on_saved, symbol),
            self._on_save_failed,
        )

    def clear_targets(self, symbol: str) -> None:
        self._show_targets(cleared(self._targets.get(symbol), symbol))
        self._jobs.submit(
            partial(self._engine.clear_targets, symbol),
            partial(self._on_saved, symbol),
            self._on_save_failed,
        )

    def save_thesis(self, symbol: str, level: float | None) -> None:
        """Set (None: clear) the thesis break: shown at once, persisted on SerialJobs."""
        try:
            self._show_targets(set_thesis(self._targets.get(symbol), symbol, level))
        except ValueError as exc:
            self.message.emit(str(exc))
            return
        self._jobs.submit(
            partial(self._engine.set_thesis_break, symbol, level),
            partial(self._on_saved, symbol),
            self._on_save_failed,
        )

    def set_thesis_break_interactively(self, symbol: str) -> None:
        st = self._targets.get(symbol)
        level = self.ask_thesis_level(symbol, None if st is None or st.thesis is None else st.thesis.level)
        if level is not None:
            self.save_thesis(symbol, level)

    def apply_call(self, symbol: str, price_level: float | None, thesis_level: float | None) -> None:
        """An AI call's Price target and/or thesis break, written in one transaction."""
        st = self._targets.get(symbol)
        try:
            if price_level is not None:
                st = edit(st, symbol, Indicator.PRICE, price_level, True)
            if thesis_level is not None:
                st = set_thesis(st, symbol, thesis_level)
        except ValueError as exc:
            self.message.emit(str(exc))
            return
        if st is None:
            return
        self._show_targets(st)
        self._jobs.submit(
            partial(self._engine.apply_call, symbol, price_level=price_level, thesis_level=thesis_level),
            partial(self._on_saved, symbol),
            self._on_save_failed,
        )

    def set_quote(self, quote: Quote) -> None:
        self.model.set_quote(quote)

    def set_trend(self, symbol: str, closes: tuple[float, ...]) -> None:
        self.model.set_trend(symbol, closes)

    def _update_summary(self) -> None:
        self.summary.setText(summary_text(self.row_views()))

    def row_views(self) -> list[RowView]:
        """Every symbol's current row, in watchlist order."""
        rows = (self.model.row(s) for s in self.model.symbols())
        return [r for r in rows if r is not None]

    def targets_for(self, symbol: str) -> SymbolTargets | None:
        return self._targets.get(symbol)

    def build_context_menu(self, index: QModelIndex) -> QMenu:
        symbol = index.data(SymbolRole)
        indicator = indicator_at(index)
        menu = QMenu(self)
        if indicator is not None:
            menu.addAction(f"Edit {LABEL[indicator]} target…", partial(self.open_editor, symbol, indicator))
        menu.addAction(f"Open {symbol} in Overview", partial(self.symbolActivated.emit, symbol))
        menu.addAction("Set thesis break…", partial(self.set_thesis_break_interactively, symbol))
        st = self._targets.get(symbol)
        if st is not None and st.thesis is not None:
            menu.addAction("Clear thesis break", partial(self.save_thesis, symbol, None))
        menu.addAction("Clear targets", partial(self.clear_targets, symbol))
        menu.addAction("Remove from watchlist", partial(self.removeRequested.emit, symbol))
        return menu

    # --- fetching -------------------------------------------------------------------------

    def _enqueue(self, symbols: list[str], *, fresh: bool = False) -> None:
        for symbol in symbols:
            if symbol not in self._queue and symbol not in self._in_flight:
                self._queue.append(symbol)
                if fresh:
                    self._fresh.add(symbol)
        self._pump()

    def _pump(self) -> None:
        while self._queue and len(self._in_flight) < self._max_in_flight:
            self._fetch(self._queue.pop(0))

    def _fetch(self, symbol: str) -> None:
        self._in_flight.add(symbol)
        fresh = symbol in self._fresh
        self._fresh.discard(symbol)
        self._loader.submit(
            f"wl:{symbol}",
            partial(fetch_and_apply, self._engine, symbol, fresh=fresh, rollups=self._rollups),
            partial(self._on_result, symbol),
            partial(self._on_error, symbol),
            scoped=False,
            timeout_s=FETCH_TIMEOUT_S,
        )

    def _retry_gaps(self, symbol: str) -> None:
        if self._active and symbol in self.model.symbols():
            self._enqueue([symbol], fresh=True)

    def _settle(self, symbol: str) -> None:
        """End a fetch; an edit saved meanwhile may have been missed, so it goes next."""
        self._in_flight.discard(symbol)
        if symbol in self._refetch:
            self._refetch.discard(symbol)
            self._queue_first(symbol)

    def _queue_first(self, symbol: str) -> None:
        if symbol in self.model.symbols() and symbol not in self._queue:
            self._queue.insert(0, symbol)

    def _on_result(self, symbol: str, payload: tuple[ValuationInputs, ApplyResult]) -> None:
        self._settle(symbol)
        inputs, result = payload
        self._inputs[symbol] = inputs
        self._targets = result.file
        if inputs.gaps:
            QTimer.singleShot(self.gap_retry_ms, self, partial(self._retry_gaps, symbol))
        self.model.set_row(RowView(symbol, RowState.READY, result.rows[0], inputs))
        if self.editor is not None and self.editor.symbol == symbol and self.editor.isVisible():
            self.editor.set_inputs(inputs)
        if result.events:
            self.message.emit(alert_message(symbol, [e.kind for e in result.events]))
        elif result.error == SAVE_FAILED:
            self.message.emit(SAVE_FAILED)
        self.hitCountChanged.emit(self.model.hit_count())
        self.dataChanged.emit()
        self._pump()

    def _on_error(self, symbol: str, err: DataError) -> None:
        self._settle(symbol)
        row = failed_row(symbol, err.user_message, self._targets.get(symbol))
        self.model.set_row(RowView(symbol, RowState.ERROR, row))
        self.hitCountChanged.emit(self.model.hit_count())
        self.dataChanged.emit()
        self._pump()

    # --- editing --------------------------------------------------------------------------

    def _delete_target(self, symbol: str, indicator: Indicator) -> None:
        self.save_target(symbol, indicator, None, True)

    def _show_targets(self, st: SymbolTargets) -> None:
        self._targets = self._targets.put(st)
        self._show_cached(st.symbol)
        self.hitCountChanged.emit(self.model.hit_count())
        self.dataChanged.emit()

    def _show_cached(self, symbol: str) -> None:
        """Recompute a row from cached data (no fetch)."""
        inputs = self._inputs.get(symbol)
        if inputs is not None:
            row = evaluate(inputs, self._targets.get(symbol))
            self.model.set_row(RowView(symbol, RowState.READY, row, inputs))

    def _on_saved(self, symbol: str, file: TargetsFile) -> None:
        self._targets = file
        if symbol in self._in_flight:
            self._refetch.add(symbol)  # that fetch may have read the pre-edit file
        else:
            self._queue_first(symbol)  # a cache hit: applies the edited target straight away
            self._pump()

    def _on_save_failed(self, exc: Exception) -> None:
        if isinstance(exc, LockBusy):
            text = BUSY_MESSAGE
        elif isinstance(exc, ValueError):
            text = str(exc)
        else:
            log.error("Could not save target", exc_info=exc)
            text = "Could not save target — see log"
        self._targets = self._engine.snapshot().file
        for symbol in self.model.symbols():
            self._show_cached(symbol)
        self.hitCountChanged.emit(self.model.hit_count())
        self.message.emit(text)

    def _show_menu(self, index: QModelIndex, pos: QPoint) -> None:
        menu = self.build_context_menu(index)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(pos)
