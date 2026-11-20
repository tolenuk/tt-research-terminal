"""Whole-B3 fundamentals from fundamentus.com.br, for the cheapest-stocks screen.

One page (resultado.php) lists every B3 ticker with EV/EBIT, margins, P/L, P/VP and two-month
liquidity; the same page filtered by sector gives the financials to exclude. There is no API:
this reads the HTML table by its headers, so a site redesign shows up as SourceUnavailable,
never as wrong numbers. The page is untrusted input: tickers are checked against B3's shape.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from html.parser import HTMLParser

from obb_terminal.data.market import SourceUnavailable
from obb_terminal.domain.cheap_screen import MarketSnapshot, ScreenStock

log = logging.getLogger(__name__)

BASE_URL = "https://www.fundamentus.com.br/resultado.php"
FINANCIAL_SECTORS = {
    20: "Intermediários Financeiros",  # banks
    31: "Previdência e Seguros",
    35: "Securitizadoras de Recebíveis",
    37: "Serviços Financeiros Diversos",  # exchanges, payments
}
TIMEOUT_S = 20
TICKER_RE = re.compile(r"^[A-Z]{4}\d{1,2}$")
# Our field -> the Fundamentus column header it comes from.
COLUMNS = {
    "price": "Cotação",
    "pl": "P/L",
    "pvp": "P/VP",
    "ev_ebit": "EV/EBIT",
    "ebit_margin": "Mrg Ebit",
    "roic": "ROIC",
    "liquidity": "Liq.2meses",
}
UNAVAILABLE = "Fundamentus is unavailable right now"
CHANGED = "Fundamentus changed its page layout; the screen can't read it"

HttpGet = Callable[[str], str]


def _http_get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return response.read().decode("iso-8859-1", "replace")


def parse_number(text: str) -> float | None:
    """'2.710.580,00' -> 2710580.0; '8,15%' -> 0.0815; anything unreadable -> None."""
    cleaned = text.strip()
    percent = cleaned.endswith("%")
    cleaned = cleaned.rstrip("%").replace(".", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value / 100 if percent else value


class _TableParser(HTMLParser):
    """Collects the #resultado table: header texts, then each row's cell texts and name tip."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headers: list[str] = []
        self.rows: list[tuple[list[str], str]] = []
        self._in_table = False
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self._name = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "table" and attr.get("id") == "resultado":
            self._in_table = True
        elif not self._in_table:
            return
        elif tag == "tr":
            self._row, self._name = [], ""
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "span" and attr.get("title"):
            self._name = attr["title"] or ""

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag == "table":
            self._in_table = False
        elif tag in ("td", "th") and self._cell is not None:
            text = "".join(self._cell).strip()
            if tag == "th":
                self.headers.append(text)
            elif self._row is not None:
                self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row:
            self.rows.append((self._row, self._name))
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_table(html: str) -> list[ScreenStock]:
    parser = _TableParser()
    parser.feed(html)
    if not parser.headers:
        raise SourceUnavailable(CHANGED)
    try:
        index = {field: parser.headers.index(header) for field, header in COLUMNS.items()}
    except ValueError as err:
        raise SourceUnavailable(CHANGED) from err
    stocks: list[ScreenStock] = []
    for cells, name in parser.rows:
        if len(cells) != len(parser.headers) or not TICKER_RE.match(cells[0]):
            continue
        values = {field: parse_number(cells[i]) for field, i in index.items()}
        stocks.append(ScreenStock(ticker=cells[0], name=name.strip() or cells[0], **values))
    return stocks


class Fundamentus:
    def __init__(
        self, http_get: HttpGet | None = None, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._get = http_get or _http_get
        self._clock = clock or (lambda: datetime.now(UTC))

    def fetch(self) -> MarketSnapshot:
        stocks = parse_table(self._page(BASE_URL))
        if not stocks:
            raise SourceUnavailable(CHANGED)
        financials = frozenset(
            ticker for sector in FINANCIAL_SECTORS for ticker in self._sector_tickers(sector)
        )
        return MarketSnapshot(tuple(stocks), financials, self._clock())

    def _sector_tickers(self, sector: int) -> list[str]:
        """A sector with no listed companies is served without the table, so none means empty;
        the main page above has already proved the layout is still readable."""
        html = self._page(f"{BASE_URL}?setor={sector}")
        if 'id="resultado"' not in html:
            return []
        return [stock.ticker for stock in parse_table(html)]

    def _page(self, url: str) -> str:
        try:
            return self._get(url)
        except (urllib.error.URLError, OSError) as err:
            log.info("Fundamentus fetch failed for %s: %s", url, err)
            raise SourceUnavailable(UNAVAILABLE, reason="network") from err
