"""What the dashboards read: one snapshot of the watchlist, its AI calls and the journal."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from obb_terminal.ai.models import CachedReport
from obb_terminal.domain.models import Score, ValuationInputs
from obb_terminal.domain.scoring import score
from obb_terminal.domain.targets import RowEvaluation
from obb_terminal.domain.valuation import default_assumptions, value
from obb_terminal.journal.models import Row
from obb_terminal.journal.store import Journal, JournalError
from obb_terminal.ui.watchlist_tab.model import RowState, RowView

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeskEntry:
    symbol: str
    inputs: ValuationInputs | None
    row: RowEvaluation | None
    score: Score | None
    report: CachedReport | None
    error: str | None = None
    loading: bool = False

    @property
    def incomplete(self) -> bool:
        """Part of the data could not be fetched, so a fair value or score may be badly off."""
        return self.inputs is not None and bool(self.inputs.gaps)


@dataclass(frozen=True, slots=True)
class DeskSnapshot:
    today: date
    now: datetime
    entries: tuple[DeskEntry, ...]
    journal_rows: tuple[Row, ...] = ()
    journal_on: bool = False
    journal_error: bool = False

    @property
    def loading(self) -> int:
        return sum(1 for e in self.entries if e.loading)

    @property
    def data_from(self) -> datetime | None:
        """When the oldest of the fetched data was fetched."""
        times = [e.inputs.fetched_at for e in self.entries if e.inputs is not None and e.inputs.fetched_at]
        return min(times) if times else None

    def entry(self, symbol: str) -> DeskEntry | None:
        return next((e for e in self.entries if e.symbol == symbol), None)


def _score(inputs: ValuationInputs) -> Score | None:
    try:
        a = default_assumptions(inputs)
        return score(inputs, value(inputs, a), a)
    except Exception:  # one odd symbol must not blank every dashboard
        log.exception("Could not score %s", inputs.symbol)
        return None


def _entry(view: RowView, last_report: Callable[[str], CachedReport | None]) -> DeskEntry:
    if view.state is RowState.LOADING:
        return DeskEntry(view.symbol, None, None, None, None, loading=True)
    if view.state is RowState.ERROR or view.inputs is None:
        error = view.row.error if view.row is not None else None
        return DeskEntry(view.symbol, None, view.row, None, None, error or "No data")
    return DeskEntry(
        view.symbol, view.inputs, view.row, _score(view.inputs), last_report(view.symbol)
    )


def build_snapshot(
    views: Sequence[RowView],
    last_report: Callable[[str], CachedReport | None],
    journal: Journal | None,
    today: date,
) -> DeskSnapshot:
    """Blocking (SerialJobs): reads saved reports and the journal."""
    rows: tuple[Row, ...] = ()
    failed = False
    if journal is not None:
        try:
            rows = tuple(journal.rows())
        except JournalError as exc:
            log.error("Could not read the journal for the dashboards", exc_info=exc)
            failed = True
    return DeskSnapshot(
        today, datetime.now(UTC), tuple(_entry(v, last_report) for v in views), rows,
        journal_on=journal is not None, journal_error=failed,
    )
