"""Photo grid: list model, card delegate and the gallery view.

The grid always shows COLUMNS columns: cell size is recomputed from the
viewport width on every resize, so cards scale with the window.

Cards animate on hover (subtle scale) and fade in when the photo list
changes. Selection shows a glowing accent border.
"""

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
    QPropertyAnimation,
    QEasingCurve,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QGraphicsOpacityEffect,
)

from gui.data import THUMB_ASPECTS
from gui.facepaint import draw_face_boxes
from gui.theme import (
    ACCENT,
    BORDER,
    CARD_SCALE_HOVER,
    CARD_SCALE_SELECTED,
    EASE_OUT,
    MUTED,
    PANEL,
    card_style,
    tag_color,
)
from timeutil import format_timestamp

DEFAULT_COLUMNS = 4
GAP = 12  # gutter between cards (GAP/2 inset on every cell side)
FILENAME_BAR = 34
RADIUS = 12
TAG_DOT = 10
MAX_TAG_DOTS = 5
FADE_DURATION_MS = 260


class PhotoModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._photos = []

    def set_photos(self, photos):
        self.beginResetModel()
        self._photos = photos
        self.endResetModel()

    def photos(self):
        return self._photos

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._photos)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.UserRole and index.isValid():
            return self._photos[index.row()]
        return None


class PhotoDelegate(QStyledItemDelegate):
    """Card with a fixed-aspect face-centered thumbnail, face boxes, filename.

    Hover/selected state drives a subtle scale via the item's decoration.
    """

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self._loader = loader
        self.aspect_key = "34"
        self.show_boxes = True
        self.cell_size = QSize(260, 390)  # replaced by GalleryView._update_grid()
        self._hover_index = QModelIndex()
        self._selected_index = QModelIndex()

    def sizeHint(self, option, index):
        return self.cell_size

    def set_hover(self, index: QModelIndex):
        if self._hover_index != index:
            self._hover_index = index
            self.parent().viewport().update()

    def set_selected(self, index: QModelIndex):
        self._selected_index = index

    @staticmethod
    def _paint_tag_dots(painter, tags, frame):
        """Colored dots in the bottom-left corner hint at a card's tags."""
        if not tags:
            return
        painter.save()
        painter.setPen(QPen(QColor(0, 0, 0, 140), 1))
        for i, tag in enumerate(tags[:MAX_TAG_DOTS]):
            painter.setBrush(tag_color(tag["id"]))
            painter.drawEllipse(
                QRectF(
                    frame.x() + 10 + i * (TAG_DOT + 5),
                    frame.bottom() - TAG_DOT - 10,
                    TAG_DOT,
                    TAG_DOT,
                )
            )
        painter.restore()

    def paint(self, painter, option, index):
        photo = index.data(Qt.UserRole)
        if photo is None:
            return
        half = GAP // 2
        card = option.rect.adjusted(half, half, -half, -half)

        # Determine visual state.
        hovered = self._hover_index == index
        selected = self._selected_index == index

        # Subtle scale transform on hover/selected.
        scale = 1.0
        if selected:
            scale = CARD_SCALE_SELECTED
        elif hovered:
            scale = CARD_SCALE_HOVER

        if scale != 1.0:
            center = card.center()
            card = QRectF(card).scaled(
                card.width() * scale, card.height() * scale, Qt.KeepAspectRatio
            )
            card.moveCenter(center)
            card = card.toRect()

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Card background.
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(card), RADIUS, RADIUS)
        painter.setClipPath(clip)
        painter.fillRect(card, QColor(PANEL))

        # Optional soft inner glow for selected cards.
        if selected:
            glow = QPainterPath()
            glow.addRoundedRect(
                QRectF(card).adjusted(-3, -3, 3, 3), RADIUS + 3, RADIUS + 3
            )
            painter.setClipPath(glow)
            painter.setClipPath(clip)  # restrict back to card
            painter.setOpacity(0.15)
            painter.fillRect(card, QColor(ACCENT))
            painter.setOpacity(1.0)

        frame = QRect(
            card.x(), card.y(), card.width(), card.height() - FILENAME_BAR
        )
        painter.fillRect(frame, QColor("#0a0b0f"))
        pixmap = self._loader.thumbnail(photo, self.aspect_key)
        if pixmap is not None:
            painter.drawPixmap(frame, pixmap)
        if self.show_boxes:
            draw_face_boxes(painter, photo["faces"], photo["crop"], QRectF(frame))
        self._paint_tag_dots(painter, photo.get("tags", []), frame)

        # Caption area.
        name_rect = QRect(card.x() + 12, frame.bottom(), card.width() - 24, FILENAME_BAR)
        painter.setPen(QColor(MUTED))
        font = painter.font()
        font.setPixelSize(12)
        font.setWeight(font.Weight.Medium)
        painter.setFont(font)
        caption = photo["filename"]
        date = format_timestamp(photo.get("taken_at") or photo.get("mtime"), "%Y-%m-%d")
        if date:
            caption += f" · {date}"
        elided = painter.fontMetrics().elidedText(
            caption, Qt.ElideMiddle, name_rect.width()
        )
        painter.drawText(name_rect, Qt.AlignCenter, elided)

        # Border.
        painter.setClipping(False)
        border_color = ACCENT if selected else (BORDER if not hovered else ACCENT_2)
        pen_width = 2 if (selected or hovered) else 1
        painter.setPen(QPen(QColor(border_color), pen_width))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(card).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        painter.restore()


class GalleryView(QListView):
    photo_clicked = Signal(dict)

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.setObjectName("gallery")
        self._model = PhotoModel(self)
        self.delegate = PhotoDelegate(loader, self)
        self.setModel(self._model)
        self.setItemDelegate(self.delegate)

        self.columns = DEFAULT_COLUMNS
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(30)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_Hover, True)

        loader.thumb_ready.connect(lambda *_: self.viewport().update())
        self.clicked.connect(self._on_clicked)

        # Track hover over items for animated scale.
        self._hover_index = QModelIndex()
        self.viewport().mouseMoveEvent = self._intercept_mouse_move
        self._original_mouse_move = self.viewport().mouseMoveEvent

    # ------------------------------------------------------------------ hover

    def _intercept_mouse_move(self, event):
        self._original_mouse_move(event)
        idx = self.indexAt(event.pos())
        if idx != self.delegate._hover_index:
            self.delegate.set_hover(idx)
        return None

    def enterEvent(self, event):
        super().enterEvent(event)
        self.delegate.set_hover(QModelIndex())
        self.viewport().update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.delegate.set_hover(QModelIndex())
        self.viewport().update()

    def set_columns(self, columns):
        self.columns = max(2, min(8, int(columns)))
        self._update_grid()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            photo = self.currentIndex().data(Qt.UserRole)
            if photo is not None:
                self.photo_clicked.emit(photo)
                return
        super().keyPressEvent(event)

    def _update_grid(self):
        """Fit self.columns columns into the viewport; cards scale with the window."""
        cell_w = max(GAP + 40, self.viewport().width() // self.columns)
        card_w = cell_w - GAP
        frame_h = round(card_w / THUMB_ASPECTS[self.delegate.aspect_key])
        cell_h = frame_h + FILENAME_BAR + GAP
        size = QSize(cell_w, cell_h)
        if size != self.delegate.cell_size:
            self.delegate.cell_size = size
            self.setGridSize(size)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_grid()

    def set_photos(self, photos):
        self._model.set_photos(photos)
        # Fade the viewport in when the list changes.
        effect = QGraphicsOpacityEffect(self.viewport())
        self.viewport().setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(FADE_DURATION_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(EASE_OUT)
        anim.start()
        self._fade_anim = anim  # keep alive

    def photos(self):
        """The photo dicts currently shown, in display order."""
        return self._model.photos()

    def set_aspect(self, aspect_key):
        self.delegate.aspect_key = aspect_key
        self._update_grid()
        self.reset()

    def set_show_boxes(self, show):
        self.delegate.show_boxes = show
        self.viewport().update()

    def selectionChanged(self, selected, deselected):
        super().selectionChanged(selected, deselected)
        # Keep the delegate in sync with selection for animated borders.
        idx = self.currentIndex()
        self.delegate.set_selected(idx if idx.isValid() else QModelIndex())
        self.viewport().update()

    def _on_clicked(self, index):
        photo = index.data(Qt.UserRole)
        if photo is not None:
            self.photo_clicked.emit(photo)

    def photo_at(self, pos):
        return self.indexAt(pos).data(Qt.UserRole)

    def selected_photos(self):
        return [
            photo
            for index in self.selectedIndexes()
            if (photo := index.data(Qt.UserRole)) is not None
        ]