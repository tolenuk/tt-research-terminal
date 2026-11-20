import os

# Force headless Qt: desktop sessions export QT_QPA_PLATFORM (e.g. "wayland;xcb"),
# which would otherwise make the UI tests open real windows.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import pytest  # noqa: E402

from tests.fakes import FakeMarketData, install_standard  # noqa: E402


@pytest.fixture
def fake_md():
    md = FakeMarketData()
    install_standard(md)
    yield md
    md.release_all()


@pytest.fixture
def loader(qtbot, fake_md):
    from obb_terminal.ui.worker import Loader

    ld = Loader(pool_size=6, timeout_s=1.0)
    with qtbot.waitSignal(ld.engineReady, timeout=2000):
        ld.start_engine(lambda: fake_md)
    yield ld
    fake_md.release_all()
    ld.shutdown()


@pytest.fixture
def fake_analyst(loader):
    """Depends on `loader` so it is torn down (gate released) before the Loader shuts down."""
    from tests.fakes import FakeAnalyst

    analyst = FakeAnalyst()
    yield analyst
    analyst.release()
