"""Export the Valuation page as a PDF: HTML (all text escaped) -> QTextDocument -> QPdfWriter."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMarginsF, QStandardPaths, QUrl
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument

from obb_terminal.ai.models import HORIZON_LABELS, CachedReport, Debate, Horizon, TradeCall
from obb_terminal.ai.schema import is_safe_url
from obb_terminal.domain.etf import coverage, sector_label
from obb_terminal.domain.models import (
    METHOD_LABELS,
    Assumptions,
    Kpi,
    Profile,
    Score,
    Valuation,
    ValuationInputs,
)
from obb_terminal.domain.scoring import LABELS as COMPONENT_LABELS
from obb_terminal.ui.fmt import DASH, fmt_kpi, fmt_pct, fmt_price
from obb_terminal.ui.valuation.ai_panel import DISCLAIMER
from obb_terminal.ui.valuation.assumptions import ETF_SCORE_WEIGHTS, METHOD_WEIGHTS, SCORE_WEIGHTS
from obb_terminal.ui.valuation.call_text import call_lines, case_lines, disagreement_lines
from obb_terminal.ui.valuation.fair_value import ROLLUP_CAVEAT
from obb_terminal.ui.valuation.kpi_table import MARK_TEXT

GAUGE_URL = "gauge.png"
REPORTS_DIR = "TT Research Reports"


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    profile: Profile
    inputs: ValuationInputs
    assumptions: Assumptions
    valuation: Valuation
    score: Score
    kpis: tuple[Kpi, ...]
    ai: CachedReport | None
    gauge_png: bytes | None
    generated_at: datetime  # timezone-aware local time


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    head = "".join(f"<th align='left'>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table border='1' cellspacing='0' cellpadding='3' width='100%'><tr>{head}</tr>{body}</table>"


def _fair_value(s: ReportSnapshot) -> str:
    v, ccy = s.valuation, s.valuation.currency.value
    parts = ["<h2>Fair value</h2>"]
    if v.reason:
        parts.append(f"<p>{_e(v.reason)}</p>")
    if v.methods:
        rows = [
            (
                METHOD_LABELS[m.method],
                fmt_price(m.value, ccy) if m.value is not None else f"n/a — {m.reason}",
                fmt_pct(m.weight) if m.weight > 0 else DASH,
            )
            for m in v.methods
        ]
        parts.append(_table(("Method", "Value", "Weight"), rows))
    if v.blended is not None and v.margin is not None and v.label is not None:
        parts.append(
            f"<p><b>Blended fair value {_e(fmt_price(v.blended, ccy))}</b> — "
            f"{_e(fmt_pct(v.margin, signed=True))} margin of safety · {_e(v.label.value)}</p>"
        )
    if s.gauge_png:
        parts.append(f"<p><img src='{GAUGE_URL}' width='500'></p>")
    return "".join(parts)


def _score(s: ReportSnapshot) -> str:
    sc = s.score
    headline = f"{sc.value:.0f} — {sc.band.value}" if sc.value is not None and sc.band else f"{DASH} — {sc.reason}"
    rows = [
        (
            COMPONENT_LABELS[c.key],
            f"{c.value:.0f}" if c.value is not None else f"n/a — {c.reason}",
            fmt_pct(c.weight) if c.weight > 0 else DASH,
        )
        for c in sc.components
    ]
    return (
        f"<h2>Outlook score</h2><p><b>{_e(headline)}</b> (a score, not a probability)</p>"
        + _table(("Component", "Value", "Weight"), rows)
    )


def _etf(i: ValuationInputs) -> str:
    """Holdings summary and the top-holdings roll-up (ETFs with look-through only)."""
    if i.etf is None:
        return ""
    p, values = i.etf.profile, i.etf.holding_values
    facts = [x for x in (p.category, p.family) if x]
    if p.expense_ratio is not None:
        facts.append(f"expense {fmt_pct(p.expense_ratio)}"
                     + (f" vs {fmt_pct(p.category_expense_ratio)} category average" if p.category_expense_ratio else ""))
    parts = ["<h2>Holdings</h2>", f"<p>{_e(' · '.join(facts))}</p>" if facts else ""]
    if p.holdings:
        parts.append(_table(("Holding", "Name", "Weight"), [(h.symbol, h.name, fmt_pct(h.weight)) for h in p.holdings]))
        parts.append(f"<p>Top {len(p.holdings)} = {_e(fmt_pct(p.top_weight))} of the fund.</p>")
    if p.sectors:
        parts.append(_table(("Sector", "Weight"), [(sector_label(k), fmt_pct(w)) for k, w in p.sectors]))
    if values:
        rows = [(hv.symbol, fmt_pct(hv.weight),
                 fmt_pct(hv.margin, signed=True) if hv.margin is not None else f"n/a — {hv.reason}")
                for hv in values]
        parts.append("<h3>Top-holdings roll-up</h3>" + _table(("Holding", "Weight", "Its margin"), rows))
        parts.append(f"<p>Covers {_e(fmt_pct(coverage(values)))} of the fund. {_e(ROLLUP_CAVEAT)}</p>")
    return "".join(parts)


def _assumptions(a: Assumptions, etf: bool = False) -> str:
    if etf:
        rows = [("10-year yield", fmt_pct(a.etf.bond_yield)), ("Minimum coverage", fmt_pct(a.etf.min_coverage))]
        rows += [(f"Score weight: {label}", fmt_pct(getattr(a.etf.score_weights, k)))
                 for k, label in ETF_SCORE_WEIGHTS.items()]
        return "<h2>Assumptions</h2>" + _table(("Assumption", "Value"), rows)
    rows = [
        ("Discount rate", fmt_pct(a.discount_rate)),
        ("Growth, years 1–5", fmt_pct(a.growth_1_5)),
        ("Terminal growth", fmt_pct(a.terminal_growth)),
        ("Dividend growth", fmt_pct(a.dividend_growth)),
    ]
    rows += [(f"Method weight: {label}", fmt_pct(getattr(a.method_weights, k))) for k, label in METHOD_WEIGHTS.items()]
    rows += [(f"Score weight: {label}", fmt_pct(getattr(a.score_weights, k))) for k, label in SCORE_WEIGHTS.items()]
    return "<h2>Assumptions</h2>" + _table(("Assumption", "Value"), rows)


def _kpis(kpis: tuple[Kpi, ...]) -> str:
    rows = [(k.label, fmt_kpi(k), MARK_TEXT[k.mark]) for k in kpis]
    return "<h2>Key indicators</h2>" + _table(("KPI", "Value", "Mark"), rows)


def _call(call: TradeCall | None, currency: str) -> str:
    if call is None:
        return ""
    return "<h3>Trade call</h3><ul>" + "".join(f"<li>{_e(line)}</li>" for line in call_lines(call, currency)) + "</ul>"


def _debate(debate: Debate | None, currency: str) -> str:
    if debate is None:
        return ""
    parts = []
    for heading, lines in (("Where they disagree", disagreement_lines(debate)[1:]),
                           ("The cases", case_lines(debate, currency)[1:])):
        parts.append(f"<h3>{heading}</h3><ul>" + "".join(f"<li>{_e(line.strip())}</li>" for line in lines) + "</ul>")
    return "".join(parts)


def _ai(entry: CachedReport | None) -> str:
    if entry is None:
        return "<h2>AI analyst</h2><p>No AI analysis has been run for this symbol.</p>"
    r = entry.report
    parts = [
        "<h2>AI analyst</h2>",
        f"<p><b>Stance: {_e(r.stance.value.capitalize())}</b></p><p>{_e(r.summary)}</p>",
        _call(r.call, entry.currency or ""),
        _debate(r.debate, entry.currency or ""),
        "<h3>Pros</h3><ul>" + "".join(f"<li><b>{_e(p.point)}</b> — {_e(p.evidence)}</li>" for p in r.pros) + "</ul>",
        "<h3>Cons</h3><ul>" + "".join(f"<li><b>{_e(p.point)}</b> — {_e(p.evidence)}</li>" for p in r.cons) + "</ul>",
        "<h3>What matters next</h3>",
    ]
    for horizon in Horizon:
        items = [w for w in r.future_watch if w.horizon is horizon]
        if items:
            parts.append(f"<p><i>{_e(HORIZON_LABELS[horizon])}</i></p><ul>")
            parts += [f"<li><b>{_e(w.item)}</b> — {_e(w.why)}</li>" for w in items]
            parts.append("</ul>")
    safe = [src for src in r.sources if is_safe_url(src.url)]
    if safe:
        parts.append("<h3>Sources</h3><ul>")
        parts += [f"<li>{_e(src.title)} — {_e(src.url)}</li>" for src in safe]
        parts.append("</ul>")
    analysed = entry.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    parts.append(f"<p>Analysed {_e(analysed)} with {_e(entry.model)} (effort {_e(entry.effort)}).</p>")
    return "".join(parts)


def build_report_html(s: ReportSnapshot) -> str:
    """Pure. Every piece of text is HTML-escaped: AI and web text is untrusted."""
    p, i = s.profile, s.inputs
    exchange = f" · {p.exchange}" if p.exchange else ""
    header = (
        f"<h1>{_e(p.name)} ({_e(p.symbol)}){_e(exchange)}</h1>"
        f"<p>Price {_e(fmt_price(i.price, i.currency.value))} · "
        f"generated {_e(s.generated_at.strftime('%Y-%m-%d %H:%M'))}</p>"
    )
    body = (
        header + _fair_value(s) + _etf(i) + _score(s) + _assumptions(s.assumptions, i.etf is not None)
        + _kpis(s.kpis) + _ai(s.ai)
        + f"<hr><p><i>{_e(DISCLAIMER)}</i></p>"
    )
    return f"<html><body>{body}</body></html>"


def export_pdf(s: ReportSnapshot, path: Path) -> Path:
    """Render the snapshot to an A4 PDF at `path`. Raises OSError if it cannot be written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = QTextDocument()
    document.setHtml(build_report_html(s))
    if s.gauge_png:
        image = QImage.fromData(s.gauge_png, "PNG")
        if not image.isNull():
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(GAUGE_URL), image)
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(300)
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
    writer.setTitle(f"{s.profile.symbol} valuation report")
    document.print_(writer)
    del writer
    if not path.exists() or path.stat().st_size == 0:
        raise OSError(f"Could not write {path}")
    return path


def documents_dir() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(location) if location else Path.home() / "Documents"


def default_report_path(symbol: str, now: datetime, documents: Path | None = None) -> Path:
    """<Documents>/TT Research Reports/<SYMBOL>-<YYYY-MM-DD-HHMM>.pdf"""
    return (documents or documents_dir()) / REPORTS_DIR / f"{symbol}-{now:%Y-%m-%d-%H%M}.pdf"
