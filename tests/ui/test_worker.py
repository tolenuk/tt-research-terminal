import gc
import threading
import weakref

from obb_terminal.data.market import DataTimeout, EngineUnavailable, NoData, SourceUnavailable
from obb_terminal.ui.worker import Loader


class Sink:
    def __init__(self):
        self.ok = []
        self.err = []
        self.threads = []

    def on_ok(self, value):
        self.threads.append(threading.current_thread())
        self.ok.append(value)

    def on_err(self, err):
        self.err.append(err)


def test_result_delivered_on_gui_thread(qtbot, loader, fake_md):
    sink = Sink()
    loader.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)
    qtbot.waitUntil(lambda: bool(sink.ok), timeout=2000)
    assert sink.ok[0].symbol == "AAPL"
    assert sink.threads[0] is threading.main_thread()


def test_submit_before_engine_is_queued(qtbot, fake_md):
    ld = Loader(timeout_s=1.0)
    sink = Sink()
    ld.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)
    qtbot.wait(100)
    assert sink.ok == [] and fake_md.calls == []
    with qtbot.waitSignal(ld.engineReady, timeout=2000):
        ld.start_engine(lambda: fake_md)
    qtbot.waitUntil(lambda: bool(sink.ok), timeout=2000)
    ld.shutdown()


def test_engine_failure_fails_pending(qtbot):
    ld = Loader(timeout_s=1.0)
    sink = Sink()
    ld.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)

    def boom():
        raise RuntimeError("no engine")

    with qtbot.waitSignal(ld.engineFailed, timeout=2000):
        ld.start_engine(boom)
    assert isinstance(sink.err[0], EngineUnavailable)
    ld.shutdown()


def test_submit_after_engine_failure_fails_async_and_clears_pending(qtbot):
    ld = Loader(timeout_s=1.0)

    def boom():
        raise RuntimeError("no engine")

    with qtbot.waitSignal(ld.engineFailed, timeout=2000):
        ld.start_engine(boom)
    sink = Sink()
    ld.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)
    assert sink.err == []  # delivered asynchronously, like any other result
    qtbot.waitUntil(lambda: bool(sink.err), timeout=2000)
    assert isinstance(sink.err[0], EngineUnavailable)
    assert sink.err[0].user_message == "Data engine failed to load"
    assert not ld.is_pending("q")
    assert sink.ok == []
    ld.shutdown()


def test_superseded_submit_after_engine_failure_reports_only_latest(qtbot):
    ld = Loader(timeout_s=1.0)

    def boom():
        raise RuntimeError("no engine")

    with qtbot.waitSignal(ld.engineFailed, timeout=2000):
        ld.start_engine(boom)
    first, second = Sink(), Sink()
    ld.submit("q", lambda md: md.quote("A"), first.on_ok, first.on_err)
    ld.submit("q", lambda md: md.quote("B"), second.on_ok, second.on_err)
    qtbot.waitUntil(lambda: bool(second.err), timeout=2000)
    qtbot.wait(50)
    assert first.err == [] and len(second.err) == 1
    ld.shutdown()


def test_superseded_key_result_dropped(qtbot, loader, fake_md):
    gate = threading.Event()
    fake_md.results["quote"] = lambda s: (gate.wait(2), s)[1] if s == "OLD" else s
    sink = Sink()
    loader.submit("k", lambda md: md.quote("OLD"), sink.on_ok, sink.on_err)
    loader.submit("k", lambda md: md.quote("NEW"), sink.on_ok, sink.on_err)
    qtbot.waitUntil(lambda: bool(sink.ok), timeout=2000)
    gate.set()
    qtbot.wait(200)
    assert sink.ok == ["NEW"]


def test_stale_generation_dropped_but_unscoped_kept(qtbot, loader, fake_md):
    gate = threading.Event()
    fake_md.gates["quote"] = gate
    scoped, unscoped = Sink(), Sink()
    loader.submit("a", lambda md: md.quote("AAPL"), scoped.on_ok, scoped.on_err)
    loader.submit("b", lambda md: md.quote("AAPL"), unscoped.on_ok, unscoped.on_err, scoped=False)
    loader.new_generation()
    gate.set()
    qtbot.waitUntil(lambda: bool(unscoped.ok), timeout=2000)
    qtbot.wait(100)
    assert scoped.ok == [] and scoped.err == []


def test_timeout_calls_on_err_and_ignores_late_result(qtbot, loader, fake_md):
    gate = threading.Event()
    fake_md.gates["quote"] = gate
    sink = Sink()
    loader.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err, timeout_s=0.2)
    qtbot.waitUntil(lambda: bool(sink.err), timeout=2000)
    assert isinstance(sink.err[0], DataTimeout)
    assert not loader.is_pending("q")
    gate.set()
    qtbot.wait(200)
    assert sink.ok == []


def test_network_error_sets_offline_and_success_restores(qtbot, loader, fake_md):
    fake_md.results["quote"] = SourceUnavailable("offline", reason="network")
    sink = Sink()
    with qtbot.waitSignal(loader.onlineChanged, timeout=2000) as blocker:
        loader.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)
    assert blocker.args == [False]
    fake_md.results["quote"] = lambda s: s
    with qtbot.waitSignal(loader.onlineChanged, timeout=2000) as blocker:
        loader.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)
    assert blocker.args == [True]


def test_rate_limit_does_not_flip_offline(qtbot, loader, fake_md):
    fake_md.results["quote"] = SourceUnavailable("slow", reason="rate_limited")
    sink = Sink()
    with qtbot.assertNotEmitted(loader.onlineChanged, wait=300):
        loader.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)
    qtbot.waitUntil(lambda: bool(sink.err), timeout=2000)


def test_unexpected_exception_wrapped(qtbot, loader):
    sink = Sink()

    def boom(md):
        raise KeyError("x")

    loader.submit("q", boom, sink.on_ok, sink.on_err)
    qtbot.waitUntil(lambda: bool(sink.err), timeout=2000)
    assert sink.err[0].user_message == "Unexpected error — see log"


def test_is_pending(qtbot, loader, fake_md):
    gate = threading.Event()
    fake_md.gates["quote"] = gate
    sink = Sink()
    loader.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)
    assert loader.is_pending("q")
    gate.set()
    qtbot.waitUntil(lambda: bool(sink.ok), timeout=2000)
    assert not loader.is_pending("q")


def test_no_data_error_delivered(qtbot, loader, fake_md):
    fake_md.results["quote"] = NoData("none")
    sink = Sink()
    loader.submit("q", lambda md: md.quote("AAPL"), sink.on_ok, sink.on_err)
    qtbot.waitUntil(lambda: bool(sink.err), timeout=2000)
    assert isinstance(sink.err[0], NoData)


_KEEP_ALIVE = []


def test_shutdown_with_task_in_flight_does_not_keep_loader_alive(qtbot, fake_md):
    # Regression: each backstop QTimer is a child of the Loader and its timeout slot held a
    # strong reference to the Loader. After shutdown() deleteLater()s the timers, dropping the
    # last Python reference left the Loader alive only through that slot; when the deferred
    # delete later ran, the timer's destructor released the slot, which destroyed the Loader,
    # which deleted the half-destroyed timer again (double delete -> SIGSEGV/SIGBUS).
    ld = Loader(timeout_s=1.0)
    with qtbot.waitSignal(ld.engineReady, timeout=2000):
        ld.start_engine(lambda: fake_md)
    fake_md.gates["quote"] = threading.Event()
    ld.submit("q", lambda md: md.quote("AAPL"), lambda q: None, lambda e: None, scoped=False)
    qtbot.waitUntil(lambda: len(fake_md.calls_to("quote")) == 1, timeout=2000)
    fake_md.release_all()
    assert ld.shutdown()
    ref = weakref.ref(ld)
    del ld
    gc.collect()
    survivor = ref()
    if survivor is not None:
        _KEEP_ALIVE.append(survivor)  # report a failure instead of crashing the next event loop
    assert survivor is None, "Loader kept alive by a child timer's slot after shutdown"
