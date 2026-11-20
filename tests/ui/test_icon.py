from PySide6.QtCore import QSize

from obb_terminal.ui.icon import ASSETS, SIZES, app_icon, set_windows_app_id


def test_icon_has_every_size(qapp):
    icon = app_icon()
    assert not icon.isNull()
    for size in SIZES:
        assert (ASSETS / f"tt-research-{size}.png").exists()
        assert icon.actualSize(QSize(size, size)) == QSize(size, size)


def test_windows_icon_and_vector_source_exist():
    assert (ASSETS / "tt-research.ico").exists()
    assert (ASSETS / "tt-research.svg").exists()


def test_setting_the_windows_app_id_is_harmless_elsewhere():
    set_windows_app_id()
