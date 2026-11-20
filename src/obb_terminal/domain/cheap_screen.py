"""'Cheapest stocks' screen: Clube do Valor's published EV/EBIT method, replicated per market.

Their stated rules: drop illiquid names (R$200k/day) and companies with negative EBIT margin
or losses, rank the rest by EV/EBIT (cheapest first), hold the top 20 at equal weight. Not
published, so assumed here: financials are excluded (EV/EBIT means nothing for banks and
insurers), companies with negative equity are dropped, and each company counts once, through
its most liquid share class. The 0-100 score is a percentile of cheapness within the eligible
set (our reading of their widget).

B3 data carries every figure, so the rules run here. UK and US data come from a screener that
has already applied the profitability, equity, EV/EBIT and financials rules and sorted the
list cheapest first without returning the EV/EBIT figures (`presorted`): here only liquidity
and one-class-per-company are applied, keeping the source's order.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

DEFAULT_MIN_LIQUIDITY = 200_000.0  # R$ per day, the floor in their 2020 write-up
DEFAULT_SIZE = 20
ROOT_LEN = 4  # B3 tickers share a four-letter company root: PETR3, PETR4


class Market(Enum):
    B3 = "B3"
    UK = "UK"
    US = "US"


@dataclass(frozen=True, slots=True)
class ScreenStock:
    ticker: str
    name: str
    price: float | None
    pl: float | None  # price / earnings
    pvp: float | None  # price / book
    ev_ebit: float | None
    ebit_margin: float | None  # fraction
    roic: float | None  # fraction
    liquidity: float | None  # average daily traded value, in the market's main currency unit
    company: str = ""  # groups share classes; empty means the B3 four-letter root


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    stocks: tuple[ScreenStock, ...]
    financials: frozenset[str]  # tickers in bank, insurance and other financial sectors
    fetched_at: datetime
    presorted: bool = False  # the source applied the rules and sorted cheapest first


@dataclass(frozen=True, slots=True)
class ScreenRules:
    min_liquidity: float = DEFAULT_MIN_LIQUIDITY
    size: int = DEFAULT_SIZE


@dataclass(frozen=True, slots=True)
class Pick:
    rank: int
    stock: ScreenStock
    score: int  # 100 = cheapest eligible stock, 0 = dearest


@dataclass(frozen=True, slots=True)
class ScreenResult:
    picks: tuple[Pick, ...]
    universe: int  # every ticker the source listed
    financials: int  # of those, dropped as financials
    eligible: int  # passed every rule, one class per company


PUBLISHED_RULES = ScreenRules()
# Clube do Valor only publishes Brazil's floor; the UK and US floors are ours, in £ and $.
MARKET_RULES = {
    Market.B3: PUBLISHED_RULES,
    Market.UK: ScreenRules(min_liquidity=250_000.0),
    Market.US: ScreenRules(min_liquidity=1_000_000.0),
}


def company_root(ticker: str) -> str:
    return ticker[:ROOT_LEN]


def company_key(stock: ScreenStock) -> str:
    return stock.company or company_root(stock.ticker)


def _positive(value: float | None) -> bool:
    return value is not None and value > 0


def _liquid(stock: ScreenStock, rules: ScreenRules) -> bool:
    return stock.liquidity is not None and stock.liquidity >= rules.min_liquidity


def _passes(stock: ScreenStock, financials: frozenset[str], rules: ScreenRules) -> bool:
    return (
        stock.ticker not in financials
        and _liquid(stock, rules)
        and _positive(stock.ebit_margin)
        and _positive(stock.pl)
        and _positive(stock.pvp)  # negative equity
        and _positive(stock.ev_ebit)
    )


def _most_liquid_per_company(stocks: Iterable[ScreenStock]) -> list[ScreenStock]:
    """One stock per company, in the order the companies' chosen lines first appear."""
    listed = list(stocks)
    best: dict[str, ScreenStock] = {}
    for stock in listed:
        key = company_key(stock)
        kept = best.get(key)
        if kept is None or (stock.liquidity or 0.0) > (kept.liquidity or 0.0):
            best[key] = stock
    chosen = {id(s) for s in best.values()}
    return [s for s in listed if id(s) in chosen]


def _score(index: int, count: int) -> int:
    return 100 if count <= 1 else round(100 * (1 - index / (count - 1)))


def _result(
    listed: tuple[ScreenStock, ...],
    ranked: list[ScreenStock],
    financials: frozenset[str],
    rules: ScreenRules,
) -> ScreenResult:
    picks = tuple(
        Pick(rank=i + 1, stock=s, score=_score(i, len(ranked)))
        for i, s in enumerate(ranked[: rules.size])
    )
    return ScreenResult(
        picks=picks,
        universe=len(listed),
        financials=sum(1 for s in listed if s.ticker in financials),
        eligible=len(ranked),
    )


def screen(
    stocks: Iterable[ScreenStock],
    financials: frozenset[str],
    rules: ScreenRules = PUBLISHED_RULES,
) -> ScreenResult:
    """Apply every rule and rank by EV/EBIT (B3, where the data has all the figures)."""
    listed = tuple(stocks)
    passing = [s for s in listed if _passes(s, financials, rules)]
    ranked = sorted(
        _most_liquid_per_company(passing),
        key=lambda s: (s.ev_ebit, -(s.liquidity or 0.0), s.ticker),
    )
    return _result(listed, ranked, financials, rules)


def screen_presorted(stocks: Iterable[ScreenStock], rules: ScreenRules) -> ScreenResult:
    """Keep the source's cheapest-first order; apply only liquidity and one class per company."""
    listed = tuple(stocks)
    ranked = _most_liquid_per_company(s for s in listed if _liquid(s, rules))
    return _result(listed, ranked, frozenset(), rules)


def screen_snapshot(snapshot: MarketSnapshot, rules: ScreenRules) -> ScreenResult:
    if snapshot.presorted:
        return screen_presorted(snapshot.stocks, rules)
    return screen(snapshot.stocks, snapshot.financials, rules)
