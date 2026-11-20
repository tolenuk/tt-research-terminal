from obb_terminal.ui.desk.feed import COALESCE_MS, DeskFeed
from obb_terminal.ui.jobs import SerialJobs


def make(qtbot, views=lambda: []):
    jobs = SerialJobs()
    feed = DeskFeed(views, lambda _s: None, None, jobs)
    got = []
    feed.updated.connect(got.append)
    return feed, got, jobs


def test_a_burst_of_changes_builds_one_snapshot(qtbot):
    feed, got, jobs = make(qtbot)
    feed.set_active(True)
    for _ in range(5):
        feed.request()
    qtbot.waitUntil(lambda: len(got) == 1, timeout=2000)
    qtbot.wait(COALESCE_MS * 2)
    assert len(got) == 1
    jobs.shutdown()


def test_nothing_is_built_while_no_dashboard_is_open(qtbot):
    feed, got, jobs = make(qtbot)
    feed.request()
    qtbot.wait(COALESCE_MS * 2)
    assert got == []
    jobs.shutdown()


def test_a_failed_build_does_not_stop_the_next_one(qtbot):
    calls = []

    def views():
        calls.append(1)
        return [object()] if len(calls) == 1 else []  # the first build trips over a bad row

    feed, got, jobs = make(qtbot, views)
    feed.set_active(True)
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=2000)
    qtbot.wait(COALESCE_MS)
    feed.request()
    qtbot.waitUntil(lambda: len(got) == 1, timeout=2000)
    jobs.shutdown()


def test_a_steady_stream_of_changes_still_builds(qtbot):
    feed, got, jobs = make(qtbot)
    feed.set_active(True)
    for _ in range(COALESCE_MS // 10 * 3):
        feed.request()
        qtbot.wait(10)
    assert got, "the build must not wait for the changes to stop"
    jobs.shutdown()
