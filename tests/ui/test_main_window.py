import sys
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from obb_terminal.data.market import NoData, SourceUnavailable
from obb_terminal.store import AppState, Store
from obb_terminal.ui import main_window
from obb_terminal.ui.main_window import (
    INCOME,
    OVERVIEW,
    PAGE_TITLES,
    TODAY,
    TOP20,
    TRACK,
    VALUATION,
    WATCHLIST,
    MainWindow,
)
from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.valuation.ai_panel import AiState
from obb_terminal.ui.worker import Loader
from tests.fakes import FakeAnalyst, make_cached_report, make_series, make_valuation_inputs


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "watchlist.json")


@pytest.fixture
def window(qtbot, loader, store, fake_analyst, tmp_path):
    w = MainWindow(loader, store, analyst=fake_analyst)
    w.valuation.documents = tmp_path / "Documents"
    qtbot.addWidget(w)
    w.tabs.setCurrentIndex(OVERVIEW)  # the app opens on TODAY; these tests start from OVERVIEW
    w.show()
    return w


def visible_panels(w):
    return (w.chart, w.quote, w.grid.analysis_panel(), w.news)


def all_ready(w):
    return all(p.state is PanelState.READY for p in visible_panels(w))


def select(qtbot, w, symbol):
    w.select_symbol(symbol)
    qtbot.waitUntil(lambda: w.current is not None and w.current.symbol == symbol, timeout=2000)
    qtbot.waitUntil(lambda: all(p.state is not PanelState.LOADING for p in visible_panels(w)), timeout=3000)


def test_engine_loading_status_until_ready(qtbot, fake_md, store):
    ld = Loader(timeout_s=1.0)
    w = MainWindow(ld, store, analyst=FakeAnalyst())
    qtbot.addWidget(w)
    assert w.status.engine_label.text() == "Loading data engine…"
    assert not w.status.engine_label.isHidden()
    w.select_symbol("AAPL")
    gate = threading.Event()
    ld.start_engine(lambda: (gate.wait(5), fake_md)[1])
    qtbot.wait(100)
    assert w.current is None
    gate.set()
    qtbot.waitUntil(lambda: w.status.engine_label.isHidden(), timeout=2000)
    qtbot.waitUntil(lambda: all_ready(w), timeout=3000)
    ld.shutdown()


def test_select_equity_fills_all_panels(qtbot, window):
    select(qtbot, window, "PETR4.SA")
    assert all_ready(window)
    assert window.grid.analysis_panel() is window.financials
    assert window.ticker.name_label.text().startswith("Petrobras")
    assert window.ticker.price_label.text() == "56.00 BRL"


def test_fii_shows_fund_panel(qtbot, window, fake_md):
    select(qtbot, window, "HGLG11.SA")
    assert window.grid.analysis_panel() is window.fund
    assert window.fund.state is PanelState.READY
    assert window.fund.holdings_button.isHidden()
    assert fake_md.calls_to("financials") == []


def test_etf_shows_holdings_and_switches_to_dividends(qtbot, window, fake_md):
    select(qtbot, window, "SPY")
    assert window.grid.analysis_panel() is window.holdings
    assert window.holdings.state is PanelState.READY
    assert fake_md.calls_to("fund_info") == [] and fake_md.calls_to("financials") == []
    window.holdings.dividendsRequested.emit()
    assert window.grid.analysis_panel() is window.fund
    qtbot.waitUntil(lambda: window.fund.state is PanelState.READY, timeout=2000)
    assert not window.fund.holdings_button.isHidden()
    window.fund.holdingsRequested.emit()
    assert window.grid.analysis_panel() is window.holdings
    assert len(fake_md.calls_to("fund_profile")) == 1  # already loaded: not fetched again


def test_next_symbol_resets_the_etf_view_to_holdings(qtbot, window):
    select(qtbot, window, "SPY")
    window.holdings.dividendsRequested.emit()
    select(qtbot, window, "AAPL")
    assert window.grid.analysis_panel() is window.financials
    select(qtbot, window, "SPY")
    assert window.grid.analysis_panel() is window.holdings


def test_double_clicked_holding_opens_it(qtbot, window):
    select(qtbot, window, "SPY")
    window.holdings.holdingActivated.emit("AAPL")
    qtbot.waitUntil(lambda: window.current.symbol == "AAPL", timeout=2000)


def test_gbp_header(qtbot, window):
    select(qtbot, window, "SHEL.L")
    assert window.ticker.price_label.text() == "27.10 GBP"


def test_unknown_symbol_keeps_current_view(qtbot, window):
    select(qtbot, window, "AAPL")
    window.select_symbol("ZZZZ")
    qtbot.waitUntil(lambda: not window.ticker.error_label.isHidden(), timeout=2000)
    assert window.ticker.error_label.text() == "Unknown symbol: ZZZZ"
    assert window.current.symbol == "AAPL"
    assert window.chart.symbol == "AAPL"
    assert all_ready(window)


@pytest.mark.parametrize("text", ["AAPL,MSFT", "AAPL MSFT", "../etc"])
def test_invalid_symbol_text_is_rejected_without_fetch(qtbot, window, fake_md, store, text):
    select(qtbot, window, "AAPL")
    profile_calls = len(fake_md.calls_to("profile"))
    window.select_symbol(text)
    assert window.ticker.error_label.text() == f"Unknown symbol: {text.upper()}"
    assert not window.ticker.error_label.isHidden()
    qtbot.wait(100)
    assert len(fake_md.calls_to("profile")) == profile_calls
    assert window.current.symbol == "AAPL"
    assert store.load().state.last_symbol == "AAPL"


def test_one_panel_error_others_render(qtbot, window, fake_md):
    fake_md.results["news"] = NoData("none")
    select(qtbot, window, "AAPL")
    assert window.news.state is PanelState.ERROR
    assert window.news.error_text == "No data available for AAPL"
    assert all(p.state is PanelState.READY for p in (window.chart, window.quote, window.financials))


def test_offline_indicator_and_retry(qtbot, window, fake_md):
    offline = SourceUnavailable("Source unavailable (offline?)", reason="network")
    saved = dict(fake_md.results)
    for method in ("quote", "history", "financials", "news"):
        fake_md.results[method] = offline
    select(qtbot, window, "AAPL")
    qtbot.waitUntil(lambda: not window.status.offline_label.isHidden(), timeout=2000)
    fake_md.results.update(saved)
    for panel in visible_panels(window):
        qtbot.mouseClick(panel.retry_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: all_ready(window), timeout=3000)
    assert window.status.offline_label.isHidden()


def test_stale_result_for_previous_symbol_dropped(qtbot, window, fake_md):
    gate = threading.Event()
    fake_md.results["history"] = lambda s, r: (
        (gate.wait(3), make_series(s, r, (9.0, 9.0)))[1] if s == "AAPL" else make_series(s, r, (5.0, 6.0, 7.0))
    )
    window.select_symbol("AAPL")
    qtbot.waitUntil(lambda: window.current is not None, timeout=2000)
    select(qtbot, window, "SPY")
    qtbot.waitUntil(lambda: window.chart.series is not None, timeout=2000)
    gate.set()
    qtbot.wait(300)
    assert window.chart.series.symbol == "SPY"


def test_tabs_overview_valuation_watchlist(window):
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "OVERVIEW", "VALUATION", "TODAY", "WATCHLIST", "VALUE MAP", "ENTRY LADDER", "INCOME",
        "CALL BOOK", "TRACK RECORD", "TOP 20"]
    assert window.tabs.currentIndex() == OVERVIEW
    assert window.tabs.objectName() == "PageTabs"


def test_page_bar_groups_the_pages_and_hides_the_tab_bar(window):
    nav = window.page_nav
    assert window.tabs.tabBar().isHidden()
    groups = [g.text() for g in nav.findChildren(QLabel, "NavGroup")]
    assert groups == ["STOCK", "DESK", "DISCOVER"]
    assert [nav.buttons[i].text() for i in sorted(nav.buttons)] == list(PAGE_TITLES)
    assert nav.buttons[OVERVIEW].isChecked()


def test_page_bar_follows_the_current_page_and_switches_it(window):
    nav = window.page_nav
    window.tabs.setCurrentIndex(INCOME)
    assert nav.buttons[INCOME].isChecked() and not nav.buttons[OVERVIEW].isChecked()
    nav.buttons[TOP20].click()
    assert window.tabs.currentIndex() == TOP20


def test_alt_digits_open_pages_in_bar_order(window):
    keys = [s.key().toString() for s in window.page_nav.shortcuts]
    assert keys == [f"Alt+{d}" for d in "1234567890"]
    window.page_nav.shortcuts[2].activated.emit()
    assert window.tabs.currentIndex() == TODAY
    window.page_nav.shortcuts[9].activated.emit()
    assert window.tabs.currentIndex() == TOP20
    assert "Alt+3" in window.page_nav.buttons[TODAY].toolTip()


def test_the_page_bar_never_makes_the_window_wider_or_taller_than_before(window):
    # main before the page bar: 1016 x 893; small laptops must still fit the window.
    size = window.minimumSizeHint()
    assert size.width() <= 1016 and size.height() <= 893


def test_the_app_opens_on_today(qtbot, loader, store, fake_analyst):
    w = MainWindow(loader, store, analyst=fake_analyst)
    qtbot.addWidget(w)
    assert w.tabs.currentIndex() == TODAY
    assert w.page_nav.buttons[TODAY].isChecked()


def test_valuation_loads_lazily_when_tab_shown(qtbot, window, fake_md):
    select(qtbot, window, "AAPL")
    assert fake_md.calls_to("valuation_inputs") == []
    window.toggle_page()
    assert window.tabs.currentIndex() == VALUATION
    qtbot.waitUntil(lambda: window.valuation.state is PanelState.READY, timeout=3000)
    assert window.valuation.inputs.symbol == "AAPL"
    select(qtbot, window, "SHEL.L")
    qtbot.waitUntil(lambda: window.valuation.state is PanelState.READY and window.valuation.inputs.symbol == "SHEL.L", timeout=3000)
    window.toggle_page()
    select(qtbot, window, "SPY")
    qtbot.wait(100)
    assert [c[1] for c in fake_md.calls_to("valuation_inputs")] == ["AAPL", "SHEL.L"]


def test_page_shortcuts_cycle_every_tab(window):
    f2, ctrl_tab, back = window.page_shortcuts
    mod = main_window.TAB_MODIFIER
    assert [s.key().toString() for s in window.page_shortcuts] == ["F2", f"{mod}+Tab", f"{mod}+Shift+Tab"]
    f2.activated.emit()
    assert window.tabs.currentIndex() == VALUATION
    ctrl_tab.activated.emit()
    assert window.tabs.currentIndex() == VALUATION + 1
    window.tabs.setCurrentIndex(TOP20)
    f2.activated.emit()
    assert window.tabs.currentIndex() == OVERVIEW
    back.activated.emit()
    assert window.tabs.currentIndex() == TOP20


def test_ctrl_number_returns_to_overview_and_escape_only_on_overview(qtbot, window):
    window.toggle_page()
    qtbot.mouseDClick(window.news.title, Qt.MouseButton.LeftButton)
    window.tabs.setCurrentIndex(VALUATION)
    window.esc_shortcut.activated.emit()
    assert window.grid.maximised is not None  # Esc ignored on VALUATION
    window.tabs.setCurrentIndex(OVERVIEW)
    window.esc_shortcut.activated.emit()
    assert window.grid.maximised is None
    window.tabs.setCurrentIndex(VALUATION)
    window._maximise_overview(lambda: window.chart)
    assert window.tabs.currentIndex() == OVERVIEW and window.grid.maximised is window.chart


def _gate_valuation(fake_md):
    gate = threading.Event()

    def slow(symbol):
        gate.wait(5)
        return make_valuation_inputs(symbol)

    fake_md.results["valuation_inputs"] = slow
    return gate


def test_reselect_same_symbol_while_valuation_in_flight_still_reaches_ready(qtbot, window, fake_md):
    gate = _gate_valuation(fake_md)
    window.tabs.setCurrentIndex(VALUATION)
    select(qtbot, window, "AAPL")
    qtbot.waitUntil(lambda: window.valuation.state is PanelState.LOADING, timeout=2000)
    window.select_symbol("AAPL")
    qtbot.wait(300)
    gate.set()
    qtbot.waitUntil(lambda: window.valuation.state is PanelState.READY, timeout=4000)
    assert window.valuation.inputs.symbol == "AAPL"


def test_reselect_same_symbol_on_overview_then_switch_loads_valuation(qtbot, window, fake_md):
    gate = _gate_valuation(fake_md)
    window.tabs.setCurrentIndex(VALUATION)
    select(qtbot, window, "AAPL")
    qtbot.waitUntil(lambda: window.valuation.state is PanelState.LOADING, timeout=2000)
    window.tabs.setCurrentIndex(OVERVIEW)
    window.select_symbol("AAPL")
    qtbot.wait(300)
    gate.set()
    qtbot.wait(200)
    window.tabs.setCurrentIndex(VALUATION)
    qtbot.waitUntil(lambda: window.valuation.state is PanelState.READY, timeout=4000)
    assert window.valuation.inputs.symbol == "AAPL"


def test_ctrl_number_from_valuation_maximises_instead_of_toggling(qtbot, window):
    window.tabs.setCurrentIndex(OVERVIEW)
    window.grid.maximise(window.chart)
    window.tabs.setCurrentIndex(VALUATION)
    window._maximise_overview(lambda: window.chart)
    assert window.tabs.currentIndex() == OVERVIEW and window.grid.maximised is window.chart
    window._maximise_overview(lambda: window.chart)  # already on OVERVIEW: toggles
    assert window.grid.maximised is None


def test_open_button_hides_when_status_message_clears(qtbot, window):
    window.status.show_report("/tmp/x.pdf")
    assert not window.status.open_report_button.isHidden()
    window.status.clearMessage()
    assert window.status.open_report_button.isHidden()


def test_export_shows_path_and_open_action(qtbot, window, fake_analyst):
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL")
    select(qtbot, window, "AAPL")
    window.toggle_page()
    qtbot.waitUntil(lambda: window.valuation.state is PanelState.READY, timeout=3000)
    opened = []
    window.status.open_file = opened.append
    path = window.valuation.export_report()
    assert window.status.currentMessage() == f"Report saved to {path}"
    assert not window.status.open_report_button.isHidden()
    window.status.open_report_button.click()
    assert opened == [str(path)]


def test_ai_missing_rest_of_page_works(qtbot, window, fake_analyst):
    fake_analyst.available = "Claude Code not found — install it and run `claude` once to log in"
    select(qtbot, window, "AAPL")
    window.toggle_page()
    qtbot.waitUntil(lambda: window.valuation.state is PanelState.READY, timeout=3000)
    panel = window.valuation.analyst_panel
    assert panel.state is AiState.IDLE and panel.hint.text() == fake_analyst.available
    panel.run()
    assert panel.state is AiState.ERROR
    assert window.valuation.valuation.blended is not None


def test_double_click_maximises_and_escape_restores(qtbot, window):
    qtbot.mouseDClick(window.news.title, Qt.MouseButton.LeftButton)
    assert window.chart.isHidden()
    assert not window.news.isHidden()
    window.esc_shortcut.activated.emit()
    assert not window.chart.isHidden()
    assert window.grid.maximised is None


def test_watchlist_and_last_symbol_persist(qtbot, loader, window, store):
    select(qtbot, window, "AAPL")
    window.watchlist.add_current()
    assert store.load().state == AppState(("AAPL",), "AAPL")
    w2 = MainWindow(loader, store, analyst=FakeAnalyst())
    qtbot.addWidget(w2)
    assert w2.watchlist.symbols() == ["AAPL"]
    qtbot.waitUntil(lambda: w2.current is not None and w2.current.symbol == "AAPL", timeout=2000)


def test_corrupt_watchlist_recovered(qtbot, loader, store):
    store.path.write_text("{not json")
    w = MainWindow(loader, store, analyst=FakeAnalyst())
    qtbot.addWidget(w)
    assert w.watchlist.symbols() == []
    assert "unreadable" in w.status.currentMessage()
    assert store.path.with_name("watchlist.json.bad").exists()


def test_ui_never_imports_openbb():
    assert not any(name.startswith(("openbb", "yfinance")) for name in sys.modules)


# --- WATCHLIST tab (v1.2) -------------------------------------------------------------------

WATCHED = ("AAPL", "SHEL.L", "PETR4.SA", "HGLG11.SA")


@pytest.fixture
def watched(qtbot, loader, store, fake_analyst):
    store.save(AppState(WATCHED))
    w = MainWindow(loader, store, analyst=fake_analyst)
    qtbot.addWidget(w)
    w.tabs.setCurrentIndex(OVERVIEW)
    w.show()
    yield w
    w.shutdown_jobs()


def test_watchlist_tab_loads_when_shown(qtbot, watched, fake_md):
    from obb_terminal.ui.watchlist_tab.model import Col, RowState

    tab = watched.watchlist_tab
    assert fake_md.calls_to("valuation_inputs") == []
    watched.tabs.setCurrentIndex(WATCHLIST)
    qtbot.waitUntil(lambda: all(tab.model.row(s).state is RowState.READY for s in WATCHED), timeout=3000)
    assert tab.model.data(tab.model.index(0, Col.PRICE)) == "336.64 USD"
    assert watched.status.targets_label.text() == "0 targets hit"
    assert not watched.status.targets_label.isHidden()


def test_one_symbol_list_shared_by_strip_tab_and_store(qtbot, watched, store):
    tab = watched.watchlist_tab
    tab.addRequested.emit("SPY")
    assert watched.watchlist.symbols() == [*WATCHED, "SPY"]
    assert tab.model.symbols() == [*WATCHED, "SPY"]
    assert store.load().state.symbols == (*WATCHED, "SPY")
    tab.addRequested.emit("SPY")  # already there: no duplicate
    assert watched.watchlist.symbols().count("SPY") == 1
    watched.watchlist.remove("AAPL")  # from the strip
    assert tab.model.symbols() == ["SHEL.L", "PETR4.SA", "HGLG11.SA", "SPY"]
    tab.removeRequested.emit("SPY")  # from the tab
    assert watched.watchlist.symbols() == ["SHEL.L", "PETR4.SA", "HGLG11.SA"]
    assert store.load().state.symbols == ("SHEL.L", "PETR4.SA", "HGLG11.SA")


def test_activating_a_row_opens_it_in_overview(qtbot, watched):
    watched.tabs.setCurrentIndex(WATCHLIST)
    watched.watchlist_tab.symbolActivated.emit("SHEL.L")
    assert watched.tabs.currentIndex() == OVERVIEW
    qtbot.waitUntil(lambda: watched.current is not None and watched.current.symbol == "SHEL.L", timeout=2000)


def test_hits_and_messages_reach_the_status_bar(qtbot, watched, alerts):
    from obb_terminal.domain.targets import Indicator

    watched.tabs.setCurrentIndex(WATCHLIST)
    tab = watched.watchlist_tab
    qtbot.waitUntil(lambda: watched.status.targets_label.isVisible(), timeout=3000)
    tab.save_target("AAPL", Indicator.PRICE, 400.0, True)
    qtbot.waitUntil(lambda: watched.status.targets_label.text() == "✓ 1 target hit", timeout=3000)
    qtbot.waitUntil(lambda: watched.status.currentMessage() == "AAPL: 1 buy alert — see notifications",
                    timeout=3000)
    assert len(alerts.notifier.sent) == 1


def test_ai_call_becomes_watchlist_targets(qtbot, watched):
    panel = watched.valuation.analyst_panel
    assert panel.targets_lookup("AAPL") == (None, True)
    assert panel.targets_lookup("MSFT") == (None, False)
    panel.useAsTargets.emit("MSFT", 300.0, 250.0, True)
    assert "MSFT" in watched.watchlist.symbols()
    engine = watched.watchlist_tab._engine
    qtbot.waitUntil(lambda: engine.snapshot().file.get("MSFT") is not None, timeout=2000)
    st = engine.snapshot().file.get("MSFT")
    assert st.targets[0].level == 300.0 and st.thesis.level == 250.0
    assert watched.status.currentMessage() == "MSFT: saving watchlist targets from the AI call…"



# --- TRACK RECORD tab (v1.3) ----------------------------------------------------------------


def test_journal_records_and_track_record_opens_symbols_in_valuation(qtbot, loader, store, fake_analyst, tmp_path):
    from obb_terminal.journal.store import Journal

    journal = Journal(tmp_path / "journal.sqlite3")
    w = MainWindow(loader, store, analyst=fake_analyst, journal=journal, score_delay_ms=None)
    qtbot.addWidget(w)
    w.show()
    select(qtbot, w, "AAPL")
    w.tabs.setCurrentIndex(VALUATION)
    qtbot.waitUntil(lambda: len(journal.rows()) == 1, timeout=3000)
    w.tabs.setCurrentIndex(TRACK)
    tab = w.track_record
    qtbot.waitUntil(lambda: tab.recent_model.rowCount() == 1, timeout=2000)
    tab.symbolActivated.emit("SHEL.L")
    assert w.tabs.currentIndex() == VALUATION
    qtbot.waitUntil(lambda: w.current is not None and w.current.symbol == "SHEL.L", timeout=2000)
    w.shutdown_jobs()


def test_startup_scoring_is_scheduled_only_with_a_journal(qtbot, loader, store, fake_analyst, tmp_path, monkeypatch):
    from obb_terminal.journal.store import Journal
    from obb_terminal.ui.track_record import TrackRecordTab

    calls = []
    monkeypatch.setattr(TrackRecordTab, "score_if_due", lambda self: calls.append(self))
    w = MainWindow(loader, store, analyst=fake_analyst, journal=Journal(tmp_path / "j.sqlite3"), score_delay_ms=10)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=2000)
    plain = MainWindow(loader, store, analyst=fake_analyst, score_delay_ms=10)
    qtbot.addWidget(plain)
    qtbot.wait(50)
    assert len(calls) == 1
    w.shutdown_jobs()
    plain.shutdown_jobs()


def test_top20_tab_loads_on_first_visit_and_opens_overview(qtbot, window, screener):
    from obb_terminal.ui.main_window import TOP20
    from obb_terminal.ui.screener import TICKER_COL

    assert screener.calls == 0  # nothing fetched until the tab is shown
    window.tabs.setCurrentIndex(TOP20)
    qtbot.waitUntil(lambda: window.screener.table.rowCount() > 0, timeout=2000)
    assert screener.calls == 1
    window.screener.table.cellDoubleClicked.emit(0, TICKER_COL)
    assert window.tabs.currentIndex() == OVERVIEW
    qtbot.waitUntil(lambda: window.current is not None, timeout=2000)
    assert window.current.symbol == "PETR4.SA"


def test_top20_plus_adds_to_the_watchlist_everywhere(qtbot, window, store):
    from obb_terminal.ui.main_window import TOP20
    from obb_terminal.ui.screener import ADD_COL

    window.tabs.setCurrentIndex(TOP20)
    qtbot.waitUntil(lambda: window.screener.table.rowCount() > 0, timeout=2000)
    button = window.screener.table.cellWidget(0, ADD_COL)
    button.click()
    assert "PETR4.SA" in window.watchlist.symbols()
    assert "PETR4.SA" in store.load().state.symbols
    assert button.text() == "✓" and not button.isEnabled()
    window.remove_symbol("PETR4.SA")
    assert button.text() == "+" and button.isEnabled()


def test_etf_without_fund_data_opens_on_dividends(qtbot, window, fake_md):
    from obb_terminal.domain.models import FundProfile

    fake_md.results["fund_profile"] = lambda s: FundProfile(s, gaps=("no fund data",))
    select(qtbot, window, "SPY")
    qtbot.waitUntil(lambda: window.grid.analysis_panel() is window.fund, timeout=2000)
    qtbot.waitUntil(lambda: window.fund.state is PanelState.READY, timeout=2000)
    assert window.fund.holdings_button.isHidden()
