import copy

import pytest

from obb_terminal.ai.errors import AnalystBadOutput
from obb_terminal.ai.models import Conviction, Debate, Role, Verdict
from obb_terminal.ai.schema import (
    JUDGE_SCHEMA,
    REPORT_SCHEMA,
    ROLE_SCHEMA,
    parse_judge,
    parse_report,
    parse_role,
    report_to_dict,
)
from tests.ai.samples import JUDGE, role_output


def test_role_schema_shape():
    assert set(ROLE_SCHEMA["required"]) == {"thesis", "points", "conviction", "key_level", "sources"}
    assert ROLE_SCHEMA["properties"]["key_level"]["type"] == ["number", "null"]
    assert ROLE_SCHEMA["properties"]["points"]["maxItems"] == 5


def test_judge_schema_extends_the_report_schema():
    assert JUDGE_SCHEMA["required"] == [*REPORT_SCHEMA["required"], "disagreements", "consensus"]
    verdict = JUDGE_SCHEMA["properties"]["disagreements"]["items"]["properties"]["verdict"]
    assert verdict["enum"] == ["favours_bull", "favours_bear", "unresolved"]
    assert REPORT_SCHEMA["required"][-1] == "call"  # the base schema is not mutated


def test_parse_role():
    case = parse_role(role_output("bull"), Role.BULL)
    assert case.role is Role.BULL and case.conviction is Conviction.HIGH
    assert case.key_level == 380.0 and len(case.points) == 2
    assert case.sources[0].url == "https://example.com/bull"


@pytest.mark.parametrize("mutate", [lambda d: d.pop("thesis"), lambda d: d.update(conviction="huge"),
                                    lambda d: d.update(key_level="x"), lambda d: d.update(points="p")])
def test_parse_role_rejects_bad_shapes(mutate):
    raw = role_output("bear")
    mutate(raw)
    with pytest.raises(AnalystBadOutput):
        parse_role(raw, Role.BEAR)


def test_parse_judge():
    report, disagreements, consensus = parse_judge(copy.deepcopy(JUDGE))
    assert report.call is not None and report.debate is None
    assert [d.verdict for d in disagreements] == [Verdict.FAVOURS_BULL, Verdict.UNRESOLVED]
    assert consensus == ("Balance sheet is strong",)


def test_parse_judge_requires_disagreements():
    raw = copy.deepcopy(JUDGE)
    raw["disagreements"] = [{"topic": "x"}]
    with pytest.raises(AnalystBadOutput):
        parse_judge(raw)


def test_report_with_debate_round_trips():
    report, disagreements, consensus = parse_judge(copy.deepcopy(JUDGE))
    cases = (parse_role(role_output("bull"), Role.BULL), parse_role(role_output("bear"), Role.BEAR))
    from dataclasses import replace

    full = replace(report, debate=Debate(cases, disagreements, consensus, (Role.RISK,)))
    assert parse_report(report_to_dict(full), stored=True) == full
