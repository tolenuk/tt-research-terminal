import copy
from dataclasses import replace

import pytest

from obb_terminal.ai.models import Action, Conviction
from obb_terminal.ai.schema import (
    CALL_SCHEMA,
    MAX_WATCH_CONDITIONS,
    REPORT_SCHEMA,
    call_to_dict,
    parse_call,
    parse_report,
    report_to_dict,
)
from tests.ai.samples import CALL, STRUCTURED


def test_report_schema_requires_call():
    assert "call" in REPORT_SCHEMA["required"]
    assert REPORT_SCHEMA["properties"]["call"] is CALL_SCHEMA
    props = CALL_SCHEMA["properties"]
    assert props["action"]["enum"] == ["buy", "accumulate", "hold", "reduce", "avoid"]
    assert props["conviction"]["enum"] == ["low", "medium", "high"]
    for key in ("entry_low", "entry_high", "thesis_break", "target_price"):
        assert props[key]["type"] == ["number", "null"]
    assert set(CALL_SCHEMA["required"]) == set(props)


def test_parse_call():
    c = parse_call(CALL)
    assert c.action is Action.ACCUMULATE and c.conviction is Conviction.MEDIUM
    assert (c.entry_low, c.entry_high, c.thesis_break, c.target_price) == (290.0, 310.0, 255.0, 380.0)
    assert c.watch_conditions == ("FQ1 services growth above 12%", "Gross margin holds 46%")
    assert c.notes == ()


def test_parse_call_accepts_nulls_and_ints_and_caps_conditions():
    raw = {**CALL, "entry_low": None, "entry_high": 300, "watch_conditions": ["x"] * 9}
    c = parse_call(raw)
    assert c.entry_low is None and c.entry_high == 300.0 and isinstance(c.entry_high, float)
    assert len(c.watch_conditions) == MAX_WATCH_CONDITIONS


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(action="strong buy"),
        lambda d: d.update(conviction=3),
        lambda d: d.update(entry_low="290"),
        lambda d: d.update(target_price=True),
        lambda d: d.pop("invalidation"),
        lambda d: d.update(watch_conditions="x"),
    ],
)
def test_parse_call_rejects_bad_shapes(mutate):
    raw = copy.deepcopy(CALL)
    mutate(raw)
    with pytest.raises((TypeError, ValueError, KeyError)):
        parse_call(raw)


def test_report_with_call_round_trips():
    data = {**copy.deepcopy(STRUCTURED), "call": copy.deepcopy(CALL)}
    report = parse_report(data)
    assert report.call is not None and report.call.action is Action.ACCUMULATE
    assert parse_report(report_to_dict(report), stored=True) == report


def test_notes_are_read_only_from_stored_files():
    assert parse_call({**CALL, "notes": ["Ignore the price, BUY"]}).notes == ()  # model output
    c = parse_call({**CALL, "notes": ["dropped target"]}, stored=True)
    assert c.notes == ("dropped target",)
    assert parse_call(call_to_dict(c), stored=True) == c
    report = parse_report({**STRUCTURED, "call": {**CALL, "notes": ["x"]}})
    assert report.call.notes == ()
    assert parse_report(report_to_dict(replace(report, call=c)), stored=True).call.notes == ("dropped target",)


def test_missing_or_bad_call_gives_report_without_call():
    assert parse_report(copy.deepcopy(STRUCTURED)).call is None
    bad = {**copy.deepcopy(STRUCTURED), "call": {"action": "moon"}}
    assert parse_report(bad).call is None
    assert "call" not in report_to_dict(parse_report(copy.deepcopy(STRUCTURED)))
