import threading
from datetime import UTC, datetime, timedelta

from obb_terminal.data.market import SourceUnavailable
from obb_terminal.data.screen_cache import load_snapshot, save_snapshot
from obb_terminal.domain.cheap_screen import Market, MarketSnapshot, ScreenStock
from obb_terminal.ui.screener import ADD_COL, EV_EBIT_COL, TICKER_COL, ScreenerPage, ScreenerSource

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def b3_stock(ticker, ev_ebit):
    return ScreenStock(
        ticker=ticker,
        name=f"{ticker} SA",
        price=10.0,
        pl=5.0,
        pvp=1.0,
        ev_ebit=ev_ebit,
        ebit_margin=0.2,
        roic=0.15,
        liquidity=5e6,
    )


def b3_snapshot(n=25, at=NOW):
    roots = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}XX3" for i in range(n)]
    return MarketSnapshot(tuple(b3_stock(t, 1.0 + i) for i, t in enumerate(roots)), frozenset(), at)


def uk_snapshot(at=NOW):
    stocks = tuple(
        ScreenStock(
            ticker=f"U{i:03d}.L",
            name=f"UK Co {i}",
            price=2.0,
            pl=6.0,
            pvp=1.1,
            ev_ebit=None,
            ebit_margin=None,
            roic=None,
            liquidity=1e6,
            company=f"UK Co {i}",
        )
        for i in range(30)
    )
    return MarketSnapshot(stocks, frozenset(), at, presorted=True)


class FakeFetch:
    def __init__(self, result, error=None, gate=None):
        self.result, self.error, self.gate, self.calls = result, error, gate, 0

    def __call__(self):
        self.calls += 1
        if self.gate:
            self.gate.wait(2)
        if self.error:
            raise self.error
        return self.result


def make(qtbot, loader, tmp_path, b3=None, uk=None, us=None):
    fetches = {
        Market.B3: b3 or FakeFetch(b3_snapshot()),
        Market.UK: uk or FakeFetch(uk_snapshot()),
        Market.US: us or FakeFetch(uk_snapshot()),
    }
    sources = {
        m: ScreenerSource(f, tmp_path / "screener" / f"{m.value.lower()}.json", clock=lambda: NOW)
        for m, f in fetches.items()
    }
    page = ScreenerPage(loader, sources)
    qtbot.addWidget(page)
    return page, sources, fetches


def tickers(page):
    return [page.table.item(r, TICKER_COL).text() for r in range(page.table.rowCount())]


def add_button(page, row):
    return page.table.cellWidget(row, ADD_COL)


def test_first_open_shows_b3_top_20_and_saves_it(qtbot, loader, tmp_path):
    page, sources, fetches = make(qtbot, loader, tmp_path)
    page.set_active(True)
    qtbot.waitUntil(lambda: page.table.rowCount() == 20, timeout=2000)
    assert tickers(page)[0] == "AAXX3"
    assert page.market is Market.B3
    assert "B3" in page.title.text()
    assert fetches[Market.UK].calls == 0  # other markets load when chosen
    assert load_snapshot(sources[Market.B3].cache_path) is not None


def test_fresh_cache_shows_without_fetching(qtbot, loader, tmp_path):
    page, sources, fetches = make(qtbot, loader, tmp_path)
    save_snapshot(sources[Market.B3].cache_path, b3_snapshot(at=NOW - timedelta(hours=2)))
    page.set_active(True)
    assert page.table.rowCount() == 20
    qtbot.wait(100)
    assert fetches[Market.B3].calls == 0


def test_stale_cache_shows_then_refetches(qtbot, loader, tmp_path):
    page, sources, _ = make(qtbot, loader, tmp_path, b3=FakeFetch(b3_snapshot(n=3)))
    save_snapshot(sources[Market.B3].cache_path, b3_snapshot(at=NOW - timedelta(days=2)))
    page.set_active(True)
    assert page.table.rowCount() == 20
    qtbot.waitUntil(lambda: page.table.rowCount() == 3, timeout=2000)


def test_failure_keeps_the_last_list_and_says_why(qtbot, loader, tmp_path):
    error = SourceUnavailable("Fundamentus is unavailable right now", reason="network")
    page, sources, _ = make(qtbot, loader, tmp_path, b3=FakeFetch(None, error=error))
    save_snapshot(sources[Market.B3].cache_path, b3_snapshot(at=NOW - timedelta(days=2)))
    page.set_active(True)
    qtbot.waitUntil(lambda: "unavailable" in page.status.text(), timeout=2000)
    assert page.table.rowCount() == 20


def test_refresh_button_refetches_and_disables_while_running(qtbot, loader, tmp_path):
    gate = threading.Event()
    page, sources, fetches = make(qtbot, loader, tmp_path, b3=FakeFetch(b3_snapshot(), gate=gate))
    save_snapshot(sources[Market.B3].cache_path, b3_snapshot())
    page.set_active(True)
    page.refresh_button.click()
    assert not page.refresh_button.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: page.refresh_button.isEnabled(), timeout=2000)
    assert fetches[Market.B3].calls == 1


def test_switching_to_uk_loads_it_and_hides_figures_yahoo_lacks(qtbot, loader, tmp_path):
    page, _, fetches = make(qtbot, loader, tmp_path)
    page.set_active(True)
    qtbot.waitUntil(lambda: page.table.rowCount() == 20, timeout=2000)
    assert not page.table.isColumnHidden(EV_EBIT_COL)
    page.market_buttons[Market.UK].click()
    qtbot.waitUntil(lambda: tickers(page)[:1] == ["U000.L"], timeout=2000)
    assert page.table.rowCount() == 20
    assert page.table.isColumnHidden(EV_EBIT_COL)
    assert "UK" in page.title.text()
    assert "£/day" in [
        page.table.horizontalHeaderItem(c).text() for c in range(page.table.columnCount())
    ]
    page.market_buttons[Market.B3].click()
    assert tickers(page)[0] == "AAXX3"  # already loaded, shown straight away
    assert fetches[Market.B3].calls == 1


def test_a_late_answer_for_another_market_does_not_replace_the_shown_list(qtbot, loader, tmp_path):
    gate = threading.Event()
    page, _, _ = make(qtbot, loader, tmp_path, uk=FakeFetch(uk_snapshot(), gate=gate))
    page.set_active(True)
    qtbot.waitUntil(lambda: page.table.rowCount() == 20, timeout=2000)
    page.market_buttons[Market.UK].click()
    page.market_buttons[Market.B3].click()
    gate.set()
    qtbot.wait(200)
    assert tickers(page)[0] == "AAXX3"


def test_double_click_opens_the_stock_by_its_yahoo_symbol(qtbot, loader, tmp_path):
    page, _, _ = make(qtbot, loader, tmp_path)
    page.set_active(True)
    qtbot.waitUntil(lambda: page.table.rowCount() == 20, timeout=2000)
    with qtbot.waitSignal(page.openOverview, timeout=1000) as blocker:
        page.table.cellDoubleClicked.emit(2, TICKER_COL)
    assert blocker.args == ["ACXX3.SA"]
    page.market_buttons[Market.UK].click()
    qtbot.waitUntil(lambda: page.table.rowCount() == 20, timeout=2000)
    with qtbot.waitSignal(page.openOverview, timeout=1000) as blocker:
        page.table.cellDoubleClicked.emit(0, TICKER_COL)
    assert blocker.args == ["U000.L"]


def test_plus_adds_the_stock_and_turns_into_a_tick(qtbot, loader, tmp_path):
    page, _, _ = make(qtbot, loader, tmp_path)
    page.set_watchlist(["ABXX3.SA"])
    page.set_active(True)
    qtbot.waitUntil(lambda: page.table.rowCount() == 20, timeout=2000)
    assert add_button(page, 1).text() == "✓"  # already watched
    assert not add_button(page, 1).isEnabled()
    first = add_button(page, 0)
    assert first.text() == "+" and first.isEnabled()
    with qtbot.waitSignal(page.addRequested, timeout=1000) as blocker:
        first.click()
    assert blocker.args == ["AAXX3.SA"]
    page.set_watchlist(["ABXX3.SA", "AAXX3.SA"])  # the window confirms the change
    assert add_button(page, 0).text() == "✓"
