import json
import threading
from datetime import UTC, datetime

import pytest

from obb_terminal.alerts.store import (
    AlertsStatus,
    StatusStore,
    TargetsLoad,
    TargetsRepo,
    decode,
    encode,
)
from obb_terminal.domain.targets import AlertState, Indicator, SymbolTargets, Target, TargetsFile
from obb_terminal.fsutil import LockBusy

NOW = datetime(2026, 10, 13, 14, 15, tzinfo=UTC)

SAMPLE = TargetsFile((
    SymbolTargets("AAPL", (Target(Indicator.PE, 25.0),
                           Target(Indicator.DIV_YIELD, 0.01, False, AlertState.FIRED, NOW)),
                  True, NOW),
    SymbolTargets("SHEL.L", (Target(Indicator.EV_EBITDA, 4.0),)),
))


@pytest.fixture
def repo(tmp_path):
    return TargetsRepo(tmp_path / "targets.json")


def test_encode_decode_round_trip():
    assert decode(json.loads(encode(SAMPLE))) == SAMPLE


def test_encoded_shape():
    doc = json.loads(encode(SAMPLE))
    assert doc["version"] == 1
    assert doc["symbols"]["AAPL"]["targets"]["div_yield"] == {
        "level": 0.01, "enabled": False, "state": "fired", "fired_at": "2026-10-13T14:15:00+00:00"}
    assert doc["symbols"]["SHEL.L"] == {
        "targets": {"ev_ebitda": {"level": 4.0, "enabled": True, "state": "armed", "fired_at": None}},
        "all_fired": False, "observed_at": None}


def test_missing_file_is_empty(repo):
    assert repo.load() == TargetsLoad(TargetsFile(), False)


def test_commit_then_load(repo):
    with repo.transaction() as txn:
        assert txn.file == TargetsFile() and not txn.recovered
        txn.commit(SAMPLE)
        assert txn.file == SAMPLE
    assert repo.load().file == SAMPLE
    assert sorted(p.name for p in repo.path.parent.iterdir()) == ["targets.json", "targets.json.lock"]


def test_corrupt_file_load_does_not_move_it_but_transaction_does(repo):
    repo.path.write_text("{not json")
    assert repo.load() == TargetsLoad(TargetsFile(), True)
    assert repo.path.exists()
    with repo.transaction() as txn:
        assert txn.recovered and txn.file == TargetsFile()
    assert not repo.path.exists()
    assert repo.path.with_name("targets.json.bad").read_text() == "{not json"


@pytest.mark.parametrize("doc", [[], {"version": 2, "symbols": {}}, {"version": 1, "symbols": []}])
def test_wrong_version_or_shape_is_corrupt(repo, doc):
    repo.path.write_text(json.dumps(doc))
    assert repo.load().recovered


def test_invalid_entries_are_dropped():
    doc = {"version": 1, "symbols": {
        "bad symbol!": {"targets": {"pe": {"level": 25}}},
        "AAPL": {"targets": {
            "pe": {"level": 25},
            "roe": {"level": 1},
            "pb": {"level": -1},
            "price": {"level": True},
            "div_yield": {"level": 1.5},
            "fcf_yield": {"level": 0.05, "state": "exploded"},
            "ev_ebitda": {"level": 4, "enabled": "yes"},
        }},
        "MSFT": {"targets": {"pe": {"level": 30}}, "all_fired": "nope"},
        "SPY": "not an object",
    }}
    f = decode(doc)
    assert [s.symbol for s in f.symbols] == ["AAPL"]
    assert f.get("AAPL").targets == (Target(Indicator.PE, 25.0),)


def test_naive_timestamps_are_read_as_utc():
    doc = {"version": 1, "symbols": {"AAPL": {"targets": {"pe": {"level": 25, "state": "fired",
                                                                   "fired_at": "2026-10-13T14:15:00"}}}}}
    assert decode(doc).get("AAPL").targets[0].fired_at == NOW


def test_second_transaction_times_out_while_first_holds(repo):
    entered, release = threading.Event(), threading.Event()

    def holder():
        with repo.transaction():
            entered.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(LockBusy), repo.transaction(timeout_s=0.1):
            pass
    finally:
        release.set()
        thread.join(2)


def test_failed_commit_keeps_old_file(repo, monkeypatch):
    with repo.transaction() as txn:
        txn.commit(SAMPLE)

    def broken(path, text):
        raise OSError("disk full")

    monkeypatch.setattr("obb_terminal.alerts.store.atomic_write_text", broken)
    with pytest.raises(OSError), repo.transaction() as txn:
        txn.commit(TargetsFile())
    assert repo.load().file == SAMPLE


def test_huge_level_is_dropped_not_fatal(repo):
    repo.path.write_text(
        json.dumps({"version": 1, "symbols": {"AAPL": {"targets": {
            "pe": {"level": 10**400}, "pb": {"level": 3}}}}}))
    load = repo.load()
    assert not load.recovered
    assert load.file.get("AAPL").targets == (Target(Indicator.PB, 3.0),)


@pytest.fixture
def unreadable(repo, monkeypatch):
    repo.path.write_text(json.dumps({"version": 1, "symbols": {}}))
    original = type(repo.path).read_text

    def read_text(self, *args, **kwargs):
        if self == repo.path:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.read_text", read_text)
    return repo


def test_unreadable_file_loads_empty_untouched(unreadable):
    before = unreadable.path.read_bytes()
    assert unreadable.load() == TargetsLoad(TargetsFile(), False, "targets unreadable")
    assert unreadable.path.read_bytes() == before
    assert not unreadable.path.with_name("targets.json.bad").exists()


def test_unreadable_file_fails_transaction_untouched(unreadable):
    before = unreadable.path.read_bytes()
    with pytest.raises(PermissionError), unreadable.transaction():
        pytest.fail("must not yield")
    assert unreadable.path.read_bytes() == before
    assert not unreadable.path.with_name("targets.json.bad").exists()


def test_every_quarantined_copy_is_kept(repo):
    for content in ("{first", "{second"):
        repo.path.write_text(content)
        with repo.transaction() as txn:
            assert txn.recovered
    assert repo.path.with_name("targets.json.bad").read_text() == "{first"
    assert repo.path.with_name("targets.json.bad.1").read_text() == "{second"


def test_status_round_trip_and_garbage(tmp_path):
    store = StatusStore(tmp_path / "alerts-status.json")
    assert store.read() is None
    status = AlertsStatus(NOW, False, 3, 1, 0, 2, "offline")
    store.write(status)
    assert store.read() == status
    store.path.write_text('{"version": 1, "last_run_at": "x"}')
    assert store.read() is None
    store.path.write_text('{"version": 1, "last_run_at": "2026-10-13T14:15:00+00:00", "ok": true,'
                          ' "checked": true, "failed": 0, "skipped": 0, "fired": 0}')
    assert store.read() is None
    store.path.write_text("[1, 2")
    assert store.read() is None


def test_thesis_break_round_trips_and_is_optional():
    from obb_terminal.domain.targets import ThesisBreak

    f = TargetsFile((
        SymbolTargets("AAPL", (Target(Indicator.PE, 25.0),), thesis=ThesisBreak(250.0, AlertState.FIRED, NOW)),
        SymbolTargets("MSFT", thesis=ThesisBreak(300.0)),
    ))
    doc = json.loads(encode(f))
    assert doc["version"] == 1
    assert doc["symbols"]["AAPL"]["thesis_break"] == {"level": 250.0, "state": "fired", "fired_at": NOW.isoformat()}
    assert "thesis_break" not in json.loads(encode(SAMPLE))["symbols"]["AAPL"]
    assert decode(doc) == f


def test_bad_thesis_break_is_dropped_but_targets_kept():
    doc = json.loads(encode(SAMPLE))
    doc["symbols"]["SHEL.L"]["thesis_break"] = {"level": -3}
    f = decode(doc)
    assert f.get("SHEL.L").thesis is None and f.get("SHEL.L").targets
