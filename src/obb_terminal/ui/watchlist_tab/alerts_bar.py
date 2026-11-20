"""The background-alerts switch: one button that installs or removes the background schedule
(systemd timer, Windows scheduled task or macOS launchd agent) off the GUI thread and says its
state and the checker's last run."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from obb_terminal.alerts.systemd import Installer, InstallError, TimerStatus
from obb_terminal.ui.jobs import SerialJobs

SCHEDULE_NOTE = "Checks targets every 15 min, Mon–Fri 07:00–21:45, even when closed."


def status_text(status: TimerStatus) -> str:
    if status.error:
        return f"Background alerts: error — {status.error}"
    if not status.enabled:
        return "Background alerts: off"
    last = status.last
    if last is None:
        return "Background alerts: on — no check yet"
    when = last.last_run_at.astimezone().strftime("%H:%M")
    outcome = "✓" if last.ok else f"✗ {last.error or 'some symbols failed'}"
    return f"Background alerts: on — last check {when} {outcome}"


def button_text(status: TimerStatus) -> str:
    """The short form on the button; the tooltip has the full `status_text`."""
    if status.error:
        return "Alerts: error"
    if not status.enabled:
        return "Alerts: off"
    last = status.last
    if last is None:
        return "Alerts: on"
    when = last.last_run_at.astimezone().strftime("%H:%M")
    return f"Alerts: on, checked {when} {'✓' if last.ok else '✗'}"


class AlertsToggle(QWidget):
    failed = Signal(str)  # switching them on or off did not work

    def __init__(self, jobs: SerialJobs, installer: Installer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._jobs = jobs
        self._installer = installer
        self._status = ""
        self.button = QPushButton()
        self.button.setCheckable(True)
        self.button.setEnabled(False)  # until the first status arrives
        self.button.setAccessibleName("Background alerts")
        self.button.clicked.connect(self._on_clicked)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.button)
        self._show("Alerts: checking…", "Background alerts: checking…")
        self.refresh_status()

    def status(self) -> str:
        """The full sentence, as the tooltip gives it."""
        return self._status

    def _show(self, short: str, full: str) -> None:
        self._status = full
        self.button.setText(short)
        self.button.setToolTip(f"{full}\n{SCHEDULE_NOTE}")
        self.button.setAccessibleDescription(full)

    def refresh_status(self) -> None:
        self._jobs.submit(self._installer.status, self._on_status, self._on_status_error)

    def _on_clicked(self, checked: bool) -> None:
        self.button.setEnabled(False)
        word = "on…" if checked else "off…"
        self._show(f"Alerts: turning {word}", f"Background alerts: turning {word}")
        action = self._installer.install if checked else self._installer.uninstall
        self._jobs.submit(action, self._on_changed, self._on_change_error)

    def _on_changed(self, _result: object) -> None:
        self.refresh_status()

    def _on_change_error(self, exc: Exception) -> None:
        message = exc.user_message if isinstance(exc, InstallError) else str(exc)
        self.button.setChecked(not self.button.isChecked())
        self.button.setEnabled(True)
        full = f"Background alerts: error — {message}"
        self._show("Alerts: error", full)
        self.failed.emit(full)

    def _on_status(self, status: TimerStatus) -> None:
        self.button.setChecked(status.enabled)
        self.button.setEnabled(True)
        self._show(button_text(status), status_text(status))

    def _on_status_error(self, exc: Exception) -> None:
        self.button.setEnabled(True)
        self._show("Alerts: error", f"Background alerts: error — {exc}")
