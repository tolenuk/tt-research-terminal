import pytest
from PySide6.QtGui import QColor

from obb_terminal.domain.targets import Heat
from obb_terminal.ui.theme import TOKENS
from obb_terminal.ui.watchlist_tab.heat import composite, contrast_ratio, heat_fill


def test_buckets_map_to_theme_colours_with_scaled_alpha():
    assert heat_fill(Heat.NA, 1.0) is None
    hit = heat_fill(Heat.HIT, 0.0)
    assert hit.name() == TOKENS["up"].lower() and hit.alphaF() == pytest.approx(0.10, abs=0.01)
    assert heat_fill(Heat.FAR, 1.0).alphaF() == pytest.approx(0.35, abs=0.01)
    assert heat_fill(Heat.NEAR, 0.5).name() == TOKENS["amber"].lower()
    assert heat_fill(Heat.FAR, 7.0).alphaF() == pytest.approx(0.35, abs=0.01)  # clamped


@pytest.mark.parametrize("bucket", [Heat.HIT, Heat.NEAR, Heat.FAR])
@pytest.mark.parametrize("intensity", [0.0, 0.5, 1.0])
def test_amber_text_keeps_aa_contrast(bucket, intensity):
    bg = composite(heat_fill(bucket, intensity), QColor(TOKENS["bg"]))
    assert contrast_ratio(QColor(TOKENS["amber"]), bg) >= 4.5


def test_contrast_ratio_reference():
    assert contrast_ratio(QColor("#ffffff"), QColor("#000000")) == pytest.approx(21.0)
    assert contrast_ratio(QColor("#000000"), QColor("#000000")) == pytest.approx(1.0)
