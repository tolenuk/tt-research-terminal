"""Keeps the dashboards up to date: whenever the watchlist's data changes, build one snapshot on
SerialJobs and hand it to whichever pages are listening. Bursts of changes are coalesced."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date
from functools import partial

from PySide6.QtCore import QObject, QTimer, Signal

from obb_terminal.ai.models import CachedReport
from obb_terminal.journal.store import Journal
from obb_terminal.ui.desk.data import DeskSnapshot, build_snapshot
from obb_terminal.ui.jobs import SerialJobs
from obb_terminal.ui.watchlist_tab.model import RowView

log = logging.getLogger(__name__)

COALESCE_MS = 150


class DeskFeed(QObject):
    updated = Signal(object)  # DeskSnapshot

    def __init__(
        self,
        views: Callable[[], Sequence[RowView]],
        last_report: Callable[[str], CachedReport | None],
        journal: Journal | None,
        jobs: SerialJobs,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._views = views
        self._last_report = last_report
        self._journal = journal
        self._jobs = jobs
        self._active = False
        self._building = False
        self._again = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COALESCE_MS)
        self._timer.timeout.connect(self._build)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self.request()

    def request(self) -> None:
        # Not restarted while pending, so a steady stream of changes cannot put the build off forever.
        if self._active and not self._timer.isActive():
            self._timer.start()

    def _build(self) -> None:
        if self._building:  # one at a time; the running one may have read older data
            self._again = True
            return
        self._building = True
        task = partial(build_snapshot, list(self._views()), self._last_report, self._journal, date.today())
        self._jobs.submit(task, self._on_built, self._on_failed)

    def _settle(self) -> None:
        self._building = False
        if self._again:
            self._again = False
            self.request()

    def _on_built(self, snapshot: DeskSnapshot) -> None:
        self.updated.emit(snapshot)
        self._settle()

    def _on_failed(self, exc: Exception) -> None:
        log.error("Could not build the dashboards' data", exc_info=exc)
        self._settle()
