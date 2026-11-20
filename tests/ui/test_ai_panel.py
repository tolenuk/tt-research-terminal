import threading

import pytest

from obb_terminal.ai.errors import AnalystFailed, ClaudeNotFound
from obb_terminal.ai.models import ReportKind
from obb_terminal.domain.models import Assumptions
from obb_terminal.ui.valuation.ai_panel import DISCLAIMER, AiState, AnalystPanel, format_report
from tests.fakes import (
    CALL,
    DEBATE,
    make_cached_report,
    make_profile,
    make_report,
    make_valuation_inputs,
)

EDITED = Assumptions(0.12, 0.10, 0.025, 0.03)


@pytest.fixture
def panel(qtbot, loader, fake_analyst):
    widget = AnalystPanel(loader, fake_analyst, lambda: EDITED)
    qtbot.addWidget(widget)
    return widget


def show(panel, symbol):
    panel.set_symbol(symbol)
    panel.set_inputs(make_valuation_inputs(symbol), make_profile(symbol))


def test_idle_then_running_then_ready(qtbot, panel, fake_analyst):
    fake_analyst.gate = threading.Event()
    show(panel, "AAPL")
    assert panel.state is AiState.IDLE and panel.run_button.isEnabled()
    panel.run_button.click()
    assert panel.state is AiState.RUNNING
    assert "Analysing AAPL" in panel.progress.text()
    fake_analyst.release()
    qtbot.waitUntil(lambda: panel.state is AiState.READY, timeout=3000)
    assert fake_analyst.contexts[0].assumptions == EDITED  # current, edited assumptions
    assert fake_analyst.contexts[0].symbol == "AAPL"
    assert "PROS" in panel.report_text.toPlainText()
    assert panel.sources.count() == 1
    assert panel.meta.text().startswith("Analysed just now with claude-opus-5-5 (assumptions: discount 12.00%")


def test_returning_shows_last_report_and_run_again_starts_new_analysis(qtbot, panel, fake_analyst):
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL")
    show(panel, "AAPL")
    assert panel.state is AiState.READY
    assert fake_analyst.contexts == []
    panel.again_button.click()
    qtbot.waitUntil(lambda: len(fake_analyst.contexts) == 1, timeout=3000)
    qtbot.waitUntil(lambda: panel.state is AiState.READY and panel._running is None, timeout=3000)


def test_error_then_retry(qtbot, panel, fake_analyst):
    fake_analyst.error = AnalystFailed("Usage limit reached")
    show(panel, "AAPL")
    panel.run()
    qtbot.waitUntil(lambda: panel.state is AiState.ERROR, timeout=3000)
    assert panel.error_label.text() == "Usage limit reached"
    fake_analyst.error = None
    panel.retry_button.click()
    qtbot.waitUntil(lambda: panel.state is AiState.READY, timeout=3000)


def test_claude_missing_shows_install_hint(qtbot, panel, fake_analyst):
    fake_analyst.available = ClaudeNotFound.default_message
    show(panel, "AAPL")
    assert panel.hint.text() == "Claude Code not found — install it and run `claude` once to log in"
    panel.run()
    assert panel.state is AiState.ERROR
    assert panel.error_label.text() == ClaudeNotFound.default_message
    assert fake_analyst.contexts == []


def test_late_result_for_other_symbol_is_stored_not_shown(qtbot, panel, fake_analyst, loader):
    fake_analyst.gate = threading.Event()
    show(panel, "AAPL")
    panel.run()
    qtbot.waitUntil(lambda: fake_analyst.entered.is_set(), timeout=2000)
    show(panel, "SPY")
    assert panel.state is AiState.IDLE
    assert not panel.run_button.isEnabled()  # one run at a time, globally
    assert "AAPL" in panel.run_button.toolTip()
    fake_analyst.release()
    qtbot.waitUntil(lambda: not loader.is_pending("ai:AAPL"), timeout=3000)
    assert panel.state is AiState.IDLE and panel.symbol == "SPY"
    assert panel.run_button.isEnabled()
    assert "AAPL" in fake_analyst.cached
    show(panel, "AAPL")
    assert panel.state is AiState.READY


def test_returning_to_symbol_with_run_in_flight_shows_running(qtbot, panel, fake_analyst):
    fake_analyst.gate = threading.Event()
    show(panel, "AAPL")
    panel.run()
    show(panel, "SPY")
    show(panel, "AAPL")
    assert panel.state is AiState.RUNNING


def test_run_disabled_until_inputs(panel):
    panel.set_symbol("AAPL")
    assert not panel.run_button.isEnabled()
    panel.run()
    assert panel.state is AiState.IDLE


def test_sources_open_only_http(qtbot, panel, fake_analyst):
    opened = []
    panel.open_url = opened.append
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL")
    show(panel, "AAPL")
    panel._on_source(panel.sources.item(0))
    assert opened == ["https://www.apple.com/newsroom/q3-2026"]


def test_format_report_is_plain_text_grouped_by_horizon():
    text = format_report(make_report())
    assert text.startswith("Stance: Neutral")
    assert "+ Buybacks <b> — $110B authorised 2026-05-01" in text
    assert text.index("Next earnings") < text.index("6–12 months") < text.index("Long term")


def test_disclaimer_always_present(panel):
    assert DISCLAIMER == "Generated from public data by a model — not investment advice."
    assert any(label.text() == DISCLAIMER for label in panel.findChildren(type(panel.meta)))


def test_format_report_shows_the_trade_call_in_the_currency():
    text = format_report(make_report(call=CALL), "USD")
    assert "TRADE CALL" in text
    assert "Action: Accumulate (medium conviction)" in text
    assert "Entry zone: 290.00–310.00 USD" in text
    assert "Thesis break: 255.00 USD" in text and "Target: 380.00 USD" in text
    assert "Wrong if: Services growth below 8% <b>" in text
    assert "• FQ1 services above 12%" in text
    assert "Note: Target was trimmed." in text
    assert text.index("TRADE CALL") < text.index("PROS")


def test_format_report_single_price_zone_and_missing_levels():
    from dataclasses import replace

    call = replace(CALL, entry_low=300.0, entry_high=300.0, thesis_break=None, target_price=None, notes=())
    text = format_report(make_report(call=call), "GBP")
    assert "Entry: 300.00 GBP" in text
    assert "Thesis break: none given" in text and "Target: none given" in text


def test_format_report_without_call_says_so():
    assert "TRADE CALL" not in format_report(make_report(), "USD")


def test_use_as_targets_needs_a_lookup_and_a_call(qtbot, panel, fake_analyst):
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL")
    panel.set_symbol("AAPL")
    assert not panel.use_button.isEnabled()  # no call
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL", call=CALL)
    panel.set_symbol("AAPL")
    assert not panel.use_button.isEnabled()  # no lookup wired
    panel.targets_lookup = lambda symbol: (None, False)
    panel.set_symbol("AAPL")
    assert panel.use_button.isEnabled()


def test_use_as_targets_emits_the_confirmed_choice(qtbot, panel, fake_analyst):
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL", call=CALL)
    seen = []
    panel.targets_lookup = lambda symbol: (None, True)
    panel.confirm_targets = lambda symbol, call, ccy, current, listed: seen.append((symbol, ccy, listed)) or (310.0, 255.0, False)
    panel.set_symbol("AAPL")
    with qtbot.waitSignal(panel.useAsTargets) as sig:
        panel.use_button.click()
    assert sig.args == ["AAPL", 310.0, 255.0, False]
    assert seen == [("AAPL", "USD", True)]


def test_use_as_targets_cancel_emits_nothing(qtbot, panel, fake_analyst):
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL", call=CALL)
    panel.targets_lookup = lambda symbol: (None, True)
    panel.confirm_targets = lambda *args: None
    panel.set_symbol("AAPL")
    with qtbot.assertNotEmitted(panel.useAsTargets):
        panel.use_button.click()


def test_format_report_shows_the_debate():
    text = format_report(make_report(debate=DEBATE), "USD")
    assert "WHERE THEY DISAGREE" in text and "THE CASES" in text
    assert "Services growth — favours the bull case: Two quarters up" in text
    assert "  Bull: Durable · Bear: Slowing" in text
    assert "AGREED: Strong balance sheet" in text
    assert "Bull (high conviction, worth 380.00 USD): Services compounding" in text
    assert "The risk case is missing (that analyst failed)." in text
    assert text.index("WHERE THEY DISAGREE") < text.index("PROS") < text.index("THE CASES")


def test_debate_button_runs_a_debate_and_shows_it(qtbot, panel, fake_analyst, loader):
    panel.set_symbol("AAPL")
    panel.set_inputs(make_valuation_inputs("AAPL"), make_profile("AAPL"))
    assert panel.debate_button.isEnabled() and panel.debate_again_button.isHidden() is False
    panel.debate_button.click()
    assert panel.state is AiState.RUNNING and "Debating AAPL" in panel.progress.text()
    assert not panel.run_button.isEnabled() and not panel.debate_button.isEnabled()
    qtbot.waitUntil(lambda: panel.state is AiState.READY, timeout=3000)
    assert fake_analyst.kinds == [ReportKind.DEBATE]
    assert "WHERE THEY DISAGREE" in panel.report_text.toPlainText()
    assert panel.meta.text().startswith("Debated ")


def test_debate_uses_a_longer_loader_timeout(qtbot, loader, fake_analyst, monkeypatch):
    widget = AnalystPanel(loader, fake_analyst, lambda: EDITED, timeout_s=100.0)
    qtbot.addWidget(widget)
    seen = []
    real = loader.submit
    monkeypatch.setattr(loader, "submit", lambda *a, **kw: (seen.append(kw["timeout_s"]), real(*a, **kw)))
    widget.set_symbol("AAPL")
    widget.set_inputs(make_valuation_inputs("AAPL"), make_profile("AAPL"))
    widget.debate_button.click()
    qtbot.waitUntil(lambda: widget.state is AiState.READY, timeout=3000)
    widget.run_button.setEnabled(True)
    widget.again_button.click()
    qtbot.waitUntil(lambda: widget.state is AiState.READY and len(seen) == 2, timeout=3000)
    assert seen == [230.0, 100.0]


def test_report_stays_readable_at_default_window_size(qtbot, panel, fake_analyst):
    # The panel's size in the default 1400×900 window, with the longest usual buttons showing.
    fake_analyst.cached["AAPL"] = make_cached_report("AAPL", call=CALL)
    show(panel, "AAPL")
    panel.resize(556, 385)
    panel.show()
    qtbot.waitExposed(panel)
    qtbot.wait(50)
    assert panel.report_text.height() >= 100  # 70 before the fix; margin for Windows fonts
    assert panel.meta.height() >= panel.meta.heightForWidth(panel.meta.width())  # never clipped
