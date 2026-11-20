"""Latest AI report per symbol: $XDG_DATA_HOME/obb-terminal/analyses/<SYMBOL>.json."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from obb_terminal.ai.models import CachedReport, ReportKind
from obb_terminal.ai.schema import parse_report, report_to_dict
from obb_terminal.data.market import DataError
from obb_terminal.domain.valuation import assumptions_from_dict, assumptions_to_dict
from obb_terminal.fsutil import atomic_write_text
from obb_terminal.store import SYMBOL_RE, data_dir

log = logging.getLogger(__name__)

VERSION = 2
READABLE = (1, 2)  # version 1 files predate trade calls and debates


def _encode(entry: CachedReport) -> dict:
    return {
        "version": VERSION,
        "kind": entry.kind.value,
        "currency": entry.currency,
        "symbol": entry.symbol,
        "created_at": entry.created_at.astimezone(UTC).isoformat(),
        "model": entry.model,
        "effort": entry.effort,
        "assumptions": assumptions_to_dict(entry.assumptions),
        "cost_usd": entry.cost_usd,
        "duration_ms": entry.duration_ms,
        "report": report_to_dict(entry.report),
    }


def _currency(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.isalpha() or len(value) > 5:
        raise ValueError("bad currency")
    return value


def _decode(data: object, symbol: str) -> CachedReport:
    if not isinstance(data, dict) or data.get("version") not in READABLE:
        raise ValueError("unsupported analysis file version")
    if data.get("symbol") != symbol:
        raise ValueError("symbol mismatch")
    created = datetime.fromisoformat(data["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    model, effort = data["model"], data["effort"]
    if not isinstance(model, str) or not isinstance(effort, str):
        raise TypeError("model and effort must be strings")
    cost, duration = data.get("cost_usd"), data.get("duration_ms")
    return CachedReport(
        symbol=symbol,
        report=parse_report(data["report"], stored=True),
        created_at=created,
        model=model,
        effort=effort,
        assumptions=assumptions_from_dict(data["assumptions"]),
        cost_usd=None if cost is None else float(cost),
        duration_ms=None if duration is None else int(duration),
        kind=ReportKind(data.get("kind", ReportKind.SINGLE.value)),
        currency=_currency(data.get("currency")),
    )


class ReportStore:
    """Only this class writes analysis files. Corrupt files read as "no previous report"."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_dir() / "analyses"

    def path_for(self, symbol: str) -> Path | None:
        s = symbol.strip().upper()
        return self.root / f"{s}.json" if SYMBOL_RE.match(s) else None

    def get(self, symbol: str) -> CachedReport | None:
        path = self.path_for(symbol)
        if path is None or not path.exists():
            return None
        try:
            return _decode(json.loads(path.read_text(encoding="utf-8")), path.stem)
        except (OSError, ValueError, TypeError, KeyError, RecursionError, DataError) as exc:
            log.warning("Ignoring unreadable analysis %s (%s)", path, exc)
            return None

    def put(self, entry: CachedReport) -> None:
        """Raises OSError when the file cannot be written."""
        path = self.path_for(entry.symbol)
        if path is None:
            raise OSError(f"Invalid symbol for analysis file: {entry.symbol!r}")
        atomic_write_text(path, json.dumps(_encode(entry), indent=2, ensure_ascii=False))
