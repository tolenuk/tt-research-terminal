import pytest

from obb_terminal.journal.models import MIN_SAMPLE, MIN_SIDE, Kind, Outcome, OutcomeStatus
from obb_terminal.journal.stats import bucket, component_ics, spearman, suggested_factor
from tests.journal.samples import DAY0, NOW, snap


def done(sid, excess, adverse=0.0, h=20):
    return Outcome(sid, h, OutcomeStatus.DONE, None, DAY0, DAY0, 100, 100, 1, 1, excess, 0.0, excess, adverse, NOW)


def unable(sid, h=20):
    return Outcome(sid, h, OutcomeStatus.UNABLE, "x", None, None, None, None, None, None, None, None, None, None, NOW)


def test_spearman():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833, rel=1e-6)  # average ranks for ties
    assert spearman([1, 2], [1, 2]) is None  # n < 3
    assert spearman([5, 5, 5], [1, 2, 3]) is None  # constant input


def rows(n, *, kind=Kind.SCORE, pattern=None):
    """n snapshots alternating bullish/bearish; bulls beat the market by 5%, bears lag by 3%."""
    out = []
    for i in range(n):
        direction = 1 if i % 2 == 0 else -1
        s = snap(id=i, kind=kind, direction=direction, strength=80.0 if direction > 0 else 20.0)
        out.append((s, {20: done(i, 0.05 if direction > 0 else -0.03, adverse=0.01 * (i % 5))}))
    return out


def test_bucket_below_the_threshold_keeps_counts_only():
    b = bucket(rows(MIN_SAMPLE - 1), Kind.SCORE, 20)
    assert (b.done, b.enough, b.hits, b.misses) == (MIN_SAMPLE - 1, False, MIN_SAMPLE - 1, 0)
    assert b.hit_rate is None and b.ic is None and b.spread is None and b.mean_excess_bull is None


def test_bucket_at_the_threshold_has_numbers():
    b = bucket(rows(MIN_SAMPLE), Kind.SCORE, 20)
    assert b.enough and b.hit_rate == 1.0
    assert b.mean_excess_bull == pytest.approx(0.05) and b.mean_excess_bear == pytest.approx(-0.03)
    assert b.spread == pytest.approx(0.08)
    assert b.ic == pytest.approx(1.0)
    assert b.worst_adverse == pytest.approx(0.04)


def test_bucket_counts_pending_unable_and_other_kinds():
    data = rows(4)
    data.append((snap(id=90), {}))  # pending
    data.append((snap(id=91), {20: unable(91)}))
    data.append((snap(id=92, kind=Kind.AI), {20: done(92, 0.5)}))
    b = bucket(data, Kind.SCORE, 20)
    assert (b.done, b.pending, b.unable) == (4, 1, 1)


def test_flat_and_neutral_calls_are_outside_the_hit_rate():
    data = [(snap(id=i, direction=1), {20: done(i, 0.05)}) for i in range(20)]
    data += [(snap(id=100 + i, direction=1), {20: done(100 + i, 0.01)}) for i in range(5)]  # flat
    data += [(snap(id=200 + i, direction=0), {20: done(200 + i, -0.2)}) for i in range(5)]  # neutral
    b = bucket(data, Kind.SCORE, 20)
    assert b.done == 30 and (b.hits, b.misses, b.flats) == (20, 0, 5)
    assert b.enough and b.hit_rate is None  # only 20 directional calls: no rate yet
    assert b.mean_excess_bull is not None and b.mean_excess_bear is None and b.spread is None


@pytest.mark.parametrize(("ic", "n", "want"), [(0.5, 30, 1.2), (0.1, 30, 1.05), (-0.9, 1000, 1 / 1.2), (0.0, 50, 1.0)])
def test_suggested_factor_is_shrunk_and_bounded(ic, n, want):
    assert suggested_factor(ic, n) == pytest.approx(want)


def test_component_ics():
    data = []
    for i in range(MIN_SAMPLE):
        s = snap(id=i, components=(("upside", float(i)), ("momentum", float(-i))))
        data.append((s, {20: done(i, i / 100)}))
    ics = {c.component: c for c in component_ics(data, 20)}
    assert ics["upside"].ic == pytest.approx(1.0) and ics["upside"].factor == pytest.approx(1.2)
    assert ics["momentum"].ic == pytest.approx(-1.0) and ics["momentum"].factor == pytest.approx(1 / 1.2)
    few = {c.component: c for c in component_ics(data[:10], 20)}
    assert few["upside"].n == 10 and few["upside"].ic is None and few["upside"].factor is None


def test_each_side_needs_its_own_sample():
    data = [(snap(id=i, direction=1), {20: done(i, 0.05)}) for i in range(MIN_SAMPLE)]
    data += [(snap(id=100 + i, direction=-1), {20: done(100 + i, -0.05)}) for i in range(MIN_SIDE - 1)]
    b = bucket(data, Kind.SCORE, 20)
    assert b.hit_rate == 1.0 and b.mean_excess_bear is None and b.spread is None
