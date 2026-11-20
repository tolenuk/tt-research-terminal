import threading
import time

from obb_terminal.ui.jobs import SerialJobs


def test_jobs_run_one_at_a_time_in_order(qtbot):
    jobs = SerialJobs()
    running, overlap, done = [], [], []

    def job(n):
        def run():
            running.append(n)
            overlap.append(len(running))
            time.sleep(0.02)
            running.remove(n)
            return n
        return run

    for n in range(4):
        jobs.submit(job(n), done.append, lambda exc: None)
    qtbot.waitUntil(lambda: len(done) == 4, timeout=2000)
    assert done == [0, 1, 2, 3] and max(overlap) == 1
    jobs.shutdown()


def test_results_and_errors_arrive_on_the_gui_thread(qtbot):
    jobs = SerialJobs()
    seen = []
    jobs.submit(lambda: 42, lambda v: seen.append((v, threading.current_thread())), seen.append)

    def boom():
        raise ValueError("bad level")

    jobs.submit(boom, seen.append, lambda e: seen.append((type(e), threading.current_thread())))
    qtbot.waitUntil(lambda: len(seen) == 2, timeout=2000)
    main = threading.main_thread()
    assert seen == [(42, main), (ValueError, main)]
    jobs.shutdown()


def test_shutdown_still_runs_queued_jobs_but_delivers_nothing(qtbot):
    jobs = SerialJobs()
    gate, ran, seen = threading.Event(), [], []
    jobs.submit(lambda: gate.wait(2), seen.append, seen.append)
    jobs.submit(lambda: ran.append("second"), seen.append, seen.append)  # queued behind the first
    threading.Timer(0.05, gate.set).start()
    assert jobs.shutdown(2000)
    assert ran == ["second"]
    qtbot.wait(50)
    assert seen == []


def test_shutdown_waits_and_drops_callbacks(qtbot):
    jobs = SerialJobs()
    gate, seen = threading.Event(), []
    jobs.submit(lambda: gate.wait(2), seen.append, seen.append)
    threading.Timer(0.05, gate.set).start()
    assert jobs.shutdown(2000)
    jobs.submit(lambda: 1, seen.append, seen.append)  # closed: ignored
    qtbot.wait(50)
    assert seen == []
