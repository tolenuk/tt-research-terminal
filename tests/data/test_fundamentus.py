import urllib.error
from datetime import UTC, datetime

import pytest

from obb_terminal.data.fundamentus import (
    FINANCIAL_SECTORS,
    Fundamentus,
    parse_number,
    parse_table,
)
from obb_terminal.data.market import SourceUnavailable
from tests.data import fundamentus_samples as s

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def test_parse_number_reads_brazilian_format():
    assert parse_number("2.710.580.000,00") == 2_710_580_000.0
    assert parse_number("-27,93") == -27.93
    assert parse_number("8,15%") == pytest.approx(0.0815)
    assert parse_number("\n\t 0,59\t") == 0.59
    assert parse_number("") is None
    assert parse_number("n/a") is None


def test_parse_table_maps_columns_by_header():
    stocks = parse_table(s.RESULT)
    assert [x.ticker for x in stocks] == ["PETR4", "CLAN3"]
    petr = stocks[0]
    assert petr.name == "PETROBRAS"
    assert petr.price == 44.70
    assert petr.pl == 4.10
    assert petr.pvp == 1.14
    assert petr.ev_ebit == 3.34
    assert petr.ebit_margin == pytest.approx(0.4451)
    assert petr.roic == pytest.approx(0.2141)
    assert petr.liquidity == 2_710_580_000.0


def test_parse_table_without_the_results_table_raises():
    with pytest.raises(SourceUnavailable):
        parse_table(s.NO_TABLE)


class FakeHttp:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        for key, text in self.pages.items():
            if url.endswith(key):
                return text
        return s.page([])


def test_fetch_combines_results_and_financial_sectors():
    http = FakeHttp({"resultado.php": s.RESULT, "setor=20": s.BANKS})
    snap = Fundamentus(http, clock=lambda: NOW).fetch()
    assert [x.ticker for x in snap.stocks] == ["PETR4", "CLAN3"]
    assert snap.financials == frozenset({"BBAS3", "ITSA4"})
    assert snap.fetched_at == NOW
    assert len(http.urls) == 1 + len(FINANCIAL_SECTORS)


def test_fetch_network_failure_is_source_unavailable():
    http = FakeHttp(error=urllib.error.URLError("down"))
    with pytest.raises(SourceUnavailable) as info:
        Fundamentus(http, clock=lambda: NOW).fetch()
    assert info.value.reason == "network"


def test_a_sector_with_no_companies_has_no_table_and_is_empty():
    http = FakeHttp({"resultado.php": s.RESULT, "setor=20": s.BANKS, "setor=35": s.NO_TABLE})
    snap = Fundamentus(http, clock=lambda: NOW).fetch()
    assert snap.financials == frozenset({"BBAS3", "ITSA4"})


def test_the_main_page_without_its_table_still_fails():
    http = FakeHttp({"resultado.php": s.NO_TABLE})
    with pytest.raises(SourceUnavailable):
        Fundamentus(http, clock=lambda: NOW).fetch()
