import pytest
from PySide6.QtWidgets import QLabel

from obb_terminal.ui import fmt
from obb_terminal.ui.theme import TOKENS, load_qss, set_dir


@pytest.mark.parametrize(
    "value,expected",
    [(None, "—"), (4912984883200, "4.91T"), (215941365760, "215.94B"), (-321000000, "-321.00M"),
     (45_000, "45.00K"), (12.0, "12")],
)
def test_fmt_big(value, expected):
    assert fmt.fmt_big(value) == expected


def test_fmt_price_and_pct():
    assert fmt.fmt_price(27.1, "GBP") == "27.10 GBP"
    assert fmt.fmt_price(None, "GBP") == "—"
    assert fmt.fmt_pct(0.0306) == "3.06%"
    assert fmt.fmt_pct(-0.0111, signed=True) == "-1.11%"
    assert fmt.fmt_int(10913950) == "10,913,950"
    assert fmt.fmt_range(25.5, 38.07) == "25.50 – 38.07"
    assert fmt.fmt_range(None, 1.0) == "—"


def test_direction_and_change():
    assert fmt.direction(0.13) == "up"
    assert fmt.direction(-1) == "down"
    assert fmt.direction(0) == "flat"
    assert fmt.direction(None) == "flat"
    assert fmt.fmt_change(0.13, 0.0034) == "▲ +0.13 (+0.34%)"
    assert fmt.fmt_change(-3.78, -0.0111) == "▼ -3.78 (-1.11%)"
    assert fmt.fmt_change(None, None) == "—"


def test_qss_tokens_substituted():
    qss = load_qss()
    assert TOKENS["amber"] in qss
    assert "$" not in qss


def test_set_dir_sets_dynamic_property(qtbot):
    label = QLabel()
    qtbot.addWidget(label)
    set_dir(label, "up")
    assert label.property("dir") == "up"


def test_value_labels_keep_direction_colours(qtbot):
    from PySide6.QtGui import QPalette

    label = QLabel("▲ +1.00")
    label.setObjectName("Value")
    label.setStyleSheet(load_qss())
    qtbot.addWidget(label)
    set_dir(label, "up")
    assert label.palette().color(QPalette.ColorRole.WindowText).name() == TOKENS["up"].lower()


def test_apply_theme_uses_fusion_and_a_dark_scheme(qapp):
    from types import SimpleNamespace

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette

    from obb_terminal.ui.theme import TOKENS, apply_theme

    calls = []
    hints = SimpleNamespace(setColorScheme=lambda scheme: calls.append(("scheme", scheme)))
    app = SimpleNamespace(
        setStyle=lambda name: calls.append(("style", name)),
        styleHints=lambda: hints,
        setFont=lambda font: calls.append(("font", font.families()[0])),
        setStyleSheet=lambda qss: calls.append(("qss", "$" not in qss)),
        palette=lambda: QPalette(),
        setPalette=lambda palette: calls.append(("placeholder", palette.color(QPalette.ColorRole.PlaceholderText).name())),
    )
    apply_theme(app)
    assert ("style", "Fusion") in calls and ("scheme", Qt.ColorScheme.Dark) in calls
    assert ("qss", True) in calls
    # Placeholder text is grey, so an empty search box never looks like a typed value.
    assert ("placeholder", TOKENS["muted"].lower()) in calls
