"""Analyst backed by headless Claude Code (`claude -p`), using the user's own subscription."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from obb_terminal.ai.errors import (
    AnalystBadOutput,
    AnalystCancelled,
    AnalystError,
    AnalystFailed,
    AnalystTimeout,
    ClaudeNotFound,
)
from obb_terminal.ai.models import (
    AnalystContext,
    AnalystReport,
    CachedReport,
    Debate,
    ReportKind,
    Role,
    RoleCase,
)
from obb_terminal.ai.prompt import build_judge_prompt, build_prompt, build_role_prompt
from obb_terminal.ai.runner import CompletedRun, RunCancelled, Runner, RunTimeout
from obb_terminal.ai.schema import (
    JUDGE_SCHEMA,
    REPORT_SCHEMA,
    ROLE_SCHEMA,
    parse_judge,
    parse_report,
    parse_role,
)
from obb_terminal.ai.store import ReportStore
from obb_terminal.config import AiConfig
from obb_terminal.domain.trade_call import sanitize
from obb_terminal.osdirs import IS_WINDOWS

log = logging.getLogger(__name__)

TOOLS = "WebSearch,WebFetch"
NO_TOOLS = ""
MAX_MESSAGE = 300
MIN_DEBATE_CASES = 2
# npm's Windows shims run through cmd.exe, which would re-parse the JSON schema argument.
BATCH_SUFFIXES = (".cmd", ".bat")
BATCH_SHIM_MESSAGE = (
    "Claude Code was installed with npm; install the native claude.exe "
    "(irm https://claude.ai/install.ps1 | iex) and run `claude` once to log in"
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def build_argv(
    exe: str, config: AiConfig, *, schema: dict = REPORT_SCHEMA, tools: str = TOOLS
) -> list[str]:
    """`tools=""` gives a pass with no tools at all (the debate judge). The prompt itself is
    sent on stdin: a Windows command line is capped at 32 KiB, prompts run up to 100 KB."""
    allowed = ["--allowedTools", tools] if tools else []
    return [
        exe, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--tools", tools,
        *allowed,
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--model", config.model,
        "--effort", config.effort,
    ]


def _clip(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:MAX_MESSAGE]


def _structured(run: CompletedRun) -> tuple[dict, dict]:
    """Return (result JSON, structured_output) or raise the matching AnalystError."""
    try:
        data = json.loads(run.stdout)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        if run.returncode != 0:
            detail = run.stderr.strip() or run.stdout.strip()
            raise AnalystFailed(_clip(detail or f"Claude Code exited with code {run.returncode}"))
        raise AnalystBadOutput()
    if run.returncode != 0 or data.get("is_error"):
        message = data.get("result")
        if not isinstance(message, str) or not message.strip():
            message = run.stderr.strip() or f"Claude Code exited with code {run.returncode}"
        raise AnalystFailed(_clip(message))
    output = data.get("structured_output")
    if not isinstance(output, dict):
        raise AnalystBadOutput()
    return data, output


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


# Homebrew's folders (Apple silicon, Intel): an app started from Finder or launchd inherits only
# PATH=/usr/bin:/bin:/usr/sbin:/sbin, so it never sees what the user's shell profile adds.
EXTRA_BIN_DIRS: tuple[Path, ...] = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))


def which_or_native(name: str) -> str | None:
    """PATH first, then the native installer's ~/.local/bin (a Claude Code installed moments
    ago by the Windows installer is not on this process's inherited PATH yet), then Homebrew's."""
    found = shutil.which(name)
    if found:
        return found
    native = Path.home() / ".local" / "bin" / (f"{name}.exe" if IS_WINDOWS else name)
    if native.is_file():
        return str(native)
    if IS_WINDOWS:
        return None
    return next((str(d / name) for d in EXTRA_BIN_DIRS if (d / name).is_file()), None)


class ClaudeCodeAnalyst:
    def __init__(
        self,
        runner: Runner,
        config: AiConfig,
        store: ReportStore,
        clock: Callable[[], datetime] = utcnow,
        which: Callable[[str], str | None] = which_or_native,
    ) -> None:
        self._runner = runner
        self._config = config
        self._store = store
        self._clock = clock
        self._which = which
        self._lock = threading.Lock()
        self._inflight: dict[str, Future[CachedReport]] = {}

    # --- Analyst --------------------------------------------------------------------------

    def availability(self) -> str | None:
        return None if self._exe() else ClaudeNotFound.default_message

    def last(self, symbol: str) -> CachedReport | None:
        try:
            return self._store.get(symbol)
        except Exception:
            log.exception("Could not read the stored analysis for %s", symbol)
            return None

    def analyse(self, ctx: AnalystContext) -> CachedReport:
        """Always runs Claude. A second call for a symbol already running joins that run."""
        return self._joined(ctx.symbol, lambda: self._run(ctx))

    def debate(self, ctx: AnalystContext) -> CachedReport:
        """Bull, bear and risk passes in parallel, then a judge pass without tools. Raises
        AnalystError subclasses; one failed role is tolerated and named in the report."""
        return self._joined(f"debate:{ctx.symbol}", lambda: self._run_debate(ctx))

    def _joined(self, key: str, work: Callable[[], CachedReport]) -> CachedReport:
        with self._lock:
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[key] = future
        assert future is not None
        if not owner:
            return future.result()
        try:
            entry = work()
        except BaseException as exc:
            with self._lock:
                self._inflight.pop(key, None)
            future.set_exception(exc if isinstance(exc, Exception) else AnalystCancelled())
            raise
        with self._lock:
            self._inflight.pop(key, None)
        future.set_result(entry)
        return entry

    def cancel(self) -> None:
        cancel_all = getattr(self._runner, "cancel_all", None)
        if callable(cancel_all):
            cancel_all()

    # --- internals ------------------------------------------------------------------------

    def _exe(self) -> str | None:
        configured = self._config.claude_path
        if configured:
            path = Path(configured).expanduser()
            return str(path) if path.is_file() and os.access(path, os.X_OK) else None
        return self._which("claude")

    def _claude(self, symbol: str) -> str:
        exe = self._exe()
        if exe is None:
            raise ClaudeNotFound(symbol=symbol)
        if Path(exe).suffix.lower() in BATCH_SUFFIXES:
            raise ClaudeNotFound(BATCH_SHIM_MESSAGE, symbol=symbol)
        return exe

    def _invoke(self, argv: list[str], prompt: str, symbol: str, what: str) -> tuple[dict, dict]:
        """One headless pass: (result JSON, structured output). Raises AnalystError subclasses."""
        log.info("Starting AI %s of %s with %s", what, symbol, self._config.model)
        with TemporaryDirectory(prefix="obb-ai-", ignore_cleanup_errors=True) as cwd:
            try:
                run = self._runner(argv, Path(cwd), self._config.timeout_s, prompt)
            except FileNotFoundError:
                raise ClaudeNotFound(symbol=symbol) from None
            except RunTimeout:
                log.warning("AI %s of %s timed out", what, symbol)
                raise AnalystTimeout(symbol=symbol) from None
            except RunCancelled:
                raise AnalystCancelled(symbol=symbol) from None
            except OSError as exc:
                log.exception("Could not start Claude Code")
                raise AnalystFailed(_clip(f"Could not start Claude Code: {exc}")) from exc
        try:
            return _structured(run)
        except (AnalystFailed, AnalystBadOutput) as exc:
            log.warning("AI %s of %s failed: %s (stderr: %s)", what, symbol, exc.user_message, _clip(run.stderr))
            raise

    def _run(self, ctx: AnalystContext) -> CachedReport:
        argv = build_argv(self._claude(ctx.symbol), self._config)
        data, output = self._invoke(argv, build_prompt(ctx), ctx.symbol, "analysis")
        try:
            report = parse_report(output)
        except AnalystBadOutput:
            log.warning("AI analysis of %s returned an unexpected format", ctx.symbol)
            raise
        duration = _number(data.get("duration_ms"))
        return self._finish(
            ctx, report, ReportKind.SINGLE, _number(data.get("total_cost_usd")),
            None if duration is None else int(duration),
        )

    def _run_role(self, exe: str, ctx: AnalystContext, role: Role) -> tuple[RoleCase, float | None]:
        argv = build_argv(exe, self._config, schema=ROLE_SCHEMA)
        data, output = self._invoke(argv, build_role_prompt(ctx, role), ctx.symbol, f"{role.value} case")
        return parse_role(output, role), _number(data.get("total_cost_usd"))

    def _run_debate(self, ctx: AnalystContext) -> CachedReport:
        exe = self._claude(ctx.symbol)
        started = time.monotonic()
        cases: list[RoleCase] = []
        costs: list[float | None] = []
        missing: list[Role] = []
        errors: list[AnalystError] = []
        with ThreadPoolExecutor(max_workers=len(Role), thread_name_prefix="obb-debate") as pool:
            futures = {role: pool.submit(self._run_role, exe, ctx, role) for role in Role}
            for role, future in futures.items():  # Role order, so cases stay ordered
                try:
                    case, cost = future.result()
                except (AnalystCancelled, ClaudeNotFound):
                    for pending in futures.values():
                        pending.cancel()  # not-yet-started passes; running ones see the runner's cancel
                    raise
                except AnalystError as exc:
                    log.warning("Debate %s case for %s failed: %s", role.value, ctx.symbol, exc.user_message)
                    missing.append(role)
                    errors.append(exc)
                    continue
                cases.append(case)
                costs.append(cost)
        if len(cases) < MIN_DEBATE_CASES:
            raise errors[0]
        argv = build_argv(exe, self._config, schema=JUDGE_SCHEMA, tools=NO_TOOLS)
        prompt = build_judge_prompt(ctx, cases, missing)
        data, output = self._invoke(argv, prompt, ctx.symbol, "debate judgement")
        report, disagreements, consensus = parse_judge(output)
        costs.append(_number(data.get("total_cost_usd")))
        known = [c for c in costs if c is not None]
        report = replace(report, debate=Debate(tuple(cases), disagreements, consensus, tuple(missing)))
        return self._finish(
            ctx, report, ReportKind.DEBATE, sum(known) if known else None,
            int((time.monotonic() - started) * 1000),
        )

    def _finish(
        self, ctx: AnalystContext, report: AnalystReport, kind: ReportKind, cost: float | None,
        duration_ms: int | None,
    ) -> CachedReport:
        if report.call is not None:
            report = replace(report, call=sanitize(report.call, ctx.price))
        entry = CachedReport(
            symbol=ctx.symbol,
            report=report,
            created_at=self._clock(),
            model=self._config.model,
            effort=self._config.effort,
            assumptions=ctx.assumptions,
            cost_usd=cost,
            duration_ms=duration_ms,
            kind=kind,
            currency=ctx.currency.value,
        )
        try:
            self._store.put(entry)
        except Exception:
            log.exception("Could not store the analysis of %s", ctx.symbol)
        return entry
