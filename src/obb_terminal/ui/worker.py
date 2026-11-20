"""Run blocking MarketData calls on a thread pool; deliver results on the GUI thread.

- A submit with an existing key supersedes the earlier task (its result is dropped).
- Scoped tasks belong to the current generation; results from older generations are dropped.
- A backstop timer (default 25 s) reports DataTimeout if the data layer's own 20 s deadline
  somehow fails; the late result is then ignored.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from obb_terminal.data.market import (
    DataError,
    DataTimeout,
    EngineUnavailable,
    MarketData,
    SourceUnavailable,
)

log = logging.getLogger(__name__)

Fetch = Callable[[MarketData], Any]
OnOk = Callable[[Any], None]
OnErr = Callable[[DataError], None]


class _Bridge(QObject):
    done = Signal(int, bool, object)
    engine = Signal(bool, object)


class _Task(QRunnable):
    def __init__(self, task_id: int, fn: Fetch, md: MarketData, bridge: _Bridge) -> None:
        super().__init__()
        self._task_id = task_id
        self._fn = fn
        self._md = md
        self._bridge = bridge

    def run(self) -> None:
        try:
            payload, ok = self._fn(self._md), True
        except DataError as exc:
            payload, ok = exc, False
        except Exception:
            log.exception("Unexpected error in background task")
            payload, ok = DataError("Unexpected error — see log"), False
        self._bridge.done.emit(self._task_id, ok, payload)


class _EngineTask(QRunnable):
    def __init__(self, factory: Callable[[], MarketData], bridge: _Bridge) -> None:
        super().__init__()
        self._factory = factory
        self._bridge = bridge

    def run(self) -> None:
        try:
            md = self._factory()
        except Exception as exc:
            log.exception("Data engine failed to start")
            self._bridge.engine.emit(False, str(exc))
            return
        self._bridge.engine.emit(True, md)


@dataclass
class _Entry:
    key: str
    generation: int
    scoped: bool
    on_ok: OnOk
    on_err: OnErr
    timer: QTimer | None = None


class Loader(QObject):
    engineReady = Signal()
    engineFailed = Signal(str)
    onlineChanged = Signal(bool)

    def __init__(
        self, pool_size: int = 6, timeout_s: float = 25.0, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(pool_size)
        self._timeout_s = timeout_s
        self._bridge = _Bridge()
        self._bridge.done.connect(self._on_done)
        self._bridge.engine.connect(self._on_engine)
        self._md: MarketData | None = None
        self._generation = 0
        self._next_id = 0
        self._live: dict[int, _Entry] = {}
        self._by_key: dict[str, int] = {}
        self._pending: list[tuple[int, Fetch, float | None]] = []
        self._online = True
        self._engine_failed = False

    @property
    def ready(self) -> bool:
        return self._md is not None

    def start_engine(self, factory: Callable[[], MarketData]) -> None:
        self._pool.start(_EngineTask(factory, self._bridge))

    def new_generation(self) -> int:
        self._generation += 1
        return self._generation

    def submit(
        self,
        key: str,
        fn: Fetch,
        on_ok: OnOk,
        on_err: OnErr,
        *,
        scoped: bool = True,
        timeout_s: float | None = None,
    ) -> int:
        old = self._by_key.get(key)
        if old is not None:
            self._drop(old)
        self._next_id += 1
        task_id = self._next_id
        self._live[task_id] = _Entry(key, self._generation, scoped, on_ok, on_err)
        self._by_key[key] = task_id
        if self._engine_failed:
            # Deliver through the normal path on a later event-loop turn, never re-entrantly.
            this = weakref.ref(self)
            QTimer.singleShot(
                0, lambda tid=task_id: (ld := this()) is not None and ld._fail_engine_task(tid)
            )
        elif self._md is None:
            self._pending.append((task_id, fn, timeout_s))
        else:
            self._start(task_id, fn, timeout_s)
        return task_id

    def is_pending(self, key: str) -> bool:
        return key in self._by_key

    def shutdown(self, wait_ms: int = 1000) -> bool:
        for task_id in list(self._live):
            self._drop(task_id)
        self._pending.clear()
        self._pool.clear()
        finished = self._pool.waitForDone(wait_ms)
        if not finished:
            log.warning("Background data calls still running at shutdown")
        return finished

    # --- internals ------------------------------------------------------------------------

    def _start(self, task_id: int, fn: Fetch, timeout_s: float | None) -> None:
        entry = self._live.get(task_id)
        if entry is None or self._md is None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        # The slot must not hold a strong reference to the Loader: the timer is the Loader's
        # child, so if the slot were the Loader's last reference, the timer's (deferred)
        # destruction would destroy the Loader, which would delete the timer a second time.
        this = weakref.ref(self)
        timer.timeout.connect(
            lambda tid=task_id: (ld := this()) is not None and ld._on_timeout(tid)
        )
        timer.start(int((timeout_s or self._timeout_s) * 1000))
        entry.timer = timer
        self._pool.start(_Task(task_id, fn, self._md, self._bridge))

    def _drop(self, task_id: int) -> _Entry | None:
        entry = self._live.pop(task_id, None)
        if entry is None:
            return None
        if entry.timer is not None:
            entry.timer.stop()
            entry.timer.deleteLater()
        if self._by_key.get(entry.key) == task_id:
            del self._by_key[entry.key]
        return entry

    def _is_stale(self, entry: _Entry) -> bool:
        return entry.scoped and entry.generation != self._generation

    def _on_done(self, task_id: int, ok: bool, payload: object) -> None:
        entry = self._drop(task_id)
        if entry is None or self._is_stale(entry):
            return
        if ok:
            self._set_online(True)
            entry.on_ok(payload)
        else:
            assert isinstance(payload, DataError)
            if isinstance(payload, SourceUnavailable) and payload.reason == "network":
                self._set_online(False)
            entry.on_err(payload)

    def _on_timeout(self, task_id: int) -> None:
        entry = self._drop(task_id)
        if entry is None or self._is_stale(entry):
            return
        entry.on_err(DataTimeout("Timed out"))

    def _on_engine(self, ok: bool, payload: object) -> None:
        pending, self._pending = self._pending, []
        if ok:
            self._md = payload  # type: ignore[assignment]
            self.engineReady.emit()
            for task_id, fn, timeout_s in pending:
                self._start(task_id, fn, timeout_s)
            return
        self._engine_failed = True
        self.engineFailed.emit(str(payload))
        for task_id, _fn, _timeout in pending:
            self._fail_engine_task(task_id)

    def _fail_engine_task(self, task_id: int) -> None:
        entry = self._drop(task_id)
        if entry is not None:
            entry.on_err(EngineUnavailable("Data engine failed to load"))

    def _set_online(self, online: bool) -> None:
        if online != self._online:
            self._online = online
            self.onlineChanged.emit(online)
