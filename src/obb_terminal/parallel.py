"""Bounded parallel fetches on daemon threads, for the one-shot checker and the scorer.

Daemon threads so a stuck network call can never keep the process alive: no fetch starts after
`start_deadline`, and any still running at `end_deadline` is abandoned (both monotonic).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Hashable, Iterable

from obb_terminal.data.market import DataError

log = logging.getLogger(__name__)

UNEXPECTED = "Unexpected error — see log"


def fetch_all[K: Hashable, V](
    fn: Callable[[K], V], keys: Iterable[K], workers: int, start_deadline: float, end_deadline: float
) -> tuple[dict[K, V], dict[K, DataError]]:
    """(results, failures). A key in neither was skipped or abandoned."""
    todo: queue.Queue[K] = queue.Queue()
    count = 0
    for key in keys:
        todo.put(key)
        count += 1
    lock = threading.Lock()
    results: dict[K, V] = {}
    failures: dict[K, DataError] = {}

    def work() -> None:
        while time.monotonic() < start_deadline:
            try:
                key = todo.get_nowait()
            except queue.Empty:
                return
            try:
                value = fn(key)
            except DataError as exc:
                with lock:
                    failures[key] = exc
            except Exception:
                log.exception("Unexpected error fetching %s", key)
                with lock:
                    failures[key] = DataError(UNEXPECTED, str(key))
            else:
                with lock:
                    results[key] = value

    threads = [
        threading.Thread(target=work, daemon=True, name=f"fetch-{n}") for n in range(min(workers, count))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.0, end_deadline - time.monotonic()))
    with lock:
        return dict(results), dict(failures)
