"""Pick the background-alerts installer for this platform."""

from __future__ import annotations

import sys

from obb_terminal.alerts.systemd import Installer, SystemdInstaller

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"


def default_installer() -> Installer:
    if IS_WINDOWS:
        from obb_terminal.alerts.schtasks import SchtasksInstaller

        return SchtasksInstaller()
    if IS_MACOS:
        from obb_terminal.alerts.launchd import LaunchdInstaller

        return LaunchdInstaller()
    return SystemdInstaller()
