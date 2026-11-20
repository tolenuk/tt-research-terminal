"""The Top 20 screen's saved market snapshots: one JSON file per market, so the tab opens
instantly and survives a source being down. Unreadable or old-format files load as None."""

from __future__ import annotations

import json
import logging
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from obb_terminal.domain.cheap_screen import MarketSnapshot, ScreenStock
from obb_terminal.fsutil import atomic_write_text

log = logging.getLogger(__name__)

SNAPSHOT_VERSION = 2
_TEXT = ("ticker", "name", "company")
_NUMBERS = tuple(f.name for f in fields(ScreenStock) if f.name not in _TEXT)


def save_snapshot(path: Path, snapshot: MarketSnapshot) -> None:
    payload = {
        "version": SNAPSHOT_VERSION,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "presorted": snapshot.presorted,
        "financials": sorted(snapshot.financials),
        "stocks": [{f: getattr(s, f) for f in (*_TEXT, *_NUMBERS)} for s in snapshot.stocks],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False))


def _stock(row: dict) -> ScreenStock:
    return ScreenStock(
        **{f: str(row[f]) for f in _TEXT},
        **{f: None if row[f] is None else float(row[f]) for f in _NUMBERS},
    )


def load_snapshot(path: Path) -> MarketSnapshot | None:
    """The saved snapshot, or None when there is none or it can't be read (refetch instead)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != SNAPSHOT_VERSION:
            return None
        return MarketSnapshot(
            tuple(_stock(row) for row in payload["stocks"]),
            frozenset(str(t) for t in payload["financials"]),
            datetime.fromisoformat(payload["fetched_at"]),
            presorted=bool(payload["presorted"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
        log.warning("Ignoring unreadable screener snapshot %s: %s", path, err)
        return None
