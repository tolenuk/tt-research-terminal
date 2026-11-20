"""The Watchlist's look: what each cell asks to be drawn, the grouped header and the hover row."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyleOptionViewItem

from obb_terminal.domain.models import Currency, Quote
from obb_terminal.domain.targets import (
    Indicator,
    SymbolTargets,
    Target,
    ThesisBreak,
    evaluate,
    failed_row,
)
from obb_terminal.ui.theme import TOKENS
from obb_terminal.ui.watchlist_tab.header import GroupedHeader
from obb_terminal.ui.watchlist_tab.heat import composite, contrast_ratio
from obb_terminal.ui.watchlist_tab.model import (
    COLUMN_GROUPS,
    Col,
    EdgeRole,
    HeatMatrixModel,
    HeatSortProxy,
    ReadinessRole,
    RowState,
    RowView,
    SubtextColourRole,
    SubtextRole,
    TrendRole,
    readiness_fraction,
    summary_text,
)
from obb_terminal.ui.watchlist_tab.view import HeatMatrixView
from tests.fakes import make_valuation_inputs

FG = Qt.ItemDataRole.ForegroundRole
BG = Qt.ItemDataRole.BackgroundRole
TIP = Qt.ItemDataRole.ToolTipRole
SORT = Qt.ItemDataRole.UserRole + 1


def ready(symbol, st=None, **changes):
    inputs = replace(make_valuation_inputs(symbol), **changes)
    return RowView(symbol, RowState.READY, evaluate(inputs, st), inputs)


def quote(symbol="AAPL", change=-5.8, pct=-0.0168):
    return Quote(symbol, Currency.USD, 338.83, change=change, change_pct=pct)


@pytest.fixture
def model():
    m = HeatMatrixModel()
    m.set_symbols(["AAPL", "SHEL.L"])
    return m


def data(model, col, role, symbol="AAPL"):
    return model.data(model.index(model.symbols().index(symbol), col), role)


def token(name):
    return TOKENS[name].lower()


# --- rows: name, day change and trend -------------------------------------------------------


def test_symbol_cell_has_the_company_name_underneath(model):
    assert data(model, Col.SYMBOL, SubtextRole) is None  # still loading
    model.set_row(ready("AAPL", name="Apple Inc."))
    assert data(model, Col.SYMBOL, Qt.ItemDataRole.DisplayRole) == "AAPL"
    assert data(model, Col.SYMBOL, SubtextRole) == "Apple Inc."


def test_ticker_is_text_coloured_and_only_the_price_is_amber(model):
    model.set_row(ready("AAPL", name="Apple Inc."))
    assert data(model, Col.SYMBOL, FG).name() == token("text")
    assert data(model, Col.PRICE, FG).name() == token("amber")


def test_price_cell_shows_the_day_change_in_its_direction(model):
    model.set_row(ready("AAPL"))
    assert data(model, Col.PRICE, SubtextRole) is None  # no quote yet
    model.set_quote(quote())
    assert data(model, Col.PRICE, SubtextRole) == "▼ -1.68%"
    assert data(model, Col.PRICE, SubtextColourRole).name() == token("down")
    assert "today ▼ -1.68%" in data(model, Col.PRICE, TIP)
    model.set_quote(quote(change=2.0, pct=0.01))
    assert data(model, Col.PRICE, SubtextRole) == "▲ +1.00%"
    assert data(model, Col.PRICE, SubtextColourRole).name() == token("up")


def test_quote_without_a_change_shows_nothing(model):
    model.set_row(ready("AAPL"))
    model.set_quote(quote(change=None, pct=None))
    assert data(model, Col.PRICE, SubtextRole) is None


def test_day_change_falls_back_to_the_last_two_closes(model):
    model.set_row(ready("AAPL"))
    model.set_quote(quote(change=None, pct=None))
    model.set_trend("AAPL", (100.0, 200.0, 190.0))
    assert data(model, Col.PRICE, SubtextRole) == "▼ -5.00%"
    assert data(model, Col.PRICE, SubtextColourRole).name() == token("down")
    model.set_quote(quote(change=2.0, pct=0.01))  # the quote's own figure wins
    assert data(model, Col.PRICE, SubtextRole) == "▲ +1.00%"


def test_trend_cell_gives_closes_month_return_and_sort_value(model):
    model.set_row(ready("AAPL"))
    assert data(model, Col.TREND, TrendRole) is None
    assert data(model, Col.TREND, Qt.ItemDataRole.DisplayRole) == "—"
    assert data(model, Col.TREND, SORT) is None
    model.set_trend("AAPL", (100.0, 90.0, 110.0))
    assert data(model, Col.TREND, TrendRole) == (100.0, 90.0, 110.0)
    assert data(model, Col.TREND, SORT) == pytest.approx(0.10)
    assert data(model, Col.TREND, TIP) == "AAPL over one month: +10.00%"
    assert data(model, Col.TREND, FG).name() == token("up")
    model.set_trend("AAPL", (100.0, 90.0))
    assert data(model, Col.TREND, FG).name() == token("down")


def test_a_single_close_is_not_a_trend(model):
    model.set_row(ready("AAPL"))
    model.set_trend("AAPL", (100.0,))
    assert data(model, Col.TREND, TrendRole) is None
    assert data(model, Col.TREND, SORT) is None


def test_quotes_and_trends_for_other_symbols_are_ignored_and_dropped_on_removal(model):
    model.set_quote(quote("MSFT"))
    model.set_trend("MSFT", (1.0, 2.0))
    model.set_row(ready("AAPL"))
    model.set_quote(quote())
    model.set_trend("AAPL", (1.0, 2.0))
    model.set_symbols(["SHEL.L"])
    model.set_symbols(["AAPL", "SHEL.L"])
    model.set_row(ready("AAPL"))
    assert data(model, Col.PRICE, SubtextRole) is None
    assert data(model, Col.TREND, TrendRole) is None


def test_quote_change_notifies_only_the_price_cell(qtbot, model):
    changes = []
    model.dataChanged.connect(lambda a, b, _r: changes.append((a.column(), b.column())))
    model.set_quote(quote())
    assert changes == [(Col.PRICE, Col.PRICE)]


# --- readiness ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("m_all", "all_hit", "expected"),
    [(None, False, None), (0.0, True, 1.0), (0.2, True, 1.0), (-0.15, False, 0.5), (-0.30, False, 0.0),
     (-0.42, False, 0.0)],
)
def test_readiness_fraction(m_all, all_hit, expected):
    assert readiness_fraction(m_all, all_hit) == (None if expected is None else pytest.approx(expected))


def test_readiness_sits_next_to_price(model):
    assert Col.READINESS == Col.PRICE + 1


def test_readiness_cell_reports_bar_and_tone(model):
    model.set_row(ready("AAPL"))
    assert data(model, Col.READINESS, ReadinessRole) is None
    near = SymbolTargets("AAPL", (Target(Indicator.PRICE, 320.0),))  # 4.9% away
    model.set_row(ready("AAPL", near))
    assert data(model, Col.READINESS, ReadinessRole) == pytest.approx(1 - 0.0494 / 0.30, abs=0.01)
    assert data(model, Col.READINESS, FG).name() == token("amber")
    far = SymbolTargets("AAPL", (Target(Indicator.PRICE, 200.0),))
    model.set_row(ready("AAPL", far))
    assert data(model, Col.READINESS, ReadinessRole) == 0.0
    assert data(model, Col.READINESS, FG).name() == token("amber_dim")
    assert "30%" in data(model, Col.READINESS, TIP)
    hit = SymbolTargets("AAPL", (Target(Indicator.PRICE, 400.0),))
    model.set_row(ready("AAPL", hit))
    assert data(model, Col.READINESS, ReadinessRole) == 1.0
    assert data(model, Col.READINESS, FG).name() == token("up")


# --- heat: text and marker instead of fills --------------------------------------------------


def test_only_hits_keep_a_fill(model):
    st = SymbolTargets(
        "AAPL",
        (Target(Indicator.PRICE, 400.0), Target(Indicator.PE, 42.0), Target(Indicator.PB, 1.0)),
    )
    model.set_row(ready("AAPL", st))
    hit, near, far = Col.PRICE_TARGET, Col.PE, Col.PB
    assert data(model, hit, BG) is not None
    assert data(model, near, BG) is None and data(model, far, BG) is None
    assert data(model, hit, FG).name() == token("up")
    assert data(model, near, FG).name() == token("amber")
    assert data(model, far, FG).name() == token("amber_dim")
    assert data(model, hit, EdgeRole).name() == token("up")
    assert data(model, near, EdgeRole).name() == token("amber")
    assert data(model, far, EdgeRole).name() == token("amber_dim")


def test_cells_without_a_target_have_no_marker_or_fill(model):
    model.set_row(ready("AAPL"))
    assert data(model, Col.PE, EdgeRole) is None
    assert data(model, Col.PE, BG) is None
    assert data(model, Col.PE, FG).name() == token("text")


def test_broken_thesis_is_red_with_a_marker(model):
    model.set_row(ready("AAPL", SymbolTargets("AAPL", thesis=ThesisBreak(350.0))))
    assert data(model, Col.THESIS, FG).name() == token("text")  # red on red would not be readable
    assert data(model, Col.THESIS, EdgeRole).name() == token("down")
    fill = data(model, Col.THESIS, BG)
    assert fill is not None
    for base in ("panel", "raised"):  # at rest and on a hovered row
        assert contrast_ratio(data(model, Col.THESIS, FG), composite(fill, QColor(TOKENS[base]))) >= 4.5


def test_hit_text_stays_readable_on_its_fill(model):
    model.set_row(ready("AAPL", SymbolTargets("AAPL", (Target(Indicator.PRICE, 400.0),))))
    fill = composite(data(model, Col.PRICE_TARGET, BG), QColor(TOKENS["panel"]))
    assert contrast_ratio(QColor(TOKENS["up"]), fill) >= 4.5


# --- missing values -------------------------------------------------------------------------


def test_missing_value_is_a_dim_dash_with_the_reason_in_the_tooltip(model):
    model.set_row(ready("AAPL", net_income=None, forward_pe=None))
    assert data(model, Col.PE, Qt.ItemDataRole.DisplayRole) == "—"
    assert data(model, Col.PE, FG).name() == token("muted")
    assert "not available — no earnings" in data(model, Col.PE, TIP)


def test_a_target_that_cannot_be_measured_stays_visible_with_its_reason(model):
    st = SymbolTargets("AAPL", (Target(Indicator.PE, 25.0),))
    model.set_row(ready("AAPL", st, net_income=None, forward_pe=None))
    assert data(model, Col.PE, Qt.ItemDataRole.DisplayRole) == "—  ≤ 25\nno earnings"
    assert data(model, Col.PE, FG).name() == token("muted")


def test_missing_fair_value_is_a_dash_with_the_reason_in_the_tooltip(model):
    model.set_row(ready("AAPL", fcf_history=(), net_income=None, dividends=(), analyst=None, forward_pe=None,
                        book_value_ps=None))
    assert data(model, Col.FAIR, Qt.ItemDataRole.DisplayRole) == "—"
    assert data(model, Col.FAIR, FG).name() == token("muted")
    assert "fair value: n/a" in data(model, Col.FAIR, TIP)


def test_targets_stay_visible_when_the_data_could_not_be_fetched(model):
    st = SymbolTargets("AAPL", (Target(Indicator.PE, 25.0),), thesis=ThesisBreak(300.0))
    model.set_row(RowView("AAPL", RowState.ERROR, failed_row("AAPL", "Timed out", st)))
    assert data(model, Col.PE, Qt.ItemDataRole.DisplayRole) == "—  ≤ 25\nTimed out"
    assert "target ≤ 25" in data(model, Col.PE, TIP) and "Timed out" in data(model, Col.PE, TIP)
    assert data(model, Col.THESIS, Qt.ItemDataRole.DisplayRole) == "300.00"
    assert data(model, Col.PB, Qt.ItemDataRole.DisplayRole) == "—"


def test_a_dash_is_read_out_as_its_reason(model):
    model.set_row(ready("AAPL", net_income=None, forward_pe=None))
    assert "no earnings" in data(model, Col.PE, Qt.ItemDataRole.AccessibleTextRole)
    assert data(model, Col.EV_EBITDA, Qt.ItemDataRole.AccessibleTextRole) == data(
        model, Col.EV_EBITDA, Qt.ItemDataRole.DisplayRole)


def test_company_name_is_in_the_tooltip_and_survives_a_failed_fetch(model):
    model.set_row(ready("AAPL", name="Apple Inc."))
    assert data(model, Col.SYMBOL, TIP).startswith("AAPL (Apple Inc.): price")
    model.set_row(RowView("AAPL", RowState.ERROR, failed_row("AAPL", "Timed out", None)))
    assert data(model, Col.SYMBOL, SubtextRole) == "Apple Inc."


def test_trend_is_only_drawn_for_rows_with_data(model):
    model.set_trend("AAPL", (1.0, 2.0))
    assert data(model, Col.TREND, TrendRole) is None  # still loading
    model.set_row(RowView("AAPL", RowState.ERROR, failed_row("AAPL", "x", None)))
    assert data(model, Col.TREND, TrendRole) is None


def test_bad_closes_are_left_out_of_the_trend(model):
    model.set_row(ready("AAPL"))
    model.set_trend("AAPL", (100.0, float("nan"), 110.0, float("nan")))
    assert data(model, Col.TREND, TrendRole) == (100.0, 110.0)


def test_fallback_change_is_not_called_today(model):
    model.set_row(ready("AAPL"))
    model.set_trend("AAPL", (100.0, 110.0))
    tip = data(model, Col.PRICE, TIP)
    assert "latest session" in tip and "today" not in tip


# --- column groups --------------------------------------------------------------------------


def test_groups_cover_every_column_once_and_in_order(model):
    columns = [c for _name, cols in COLUMN_GROUPS for c in cols]
    assert columns == list(Col)
    names = [name for name, _cols in COLUMN_GROUPS if name]
    assert names == ["Price", "Targets", "Valuation", "Income"]


def test_numeric_headers_line_up_with_their_numbers(model):
    align = Qt.ItemDataRole.TextAlignmentRole
    assert model.headerData(Col.SYMBOL, Qt.Orientation.Horizontal, align) & Qt.AlignmentFlag.AlignLeft
    assert model.headerData(Col.PE, Qt.Orientation.Horizontal, align) & Qt.AlignmentFlag.AlignRight


@pytest.fixture
def view(qtbot):
    source = HeatMatrixModel()
    source.set_symbols(["AAPL", "SHEL.L"])
    source.set_row(ready("AAPL", SymbolTargets("AAPL", (Target(Indicator.PRICE, 400.0),)), name="Apple Inc."))
    source.set_row(ready("SHEL.L", name="Shell plc"))
    source.set_quote(quote())
    source.set_trend("AAPL", (1.0, 2.0, 1.5))
    proxy = HeatSortProxy()
    proxy.setSourceModel(source)
    v = HeatMatrixView()
    v.setModel(proxy)
    v.sortByColumn(Col.SYMBOL, Qt.SortOrder.AscendingOrder)
    v.resize(1600, 300)
    qtbot.addWidget(v)
    v.show()
    qtbot.waitExposed(v)
    v._source = source
    return v


def test_header_is_taller_than_a_plain_one_and_clicks_on_group_labels_do_not_sort(qtbot, view):
    header = view.horizontalHeader()
    assert isinstance(header, GroupedHeader)
    assert header.height() > header.group_band()
    before = (header.sortIndicatorSection(), header.sortIndicatorOrder())
    x = header.sectionViewportPosition(Col.PE) + 5
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, 2))
    assert (header.sortIndicatorSection(), header.sortIndicatorOrder()) == before


def test_clicks_on_a_column_title_still_sort(qtbot, view):
    header = view.horizontalHeader()
    x = header.sectionViewportPosition(Col.PE) + 5
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, header.height() - 4))
    assert header.sortIndicatorSection() == Col.PE


def test_buy_at_header_explains_itself(model):
    assert "all of this stock" in model.headerData(Col.BUY_AT, Qt.Orientation.Horizontal, TIP)


def test_every_sort_arrow_position_paints(qtbot, view):
    for col, order in ((Col.SYMBOL, Qt.SortOrder.AscendingOrder), (Col.READINESS, Qt.SortOrder.DescendingOrder)):
        view.sortByColumn(col, order)
        assert not view.horizontalHeader().grab().isNull()


def test_the_view_has_no_grid(view):
    assert not view.showGrid()


def test_hover_tracks_the_row_and_clears_on_leave(qtbot, view):
    assert view.hovered_row() == -1
    pos = view.visualRect(view.model().index(1, Col.PE)).center()
    qtbot.mouseMove(view.viewport(), pos)
    qtbot.waitUntil(lambda: view.hovered_row() == 1, timeout=2000)
    view.clear_hover()
    assert view.hovered_row() == -1


def test_two_lines_fit_the_row_and_nothing_is_cut_off(view):
    """Every cell's text fits its column, as the old single-line target cells did not."""
    for col in Col:
        index = view.model().index(0, col)
        hint = view.itemDelegate().sizeHint(QStyleOptionViewItem(), index)
        assert view.columnWidth(col) >= hint.width()
        assert view.rowHeight(0) >= hint.height()


def test_long_company_names_and_errors_do_not_stretch_their_columns(qtbot, view):
    source = view._source
    source.set_row(ready("AAPL", name="A very long company name that goes on and on Holdings plc"))
    source.set_row(RowView("SHEL.L", RowState.ERROR, failed_row("SHEL.L", "x" * 200, None)))
    view.resizeColumnsToContents()
    assert view.columnWidth(Col.SYMBOL) <= 190
    assert view.columnWidth(Col.PRICE) <= 190


def test_the_last_column_is_never_narrower_than_its_targets(qtbot):
    source = HeatMatrixModel()
    source.set_symbols(["AAPL"])
    levels = {Indicator.PRICE: 300.0, Indicator.PE: 25.0, Indicator.EV_EBITDA: 20.0, Indicator.PB: 5.0,
              Indicator.DIV_YIELD: 5.0, Indicator.FCF_YIELD: 5.0}
    st = SymbolTargets("AAPL", tuple(Target(i, v) for i, v in levels.items()))
    source.set_row(ready("AAPL", st))
    proxy = HeatSortProxy()
    proxy.setSourceModel(source)
    v = HeatMatrixView()
    v.setModel(proxy)
    v.resize(1000, 300)
    qtbot.addWidget(v)
    v.show()
    assert v.columnWidth(Col.DIV_YIELD) >= v.sizeHintForColumn(Col.DIV_YIELD)


def test_hover_follows_the_pointer_when_the_table_scrolls(qtbot, view, monkeypatch):
    view.resize(1600, 110)
    qtbot.wait(10)
    second = view.visualRect(view.model().index(0, Col.PE)).center()
    pointer = view.viewport().mapToGlobal(second)
    monkeypatch.setattr(view, "_pointer", lambda: pointer)
    view._set_hovered_row(0)
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
    assert view.verticalScrollBar().value() > 0
    assert view.hovered_row() == view.rowAt(second.y())


def test_every_state_paints_without_error(qtbot, view):
    source = view._source
    source.set_symbols(["AAPL", "SHEL.L", "LOAD"])
    source.set_row(RowView("SHEL.L", RowState.ERROR, failed_row("SHEL.L", "Timed out", None)))
    view.setFocus()
    view.select_cell("AAPL", Col.PRICE_TARGET)
    assert not view.grab().isNull()


# --- summary --------------------------------------------------------------------------------


def test_summary_counts_stocks_by_readiness():
    views = [
        ready("AAPL", SymbolTargets("AAPL", (Target(Indicator.PRICE, 9999.0),))),
        ready("AAPL", SymbolTargets("AAPL", (Target(Indicator.PRICE, 320.0),))),
        ready("AAPL", SymbolTargets("AAPL", (Target(Indicator.PRICE, 1.0),))),
        ready("AAPL"),
        RowView("EEE", RowState.LOADING),
        RowView("FFF", RowState.ERROR, failed_row("FFF", "x", None)),
    ]
    assert summary_text(views) == "1 ready, 1 near, 1 far, 1 without active targets, 1 loading, 1 failed"


def test_summary_leaves_out_empty_counts():
    assert summary_text([ready("AAPL"), ready("SHEL.L")]) == "2 without active targets"
    assert summary_text([]) == ""
