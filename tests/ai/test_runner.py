import logging
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from obb_terminal.ai import runner as runner_mod
from obb_terminal.ai.runner import RunCancelled, RunTimeout, SubprocessRunner, child_env

PY = sys.executable


def test_runs_and_captures_output(tmp_path):
    run = SubprocessRunner()([PY, "-c", "import os; print(os.getcwd()); import sys; sys.stderr.write('e')"], tmp_path, 10)
    assert run.returncode == 0
    assert run.stdout.strip() == str(tmp_path)
    assert run.stderr == "e"


def test_input_is_written_to_stdin(tmp_path):
    text = "héllo\n" * 20_000  # bigger than a pipe buffer and than a Windows command line
    run = SubprocessRunner()([PY, "-c", "import sys; print(len(sys.stdin.buffer.read().decode('utf-8').replace(chr(13), '')))"], tmp_path, 10, text)
    assert run.returncode == 0 and run.stdout.strip() == str(len(text))


def test_no_input_means_empty_stdin(tmp_path):
    run = SubprocessRunner()([PY, "-c", "import sys; print(repr(sys.stdin.read()))"], tmp_path, 10)
    assert run.stdout.strip() == "''"


def test_windows_spawn_uses_a_new_process_group_and_no_console(monkeypatch):
    monkeypatch.setattr(runner_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(runner_mod.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(runner_mod.subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False)
    assert runner_mod.spawn_options() == {"creationflags": 0x200 | 0x8000000}
    monkeypatch.setattr(runner_mod, "IS_WINDOWS", False)
    assert runner_mod.spawn_options() == {"start_new_session": True}


def test_windows_kill_takes_down_the_whole_tree_with_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(runner_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(runner_mod.subprocess, "run", lambda argv, **kw: calls.append(argv))

    class Proc:
        pid = 4242

    runner_mod._signal_group(Proc(), runner_mod.SIGKILL)
    runner_mod._signal_group(Proc(), runner_mod.SIGTERM)  # no graceful stop on Windows
    assert calls == [["taskkill", "/PID", "4242", "/T", "/F"]]


def test_nonzero_exit_is_returned_not_raised(tmp_path):
    run = SubprocessRunner()([PY, "-c", "raise SystemExit(3)"], tmp_path, 10)
    assert run.returncode == 3


def test_missing_executable_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        SubprocessRunner()([str(tmp_path / "no-such-claude")], tmp_path, 10)


def test_timeout_kills_process(tmp_path):
    runner = SubprocessRunner(grace_s=1.0)
    started = time.monotonic()
    with pytest.raises(RunTimeout):
        runner([PY, "-c", "import time; time.sleep(30)"], tmp_path, 0.5)
    assert time.monotonic() - started < 5
    assert runner._live == {}


def test_cancel_all_kills_in_flight_run(tmp_path):
    runner = SubprocessRunner(grace_s=1.0)
    errors = []

    def call():
        try:
            runner([PY, "-c", "import time; time.sleep(30)"], tmp_path, 60)
        except RunCancelled as exc:
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    deadline = time.monotonic() + 5
    while not runner._live and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.cancel_all()
    thread.join(5)
    assert not thread.is_alive()
    assert len(errors) == 1


def test_child_env_strips_claude_code_variables(monkeypatch, tmp_path):
    base = {"PATH": "/bin", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CONFIG": "keep"}
    assert child_env(base) == {"PATH": "/bin", "CLAUDE_CONFIG": "keep"}
    monkeypatch.setenv("CLAUDECODE", "1")
    run = SubprocessRunner()([PY, "-c", "import os; print(os.environ.get('CLAUDECODE'))"], tmp_path, 10)
    assert run.stdout.strip() == "None"


# --- process-group escalation, cancellation and closed state -------------------------------

MARKER = "obbt-runner-test-marker"

# Grandchild: ignores SIGTERM, writes its pid once the handler is installed, sleeps.
_GRANDCHILD = (
    "import os, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "open(sys.argv[1] + '.tmp', 'w').write(str(os.getpid())); os.replace(sys.argv[1] + '.tmp', sys.argv[1]); "
    "time.sleep(30)"
)
# Leader: spawns the grandchild (inheriting stdout/stderr), waits for its pid file, sleeps.
_LEADER = (
    "import os, subprocess, sys, time; "
    f"subprocess.Popen([sys.executable, '-c', {_GRANDCHILD!r}, sys.argv[1], sys.argv[2]]); "
    "[time.sleep(0.01) for _ in range(500) if not os.path.exists(sys.argv[1])]; "
    "time.sleep(30)"
)
# A single process that ignores SIGTERM and writes its pid once the handler is installed.
_STUBBORN = _GRANDCHILD


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):  # reaped between open() and read()
        return False


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.02)
    return not _alive(pid)


def _wait_for_file(path, seconds: float = 5.0) -> int:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return int(path.read_text())
        except (FileNotFoundError, PermissionError, ValueError):
            pass  # not written yet; Windows refuses to open a file another process is writing
        time.sleep(0.01)
    raise AssertionError(f"{path} never appeared")


@pytest.fixture
def reap():
    """Kill any pid registered here at teardown, so a failing test leaves no orphans.

    Only pids whose command line still carries MARKER are killed: after a passing test the
    pid may already have been reused by an unrelated process."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        if not _is_ours(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _is_ours(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return MARKER.encode() in fh.read()
    except OSError:
        return False


def test_timeout_kills_sigterm_ignoring_grandchild(tmp_path, reap, monkeypatch):
    # The timeout is triggered deterministically once the grandchild is ready (its SIGTERM
    # handler installed), so the real kill-and-drain path runs without racing interpreter
    # start-up under load. Coarse timeout timing is covered by test_timeout_kills_process.
    pidfile = tmp_path / "grandchild.pid"
    real_popen = runner_mod.subprocess.Popen

    class GatedPopen(real_popen):
        _gated = False

        def communicate(self, input=None, timeout=None):
            if not self._gated:
                self._gated = True
                try:
                    _wait_for_file(pidfile, 30)
                except AssertionError:
                    os.killpg(self.pid, signal.SIGKILL)  # leave no orphans if never ready
                    raise
                raise subprocess.TimeoutExpired(self.args, timeout)
            return super().communicate(input, timeout)

    monkeypatch.setattr(runner_mod.subprocess, "Popen", GatedPopen)
    runner = SubprocessRunner(grace_s=0.5)
    with pytest.raises(RunTimeout):
        runner([PY, "-c", _LEADER, str(pidfile), MARKER], tmp_path, 120)
    grandchild = int(pidfile.read_text())
    reap.append(grandchild)
    assert _gone_within(grandchild, 10.0)
    assert runner._live == {}


def test_cancel_with_sigterm_ignoring_grandchild_raises_cancelled_promptly(tmp_path, reap):
    # With a 120 s run timeout, ending in RunCancelled proves cancel_all() ended the run.
    pidfile = tmp_path / "grandchild.pid"
    runner = SubprocessRunner(grace_s=0.5)
    outcome: list[BaseException] = []
    done = threading.Event()

    def call():
        try:
            runner([PY, "-c", _LEADER, str(pidfile), MARKER], tmp_path, 120)
        except BaseException as exc:  # noqa: BLE001 - record whatever the run raised
            outcome.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    grandchild = _wait_for_file(pidfile, 30)
    reap.append(grandchild)
    runner.cancel_all()
    assert done.wait(30), "caller still blocked after cancel_all()"
    assert len(outcome) == 1 and isinstance(outcome[0], RunCancelled)
    assert _gone_within(grandchild, 10.0)


def test_call_after_cancel_all_raises_cancelled_without_spawning(tmp_path):
    runner = SubprocessRunner(grace_s=0.5)
    runner.cancel_all()
    marker = tmp_path / "spawned"
    with pytest.raises(RunCancelled):
        runner([PY, "-c", f"open({str(marker)!r}, 'w').close()", MARKER], tmp_path, 10)
    time.sleep(0.3)
    assert not marker.exists()
    assert runner._live == {}


def test_cancel_all_racing_spawn_kills_new_process(tmp_path, monkeypatch, reap):
    runner = SubprocessRunner(grace_s=0.5)
    real_popen = runner_mod.subprocess.Popen
    spawned: list[int] = []

    def popen_then_cancel(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc.pid)
        reap.append(proc.pid)
        runner.cancel_all()  # cancel lands between spawn and registration
        return proc

    monkeypatch.setattr(runner_mod.subprocess, "Popen", popen_then_cancel)
    started = time.monotonic()
    with pytest.raises(RunCancelled):
        runner([PY, "-c", "import time; time.sleep(5)", MARKER], tmp_path, 60)
    assert time.monotonic() - started < 3
    assert _gone_within(spawned[0], 2.0)
    assert runner._live == {}


def test_cancel_all_waits_one_shared_grace_period(tmp_path, reap):
    grace = 2.0
    runner = SubprocessRunner(grace_s=grace)
    outcomes: list[BaseException] = []
    pidfiles = [tmp_path / f"p{i}.pid" for i in range(3)]

    def call(pidfile):
        try:
            runner([PY, "-c", _STUBBORN, str(pidfile), MARKER], tmp_path, 30)
        except BaseException as exc:  # noqa: BLE001
            outcomes.append(exc)

    threads = [threading.Thread(target=call, args=(p,), daemon=True) for p in pidfiles]
    for thread in threads:
        thread.start()
    pids = [_wait_for_file(p, 30) for p in pidfiles]
    reap.extend(pids)
    started = time.monotonic()
    runner.cancel_all()
    elapsed = time.monotonic() - started
    for thread in threads:
        thread.join(30)
    # One shared grace period is ~grace; a per-process grace would be >= 3 * grace.
    assert elapsed < 2 * grace, f"cancel_all blocked {elapsed:.2f} s (grace applied per process?)"
    assert len(outcomes) == 3 and all(isinstance(e, RunCancelled) for e in outcomes)
    assert all(_gone_within(pid, 10.0) for pid in pids)


def test_timeout_log_shows_sub_second_value(tmp_path, caplog):
    runner = SubprocessRunner(grace_s=0.5)
    with caplog.at_level(logging.WARNING, logger="obb_terminal.ai.runner"):
        with pytest.raises(RunTimeout, match=r"0\.5"):
            runner([PY, "-c", "import time; time.sleep(30)", MARKER], tmp_path, 0.5)
    assert any("0.5" in rec.getMessage() for rec in caplog.records)
