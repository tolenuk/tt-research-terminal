from datetime import timedelta

import pytest

from obb_terminal.journal.models import Classification, OutcomeStatus
from obb_terminal.journal.scoring import (
    BENCH_MISSING,
    OUT_OF_RANGE,
    classify,
    due_after_days,
    evaluate_outcome,
)
from tests.journal.samples import DAY0, NOW, bars, snap, weekdays


def test_hand_computed_excess_and_adverse_for_a_bullish_call():
    s = snap(id=7, direction=1)
    stock = bars([100, 90, 95, 110, 120, 130])  # start 100, end (h=4) 120, min 90
    bench = bars([50, 50, 51, 52, 55, 60])  # 50 -> 55
    o = evaluate_outcome(s, 4, stock, bench, NOW)
    assert o.status is OutcomeStatus.DONE and o.snapshot_id == 7 and o.horizon == 4
    assert (o.start_date, o.end_date) == (DAY0, weekdays(DAY0, 5)[4])
    assert (o.start_price, o.end_price, o.bench_start, o.bench_end) == (100, 120, 50, 55)
    assert o.ret == pytest.approx(0.20) and o.bench_ret == pytest.approx(0.10)
    assert o.excess == pytest.approx(0.10)
    assert o.adverse == pytest.approx(0.10)  # fell 10% against the call before rising
    assert o.evaluated_at == NOW


def test_bearish_adverse_is_the_rally_against_it():
    o = evaluate_outcome(snap(id=1, direction=-1), 3, bars([100, 104, 99, 95]), bars([10, 10, 10, 10]), NOW)
    assert o.adverse == pytest.approx(0.04) and o.excess == pytest.approx(-0.05)


def test_neutral_call_has_no_adverse_and_never_goes_negative():
    o = evaluate_outcome(snap(id=1, direction=0), 2, bars([100, 101, 102]), bars([1, 1, 1]), NOW)
    assert o.adverse is None
    up = evaluate_outcome(snap(id=1, direction=1), 2, bars([100, 101, 102]), bars([1, 1, 1]), NOW)
    assert up.adverse == 0.0


def test_start_is_the_first_bar_on_or_after_the_day():
    saturday = DAY0 + timedelta(days=5)
    s = snap(id=1, day=saturday)
    o = evaluate_outcome(s, 1, bars([100, 100, 100, 100, 100, 200, 300]), bars([1] * 7), NOW)
    assert o.start_date == DAY0 + timedelta(days=7) and o.start_price == 200 and o.end_price == 300


def test_too_few_forward_bars_is_pending():
    assert evaluate_outcome(snap(id=1), 5, bars([100] * 5), bars([1] * 5), NOW) is None
    after = snap(id=1, day=DAY0 + timedelta(days=30))
    assert evaluate_outcome(after, 1, bars([100] * 5), bars([1] * 5), NOW) is None


def test_history_starting_after_the_day_is_unable():
    o = evaluate_outcome(snap(id=1, day=DAY0 - timedelta(days=1)), 1, bars([100, 101]), bars([1, 1]), NOW)
    assert o.status is OutcomeStatus.UNABLE and o.reason == OUT_OF_RANGE and o.excess is None


def test_benchmark_aligns_on_or_before_and_missing_is_unable():
    stock = bars([100, 110, 121])
    bench = bars([10, 12], start=DAY0)  # no bench bar on the end date: last on-or-before is used
    o = evaluate_outcome(snap(id=1), 2, stock, bench, NOW)
    assert o.bench_end == 12 and o.bench_ret == pytest.approx(0.2)
    late = bars([10, 12], start=DAY0 + timedelta(days=1))  # no bench bar on or before the start
    assert evaluate_outcome(snap(id=1), 2, stock, late, NOW).reason == BENCH_MISSING
    assert evaluate_outcome(snap(id=1), 2, stock, [], NOW).reason == BENCH_MISSING


def test_non_positive_start_is_unable():
    o = evaluate_outcome(snap(id=1), 1, bars([0.0, 5.0]), bars([1, 1]), NOW)
    assert o.status is OutcomeStatus.UNABLE


@pytest.mark.parametrize(
    ("direction", "excess", "want"),
    [(1, 0.02, Classification.HIT), (1, 0.0199, Classification.FLAT), (1, -0.02, Classification.MISS),
     (-1, -0.02, Classification.HIT), (-1, 0.05, Classification.MISS), (-1, -0.01, Classification.FLAT),
     (0, 0.30, Classification.NEUTRAL), (1, None, None)],
)
def test_classify_with_the_two_percent_flat_band(direction, excess, want):
    assert classify(direction, excess) is want


@pytest.mark.parametrize(("h", "days"), [(20, 30), (60, 86), (120, 170)])
def test_due_after_days(h, days):
    assert due_after_days(h) == days


def test_stale_benchmark_close_is_unable():
    from obb_terminal.journal.scoring import BENCH_STALE

    stock = bars([100.0] * 12)
    bench = bars([10.0, 10.0])  # ends on DAY0+1, a week before the end bar
    o = evaluate_outcome(snap(id=1), 10, stock, bench, NOW)
    assert o.status is OutcomeStatus.UNABLE and o.reason == BENCH_STALE
