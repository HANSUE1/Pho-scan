"""Tags sidebar: colored tag rows with filtering, rename and delete."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QLabel

from gui.sidebar import ElidedLabel, SidebarList, SidebarRow
from gui.theme import tag_color

DOT = 12


def _dot_pixmap(color):
    pixmap = QPixmap(DOT, DOT)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(QRectF(0.5, 0.5, DOT - 1, DOT - 1))
    painter.end()
    return pixmap


class TagRow(SidebarRow):
    rename_requested = Signal(dict)
    delete_requested = Signal(dict)

    def __init__(self, tag, active, parent=None):
        super().__init__(tag, active, margins=(8, 4, 8, 4), button_size=22, parent=parent)
        self.layout_.setSpacing(8)

        dot = QLabel()
        dot.setPixmap(_dot_pixmap(tag_color(tag["id"])))
        dot.setFixedSize(DOT, DOT)
        self.layout_.addWidget(dot)

        name = ElidedLabel(tag["name"])
        name.setStyleSheet(
            f"color: {tag_color(tag['id']).name()}; font-size: 13px;"
            " font-weight: 600; background: transparent;"
        )
        self.layout_.addWidget(name, 1)

        count = QLabel(str(tag["photo_count"]))
        count.setObjectName("personCount")
        self.layout_.addWidget(count)

        self.add_action("✎", "Rename tag", self.rename_requested)
        self.add_action("\U0001f5d1", "Delete tag", self.delete_requested)
        self.finish_layout()

    @property
    def tag(self):
        return self.item


class TagsPanel(SidebarList):
    """Tag list rebuilt from list_tags() data."""

    tag_clicked = Signal(int)
    rename_requested = Signal(dict)
    delete_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__("No tags yet. Right-click a photo to add one.", parent)

    def tags(self):
        return self.items()

    def make_row(self, tag, active):
        row = TagRow(tag, active)
        row.clicked.connect(self.tag_clicked)
        row.rename_requested.connect(self.rename_requested)
        row.delete_requested.connect(self.delete_requested)
        return row
