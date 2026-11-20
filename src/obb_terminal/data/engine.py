"""Background import of the OpenBB providers. The only module allowed to import openbb_*."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import Enum
from typing import Any

from obb_terminal.data.market import EngineUnavailable

log = logging.getLogger(__name__)


class EngineState(Enum):
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


def _import_providers() -> dict[str, Any]:
    from openbb_yfinance import yfinance_provider

    return {"yfinance": yfinance_provider}


class Engine:
    def __init__(self, importer: Callable[[], dict[str, Any]] | None = None) -> None:
        self._importer = importer or _import_providers
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._started = False
        self._providers: dict[str, Any] = {}
        self.state = EngineState.LOADING
        self.error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run, daemon=True, name="openbb-import").start()

    def _run(self) -> None:
        try:
            self._providers = self._importer()
            self.state = EngineState.READY
        except Exception as exc:
            log.exception("OpenBB engine failed to load")
            self.error = str(exc)
            self.state = EngineState.FAILED
        finally:
            self._done.set()

    def wait(self, timeout: float) -> bool:
        self._done.wait(timeout)
        return self.state is EngineState.READY

    def providers(self) -> dict[str, Any]:
        if self.state is not EngineState.READY:
            raise EngineUnavailable("Data engine unavailable")
        return self._providers
