from pathlib import Path

from obb_terminal import osdirs
from obb_terminal.config import config_dir
from obb_terminal.store import data_dir


def test_linux_uses_xdg_with_home_fallbacks(monkeypatch, tmp_path):
    monkeypatch.setattr(osdirs, "IS_WINDOWS", False)
    monkeypatch.setattr(osdirs, "IS_MACOS", False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(osdirs.Path, "home", lambda: tmp_path)
    assert data_dir() == tmp_path / ".local" / "share" / "obb-terminal"
    assert config_dir() == tmp_path / ".config" / "obb-terminal"


def test_windows_uses_local_and_roaming_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(osdirs, "IS_WINDOWS", True)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert data_dir() == tmp_path / "Local" / "obb-terminal"
    assert config_dir() == tmp_path / "Roaming" / "obb-terminal"


def test_windows_without_appdata_falls_back_under_home(monkeypatch, tmp_path):
    monkeypatch.setattr(osdirs, "IS_WINDOWS", True)
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert data_dir() == tmp_path / "AppData" / "Local" / "obb-terminal"
    assert config_dir() == tmp_path / "AppData" / "Roaming" / "obb-terminal"


def test_xdg_overrides_win_on_every_platform(monkeypatch, tmp_path):
    """The alerts checker and the tests pin both folders through XDG_* everywhere."""
    monkeypatch.setattr(osdirs, "IS_WINDOWS", True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    assert data_dir() == tmp_path / "d" / "obb-terminal"
    assert config_dir() == tmp_path / "c" / "obb-terminal"


def test_macos_keeps_data_in_application_support_and_config_in_dot_config(monkeypatch, tmp_path):
    monkeypatch.setattr(osdirs, "IS_WINDOWS", False)
    monkeypatch.setattr(osdirs, "IS_MACOS", True)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(osdirs.Path, "home", lambda: tmp_path)
    assert data_dir() == tmp_path / "Library" / "Application Support" / "obb-terminal"
    assert config_dir() == tmp_path / ".config" / "obb-terminal"
