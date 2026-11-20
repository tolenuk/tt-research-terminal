import threading

import pytest

from obb_terminal.data.engine import Engine, EngineState
from obb_terminal.data.market import EngineUnavailable


def test_engine_becomes_ready():
    engine = Engine(importer=lambda: {"yfinance": "provider"})
    engine.start()
    assert engine.wait(2) is True
    assert engine.state is EngineState.READY
    assert engine.providers() == {"yfinance": "provider"}


def test_engine_failure_is_reported():
    def boom():
        raise ImportError("no openbb")

    engine = Engine(importer=boom)
    engine.start()
    assert engine.wait(2) is False
    assert engine.state is EngineState.FAILED
    assert "no openbb" in (engine.error or "")
    with pytest.raises(EngineUnavailable):
        engine.providers()


def test_providers_before_ready_raises():
    gate = threading.Event()
    engine = Engine(importer=lambda: gate.wait(2) and {"yfinance": 1})
    engine.start()
    try:
        assert engine.state is EngineState.LOADING
        with pytest.raises(EngineUnavailable):
            engine.providers()
    finally:
        gate.set()
        engine.wait(2)


def test_start_is_idempotent():
    calls = []
    engine = Engine(importer=lambda: calls.append(1) or {"yfinance": 1})
    engine.start()
    engine.start()
    engine.wait(2)
    assert calls == [1]
