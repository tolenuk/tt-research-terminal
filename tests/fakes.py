"""Test doubles for the UI: a scriptable MarketData and sample domain objects."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from obb_terminal.ai.errors import AnalystCancelled, AnalystError, ClaudeNotFound
from obb_terminal.ai.models import (
    Action,
    AnalystContext,
    AnalystReport,
    CachedReport,
    Conviction,
    Debate,
    Disagreement,
    Horizon,
    Point,
    ReportKind,
    Role,
    RoleCase,
    Source,
    Stance,
    TradeCall,
    Verdict,
    WatchItem,
)
from obb_terminal.data.market import NoData, SymbolNotFound
from obb_terminal.domain.models import (
    AnalystConsensus,
    AssetType,
    Assumptions,
    Currency,
    Dividend,
    EtfData,
    FinancialRow,
    FinancialTable,
    FundInfo,
    FundProfile,
    HoldingValue,
    NewsItem,
    Period,
    PricePoint,
    PriceSeries,
    Profile,
    Quote,
    Range,
    Statement,
    Stats,
    SymbolMatch,
    ValuationInputs,
)

PROFILES: dict[str, tuple[str, AssetType, Currency, str]] = {
    "AAPL": ("Apple Inc.", AssetType.EQUITY, Currency.USD, "NMS"),
    "PETR4.SA": ("Petrobras", AssetType.EQUITY, Currency.BRL, "SAO"),
    "SHEL.L": ("Shell plc", AssetType.EQUITY, Currency.GBP, "LSE"),
    "SPY": ("SPDR S&P 500 ETF", AssetType.ETF, Currency.USD, "PCX"),
    "HGLG11.SA": ("CSHG Logistica FII", AssetType.FII, Currency.BRL, "SAO"),
}
LAST = {"SHEL.L": 27.10, "PETR4.SA": 56.0, "HGLG11.SA": 157.58}


def _currency(symbol: str) -> Currency:
    return PROFILES[symbol][2] if symbol in PROFILES else Currency.USD


def make_profile(symbol: str) -> Profile:
    if symbol not in PROFILES:
        raise SymbolNotFound(f"Unknown symbol: {symbol}", symbol)
    name, asset_type, currency, exchange = PROFILES[symbol]
    return Profile(symbol=symbol, name=name, asset_type=asset_type, currency=currency, exchange=exchange)


def make_quote(symbol: str, last: float | None = None) -> Quote:
    last = LAST.get(symbol, 100.0) if last is None else last
    prev = last - 1.0
    return Quote(
        symbol=symbol, currency=_currency(symbol), last=last, change=1.0, change_pct=1.0 / prev,
        prev_close=prev, open=prev + 0.5, day_high=last + 1, day_low=prev - 1,
        volume=1_000_000, avg_volume=900_000, year_high=last * 1.2, year_low=last * 0.8,
    )


def make_stats(symbol: str) -> Stats:
    return Stats(symbol=symbol, currency=_currency(symbol), market_cap=215941365760,
                 pe_ratio=11.22, dividend_yield=0.0306, beta=1.1)


def make_series(symbol: str, range_: Range = Range.Y1, closes: tuple[float, ...] = (1.0, 2.0, 3.0)) -> PriceSeries:
    points = tuple(
        PricePoint(datetime(2026, 10, i + 1, tzinfo=UTC), c) for i, c in enumerate(closes)
    )
    return PriceSeries(symbol, range_, _currency(symbol), points)


def make_table(symbol: str, statement: Statement = Statement.INCOME, period: Period = Period.ANNUAL) -> FinancialTable:
    return FinancialTable(
        symbol=symbol, statement=statement, period=period, currency="USD",
        periods=(date(2025, 9, 30), date(2024, 9, 28)),
        rows=(
            FinancialRow("total_revenue", "Total revenue", (416161000000.0, 391035000000.0)),
            FinancialRow("net_income", "Net income", (112010000000.0, None)),
        ),
    )


def make_fund(symbol: str) -> FundInfo:
    return FundInfo(
        symbol=symbol, currency=_currency(symbol),
        dividends=(Dividend(date(2026, 9, 1), 1.10), Dividend(date(2026, 8, 1), 1.10),
                   Dividend(date(2026, 7, 1), 1.05)),
        ttm_yield=0.0545, total_assets=None,
    )


def make_news(symbol: str) -> list[NewsItem]:
    return [
        NewsItem(f"{symbol} headline one", "https://finance.yahoo.com/news/one.html",
                 datetime(2026, 10, 9, 20, 0, tzinfo=UTC), None),
        NewsItem(f"{symbol} headline two", "https://finance.yahoo.com/news/two.html",
                 datetime(2026, 10, 8, 13, 10, tzinfo=UTC), None),
    ]


SEARCH_RESULTS = [
    SymbolMatch("PETR4.SA", "Petróleo Brasileiro S.A. - Petrobras", "SAO", AssetType.EQUITY, "EQUITY"),
    SymbolMatch("PBR", "Petróleo Brasileiro S.A. - Petrobras", "NYQ", AssetType.EQUITY, "EQUITY"),
]


class FakeMarketData:
    """Each method looks up `results[method]`: a value, an exception, or a callable(*args)."""

    def __init__(self) -> None:
        self.results: dict[str, Any] = {}
        self.gates: dict[str, threading.Event] = {}
        self.calls: list[tuple] = []
        self._lock = threading.Lock()

    def release_all(self) -> None:
        for gate in self.gates.values():
            gate.set()

    def calls_to(self, method: str) -> list[tuple]:
        with self._lock:
            return [c for c in self.calls if c[0] == method]

    def _do(self, method: str, *args: Any) -> Any:
        with self._lock:
            self.calls.append((method, *args))
        gate = self.gates.get(method)
        if gate is not None:
            gate.wait(5)
        result = self.results.get(method)
        if callable(result) and not isinstance(result, BaseException):
            result = result(*args)
        if isinstance(result, BaseException):
            raise result
        if result is None:
            raise NoData(f"No data available for {args[0] if args else ''}")
        return result

    def search(self, query, limit=8):
        return self._do("search", query, limit)

    def profile(self, symbol):
        return self._do("profile", symbol)

    def quote(self, symbol):
        return self._do("quote", symbol)

    def stats(self, symbol):
        return self._do("stats", symbol)

    def history(self, symbol, range):
        return self._do("history", symbol, range)

    def financials(self, symbol, statement, period):
        return self._do("financials", symbol, statement, period)

    def fund_info(self, symbol):
        return self._do("fund_info", symbol)

    def news(self, symbol, limit=20):
        return self._do("news", symbol, limit)

    def valuation_inputs(self, symbol):
        return self._do("valuation_inputs", symbol)

    def fund_profile(self, symbol):
        return self._do("fund_profile", symbol)


def install_standard(md: FakeMarketData) -> None:
    md.results.update(
        {
            "search": lambda q, limit: list(SEARCH_RESULTS),
            "profile": make_profile,
            "quote": lambda s: make_quote(s),
            "stats": lambda s: make_stats(s),
            "history": lambda s, r: make_series(s, r),
            "financials": lambda s, st, p: make_table(s, st, p),
            "fund_info": lambda s: make_fund(s),
            "news": lambda s, limit: make_news(s),
            "valuation_inputs": lambda s: make_valuation_inputs(s),
            "fund_profile": lambda s: make_fund_profile(s),
        }
    )


# --- valuation (v1.1) ---------------------------------------------------------------------


def _divs(amount: float, months: tuple[tuple[int, int], ...]) -> tuple[Dividend, ...]:
    return tuple(Dividend(date(y, m, 1), amount) for y, m in sorted(months, reverse=True))


_QUARTERS = ((2025, 11), (2026, 2), (2026, 5), (2026, 8))
_MONTHS = tuple((2025, m) for m in (11, 12)) + tuple((2026, m) for m in range(1, 11))

VALUATION_INPUTS: dict[str, ValuationInputs] = {
    "AAPL": ValuationInputs(
        symbol="AAPL", name="Apple Inc.", asset_type=AssetType.EQUITY, currency=Currency.USD,
        as_of=date(2026, 10, 10), price=336.64, ma_200d=290.74, shares_outstanding=14594180000.0,
        reporting_currency="USD", fx_rate=1.0,
        fcf_history=(98767e6, 108807e6, 99584e6, 111443e6), net_income=112010e6, book_value_ps=7.36,
        dividends=_divs(0.27, _QUARTERS), forward_pe=35.11, peg_ratio=2.72, ev_to_ebitda=29.38,
        return_on_equity=1.4875, operating_margin=0.3262, profit_margin=0.2762, debt_to_equity=0.78445,
        current_ratio=1.003, revenue_growth=0.164, earnings_growth=0.287, dividend_yield=0.0032,
        payout_ratio=0.1204, price_return_1y=0.3593,
        analyst=AnalystConsensus(328.09, 340.0, 215.0, 405.0, "buy", 2.20455, 39),
    ),
    "SHEL.L": ValuationInputs(
        symbol="SHEL.L", name="Shell plc", asset_type=AssetType.EQUITY, currency=Currency.GBP,
        as_of=date(2026, 10, 10), price=37.935, ma_200d=32.02, shares_outstanding=5691474708.0,
        reporting_currency="USD", fx_rate=0.7556,
        fcf_history=(23916e6, 35086e6, 31198e6, 45814e6), net_income=17837e6, book_value_ps=24.5614,
        dividends=_divs(0.2892, _QUARTERS), forward_pe=9.01, peg_ratio=1.69, ev_to_ebitda=4.41,
        return_on_equity=0.1434, operating_margin=0.1669, debt_to_equity=0.402, current_ratio=1.435,
        revenue_growth=0.447, dividend_yield=0.0306, payout_ratio=0.3274, price_return_1y=0.4017,
        yahoo_cross_currency=True,
        analyst=AnalystConsensus(40.22854, 39.404, 34.869, 49.677, "buy", 2.10526, 16),
    ),
    "PETR4.SA": ValuationInputs(
        symbol="PETR4.SA", name="Petrobras", asset_type=AssetType.EQUITY, currency=Currency.BRL,
        as_of=date(2026, 10, 10), price=56.0, ma_200d=42.81, shares_outstanding=5446501379.0,
        reporting_currency="BRL", fx_rate=1.0, fcf_history=(16526e6, 23340e6, 31098e6),
        net_income=19634e6, book_value_ps=37.316, dividends=_divs(1.0, _QUARTERS),
        forward_pe=4.12, peg_ratio=5.52, return_on_equity=0.3027, operating_margin=0.4468,
        debt_to_equity=0.7607, revenue_growth=0.423, dividend_yield=0.066, price_return_1y=0.8525,
        analyst=AnalystConsensus(58.31, 58.5, 47.0, 65.0, "strong_buy", 1.46154, 13),
    ),
    "HGLG11.SA": ValuationInputs(
        symbol="HGLG11.SA", name="CSHG Logistica FII", asset_type=AssetType.FII, currency=Currency.BRL,
        as_of=date(2026, 10, 10), price=157.58, ma_200d=153.01, shares_outstanding=33787575.0,
        reporting_currency="BRL", fx_rate=1.0, fcf_history=(327032e3, 276745e3, 231668e3),
        net_income=548153e3, book_value_ps=158.239, dividends=_divs(1.17, _MONTHS),
        return_on_equity=0.1224, operating_margin=0.9433, current_ratio=0.451, revenue_growth=0.879,
        dividend_yield=0.0903, payout_ratio=0.8349, price_return_1y=-0.0155,
    ),
    "SPY": ValuationInputs(
        symbol="SPY", name="SPDR S&P 500 ETF", asset_type=AssetType.ETF, currency=Currency.USD,
        as_of=date(2026, 10, 10), price=777.0, ma_200d=722.93, shares_outstanding=917782016.0,
        dividends=_divs(1.89, _QUARTERS), gaps=("km",),
    ),
}

# v1.2 watchlist fields (REPORTING currency for EBITDA, debt and cash; spike S3 magnitudes).
_WATCH_FIELDS: dict[str, dict[str, Any]] = {
    "AAPL": {"ebitda": 144748e6, "total_debt": 98657e6, "cash": 35934e6,
             "year_high": 360.0, "year_low": 240.0},
    "SHEL.L": {"ebitda": 56664e6, "total_debt": 75643e6, "cash": 29630e6,
               "year_high": 40.62, "year_low": 25.885},
    "PETR4.SA": {"ebitda": 230100e6, "total_debt": 330200e6, "cash": 60400e6,
                 "year_high": 58.0, "year_low": 30.5},
    "HGLG11.SA": {"total_debt": 0.0, "cash": 12.053e6, "year_high": 165.0, "year_low": 150.0},
}
VALUATION_INPUTS = {
    symbol: replace(inputs, **_WATCH_FIELDS.get(symbol, {}))
    for symbol, inputs in VALUATION_INPUTS.items()
}


def make_valuation_inputs(symbol: str) -> ValuationInputs:
    if symbol not in VALUATION_INPUTS:
        raise NoData(f"No data available for {symbol}", symbol)
    return VALUATION_INPUTS[symbol]


CALL = TradeCall(
    action=Action.ACCUMULATE, conviction=Conviction.MEDIUM, entry_low=290.0, entry_high=310.0,
    thesis_break=255.0, target_price=380.0, invalidation="Services growth below 8% <b>",
    watch_conditions=("FQ1 services above 12%",), notes=("Target was trimmed.",),
)


DEBATE = Debate(
    cases=(
        RoleCase(Role.BULL, "Services compounding", (Point("Services", "+14% y/y"),), Conviction.HIGH, 380.0, ()),
        RoleCase(Role.BEAR, "Priced for perfection", (Point("Multiple", "35x forward"),), Conviction.MEDIUM, 240.0, ()),
    ),
    disagreements=(
        Disagreement("Services growth", "Durable", "Slowing", Verdict.FAVOURS_BULL, "Two quarters up"),
        Disagreement("China", "Stabilising", "Losing share", Verdict.UNRESOLVED, "Data conflicts"),
    ),
    consensus=("Strong balance sheet",),
    missing=(Role.RISK,),
)


def make_report(
    stance: Stance = Stance.NEUTRAL, call: TradeCall | None = None, debate: Debate | None = None
) -> AnalystReport:
    return AnalystReport(
        summary="Quality franchise; valuation leaves little margin of safety.",
        stance=stance,
        pros=(Point("Services growth", "Services +14% y/y (2026-07-30)"),
              Point("Buybacks <b>", "$110B authorised 2026-05-01")),
        cons=(Point("Rich valuation", "Forward P/E 35x"), Point("China", "Sales -6% y/y")),
        future_watch=(WatchItem("FQ4 results", "iPhone cycle", Horizon.NEXT_EARNINGS),
                      WatchItem("AI features", "Upgrade driver", Horizon.MID),
                      WatchItem("Regulation", "App Store rulings", Horizon.LONG)),
        sources=(Source("Apple results", "https://www.apple.com/newsroom/q3-2026"),),
        call=call,
        debate=debate,
    )


def make_cached_report(
    symbol: str, *, created_at: datetime | None = None, assumptions: Assumptions | None = None,
    call: TradeCall | None = None, kind: ReportKind = ReportKind.SINGLE, debate: Debate | None = None,
) -> CachedReport:
    return CachedReport(
        symbol=symbol, report=make_report(call=call, debate=debate), kind=kind, currency="USD",
        created_at=created_at or datetime.now(UTC),
        model="claude-opus-5-5", effort="medium",
        assumptions=assumptions or Assumptions(0.09, 0.15, 0.025, 0.03),
        cost_usd=0.44, duration_ms=18234,
    )


class FakeAnalyst:
    """Scriptable Analyst: optional gate, scripted error, stored reports and availability."""

    def __init__(self) -> None:
        self.cached: dict[str, CachedReport] = {}
        self.available: str | None = None
        self.gate: threading.Event | None = None
        self.error: AnalystError | None = None
        self.contexts: list[AnalystContext] = []
        self.kinds: list[ReportKind] = []
        self.entered = threading.Event()
        self.cancelled = False
        self._lock = threading.Lock()

    def availability(self) -> str | None:
        return self.available

    def last(self, symbol: str) -> CachedReport | None:
        return self.cached.get(symbol)

    def analyse(self, ctx: AnalystContext) -> CachedReport:
        return self._answer(ctx, ReportKind.SINGLE)

    def debate(self, ctx: AnalystContext) -> CachedReport:
        return self._answer(ctx, ReportKind.DEBATE)

    def _answer(self, ctx: AnalystContext, kind: ReportKind) -> CachedReport:
        with self._lock:
            self.contexts.append(ctx)
            self.kinds.append(kind)
        self.entered.set()
        if self.cancelled:
            raise AnalystCancelled(symbol=ctx.symbol)
        if self.available is not None:
            raise ClaudeNotFound(symbol=ctx.symbol)
        if self.gate is not None:
            self.gate.wait(5)
        if self.cancelled:
            raise AnalystCancelled(symbol=ctx.symbol)
        if self.error is not None:
            raise self.error
        entry = make_cached_report(ctx.symbol, assumptions=ctx.assumptions, kind=kind,
                                   debate=DEBATE if kind is ReportKind.DEBATE else None)
        self.cached[ctx.symbol] = entry
        return entry

    def cancel(self) -> None:
        self.cancelled = True
        self.release()

    def release(self) -> None:
        if self.gate is not None:
            self.gate.set()


# --- watchlist alerts (v1.2) ----------------------------------------------------------------


class FakeNotifier:
    """Records notifications (thread-safe); `accept` is what notify() returns."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.sent: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def notify(self, title: str, body: str) -> bool:
        with self._lock:
            self.sent.append((title, body))
        return self.accept

    def bodies(self) -> list[str]:
        with self._lock:
            return [body for _, body in self.sent]


class FakeSystemctl:
    """Scriptable systemctl runner: `results[subcommand]` is a CompletedRun or an exception."""

    def __init__(self) -> None:
        from obb_terminal.ai.runner import CompletedRun

        self.calls: list[list[str]] = []
        self.results: dict[str, Any] = {
            "is-enabled": CompletedRun(0, "enabled\n", ""),
            "is-active": CompletedRun(0, "active\n", ""),
        }
        self._ok = CompletedRun(0, "", "")
        self._lock = threading.Lock()

    def __call__(self, argv: list[str]) -> Any:
        with self._lock:
            self.calls.append(list(argv))
        result = self.results.get(argv[2], self._ok)
        if isinstance(result, BaseException):
            raise result
        return result

    def subcommands(self) -> list[str]:
        with self._lock:
            return [" ".join(c[2:]) for c in self.calls]


class FakeInstaller:
    """Stands in for SystemdInstaller: records calls; `error` makes install/uninstall fail."""

    def __init__(self) -> None:
        from obb_terminal.alerts.systemd import TimerStatus

        self.calls: list[str] = []
        self.error: Exception | None = None
        self.gate: threading.Event | None = None
        self.timer = TimerStatus(False, False, False, None)
        self._lock = threading.Lock()

    def _record(self, name: str) -> None:
        with self._lock:
            self.calls.append(name)
        if self.gate is not None:
            self.gate.wait(5)

    def install(self) -> None:
        self._record("install")
        if self.error is not None:
            raise self.error
        self.timer = replace(self.timer, installed=True, enabled=True, active=True)

    def uninstall(self) -> None:
        self._record("uninstall")
        if self.error is not None:
            raise self.error
        self.timer = replace(self.timer, installed=False, enabled=False, active=False)

    def status(self):
        self._record("status")
        return self.timer


# --- ETF look-through ----------------------------------------------------------------------


def make_fund_profile(symbol: str) -> FundProfile:
    """Real captured Yahoo fund data for SPY, VUSA.L, AGG and GLD; empty for anything else."""
    from obb_terminal.data.yahoo_funds import empty_fund_profile, to_fund_profile
    from tests.data.fund_samples import RAW

    return to_fund_profile(symbol, RAW[symbol]) if symbol in RAW else empty_fund_profile(symbol)


def make_etf_inputs(
    symbol: str = "SPY", holding_values: tuple[HoldingValue, ...] | None = None, ten_year: float | None = 0.051
) -> ValuationInputs:
    """The SPY fake with look-through attached (holdings not valued unless given)."""
    base = replace(VALUATION_INPUTS["SPY"], symbol=symbol, gaps=())
    return replace(base, etf=EtfData(make_fund_profile(symbol), ten_year, holding_values))


def make_holding_values(margin: float = 0.10) -> tuple[HoldingValue, ...]:
    """Every SPY top holding valued at `margin` (about 37% of the fund)."""
    return tuple(HoldingValue(h.symbol, h.weight, margin) for h in make_fund_profile("SPY").holdings)
