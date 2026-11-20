"""AlertEngine: the one place targets.json is changed, shared by the app and the checker.

`apply` evaluates fresh observations against the targets read under the lock, commits any
state transitions, releases the lock and only then notifies. Whichever process commits the
ARMED -> FIRED change sends the notification, so each hit is notified at most once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from obb_terminal.alerts.notify import Notifier
from obb_terminal.alerts.store import LOCK_TIMEOUT_S, TargetsLoad, TargetsRepo
from obb_terminal.domain.models import ValuationInputs
from obb_terminal.domain.targets import (
    AlertEvent,
    Indicator,
    RowEvaluation,
    SymbolTargets,
    TargetsFile,
    cleared,
    edit,
    evaluate,
    notification_text,
    set_thesis,
    transition,
)
from obb_terminal.fsutil import LockBusy

log = logging.getLogger(__name__)

BUSY = "busy"
SAVE_FAILED = "Could not save targets — see log"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    file: TargetsFile
    rows: tuple[RowEvaluation, ...]  # one per observation, after any transition
    events: tuple[AlertEvent, ...]  # committed transitions only
    notified: int  # events the notifier accepted
    error: str | None = None  # BUSY (lock held elsewhere) or SAVE_FAILED; no events then

    @property
    def busy(self) -> bool:
        return self.error == BUSY


class AlertEngine:
    def __init__(
        self,
        repo: TargetsRepo,
        notifier: Notifier,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repo = repo
        self._notifier = notifier
        self._clock = clock

    def snapshot(self) -> TargetsLoad:
        """Lock-free read for display. Never raises."""
        return self._repo.load()

    def apply(
        self, observations: Sequence[ValuationInputs], *, lock_timeout_s: float = LOCK_TIMEOUT_S
    ) -> ApplyResult:
        """Evaluate, transition, commit, then notify. Never raises.

        An observation not newer than the symbol's last applied one (`observed_at`) is
        evaluated for display but makes no transition.
        """
        try:
            with self._repo.transaction(lock_timeout_s) as txn:
                file, rows, events = self._transition_all(txn.file, observations)
                if file != txn.file:
                    txn.commit(file)
        except LockBusy:
            log.info("Targets busy; %d observation(s) not applied", len(observations))
            return self._display_only(observations, BUSY)
        except OSError:
            log.exception("Could not apply targets")
            return self._display_only(observations, SAVE_FAILED)
        notified = sum(1 for e in events if self._notifier.notify(*notification_text(e)))
        return ApplyResult(file, rows, events, notified)

    def set_target(
        self,
        symbol: str,
        ind: Indicator,
        level: float | None,
        enabled: bool,
        *,
        lock_timeout_s: float = LOCK_TIMEOUT_S,
    ) -> TargetsFile:
        """Set (level None: delete) one target. Raises ValueError, LockBusy or OSError."""
        with self._repo.transaction(lock_timeout_s) as txn:
            new = txn.file.put(edit(txn.file.get(symbol), symbol, ind, level, enabled))
            txn.commit(new)
            return new

    def remove_target(
        self, symbol: str, ind: Indicator, *, lock_timeout_s: float = LOCK_TIMEOUT_S
    ) -> TargetsFile:
        return self.set_target(symbol, ind, None, True, lock_timeout_s=lock_timeout_s)

    def set_thesis_break(
        self, symbol: str, level: float | None, *, lock_timeout_s: float = LOCK_TIMEOUT_S
    ) -> TargetsFile:
        """Set (level None: remove) the thesis break. Raises ValueError, LockBusy or OSError."""
        with self._repo.transaction(lock_timeout_s) as txn:
            new = txn.file.put(set_thesis(txn.file.get(symbol), symbol, level))
            txn.commit(new)
            return new

    def apply_call(
        self,
        symbol: str,
        *,
        price_level: float | None,
        thesis_level: float | None,
        lock_timeout_s: float = LOCK_TIMEOUT_S,
    ) -> TargetsFile:
        """Write an AI call's Price target and/or thesis break in one transaction; the parts
        given are re-armed, the others left alone. Raises ValueError (nothing to set or a bad
        level), LockBusy or OSError."""
        if price_level is None and thesis_level is None:
            raise ValueError("Nothing to set")
        with self._repo.transaction(lock_timeout_s) as txn:
            st = txn.file.get(symbol)
            if price_level is not None:
                st = edit(st, symbol, Indicator.PRICE, price_level, True)
            if thesis_level is not None:
                st = set_thesis(st, symbol, thesis_level)
            assert st is not None
            new = txn.file.put(st)
            txn.commit(new)
            return new

    def clear_targets(self, symbol: str, *, lock_timeout_s: float = LOCK_TIMEOUT_S) -> TargetsFile:
        """Remove the buy targets; a thesis break stays (it has its own Clear action)."""
        with self._repo.transaction(lock_timeout_s) as txn:
            new = txn.file.put(cleared(txn.file.get(symbol), symbol))
            txn.commit(new)
            return new

    # --- internals ------------------------------------------------------------------------

    def _transition_all(
        self, file: TargetsFile, observations: Sequence[ValuationInputs]
    ) -> tuple[TargetsFile, tuple[RowEvaluation, ...], tuple[AlertEvent, ...]]:
        now = self._clock()
        rows: list[RowEvaluation] = []
        events: list[AlertEvent] = []
        for i in observations:
            st = file.get(i.symbol)
            if st is not None and not self._is_stale(st, i):
                new, fired = transition(st, evaluate(i, st), now)
                if i.fetched_at is not None:
                    new = replace(new, observed_at=i.fetched_at)
                file = file.put(new)
                events.extend(fired)
            rows.append(evaluate(i, file.get(i.symbol)))
        return file, tuple(rows), tuple(events)

    @staticmethod
    def _is_stale(st: SymbolTargets, i: ValuationInputs) -> bool:
        return (
            st.observed_at is not None and i.fetched_at is not None and i.fetched_at <= st.observed_at
        )

    def _display_only(self, observations: Sequence[ValuationInputs], error: str) -> ApplyResult:
        file = self._repo.load().file
        rows = tuple(evaluate(i, file.get(i.symbol)) for i in observations)
        return ApplyResult(file, rows, (), 0, error)
