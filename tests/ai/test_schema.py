import copy

import pytest

from obb_terminal.ai.errors import AnalystBadOutput, AnalystError, ClaudeNotFound
from obb_terminal.ai.models import Horizon, Stance
from obb_terminal.ai.schema import REPORT_SCHEMA, is_safe_url, parse_report, report_to_dict
from obb_terminal.data.market import DataError
from tests.ai.samples import STRUCTURED


def test_schema_matches_appendix_b():
    assert REPORT_SCHEMA["required"] == ["summary", "stance", "pros", "cons", "future_watch", "sources", "call"]
    horizon = REPORT_SCHEMA["properties"]["future_watch"]["items"]["properties"]["horizon"]
    assert horizon["enum"] == ["next earnings", "6-12 months", "long term"]
    assert REPORT_SCHEMA["properties"]["sources"]["maxItems"] == 12


def test_parse_valid_report_drops_unsafe_sources():
    report = parse_report(STRUCTURED)
    assert report.stance is Stance.NEUTRAL
    assert len(report.pros) == 2 and report.cons[1].point == "China demand"
    assert [w.horizon for w in report.future_watch] == [Horizon.NEXT_EARNINGS, Horizon.MID, Horizon.LONG]
    assert [s.url for s in report.sources] == ["https://www.apple.com/newsroom/q3-2026"]


def test_parse_truncates_lists_and_strings_and_accepts_en_dash():
    data = copy.deepcopy(STRUCTURED)
    data["pros"] = [{"point": "p" * 5000, "evidence": "e"}] * 9
    data["future_watch"][1]["horizon"] = "6–12 months"
    report = parse_report(data)
    assert len(report.pros) == 6
    assert len(report.pros[0].point) == 2000
    assert report.future_watch[1].horizon is Horizon.MID


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("summary"),
        lambda d: d.update(stance="very bullish"),
        lambda d: d.update(pros="lots"),
        lambda d: d.update(cons=[{"point": 1, "evidence": "x"}]),
        lambda d: d["future_watch"][0].update(horizon="soon"),
        lambda d: d.update(sources=["https://x.com"]),
    ],
)
def test_parse_rejects_bad_shapes(mutate):
    data = copy.deepcopy(STRUCTURED)
    mutate(data)
    with pytest.raises(AnalystBadOutput):
        parse_report(data)


def test_parse_rejects_non_dict():
    with pytest.raises(AnalystBadOutput):
        parse_report(["not", "a", "dict"])


def test_round_trip():
    report = parse_report(STRUCTURED)
    assert parse_report(report_to_dict(report)) == report


@pytest.mark.parametrize(
    ("url", "ok"),
    [("https://a.com/x", True), ("http://a.com", True), ("javascript:alert(1)", False),
     ("file:///etc/passwd", False), ("https://", False), ("ftp://a.com", False), ("http://[::1", False)],
)
def test_is_safe_url(url, ok):
    assert is_safe_url(url) is ok


def test_errors_are_data_errors_with_fixed_messages():
    assert issubclass(AnalystError, DataError)
    assert ClaudeNotFound().user_message == "Claude Code not found — install it and run `claude` once to log in"
    assert AnalystBadOutput().user_message == "Analysis returned an unexpected format"
    assert AnalystBadOutput("custom").user_message == "custom"
