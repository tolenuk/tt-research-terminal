"""Plain-text lines for a trade call and a debate, shared by the AI panel and the PDF export."""

from __future__ import annotations

from obb_terminal.ai.models import (
    ACTION_LABELS,
    ROLE_LABELS,
    VERDICT_LABELS,
    Debate,
    Role,
    TradeCall,
)
from obb_terminal.ui.fmt import fmt_price

NONE_GIVEN = "none given"
KEY_LEVEL_LABELS: dict[Role, str] = {Role.BULL: "worth", Role.BEAR: "worth", Role.RISK: "bad case"}


def _level(value: float | None, currency: str) -> str:
    return NONE_GIVEN if value is None else fmt_price(value, currency)


def zone_text(call: TradeCall, currency: str) -> str:
    low, high = call.entry_low, call.entry_high
    if low is None or high is None:
        return f"Entry zone: {NONE_GIVEN}"
    if low == high:
        return f"Entry: {fmt_price(low, currency)}"
    return f"Entry zone: {low:,.2f}–{high:,.2f} {currency}".rstrip()


def call_lines(call: TradeCall, currency: str) -> list[str]:
    """Headline lines, then watch conditions as bullets, then the app's sanitising notes."""
    lines = [
        f"Action: {ACTION_LABELS[call.action]} ({call.conviction.value} conviction)",
        zone_text(call, currency),
        f"Thesis break: {_level(call.thesis_break, currency)}",
        f"Target: {_level(call.target_price, currency)}",
    ]
    if call.invalidation:
        lines.append(f"Wrong if: {call.invalidation}")
    lines += [f"• {w}" for w in call.watch_conditions]
    lines += [f"Note: {n}" for n in call.notes]
    return lines


def disagreement_lines(debate: Debate) -> list[str]:
    lines = ["WHERE THEY DISAGREE"]
    for d in debate.disagreements:
        lines += [f"• {d.topic} — {VERDICT_LABELS[d.verdict]}: {d.why}", f"  Bull: {d.bull} · Bear: {d.bear}"]
    lines += [f"AGREED: {c}" for c in debate.consensus]
    return lines


def case_lines(debate: Debate, currency: str) -> list[str]:
    lines = ["THE CASES"]
    for case in debate.cases:
        level = "" if case.key_level is None else f", {KEY_LEVEL_LABELS[case.role]} {fmt_price(case.key_level, currency)}"
        lines.append(f"{ROLE_LABELS[case.role]} ({case.conviction.value} conviction{level}): {case.thesis}")
        lines += [f"  • {p.point} — {p.evidence}" for p in case.points]
    lines += [f"The {ROLE_LABELS[r].lower()} case is missing (that analyst failed)." for r in debate.missing]
    return lines
