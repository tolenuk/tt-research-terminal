"""Install the background checker as a Windows scheduled task (Mon-Fri 07:00-21:45, every
15 min), the Windows twin of alerts/systemd.py.

The task runs `pythonw.exe -m obb_terminal.alerts.checker` (no console window) only while the
user is logged on, so its toasts reach the desktop and no password is stored. Every schtasks
call goes through an injectable runner, so tests never touch the real one.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from xml.sax.saxutils import escape

from obb_terminal.ai.runner import CompletedRun
from obb_terminal.alerts.store import StatusStore
from obb_terminal.alerts.systemd import InstallError, TimerStatus
from obb_terminal.store import data_dir

log = logging.getLogger(__name__)

TASK = "OBB Terminal Alerts"
SCHTASKS_TIMEOUT_S = 15.0
CHECKER_MODULE = "obb_terminal.alerts.checker"
_SETTINGS_ENABLED = re.compile(
    r"<Settings>.*?<Enabled>\s*(true|false)\s*</Enabled>.*?</Settings>", re.DOTALL | re.IGNORECASE
)

SchtasksRunner = Callable[[list[str]], CompletedRun]


def checker_command() -> Path:
    """pythonw.exe beside this interpreter (the uv tool venv's Scripts folder)."""
    return Path(sys.executable).with_name("pythonw.exe")


def current_user() -> str:
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain and user else user


def task_xml(pythonw: Path, user: str) -> str:
    """Task Scheduler 1.2 XML. 07:00 + 14h46m of 15-minute repeats ends with the 21:45 run;
    StartBoundary is a Monday so the weekly schedule starts cleanly."""
    user_id = f"<UserId>{escape(user)}</UserId>" if user else ""
    days = "".join(f"<{d} />" for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"))
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo><Description>TT Research Terminal watchlist alerts "
        "(Mon-Fri 07:00-21:45, every 15 min)</Description></RegistrationInfo>\n"
        "  <Triggers><CalendarTrigger>\n"
        "    <Repetition><Interval>PT15M</Interval><Duration>PT14H46M</Duration>"
        "<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>\n"
        "    <StartBoundary>2026-01-05T07:00:00</StartBoundary>\n"
        "    <Enabled>true</Enabled>\n"
        f"    <ScheduleByWeek><DaysOfWeek>{days}</DaysOfWeek>"
        "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>\n"
        "  </CalendarTrigger></Triggers>\n"
        f'  <Principals><Principal id="Author">{user_id}<LogonType>InteractiveToken</LogonType>'
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>false</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        '  <Actions Context="Author"><Exec>'
        f"<Command>{escape(str(pythonw))}</Command>"
        f"<Arguments>-m {CHECKER_MODULE}</Arguments></Exec></Actions>\n"
        "</Task>\n"
    )


def _run(argv: list[str]) -> CompletedRun:
    done = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=SCHTASKS_TIMEOUT_S,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return CompletedRun(done.returncode, done.stdout, done.stderr)


def _message(done: CompletedRun) -> str:
    text = done.stderr.strip() or done.stdout.strip()
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line.removeprefix("ERROR:").strip() or f"schtasks exited with code {done.returncode}"


class SchtasksInstaller:
    def __init__(
        self,
        runner: SchtasksRunner | None = None,
        which: Callable[[str], str | None] = shutil.which,
        pythonw: Path | None = None,
        work_dir: Path | None = None,
        status: StatusStore | None = None,
        user: str | None = None,
    ) -> None:
        self._runner = runner or _run
        self._which = which
        self._pythonw = pythonw
        self._work_dir = work_dir
        self._status = status or StatusStore()
        self._user = current_user() if user is None else user

    def install(self) -> None:
        """Register (or replace) the task. All or nothing: on failure it is deleted again.
        Raises InstallError."""
        if self._which("schtasks") is None:
            raise InstallError("schtasks not found")
        pythonw = self._pythonw or checker_command()
        if not pythonw.exists():
            raise InstallError(f"{pythonw} not found — reinstall TT Research Terminal")
        work_dir = self._work_dir or data_dir()
        xml_path = work_dir / "alerts-task.xml"
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            xml_path.write_text(task_xml(pythonw, self._user), encoding="utf-16")
            self._schtasks("/Create", "/TN", TASK, "/XML", str(xml_path), "/F")
        except (InstallError, OSError) as exc:
            self._delete_quiet()
            if isinstance(exc, InstallError):
                raise
            raise InstallError(f"Could not write the task file: {exc}") from exc
        finally:
            xml_path.unlink(missing_ok=True)
        log.info("Installed scheduled task %s", TASK)

    def uninstall(self) -> None:
        """Delete the task. Safe to repeat. Raises InstallError if a delete fails."""
        if self._which("schtasks") is None:
            return
        if self._query() is None:
            return
        self._schtasks("/Delete", "/TN", TASK, "/F")
        log.info("Uninstalled scheduled task %s", TASK)

    def status(self) -> TimerStatus:
        """Never raises."""
        last = self._status.read()
        if self._which("schtasks") is None:
            return TimerStatus(False, False, False, last, "schtasks not found")
        try:
            xml = self._query()
        except InstallError as exc:
            return TimerStatus(False, False, False, last, exc.user_message)
        if xml is None:
            return TimerStatus(False, False, False, last)
        match = _SETTINGS_ENABLED.search(xml)
        enabled = match is None or match.group(1).lower() == "true"  # absent means enabled
        return TimerStatus(True, enabled, enabled, last)

    # --- internals ------------------------------------------------------------------------

    def _query(self) -> str | None:
        """The registered task's XML, or None when there is no such task."""
        try:
            done = self._runner(["schtasks", "/Query", "/TN", TASK, "/XML"])
        except (OSError, subprocess.SubprocessError) as exc:
            raise InstallError(f"schtasks failed: {exc}") from exc
        return done.stdout if done.returncode == 0 else None

    def _schtasks(self, *args: str) -> None:
        try:
            done = self._runner(["schtasks", *args])
        except (OSError, subprocess.SubprocessError) as exc:
            raise InstallError(f"schtasks failed: {exc}") from exc
        if done.returncode != 0:
            raise InstallError(_message(done))

    def _delete_quiet(self) -> None:
        try:
            self._schtasks("/Delete", "/TN", TASK, "/F")
        except InstallError as exc:
            log.info("schtasks /Delete: %s", exc.user_message)
