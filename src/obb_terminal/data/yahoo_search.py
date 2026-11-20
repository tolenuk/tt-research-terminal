"""Symbol search via yfinance (OpenBB's free providers have no name search).

yfinance is imported lazily here and in yahoo_screener (startup must not load it).
"""

from __future__ import annotations

from collections.abc import Callable

from obb_terminal.domain.classify import classify
from obb_terminal.domain.models import SymbolMatch

SearchFn = Callable[[str, int], list[dict]]

_KEEP_TYPES = frozenset({"EQUITY", "ETF", "MUTUALFUND"})
_KEEP_SUFFIXES = (".L", ".SA")


def _yf_search(query: str, max_results: int) -> list[dict]:
    import yfinance as yf

    return list(yf.Search(query, max_results=max_results, news_count=0).quotes)


class YahooSearch:
    def __init__(self, search_fn: SearchFn | None = None) -> None:
        self._search_fn = search_fn or _yf_search

    def search(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        matches: list[SymbolMatch] = []
        for quote in self._search_fn(query, limit * 2):
            symbol = quote.get("symbol") or ""
            quote_type = quote.get("quoteType")
            if not symbol or quote_type not in _KEEP_TYPES:
                continue
            if "." in symbol and not symbol.endswith(_KEEP_SUFFIXES):
                continue
            short, long = quote.get("shortname") or "", quote.get("longname") or ""
            name = (long or short or symbol).strip()
            matches.append(
                SymbolMatch(
                    symbol=symbol,
                    name=name,
                    exchange=quote.get("exchange"),
                    asset_type=classify(symbol, quote_type, f"{short} {long}"),
                    quote_type=quote_type,
                )
            )
            if len(matches) >= limit:
                break
        return matches
