"""Amber Classic: bright amber only for live figures, tan section headings, warm-grey labels and
grey secondary text on near-black panels. Shared by QSS and pyqtgraph."""

from __future__ import annotations

from pathlib import Path
from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableView, QWidget

TOKENS: dict[str, str] = {
    "bg": "#0B0B0C",
    "panel": "#141416",
    "raised": "#1C1C1F",
    "border": "#1F1F23",
    "rule": "#26262B",
    "zebra": "#18181B",
    "scroll": "#3A3A40",
    "amber": "#FFB000",
    "amber_dim": "#C9A46A",
    "text": "#E8E2D6",
    "label": "#B5B0A6",  # field labels: warm grey, so amber stays for figures
    "muted": "#8B9099",
    "sel_bg": "#2A2010",
    "up": "#4CC38A",
    "down": "#F2555A",
    "error": "#FF6B6B",
    "focus": "#FFD54A",
}

# Labels and prose in a sans face; prices, tickers and tables of figures in mono.
SANS_FAMILIES = ["Inter", "Adwaita Sans", "Segoe UI", "Helvetica Neue", "Noto Sans", "Liberation Sans"]
MONO_FAMILIES = ["JetBrainsMono Nerd Font", "JetBrains Mono", "Consolas", "Menlo", "DejaVu Sans Mono"]


def _qss_families(families: list[str]) -> str:
    return ", ".join(f'"{f}"' for f in families)


def load_qss() -> str:
    return Template(Path(__file__).with_name("theme.qss").read_text(encoding="utf-8")).substitute(
        TOKENS, mono=_qss_families(MONO_FAMILIES)
    )


def app_font() -> QFont:
    font = QFont()
    font.setFamilies(SANS_FAMILIES)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSize(10)
    # Tabular figures: digits share one width, so columns of numbers line up in the sans face.
    font.setFeature(QFont.Tag("tnum"), 1)
    return font


def mono_font(point_size: int = 10) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES)
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setPointSize(point_size)
    return font


def configure_pyqtgraph() -> None:
    import pyqtgraph as pg

    pg.setConfigOptions(background=TOKENS["panel"], foreground=TOKENS["muted"], antialias=True)


def apply_theme(app: QApplication) -> None:
    # Fusion honours the whole stylesheet everywhere (the Windows 11 style ignores parts of
    # it); a dark colour scheme also darkens the Windows title bar to match.
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    configure_pyqtgraph()
    app.setFont(app_font())
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TOKENS["muted"]))
    app.setPalette(palette)
    app.setStyleSheet(load_qss())


def set_dir(widget: QWidget, direction: str) -> None:
    """Set the dynamic `dir` property (up/down/flat) and re-polish so QSS colours apply."""
    widget.setProperty("dir", direction)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def style_table(view: QAbstractItemView) -> None:
    """Striped rows instead of grid lines (the stripe colour is the QSS alternate-background)."""
    view.setAlternatingRowColors(True)
    if isinstance(view, QTableView):
        view.setShowGrid(False)


COMPACT_ROW = 22


def fit_rows(view: QTableView) -> None:
    """Compact rows, and a fixed height that shows exactly every one: the last row is never cut
    in half behind a scrollbar, and no blank space opens up under the rows."""
    view.verticalHeader().setDefaultSectionSize(COMPACT_ROW)
    rows = sum(view.rowHeight(r) for r in range(view.model().rowCount()))
    # isHidden, not isVisible: the table is often filled while its page is still hidden.
    header = 0 if view.horizontalHeader().isHidden() else view.horizontalHeader().sizeHint().height()
    view.setFixedHeight(header + rows + 2 * view.frameWidth())
