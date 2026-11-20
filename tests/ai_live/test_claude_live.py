"""One real Claude Code analysis. Uses plan quota. Run: uv run pytest -m ai"""

import shutil

import pytest

from obb_terminal.ai.claude_code import ClaudeCodeAnalyst
from obb_terminal.ai.context import collect_context
from obb_terminal.ai.runner import SubprocessRunner
from obb_terminal.ai.store import ReportStore
from obb_terminal.config import AiConfig
from obb_terminal.domain.valuation import default_assumptions
from tests.fakes import FakeMarketData, install_standard, make_profile, make_valuation_inputs

pytestmark = pytest.mark.ai


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not on PATH")
def test_real_analysis_of_aapl(tmp_path):
    md = FakeMarketData()
    install_standard(md)
    inputs = make_valuation_inputs("AAPL")
    ctx = collect_context(md, inputs, make_profile("AAPL"), default_assumptions(inputs))
    store = ReportStore(tmp_path)
    analyst = ClaudeCodeAnalyst(SubprocessRunner(), AiConfig(), store)
    entry = analyst.analyse(ctx)
    report = entry.report
    assert report.summary and len(report.pros) >= 2 and len(report.cons) >= 2
    assert len(report.future_watch) >= 2
    assert report.sources, "expected at least one web source"
    assert store.get("AAPL") == entry


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not on PATH")
def test_real_analysis_returns_a_parseable_trade_call(tmp_path):
    md = FakeMarketData()
    install_standard(md)
    inputs = make_valuation_inputs("AAPL")
    ctx = collect_context(md, inputs, make_profile("AAPL"), default_assumptions(inputs))
    entry = ClaudeCodeAnalyst(SubprocessRunner(), AiConfig(), ReportStore(tmp_path)).analyse(ctx)
    call = entry.report.call
    assert call is not None, "the CLI accepted the schema but no call came back"
    assert call.entry_high is None or 0.2 * inputs.price <= call.entry_high <= 5 * inputs.price


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not on PATH")
def test_real_debate(tmp_path):
    from obb_terminal.ai.models import ReportKind

    md = FakeMarketData()
    install_standard(md)
    inputs = make_valuation_inputs("SHEL.L")
    ctx = collect_context(md, inputs, make_profile("SHEL.L"), default_assumptions(inputs))
    entry = ClaudeCodeAnalyst(SubprocessRunner(), AiConfig(), ReportStore(tmp_path)).debate(ctx)
    debate = entry.report.debate
    assert entry.kind is ReportKind.DEBATE and debate is not None
    assert len(debate.cases) >= 2 and debate.disagreements
    assert entry.report.call is not None
