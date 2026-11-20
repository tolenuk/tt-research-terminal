"""The page bar: the tabs in labelled groups (STOCK · DESK · DISCOVER), each with an Alt+digit key.

It drives a QTabWidget whose own tab bar is hidden, so pages, titles and indexes stay the tab
widget's; this bar only draws them in groups and keeps the checked button in step.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QWidget,
)

SHORTCUT_KEYS = "1234567890"  # Ctrl+1..4 already maximise Overview panels, so pages use Alt


def shortcut_for(position: int) -> str | None:
    """The key for the page shown in this position along the bar (left to right), if any."""
    return f"Alt+{SHORTCUT_KEYS[position]}" if position < len(SHORTCUT_KEYS) else None


class PageNav(QWidget):
    def __init__(
        self,
        tabs: QTabWidget,
        groups: Sequence[tuple[str, Sequence[int]]],
        shortcut_parent: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._shortcut_parent = shortcut_parent
        self.setObjectName("PageNav")
        self._tabs = tabs
        self.buttons: dict[int, QPushButton] = {}
        self.shortcuts: list[QShortcut] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        position = 0
        for g, (name, indexes) in enumerate(groups):
            if g:
                rule = QFrame()
                rule.setObjectName("NavSep")
                rule.setFixedSize(1, 18)
                layout.addSpacing(10)
                layout.addWidget(rule, 0, Qt.AlignmentFlag.AlignVCenter)
                layout.addSpacing(10)
            group_label = QLabel(name)
            group_label.setObjectName("NavGroup")
            layout.addWidget(group_label, 0, Qt.AlignmentFlag.AlignVCenter)
            for index in indexes:
                self._add_button(layout, index, shortcut_for(position))
                position += 1
        layout.addStretch(1)
        tabs.tabBar().hide()
        tabs.currentChanged.connect(self._sync)
        self._sync(tabs.currentIndex())

    def _add_button(self, layout: QHBoxLayout, index: int, keys: str | None) -> None:
        title = self._tabs.tabText(index)
        button = QPushButton(title)
        button.setObjectName("NavTab")
        button.setCheckable(True)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setAccessibleName(f"{title.title()} page")
        if keys:
            button.setToolTip(f"{title.title()}  ({keys})")
            shortcut = QShortcut(QKeySequence(keys), self._shortcut_parent)
            shortcut.activated.connect(partial(self._tabs.setCurrentIndex, index))
            self.shortcuts.append(shortcut)
        button.clicked.connect(partial(self._tabs.setCurrentIndex, index))
        self._group.addButton(button)
        self.buttons[index] = button
        layout.addWidget(button)

    def _sync(self, index: int) -> None:
        button = self.buttons.get(index)
        if button is not None:
            button.setChecked(True)
