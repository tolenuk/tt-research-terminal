"""The JSON schema Claude must fill, and a defensive parser for what comes back."""

from __future__ import annotations

import logging
import math
from urllib.parse import urlsplit

from obb_terminal.ai.errors import AnalystBadOutput
from obb_terminal.ai.models import (
    Action,
    AnalystReport,
    Conviction,
    Debate,
    Disagreement,
    Horizon,
    Point,
    Role,
    RoleCase,
    Source,
    Stance,
    TradeCall,
    Verdict,
    WatchItem,
)

log = logging.getLogger(__name__)

MAX_ITEMS = 6
MAX_SOURCES = 12
MAX_CHARS = 2000
MAX_WATCH_CONDITIONS = 4
MAX_NOTES = 8
MAX_ROLE_POINTS = 5
MAX_DISAGREEMENTS = 5
MAX_CONSENSUS = 4

_PRICE = {"type": ["number", "null"]}
CALL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["action", "conviction", "entry_low", "entry_high", "thesis_break", "target_price",
                 "invalidation", "watch_conditions"],
    "properties": {
        "action": {"type": "string", "enum": [a.value for a in Action]},
        "conviction": {"type": "string", "enum": [c.value for c in Conviction]},
        "entry_low": _PRICE,
        "entry_high": _PRICE,
        "thesis_break": _PRICE,
        "target_price": _PRICE,
        "invalidation": {"type": "string"},
        "watch_conditions": {"type": "array", "maxItems": MAX_WATCH_CONDITIONS, "items": {"type": "string"}},
    },
}

_P = {"type": "object", "additionalProperties": False, "required": ["point", "evidence"],
      "properties": {"point": {"type": "string"}, "evidence": {"type": "string"}}}
REPORT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "stance", "pros", "cons", "future_watch", "sources"],
    "properties": {
        "summary": {"type": "string"},
        "stance": {"type": "string", "enum": ["bullish", "neutral", "bearish"]},
        "pros": {"type": "array", "minItems": 2, "maxItems": 6, "items": _P},
        "cons": {"type": "array", "minItems": 2, "maxItems": 6, "items": _P},
        "future_watch": {"type": "array", "minItems": 2, "maxItems": 6, "items": {
            "type": "object", "additionalProperties": False, "required": ["item", "why", "horizon"],
            "properties": {"item": {"type": "string"}, "why": {"type": "string"},
                           "horizon": {"type": "string", "enum": ["next earnings", "6-12 months", "long term"]}}}},
        "sources": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "url"],
            "properties": {"title": {"type": "string"}, "url": {"type": "string"}}}},
        "call": CALL_SCHEMA,
    },
}
REPORT_SCHEMA["required"].append("call")

_SOURCES = REPORT_SCHEMA["properties"]["sources"]
ROLE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["thesis", "points", "conviction", "key_level", "sources"],
    "properties": {
        "thesis": {"type": "string"},
        "points": {"type": "array", "minItems": 2, "maxItems": MAX_ROLE_POINTS, "items": _P},
        "conviction": {"type": "string", "enum": [c.value for c in Conviction]},
        "key_level": _PRICE,
        "sources": _SOURCES,
    },
}
JUDGE_SCHEMA = {
    **REPORT_SCHEMA,
    "required": [*REPORT_SCHEMA["required"], "disagreements", "consensus"],
    "properties": {
        **REPORT_SCHEMA["properties"],
        "disagreements": {"type": "array", "minItems": 1, "maxItems": MAX_DISAGREEMENTS, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["topic", "bull", "bear", "verdict", "why"],
            "properties": {"topic": {"type": "string"}, "bull": {"type": "string"},
                           "bear": {"type": "string"}, "why": {"type": "string"},
                           "verdict": {"type": "string", "enum": [v.value for v in Verdict]}}}},
        "consensus": {"type": "array", "maxItems": MAX_CONSENSUS, "items": {"type": "string"}},
    },
}


def is_safe_url(url: str) -> bool:
    """Only http(s) URLs with a host may be shown as links or opened."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value.strip()[:MAX_CHARS]


def _items(value: object, limit: int = MAX_ITEMS) -> list[dict]:
    if not isinstance(value, list):
        raise TypeError("expected a list")
    return value[:limit]


def _price(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("expected a number or null")
    number = float(value)
    return number if math.isfinite(number) else None


def _strings(value: object, limit: int) -> tuple[str, ...]:
    return tuple(t for t in (_text(v) for v in _items(value, limit)) if t)


def parse_call(obj: object, *, stored: bool = False) -> TradeCall:
    """Structural parse only (no price checks: see domain.trade_call.sanitize). `notes` are the
    app's own remarks, so they are read only from `stored` files, never from model output.
    Raises TypeError, ValueError or KeyError on a bad shape."""
    if not isinstance(obj, dict):
        raise TypeError("call must be an object")
    return TradeCall(
        action=Action(obj["action"]),
        conviction=Conviction(obj["conviction"]),
        entry_low=_price(obj["entry_low"]),
        entry_high=_price(obj["entry_high"]),
        thesis_break=_price(obj["thesis_break"]),
        target_price=_price(obj["target_price"]),
        invalidation=_text(obj["invalidation"]),
        watch_conditions=_strings(obj["watch_conditions"], MAX_WATCH_CONDITIONS),
        notes=_strings(obj.get("notes", []), MAX_NOTES) if stored else (),
    )


def _optional_call(obj: dict, stored: bool) -> TradeCall | None:
    raw = obj.get("call")
    if raw is None:
        return None
    try:
        return parse_call(raw, stored=stored)
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Ignoring a malformed trade call (%s)", exc)
        return None


def _points(value: object, limit: int = MAX_ITEMS) -> tuple[Point, ...]:
    return tuple(Point(_text(p["point"]), _text(p["evidence"])) for p in _items(value, limit))


def _sources(value: object) -> tuple[Source, ...]:
    return tuple(
        Source(_text(s["title"]), _text(s["url"]))
        for s in _items(value, MAX_SOURCES)
        if is_safe_url(_text(s["url"]))
    )


def _role_case(obj: object, role: Role) -> RoleCase:
    if not isinstance(obj, dict):
        raise TypeError("role case must be an object")
    return RoleCase(
        role=role,
        thesis=_text(obj["thesis"]),
        points=_points(obj["points"], MAX_ROLE_POINTS),
        conviction=Conviction(obj["conviction"]),
        key_level=_price(obj["key_level"]),
        sources=_sources(obj["sources"]),
    )


def parse_role(obj: object, role: Role) -> RoleCase:
    """Validate one role pass. Raises AnalystBadOutput on any shape problem."""
    try:
        return _role_case(obj, role)
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalystBadOutput() from exc


def _disagreements(value: object) -> tuple[Disagreement, ...]:
    return tuple(
        Disagreement(_text(d["topic"]), _text(d["bull"]), _text(d["bear"]), Verdict(d["verdict"]), _text(d["why"]))
        for d in _items(value, MAX_DISAGREEMENTS)
    )


def parse_judge(obj: object) -> tuple[AnalystReport, tuple[Disagreement, ...], tuple[str, ...]]:
    """Validate the judge pass: a full report plus the debate summary. Raises AnalystBadOutput."""
    report = parse_report(obj)
    assert isinstance(obj, dict)
    try:
        disagreements = _disagreements(obj["disagreements"])
        consensus = _strings(obj["consensus"], MAX_CONSENSUS)
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalystBadOutput() from exc
    if not disagreements:
        raise AnalystBadOutput()
    return report, disagreements, consensus


def _debate(obj: object) -> Debate:
    if not isinstance(obj, dict):
        raise TypeError("debate must be an object")
    cases = tuple(_role_case(c, Role(c["role"])) for c in _items(obj["cases"], len(Role)))
    return Debate(
        cases=cases,
        disagreements=_disagreements(obj["disagreements"]),
        consensus=_strings(obj["consensus"], MAX_CONSENSUS),
        missing=tuple(Role(r) for r in _items(obj.get("missing", []), len(Role))),
    )


def _optional_debate(obj: dict) -> Debate | None:
    raw = obj.get("debate")
    if raw is None:
        return None
    try:
        return _debate(raw)
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Ignoring a malformed debate (%s)", exc)
        return None


def parse_report(obj: object, *, stored: bool = False) -> AnalystReport:
    """Validate Claude's structured output (or, with `stored`, a report file's). Raises
    AnalystBadOutput on any shape problem."""
    if not isinstance(obj, dict):
        raise AnalystBadOutput()
    try:
        return AnalystReport(
            summary=_text(obj["summary"]),
            stance=Stance(obj["stance"]),
            pros=_points(obj["pros"]),
            cons=_points(obj["cons"]),
            future_watch=tuple(
                WatchItem(
                    _text(w["item"]),
                    _text(w["why"]),
                    Horizon(_text(w["horizon"]).replace("–", "-")),
                )
                for w in _items(obj["future_watch"])
            ),
            sources=_sources(obj["sources"]),
            call=_optional_call(obj, stored),
            debate=_optional_debate(obj),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalystBadOutput() from exc


def call_to_dict(call: TradeCall) -> dict:
    return {
        "action": call.action.value,
        "conviction": call.conviction.value,
        "entry_low": call.entry_low,
        "entry_high": call.entry_high,
        "thesis_break": call.thesis_break,
        "target_price": call.target_price,
        "invalidation": call.invalidation,
        "watch_conditions": list(call.watch_conditions),
        "notes": list(call.notes),
    }


def report_to_dict(report: AnalystReport) -> dict:
    """Plain JSON-able dict in the REPORT_SCHEMA shape (round-trips through parse_report)."""
    out = {
        "summary": report.summary,
        "stance": report.stance.value,
        "pros": _points_dict(report.pros),
        "cons": _points_dict(report.cons),
        "future_watch": [
            {"item": w.item, "why": w.why, "horizon": w.horizon.value} for w in report.future_watch
        ],
        "sources": [{"title": s.title, "url": s.url} for s in report.sources],
    }
    if report.call is not None:
        out["call"] = call_to_dict(report.call)
    if report.debate is not None:
        out["debate"] = debate_to_dict(report.debate)
    return out


def _points_dict(points: tuple[Point, ...]) -> list[dict]:
    return [{"point": p.point, "evidence": p.evidence} for p in points]


def role_case_to_dict(case: RoleCase) -> dict:
    return {
        "role": case.role.value,
        "thesis": case.thesis,
        "points": _points_dict(case.points),
        "conviction": case.conviction.value,
        "key_level": case.key_level,
        "sources": [{"title": s.title, "url": s.url} for s in case.sources],
    }


def debate_to_dict(debate: Debate) -> dict:
    return {
        "cases": [role_case_to_dict(c) for c in debate.cases],
        "disagreements": [
            {"topic": d.topic, "bull": d.bull, "bear": d.bear, "verdict": d.verdict.value, "why": d.why}
            for d in debate.disagreements
        ],
        "consensus": list(debate.consensus),
        "missing": [r.value for r in debate.missing],
    }
