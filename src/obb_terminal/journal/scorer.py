"""Score journal snapshots whose horizons have elapsed. No Qt; used by the app and the checker."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime

from obb_terminal.data.market import MarketData
from obb_terminal.domain.models import PriceSeries, Range
from obb_terminal.journal.models import Outcome
from obb_terminal.journal.scoring import Bars, evaluate_outcome
from obb_terminal.journal.store import Journal, JournalError
from obb_terminal.parallel import fetch_all

log = logging.getLogger(__name__)

LAST_SCORED = "last_scored_day"
BUDGET_S = 180.0
MARGIN_S = 15.0
WORKERS = 4


@dataclass(frozen=True, slots=True)
class ScoringResult:
    scored: int  # outcomes written
    pending: int  # due horizons still short of forward bars
    failed: tuple[str, ...] = ()  # symbols whose history failed (retried next run)
    skipped: tuple[str, ...] = ()  # symbols not fetched within the budget
    error: str | None = None  # the journal could not be read or written


def _bars(series: PriceSeries, today: date) -> Bars:
    """Daily closes before `today`: today's bar may still be an unfinished session."""
    by_day: dict[date, float] = {}
    for p in series.points:
        if p.at.date() < today:
            by_day[p.at.date()] = p.close
    return sorted(by_day.items())


def scoring_due(journal: Journal, today: date) -> bool:
    """True when the journal has not been scored today. False if it cannot be read."""
    try:
        return journal.get_meta(LAST_SCORED) != today.isoformat()
    except JournalError:
        return False


def run_scoring(
    md: MarketData,
    journal: Journal,
    today: date,
    *,
    now: datetime | None = None,
    budget_s: float = BUDGET_S,
    margin_s: float = MARGIN_S,
    workers: int = WORKERS,
) -> ScoringResult:
    """Fetch one year of daily history per symbol and benchmark, then write every outcome
    that can be computed. Never raises."""
    now = now or datetime.now(UTC)
    start = time.monotonic()
    try:
        due = journal.due(today)
    except JournalError as exc:
        return ScoringResult(0, 0, error=str(exc))
    symbols = sorted({s.symbol for s, _ in due} | {s.benchmark for s, _ in due})
    series, failures = fetch_all(
        lambda sym: _bars(md.history(sym, Range.Y1), today), symbols, workers,
        start + budget_s - margin_s, start + budget_s,
    )
    for symbol, exc in failures.items():
        log.info("Scoring: no history for %s (%s)", symbol, exc.user_message)
    skipped = tuple(s for s in symbols if s not in series and s not in failures)
    outcomes: list[Outcome] = []
    pending = 0
    for s, horizons in due:
        bars, bench = series.get(s.symbol), series.get(s.benchmark)
        if bars is None or bench is None:
            continue
        for h in horizons:
            o = evaluate_outcome(s, h, bars, bench, now)
            if o is None:
                pending += 1
            else:
                outcomes.append(o)
    try:
        if outcomes:
            journal.write_outcomes(outcomes)
        if not failures and not skipped:  # otherwise the next run (app or checker) tries again
            journal.set_meta(LAST_SCORED, today.isoformat())
    except JournalError as exc:
        return ScoringResult(0, pending, tuple(sorted(failures)), skipped, str(exc))
    log.info("Scoring: %d outcomes written, %d pending, %d failed, %d skipped",
             len(outcomes), pending, len(failures), len(skipped))
    return ScoringResult(len(outcomes), pending, tuple(sorted(failures)), skipped)
