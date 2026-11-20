import json
import threading
from datetime import UTC, datetime

import pytest

from obb_terminal.ai.claude_code import ClaudeCodeAnalyst
from obb_terminal.ai.errors import AnalystCancelled, AnalystFailed, AnalystTimeout, ClaudeNotFound
from obb_terminal.ai.models import ReportKind, Role, Verdict
from obb_terminal.ai.runner import CompletedRun, RunCancelled, RunTimeout
from obb_terminal.ai.schema import JUDGE_SCHEMA, ROLE_SCHEMA
from obb_terminal.ai.store import ReportStore
from obb_terminal.config import AiConfig
from tests.ai.samples import JUDGE, RESULT, make_context, role_output

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def role_of(prompt):
    for role in Role:
        if f"You are the {role.value.upper()} analyst" in prompt:
            return role.value
    return "judge" if "research director" in prompt else "single"


class DebateRunner:
    """Answers each pass by its role; `fail` maps a role to an exception or a bad returncode."""

    def __init__(self, fail=None, costs=None):
        self.fail = fail or {}
        self.costs = costs or {}
        self.calls = []
        self.lock = threading.Lock()
        self.cancelled = False

    def __call__(self, argv, cwd, timeout_s, input=None):
        role = role_of(input)
        with self.lock:
            self.calls.append((role, argv, timeout_s, input))
        error = self.fail.get(role)
        if isinstance(error, Exception):
            raise error
        if error == "bad":
            return CompletedRun(1, json.dumps({"is_error": True, "result": f"{role} broke"}), "")
        output = JUDGE if role == "judge" else role_output(role)
        result = {**RESULT, "structured_output": output, "total_cost_usd": self.costs.get(role, 0.1)}
        return CompletedRun(0, json.dumps(result), "")

    def cancel_all(self):
        self.cancelled = True


def make(runner, tmp_path, which=lambda name: "/usr/bin/claude"):
    store = ReportStore(tmp_path / "analyses")
    return ClaudeCodeAnalyst(runner, AiConfig(timeout_s=120.0), store, clock=lambda: NOW, which=which), store


def test_debate_runs_three_roles_then_the_judge(tmp_path):
    runner = DebateRunner(costs={"bull": 0.2, "bear": 0.3, "risk": 0.1, "judge": 0.05})
    analyst, store = make(runner, tmp_path)
    entry = analyst.debate(make_context())
    roles = [c[0] for c in runner.calls]
    assert sorted(roles[:3]) == ["bear", "bull", "risk"] and roles[3] == "judge"
    debate = entry.report.debate
    assert [c.role for c in debate.cases] == [Role.BULL, Role.BEAR, Role.RISK]
    assert debate.missing == ()
    assert debate.disagreements[0].verdict is Verdict.FAVOURS_BULL
    assert entry.kind is ReportKind.DEBATE and entry.currency == "USD"
    assert entry.cost_usd == pytest.approx(0.65)
    assert entry.report.call is not None and entry.report.call.entry_high == 310.0
    assert store.get("AAPL") == entry
    assert all(t == 120.0 for _, _, t, _ in runner.calls)


def test_role_passes_use_web_tools_and_the_judge_none(tmp_path):
    runner = DebateRunner()
    analyst, _ = make(runner, tmp_path)
    analyst.debate(make_context())
    by_role = {role: argv for role, argv, *_ in runner.calls}
    for role in ("bull", "bear", "risk"):
        argv = by_role[role]
        assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
        assert json.loads(argv[argv.index("--json-schema") + 1]) == ROLE_SCHEMA
    judge = by_role["judge"]
    assert judge[judge.index("--tools") + 1] == ""
    assert "--allowedTools" not in judge
    assert json.loads(judge[judge.index("--json-schema") + 1]) == JUDGE_SCHEMA


def test_one_failed_role_still_gives_a_report_naming_it(tmp_path):
    runner = DebateRunner(fail={"risk": RunTimeout("slow")})
    analyst, _ = make(runner, tmp_path)
    entry = analyst.debate(make_context())
    assert entry.report.debate.missing == (Role.RISK,)
    assert [c.role for c in entry.report.debate.cases] == [Role.BULL, Role.BEAR]
    judge_prompt = next(prompt for role, _, _, prompt in runner.calls if role == "judge")
    assert "The risk case is missing" in judge_prompt


def test_two_failed_roles_fail_the_debate_without_a_judge(tmp_path):
    runner = DebateRunner(fail={"bull": "bad", "bear": RunTimeout("slow")})
    analyst, store = make(runner, tmp_path)
    with pytest.raises((AnalystFailed, AnalystTimeout)):
        analyst.debate(make_context())
    assert "judge" not in [c[0] for c in runner.calls]
    assert store.get("AAPL") is None


def test_cancelled_role_cancels_the_debate(tmp_path):
    runner = DebateRunner(fail={"bear": RunCancelled()})
    analyst, _ = make(runner, tmp_path)
    with pytest.raises(AnalystCancelled):
        analyst.debate(make_context())
    assert "judge" not in [c[0] for c in runner.calls]


def test_judge_failure_fails_the_debate(tmp_path):
    analyst, _ = make(DebateRunner(fail={"judge": "bad"}), tmp_path)
    with pytest.raises(AnalystFailed, match="judge broke"):
        analyst.debate(make_context())


def test_debate_needs_claude(tmp_path):
    analyst, _ = make(DebateRunner(), tmp_path, which=lambda name: None)
    with pytest.raises(ClaudeNotFound):
        analyst.debate(make_context())
