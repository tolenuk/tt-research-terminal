"""Turn what the app shows into journal snapshots (pure), and record them."""

from __future__ import annotations

import logging
from datetime import datetime

from obb_terminal.ai.models import CachedReport, ReportKind
from obb_terminal.domain.etf import etf_kind, rollup_settled
from obb_terminal.domain.models import EtfKind, Score, ScoreBand, Valuation, ValuationInputs
from obb_terminal.domain.scoring import score
from obb_terminal.domain.trade_call import direction, strength
from obb_terminal.domain.valuation import default_assumptions, value
from obb_terminal.journal.benchmarks import BENCHMARK
from obb_terminal.journal.models import Kind, Snapshot
from obb_terminal.journal.store import Journal, JournalError

log = logging.getLogger(__name__)

ETF_MODEL = "etf-1"  # ETF Outlook rows: kept out of the stock score statistics
_BAND_DIRECTION = {ScoreBand.FAVOURABLE: 1, ScoreBand.MIXED: 0, ScoreBand.UNFAVOURABLE: -1}


def _base(inputs: ValuationInputs, kind: Kind, now: datetime) -> dict:
    return dict(
        symbol=inputs.symbol, day=inputs.as_of, kind=kind, recorded_at=now, price=inputs.price,
        currency=inputs.currency.value, benchmark=BENCHMARK[inputs.currency],
    )


def score_snapshot(inputs: ValuationInputs, v: Valuation, s: Score, now: datetime) -> Snapshot | None:
    """The page's Outlook score and fair value (default assumptions); None if it has neither."""
    if s.value is None and v.blended is None:
        return None
    return Snapshot(
        **_base(inputs, Kind.SCORE, now),
        direction=0 if s.band is None else _BAND_DIRECTION[s.band],
        strength=s.value,
        outlook=s.value,
        band=None if s.band is None else s.band.value,
        margin=v.margin,
        fair_value=v.blended,
        components=tuple((c.key.value, c.value) for c in s.components if c.value is not None),
        stance=None, action=None, conviction=None, entry_low=None, entry_high=None, thesis_break=None,
        target_price=None, model=None if inputs.etf is None else ETF_MODEL,
    )


def call_snapshot(entry: CachedReport, inputs: ValuationInputs, now: datetime) -> Snapshot:
    """An AI or debate report's call, priced at the inputs it was run on."""
    r, call = entry.report, entry.report.call
    return Snapshot(
        **_base(inputs, Kind.DEBATE if entry.kind is ReportKind.DEBATE else Kind.AI, now),
        direction=direction(call, r.stance),
        strength=float(strength(call, r.stance)),
        outlook=None, band=None, margin=None, fair_value=None, components=(),
        stance=r.stance.value,
        action=None if call is None else call.action.value,
        conviction=None if call is None else call.conviction.value,
        entry_low=None if call is None else call.entry_low,
        entry_high=None if call is None else call.entry_high,
        thesis_break=None if call is None else call.thesis_break,
        target_price=None if call is None else call.target_price,
        model=entry.model,
    )


def rollup_pending(inputs: ValuationInputs) -> bool:
    """An equity ETF whose top holdings are not valued yet, or were valued with failures that may
    pass: its score would lack (or misstate) the upside."""
    etf = inputs.etf
    if etf is None or etf_kind(etf.profile) is not EtfKind.EQUITY:
        return False
    return etf.holding_values is None or not rollup_settled(etf.holding_values)


def record_page(journal: Journal, inputs: ValuationInputs, now: datetime) -> bool:
    """Record the page's score with default assumptions (worker thread). True if a new row
    was written; journal problems are logged, never raised. A day keeps its first score, so
    partial data is not recorded: it would stay as that day's score."""
    if inputs.gaps or rollup_pending(inputs):
        return False
    try:
        a = default_assumptions(inputs)
        v = value(inputs, a)
        snap = score_snapshot(inputs, v, score(inputs, v, a), now)
        return snap is not None and journal.record(snap) is not None
    except JournalError:
        return False
    except Exception:  # the page shows its own compute error; recording must not fail the load
        log.exception("Could not record the score of %s", inputs.symbol)
        return False


def record_call(journal: Journal, entry: CachedReport, inputs: ValuationInputs, now: datetime) -> bool:
    """Record (or replace today's) AI or debate call. Never raises."""
    try:
        return journal.record(call_snapshot(entry, inputs, now)) is not None
    except JournalError:
        return False
    except Exception:  # a finished, paid analysis must still be shown
        log.exception("Could not record the AI call on %s", inputs.symbol)
        return False
