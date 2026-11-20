"""Persisted buy targets (targets.json, guarded by an flock) and the checker's last-run status.

Only `AlertEngine` writes targets.json, always inside `TargetsRepo.transaction()`. Only the
checker writes alerts-status.json.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from obb_terminal.domain.targets import (
    AlertState,
    Indicator,
    SymbolTargets,
    Target,
    TargetsFile,
    ThesisBreak,
    validate_level,
)
from obb_terminal.fsutil import atomic_write_text, file_lock
from obb_terminal.store import SYMBOL_RE, data_dir

log = logging.getLogger(__name__)

VERSION = 1
LOCK_TIMEOUT_S = 5.0
TARGETS_UNREADABLE = "targets unreadable"


@dataclass(frozen=True, slots=True)
class TargetsLoad:
    file: TargetsFile
    recovered: bool = False  # the file was unreadable and was treated as empty
    error: str | None = None  # the file could not be read at all (OSError)


@dataclass(frozen=True, slots=True)
class AlertsStatus:
    last_run_at: datetime
    ok: bool
    checked: int
    failed: int
    skipped: int
    fired: int
    error: str | None = None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _encode_symbol(st: SymbolTargets) -> dict:
    out: dict = {
        "targets": {
            t.indicator.value: {
                "level": t.level,
                "enabled": t.enabled,
                "state": t.state.value,
                "fired_at": _iso(t.fired_at),
            }
            for t in st.targets
        },
        "all_fired": st.all_fired,
        "observed_at": _iso(st.observed_at),
    }
    if st.thesis is not None:
        out["thesis_break"] = {
            "level": st.thesis.level,
            "state": st.thesis.state.value,
            "fired_at": _iso(st.thesis.fired_at),
        }
    return out


def encode(f: TargetsFile) -> str:
    symbols = {st.symbol: _encode_symbol(st) for st in f.symbols}
    return json.dumps({"version": VERSION, "symbols": symbols}, indent=2, ensure_ascii=False)


def _target(key: object, raw: object) -> Target:
    indicator = Indicator(key)
    if not isinstance(raw, dict):
        raise TypeError("target must be an object")
    level = raw.get("level")
    if isinstance(level, bool) or not isinstance(level, int | float):
        raise TypeError("level must be a number")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise TypeError("enabled must be true or false")
    return Target(
        indicator,
        validate_level(indicator, level),
        enabled,
        AlertState(raw.get("state", AlertState.ARMED.value)),
        _parse_time(raw.get("fired_at")),
    )


def _symbol(symbol: str, raw: object) -> SymbolTargets:
    if not isinstance(raw, dict) or not isinstance(raw.get("targets", {}), dict):
        raise TypeError("symbol entry must be an object with a targets object")
    targets: dict[Indicator, Target] = {}
    for key, value in raw.get("targets", {}).items():
        try:
            target = _target(key, value)
        except (TypeError, ValueError) as exc:
            log.warning("Dropping invalid target %s/%r (%s)", symbol, key, exc)
            continue
        targets[target.indicator] = target
    all_fired = raw.get("all_fired", False)
    if not isinstance(all_fired, bool):
        raise TypeError("all_fired must be true or false")
    ordered = tuple(targets[i] for i in Indicator if i in targets)
    return SymbolTargets(
        symbol, ordered, all_fired, _parse_time(raw.get("observed_at")), _thesis(symbol, raw.get("thesis_break"))
    )


def _thesis(symbol: str, raw: object) -> ThesisBreak | None:
    if raw is None:
        return None
    try:
        if not isinstance(raw, dict):
            raise TypeError("thesis_break must be an object")
        level = raw.get("level")
        if isinstance(level, bool) or not isinstance(level, int | float):
            raise TypeError("level must be a number")
        return ThesisBreak(
            validate_level(Indicator.PRICE, level),
            AlertState(raw.get("state", AlertState.ARMED.value)),
            _parse_time(raw.get("fired_at")),
        )
    except (TypeError, ValueError) as exc:
        log.warning("Dropping invalid thesis break for %s (%s)", symbol, exc)
        return None


def decode(obj: object) -> TargetsFile:
    """Parse a targets document. A wrong version or shape raises ValueError/TypeError; a bad
    symbol, target or field is dropped with a warning so one stray value costs only itself."""
    if not isinstance(obj, dict) or obj.get("version") != VERSION:
        raise ValueError("unsupported targets version")
    symbols = obj.get("symbols")
    if not isinstance(symbols, dict):
        raise TypeError("symbols must be an object")
    f = TargetsFile()
    for symbol, raw in symbols.items():
        if not isinstance(symbol, str) or not SYMBOL_RE.match(symbol):
            log.warning("Dropping targets for invalid symbol %r", symbol)
            continue
        try:
            f = f.put(_symbol(symbol, raw))
        except (TypeError, ValueError) as exc:
            log.warning("Dropping invalid targets for %s (%s)", symbol, exc)
    return f


class Txn:
    """The file as read under the lock; `commit` writes a new version atomically (OSError)."""

    def __init__(self, path: Path, file: TargetsFile, recovered: bool) -> None:
        self._path = path
        self.file = file
        self.recovered = recovered

    def commit(self, new: TargetsFile) -> None:
        atomic_write_text(self._path, encode(new))
        self.file = new


class TargetsRepo:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "targets.json"
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def load(self) -> TargetsLoad:
        """Lock-free read for display. Never raises and never moves a file: a corrupt file
        loads as empty with recovered=True, a file that cannot be read (OSError) as empty with
        recovered=False and `error` set."""
        try:
            file, recovered = self._read(quarantine=False)
        except OSError as exc:
            log.warning("Cannot read targets %s (%s); showing none", self.path, exc)
            return TargetsLoad(TargetsFile(), recovered=False, error=TARGETS_UNREADABLE)
        return TargetsLoad(file, recovered)

    @contextmanager
    def transaction(self, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[Txn]:
        """Hold the lock, re-read the file and yield it. A corrupt file is quarantined and
        yielded as empty (recovered=True). Raises LockBusy or OSError; an unreadable file raises
        OSError so no commit can overwrite it."""
        with file_lock(self.lock_path, timeout_s):
            file, recovered = self._read(quarantine=True)
            yield Txn(self.path, file, recovered)

    def _read(self, *, quarantine: bool) -> tuple[TargetsFile, bool]:
        """(file, recovered). Missing -> empty. Content that cannot be decoded is corrupt:
        empty, recovered=True, and with `quarantine` moved aside. An OSError reading it
        propagates untouched: the data may be fine, so it is never moved."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return TargetsFile(), False
        try:
            return decode(json.loads(text)), False
        except (ValueError, TypeError, RecursionError) as exc:  # UnicodeDecodeError is a ValueError
            log.warning("Unreadable targets %s (%s); treating as empty", self.path, exc)
            if quarantine:
                self._quarantine()
            return TargetsFile(), True

    def _quarantine(self) -> None:
        """Move the corrupt file to targets.json.bad, or the first free .bad.N; never overwrite."""
        for n in range(1000):
            bad = self.path.with_name(self.path.name + (".bad" if n == 0 else f".bad.{n}"))
            if bad.exists():
                continue
            try:
                os.replace(self.path, bad)
            except OSError:
                log.exception("Could not move aside %s", self.path)
            return
        log.error("Too many quarantined copies of %s; leaving it in place", self.path)


class StatusStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "alerts-status.json"

    def read(self) -> AlertsStatus | None:
        """The last checker run, or None if there is none or it is unreadable. Never raises."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != VERSION:
                return None
            last = _parse_time(raw["last_run_at"])
            counts = [raw[k] for k in ("checked", "failed", "skipped", "fired")]
            if last is None or not isinstance(raw["ok"], bool):
                return None
            if any(isinstance(c, bool) or not isinstance(c, int) for c in counts):
                return None
            error = raw.get("error")
            return AlertsStatus(last, raw["ok"], *counts, error if isinstance(error, str) else None)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None

    def write(self, status: AlertsStatus) -> None:
        payload = {
            "version": VERSION,
            "last_run_at": status.last_run_at.isoformat(),
            "ok": status.ok,
            "checked": status.checked,
            "failed": status.failed,
            "skipped": status.skipped,
            "fired": status.fired,
            "error": status.error,
        }
        atomic_write_text(self.path, json.dumps(payload, indent=2))
