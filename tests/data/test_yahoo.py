from datetime import UTC, datetime

import pytest

from obb_terminal.data.market import NoData
from obb_terminal.data.yahoo_news import RSS_URL, YahooNews, parse_rss
from obb_terminal.data.yahoo_search import YahooSearch
from obb_terminal.domain.models import AssetType
from tests.data import samples as s


def test_search_filters_markets_and_types():
    calls = []

    def fake(query, n):
        calls.append((query, n))
        return s.PETROBRAS_SEARCH

    matches = YahooSearch(fake).search("Petrobras", limit=8)
    assert calls == [("Petrobras", 16)]
    assert [m.symbol for m in matches] == ["PBR", "PETR4.SA", "PETR3.SA"]
    petr4 = matches[1]
    assert petr4.name == "Petróleo Brasileiro S.A. - Petrobras"
    assert petr4.exchange == "SAO"
    assert petr4.asset_type is AssetType.EQUITY


def test_search_respects_limit():
    assert len(YahooSearch(lambda q, n: s.PETROBRAS_SEARCH).search("p", limit=1)) == 1


def test_search_classifies_fii_from_shortname():
    [match] = YahooSearch(lambda q, n: s.HGLG_SEARCH).search("HGLG")
    assert match.asset_type is AssetType.FII


def test_parse_rss_newest_first_and_safe_links_only():
    items = parse_rss(s.AAPL_RSS)
    assert [i.title for i in items] == ["Newer story", "Older story"]
    assert items[0].published == datetime(2026, 10, 9, 20, 0, 21, tzinfo=UTC)
    assert items[0].url == "https://finance.yahoo.com/news/newer.html"


def test_news_fetch_builds_url_and_limits():
    urls = []

    def fake_get(url):
        urls.append(url)
        return s.AAPL_RSS

    items = YahooNews(fake_get).fetch("SHEL.L", limit=1)
    assert urls == [RSS_URL.format(symbol="SHEL.L")]
    assert len(items) == 1


def test_news_fetch_empty_raises_no_data():
    with pytest.raises(NoData):
        YahooNews(lambda url: s.EMPTY_RSS).fetch("AAPL")
