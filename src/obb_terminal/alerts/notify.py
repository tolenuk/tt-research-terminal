"""Desktop notifications: `notify-send` (libnotify) on Linux, a toast through Windows
PowerShell on Windows, `osascript` on macOS. Never raises."""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger(__name__)

APP_NAME = "TT Research Terminal"
TIMEOUT_S = 3.0
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
TOAST_TIMEOUT_S = 20.0  # a cold PowerShell start can take several seconds
# Windows PowerShell's own registered app id, so a toast needs no Start-menu registration.
TOAST_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"
# Title and body arrive base64-encoded and become XML text nodes: never PowerShell or XML source.
TOAST_SCRIPT = """$ErrorActionPreference = 'Stop'
function Text($b64) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)) }
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($xml.CreateTextNode((Text '{title}'))) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode((Text '{body}'))) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""

# Title and body arrive as `argv` items, so they are data to AppleScript, never its source.
OSASCRIPT_LINES = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)

NotifyRunner = Callable[[list[str]], int]


class Notifier(Protocol):
    def notify(self, title: str, body: str) -> bool: ...


def pango_escape(text: str) -> str:
    """notify-send bodies are Pango markup: escape & first, then < and >."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _run(argv: list[str]) -> int:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=TOAST_TIMEOUT_S if IS_WINDOWS else TIMEOUT_S,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).returncode


def default_notifier() -> Notifier:
    if IS_WINDOWS:
        return WindowsToastNotifier()
    if IS_MACOS:
        return MacNotifier()
    return DesktopNotifier()


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def toast_argv(title: str, body: str) -> list[str]:
    script = TOAST_SCRIPT.replace("{title}", _b64(title)).replace("{body}", _b64(body))
    script = script.replace("{app_id}", TOAST_APP_ID)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-EncodedCommand", encoded,
    ]


def osascript_argv(title: str, body: str) -> list[str]:
    """osascript stops reading options at the first plain argument, the title: so the title
    must not start with '-' (the body may)."""
    if title.startswith("-"):
        title = " " + title
    script = [part for line in OSASCRIPT_LINES for part in ("-e", line)]
    return ["osascript", *script, title, body]


class _CommandNotifier:
    """Runs one command per notification. `tool` must be on PATH; its absence is logged once."""

    tool = ""
    label = ""

    def __init__(
        self,
        runner: NotifyRunner | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._runner = runner or _run
        self._which = which
        self._lock = threading.Lock()
        self._missing_logged = False

    def argv(self, title: str, body: str) -> list[str]:
        raise NotImplementedError

    def notify(self, title: str, body: str) -> bool:
        if self._which(self.tool) is None:
            with self._lock:
                if not self._missing_logged:
                    log.warning("%s not found; desktop alerts are disabled", self.tool)
                    self._missing_logged = True
            return False
        try:
            code = self._runner(self.argv(title, body))
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("%s failed: %s", self.label, exc)
            return False
        if code != 0:
            log.warning("%s exited with %s", self.label, code)
            return False
        return True


class DesktopNotifier(_CommandNotifier):
    tool = label = "notify-send"

    def argv(self, title: str, body: str) -> list[str]:
        return ["notify-send", "--app-name", APP_NAME, "--", title, pango_escape(body)]


class WindowsToastNotifier(_CommandNotifier):
    """A Windows toast via the built-in Windows PowerShell 5.1 (it can load the WinRT types)."""

    tool = "powershell.exe"
    label = "Toast notification"

    def argv(self, title: str, body: str) -> list[str]:
        return toast_argv(title, body)


class MacNotifier(_CommandNotifier):
    """A Notification Centre banner via AppleScript. macOS shows it as from Script Editor."""

    tool = label = "osascript"

    def argv(self, title: str, body: str) -> list[str]:
        return osascript_argv(title, body)
