"""UK and US 'cheapest stocks' lists from Yahoo's equity screener.

Yahoo applies the screen's rules on its side (main exchanges only, no financials, positive
EV/EBIT, EBIT, net income and equity) and sorts cheapest EV/EBIT first, but it does not return
the EV/EBIT figures, so the snapshot is `presorted`. yfinance is imported lazily (startup must
not load it); its session handles Yahoo's cookie and crumb. Quotes are untrusted input.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from obb_terminal.data.market import SourceUnavailable
from obb_terminal.domain.cheap_screen import Market, MarketSnapshot, ScreenStock

log = logging.getLogger(__name__)

SCREENER_URL = "https://query1.finance.yahoo.com/v1/finance/screener"
PAGE_SIZE = 250  # Yahoo's maximum
MAX_PAGES = 12  # the US list is about 1,500 names
SORT_FIELD = "lastclosetevebit.lasttwelvemonths"
SECTORS = (  # every Yahoo sector except Financial Services
    "Basic Materials",
    "Communication Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Healthcare",
    "Industrials",
    "Real Estate",
    "Technology",
    "Utilities",
)
POSITIVE = (
    SORT_FIELD,
    "ebit.lasttwelvemonths",
    "netincomeis.lasttwelvemonths",
    "totalequity.lasttwelvemonths",
)
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
UNAVAILABLE = "Yahoo's screener is unavailable right now"


@dataclass(frozen=True)
class MarketQuery:
    exchanges: tuple[str, ...]
    currencies: dict[str, float]  # quote currency -> factor to the main unit
    reporting: frozenset[str] | None = None  # only companies reporting in these currencies


QUERIES = {
    Market.UK: MarketQuery(("LSE",), {"GBp": 0.01, "GBX": 0.01, "GBP": 1.0}),
    # NYSE, Nasdaq (three tiers) and NYSE American; OTC and Cboe venues left out. Reporting
    # in dollars drops foreign ADRs reporting in their own currency (StoneCo, Kaspi), not those
    # reporting in $ (Weibo, Equinor). Not applied to the UK: many UK firms report in $.
    Market.US: MarketQuery(
        ("NYQ", "NMS", "NGM", "NCM", "ASE"), {"USD": 1.0}, reporting=frozenset({"USD"})
    ),
}

Post = Callable[[dict], dict]


def _yahoo_post(body: dict) -> dict:
    from yfinance.data import YfData

    params = {"formatted": "false", "lang": "en-US", "region": "US"}
    response = YfData().post(SCREENER_URL, data=json.dumps(body), params=params)
    response.raise_for_status()
    return response.json()["finance"]["result"][0]


def _is_in(field: str, values: tuple[str, ...]) -> dict:
    return {
        "operator": "or",
        "operands": [{"operator": "eq", "operands": [field, v]} for v in values],
    }


def build_query(market: Market) -> dict:
    rules = [_is_in("exchange", QUERIES[market].exchanges), _is_in("sector", SECTORS)]
    rules += [{"operator": "gt", "operands": [field, 0]} for field in POSITIVE]
    return {"operator": "and", "operands": rules}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def to_stock(quote: dict, market: MarketQuery) -> ScreenStock | None:
    symbol = quote.get("symbol")
    factor = market.currencies.get(str(quote.get("currency")))
    if not isinstance(symbol, str) or not SYMBOL_RE.match(symbol) or factor is None:
        return None
    if market.reporting is not None and quote.get("financialCurrency") not in market.reporting:
        return None
    raw_price = _number(quote.get("regularMarketPrice"))
    price = None if raw_price is None else raw_price * factor
    volume = _number(quote.get("averageDailyVolume3Month"))
    name = str(quote.get("longName") or quote.get("shortName") or symbol).strip()
    return ScreenStock(
        ticker=symbol,
        name=name,
        price=price,
        pl=_number(quote.get("trailingPE")),
        pvp=_number(quote.get("priceToBook")),
        ev_ebit=None,
        ebit_margin=None,
        roic=None,
        liquidity=None if price is None or volume is None else price * volume,
        company=name,
    )


class YahooScreener:
    def __init__(
        self, post: Post | None = None, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._post = post or _yahoo_post
        self._clock = clock or (lambda: datetime.now(UTC))

    def fetch(self, market: Market) -> MarketSnapshot:
        quotes = self._all_quotes(build_query(market))
        stocks = tuple(s for s in (to_stock(q, QUERIES[market]) for q in quotes) if s is not None)
        if not stocks:
            raise SourceUnavailable(UNAVAILABLE)
        return MarketSnapshot(stocks, frozenset(), self._clock(), presorted=True)

    def _all_quotes(self, query: dict) -> list[dict]:
        quotes: list[dict] = []
        for page in range(MAX_PAGES):
            result = self._page(query, page * PAGE_SIZE)
            batch = result.get("quotes") or []
            quotes.extend(q for q in batch if isinstance(q, dict))
            if not batch or len(quotes) >= int(result.get("total") or 0):
                break
        return quotes

    def _page(self, query: dict, offset: int) -> dict:
        body = {
            "offset": offset,
            "size": PAGE_SIZE,
            "sortField": SORT_FIELD,
            "sortType": "ASC",
            "quoteType": "EQUITY",
            "query": query,
        }
        try:
            return self._post(body)
        except Exception as err:  # requests, HTTP status and JSON errors all mean "try later"
            log.info("Yahoo screener page at %d failed: %s", offset, err)
            raise SourceUnavailable(UNAVAILABLE, reason="network") from err
