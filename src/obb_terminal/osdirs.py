"""Per-user base folders. XDG_* variables win everywhere (the checker and tests pin them);
otherwise Linux uses ~/.local/share and ~/.config, Windows %LOCALAPPDATA% and %APPDATA%, and
macOS ~/Library/Application Support for data and ~/.config for the hand-edited config.toml."""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"


def _base(
    xdg: str, windows_var: str, windows_default: str, posix_default: str, macos_default: str | None = None
) -> Path:
    override = os.environ.get(xdg)
    if override:
        return Path(override)
    if IS_WINDOWS:
        return Path(os.environ.get(windows_var) or Path.home() / windows_default)
    if IS_MACOS and macos_default:
        return Path.home() / macos_default
    return Path.home() / posix_default


def data_base() -> Path:
    """Machine-local data (journal, caches): %LOCALAPPDATA% on Windows, Application Support on macOS."""
    return _base(
        "XDG_DATA_HOME", "LOCALAPPDATA", "AppData/Local", ".local/share", "Library/Application Support"
    )


def config_base() -> Path:
    """Settings that may roam with the user: %APPDATA% on Windows."""
    return _base("XDG_CONFIG_HOME", "APPDATA", "AppData/Roaming", ".config")
