import logging

import pytest

from obb_terminal.ai.claude_code import ClaudeCodeAnalyst
from obb_terminal.ai.store import ReportStore
from obb_terminal.app import build_analyst, build_engine_factory, setup_logging
from obb_terminal.config import AiConfig, AppConfig
from obb_terminal.data.cache import CachedMarketData
from obb_terminal.data.engine import Engine
from obb_terminal.data.market import EngineUnavailable


def test_factory_returns_cached_market_data():
    engine = Engine(importer=lambda: {"yfinance": object()})
    engine.start()
    assert isinstance(build_engine_factory(engine, wait_s=2)(), CachedMarketData)


def test_factory_raises_when_engine_fails():
    def boom():
        raise ImportError("missing")

    engine = Engine(importer=boom)
    engine.start()
    with pytest.raises(EngineUnavailable):
        build_engine_factory(engine, wait_s=2)()


def test_setup_logging_writes_file(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = setup_logging(tmp_path / "logs")
        logging.getLogger("obb_terminal.test").warning("hello")
        for handler in root.handlers:
            handler.flush()
        assert "hello" in path.read_text()
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()


def test_build_analyst_uses_config_and_store(tmp_path):
    analyst = build_analyst(AppConfig(ai=AiConfig(model="claude-sonnet-5-5")), ReportStore(tmp_path))
    assert isinstance(analyst, ClaudeCodeAnalyst)
    assert analyst._config.model == "claude-sonnet-5-5"
    assert analyst.last("AAPL") is None
    analyst.cancel()  # no process running: a no-op


def test_module_entry_point_runs_the_app(monkeypatch):
    import runpy

    from obb_terminal import app

    monkeypatch.setattr(app, "main", lambda: 7)
    try:
        runpy.run_module("obb_terminal", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 7
    else:
        raise AssertionError("expected SystemExit")
