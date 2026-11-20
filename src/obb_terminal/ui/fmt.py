"""Display formatting. Fractions in, human strings out; None renders as an em dash."""

from __future__ import annotations

from datetime import timedelta

from obb_terminal.domain.models import Assumptions, Kpi, KpiFormat

DASH = "—"
ARROWS = {"up": "▲", "down": "▼", "flat": "■"}


def fmt_num(value: float | None, dp: int = 2) -> str:
    return DASH if value is None else f"{value:,.{dp}f}"


def fmt_int(value: int | None) -> str:
    return DASH if value is None else f"{value:,}"


def fmt_price(value: float | None, currency: str) -> str:
    return DASH if value is None else f"{value:,.2f} {currency}"


def fmt_pct(fraction: float | None, *, signed: bool = False) -> str:
    if fraction is None:
        return DASH
    return f"{fraction * 100:+.2f}%" if signed else f"{fraction * 100:.2f}%"


def fmt_big(value: float | None) -> str:
    if value is None:
        return DASH
    for divisor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= divisor:
            return f"{value / divisor:,.2f}{suffix}"
    return f"{value:,.0f}"


def fmt_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return DASH
    return f"{low:,.2f} – {high:,.2f}"


def direction(change: float | None) -> str:
    if not change:
        return "flat"
    return "up" if change > 0 else "down"


def fmt_change(change: float | None, pct: float | None) -> str:
    if change is None:
        return DASH
    return f"{ARROWS[direction(change)]} {change:+,.2f} ({fmt_pct(pct, signed=True)})"


def fmt_age(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"


def fmt_assumptions(a: Assumptions) -> str:
    return (
        f"discount {fmt_pct(a.discount_rate)} · growth {fmt_pct(a.growth_1_5)} · "
        f"terminal {fmt_pct(a.terminal_growth)} · dividend growth {fmt_pct(a.dividend_growth)}"
    )


def fmt_kpi(kpi: Kpi) -> str:
    if kpi.value is None:
        return DASH if kpi.note is None else f"{DASH} ({kpi.note})"
    if kpi.fmt is KpiFormat.MULTIPLE:
        text = f"{kpi.value:,.1f}x"
    elif kpi.fmt is KpiFormat.PERCENT:
        text = fmt_pct(kpi.value)
    else:
        text = f"{kpi.value:,.2f}"
    return text if kpi.note is None else f"{text} ({kpi.note})"
