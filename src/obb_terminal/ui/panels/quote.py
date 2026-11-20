"""Quote & key statistics. Stats are optional (ETFs often have none)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QWidget

from obb_terminal.data.market import MarketData, NoData
from obb_terminal.domain.models import Quote, Stats
from obb_terminal.ui.fmt import (
    DASH,
    direction,
    fmt_big,
    fmt_change,
    fmt_int,
    fmt_num,
    fmt_pct,
    fmt_price,
    fmt_range,
)
from obb_terminal.ui.panels.base import Panel
from obb_terminal.ui.theme import set_dir
from obb_terminal.ui.worker import Loader

ROWS = ("Last", "Change", "Open", "Day range", "52w range", "Volume", "Avg volume",
        "Market cap", "P/E", "Div yield", "Beta")


@dataclass(frozen=True)
class QuoteView:
    quote: Quote
    stats: Stats | None


class QuotePanel(Panel):
    name = "quote"

    def __init__(self, loader: Loader, parent: QWidget | None = None) -> None:
        super().__init__(loader, "QUOTE / KEY STATS", parent)

    def build_content(self) -> QWidget:
        content = QWidget()
        form = QFormLayout(content)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setVerticalSpacing(7)
        self.values: dict[str, QLabel] = {}
        for row in ROWS:
            label = QLabel(row)
            label.setObjectName("Muted")
            value = QLabel(DASH)
            value.setObjectName("Value")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            form.addRow(label, value)
            self.values[row] = value
        return content

    def make_fetch(self, symbol: str) -> Callable[[MarketData], QuoteView]:
        def run(md: MarketData) -> QuoteView:
            quote = md.quote(symbol)
            try:
                stats: Stats | None = md.stats(symbol)
            except NoData:
                stats = None
            return QuoteView(quote, stats)

        return run

    def render(self, view: QuoteView) -> None:
        q, s = view.quote, view.stats
        ccy = q.currency.value
        v = self.values
        v["Last"].setText(fmt_price(q.last, ccy))
        v["Change"].setText(fmt_change(q.change, q.change_pct))
        set_dir(v["Change"], direction(q.change))
        v["Open"].setText(fmt_num(q.open))
        v["Day range"].setText(fmt_range(q.day_low, q.day_high))
        v["52w range"].setText(fmt_range(q.year_low, q.year_high))
        v["Volume"].setText(fmt_int(q.volume))
        v["Avg volume"].setText(fmt_int(q.avg_volume))
        market_cap = s.market_cap if s else None
        v["Market cap"].setText(DASH if market_cap is None else f"{fmt_big(market_cap)} {ccy}")
        v["P/E"].setText(fmt_num(s.pe_ratio if s else None))
        v["Div yield"].setText(fmt_pct(s.dividend_yield if s else None))
        v["Beta"].setText(fmt_num(s.beta if s else None))
