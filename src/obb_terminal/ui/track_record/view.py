"""What the TRACK RECORD tab shows, built off the GUI thread, and its cell text. No Qt.

Every cell is (text, sentence): the text carries the meaning on its own (no colour-only
meaning) and the sentence is the tooltip and accessible description.
"""

from __future__ import annotations

from dataclasses import dataclass

from obb_terminal.domain.models import Component
from obb_terminal.domain.scoring import LABELS
from obb_terminal.journal.models import (
    HORIZONS,
    KIND_LABELS,
    MIN_SAMPLE,
    Bucket,
    Classification,
    ComponentIc,
    Kind,
    OutcomeStatus,
    Row,
)
from obb_terminal.journal.record import ETF_MODEL
from obb_terminal.journal.scorer import LAST_SCORED
from obb_terminal.journal.scoring import classify
from obb_terminal.journal.stats import bucket, component_ics
from obb_terminal.journal.store import Journal

DASH = "—"
RECENT_LIMIT = 100
Cell = tuple[str, str]
CALLS_HEADERS = ("Kind", "Horizon", "Done / pending", "Hit rate", "Excess (bullish)", "Excess (bearish)",
                 "Spread", "Rank IC", "Worst adverse")
COMPONENT_HEADERS = ("Component", "Horizon", "Samples", "Rank IC", "Suggested weight")
RECENT_HEADERS = ("Date", "Symbol", "Kind", "Signal", "Price", *(f"{h}d excess" for h in HORIZONS))
_MARKS = {Classification.HIT: " ✓", Classification.MISS: " ✗"}


@dataclass(frozen=True, slots=True)
class TrackRecordView:
    snapshots: int
    outcomes: int
    last_scored: str | None
    buckets: tuple[Bucket, ...]  # Kind order x HORIZONS
    components: tuple[ComponentIc, ...]  # Component order x HORIZONS
    recent: tuple[Row, ...]  # newest first


def load_view(journal: Journal, recent_limit: int = RECENT_LIMIT) -> TrackRecordView:
    """Blocking (SerialJobs). Raises JournalError."""
    all_rows = journal.rows()
    # ETF Outlook rows use other components and scales: they stay in the recent list but out of
    # the score statistics until they have enough samples of their own.
    rows = [r for r in all_rows if r[0].model != ETF_MODEL]
    snapshots, outcomes = journal.counts()
    order = {c.value: n for n, c in enumerate(Component)}
    components = sorted(
        (ic for h in HORIZONS for ic in component_ics(rows, h)),
        key=lambda ic: (order.get(ic.component, len(order)), ic.horizon),
    )
    return TrackRecordView(
        snapshots=snapshots,
        outcomes=outcomes,
        last_scored=journal.get_meta(LAST_SCORED),
        buckets=tuple(bucket(rows, kind, h) for kind in Kind for h in HORIZONS),
        components=tuple(components),
        recent=tuple(journal.recent(recent_limit)),
    )


def _pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return DASH
    return f"{value * 100:+.1f}%" if signed else f"{value * 100:.1f}%"


def summary_text(view: TrackRecordView) -> str:
    last = view.last_scored or "never"
    return f"Journal: {view.snapshots} snapshots · {view.outcomes} outcomes · last scored {last}"


def bucket_cells(b: Bucket) -> list[Cell]:
    name = f"{KIND_LABELS[b.kind]}, {b.horizon} trading days"
    counts = f"{b.done} / {b.pending}"
    head = [
        (KIND_LABELS[b.kind], name),
        (f"{b.horizon}d", name),
        (counts, f"{name}: {b.done} scored, {b.pending} pending, {b.unable} not scorable"),
    ]
    if not b.enough:
        need = f"{name}: only {b.done} scored calls; rates appear at {MIN_SAMPLE}"
        return [*head, (f"n={b.done} — needs {MIN_SAMPLE}", need), *[(DASH, need)] * 5]
    directional = b.hits + b.misses
    hit = DASH if b.hit_rate is None else f"{b.hit_rate * 100:.0f}%"
    return [
        *head,
        (hit, f"{name}: {b.hits} of {directional} directional calls beat the market by 2% or more in their "
              f"direction ({b.flats} were within 2%)"),
        (_pct(b.mean_excess_bull), f"{name}: mean excess return after bullish calls"),
        (_pct(b.mean_excess_bear), f"{name}: mean excess return after bearish calls"),
        (DASH if b.spread is None else f"{b.spread * 100:+.1f} pts",
         f"{name}: bullish minus bearish mean excess, in percentage points"),
        (DASH if b.ic is None else f"{b.ic:.2f}",
         f"{name}: rank correlation between call strength and excess return (1 = perfectly ordered)"),
        (_pct(b.worst_adverse, signed=False), f"{name}: the largest move against a call within its window"),
    ]


def _component_label(key: str) -> str:
    try:
        return LABELS[Component(key)]
    except ValueError:  # a component a later version added
        return key


def component_cells(ic: ComponentIc) -> list[Cell]:
    label = _component_label(ic.component)
    name = f"{label}, {ic.horizon} trading days"
    if ic.ic is None or ic.factor is None:
        need = f"{name}: {ic.n} scored snapshots; the IC appears at {MIN_SAMPLE}"
        return [(label, name), (f"{ic.horizon}d", name), (str(ic.n), need), (f"needs {MIN_SAMPLE}", need), (DASH, need)]
    return [
        (label, name),
        (f"{ic.horizon}d", name),
        (str(ic.n), f"{name}: {ic.n} scored snapshots"),
        (f"{ic.ic:.2f}", f"{name}: rank correlation of the component with excess return"),
        (f"×{ic.factor:.2f}", f"{name}: suggested multiplier for this component's Outlook weight"),
    ]


def _signal(row: Row) -> str:
    s = row[0]
    if s.kind is Kind.SCORE:
        if s.outlook is None:
            return "Fair value only"
        return f"{s.band} {s.outlook:.0f}" if s.band else f"Outlook {s.outlook:.0f}"
    if s.action is not None:
        return f"{s.action.capitalize()} ({s.conviction})" if s.conviction else s.action.capitalize()
    return (s.stance or DASH).capitalize()


def recent_cells(row: Row) -> list[Cell]:
    s, outcomes = row
    cells: list[Cell] = [
        (s.day.isoformat(), f"{s.symbol} call recorded for {s.day.isoformat()}"),
        (s.symbol, s.symbol),
        (KIND_LABELS[s.kind], KIND_LABELS[s.kind]),
        (_signal(row), f"{s.symbol}: {_signal(row)}"),
        (f"{s.price:,.2f} {s.currency}", f"{s.symbol} price when recorded, vs {s.benchmark}"),
    ]
    for h in HORIZONS:
        o = outcomes.get(h)
        name = f"{s.symbol} {h} days"
        if o is None:
            cells.append(("pending", f"{name}: not enough trading days yet"))
        elif o.status is OutcomeStatus.UNABLE or o.excess is None:
            cells.append(("n/a", f"{name}: not scored — {o.reason or 'unknown'}"))
        else:
            c = classify(s.direction, o.excess)
            mark = _MARKS.get(c, "") if c is not None else ""
            verdict = "" if c is None else f" ({c.value})"
            cells.append((f"{o.excess * 100:+.1f}%{mark}",
                          f"{name}: {o.excess * 100:+.1f}% vs {s.benchmark}{verdict}"))
    return cells
