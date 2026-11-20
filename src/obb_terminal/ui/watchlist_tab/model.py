"""The heat-matrix table model: one row per watchlist symbol, 15 columns, plus a sort proxy
that keeps empty values last in both sort orders. The delegate draws what its roles describe."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor

from obb_terminal.domain.models import Quote, ValuationInputs
from obb_terminal.domain.targets import (
    HEAT_CAP,
    LABEL,
    OP,
    YIELDS,
    Cell,
    Heat,
    Indicator,
    RowEvaluation,
    format_level,
    heat,
)
from obb_terminal.ui.fmt import ARROWS, DASH, direction, fmt_pct
from obb_terminal.ui.theme import TOKENS
from obb_terminal.ui.watchlist_tab.heat import heat_fill


class Col(IntEnum):
    SYMBOL = 0
    TREND = 1
    PRICE = 2
    READINESS = 3
    BUY_AT = 4
    PRICE_TARGET = 5
    THESIS = 6
    PE = 7
    EV_EBITDA = 8
    PB = 9
    FCF_YIELD = 10
    FAIR = 11
    MARGIN = 12
    POS_52W = 13
    DIV_YIELD = 14


# The labels above the column titles; the symbol column stands alone.
COLUMN_GROUPS: tuple[tuple[str, tuple[Col, ...]], ...] = (
    ("", (Col.SYMBOL,)),
    ("Price", (Col.TREND, Col.PRICE)),
    ("Targets", (Col.READINESS, Col.BUY_AT, Col.PRICE_TARGET, Col.THESIS)),
    ("Valuation", (Col.PE, Col.EV_EBITDA, Col.PB, Col.FCF_YIELD, Col.FAIR, Col.MARGIN, Col.POS_52W)),
    ("Income", (Col.DIV_YIELD,)),
)


INDICATOR_COL: dict[Col, Indicator] = {
    Col.PRICE_TARGET: Indicator.PRICE,
    Col.PE: Indicator.PE,
    Col.EV_EBITDA: Indicator.EV_EBITDA,
    Col.PB: Indicator.PB,
    Col.DIV_YIELD: Indicator.DIV_YIELD,
    Col.FCF_YIELD: Indicator.FCF_YIELD,
}
COL_OF: dict[Indicator, Col] = {ind: col for col, ind in INDICATOR_COL.items()}
HEADERS: dict[Col, str] = {
    Col.SYMBOL: "Symbol",
    Col.TREND: "1M",
    Col.PRICE: "Price",
    Col.BUY_AT: "Buy-at",
    Col.READINESS: "Readiness",
    **{col: LABEL[ind] for col, ind in INDICATOR_COL.items()},
    Col.PRICE_TARGET: "Price target",
    Col.POS_52W: "52-wk",
    Col.FAIR: "Fair value",
    Col.MARGIN: "Margin",
    Col.THESIS: "Thesis break",
}
BUY_AT_NOTE = "The price at which all of this stock's active targets are met."
EBITDA_NOTE = "EV/EBITDA uses the latest annual EBITDA, so it can differ from Yahoo's TTM ratio."

SortRole = Qt.ItemDataRole.UserRole + 1
HeatRole = Qt.ItemDataRole.UserRole + 2
SymbolRole = Qt.ItemDataRole.UserRole + 3
IndicatorRole = Qt.ItemDataRole.UserRole + 4
SubtextRole = Qt.ItemDataRole.UserRole + 5  # a second line that is not part of the cell's text
SubtextColourRole = Qt.ItemDataRole.UserRole + 6
EdgeRole = Qt.ItemDataRole.UserRole + 7  # the colour of the marker at the cell's edge
ReadinessRole = Qt.ItemDataRole.UserRole + 8  # 0..1, how full the readiness bar is
TrendRole = Qt.ItemDataRole.UserRole + 9  # one month of closes

_HEAT_TOKEN = {Heat.HIT: "up", Heat.NEAR: "amber", Heat.FAR: "amber_dim"}
_DIRECTION_TOKEN = {"up": "up", "down": "down", "flat": "muted"}
HIT_FILL_INTENSITY = 0.5  # how strongly a hit target's cell is tinted
READINESS_NOTE = f"The bar fills as the price nears the target; it is empty at {HEAT_CAP:.0%} or more away."


def indicator_at(index) -> Indicator | None:
    """The indicator of a cell. Qt returns IndicatorRole's StrEnum as a plain str; restore it."""
    value = index.data(IndicatorRole)
    return None if value is None else Indicator(value)


class RowState(StrEnum):
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RowView:
    symbol: str
    state: RowState
    row: RowEvaluation | None = None
    inputs: ValuationInputs | None = None  # the data behind `row`; None for ERROR / LOADING


def _num(value: float, dp: int = 2) -> str:
    return f"{value:,.{dp}f}"


def fmt_value(ind: Indicator, value: float | None) -> str:
    """An indicator's current value: price, multiple (x) or yield (%)."""
    if value is None:
        return DASH
    if ind in YIELDS:
        return fmt_pct(value)
    if ind is Indicator.PRICE:
        return _num(value)
    return f"{_num(value)}x"


def fmt_move(move: float | None, hit: bool) -> str:
    if move is None:
        return DASH
    return "✓ hit" if hit else f"↓ {abs(move) * 100:.1f}%"


def cell_text(c: Cell) -> str:
    current = fmt_value(c.indicator, c.current)
    if c.target is None:
        return DASH if c.reason is not None else current
    target = f"{OP[c.indicator]} {format_level(c.indicator, c.target.level)}"
    if c.reason is not None:  # the user's target is still there, but cannot be measured
        return f"{DASH}  {target}\n{c.reason}"
    buy_at = DASH if c.buy_at is None else _num(c.buy_at)
    if not c.target.enabled:
        return f"{current}  {target}\n@ {buy_at}  (off)"
    return f"{current}  {target}\n@ {buy_at}  {fmt_move(c.move, c.hit)}"


def cell_sentence(symbol: str, currency: str, c: Cell) -> str:
    """The full meaning of a cell, for tooltips and screen readers."""
    name = f"{symbol} {LABEL[c.indicator]}"
    if c.reason is not None:
        sentence = f"{name}: not available — {c.reason}"
        if c.target is not None:
            sentence += f"; target {OP[c.indicator]} {format_level(c.indicator, c.target.level)}"
    elif c.target is None:
        sentence = f"{name}: current {fmt_value(c.indicator, c.current)}; no target set"
    else:
        parts = [
            f"{name}: current {fmt_value(c.indicator, c.current)}",
            f"target {OP[c.indicator]} {format_level(c.indicator, c.target.level)}",
        ]
        if c.buy_at is not None:
            parts.append(f"buy at {_num(c.buy_at)} {currency}")
        if not c.target.enabled:
            parts.append("alert off")
        elif c.hit:
            parts.append("target hit")
        elif c.move is not None:
            parts.append(f"needs a {abs(c.move) * 100:.1f}% drop")
        sentence = "; ".join(parts)
    if c.indicator is Indicator.EV_EBITDA:
        sentence += f". {EBITDA_NOTE}"
    return sentence


def thesis_text(row: RowEvaluation) -> str:
    if row.thesis_level is None or row.thesis_move is None:
        return DASH
    if row.thesis_broken:
        return f"{_num(row.thesis_level)}  ✗ broken"
    return f"{_num(row.thesis_level)}  ↓ {abs(row.thesis_move) * 100:.1f}%"


def thesis_sentence(symbol: str, currency: str, row: RowEvaluation) -> str:
    if row.thesis_level is None or row.thesis_move is None:
        return f"{symbol}: no thesis break set"
    head = f"{symbol} thesis break at {_num(row.thesis_level)} {currency}"
    if row.thesis_broken:
        return f"{head}: broken — the price is at or below it"
    return f"{head}: the price must fall {abs(row.thesis_move) * 100:.1f}% to reach it"


def readiness_text(row: RowEvaluation) -> str:
    if row.all_hit:
        return "✓ all targets"
    return DASH if row.m_all is None else f"↓ {abs(row.m_all) * 100:.1f}%"


def readiness_fraction(m_all: float | None, all_hit: bool) -> float | None:
    """How full the readiness bar is; None when the row has no active target."""
    if all_hit:
        return 1.0
    return None if m_all is None else 1.0 - min(abs(m_all), HEAT_CAP) / HEAT_CAP


def month_return(closes: tuple[float, ...]) -> float | None:
    return closes[-1] / closes[0] - 1 if closes[0] else None


def summary_text(views: Iterable[RowView]) -> str:
    """How many stocks stand where, e.g. "1 ready, 2 near, 3 without active targets"."""
    counts = dict.fromkeys(("ready", "near", "far", "without active targets", "loading", "failed"), 0)
    for v in views:
        if v.state is RowState.LOADING:
            counts["loading"] += 1
        elif v.row is None or v.row.price is None:
            counts["failed"] += 1
        elif v.row.all_hit:
            counts["ready"] += 1
        elif v.row.m_all is None:
            counts["without active targets"] += 1
        else:
            counts["near" if heat(v.row.m_all)[0] is Heat.NEAR else "far"] += 1
    return ", ".join(f"{n} {label}" for label, n in counts.items() if n)


class HeatMatrixModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._symbols: list[str] = []
        self._views: dict[str, RowView] = {}
        self._quotes: dict[str, Quote] = {}
        self._trends: dict[str, tuple[float, ...]] = {}
        self._names: dict[str, str] = {}  # kept through a failed fetch

    # --- API ------------------------------------------------------------------------------

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def set_symbols(self, symbols: list[str]) -> None:
        """Show exactly `symbols`, keeping what is known about the ones already shown."""
        self.beginResetModel()
        self._symbols = list(dict.fromkeys(symbols))
        self._views = {
            s: self._views.get(s, RowView(s, RowState.LOADING)) for s in self._symbols
        }
        self._quotes = {s: q for s, q in self._quotes.items() if s in self._views}
        self._trends = {s: c for s, c in self._trends.items() if s in self._views}
        self._names = {s: n for s, n in self._names.items() if s in self._views}
        self.endResetModel()

    def set_row(self, view: RowView) -> None:
        if view.symbol not in self._views:
            return
        self._views[view.symbol] = view
        if view.inputs is not None:
            self._names[view.symbol] = view.inputs.name
        r = self._symbols.index(view.symbol)
        self.dataChanged.emit(self.index(r, 0), self.index(r, len(Col) - 1))

    def set_quote(self, quote: Quote) -> None:
        """The latest quote, for the day's change under the price."""
        if quote.symbol in self._views:
            self._quotes[quote.symbol] = quote
            self._changed(quote.symbol, Col.PRICE)

    def set_trend(self, symbol: str, closes: tuple[float, ...]) -> None:
        """One month of closes for the small price line."""
        if symbol in self._views:
            self._trends[symbol] = tuple(c for c in closes if math.isfinite(c))
            self._changed(symbol, Col.TREND)
            self._changed(symbol, Col.PRICE)  # the day's change may come from these closes

    def _changed(self, symbol: str, col: Col) -> None:
        index = self.index(self._symbols.index(symbol), col)
        self.dataChanged.emit(index, index)

    def row(self, symbol: str) -> RowView | None:
        return self._views.get(symbol)

    def hit_count(self) -> int:
        return sum(
            1
            for v in self._views.values()
            if v.state is RowState.READY and v.row is not None
            for c in v.row.cells
            if c.hit
        )

    # --- Qt -------------------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._symbols)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(Col)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[Col(section)]
        if role == Qt.ItemDataRole.ToolTipRole:
            return {Col.EV_EBITDA: EBITDA_NOTE, Col.BUY_AT: BUY_AT_NOTE}.get(Col(section))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return self._alignment(Col(section))
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        view = self._views[self._symbols[index.row()]]
        col = Col(index.column())
        if role == SymbolRole:
            return view.symbol
        if role == IndicatorRole:
            return INDICATOR_COL.get(col)
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(view, col)
        if role == Qt.ItemDataRole.AccessibleTextRole:  # a bare dash would say nothing
            text = self._text(view, col)
            reason = self._cell(view, col) is not None and self._cell(view, col).reason is not None
            return self._sentence(view, col) if text == DASH and reason else text
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleDescriptionRole):
            return self._sentence(view, col)
        if role == SortRole:
            return self._sort_value(view, col)
        if role == SubtextRole:
            return self._subtext(view, col)
        if role == SubtextColourRole:
            return self._subtext_colour(view, col)
        if role == TrendRole:
            return self._closes(view.symbol) if col is Col.TREND and view.state is RowState.READY else None
        if role == ReadinessRole:
            ok = col is Col.READINESS and view.state is RowState.READY and view.row is not None
            return readiness_fraction(view.row.m_all, view.row.all_hit) if ok else None
        c = self._cell(view, col)
        if role == HeatRole:
            return None if c is None else (c.heat, c.intensity)
        if role == Qt.ItemDataRole.BackgroundRole:
            if self._thesis_broken(view, col):
                return heat_fill(Heat.FAR, 1.0)
            hit = c is not None and c.heat is Heat.HIT and self._measured(c)
            return heat_fill(Heat.HIT, HIT_FILL_INTENSITY) if hit else None
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(TOKENS[self._foreground(view, col, c)])
        if role == EdgeRole:
            if self._thesis_broken(view, col):
                return QColor(TOKENS["down"])
            return QColor(TOKENS[_HEAT_TOKEN[c.heat]]) if c is not None and self._measured(c) else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return self._alignment(col)
        return None

    # --- internals ------------------------------------------------------------------------

    @staticmethod
    def _alignment(col: Col) -> int:
        side = Qt.AlignmentFlag.AlignLeft if col is Col.SYMBOL else Qt.AlignmentFlag.AlignRight
        return int(side | Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _measured(c: Cell) -> bool:
        """An enabled target that has a place on the heat scale."""
        return c.target is not None and c.target.enabled and c.heat is not Heat.NA

    @staticmethod
    def _thesis_broken(view: RowView, col: Col) -> bool:
        return col is Col.THESIS and view.state is RowState.READY and view.row is not None and view.row.thesis_broken

    def _closes(self, symbol: str) -> tuple[float, ...] | None:
        closes = self._trends.get(symbol)
        return closes if closes is not None and len(closes) >= 2 else None

    def _day_move(self, symbol: str) -> float | None:
        """Today's change as a fraction. Yahoo's quote sometimes leaves it out; the last two
        daily closes then give the same figure."""
        quote = self._quotes.get(symbol)
        if quote is not None and quote.change_pct is not None:
            return quote.change_pct
        closes = self._closes(symbol)
        return closes[-1] / closes[-2] - 1 if closes is not None and closes[-2] else None

    def _day_change(self, symbol: str) -> str | None:
        move = self._day_move(symbol)
        return None if move is None else f"{ARROWS[direction(move)]} {fmt_pct(move, signed=True)}"

    def _subtext(self, view: RowView, col: Col) -> str | None:
        if col is Col.SYMBOL:
            return self._names.get(view.symbol)
        if col is Col.PRICE and view.state is RowState.READY:
            return self._day_change(view.symbol)
        return None

    def _subtext_colour(self, view: RowView, col: Col) -> QColor | None:
        if col is not Col.PRICE:
            return None
        return QColor(TOKENS[_DIRECTION_TOKEN[direction(self._day_move(view.symbol))]])

    def _foreground(self, view: RowView, col: Col, c: Cell | None) -> str:
        """The name of the colour token for a cell's main text."""
        row = view.row
        if view.state is not RowState.READY or row is None or row.price is None:
            if col is Col.PRICE and view.state is RowState.ERROR:
                return "error"
            return "label" if col is Col.SYMBOL else "muted"
        if col is Col.SYMBOL:
            return "text"  # amber is for figures; the ticker is a name
        if col is Col.PRICE:
            return "amber"
        if col is Col.TREND:
            closes = self._closes(view.symbol)
            if closes is None:
                return "muted"
            return "up" if closes[-1] >= closes[0] else "down"
        if col is Col.READINESS:
            if row.all_hit:
                return "up"
            return "muted" if row.m_all is None else _HEAT_TOKEN[heat(row.m_all)[0]]
        if self._thesis_broken(view, col):
            return "text"  # on the red fill: red text would not be readable
        if c is not None and c.target is not None and self._measured(c):
            return _HEAT_TOKEN[c.heat]
        if c is not None and c.target is not None:
            return "muted"  # switched off, or it cannot be measured
        return "muted" if self._text(view, col) == DASH else "text"

    @staticmethod
    def _cell(view: RowView, col: Col) -> Cell | None:
        ind = INDICATOR_COL.get(col)
        if ind is None or view.row is None:
            return None
        return view.row.cells[list(Indicator).index(ind)]

    def _text(self, view: RowView, col: Col) -> str:
        if col is Col.SYMBOL:
            return view.symbol
        if view.state is RowState.LOADING:
            return "…" if col is Col.PRICE else ""
        row = view.row
        if row is None or row.price is None:
            return self._text_without_price(view, col)
        c = self._cell(view, col)
        if c is not None:
            return cell_text(c)
        ccy = row.currency.value if row.currency else ""
        if col is Col.TREND:
            closes = self._closes(view.symbol)
            change = None if closes is None else month_return(closes)
            return DASH if change is None else fmt_pct(change, signed=True)
        if col is Col.PRICE:
            return f"{_num(row.price)} {ccy}"
        if col is Col.BUY_AT:
            return DASH if row.b_all is None else _num(row.b_all)
        if col is Col.READINESS:
            return readiness_text(row)
        if col is Col.POS_52W:
            return DASH if row.pos_52w is None else f"{row.pos_52w * 100:.0f}%"
        if col is Col.FAIR:
            return DASH if row.fair_value is None else _num(row.fair_value)
        if col is Col.THESIS:
            return thesis_text(row)
        return fmt_pct(row.margin, signed=True)  # Col.MARGIN

    def _text_without_price(self, view: RowView, col: Col) -> str:
        """The fetch failed, but what the user set is still theirs to see."""
        row = view.row
        if row is None:
            return DASH
        if col is Col.PRICE and row.error:
            return f"{DASH} {row.error}"
        c = self._cell(view, col)
        if c is not None and c.target is not None:
            return cell_text(c)
        return _num(row.thesis_level) if col is Col.THESIS and row.thesis_level is not None else DASH

    def _sentence(self, view: RowView, col: Col) -> str:
        row = view.row
        if view.state is RowState.LOADING:
            return f"{view.symbol}: loading"
        if row is None or row.price is None:
            c = self._cell(view, col)
            if c is not None and c.target is not None:
                return cell_sentence(view.symbol, "", c)
            reason = (row.error if row else None) or "unknown error"
            return f"{view.symbol}: data unavailable — {reason}"
        ccy = row.currency.value if row.currency else ""
        c = self._cell(view, col)
        if c is not None:
            return cell_sentence(view.symbol, ccy, c)
        if col is Col.SYMBOL:
            name = self._names.get(view.symbol)
            summary = f"{view.symbol}{f' ({name})' if name else ''}: price {_num(row.price)} {ccy}"
            if row.b_all is not None:
                summary += f"; buy at {_num(row.b_all)} for all {row.n_valid} targets"
            hits = sum(1 for x in row.cells if x.hit)
            return f"{summary}; {readiness_text(row)}; {hits} of {row.n_valid} targets hit"
        if col is Col.READINESS:
            sentence = f"{view.symbol} readiness: {readiness_text(row)}"
            return sentence if row.m_all is None else f"{sentence}. {READINESS_NOTE}"
        if col is Col.TREND:
            return f"{view.symbol} over one month: {self._text(view, col)}"
        if col is Col.PRICE and self._day_change(view.symbol):
            quote = self._quotes.get(view.symbol)
            when = "today" if quote is not None and quote.change_pct is not None else "latest session"
            return f"{view.symbol} Price: {self._text(view, col)}; {when} {self._day_change(view.symbol)}"
        if col is Col.THESIS:
            return thesis_sentence(view.symbol, ccy, row)
        if col is Col.FAIR and row.fair_value is None:
            return f"{view.symbol} fair value: n/a — {row.fair_reason or 'not available'}"
        return f"{view.symbol} {HEADERS[col]}: {self._text(view, col)}"

    def _sort_value(self, view: RowView, col: Col) -> float | str | None:
        if col is Col.SYMBOL:
            return view.symbol
        row = view.row
        if row is None or row.price is None:
            return None
        c = self._cell(view, col)
        if c is not None:
            return c.move if c.target is not None else None
        if col is Col.TREND:
            closes = self._closes(view.symbol)
            return None if closes is None else month_return(closes)
        return {
            Col.PRICE: row.price,
            Col.BUY_AT: row.b_all,
            Col.READINESS: row.m_all,
            Col.POS_52W: row.pos_52w,
            Col.FAIR: row.fair_value,
            Col.MARGIN: row.margin,
            Col.THESIS: row.thesis_move,
        }[col]


class HeatSortProxy(QSortFilterProxyModel):
    """Sorts on SortRole; rows without a value go last in both orders."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortRole(SortRole)
        self.setDynamicSortFilter(True)

    def lessThan(self, left, right) -> bool:  # noqa: N802
        a, b = left.data(SortRole), right.data(SortRole)
        if a is None or b is None:
            if a is None and b is None:
                return str(left.data(SymbolRole)) < str(right.data(SymbolRole))
            descending = self.sortOrder() == Qt.SortOrder.DescendingOrder
            return descending if a is None else not descending
        return a < b
