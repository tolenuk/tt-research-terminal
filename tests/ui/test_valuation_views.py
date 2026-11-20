from dataclasses import replace
from datetime import timedelta

import pytest
from PySide6.QtCore import Qt

from obb_terminal.domain.kpis import kpis
from obb_terminal.domain.models import Component, Kpi, KpiFormat, Mark
from obb_terminal.domain.scoring import score
from obb_terminal.domain.valuation import default_assumptions, value
from obb_terminal.ui import fmt
from obb_terminal.ui.theme import TOKENS
from obb_terminal.ui.valuation.fair_value import (
    GAUGE_HEIGHT,
    FairValueView,
    ValueGauge,
    gauge_scale,
)
from obb_terminal.ui.valuation.kpi_table import KpiTable
from obb_terminal.ui.valuation.score import ScoreView
from tests.fakes import make_valuation_inputs


def computed(symbol):
    inputs = make_valuation_inputs(symbol)
    a = default_assumptions(inputs)
    v = value(inputs, a)
    return inputs, a, v, score(inputs, v, a)


def test_gauge_scale():
    assert gauge_scale(100.0, 150.0, 80.0, 200.0) == pytest.approx((68.0, 212.0))
    assert gauge_scale(100.0, None, None, None) == pytest.approx((90.0, 110.0))
    assert gauge_scale(100.0, -5.0, None, 300.0) == pytest.approx((80.0, 320.0))


def test_fmt_helpers():
    assert fmt.fmt_age(timedelta(seconds=30)) == "just now"
    assert fmt.fmt_age(timedelta(minutes=5)) == "5 min ago"
    assert fmt.fmt_age(timedelta(hours=3)) == "3 h ago"
    assert fmt.fmt_age(timedelta(days=3)) == "3 d ago"
    _, a, _, _ = computed("AAPL")
    assert fmt.fmt_assumptions(a) == "discount 9.00% · growth 15.00% · terminal 2.50% · dividend growth 3.00%"
    assert fmt.fmt_kpi(Kpi("pe", "P/E", 12.34, KpiFormat.MULTIPLE, Mark.GOOD)) == "12.3x"
    assert fmt.fmt_kpi(Kpi("roe", "ROE", 0.25, KpiFormat.PERCENT, Mark.GOOD)) == "25.00%"
    assert fmt.fmt_kpi(Kpi("de", "Debt/Equity", 0.784, KpiFormat.RATIO, Mark.NEUTRAL)) == "0.78"
    assert fmt.fmt_kpi(Kpi("fpe", "Forward P/E", 9.0, KpiFormat.MULTIPLE, Mark.GOOD, "Yahoo")) == "9.0x (Yahoo)"
    assert fmt.fmt_kpi(Kpi("pe", "P/E", None, KpiFormat.MULTIPLE, Mark.MISSING, "negative earnings")) == (
        "— (negative earnings)"
    )


def test_fair_value_view_aapl(qtbot):
    view = FairValueView()
    qtbot.addWidget(view)
    inputs, _, v, _ = computed("AAPL")
    view.show_valuation(v, inputs.analyst)
    assert view.table.rowCount() == 4
    assert view.table.item(1, 1).text() == "328.09 USD"
    assert view.blended_label.text().startswith("Fair value ") and "USD" in view.blended_label.text()
    assert view.margin_label.text().endswith(v.label.value)
    assert view.na_label.isHidden()
    assert view.gauge.fair == pytest.approx(v.blended)
    assert (view.gauge.low, view.gauge.high) == (215.0, 405.0)


def test_fair_value_view_shows_reasons_inline(qtbot):
    view = FairValueView()
    qtbot.addWidget(view)
    inputs, _, v, _ = computed("HGLG11.SA")
    view.show_valuation(v, inputs.analyst)
    assert view.table.item(1, 1).text() == "n/a — no analyst coverage"
    assert view.table.item(0, 2).text() == "—"  # DCF shown but not weighted for an FII
    assert view.table.item(3, 2).text() == "100.00%"


def test_fair_value_view_etf(qtbot):
    view = FairValueView()
    qtbot.addWidget(view)
    _, _, v, _ = computed("SPY")
    view.show_valuation(v)
    assert view.na_label.text() == "Fair value not applicable to ETFs"
    assert not view.na_label.isHidden()
    assert view.table.isHidden() and view.gauge.isHidden()


def test_gauge_png(qtbot):
    gauge = ValueGauge()
    qtbot.addWidget(gauge)
    assert gauge.png_bytes() is None
    gauge.set_values(100.0, 150.0, 80.0, 200.0)
    assert gauge.png_bytes().startswith(b"\x89PNG")


def test_gauge_names_fair_value_and_price_on_the_bar(qtbot):
    gauge = ValueGauge()
    qtbot.addWidget(gauge)
    gauge.resize(600, GAUGE_HEIGHT)
    captions = []
    gauge._caption = lambda _p, centre, top, text, colour: captions.append((text, colour))
    gauge.set_values(336.64, 186.02, None, None, "USD")
    gauge.grab()
    assert [t for t, _ in captions] == ["Fair value 186.02", "Price 336.64"]
    assert captions[1][1] == TOKENS["down"]  # price above fair value


def test_gauge_caption_stays_inside_the_bar(qtbot):
    gauge = ValueGauge()
    qtbot.addWidget(gauge)
    gauge.resize(300, GAUGE_HEIGHT)
    gauge.set_values(100.0, 150.0, None, None)
    image = gauge.grab().toImage()  # captions at the very ends are slid inward, so this paints
    assert image.width() == 300


def test_gauge_png_for_the_report_leaves_the_captions_out(qtbot):
    gauge = ValueGauge()
    qtbot.addWidget(gauge)
    gauge.resize(600, GAUGE_HEIGHT)
    calls = []
    gauge._caption = lambda *a: calls.append(a)
    gauge.set_values(100.0, 150.0, 80.0, 200.0)
    assert gauge.png_bytes().startswith(b"\x89PNG")
    assert calls == [] and gauge.captions


def test_fit_rows_counts_the_header_while_the_table_is_still_hidden(qtbot):
    from PySide6.QtWidgets import QTableWidget

    from obb_terminal.ui.theme import COMPACT_ROW, fit_rows

    hidden, shown = QTableWidget(5, 2), QTableWidget(5, 2)
    for t in (hidden, shown):
        qtbot.addWidget(t)
    shown.show()
    fit_rows(hidden)
    fit_rows(shown)
    assert hidden.minimumHeight() == shown.minimumHeight()
    assert hidden.minimumHeight() >= 5 * COMPACT_ROW + hidden.horizontalHeader().sizeHint().height()


def test_both_valuation_tables_are_tall_enough_for_every_row(qtbot):
    table = KpiTable()
    qtbot.addWidget(table)
    table.show_kpis(kpis(make_valuation_inputs("SHEL.L")))
    rows = sum(table.rowHeight(r) for r in range(table.rowCount()))
    assert table.minimumHeight() >= rows
    view = FairValueView()
    qtbot.addWidget(view)
    _inputs, _a, v, _s = computed("AAPL")
    view.show_valuation(v)
    method_rows = sum(view.table.rowHeight(r) for r in range(view.table.rowCount()))
    assert view.table.minimumHeight() >= method_rows > 0


def test_score_view(qtbot):
    view = ScoreView()
    qtbot.addWidget(view)
    _, _, _, s = computed("AAPL")
    view.show_score(s)
    assert view.big.text() == f"{s.value:.0f}"
    assert view.band.text() == s.band.value
    assert view.bars[Component.MOMENTUM].value() > 0


def test_score_view_na_components_and_too_few(qtbot):
    view = ScoreView()
    qtbot.addWidget(view)
    _, _, _, s = computed("SPY")
    view.show_score(s)
    assert view.big.text() == "—"
    assert view.band.text() == "needs at least 2 components"
    assert view.values[Component.ANALYST].text() == "n/a — no analyst coverage"
    assert view.values[Component.UPSIDE].text() == "n/a — Fair value not applicable to ETFs"


def test_kpi_table(qtbot):
    table = KpiTable()
    qtbot.addWidget(table)
    table.show_kpis(kpis(make_valuation_inputs("SHEL.L")))
    assert table.rowCount() == 12
    labels = [table.item(r, 0).text() for r in range(12)]
    row = labels.index("Forward P/E")
    assert table.item(row, 1).text() == "9.0x (Yahoo)"
    assert table.item(row, 2).text() == "▲ good"
    assert "reporting-currency" in table.item(row, 1).toolTip()


def test_gauge_scale_edge_cases():
    assert gauge_scale(100.0, 100.0, None, None) == pytest.approx((90.0, 110.0))
    assert gauge_scale(0.0, None, None, None) == (0.0, 1.0)
    assert gauge_scale(-3.0, None, None, None) == (0.0, 1.0)
    assert gauge_scale(None, None, None, None) == (0.0, 1.0)
    lo, hi = gauge_scale(100.0, None, 80.0, None)
    assert lo < 80.0 and hi > 100.0


@pytest.mark.parametrize(
    "args",
    [
        (100.0, 100.0, None, None),
        (100.0, None, None, None),
        (100.0, 150.0, None, None),
        (100.0, 150.0, 80.0, None),
        (100.0, -50.0, 80.0, 200.0),
        (100.0, 150.0, -10.0, 900.0),
        (100.0, 5000.0, 80.0, 200.0),
    ],
)
def test_gauge_paint_smoke(qtbot, args):
    gauge = ValueGauge()
    qtbot.addWidget(gauge)
    gauge.set_values(*args, currency="USD")
    assert gauge.png_bytes().startswith(b"\x89PNG")


def test_gauge_accessible_description(qtbot):
    gauge = ValueGauge()
    qtbot.addWidget(gauge)
    gauge.set_values(180.0, 210.0, 150.0, 250.0, "USD")
    expected = "Price 180.00 USD is below fair value 210.00 USD; analyst range 150.00–250.00 USD"
    assert gauge.accessibleDescription() == expected
    assert gauge.toolTip() == expected
    gauge.set_values(240.0, 210.0, None, None, "USD")
    assert gauge.accessibleDescription() == "Price 240.00 USD is above fair value 210.00 USD"
    gauge.set_values(210.0, 210.0, None, 250.0, "EUR")
    assert gauge.accessibleDescription() == "Price 210.00 EUR is at fair value 210.00 EUR"
    gauge.set_values(210.0, None, None, None, "EUR")
    assert "210.00 EUR" in gauge.accessibleDescription()


def test_fair_value_view_accessibility(qtbot):
    view = FairValueView()
    qtbot.addWidget(view)
    inputs, _, v, _ = computed("AAPL")
    view.show_valuation(v, inputs.analyst)
    assert "USD" in view.gauge.accessibleDescription()
    assert view.gauge.accessibleDescription() == view.gauge.toolTip()
    assert view.table.accessibleName()
    assert "USD" in view.table.accessibleDescription()
    for label in (view.blended_label, view.margin_label):
        assert label.textFormat() == Qt.TextFormat.PlainText


def test_score_view_plain_text_and_reset(qtbot):
    view = ScoreView()
    qtbot.addWidget(view)
    _, _, _, s = computed("AAPL")
    view.show_score(s)
    assert view.big.textFormat() == Qt.TextFormat.PlainText
    assert all(bar.accessibleDescription() for bar in view.bars.values())
    assert view.bars[Component.MOMENTUM].accessibleDescription().startswith("Momentum")
    view.show_score(replace(s, components=()))
    assert all(bar.value() == 0 for bar in view.bars.values())
    assert all(value.text() == "—" for value in view.values.values())
    assert all("no data" in bar.accessibleDescription() for bar in view.bars.values())


def test_kpi_missing_mark_text(qtbot):
    table = KpiTable()
    qtbot.addWidget(table)
    table.show_kpis([Kpi("pe", "P/E", None, KpiFormat.MULTIPLE, Mark.MISSING)])
    assert table.item(0, 2).text() == "— n/a"
