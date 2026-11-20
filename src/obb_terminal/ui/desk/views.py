"""The dashboards' pure logic: from a snapshot to what each page shows. No Qt here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import IntEnum

from obb_terminal.domain.dividends import ttm_yield
from obb_terminal.domain.models import AssetType
from obb_terminal.domain.targets import LABEL, Indicator, format_level
from obb_terminal.journal.models import HORIZONS, KIND_LABELS, MIN_SAMPLE, Kind, OutcomeStatus, Row
from obb_terminal.journal.scoring import due_after_days
from obb_terminal.journal.stats import bucket
from obb_terminal.ui.desk.data import DeskEntry, DeskSnapshot
from obb_terminal.ui.fmt import DASH, fmt_num, fmt_range
from obb_terminal.ui.track_record.view import Cell, recent_cells

OUTLOOK_MOVE = 5.0  # points; a smaller move is noise
RECENT_DAYS = 7
CALL_KINDS = (Kind.AI, Kind.DEBATE)
FIRST_HORIZON = HORIZONS[0]
INCOMPLETE = "Yahoo's data is incomplete right now. Trying again shortly."
MONTHS = 13  # this month and the same month last year, so a yearly payer is never missed


class Urgency(IntEnum):
    THESIS = 0
    TARGET = 1
    CALL = 2
    OUTLOOK = 3
    FAILED = 4


class BriefAction(IntEnum):
    VALUATION = 0
    CALL_BOOK = 1
    WATCHLIST = 2


ACTION_TEXT = {BriefAction.VALUATION: "Open valuation", BriefAction.CALL_BOOK: "Open call book",
               BriefAction.WATCHLIST: "Open watchlist"}


@dataclass(frozen=True, slots=True)
class BriefItem:
    urgency: Urgency
    symbol: str
    what: str
    why: str
    last: str
    action: BriefAction


def _last(entry: DeskEntry | None) -> str:
    if entry is None or entry.inputs is None:
        return DASH
    return f"{entry.inputs.price:,.2f} {entry.inputs.currency.value}"


def _target_item(e: DeskEntry) -> BriefItem | None:
    row = e.row
    if row is None:
        return None
    hits = [c for c in row.cells if c.hit and c.target is not None and c.target.enabled]
    if not hits:
        return None
    if len(hits) > 1:
        what = f"{len(hits)} buy targets hit"
        why = ", ".join(LABEL[c.indicator] for c in hits)
    else:
        c = hits[0]
        what = f"{LABEL[c.indicator]} target hit"
        why = f"Your level is {format_level(c.indicator, c.target.level)}"
    return BriefItem(Urgency.TARGET, e.symbol, what, why, _last(e), BriefAction.VALUATION)


def _entry_items(e: DeskEntry) -> list[BriefItem]:
    if e.error is not None:
        return [BriefItem(Urgency.FAILED, e.symbol, "Data unavailable", e.error, DASH, BriefAction.WATCHLIST)]
    items: list[BriefItem] = []
    if e.row is not None and e.row.thesis_broken:
        why = f"Your break level is {fmt_num(e.row.thesis_level)}"
        items.append(BriefItem(Urgency.THESIS, e.symbol, "Thesis break crossed", why, _last(e), BriefAction.VALUATION))
    target = _target_item(e)
    if target is not None:
        items.append(target)
    return items


def _outlook_items(snapshot: DeskSnapshot) -> list[BriefItem]:
    latest: dict[str, list] = {}
    for s, _ in snapshot.journal_rows:  # oldest first
        if s.kind is Kind.SCORE and s.outlook is not None:
            latest.setdefault(s.symbol, []).append(s)
    items = []
    for symbol, snaps in latest.items():
        if len(snaps) < 2 or snaps[-1].day < snapshot.today - timedelta(days=RECENT_DAYS):
            continue
        before, after = snaps[-2], snaps[-1]
        change = after.outlook - before.outlook
        if abs(change) < OUTLOOK_MOVE:
            continue
        verb = "rose" if change > 0 else "fell"
        items.append(BriefItem(
            Urgency.OUTLOOK, symbol, f"Outlook score {verb} {abs(change):.0f} points",
            f"From {before.outlook:.0f} to {after.outlook:.0f} since {before.day.isoformat()}",
            _last(snapshot.entry(symbol)), BriefAction.VALUATION,
        ))
    return items


def _call_item(snapshot: DeskSnapshot, row: Row) -> BriefItem | None:
    s, outcomes = row
    label = KIND_LABELS[s.kind] if s.kind is Kind.DEBATE else "AI"
    cutoff = snapshot.now - timedelta(days=RECENT_DAYS)
    last = _last(snapshot.entry(s.symbol))
    done = [o for o in outcomes.values() if o.status is OutcomeStatus.DONE and o.excess is not None]
    fresh = [o for o in done if o.evaluated_at >= cutoff]
    if fresh:
        o = max(fresh, key=lambda x: x.horizon)
        why = f"{o.excess * 100:+.1f}% against {s.benchmark}"
        return BriefItem(Urgency.CALL, s.symbol, f"{label} call scored at {o.horizon} days", why, last,
                         BriefAction.CALL_BOOK)
    for h in HORIZONS:
        if h not in outcomes and s.day <= snapshot.today - timedelta(days=due_after_days(h)):
            return BriefItem(Urgency.CALL, s.symbol, f"{label} call ready to score at {h} days",
                             f"Called on {s.day.isoformat()}", last, BriefAction.CALL_BOOK)
    return None


def brief_items(snapshot: DeskSnapshot) -> list[BriefItem]:
    items: list[BriefItem] = []
    for e in snapshot.entries:
        items.extend(_entry_items(e))
    watched = {e.symbol for e in snapshot.entries}
    items.extend(i for i in _outlook_items(snapshot) if i.symbol in watched)
    for row in snapshot.journal_rows:  # the journal also holds symbols no longer watched
        if row[0].kind in CALL_KINDS and row[0].symbol in watched:
            item = _call_item(snapshot, row)
            if item is not None:
                items.append(item)
    return sorted(items, key=lambda i: (i.urgency, i.symbol))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def brief_title(items: list[BriefItem]) -> str:
    decisions = sum(1 for i in items if i.urgency <= Urgency.TARGET)
    reading = sum(1 for i in items if i.urgency in (Urgency.CALL, Urgency.OUTLOOK))
    parts = []
    if decisions:
        parts.append(f"{_plural(decisions, 'decision')} waiting")
    if reading:
        parts.append(f"{_plural(reading, 'thing')} to read")
    failed = sum(1 for i in items if i.urgency is Urgency.FAILED)
    if failed:
        parts.append(f"{_plural(failed, 'symbol')} without data")
    return ", ".join(parts) or "Nothing needs a look today"


# --- Value map --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValuePoint:
    symbol: str
    price: float
    currency: str
    fair_value: float
    upside: float  # fraction
    outlook: float  # 0-100


def value_points(snapshot: DeskSnapshot) -> tuple[list[ValuePoint], list[tuple[str, str]]]:
    """The stocks on the map, best upside first, and the others with the reason they are off it."""
    points: list[ValuePoint] = []
    left_out: list[tuple[str, str]] = []
    for e in snapshot.entries:
        if e.loading:
            continue
        row, s = e.row, e.score
        if e.error is not None:
            left_out.append((e.symbol, e.error))
        elif e.incomplete and e.inputs.asset_type is not AssetType.ETF:
            left_out.append((e.symbol, INCOMPLETE))
        elif row is None or row.margin is None or row.fair_value is None or row.price is None:
            left_out.append((e.symbol, (row.fair_reason if row else None) or "No fair value"))
        elif s is None or s.value is None:
            left_out.append((e.symbol, (s.reason if s else None) or "No Outlook score"))
        else:
            points.append(ValuePoint(e.symbol, row.price, row.currency.value, row.fair_value, row.margin, s.value))
    return sorted(points, key=lambda p: (-p.upside, p.symbol)), left_out


def fair_value_gaps(snapshot: DeskSnapshot) -> list[tuple[str, float]]:
    """(symbol, upside to fair value) for every loaded stock with a fair value; unlike the map,
    a stock needs no Outlook score here."""
    gaps: list[tuple[str, float]] = []
    for e in snapshot.entries:
        row = e.row
        if e.loading or e.error is not None or (e.incomplete and e.inputs.asset_type is not AssetType.ETF):
            continue
        if row is not None and row.margin is not None and row.fair_value is not None and row.price is not None:
            gaps.append((e.symbol, row.margin))
    return gaps


# --- Entry ladder -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LadderRow:
    """Levels as fractions away from the current price."""

    symbol: str
    price: float
    currency: str
    buy: float | None
    zone: tuple[float, float] | None
    thesis: float | None
    fair: float | None

    @property
    def has_target(self) -> bool:
        return self.buy is not None


def _away(level: float | None, price: float) -> float | None:
    return None if level is None else round(level / price - 1, 6)


def _ladder_row(e: DeskEntry) -> LadderRow | None:
    row = e.row
    if e.inputs is None or row is None or not row.price:
        return None
    call = None if e.report is None else e.report.report.call
    zone = None
    if call is not None and call.entry_low is not None and call.entry_high is not None:
        zone = (_away(call.entry_low, row.price), _away(call.entry_high, row.price))
    thesis = row.thesis_level if row.thesis_level is not None else (call.thesis_break if call else None)
    return LadderRow(
        e.symbol, row.price, row.currency.value, _away(row.b_all, row.price), zone,
        _away(thesis, row.price), None if e.incomplete else _away(row.fair_value, row.price),
    )


def ladder_rows(snapshot: DeskSnapshot) -> list[LadderRow]:
    """Closest to your buy level first; stocks without a target last."""
    rows = [r for r in (_ladder_row(e) for e in snapshot.entries) if r is not None]
    return sorted(rows, key=lambda r: (r.buy is None, -(r.buy or 0.0), r.symbol))


# --- Call book --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallRow:
    symbol: str
    day: date
    kind: str
    action: str
    zone: str
    thesis_break: str
    target: str
    thesis: str  # Alive, Broken or a dash
    thesis_tip: str
    horizons: tuple[Cell, ...]


def _thesis(snapshot: DeskSnapshot, row: Row) -> tuple[str, str]:
    s = row[0]
    entry = snapshot.entry(s.symbol)
    reason = ""
    if entry is not None and entry.report is not None and entry.report.report.call is not None:
        if entry.report.created_at.date() == s.recorded_at.date():
            reason = entry.report.report.call.invalidation
    suffix = f" The call said it breaks if: {reason}" if reason else ""
    if s.thesis_break is None or entry is None or entry.inputs is None:
        return DASH, (reason and f"The call said it breaks if: {reason}") or "No current price or no break level"
    if entry.inputs.price <= s.thesis_break:
        return "Broken", f"The price is at or below the break level.{suffix}"
    return "Alive", f"The price is above the break level.{suffix}"


def call_rows(snapshot: DeskSnapshot) -> list[CallRow]:
    calls = [r for r in snapshot.journal_rows if r[0].kind in CALL_KINDS]
    calls.sort(key=lambda r: (r[0].day, r[0].id or 0), reverse=True)
    out = []
    for row in calls:
        s = row[0]
        cells = recent_cells(row)
        thesis, tip = _thesis(snapshot, row)
        out.append(CallRow(
            s.symbol, s.day, KIND_LABELS[s.kind], cells[3][0], fmt_range(s.entry_low, s.entry_high),
            fmt_num(s.thesis_break), fmt_num(s.target_price), thesis, tip, tuple(cells[5:]),
        ))
    return out


def call_progress(snapshot: DeskSnapshot) -> list[tuple[str, int, int]]:
    """(label, scored at the first horizon, needed before rates are shown) per kind of call."""
    return [(KIND_LABELS[k], bucket(snapshot.journal_rows, k, FIRST_HORIZON).done, MIN_SAMPLE) for k in CALL_KINDS]


# --- Income desk ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncomeRow:
    symbol: str
    currency: str
    asset_type: AssetType
    paid: tuple[float | None, ...]  # per share, one per income_months()
    ttm_yield: float | None
    target: float | None
    target_hit: bool


def _month_start(day: date, back: int) -> date:
    index = day.year * 12 + day.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def income_months(today: date) -> tuple[date, ...]:
    """From this month last year to this month, oldest first."""
    return tuple(_month_start(today, back) for back in range(MONTHS - 1, -1, -1))


def _income_row(e: DeskEntry, months: tuple[date, ...], today: date) -> IncomeRow | None:
    if e.inputs is None:
        return None
    totals: dict[date, float] = {}
    for d in e.inputs.dividends:
        key = d.ex_date.replace(day=1)
        if key in months:
            totals[key] = totals.get(key, 0.0) + d.amount
    cell = None if e.row is None else next(
        (c for c in e.row.cells if c.indicator is Indicator.DIV_YIELD), None
    )
    target = cell.target if cell is not None and cell.target is not None and cell.target.enabled else None
    return IncomeRow(
        e.symbol, e.inputs.currency.value, e.inputs.asset_type,
        tuple(totals.get(m) for m in months),
        ttm_yield(e.inputs.dividends, e.inputs.price, today) or None,
        None if target is None else target.level,
        cell is not None and target is not None and cell.hit,
    )


def income_rows(snapshot: DeskSnapshot) -> list[IncomeRow]:
    """Highest yield first; stocks that paid nothing last."""
    months = income_months(snapshot.today)
    rows = [r for r in (_income_row(e, months, snapshot.today) for e in snapshot.entries) if r is not None]
    return sorted(rows, key=lambda r: (r.ttm_yield is None, -(r.ttm_yield or 0.0), r.symbol))


def next_ex_dates(snapshot: DeskSnapshot, today: date) -> list[tuple[str, date]]:
    """The soonest month each stock went ex-dividend a year ago: an estimate, not a declared date."""
    months = income_months(today)
    this_month = months[-1]
    out: list[tuple[str, date]] = []
    for row in income_rows(snapshot):
        paid = {m: amount for m, amount in zip(months, row.paid, strict=True) if amount is not None}
        paid_months = {m.month for m in paid}
        for ahead in range(MONTHS):
            candidate = _month_start(today, -ahead)
            if candidate == this_month and this_month in paid:
                continue  # already went ex-dividend this month
            if candidate.month in paid_months:
                out.append((row.symbol, candidate))
                break
    return sorted(out, key=lambda x: (x[1], x[0]))
