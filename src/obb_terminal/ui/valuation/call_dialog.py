"""Confirm turning an AI trade call into a watchlist Price target and thesis break."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from obb_terminal.ai.models import TradeCall
from obb_terminal.domain.targets import Indicator, SymbolTargets, format_level
from obb_terminal.ui.fmt import fmt_price

Choice = tuple[float | None, float | None, bool]  # price level, thesis level, add to watchlist


def _label(name: str, op: str, level: float | None, currency: str, now: str) -> str:
    if level is None:
        return f"{name}: none given"
    return f"{name} {op} {fmt_price(level, currency)} ({now})"


class UseAsTargetsDialog(QDialog):
    def __init__(
        self,
        symbol: str,
        call: TradeCall,
        currency: str,
        current: SymbolTargets | None,
        in_watchlist: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Use AI call as {symbol} watchlist targets")
        self._call = call
        price_now = None if current is None else current.target(Indicator.PRICE)
        thesis_now = None if current is None else current.thesis

        self.price_box = QCheckBox(_label(
            "Price target", "≤", call.entry_high, currency,
            "none set" if price_now is None else f"now ≤ {format_level(Indicator.PRICE, price_now.level)}",
        ))
        self.thesis_box = QCheckBox(_label(
            "Thesis break", "at", call.thesis_break, currency,
            "none set" if thesis_now is None else f"now {thesis_now.level:,.2f}",
        ))
        # Targets only fire for watchlist symbols, so adding is not optional.
        self.add_box = QCheckBox(f"Add {symbol} to the watchlist (alerts only run for watchlist symbols)")
        for box, level in ((self.price_box, call.entry_high), (self.thesis_box, call.thesis_break)):
            box.setEnabled(level is not None)
            box.setChecked(level is not None)
            box.toggled.connect(self._update_ok)
        self.add_box.setChecked(True)
        self.add_box.setEnabled(False)
        self.add_box.setHidden(in_watchlist)

        hint = QLabel("Edited targets are re-armed, so a level already reached alerts at once.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for widget in (self.price_box, self.thesis_box, self.add_box, hint, self.buttons):
            layout.addWidget(widget)
        self._update_ok()

    def choice(self) -> Choice:
        price = self._call.entry_high if self.price_box.isChecked() else None
        thesis = self._call.thesis_break if self.thesis_box.isChecked() else None
        return price, thesis, not self.add_box.isHidden() and self.add_box.isChecked()

    def _update_ok(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(self.price_box.isChecked() or self.thesis_box.isChecked())


def confirm_targets(
    parent: QWidget, symbol: str, call: TradeCall, currency: str, current: SymbolTargets | None,
    in_watchlist: bool,
) -> Choice | None:
    dialog = UseAsTargetsDialog(symbol, call, currency, current, in_watchlist, parent)
    return dialog.choice() if dialog.exec() == QDialog.DialogCode.Accepted else None
