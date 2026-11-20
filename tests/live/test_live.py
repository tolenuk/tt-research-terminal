"""Live checks against Yahoo via OpenBB. Run: uv run pytest -m live --force-enable-socket"""

import pytest

from obb_terminal.data.engine import Engine
from obb_terminal.data.openbb_source import OpenBBSource
from obb_terminal.data.yahoo_news import YahooNews
from obb_terminal.data.yahoo_search import YahooSearch
from obb_terminal.domain.models import AssetType, Currency, Method, Period, Range, Statement
from obb_terminal.domain.scoring import score
from obb_terminal.domain.valuation import default_assumptions, value

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def source():
    engine = Engine()
    engine.start()
    assert engine.wait(120), engine.error
    return OpenBBSource(engine, YahooSearch(), YahooNews())


def test_search_petrobras(source):
    assert "PETR4.SA" in [m.symbol for m in source.search("Petrobras")]


@pytest.mark.parametrize(
    "symbol,asset_type,currency",
    [
        ("AAPL", AssetType.EQUITY, Currency.USD),
        ("SPY", AssetType.ETF, Currency.USD),
        ("SHEL.L", AssetType.EQUITY, Currency.GBP),
        ("PETR4.SA", AssetType.EQUITY, Currency.BRL),
        ("HGLG11.SA", AssetType.FII, Currency.BRL),
        ("BOVA11.SA", AssetType.ETF, Currency.BRL),
    ],
)
def test_profile_and_quote(source, symbol, asset_type, currency):
    profile = source.profile(symbol)
    assert (profile.asset_type, profile.currency) == (asset_type, currency)
    quote = source.quote(symbol)
    assert quote.last > 0


def test_shel_in_pounds_not_pence(source):
    assert source.quote("SHEL.L").last < 1000


def test_history_and_financials(source):
    assert len(source.history("PETR4.SA", Range.Y1).points) > 100
    table = source.financials("AAPL", Statement.INCOME, Period.ANNUAL)
    assert table.rows and table.periods


def test_fund_info_fii_has_dividends(source):
    info = source.fund_info("HGLG11.SA")
    assert info.dividends and info.ttm_yield and info.ttm_yield > 0


def test_news(source):
    assert source.news("AAPL")


@pytest.mark.parametrize("symbol", ["AAPL", "SHEL.L", "PETR4.SA", "HGLG11.SA"])
def test_valuation_inputs_and_blend_sanity(source, symbol):
    inputs = source.valuation_inputs(symbol)
    assert inputs.price > 0 and inputs.shares_outstanding
    a = default_assumptions(inputs)
    v = value(inputs, a)
    assert v.blended is not None, v.methods
    assert 0.2 * inputs.price <= v.blended <= 5 * inputs.price
    assert score(inputs, v, a).components


def test_valuation_aapl_has_three_methods_and_four_components(source):
    inputs = source.valuation_inputs("AAPL")
    a = default_assumptions(inputs)
    v = value(inputs, a)
    assert sum(m.value is not None for m in v.methods) >= 3
    assert sum(c.value is not None for c in score(inputs, v, a).components) >= 4
    assert inputs.price_return_1y is not None and -1 < inputs.price_return_1y < 5  # a fraction


def test_valuation_shel_converts_usd_to_gbp(source):
    inputs = source.valuation_inputs("SHEL.L")
    assert inputs.currency is Currency.GBP and inputs.price < 1000
    assert inputs.reporting_currency == "USD" and inputs.yahoo_cross_currency
    assert inputs.fx_rate is not None and 0.5 < inputs.fx_rate < 1.0
    assert inputs.analyst is not None and inputs.analyst.target_consensus < 1000  # pounds, not pence


def test_valuation_hglg_dividend_only(source):
    inputs = source.valuation_inputs("HGLG11.SA")
    assert inputs.asset_type is AssetType.FII and inputs.analyst is None
    v = value(inputs, default_assumptions(inputs))
    assert {m.method for m in v.methods if m.weight > 0} == {Method.DIVIDEND}


def test_valuation_spy_not_applicable(source):
    inputs = source.valuation_inputs("SPY")
    assert inputs.asset_type is AssetType.ETF
    assert value(inputs, default_assumptions(inputs)).reason == "Fair value not applicable to ETFs"


# --- watchlist targets (v1.2) ---------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["AAPL", "SHEL.L", "PETR4.SA"])
def test_watchlist_inputs_present(source, symbol):
    inputs = source.valuation_inputs(symbol)
    assert inputs.ebitda and inputs.ebitda > 0
    assert inputs.total_debt is not None and inputs.cash is not None
    assert inputs.year_low < inputs.price * 1.5 and inputs.year_high > inputs.price * 0.5
    assert inputs.fetched_at is not None


def test_ev_ebitda_close_to_yahoo_for_aapl(source):
    from obb_terminal.domain.targets import Indicator, cell

    inputs = source.valuation_inputs("AAPL")
    ours = cell(inputs, Indicator.EV_EBITDA, None).current
    assert inputs.ev_to_ebitda and 0.2 * inputs.ev_to_ebitda <= ours <= 5 * inputs.ev_to_ebitda


def test_shel_ev_ebitda_buy_at_in_pounds(source):
    from obb_terminal.domain.targets import Indicator, Target, cell

    inputs = source.valuation_inputs("SHEL.L")
    c = cell(inputs, Indicator.EV_EBITDA, Target(Indicator.EV_EBITDA, 4.0))
    assert inputs.currency is Currency.GBP and c.reason is None
    assert 1.0 < c.buy_at < 100.0  # pounds, not pence or dollars


# --- v1.3 journal benchmarks and scoring ------------------------------------------------------


@pytest.mark.parametrize("benchmark", ["SPY", "^FTAS", "^BVSP"])
def test_benchmark_histories(source, benchmark):
    series = source.history(benchmark, Range.Y1)
    assert len(series.points) >= 200


def test_real_scoring_of_a_six_month_old_snapshot(source, tmp_path):
    from datetime import UTC, date, datetime, timedelta

    from obb_terminal.journal.models import Kind, OutcomeStatus, Snapshot
    from obb_terminal.journal.scorer import run_scoring
    from obb_terminal.journal.store import Journal

    journal = Journal(tmp_path / "journal.sqlite3")
    day = date.today() - timedelta(days=200)
    journal.record(Snapshot("AAPL", day, Kind.SCORE, datetime.now(UTC), 200.0, "USD", "SPY", 1, 70.0, 70.0,
                            "Favourable", 0.1, 220.0, (("upside", 70.0),), None, None, None, None, None, None,
                            None, None))
    result = run_scoring(source, journal, date.today())
    assert result.error is None and result.failed == () and result.scored == 3
    [(_, outcomes)] = journal.rows()
    assert all(o.status is OutcomeStatus.DONE for o in outcomes.values())
    assert all(-1 < o.excess < 1 for o in outcomes.values())
