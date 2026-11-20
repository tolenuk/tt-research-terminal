"""AI report and context models. All report text is untrusted plain text."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from obb_terminal.domain.models import (
    AnalystConsensus,
    AssetType,
    Assumptions,
    Currency,
    EtfData,
    Kpi,
    NewsItem,
    Score,
    Valuation,
)


class Stance(StrEnum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class Horizon(StrEnum):
    NEXT_EARNINGS = "next earnings"
    MID = "6-12 months"
    LONG = "long term"


HORIZON_LABELS: dict[Horizon, str] = {
    Horizon.NEXT_EARNINGS: "Next earnings",
    Horizon.MID: "6–12 months",
    Horizon.LONG: "Long term",
}


class Action(StrEnum):
    BUY = "buy"
    ACCUMULATE = "accumulate"
    HOLD = "hold"
    REDUCE = "reduce"
    AVOID = "avoid"


class Conviction(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


ACTION_LABELS: dict[Action, str] = {a: a.value.capitalize() for a in Action}


@dataclass(frozen=True, slots=True)
class TradeCall:
    """The AI's call in numbers. Prices are in the trading currency; `notes` are the app's
    own remarks from sanitising (never model text)."""

    action: Action
    conviction: Conviction
    entry_low: float | None
    entry_high: float | None
    thesis_break: float | None
    target_price: float | None
    invalidation: str
    watch_conditions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Point:
    point: str
    evidence: str


@dataclass(frozen=True, slots=True)
class WatchItem:
    item: str
    why: str
    horizon: Horizon


@dataclass(frozen=True, slots=True)
class Source:
    title: str
    url: str


class Role(StrEnum):
    BULL = "bull"
    BEAR = "bear"
    RISK = "risk"


ROLE_LABELS: dict[Role, str] = {Role.BULL: "Bull", Role.BEAR: "Bear", Role.RISK: "Risk"}


class Verdict(StrEnum):
    FAVOURS_BULL = "favours_bull"
    FAVOURS_BEAR = "favours_bear"
    UNRESOLVED = "unresolved"


VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.FAVOURS_BULL: "favours the bull case",
    Verdict.FAVOURS_BEAR: "favours the bear case",
    Verdict.UNRESOLVED: "unresolved",
}


@dataclass(frozen=True, slots=True)
class RoleCase:
    role: Role
    thesis: str
    points: tuple[Point, ...]
    conviction: Conviction
    key_level: float | None  # bull/bear: what it is worth; risk: the price in a bad case
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class Disagreement:
    topic: str
    bull: str
    bear: str
    verdict: Verdict
    why: str


@dataclass(frozen=True, slots=True)
class Debate:
    cases: tuple[RoleCase, ...]  # Role order; a failed role is absent and listed in `missing`
    disagreements: tuple[Disagreement, ...]
    consensus: tuple[str, ...]
    missing: tuple[Role, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalystReport:
    summary: str
    stance: Stance
    pros: tuple[Point, ...]
    cons: tuple[Point, ...]
    future_watch: tuple[WatchItem, ...]
    sources: tuple[Source, ...]
    call: TradeCall | None = None
    debate: Debate | None = None


@dataclass(frozen=True, slots=True)
class AnalystContext:
    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency
    price: float
    as_of: date
    sector: str | None
    industry: str | None
    assumptions: Assumptions
    valuation: Valuation
    score: Score
    kpis: tuple[Kpi, ...]
    analyst: AnalystConsensus | None
    headlines: tuple[NewsItem, ...]  # <= 10, untrusted
    etf: EtfData | None = None  # ETF look-through (holdings, sectors, roll-up); None otherwise


class ReportKind(StrEnum):
    SINGLE = "single"
    DEBATE = "debate"


@dataclass(frozen=True, slots=True)
class CachedReport:
    """The latest report for a symbol, with what produced it."""

    symbol: str
    report: AnalystReport
    created_at: datetime  # timezone-aware UTC
    model: str
    effort: str
    assumptions: Assumptions
    cost_usd: float | None
    duration_ms: int | None
    kind: ReportKind = ReportKind.SINGLE
    currency: str | None = None  # trading currency of the call's prices; None in v1 files
