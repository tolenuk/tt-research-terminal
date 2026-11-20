"""Target editor: level, alert on/off, delete, with a live preview of the buy-at price."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.domain.models import ValuationInputs
from obb_terminal.domain.targets import (
    LABEL,
    OP,
    YIELDS,
    Indicator,
    Target,
    cell,
    default_level,
)
from obb_terminal.ui.watchlist_tab.model import fmt_move, fmt_value

MAX_LEVEL = 1e9


class TargetEditor(QDialog):
    saved = Signal(str, object, float, bool)  # symbol, Indicator, level (yields as fractions), enabled
    deleted = Signal(str, object)  # symbol, Indicator

    def __init__(
        self,
        symbol: str,
        indicator: Indicator,
        inputs: ValuationInputs | None,
        current: Target | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.symbol, self.indicator = symbol, indicator
        self._inputs = inputs
        self._yield = indicator in YIELDS
        self.setWindowTitle(f"{symbol} — {LABEL[indicator]} target")

        self.level = QDoubleSpinBox()
        self.level.setDecimals(2)
        self.level.setRange(0.01, 100.0 if self._yield else MAX_LEVEL)
        self.level.setSuffix(" %" if self._yield else "")
        self.level.setPrefix(f"{OP[indicator]} ")
        self.level.setAccessibleName(f"{LABEL[indicator]} target level")
        self.enabled = QCheckBox("Alert when hit")
        self.enabled.setChecked(current.enabled if current else True)
        self.preview = QLabel("")
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setWordWrap(True)
        self.preview.setAccessibleName("Target preview")

        start = current.level if current else default_level(indicator, self._current_value())
        self.level.setValue(self._display(start) if start is not None else 1.0)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.delete_button = QPushButton("Delete target")
        self.delete_button.setEnabled(current is not None)
        self.buttons.addButton(self.delete_button, QDialogButtonBox.ButtonRole.DestructiveRole)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        self.delete_button.clicked.connect(self._delete)

        form = QFormLayout()
        form.addRow(f"{LABEL[indicator]}", self.level)
        form.addRow("", self.enabled)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.preview)
        layout.addWidget(self.buttons)

        self.level.valueChanged.connect(self._update_preview)
        self.enabled.toggled.connect(self._update_preview)
        self._update_preview()
        self.level.setFocus()
        self.level.selectAll()

    def set_inputs(self, inputs: ValuationInputs | None) -> None:
        self._inputs = inputs
        self._update_preview()

    def value(self) -> float:
        """The level as stored: yields as fractions."""
        v = self.level.value()
        return v / 100 if self._yield else v

    # --- internals ------------------------------------------------------------------------

    def _display(self, level: float) -> float:
        return level * 100 if self._yield else level

    def _current_value(self) -> float | None:
        if self._inputs is None:
            return None
        return cell(self._inputs, self.indicator, None).current

    def _update_preview(self) -> None:
        if self._inputs is None:
            self.preview.setText("No data yet — the buy-at price appears once prices load.")
            return
        c = cell(self._inputs, self.indicator, Target(self.indicator, self.value(), True))
        ccy = self._inputs.currency.value
        if c.reason is not None:
            self.preview.setText(f"Current {LABEL[self.indicator]}: n/a — {c.reason}")
            return
        assert c.buy_at is not None
        self.preview.setText(
            f"Current {LABEL[self.indicator]} {fmt_value(self.indicator, c.current)} · "
            f"price {self._inputs.price:,.2f} {ccy}\n"
            f"Buy at {c.buy_at:,.2f} {ccy} · {fmt_move(c.move, c.hit)}"
        )

    def _save(self) -> None:
        self.saved.emit(self.symbol, self.indicator, self.value(), self.enabled.isChecked())
        self.accept()

    def _delete(self) -> None:
        self.deleted.emit(self.symbol, self.indicator)
        self.accept()
