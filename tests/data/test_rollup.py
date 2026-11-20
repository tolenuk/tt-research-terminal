from dataclasses import replace

import pytest

from obb_terminal.data.market import DataTimeout, SymbolNotFound
from obb_terminal.data.rollup import (
    NO_DATA,
    NOT_VALUED_FUND,
    ROLLUP_TTL_S,
    RollupMemo,
    value_holding,
)
from obb_terminal.domain.models import Holding, HoldingValue
from obb_terminal.domain.valuation import default_assumptions, value
from tests.fakes import FakeMarketData, install_standard, make_valuation_inputs

AAPL = Holding("AAPL", "Apple Inc", 0.077)


def market():
    md = FakeMarketData()
    install_standard(md)
    return md


def test_values_a_stock_holding_with_its_default_assumptions():
    inputs = make_valuation_inputs("AAPL")
    expected = value(inputs, default_assumptions(inputs)).margin
    hv = value_holding(market(), AAPL)
    assert hv == HoldingValue("AAPL", 0.077, pytest.approx(expected), None)


def test_unknown_holding_is_a_row_with_a_reason():
    md = market()
    md.results["valuation_inputs"] = SymbolNotFound("nope", "XYZ")
    assert value_holding(md, Holding("XYZ", "X", 0.01)) == HoldingValue("XYZ", 0.01, None, NO_DATA)


def test_failed_holding_keeps_the_error_message():
    md = market()
    md.results["valuation_inputs"] = DataTimeout("Timed out")
    assert value_holding(md, AAPL).reason == "Timed out"


def test_fund_holdings_are_not_valued():
    assert value_holding(market(), Holding("SPY", "SPDR", 0.02)).reason == NOT_VALUED_FUND


def test_holding_without_fair_value_says_why():
    md = market()
    bare = replace(make_valuation_inputs("AAPL"), fcf_history=(), analyst=None, net_income=None,
                   book_value_ps=None, dividends=())
    md.results["valuation_inputs"] = lambda s: bare
    hv = value_holding(md, AAPL)
    assert hv.margin is None and hv.reason


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_memo_expires():
    clock = Clock()
    memo = RollupMemo(clock)
    rows = (HoldingValue("AAPL", 0.07, 0.1),)
    assert memo.get("spy") is None
    memo.put("spy", rows)
    assert memo.get("SPY") == rows
    clock.now = ROLLUP_TTL_S + 1
    assert memo.get("SPY") is None


def test_transient_and_partial_holdings_are_marked():
    from obb_terminal.data.market import SourceUnavailable

    md = market()
    md.results["valuation_inputs"] = DataTimeout("Timed out")
    assert value_holding(md, AAPL).transient
    md.results["valuation_inputs"] = SourceUnavailable("Source unavailable (offline?)", "AAPL", "network")
    assert value_holding(md, AAPL).transient
    md.results["valuation_inputs"] = SymbolNotFound("nope", "AAPL")
    assert not value_holding(md, AAPL).transient
    partial = replace(make_valuation_inputs("AAPL"), gaps=("km",))
    md.results["valuation_inputs"] = lambda s: partial
    hv = value_holding(md, AAPL)
    assert hv.transient and hv.margin is not None
