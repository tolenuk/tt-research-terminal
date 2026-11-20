"""`obb-terminal-alerts`: a one-shot background check of the watchlist's buy targets.

Run by the systemd user timer (alerts/systemd.py), the Windows scheduled task
(alerts/schtasks.py) or the macOS launchd agent (alerts/launchd.py). No Qt. Exit codes: 0 when the run
finished (even offline or busy; see alerts-status.json), 1 when the data engine or an install
step failed, 2 on bad arguments.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from datetime import date, datetime
from logging.handlers import RotatingFileHandler, WatchedFileHandler
from pathlib import Path

from obb_terminal.alerts.background import default_installer
from obb_terminal.alerts.engine import AlertEngine, utcnow
from obb_terminal.alerts.notify import default_notifier
from obb_terminal.alerts.store import AlertsStatus, StatusStore, TargetsRepo
from obb_terminal.alerts.systemd import InstallError, TimerStatus
from obb_terminal.data.market import DataError, MarketData, SourceUnavailable
from obb_terminal.domain.models import ValuationInputs
from obb_terminal.journal.scorer import BUDGET_S as BUDGET_S_SCORING
from obb_terminal.journal.scorer import LAST_SCORED, ScoringResult, run_scoring, scoring_due
from obb_terminal.journal.store import Journal, JournalError
from obb_terminal.parallel import fetch_all
from obb_terminal.store import Store, data_dir

log = logging.getLogger("obb_terminal.alerts")

BUDGET_S = 240.0
MARGIN_S = 15.0  # stop starting fetches this long before the budget ends
WORKERS = 4
ENGINE_WAIT_S = 120.0
ENGINE_ERROR = "Data engine unavailable"
OFFLINE = "offline"
UNEXPECTED = "Unexpected error — see log"

MarketDataFactory = Callable[[float], MarketData]  # engine wait in seconds -> MarketData
IS_WINDOWS = sys.platform == "win32"


def log_handler(directory: Path) -> logging.Handler:
    """POSIX: append to the app's log; WatchedFileHandler reopens it after the app rotates it.
    Windows: a log of its own, since a file open in this process blocks the app's rotation."""
    if IS_WINDOWS:
        return RotatingFileHandler(
            directory / "obb-terminal-alerts.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8"
        )
    return WatchedFileHandler(directory / "obb-terminal.log", encoding="utf-8")


def setup_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handler = log_handler(directory)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [alerts]: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _fetch_all(
    md: MarketData, symbols: list[str], workers: int, start_deadline: float, end_deadline: float
) -> tuple[list[ValuationInputs], list[tuple[str, DataError]]]:
    results, failures = fetch_all(md.valuation_inputs, symbols, workers, start_deadline, end_deadline)
    return list(results.values()), list(failures.items())


def run_check(
    md_factory: MarketDataFactory,
    engine: AlertEngine,
    watchlist: Store,
    status: StatusStore,
    *,
    budget_s: float = BUDGET_S,
    margin_s: float = MARGIN_S,
    workers: int = WORKERS,
    now: Callable[[], datetime] = utcnow,
) -> tuple[int, AlertsStatus]:
    """Check every watchlisted symbol that has an enabled target. Returns (exit code, status)."""
    start = time.monotonic()
    checked = failed = 0
    symbols: list[str] = []
    try:
        snapshot = engine.snapshot()
        if snapshot.error is not None:
            return _finish(status, AlertsStatus(now(), False, 0, 0, 0, 0, snapshot.error), 0)
        targets = snapshot.file
        symbols = [
            s for s in watchlist.load().state.symbols
            if (st := targets.get(s)) and (st.enabled_count() or st.thesis is not None)
        ]
        if not symbols:
            return _finish(status, AlertsStatus(now(), True, 0, 0, 0, 0), 0)
        try:
            md = md_factory(min(ENGINE_WAIT_S, max(0.0, budget_s - margin_s)))
        except DataError:
            log.exception("Data engine failed to start")
            return _finish(status, AlertsStatus(now(), False, 0, 0, len(symbols), 0, ENGINE_ERROR), 1)
        results, failures = _fetch_all(
            md, symbols, workers, start + budget_s - margin_s, start + budget_s
        )
        for symbol, exc in failures:
            log.info("Alert check failed for %s: %s", symbol, exc.user_message)
        checked, failed = len(results), len(failures)
        skipped = len(symbols) - checked - failed
        applied = engine.apply(results) if results else None
        error = applied.error if applied is not None else None
        if error is None and any(
            isinstance(e, SourceUnavailable) and e.reason == "network" for _, e in failures
        ):
            error = OFFLINE
        fired = len(applied.events) if applied is not None else 0
        ok = error is None and not failures and not skipped
        result = AlertsStatus(now(), ok, len(results), len(failures), skipped, fired, error)
        return _finish(status, result, 0)
    except Exception:
        log.exception("Unexpected error in alert check")
        remaining = max(0, len(symbols) - checked - failed)
        return _finish(status, AlertsStatus(now(), False, checked, failed, remaining, 0, UNEXPECTED), 1)


def shared_factory(factory: MarketDataFactory) -> MarketDataFactory:
    """Start the data engine at most once per run, for the alert check and the scoring."""
    made: list[MarketData] = []

    def get(wait_s: float) -> MarketData:
        if not made:
            made.append(factory(wait_s))
        return made[0]

    return get


RUN_LIMIT_S = 300.0  # TimeoutStartSec / ExecutionTimeLimit (systemd.py, schtasks.py); launchd sets none
RUN_SLACK_S = 30.0
MIN_SCORING_S = 30.0


def scoring_budget(started: float, now: float) -> float:
    """Seconds left for scoring before systemd would kill the run."""
    return RUN_LIMIT_S - RUN_SLACK_S - (now - started)


def run_daily_scoring(
    md_factory: MarketDataFactory, journal: Journal, today: date, *, wait_s: float = ENGINE_WAIT_S,
    budget_s: float = BUDGET_S_SCORING,
) -> ScoringResult | None:
    """Score the journal once per day within `budget_s`. None when already done today, too
    little time is left or the engine failed; the engine is not started when nothing is due.
    Never raises."""
    if budget_s < MIN_SCORING_S or not scoring_due(journal, today):
        return None
    try:
        if not journal.due(today):
            journal.set_meta(LAST_SCORED, today.isoformat())
            return ScoringResult(0, 0)
    except JournalError:
        return None
    started = time.monotonic()
    try:
        md = md_factory(min(wait_s, budget_s))
    except DataError:
        log.exception("Data engine failed to start for scoring")
        return None
    left = budget_s - (time.monotonic() - started)
    if left < MIN_SCORING_S:
        return None
    return run_scoring(md, journal, today, budget_s=left)


def _finish(store: StatusStore, result: AlertsStatus, code: int) -> tuple[int, AlertsStatus]:
    try:
        store.write(result)
    except OSError:
        log.exception("Could not write %s", store.path)
    log.info(
        "Alert check: ok=%s checked=%d failed=%d skipped=%d fired=%d error=%s",
        result.ok, result.checked, result.failed, result.skipped, result.fired, result.error,
    )
    return code, result


def _market_data(wait_s: float) -> MarketData:
    from obb_terminal.app import build_engine_factory
    from obb_terminal.data.engine import Engine

    engine = Engine()
    engine.start()
    return build_engine_factory(engine, wait_s)()


def describe(status: TimerStatus) -> str:
    state = "on" if status.enabled else "off"
    lines = [f"Background alerts: {state} (timer {'active' if status.active else 'inactive'})"]
    if status.error:
        lines.append(f"Error: {status.error}")
    last = status.last
    if last is None:
        lines.append("Last check: never")
    else:
        when = last.last_run_at.astimezone().strftime("%Y-%m-%d %H:%M")
        outcome = "ok" if last.ok else f"problem ({last.error or 'some symbols failed'})"
        lines.append(
            f"Last check: {when} — {outcome}; {last.checked} checked, {last.failed} failed, "
            f"{last.skipped} skipped, {last.fired} alerts"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="obb-terminal-alerts", description="Check TT Research Terminal watchlist buy targets once."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", action="store_true", help="install and start the timer")
    action.add_argument("--uninstall", action="store_true", help="stop and remove the timer")
    action.add_argument("--status", action="store_true", help="show the timer and last check")
    args = parser.parse_args(argv)
    setup_logging(data_dir())
    if args.install or args.uninstall:
        installer = default_installer()
        try:
            if args.install:
                installer.install()
            else:
                installer.uninstall()
        except InstallError as exc:
            print(f"obb-terminal-alerts: {exc.user_message}", file=sys.stderr)
            return 1
        print("Background alerts " + ("installed" if args.install else "removed"))
        return 0
    if args.status:
        print(describe(default_installer().status()))
        return 0
    started = time.monotonic()
    engine = AlertEngine(TargetsRepo(), default_notifier())
    factory = shared_factory(_market_data)
    code, _ = run_check(factory, engine, Store(), StatusStore())
    try:
        budget = scoring_budget(started, time.monotonic())
        run_daily_scoring(factory, Journal(), date.today(), budget_s=budget)
    except Exception:
        log.exception("Unexpected error in journal scoring")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
