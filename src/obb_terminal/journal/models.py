"""Journal records. A snapshot is one call (an Outlook score or an AI call) on one day; an
outcome is how it did over one horizon, relative to the local market."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

HORIZONS: tuple[int, ...] = (20, 60, 120)  # trading days
FLAT_BAND = 0.02  # |excess| below this is neither a hit nor a miss
MIN_SAMPLE = 30  # completed outcomes (and directional calls) needed before a rate is shown
MIN_SIDE = 10  # bullish (or bearish) outcomes needed before that side's mean is shown


class Kind(StrEnum):
    SCORE = "score"
    AI = "ai"
    DEBATE = "debate"


KIND_LABELS: dict[Kind, str] = {Kind.SCORE: "Outlook score", Kind.AI: "AI analyst", Kind.DEBATE: "Debate"}


class OutcomeStatus(StrEnum):
    DONE = "done"
    UNABLE = "unable"


class Classification(StrEnum):
    HIT = "hit"
    MISS = "miss"
    FLAT = "flat"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class Snapshot:
    symbol: str
    day: date  # the data date of the call
    kind: Kind
    recorded_at: datetime
    price: float  # trading currency
    currency: str
    benchmark: str
    direction: int  # +1 bullish, -1 bearish, 0 neutral
    strength: float | None  # outlook value (score) or direction x conviction (AI)
    outlook: float | None
    band: str | None
    margin: float | None
    fair_value: float | None
    components: tuple[tuple[str, float], ...]  # Outlook component key -> value (0-100)
    stance: str | None
    action: str | None
    conviction: str | None
    entry_low: float | None
    entry_high: float | None
    thesis_break: float | None
    target_price: float | None
    model: str | None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    snapshot_id: int
    horizon: int
    status: OutcomeStatus
    reason: str | None
    start_date: date | None
    end_date: date | None
    start_price: float | None
    end_price: float | None
    bench_start: float | None
    bench_end: float | None
    ret: float | None
    bench_ret: float | None
    excess: float | None
    adverse: float | None  # >= 0: the worst move against the call within the window
    evaluated_at: datetime


# A snapshot with its outcomes by horizon (missing horizon = pending).
Row = tuple[Snapshot, dict[int, Outcome]]


@dataclass(frozen=True, slots=True)
class Bucket:
    kind: Kind
    horizon: int
    done: int
    pending: int
    unable: int
    hits: int
    misses: int
    flats: int
    enough: bool  # done >= MIN_SAMPLE; the rates below are None otherwise
    hit_rate: float | None
    mean_excess_bull: float | None
    mean_excess_bear: float | None
    spread: float | None
    ic: float | None
    worst_adverse: float | None


@dataclass(frozen=True, slots=True)
class ComponentIc:
    component: str
    horizon: int
    n: int
    ic: float | None
    factor: float | None  # suggested Outlook weight multiplier, x0.83 - x1.2
