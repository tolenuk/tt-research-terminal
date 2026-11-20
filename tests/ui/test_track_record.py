from datetime import timedelta

import pytest
from PySide6.QtCore import Qt

from obb_terminal.journal.models import MIN_SAMPLE, Kind, Outcome, OutcomeStatus
from obb_terminal.journal.store import Journal
from obb_terminal.ui.jobs import SerialJobs
from obb_terminal.ui.track_record import TrackRecordTab
from obb_terminal.ui.track_record.view import bucket_cells, load_view, recent_cells
from tests.journal.samples import DAY0, NOW, snap

TIP = Qt.ItemDataRole.ToolTipRole


def done(sid, excess, h=20):
    return Outcome(sid, h, OutcomeStatus.DONE, None, DAY0, DAY0, 100, 100, 1, 1, excess, 0.0, excess, 0.03, NOW)


def seed(journal, n=MIN_SAMPLE):
    outcomes = []
    for i in range(n):
        bull = i % 2 == 0
        sid = journal.record(snap(symbol=f"S{i:02d}", day=DAY0 + timedelta(days=i), direction=1 if bull else -1,
                                  strength=80.0 if bull else 20.0, band="Favourable" if bull else "Unfavourable"))
        outcomes.append(done(sid, 0.05 if bull else -0.03))
    ai = journal.record(snap(symbol="AAPL", day=DAY0, kind=Kind.AI, action="buy", conviction="high",
                             direction=1, strength=3.0, components=()))
    unable = Outcome(ai, 20, OutcomeStatus.UNABLE, "benchmark missing", None, None, None, None, None, None,
                     None, None, None, None, NOW)
    journal.write_outcomes([*outcomes, unable])


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "journal.sqlite3")


def test_view_has_every_kind_and_horizon_and_the_recent_calls(journal):
    seed(journal)
    view = load_view(journal)
    assert (view.snapshots, view.outcomes) == (MIN_SAMPLE + 1, MIN_SAMPLE + 1)
    assert [(b.kind, b.horizon) for b in view.buckets] == [(k, h) for k in Kind for h in (20, 60, 120)]
    assert len(view.recent) == MIN_SAMPLE + 1 and view.recent[0][0].symbol == "S29"


def test_bucket_text_with_and_without_enough_samples(journal):
    seed(journal)
    view = load_view(journal)
    score20 = next(b for b in view.buckets if b.kind is Kind.SCORE and b.horizon == 20)
    texts = [t for t, _ in bucket_cells(score20)]
    assert texts == ["Outlook score", "20d", "30 / 0", "100%", "+5.0%", "-3.0%", "+8.0 pts", "1.00", "3.0%"]
    ai20 = next(b for b in view.buckets if b.kind is Kind.AI and b.horizon == 20)
    texts = [t for t, _ in bucket_cells(ai20)]
    assert texts[2:5] == ["0 / 0", "n=0 — needs 30", "—"]
    tips = [tip for _, tip in bucket_cells(score20)]
    assert "beat the market by 2% or more" in tips[3]


def test_recent_rows_carry_meaning_in_text(journal):
    seed(journal)
    rows = {r[0].symbol: r for r in load_view(journal).recent}
    texts = [t for t, _ in recent_cells(rows["S00"])]
    assert texts[:5] == [DAY0.isoformat(), "S00", "Outlook score", "Favourable 80", "100.00 USD"]
    assert texts[5:] == ["+5.0% ✓", "pending", "pending"]
    ai = recent_cells(rows["AAPL"])
    assert ai[3][0] == "Buy (high)" and ai[5] == ("n/a", "AAPL 20 days: not scored — benchmark missing")


def test_tab_shows_the_journal_and_opens_symbols(qtbot, loader, journal):
    seed(journal)
    jobs = SerialJobs()
    tab = TrackRecordTab(loader, journal, jobs)
    qtbot.addWidget(tab)
    tab.set_active(True)
    qtbot.waitUntil(lambda: tab.calls_model.rowCount() == 9, timeout=2000)
    assert tab.summary.text().startswith("Journal: 31 snapshots · 31 outcomes")
    assert tab.calls_model.data(tab.calls_model.index(0, 3)) == "100%"
    assert tab.components_model.rowCount() > 0
    assert tab.recent_model.rowCount() == 31
    with qtbot.waitSignal(tab.symbolActivated) as opened:
        tab.recent_view.doubleClicked.emit(tab.recent_model.index(0, 1))
    assert opened.args == ["S29"]
    jobs.shutdown()


def test_score_now_scores_and_refreshes(qtbot, loader, journal, fake_md):

    journal.record(snap(symbol="AAPL"))
    fake_md.results["history"] = lambda s, r: (_ for _ in ()).throw(RuntimeError("boom"))
    jobs = SerialJobs()
    tab = TrackRecordTab(loader, journal, jobs)
    qtbot.addWidget(tab)
    tab.score_now()
    assert not tab.score_button.isEnabled()
    qtbot.waitUntil(lambda: tab.score_button.isEnabled(), timeout=5000)
    assert tab.status.text().startswith("Scored: 0 outcomes")
    assert "2 symbols failed" in tab.status.text()
    jobs.shutdown()


def test_without_a_journal_the_tab_says_so(qtbot, loader):
    jobs = SerialJobs()
    tab = TrackRecordTab(loader, None, jobs)
    qtbot.addWidget(tab)
    tab.set_active(True)
    assert tab.summary.text() == "Journal off"
    assert not tab.score_button.isEnabled()
    jobs.shutdown()
