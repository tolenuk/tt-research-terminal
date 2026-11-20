"""Top-holdings roll-up: value each ETF holding with the stock valuation, and remember results.

`value_holding` blocks (a worker thread) and never raises a DataError: a holding that cannot be
valued becomes a row with its reason. `RollupMemo` is shared by the Valuation page (which runs
the roll-up) and the desk dashboards (which only read it, never start one).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from obb_terminal.data.market import DataError, MarketData, NoData, SymbolNotFound
from obb_terminal.domain.etf import holding_value
from obb_terminal.domain.models import AssetType, Holding, HoldingValue, ValuationInputs
from obb_terminal.domain.valuation import default_assumptions, value

log = logging.getLogger(__name__)

ROLLUP_TTL_S = 6 * 3600.0
MAX_PARALLEL = 3
NOT_VALUED_FUND = "a fund, not valued"
NO_DATA = "no data"
TIMED_OUT = "timed out"


def value_holding(md: MarketData, holding: Holding) -> HoldingValue:
    try:
        inputs = md.valuation_inputs(holding.symbol)
    except (NoData, SymbolNotFound):
        return holding_value(holding, None, NO_DATA)
    except DataError as exc:  # timeout, offline, refused: may pass on the next try
        return holding_value(holding, None, exc.user_message, transient=True)
    if inputs.asset_type is not AssetType.EQUITY:
        return holding_value(holding, None, NOT_VALUED_FUND)
    try:
        v = value(inputs, default_assumptions(inputs))
    except Exception:  # pure domain code; a bug must not sink the whole roll-up
        log.exception("Could not value holding %s", holding.symbol)
        return holding_value(holding, None, "could not compute")
    # Partial inputs (some sub-fetch failed) give a provisional value, like a stock page's gaps.
    return holding_value(holding, v.margin, v.reason or "no fair value", transient=bool(inputs.gaps))


class RollupMemo:
    """symbol -> finished roll-up rows, for `ttl_s`. Thread-safe."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl_s: float = ROLLUP_TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, tuple[HoldingValue, ...]]] = {}

    def get(self, symbol: str) -> tuple[HoldingValue, ...] | None:
        key = symbol.strip().upper()
        with self._lock:
            hit = self._entries.get(key)
            if hit is None:
                return None
            if hit[0] <= self._clock():
                del self._entries[key]
                return None
            return hit[1]

    def put(self, symbol: str, values: tuple[HoldingValue, ...]) -> None:
        with self._lock:
            self._entries[symbol.strip().upper()] = (self._clock() + self._ttl_s, values)


def attach_cached(inputs: ValuationInputs, memo: RollupMemo | None) -> ValuationInputs:
    """An ETF's inputs with its remembered roll-up, if one is there; otherwise unchanged."""
    etf = inputs.etf
    if memo is None or etf is None or etf.holding_values is not None:
        return inputs
    cached = memo.get(inputs.symbol)
    return inputs if cached is None else replace(inputs, etf=replace(etf, holding_values=cached))
