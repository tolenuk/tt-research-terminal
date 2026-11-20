"""Sanitise the AI's trade call against the current price, and turn calls into signals.

Pure functions. A bad level is dropped (with a note) rather than rejecting the whole call.
"""

from __future__ import annotations

import math
from dataclasses import replace

from obb_terminal.ai.models import Action, Conviction, Stance, TradeCall

PLAUSIBLE = (0.2, 5.0)  # a level must lie within [0.2 P, 5 P]
IMPLAUSIBLE = "implausible"
BREAK_NOT_BELOW = "Thesis-break level was not below the entry zone, so it was dropped."
TARGET_NOT_ABOVE = "Target was not above the entry zone, so it was dropped."

_FIELDS = {
    "entry_low": "Entry low",
    "entry_high": "Entry high",
    "thesis_break": "Thesis-break level",
    "target_price": "Target",
}
_DIRECTION = {
    Action.BUY: 1,
    Action.ACCUMULATE: 1,
    Action.HOLD: 0,
    Action.REDUCE: -1,
    Action.AVOID: -1,
}
_STANCE = {Stance.BULLISH: 1, Stance.NEUTRAL: 0, Stance.BEARISH: -1}
_CONVICTION = {Conviction.LOW: 1, Conviction.MEDIUM: 2, Conviction.HIGH: 3}


def _plausible(level: float | None, price: float) -> bool:
    lo, hi = PLAUSIBLE
    return level is not None and math.isfinite(level) and lo * price <= level <= hi * price


def sanitize(call: TradeCall, price: float) -> TradeCall:
    """Drop implausible or inconsistent levels; order the entry zone. Never raises."""
    notes = list(call.notes)
    levels: dict[str, float | None] = {}
    for field, label in _FIELDS.items():
        level = getattr(call, field)
        if level is not None and not _plausible(level, price):
            notes.append(f"{label} {level:g} was {IMPLAUSIBLE} against the price {price:,.2f}, so it was dropped.")
            level = None
        levels[field] = level
    low, high = levels["entry_low"], levels["entry_high"]
    if low is None or high is None:
        low = high = low if high is None else high
    elif low > high:
        low, high = high, low
    brk, target = levels["thesis_break"], levels["target_price"]
    if brk is not None and brk >= (low if low is not None else price):
        notes.append(BREAK_NOT_BELOW)
        brk = None
    if target is not None and high is not None and target <= high:
        notes.append(TARGET_NOT_ABOVE)
        target = None
    return replace(
        call, entry_low=low, entry_high=high, thesis_break=brk, target_price=target, notes=tuple(notes)
    )


def direction(call: TradeCall | None, stance: Stance) -> int:
    """+1 bullish, -1 bearish, 0 neutral. Reports without a call fall back to the stance."""
    return _STANCE[stance] if call is None else _DIRECTION[call.action]


def strength(call: TradeCall | None, stance: Stance) -> int:
    """Direction x conviction (1-3); a report without a call counts as medium conviction."""
    conviction = Conviction.MEDIUM if call is None else call.conviction
    return direction(call, stance) * _CONVICTION[conviction]
