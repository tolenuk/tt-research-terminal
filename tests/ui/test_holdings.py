from PySide6.QtCore import Qt

from obb_terminal.domain.models import FundProfile
from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.panels.holdings import NO_HOLDINGS_TEXT, HoldingsPanel
from tests.fakes import make_fund_profile, make_profile


def load(qtbot, panel, symbol="SPY"):
    panel.load(symbol, make_profile("SPY") if symbol == "SPY" else None)
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)


def test_renders_spy_holdings_sectors_and_mix(qtbot, loader):
    panel = HoldingsPanel(loader)
    qtbot.addWidget(panel)
    load(qtbot, panel)
    assert panel.summary.text() == "Large Blend · State Street Investment Management · Expense 0.09% vs 0.72% avg · Turnover 3.00%"
    assert panel.holdings.rowCount() == 10
    assert [panel.holdings.item(0, c).text() for c in range(3)] == ["AAPL", "Apple Inc", "7.70%"]
    assert panel.coverage.text() == "Top 10 = 37.3% of the fund"
    assert panel.sectors.item(0, 0).text() == "Technology"
    assert panel.sectors.item(0, 1).text() == "38.23%"
    assert panel.mix.parts[0] == ("Stocks", 0.9994)
    assert panel.empty_note.isHidden()


def test_bond_fund_shows_ratings_instead_of_sectors(qtbot, loader, fake_md):
    fake_md.results["fund_profile"] = lambda s: make_fund_profile("AGG")
    panel = HoldingsPanel(loader)
    qtbot.addWidget(panel)
    load(qtbot, panel, "AGG")
    assert panel.sector_heading.text() == "CREDIT RATING"
    assert panel.sectors.item(0, 0).text() == "AA"


def test_no_fund_data_shows_the_note(qtbot, loader, fake_md):
    fake_md.results["fund_profile"] = lambda s: FundProfile(s, gaps=("no fund data",))
    panel = HoldingsPanel(loader)
    qtbot.addWidget(panel)
    load(qtbot, panel, "BOVA11.SA")
    assert not panel.empty_note.isHidden()
    assert panel.empty_note.text() == NO_HOLDINGS_TEXT
    assert panel.holdings.rowCount() == 0


def test_double_click_opens_a_holding(qtbot, loader):
    panel = HoldingsPanel(loader)
    qtbot.addWidget(panel)
    load(qtbot, panel)
    with qtbot.waitSignal(panel.holdingActivated) as sig:
        panel.holdings.cellDoubleClicked.emit(1, 0)
    assert sig.args == ["NVDA"]


def test_dividends_button_asks_to_switch(qtbot, loader):
    panel = HoldingsPanel(loader)
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.dividendsRequested):
        qtbot.mouseClick(panel.dividends_button, Qt.MouseButton.LeftButton)
