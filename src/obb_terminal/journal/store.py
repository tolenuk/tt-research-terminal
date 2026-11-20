"""The journal database: $XDG_DATA_HOME/obb-terminal/journal.sqlite3 (WAL, user_version 1).

Only `Journal` touches the file. Each call opens its own short-lived connection and runs in one
`BEGIN IMMEDIATE` transaction, so the app's threads and the background checker can share it. Only
a corrupt file, or one that is not a journal, is moved to a timestamped `.bad` and replaced; a
busy or newer-version file raises JournalError and is left alone.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from obb_terminal.journal.models import HORIZONS, Kind, Outcome, OutcomeStatus, Row, Snapshot
from obb_terminal.journal.scoring import due_after_days
from obb_terminal.store import data_dir

log = logging.getLogger(__name__)

USER_VERSION = 1
BUSY_TIMEOUT_MS = 2000
_CORRUPT_CODES = (sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT)
_BUSY_CODES = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
# Creating the file races other instances, and on Windows switching it to WAL can report
# "locked" at once instead of waiting, so the first open retries for a while.
PREPARE_ATTEMPTS = 20
PREPARE_RETRY_S = 0.1
_SNAPSHOT_FIELDS = (
    "symbol", "day", "kind", "recorded_at", "price", "currency", "benchmark", "direction", "strength",
    "outlook", "band", "margin", "fair_value", "components", "stance", "action", "conviction",
    "entry_low", "entry_high", "thesis_break", "target_price", "model",
)
_OUTCOME_FIELDS = (
    "snapshot_id", "horizon", "status", "reason", "start_date", "end_date", "start_price", "end_price",
    "bench_start", "bench_end", "ret", "bench_ret", "excess", "adverse", "evaluated_at",
)
_SCHEMA = f"""
CREATE TABLE snapshots(
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL, day TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ({", ".join(f"'{k.value}'" for k in Kind)})),
    recorded_at TEXT NOT NULL, price REAL NOT NULL, currency TEXT NOT NULL, benchmark TEXT NOT NULL,
    direction INTEGER NOT NULL, strength REAL, outlook REAL, band TEXT, margin REAL, fair_value REAL,
    components TEXT NOT NULL, stance TEXT, action TEXT, conviction TEXT,
    entry_low REAL, entry_high REAL, thesis_break REAL, target_price REAL, model TEXT,
    UNIQUE(symbol, day, kind)
);
CREATE TABLE outcomes(
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    horizon INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('done', 'unable')), reason TEXT,
    start_date TEXT, end_date TEXT, start_price REAL, end_price REAL, bench_start REAL, bench_end REAL,
    ret REAL, bench_ret REAL, excess REAL, adverse REAL, evaluated_at TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, horizon)
);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX snapshots_day ON snapshots(day);
PRAGMA user_version = {USER_VERSION};
"""


class JournalError(Exception):
    """The journal could not be read or written (the cause is logged)."""


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _snapshot_values(s: Snapshot) -> tuple:
    return (
        s.symbol, s.day.isoformat(), s.kind.value, s.recorded_at.isoformat(), s.price, s.currency, s.benchmark,
        s.direction, s.strength, s.outlook, s.band, s.margin, s.fair_value, json.dumps(list(s.components)),
        s.stance, s.action, s.conviction, s.entry_low, s.entry_high, s.thesis_break, s.target_price, s.model,
    )


def _snapshot(row: sqlite3.Row) -> Snapshot:
    components = json.loads(row["components"])
    if not isinstance(components, list):
        raise TypeError("components must be a list")
    return Snapshot(
        symbol=row["symbol"], day=date.fromisoformat(row["day"]), kind=Kind(row["kind"]),
        recorded_at=datetime.fromisoformat(row["recorded_at"]), price=row["price"], currency=row["currency"],
        benchmark=row["benchmark"], direction=int(row["direction"]), strength=row["strength"],
        outlook=row["outlook"], band=row["band"], margin=row["margin"], fair_value=row["fair_value"],
        components=tuple((str(k), float(v)) for k, v in components), stance=row["stance"],
        action=row["action"], conviction=row["conviction"], entry_low=row["entry_low"],
        entry_high=row["entry_high"], thesis_break=row["thesis_break"], target_price=row["target_price"],
        model=row["model"], id=row["id"],
    )


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _outcome(row: sqlite3.Row) -> Outcome:
    return Outcome(
        snapshot_id=row["snapshot_id"], horizon=row["horizon"], status=OutcomeStatus(row["status"]),
        reason=row["reason"], start_date=_date(row["start_date"]), end_date=_date(row["end_date"]),
        start_price=row["start_price"], end_price=row["end_price"], bench_start=row["bench_start"],
        bench_end=row["bench_end"], ret=row["ret"], bench_ret=row["bench_ret"], excess=row["excess"],
        adverse=row["adverse"], evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
    )


def _outcome_values(o: Outcome) -> tuple:
    return (
        o.snapshot_id, o.horizon, o.status.value, o.reason, _iso(o.start_date), _iso(o.end_date), o.start_price,
        o.end_price, o.bench_start, o.bench_end, o.ret, o.bench_ret, o.excess, o.adverse, o.evaluated_at.isoformat(),
    )


class Journal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "journal.sqlite3"
        self.recovered = False  # a corrupt or unknown file was moved aside
        self._ready = False
        self._init_lock = threading.Lock()

    # --- writes ---------------------------------------------------------------------------

    def record(self, s: Snapshot) -> int | None:
        """Score snapshots keep the first of the day (returns None for a repeat); AI and debate
        calls replace that day's call and drop its outcomes. Returns the snapshot id."""
        values = _snapshot_values(s)
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT id FROM snapshots WHERE symbol = ? AND day = ? AND kind = ?",
                (s.symbol, s.day.isoformat(), s.kind.value),
            ).fetchone()
            if existing is None:
                cols = ", ".join(_SNAPSHOT_FIELDS)
                marks = ", ".join("?" for _ in _SNAPSHOT_FIELDS)
                cur = conn.execute(f"INSERT INTO snapshots({cols}) VALUES ({marks})", values)
                return cur.lastrowid
            if s.kind is Kind.SCORE:
                return None
            sid = existing["id"]
            sets = ", ".join(f"{f} = ?" for f in _SNAPSHOT_FIELDS)
            conn.execute(f"UPDATE snapshots SET {sets} WHERE id = ?", (*values, sid))
            conn.execute("DELETE FROM outcomes WHERE snapshot_id = ?", (sid,))
            return sid

    def write_outcomes(self, outcomes: Iterable[Outcome]) -> None:
        cols = ", ".join(_OUTCOME_FIELDS)
        marks = ", ".join("?" for _ in _OUTCOME_FIELDS)
        with self._tx() as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO outcomes({cols}) VALUES ({marks})",
                [_outcome_values(o) for o in outcomes],
            )

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    # --- reads ----------------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        with self._tx() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def counts(self) -> tuple[int, int]:
        """(snapshots, outcomes)."""
        with self._tx() as conn:
            snapshots = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            outcomes = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        return snapshots, outcomes

    def rows(self) -> list[Row]:
        return self._rows("ORDER BY day, id", ())

    def recent(self, limit: int) -> list[Row]:
        return self._rows("ORDER BY day DESC, id DESC LIMIT ?", (limit,))

    def due(self, today: date) -> list[tuple[Snapshot, tuple[int, ...]]]:
        """Snapshots with a horizon that has no outcome yet and has surely elapsed by `today`."""
        oldest_wait = min(due_after_days(h) for h in HORIZONS)
        cutoff = (today - timedelta(days=oldest_wait)).isoformat()
        out = []
        for s, outcomes in self._rows("WHERE day <= ? ORDER BY day, id", (cutoff,)):
            horizons = tuple(
                h for h in HORIZONS if h not in outcomes and s.day <= today - timedelta(days=due_after_days(h))
            )
            if horizons:
                out.append((s, horizons))
        return out

    # --- internals ------------------------------------------------------------------------

    def _rows(self, tail: str, params: tuple) -> list[Row]:
        with self._tx() as conn:
            snaps = conn.execute(f"SELECT * FROM snapshots {tail}", params).fetchall()
            ids = [r["id"] for r in snaps]
            outcomes: dict[int, dict[int, Outcome]] = {}
            for chunk in range(0, len(ids), 500):
                part = ids[chunk : chunk + 500]
                marks = ", ".join("?" for _ in part)
                for r in conn.execute(f"SELECT * FROM outcomes WHERE snapshot_id IN ({marks})", part):
                    try:
                        o = _outcome(r)
                    except (TypeError, ValueError) as exc:
                        log.warning("Skipping unreadable outcome for snapshot %s (%s)", r["snapshot_id"], exc)
                        continue
                    outcomes.setdefault(o.snapshot_id, {})[o.horizon] = o
        result: list[Row] = []
        for r in snaps:
            try:
                s = _snapshot(r)
            except (TypeError, ValueError, KeyError) as exc:
                log.warning("Skipping unreadable journal snapshot %s (%s)", r["id"], exc)
                continue
            result.append((s, outcomes.get(r["id"], {})))
        return result

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One connection and transaction; sqlite errors become JournalError."""
        self._ensure()
        try:
            conn = self._connect()
        except sqlite3.Error as exc:
            log.exception("Could not open %s", self.path)
            raise JournalError(str(exc)) from exc
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            conn.commit()
        except sqlite3.Error as exc:
            log.exception("Journal operation failed on %s", self.path)
            raise JournalError(str(exc)) from exc
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure(self) -> None:
        with self._init_lock:
            if self._ready:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._open_or_create_retrying()
            except (OSError, sqlite3.Error, JournalError) as exc:
                log.warning("Could not prepare %s (%s)", self.path, exc)
                raise exc if isinstance(exc, JournalError) else JournalError(str(exc)) from exc
            self._ready = True

    def _open_or_create_retrying(self) -> None:
        for attempt in range(PREPARE_ATTEMPTS):
            try:
                self._open_or_create()
                return
            except sqlite3.OperationalError as exc:
                busy = (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in _BUSY_CODES
                if not busy or attempt == PREPARE_ATTEMPTS - 1:
                    raise
                time.sleep(PREPARE_RETRY_S)

    def _open_or_create(self) -> None:
        """Raises sqlite3.Error (busy, I/O) or JournalError (newer version); _ready stays False
        so the next call retries."""
        try:
            version = self._version()
        except sqlite3.DatabaseError as exc:
            if (exc.sqlite_errorcode & 0xFF) not in _CORRUPT_CODES:
                raise
            log.warning("Journal %s is corrupt (%s); starting a new one", self.path, exc)
            self._quarantine()
            version = 0
        if version > USER_VERSION:
            raise JournalError(f"journal version {version} is newer than this app understands")
        if version < 0:
            log.warning("%s is not a TT Research Terminal journal; starting a new one", self.path)
            self._quarantine()
            version = 0
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            if version == 0:
                conn.execute("BEGIN IMMEDIATE")
                # Another process or instance may have created it since we looked.
                if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
                    for statement in filter(str.strip, _SCHEMA.split(";")):
                        conn.execute(statement)
                conn.commit()
        finally:
            conn.close()

    def _version(self) -> int:
        if not self.path.exists():
            return 0
        conn = self._connect()
        try:
            conn.execute("BEGIN")  # one snapshot: a creation committing mid-read is not "foreign"
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
            conn.rollback()
            return -1 if version == 0 and tables else int(version)  # -1: tables but no version
        finally:
            conn.close()

    def _quarantine(self) -> None:
        self.recovered = True
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.path.replace(self.path.with_name(f"{self.path.name}.{stamp}.bad"))
        for suffix in ("-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
