import threading

import pytest

from obb_terminal.alerts.engine import AlertEngine
from obb_terminal.alerts.store import TargetsRepo
from obb_terminal.data.market import NoData
from obb_terminal.domain.targets import AlertState, Heat, Indicator
from obb_terminal.fsutil import LockBusy
from obb_terminal.ui.jobs import SerialJobs
from obb_terminal.ui.watchlist_tab import AlertServices, WatchlistTab
from obb_terminal.ui.watchlist_tab.editor import TargetEditor
from obb_terminal.ui.watchlist_tab.model import Col, RowState
from obb_terminal.ui.watchlist_tab.tab import BUSY_MESSAGE, RECOVERED_MESSAGE, UNREADABLE_MESSAGE
from tests.fakes import FakeInstaller, FakeNotifier

SYMBOLS = ["AAPL", "SHEL.L", "PETR4.SA", "HGLG11.SA"]


class Harness:
    def __init__(self, qtbot, loader, tmp_path, symbols=SYMBOLS, **kw):
        self.notifier = FakeNotifier()
        self.repo = TargetsRepo(tmp_path / "targets.json")
        self.engine = AlertEngine(self.repo, self.notifier)
        self.installer = FakeInstaller()
        self.jobs = SerialJobs()
        self.tab = WatchlistTab(loader, AlertServices(self.engine, self.installer), self.jobs,
                                list(symbols), **kw)
        qtbot.addWidget(self.tab)
        self.messages: list[str] = []
        self.hits: list[int] = []
        self.tab.message.connect(self.messages.append)
        self.tab.hitCountChanged.connect(self.hits.append)

    def state(self, symbol):
        return self.tab.model.row(symbol).state

    def text(self, symbol, col):
        return self.tab.model.data(self.tab.model.index(self.tab.model.symbols().index(symbol), col))

    def n_targets(self, symbol):
        st = self.engine.snapshot().file.get(symbol)
        return 0 if st is None else len(st.targets)

    def all_settled(self):
        return all(self.state(s) is not RowState.LOADING for s in self.tab.model.symbols())


@pytest.fixture
def h(qtbot, loader, tmp_path):
    harness = Harness(qtbot, loader, tmp_path)
    yield harness
    harness.jobs.shutdown()


def test_lists_all_symbols_with_prices_once_active(qtbot, h, fake_md):
    assert fake_md.calls_to("valuation_inputs") == []  # nothing until the tab is shown
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    assert [h.text(s, Col.PRICE) for s in SYMBOLS] == [
        "336.64 USD", "37.94 GBP", "56.00 BRL", "157.58 BRL"]
    assert sorted(c[1] for c in fake_md.calls_to("valuation_inputs")) == sorted(SYMBOLS)


def test_reactivation_within_refresh_interval_does_not_refetch(qtbot, h, fake_md):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.set_active(False)
    h.tab.set_active(True)
    qtbot.wait(50)
    assert len(fake_md.calls_to("valuation_inputs")) == 4


def test_at_most_three_fetches_in_flight(qtbot, h, fake_md):
    gate = threading.Event()
    fake_md.gates["valuation_inputs"] = gate
    h.tab.set_active(True)
    qtbot.waitUntil(lambda: len(fake_md.calls_to("valuation_inputs")) == 3, timeout=2000)
    qtbot.wait(100)
    assert len(fake_md.calls_to("valuation_inputs")) == 3
    gate.set()
    qtbot.waitUntil(h.all_settled, timeout=3000)
    assert len(fake_md.calls_to("valuation_inputs")) == 4


def test_failing_symbol_is_isolated(qtbot, h, fake_md):
    good = fake_md.results["valuation_inputs"]
    fake_md.results["valuation_inputs"] = lambda s: NoData("No data available for SHEL.L", s) if s == "SHEL.L" else good(s)
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    assert h.state("SHEL.L") is RowState.ERROR
    assert h.text("SHEL.L", Col.PRICE) == "— No data available for SHEL.L"
    assert h.state("AAPL") is RowState.READY


def test_edit_shows_at_once_then_persists_and_fires_once(qtbot, h, fake_md):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    calls = len(fake_md.calls_to("valuation_inputs"))
    h.tab.save_target("AAPL", Indicator.PRICE, 370.0, True)  # 1.1 x 336.64
    assert h.text("AAPL", Col.PRICE_TARGET) == "336.64  ≤ 370\n@ 370.00  ✓ hit"  # no fetch needed
    assert len(fake_md.calls_to("valuation_inputs")) == calls
    qtbot.waitUntil(lambda: len(h.notifier.sent) == 1, timeout=3000)
    assert h.notifier.bodies() == ["AAPL: Price ≤ 370 hit — price 336.64 USD (buy-at 370.00)"]
    assert h.engine.snapshot().file.get("AAPL").targets[0].state is AlertState.FIRED
    # The status message follows the notification through a queued signal (macOS runner).
    qtbot.waitUntil(lambda: bool(h.messages), timeout=3000)
    assert h.messages[-1] == "AAPL: 1 buy alert — see notifications"
    assert h.hits[-1] == 1
    h.tab.refresh()
    qtbot.waitUntil(lambda: not h.tab._in_flight, timeout=3000)
    assert len(h.notifier.sent) == 1  # no second notification


def test_delete_and_clear_targets(qtbot, h):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.save_target("SHEL.L", Indicator.PE, 5.0, True)
    h.tab.save_target("SHEL.L", Indicator.PB, 1.0, True)
    qtbot.waitUntil(lambda: h.n_targets("SHEL.L") == 2, timeout=2000)
    h.tab.view.deleteRequested.emit("SHEL.L", Indicator.PE)
    qtbot.waitUntil(lambda: h.n_targets("SHEL.L") == 1, timeout=2000)
    h.tab.clear_targets("SHEL.L")
    assert h.text("SHEL.L", Col.PB) == "2.04x"  # current value only: no target shown
    qtbot.waitUntil(lambda: h.n_targets("SHEL.L") == 0, timeout=2000)


def test_busy_save_reverts_and_tells_the_user(qtbot, h, monkeypatch):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)

    def busy(*args, **kwargs):
        raise LockBusy("targets.json.lock")

    monkeypatch.setattr(h.engine, "set_target", busy)
    h.tab.save_target("AAPL", Indicator.PE, 25.0, True)
    assert "≤ 25" in h.text("AAPL", Col.PE)
    qtbot.waitUntil(lambda: BUSY_MESSAGE in h.messages, timeout=2000)
    assert h.text("AAPL", Col.PE) == "43.86x"


def test_invalid_level_is_reported_without_saving(qtbot, h):
    h.tab.save_target("AAPL", Indicator.DIV_YIELD, 2.0, True)
    assert h.messages == ["Yield target must be at most 100%"]
    assert not h.repo.path.exists()


def test_editor_opens_with_cached_inputs_and_saves(qtbot, h):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.view.editRequested.emit("PETR4.SA", Indicator.DIV_YIELD)
    editor = h.tab.editor
    assert editor is not None and editor.isVisible() and editor.preview.text().startswith("Current Div yield")
    editor.level.setValue(9.0)
    editor.buttons.accepted.emit()
    qtbot.waitUntil(lambda: h.engine.snapshot().file.get("PETR4.SA") is not None, timeout=2000)
    assert h.engine.snapshot().file.get("PETR4.SA").targets[0].level == pytest.approx(0.09)


def test_activation_and_context_menu_signals(qtbot, h):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    with qtbot.waitSignal(h.tab.symbolActivated) as opened:
        h.tab.view.activateRequested.emit("HGLG11.SA")
    assert opened.args == ["HGLG11.SA"]
    index = h.tab.proxy.mapFromSource(h.tab.model.index(0, Col.PE))
    menu = h.tab.build_context_menu(index)
    labels = [a.text() for a in menu.actions()]
    assert labels == ["Edit P/E target…", "Open AAPL in Overview", "Set thesis break…", "Clear targets",
                      "Remove from watchlist"]
    with qtbot.waitSignal(h.tab.removeRequested) as removed:
        menu.actions()[4].trigger()
    assert removed.args == ["AAPL"]
    plain = h.tab.build_context_menu(h.tab.proxy.mapFromSource(h.tab.model.index(0, Col.PRICE)))
    assert [a.text() for a in plain.actions()][0] == "Open AAPL in Overview"


def test_new_symbols_fetch_only_while_active(qtbot, h, fake_md):
    h.tab.set_symbols(SYMBOLS + ["SPY"])
    qtbot.wait(50)
    assert fake_md.calls_to("valuation_inputs") == []
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.set_symbols(SYMBOLS + ["SPY", "MSFT"])
    qtbot.waitUntil(lambda: h.state("MSFT") is RowState.ERROR, timeout=3000)  # not in the fakes
    assert ("valuation_inputs", "MSFT") in fake_md.calls_to("valuation_inputs")
    assert h.text("SPY", Col.FAIR) == "—"


def test_add_box_and_toggle_are_wired(qtbot, h):
    with qtbot.waitSignal(h.tab.addRequested) as added:
        h.tab.add_box.symbolChosen.emit("SPY")
    assert added.args == ["SPY"]
    qtbot.waitUntil(lambda: h.installer.calls[:1] == ["status"], timeout=2000)


def test_readiness_is_the_default_sort(qtbot, h):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.save_target("PETR4.SA", Indicator.PRICE, 60.0, True)  # hit
    h.tab.save_target("AAPL", Indicator.PRICE, 300.0, True)  # -10.9%
    order = [h.tab.proxy.index(r, 0).data() for r in range(4)]
    assert order[:2] == ["PETR4.SA", "AAPL"]  # hit first, then the nearest; no targets last
    assert h.tab.model.row("AAPL").row.cells[0].heat is Heat.FAR


def test_recovered_targets_file_is_reported(qtbot, loader, tmp_path):
    (tmp_path / "targets.json").write_text("{broken")
    harness = Harness(qtbot, loader, tmp_path)
    qtbot.waitUntil(lambda: RECOVERED_MESSAGE in harness.messages, timeout=2000)
    harness.jobs.shutdown()


def test_unreadable_targets_file_is_reported(qtbot, loader, tmp_path, monkeypatch):
    (tmp_path / "targets.json").write_text("{}")
    original = type(tmp_path).read_text

    def read_text(self, *args, **kwargs):
        if self == tmp_path / "targets.json":
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.read_text", read_text)
    harness = Harness(qtbot, loader, tmp_path)
    qtbot.waitUntil(lambda: UNREADABLE_MESSAGE in harness.messages, timeout=2000)
    assert RECOVERED_MESSAGE not in harness.messages
    harness.jobs.shutdown()


def test_refresh_fetches_fresh_but_the_post_save_refetch_does_not(qtbot, h, fake_md):
    invalidated = []
    fake_md.invalidate_valuation = invalidated.append
    h.tab.set_active(True)  # first activation is a refresh
    qtbot.waitUntil(h.all_settled, timeout=3000)
    qtbot.waitUntil(lambda: not h.tab._in_flight, timeout=3000)
    assert sorted(invalidated) == sorted(SYMBOLS)
    invalidated.clear()
    h.tab.save_target("AAPL", Indicator.PRICE, 300.0, True)  # the _on_saved refetch
    qtbot.waitUntil(lambda: h.n_targets("AAPL") == 1, timeout=2000)
    calls = len(fake_md.calls_to("valuation_inputs"))
    qtbot.waitUntil(lambda: len(fake_md.calls_to("valuation_inputs")) > calls - 1 and not h.tab._in_flight,
                    timeout=3000)
    assert invalidated == []
    h.tab.refresh_button.click()
    qtbot.waitUntil(lambda: sorted(invalidated) == sorted(SYMBOLS), timeout=3000)


def test_symbol_added_while_hidden_loads_on_show(qtbot, h, fake_md):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.set_active(False)
    h.tab.set_symbols(SYMBOLS + ["SPY"])
    h.tab.set_active(True)
    qtbot.waitUntil(lambda: h.state("SPY") is RowState.READY, timeout=3000)
    calls = [c[1] for c in fake_md.calls_to("valuation_inputs")]
    assert calls.count("SPY") == 1 and len(calls) == 5


def test_edit_refetch_respects_the_in_flight_cap(qtbot, h, fake_md):
    gate = threading.Event()
    fake_md.gates["valuation_inputs"] = gate
    h.tab.set_active(True)
    qtbot.waitUntil(lambda: len(fake_md.calls_to("valuation_inputs")) == 3, timeout=2000)
    h.tab.save_target("HGLG11.SA", Indicator.PRICE, 200.0, True)  # 4th symbol, still queued
    qtbot.waitUntil(lambda: h.n_targets("HGLG11.SA") == 1, timeout=2000)
    qtbot.wait(100)
    assert len(fake_md.calls_to("valuation_inputs")) == 3
    gate.set()
    qtbot.waitUntil(h.all_settled, timeout=3000)
    qtbot.waitUntil(lambda: len(h.notifier.sent) >= 1 and not h.tab._in_flight, timeout=3000)
    assert "HGLG11.SA" in h.notifier.bodies()[0]


def test_editor_is_replaced_and_cleared_on_close(qtbot, h):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.open_editor("AAPL", Indicator.PE)
    h.tab.open_editor("AAPL", Indicator.PB)
    qtbot.waitUntil(lambda: len(h.tab.findChildren(TargetEditor)) == 1, timeout=2000)
    assert h.tab.findChildren(TargetEditor)[0].isVisible()
    h.tab.editor.close()
    assert h.tab.editor is None
    h.tab._on_result("AAPL", (h.tab._inputs["AAPL"], h.engine.apply([h.tab._inputs["AAPL"]])))


def test_clicking_a_cell_then_saving_persists_the_target(qtbot, h):
    from PySide6.QtCore import Qt

    h.tab.set_active(True)
    h.tab.resize(1600, 400)
    h.tab.show()
    qtbot.waitUntil(h.all_settled, timeout=3000)
    view = h.tab.view
    index = h.tab.proxy.mapFromSource(h.tab.model.index(h.tab.model.symbols().index("AAPL"), Col.PE))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center())
    editor = h.tab.editor
    assert editor is not None and type(editor.indicator) is Indicator
    editor.level.setValue(25.0)
    editor.buttons.accepted.emit()
    qtbot.waitUntil(lambda: h.n_targets("AAPL") == 1, timeout=2000)
    assert h.repo.path.exists() and '"pe"' in h.repo.path.read_text()
    assert h.messages == [] or "Could not save" not in h.messages[-1]


def test_set_and_clear_thesis_break_from_the_menu(qtbot, h):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    asked = []
    h.tab.ask_thesis_level = lambda symbol, current: asked.append((symbol, current)) or 300.0
    index = h.tab.proxy.mapFromSource(h.tab.model.index(0, Col.THESIS))
    menu = h.tab.build_context_menu(index)
    next(a for a in menu.actions() if a.text() == "Set thesis break…").trigger()
    assert asked == [("AAPL", None)]
    assert h.text("AAPL", Col.THESIS) == "300.00  ↓ 10.9%"  # shown before the save lands
    qtbot.waitUntil(lambda: (st := h.engine.snapshot().file.get("AAPL")) is not None and st.thesis is not None, timeout=2000)
    menu = h.tab.build_context_menu(index)
    labels = [a.text() for a in menu.actions()]
    assert "Clear thesis break" in labels
    next(a for a in menu.actions() if a.text() == "Clear thesis break").trigger()
    qtbot.waitUntil(lambda: h.engine.snapshot().file.get("AAPL") is None, timeout=2000)


def test_cancelled_thesis_prompt_saves_nothing(qtbot, h):
    h.tab.ask_thesis_level = lambda symbol, current: None
    h.tab.set_thesis_break_interactively("AAPL")
    qtbot.wait(50)
    assert h.engine.snapshot().file.get("AAPL") is None


def test_apply_call_sets_both_and_the_thesis_fires_once(qtbot, h):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.apply_call("AAPL", 310.0, 340.0)  # thesis above the 336.64 price: broken at once
    qtbot.waitUntil(lambda: len(h.notifier.sent) == 1, timeout=3000)
    st = h.engine.snapshot().file.get("AAPL")
    assert st.target(Indicator.PRICE).level == 310.0 and st.thesis.level == 340.0
    assert "thesis-break level 340 hit" in h.notifier.bodies()[0]
    # The status message follows the notification through a queued signal: wait for it too
    # (a slow Windows runner read it too early).
    qtbot.waitUntil(lambda: bool(h.messages), timeout=3000)
    assert h.messages[-1] == "AAPL: thesis break hit — see notifications"


def test_clear_targets_keeps_the_thesis_break_in_the_tab(qtbot, h):
    h.tab.set_active(True)
    qtbot.waitUntil(h.all_settled, timeout=3000)
    h.tab.apply_call("AAPL", 300.0, 250.0)
    qtbot.waitUntil(lambda: h.n_targets("AAPL") == 1, timeout=2000)
    h.tab.clear_targets("AAPL")
    assert h.text("AAPL", Col.THESIS) == "250.00  ↓ 25.7%"
    qtbot.waitUntil(lambda: h.n_targets("AAPL") == 0, timeout=2000)
    assert h.engine.snapshot().file.get("AAPL").thesis.level == 250.0


def test_a_failed_alerts_switch_reaches_the_status_bar(qtbot, h):
    with qtbot.waitSignal(h.tab.message) as signal:
        h.tab.alerts.failed.emit("Background alerts: error — nope")
    assert signal.args == ["Background alerts: error — nope"]
