"""AI analyst panel: IDLE -> RUNNING -> READY | ERROR. Every Run starts a fresh analysis."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.ai.analyst import Analyst
from obb_terminal.ai.context import collect_context
from obb_terminal.ai.models import HORIZON_LABELS, AnalystReport, CachedReport, Horizon, ReportKind
from obb_terminal.ai.schema import is_safe_url
from obb_terminal.data.market import DataError, MarketData
from obb_terminal.domain.models import Assumptions, Profile, ValuationInputs
from obb_terminal.domain.targets import SymbolTargets
from obb_terminal.journal.record import record_call
from obb_terminal.journal.store import Journal
from obb_terminal.ui.fmt import fmt_age, fmt_assumptions
from obb_terminal.ui.valuation.call_dialog import Choice, confirm_targets
from obb_terminal.ui.valuation.call_text import call_lines, case_lines, disagreement_lines
from obb_terminal.ui.worker import Loader

log = logging.getLogger(__name__)

DISCLAIMER = "Generated from public data by a model — not investment advice."
DEBATE_LABEL = "Debate"
DEBATE_TIP = (
    "Bull, bear and risk analysts argue the case in parallel, then a judge writes the report and "
    "where they disagree. Uses about four times the plan usage of one analysis."
)
IDLE_HINT = (
    "Run an AI analysis of this page's numbers plus recent news and research. "
    "It uses your Claude plan and takes a minute or two."
)


class AiState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    READY = "ready"
    ERROR = "error"


def format_report(report: AnalystReport, currency: str = "") -> str:
    """Plain-text rendering of a report (never interpreted as HTML)."""
    lines = [f"Stance: {report.stance.value.capitalize()}", "", report.summary, ""]
    if report.call is not None:
        lines += ["TRADE CALL", *call_lines(report.call, currency), ""]
    if report.debate is not None:
        lines += [*disagreement_lines(report.debate), ""]
    lines.append("PROS")
    lines += [f"+ {p.point} — {p.evidence}" for p in report.pros]
    lines += ["", "CONS"]
    lines += [f"− {p.point} — {p.evidence}" for p in report.cons]
    lines += ["", "WHAT MATTERS NEXT"]
    for horizon in Horizon:
        items = [w for w in report.future_watch if w.horizon is horizon]
        if items:
            lines.append(HORIZON_LABELS[horizon])
            lines += [f"• {w.item} — {w.why}" for w in items]
    if report.debate is not None:
        lines += ["", *case_lines(report.debate, currency)]
    return "\n".join(lines)


def _open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


# symbol -> (its watchlist targets, whether it is on the watchlist)
TargetsLookup = Callable[[str], tuple[SymbolTargets | None, bool]]


class AnalystPanel(QFrame):
    stateChanged = Signal(str)
    useAsTargets = Signal(str, object, object, bool)  # symbol, price level, thesis level, add

    def __init__(
        self,
        loader: Loader,
        analyst: Analyst,
        assumptions: Callable[[], Assumptions],
        *,
        timeout_s: float = 330.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        journal: Journal | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Section")
        self._journal = journal
        self._loader = loader
        self._analyst = analyst
        self._assumptions = assumptions
        self._timeout_s = timeout_s
        self._clock = clock
        self.open_url: Callable[[str], None] = _open_url
        self.targets_lookup: TargetsLookup | None = None  # set by MainWindow
        self.confirm_targets: Callable[..., Choice | None] = partial(confirm_targets, self)
        self._entry: CachedReport | None = None  # the report on show
        self.symbol: str | None = None
        self._inputs: ValuationInputs | None = None
        self._profile: Profile | None = None
        self._running: str | None = None  # the one symbol being analysed (global single-run guard)
        self._kind = ReportKind.SINGLE  # of the run in flight, or the last one (Retry repeats it)
        self._started = 0.0
        self.state = AiState.IDLE

        title = QLabel("AI ANALYST")
        title.setObjectName("SectionHeading")
        self.stack = QStackedWidget()

        idle = QWidget()
        self.hint = QLabel(IDLE_HINT)
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        self.hint.setWordWrap(True)
        self.run_button = QPushButton("Run analysis")
        self.run_button.clicked.connect(self.run)
        self.debate_button = QPushButton(DEBATE_LABEL)
        self.debate_button.setToolTip(DEBATE_TIP)
        self.debate_button.clicked.connect(self.debate)
        idle_layout = QVBoxLayout(idle)
        idle_layout.addWidget(self.hint)
        idle_buttons = QHBoxLayout()
        idle_buttons.addWidget(self.run_button)
        idle_buttons.addWidget(self.debate_button)
        idle_buttons.addStretch(1)
        idle_layout.addLayout(idle_buttons)
        idle_layout.addStretch(1)

        running = QWidget()
        self.progress = QLabel("")
        self.progress.setTextFormat(Qt.TextFormat.PlainText)
        self.progress.setWordWrap(True)
        running_layout = QVBoxLayout(running)
        running_layout.addWidget(self.progress)
        running_layout.addStretch(1)

        ready = QWidget()
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setAccessibleName("AI analysis")
        self.sources = QListWidget()
        self.sources.setMaximumHeight(90)
        self.sources.setAccessibleName("Sources")
        self.sources.itemClicked.connect(self._on_source)
        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        self.meta.setTextFormat(Qt.TextFormat.PlainText)
        self.meta.setWordWrap(True)
        self.again_button = QPushButton("Run analysis again")
        self.again_button.clicked.connect(self.run)
        self.use_button = QPushButton("Use as watchlist targets…")
        self.use_button.setAccessibleName("Use the AI trade call as watchlist targets")
        self.use_button.clicked.connect(self.use_as_targets)
        self.debate_again_button = QPushButton(DEBATE_LABEL)
        self.debate_again_button.setToolTip(DEBATE_TIP)
        self.debate_again_button.clicked.connect(self.debate)
        sources_label = QLabel("Sources")
        sources_label.setObjectName("Muted")
        ready_layout = QVBoxLayout(ready)
        ready_layout.setContentsMargins(0, 0, 0, 0)
        ready_layout.addWidget(self.report_text, 1)
        ready_layout.addWidget(sources_label)
        ready_layout.addWidget(self.sources)
        # On its own line: beside the buttons the wrapped text grew tall and squeezed the report.
        ready_layout.addWidget(self.meta)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.use_button)
        row.addWidget(self.again_button)
        row.addWidget(self.debate_again_button)
        ready_layout.addLayout(row)

        error = QWidget()
        self.error_label = QLabel("")
        self.error_label.setObjectName("PanelError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self.retry)
        error_layout = QVBoxLayout(error)
        error_layout.addWidget(self.error_label)
        error_layout.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        error_layout.addStretch(1)

        self._pages = {AiState.IDLE: idle, AiState.RUNNING: running, AiState.READY: ready, AiState.ERROR: error}
        for page in self._pages.values():
            self.stack.addWidget(page)
        disclaimer = QLabel(DISCLAIMER)
        disclaimer.setObjectName("Disclaimer")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(title)
        layout.addWidget(self.stack, 1)
        layout.addWidget(disclaimer)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        self._set_state(AiState.IDLE)

    # --- public API -----------------------------------------------------------------------

    def key(self, symbol: str) -> str:
        return f"ai:{symbol}"

    def set_symbol(self, symbol: str | None) -> None:
        """Show what we have for `symbol`: a run in flight, else its last report, else IDLE."""
        self.symbol, self._inputs, self._profile = symbol, None, None
        if symbol is None:
            self._set_state(AiState.IDLE)
        elif self._running == symbol and self._loader.is_pending(self.key(symbol)):
            self._show_running()
        elif (last := self._analyst.last(symbol)) is not None:
            self._show_report(last)
        else:
            self._show_idle()

    def set_inputs(self, inputs: ValuationInputs, profile: Profile | None) -> None:
        if inputs.symbol != self.symbol:
            return
        self._inputs, self._profile = inputs, profile
        self._update_buttons()

    def run(self) -> None:
        self._start(ReportKind.SINGLE)

    def debate(self) -> None:
        self._start(ReportKind.DEBATE)

    def _start(self, kind: ReportKind) -> None:
        symbol, inputs = self.symbol, self._inputs
        if symbol is None or inputs is None or self._running is not None:
            return
        hint = self._analyst.availability()
        if hint is not None:
            self._show_error(hint)
            return
        analyst, profile, assumptions = self._analyst, self._profile, self._assumptions()

        journal, clock = self._journal, self._clock

        def fetch(md: MarketData) -> CachedReport:
            ctx = collect_context(md, inputs, profile, assumptions)
            entry = analyst.debate(ctx) if kind is ReportKind.DEBATE else analyst.analyse(ctx)
            if journal is not None:
                record_call(journal, entry, inputs, clock())
            return entry

        self._running, self._kind = symbol, kind
        self._started = time.monotonic()
        self._loader.submit(
            self.key(symbol),
            fetch,
            partial(self._on_ok, symbol),
            partial(self._on_err, symbol),
            scoped=False,
            timeout_s=self._timeout_s if kind is ReportKind.SINGLE else 2 * self._timeout_s + 30,
        )
        self._show_running()

    def retry(self) -> None:
        self._start(self._kind)

    def use_as_targets(self) -> None:
        entry, lookup = self._entry, self.targets_lookup
        if entry is None or entry.report.call is None or lookup is None:
            return
        current, listed = lookup(entry.symbol)
        choice = self.confirm_targets(entry.symbol, entry.report.call, entry.currency or "", current, listed)
        if choice is not None:
            price, thesis, add = choice
            if price is not None or thesis is not None:
                self.useAsTargets.emit(entry.symbol, price, thesis, add)

    # --- internals ------------------------------------------------------------------------

    def _on_ok(self, symbol: str, entry: CachedReport) -> None:
        self._running = None
        self._tick_timer.stop()
        if symbol == self.symbol:
            self._show_report(entry)
        else:
            self._update_buttons()

    def _on_err(self, symbol: str, err: DataError) -> None:
        self._running = None
        self._tick_timer.stop()
        log.warning("AI analysis of %s failed: %s", symbol, err.user_message)
        if symbol == self.symbol:
            self._show_error(err.user_message)
        else:
            self._update_buttons()

    def _set_state(self, state: AiState) -> None:
        if state is not AiState.READY:
            self._entry = None
        self.state = state
        self.stack.setCurrentWidget(self._pages[state])
        self._update_buttons()
        self.stateChanged.emit(state.value)

    def _show_idle(self) -> None:
        self.hint.setText(self._analyst.availability() or IDLE_HINT)
        self._set_state(AiState.IDLE)

    def _show_running(self) -> None:
        self._tick()
        self._tick_timer.start()
        self._set_state(AiState.RUNNING)

    def _show_report(self, entry: CachedReport) -> None:
        self._entry = entry
        self.report_text.setPlainText(format_report(entry.report, entry.currency or ""))
        self.sources.clear()
        for source in entry.report.sources:
            if is_safe_url(source.url):
                item = QListWidgetItem(source.title or source.url)
                item.setData(Qt.ItemDataRole.UserRole, source.url)
                item.setToolTip(source.url)
                self.sources.addItem(item)
        age = fmt_age(self._clock() - entry.created_at)
        verb = "Debated" if entry.kind is ReportKind.DEBATE else "Analysed"
        self.meta.setText(
            f"{verb} {age} with {entry.model} (assumptions: {fmt_assumptions(entry.assumptions)})"
        )
        self._set_state(AiState.READY)

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self._set_state(AiState.ERROR)

    def _update_buttons(self) -> None:
        busy_elsewhere = self._running is not None and self._running != self.symbol
        enabled = self._inputs is not None and self._running is None
        tip = f"Another analysis is running ({self._running})" if busy_elsewhere else ""
        for button in (self.run_button, self.again_button, self.retry_button, self.debate_button,
                       self.debate_again_button):
            button.setEnabled(enabled)
            button.setToolTip(tip)
        call = None if self._entry is None else self._entry.report.call
        usable = call is not None and (call.entry_high is not None or call.thesis_break is not None)
        self.use_button.setEnabled(usable and self.targets_lookup is not None)

    def _tick(self) -> None:
        seconds = int(time.monotonic() - self._started)
        symbol, clock = self._running or self.symbol, f"{seconds // 60}:{seconds % 60:02d}"
        if self._kind is ReportKind.DEBATE:
            text = f"Debating {symbol} — bull, bear and risk cases, then the judge… {clock}"
        else:
            text = f"Analysing {symbol} — searching recent news and writing the report… {clock}"
        self.progress.setText(text)

    def _on_source(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.ItemDataRole.UserRole) or ""
        if is_safe_url(url):
            self.open_url(url)
