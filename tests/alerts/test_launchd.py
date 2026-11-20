import os
import plistlib
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from obb_terminal.ai.runner import CompletedRun
from obb_terminal.alerts import background
from obb_terminal.alerts.launchd import LABEL, LaunchdInstaller, agent_plist, schedule
from obb_terminal.alerts.store import StatusStore
from obb_terminal.alerts.systemd import InstallError

UID = 501
TARGET = f"gui/{UID}/{LABEL}"
OK = CompletedRun(0, "", "")
NOT_LOADED = CompletedRun(113, "", f'Could not find service "{LABEL}" in domain for user gui: {UID}\n')
IO_ERROR = CompletedRun(5, "", "Bootstrap failed: 5: Input/output error\n")


class FakeLaunchctl:
    """Scriptable launchctl keyed by its verb (bootstrap, bootout, enable, print)."""

    def __init__(self, loaded: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.loaded = loaded
        self.results: dict[str, list[object]] = {}
        self._lock = threading.Lock()

    def __call__(self, argv: list[str]) -> CompletedRun:
        verb = argv[1]
        with self._lock:
            self.calls.append(list(argv))
        queued = self.results.get(verb)
        if queued:
            result = queued.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        if verb == "bootstrap":
            self.loaded = True
        elif verb == "bootout":
            if not self.loaded:
                return NOT_LOADED
            self.loaded = False
        elif verb == "print":
            return CompletedRun(0, f"{TARGET} = {{\n\tstate = waiting\n}}\n", "") if self.loaded else NOT_LOADED
        return OK

    def verbs(self) -> list[str]:
        return [c[1] for c in self.calls]


def make(tmp_path, runner, *, which=lambda n: "/bin/launchctl", python=None):
    exe = python or tmp_path / "venv" / "bin" / "python"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.touch()
    return LaunchdInstaller(
        runner=runner, which=which, python=exe, agent_dir=tmp_path / "LaunchAgents",
        data_home=tmp_path / "Application Support", config_home=tmp_path / ".config",
        status=StatusStore(tmp_path / "data" / "alerts-status.json"), uid=UID, sleep=lambda s: None,
    )


def test_schedule_is_every_15_minutes_from_07_00_to_21_45_on_weekdays():
    slots = schedule()
    assert len(slots) == 5 * 15 * 4
    assert slots[0] == {"Weekday": 1, "Hour": 7, "Minute": 0}
    assert slots[-1] == {"Weekday": 5, "Hour": 21, "Minute": 45}
    assert {s["Weekday"] for s in slots} == {1, 2, 3, 4, 5}  # launchd: 0 and 7 are Sunday


def test_agent_plist_runs_the_checker_module_with_pinned_folders(tmp_path):
    python = Path("/Users/u/Applications/TT Research Terminal.app/Contents/Resources/venv/bin/python")
    data = tmp_path / "Application Support"
    agent = plistlib.loads(agent_plist(python, data, tmp_path / ".config", data / "obb-terminal" / "x.log"))
    assert agent["Label"] == LABEL
    # python -m, not the console script: a shebang cannot hold the spaces in the .app path
    assert agent["ProgramArguments"] == [str(python), "-m", "obb_terminal.alerts.checker"]
    env = agent["EnvironmentVariables"]
    assert env["XDG_DATA_HOME"] == str(data) and env["XDG_CONFIG_HOME"] == str(tmp_path / ".config")
    # startswith, not split(":"): a Windows home (C:\\Users\\...) has a colon of its own
    assert env["PATH"].startswith(f"{Path.home() / '.local' / 'bin'}:/opt/homebrew/bin:/usr/local/bin:")
    assert agent["StartCalendarInterval"] == schedule()
    assert agent["RunAtLoad"] is False and agent["ProcessType"] == "Background"
    assert agent["StandardErrorPath"] == str(data / "obb-terminal" / "x.log")


def test_install_writes_the_agent_and_bootstraps_it(tmp_path):
    runner = FakeLaunchctl()
    installer = make(tmp_path, runner)
    installer.install()
    plist = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
    assert plistlib.loads(plist.read_bytes())["Label"] == LABEL
    assert runner.verbs() == ["bootout", "print", "enable", "bootstrap"]
    assert runner.calls[2] == ["launchctl", "enable", TARGET]
    assert runner.calls[3] == ["launchctl", "bootstrap", f"gui/{UID}", str(plist)]
    assert (tmp_path / "Application Support" / "obb-terminal").is_dir()  # launchd opens its log there


def test_install_replaces_a_loaded_agent(tmp_path):
    runner = FakeLaunchctl(loaded=True)
    make(tmp_path, runner).install()
    assert runner.verbs() == ["bootout", "print", "enable", "bootstrap"] and runner.loaded


def test_install_retries_a_bootstrap_that_races_the_bootout(tmp_path):
    runner = FakeLaunchctl(loaded=True)
    runner.results["bootstrap"] = [IO_ERROR]
    make(tmp_path, runner).install()
    assert runner.verbs() == ["bootout", "print", "enable", "bootstrap", "bootstrap"] and runner.loaded


def test_install_waits_for_a_running_check_to_stop_before_bootstrapping(tmp_path):
    runner = FakeLaunchctl(loaded=True)
    still = CompletedRun(0, f"{TARGET} = {{\n\tstate = running\n}}\n", "")
    runner.results["print"] = [still, still]  # bootout returned, but the check is still exiting
    pauses = []
    installer = make(tmp_path, runner)
    installer._sleep = pauses.append
    installer.install()
    assert runner.verbs() == ["bootout", "print", "print", "print", "enable", "bootstrap"]
    assert pauses == [0.5, 0.5] and runner.loaded


def test_install_failure_rolls_back_and_reports_launchctl(tmp_path):
    runner = FakeLaunchctl()
    runner.results["bootstrap"] = [IO_ERROR, IO_ERROR]
    with pytest.raises(InstallError, match="Bootstrap failed: 5"):
        make(tmp_path, runner).install()
    assert runner.verbs()[-1] == "bootout"
    assert not (tmp_path / "LaunchAgents" / f"{LABEL}.plist").exists()


def test_install_needs_launchctl_and_the_venv_python(tmp_path):
    with pytest.raises(InstallError, match="launchctl not found"):
        make(tmp_path, FakeLaunchctl(), which=lambda n: None).install()
    installer = make(tmp_path, FakeLaunchctl())
    installer._python = tmp_path / "missing" / "python"
    with pytest.raises(InstallError, match="reinstall TT Research Terminal"):
        installer.install()


def test_install_reports_launchctl_that_cannot_run(tmp_path):
    runner = FakeLaunchctl()
    runner.results["bootstrap"] = [subprocess.TimeoutExpired("launchctl", 15)] * 2
    with pytest.raises(InstallError, match="launchctl failed"):
        make(tmp_path, runner).install()


def test_uninstall_is_safe_to_repeat(tmp_path):
    runner = FakeLaunchctl()
    installer = make(tmp_path, runner)
    installer.install()
    installer.uninstall()
    installer.uninstall()
    assert not runner.loaded
    assert not (tmp_path / "LaunchAgents" / f"{LABEL}.plist").exists()


def test_status_follows_the_loaded_agent(tmp_path):
    runner = FakeLaunchctl()
    installer = make(tmp_path, runner)
    status = installer.status()
    assert (status.installed, status.enabled, status.active, status.error) == (False, False, False, None)
    installer.install()
    status = installer.status()
    assert (status.installed, status.enabled, status.active, status.error) == (True, True, True, None)


def test_status_never_raises(tmp_path):
    assert make(tmp_path, FakeLaunchctl(), which=lambda n: None).status().error == "launchctl not found"
    runner = FakeLaunchctl()
    runner.results["print"] = [OSError("exec failed")]
    assert "launchctl failed" in make(tmp_path, runner).status().error


def test_default_installer_on_macos_is_launchd(monkeypatch):
    monkeypatch.setattr(background, "IS_WINDOWS", False)
    monkeypatch.setattr(background, "IS_MACOS", True)
    assert isinstance(background.default_installer(), LaunchdInstaller)


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("OBB_REAL_LAUNCHD") != "1",
    reason="touches the real launchd; CI sets OBB_REAL_LAUNCHD=1 on its macOS runner",
)
def test_real_launchd_round_trip(tmp_path):
    installer = LaunchdInstaller(  # its own label and folder: a developer's real agent stays put
        python=Path(sys.executable), agent_dir=tmp_path / "LaunchAgents", data_home=tmp_path / "data",
        config_home=tmp_path / "config", status=StatusStore(tmp_path / "alerts-status.json"),
        label=f"{LABEL}.test",
    )
    try:
        installer.install()
        status = installer.status()
        assert status.installed and status.active, status
    finally:
        installer.uninstall()
    assert not installer.status().active
