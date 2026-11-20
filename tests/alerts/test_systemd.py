import subprocess
import sys
from datetime import UTC, datetime

import pytest

from obb_terminal.ai.runner import CompletedRun
from obb_terminal.alerts.store import AlertsStatus, StatusStore
from obb_terminal.alerts.systemd import (
    InstallError,
    SystemdInstaller,
    TimerStatus,
    alerts_executable,
    service_text,
    timer_text,
)
from tests.fakes import FakeSystemctl

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="systemd is Linux-only")

SERVICE = """[Unit]
Description=TT Research Terminal watchlist alerts check

[Service]
Type=oneshot
ExecStart="/home/u/.local/share/uv/tools/obb-terminal/bin/obb-terminal-alerts"
Environment="XDG_DATA_HOME=/home/u/.local/share"
Environment="XDG_CONFIG_HOME=/home/u/.config"
TimeoutStartSec=300
Nice=10
"""

TIMER = """[Unit]
Description=TT Research Terminal watchlist alerts (Mon-Fri 07:00-21:45, every 15 min)

[Timer]
OnCalendar=Mon..Fri *-*-* 07..21:00/15
Persistent=false
AccuracySec=1min

[Install]
WantedBy=timers.target
"""


def test_unit_texts_exact(tmp_path):
    from pathlib import Path

    exe = Path("/home/u/.local/share/uv/tools/obb-terminal/bin/obb-terminal-alerts")
    assert service_text(exe, Path("/home/u/.local/share"), Path("/home/u/.config")) == SERVICE
    assert timer_text() == TIMER


def test_percent_is_escaped_and_quotes_rejected():
    from pathlib import Path

    text = service_text(Path("/opt/100%/bin/x"), Path("/d"), Path("/c"))
    assert 'ExecStart="/opt/100%%/bin/x"' in text
    dollar = service_text(Path("/opt/$HOME/x"), Path("/d$x"), Path("/c"))
    assert 'ExecStart="/opt/$$HOME/x"' in dollar  # systemd expands $VAR in ExecStart
    assert 'Environment="XDG_DATA_HOME=/d$x"' in dollar  # but not in Environment=
    for bad in ('/opt/a"b/x', "/opt/a\\b/x", "/opt/a\nb/x"):
        with pytest.raises(InstallError, match="Unsupported character"):
            service_text(Path(bad), Path("/d"), Path("/c"))


def test_alerts_executable_sits_next_to_the_interpreter(monkeypatch):
    monkeypatch.setattr("sys.executable", "/venv/bin/python")
    assert str(alerts_executable()) == "/venv/bin/obb-terminal-alerts"


@pytest.fixture
def exe(tmp_path):
    path = tmp_path / "bin" / "obb-terminal-alerts"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\n")
    return path


@pytest.fixture
def ctl():
    return FakeSystemctl()


@pytest.fixture
def installer(tmp_path, ctl, exe):
    return SystemdInstaller(
        runner=ctl, unit_dir=tmp_path / "units", which=lambda n: "/usr/bin/" + n, exec_path=exe,
        status=StatusStore(tmp_path / "alerts-status.json"),
        data_dir=tmp_path / "data", config_dir=tmp_path / "config",
    )


def test_install_writes_units_then_reloads_and_enables(installer, ctl, tmp_path, exe):
    installer.install()
    units = tmp_path / "units"
    assert (units / "obb-terminal-alerts.timer").read_text() == TIMER
    service = (units / "obb-terminal-alerts.service").read_text()
    assert f'ExecStart="{exe}"' in service and f"XDG_DATA_HOME={tmp_path / 'data'}" in service
    assert ctl.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "obb-terminal-alerts.timer"],
    ]


def test_failed_enable_rolls_back(installer, ctl, tmp_path):
    ctl.results["enable"] = CompletedRun(1, "", "Failed to connect to bus: No medium found\n")
    with pytest.raises(InstallError) as info:
        installer.install()
    assert info.value.user_message == "systemd user session unavailable"
    assert list((tmp_path / "units").iterdir()) == []
    assert ctl.subcommands() == [
        "daemon-reload", "enable --now obb-terminal-alerts.timer",
        "disable --now obb-terminal-alerts.timer", "daemon-reload",
    ]


def test_other_systemctl_error_uses_first_stderr_line(installer, ctl):
    ctl.results["daemon-reload"] = CompletedRun(1, "", "\nAccess denied\nmore\n")
    with pytest.raises(InstallError, match="^Access denied$"):
        installer.install()
    ctl.results["daemon-reload"] = subprocess.TimeoutExpired(["systemctl"], 15)
    with pytest.raises(InstallError, match="systemctl failed"):
        installer.install()


def test_missing_systemctl_or_executable(tmp_path, ctl, exe):
    no_ctl = SystemdInstaller(runner=ctl, unit_dir=tmp_path / "u", which=lambda n: None, exec_path=exe)
    with pytest.raises(InstallError, match="systemctl not found"):
        no_ctl.install()
    missing = SystemdInstaller(runner=ctl, unit_dir=tmp_path / "u", which=lambda n: "/x",
                               exec_path=tmp_path / "nope")
    with pytest.raises(InstallError, match="not found — reinstall TT Research Terminal"):
        missing.install()
    assert ctl.calls == [] and not (tmp_path / "u").exists()


def test_uninstall_is_repeatable(installer, ctl, tmp_path):
    installer.install()
    ctl.calls.clear()
    installer.uninstall()
    assert list((tmp_path / "units").iterdir()) == []
    assert ctl.subcommands() == ["disable --now obb-terminal-alerts.timer", "daemon-reload"]
    ctl.results["disable"] = CompletedRun(1, "", "Unit file obb-terminal-alerts.timer does not exist.\n")
    installer.uninstall()  # nothing installed: still fine


def test_status(installer, ctl, tmp_path):
    from obb_terminal.ai.runner import CompletedRun as Run

    last = AlertsStatus(datetime(2026, 10, 13, 14, 15, tzinfo=UTC), True, 4, 0, 0, 1)
    StatusStore(tmp_path / "alerts-status.json").write(last)
    assert installer.status() == TimerStatus(False, True, True, last)
    installer.install()
    ctl.results["is-enabled"] = Run(1, "disabled\n", "")
    ctl.results["is-active"] = Run(3, "inactive\n", "")
    assert installer.status() == TimerStatus(True, False, False, last)
    ctl.results["is-enabled"] = Run(1, "", "Failed to connect to bus: x")
    assert installer.status().error == "systemd user session unavailable"
    ctl.results["is-enabled"] = OSError("boom")
    assert installer.status().error == "systemctl failed: boom"


def test_install_error_message_is_capped():
    assert len(InstallError("x" * 500).user_message) == 200
