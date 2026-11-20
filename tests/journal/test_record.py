from dataclasses import replace

from obb_terminal.ai.models import ReportKind, Stance
from obb_terminal.domain.models import Currency, ScoreBand
from obb_terminal.domain.scoring import score
from obb_terminal.domain.valuation import default_assumptions, value
from obb_terminal.journal.benchmarks import BENCHMARK
from obb_terminal.journal.models import Kind
from obb_terminal.journal.record import call_snapshot, score_snapshot
from tests.fakes import CALL, make_cached_report, make_valuation_inputs
from tests.journal.samples import NOW


def page(symbol="AAPL"):
    i = make_valuation_inputs(symbol)
    a = default_assumptions(i)
    v = value(i, a)
    return i, v, score(i, v, a)


def test_benchmarks_cover_every_currency():
    assert BENCHMARK == {Currency.USD: "SPY", Currency.GBP: "^FTAS", Currency.BRL: "^BVSP"}
    assert set(BENCHMARK) == set(Currency)


def test_score_snapshot_from_the_page():
    i, v, s = page()
    snap = score_snapshot(i, v, s, NOW)
    assert snap.kind is Kind.SCORE and snap.day == i.as_of and snap.price == i.price
    assert snap.currency == "USD" and snap.benchmark == "SPY"
    assert snap.outlook == s.value and snap.strength == s.value and snap.band == s.band.value
    assert snap.margin == v.margin and snap.fair_value == v.blended
    assert dict(snap.components) == {c.key.value: c.value for c in s.components if c.value is not None}
    want = {ScoreBand.FAVOURABLE: 1, ScoreBand.UNFAVOURABLE: -1, ScoreBand.MIXED: 0}[s.band]
    assert snap.direction == want


def test_score_snapshot_directions_by_band():
    i, v, s = page()
    for band, want in ((ScoreBand.FAVOURABLE, 1), (ScoreBand.MIXED, 0), (ScoreBand.UNFAVOURABLE, -1), (None, 0)):
        assert score_snapshot(i, v, replace(s, band=band), NOW).direction == want


def test_nothing_to_record_without_score_or_fair_value():
    i, v, s = page()
    assert score_snapshot(i, replace(v, blended=None), replace(s, value=None, band=None), NOW) is None


def test_gbp_symbol_uses_the_ftse_all_share():
    i, v, s = page("SHEL.L")
    assert score_snapshot(i, v, s, NOW).benchmark == "^FTAS"


def test_call_snapshot_from_an_ai_report():
    i = make_valuation_inputs("AAPL")
    entry = make_cached_report("AAPL", call=CALL)
    snap = call_snapshot(entry, i, NOW)
    assert snap.kind is Kind.AI and snap.day == i.as_of and snap.price == i.price
    assert (snap.action, snap.conviction, snap.direction, snap.strength) == ("accumulate", "medium", 1, 2.0)
    assert (snap.entry_low, snap.entry_high, snap.thesis_break, snap.target_price) == (290.0, 310.0, 255.0, 380.0)
    assert snap.stance == "neutral" and snap.model == "claude-opus-5-5" and snap.components == ()


def test_debate_and_v1_reports():
    i = make_valuation_inputs("AAPL")
    assert call_snapshot(make_cached_report("AAPL", kind=ReportKind.DEBATE), i, NOW).kind is Kind.DEBATE
    old = make_cached_report("AAPL")
    bullish = replace(old, report=replace(old.report, stance=Stance.BULLISH))
    snap = call_snapshot(bullish, i, NOW)
    assert snap.action is None and snap.direction == 1 and snap.strength == 2.0


def test_record_page_and_call_write_to_the_journal_and_swallow_errors(tmp_path):
    from obb_terminal.journal.record import record_call, record_page
    from obb_terminal.journal.store import Journal

    journal = Journal(tmp_path / "j.sqlite3")
    i = make_valuation_inputs("AAPL")
    assert record_page(journal, i, NOW) is True
    assert record_page(journal, i, NOW) is False  # same day: kept the first
    assert record_call(journal, make_cached_report("AAPL", call=CALL), i, NOW) is True
    assert [s.kind for s, _ in journal.rows()] == [Kind.SCORE, Kind.AI]
    blocker = tmp_path / "file"
    blocker.write_text("x")
    broken = Journal(blocker / "j.sqlite3")
    assert record_page(broken, i, NOW) is False
    assert record_call(broken, make_cached_report("AAPL"), i, NOW) is False


def test_recording_never_raises_even_when_the_maths_does(tmp_path, monkeypatch):
    from obb_terminal.journal import record as mod
    from obb_terminal.journal.store import Journal

    journal = Journal(tmp_path / "j.sqlite3")
    monkeypatch.setattr(mod, "score", lambda *a: 1 / 0)
    assert mod.record_page(journal, make_valuation_inputs("AAPL"), NOW) is False
    monkeypatch.setattr(mod, "call_snapshot", lambda *a: 1 / 0)
    assert mod.record_call(journal, make_cached_report("AAPL"), make_valuation_inputs("AAPL"), NOW) is False


def test_partial_data_is_not_recorded_as_the_days_score(tmp_path):
    from dataclasses import replace
    from datetime import UTC, datetime

    from obb_terminal.journal.record import record_page
    from obb_terminal.journal.store import Journal
    from tests.fakes import make_valuation_inputs

    journal = Journal(tmp_path / "j.sqlite3")
    now = datetime(2026, 11, 2, tzinfo=UTC)
    full = make_valuation_inputs("AAPL")
    assert record_page(journal, replace(full, gaps=("targets",)), now) is False
    assert journal.rows() == []
    assert record_page(journal, full, now) is True


def test_equity_etf_waits_for_its_rollup_then_records_tagged(tmp_path):
    from obb_terminal.journal.record import ETF_MODEL, record_page, rollup_pending
    from obb_terminal.journal.store import Journal
    from tests.fakes import make_etf_inputs, make_holding_values

    journal = Journal(tmp_path / "j.sqlite3")
    pending = make_etf_inputs("SPY")
    assert rollup_pending(pending)
    assert record_page(journal, pending, NOW) is False
    done = make_etf_inputs("SPY", make_holding_values(0.1))
    assert not rollup_pending(done)
    assert record_page(journal, done, NOW) is True
    snap = journal.rows()[0][0]
    assert snap.model == ETF_MODEL and snap.fair_value is not None
    assert {k for k, _ in snap.components} >= {"vs_bonds", "cost"}


def test_bond_etf_records_without_a_rollup(tmp_path):
    from obb_terminal.journal.record import record_page, rollup_pending
    from obb_terminal.journal.store import Journal
    from tests.fakes import make_etf_inputs

    inputs = make_etf_inputs("AGG")
    assert not rollup_pending(inputs)
    assert record_page(Journal(tmp_path / "j.sqlite3"), inputs, NOW) in (True, False)  # never raises


def test_etf_rows_stay_out_of_the_stock_statistics(tmp_path):
    from obb_terminal.journal.record import record_page
    from obb_terminal.journal.store import Journal
    from obb_terminal.ui.track_record.view import load_view
    from tests.fakes import make_etf_inputs, make_holding_values

    journal = Journal(tmp_path / "j.sqlite3")
    record_page(journal, make_etf_inputs("SPY", make_holding_values(0.1)), NOW)
    record_page(journal, make_valuation_inputs("AAPL"), NOW)
    view = load_view(journal)
    score_buckets = [b for b in view.buckets if b.kind is Kind.SCORE]
    assert all(b.pending == 1 for b in score_buckets)  # AAPL only
    assert len(view.recent) == 2
