from datetime import UTC, datetime

import pytest

from obb_terminal.data.market import SourceUnavailable
from obb_terminal.data.yahoo_screener import PAGE_SIZE, YahooScreener
from obb_terminal.domain.cheap_screen import Market

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def quote(symbol, name="Co plc", currency="GBp", price=100.0, volume=1_000_000, pe=8.0, pb=1.2):
    return {
        "symbol": symbol,
        "longName": name,
        "currency": currency,
        "regularMarketPrice": price,
        "averageDailyVolume3Month": volume,
        "trailingPE": pe,
        "priceToBook": pb,
    }


class FakePost:
    def __init__(self, quotes, total=None, error=None):
        self.quotes, self.total, self.error, self.bodies = quotes, total, error, []

    def __call__(self, body):
        self.bodies.append(body)
        if self.error:
            raise self.error
        start = body["offset"]
        page = self.quotes[start : start + body["size"]]
        return {"total": len(self.quotes) if self.total is None else self.total, "quotes": page}


def fetch(post, market=Market.UK):
    return YahooScreener(post, clock=lambda: NOW).fetch(market)


def test_uk_query_filters_and_sorts_on_yahoos_side():
    post = FakePost([quote("JET2.L")])
    fetch(post)
    body = post.bodies[0]
    assert body["sortField"] == "lastclosetevebit.lasttwelvemonths"
    assert body["sortType"] == "ASC"
    assert body["quoteType"] == "EQUITY"
    text = str(body["query"])
    assert "'LSE'" in text and "'NYQ'" not in text
    assert "Financial Services" not in text  # every other sector is listed
    for rule in ("lastclosetevebit", "ebit.lasttwelvemonths", "netincomeis", "totalequity"):
        assert rule in text


def test_us_query_uses_us_exchanges_without_otc():
    post = FakePost([{**quote("AAPL", currency="USD"), "financialCurrency": "USD"}])
    fetch(post, Market.US)
    text = str(post.bodies[0]["query"])
    assert "'NMS'" in text and "'NYQ'" in text
    assert "'PNK'" not in text and "'LSE'" not in text


def test_quotes_become_presorted_stocks_in_pounds():
    snap = fetch(
        FakePost([quote("JET2.L", "Jet2 plc", price=1510.0, volume=500_000, pe=7.3, pb=2.1)])
    )
    assert snap.presorted
    assert snap.fetched_at == NOW
    jet = snap.stocks[0]
    assert (jet.ticker, jet.name, jet.company) == ("JET2.L", "Jet2 plc", "Jet2 plc")
    assert jet.price == pytest.approx(15.10)  # pence to pounds
    assert jet.liquidity == pytest.approx(500_000 * 15.10)
    assert (jet.pl, jet.pvp) == (7.3, 2.1)
    assert jet.ev_ebit is None


def test_foreign_currency_lines_and_odd_symbols_are_dropped_in_order():
    quotes = [quote("0LUG.L", currency="USD"), quote("THX.L"), quote("bad sym"), quote("CNA.L")]
    assert [s.ticker for s in fetch(FakePost(quotes)).stocks] == ["THX.L", "CNA.L"]


def test_pages_until_the_total_is_read():
    quotes = [quote(f"T{i:04d}.L") for i in range(PAGE_SIZE + 3)]
    post = FakePost(quotes)
    snap = fetch(post)
    assert len(snap.stocks) == PAGE_SIZE + 3
    assert [b["offset"] for b in post.bodies] == [0, PAGE_SIZE]


def test_failure_is_source_unavailable():
    with pytest.raises(SourceUnavailable) as info:
        fetch(FakePost([], error=OSError("down")))
    assert info.value.reason == "network"


def test_an_empty_answer_is_source_unavailable():
    with pytest.raises(SourceUnavailable):
        fetch(FakePost([]))


def test_us_keeps_only_companies_reporting_in_dollars():
    quotes = [
        {**quote("WB", currency="USD"), "financialCurrency": "CNY"},  # a Chinese ADR
        {**quote("PBF", currency="USD"), "financialCurrency": "USD"},
    ]
    assert [s.ticker for s in fetch(FakePost(quotes), Market.US).stocks] == ["PBF"]


def test_uk_keeps_companies_reporting_in_dollars():
    quotes = [{**quote("BP.L"), "financialCurrency": "USD"}]
    assert [s.ticker for s in fetch(FakePost(quotes)).stocks] == ["BP.L"]
