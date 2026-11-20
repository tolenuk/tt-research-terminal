"""Outlook score: the big number, its band and one bar per component."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from obb_terminal.domain.models import Component, Score, ScoreBand
from obb_terminal.domain.scoring import LABELS
from obb_terminal.ui.fmt import DASH, fmt_pct
from obb_terminal.ui.theme import set_dir

_BAND_DIR = {ScoreBand.FAVOURABLE: "up", ScoreBand.MIXED: "flat", ScoreBand.UNFAVOURABLE: "down"}
ETF_ONLY = (Component.VS_BONDS, Component.COST, Component.DIVERSIFICATION, Component.INCOME)
NOT_A_PROBABILITY = "A transparent 0–100 score, not a probability that the price rises."


class ScoreView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        heading = QLabel("OUTLOOK SCORE")
        heading.setObjectName("SectionHeading")
        self.big = QLabel(DASH)
        self.big.setObjectName("BigScore")
        self.big.setTextFormat(Qt.TextFormat.PlainText)
        self.band = QLabel("")
        self.band.setTextFormat(Qt.TextFormat.PlainText)
        note = QLabel(NOT_A_PROBABILITY)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        grid = QGridLayout()
        self.bars: dict[Component, QProgressBar] = {}
        self.values: dict[Component, QLabel] = {}
        self.labels: dict[Component, QLabel] = {}
        for row, key in enumerate(Component):
            label = QLabel(LABELS[key])
            label.setObjectName("Muted")
            bar = QProgressBar()
            bar.setObjectName("ScoreBar")
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setAccessibleName(f"{LABELS[key]} score")
            value = QLabel(DASH)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            grid.addWidget(label, row, 0)
            grid.addWidget(bar, row, 1)
            grid.addWidget(value, row, 2)
            self.bars[key], self.values[key], self.labels[key] = bar, value, label
        for key in ETF_ONLY:  # until a score says otherwise, the page shows the stock rows
            for widget in (self.labels[key], self.bars[key], self.values[key]):
                widget.hide()
        grid.setColumnStretch(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(heading)
        layout.addWidget(self.big)
        layout.addWidget(self.band)
        layout.addLayout(grid)
        layout.addWidget(note)
        layout.addStretch(1)

    def show_score(self, s: Score) -> None:
        if s.value is None:
            self.big.setText(DASH)
            self.band.setText(s.reason or "")
            set_dir(self.band, "flat")
        else:
            self.big.setText(f"{s.value:.0f}")
            self.band.setText(s.band.value if s.band else "")
            set_dir(self.band, _BAND_DIR[s.band] if s.band else "flat")
        shown = {c.key for c in s.components}
        for key in Component:
            self.bars[key].setValue(0)
            self.values[key].setText(DASH)
            self.bars[key].setAccessibleDescription(f"{LABELS[key]} score: no data")
            # Stocks and ETFs score on different components: show only this score's rows.
            for widget in (self.labels[key], self.bars[key], self.values[key]):
                widget.setVisible(key in shown)
        for c in s.components:
            bar, value = self.bars[c.key], self.values[c.key]
            if c.value is None:
                text = f"n/a — {c.reason or 'no data'}"
                value.setText(text)
                bar.setAccessibleDescription(f"{LABELS[c.key]} score: {text}")
            else:
                bar.setValue(round(c.value))
                weight = f" · weight {fmt_pct(c.weight, signed=False)}" if c.weight > 0 else " · not weighted"
                value.setText(f"{c.value:.0f}{weight}")
                bar.setAccessibleDescription(f"{LABELS[c.key]} score {c.value:.0f} out of 100{weight.replace(' · ', ', ')}")
