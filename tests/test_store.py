import json

import pytest

from obb_terminal.store import AppState, Store, StoreError, data_dir


def test_missing_file_gives_empty_state(tmp_path):
    result = Store(tmp_path / "watchlist.json").load()
    assert result.state == AppState()
    assert result.recovered is False


def test_round_trip(tmp_path):
    store = Store(tmp_path / "sub" / "watchlist.json")
    store.save(AppState(("AAPL", "SHEL.L"), "PETR4.SA"))
    assert store.load().state == AppState(("AAPL", "SHEL.L"), "PETR4.SA")
    assert json.loads(store.path.read_text())["version"] == 1
    assert list(store.path.parent.glob(".watchlist.*")) == []  # no temp files left


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"version": 2, "symbols": []}),
        json.dumps({"version": 1, "symbols": "AAPL"}),
        json.dumps(["AAPL"]),
    ],
)
def test_corrupt_file_is_moved_aside(tmp_path, content):
    path = tmp_path / "watchlist.json"
    path.write_text(content)
    result = Store(path).load()
    assert result.state == AppState()
    assert result.recovered is True
    assert (tmp_path / "watchlist.json.bad").read_text() == content
    assert not path.exists()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({"version": 1, "symbols": ["AAPL", "../../etc", 5, "AAPL,MSFT", "SPY"]}, AppState(("AAPL", "SPY"))),
        ({"version": 1, "symbols": ["AAPL"], "last_symbol": 5}, AppState(("AAPL",))),
        ({"version": 1, "symbols": ["AAPL"], "last_symbol": "AAPL,MSFT"}, AppState(("AAPL",))),
    ],
)
def test_invalid_entries_are_dropped_and_file_kept(tmp_path, content, expected):
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps(content))
    result = Store(path).load()
    assert result.state == expected
    assert result.recovered is False
    assert path.exists()
    assert not (tmp_path / "watchlist.json.bad").exists()


def test_duplicates_removed_in_order(tmp_path):
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps({"version": 1, "symbols": ["AAPL", "SPY", "AAPL"]}))
    assert Store(path).load().state.symbols == ("AAPL", "SPY")


def test_save_failure_raises_store_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StoreError):
        Store(blocker / "watchlist.json").save(AppState(("AAPL",)))


def test_data_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert data_dir() == tmp_path / "obb-terminal"


def test_pathologically_nested_file_is_moved_aside(tmp_path):
    path = tmp_path / "watchlist.json"
    content = "[" * 200_000 + "]" * 200_000
    path.write_text(content)
    result = Store(path).load()
    assert result.state == AppState()
    assert result.recovered is True
    assert (tmp_path / "watchlist.json.bad").exists()
