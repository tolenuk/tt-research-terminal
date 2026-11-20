"""The MarketData boundary: every method blocks, is thread-safe, and raises only DataError."""

from __future__ import annotations

from typing import Literal, Protocol

from obb_terminal.domain.models import (
    FinancialTable,
    FundInfo,
    FundProfile,
    NewsItem,
    Period,
    PriceSeries,
    Profile,
    Quote,
    Range,
    Statement,
    Stats,
    SymbolMatch,
    ValuationInputs,
)

Reason = Literal["network", "rate_limited", "source"]


class DataError(Exception):
    def __init__(self, user_message: str, symbol: str | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.symbol = symbol


class SymbolNotFound(DataError):
    pass


class NoData(DataError):
    pass


class DataTimeout(DataError):
    pass


class EngineUnavailable(DataError):
    pass


class UnsupportedCurrency(DataError):
    pass


class SourceUnavailable(DataError):
    def __init__(
        self, user_message: str, symbol: str | None = None, reason: Reason = "source"
    ) -> None:
        super().__init__(user_message, symbol)
        self.reason: Reason = reason


def no_data(symbol: str | None) -> NoData:
    return NoData(f"No data available for {symbol}", symbol)


class MarketData(Protocol):
    def search(self, query: str, limit: int = 8) -> list[SymbolMatch]: ...
    def profile(self, symbol: str) -> Profile: ...
    def quote(self, symbol: str) -> Quote: ...
    def stats(self, symbol: str) -> Stats: ...
    def history(self, symbol: str, range: Range) -> PriceSeries: ...
    def financials(
        self, symbol: str, statement: Statement, period: Period
    ) -> FinancialTable: ...
    def fund_info(self, symbol: str) -> FundInfo: ...
    def news(self, symbol: str, limit: int = 20) -> list[NewsItem]: ...
    def valuation_inputs(self, symbol: str) -> ValuationInputs: ...
    def fund_profile(self, symbol: str) -> FundProfile: ...
