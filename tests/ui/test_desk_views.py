"""The dashboards' pure logic: what each page shows for a given snapshot."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from obb_terminal.ai.models import Action, Conviction, TradeCall
from obb_terminal.domain.models import Dividend
from obb_terminal.domain.targets import Indicator, SymbolTargets, Target, ThesisBreak, evaluate
from obb_terminal.journal.models import MIN_SAMPLE, Kind, Outcome, OutcomeStatus
from obb_terminal.ui.desk.data import DeskEntry, DeskSnapshot, build_snapshot
from obb_terminal.ui.desk.views import (
    Urgency,
    brief_items,
    brief_title,
    call_progress,
    call_rows,
    income_months,
    income_rows,
    ladder_rows,
    next_ex_dates,
    value_points,
)
from obb_terminal.ui.watchlist_tab.model import RowState, RowView
from tests.fakes import make_cached_report, make_valuation_inputs
from tests.journal.samples import snap

TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def entry(symbol="AAPL", st=None, report=None, **changes) -> DeskEntry:
    inputs = replace(make_valuation_inputs(symbol), **changes)
    view = RowView(symbol, RowState.READY, evaluate(inputs, st), inputs)
    return build_snapshot([view], lambda _s: report, None, TODAY).entries[0]


def snapshot(*entries, rows=()) -> DeskSnapshot:
    return DeskSnapshot(TODAY, NOW, tuple(entries), tuple(rows), journal_on=True)


def price_target(symbol, level, thesis=None):
    return SymbolTargets(
        symbol, (Target(Indicator.PRICE, level),),
        thesis=None if thesis is None else ThesisBreak(thesis),
    )


def scored(sid, h=20, excess=0.042, when=NOW):
    return Outcome(sid, h, OutcomeStatus.DONE, None, TODAY, TODAY, 100, 104, 1, 1, 0.05, 0.01, excess, 0.01, when)


# --- build_snapshot ---------------------------------------------------------------------------


def test_snapshot_keeps_loading_and_failed_symbols_without_scores():
    views = [RowView("AAPL", RowState.LOADING), RowView("BAD", RowState.ERROR)]
    entries = build_snapshot(views, lambda _s: None, None, TODAY).entries
    assert [e.loading for e in entries] == [True, False]
    assert entries[0].score is None and entries[1].score is None


def test_snapshot_without_a_journal_says_so():
    snap_ = build_snapshot([], lambda _s: None, None, TODAY)
    assert snap_.journal_on is False and snap_.journal_rows == ()


# --- Morning brief ----------------------------------------------------------------------------


def test_brief_puts_a_broken_thesis_before_a_hit_target():
    hit = entry("AAPL", price_target("AAPL", 400.0))
    broken = entry("SHEL.L", price_target("SHEL.L", 1.0, thesis=500.0))
    items = brief_items(snapshot(hit, broken))
    assert [(i.symbol, i.urgency) for i in items] == [
        ("SHEL.L", Urgency.THESIS), ("AAPL", Urgency.TARGET)]
    assert items[0].what == "Thesis break crossed"
    assert "500" in items[0].why
    assert items[1].what == "Price target hit"


def test_brief_ignores_targets_not_hit_and_disabled_targets():
    far = entry("AAPL", price_target("AAPL", 1.0))
    off = SymbolTargets("SHEL.L", (Target(Indicator.PRICE, 500.0, enabled=False),))
    assert brief_items(snapshot(far, entry("SHEL.L", off))) == []


def test_brief_counts_several_hit_targets_as_one_item():
    st = SymbolTargets("AAPL", (Target(Indicator.PRICE, 400.0), Target(Indicator.PE, 500.0)))
    items = brief_items(snapshot(entry("AAPL", st)))
    assert [i.what for i in items] == ["2 buy targets hit"]


def test_brief_reports_a_big_outlook_move_from_the_journal():
    rows = [
        (snap(day=TODAY - timedelta(days=1), strength=71.0, id=1), {}),
        (snap(day=TODAY, strength=62.0, id=2), {}),
    ]
    items = brief_items(snapshot(entry("AAPL"), rows=rows))
    assert [i.what for i in items] == ["Outlook score fell 9 points"]
    assert items[0].urgency is Urgency.OUTLOOK


def test_brief_skips_small_or_stale_outlook_moves():
    small = [(snap(day=TODAY - timedelta(days=1), strength=71.0, id=1), {}),
             (snap(day=TODAY, strength=68.0, id=2), {})]
    stale = [(snap(day=TODAY - timedelta(days=40), strength=71.0, id=1), {}),
             (snap(day=TODAY - timedelta(days=30), strength=40.0, id=2), {})]
    assert brief_items(snapshot(rows=small)) == []
    assert brief_items(snapshot(rows=stale)) == []


def test_brief_reports_a_recently_scored_and_a_ready_to_score_call():
    new = snap(symbol="AAPL", kind=Kind.AI, day=TODAY - timedelta(days=40), id=1)
    old = snap(symbol="MSFT", kind=Kind.AI, day=TODAY - timedelta(days=100), id=2)
    old = replace(old, symbol="SHEL.L")
    rows = [(new, {20: scored(1)}), (old, {20: scored(2, when=NOW - timedelta(days=60))})]
    items = brief_items(snapshot(entry("AAPL"), entry("SHEL.L"), rows=rows))
    assert [(i.symbol, i.what) for i in items] == [
        ("AAPL", "AI call scored at 20 days"), ("SHEL.L", "AI call ready to score at 60 days")]
    assert items[0].why == "+4.2% against SPY"


def test_brief_lists_a_failed_symbol_last():
    failed = DeskEntry("BAD", None, None, None, None, "Data source unavailable")
    items = brief_items(snapshot(failed, entry("AAPL", price_target("AAPL", 400.0))))
    assert [i.symbol for i in items] == ["AAPL", "BAD"]
    assert items[1].why == "Data source unavailable"


def test_brief_title_counts_decisions_and_reading():
    hit = entry("AAPL", price_target("AAPL", 400.0))
    rows = [(snap(day=TODAY - timedelta(days=1), strength=71.0, id=1), {}),
            (snap(day=TODAY, strength=62.0, id=2), {})]
    assert brief_title(brief_items(snapshot(hit, rows=rows))) == "1 decision waiting, 1 thing to read"
    assert brief_title([]) == "Nothing needs a look today"


# --- Value map --------------------------------------------------------------------------------


def test_value_points_rank_by_upside_and_explain_what_is_left_out():
    points, left_out = value_points(snapshot(entry("AAPL"), entry("SHEL.L"), entry("SPY")))
    margins = [p.upside for p in points]
    assert margins == sorted(margins, reverse=True) and {p.symbol for p in points} == {"AAPL", "SHEL.L"}
    assert [s for s, _ in left_out] == ["SPY"]
    assert "ETF" in left_out[0][1]
    assert all(0 <= p.outlook <= 100 for p in points)


# --- Entry ladder -----------------------------------------------------------------------------


def test_ladder_shows_levels_as_percent_from_the_price():
    call = TradeCall(Action.BUY, Conviction.HIGH, 270.0, 300.0, 240.0, 400.0, "x", ())
    e = entry("AAPL", price_target("AAPL", 300.0), make_cached_report("AAPL", call=call), price=300.0)
    row = ladder_rows(snapshot(e))[0]
    assert row.buy == 0.0 and row.zone == (-0.1, 0.0) and row.thesis == -0.2
    assert row.fair is not None and row.has_target


def test_ladder_prefers_your_thesis_break_and_sorts_closest_target_first():
    near = entry("AAPL", price_target("AAPL", 330.0, thesis=250.0), price=336.64)
    far = entry("SHEL.L", price_target("SHEL.L", 20.0))
    none = entry("SPY")
    rows = ladder_rows(snapshot(none, far, near))
    assert [r.symbol for r in rows] == ["AAPL", "SHEL.L", "SPY"]
    assert round(rows[0].thesis, 3) == round(250.0 / 336.64 - 1, 3)
    assert rows[2].buy is None and rows[2].has_target is False


# --- Call book --------------------------------------------------------------------------------


def test_call_rows_are_newest_first_and_only_ai_calls():
    calls = [
        (snap(day=TODAY, kind=Kind.SCORE, id=1), {}),
        (snap(symbol="AAPL", day=TODAY - timedelta(days=9), kind=Kind.AI, action="buy", conviction="high",
              entry_low=250.0, entry_high=260.0, thesis_break=200.0, target_price=400.0, id=2), {20: scored(2)}),
        (snap(symbol="MSFT", day=TODAY, kind=Kind.DEBATE, action="avoid", id=3), {}),
    ]
    rows = call_rows(snapshot(entry("AAPL"), rows=calls))
    assert [r.symbol for r in rows] == ["MSFT", "AAPL"]
    aapl = rows[1]
    assert aapl.action == "Buy (high)" and aapl.zone == "250.00 – 260.00"
    assert aapl.thesis == "Alive"
    assert aapl.horizons[0][0] == "+4.2% ✓" and aapl.horizons[1][0] == "pending"
    assert rows[0].thesis == "—"


def test_call_row_marks_a_broken_thesis_and_quotes_the_saved_reason():
    call = TradeCall(Action.BUY, Conviction.LOW, None, None, 400.0, None, "Margins collapse", ())
    report = make_cached_report("AAPL", call=call, created_at=NOW)
    s = snap(symbol="AAPL", day=TODAY, kind=Kind.AI, thesis_break=400.0, recorded_at=NOW, id=1)
    row = call_rows(snapshot(entry("AAPL", report=report), rows=[(s, {})]))[0]
    assert row.thesis == "Broken" and "Margins collapse" in row.thesis_tip


def test_call_progress_counts_scored_calls_towards_the_sample_gate():
    rows = [(snap(kind=Kind.AI, id=1), {20: scored(1)}), (snap(kind=Kind.DEBATE, id=2), {})]
    assert call_progress(snapshot(rows=rows)) == [("AI analyst", 1, MIN_SAMPLE), ("Debate", 0, MIN_SAMPLE)]


# --- Income desk ------------------------------------------------------------------------------


def test_income_months_end_this_month():
    months = income_months(TODAY)
    assert len(months) == 13 and months[0] == date(2025, 11, 1) and months[-1] == date(2026, 11, 1)


def test_income_groups_dividends_by_month_and_sorts_by_yield():
    pays = (Dividend(date(2026, 10, 3), 1.0), Dividend(date(2026, 10, 20), 0.5), Dividend(date(2024, 1, 1), 9.0))
    payer = entry("AAPL", dividends=pays, price=100.0)
    silent = entry("SHEL.L", dividends=())
    rows = income_rows(snapshot(silent, payer))
    assert [r.symbol for r in rows] == ["AAPL", "SHEL.L"]
    assert rows[0].paid[11] == 1.5 and rows[0].paid[12] is None
    assert rows[0].ttm_yield == 0.015
    assert rows[1].ttm_yield is None and rows[1].paid == (None,) * 13


def test_income_shows_the_yield_target_and_whether_it_is_hit():
    st = SymbolTargets("AAPL", (Target(Indicator.DIV_YIELD, 0.01),))
    pays = (Dividend(date(2026, 10, 3), 5.0),)
    row = income_rows(snapshot(entry("AAPL", st, dividends=pays, price=100.0)))[0]
    assert row.target == 0.01 and row.target_hit is True


def test_next_ex_dates_repeat_last_years_months():
    pays = (Dividend(date(2026, 8, 3), 1.0), Dividend(date(2026, 2, 3), 1.0))
    early = entry("AAPL", dividends=pays, price=100.0)
    soon = entry("SHEL.L", dividends=(Dividend(date(2025, 12, 3), 1.0),), price=100.0)
    assert next_ex_dates(snapshot(early, soon), TODAY) == [
        ("SHEL.L", date(2026, 12, 1)), ("AAPL", date(2027, 2, 1))]


def test_next_ex_date_can_be_this_month_until_it_has_happened():
    last_year = Dividend(date(2025, 11, 10), 1.0)
    waiting = entry("AAPL", dividends=(last_year,), price=100.0)
    done = entry("SHEL.L", dividends=(last_year, Dividend(date(2026, 11, 1), 1.0)), price=100.0)
    assert next_ex_dates(snapshot(waiting, done), TODAY) == [
        ("AAPL", date(2026, 11, 1)), ("SHEL.L", date(2027, 11, 1))]


def test_a_yearly_payer_from_this_month_last_year_shows_on_the_grid():
    row = income_rows(snapshot(entry("AAPL", dividends=(Dividend(date(2025, 11, 10), 4.0),), price=100.0)))[0]
    assert row.paid[0] == 4.0 and row.ttm_yield == 0.04


def test_a_disabled_yield_target_counts_as_no_target():
    st = SymbolTargets("AAPL", (Target(Indicator.DIV_YIELD, 0.01, enabled=False),))
    row = income_rows(snapshot(entry("AAPL", st, dividends=(Dividend(date(2026, 10, 3), 5.0),), price=100.0)))[0]
    assert row.target is None and row.target_hit is False


def test_brief_title_says_when_symbols_have_no_data():
    failed = DeskEntry("BAD", None, None, None, None, "Offline")
    assert brief_title(brief_items(snapshot(failed))) == "1 symbol without data"


def test_brief_leaves_out_journal_symbols_no_longer_watched():
    moved = [(snap(symbol="OLD", day=TODAY - timedelta(days=1), strength=71.0, id=1), {}),
             (snap(symbol="OLD", day=TODAY, strength=40.0, id=2), {}),
             (snap(symbol="OLD", kind=Kind.AI, day=TODAY - timedelta(days=100), id=3), {})]
    assert brief_items(snapshot(entry("AAPL"), rows=moved)) == []


# --- partial data -----------------------------------------------------------------------------


def test_partial_data_stays_off_the_map_and_says_why():
    points, left_out = value_points(snapshot(entry("AAPL", gaps=("targets",)), entry("SHEL.L")))
    assert [p.symbol for p in points] == ["SHEL.L"]
    assert left_out[0][0] == "AAPL" and "incomplete" in left_out[0][1]


def test_partial_data_shows_no_fair_value_on_the_ladder():
    row = ladder_rows(snapshot(entry("AAPL", gaps=("km",))))[0]
    assert row.fair is None


def test_snapshot_says_how_old_its_data_is():
    old, new = NOW - timedelta(hours=1), NOW
    snap_ = snapshot(entry("AAPL", fetched_at=new), entry("SHEL.L", fetched_at=old))
    assert snap_.data_from == old
    assert snapshot().data_from is None


def test_fair_value_gaps_keep_stocks_that_have_no_outlook_score():
    from obb_terminal.ui.desk.views import fair_value_gaps, value_points

    full = snapshot(entry("AAPL"), entry("SHEL.L"))
    no_score = replace(full, entries=tuple(replace(e, score=None) for e in full.entries))
    assert value_points(no_score)[0] == []
    assert {s for s, _ in fair_value_gaps(no_score)} == {s for s, _ in fair_value_gaps(full)} == {"AAPL", "SHEL.L"}
