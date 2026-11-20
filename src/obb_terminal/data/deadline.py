"""Bound a blocking call by a deadline without blocking interpreter exit.

The call runs on a daemon thread. On timeout the thread is abandoned (its result is
dropped) but keeps its semaphore slot until it returns, so a wedged source turns into
fast timeouts instead of unbounded thread growth.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future

from obb_terminal.data.market import DataTimeout

_SLOTS = threading.BoundedSemaphore(12)


def run_with_deadline[T](
    fn: Callable[[], T], timeout: float, slots: threading.BoundedSemaphore | None = None
) -> T:
    sem = slots or _SLOTS
    deadline = time.monotonic() + timeout
    if not sem.acquire(timeout=timeout):
        raise DataTimeout("Timed out")
    future: Future[T] = Future()

    def target() -> None:
        try:
            future.set_result(fn())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            future.set_exception(exc)
        finally:
            sem.release()

    threading.Thread(target=target, daemon=True, name="data-call").start()
    try:
        return future.result(timeout=max(0.0, deadline - time.monotonic()))
    except TimeoutError:
        raise DataTimeout("Timed out") from None
