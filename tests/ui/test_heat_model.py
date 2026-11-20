from dataclasses import replace

import pytest
from PySide6.QtCore import Qt

from obb_terminal.domain.targets import (
    AlertState,
    Heat,
    Indicator,
    SymbolTargets,
    Target,
    ThesisBreak,
    evaluate,
    failed_row,
)
from obb_terminal.ui.theme import TOKENS
from obb_terminal.ui.watchlist_tab.model import (
    Col,
    HeatMatrixModel,
    HeatRole,
    HeatSortProxy,
    IndicatorRole,
    RowState,
    RowView,
    SymbolRole,
)
from tests.fakes import make_valuation_inputs

SORT = Qt.ItemDataRole.UserRole + 1  # SortRole

AAPL_TARGETS = SymbolTargets("AAPL", (Target(Indicator.PRICE, 400.0), Target(Indicator.PE, 25.0)))


def ready(symbol, st=None, **changes):
    inputs = replace(make_valuation_inputs(symbol), **changes)
    return RowView(symbol, RowState.READY, evaluate(inputs, st), inputs)


@pytest.fixture
def model():
    m = HeatMatrixModel()
    m.set_symbols(["AAPL", "HGLG11.SA", "SHEL.L"])
    return m


def text(model, symbol, col, role=Qt.ItemDataRole.DisplayRole):
    return model.data(model.index(model.symbols().index(symbol), col), role)


def test_columns_and_loading_rows(model):
    assert model.columnCount() == 15 and model.rowCount() == 3
    headers = [model.headerData(c, Qt.Orientation.Horizontal) for c in Col]
    assert headers == ["Symbol", "1M", "Price", "Readiness", "Buy-at", "Price target", "Thesis break",
                       "P/E", "EV/EBITDA", "P/B", "FCF yield", "Fair value", "Margin", "52-wk", "Div yield"]
    assert "annual EBITDA" in model.headerData(Col.EV_EBITDA, Qt.Orientation.Horizontal,
                                               Qt.ItemDataRole.ToolTipRole)
    assert (text(model, "AAPL", Col.SYMBOL), text(model, "AAPL", Col.PRICE)) == ("AAPL", "…")
    assert model.row("AAPL").state is RowState.LOADING


def test_ready_row_text(model):
    model.set_row(ready("AAPL", AAPL_TARGETS))
    assert text(model, "AAPL", Col.PRICE) == "336.64 USD"
    assert text(model, "AAPL", Col.BUY_AT) == "191.87"
    assert text(model, "AAPL", Col.READINESS) == "↓ 43.0%"
    assert text(model, "AAPL", Col.PRICE_TARGET) == "336.64  ≤ 400\n@ 400.00  ✓ hit"
    assert text(model, "AAPL", Col.PE) == "43.86x  ≤ 25\n@ 191.87  ↓ 43.0%"
    assert text(model, "AAPL", Col.EV_EBITDA) == "34.37x"
    assert text(model, "AAPL", Col.DIV_YIELD) == "0.32%"
    assert text(model, "AAPL", Col.POS_52W) == "81%"
    assert text(model, "AAPL", Col.FAIR) == "186.02"
    assert text(model, "AAPL", Col.MARGIN) == "-44.74%"


def test_cell_colours_and_roles(model):
    model.set_row(ready("AAPL", AAPL_TARGETS))
    pe = model.index(0, Col.PE)
    assert model.data(pe, HeatRole) == (Heat.FAR, 1.0)
    assert model.data(pe, Qt.ItemDataRole.BackgroundRole) is None  # only hits keep a fill
    assert model.data(pe, Qt.ItemDataRole.ForegroundRole).name() == TOKENS["amber_dim"].lower()
    assert model.data(pe, IndicatorRole) is Indicator.PE and model.data(pe, SymbolRole) == "AAPL"
    plain = model.index(0, Col.PB)
    assert model.data(plain, Qt.ItemDataRole.BackgroundRole) is None
    assert model.data(plain, Qt.ItemDataRole.ForegroundRole).name() == TOKENS["text"].lower()
    assert model.data(model.index(0, Col.PRICE), IndicatorRole) is None


def test_accessible_sentences(model):
    model.set_row(ready("AAPL", AAPL_TARGETS))
    assert text(model, "AAPL", Col.PE, Qt.ItemDataRole.AccessibleDescriptionRole) == (
        "AAPL P/E: current 43.86x; target ≤ 25; buy at 191.87 USD; needs a 43.0% drop")
    assert text(model, "AAPL", Col.PE, Qt.ItemDataRole.ToolTipRole) == text(
        model, "AAPL", Col.PE, Qt.ItemDataRole.AccessibleDescriptionRole)
    assert text(model, "AAPL", Col.SYMBOL, Qt.ItemDataRole.ToolTipRole) == (
        "AAPL (Apple Inc.): price 336.64 USD; buy at 191.87 for all 2 targets; ↓ 43.0%; 1 of 2 targets hit")
    assert text(model, "AAPL", Col.EV_EBITDA, Qt.ItemDataRole.ToolTipRole).endswith(
        "uses the latest annual EBITDA, so it can differ from Yahoo's TTM ratio.")


def test_na_and_error_rows(model):
    model.set_row(ready("HGLG11.SA"))
    assert text(model, "HGLG11.SA", Col.EV_EBITDA) == "—"
    model.set_row(RowView("SHEL.L", RowState.ERROR, failed_row("SHEL.L", "Timed out", None)))
    assert text(model, "SHEL.L", Col.PRICE) == "— Timed out"
    assert text(model, "SHEL.L", Col.PE) == "—"
    assert text(model, "SHEL.L", Col.SYMBOL, Qt.ItemDataRole.ToolTipRole) == (
        "SHEL.L: data unavailable — Timed out")
    model.set_row(RowView("HGLG11.SA", RowState.ERROR, replace(failed_row("HGLG11.SA", "x", None), error=None)))
    assert text(model, "HGLG11.SA", Col.SYMBOL, Qt.ItemDataRole.ToolTipRole) == (
        "HGLG11.SA: data unavailable — unknown error")


def test_indicator_sort_orders_by_move_with_untargeted_last():
    model = HeatMatrixModel()
    model.set_symbols(["AAPL", "HGLG11.SA", "SHEL.L"])
    model.set_row(ready("AAPL", SymbolTargets("AAPL", (Target(Indicator.PE, 25.0),))))  # far
    model.set_row(ready("HGLG11.SA", SymbolTargets("HGLG11.SA", (Target(Indicator.PE, 40.0),))))  # near
    model.set_row(ready("SHEL.L", SymbolTargets("SHEL.L", (Target(Indicator.PRICE, 40.0),))))  # no P/E target
    proxy = HeatSortProxy()
    proxy.setSourceModel(model)

    def order():
        return [proxy.index(r, 0).data(SymbolRole) for r in range(proxy.rowCount())]

    proxy.sort(Col.PE, Qt.SortOrder.AscendingOrder)
    assert order() == ["AAPL", "HGLG11.SA", "SHEL.L"]
    proxy.sort(Col.PE, Qt.SortOrder.DescendingOrder)
    assert order() == ["HGLG11.SA", "AAPL", "SHEL.L"]


def test_all_targets_hit_and_hit_count(model):
    st = SymbolTargets("AAPL", (Target(Indicator.PRICE, 400.0), Target(Indicator.PE, 50.0)))
    model.set_row(ready("AAPL", st))
    assert text(model, "AAPL", Col.READINESS) == "✓ all targets"
    assert model.hit_count() == 2
    model.set_row(RowView("AAPL", RowState.ERROR, failed_row("AAPL", "x", st)))
    assert model.hit_count() == 0


def test_set_symbols_keeps_known_rows_and_ignores_unknown(model):
    model.set_row(ready("AAPL", AAPL_TARGETS))
    model.set_symbols(["PETR4.SA", "AAPL"])
    assert model.symbols() == ["PETR4.SA", "AAPL"]
    assert model.row("AAPL").state is RowState.READY and model.row("SHEL.L") is None
    model.set_row(ready("SHEL.L"))  # no longer shown: ignored
    assert model.row("SHEL.L") is None


def test_readiness_sort_descending_with_empty_last_in_both_orders():
    model = HeatMatrixModel()
    model.set_symbols(["AAPL", "SHEL.L", "PETR4.SA", "HGLG11.SA"])
    model.set_row(ready("AAPL", SymbolTargets("AAPL", (Target(Indicator.PRICE, 320.0),))))  # -4.9%
    model.set_row(ready("SHEL.L", SymbolTargets("SHEL.L", (Target(Indicator.PRICE, 40.0),))))  # +5.4%
    model.set_row(ready("PETR4.SA", SymbolTargets("PETR4.SA", (Target(Indicator.PRICE, 40.0),))))
    proxy = HeatSortProxy()
    proxy.setSourceModel(model)

    def order():
        return [proxy.index(r, 0).data(SymbolRole) for r in range(proxy.rowCount())]

    proxy.sort(Col.READINESS, Qt.SortOrder.DescendingOrder)
    assert order() == ["SHEL.L", "AAPL", "PETR4.SA", "HGLG11.SA"]
    proxy.sort(Col.READINESS, Qt.SortOrder.AscendingOrder)
    assert order() == ["PETR4.SA", "AAPL", "SHEL.L", "HGLG11.SA"]
    model.set_row(ready("AAPL", SymbolTargets("AAPL", (Target(Indicator.PRICE, 400.0),))))
    assert order()[-2:] == ["AAPL", "HGLG11.SA"]  # dynamic re-sort on update


def test_thesis_break_column_text_sentence_colour_and_sort(model):
    model.set_row(ready("AAPL", SymbolTargets("AAPL", thesis=ThesisBreak(300.0))))
    assert text(model, "AAPL", Col.THESIS) == "300.00  ↓ 10.9%"
    sentence = text(model, "AAPL", Col.THESIS, Qt.ItemDataRole.ToolTipRole)
    assert sentence == "AAPL thesis break at 300.00 USD: the price must fall 10.9% to reach it"
    assert text(model, "AAPL", Col.THESIS, Qt.ItemDataRole.BackgroundRole) is None
    assert text(model, "AAPL", Col.THESIS, SORT) == pytest.approx(300.0 / 336.64 - 1)
    model.set_row(ready("AAPL", SymbolTargets("AAPL", thesis=ThesisBreak(350.0, AlertState.FIRED))))
    assert text(model, "AAPL", Col.THESIS) == "350.00  ✗ broken"
    assert "broken — the price is at or below it" in text(model, "AAPL", Col.THESIS, Qt.ItemDataRole.ToolTipRole)
    assert text(model, "AAPL", Col.THESIS, Qt.ItemDataRole.BackgroundRole) is not None
    model.set_row(ready("SHEL.L"))
    assert text(model, "SHEL.L", Col.THESIS) == "—"
    assert text(model, "SHEL.L", Col.THESIS, SORT) is None
