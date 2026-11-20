from obb_terminal.domain.models import AssetType
from obb_terminal.domain.valuation import default_assumptions, value
from tests.fakes import FakeMarketData, install_standard, make_valuation_inputs


def test_fake_valuation_inputs_cover_page_cases():
    md = FakeMarketData()
    install_standard(md)
    aapl = md.valuation_inputs("AAPL")
    v = value(aapl, default_assumptions(aapl))
    assert sum(m.value is not None for m in v.methods) >= 3 and v.blended is not None
    assert make_valuation_inputs("SHEL.L").yahoo_cross_currency
    assert make_valuation_inputs("HGLG11.SA").asset_type is AssetType.FII
    spy = make_valuation_inputs("SPY")
    assert value(spy, default_assumptions(spy)).reason == "Fair value not applicable to ETFs"


# --- FakeAnalyst mirrors ClaudeCodeAnalyst cancel and missing-claude behaviour ---------------


def test_fake_analyst_cancel_during_gated_analyse_raises_cancelled():
    import threading

    from obb_terminal.ai.errors import AnalystCancelled
    from tests.ai.samples import make_context
    from tests.fakes import FakeAnalyst

    analyst = FakeAnalyst()
    analyst.gate = threading.Event()
    ctx = make_context()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            analyst.analyse(ctx)
        except BaseException as exc:  # noqa: BLE001 - captured for assertion
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert analyst.entered.wait(5)
    analyst.cancel()
    worker.join(5)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], AnalystCancelled)
    assert errors[0].symbol == ctx.symbol
    assert analyst.last(ctx.symbol) is None


def test_fake_analyst_analyse_after_cancel_raises_cancelled():
    from obb_terminal.ai.errors import AnalystCancelled
    from tests.ai.samples import make_context
    from tests.fakes import FakeAnalyst

    analyst = FakeAnalyst()
    analyst.cancel()
    ctx = make_context()
    try:
        analyst.analyse(ctx)
    except AnalystCancelled as exc:
        assert exc.symbol == ctx.symbol
    else:
        raise AssertionError("expected AnalystCancelled")
    assert analyst.last(ctx.symbol) is None


def test_fake_analyst_missing_claude_raises_not_found_without_report():
    from obb_terminal.ai.errors import ClaudeNotFound
    from tests.ai.samples import make_context
    from tests.fakes import FakeAnalyst

    analyst = FakeAnalyst()
    analyst.available = ClaudeNotFound.default_message
    ctx = make_context()
    try:
        analyst.analyse(ctx)
    except ClaudeNotFound as exc:
        assert exc.symbol == ctx.symbol
    else:
        raise AssertionError("expected ClaudeNotFound")
    assert analyst.last(ctx.symbol) is None
