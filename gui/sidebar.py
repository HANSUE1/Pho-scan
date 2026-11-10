"""Shared building blocks for the sidebar lists (people and tags).

A `SidebarRow` is a clickable, hover-highlighted row whose action buttons
appear on hover (and stay visible while the row is the active filter). A
`SidebarList` is a scrollable stack of such rows with an empty-state label,
rebuilt from plain data dicts only when that data actually changed.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ElidedLabel(QLabel):
    """Single-line label that elides with "…" instead of widening its row."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(20)
        super().setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        super().setText(
            self.fontMetrics().elidedText(self._full, Qt.ElideRight, self.width())
        )


class SidebarRow(QFrame):
    """Clickable row; emits `clicked(item_id)` and per-action `(item dict)`."""

    clicked = Signal(int)

    def __init__(self, item, active, margins, button_size, parent=None):
        super().__init__(parent)
        self.item = item
        self._active = active
        self.setObjectName("personRow")  # shared sidebar row styling
        self.setProperty("active", "true" if active else "false")
        self.setCursor(Qt.PointingHandCursor)

        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(*margins)

        self._buttons = QWidget()
        self._buttons.setStyleSheet("background: transparent;")
        self._buttons_layout = QHBoxLayout(self._buttons)
        self._buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._buttons_layout.setSpacing(2)
        self._button_size = button_size
        self._buttons.setVisible(active)

    def add_action(self, text, tooltip, signal):
        """Hover button that emits `signal(self.item)` when clicked."""
        btn = QToolButton()
        btn.setObjectName("personBtn")
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setFixedSize(self._button_size, self._button_size)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _=False: signal.emit(self.item))
        self._buttons_layout.addWidget(btn)

    def finish_layout(self):
        """Append the action buttons after the row's own widgets."""
        self.layout_.addWidget(self._buttons)

    def enterEvent(self, event):
        self._buttons.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._buttons.setVisible(self._active)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item["id"])
        super().mousePressEvent(event)


class SidebarList(QWidget):
    """Scrollable list of rows built by `make_row(item, active)`."""

    def __init__(self, empty_text, parent=None):
        super().__init__(parent)
        self._rows = []
        self._items = []
        self._active_ids = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 0, 4, 0)
        self._list.setSpacing(2)
        self._list.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self.empty = QLabel(empty_text)
        self.empty.setObjectName("mutedLabel")
        self.empty.setWordWrap(True)
        outer.addWidget(self.empty)

    def items(self):
        return self._items

    def make_row(self, item, active):  # pragma: no cover - abstract
        raise NotImplementedError

    def rebuild(self, items, active_ids):
        active_ids = set(active_ids)
        if items == self._items and active_ids == self._active_ids:
            return  # nothing changed: keep widgets (and hover state) as they are
        self._items = items
        self._active_ids = active_ids
        for row in self._rows:
            self._list.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows = []

        for item in items:
            row = self.make_row(item, item["id"] in active_ids)
            self._list.insertWidget(self._list.count() - 1, row)
            self._rows.append(row)

        self.empty.setVisible(not items)
