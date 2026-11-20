"""Watchlist buy targets: indicator maths, heat, readiness and the alert state machine.

Pure functions that never fetch. Only `validate_level` and `edit` raise (ValueError on a bad
level). Yields and moves are fractions; prices are in the trading currency.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from obb_terminal.domain.dividends import ttm_total
from obb_terminal.domain.models import Currency, ValuationInputs
from obb_terminal.domain.valuation import bvps, default_assumptions, eps, fcf_per_share, value


class Indicator(StrEnum):
    PRICE = "price"
    PE = "pe"
    EV_EBITDA = "ev_ebitda"
    PB = "pb"
    DIV_YIELD = "div_yield"
    FCF_YIELD = "fcf_yield"


INDICATORS: tuple[Indicator, ...] = tuple(Indicator)
YIELDS = frozenset({Indicator.DIV_YIELD, Indicator.FCF_YIELD})
LABEL: dict[Indicator, str] = {
    Indicator.PRICE: "Price",
    Indicator.PE: "P/E",
    Indicator.EV_EBITDA: "EV/EBITDA",
    Indicator.PB: "P/B",
    Indicator.DIV_YIELD: "Div yield",
    Indicator.FCF_YIELD: "FCF yield",
}
OP: dict[Indicator, str] = {ind: "≥" if ind in YIELDS else "≤" for ind in INDICATORS}


class AlertState(StrEnum):
    ARMED = "armed"
    FIRED = "fired"


class Heat(StrEnum):
    HIT = "hit"
    NEAR = "near"
    FAR = "far"
    NA = "na"


class EventKind(StrEnum):
    TARGET_HIT = "target_hit"
    ALL_HIT = "all_hit"
    THESIS_BROKEN = "thesis_broken"


REARM = 1.02  # a fired target re-arms once the price is more than 2% above its buy-at price
NEAR_BAND = -0.10
HEAT_CAP = 0.30
DEFAULT_DISCOUNT = 0.10  # new targets start 10% cheaper than the current value

NO_EARNINGS = "no earnings"
NEGATIVE_EARNINGS = "negative earnings"
NO_EBITDA = "no EBITDA"
NO_BALANCE = "no balance sheet"
NO_BOOK = "no book value"
NO_DIVIDENDS = "no dividends"
NO_FCF = "no free cash flow"
NO_FX = "currency conversion unavailable"
NO_SHARES = "shares unavailable"
UNREACHABLE = "target unreachable"
NOT_FOR_ETFS = "n/a for ETFs"
NO_LOOK_THROUGH = "no look-through data"


@dataclass(frozen=True, slots=True)
class Target:
    indicator: Indicator
    level: float
    enabled: bool = True
    state: AlertState = AlertState.ARMED
    fired_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThesisBreak:
    """A price below which the investment case is probably wrong. Fires when P <= level."""

    level: float
    state: AlertState = AlertState.ARMED
    fired_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SymbolTargets:
    symbol: str
    targets: tuple[Target, ...] = ()  # INDICATORS order, at most one per indicator
    all_fired: bool = False
    observed_at: datetime | None = None  # fetched_at of the last applied observation
    thesis: ThesisBreak | None = None  # not part of readiness or all-hit

    def target(self, indicator: Indicator) -> Target | None:
        return next((t for t in self.targets if t.indicator is indicator), None)

    def enabled_count(self) -> int:
        return sum(1 for t in self.targets if t.enabled)


@dataclass(frozen=True, slots=True)
class TargetsFile:
    symbols: tuple[SymbolTargets, ...] = ()  # sorted by symbol; no entry without targets

    def get(self, symbol: str) -> SymbolTargets | None:
        return next((s for s in self.symbols if s.symbol == symbol), None)

    def put(self, st: SymbolTargets) -> TargetsFile:
        """A copy with `st` added or replaced; a symbol left with no targets and no thesis
        break is removed."""
        others = [s for s in self.symbols if s.symbol != st.symbol]
        if st.targets or st.thesis is not None:
            others.append(st)
        return TargetsFile(tuple(sorted(others, key=lambda s: s.symbol)))


@dataclass(frozen=True, slots=True)
class Cell:
    indicator: Indicator
    target: Target | None
    current: float | None
    buy_at: float | None
    move: float | None  # (buy_at - price) / price; >= 0 means hit
    hit: bool
    heat: Heat
    intensity: float  # 0..1, |move| capped at 30%
    reason: str | None


@dataclass(frozen=True, slots=True)
class RowEvaluation:
    symbol: str
    currency: Currency | None
    price: float | None
    cells: tuple[Cell, ...]  # INDICATORS order
    b_all: float | None
    m_all: float | None
    all_hit: bool
    n_valid: int
    pos_52w: float | None
    fair_value: float | None
    margin: float | None
    fair_reason: str | None
    error: str | None = None
    thesis_level: float | None = None
    thesis_move: float | None = None  # level / price - 1; >= 0 means broken
    thesis_broken: bool = False


@dataclass(frozen=True, slots=True)
class AlertEvent:
    kind: EventKind
    symbol: str
    indicator: Indicator | None
    level: float | None
    price: float
    buy_at: float
    currency: Currency
    n: int
    at: datetime = field(compare=False)


# --- indicator maths ------------------------------------------------------------------------

_Maths = tuple[float | None, Callable[[float], float] | None, str | None]  # current, B(t), reason


def ebitda_t(i: ValuationInputs) -> float | None:
    """Latest annual EBITDA in the trading currency."""
    if i.ebitda is None or i.fx_rate is None:
        return None
    return i.ebitda * i.fx_rate


def net_debt_t(i: ValuationInputs) -> float | None:
    """(total debt - cash) in the trading currency; negative when cash exceeds debt."""
    if i.total_debt is None or i.fx_rate is None:
        return None
    return (i.total_debt - (i.cash or 0.0)) * i.fx_rate


def _price(i: ValuationInputs) -> _Maths:
    return i.price, lambda t: t, None


def _pe(i: ValuationInputs) -> _Maths:
    if i.net_income is None:
        return None, None, NO_EARNINGS
    if i.net_income <= 0:
        return None, None, NEGATIVE_EARNINGS
    if not i.shares_outstanding or i.shares_outstanding <= 0:
        return None, None, NO_SHARES
    e = eps(i)
    if e is None:
        return None, None, NO_FX
    return i.price / e, lambda t: t * e, None


def _ev_ebitda(i: ValuationInputs) -> _Maths:
    if i.ebitda is None or i.ebitda <= 0:
        return None, None, NO_EBITDA
    if i.total_debt is None:
        return None, None, NO_BALANCE
    shares = i.shares_outstanding
    if not shares or shares <= 0:
        return None, None, NO_SHARES
    ebitda, net_debt = ebitda_t(i), net_debt_t(i)
    if ebitda is None or net_debt is None:
        return None, None, NO_FX
    return (i.price * shares + net_debt) / ebitda, lambda t: (t * ebitda - net_debt) / shares, None


def _pb(i: ValuationInputs) -> _Maths:
    if i.book_value_ps is None or i.book_value_ps <= 0:
        return None, None, NO_BOOK
    b = bvps(i)
    if b is None:
        return None, None, NO_FX
    return i.price / b, lambda t: t * b, None


def _div_yield(i: ValuationInputs) -> _Maths:
    ttm = ttm_total(i.dividends, i.as_of)
    if ttm <= 0:
        return None, None, NO_DIVIDENDS
    return ttm / i.price, lambda t: ttm / t, None


def _fcf_yield(i: ValuationInputs) -> _Maths:
    recent = i.fcf_history[:3]
    if not recent or sum(recent) <= 0:
        return None, None, NO_FCF
    if not i.shares_outstanding or i.shares_outstanding <= 0:
        return None, None, NO_SHARES
    f = fcf_per_share(i)
    if f is None:
        return None, None, NO_FX
    return f / i.price, lambda t: f / t, None


def _look_through(multiple: Callable[[ValuationInputs], float | None]) -> Callable[[ValuationInputs], _Maths]:
    """An ETF's P/E or P/B from its holdings: the price moves the multiple one for one."""

    def maths(i: ValuationInputs) -> _Maths:
        m = multiple(i)
        if m is None or m <= 0:
            return None, None, NO_LOOK_THROUGH
        per_share = i.price / m
        return m, lambda t: t * per_share, None

    return maths


def _etf_fcf_yield(i: ValuationInputs) -> _Maths:
    pcf = i.etf.profile.pcf if i.etf is not None else None
    if pcf is None or pcf <= 0:
        return None, None, NO_LOOK_THROUGH
    f = i.price / pcf  # look-through cash flow per unit
    return f / i.price, lambda t: f / t, None


_ETF_MATHS: dict[Indicator, Callable[[ValuationInputs], _Maths]] = {
    Indicator.PE: _look_through(lambda i: i.etf.profile.pe if i.etf else None),
    Indicator.PB: _look_through(lambda i: i.etf.profile.pb if i.etf else None),
    Indicator.EV_EBITDA: lambda i: (None, None, NOT_FOR_ETFS),
    Indicator.FCF_YIELD: _etf_fcf_yield,
}

_MATHS: dict[Indicator, Callable[[ValuationInputs], _Maths]] = {
    Indicator.PRICE: _price,
    Indicator.PE: _pe,
    Indicator.EV_EBITDA: _ev_ebitda,
    Indicator.PB: _pb,
    Indicator.DIV_YIELD: _div_yield,
    Indicator.FCF_YIELD: _fcf_yield,
}


def heat(move: float | None) -> tuple[Heat, float]:
    if move is None:
        return Heat.NA, 0.0
    intensity = min(abs(move), HEAT_CAP) / HEAT_CAP
    if move >= 0:
        return Heat.HIT, intensity
    return (Heat.NEAR if move >= NEAR_BAND else Heat.FAR), intensity


def cell(i: ValuationInputs, ind: Indicator, target: Target | None) -> Cell:
    maths = _ETF_MATHS.get(ind, _MATHS[ind]) if i.etf is not None else _MATHS[ind]
    current, buy_at_of, reason = maths(i)
    if buy_at_of is None or target is None:
        return Cell(ind, target, current, None, None, False, Heat.NA, 0.0, reason)
    buy_at = buy_at_of(target.level)
    if not math.isfinite(buy_at) or buy_at <= 0:
        return Cell(ind, target, current, None, None, False, Heat.NA, 0.0, UNREACHABLE)
    move = (buy_at - i.price) / i.price
    if not target.enabled:
        return Cell(ind, target, current, buy_at, move, False, Heat.NA, 0.0, None)
    bucket, intensity = heat(move)
    return Cell(ind, target, current, buy_at, move, i.price <= buy_at, bucket, intensity, None)


def position_52w(price: float | None, low: float | None, high: float | None) -> float | None:
    if price is None or low is None or high is None or high <= low:
        return None
    return min(max((price - low) / (high - low), 0.0), 1.0)


def evaluate(i: ValuationInputs, st: SymbolTargets | None) -> RowEvaluation:
    st = st or SymbolTargets(i.symbol)
    cells = tuple(cell(i, ind, st.target(ind)) for ind in INDICATORS)
    valid = [c.buy_at for c in cells if c.buy_at is not None and c.target and c.target.enabled]
    b_all = min(valid) if valid else None
    m_all = None if b_all is None else (b_all - i.price) / i.price
    fair = value(i, default_assumptions(i))
    thesis = st.thesis
    return RowEvaluation(
        symbol=i.symbol,
        currency=i.currency,
        price=i.price,
        cells=cells,
        b_all=b_all,
        m_all=m_all,
        all_hit=b_all is not None and i.price <= b_all,
        n_valid=len(valid),
        pos_52w=position_52w(i.price, i.year_low, i.year_high),
        fair_value=fair.blended,
        margin=fair.margin,
        fair_reason=fair.reason,
        thesis_level=None if thesis is None else thesis.level,
        thesis_move=None if thesis is None else thesis.level / i.price - 1,
        thesis_broken=thesis is not None and i.price <= thesis.level,
    )


def failed_row(symbol: str, reason: str, st: SymbolTargets | None) -> RowEvaluation:
    """A row for a symbol whose data could not be fetched: every cell n/a with `reason`."""
    st = st or SymbolTargets(symbol)
    cells = tuple(
        Cell(ind, st.target(ind), None, None, None, False, Heat.NA, 0.0, reason) for ind in INDICATORS
    )
    return RowEvaluation(
        symbol, None, None, cells, None, None, False, 0, None, None, None, None, error=reason,
        thesis_level=None if st.thesis is None else st.thesis.level,
    )


def readiness_key(row: RowEvaluation) -> tuple[bool, float, str]:
    """Ascending sort key: highest m_all first, rows without a valid target last."""
    return (row.m_all is None, -(row.m_all or 0.0), row.symbol)


# --- alert state machine --------------------------------------------------------------------


def transition(
    st: SymbolTargets, row: RowEvaluation, now: datetime
) -> tuple[SymbolTargets, tuple[AlertEvent, ...]]:
    """Apply one evaluation. Only enabled targets with a valid buy-at price move state."""
    if row.error is not None or row.price is None or row.currency is None:
        return st, ()
    price, currency = row.price, row.currency
    by_indicator = {c.indicator: c for c in row.cells}
    events: list[AlertEvent] = []
    targets: list[Target] = []
    for target in st.targets:
        c = by_indicator.get(target.indicator)
        if not target.enabled or c is None or c.buy_at is None:
            targets.append(target)
        elif target.state is AlertState.ARMED and c.hit:
            targets.append(Target(target.indicator, target.level, True, AlertState.FIRED, now))
            events.append(
                AlertEvent(EventKind.TARGET_HIT, st.symbol, target.indicator, target.level,
                           price, c.buy_at, currency, 1, now)
            )
        elif target.state is AlertState.FIRED and price > c.buy_at * REARM:
            targets.append(Target(target.indicator, target.level, True))
        else:
            targets.append(target)
    all_fired = st.all_fired
    if row.n_valid >= 1 and row.b_all is not None:
        if row.all_hit and not all_fired:
            all_fired = True
            if row.n_valid >= 2:  # with one target the TARGET_HIT already said it
                events.append(
                    AlertEvent(EventKind.ALL_HIT, st.symbol, None, None, price, row.b_all, currency,
                               row.n_valid, now)
                )
        elif all_fired and price > row.b_all * REARM:
            all_fired = False
    thesis, broken = _thesis_transition(st, price, currency, now)
    events.extend(broken)
    return SymbolTargets(st.symbol, tuple(targets), all_fired, st.observed_at, thesis), tuple(events)


def _thesis_transition(
    st: SymbolTargets, price: float, currency: Currency, now: datetime
) -> tuple[ThesisBreak | None, tuple[AlertEvent, ...]]:
    thesis = st.thesis
    if thesis is None:
        return None, ()
    if thesis.state is AlertState.ARMED and price <= thesis.level:
        event = AlertEvent(EventKind.THESIS_BROKEN, st.symbol, None, thesis.level, price, thesis.level,
                           currency, 0, now)
        return ThesisBreak(thesis.level, AlertState.FIRED, now), (event,)
    if thesis.state is AlertState.FIRED and price > thesis.level * REARM:
        return ThesisBreak(thesis.level), ()
    return thesis, ()


def validate_level(ind: Indicator, level: float) -> float:
    try:
        level = float(level)
    except (TypeError, ValueError, OverflowError) as exc:  # OverflowError: a huge int
        raise ValueError("Target must be a number") from exc
    if not math.isfinite(level) or level <= 0:
        raise ValueError("Target must be a positive number")
    if ind in YIELDS and level > 1:
        raise ValueError("Yield target must be at most 100%")
    return level


def edit(
    st: SymbolTargets | None, symbol: str, ind: Indicator, level: float | None, enabled: bool
) -> SymbolTargets:
    """Set (or, with level None, delete) one target. The edited target is re-armed, and the
    symbol's all-targets flag and last observation are reset so the next evaluation counts."""
    ind = Indicator(ind)  # callers may pass the value string (e.g. from a Qt model role)
    st = st or SymbolTargets(symbol)
    others = {t.indicator: t for t in st.targets if t.indicator is not ind}
    if level is not None:
        others[ind] = Target(ind, validate_level(ind, level), enabled)
    ordered = tuple(others[i] for i in INDICATORS if i in others)
    return SymbolTargets(symbol, ordered, False, None, st.thesis)


def cleared(st: SymbolTargets | None, symbol: str) -> SymbolTargets:
    """No buy targets; the thesis break (if any) is kept."""
    return SymbolTargets(symbol, thesis=None if st is None else st.thesis)


def set_thesis(st: SymbolTargets | None, symbol: str, level: float | None) -> SymbolTargets:
    """Set (re-armed) or, with None, remove the thesis break. Clears the last observation so
    the next evaluation counts. Raises ValueError on a bad level."""
    st = st or SymbolTargets(symbol)
    thesis = None if level is None else ThesisBreak(validate_level(Indicator.PRICE, level))
    return SymbolTargets(symbol, st.targets, st.all_fired, None, thesis)


def default_level(ind: Indicator, current: float | None) -> float | None:
    """A starting level 10% cheaper than now: lower multiples and prices, higher yields."""
    if current is None or not math.isfinite(current) or current <= 0:
        return None
    if ind in YIELDS:
        return round(min(current * (1 + DEFAULT_DISCOUNT), 1.0), 4)
    return round(current * (1 - DEFAULT_DISCOUNT), 2)


def _trim(value: float, dp: int = 2) -> str:
    text = f"{value:,.{dp}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_level(ind: Indicator, level: float) -> str:
    if ind in YIELDS:
        return f"{_trim(level * 100)}%"
    return _trim(level)


def notification_text(e: AlertEvent) -> tuple[str, str]:
    ccy = e.currency.value
    if e.kind is EventKind.ALL_HIT:
        body = f"{e.symbol}: all {e.n} buy targets hit — price {e.price:,.2f} {ccy}"
    elif e.kind is EventKind.THESIS_BROKEN:
        assert e.level is not None
        body = (
            f"{e.symbol}: thesis-break level {format_level(Indicator.PRICE, e.level)} hit — "
            f"price {e.price:,.2f} {ccy}. Re-check the case."
        )
    else:
        assert e.indicator is not None and e.level is not None
        body = (
            f"{e.symbol}: {LABEL[e.indicator]} {OP[e.indicator]} {format_level(e.indicator, e.level)}"
            f" hit — price {e.price:,.2f} {ccy} (buy-at {e.buy_at:,.2f})"
        )
    return "TT Research Terminal", body
