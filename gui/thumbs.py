"""Background image loading: gallery thumbnails and person portraits.

Workers decode with QImageReader (EXIF auto-transform, matching the
orientation the analyzer stored coordinates in) and hand QImages to the GUI
thread through signals; pixmaps are created and cached there.
"""

import glob
import logging
import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QRect, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from gui.data import PORTRAIT_SIZE, THUMB_MAX, face_portrait_source
from paths import data_dir

log = logging.getLogger(__name__)

# A 450x600 thumbnail is ~1 MB as a pixmap; without a bound, scrolling
# through a large library would keep every one of them in memory.
MAX_CACHED_THUMBS = 400


def read_image(path):
    """Decode an image with its EXIF orientation applied (null QImage on failure)."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    return reader.read()


class _LruCache(OrderedDict):
    """Dict that evicts its least recently used entries beyond `limit`."""

    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def get(self, key, default=None):
        if key not in self:
            return default
        self.move_to_end(key)
        return self[key]

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.limit:
            self.popitem(last=False)


class _Job(QRunnable):
    def __init__(self, fn, on_error=None):
        super().__init__()
        self._fn = fn
        self._on_error = on_error

    def run(self):
        try:
            self._fn()
        except Exception:  # noqa: BLE001 - keep the thread pool alive
            log.exception("image loader job failed")
            if self._on_error is not None:
                self._on_error()


class ImageLoader(QObject):
    """Async loader with in-memory caches keyed by photo/face ids."""

    _thumb_done = Signal(str, int, str, QImage)
    _portrait_done = Signal(int, QImage)
    thumb_ready = Signal(int, str)  # photo_id, aspect_key
    portrait_ready = Signal(int)  # face_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        # Keyed by the disk-cache key, so a changed file or crop (face boxes
        # edited) misses the cache instead of serving a stale image under
        # the new face boxes.
        self._thumbs = _LruCache(MAX_CACHED_THUMBS)  # cache key -> QPixmap
        self._portraits = {}  # face_id -> QPixmap (small, bounded by #persons)
        self._pending = set()
        self._cache_dir = os.path.join(data_dir(), "thumbs_cache")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._thumb_done.connect(self._on_thumb_done)
        self._portrait_done.connect(self._on_portrait_done)

    @staticmethod
    def _cache_key(photo, aspect_key):
        """Invalidates on file change (mtime), aspect switch and crop change."""
        crop = photo["crop"]
        return (
            f"{photo['id']}-{int(photo.get('mtime') or 0)}-{aspect_key}"
            f"-{crop['x']}x{crop['y']}x{crop['w']}x{crop['h']}"
        )

    def _cache_path(self, photo, aspect_key):
        return os.path.join(self._cache_dir, self._cache_key(photo, aspect_key) + ".jpg")

    # ------------------------------------------------------------- thumbnails

    def thumbnail(self, photo, aspect_key):
        """Cached pixmap, or None (and schedule a load) if not ready yet."""
        key = self._cache_key(photo, aspect_key)
        pixmap = self._thumbs.get(key)
        if pixmap is None and key not in self._pending:
            self._pending.add(key)
            photo_id, path, crop = photo["id"], photo["path"], dict(photo["crop"])
            cache = self._cache_path(photo, aspect_key)
            self._pool.start(
                _Job(lambda: self._load_thumb(key, photo_id, aspect_key, path, crop, cache))
            )
        return pixmap

    def _load_thumb(self, key, photo_id, aspect_key, path, crop, cache):
        image = QImage(cache) if os.path.exists(cache) else QImage()
        if image.isNull():
            image = read_image(path)
            if image.isNull():
                log.warning("could not read thumbnail source %s", path)
            else:
                image = image.copy(QRect(crop["x"], crop["y"], crop["w"], crop["h"]))
                image = image.scaled(
                    QSize(*THUMB_MAX[aspect_key]),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                if image.save(cache, "JPG", 85):
                    self._remove_stale_cache_files(photo_id, aspect_key, cache)
                else:
                    log.warning("could not write thumbnail cache %s", cache)
        self._thumb_done.emit(key, photo_id, aspect_key, image)

    def _remove_stale_cache_files(self, photo_id, aspect_key, keep):
        """Delete superseded cache files of this photo+aspect (old mtime/crop)."""
        for old in glob.glob(os.path.join(self._cache_dir, f"{photo_id}-*-{aspect_key}-*.jpg")):
            if old != keep:
                try:
                    os.remove(old)
                except OSError:
                    pass  # e.g. locked by another process; harmless

    def _on_thumb_done(self, key, photo_id, aspect_key, image):
        self._pending.discard(key)
        if not image.isNull():
            self._thumbs.put(key, QPixmap.fromImage(image))
            self.thumb_ready.emit(photo_id, aspect_key)

    # -------------------------------------------------------------- portraits

    def portrait(self, face_id):
        """Cached avatar pixmap, or None (and schedule a load) if not ready."""
        if face_id is None:
            return None
        pixmap = self._portraits.get(face_id)
        if pixmap is None and ("face", face_id) not in self._pending:
            self._pending.add(("face", face_id))
            self._pool.start(_Job(lambda: self._load_portrait(face_id)))
        return pixmap

    def _load_portrait(self, face_id):
        try:
            row = face_portrait_source(face_id)
        except Exception:  # noqa: BLE001 - a broken DB must not kill the worker
            log.exception("loading portrait source for face %s failed", face_id)
            row = None
        image = read_image(row["path"]) if row else QImage()
        if image.isNull() and row is not None:
            log.warning("could not read portrait source %s", row["path"])
        if not image.isNull():
            # Square crop around the face with a 30% margin, clamped to the image.
            cx, cy = row["x"] + row["w"] / 2, row["y"] + row["h"] / 2
            side = max(row["w"], row["h"]) * 1.3
            x1 = max(0, int(cx - side / 2))
            y1 = max(0, int(cy - side / 2))
            x2 = min(image.width(), int(cx + side / 2))
            y2 = min(image.height(), int(cy + side / 2))
            image = image.copy(QRect(x1, y1, x2 - x1, y2 - y1))
            image = image.scaled(
                PORTRAIT_SIZE, PORTRAIT_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        self._portrait_done.emit(face_id, image)

    def _on_portrait_done(self, face_id, image):
        self._pending.discard(("face", face_id))
        if not image.isNull():
            self._portraits[face_id] = QPixmap.fromImage(image)
            self.portrait_ready.emit(face_id)

    def is_idle(self):
        return not self._pending
