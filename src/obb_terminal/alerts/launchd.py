"""Install the background checker as a launchd user agent (Mon-Fri 07:00-21:45, every 15 min),
the macOS twin of alerts/systemd.py and alerts/schtasks.py.

The agent lives in ~/Library/LaunchAgents, so it loads again at every login, and runs
`<venv python> -m obb_terminal.alerts.checker` in the user's GUI session, where its
notifications can reach the screen. launchd has no run time limit; the checker keeps to its own
budget (alerts/checker.py). Every launchctl call goes through an injectable runner, so tests never
touch the real one.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from obb_terminal.ai.runner import CompletedRun
from obb_terminal.alerts.store import StatusStore
from obb_terminal.alerts.systemd import InstallError, TimerStatus
from obb_terminal.fsutil import atomic_write_text
from obb_terminal.osdirs import config_base, data_base

log = logging.getLogger(__name__)

LABEL = "com.ttresearch.obb-terminal-alerts"
PLIST = f"{LABEL}.plist"
CHECKER_MODULE = "obb_terminal.alerts.checker"
APP_FOLDER = "obb-terminal"  # store.data_dir() under the data base
STDIO_LOG = "obb-terminal-alerts.stdio.log"
LAUNCHCTL_TIMEOUT_S = 15.0
BOOTSTRAP_RETRY_S = 1.0
BOOTOUT_POLL_S = 0.5
BOOTOUT_WAIT_S = 25.0  # launchd gives a running check 20 s after SIGTERM before SIGKILL
WEEKDAYS = range(1, 6)  # launchd: 1 = Monday ... 5 = Friday
HOURS = range(7, 22)
MINUTES = (0, 15, 30, 45)
NICE = 10

LaunchctlRunner = Callable[[list[str]], CompletedRun]


def schedule() -> list[dict[str, int]]:
    """One StartCalendarInterval entry per run: launchd has no "every 15 min between" form."""
    return [{"Weekday": d, "Hour": h, "Minute": m} for d in WEEKDAYS for h in HOURS for m in MINUTES]


def agent_path() -> str:
    """launchd starts agents with a bare PATH; the checker may need Claude Code's folders."""
    dirs = [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    return ":".join([*dirs, "/usr/bin", "/bin", "/usr/sbin", "/sbin"])


def agent_plist(
    python: Path, data_home: Path, config_home: Path, log_path: Path, label: str = LABEL
) -> bytes:
    """`python -m`, not the console script: a shebang cannot hold the spaces in the .app path."""
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": [str(python), "-m", CHECKER_MODULE],
        "EnvironmentVariables": {
            "XDG_DATA_HOME": str(data_home),
            "XDG_CONFIG_HOME": str(config_home),
            "PATH": agent_path(),
        },
        "StartCalendarInterval": schedule(),
        "RunAtLoad": False,
        "ProcessType": "Background",
        "Nice": NICE,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    })


def _run(argv: list[str]) -> CompletedRun:
    done = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=LAUNCHCTL_TIMEOUT_S,
        check=False,
    )
    return CompletedRun(done.returncode, done.stdout, done.stderr)


def _message(done: CompletedRun) -> str:
    text = done.stderr.strip() or done.stdout.strip()
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line or f"launchctl exited with code {done.returncode}"


class LaunchdInstaller:
    def __init__(
        self,
        runner: LaunchctlRunner | None = None,
        which: Callable[[str], str | None] = shutil.which,
        python: Path | None = None,
        agent_dir: Path | None = None,
        data_home: Path | None = None,
        config_home: Path | None = None,
        status: StatusStore | None = None,
        uid: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        label: str = LABEL,
    ) -> None:
        self._runner = runner or _run
        self._which = which
        self._python = python
        self._agent_dir = agent_dir
        self._data_home = data_home
        self._config_home = config_home
        self._status = status or StatusStore()
        self._uid = uid
        self._sleep = sleep
        self._label = label

    @property
    def plist_path(self) -> Path:
        return (self._agent_dir or Path.home() / "Library" / "LaunchAgents") / f"{self._label}.plist"

    @property
    def _domain(self) -> str:
        return f"gui/{os.getuid() if self._uid is None else self._uid}"

    @property
    def _target(self) -> str:
        return f"{self._domain}/{self._label}"

    def install(self) -> None:
        """Write the agent and load it, replacing a loaded one. All or nothing: on any failure
        it is unloaded and removed again. Raises InstallError."""
        if self._which("launchctl") is None:
            raise InstallError("launchctl not found")
        python = self._python or Path(sys.executable)  # unresolved: the venv's own python
        if not python.exists():
            raise InstallError(f"{python} not found — reinstall TT Research Terminal")
        data_home = self._data_home or data_base()
        app_data = data_home / APP_FOLDER
        try:
            app_data.mkdir(parents=True, exist_ok=True)  # launchd opens the log but makes no folder
            plist = agent_plist(
                python, data_home, self._config_home or config_base(), app_data / STDIO_LOG, self._label
            )
            atomic_write_text(self.plist_path, plist.decode("utf-8"))
            self._ctl_quiet("bootout", self._target)
            self._wait_unloaded()
            self._ctl("enable", self._target)  # clears an earlier "disabled" override
            self._bootstrap()
        except (InstallError, OSError) as exc:
            self._rollback()
            if isinstance(exc, InstallError):
                raise
            raise InstallError(f"Could not write the agent file: {exc}") from exc
        log.info("Installed launchd agent %s", self._label)

    def uninstall(self) -> None:
        """Unload and remove the agent. Safe to repeat. Raises InstallError if the file cannot
        be removed."""
        if self._which("launchctl") is not None:
            self._ctl_quiet("bootout", self._target)
        try:
            self.plist_path.unlink(missing_ok=True)
        except OSError as exc:
            raise InstallError(f"Could not remove the agent file: {exc}") from exc
        log.info("Uninstalled launchd agent %s", self._label)

    def status(self) -> TimerStatus:
        """Never raises. A loaded agent in ~/Library/LaunchAgents is both on and scheduled."""
        installed = self.plist_path.exists()
        last = self._status.read()
        if self._which("launchctl") is None:
            return TimerStatus(installed, False, False, last, "launchctl not found")
        try:
            done = self._runner(["launchctl", "print", self._target])
        except (OSError, subprocess.SubprocessError) as exc:
            return TimerStatus(installed, False, False, last, f"launchctl failed: {exc}")
        loaded = installed and done.returncode == 0
        return TimerStatus(installed, loaded, loaded, last)

    # --- internals ------------------------------------------------------------------------

    def _wait_unloaded(self) -> None:
        """A bootout returns before launchd has stopped a running check (an update can land
        mid-check), and a bootstrap before then fails; wait until `print` no longer finds it."""
        for _ in range(int(BOOTOUT_WAIT_S / BOOTOUT_POLL_S)):
            try:
                if self._runner(["launchctl", "print", self._target]).returncode != 0:
                    return
            except (OSError, subprocess.SubprocessError):
                return  # the bootstrap below reports a launchctl that cannot run
            self._sleep(BOOTOUT_POLL_S)
        log.warning("launchd still lists %s after %.0f s", self._label, BOOTOUT_WAIT_S)

    def _bootstrap(self) -> None:
        """Even after the wait a bootstrap can fail with "5: Input/output error" while launchd
        settles; one retry after a pause covers that."""
        try:
            self._ctl("bootstrap", self._domain, str(self.plist_path))
        except InstallError as exc:
            log.info("launchctl bootstrap: %s; retrying", exc.user_message)
            self._sleep(BOOTSTRAP_RETRY_S)
            self._ctl("bootstrap", self._domain, str(self.plist_path))

    def _ctl(self, *args: str) -> None:
        try:
            done = self._runner(["launchctl", *args])
        except (OSError, subprocess.SubprocessError) as exc:
            raise InstallError(f"launchctl failed: {exc}") from exc
        if done.returncode != 0:
            raise InstallError(_message(done))

    def _ctl_quiet(self, *args: str) -> None:
        try:
            self._ctl(*args)
        except InstallError as exc:
            log.info("launchctl %s: %s", args[0], exc.user_message)

    def _rollback(self) -> None:
        self._ctl_quiet("bootout", self._target)
        try:
            self.plist_path.unlink(missing_ok=True)
        except OSError:
            log.exception("Could not remove %s", self.plist_path)
