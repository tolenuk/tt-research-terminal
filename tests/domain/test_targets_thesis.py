from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from obb_terminal.domain.models import AssetType, Currency, ValuationInputs
from obb_terminal.domain.targets import (
    AlertState,
    EventKind,
    Indicator,
    SymbolTargets,
    Target,
    TargetsFile,
    ThesisBreak,
    edit,
    evaluate,
    failed_row,
    notification_text,
    set_thesis,
    transition,
)

NOW = datetime(2026, 10, 27, 14, 15, tzinfo=UTC)
SEEN = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)


def inputs(price=100.0) -> ValuationInputs:
    return ValuationInputs(symbol="TEST", name="Test", asset_type=AssetType.EQUITY, currency=Currency.USD,
                           as_of=date(2026, 10, 27), price=price)


def st(thesis=None, *targets) -> SymbolTargets:
    return SymbolTargets("TEST", tuple(targets), thesis=thesis)


def test_evaluate_reports_thesis_level_and_move():
    row = evaluate(inputs(100.0), st(ThesisBreak(80.0)))
    assert row.thesis_level == 80.0
    assert row.thesis_move == pytest.approx(-0.2)
    assert row.thesis_broken is False
    broken = evaluate(inputs(79.0), st(ThesisBreak(80.0)))
    assert broken.thesis_broken is True and broken.thesis_move > 0


def test_evaluate_without_thesis():
    row = evaluate(inputs(), st())
    assert (row.thesis_level, row.thesis_move, row.thesis_broken) == (None, None, False)


def test_thesis_does_not_count_towards_readiness():
    row = evaluate(inputs(50.0), st(ThesisBreak(80.0)))
    assert row.n_valid == 0 and row.b_all is None and not row.all_hit


def test_thesis_fires_once_with_one_event():
    s0 = st(ThesisBreak(80.0))
    s1, events = transition(s0, evaluate(inputs(80.0), s0), NOW)  # exactly at the level fires
    assert s1.thesis == ThesisBreak(80.0, AlertState.FIRED, NOW)
    assert [e.kind for e in events] == [EventKind.THESIS_BROKEN]
    e = events[0]
    assert (e.symbol, e.indicator, e.level, e.price, e.currency) == ("TEST", None, 80.0, 80.0, Currency.USD)
    s2, again = transition(s1, evaluate(inputs(70.0), s1), NOW)
    assert again == () and s2.thesis.state is AlertState.FIRED


def test_thesis_rearms_only_beyond_two_percent():
    fired = st(ThesisBreak(100.0, AlertState.FIRED, NOW))
    same, _ = transition(fired, evaluate(inputs(102.0), fired), NOW)
    assert same.thesis.state is AlertState.FIRED
    rearmed, _ = transition(fired, evaluate(inputs(102.01), fired), NOW)
    assert rearmed.thesis == ThesisBreak(100.0)


def test_failed_row_never_moves_the_thesis():
    s0 = st(ThesisBreak(80.0))
    s1, events = transition(s0, failed_row("TEST", "offline", s0), NOW)
    assert s1 == s0 and events == ()


def test_thesis_and_targets_fire_together_and_survive_transition():
    s0 = st(ThesisBreak(80.0), Target(Indicator.PRICE, 90.0))
    s1, events = transition(s0, evaluate(inputs(75.0), s0), NOW)
    assert {e.kind for e in events} == {EventKind.TARGET_HIT, EventKind.THESIS_BROKEN}
    assert s1.thesis.state is AlertState.FIRED and s1.targets[0].state is AlertState.FIRED


def test_put_keeps_a_symbol_with_only_a_thesis():
    f = TargetsFile().put(st(ThesisBreak(80.0)))
    assert f.get("TEST") is not None
    assert TargetsFile().put(st()).get("TEST") is None


def test_set_thesis_validates_rearms_and_clears_observation():
    old = SymbolTargets("TEST", (Target(Indicator.PE, 20.0),), True, SEEN, ThesisBreak(70.0, AlertState.FIRED, NOW))
    new = set_thesis(old, "TEST", 80.0)
    assert new.thesis == ThesisBreak(80.0)
    assert new.targets == old.targets and new.observed_at is None
    assert set_thesis(new, "TEST", None).thesis is None
    assert set_thesis(None, "NEW", 5.0) == SymbolTargets("NEW", thesis=ThesisBreak(5.0))
    for bad in (0.0, -1.0, float("nan"), "x"):
        with pytest.raises(ValueError):
            set_thesis(old, "TEST", bad)


def test_edit_keeps_the_thesis():
    old = st(ThesisBreak(80.0))
    assert edit(old, "TEST", Indicator.PE, 20.0, True).thesis == ThesisBreak(80.0)


def test_thesis_notification_text():
    s0 = st(ThesisBreak(80.0))
    _, (event,) = transition(s0, evaluate(inputs(79.5), s0), NOW)
    assert notification_text(event) == (
        "TT Research Terminal", "TEST: thesis-break level 80 hit — price 79.50 USD. Re-check the case."
    )


def test_replace_keeps_slots_dataclass_semantics():
    s0 = st(ThesisBreak(80.0))
    assert replace(s0, all_fired=True).thesis == ThesisBreak(80.0)
