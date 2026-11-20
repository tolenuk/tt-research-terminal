"""Domain models shared by the data layer and the UI.

Ratios and percentages are fractions (0.034 == 3.4%). Prices are in major currency units.
Datetimes are UTC-labelled exchange wall time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, StrEnum


class AssetType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    FII = "fii"


class Currency(StrEnum):
    USD = "USD"
    GBP = "GBP"
    BRL = "BRL"


class Range(Enum):
    D1 = "1D"
    M1 = "1M"
    M6 = "6M"
    Y1 = "1Y"
    Y5 = "5Y"
    ALL = "ALL"


# (interval, lookback days) per chart range; D1 is trimmed to the last session after fetch.
RANGE_SPEC: dict[Range, tuple[str, int]] = {
    Range.D1: ("5m", 5),
    Range.M1: ("1d", 31),
    Range.M6: ("1d", 183),
    Range.Y1: ("1d", 366),
    Range.Y5: ("1W", 1828),
    Range.ALL: ("1M", 36_525),  # a century back: Yahoo returns from the first listed month
}


class Statement(StrEnum):
    INCOME = "income"
    BALANCE = "balance"
    CASH_FLOW = "cash_flow"


class Period(StrEnum):
    ANNUAL = "annual"
    QUARTER = "quarter"


@dataclass(frozen=True, slots=True)
class SymbolMatch:
    symbol: str
    name: str
    exchange: str | None
    asset_type: AssetType | None
    quote_type: str | None


@dataclass(frozen=True, slots=True)
class Profile:
    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    website: str | None = None
    description: str | None = None
    quote_type: str | None = None


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    currency: Currency
    last: float
    change: float | None = None
    change_pct: float | None = None
    prev_close: float | None = None
    open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: int | None = None
    avg_volume: int | None = None
    year_high: float | None = None
    year_low: float | None = None


@dataclass(frozen=True, slots=True)
class Stats:
    symbol: str
    currency: Currency
    market_cap: float | None = None
    pe_ratio: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None


@dataclass(frozen=True, slots=True)
class PricePoint:
    at: datetime
    close: float


@dataclass(frozen=True, slots=True)
class PriceSeries:
    symbol: str
    range: Range
    currency: Currency
    points: tuple[PricePoint, ...]

    def arrays(self) -> tuple[list[float], list[float]]:
        return [p.at.timestamp() for p in self.points], [p.close for p in self.points]


@dataclass(frozen=True, slots=True)
class FinancialRow:
    key: str
    label: str
    values: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class FinancialTable:
    symbol: str
    statement: Statement
    period: Period
    currency: str | None
    periods: tuple[date, ...]  # newest first
    rows: tuple[FinancialRow, ...]


@dataclass(frozen=True, slots=True)
class Dividend:
    ex_date: date
    amount: float


@dataclass(frozen=True, slots=True)
class FundInfo:
    symbol: str
    currency: Currency
    dividends: tuple[Dividend, ...]  # newest first
    ttm_yield: float | None
    expense_ratio: float | None = None
    total_assets: float | None = None


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    name: str
    weight: float  # fraction of the fund


@dataclass(frozen=True, slots=True)
class FundProfile:
    """An ETF's look-through data (Yahoo funds_data). Multiples are already un-inverted."""

    symbol: str
    category: str | None = None
    family: str | None = None
    expense_ratio: float | None = None
    category_expense_ratio: float | None = None
    turnover: float | None = None
    holdings: tuple[Holding, ...] = ()  # largest first, usually the top 10
    sectors: tuple[tuple[str, float], ...] = ()  # (yahoo key, weight), largest first
    stock_position: float | None = None
    bond_position: float | None = None
    cash_position: float | None = None
    other_position: float | None = None
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None
    pcf: float | None = None
    bond_ratings: tuple[tuple[str, float], ...] = ()
    gaps: tuple[str, ...] = ()

    @property
    def top_weight(self) -> float | None:
        return sum(h.weight for h in self.holdings) if self.holdings else None


class EtfKind(StrEnum):
    EQUITY = "equity"
    BOND = "bond"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class HoldingValue:
    """One row of the top-holdings roll-up: a holding's own margin of safety, or why none."""

    symbol: str
    weight: float
    margin: float | None
    reason: str | None = None
    # A failure that may pass (timeout, offline, refused) or partial inputs: the roll-up is shown
    # but not remembered or journaled, so the next load tries again.
    transient: bool = False


@dataclass(frozen=True, slots=True)
class EtfData:
    """ETF look-through attached to ValuationInputs. `holding_values` None = not valued yet."""

    profile: FundProfile
    ten_year: float | None = None  # market 10-year yield as a fraction; None = unknown
    holding_values: tuple[HoldingValue, ...] | None = None


@dataclass(frozen=True, slots=True)
class NewsItem:
    title: str
    url: str
    published: datetime
    source: str | None


# --- valuation (v1.1) ---------------------------------------------------------------------
# Prices are in major units of the TRADING currency unless a field says REPORTING.


@dataclass(frozen=True, slots=True)
class AnalystConsensus:
    target_consensus: float | None
    target_median: float | None
    target_low: float | None
    target_high: float | None
    recommendation: str | None
    recommendation_mean: float | None  # 1 = strong buy … 5 = sell
    number_of_analysts: int | None


@dataclass(frozen=True, slots=True)
class ValuationInputs:
    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency
    as_of: date
    price: float
    ma_200d: float | None = None
    shares_outstanding: float | None = None
    reporting_currency: str | None = None
    fx_rate: float | None = 1.0  # reporting -> trading; 1.0 same currency; None unknown
    fcf_history: tuple[float, ...] = ()  # REPORTING, annual, newest first, <= 4
    net_income: float | None = None  # REPORTING, latest annual
    book_value_ps: float | None = None  # REPORTING, per share
    dividends: tuple[Dividend, ...] = ()  # trading currency, newest first
    forward_pe: float | None = None
    peg_ratio: float | None = None
    ev_to_ebitda: float | None = None
    return_on_equity: float | None = None
    operating_margin: float | None = None
    profit_margin: float | None = None
    debt_to_equity: float | None = None  # a ratio (0.78), not Yahoo's percentage
    current_ratio: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    price_return_1y: float | None = None
    yahoo_cross_currency: bool = False
    analyst: AnalystConsensus | None = None
    gaps: tuple[str, ...] = ()
    # v1.2 watchlist targets
    ebitda: float | None = None  # REPORTING, latest annual
    total_debt: float | None = None  # REPORTING; None = no balance sheet, 0.0 = none reported
    cash: float | None = None  # REPORTING cash and equivalents; same rule as total_debt
    year_high: float | None = None  # trading currency
    year_low: float | None = None
    fetched_at: datetime | None = None  # UTC, when the source fetched (survives the cache)
    # ETF look-through (fund profile, 10-year yield, top-holdings roll-up); None for non-ETFs
    etf: EtfData | None = None


@dataclass(frozen=True, slots=True)
class MethodWeights:
    dcf: float = 0.35
    analyst: float = 0.30
    multiple: float = 0.20
    dividend: float = 0.15


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    upside: float = 0.30
    analyst: float = 0.20
    quality: float = 0.20
    value_growth: float = 0.15
    momentum: float = 0.15


@dataclass(frozen=True, slots=True)
class EtfScoreWeights:
    upside: float = 0.30
    vs_bonds: float = 0.20
    cost: float = 0.15
    diversification: float = 0.15
    income: float = 0.30  # bond ETFs only
    momentum: float = 0.20


GBP_TEN_YEAR_DEFAULT = 0.045  # Yahoo has no gilt yield
MIN_COVERAGE = 0.20


@dataclass(frozen=True, slots=True)
class EtfAssumptions:
    bond_yield: float = GBP_TEN_YEAR_DEFAULT
    min_coverage: float = MIN_COVERAGE
    score_weights: EtfScoreWeights = EtfScoreWeights()


@dataclass(frozen=True, slots=True)
class Assumptions:
    discount_rate: float
    growth_1_5: float
    terminal_growth: float
    dividend_growth: float
    method_weights: MethodWeights = MethodWeights()
    score_weights: ScoreWeights = ScoreWeights()
    etf: EtfAssumptions = EtfAssumptions()


class Method(StrEnum):
    DCF = "dcf"
    ANALYST = "analyst"
    MULTIPLE = "multiple"
    DIVIDEND = "dividend"
    LOOK_THROUGH = "look_through"


METHOD_LABELS: dict[Method, str] = {
    Method.DCF: "DCF",
    Method.ANALYST: "Analyst consensus",
    Method.MULTIPLE: "Earnings multiple (Graham)",
    Method.DIVIDEND: "Dividend (Gordon)",
    Method.LOOK_THROUGH: "Top-holdings roll-up",
}


@dataclass(frozen=True, slots=True)
class MethodResult:
    method: Method
    value: float | None
    reason: str | None
    weight: float  # effective, normalised weight in the blend (0 when left out)


class ValuationLabel(StrEnum):
    UNDERVALUED = "Undervalued"
    FAIR = "Fairly valued"
    OVERVALUED = "Overvalued"


@dataclass(frozen=True, slots=True)
class Valuation:
    applicable: bool
    currency: Currency
    price: float
    methods: tuple[MethodResult, ...]
    blended: float | None
    margin: float | None
    label: ValuationLabel | None
    reason: str | None


class Component(StrEnum):
    UPSIDE = "upside"
    ANALYST = "analyst"
    QUALITY = "quality"
    VALUE_GROWTH = "value_growth"
    MOMENTUM = "momentum"
    VS_BONDS = "vs_bonds"
    COST = "cost"
    DIVERSIFICATION = "diversification"
    INCOME = "income"


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    key: Component
    label: str
    value: float | None
    weight: float  # effective, normalised weight (0 when left out)
    reason: str | None
    parts: tuple[tuple[str, float | None], ...]


class ScoreBand(StrEnum):
    FAVOURABLE = "Favourable"
    MIXED = "Mixed"
    UNFAVOURABLE = "Unfavourable"


@dataclass(frozen=True, slots=True)
class Score:
    value: float | None
    band: ScoreBand | None
    components: tuple[ScoreComponent, ...]
    reason: str | None


class Mark(StrEnum):
    GOOD = "good"
    NEUTRAL = "neutral"
    WEAK = "weak"
    MISSING = "missing"


class KpiFormat(StrEnum):
    MULTIPLE = "multiple"
    PERCENT = "percent"
    RATIO = "ratio"


@dataclass(frozen=True, slots=True)
class Kpi:
    key: str
    label: str
    value: float | None
    fmt: KpiFormat
    mark: Mark
    note: str | None = None
