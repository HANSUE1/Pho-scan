"""People sidebar: avatar rows with filter, rename, merge and delete."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout

from gui.sidebar import ElidedLabel, SidebarList, SidebarRow
from gui.theme import person_color

AVATAR = 44


def _circular_avatar(pixmap, color):
    """Avatar pixmap cropped to a circle with a 2px person-colored ring."""
    result = QPixmap(AVATAR, AVATAR)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    if pixmap is not None:
        path = QPainterPath()
        path.addEllipse(QRectF(1, 1, AVATAR - 2, AVATAR - 2))
        painter.setClipPath(path)
        painter.drawPixmap(result.rect(), pixmap)
        painter.setClipping(False)
    painter.setPen(QPen(color, 2))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(QRectF(1, 1, AVATAR - 2, AVATAR - 2))
    painter.end()
    return result


class PersonRow(SidebarRow):
    rename_requested = Signal(dict)
    merge_requested = Signal(dict)
    delete_requested = Signal(dict)

    def __init__(self, person, active, parent=None):
        super().__init__(person, active, margins=(8, 6, 8, 6), button_size=24, parent=parent)
        self.layout_.setSpacing(10)

        self.avatar = QLabel()
        self.avatar.setFixedSize(AVATAR, AVATAR)
        self.set_portrait(None)
        self.layout_.addWidget(self.avatar)

        meta = QVBoxLayout()
        meta.setSpacing(1)
        name = ElidedLabel(person["name"])
        name.setStyleSheet(
            f"color: {person_color(person['id']).name()};"
            " font-size: 14px; font-weight: 600; background: transparent;"
        )
        n = person["photo_count"]
        count = QLabel(f"{n} photo{'' if n == 1 else 's'}")
        count.setObjectName("personCount")
        meta.addWidget(name)
        meta.addWidget(count)
        self.layout_.addLayout(meta, 1)

        self.add_action("✎", "Rename", self.rename_requested)
        self.add_action("⇆", "Merge into another person", self.merge_requested)
        self.add_action("\U0001f5d1", "Delete person and its face boxes", self.delete_requested)
        self.finish_layout()

    @property
    def person(self):
        return self.item

    def set_portrait(self, pixmap):
        self.avatar.setPixmap(_circular_avatar(pixmap, person_color(self.item["id"])))


class PeoplePanel(SidebarList):
    """People list rebuilt from list_persons() data; avatars load async."""

    person_clicked = Signal(int)
    rename_requested = Signal(dict)
    merge_requested = Signal(dict)
    delete_requested = Signal(dict)

    def __init__(self, loader, parent=None):
        super().__init__("No people detected yet.", parent)
        self._loader = loader
        loader.portrait_ready.connect(self._on_portrait_ready)

    def persons(self):
        return self.items()

    def make_row(self, person, active):
        row = PersonRow(person, active)
        row.clicked.connect(self.person_clicked)
        row.rename_requested.connect(self.rename_requested)
        row.merge_requested.connect(self.merge_requested)
        row.delete_requested.connect(self.delete_requested)
        pixmap = self._loader.portrait(person["portrait_face_id"])
        if pixmap is not None:
            row.set_portrait(pixmap)
        return row

    def _on_portrait_ready(self, face_id):
        pixmap = self._loader.portrait(face_id)
        if pixmap is None:
            return
        for row in self._rows:
            if row.person["portrait_face_id"] == face_id:
                row.set_portrait(pixmap)
