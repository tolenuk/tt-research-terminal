import threading
from datetime import UTC, datetime

import numpy as np
from PySide6.QtCore import Qt

from obb_terminal.domain.models import Range
from obb_terminal.ui.panels.base import PanelState
from obb_terminal.ui.panels.chart import ChartPanel, crosshair_text, snap_index
from tests.fakes import make_profile, make_series


def test_renders_series(qtbot, loader):
    panel = ChartPanel(loader)
    qtbot.addWidget(panel)
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    xs, ys = panel.curve.getData()
    assert list(ys) == [1.0, 2.0, 3.0]
    assert panel.series.range is Range.Y1
    assert panel.range_buttons[Range.Y1].isChecked()


def test_range_change_supersedes(qtbot, loader, fake_md):
    gate = threading.Event()
    fake_md.results["history"] = lambda s, r: (gate.wait(2), make_series(s, r))[1] if r is Range.M1 else make_series(s, r)
    panel = ChartPanel(loader)
    qtbot.addWidget(panel)
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    qtbot.mouseClick(panel.range_buttons[Range.M1], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(panel.range_buttons[Range.Y5], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.series.range is Range.Y5, timeout=2000)
    gate.set()
    qtbot.wait(200)
    assert panel.series.range is Range.Y5
    assert panel.range_buttons[Range.Y5].isChecked()


def test_snap_index_picks_nearest():
    xs = np.array([10.0, 20.0, 30.0])
    assert snap_index(xs, -5) == 0
    assert snap_index(xs, 14) == 0
    assert snap_index(xs, 16) == 1
    assert snap_index(xs, 99) == 2


def test_crosshair_text_daily_and_intraday():
    day = datetime(2026, 10, 8, tzinfo=UTC).timestamp()
    intraday = datetime(2026, 10, 9, 16, 25, tzinfo=UTC).timestamp()
    assert crosshair_text(day, 27.1, "GBP") == "2026-10-08  27.10 GBP"
    assert crosshair_text(intraday, 37.955, "GBP") == "2026-10-09 16:25  37.95 GBP"


def test_all_button_is_last_and_loads_full_history(qtbot, loader):
    panel = ChartPanel(loader)
    qtbot.addWidget(panel)
    assert list(panel.range_buttons)[-1] is Range.ALL
    assert panel.range_buttons[Range.ALL].text() == "ALL"
    panel.load("AAPL", make_profile("AAPL"))
    qtbot.waitUntil(lambda: panel.state is PanelState.READY, timeout=2000)
    qtbot.mouseClick(panel.range_buttons[Range.ALL], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.series.range is Range.ALL, timeout=2000)
    assert panel.range_buttons[Range.ALL].isChecked()
