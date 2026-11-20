import json
from datetime import UTC, datetime

from obb_terminal.ai.models import CachedReport, ReportKind
from obb_terminal.ai.schema import parse_report
from obb_terminal.ai.store import ReportStore
from tests.ai.samples import ASSUMPTIONS, CALL, STRUCTURED

CREATED = datetime(2026, 10, 10, 9, 30, tzinfo=UTC)


def entry(symbol="AAPL", **changes):
    base = dict(symbol=symbol, report=parse_report(STRUCTURED), created_at=CREATED, model="claude-opus-5-5",
                effort="medium", assumptions=ASSUMPTIONS, cost_usd=0.44, duration_ms=18234)
    base.update(changes)
    return CachedReport(**base)


def test_missing_gives_none(tmp_path):
    assert ReportStore(tmp_path).get("AAPL") is None


def test_round_trip_and_latest_wins(tmp_path):
    store = ReportStore(tmp_path)
    store.put(entry())
    newer = entry(created_at=datetime(2026, 10, 11, tzinfo=UTC), cost_usd=None, duration_ms=None)
    store.put(newer)
    assert store.get("aapl") == newer
    data = json.loads((tmp_path / "AAPL.json").read_text())
    assert data["version"] == 2 and data["kind"] == "single" and data["assumptions"]["discount_rate"] == 0.09
    assert data["created_at"] == "2026-10-11T00:00:00+00:00"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AAPL.json"]


def test_corrupt_files_read_as_none(tmp_path):
    store = ReportStore(tmp_path)
    for content in ("{not json", json.dumps({"version": 2}), json.dumps(["x"]), "[" * 100_000):
        (tmp_path / "AAPL.json").write_text(content)
        assert store.get("AAPL") is None


def test_wrong_symbol_or_bad_report_reads_as_none(tmp_path):
    store = ReportStore(tmp_path)
    store.put(entry())
    data = json.loads((tmp_path / "AAPL.json").read_text())
    (tmp_path / "SPY.json").write_text(json.dumps(data))
    assert store.get("SPY") is None
    data["report"]["stance"] = "moon"
    (tmp_path / "AAPL.json").write_text(json.dumps(data))
    assert store.get("AAPL") is None


def test_invalid_symbol_never_touches_disk(tmp_path):
    store = ReportStore(tmp_path)
    assert store.path_for("../etc/passwd") is None
    assert store.get("../etc/passwd") is None
    try:
        store.put(entry(symbol="../x"))
    except OSError:
        pass
    assert list(tmp_path.iterdir()) == []


def test_default_root_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert ReportStore().root == tmp_path / "obb-terminal" / "analyses"


def test_version_1_files_still_load_without_a_call(tmp_path):
    store = ReportStore(tmp_path)
    store.put(entry())
    data = json.loads((tmp_path / "AAPL.json").read_text())
    data["version"] = 1
    del data["kind"]
    (tmp_path / "AAPL.json").write_text(json.dumps(data))
    loaded = store.get("AAPL")
    assert loaded is not None and loaded.kind is ReportKind.SINGLE and loaded.report.call is None


def test_kind_and_call_round_trip(tmp_path):
    store = ReportStore(tmp_path)
    report = parse_report({**STRUCTURED, "call": {**CALL, "notes": ["n"]}})
    e = entry(report=report, kind=ReportKind.DEBATE)
    store.put(e)
    assert store.get("AAPL") == e


def test_unknown_kind_reads_as_none(tmp_path):
    store = ReportStore(tmp_path)
    store.put(entry())
    data = json.loads((tmp_path / "AAPL.json").read_text())
    data["kind"] = "oracle"
    (tmp_path / "AAPL.json").write_text(json.dumps(data))
    assert store.get("AAPL") is None
