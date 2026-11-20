"""Run a child process with a timeout, in its own process group, killable on app quit.

POSIX: a new session, signalled as a group. Windows: a new process group with no console
window, stopped by `taskkill /T /F` on its whole tree (Windows has no SIGTERM to offer).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
# Windows lacks SIGKILL; the values only pick an action in _signal_group there, so they must
# differ (SIGTERM does nothing on Windows, SIGKILL runs taskkill).
SIGTERM = signal.SIGTERM
SIGKILL = getattr(signal, "SIGKILL", 9)
TASKKILL_TIMEOUT_S = 10.0


@dataclass(frozen=True, slots=True)
class CompletedRun:
    returncode: int
    stdout: str
    stderr: str


class RunTimeout(Exception):
    pass


class RunCancelled(Exception):
    pass


# (argv, cwd, timeout_s, stdin text) -> CompletedRun; raises FileNotFoundError, RunTimeout,
# RunCancelled.
Runner = Callable[[list[str], Path, float, str | None], CompletedRun]


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The parent's environment minus CLAUDECODE and CLAUDE_CODE_* (so launching the app
    from inside a Claude Code terminal cannot change how the headless call behaves)."""
    env = os.environ if base is None else base
    return {k: v for k, v in env.items() if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}


def spawn_options() -> dict[str, Any]:
    """Popen options that put the child in its own group, so one call can stop all of it."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


class SubprocessRunner:
    def __init__(self, grace_s: float = 3.0) -> None:
        self._grace_s = grace_s
        self._lock = threading.Lock()
        self._live: dict[subprocess.Popen[str], bool] = {}  # process -> cancelled
        self._closed = False  # set by cancel_all(); no new runs start afterwards

    def __call__(
        self, argv: list[str], cwd: Path, timeout_s: float, input: str | None = None
    ) -> CompletedRun:
        with self._lock:
            if self._closed:
                raise RunCancelled()
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=child_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **spawn_options(),
        )
        with self._lock:
            closed = self._closed
            if not closed:
                self._live[proc] = False
        if closed:  # cancel_all() ran while we were spawning
            _signal_group(proc, SIGKILL)
            self._drain(proc)
            raise RunCancelled()
        try:
            try:
                out, err = proc.communicate(input=input or "", timeout=timeout_s)
            except subprocess.TimeoutExpired:
                if self._is_cancelled(proc):
                    self._drain(proc)
                    raise RunCancelled() from None
                log.warning("Process %s timed out after %.1f s; killing it", proc.pid, timeout_s)
                self._kill([proc])
                self._drain(proc)
                if self._is_cancelled(proc):
                    raise RunCancelled() from None
                raise RunTimeout(f"timed out after {timeout_s:.1f} s") from None
            if self._is_cancelled(proc):
                raise RunCancelled()
            return CompletedRun(proc.returncode, out, err)
        finally:
            with self._lock:
                self._live.pop(proc, None)

    def cancel_all(self) -> None:
        """Cancel every in-flight run and refuse new ones. Blocks for at most one grace period."""
        with self._lock:
            self._closed = True
            procs = list(self._live)
            for proc in procs:
                self._live[proc] = True
        self._kill(procs)

    def _is_cancelled(self, proc: subprocess.Popen[str]) -> bool:
        with self._lock:
            return self._live.get(proc, False)

    def _kill(self, procs: list[subprocess.Popen[str]]) -> None:
        """SIGTERM every process group, wait one shared grace period for them to empty, then
        SIGKILL every group regardless -- so group members that ignore SIGTERM (and may hold
        the stdout pipe) cannot outlive the leader."""
        if not procs:
            return
        for proc in procs:
            _signal_group(proc, SIGTERM)
        deadline = time.monotonic() + self._grace_s
        for proc in procs:
            _wait_group(proc, deadline)
        for proc in procs:
            _signal_group(proc, SIGKILL)

    def _drain(self, proc: subprocess.Popen[str]) -> None:
        try:
            proc.communicate(timeout=self._grace_s)
        except subprocess.TimeoutExpired:
            log.warning("Process %s did not exit after kill", proc.pid)


def _signal_group(proc: subprocess.Popen[str], sig: int) -> None:
    if IS_WINDOWS:
        if sig == SIGKILL:
            _taskkill_tree(proc.pid)
        return
    try:
        os.killpg(proc.pid, sig)  # start_new_session=True: pgid == leader pid
    except (ProcessLookupError, PermissionError):
        pass


def _taskkill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=TASKKILL_TIMEOUT_S,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("taskkill of %s failed: %s", pid, exc)


def _wait_group(proc: subprocess.Popen[str], deadline: float) -> None:
    """Wait until the leader is reaped and the group has no members, or the deadline passes."""
    if IS_WINDOWS:  # no SIGTERM was sent, so there is nothing to wait for
        return
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            proc.wait(remaining)
        except subprocess.TimeoutExpired:
            return
        try:
            os.killpg(proc.pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
