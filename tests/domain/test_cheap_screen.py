from obb_terminal.domain.cheap_screen import ScreenRules, ScreenStock, company_root, screen


def stock(ticker, ev_ebit=5.0, *, pl=8.0, margin=0.2, liquidity=1_000_000.0, name=None):
    return ScreenStock(
        ticker=ticker,
        name=name or ticker[:4],
        price=10.0,
        pl=pl,
        pvp=1.0,
        ev_ebit=ev_ebit,
        ebit_margin=margin,
        roic=0.1,
        liquidity=liquidity,
    )


def tickers(result):
    return [p.stock.ticker for p in result.picks]


def test_ranks_by_ev_ebit_cheapest_first():
    result = screen([stock("AAAA3", 9.0), stock("BBBB3", 3.0), stock("CCCC3", 6.0)], frozenset())
    assert tickers(result) == ["BBBB3", "CCCC3", "AAAA3"]
    assert [p.rank for p in result.picks] == [1, 2, 3]


def test_drops_illiquid_unprofitable_and_meaningless_multiples():
    stocks = [
        stock("GOOD3"),
        stock("THIN3", liquidity=199_999.0),
        stock("LOSS3", pl=-4.0),
        stock("ZERO3", pl=0.0),
        stock("NEGM3", margin=-0.05),
        stock("NEGE3", ev_ebit=-2.0),
        stock("NOEV3", ev_ebit=0.0),
    ]
    result = screen(stocks, frozenset())
    assert tickers(result) == ["GOOD3"]
    assert result.universe == 7
    assert result.eligible == 1


def test_excludes_financials():
    result = screen([stock("BBAS3", 2.0), stock("VALE3", 4.0)], frozenset({"BBAS3"}))
    assert tickers(result) == ["VALE3"]
    assert result.financials == 1


def test_keeps_only_the_most_liquid_class_per_company():
    stocks = [stock("PETR3", 3.0, liquidity=500_000.0), stock("PETR4", 3.1, liquidity=2_000_000.0)]
    assert tickers(screen(stocks, frozenset())) == ["PETR4"]


def test_liquidity_floor_and_size_are_configurable():
    stocks = [
        stock(f"T{c}AA3", float(i + 1), liquidity=1e6 * (i + 1)) for i, c in enumerate("ABCDE")
    ]
    result = screen(stocks, frozenset(), ScreenRules(min_liquidity=2.5e6, size=2))
    assert tickers(result) == ["TCAA3", "TDAA3"]
    assert result.eligible == 3


def test_score_is_percentile_of_cheapness_within_the_eligible_set():
    stocks = [stock(f"T{c}AA3", float(i + 1)) for i, c in enumerate("ABCDE")]
    result = screen(stocks, frozenset(), ScreenRules(size=3))
    assert [p.score for p in result.picks] == [100, 75, 50]  # out of 5 eligible, not 3 shown


def test_single_eligible_stock_scores_100_and_empty_is_empty():
    assert [p.score for p in screen([stock("AAAA3")], frozenset()).picks] == [100]
    assert screen([], frozenset()).picks == ()


def test_company_root():
    assert company_root("SANB11") == "SANB"
    assert company_root("PETR4") == "PETR"


def test_drops_negative_equity():
    negative = ScreenStock(
        ticker="DOTZ3",
        name="DOTZ",
        price=1.0,
        pl=3.5,
        pvp=-0.4,
        ev_ebit=1.5,
        ebit_margin=0.1,
        roic=0.1,
        liquidity=1e6,
    )
    assert tickers(screen([negative, stock("VALE3")], frozenset())) == ["VALE3"]


# --- markets ranked by the source (Yahoo: UK, US) ------------------------------------------

from datetime import UTC, datetime  # noqa: E402

from obb_terminal.domain.cheap_screen import (  # noqa: E402
    MARKET_RULES,
    Market,
    MarketSnapshot,
    screen_snapshot,
)


def listed(symbol, company, liquidity=5e6):
    return ScreenStock(
        ticker=symbol,
        name=company,
        price=10.0,
        pl=8.0,
        pvp=1.2,
        ev_ebit=None,
        ebit_margin=None,
        roic=None,
        liquidity=liquidity,
        company=company,
    )


def presorted(*stocks):
    return MarketSnapshot(
        tuple(stocks), frozenset(), datetime(2026, 11, 13, tzinfo=UTC), presorted=True
    )


def test_presorted_markets_keep_the_source_order():
    snap = presorted(listed("JET2.L", "Jet2"), listed("HBR.L", "Harbour"), listed("BP.L", "BP"))
    result = screen_snapshot(snap, MARKET_RULES[Market.UK])
    assert tickers(result) == ["JET2.L", "HBR.L", "BP.L"]
    assert [p.score for p in result.picks] == [100, 50, 0]


def test_presorted_markets_apply_the_liquidity_floor_and_one_class_per_company():
    snap = presorted(
        listed("BP-A.L", "BP p.l.c.", liquidity=9e5),
        listed("THIN.L", "Thin", liquidity=1e3),
        listed("BP.L", "BP p.l.c.", liquidity=2e8),
        listed("CNA.L", "Centrica"),
    )
    result = screen_snapshot(snap, MARKET_RULES[Market.UK])
    assert tickers(result) == ["BP.L", "CNA.L"]  # BP's most traded line, at its own place
    assert result.eligible == 2


def test_b3_snapshots_still_rank_by_ev_ebit():
    snap = MarketSnapshot(
        (stock("AAAA3", 9.0), stock("BBBB3", 3.0)), frozenset(), datetime(2026, 11, 13, tzinfo=UTC)
    )
    assert tickers(screen_snapshot(snap, MARKET_RULES[Market.B3])) == ["BBBB3", "AAAA3"]


def test_market_rules_have_local_liquidity_floors():
    assert MARKET_RULES[Market.B3].min_liquidity == 200_000
    assert MARKET_RULES[Market.UK].min_liquidity == 250_000
    assert MARKET_RULES[Market.US].min_liquidity == 1_000_000
