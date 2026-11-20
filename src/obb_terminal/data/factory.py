"""Composition root for the data layer."""

from __future__ import annotations

from obb_terminal.data.cache import CachedMarketData
from obb_terminal.data.engine import Engine
from obb_terminal.data.market import MarketData
from obb_terminal.data.openbb_source import OpenBBSource
from obb_terminal.data.yahoo_news import YahooNews
from obb_terminal.data.yahoo_search import YahooSearch


def build_market_data(engine: Engine) -> MarketData:
    return CachedMarketData(OpenBBSource(engine, YahooSearch(), YahooNews()))
