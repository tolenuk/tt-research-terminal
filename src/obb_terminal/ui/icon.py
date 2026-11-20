"""The application icon (Chart Loupe), loaded from the PNGs in ui/assets."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon

log = logging.getLogger(__name__)

ASSETS = Path(__file__).with_name("assets")
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
WINDOWS_APP_ID = "TTResearch.Terminal"


def app_icon() -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addFile(str(ASSETS / f"tt-research-{size}.png"))
    return icon


def set_windows_app_id() -> None:
    """Windows shows a pythonw process under Python's icon on the taskbar without its own app id."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except (AttributeError, OSError):  # only the taskbar icon suffers
        log.warning("Could not set the Windows app id", exc_info=True)
