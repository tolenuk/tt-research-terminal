"""How one call did over one horizon. Pure: bars are (date, close) pairs, oldest first."""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from datetime import date, datetime

from obb_terminal.journal.models import FLAT_BAND, Classification, Outcome, OutcomeStatus, Snapshot

Bars = Sequence[tuple[date, float]]

OUT_OF_RANGE = "history out of range"
BENCH_MISSING = "benchmark missing"
BENCH_STALE = "benchmark data stale"
MAX_BENCH_GAP_DAYS = 5  # a benchmark close older than this before a bar date is not trusted
BAD_PRICE = "bad price"


def due_after_days(horizon: int) -> int:
    """Calendar days after which `horizon` trading days have surely passed (plus slack)."""
    return math.ceil(horizon * 7 / 5) + 2


def _on_or_before(bars: Bars, day: date) -> tuple[date, float] | None:
    i = bisect.bisect_right([d for d, _ in bars], day)
    return bars[i - 1] if i else None


def _unable(s: Snapshot, horizon: int, reason: str, now: datetime) -> Outcome:
    assert s.id is not None
    return Outcome(s.id, horizon, OutcomeStatus.UNABLE, reason, None, None, None, None, None, None, None, None,
                   None, None, now)


def evaluate_outcome(s: Snapshot, horizon: int, bars: Bars, bench: Bars, now: datetime) -> Outcome | None:
    """The outcome, or None while it is still pending (not enough forward bars yet)."""
    assert s.id is not None
    days = [d for d, _ in bars]
    start = bisect.bisect_left(days, s.day)
    if start == len(days):
        return None
    if start == 0 and days[0] > s.day:
        return _unable(s, horizon, OUT_OF_RANGE, now)
    end = start + horizon
    if end >= len(days):
        return None
    start_day, start_price = bars[start]
    end_day, end_price = bars[end]
    if not (start_price > 0 and end_price > 0):
        return _unable(s, horizon, BAD_PRICE, now)
    first, last = _on_or_before(bench, start_day), _on_or_before(bench, end_day)
    if first is None or last is None or first[1] <= 0:
        return _unable(s, horizon, BENCH_MISSING, now)
    if (start_day - first[0]).days > MAX_BENCH_GAP_DAYS or (end_day - last[0]).days > MAX_BENCH_GAP_DAYS:
        return _unable(s, horizon, BENCH_STALE, now)
    b0, b1 = first[1], last[1]
    ret, bench_ret = end_price / start_price - 1, b1 / b0 - 1
    window = [c for _, c in bars[start : end + 1]]
    if s.direction > 0:
        adverse: float | None = max(0.0, 1 - min(window) / start_price)
    elif s.direction < 0:
        adverse = max(0.0, max(window) / start_price - 1)
    else:
        adverse = None
    return Outcome(s.id, horizon, OutcomeStatus.DONE, None, start_day, end_day, start_price, end_price, b0, b1,
                   ret, bench_ret, ret - bench_ret, adverse, now)


def classify(direction: int, excess: float | None) -> Classification | None:
    if excess is None:
        return None
    if direction == 0:
        return Classification.NEUTRAL
    if abs(excess) < FLAT_BAND - 1e-12:
        return Classification.FLAT
    return Classification.HIT if (excess > 0) == (direction > 0) else Classification.MISS
