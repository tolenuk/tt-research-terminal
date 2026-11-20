"""TOP 20: the 20 cheapest stocks by EV/EBIT in B3, the UK or the US (Clube do Valor's method).

B3 comes from Fundamentus with every figure; UK and US from Yahoo's screener, which ranks by
EV/EBIT without returning it, so those columns are hidden there. Each market loads the first
time it is chosen, is saved to disk so it reopens instantly, and refetches when the saved list
is older than STALE_AFTER or on Refresh. The + on each row adds that stock to the watchlist.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from obb_terminal.data.fundamentus import Fundamentus
from obb_terminal.data.market import DataError
from obb_terminal.data.screen_cache import load_snapshot, save_snapshot
from obb_terminal.data.yahoo_screener import YahooScreener
from obb_terminal.domain.cheap_screen import (
    MARKET_RULES,
    Market,
    MarketSnapshot,
    Pick,
    ScreenResult,
    screen_snapshot,
)
from obb_terminal.store import data_dir
from obb_terminal.ui.desk.common import (
    TableCell,
    fill_table,
    heading,
    label,
    panel,
    selected_symbol,
    table,
)
from obb_terminal.ui.fmt import DASH, fmt_big, fmt_num
from obb_terminal.ui.worker import Loader

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(hours=12)  # both sources update once a day
FETCH_TIMEOUT_S = 90.0
ADD_COL, RANK_COL, TICKER_COL, NAME_COL, EV_EBIT_COL = range(5)
HEADERS = [
    "",
    "#",
    "Ticker",
    "Company",
    "EV/EBIT",
    "P/L",
    "P/VP",
    "EBIT margin",
    "ROIC",
    "{cur}/day",
    "Score",
]
LIQUIDITY_COL = HEADERS.index("{cur}/day")
YAHOO_LACKS = (EV_EBIT_COL, HEADERS.index("EBIT margin"), HEADERS.index("ROIC"))
NOTE = "Cheapest enterprise value per unit of operating profit first. Double-click a row to open it in Overview."


@dataclass(frozen=True)
class MarketText:
    name: str
    currency: str  # symbol for the liquidity column
    floor: str
    source: str
    caveat: str


TEXT = {
    Market.B3: MarketText(
        "B3",
        "R$",
        "R$200k",
        "Fundamentus",
        "Clube do Valor's own market. Their exact list isn't public, so expect heavy overlap "
        "rather than a perfect match. Figures are Fundamentus's latest filings.",
    ),
    Market.UK: MarketText(
        "UK",
        "£",
        "£250k",
        "Yahoo",
        "Main-market and AIM shares on the LSE, ranked by Yahoo's EV/EBIT, which Yahoo doesn't "
        "show. A few investment trusts Yahoo files outside Financials can still appear.",
    ),
    Market.US: MarketText(
        "US",
        "$",
        "$1M",
        "Yahoo",
        "NYSE and Nasdaq companies reporting in dollars, ranked by Yahoo's EV/EBIT, which Yahoo "
        "doesn't show. Foreign companies that report in dollars (Weibo, Equinor) still appear.",
    ),
}


def _rules_text(text: MarketText) -> str:
    return (
        f"Kept: trades over {text.floor} a day, positive EBIT, profitable, positive equity, "
        "EV/EBIT > 0.\n\n"
        "Dropped: banks, insurers and other financials, where EV/EBIT means nothing.\n\n"
        "One share class per company, the most traded one.\n\n"
        "Score: 100 is the cheapest stock that passes, 0 the dearest."
    )


@dataclass(frozen=True)
class ScreenerSource:
    """Where one market's data comes from and is kept; tests pass fakes and temp paths."""

    fetch: Callable[[], MarketSnapshot]
    cache_path: Path
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def default_screener_sources() -> dict[Market, ScreenerSource]:
    folder = data_dir() / "screener"
    yahoo = YahooScreener()
    return {
        Market.B3: ScreenerSource(Fundamentus().fetch, folder / "b3.json"),
        Market.UK: ScreenerSource(partial(yahoo.fetch, Market.UK), folder / "uk.json"),
        Market.US: ScreenerSource(partial(yahoo.fetch, Market.US), folder / "us.json"),
    }


def yahoo_symbol(market: Market, ticker: str) -> str:
    """The symbol the rest of the app uses: B3 tickers need Yahoo's .SA suffix."""
    return f"{ticker}.SA" if market is Market.B3 else ticker


def _pct(value: float | None) -> str:
    return DASH if value is None else f"{value * 100:.1f}%"


def _cells(pick: Pick) -> list[TableCell]:
    s = pick.stock
    return [
        TableCell(""),  # the + button sits over this cell
        TableCell(str(pick.rank), right=True, mono=True),
        TableCell(s.ticker, mono=True, tone="amber"),
        TableCell(s.name),
        TableCell(fmt_num(s.ev_ebit), "Enterprise value / EBIT", right=True, mono=True),
        TableCell(fmt_num(s.pl), "Price / earnings", right=True, mono=True),
        TableCell(fmt_num(s.pvp), "Price / book value", right=True, mono=True),
        TableCell(_pct(s.ebit_margin), right=True, mono=True),
        TableCell(_pct(s.roic), "Return on invested capital", right=True, mono=True),
        TableCell(fmt_big(s.liquidity), "Average traded value per day", right=True, mono=True),
        TableCell(str(pick.score), right=True, mono=True, tone="up"),
    ]


def _fetch_and_save(source: ScreenerSource) -> MarketSnapshot:
    """Runs on a worker thread: a failed save only costs the next start a refetch."""
    snap = source.fetch()
    try:
        save_snapshot(source.cache_path, snap)
    except OSError:
        log.warning("Could not save the screener snapshot", exc_info=True)
    return snap


def _counts_text(market: Market, snap: MarketSnapshot, result: ScreenResult) -> str:
    at = snap.fetched_at.astimezone()
    if snap.presorted:
        counts = (
            f"{result.universe:,} companies pass Yahoo's filters; {result.eligible:,} are liquid "
            "enough, one share class each."
        )
    else:
        counts = (
            f"{result.universe:,} {TEXT[market].name} tickers, {result.financials} financials "
            f"dropped, {result.eligible} companies pass the rules."
        )
    return f"{counts}\nFetched {at:%d %b %Y %H:%M} from {TEXT[market].source}."


class ScreenerPage(QWidget):
    openOverview = Signal(str)
    addRequested = Signal(str)

    def __init__(
        self,
        loader: Loader,
        sources: Mapping[Market, ScreenerSource],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._loader = loader
        self._sources = dict(sources)
        self._snapshots: dict[Market, MarketSnapshot] = {}
        self._started: set[Market] = set()
        self._loading: set[Market] = set()
        self._errors: dict[Market, str] = {}
        self._watched: frozenset[str] = frozenset()
        self._active = False
        self.market = Market.B3

        self.title = label("", "DeskTitle", wrap=True)
        self.note = label(NOTE, "Secondary", wrap=True)
        self.status = label("", "Secondary", wrap=True)
        self.market_buttons = self._market_buttons()
        self.table = table(HEADERS, "Top 20 cheapest stocks", stretch=NAME_COL)
        self.table.cellDoubleClicked.connect(self._activate)
        self.table.cellActivated.connect(self._activate)
        self.rules = label("", "Secondary", wrap=True)
        self.counts = label("", "Body", wrap=True)
        self.caveat = label("", "Secondary", wrap=True)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setAccessibleName("Refresh this market's top 20")
        self.refresh_button.clicked.connect(self.refresh)

        markets = QHBoxLayout()
        markets.setContentsMargins(0, 0, 0, 0)
        markets.setSpacing(6)
        for button in self.market_buttons.values():
            markets.addWidget(button)
        markets.addStretch(1)
        bar = QWidget()
        bar.setLayout(markets)
        side = QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(panel(heading("THE RULES"), self.rules), 1)
        side.addWidget(panel(heading("DATA"), self.counts, self.caveat, self.refresh_button))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(
            panel(bar, self.title, self.note, self.status, self.table, stretch_last=True), 4
        )
        layout.addLayout(side, 1)
        self._show_market()

    # --- public ---------------------------------------------------------------------------

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self._start(self.market)

    def set_market(self, market: Market) -> None:
        self.market = market
        self.market_buttons[market].setChecked(True)
        self._show_market()
        if self._active:
            self._start(market)

    def set_watchlist(self, symbols: Iterable[str]) -> None:
        self._watched = frozenset(symbols)
        self._update_add_buttons()

    def refresh(self) -> None:
        market = self.market
        source = self._sources[market]
        self._loading.add(market)
        self._errors.pop(market, None)
        self._update_status()
        self._loader.submit(
            f"screener:{market.value}",
            lambda _md: _fetch_and_save(source),
            partial(self._on_snapshot, market),
            partial(self._on_error, market),
            scoped=False,
            timeout_s=FETCH_TIMEOUT_S,
        )

    # --- loading --------------------------------------------------------------------------

    def _start(self, market: Market) -> None:
        """A market's first showing uses its saved list and refetches it if missing or stale."""
        if market in self._started:
            return
        self._started.add(market)
        source = self._sources[market]
        saved = load_snapshot(source.cache_path)
        if saved is not None:
            self._snapshots[market] = saved
            self._show_market()
        if saved is None or source.clock() - saved.fetched_at > STALE_AFTER:
            self.refresh()

    def _on_snapshot(self, market: Market, snap: MarketSnapshot) -> None:
        self._loading.discard(market)
        self._snapshots[market] = snap
        if market is self.market:
            self._show_market()

    def _on_error(self, market: Market, err: DataError) -> None:
        self._loading.discard(market)
        kept = " Showing the last saved list." if market in self._snapshots else ""
        self._errors[market] = f"{err.user_message}.{kept}"
        if market is self.market:
            self._update_status()

    # --- display --------------------------------------------------------------------------

    def _market_buttons(self) -> dict[Market, QPushButton]:
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons: dict[Market, QPushButton] = {}
        for market in Market:
            button = QPushButton(TEXT[market].name)
            button.setCheckable(True)
            button.setChecked(market is Market.B3)
            button.setAccessibleName(f"Show the {TEXT[market].name} top 20")
            button.clicked.connect(partial(self.set_market, market))
            group.addButton(button)
            buttons[market] = button
        return buttons

    def _show_market(self) -> None:
        market, text = self.market, TEXT[self.market]
        self.title.setText(f"The 20 cheapest {text.name} stocks by EV/EBIT")
        self.rules.setText(_rules_text(text))
        self.caveat.setText(text.caveat)
        header = self.table.horizontalHeaderItem(LIQUIDITY_COL)
        if header is not None:
            header.setText(f"{text.currency}/day")
        snap = self._snapshots.get(market)
        for col in YAHOO_LACKS:
            self.table.setColumnHidden(col, snap is not None and snap.presorted)
        if snap is None:
            fill_table(self.table, [], [])
            self.counts.setText("")
        else:
            result = screen_snapshot(snap, MARKET_RULES[market])
            symbols = [yahoo_symbol(market, p.stock.ticker) for p in result.picks]
            fill_table(self.table, [_cells(p) for p in result.picks], symbols)
            self._place_add_buttons(symbols)
            self.counts.setText(_counts_text(market, snap, result))
        self._update_status()

    def _update_status(self) -> None:
        market = self.market
        loading = market in self._loading
        self.refresh_button.setEnabled(not loading)
        if loading:
            self.status.setText(
                f"Loading the {TEXT[market].name} market from {TEXT[market].source}…"
            )
        else:
            self.status.setText(self._errors.get(market, ""))

    def _place_add_buttons(self, symbols: list[str]) -> None:
        for row, symbol in enumerate(symbols):
            button = QPushButton("+")
            button.setObjectName("RowAdd")
            button.setProperty("symbol", symbol)
            button.clicked.connect(partial(self.addRequested.emit, symbol))
            self.table.setCellWidget(row, ADD_COL, button)
        self._update_add_buttons()

    def _update_add_buttons(self) -> None:
        for row in range(self.table.rowCount()):
            button = self.table.cellWidget(row, ADD_COL)
            if not isinstance(button, QPushButton):
                continue
            symbol = str(button.property("symbol"))
            watched = symbol in self._watched
            button.setText("✓" if watched else "+")
            button.setEnabled(not watched)
            tip = "In your watchlist" if watched else f"Add {symbol} to your watchlist"
            button.setToolTip(tip)
            button.setAccessibleName(tip)

    def _activate(self, row: int, col: int) -> None:
        if col == ADD_COL:
            return
        self.table.selectRow(row)
        symbol = selected_symbol(self.table)
        if symbol:
            self.openOverview.emit(symbol)
