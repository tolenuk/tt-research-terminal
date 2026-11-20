"""Pure mappers from OpenBB row dicts (model_dump(mode="json")) to domain models.

Never imports openbb: exceptions are recognised by class name and module so this module
stays testable offline.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime

from obb_terminal.data.market import (
    DataError,
    SourceUnavailable,
    SymbolNotFound,
    no_data,
)
from obb_terminal.domain.classify import classify
from obb_terminal.domain.currency import PENCE, normalise
from obb_terminal.domain.dividends import ttm_yield
from obb_terminal.domain.models import (
    AnalystConsensus,
    Currency,
    Dividend,
    FinancialRow,
    FinancialTable,
    FundInfo,
    Period,
    PricePoint,
    PriceSeries,
    Profile,
    Quote,
    Range,
    Statement,
    Stats,
    ValuationInputs,
)

_META_KEYS = frozenset({"period_ending", "fiscal_period", "fiscal_year", "reported_currency", "symbol"})
_NETWORK_MODULES = frozenset({"requests", "curl_cffi", "aiohttp", "urllib3", "httpx"})
_NO_DATA_MESSAGE = re.compile(r"not found|no data|not an etf|no dividend|invalid symbol", re.I)
# OpenBB's fetchers wrap transport failures in OpenBBError, keeping only the text (S1 / C1).
_RATE_LIMIT_MESSAGE = re.compile(r"YFRateLimitError|Too Many Requests", re.I)
_NETWORK_MESSAGE = re.compile(
    r"ConnectionError|curl: \(\d+\)|Failed to connect|Could not resolve", re.I
)


def _first(rows: list[dict]) -> dict:
    return rows[0] if rows else {}


def _float(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


def _int(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


def _scaled(value: object, factor: float) -> float | None:
    return None if value is None else float(value) * factor  # type: ignore[arg-type]


def _wall_time(value: str) -> datetime:
    """Parse a date or naive exchange-local datetime, labelling the wall time as UTC."""
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def to_quote(rows: list[dict], symbol: str, fallback_last: float | None = None) -> Quote:
    if not rows:
        raise no_data(symbol)
    row = rows[0]
    currency, factor = normalise(row.get("currency"))
    raw_last = row.get("last_price")
    if raw_last is None:
        raw_last = fallback_last
    if raw_last is None:
        raise no_data(symbol)
    last = float(raw_last) * factor
    prev = _scaled(row.get("prev_close"), factor)
    change = last - prev if prev else None
    return Quote(
        symbol=symbol,
        currency=currency,
        last=last,
        change=change,
        change_pct=change / prev if prev and change is not None else None,
        prev_close=prev,
        open=_scaled(row.get("open"), factor),
        day_high=_scaled(row.get("high"), factor),
        day_low=_scaled(row.get("low"), factor),
        volume=_int(row.get("volume")),
        avg_volume=_int(row.get("volume_average")),
        year_high=_scaled(row.get("year_high"), factor),
        year_low=_scaled(row.get("year_low"), factor),
    )


def to_profile(info_rows: list[dict], quote_rows: list[dict], symbol: str) -> Profile:
    if not info_rows and not quote_rows:
        raise SymbolNotFound(f"Unknown symbol: {symbol}", symbol)
    info, quote = _first(info_rows), _first(quote_rows)
    currency, _ = normalise(info.get("currency") or quote.get("currency"))
    name = info.get("name") or quote.get("name") or symbol
    quote_type = info.get("issue_type") or quote.get("asset_type")
    return Profile(
        symbol=symbol,
        name=name,
        asset_type=classify(symbol, quote_type, name),
        currency=currency,
        exchange=info.get("stock_exchange") or quote.get("exchange"),
        sector=info.get("sector"),
        industry=info.get("industry_category"),
        country=info.get("hq_country"),
        website=info.get("company_url"),
        description=info.get("long_description"),
        quote_type=quote_type,
    )


def to_stats(km_rows: list[dict], info_rows: list[dict], symbol: str, currency: Currency) -> Stats:
    src = _first(km_rows) or _first(info_rows)
    if not src:
        raise no_data(symbol)
    dividend_yield = _float(src.get("dividend_yield"))
    return Stats(
        symbol=symbol,
        currency=currency,
        market_cap=_float(src.get("market_cap")),  # already in major units (S1)
        pe_ratio=_float(src.get("pe_ratio")),
        dividend_yield=None if dividend_yield is None else dividend_yield / 100,  # percent (S1)
        beta=_float(src.get("beta")),
    )


def to_series(rows: list[dict], symbol: str, range_: Range, currency_code: str) -> PriceSeries:
    if not rows:
        raise no_data(symbol)
    currency, factor = normalise(currency_code)
    points = sorted(
        (
            PricePoint(_wall_time(r["date"]), float(r["close"]) * factor)
            for r in rows
            if r.get("close") is not None
        ),
        key=lambda p: p.at,
    )
    if range_ is Range.D1 and points:
        last_day = points[-1].at.date()
        points = [p for p in points if p.at.date() == last_day]
    return PriceSeries(symbol, range_, currency, tuple(points))


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def to_financials(
    rows: list[dict], symbol: str, statement: Statement, period: Period
) -> FinancialTable:
    dated = [r for r in rows if r.get("period_ending")]
    if not dated:
        raise no_data(symbol)
    ordered = sorted(dated, key=lambda r: r["period_ending"], reverse=True)
    keys: list[str] = []
    for row in ordered:
        for key, value in row.items():
            if key not in _META_KEYS and key not in keys and isinstance(value, int | float):
                keys.append(key)
    return FinancialTable(
        symbol=symbol,
        statement=statement,
        period=period,
        currency=next((r["reported_currency"] for r in ordered if r.get("reported_currency")), None),
        periods=tuple(date.fromisoformat(r["period_ending"][:10]) for r in ordered),
        rows=tuple(
            FinancialRow(key, _label(key), tuple(_float(r.get(key)) for r in ordered)) for key in keys
        ),
    )


def to_dividends(rows: list[dict], currency_code: str) -> tuple[Dividend, ...]:
    _, factor = normalise(currency_code)
    dividends = [
        Dividend(date.fromisoformat(r["ex_dividend_date"][:10]), float(r["amount"]) * factor)
        for r in rows
        if r.get("amount") is not None
    ]
    return tuple(sorted(dividends, key=lambda d: d.ex_date, reverse=True))


def to_fund_info(
    symbol: str,
    currency: Currency,
    dividends: tuple[Dividend, ...],
    price: float | None,
    etf_rows: list[dict],
    today: date,
) -> FundInfo:
    etf = _first(etf_rows)
    return FundInfo(
        symbol=symbol,
        currency=currency,
        dividends=dividends,
        ttm_yield=ttm_yield(dividends, price, today),
        expense_ratio=_float(etf.get("expense_ratio")),
        total_assets=_float(etf.get("total_assets")),
    )


# KeyMetrics source key -> (ValuationInputs field, factor to a fraction / plain ratio).
# Verified units (S1/S2 + 2026-10-10 capture): debt_to_equity and dividend_yield are
# percentages; price_return_1y is a fraction (AAPL 0.359 for a ~36% year).
_KM_SCALE: dict[str, tuple[str, float]] = {
    "forward_pe": ("forward_pe", 1.0),
    "peg_ratio": ("peg_ratio", 1.0),
    "enterprise_to_ebitda": ("ev_to_ebitda", 1.0),
    "return_on_equity": ("return_on_equity", 1.0),
    "operating_margin": ("operating_margin", 1.0),
    "profit_margin": ("profit_margin", 1.0),
    "debt_to_equity": ("debt_to_equity", 0.01),
    "current_ratio": ("current_ratio", 1.0),
    "revenue_growth": ("revenue_growth", 1.0),
    "earnings_growth": ("earnings_growth", 1.0),
    "dividend_yield": ("dividend_yield", 0.01),
    "payout_ratio": ("payout_ratio", 1.0),
    "price_return_1y": ("price_return_1y", 1.0),
}


def reporting_currency(km_rows: list[dict]) -> str | None:
    """KeyMetrics `currency` is the financial-reporting currency (SHEL.L -> USD)."""
    code = _first(km_rows).get("currency")
    return code if isinstance(code, str) and code else None


def fx_pair(reporting: str | None, trading: Currency) -> tuple[str | None, float]:
    """(CurrencyHistorical symbol or None when no conversion is needed, extra factor).

    Unknown reporting currency is assumed to be the trading currency. Pence reporting is
    GBP x 0.01.
    """
    if not reporting:
        return None, 1.0
    major, factor = ("GBP", 0.01) if reporting in PENCE else (reporting.upper(), 1.0)
    if major == trading.value:
        return None, factor
    return f"{major}{trading.value}", factor


def to_fx_rate(rows: list[dict]) -> float | None:
    """Last daily close of a currency pair; None when missing or not a positive number."""
    dated = sorted((r for r in rows if r.get("date") and r.get("close") is not None), key=lambda r: r["date"])
    if not dated:
        return None
    rate = float(dated[-1]["close"])
    return rate if math.isfinite(rate) and rate > 0 else None


def to_consensus(rows: list[dict], fallback_factor: float) -> AnalystConsensus | None:
    """Analyst targets are in the row's own quote currency (GBp for LSE listings)."""
    if not rows:
        return None
    row = rows[0]
    code = row.get("currency")
    factor = fallback_factor if code is None else 0.01 if code in PENCE else 1.0
    return AnalystConsensus(
        target_consensus=_scaled(row.get("target_consensus"), factor),
        target_median=_scaled(row.get("target_median"), factor),
        target_low=_scaled(row.get("target_low"), factor),
        target_high=_scaled(row.get("target_high"), factor),
        recommendation=row.get("recommendation"),
        recommendation_mean=_float(row.get("recommendation_mean")),
        number_of_analysts=_int(row.get("number_of_analysts")),
    )


def _newest(rows: list[dict], key: str) -> list[float]:
    dated = sorted(
        (r for r in rows if r.get("period_ending") and r.get(key) is not None),
        key=lambda r: r["period_ending"],
        reverse=True,
    )
    return [float(r[key]) for r in dated]


def to_valuation_inputs(
    symbol: str,
    *,
    quote_rows: list[dict],
    info_rows: list[dict],
    km_rows: list[dict],
    cash_flow_rows: list[dict],
    income_rows: list[dict],
    target_rows: list[dict],
    dividend_rows: list[dict],
    fx_rate: float | None,
    fallback_last: float | None,
    today: date,
    gaps: tuple[str, ...] = (),
    balance_rows: list[dict] | None = None,
    fetched_at: datetime | None = None,
) -> ValuationInputs:
    if not quote_rows:
        raise no_data(symbol)
    quote, info, km = quote_rows[0], _first(info_rows), _first(km_rows)
    code = quote.get("currency")
    currency, factor = normalise(code)
    raw_last = quote.get("last_price")
    if raw_last is None:
        raw_last = fallback_last
    if raw_last is None:
        raise no_data(symbol)
    price = float(raw_last) * factor
    if not math.isfinite(price) or price <= 0:  # the domain divides by price
        raise no_data(symbol)
    name = info.get("name") or quote.get("name") or symbol
    rc = reporting_currency(km_rows)
    metrics = {field: _scaled(km.get(key), scale) for key, (field, scale) in _KM_SCALE.items()}
    net_income = _newest(income_rows, "net_income")
    ebitda = _newest(income_rows, "ebitda")
    debt, cash = _debt_and_cash(balance_rows or [])
    return ValuationInputs(
        symbol=symbol,
        name=name,
        asset_type=classify(symbol, info.get("issue_type") or quote.get("asset_type"), name),
        currency=currency,
        as_of=today,
        price=price,
        ma_200d=_scaled(quote.get("ma_200d"), factor),
        shares_outstanding=_float(info.get("shares_outstanding")),
        reporting_currency=rc,
        fx_rate=fx_rate,
        fcf_history=tuple(_newest(cash_flow_rows, "free_cash_flow")[:4]),
        net_income=net_income[0] if net_income else None,
        book_value_ps=_float(km.get("book_value")),
        dividends=to_dividends(dividend_rows, code),
        yahoo_cross_currency=fx_pair(rc, currency)[0] is not None,
        analyst=to_consensus(target_rows, factor),
        gaps=gaps,
        ebitda=ebitda[0] if ebitda else None,
        total_debt=debt,
        cash=cash,
        year_high=_scaled(quote.get("year_high"), factor),
        year_low=_scaled(quote.get("year_low"), factor),
        fetched_at=fetched_at,
        **metrics,
    )


def _debt_and_cash(balance_rows: list[dict]) -> tuple[float | None, float | None]:
    """Latest balance sheet's debt and cash; an absent key on a present row means 0 (spike S3)."""
    dated = sorted(
        (r for r in balance_rows if r.get("period_ending")),
        key=lambda r: r["period_ending"],
        reverse=True,
    )
    if not dated:
        return None, None
    row = dated[0]
    return _float(row.get("total_debt")) or 0.0, _float(row.get("cash_and_cash_equivalents")) or 0.0


def offline(symbol: str | None = None) -> SourceUnavailable:
    return SourceUnavailable("Source unavailable (offline?)", symbol, reason="network")


def _rate_limited(symbol: str | None) -> SourceUnavailable:
    return SourceUnavailable(
        "Rate limited by Yahoo — try again shortly", symbol, reason="rate_limited"
    )


def map_exception(exc: BaseException, symbol: str | None = None) -> DataError:
    if isinstance(exc, DataError):
        return exc
    name = type(exc).__name__
    root_module = (type(exc).__module__ or "").split(".")[0]
    if name == "EmptyDataError":
        return no_data(symbol)
    if name == "YFRateLimitError":
        return _rate_limited(symbol)
    if isinstance(exc, OSError) or root_module in _NETWORK_MODULES:
        return offline(symbol)
    if name == "OpenBBError":
        message = str(exc)
        if _RATE_LIMIT_MESSAGE.search(message):
            return _rate_limited(symbol)
        if _NETWORK_MESSAGE.search(message):
            return offline(symbol)
        if _NO_DATA_MESSAGE.search(message):
            return no_data(symbol)
    return DataError("Unexpected source response", symbol)
