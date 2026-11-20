import subprocess
import threading
from pathlib import Path

import pytest

from obb_terminal.ai.runner import CompletedRun
from obb_terminal.alerts import background
from obb_terminal.alerts.schtasks import TASK, SchtasksInstaller, checker_command, task_xml
from obb_terminal.alerts.store import StatusStore
from obb_terminal.alerts.systemd import InstallError, SystemdInstaller

PYTHONW = Path(r"C:\Users\u\AppData\Roaming\uv\tools\obb-terminal\Scripts\pythonw.exe")
OK = CompletedRun(0, "", "")
NOT_FOUND = CompletedRun(1, "", "ERROR: The system cannot find the file specified.\r\n")
REGISTERED = (
    '<?xml version="1.0" encoding="UTF-16"?>\r\n<Task><Triggers><CalendarTrigger>'
    "<Enabled>true</Enabled></CalendarTrigger></Triggers>"
    "<Settings><ExecutionTimeLimit>PT5M</ExecutionTimeLimit><Enabled>{enabled}</Enabled>"
    "</Settings></Task>\r\n"
)


class FakeSchtasks:
    """Scriptable schtasks runner keyed by its verb (/Create, /Delete, /Query)."""

    def __init__(self, registered: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.xml: str | None = None
        self.registered = registered
        self.results: dict[str, object] = {}
        self._lock = threading.Lock()

    def __call__(self, argv: list[str]) -> CompletedRun:
        verb = argv[1]
        with self._lock:
            self.calls.append(list(argv))
        result = self.results.get(verb)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, CompletedRun):
            return result
        if verb == "/Create":
            self.xml = Path(argv[argv.index("/XML") + 1]).read_text(encoding="utf-16")
            self.registered = True
        elif verb == "/Delete":
            if not self.registered:
                return NOT_FOUND
            self.registered = False
        elif verb == "/Query":
            return CompletedRun(0, REGISTERED.format(enabled="true"), "") if self.registered else NOT_FOUND
        return OK

    def verbs(self) -> list[str]:
        return [c[1] for c in self.calls]


def make(tmp_path, runner, *, which=lambda n: "C:/Windows/System32/schtasks.exe", pythonw=None):
    exe = pythonw or tmp_path / "Scripts" / "pythonw.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.touch()
    return SchtasksInstaller(
        runner=runner, which=which, pythonw=exe, work_dir=tmp_path / "data",
        status=StatusStore(tmp_path / "data" / "alerts-status.json"), user="PC\\u",
    )


def test_task_xml_runs_the_checker_hidden_on_weekdays_every_15_minutes():
    xml = task_xml(PYTHONW, user="PC\\u")
    assert xml.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    for part in (
        "<StartBoundary>2026-01-05T07:00:00</StartBoundary>",
        "<Interval>PT15M</Interval>", "<Duration>PT14H46M</Duration>",
        "<Monday />", "<Friday />", "<ExecutionTimeLimit>PT5M</ExecutionTimeLimit>",
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
        "<LogonType>InteractiveToken</LogonType>", "<UserId>PC\\u</UserId>",
        f"<Command>{PYTHONW}</Command>", "<Arguments>-m obb_terminal.alerts.checker</Arguments>",
    ):
        assert part in xml
    assert "<Saturday />" not in xml and "<Sunday />" not in xml


def test_task_xml_escapes_paths():
    xml = task_xml(Path(r"C:\R&D <x>\pythonw.exe"), user="PC\\a&b")
    assert r"<Command>C:\R&amp;D &lt;x&gt;\pythonw.exe</Command>" in xml
    assert "<UserId>PC\\a&amp;b</UserId>" in xml


def test_checker_command_uses_pythonw_so_no_console_window_flashes(monkeypatch, tmp_path):
    python = tmp_path / "Scripts" / "python.exe"
    monkeypatch.setattr("sys.executable", str(python))
    assert checker_command() == tmp_path / "Scripts" / "pythonw.exe"


def test_install_registers_the_task_from_a_utf16_xml_and_cleans_up(tmp_path):
    runner = FakeSchtasks()
    make(tmp_path, runner).install()
    create = runner.calls[0]
    assert create[:4] == ["schtasks", "/Create", "/TN", TASK] and create[-1] == "/F"
    assert "<Arguments>-m obb_terminal.alerts.checker</Arguments>" in runner.xml
    assert not list((tmp_path / "data").glob("*.xml"))  # the temp XML is removed


def test_install_failure_rolls_back_and_reports_schtasks_message(tmp_path):
    runner = FakeSchtasks()
    runner.results["/Create"] = CompletedRun(1, "", "ERROR: Access is denied.\r\n")
    with pytest.raises(InstallError, match="Access is denied"):
        make(tmp_path, runner).install()
    assert runner.verbs()[-1] == "/Delete"
    assert not list((tmp_path / "data").glob("*.xml"))


def test_install_needs_schtasks_and_pythonw(tmp_path):
    with pytest.raises(InstallError, match="schtasks not found"):
        make(tmp_path, FakeSchtasks(), which=lambda n: None).install()
    installer = make(tmp_path, FakeSchtasks())
    installer._pythonw = tmp_path / "missing" / "pythonw.exe"
    with pytest.raises(InstallError, match="reinstall TT Research Terminal"):
        installer.install()


def test_uninstall_is_safe_to_repeat(tmp_path):
    runner = FakeSchtasks(registered=True)
    installer = make(tmp_path, runner)
    installer.uninstall()
    installer.uninstall()
    assert runner.verbs() == ["/Query", "/Delete", "/Query"]


def test_uninstall_reports_a_failed_delete(tmp_path):
    runner = FakeSchtasks(registered=True)
    runner.results["/Delete"] = CompletedRun(1, "", "ERROR: Access is denied.\r\n")
    with pytest.raises(InstallError, match="Access is denied"):
        make(tmp_path, runner).uninstall()


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_status_reads_the_registered_task(tmp_path, enabled):
    runner = FakeSchtasks()
    runner.results["/Query"] = CompletedRun(0, REGISTERED.format(enabled=enabled), "")
    status = make(tmp_path, runner).status()
    assert status.installed and status.enabled is (enabled == "true")
    assert status.active is status.enabled and status.error is None


def test_status_when_missing_or_broken_never_raises(tmp_path):
    status = make(tmp_path, FakeSchtasks()).status()
    assert (status.installed, status.enabled, status.error) == (False, False, None)
    runner = FakeSchtasks()
    runner.results["/Query"] = subprocess.TimeoutExpired("schtasks", 15)
    assert "schtasks failed" in make(tmp_path, runner).status().error
    assert make(tmp_path, FakeSchtasks(), which=lambda n: None).status().error == "schtasks not found"


def test_default_installer_follows_the_platform(monkeypatch):
    monkeypatch.setattr(background, "IS_WINDOWS", True)
    assert isinstance(background.default_installer(), SchtasksInstaller)
    monkeypatch.setattr(background, "IS_WINDOWS", False)
    monkeypatch.setattr(background, "IS_MACOS", False)
    assert isinstance(background.default_installer(), SystemdInstaller)
