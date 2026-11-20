import errno
import json
import shutil
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from obb_terminal.ai import claude_code
from obb_terminal.ai.claude_code import ClaudeCodeAnalyst, build_argv, which_or_native
from obb_terminal.ai.errors import (
    AnalystBadOutput,
    AnalystCancelled,
    AnalystFailed,
    AnalystTimeout,
    ClaudeNotFound,
)
from obb_terminal.ai.models import ReportKind
from obb_terminal.ai.runner import CompletedRun, RunCancelled, RunTimeout
from obb_terminal.ai.schema import REPORT_SCHEMA
from obb_terminal.ai.store import ReportStore
from obb_terminal.config import AiConfig
from tests.ai.samples import ASSUMPTIONS, CALL, RESULT, make_context

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
DEFAULT_CONFIG = AiConfig()


class FakeRunner:
    def __init__(self, result=None, *, error=None, returncode=0, stdout=None, stderr="", gate=None):
        self.stdout = json.dumps(RESULT if result is None else result) if stdout is None else stdout
        self.error, self.returncode, self.stderr, self.gate = error, returncode, stderr, gate
        self.calls = []
        self.inputs = []
        self.entered = threading.Event()
        self.cancelled = False

    def __call__(self, argv, cwd, timeout_s, input=None):
        self.calls.append((argv, cwd, timeout_s, cwd.is_dir() and not any(cwd.iterdir())))
        self.inputs.append(input)
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return CompletedRun(self.returncode, self.stdout, self.stderr)

    def cancel_all(self):
        self.cancelled = True


def make(runner, tmp_path, *, which=lambda name: "/usr/bin/claude", config=DEFAULT_CONFIG):
    store = ReportStore(tmp_path / "analyses")
    return ClaudeCodeAnalyst(runner, config, store, clock=lambda: NOW, which=which), store


def test_argv_restricts_tools_and_passes_schema_model_effort():
    argv = build_argv("/usr/bin/claude", AiConfig(model="claude-sonnet-5-5", effort="low"))
    # The prompt goes on stdin: Windows caps a whole command line at 32 KiB.
    assert argv[:2] == ["/usr/bin/claude", "-p"] and argv[2].startswith("--")
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == REPORT_SCHEMA
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--effort") + 1] == "low"
    for flag in ("--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config"):
        assert flag in argv
    assert "--bare" not in argv  # S2: --bare breaks subscription login


def test_analyse_runs_parses_and_stores(tmp_path):
    runner = FakeRunner()
    analyst, store = make(runner, tmp_path)
    edited = replace(ASSUMPTIONS, discount_rate=0.12)
    entry = analyst.analyse(make_context(edited))
    assert entry.report.stance.value == "neutral"
    assert (entry.created_at, entry.model, entry.effort) == (NOW, "claude-opus-5-5", "medium")
    assert entry.assumptions == edited
    assert (entry.cost_usd, entry.duration_ms) == (0.44, 18234)
    assert store.get("AAPL") == entry
    argv, cwd, timeout_s, empty_dir = runner.calls[0]
    assert timeout_s == 300.0 and empty_dir and "obb-ai-" in cwd.name
    assert not Path(cwd).exists()  # temp dir removed after the run


def test_analyse_sanitises_the_call_against_the_price(tmp_path):
    structured = {**RESULT["structured_output"], "call": {**CALL, "target_price": 5000.0}}
    analyst, store = make(FakeRunner({**RESULT, "structured_output": structured}), tmp_path)
    entry = analyst.analyse(make_context())
    assert entry.kind is ReportKind.SINGLE
    call = entry.report.call
    assert call is not None and call.target_price is None and call.entry_high == 310.0
    assert any("implausible" in n for n in call.notes)
    assert store.get("AAPL") == entry


def test_analyse_always_runs_even_with_a_stored_report(tmp_path):
    runner = FakeRunner()
    analyst, _ = make(runner, tmp_path)
    analyst.analyse(make_context())
    analyst.analyse(make_context())
    assert len(runner.calls) == 2


def test_last_returns_stored_report_and_none_when_corrupt(tmp_path):
    analyst, store = make(FakeRunner(), tmp_path)
    assert analyst.last("AAPL") is None
    entry = analyst.analyse(make_context())
    assert analyst.last("AAPL") == entry
    store.path_for("AAPL").write_text("{broken")
    assert analyst.last("AAPL") is None


def test_store_write_failure_still_returns_report(tmp_path):
    analyst, store = make(FakeRunner(), tmp_path)
    store.root.write_text("not a directory")
    assert analyst.analyse(make_context()).report is not None


def test_temp_dir_cleanup_error_still_stores_and_returns_report(tmp_path, monkeypatch):
    real_rmtree = shutil.rmtree

    def rmtree_then_fail(path, *args, onexc=None, **kwargs):
        real_rmtree(path)  # really remove it, then report a late failure (e.g. EBUSY on NFS)
        try:
            raise OSError(errno.EBUSY, "Device or resource busy", path)
        except OSError as exc:
            onexc(real_rmtree, path, exc)

    monkeypatch.setattr(shutil, "rmtree", rmtree_then_fail)
    analyst, store = make(FakeRunner(), tmp_path)
    entry = analyst.analyse(make_context())
    assert entry.report is not None
    assert store.get("AAPL") == entry


def test_availability(tmp_path):
    assert make(FakeRunner(), tmp_path)[0].availability() is None
    missing, _ = make(FakeRunner(), tmp_path, which=lambda name: None)
    assert missing.availability() == "Claude Code not found — install it and run `claude` once to log in"
    configured, _ = make(FakeRunner(), tmp_path, config=AiConfig(claude_path=str(tmp_path / "nope")))
    assert configured.availability() is not None


def test_claude_missing_raises_not_found(tmp_path):
    analyst, _ = make(FakeRunner(), tmp_path, which=lambda name: None)
    with pytest.raises(ClaudeNotFound):
        analyst.analyse(make_context())
    analyst2, _ = make(FakeRunner(error=FileNotFoundError("claude")), tmp_path)
    with pytest.raises(ClaudeNotFound):
        analyst2.analyse(make_context())


@pytest.mark.parametrize(
    ("runner", "error", "message"),
    [
        (FakeRunner({**RESULT, "is_error": True, "result": "Not logged in · Please run /login"}),
         AnalystFailed, "Not logged in · Please run /login"),
        (FakeRunner(returncode=1, stdout="", stderr="Usage limit reached\n"), AnalystFailed, "Usage limit reached"),
        (FakeRunner(returncode=2, stdout="", stderr=""), AnalystFailed, "Claude Code exited with code 2"),
        (FakeRunner(error=RunTimeout()), AnalystTimeout, "Analysis timed out"),
        (FakeRunner(error=RunCancelled()), AnalystCancelled, "Analysis cancelled"),
        (FakeRunner({**RESULT, "structured_output": None}), AnalystBadOutput, "Analysis returned an unexpected format"),
        (FakeRunner(stdout="not json"), AnalystBadOutput, "Analysis returned an unexpected format"),
        (FakeRunner({**RESULT, "structured_output": {"summary": "x"}}), AnalystBadOutput,
         "Analysis returned an unexpected format"),
    ],
)
def test_error_paths(tmp_path, runner, error, message):
    analyst, store = make(runner, tmp_path)
    with pytest.raises(error) as info:
        analyst.analyse(make_context())
    assert info.value.user_message == message
    assert store.get("AAPL") is None


def test_long_error_message_is_clipped(tmp_path):
    analyst, _ = make(FakeRunner({**RESULT, "is_error": True, "result": "x " * 500}), tmp_path)
    with pytest.raises(AnalystFailed) as info:
        analyst.analyse(make_context())
    assert len(info.value.user_message) == 300


def test_second_call_joins_in_flight_run(tmp_path):
    gate = threading.Event()
    runner = FakeRunner(gate=gate)
    analyst, _ = make(runner, tmp_path)
    results = []
    threads = [threading.Thread(target=lambda: results.append(analyst.analyse(make_context()))) for _ in range(2)]
    threads[0].start()
    assert runner.entered.wait(2)
    threads[1].start()
    threads[1].join(0.2)
    assert threads[1].is_alive()  # parked on the in-flight future
    gate.set()
    for t in threads:
        t.join(5)
    assert len(runner.calls) == 1
    assert len(results) == 2 and results[0] is results[1]
    assert analyst._inflight == {}


def test_cancel_forwards_to_runner(tmp_path):
    runner = FakeRunner()
    analyst, _ = make(runner, tmp_path)
    analyst.cancel()
    assert runner.cancelled


def test_prompt_is_sent_on_stdin_not_argv(tmp_path):
    runner = FakeRunner()
    analyst, _ = make(runner, tmp_path)
    analyst.analyse(make_context())
    (argv, *_), prompt = runner.calls[0], runner.inputs[0]
    assert "You are" in prompt and len(prompt) > 500
    assert prompt not in argv


@pytest.mark.parametrize("name", ["claude.cmd", "CLAUDE.BAT"])
def test_windows_batch_shims_are_refused(tmp_path, name):
    """cmd.exe would re-parse the JSON schema argument; only the native claude.exe is safe."""
    runner = FakeRunner()
    analyst, _ = make(runner, tmp_path, which=lambda n: f"C:/npm/{name}")
    with pytest.raises(ClaudeNotFound) as info:
        analyst.analyse(make_context())
    assert "native" in str(info.value.user_message)
    assert runner.calls == []


def test_which_or_native_falls_back_to_the_native_install(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    monkeypatch.setattr(claude_code.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(claude_code, "IS_WINDOWS", True)
    assert which_or_native("claude") is None
    exe = tmp_path / ".local" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    assert which_or_native("claude") == str(exe)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: "C:/on/path/claude.exe")
    assert which_or_native("claude") == "C:/on/path/claude.exe"


def test_which_or_native_on_macos_also_looks_in_homebrew(monkeypatch, tmp_path):
    """An app started from Finder or launchd gets PATH=/usr/bin:/bin:/usr/sbin:/sbin."""
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    monkeypatch.setattr(claude_code.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(claude_code, "IS_WINDOWS", False)
    brew = tmp_path / "opt" / "homebrew" / "bin"
    monkeypatch.setattr(claude_code, "EXTRA_BIN_DIRS", (brew,))
    assert which_or_native("claude") is None
    brew.mkdir(parents=True)
    (brew / "claude").touch()
    assert which_or_native("claude") == str(brew / "claude")
    native = tmp_path / "home" / ".local" / "bin" / "claude"
    native.parent.mkdir(parents=True)
    native.touch()
    assert which_or_native("claude") == str(native)  # the native installer wins
