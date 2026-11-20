from obb_terminal.data.market import NoData
from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.panels.quote import QuotePanel, QuoteView
from tests.fakes import make_profile


def load(qtbot, loader, symbol):
    panel = QuotePanel(loader)
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.loaded, timeout=2000) as blocker:
        panel.load(symbol, make_profile(symbol))
    return panel, blocker.args[0]


def test_renders_quote_and_stats(qtbot, loader):
    panel, view = load(qtbot, loader, "SHEL.L")
    assert isinstance(view, QuoteView)
    assert panel.values["Last"].text() == "27.10 GBP"
    assert panel.values["Change"].text().startswith("▲ +1.00")
    assert panel.values["Change"].property("dir") == "up"
    assert panel.values["Market cap"].text() == "215.94B GBP"
    assert panel.values["Div yield"].text() == "3.06%"
    assert panel.values["Volume"].text() == "1,000,000"


def test_missing_stats_show_dashes(qtbot, loader, fake_md):
    fake_md.results["stats"] = NoData("none")
    panel, view = load(qtbot, loader, "SPY")
    assert view.stats is None
    assert panel.state is PanelState.READY
    assert panel.values["P/E"].text() == "—"
    assert panel.values["Market cap"].text() == "—"
