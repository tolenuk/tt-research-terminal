import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta

import pytest

from obb_terminal.journal.models import Kind, Outcome, OutcomeStatus
from obb_terminal.journal.store import Journal, JournalError
from tests.journal.samples import DAY0, NOW, snap


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "journal.sqlite3")


def outcome(sid, h=20, excess=0.05):
    return Outcome(sid, h, OutcomeStatus.DONE, None, DAY0, DAY0 + timedelta(days=28), 100.0, 110.0, 10.0, 10.5,
                   0.10, 0.05, excess, 0.02, NOW)


def test_score_snapshots_keep_the_first_of_the_day(journal):
    first = journal.record(snap(price=100.0))
    again = journal.record(snap(price=105.0))
    assert first is not None and again is None
    [(s, outcomes)] = journal.rows()
    assert s.price == 100.0 and s.id == first and outcomes == {}
    assert s.components == (("upside", 80.0), ("momentum", 60.0))
    journal.record(snap(day=DAY0 + timedelta(days=1)))
    assert journal.counts() == (2, 0)


def test_ai_snapshots_keep_the_latest_and_drop_its_outcomes(journal):
    sid = journal.record(snap(kind=Kind.AI, action="buy", direction=1, strength=3.0))
    journal.write_outcomes([outcome(sid)])
    assert journal.counts() == (1, 1)
    again = journal.record(snap(kind=Kind.AI, action="avoid", direction=-1, strength=-1.0))
    assert again == sid
    [(s, outcomes)] = journal.rows()
    assert (s.action, s.direction, s.strength) == ("avoid", -1, -1.0) and outcomes == {}


def test_kinds_are_separate_rows(journal):
    for kind in Kind:
        journal.record(snap(kind=kind))
    assert journal.counts() == (3, 0)


def test_outcomes_round_trip_and_replace(journal):
    sid = journal.record(snap())
    journal.write_outcomes([outcome(sid), outcome(sid, h=60)])
    journal.write_outcomes([outcome(sid, excess=-0.01)])
    [(_, outcomes)] = journal.rows()
    assert outcomes[20] == outcome(sid, excess=-0.01) and outcomes[60] == outcome(sid, h=60)


def test_due_lists_snapshots_old_enough_with_missing_horizons(journal):
    old = journal.record(snap(symbol="AAPL", day=date(2026, 1, 5)))
    journal.record(snap(symbol="MSFT", day=date(2026, 10, 20)))
    journal.write_outcomes([outcome(old, h=20)])
    due = journal.due(date(2026, 4, 1))  # 86 days later: 20 (done) and 60 are due, 120 not
    assert [(s.symbol, hs) for s, hs in due] == [("AAPL", (60,))]
    assert journal.due(date(2026, 1, 6)) == []


def test_recent_is_newest_first_with_a_limit(journal):
    for i in range(5):
        journal.record(snap(symbol=f"S{i}", day=DAY0 + timedelta(days=i)))
    assert [s.symbol for s, _ in journal.recent(3)] == ["S4", "S3", "S2"]


def test_meta(journal):
    assert journal.get_meta("last_scored_day") is None
    journal.set_meta("last_scored_day", "2026-10-27")
    assert journal.get_meta("last_scored_day") == "2026-10-27"


def test_wal_and_version(journal):
    journal.counts()
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_corrupt_file_is_moved_aside_and_recreated(tmp_path):
    path = tmp_path / "journal.sqlite3"
    path.write_bytes(b"this is not a database" * 100)
    j = Journal(path)
    assert j.counts() == (0, 0)
    assert len(list(tmp_path.glob("journal.sqlite3.*.bad"))) == 1
    assert j.recovered
    path.write_bytes(b"corrupt again" * 100)
    assert Journal(path).counts() == (0, 0)
    assert len(list(tmp_path.glob("journal.sqlite3.*.bad"))) == 2  # an earlier .bad is never overwritten


def test_foreign_file_is_moved_aside_but_a_newer_version_is_left_alone(tmp_path):
    path = tmp_path / "journal.sqlite3"
    with closing(sqlite3.connect(path)) as conn:  # Windows cannot move a file left open
        conn.execute("CREATE TABLE other(x)")
    assert Journal(path).counts() == (0, 0)
    assert len(list(tmp_path.glob("*.bad"))) == 1
    newer = tmp_path / "newer.sqlite3"
    with closing(sqlite3.connect(newer)) as conn:
        conn.execute("PRAGMA user_version = 9")
    with pytest.raises(JournalError):
        Journal(newer).counts()
    assert newer.exists() and not list(tmp_path.glob("newer*.bad"))


def test_a_locked_journal_is_an_error_not_corruption(journal):
    sid = journal.record(snap())
    other = Journal(journal.path)
    holder = sqlite3.connect(journal.path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(JournalError):
            other.counts()
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert not list(journal.path.parent.glob("*.bad")) and not other.recovered
    assert other.rows()[0][0].id == sid  # retried cleanly once the lock was gone


def test_concurrent_ai_records_keep_one_row(journal):
    import threading

    others = [Journal(journal.path) for _ in range(6)]
    errors = []

    def write(j, n):
        try:
            j.record(snap(kind=Kind.AI, strength=float(n)))
        except JournalError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(j, n)) for n, j in enumerate(others)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and journal.counts() == (1, 0)


def test_bad_rows_are_skipped_on_read(journal):
    journal.record(snap())
    with sqlite3.connect(journal.path) as conn:
        conn.execute("UPDATE snapshots SET components = 'not json'")
    assert journal.rows() == []


def test_unwritable_location_raises_journal_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(JournalError):
        Journal(blocker / "journal.sqlite3").record(snap())


def test_snapshot_round_trips_every_field(journal):
    full = snap(kind=Kind.DEBATE, band="favourable", margin=0.12, fair_value=120.5, stance="bullish",
                action="accumulate", conviction="high", entry_low=90.0, entry_high=95.0, thesis_break=80.0,
                target_price=130.0, model="claude-opus-5-5", currency="GBP", benchmark="^FTAS")
    sid = journal.record(full)
    [(s, _)] = journal.rows()
    assert s == replace(full, id=sid)


def test_a_busy_first_open_is_retried(journal, monkeypatch):
    from obb_terminal.journal import store as store_mod

    real = Journal._open_or_create
    calls = []

    def flaky(self):
        calls.append(1)
        if len(calls) < 3:
            exc = sqlite3.OperationalError("database is locked")
            exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise exc
        real(self)

    monkeypatch.setattr(Journal, "_open_or_create", flaky)
    monkeypatch.setattr(store_mod, "PREPARE_RETRY_S", 0)
    assert journal.counts() == (0, 0) and len(calls) == 3
