"""Assemble an AnalystContext. Runs on a Loader thread (it may fetch news)."""

from __future__ import annotations

import logging

from obb_terminal.ai.models import AnalystContext
from obb_terminal.data.market import DataError, MarketData
from obb_terminal.domain.kpis import kpis
from obb_terminal.domain.models import Assumptions, Profile, ValuationInputs
from obb_terminal.domain.scoring import score
from obb_terminal.domain.valuation import value

log = logging.getLogger(__name__)

MAX_HEADLINES = 10


def collect_context(
    md: MarketData, inputs: ValuationInputs, profile: Profile | None, assumptions: Assumptions
) -> AnalystContext:
    """Blocking. Uses the page's CURRENT (possibly edited) assumptions; news is best-effort."""
    valuation = value(inputs, assumptions)
    try:
        headlines = tuple(md.news(inputs.symbol, MAX_HEADLINES)[:MAX_HEADLINES])
    except DataError as exc:
        log.info("No headlines for %s: %s", inputs.symbol, exc.user_message)
        headlines = ()
    return AnalystContext(
        symbol=inputs.symbol,
        name=inputs.name,
        asset_type=inputs.asset_type,
        currency=inputs.currency,
        price=inputs.price,
        as_of=inputs.as_of,
        sector=profile.sector if profile else None,
        industry=profile.industry if profile else None,
        assumptions=assumptions,
        valuation=valuation,
        score=score(inputs, valuation, assumptions),
        kpis=tuple(kpis(inputs, assumptions.etf.bond_yield)),
        analyst=inputs.analyst,
        headlines=headlines,
        etf=inputs.etf,
    )
