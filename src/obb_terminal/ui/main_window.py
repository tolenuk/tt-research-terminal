"""Main window: ticker bar, watchlist strip, the grouped page bar and pages (see PAGE_GROUPS), status bar."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from obb_terminal.ai.analyst import Analyst
from obb_terminal.data.market import DataError, SymbolNotFound
from obb_terminal.data.rollup import RollupMemo
from obb_terminal.domain.models import AssetType, FundProfile, Profile
from obb_terminal.domain.targets import Indicator
from obb_terminal.journal.store import Journal
from obb_terminal.store import SYMBOL_RE, Store, StoreError
from obb_terminal.ui.desk import (
    CallBookPage,
    DeskFeed,
    EntryLadderPage,
    IncomePage,
    TodayPage,
    ValueMapPage,
)
from obb_terminal.ui.jobs import SerialJobs
from obb_terminal.ui.page_nav import PageNav
from obb_terminal.ui.panels.base import Panel
from obb_terminal.ui.panels.chart import ChartPanel
from obb_terminal.ui.panels.financials import FinancialsPanel
from obb_terminal.ui.panels.fund import FundPanel
from obb_terminal.ui.panels.holdings import HoldingsPanel
from obb_terminal.ui.panels.news import NewsPanel
from obb_terminal.ui.panels.quote import QuotePanel, QuoteView
from obb_terminal.ui.screener import ScreenerPage, default_screener_sources
from obb_terminal.ui.theme import set_dir
from obb_terminal.ui.ticker_bar import TickerBar
from obb_terminal.ui.track_record import TrackRecordTab
from obb_terminal.ui.valuation import ValuationPage
from obb_terminal.ui.watchlist import WatchlistStrip
from obb_terminal.ui.watchlist_tab import AlertServices, WatchlistTab, default_alert_services
from obb_terminal.ui.worker import Loader

ENGINE_TEXT = {"loading": "Loading data engine…", "failed": "Data engine failed — see log"}
# Page order is the page bar's order, left to right: STOCK · DESK · DISCOVER.
OVERVIEW, VALUATION, TODAY, WATCHLIST, VALUE_MAP, LADDER, INCOME, CALL_BOOK, TRACK, TOP20 = range(10)
PAGE_TITLES = ("OVERVIEW", "VALUATION", "TODAY", "WATCHLIST", "VALUE MAP", "ENTRY LADDER", "INCOME",
               "CALL BOOK", "TRACK RECORD", "TOP 20")
PAGE_GROUPS = (
    ("STOCK", (OVERVIEW, VALUATION)),
    ("DESK", (TODAY, WATCHLIST, VALUE_MAP, LADDER, INCOME, CALL_BOOK, TRACK)),
    ("DISCOVER", (TOP20,)),
)
PAGES = len(PAGE_TITLES)
DESK_PAGES = frozenset({TODAY, VALUE_MAP, LADDER, INCOME, CALL_BOOK})  # built from the watchlist's data
NAV_SCROLLBAR = 6  # room under the page bar for its thin sideways scrollbar
SCORE_DELAY_MS = 30_000  # start-up scoring waits for the first page to settle


def _open_file(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


# Qt reads "Ctrl" as Cmd on macOS, and Cmd+Tab belongs to the system app switcher; "Meta" is the
# Mac's physical Control key, so the page cycle stays on Control+Tab there.
TAB_MODIFIER = "Meta" if sys.platform == "darwin" else "Ctrl"

class StatusBar(QStatusBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine_label = QLabel(ENGINE_TEXT["loading"])
        self.engine_label.setObjectName("Muted")
        self.offline_label = QLabel("● OFFLINE")
        self.offline_label.setObjectName("Offline")
        self.offline_label.hide()
        self.updated_label = QLabel("")
        self.updated_label.setObjectName("Muted")
        self.targets_label = QLabel("")
        self.targets_label.setAccessibleName("Watchlist targets hit")
        self.targets_label.hide()  # until the watchlist has been evaluated once
        self.open_report_button = QPushButton("Open")
        self.open_report_button.setAccessibleName("Open the exported report")
        self.open_report_button.hide()
        self.open_report_button.clicked.connect(self._open_report)
        self.messageChanged.connect(self._on_message_changed)
        self.open_file: Callable[[str], None] = _open_file
        self.report_path: str | None = None
        for widget in (
            self.open_report_button, self.targets_label, self.engine_label, self.offline_label,
            self.updated_label,
        ):
            self.addPermanentWidget(widget)

    def set_engine(self, state: str) -> None:
        if state == "ready":
            self.engine_label.hide()
            return
        self.engine_label.setText(ENGINE_TEXT[state])
        self.engine_label.show()

    def set_online(self, online: bool) -> None:
        self.offline_label.setVisible(not online)

    def set_updated(self, when: datetime) -> None:
        self.updated_label.setText(f"Updated {when:%H:%M:%S}")

    def set_targets_hit(self, n: int) -> None:
        self.targets_label.setText(f"✓ {n} target{'s' if n != 1 else ''} hit" if n else "0 targets hit")
        set_dir(self.targets_label, "up" if n else "flat")
        self.targets_label.show()

    def show_report(self, path: str) -> None:
        self.report_path = path
        self.showMessage(f"Report saved to {path}", 30_000)
        self.open_report_button.show()

    def _on_message_changed(self, text: str) -> None:
        if not text:  # cleared or expired: never leave Open lingering for a stale file
            self.open_report_button.hide()

    def _open_report(self) -> None:
        if self.report_path and Path(self.report_path).exists():
            self.open_file(self.report_path)


class PanelGrid(QWidget):
    def __init__(
        self,
        chart: Panel,
        quote: Panel,
        financials: Panel,
        fund: Panel,
        news: Panel,
        holdings: Panel | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.financials, self.fund, self.holdings = financials, fund, holdings
        self._analysis = QStackedWidget()
        for panel in self.analysis_panels():
            self._analysis.addWidget(panel)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(10)
        self._positions: dict[QWidget, tuple[int, int]] = {
            chart: (0, 0),
            quote: (0, 1),
            self._analysis: (1, 0),
            news: (1, 1),
        }
        for cell, (row, col) in self._positions.items():
            self._grid.addWidget(cell, row, col)
        self._grid.setColumnStretch(0, 3)
        self._grid.setColumnStretch(1, 2)
        self._grid.setRowStretch(0, 1)
        self._grid.setRowStretch(1, 1)
        self.maximised: QWidget | None = None
        for panel in (chart, quote, *self.analysis_panels(), news):
            panel.maximiseRequested.connect(lambda p=panel: self.toggle_maximise(p))

    def analysis_panels(self) -> tuple[Panel, ...]:
        """The panels that share the bottom-left cell: financials, fund and (ETFs) holdings."""
        return tuple(p for p in (self.financials, self.fund, self.holdings) if p is not None)

    def _cell_of(self, panel: Panel) -> QWidget:
        return self._analysis if panel in self.analysis_panels() else panel

    def toggle_maximise(self, panel: Panel) -> None:
        if self.maximised is self._cell_of(panel):
            self.restore()
        else:
            self.maximise(panel)

    def maximise(self, panel: Panel) -> None:
        self.restore()
        cell = self._cell_of(panel)
        for other in self._positions:
            if other is not cell:
                other.hide()
        self._grid.removeWidget(cell)
        self._grid.addWidget(cell, 0, 0, 2, 2)
        self.maximised = cell

    def restore(self) -> None:
        cell = self.maximised
        if cell is None:
            return
        self._grid.removeWidget(cell)
        row, col = self._positions[cell]
        self._grid.addWidget(cell, row, col)
        for other in self._positions:
            other.show()
        self.maximised = None

    def set_asset_type(self, asset_type: AssetType) -> None:
        if asset_type is AssetType.EQUITY:
            panel = self.financials
        elif asset_type is AssetType.ETF and self.holdings is not None:
            panel = self.holdings
        else:
            panel = self.fund
        self._analysis.setCurrentWidget(panel)

    def show_analysis(self, panel: Panel) -> None:
        self._analysis.setCurrentWidget(panel)

    def analysis_panel(self) -> Panel:
        return self._analysis.currentWidget()  # type: ignore[return-value]


def _scrolling(bar: QWidget) -> QScrollArea:
    """The page bar in a strip that scrolls sideways when the window is narrower than the bar,
    so the bar never sets the window's minimum width."""
    strip = QScrollArea()
    strip.setObjectName("PageNavStrip")
    strip.setWidget(bar)
    strip.setWidgetResizable(True)
    strip.setFrameShape(QScrollArea.Shape.NoFrame)
    strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    strip.setFixedHeight(bar.sizeHint().height() + NAV_SCROLLBAR)
    return strip


class MainWindow(QMainWindow):
    def __init__(
        self,
        loader: Loader,
        store: Store,
        *,
        analyst: Analyst,
        alerts: AlertServices | None = None,
        refresh_ms: int = 60_000,
        watchlist_refresh_ms: int = 300_000,
        ai_timeout_s: float = 330.0,
        journal: Journal | None = None,
        score_delay_ms: int | None = SCORE_DELAY_MS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("TT Research Terminal")
        self._loader = loader
        self._store = store
        result = store.load()
        self._state = result.state
        self.current: Profile | None = None

        self.status = StatusBar()
        self.ticker = TickerBar(loader)
        self.watchlist = WatchlistStrip(loader, list(self._state.symbols), refresh_ms=refresh_ms)
        self.chart = ChartPanel(loader)
        self.quote = QuotePanel(loader)
        self.financials = FinancialsPanel(loader)
        self.fund = FundPanel(loader)
        self.holdings = HoldingsPanel(loader)
        self.news = NewsPanel(loader)
        self.grid = PanelGrid(self.chart, self.quote, self.financials, self.fund, self.news, self.holdings)
        self.rollups = RollupMemo()  # ETF top-holdings roll-ups: run by Valuation, read by the desk
        self.valuation = ValuationPage(
            loader, analyst, ai_timeout_s=ai_timeout_s, journal=journal, rollups=self.rollups
        )
        self.jobs = SerialJobs(self)
        self.track_record = TrackRecordTab(loader, journal, self.jobs)
        self.watchlist_tab = WatchlistTab(
            loader, alerts or default_alert_services(), self.jobs, list(self._state.symbols),
            refresh_ms=watchlist_refresh_ms, rollups=self.rollups,
        )
        self.today = TodayPage(loader)
        self.value_map = ValueMapPage()
        self.screener = ScreenerPage(loader, default_screener_sources())
        self.screener.set_watchlist(self._state.symbols)
        self.ladder = EntryLadderPage()
        self.income = IncomePage()
        self.call_book = CallBookPage()
        self.desk = DeskFeed(self.watchlist_tab.row_views, analyst.last, journal, self.jobs, self)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("PageTabs")
        pages = (self.grid, self.valuation, self.today, self.watchlist_tab, self.value_map, self.ladder,
                 self.income, self.call_book, self.track_record, self.screener)
        for page, title in zip(pages, PAGE_TITLES, strict=True):
            self.tabs.addTab(page, title)
        self.tabs.setCurrentIndex(TODAY)
        self.page_nav = PageNav(self.tabs, PAGE_GROUPS, self)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 4)
        layout.setSpacing(10)
        layout.addWidget(self.ticker)
        layout.addWidget(self.watchlist)
        layout.addWidget(_scrolling(self.page_nav))
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.setStatusBar(self.status)

        self.ticker.symbolRequested.connect(self.select_symbol)
        self.watchlist.symbolActivated.connect(self.select_symbol)
        self.watchlist.changed.connect(self._set_symbols)
        self.watchlist.quoteUpdated.connect(self.watchlist_tab.set_quote)
        self.watchlist.trendUpdated.connect(self.watchlist_tab.set_trend)
        self.watchlist_tab.symbolActivated.connect(self._open_in_overview)
        self.watchlist_tab.addRequested.connect(self.add_symbol)
        self.watchlist_tab.removeRequested.connect(self.remove_symbol)
        self.watchlist_tab.hitCountChanged.connect(self.status.set_targets_hit)
        self.watchlist_tab.message.connect(self._show_message)
        self.track_record.symbolActivated.connect(self._open_in_valuation)
        self._connect_desk()
        self.quote.loaded.connect(self._on_quote_view)
        self.holdings.dividendsRequested.connect(lambda: self._switch_analysis(self.fund))
        self.fund.holdingsRequested.connect(lambda: self._switch_analysis(self.holdings))
        self.holdings.holdingActivated.connect(self.select_symbol)
        self.holdings.loaded.connect(self._on_holdings)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.valuation.exported.connect(self.status.show_report)
        panel = self.valuation.analyst_panel
        panel.targets_lookup = lambda symbol: (
            self.watchlist_tab.targets_for(symbol), symbol in self._state.symbols
        )
        panel.useAsTargets.connect(self._use_call_as_targets)
        loader.engineReady.connect(lambda: self.status.set_engine("ready"))
        loader.engineFailed.connect(lambda _msg: self.status.set_engine("failed"))
        loader.onlineChanged.connect(self.status.set_online)
        if loader.ready:
            self.status.set_engine("ready")

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(refresh_ms)
        self._refresh_timer.timeout.connect(self.quote.refresh)
        self._refresh_timer.start()

        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.esc_shortcut.activated.connect(self._on_escape)
        focus = QShortcut(QKeySequence("Ctrl+L"), self)
        focus.activated.connect(lambda: (self.ticker.edit.setFocus(), self.ticker.edit.selectAll()))
        for n, get_panel in enumerate(
            (lambda: self.chart, lambda: self.quote, self.grid.analysis_panel, lambda: self.news),
            start=1,
        ):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{n}"), self)
            shortcut.activated.connect(partial(self._maximise_overview, get_panel))
        self.page_shortcuts: list[QShortcut] = []
        for keys, slot in (("F2", self.next_page), (f"{TAB_MODIFIER}+Tab", self.next_page),
                           (f"{TAB_MODIFIER}+Shift+Tab", self.prev_page)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
            self.page_shortcuts.append(shortcut)

        if result.recovered:
            self.status.showMessage(
                "Watchlist file was unreadable — started with an empty list", 10_000
            )
        if journal is not None and score_delay_ms is not None:
            QTimer.singleShot(score_delay_ms, self.track_record.score_if_due)
        if self._state.last_symbol:
            last = self._state.last_symbol
            QTimer.singleShot(0, lambda: self.select_symbol(last))
        QTimer.singleShot(0, self, lambda: self._on_tab_changed(self.tabs.currentIndex()))

    def select_symbol(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        if not symbol:
            return
        if not SYMBOL_RE.match(symbol):
            # Yahoo accepts comma lists ("AAPL,MSFT"); never fetch or persist such text.
            self.ticker.show_error(f"Unknown symbol: {symbol}")
            return
        self._loader.submit(
            "profile",
            lambda md: md.profile(symbol),
            self._on_profile,
            lambda err: self._on_profile_error(symbol, err),
            scoped=False,
        )

    def _on_profile(self, profile: Profile) -> None:
        self._loader.new_generation()
        self.current = profile
        self.ticker.clear_error()
        self.ticker.set_profile(profile)
        self.grid.set_asset_type(profile.asset_type)
        self.fund.set_holdings_switch(profile.asset_type is AssetType.ETF)
        self.watchlist.set_current(profile.symbol)
        active = self.grid.analysis_panel()
        for inactive in self.grid.analysis_panels():
            if inactive is not active:
                inactive.clear()
        for panel in (self.chart, self.quote, active, self.news):
            panel.load(profile.symbol, profile)
        self.valuation.set_profile(profile)
        self._update_state(last_symbol=profile.symbol)

    def _on_holdings(self, profile: FundProfile) -> None:
        """No Yahoo fund data (B3 ETFs): keep the old dividends view, with no Holdings switch."""
        if profile.holdings or profile.sectors or profile.bond_ratings:
            return
        self.fund.set_holdings_switch(False)
        if self.grid.analysis_panel() is self.holdings:
            self._switch_analysis(self.fund)

    def _switch_analysis(self, panel: Panel) -> None:
        """ETF Holdings <-> Dividends: show the other view, loading it the first time."""
        self.grid.show_analysis(panel)
        profile = self.current
        if profile is not None and panel.symbol != profile.symbol:
            panel.load(profile.symbol, profile)

    def next_page(self) -> None:
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % PAGES)

    def prev_page(self) -> None:
        self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % PAGES)

    toggle_page = next_page  # v1.1 name

    def add_symbol(self, symbol: str) -> None:
        if symbol not in self._state.symbols:
            self._set_symbols([*self._state.symbols, symbol])

    def remove_symbol(self, symbol: str) -> None:
        if symbol in self._state.symbols:
            self._set_symbols([s for s in self._state.symbols if s != symbol])

    def shutdown_jobs(self) -> bool:
        return self.jobs.shutdown()

    def _set_symbols(self, symbols: list[str]) -> None:
        """The one place the watchlist changes: persist it, then show it in the strip and tab."""
        self._update_state(symbols=tuple(symbols))
        self.watchlist.set_symbols(list(symbols))
        self.watchlist_tab.set_symbols(list(symbols))
        self.screener.set_watchlist(symbols)

    def _use_call_as_targets(self, symbol: str, price: float | None, thesis: float | None, add: bool) -> None:
        if add:
            self.add_symbol(symbol)
        self.watchlist_tab.apply_call(symbol, price, thesis)
        self._show_message(f"{symbol}: saving watchlist targets from the AI call…")

    def _open_in_overview(self, symbol: str) -> None:
        self.select_symbol(symbol)
        self.tabs.setCurrentIndex(OVERVIEW)

    def _open_in_valuation(self, symbol: str) -> None:
        self.select_symbol(symbol)
        self.tabs.setCurrentIndex(VALUATION)

    def _show_message(self, text: str) -> None:
        self.status.showMessage(text, 10_000)

    def _connect_desk(self) -> None:
        self.watchlist_tab.dataChanged.connect(self.desk.request)
        for page in (self.today, self.value_map, self.ladder, self.income, self.call_book):
            self.desk.updated.connect(page.show_snapshot)
        for page in (self.today, self.value_map, self.ladder, self.call_book):
            page.openValuation.connect(self._open_in_valuation)
        self.today.openCallBook.connect(partial(self.tabs.setCurrentIndex, CALL_BOOK))
        self.today.openWatchlist.connect(partial(self.tabs.setCurrentIndex, WATCHLIST))
        self.today.openValueMap.connect(partial(self.tabs.setCurrentIndex, VALUE_MAP))
        self.call_book.openTrackRecord.connect(partial(self.tabs.setCurrentIndex, TRACK))
        self.income.openOverview.connect(self._open_in_overview)
        self.screener.openOverview.connect(self._open_in_overview)
        self.screener.addRequested.connect(self.add_symbol)
        self.value_map.refreshRequested.connect(self.watchlist_tab.refresh)
        self.ladder.setTargetRequested.connect(partial(self._edit_target, Indicator.PRICE))
        self.income.setYieldTarget.connect(partial(self._edit_target, Indicator.DIV_YIELD))

    def _edit_target(self, indicator: Indicator, symbol: str) -> None:
        self.watchlist_tab.open_editor(symbol, indicator)

    def _on_tab_changed(self, index: int) -> None:
        self.valuation.set_active(index == VALUATION)
        # The dashboards read the watchlist's data, so it stays fresh while one of them is open.
        self.watchlist_tab.set_active(index == WATCHLIST or index in DESK_PAGES, background=index != WATCHLIST)
        self.track_record.set_active(index == TRACK)
        self.screener.set_active(index == TOP20)
        self.today.set_active(index == TODAY)
        self.desk.set_active(index in DESK_PAGES)

    def _on_escape(self) -> None:
        if self.tabs.currentIndex() == OVERVIEW:
            self.grid.restore()

    def _maximise_overview(self, get_panel: Callable[[], Panel]) -> None:
        panel = get_panel()
        if self.tabs.currentIndex() != OVERVIEW:
            self.tabs.setCurrentIndex(OVERVIEW)
            self.grid.maximise(panel)
        else:
            self.grid.toggle_maximise(panel)

    def _on_profile_error(self, symbol: str, err: DataError) -> None:
        message = f"Unknown symbol: {symbol}" if isinstance(err, SymbolNotFound) else err.user_message
        self.ticker.show_error(message)

    def _on_quote_view(self, view: QuoteView) -> None:
        self.ticker.set_quote(view.quote)
        self.status.set_updated(datetime.now())

    def _update_state(self, **changes) -> None:
        self._state = replace(self._state, **changes)
        try:
            self._store.save(self._state)
        except StoreError:
            self.status.showMessage("Could not save watchlist — see log", 10_000)
