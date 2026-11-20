"""Builders for journal tests: snapshots and simple daily bar series."""

from datetime import UTC, date, datetime, timedelta

from obb_terminal.journal.models import Kind, Snapshot

DAY0 = date(2026, 1, 5)  # a Monday
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def snap(symbol="AAPL", *, day=DAY0, kind=Kind.SCORE, direction=1, strength=75.0, price=100.0, currency="USD",
         benchmark="SPY", components=(("upside", 80.0), ("momentum", 60.0)), id=None, **kw) -> Snapshot:
    base = dict(
        symbol=symbol, day=day, kind=kind, recorded_at=NOW, price=price, currency=currency,
        benchmark=benchmark, direction=direction, strength=strength, outlook=strength if kind is Kind.SCORE else None,
        band=None, margin=None, fair_value=None, components=components, stance=None, action=None,
        conviction=None, entry_low=None, entry_high=None, thesis_break=None, target_price=None, model=None,
        id=id,
    )
    return Snapshot(**{**base, **kw})


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bars(closes, start=DAY0):
    return list(zip(weekdays(start, len(closes)), closes, strict=True))
