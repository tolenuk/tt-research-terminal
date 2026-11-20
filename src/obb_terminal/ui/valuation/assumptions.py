"""Editable assumptions. Edits emit `changed(Assumptions)` after a 100 ms debounce; no fetch."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.domain.models import (
    Assumptions,
    EtfAssumptions,
    EtfScoreWeights,
    MethodWeights,
    ScoreWeights,
)

# key -> (label, minimum %, maximum %, step %, decimals)
RATES = {
    "discount_rate": ("Discount rate", 0.0, 30.0, 0.5, 2),
    "growth_1_5": ("Growth, years 1–5", -20.0, 50.0, 0.5, 2),
    "terminal_growth": ("Terminal growth", 0.0, 10.0, 0.25, 2),
    "dividend_growth": ("Dividend growth", -10.0, 30.0, 0.5, 2),
}
METHOD_WEIGHTS = {"dcf": "DCF", "analyst": "Analyst", "multiple": "Multiple", "dividend": "Dividend"}
SCORE_WEIGHTS = {
    "upside": "Upside",
    "analyst": "Analyst",
    "quality": "Quality",
    "value_growth": "Value vs growth",
    "momentum": "Momentum",
}
# ETF mode (key -> label, min %, max %, step %, decimals); the spins are "e_<key>" / "es_<key>".
ETF_RATES = {
    "bond_yield": ("10-year yield", 0.0, 20.0, 0.25, 2),
    "min_coverage": ("Minimum coverage", 0.0, 100.0, 5.0, 0),
}
ETF_SCORE_WEIGHTS = {
    "upside": "Upside",
    "vs_bonds": "Vs bonds",
    "cost": "Cost",
    "diversification": "Diversification",
    "income": "Income",
    "momentum": "Momentum",
}
DEBOUNCE_MS = 100

FORM_ROW_GAP = 1  # compact rows, so the whole Valuation page fits the default window


def _spin(minimum: float, maximum: float, step: float, decimals: int, name: str) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setSuffix(" %")
    spin.setKeyboardTracking(False)
    spin.setAccessibleName(name)
    return spin


class AssumptionsBox(QWidget):
    changed = Signal(object)  # Assumptions

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._defaults: Assumptions | None = None
        self.spins: dict[str, QDoubleSpinBox] = {}
        heading = QLabel("ASSUMPTIONS")
        heading.setObjectName("SectionHeading")
        rates = QFormLayout()
        rates.setVerticalSpacing(FORM_ROW_GAP)
        for key, (label, lo, hi, step, dp) in RATES.items():
            self.spins[key] = _spin(lo, hi, step, dp, label)
            rates.addRow(label, self.spins[key])
        methods = QFormLayout()
        methods.setVerticalSpacing(FORM_ROW_GAP)
        for key, label in METHOD_WEIGHTS.items():
            self.spins[f"w_{key}"] = _spin(0.0, 100.0, 5.0, 0, f"{label} method weight")
            methods.addRow(label, self.spins[f"w_{key}"])
        scores = QFormLayout()
        scores.setVerticalSpacing(FORM_ROW_GAP)
        for key, label in SCORE_WEIGHTS.items():
            self.spins[f"s_{key}"] = _spin(0.0, 100.0, 5.0, 0, f"{label} score weight")
            scores.addRow(label, self.spins[f"s_{key}"])
        self.reset_button = QPushButton("Reset")
        self.reset_button.setAccessibleName("Reset assumptions to defaults")
        self.reset_button.clicked.connect(self.reset)
        method_label, score_label = QLabel("Method weights"), QLabel("Score weights")
        method_label.setObjectName("Muted")
        score_label.setObjectName("Muted")
        weights = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(method_label)
        left.addLayout(methods)
        left.addStretch(1)
        right.addWidget(score_label)
        right.addLayout(scores)
        weights.addLayout(left)
        weights.addLayout(right)
        self.stock_part = QWidget()
        stock = QVBoxLayout(self.stock_part)
        stock.setContentsMargins(0, 0, 0, 0)
        stock.addLayout(rates)
        stock.addLayout(weights)
        self.etf_part = self._build_etf_part()
        self.etf_part.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(heading)
        layout.addWidget(self.stock_part)
        layout.addWidget(self.etf_part)
        layout.addWidget(self.reset_button)
        layout.addStretch(1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_changed)
        for spin in self.spins.values():
            spin.valueChanged.connect(self._schedule)

    def _build_etf_part(self) -> QWidget:
        part = QWidget()
        rates = self._etf_rates = QFormLayout()
        rates.setVerticalSpacing(FORM_ROW_GAP)
        for key, (label, lo, hi, step, dp) in ETF_RATES.items():
            self.spins[f"e_{key}"] = _spin(lo, hi, step, dp, label)
            rates.addRow(label, self.spins[f"e_{key}"])
        self.yield_note = QLabel("")
        self.yield_note.setObjectName("Muted")
        self.yield_note.setWordWrap(True)
        scores = self._etf_scores = QFormLayout()
        scores.setVerticalSpacing(FORM_ROW_GAP)
        for key, label in ETF_SCORE_WEIGHTS.items():
            self.spins[f"es_{key}"] = _spin(0.0, 100.0, 5.0, 0, f"{label} ETF score weight")
            scores.addRow(label, self.spins[f"es_{key}"])
        score_label = QLabel("Score weights")
        score_label.setObjectName("Muted")
        layout = QVBoxLayout(part)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(rates)
        layout.addWidget(self.yield_note)
        layout.addWidget(score_label)
        layout.addLayout(scores)
        return part

    def set_mode(self, etf: bool, yield_note: str = "", used: frozenset[str] | None = None) -> None:
        """ETF look-through fields, or the stock DCF/method fields. `used` = the ETF score
        weights this fund's kind uses (None: all); the others are hidden, not changed."""
        self.stock_part.setVisible(not etf)
        self.etf_part.setVisible(etf)
        self.yield_note.setText(yield_note)
        self.yield_note.setVisible(bool(yield_note))
        coverage_used = used is None or "upside" in used
        for key in ETF_SCORE_WEIGHTS:
            self._etf_scores.setRowVisible(self.spins[f"es_{key}"], used is None or key in used)
        self._etf_rates.setRowVisible(self.spins["e_min_coverage"], coverage_used)

    def set_defaults(self, a: Assumptions) -> None:
        self._defaults = a

    def set_values(self, a: Assumptions) -> None:
        """Show `a` without emitting `changed`."""
        values = {
            "discount_rate": a.discount_rate, "growth_1_5": a.growth_1_5,
            "terminal_growth": a.terminal_growth, "dividend_growth": a.dividend_growth,
            **{f"w_{k}": getattr(a.method_weights, k) for k in METHOD_WEIGHTS},
            **{f"s_{k}": getattr(a.score_weights, k) for k in SCORE_WEIGHTS},
            "e_bond_yield": a.etf.bond_yield, "e_min_coverage": a.etf.min_coverage,
            **{f"es_{k}": getattr(a.etf.score_weights, k) for k in ETF_SCORE_WEIGHTS},
        }
        for key, fraction in values.items():
            spin = self.spins[key]
            spin.blockSignals(True)
            spin.setValue(fraction * 100)
            spin.blockSignals(False)
        self._debounce.stop()

    def cancel_pending(self) -> None:
        """Drop a debounced edit that has not been emitted yet."""
        self._debounce.stop()

    def values(self) -> Assumptions:
        def pct(key: str) -> float:
            return self.spins[key].value() / 100

        return Assumptions(
            discount_rate=pct("discount_rate"),
            growth_1_5=pct("growth_1_5"),
            terminal_growth=pct("terminal_growth"),
            dividend_growth=pct("dividend_growth"),
            method_weights=MethodWeights(**{k: pct(f"w_{k}") for k in METHOD_WEIGHTS}),
            score_weights=ScoreWeights(**{k: pct(f"s_{k}") for k in SCORE_WEIGHTS}),
            etf=EtfAssumptions(
                bond_yield=pct("e_bond_yield"),
                min_coverage=pct("e_min_coverage"),
                score_weights=EtfScoreWeights(**{k: pct(f"es_{k}") for k in ETF_SCORE_WEIGHTS}),
            ),
        )

    def reset(self) -> None:
        if self._defaults is None:
            return
        self.set_values(self._defaults)
        self.changed.emit(self._defaults)

    def _schedule(self, _value: float) -> None:
        self._debounce.start()

    def _emit_changed(self) -> None:
        self.changed.emit(self.values())
