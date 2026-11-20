"""The dashboards in the real main window, on the fake data source."""

from datetime import timedelta

import pytest
from PySide6.QtCore import QPointF, Qt

from obb_terminal.domain.targets import Indicator
from obb_terminal.journal.models import Kind
from obb_terminal.journal.store import Journal
from obb_terminal.store import AppState, Store
from obb_terminal.ui.desk.ladder import track_x
from obb_terminal.ui.desk.value_map import map_position
from obb_terminal.ui.main_window import (
    CALL_BOOK,
    INCOME,
    LADDER,
    OVERVIEW,
    TODAY,
    VALUATION,
    VALUE_MAP,
    WATCHLIST,
    MainWindow,
)
from tests.journal.samples import snap

SYMBOLS = ("AAPL", "SHEL.L", "SPY")


@pytest.fixture
def desk(qtbot, loader, tmp_path, fake_analyst):
    store = Store(tmp_path / "watchlist.json")
    store.save(AppState(symbols=SYMBOLS))
    journal = Journal(tmp_path / "journal.sqlite3")
    w = MainWindow(loader, store, analyst=fake_analyst, journal=journal, score_delay_ms=None)
    qtbot.addWidget(w)
    w.show()
    w.journal = journal
    yield w
    w.shutdown_jobs()


def settled(w):
    return w.value_map.table.rowCount() == 2 and w.income.table.rowCount() == 3


def test_the_app_opens_on_today_and_loads_the_watchlist(qtbot, desk):
    assert desk.tabs.currentIndex() == TODAY
    assert desk.tabs.tabText(TODAY) == "TODAY"
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    assert desk.today.title.text() == "Nothing needs a look today"
    assert desk.today.queue.rowCount() == 0


def test_today_lists_a_hit_target_and_its_button_opens_valuation(qtbot, desk):
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    desk.watchlist_tab.save_target("AAPL", Indicator.PRICE, 9000.0, True)
    qtbot.waitUntil(lambda: desk.today.queue.rowCount() == 1, timeout=3000)
    assert desk.today.title.text() == "1 decision waiting"
    assert desk.today.queue.item(0, 1).text() == "Price target hit"
    button = desk.today.queue.cellWidget(0, 4)
    assert button.text() == "Open valuation"
    button.click()
    assert desk.tabs.currentIndex() == VALUATION
    qtbot.waitUntil(lambda: desk.current is not None and desk.current.symbol == "AAPL", timeout=2000)


def test_today_shows_benchmarks_and_headlines(qtbot, desk):
    qtbot.waitUntil(lambda: desk.today.headlines.count() > 0, timeout=4000)
    qtbot.waitUntil(lambda: desk.today.benchmarks["SPY"][0].text() != "—", timeout=4000)
    assert desk.today.headlines.item(0).text().split()[0] in SYMBOLS


def test_today_only_opens_web_links(qtbot, desk):
    from PySide6.QtWidgets import QListWidgetItem

    opened = []
    desk.today.open_url = opened.append
    bad, good = QListWidgetItem("x"), QListWidgetItem("y")
    bad.setData(Qt.ItemDataRole.UserRole, "file:///etc/passwd")
    good.setData(Qt.ItemDataRole.UserRole, "https://example.com/a")
    desk.today._open_item(bad)
    desk.today._open_item(good)
    assert opened == ["https://example.com/a"]


def test_empty_watchlist_explains_itself(qtbot, loader, tmp_path, fake_analyst):
    w = MainWindow(loader, Store(tmp_path / "w.json"), analyst=fake_analyst, score_delay_ms=None)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: "empty" in w.today.title.text(), timeout=2000)
    assert not w.today.add_button.isHidden()
    w.today.add_button.click()
    assert w.tabs.currentIndex() == WATCHLIST
    w.shutdown_jobs()


def test_value_map_shows_stocks_and_explains_the_etf(qtbot, desk):
    desk.tabs.setCurrentIndex(VALUE_MAP)
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    assert {desk.value_map.table.item(r, 0).text() for r in range(2)} == {"AAPL", "SHEL.L"}
    assert "SPY" in desk.value_map.left_out.text()
    assert "AAPL" in desk.value_map.map.accessibleDescription()
    desk.value_map.grab()  # paints without error


def test_today_shows_distance_to_fair_value_and_a_small_value_map(qtbot, desk):
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    rows = desk.today.distance.rows
    assert {r.symbol for r in rows} == {"AAPL", "SHEL.L"}
    assert abs(rows[0].upside) <= abs(rows[-1].upside)
    assert {p.symbol for p in desk.today.map.points} == {"AAPL", "SHEL.L"}
    desk.today.grab()  # paints without error
    desk.today.distance.rowActivated.emit("SHEL.L")
    assert desk.tabs.currentIndex() == VALUATION


def test_value_map_labels_of_stocks_on_the_same_spot_do_not_overlap(qtbot):
    from obb_terminal.ui.desk.value_map import MapWidget
    from obb_terminal.ui.desk.views import ValuePoint

    m = MapWidget(compact=True)
    qtbot.addWidget(m)
    m.resize(400, 300)
    m.set_points([ValuePoint(s, 10.0, "GBP", 20.0, 0.9, 85.0) for s in ("SHEL.L", "PETR4.SA", "BP.L")])
    rects = []
    m._label_rect = (lambda orig: lambda *a: rects.append(orig(*a)) or rects[-1])(m._label_rect)
    m.grab()
    assert len(rects) == 3
    assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:])


def test_today_clicking_a_map_point_selects_it(qtbot, desk):
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    desk.today.map.pointClicked.emit("AAPL")
    assert desk.today.map.selected == "AAPL"


def test_today_map_button_opens_the_full_value_map(qtbot, desk):
    desk.today.map_button.click()
    assert desk.tabs.currentIndex() == VALUE_MAP


def test_value_map_click_selects_and_double_click_opens(qtbot, desk):
    desk.tabs.setCurrentIndex(VALUE_MAP)
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    m = desk.value_map.map
    point = m.points[0]
    at = m._at(point)
    assert m.point_at(at) == point.symbol
    assert m.point_at(QPointF(-100, -100)) is None
    m.pointClicked.emit(point.symbol)
    assert m.selected == point.symbol
    m.pointActivated.emit(point.symbol)
    assert desk.tabs.currentIndex() == VALUATION


def test_map_position_holds_outliers_on_the_edge():
    assert map_position(0.0, 50.0, 100, 100) == (50.0, 50.0)
    assert map_position(5.0, 100.0, 100, 100) == (100.0, 0.0)
    assert map_position(-5.0, 0.0, 100, 100) == (0.0, 100.0)


def test_ladder_offers_set_target_until_there_is_one(qtbot, desk):
    desk.tabs.setCurrentIndex(LADDER)
    qtbot.waitUntil(lambda: len(desk.ladder.rows) == 3, timeout=4000)
    button = desk.ladder.buttons["AAPL"]
    assert button.text() == "Set target"
    button.click()
    assert desk.watchlist_tab.editor is not None and desk.watchlist_tab.editor.symbol == "AAPL"
    desk.watchlist_tab.editor.close()
    desk.ladder.grab()


def test_track_x_centres_the_price():
    assert track_x(0.0, 100) == 50.0
    assert track_x(9.0, 100) == 100.0
    assert track_x(-9.0, 100) == 0.0


def test_income_shows_twelve_months_and_opens_the_yield_editor(qtbot, desk):
    desk.tabs.setCurrentIndex(INCOME)
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    t = desk.income.table
    assert t.columnCount() == 16
    assert not desk.income.target_button.isEnabled()
    t.selectRow(0)
    assert desk.income.target_button.isEnabled()
    desk.income.target_button.click()
    assert desk.watchlist_tab.editor is not None
    desk.watchlist_tab.editor.close()
    t.cellDoubleClicked.emit(0, 0)
    assert desk.tabs.currentIndex() == OVERVIEW


def test_call_book_lists_journal_calls(qtbot, desk):
    from datetime import date

    desk.journal.record(snap(symbol="AAPL", kind=Kind.AI, action="buy", conviction="high", direction=1,
                             strength=3.0, components=(), day=date.today() - timedelta(days=1),
                             thesis_break=1.0))
    desk.tabs.setCurrentIndex(CALL_BOOK)
    qtbot.waitUntil(lambda: desk.call_book.table.rowCount() == 1, timeout=4000)
    t = desk.call_book.table
    assert t.item(0, 0).text() == "AAPL" and t.item(0, 3).text() == "Buy (high)"
    qtbot.waitUntil(lambda: t.item(0, 7).text() == "Alive", timeout=4000)
    assert desk.call_book._progress[0][0].text() == "AI analyst: 0 of 30 scored at 20 days"
    t.cellDoubleClicked.emit(0, 0)
    assert desk.tabs.currentIndex() == VALUATION


def test_call_book_says_when_the_journal_is_off(qtbot, loader, tmp_path, fake_analyst):
    w = MainWindow(loader, Store(tmp_path / "w.json"), analyst=fake_analyst, score_delay_ms=None)
    qtbot.addWidget(w)
    w.show()
    w.tabs.setCurrentIndex(CALL_BOOK)
    qtbot.waitUntil(lambda: "journal is off" in w.call_book.note.text(), timeout=2000)
    w.shutdown_jobs()


def test_a_story_about_two_symbols_is_listed_once(qtbot, desk):
    from datetime import UTC, datetime

    from obb_terminal.domain.models import NewsItem

    story = NewsItem("Same story", "https://example.com/a", datetime(2026, 1, 1, tzinfo=UTC), None)
    desk.today._symbols = ["AAPL", "SPY"]
    desk.today._news = {"AAPL": [story], "SPY": [story]}
    desk.today._show_headlines()
    assert [desk.today.headlines.item(i).text() for i in range(desk.today.headlines.count())] == [
        "AAPL, SPY  Same story"]


def test_today_refreshes_the_market_only_while_it_is_showing(qtbot, desk, fake_md):
    desk.today.set_active(True)
    assert desk.today._timer.isActive()
    desk.today.set_active(False)
    assert not desk.today._timer.isActive()
    qtbot.waitUntil(lambda: len(fake_md.calls_to("quote")) >= 3, timeout=3000)
    before = len(fake_md.calls_to("quote"))
    desk.today.set_active(True)  # shown again soon after: no new fetch
    qtbot.wait(100)
    assert len(fake_md.calls_to("quote")) == before
    desk.today._timer.timeout.emit()  # the timer always fetches
    qtbot.waitUntil(lambda: len(fake_md.calls_to("quote")) == before + 3, timeout=3000)


def test_dashboards_refresh_the_watchlist_less_often_than_its_own_tab(qtbot, desk):
    desk.tabs.setCurrentIndex(LADDER)
    slow = desk.watchlist_tab._timer.interval()
    desk.tabs.setCurrentIndex(WATCHLIST)
    assert slow == desk.watchlist_tab._timer.interval() * 3


def test_ladder_paints_a_row_with_every_level(qtbot):
    from obb_terminal.ui.desk.ladder import LadderTrack, describe
    from obb_terminal.ui.desk.views import LadderRow

    row = LadderRow("AAPL", 100.0, "USD", -0.1, (-0.2, -0.1), -0.3, 0.9)
    track = LadderTrack(row)
    qtbot.addWidget(track)
    track.resize(400, 58)
    assert not track.grab().isNull()
    assert describe(row) == ("AAPL, price 100.00 USD, your buy level -10%, AI entry zone -20% to -10%, "
                             "thesis break -30%, fair value +90%")


def test_value_map_refresh_fetches_fresh_data_and_says_when(qtbot, desk, fake_md):
    desk.tabs.setCurrentIndex(VALUE_MAP)
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    before = len(fake_md.calls_to("valuation_inputs"))
    desk.value_map.refresh_button.click()
    qtbot.waitUntil(lambda: len(fake_md.calls_to("valuation_inputs")) >= before + len(SYMBOLS), timeout=4000)


def test_partial_data_is_fetched_again_soon(qtbot, desk, fake_md):
    from dataclasses import replace

    from obb_terminal.alerts.engine import ApplyResult
    from tests.fakes import make_valuation_inputs

    tab = desk.watchlist_tab
    tab.gap_retry_ms = 50
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    aapl = ("valuation_inputs", "AAPL")
    calls = fake_md.calls_to("valuation_inputs").count(aapl)
    partial_inputs = replace(make_valuation_inputs("AAPL"), gaps=("targets",))
    tab._in_flight.add("AAPL")
    good = tab._engine.apply([partial_inputs])
    assert isinstance(good, ApplyResult)
    tab._on_result("AAPL", (partial_inputs, good))
    qtbot.waitUntil(lambda: fake_md.calls_to("valuation_inputs").count(aapl) > calls, timeout=3000)


def test_hidden_watchlist_does_not_retry_partial_data(qtbot, desk, fake_md):
    tab = desk.watchlist_tab
    qtbot.waitUntil(lambda: settled(desk), timeout=4000)
    desk.tabs.setCurrentIndex(VALUATION)
    calls = len(fake_md.calls_to("valuation_inputs"))
    tab._retry_gaps("AAPL")
    qtbot.wait(100)
    assert len(fake_md.calls_to("valuation_inputs")) == calls
