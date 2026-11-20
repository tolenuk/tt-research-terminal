"""Application entry point: logging, theme, engine, loader, window."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from obb_terminal.ai.claude_code import ClaudeCodeAnalyst
from obb_terminal.ai.runner import SubprocessRunner
from obb_terminal.ai.store import ReportStore
from obb_terminal.config import AppConfig, load_config
from obb_terminal.data.engine import Engine
from obb_terminal.data.factory import build_market_data
from obb_terminal.data.market import EngineUnavailable, MarketData
from obb_terminal.journal.store import Journal
from obb_terminal.store import Store, data_dir


def setup_logging(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "obb-terminal.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return path


def build_engine_factory(engine: Engine, wait_s: float = 120.0) -> Callable[[], MarketData]:
    def factory() -> MarketData:
        if not engine.wait(wait_s):
            raise EngineUnavailable("Data engine failed to load")
        return build_market_data(engine)

    return factory


def build_analyst(config: AppConfig, reports: ReportStore | None = None) -> ClaudeCodeAnalyst:
    return ClaudeCodeAnalyst(SubprocessRunner(), config.ai, reports or ReportStore())


def main() -> int:
    setup_logging(data_dir())
    config = load_config()
    from PySide6.QtWidgets import QApplication

    from obb_terminal.ui.icon import app_icon, set_windows_app_id
    from obb_terminal.ui.main_window import MainWindow
    from obb_terminal.ui.theme import apply_theme
    from obb_terminal.ui.watchlist_tab import default_alert_services
    from obb_terminal.ui.worker import Loader

    app = QApplication(sys.argv)
    app.setApplicationName("TT Research Terminal")
    app.setDesktopFileName("obb-terminal")
    set_windows_app_id()
    app.setWindowIcon(app_icon())
    apply_theme(app)

    engine = Engine()
    engine.start()
    loader = Loader()
    analyst = build_analyst(config)
    window = MainWindow(
        loader, Store(), analyst=analyst, alerts=default_alert_services(),
        ai_timeout_s=config.ai.timeout_s + 30, journal=Journal(),
    )
    window.resize(1400, 900)
    window.show()
    loader.start_engine(build_engine_factory(engine))

    code = app.exec()
    analyst.cancel()  # kill any claude process group before the Loader waits for its threads
    jobs_done = window.shutdown_jobs()  # let queued target writes and systemctl calls finish
    if not jobs_done or not loader.shutdown():
        logging.shutdown()
        os._exit(code)  # a stuck network call must not hang quit
    return code


if __name__ == "__main__":
    raise SystemExit(main())
