from dataclasses import replace

import pytest

from obb_terminal.data.rollup import RollupMemo
from obb_terminal.domain.etf import LOW_COVERAGE, NEEDS_EQUITY
from obb_terminal.domain.models import Component, Method
from obb_terminal.journal.store import Journal
from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.valuation import ValuationPage
from tests.fakes import (
    VALUATION_INPUTS,
    make_etf_inputs,
    make_holding_values,
    make_profile,
    make_valuation_inputs,
)


def etf_market(fake_md, etf="SPY"):
    """`etf` comes with look-through; every other symbol is valued like AAPL."""
    def inputs(symbol):
        if symbol == etf:
            return make_etf_inputs(etf)
        if symbol in VALUATION_INPUTS:
            return make_valuation_inputs(symbol)
        return replace(make_valuation_inputs("AAPL"), symbol=symbol)

    fake_md.results["valuation_inputs"] = inputs


@pytest.fixture
def memo():
    return RollupMemo()


@pytest.fixture
def page(qtbot, loader, fake_analyst, tmp_path, memo):
    widget = ValuationPage(loader, fake_analyst, rollups=memo)
    widget.documents = tmp_path / "Documents"
    qtbot.addWidget(widget)
    widget.show()
    return widget


def open_etf(qtbot, page, symbol="SPY"):
    page.set_profile(make_profile("SPY"))
    page.set_active(True)
    qtbot.waitUntil(lambda: page.state is PanelState.READY, timeout=3000)


def rollup_done(page):
    return page.inputs is not None and page.inputs.etf.holding_values is not None


def test_equity_etf_values_its_holdings_then_shows_a_fair_value(qtbot, page, fake_md, memo):
    etf_market(fake_md)
    open_etf(qtbot, page)
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)
    v = page.valuation
    assert [m.method for m in v.methods] == [Method.LOOK_THROUGH]
    assert v.blended is not None and v.margin is not None
    assert len(page.fair_value.rollup_rows) == 10 and page.fair_value.rollup_table.rowCount() == 5
    assert page.fair_value.rollup_note.text().startswith("Valued 10 of 10 holdings, 37.")
    assert memo.get("SPY") is not None
    keys = {c.key for c in page.score.components}
    assert Component.VS_BONDS in keys and Component.QUALITY not in keys
    assert page.score_view.bars[Component.QUALITY].isHidden()
    assert not page.score_view.bars[Component.COST].isHidden()
    assert page.kpi_table.item(0, 0).text() == "P/E (look-through)"


def test_progress_shows_while_holdings_are_valued(qtbot, page, fake_md):
    import threading

    etf_market(fake_md)
    gate = threading.Event()
    real = fake_md.results["valuation_inputs"]

    def slow(symbol):
        if symbol != "SPY":
            gate.wait(5)
        return real(symbol)

    fake_md.results["valuation_inputs"] = slow
    open_etf(qtbot, page)
    assert page.fair_value.na_label.text() == "Valuing holdings… 0 of 10"
    gate.set()
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)
    assert page.fair_value.na_label.isHidden()


def test_cached_rollup_is_used_without_refetching_holdings(qtbot, page, fake_md, memo):
    etf_market(fake_md)
    memo.put("SPY", make_holding_values(0.10))
    open_etf(qtbot, page)
    assert rollup_done(page)
    assert page.valuation.margin == pytest.approx(0.10)
    assert [c[1] for c in fake_md.calls_to("valuation_inputs")] == ["SPY"]


def test_too_little_coverage_says_so(qtbot, page, fake_md):
    fake_md.results["valuation_inputs"] = lambda s: (
        make_etf_inputs("SPY") if s == "SPY" else make_valuation_inputs(s))  # only AAPL is known
    open_etf(qtbot, page)
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)
    assert page.fair_value.na_label.text() == LOW_COVERAGE
    assert len(page.fair_value.rollup_rows) == 10


def test_bond_etf_has_no_rollup(qtbot, page, fake_md):
    fake_md.results["valuation_inputs"] = lambda s: make_etf_inputs("AGG")
    open_etf(qtbot, page)
    assert page.fair_value.na_label.text() == NEEDS_EQUITY
    assert page.fair_value.rollup_table.isHidden()
    assert [c[1] for c in fake_md.calls_to("valuation_inputs")] == ["SPY"]


def test_assumptions_switch_to_etf_fields(qtbot, page, fake_md):
    etf_market(fake_md)
    open_etf(qtbot, page)
    box = page.assumptions_box
    assert box.etf_part.isVisibleTo(page) and not box.stock_part.isVisibleTo(page)
    assert box.spins["e_bond_yield"].value() == pytest.approx(5.1)
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)
    before = next(c for c in page.score.components if c.key is Component.VS_BONDS).value
    box.spins["e_bond_yield"].setValue(2.0)
    qtbot.waitUntil(
        lambda: next(c for c in page.score.components if c.key is Component.VS_BONDS).value != before,
        timeout=2000)
    gap = next(k for k in page._kpis if k.key == "gap_vs_10y")
    assert gap.value == pytest.approx(0.0416 - 0.02)


def test_stock_after_etf_restores_stock_assumptions(qtbot, page, fake_md):
    etf_market(fake_md)
    open_etf(qtbot, page)
    page.set_profile(make_profile("AAPL"))
    qtbot.waitUntil(lambda: page.state is PanelState.READY and page.inputs.symbol == "AAPL", timeout=3000)
    box = page.assumptions_box
    assert box.stock_part.isVisibleTo(page) and not box.etf_part.isVisibleTo(page)
    assert not page.fair_value.rollup_table.isVisibleTo(page)
    assert not page.score_view.bars[Component.QUALITY].isHidden()


def test_journal_records_the_etf_once_the_rollup_is_done(qtbot, loader, fake_analyst, fake_md, tmp_path):
    journal = Journal(tmp_path / "journal.sqlite3")
    etf_market(fake_md)
    page = ValuationPage(loader, fake_analyst, journal=journal, rollups=RollupMemo())
    qtbot.addWidget(page)
    open_etf(qtbot, page)
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)
    qtbot.waitUntil(lambda: len(journal.rows()) == 1, timeout=3000)
    snap = journal.rows()[0][0]
    assert snap.symbol == "SPY" and snap.model == "etf-1" and snap.fair_value is not None


def test_bond_etf_shows_only_the_weights_it_uses(qtbot, page, fake_md):
    fake_md.results["valuation_inputs"] = lambda s: make_etf_inputs("AGG")
    open_etf(qtbot, page)
    box = page.assumptions_box
    assert box.spins["es_income"].isVisibleTo(page)
    assert not box.spins["es_upside"].isVisibleTo(page)
    assert not box.spins["e_min_coverage"].isVisibleTo(page)


# --- review fixes ----------------------------------------------------------------------------


def gated(fake_md):
    import threading

    etf_market(fake_md)
    gate = threading.Event()
    real = fake_md.results["valuation_inputs"]

    def slow(symbol):
        if symbol != "SPY":
            gate.wait(5)
        return real(symbol)

    fake_md.results["valuation_inputs"] = slow
    return gate


def test_reselecting_the_same_etf_mid_rollup_still_finishes(qtbot, page, fake_md, loader):
    gate = gated(fake_md)
    open_etf(qtbot, page)
    loader.new_generation()  # what the main window does on every profile, same symbol or not
    page.set_profile(make_profile("SPY"))
    gate.set()
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)


def test_transient_holding_failures_are_shown_but_not_remembered(qtbot, page, fake_md, memo):
    from obb_terminal.data.market import DataTimeout

    etf_market(fake_md)
    real = fake_md.results["valuation_inputs"]
    fake_md.results["valuation_inputs"] = lambda s: (
        real(s) if s in ("SPY", "AAPL", "NVDA", "MSFT") else (_ for _ in ()).throw(DataTimeout("Timed out")))
    open_etf(qtbot, page)
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)
    assert sum(hv.transient for hv in page.inputs.etf.holding_values) == 7  # 10 holdings, 3 valued
    assert memo.get("SPY") is None
    assert "Provisional: 7 holdings" in page.fair_value.rollup_note.text()


def test_rollup_with_failures_is_not_journaled(qtbot, loader, fake_analyst, fake_md, tmp_path):
    from obb_terminal.data.market import DataTimeout

    journal = Journal(tmp_path / "journal.sqlite3")
    etf_market(fake_md)
    real = fake_md.results["valuation_inputs"]
    fake_md.results["valuation_inputs"] = lambda s: (
        real(s) if s != "TSLA" else (_ for _ in ()).throw(DataTimeout("Timed out")))
    page = ValuationPage(loader, fake_analyst, journal=journal, rollups=RollupMemo())
    qtbot.addWidget(page)
    open_etf(qtbot, page)
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)
    qtbot.wait(300)
    assert journal.rows() == []


def test_failed_ten_year_says_so(qtbot, page, fake_md):
    fake_md.results["valuation_inputs"] = lambda s: replace(
        make_etf_inputs("AGG", ten_year=None), gaps=("ten_year",))
    open_etf(qtbot, page)
    assert "unavailable right now" in page.assumptions_box.yield_note.text()


def test_settled_rollup_has_no_provisional_note(qtbot, page, fake_md):
    etf_market(fake_md)
    open_etf(qtbot, page)
    qtbot.waitUntil(lambda: rollup_done(page), timeout=5000)
    assert "Provisional" not in page.fair_value.rollup_note.text()
