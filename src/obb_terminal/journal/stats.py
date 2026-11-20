"""Track-record statistics. Pure; every rate needs MIN_SAMPLE completed outcomes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import fmean

from obb_terminal.journal.models import (
    MIN_SAMPLE,
    MIN_SIDE,
    Bucket,
    Classification,
    ComponentIc,
    Kind,
    OutcomeStatus,
    Row,
)
from obb_terminal.journal.scoring import classify

FACTOR_MAX = 1.2
FACTOR_MIN = 1 / FACTOR_MAX
SHRINK = 30  # ic x n / (n + SHRINK): a beta-prior style pull towards "no evidence"


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Rank correlation with average ranks for ties; None for n < 3 or a constant input."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = fmean(rx), fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def suggested_factor(ic: float, n: int) -> float:
    return min(max(1 + ic * n / (n + SHRINK), FACTOR_MIN), FACTOR_MAX)


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def bucket(rows: Sequence[Row], kind: Kind, horizon: int) -> Bucket:
    done = pending = unable = 0
    counts = dict.fromkeys(Classification, 0)
    bull: list[float] = []
    bear: list[float] = []
    adverse: list[float] = []
    pairs: list[tuple[float, float]] = []
    for s, outcomes in rows:
        if s.kind is not kind:
            continue
        o = outcomes.get(horizon)
        if o is None:
            pending += 1
            continue
        if o.status is OutcomeStatus.UNABLE or o.excess is None:
            unable += 1
            continue
        done += 1
        c = classify(s.direction, o.excess)
        if c is not None:
            counts[c] += 1
        if s.direction > 0:
            bull.append(o.excess)
        elif s.direction < 0:
            bear.append(o.excess)
        if o.adverse is not None:
            adverse.append(o.adverse)
        if s.strength is not None:
            pairs.append((s.strength, o.excess))
    hits, misses = counts[Classification.HIT], counts[Classification.MISS]
    enough = done >= MIN_SAMPLE
    mb = _mean(bull) if enough and len(bull) >= MIN_SIDE else None
    mr = _mean(bear) if enough and len(bear) >= MIN_SIDE else None
    return Bucket(
        kind=kind,
        horizon=horizon,
        done=done,
        pending=pending,
        unable=unable,
        hits=hits,
        misses=misses,
        flats=counts[Classification.FLAT],
        enough=enough,
        hit_rate=hits / (hits + misses) if enough and hits + misses >= MIN_SAMPLE else None,
        mean_excess_bull=mb,
        mean_excess_bear=mr,
        spread=None if mb is None or mr is None else mb - mr,
        ic=spearman([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) >= MIN_SAMPLE else None,
        worst_adverse=max(adverse) if enough and adverse else None,
    )


def component_ics(rows: Sequence[Row], horizon: int) -> list[ComponentIc]:
    """Rank IC of each Outlook component against excess return (score snapshots only)."""
    by_component: dict[str, list[tuple[float, float]]] = {}
    for s, outcomes in rows:
        o = outcomes.get(horizon)
        if s.kind is not Kind.SCORE or o is None or o.status is not OutcomeStatus.DONE or o.excess is None:
            continue
        for key, value in s.components:
            by_component.setdefault(key, []).append((value, o.excess))
    out = []
    for key, pairs in by_component.items():
        n = len(pairs)
        ic = spearman([a for a, _ in pairs], [b for _, b in pairs]) if n >= MIN_SAMPLE else None
        out.append(ComponentIc(key, horizon, n, ic, None if ic is None else suggested_factor(ic, n)))
    return out
