from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from obb_terminal.ai.models import Point, Source
from obb_terminal.domain.kpis import kpis
from obb_terminal.domain.scoring import score
from obb_terminal.domain.valuation import default_assumptions, value
from obb_terminal.ui.valuation.report_export import (
    ReportSnapshot,
    build_report_html,
    default_report_path,
    documents_dir,
    export_pdf,
)
from tests.fakes import CALL, DEBATE, make_cached_report, make_profile, make_valuation_inputs

NOW = datetime(2026, 10, 10, 14, 5).astimezone()
PNG = b"\x89PNG placeholder"  # build_report_html never decodes it


def snapshot(symbol="AAPL", ai=True, gauge=PNG):
    inputs = make_valuation_inputs(symbol)
    a = default_assumptions(inputs)
    v = value(inputs, a)
    entry = make_cached_report(symbol) if ai else None
    return ReportSnapshot(make_profile(symbol), inputs, a, v, score(inputs, v, a), tuple(kpis(inputs)),
                          entry, gauge, NOW)


def test_html_has_every_section():
    text = build_report_html(snapshot())
    for heading in ("Fair value", "Outlook score", "Assumptions", "Key indicators", "AI analyst", "Sources"):
        assert heading in text
    assert "Apple Inc. (AAPL) · NMS" in text
    assert "336.64 USD" in text and "2026-10-10 14:05" in text
    assert "<img src='gauge.png'" in text
    assert "Generated from public data by a model — not investment advice." in text
    assert "Stance: Neutral" in text


def test_html_escapes_untrusted_text():
    s = snapshot()
    evil = replace(s.ai.report, summary="<script>alert(1)</script>",
                   pros=(Point("<b>bold</b>", "x & y"),),
                   sources=(Source("<i>t</i>", "https://a.com/?q=<x>"), Source("bad", "javascript:alert(1)")))
    text = build_report_html(replace(s, ai=replace(s.ai, report=evil)))
    assert "<script>" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<b>bold</b>" not in text and "&lt;b&gt;bold&lt;/b&gt;" in text
    assert "x &amp; y" in text
    assert "javascript:" not in text


def test_html_without_ai_or_gauge_and_for_etf():
    text = build_report_html(snapshot("SPY", ai=False, gauge=None))
    assert "No AI analysis has been run for this symbol." in text
    assert "gauge.png" not in text
    assert "Fair value not applicable to ETFs" in text


def real_png() -> bytes:
    image = QImage(120, 12, QImage.Format.Format_RGB32)
    image.fill(QColor("#FFB000"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def test_export_pdf_writes_pdf_with_gauge_image(qtbot, tmp_path):
    with_gauge = export_pdf(snapshot(gauge=real_png()), tmp_path / "reports" / "AAPL.pdf")
    without = export_pdf(snapshot(gauge=None), tmp_path / "reports" / "AAPL-plain.pdf")
    data = with_gauge.read_bytes()
    assert data.startswith(b"%PDF") and without.read_bytes().startswith(b"%PDF")
    assert len(data) > len(without.read_bytes())  # the gauge image is embedded


def test_export_pdf_unwritable_raises_oserror(qtbot, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OSError):
        export_pdf(snapshot(), blocker / "AAPL.pdf")


def test_default_report_path():
    path = default_report_path("SHEL.L", datetime(2026, 10, 10, 9, 7), Path("/home/u/Documents"))
    assert path == Path("/home/u/Documents/TT Research Reports/SHEL.L-2026-10-10-0907.pdf")
    assert isinstance(documents_dir(), Path)


def test_html_has_the_trade_call_escaped():
    s = snapshot()
    entry = make_cached_report("AAPL", call=CALL)
    text = build_report_html(replace(s, ai=entry))
    assert "<h3>Trade call</h3>" in text
    assert "Action: Accumulate (medium conviction)" in text
    assert "Entry zone: 290.00–310.00 USD" in text
    assert "Services growth below 8% &lt;b&gt;" in text


def test_html_has_the_debate():
    entry = make_cached_report("AAPL", debate=DEBATE)
    text = build_report_html(replace(snapshot(), ai=entry))
    assert "<h3>Where they disagree</h3>" in text and "<h3>The cases</h3>" in text
    assert "Services growth — favours the bull case: Two quarters up" in text
    assert "The risk case is missing (that analyst failed)." in text


def test_html_for_an_equity_etf_has_holdings_and_rollup():
    from tests.fakes import make_etf_inputs, make_holding_values

    inputs = make_etf_inputs("SPY", make_holding_values(0.10))
    a = default_assumptions(inputs)
    v = value(inputs, a)
    s = ReportSnapshot(make_profile("SPY"), inputs, a, v, score(inputs, v, a), tuple(kpis(inputs)),
                       None, None, NOW)
    text = build_report_html(s)
    for words in ("Holdings", "Top-holdings roll-up", "Apple Inc", "Technology", "10-year yield",
                  "Top-holdings roll-up</td>", "Assumes the rest of the fund"):
        assert words in text
    assert "Discount rate" not in text
