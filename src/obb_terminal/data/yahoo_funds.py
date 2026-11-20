"""ETF look-through data via yfinance `Ticker.funds_data` (OpenBB 1.x has no holdings fetcher).

The seam (`_yf_funds`) turns yfinance's pandas frames into plain JSON-like dicts so the mapping
(`to_fund_profile`) is pure and testable from saved samples. yfinance is imported lazily.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from obb_terminal.domain.models import FundProfile, Holding

log = logging.getLogger(__name__)

FundsFn = Callable[[str], dict[str, Any]]

NO_FUND_DATA = "no fund data"
MAX_MULTIPLE = 200.0  # an inverted multiple above this is treated as bad data
_SECTOR_SUM = (0.9, 1.1)
_NO_FUND_MESSAGE = "No Fund data found"


class NoFundData(Exception):
    """Yahoo has no fund data for this symbol (B3 ETFs, some UK listings)."""


def _clean(value: Any) -> Any:
    """pandas NA/NaN -> None; numpy scalars -> plain Python."""
    if value is None:
        return None
    try:
        if value != value:  # NaN, pd.NA raises TypeError on bool()
            return None
    except TypeError:
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value


def _pairs(frame: Any) -> dict[str, list[Any]]:
    """A fund/category-average frame -> {row label: [fund, category average]}."""
    if frame is None or len(frame) == 0:
        return {}
    return {str(label): [_clean(v) for v in row] for label, row in zip(frame.index, frame.values, strict=True)}


def _yf_funds(symbol: str) -> dict[str, Any]:
    import yfinance as yf

    return read_funds(yf.Ticker(symbol), symbol)


def read_funds(ticker: Any, symbol: str) -> dict[str, Any]:
    """A yfinance Ticker's funds_data as plain dicts; NoFundData when Yahoo has none."""
    try:
        data = ticker.funds_data
        overview = dict(data.fund_overview or {})
        holdings_frame = data.top_holdings
        raw = {
            "overview": {k: _clean(v) for k, v in overview.items()},
            "operations": _pairs(data.fund_operations),
            "holdings": [
                [str(sym), _clean(row.get("Name")), _clean(row.get("Holding Percent"))]
                for sym, row in holdings_frame.iterrows()
            ] if holdings_frame is not None else [],
            "sectors": {k: _clean(v) for k, v in (data.sector_weightings or {}).items()},
            "asset_classes": {k: _clean(v) for k, v in (data.asset_classes or {}).items()},
            "equity": _pairs(data.equity_holdings),
            "bond_ratings": {k: _clean(v) for k, v in (data.bond_ratings or {}).items()},
        }
    except Exception as exc:
        if _NO_FUND_MESSAGE in str(exc):
            raise NoFundData(symbol) from exc
        raise
    return raw


class YahooFunds:
    def __init__(self, funds_fn: FundsFn | None = None) -> None:
        self._funds_fn = funds_fn or _yf_funds

    def fetch(self, symbol: str) -> dict[str, Any]:
        """Raw fund data; raises NoFundData when Yahoo has none, other errors as raised."""
        return self._funds_fn(symbol)


# --- mapping ------------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fund_value(pairs: dict[str, list[Any]], label: str) -> float | None:
    row = pairs.get(label) or []
    return _num(row[0]) if len(row) > 0 else None


def _category_value(pairs: dict[str, list[Any]], label: str) -> float | None:
    row = pairs.get(label) or []
    return _num(row[1]) if len(row) > 1 else None


def invert_multiple(raw: float | None) -> float | None:
    """Yahoo stores look-through multiples inverted (SPY P/E 0.0416 = 24.0x). 0 = no value."""
    if raw is None or raw <= 0:
        return None
    value = 1.0 / raw
    return value if value <= MAX_MULTIPLE else None


def _fractions(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out = {str(k): f for k, v in raw.items() if (f := _num(v)) is not None and f >= 0}
    return out


def _sectors(raw: Any) -> tuple[tuple[str, float], ...]:
    weights = {k: v for k, v in _fractions(raw).items() if v > 0}
    total = sum(weights.values())
    if not (_SECTOR_SUM[0] <= total <= _SECTOR_SUM[1]):
        return ()
    return tuple(sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])))


def _holdings(raw: Any) -> tuple[Holding, ...]:
    out: list[Holding] = []
    for row in raw or ():
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        symbol, name, weight = str(row[0] or "").strip(), row[1], _num(row[2])
        if not symbol or weight is None or weight <= 0:
            continue
        out.append(Holding(symbol=symbol, name=str(name or symbol), weight=weight))
    return tuple(sorted(out, key=lambda h: -h.weight))


def empty_fund_profile(symbol: str, gap: str = NO_FUND_DATA) -> FundProfile:
    return FundProfile(symbol=symbol, gaps=(gap,))


def to_fund_profile(symbol: str, raw: dict[str, Any]) -> FundProfile:
    # yfinance hides some parse failures and returns None for everything: no fund data, not a
    # fund with nothing in it (which would read as kind OTHER for a day).
    if not raw.get("holdings") and not raw.get("equity") and not raw.get("asset_classes"):
        return empty_fund_profile(symbol)
    overview = raw.get("overview") or {}
    ops = raw.get("operations") or {}
    equity = raw.get("equity") or {}
    mix = _fractions(raw.get("asset_classes"))
    ratings = tuple(
        sorted(((k, v) for k, v in _fractions(raw.get("bond_ratings")).items() if v > 0),
               key=lambda kv: (-kv[1], kv[0]))
    )
    return FundProfile(
        symbol=symbol,
        category=overview.get("categoryName") or None,
        family=overview.get("family") or None,
        expense_ratio=_fund_value(ops, "Annual Report Expense Ratio"),
        category_expense_ratio=_category_value(ops, "Annual Report Expense Ratio"),
        turnover=_fund_value(ops, "Annual Holdings Turnover"),
        holdings=_holdings(raw.get("holdings")),
        sectors=_sectors(raw.get("sectors")),
        stock_position=mix.get("stockPosition"),
        bond_position=mix.get("bondPosition"),
        cash_position=mix.get("cashPosition"),
        other_position=sum(
            mix.get(k, 0.0) for k in ("preferredPosition", "convertiblePosition", "otherPosition")
        ) if mix else None,
        pe=invert_multiple(_fund_value(equity, "Price/Earnings")),
        pb=invert_multiple(_fund_value(equity, "Price/Book")),
        ps=invert_multiple(_fund_value(equity, "Price/Sales")),
        pcf=invert_multiple(_fund_value(equity, "Price/Cashflow")),
        bond_ratings=ratings,
    )
