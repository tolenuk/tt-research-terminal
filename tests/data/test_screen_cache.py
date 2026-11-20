from datetime import UTC, datetime

from obb_terminal.data.screen_cache import load_snapshot, save_snapshot
from obb_terminal.domain.cheap_screen import MarketSnapshot, ScreenStock

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
PETR = ScreenStock(
    ticker="PETR4",
    name="PETROBRAS",
    price=44.7,
    pl=4.1,
    pvp=1.14,
    ev_ebit=3.34,
    ebit_margin=0.45,
    roic=0.21,
    liquidity=2.7e9,
)
BP = ScreenStock(
    ticker="BP.L",
    name="BP p.l.c.",
    price=5.75,
    pl=23.0,
    pvp=1.3,
    ev_ebit=None,
    ebit_margin=None,
    roic=None,
    liquidity=2.7e8,
    company="BP p.l.c.",
)


def test_b3_snapshot_round_trips(tmp_path):
    snap = MarketSnapshot((PETR,), frozenset({"BBAS3"}), NOW)
    path = tmp_path / "screener" / "b3.json"
    save_snapshot(path, snap)
    assert load_snapshot(path) == snap


def test_presorted_snapshot_with_missing_figures_round_trips(tmp_path):
    snap = MarketSnapshot((BP,), frozenset(), NOW, presorted=True)
    path = tmp_path / "uk.json"
    save_snapshot(path, snap)
    assert load_snapshot(path) == snap


def test_missing_corrupt_or_old_format_snapshot_loads_as_none(tmp_path):
    assert load_snapshot(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_snapshot(bad) is None
    old = tmp_path / "old.json"
    old.write_text('{"version": 1, "stocks": []}', encoding="utf-8")
    assert load_snapshot(old) is None
