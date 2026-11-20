"""Persisted app state (watchlist + last symbol) as versioned JSON, written atomically."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from obb_terminal.fsutil import atomic_write_text
from obb_terminal.osdirs import data_base

log = logging.getLogger(__name__)

SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=]{1,20}$")
MAX_SYMBOLS = 200
VERSION = 1


def data_dir() -> Path:
    return data_base() / "obb-terminal"


@dataclass(frozen=True)
class AppState:
    symbols: tuple[str, ...] = ()
    last_symbol: str | None = None


@dataclass(frozen=True)
class LoadResult:
    state: AppState
    recovered: bool = False


class StoreError(Exception):
    pass


def _parse(data: object) -> AppState:
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError("unsupported watchlist version")
    symbols = data.get("symbols")
    if not isinstance(symbols, list):
        raise TypeError("symbols must be a list")
    # Structure errors above quarantine the file; a bad individual entry is only dropped,
    # so one stray value never costs the whole watchlist.
    clean: list[str] = []
    for symbol in symbols:
        if not _valid(symbol):
            log.warning("Dropping invalid watchlist symbol %r", symbol)
            continue
        if symbol not in clean:
            clean.append(symbol)
    last = data.get("last_symbol")
    if last is not None and not _valid(last):
        log.warning("Dropping invalid last_symbol %r", last)
        last = None
    return AppState(tuple(clean[:MAX_SYMBOLS]), last)


def _valid(symbol: object) -> bool:
    return isinstance(symbol, str) and SYMBOL_RE.match(symbol) is not None


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "watchlist.json"

    def load(self) -> LoadResult:
        if not self.path.exists():
            return LoadResult(AppState())
        try:
            return LoadResult(_parse(json.loads(self.path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            log.warning("Unreadable watchlist %s (%s); starting empty", self.path, exc)
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
            except OSError:
                log.exception("Could not move aside %s", self.path)
            return LoadResult(AppState(), recovered=True)

    def save(self, state: AppState) -> None:
        payload = json.dumps(
            {"version": VERSION, "symbols": list(state.symbols), "last_symbol": state.last_symbol},
            indent=2,
        )
        try:
            atomic_write_text(self.path, payload)
        except OSError as exc:
            log.exception("Could not save watchlist to %s", self.path)
            raise StoreError(str(exc)) from exc
