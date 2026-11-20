"""Install the background checker as a systemd user timer (Mon-Fri 07:00-21:45, every 15 min).

Every systemctl call goes through an injectable runner, so tests never touch the real one.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from obb_terminal.ai.runner import CompletedRun
from obb_terminal.alerts.store import AlertsStatus, StatusStore
from obb_terminal.fsutil import atomic_write_text

log = logging.getLogger(__name__)

UNIT = "obb-terminal-alerts"
SERVICE = f"{UNIT}.service"
TIMER = f"{UNIT}.timer"
SYSTEMCTL_TIMEOUT_S = 15.0
MAX_MESSAGE = 200
NO_BUS = "systemd user session unavailable"

SystemctlRunner = Callable[[list[str]], CompletedRun]


class InstallError(Exception):
    def __init__(self, user_message: str) -> None:
        user_message = user_message[:MAX_MESSAGE]
        super().__init__(user_message)
        self.user_message = user_message


@dataclass(frozen=True, slots=True)
class TimerStatus:
    installed: bool
    enabled: bool
    active: bool
    last: AlertsStatus | None
    error: str | None = None


class Installer(Protocol):
    def install(self) -> None: ...
    def uninstall(self) -> None: ...
    def status(self) -> TimerStatus: ...


def _home(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or str(Path.home() / default))


def data_home() -> Path:
    return _home("XDG_DATA_HOME", ".local/share")


def config_home() -> Path:
    return _home("XDG_CONFIG_HOME", ".config")


def alerts_executable() -> Path:
    """The console script next to this interpreter (the uv tool venv's bin). Not resolved:
    the venv's python is a symlink to the system interpreter."""
    return Path(sys.executable).parent / UNIT


def _unit_value(path: Path) -> str:
    text = str(path)
    if any(c in text for c in '"\\\n'):
        raise InstallError(f"Unsupported character in path: {text}")
    return text.replace("%", "%%")


def service_text(exec_path: Path, data_home: Path, config_home: Path) -> str:
    return (
        "[Unit]\n"
        "Description=TT Research Terminal watchlist alerts check\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f'ExecStart="{_unit_value(exec_path).replace("$", "$$")}"\n'
        f'Environment="XDG_DATA_HOME={_unit_value(data_home)}"\n'
        f'Environment="XDG_CONFIG_HOME={_unit_value(config_home)}"\n'
        "TimeoutStartSec=300\n"
        "Nice=10\n"
    )


def timer_text() -> str:
    return (
        "[Unit]\n"
        "Description=TT Research Terminal watchlist alerts (Mon-Fri 07:00-21:45, every 15 min)\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=Mon..Fri *-*-* 07..21:00/15\n"
        "Persistent=false\n"
        "AccuracySec=1min\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _run(argv: list[str]) -> CompletedRun:
    done = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=SYSTEMCTL_TIMEOUT_S,
        check=False,
    )
    return CompletedRun(done.returncode, done.stdout, done.stderr)


def _message(stderr: str) -> str:
    if "Failed to connect to bus" in stderr or "Failed to connect to user scope bus" in stderr:
        return NO_BUS
    line = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")
    return line or "systemctl failed"


class SystemdInstaller:
    def __init__(
        self,
        runner: SystemctlRunner | None = None,
        unit_dir: Path | None = None,
        which: Callable[[str], str | None] = shutil.which,
        exec_path: Path | None = None,
        status: StatusStore | None = None,
        data_dir: Path | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self._runner = runner or _run
        self._unit_dir = unit_dir
        self._which = which
        self._exec_path = exec_path
        self._status = status or StatusStore()
        self._data_home = data_dir
        self._config_home = config_dir

    @property
    def unit_dir(self) -> Path:
        return self._unit_dir or config_home() / "systemd" / "user"

    def install(self) -> None:
        """Write both units, reload, enable and start the timer. All or nothing: on any
        failure the units are disabled and removed again. Raises InstallError."""
        if self._which("systemctl") is None:
            raise InstallError("systemctl not found")
        exec_path = self._exec_path or alerts_executable()
        if not exec_path.exists():
            raise InstallError(f"{exec_path} not found — reinstall TT Research Terminal")
        service = service_text(
            exec_path, self._data_home or data_home(), self._config_home or config_home()
        )
        try:
            atomic_write_text(self.unit_dir / SERVICE, service)
            atomic_write_text(self.unit_dir / TIMER, timer_text())
            self._ctl("daemon-reload")
            self._ctl("enable", "--now", TIMER)
        except (InstallError, OSError) as exc:
            self._rollback()
            if isinstance(exc, InstallError):
                raise
            raise InstallError(f"Could not write timer files: {exc}") from exc
        log.info("Installed %s", TIMER)

    def uninstall(self) -> None:
        """Stop and remove the timer. Safe to repeat. Raises InstallError if a file cannot
        be removed."""
        has_systemctl = self._which("systemctl") is not None
        if has_systemctl:
            self._ctl_quiet("disable", "--now", TIMER)
        try:
            for name in (TIMER, SERVICE):
                (self.unit_dir / name).unlink(missing_ok=True)
        except OSError as exc:
            raise InstallError(f"Could not remove timer files: {exc}") from exc
        if has_systemctl:
            self._ctl_quiet("daemon-reload")
        log.info("Uninstalled %s", TIMER)

    def status(self) -> TimerStatus:
        """Never raises."""
        installed = (self.unit_dir / TIMER).exists() and (self.unit_dir / SERVICE).exists()
        last = self._status.read()
        if self._which("systemctl") is None:
            return TimerStatus(installed, False, False, last, "systemctl not found")
        try:
            enabled = self._runner(["systemctl", "--user", "is-enabled", TIMER])
            active = self._runner(["systemctl", "--user", "is-active", TIMER])
        except (OSError, subprocess.SubprocessError) as exc:
            return TimerStatus(installed, False, False, last, f"systemctl failed: {exc}")
        error = NO_BUS if _message(enabled.stderr) == NO_BUS else None
        return TimerStatus(
            installed,
            enabled.returncode == 0 and enabled.stdout.strip() == "enabled",
            active.returncode == 0 and active.stdout.strip() == "active",
            last,
            error,
        )

    # --- internals ------------------------------------------------------------------------

    def _ctl(self, *args: str) -> None:
        argv = ["systemctl", "--user", *args]
        try:
            done = self._runner(argv)
        except (OSError, subprocess.SubprocessError) as exc:
            raise InstallError(f"systemctl failed: {exc}") from exc
        if done.returncode != 0:
            raise InstallError(_message(done.stderr))

    def _ctl_quiet(self, *args: str) -> None:
        try:
            self._ctl(*args)
        except InstallError as exc:
            log.info("systemctl %s: %s", " ".join(args), exc.user_message)

    def _rollback(self) -> None:
        self._ctl_quiet("disable", "--now", TIMER)
        for name in (TIMER, SERVICE):
            try:
                (self.unit_dir / name).unlink(missing_ok=True)
            except OSError:
                log.exception("Could not remove %s", name)
        self._ctl_quiet("daemon-reload")
