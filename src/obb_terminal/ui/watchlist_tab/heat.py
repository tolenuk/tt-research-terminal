"""Heat-matrix cell colours: a translucent fill per bucket over the black background, with the
amber text kept at WCAG AA contrast (at least 4.5:1)."""

from __future__ import annotations

from PySide6.QtGui import QColor

from obb_terminal.domain.targets import Heat
from obb_terminal.ui.theme import TOKENS

BASE_ALPHA = 0.10
ALPHA_RANGE = 0.25
_TOKEN = {Heat.HIT: "up", Heat.NEAR: "amber", Heat.FAR: "down"}


def heat_fill(bucket: Heat, intensity: float) -> QColor | None:
    """The cell background for a bucket; None (no fill) for n/a cells."""
    token = _TOKEN.get(bucket)
    if token is None:
        return None
    colour = QColor(TOKENS[token])
    colour.setAlphaF(BASE_ALPHA + ALPHA_RANGE * min(max(intensity, 0.0), 1.0))
    return colour


def composite(fill: QColor, base: QColor) -> QColor:
    """`fill` drawn over the opaque `base`."""
    a = fill.alphaF()
    return QColor.fromRgbF(
        fill.redF() * a + base.redF() * (1 - a),
        fill.greenF() * a + base.greenF() * (1 - a),
        fill.blueF() * a + base.blueF() * (1 - a),
    )


def _luminance(c: QColor) -> float:
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(c.redF()) + 0.7152 * channel(c.greenF()) + 0.0722 * channel(c.blueF())


def contrast_ratio(fg: QColor, bg: QColor) -> float:
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
