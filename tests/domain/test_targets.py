from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from obb_terminal.domain.models import AssetType, Currency, Dividend, ValuationInputs
from obb_terminal.domain.targets import (
    INDICATORS,
    OP,
    AlertEvent,
    AlertState,
    EventKind,
    Heat,
    Indicator,
    SymbolTargets,
    Target,
    TargetsFile,
    cell,
    default_level,
    ebitda_t,
    edit,
    evaluate,
    failed_row,
    format_level,
    heat,
    net_debt_t,
    notification_text,
    position_52w,
    readiness_key,
    transition,
    validate_level,
)

TODAY = date(2026, 10, 10)
NOW = datetime(2026, 10, 13, 14, 15, tzinfo=UTC)


def inputs(**kw) -> ValuationInputs:
    base = dict(symbol="TEST", name="Test", asset_type=AssetType.EQUITY, currency=Currency.USD,
                as_of=TODAY, price=100.0)
    return ValuationInputs(**{**base, **kw})


def t(ind, level, **kw) -> Target:
    return Target(ind, level, **kw)


def st(*targets, **kw) -> SymbolTargets:
    return SymbolTargets("TEST", tuple(targets), **kw)


# --- reference values (architecture §5) -----------------------------------------------------


def test_pe_reference():
    i = inputs(price=250.0, net_income=100e9, shares_outstanding=15e9)
    c = cell(i, Indicator.PE, t(Indicator.PE, 25))
    assert c.current == pytest.approx(37.5)
    assert c.buy_at == pytest.approx(166.666667)
    assert c.move == pytest.approx(-0.333333, abs=1e-6)
    assert (c.heat, c.intensity, c.hit) == (Heat.FAR, 1.0, False)


def test_ev_ebitda_shel_like_reference():
    i = inputs(currency=Currency.GBP, price=27.49375, shares_outstanding=8e9, fx_rate=0.7557,
               ebitda=56.664e9, total_debt=75.643e9, cash=29.630e9)
    assert ebitda_t(i) == pytest.approx(42.8209848e9)
    assert net_debt_t(i) == pytest.approx(34.7720241e9)
    c = cell(i, Indicator.EV_EBITDA, t(Indicator.EV_EBITDA, 4))
    assert c.current == pytest.approx(5.948533, abs=1e-6)
    assert c.buy_at == pytest.approx(17.0639894)
    assert c.move == pytest.approx(-0.379350, abs=1e-6)


def test_negative_net_debt_and_unreachable():
    i = inputs(price=50.0, shares_outstanding=1e9, ebitda=10e9, total_debt=5e9, cash=25e9)
    c = cell(i, Indicator.EV_EBITDA, t(Indicator.EV_EBITDA, 4))
    assert (c.current, c.buy_at) == (pytest.approx(3.0), pytest.approx(60.0))
    assert c.move == pytest.approx(0.2) and c.hit and c.heat is Heat.HIT
    c = cell(replace(i, total_debt=50e9, cash=0.0), Indicator.EV_EBITDA, t(Indicator.EV_EBITDA, 4))
    assert (c.buy_at, c.heat, c.reason) == (None, Heat.NA, "target unreachable")


def test_dividend_yield_reference():
    divs = tuple(Dividend(date(2026, m, 1), 1.10) for m in range(10, 0, -1)) + (
        Dividend(date(2025, 12, 1), 1.10), Dividend(date(2025, 11, 1), 1.10))
    i = inputs(currency=Currency.BRL, price=160.0, dividends=divs)
    c = cell(i, Indicator.DIV_YIELD, t(Indicator.DIV_YIELD, 0.08))
    assert c.current == pytest.approx(0.0825)
    assert c.buy_at == pytest.approx(165.0)
    assert c.move == pytest.approx(0.03125) and c.hit


def test_fcf_yield_reference():
    i = inputs(price=200.0, shares_outstanding=1e9, fcf_history=(12e9, 10e9, 8e9, 1e9))
    c = cell(i, Indicator.FCF_YIELD, t(Indicator.FCF_YIELD, 0.06))
    assert c.current == pytest.approx(0.05)
    assert c.buy_at == pytest.approx(166.666667)
    assert c.move == pytest.approx(-0.166667, abs=1e-6)


def test_pb_and_price():
    i = inputs(book_value_ps=20.0)
    assert cell(i, Indicator.PB, t(Indicator.PB, 1.5)).buy_at == pytest.approx(30.0)
    c = cell(i, Indicator.PRICE, t(Indicator.PRICE, 110))
    assert (c.current, c.buy_at, c.hit) == (100.0, 110, True)


def test_heat_boundaries():
    assert heat(-0.1)[0] is Heat.NEAR  # B 90 at P 100
    assert heat((89.99 - 100) / 100)[0] is Heat.FAR
    assert heat(0.0) == (Heat.HIT, 0.0)
    assert heat(None) == (Heat.NA, 0.0)
    assert heat(-0.15) == (Heat.FAR, pytest.approx(0.5))
    i = inputs()
    assert cell(i, Indicator.PRICE, t(Indicator.PRICE, 90)).heat is Heat.NEAR
    assert cell(i, Indicator.PRICE, t(Indicator.PRICE, 89.99)).heat is Heat.FAR
    assert cell(i, Indicator.PRICE, t(Indicator.PRICE, 100)).heat is Heat.HIT


# --- n/a reasons ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ind", "kw", "reason"),
    [
        (Indicator.PE, {}, "no earnings"),
        (Indicator.PE, {"net_income": -5e9, "shares_outstanding": 1e9}, "negative earnings"),
        (Indicator.PE, {"net_income": 5e9}, "shares unavailable"),
        (Indicator.PE, {"net_income": 5e9, "shares_outstanding": 1e9, "fx_rate": None},
         "currency conversion unavailable"),
        (Indicator.EV_EBITDA, {}, "no EBITDA"),
        (Indicator.EV_EBITDA, {"ebitda": -1e9}, "no EBITDA"),
        (Indicator.EV_EBITDA, {"ebitda": 1e9}, "no balance sheet"),
        (Indicator.EV_EBITDA, {"ebitda": 1e9, "total_debt": 0.0}, "shares unavailable"),
        (Indicator.EV_EBITDA, {"ebitda": 1e9, "total_debt": 0.0, "shares_outstanding": 1e9,
                               "fx_rate": None}, "currency conversion unavailable"),
        (Indicator.PB, {}, "no book value"),
        (Indicator.PB, {"book_value_ps": -2.0}, "no book value"),
        (Indicator.PB, {"book_value_ps": 2.0, "fx_rate": None}, "currency conversion unavailable"),
        (Indicator.DIV_YIELD, {}, "no dividends"),
        (Indicator.FCF_YIELD, {}, "no free cash flow"),
        (Indicator.FCF_YIELD, {"fcf_history": (-1e9, -2e9)}, "no free cash flow"),
        (Indicator.FCF_YIELD, {"fcf_history": (1e9,)}, "shares unavailable"),
    ],
)
def test_na_reasons(ind, kw, reason):
    c = cell(inputs(**kw), ind, t(ind, 0.05 if ind in (Indicator.DIV_YIELD, Indicator.FCF_YIELD) else 10))
    assert (c.current, c.buy_at, c.move, c.heat, c.reason) == (None, None, None, Heat.NA, reason)


def test_cell_without_target_shows_current_only():
    c = cell(inputs(book_value_ps=20.0), Indicator.PB, None)
    assert (c.current, c.buy_at, c.heat, c.reason) == (5.0, None, Heat.NA, None)


def test_disabled_target_shows_buy_at_but_never_hits():
    c = cell(inputs(), Indicator.PRICE, t(Indicator.PRICE, 120, enabled=False))
    assert (c.buy_at, c.hit, c.heat) == (120, False, Heat.NA)


# --- rows -----------------------------------------------------------------------------------


def test_b_all_is_the_lowest_valid_buy_at():
    i = inputs(price=250.0, net_income=100e9, shares_outstanding=15e9)
    row = evaluate(i, st(t(Indicator.PRICE, 180), t(Indicator.PE, 25), t(Indicator.PB, 2)))
    assert row.b_all == pytest.approx(166.667, abs=1e-3)
    assert row.m_all == pytest.approx(-0.3333, abs=1e-4)
    assert (row.n_valid, row.all_hit) == (2, False)  # P/B is n/a (no book value)
    assert [c.indicator for c in row.cells] == list(INDICATORS)


def test_all_hit_and_no_targets():
    i = inputs()
    assert evaluate(i, st(t(Indicator.PRICE, 101))).all_hit
    row = evaluate(i, None)
    assert (row.b_all, row.m_all, row.all_hit, row.n_valid) == (None, None, False, 0)


def test_disabled_targets_are_left_out_of_b_all():
    row = evaluate(inputs(), st(t(Indicator.PRICE, 120), t(Indicator.PB, 1, enabled=False)))
    assert (row.b_all, row.n_valid, row.all_hit) == (120, 1, True)


def test_fair_value_columns_and_etf():
    from tests.fakes import make_valuation_inputs

    row = evaluate(make_valuation_inputs("AAPL"), None)
    assert row.fair_value is not None and row.margin is not None and row.fair_reason is None
    etf = evaluate(make_valuation_inputs("SPY"), None)
    assert (etf.fair_value, etf.fair_reason) == (None, "Fair value not applicable to ETFs")


def test_position_52w():
    assert position_52w(150, 100, 200) == 0.5
    assert position_52w(250, 100, 200) == 1.0
    assert position_52w(50, 100, 200) == 0.0
    assert position_52w(150, 100, 100) is None
    assert position_52w(150, None, 200) is None


def test_readiness_order():
    def row(sym, m):
        return replace(failed_row(sym, "x", None), m_all=m)

    rows = [row("A", -0.05), row("B", 0.10), row("C", -0.30), row("D", None)]
    assert [r.symbol for r in sorted(rows, key=readiness_key)] == ["B", "A", "C", "D"]


def test_failed_row_keeps_targets_and_reason():
    row = failed_row("AAPL", "Timed out", st(t(Indicator.PE, 25)))
    assert row.error == "Timed out" and row.price is None
    assert all(c.reason == "Timed out" and c.heat is Heat.NA for c in row.cells)
    assert row.cells[1].target == t(Indicator.PE, 25)


# --- state machine --------------------------------------------------------------------------


def run(targets, price, **kw):
    s = st(*targets, **kw)
    return transition(s, evaluate(inputs(price=price), s), NOW)


def test_armed_fires_once_with_one_event():
    s, events = run([t(Indicator.PRICE, 100)], 100.0)
    assert s.targets[0].state is AlertState.FIRED and s.targets[0].fired_at == NOW
    hit = [e for e in events if e.kind is EventKind.TARGET_HIT]
    assert hit == [AlertEvent(EventKind.TARGET_HIT, "TEST", Indicator.PRICE, 100, 100.0, 100,
                              Currency.USD, 1, NOW)]
    s2, events2 = transition(s, evaluate(inputs(price=99.0), s), NOW)
    assert events2 == () and s2 == s


def test_hysteresis_exactly_two_percent():
    fired = t(Indicator.PRICE, 100, state=AlertState.FIRED, fired_at=NOW)
    s, _ = run([fired], 102.0, all_fired=True)
    assert s.targets[0].state is AlertState.FIRED
    s, events = run([fired], 102.01, all_fired=True)
    assert s.targets[0] == t(Indicator.PRICE, 100) and not s.all_fired and events == ()


def test_na_and_disabled_targets_do_not_move():
    fired_pe = t(Indicator.PE, 10, state=AlertState.FIRED, fired_at=NOW)
    s, events = run([fired_pe, t(Indicator.PRICE, 150, enabled=False)], 500.0)
    assert s.targets == (fired_pe, t(Indicator.PRICE, 150, enabled=False)) and events == ()


def test_failed_row_never_transitions():
    s = st(t(Indicator.PRICE, 100))
    assert transition(s, failed_row("TEST", "offline", s), NOW) == (s, ())


def test_all_hit_fires_once_then_clears_past_two_percent():
    targets = [t(Indicator.PRICE, 100), t(Indicator.PB, 10)]
    s = st(*targets)
    i = inputs(price=95.0, book_value_ps=10.0)  # P/B buy-at 100
    s, events = transition(s, evaluate(i, s), NOW)
    kinds = [e.kind for e in events]
    assert kinds.count(EventKind.TARGET_HIT) == 2 and kinds.count(EventKind.ALL_HIT) == 1
    all_event = events[-1]
    assert (all_event.n, all_event.buy_at, all_event.indicator) == (2, 100.0, None)
    assert s.all_fired
    _, again = transition(s, evaluate(i, s), NOW)
    assert again == ()
    s3, _ = transition(s, evaluate(replace(i, price=102.5), s), NOW)
    assert not s3.all_fired


def test_single_target_hit_sends_one_event_but_sets_all_fired():
    s, events = run([t(Indicator.PRICE, 100)], 100.0)
    assert [e.kind for e in events] == [EventKind.TARGET_HIT]
    assert s.all_fired
    s2, _ = run([t(Indicator.PRICE, 100)], 103.0, all_fired=True)
    assert not s2.all_fired  # clears past 2%, as for any symbol


def test_two_targets_hit_send_two_hits_and_one_all_hit():
    s = st(t(Indicator.PRICE, 100), t(Indicator.PB, 10))
    _, events = transition(s, evaluate(inputs(price=95.0, book_value_ps=10.0), s), NOW)
    kinds = [e.kind for e in events]
    assert kinds == [EventKind.TARGET_HIT, EventKind.TARGET_HIT, EventKind.ALL_HIT]
    assert events[-1].n == 2


def test_observed_at_is_preserved_by_transition():
    s, _ = run([t(Indicator.PRICE, 100)], 100.0, observed_at=NOW)
    assert s.observed_at == NOW


# --- editing --------------------------------------------------------------------------------


def test_edit_rearms_and_resets_symbol_flags():
    fired = st(t(Indicator.PRICE, 100, state=AlertState.FIRED, fired_at=NOW), all_fired=True,
               observed_at=NOW)
    s = edit(fired, "TEST", Indicator.PRICE, 90, True)
    assert s == SymbolTargets("TEST", (t(Indicator.PRICE, 90),), False, None)


def test_edit_orders_targets_and_deletes():
    s = edit(None, "TEST", Indicator.FCF_YIELD, 0.06, True)
    s = edit(s, "TEST", Indicator.PRICE, 90, False)
    assert [x.indicator for x in s.targets] == [Indicator.PRICE, Indicator.FCF_YIELD]
    s = edit(s, "TEST", Indicator.PRICE, None, True)
    assert [x.indicator for x in s.targets] == [Indicator.FCF_YIELD]


@pytest.mark.parametrize(
    ("ind", "level", "message"),
    [
        (Indicator.PE, 0, "Target must be a positive number"),
        (Indicator.PE, -3, "Target must be a positive number"),
        (Indicator.PE, float("nan"), "Target must be a positive number"),
        (Indicator.PRICE, float("inf"), "Target must be a positive number"),
        (Indicator.DIV_YIELD, 1.5, "Yield target must be at most 100%"),
        (Indicator.PE, "abc", "Target must be a number"),
        (Indicator.PE, 10**400, "Target must be a number"),
    ],
)
def test_validate_level_rejects(ind, level, message):
    with pytest.raises(ValueError, match=message):
        validate_level(ind, level)


def test_default_level_is_ten_percent_cheaper():
    assert default_level(Indicator.PRICE, 336.64) == 302.98
    assert default_level(Indicator.PE, 37.5) == 33.75
    assert default_level(Indicator.DIV_YIELD, 0.05) == 0.055
    assert default_level(Indicator.PE, None) is None
    assert default_level(Indicator.PE, -4.0) is None


def test_targets_file_put_and_get():
    f = TargetsFile()
    f = f.put(st(t(Indicator.PRICE, 1)))
    f = f.put(SymbolTargets("AAPL", (t(Indicator.PE, 25),)))
    assert [s.symbol for s in f.symbols] == ["AAPL", "TEST"]
    assert f.get("AAPL").targets[0].level == 25
    f = f.put(SymbolTargets("AAPL"))
    assert [s.symbol for s in f.symbols] == ["TEST"] and f.get("AAPL") is None


# --- text -----------------------------------------------------------------------------------


def test_format_level():
    assert format_level(Indicator.PE, 25) == "25"
    assert format_level(Indicator.PRICE, 1234.5) == "1,234.5"
    assert format_level(Indicator.DIV_YIELD, 0.08) == "8%"
    assert format_level(Indicator.FCF_YIELD, 0.065) == "6.5%"
    assert OP[Indicator.DIV_YIELD] == "≥" and OP[Indicator.PE] == "≤"


def test_notification_text():
    hit = AlertEvent(EventKind.TARGET_HIT, "AAPL", Indicator.PE, 25.0, 250.0, 166.666667,
                     Currency.USD, 1, NOW)
    assert notification_text(hit) == (
        "TT Research Terminal", "AAPL: P/E ≤ 25 hit — price 250.00 USD (buy-at 166.67)")
    both = AlertEvent(EventKind.ALL_HIT, "SHEL.L", None, None, 27.49, 27.5, Currency.GBP, 3, NOW)
    assert notification_text(both) == ("TT Research Terminal", "SHEL.L: all 3 buy targets hit — price 27.49 GBP")


def test_edit_accepts_an_indicator_value_string():
    s = edit(None, "TEST", "pe", 25, True)
    assert type(s.targets[0].indicator) is Indicator and s.targets[0].indicator is Indicator.PE
    s = edit(s, "TEST", "pe", None, True)
    assert s.targets == ()


# --- ETF look-through targets ----------------------------------------------------------------


def test_etf_targets_use_look_through_multiples():
    from obb_terminal.domain.targets import NOT_FOR_ETFS, Indicator, Target, cell
    from tests.fakes import make_etf_inputs

    i = make_etf_inputs("SPY")  # price 777, look-through P/E 24.04, P/B 5.08, P/CF 18.2
    pe = cell(i, Indicator.PE, Target(Indicator.PE, 20.0))
    assert pe.current == pytest.approx(1 / 0.0416)
    assert pe.buy_at == pytest.approx(777.0 * 20.0 * 0.0416)
    pb = cell(i, Indicator.PB, Target(Indicator.PB, 4.0))
    assert pb.buy_at == pytest.approx(777.0 * 4.0 * 0.197)
    fcf = cell(i, Indicator.FCF_YIELD, Target(Indicator.FCF_YIELD, 0.06))
    assert fcf.current == pytest.approx(0.05486)
    assert fcf.buy_at == pytest.approx(777.0 * 0.05486 / 0.06)
    ev = cell(i, Indicator.EV_EBITDA, Target(Indicator.EV_EBITDA, 10.0))
    assert ev.current is None and ev.reason == NOT_FOR_ETFS
    price = cell(i, Indicator.PRICE, Target(Indicator.PRICE, 700.0))
    assert price.buy_at == 700.0
