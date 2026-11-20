import pytest

from obb_terminal.data.yahoo_funds import (
    NO_FUND_DATA,
    NoFundData,
    YahooFunds,
    empty_fund_profile,
    invert_multiple,
    read_funds,
    to_fund_profile,
)
from obb_terminal.domain.etf import etf_kind
from obb_terminal.domain.models import EtfKind
from tests.data.fund_samples import RAW


def test_spy_maps_and_un_inverts_multiples():
    p = to_fund_profile("SPY", RAW["SPY"])
    assert (p.category, p.family) == ("Large Blend", "State Street Investment Management")
    assert p.expense_ratio == pytest.approx(0.000945)
    assert p.category_expense_ratio == pytest.approx(0.0072177)
    assert p.turnover == pytest.approx(0.03)
    assert p.pe == pytest.approx(1 / 0.0416) and p.pb == pytest.approx(1 / 0.197)
    assert p.ps == pytest.approx(1 / 0.29058) and p.pcf == pytest.approx(1 / 0.05486)
    assert [h.symbol for h in p.holdings][:3] == ["AAPL", "NVDA", "MSFT"]
    assert len(p.holdings) == 10 and p.top_weight == pytest.approx(0.3725, abs=1e-3)
    assert p.sectors[0] == ("technology", pytest.approx(0.3823))
    assert p.stock_position == pytest.approx(0.9994) and p.other_position == 0.0
    assert p.gaps == ()
    assert etf_kind(p) is EtfKind.EQUITY


def test_uk_listing_without_category():
    p = to_fund_profile("VUSA.L", RAW["VUSA.L"])
    assert p.category is None
    assert p.pe == pytest.approx(1 / 0.04261)
    assert etf_kind(p) is EtfKind.EQUITY


def test_bond_and_gold_funds():
    agg = to_fund_profile("AGG", RAW["AGG"])
    assert etf_kind(agg) is EtfKind.BOND and agg.pe is None
    assert dict(agg.bond_ratings)["aa"] == pytest.approx(0.7415)
    assert "bb" not in dict(agg.bond_ratings)  # zero weights dropped
    gld = to_fund_profile("GLD", RAW["GLD"])
    assert gld.holdings == () and etf_kind(gld) is EtfKind.OTHER


def test_invert_multiple_edges():
    assert invert_multiple(None) is None
    assert invert_multiple(0.0) is None
    assert invert_multiple(-0.1) is None
    assert invert_multiple(0.004) is None  # 250x: bad data
    assert invert_multiple(0.05) == pytest.approx(20.0)


def test_sectors_dropped_when_weights_do_not_sum_to_one():
    raw = dict(RAW["SPY"], sectors={"technology": 0.3, "energy": 0.2})
    assert to_fund_profile("SPY", raw).sectors == ()


def test_malformed_rows_are_skipped_not_raised():
    raw = {"holdings": [["", "x", 0.1], ["A", None, "lots"], ["B", None, 0.2], "junk"],
           "equity": {"Price/Earnings": ["n/a"]}, "asset_classes": "junk"}
    p = to_fund_profile("X", raw)
    assert [(h.symbol, h.name) for h in p.holdings] == [("B", "B")]
    assert p.pe is None and p.stock_position is None and p.other_position is None


def test_empty_profile_carries_the_gap():
    p = empty_fund_profile("BOVA11.SA")
    assert p.gaps == (NO_FUND_DATA,) and p.holdings == ()


def test_seam_maps_no_fund_data():
    class Boom:
        @property
        def funds_data(self):
            raise RuntimeError("BOVA11.SA: No Fund data found.")

    with pytest.raises(NoFundData):
        read_funds(Boom(), "BOVA11.SA")


def test_seam_lets_other_errors_through():
    class Refused:
        @property
        def funds_data(self):
            raise RuntimeError("HTTP Error 401: Invalid Crumb")

    with pytest.raises(RuntimeError):
        read_funds(Refused(), "SPY")


def test_yahoo_funds_uses_the_injected_fn():
    assert YahooFunds(lambda s: {"symbol": s}).fetch("SPY") == {"symbol": "SPY"}


def test_an_empty_payload_is_no_fund_data():
    """yfinance hides parse failures and returns None everywhere: that is not a real profile."""
    raw = {"overview": {}, "operations": {}, "holdings": [], "sectors": {}, "asset_classes": {},
           "equity": {}, "bond_ratings": {}}
    p = to_fund_profile("SPY", raw)
    assert p.gaps == (NO_FUND_DATA,) and p.holdings == ()
