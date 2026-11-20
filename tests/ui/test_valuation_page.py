import threading

import pytest

from obb_terminal.data.market import SourceUnavailable
from obb_terminal.domain.models import Component, Method, ValuationLabel
from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.valuation import ValuationPage
from obb_terminal.ui.valuation.ai_panel import AiState
from tests.fakes import make_cached_report, make_profile, make_valuation_inputs


@pytest.fixture
def page(qtbot, loader, fake_analyst, tmp_path):
    widget = ValuationPage(loader, fake_analyst)
    widget.documents = tmp_path / "Documents"
    qtbot.addWidget(widget)
    widget.show()
    return widget


def open_symbol(qtbot, page, symbol):
    page.set_profile(make_profile(symbol))
    page.set_active(True)
    qtbot.waitUntil(lambda: page.state is PanelState.READY, timeout=3000)


def test_loads_lazily_only_when_active(qtbot, page, fake_md):
    page.set_profile(make_profile("AAPL"))
    qtbot.wait(100)
    assert fake_md.calls_to("valuation_inputs") == []
    assert page.state is PanelState.EMPTY
    page.set_active(True)
    qtbot.waitUntil(lambda: page.state is PanelState.READY, timeout=3000)
    page.set_active(False)
    page.set_active(True)
    page.set_profile(make_profile("AAPL"))
    qtbot.wait(100)
    assert len(fake_md.calls_to("valuation_inputs")) == 1


def test_aapl_fills_every_section(qtbot, page):
    open_symbol(qtbot, page, "AAPL")
    v, s = page.valuation, page.score
    assert sum(m.value is not None for m in v.methods) >= 3
    assert v.blended is not None and v.label in set(ValuationLabel)
    assert sum(c.value is not None for c in s.components) >= 4 and s.value is not None
    assert page.kpi_table.rowCount() == 12
    assert page.export_button.isEnabled()
    assert page.analyst_panel.run_button.isEnabled()


def test_editing_discount_rate_recomputes_without_fetch(qtbot, page, fake_md):
    open_symbol(qtbot, page, "AAPL")
    before = page.valuation.blended
    page.assumptions_box.spins["discount_rate"].setValue(12.0)
    qtbot.waitUntil(lambda: page.valuation.blended != before, timeout=2000)
    upside = next(c for c in page.score.components if c.key is Component.UPSIDE)
    assert dict(upside.parts)["Margin of safety"] == pytest.approx(page.valuation.margin)
    assert page.fair_value.blended_label.text() == f"Fair value {page.valuation.blended:,.2f} USD"
    assert len(fake_md.calls_to("valuation_inputs")) == 1


def test_etf_shows_not_applicable_but_still_shows_score(qtbot, page):
    open_symbol(qtbot, page, "SPY")
    assert page.fair_value.na_label.text() == "Fair value not applicable to ETFs"
    assert not page.score_view.isHidden()
    assert page.score_view.values[Component.MOMENTUM].text().startswith(
        f"{page.score.components[-1].value:.0f}"
    )


def test_fii_uses_only_dividend_method(qtbot, page):
    open_symbol(qtbot, page, "HGLG11.SA")
    weights = {m.method: m.weight for m in page.valuation.methods}
    assert weights[Method.DIVIDEND] == 1.0
    assert all(w == 0.0 for m, w in weights.items() if m is not Method.DIVIDEND)
    assert page.score_view.values[Component.ANALYST].text() == "n/a — no analyst coverage"


def test_error_then_retry_on_next_activation(qtbot, page, fake_md):
    fake_md.results["valuation_inputs"] = SourceUnavailable("Source unavailable (offline?)", reason="network")
    page.set_profile(make_profile("AAPL"))
    page.set_active(True)
    qtbot.waitUntil(lambda: page.state is PanelState.ERROR, timeout=3000)
    assert page.error_text == "Source unavailable (offline?)"
    fake_md.results["valuation_inputs"] = make_valuation_inputs
    page.set_active(False)
    page.set_active(True)
    qtbot.waitUntil(lambda: page.state is PanelState.READY, timeout=3000)


def test_new_symbol_clears_page_and_resets_ai_panel(qtbot, page, fake_analyst):
    fake_analyst.cached["SPY"] = make_cached_report("SPY")
    open_symbol(qtbot, page, "AAPL")
    page.set_active(False)
    page.set_profile(make_profile("SPY"))
    assert page.state is PanelState.EMPTY
    assert page.valuation is None and not page.export_button.isEnabled()
    assert page.analyst_panel.symbol == "SPY"
    assert page.analyst_panel.state is AiState.READY


def test_superseded_result_for_previous_symbol_is_dropped(qtbot, page, fake_md):
    gate = threading.Event()

    def slow_aapl(symbol):
        if symbol == "AAPL":
            gate.wait(3)
        return make_valuation_inputs(symbol)

    fake_md.results["valuation_inputs"] = slow_aapl
    page.set_active(True)
    page.set_profile(make_profile("AAPL"))
    page.set_profile(make_profile("SPY"))
    qtbot.waitUntil(lambda: page.state is PanelState.READY, timeout=3000)
    gate.set()
    qtbot.wait(200)
    assert page.inputs.symbol == "SPY"


def test_export_report_writes_pdf_and_emits_path(qtbot, page, fake_analyst):
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL")
    open_symbol(qtbot, page, "AAPL")
    with qtbot.waitSignal(page.exported, timeout=3000) as blocker:
        path = page.export_report()
    assert blocker.args[0] == str(path)
    assert path.parent == page.documents / "TT Research Reports"
    assert path.name.startswith("AAPL-") and path.read_bytes().startswith(b"%PDF")


def test_export_failure_shows_inline_error(qtbot, page, tmp_path):
    page.documents = tmp_path / "blocker"
    page.documents.write_text("x")
    open_symbol(qtbot, page, "AAPL")
    assert page.export_report() is None
    assert page.export_error.text().startswith("Could not save report")
    assert not page.export_error.isHidden()


def _fail_then_ready(qtbot, page, fake_md):
    fake_md.results["valuation_inputs"] = SourceUnavailable("Source unavailable (offline?)", reason="network")
    page.set_profile(make_profile("AAPL"))
    page.set_active(True)
    qtbot.waitUntil(lambda: page.state is PanelState.ERROR, timeout=3000)
    fake_md.results["valuation_inputs"] = make_valuation_inputs
    page.retry_button.click()
    qtbot.waitUntil(lambda: page.state is PanelState.READY, timeout=3000)


def test_retry_keeps_loaded_symbol_and_edits_across_tab_flip(qtbot, page, fake_md):
    _fail_then_ready(qtbot, page, fake_md)
    calls = len(fake_md.calls_to("valuation_inputs"))
    page.assumptions_box.spins["discount_rate"].setValue(12.0)
    qtbot.wait(600)
    page.set_active(False)
    page.set_active(True)
    qtbot.wait(150)
    assert len(fake_md.calls_to("valuation_inputs")) == calls
    assert page.assumptions_box.spins["discount_rate"].value() == pytest.approx(12.0)


def test_reselecting_same_symbol_does_not_reset_or_refetch(qtbot, page, fake_md):
    open_symbol(qtbot, page, "AAPL")
    page.assumptions_box.spins["discount_rate"].setValue(12.0)
    qtbot.wait(600)
    page.set_profile(make_profile("AAPL"))
    qtbot.wait(150)
    assert page.state is PanelState.READY
    assert len(fake_md.calls_to("valuation_inputs")) == 1
    assert page.assumptions_box.spins["discount_rate"].value() == pytest.approx(12.0)


def test_retry_then_reselect_same_symbol_keeps_page(qtbot, page, fake_md):
    _fail_then_ready(qtbot, page, fake_md)
    calls = len(fake_md.calls_to("valuation_inputs"))
    page.set_profile(make_profile("AAPL"))
    qtbot.wait(150)
    assert page.state is PanelState.READY and page.valuation is not None
    assert len(fake_md.calls_to("valuation_inputs")) == calls


def test_flip_while_load_in_flight_does_not_refetch(qtbot, page, fake_md):
    gate = threading.Event()

    def slow(symbol):
        gate.wait(3)
        return make_valuation_inputs(symbol)

    fake_md.results["valuation_inputs"] = slow
    page.set_profile(make_profile("AAPL"))
    page.set_active(True)
    page.set_active(False)
    page.set_active(True)
    gate.set()
    qtbot.waitUntil(lambda: page.state is PanelState.READY, timeout=3000)
    qtbot.wait(150)
    assert len(fake_md.calls_to("valuation_inputs")) == 1


def test_export_button_tracks_valuation_and_guard_shows_error(qtbot, page):
    assert not page.export_button.isEnabled()
    open_symbol(qtbot, page, "AAPL")
    assert page.export_button.isEnabled()
    page._valuation = None
    assert page.export_report() is None
    assert not page.export_error.isHidden()


def test_new_symbol_cancels_pending_assumption_edit(qtbot, page, fake_md):
    open_symbol(qtbot, page, "AAPL")
    page.assumptions_box.spins["discount_rate"].setValue(12.0)  # debounce now pending
    page.set_active(False)
    page.set_profile(make_profile("SPY"))
    assert not page.assumptions_box._debounce.isActive()
    qtbot.wait(600)
    assert page.valuation is None and page._assumptions is None


def test_loading_a_symbol_records_one_score_snapshot_per_day(qtbot, loader, fake_analyst, tmp_path):
    from obb_terminal.journal.models import Kind
    from obb_terminal.journal.store import Journal

    journal = Journal(tmp_path / "journal.sqlite3")
    widget = ValuationPage(loader, fake_analyst, journal=journal)
    qtbot.addWidget(widget)
    open_symbol(qtbot, widget, "AAPL")
    open_symbol(qtbot, widget, "SHEL.L")
    widget.set_profile(make_profile("AAPL"))
    qtbot.waitUntil(lambda: widget.state is PanelState.READY and widget.inputs.symbol == "AAPL", timeout=3000)
    assert sorted((s.symbol, s.kind) for s, _ in journal.rows()) == [("AAPL", Kind.SCORE), ("SHEL.L", Kind.SCORE)]


def test_ai_run_records_the_call(qtbot, loader, fake_analyst, tmp_path):
    from obb_terminal.journal.models import Kind
    from obb_terminal.journal.store import Journal

    journal = Journal(tmp_path / "journal.sqlite3")
    widget = ValuationPage(loader, fake_analyst, journal=journal)
    qtbot.addWidget(widget)
    open_symbol(qtbot, widget, "AAPL")
    widget.analyst_panel.run()
    qtbot.waitUntil(lambda: widget.analyst_panel.state is AiState.READY, timeout=3000)
    widget.analyst_panel.debate()
    # The journal is written on the worker thread before the result reaches the panel; wait
    # for both, or the result lands after teardown has deleted the panel.
    qtbot.waitUntil(
        lambda: len(journal.rows()) == 3 and widget.analyst_panel.state is AiState.READY, timeout=3000
    )
    assert sorted(s.kind for s, _ in journal.rows()) == sorted([Kind.SCORE, Kind.AI, Kind.DEBATE])
