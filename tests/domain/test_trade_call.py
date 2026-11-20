import math

import pytest

from obb_terminal.ai.models import Action, Conviction, Stance, TradeCall
from obb_terminal.domain.trade_call import (
    BREAK_NOT_BELOW,
    IMPLAUSIBLE,
    TARGET_NOT_ABOVE,
    direction,
    sanitize,
    strength,
)

P = 100.0


def call(**kw) -> TradeCall:
    base = dict(
        action=Action.BUY, conviction=Conviction.MEDIUM, entry_low=90.0, entry_high=95.0,
        thesis_break=80.0, target_price=130.0, invalidation="Margins fall below 20%",
    )
    return TradeCall(**{**base, **kw})


def test_clean_call_passes_unchanged():
    assert sanitize(call(), P) == call()


@pytest.mark.parametrize("field", ["entry_low", "entry_high", "thesis_break", "target_price"])
@pytest.mark.parametrize("bad", [0.0, -5.0, math.nan, math.inf, 19.99, 500.01])
def test_implausible_prices_are_dropped_with_a_note(field, bad):
    out = sanitize(call(**{field: bad}), P)
    if field.startswith("entry"):  # the zone collapses onto the surviving end
        other = 95.0 if field == "entry_low" else 90.0
        assert (out.entry_low, out.entry_high) == (other, other)
    else:
        assert getattr(out, field) is None
    assert any(IMPLAUSIBLE in n for n in out.notes)


@pytest.mark.parametrize("edge", [20.0, 500.0])
def test_bounds_are_inclusive(edge):
    out = sanitize(call(entry_low=None, entry_high=None, thesis_break=None, target_price=edge), P)
    assert out.target_price == edge


def test_inverted_zone_is_swapped():
    out = sanitize(call(entry_low=95.0, entry_high=90.0), P)
    assert (out.entry_low, out.entry_high) == (90.0, 95.0)
    assert out.notes == ()


def test_single_entry_becomes_a_one_price_zone():
    out = sanitize(call(entry_low=None, entry_high=92.0), P)
    assert (out.entry_low, out.entry_high) == (92.0, 92.0)
    out = sanitize(call(entry_low=91.0, entry_high=None), P)
    assert (out.entry_low, out.entry_high) == (91.0, 91.0)


@pytest.mark.parametrize("brk", [90.0, 93.0])
def test_thesis_break_must_be_below_the_zone(brk):
    out = sanitize(call(thesis_break=brk), P)
    assert out.thesis_break is None
    assert BREAK_NOT_BELOW in out.notes


def test_thesis_break_without_zone_must_be_below_price():
    assert sanitize(call(entry_low=None, entry_high=None, thesis_break=85.0), P).thesis_break == 85.0
    out = sanitize(call(entry_low=None, entry_high=None, thesis_break=100.0), P)
    assert out.thesis_break is None and BREAK_NOT_BELOW in out.notes


@pytest.mark.parametrize("target", [95.0, 94.0])
def test_target_must_be_above_the_zone(target):
    out = sanitize(call(target_price=target), P)
    assert out.target_price is None
    assert TARGET_NOT_ABOVE in out.notes


def test_no_zone_keeps_any_plausible_target():
    out = sanitize(call(entry_low=None, entry_high=None, thesis_break=None, target_price=60.0), P)
    assert out.target_price == 60.0


def test_existing_notes_are_kept():
    out = sanitize(call(notes=("earlier",), target_price=1000.0), P)
    assert out.notes[0] == "earlier" and len(out.notes) == 2


@pytest.mark.parametrize(
    ("action", "want"),
    [(Action.BUY, 1), (Action.ACCUMULATE, 1), (Action.HOLD, 0), (Action.REDUCE, -1), (Action.AVOID, -1)],
)
def test_direction_from_action(action, want):
    assert direction(call(action=action), Stance.BEARISH) == want


@pytest.mark.parametrize(("stance", "want"), [(Stance.BULLISH, 1), (Stance.NEUTRAL, 0), (Stance.BEARISH, -1)])
def test_direction_falls_back_to_stance(stance, want):
    assert direction(None, stance) == want


@pytest.mark.parametrize(
    ("action", "conv", "want"),
    [(Action.BUY, Conviction.HIGH, 3), (Action.AVOID, Conviction.LOW, -1), (Action.HOLD, Conviction.HIGH, 0),
     (Action.REDUCE, Conviction.MEDIUM, -2)],
)
def test_strength_is_direction_times_conviction(action, conv, want):
    assert strength(call(action=action, conviction=conv), Stance.NEUTRAL) == want


def test_strength_without_call_uses_stance_at_medium():
    assert strength(None, Stance.BULLISH) == 2
