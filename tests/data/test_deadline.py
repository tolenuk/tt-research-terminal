import threading
import time

import pytest

from obb_terminal.data.deadline import run_with_deadline
from obb_terminal.data.market import DataTimeout


def test_returns_value():
    assert run_with_deadline(lambda: 42, 1.0) == 42


def test_reraises_function_exception():
    def boom():
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        run_with_deadline(boom, 1.0)


def test_times_out_and_does_not_block():
    started = time.monotonic()
    with pytest.raises(DataTimeout):
        run_with_deadline(lambda: time.sleep(2), 0.1)
    assert time.monotonic() - started < 1.0


def test_waiting_for_a_slot_counts_against_deadline():
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    try:
        with pytest.raises(DataTimeout):
            run_with_deadline(lambda: 1, 0.1, slots=slots)
    finally:
        slots.release()


def test_slot_released_after_completion():
    slots = threading.BoundedSemaphore(1)
    assert run_with_deadline(lambda: 1, 1.0, slots=slots) == 1
    time.sleep(0.05)
    assert slots.acquire(timeout=0.5)
    slots.release()
