"""Run small blocking jobs (target edits, systemctl) one at a time, in submission order, on a
single worker thread, and deliver results on the GUI thread.

Unlike the Loader, a job never waits for the data engine, is never superseded by a later job
with the same key, and reports any Exception (not only DataError) to `on_err`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)

OnOk = Callable[[Any], None]
OnErr = Callable[[Exception], None]


class _Bridge(QObject):
    done = Signal(int, bool, object)


class _Job(QRunnable):
    def __init__(self, job_id: int, fn: Callable[[], Any], bridge: _Bridge) -> None:
        super().__init__()
        self._job_id = job_id
        self._fn = fn
        self._bridge = bridge

    def run(self) -> None:
        try:
            payload, ok = self._fn(), True
        except Exception as exc:
            payload, ok = exc, False
        self._bridge.done.emit(self._job_id, ok, payload)


class SerialJobs(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._bridge = _Bridge()
        self._bridge.done.connect(self._on_done)
        self._callbacks: dict[int, tuple[OnOk, OnErr]] = {}
        self._next_id = 0
        self._closed = False

    def submit(self, fn: Callable[[], Any], on_ok: OnOk, on_err: OnErr) -> None:
        if self._closed:
            return
        self._next_id += 1
        self._callbacks[self._next_id] = (on_ok, on_err)
        self._pool.start(_Job(self._next_id, fn, self._bridge))

    def shutdown(self, wait_ms: int = 6000) -> bool:
        """Accept no more jobs and drop pending callbacks, but let already-submitted jobs run
        (a queued target write must not be lost), waiting up to `wait_ms` for them."""
        self._closed = True
        self._callbacks.clear()
        finished = self._pool.waitForDone(wait_ms)
        if not finished:
            log.warning("A background job was still running at shutdown")
        return finished

    def _on_done(self, job_id: int, ok: bool, payload: object) -> None:
        callbacks = self._callbacks.pop(job_id, None)
        if callbacks is None:
            return
        on_ok, on_err = callbacks
        if ok:
            on_ok(payload)
        else:
            assert isinstance(payload, Exception)
            on_err(payload)
