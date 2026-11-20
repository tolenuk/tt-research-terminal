"""The VALUATION page: fair value, score, assumptions, KPIs and the AI analyst (2×2 grid)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.ai.analyst import Analyst
from obb_terminal.data.market import DataError, MarketData
from obb_terminal.data.rollup import (
    MAX_PARALLEL,
    TIMED_OUT,
    RollupMemo,
    attach_cached,
    value_holding,
)
from obb_terminal.domain.etf import etf_kind, holding_value, rollup_settled, used_weights
from obb_terminal.domain.kpis import kpis
from obb_terminal.domain.models import (
    Assumptions,
    EtfKind,
    Holding,
    HoldingValue,
    Kpi,
    Profile,
    Score,
    Valuation,
    ValuationInputs,
)
from obb_terminal.domain.scoring import score
from obb_terminal.domain.valuation import default_assumptions, value
from obb_terminal.journal.record import record_page
from obb_terminal.journal.store import Journal
from obb_terminal.ui.panels.base import Panel, PanelState
from obb_terminal.ui.valuation.ai_panel import AnalystPanel
from obb_terminal.ui.valuation.assumptions import AssumptionsBox
from obb_terminal.ui.valuation.fair_value import FairValueView
from obb_terminal.ui.valuation.kpi_table import KpiTable
from obb_terminal.ui.valuation.report_export import ReportSnapshot, default_report_path, export_pdf
from obb_terminal.ui.valuation.score import ScoreView
from obb_terminal.ui.worker import Loader

log = logging.getLogger(__name__)

COMPUTE_ERROR = "Could not compute — see log"
HOLDING_TIMEOUT_S = 60.0
LIVE_YIELD_NOTE = "Live 10-year Treasury yield (^TNX)"
DEFAULT_YIELD_NOTE = "Default: Yahoo has no 10-year yield for {currency}"
YIELD_UNAVAILABLE_NOTE = "10-year yield unavailable right now: using the default"


def _section(widget: QWidget) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Section")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    return frame


class ValuationPage(Panel):
    name = "valuation"
    timeout_s = 50.0
    exported = Signal(str)  # path of the saved PDF

    def __init__(
        self,
        loader: Loader,
        analyst: Analyst,
        parent: QWidget | None = None,
        *,
        ai_timeout_s: float = 330.0,
        journal: Journal | None = None,
        rollups: RollupMemo | None = None,
    ) -> None:
        # build_content (called by Panel.__init__) needs these.
        self._analyst = analyst
        self._journal = journal  # None: nothing is recorded
        self._rollups = rollups if rollups is not None else RollupMemo()
        # The running top-holdings roll-up: the ETF it is for, its holdings, results by index.
        self._rollup_symbol: str | None = None
        self._rollup_holdings: tuple[Holding, ...] = ()
        self._rollup_results: dict[int, HoldingValue] = {}
        self._rollup_next = 0
        self._rollup_run = 0  # bumped by every start/stop: results of an older run are ignored
        self._ai_timeout_s = ai_timeout_s
        self._active = False
        self._wanted: Profile | None = None
        self._loaded_symbol: str | None = None
        self.inputs: ValuationInputs | None = None
        self._assumptions: Assumptions | None = None
        self._valuation: Valuation | None = None
        self._score: Score | None = None
        self._kpis: tuple[Kpi, ...] = ()
        self.now: Callable[[], datetime] = lambda: datetime.now().astimezone()
        self.documents: Path | None = None  # None = QStandardPaths Documents (tests override)
        super().__init__(loader, "VALUATION", parent)
        self.export_button = QPushButton("Export report")
        self.export_button.setAccessibleName("Export valuation report as PDF")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_report)
        self.header.addWidget(self.export_button)

    # --- Panel hooks ----------------------------------------------------------------------

    def build_content(self) -> QWidget:
        content = QWidget()
        self.fair_value = FairValueView()
        self.score_view = ScoreView()
        self.assumptions_box = AssumptionsBox()
        self.kpi_table = KpiTable()
        self.analyst_panel = AnalystPanel(
            self._loader, self._analyst, self.assumptions_box.values, timeout_s=self._ai_timeout_s,
            journal=self._journal,
        )
        self.compute_error = QLabel(COMPUTE_ERROR)
        self.compute_error.setObjectName("InlineError")
        self.compute_error.hide()
        self.export_error = QLabel("")
        self.export_error.setObjectName("InlineError")
        self.export_error.setTextFormat(Qt.TextFormat.PlainText)
        self.export_error.hide()
        self.assumptions_box.changed.connect(self._recompute)

        bottom_left = QWidget()
        bl = QHBoxLayout(bottom_left)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(_section(self.assumptions_box), 2)
        kpi_section = QWidget()
        kl = QVBoxLayout(kpi_section)
        kl.setContentsMargins(4, 4, 4, 4)
        kpi_heading = QLabel("KEY INDICATORS")
        kpi_heading.setObjectName("SectionHeading")
        kl.addWidget(kpi_heading)
        kl.addWidget(self.kpi_table)
        kl.addStretch(1)
        bl.addWidget(_section(kpi_section), 3)

        grid = QGridLayout()
        grid.setSpacing(2)
        grid.addWidget(_section(self.fair_value), 0, 0)
        grid.addWidget(_section(self.score_view), 0, 1)
        grid.addWidget(bottom_left, 1, 0)
        grid.addWidget(self.analyst_panel, 1, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setRowStretch(1, 1)  # the tables and the AI report take the spare height
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.compute_error)
        layout.addWidget(self.export_error)
        layout.addLayout(grid, 1)
        # Every table shows all its rows; in a short window the page scrolls instead of clipping.
        scroll = QScrollArea()
        scroll.setObjectName("ValuationScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def make_fetch(self, symbol: str) -> Callable[[MarketData], ValuationInputs]:
        journal, now, rollups = self._journal, self.now, self._rollups

        def fetch(md: MarketData) -> ValuationInputs:
            inputs = attach_cached(md.valuation_inputs(symbol), rollups)
            if journal is not None:
                record_page(journal, inputs, now())  # first load of the day; worker thread
            return inputs

        return fetch

    def render(self, inputs: ValuationInputs) -> None:
        self.inputs = inputs
        defaults = default_assumptions(inputs)
        self.assumptions_box.set_mode(
            inputs.etf is not None, self._yield_note(inputs),
            None if inputs.etf is None else used_weights(etf_kind(inputs.etf.profile)),
        )
        self.assumptions_box.set_defaults(defaults)
        self.assumptions_box.set_values(defaults)
        self._kpis = tuple(kpis(inputs))
        self.kpi_table.show_kpis(list(self._kpis))
        self._start_rollup(inputs)
        self._recompute(defaults)
        self.analyst_panel.set_inputs(self.inputs, self.profile)  # with the roll-up if it finished
        self.export_error.hide()

    # --- public API -----------------------------------------------------------------------

    @property
    def valuation(self) -> Valuation | None:
        return self._valuation

    @property
    def score(self) -> Score | None:
        return self._score

    def set_profile(self, profile: Profile) -> None:
        """The selected symbol changed (or was re-selected). Loads only while active."""
        self._wanted = profile
        if profile.symbol != self._loaded_symbol:
            self._loaded_symbol = None
            self._reset()
            self.analyst_panel.set_symbol(profile.symbol)
        elif self.state is PanelState.LOADING:
            # The host bumps the loader generation on every profile, silently dropping the
            # in-flight scoped result; forget the symbol so _maybe_load re-submits it.
            self._loaded_symbol = None
        self._maybe_load()

    def load(self, symbol: str, profile: Profile | None) -> None:
        # Every load (initial, retry, reload) records its symbol so tab flips and re-selects
        # of the same symbol never refetch or reset the user's edited assumptions.
        self._loaded_symbol = symbol
        super().load(symbol, profile)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._maybe_load()

    def export_report(self) -> Path | None:
        if self.inputs is None or self.profile is None or self._valuation is None or self._score is None:
            self.export_error.setText("Nothing to export yet")
            self.export_error.show()
            return None
        snapshot = ReportSnapshot(
            profile=self.profile,
            inputs=self.inputs,
            assumptions=self._assumptions or default_assumptions(self.inputs),
            valuation=self._valuation,
            score=self._score,
            kpis=self._kpis,
            ai=self._analyst.last(self.inputs.symbol),
            gauge_png=self.fair_value.gauge.png_bytes(),
            generated_at=self.now(),
        )
        path = default_report_path(self.inputs.symbol, snapshot.generated_at, self.documents)
        try:
            export_pdf(snapshot, path)
        except OSError as exc:
            log.exception("Could not export report to %s", path)
            self.export_error.setText(f"Could not save report: {exc.strerror or exc}")
            self.export_error.show()
            return None
        self.export_error.hide()
        self.exported.emit(str(path))
        return path

    # --- internals ------------------------------------------------------------------------

    def _reset(self) -> None:
        self._stop_rollup()
        self.fair_value.show_rollup(None)
        self.assumptions_box.cancel_pending()
        self.clear()
        self.inputs = None
        self._assumptions = self._valuation = self._score = None
        self._kpis = ()
        self.export_button.setEnabled(False)

    def _maybe_load(self) -> None:
        profile = self._wanted
        if self._active and profile is not None and profile.symbol != self._loaded_symbol:
            self.load(profile.symbol, profile)

    def _on_err(self, err: DataError, silent: bool) -> None:
        self._loaded_symbol = None  # the next activation retries
        super()._on_err(err, silent)

    def _recompute(self, a: Assumptions) -> None:
        """GUI thread, no fetch: edits re-run the pure domain functions only."""
        if self.inputs is None:
            return
        try:
            v = value(self.inputs, a)
            s = score(self.inputs, v, a)
            self.fair_value.show_valuation(v, self.inputs.analyst)
            self.score_view.show_score(s)
            if self.inputs.etf is not None:  # the gap vs the 10-year follows the edited yield
                self._kpis = tuple(kpis(self.inputs, a.etf.bond_yield))
                self.kpi_table.show_kpis(list(self._kpis))
        except Exception:
            log.exception("Valuation recompute failed for %s", self.inputs.symbol)
            self.compute_error.show()
            return
        self.compute_error.hide()
        self._assumptions, self._valuation, self._score = a, v, s
        self.export_button.setEnabled(True)

    # --- ETF top-holdings roll-up ---------------------------------------------------------

    @staticmethod
    def _yield_note(inputs: ValuationInputs) -> str:
        if inputs.etf is None:
            return ""
        if "ten_year" in inputs.gaps:
            return YIELD_UNAVAILABLE_NOTE
        if inputs.etf.ten_year is not None:
            return LIVE_YIELD_NOTE
        return DEFAULT_YIELD_NOTE.format(currency=inputs.currency.value)

    def _start_rollup(self, inputs: ValuationInputs) -> None:
        """Value an equity ETF's top holdings in the background, MAX_PARALLEL at a time."""
        self._stop_rollup()
        etf = inputs.etf
        if etf is None or etf_kind(etf.profile) is not EtfKind.EQUITY:
            self.fair_value.show_rollup(None)
            return
        if etf.holding_values is not None:
            self.fair_value.show_rollup(etf.holding_values, len(etf.holding_values))
            return
        holdings = etf.profile.holdings
        self._rollup_symbol, self._rollup_holdings = inputs.symbol, holdings
        self.fair_value.show_rollup((), len(holdings))
        self.fair_value.set_rollup_progress((0, len(holdings)))
        if not holdings:
            self._finish_rollup()
            return
        for slot in range(min(MAX_PARALLEL, len(holdings))):
            self._value_next(slot)

    def _stop_rollup(self) -> None:
        self._rollup_run += 1
        self._rollup_symbol, self._rollup_holdings = None, ()
        self._rollup_results, self._rollup_next = {}, 0
        self.fair_value.set_rollup_progress(None)

    def _value_next(self, slot: int) -> None:
        index = self._rollup_next
        if index >= len(self._rollup_holdings):
            return
        self._rollup_next += 1
        holding, run = self._rollup_holdings[index], self._rollup_run
        # Unscoped: the host bumps the loader generation on every profile, even a re-select of
        # the same symbol, which would silently drop scoped results and stall the roll-up. The
        # run token filters stale results instead.
        self._loader.submit(
            f"{self.name}.rollup.{slot}",
            lambda md, h=holding: value_holding(md, h),
            lambda hv, i=index: self._on_holding(run, slot, i, hv),
            lambda err, i=index, h=holding: self._on_holding(
                run, slot, i, holding_value(
                    h, None, TIMED_OUT if err.user_message == "Timed out" else err.user_message, transient=True)
            ),
            scoped=False,
            timeout_s=HOLDING_TIMEOUT_S,
        )

    def _on_holding(self, run: int, slot: int, index: int, hv: HoldingValue) -> None:
        if run != self._rollup_run:
            return  # a roll-up that was stopped or restarted since
        self._rollup_results[index] = hv
        done, total = len(self._rollup_results), len(self._rollup_holdings)
        self.fair_value.set_rollup_progress((done, total))
        self.fair_value.show_rollup(tuple(self._rollup_results[i] for i in sorted(self._rollup_results)), total)
        if done >= total:
            self._finish_rollup()
            return
        self._value_next(slot)
        self._recompute(self._assumptions or self.assumptions_box.values())

    def _finish_rollup(self) -> None:
        inputs = self.inputs
        if inputs is None or inputs.etf is None:
            return
        values = tuple(self._rollup_results[i] for i in sorted(self._rollup_results))
        self._stop_rollup()
        self.inputs = replace(inputs, etf=replace(inputs.etf, holding_values=values))
        self.fair_value.show_rollup(values, len(values))
        self._recompute(self._assumptions or self.assumptions_box.values())
        self.analyst_panel.set_inputs(self.inputs, self.profile)
        if rollup_settled(values):  # a passing failure would otherwise stick for hours
            self._rollups.put(inputs.symbol, values)
            self._record(self.inputs)

    def _record(self, inputs: ValuationInputs) -> None:
        journal, now = self._journal, self.now
        if journal is None:
            return
        self._loader.submit(
            f"{self.name}.record",
            lambda md: record_page(journal, inputs, now()),
            lambda _ok: None,
            lambda err: log.warning("Could not record %s: %s", inputs.symbol, err.user_message),
            scoped=False,
        )
