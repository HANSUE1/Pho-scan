"""Recursive photo scanning, face detection (YuNet) and face clustering (SFace)."""

import logging
import os
import sqlite3
import struct
import threading
from collections.abc import Set
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageOps

from database import drop_orphan_persons, get_db
from paths import resource_dir
from timeutil import to_timestamp

log = logging.getLogger(__name__)

MODELS_DIR = os.path.join(resource_dir(), "models")
DETECTOR_MODEL = os.path.join(MODELS_DIR, "face_detection_yunet_2023mar.onnx")
RECOGNIZER_MODEL = os.path.join(MODELS_DIR, "face_recognition_sface_2021dec.onnx")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

# Cosine similarity threshold for the live (greedy, mean-linkage) assignment
# used while a run is in progress.
GREEDY_THRESHOLD = 0.32
# Threshold for the final average-linkage re-clustering of all faces. Lower
# than pairwise verification thresholds because it applies to cluster means.
CLUSTER_THRESHOLD = 0.28
# Full re-clustering is O(n^2) memory; above this face count keep greedy results.
MAX_RECLUSTER_FACES = 4000
DETECTION_SCORE_THRESHOLD = 0.8
MAX_DETECTION_SIDE = 1280
MIN_FACE_SIZE = 24
# At most this many embeddings per person are kept in memory for matching.
MAX_EMBEDDINGS_PER_PERSON = 32

# ONNX/protobuf files start with a varint-encoded field tag. A quick sanity
# check: the first byte should be a plausible field tag (0x08 = field 1 wire
# type 0, which is how ONNX graphs begin) and the file should contain protobuf
# keywords like "graph", "node", or "op".
_MODEL_SANITY_BYTES = (b"graph", b"node", b"op", b"input", b"output")


class ModelError(Exception):
    """Raised when a model file is missing, corrupt, or unsupported."""


def validate_model(path: str) -> None:
    """Check that `path` looks like a valid ONNX/protobuf model file.

    Raises ModelError with a user-friendly message if the file is missing,
    empty, or clearly not a model.
    """
    if not os.path.isfile(path):
        raise ModelError(
            f"Model file missing: {path}\n"
            "Run `python scripts/download_models.py` to download it."
        )
    if os.path.getsize(path) < 1024:
        raise ModelError(
            f"Model file too small ({os.path.getsize(path)} bytes): {path}\n"
            "The download may have been interrupted. Delete the file and run "
            "`python scripts/download_models.py` again."
        )
    with open(path, "rb") as f:
        header = f.read(64)
    # Protobuf varint for field 1 (the ONNX "graph" or "ir_version" field)
    # typically starts with 0x08. Also look for protobuf keywords.
    if not any(k in header for k in _MODEL_SANITY_BYTES):
        raise ModelError(
            f"Model file does not look like a valid ONNX model: {path}\n"
            "Delete it and run `python scripts/download_models.py` again."
        )


# EXIF tags: DateTimeOriginal lives in the Exif sub-IFD, DateTime in IFD0.
EXIF_SUB_IFD = 0x8769
GPS_IFD = 0x8825
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME = 306
# GPS IFD tags: latitude/longitude and their N/S, E/W reference.
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4


def _read_exif(path: str):
    """The file's EXIF block, or None if the file can't be opened."""
    try:
        with Image.open(path) as img:
            return img.getexif()
    except Exception:  # noqa: BLE001 - EXIF is best-effort
        return None


def _taken_at_from_exif(exif) -> float | None:
    try:
        value = exif.get_ifd(EXIF_SUB_IFD).get(TAG_DATETIME_ORIGINAL) or exif.get(
            TAG_DATETIME
        )
        if not value:
            return None
        return to_timestamp(datetime.strptime(str(value).strip(), "%Y:%m:%d %H:%M:%S"))
    except Exception:  # noqa: BLE001 - malformed EXIF dates are common
        return None


def _dms_to_degrees(dms, ref) -> float | None:
    """Degrees/minutes/seconds triple plus an N/S/E/W reference to a float."""
    if not dms or not ref:
        return None
    degrees, minutes, seconds = (float(part) for part in dms)
    value = degrees + minutes / 60 + seconds / 3600
    return -value if str(ref).strip().upper() in ("S", "W") else value


def _gps_from_exif(exif) -> tuple[float, float] | None:
    try:
        gps = exif.get_ifd(GPS_IFD)
        if not gps:
            return None
        lat = _dms_to_degrees(gps.get(GPS_LAT), gps.get(GPS_LAT_REF))
        lon = _dms_to_degrees(gps.get(GPS_LON), gps.get(GPS_LON_REF))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return lat, lon
    except Exception:  # noqa: BLE001 - malformed GPS blocks are common
        return None


def read_taken_at(path: str) -> float | None:
    """Capture timestamp from EXIF metadata, or None when unavailable."""
    exif = _read_exif(path)
    return _taken_at_from_exif(exif) if exif is not None else None


def read_gps(path: str) -> tuple[float, float] | None:
    """(latitude, longitude) from EXIF GPS metadata, or None when absent."""
    exif = _read_exif(path)
    return _gps_from_exif(exif) if exif is not None else None


def upsert_photo(
    db: sqlite3.Connection,
    path: str,
    width: int,
    height: int,
    mtime: float,
    existing_id: int | None = None,
) -> int:
    """Insert or refresh a photo row and return its id.

    A changed file keeps its row (and therefore its tags); only its faces are
    dropped so the detector can re-populate them.

    `existing_id` lets a caller that already looked up the row (as
    `_process_photo` does, to check `mtime`) skip a second identical SELECT.
    Left as None, the row is looked up here exactly as before.
    """
    exif = None
    taken_at = None
    gps = None
    try:
        exif = _read_exif(path)
        if exif is not None:
            taken_at = _taken_at_from_exif(exif)
            gps = _gps_from_exif(exif)
    except Exception:  # noqa: BLE001 - EXIF is best-effort, never block analysis
        log.warning("could not read EXIF from %s", path)
    lat, lon = gps if gps else (None, None)
    if existing_id is None:
        existing = db.execute("SELECT id FROM photos WHERE path = ?", (path,)).fetchone()
        existing_id = existing["id"] if existing is not None else None
    if existing_id is not None:
        photo_id = existing_id
        db.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
        db.execute(
            "UPDATE photos SET filename = ?, width = ?, height = ?, mtime = ?,"
            " taken_at = ?, lat = ?, lon = ? WHERE id = ?",
            (os.path.basename(path), width, height, mtime, taken_at, lat, lon, photo_id),
        )
        return photo_id

    cur = db.execute(
        "INSERT INTO photos (path, filename, width, height, mtime, taken_at, lat, lon)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (path, os.path.basename(path), width, height, mtime, taken_at, lat, lon),
    )
    return cur.lastrowid


def _scan(folder: str) -> tuple[list[str], list[str]]:
    """(sorted image paths under folder, directories that could not be read)."""
    files, unreadable = [], []

    def on_error(err: OSError) -> None:
        unreadable.append(err.filename)

    for root, _dirs, names in os.walk(folder, onerror=on_error):
        for name in names:
            if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS:
                files.append(os.path.join(root, name))
    files.sort()
    return files, unreadable


def scan_image_files(folder: str) -> list[str]:
    """Return sorted list of image file paths under folder, recursively."""
    return _scan(folder)[0]


def _is_under(path: str, folder: str) -> bool:
    return path.startswith(os.path.join(folder, ""))


def prune_missing_photos(
    db: sqlite3.Connection,
    folder: str,
    present: Set[str],
    unreadable_dirs: list[str] = (),
) -> int:
    """Delete rows for photos under `folder` whose file is gone.

    Only rows inside the scanned folder are considered (other analyzed
    folders are left alone), and nothing under a directory that couldn't be
    listed is touched — a permission hiccup must not wipe tags and faces.
    Faces and tag links go with the photo (ON DELETE CASCADE); persons left
    without faces are dropped. Returns the number of photos removed.
    """
    gone = [
        (row["id"],)
        for row in db.execute("SELECT id, path FROM photos").fetchall()
        if _is_under(row["path"], folder)
        and row["path"] not in present
        and not any(row["path"] == d or _is_under(row["path"], d) for d in unreadable_dirs)
    ]
    if gone:
        db.executemany("DELETE FROM photos WHERE id = ?", gone)
        drop_orphan_persons(db)
        db.commit()
    return len(gone)


def clip_box(x: int, y: int, w: int, h: int, width: int, height: int) -> tuple[int, ...]:
    """Intersect a detection box with the image bounds on all four sides.

    Clamping only x/y and keeping w/h would *shift* a box that pokes out past
    the left/top edge instead of trimming it.
    """
    x2, y2 = min(x + w, width), min(y + h, height)
    x, y = max(x, 0), max(y, 0)
    return int(x), int(y), int(x2 - x), int(y2 - y)


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _average_linkage(embeddings: np.ndarray, threshold: float) -> list[list[int]]:
    """Agglomerative average-linkage clustering on cosine similarities.

    Uses Lance-Williams updates on a full similarity matrix: O(n^2) memory,
    fine for a few thousand faces. Returns a list of index lists.
    """
    n = len(embeddings)
    sim = embeddings @ embeddings.T
    np.fill_diagonal(sim, -2.0)
    sizes = np.ones(n)
    members = {i: [i] for i in range(n)}
    active = np.ones(n, dtype=bool)

    while True:
        idx = np.flatnonzero(active)
        if len(idx) < 2:
            break
        sub = sim[np.ix_(idx, idx)]
        flat = int(np.argmax(sub))
        i_, j_ = divmod(flat, len(idx))
        if sub[i_, j_] < threshold:
            break
        i, j = int(idx[i_]), int(idx[j_])

        merged_row = (sizes[i] * sim[i] + sizes[j] * sim[j]) / (sizes[i] + sizes[j])
        sim[i, :] = merged_row
        sim[:, i] = merged_row
        sim[i, i] = -2.0
        sim[j, :] = -2.0
        sim[:, j] = -2.0
        sizes[i] += sizes[j]
        members[i].extend(members.pop(j))
        active[j] = False

    return [members[i] for i in np.flatnonzero(active)]


class Analyzer:
    """Runs analysis in a background thread and exposes progress state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self.progress: dict[str, object] = {
            "status": "idle",  # idle | running | done | canceled | error
            "total": 0,
            "done": 0,
            "current": "",
            "error": "",
        }

    def _set(self, **kwargs: object) -> None:
        with self._lock:
            self.progress.update(kwargs)

    def get_progress(self) -> dict[str, object]:
        with self._lock:
            return dict(self.progress)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, folder: str) -> bool:
        if self.is_running():
            return False
        self._cancel_event.clear()
        self._set(status="running", total=0, done=0, current="", error="")
        self._thread = threading.Thread(target=self._run, args=(folder,), daemon=True)
        self._thread.start()
        return True

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the worker thread exits; True if it did within timeout."""
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.is_running()

    def cancel(self) -> None:
        """Ask a running analysis to stop after the photo it's currently on.

        Whatever was already processed stays committed; faces detected so
        far are kept, just not re-clustered against the rest of the library
        until the next run.
        """
        self._cancel_event.set()

    # ---------------------------------------------------------------- core

    def _run(self, folder: str) -> None:
        db = None
        try:
            # Checked here, not only in the GUI: an auto-rescan can fire after
            # the folder (or the drive it lives on) went away, and an empty
            # scan must never be mistaken for "every photo was deleted".
            if not os.path.isdir(folder):
                raise FileNotFoundError(f"Folder not found: {folder}")
            for model in (DETECTOR_MODEL, RECOGNIZER_MODEL):
                try:
                    validate_model(model)
                except ModelError as exc:
                    raise ModelError(str(exc)) from None
            try:
                detector = cv2.FaceDetectorYN.create(
                    DETECTOR_MODEL, "", (320, 320), DETECTION_SCORE_THRESHOLD
                )
                recognizer = cv2.FaceRecognizerSF.create(RECOGNIZER_MODEL, "")
            except cv2.error as exc:
                raise ModelError(
                    f"OpenCV could not load the face models.\n{exc}\n"
                    "The model files may be corrupt or incompatible with this "
                    "OpenCV version. Delete `models/` and run "
                    "`python scripts/download_models.py` again."
                ) from exc

            files, unreadable = _scan(folder)
            self._set(total=len(files))

            db = get_db()
            removed = prune_missing_photos(db, folder, set(files), unreadable)
            if removed:
                log.info("removed %d photo(s) no longer on disk", removed)
            persons = self._load_persons(db)
            # Faces that existed before this run carry assignments from the
            # previous run and any manual edits (rename/merge); they outrank
            # this run's greedy guesses when clusters are mapped to persons.
            confirmed_ids = {r["id"] for r in db.execute("SELECT id FROM faces").fetchall()}

            canceled = False
            processed = 0
            for i, path in enumerate(files):
                if self._cancel_event.is_set():
                    canceled = True
                    break
                self._set(done=i, current=os.path.basename(path))
                try:
                    self._process_photo(db, detector, recognizer, persons, path)
                except Exception as exc:  # noqa: BLE001 - skip unreadable files
                    # Drop the half-written photo (its old faces were already
                    # deleted) instead of letting the next commit persist it,
                    # and forget persons that only existed in that rollback.
                    db.rollback()
                    alive = {r[0] for r in db.execute("SELECT id FROM persons")}
                    for pid in [p for p in persons if p not in alive]:
                        del persons[pid]
                    log.warning("skipping %s: %s", path, exc)
                processed = i + 1

            if not canceled:
                self._set(current="Clustering faces…")
                self._recluster(db, confirmed_ids)
            self._set(
                status="canceled" if canceled else "done",
                done=processed if canceled else len(files),
                current="",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("analysis of %s failed", folder)
            self._set(status="error", error=str(exc))
        finally:
            if db is not None:
                db.close()

    def _load_persons(self, db: sqlite3.Connection) -> dict[int, list[np.ndarray]]:
        """Load stored embeddings grouped by person for incremental clustering."""
        persons: dict[int, list[np.ndarray]] = {}
        rows = db.execute(
            "SELECT person_id, embedding FROM faces WHERE person_id IS NOT NULL"
        ).fetchall()
        for row in rows:
            vec = _normalize(np.frombuffer(row["embedding"], dtype=np.float32))
            persons.setdefault(row["person_id"], []).append(vec)
        for embeddings in persons.values():
            del embeddings[MAX_EMBEDDINGS_PER_PERSON:]
        return persons

    def _process_photo(
        self,
        db: sqlite3.Connection,
        detector: cv2.FaceDetectorYN,
        recognizer: cv2.FaceRecognizerSF,
        persons: dict[int, list[np.ndarray]],
        path: str,
    ) -> None:
        mtime = os.path.getmtime(path)
        existing = db.execute("SELECT id, mtime FROM photos WHERE path = ?", (path,)).fetchone()
        if existing is not None and abs(existing["mtime"] - mtime) < 1e-6:
            return  # already analyzed and unchanged

        image = cv2.imread(path)
        if image is None:
            image = self._read_with_pillow(path)
        if image is None or image.size == 0:
            raise ValueError(f"unreadable image: {os.path.basename(path)}")

        height, width = image.shape[:2]
        if width == 0 or height == 0:
            raise ValueError(f"empty image: {os.path.basename(path)}")

        # Detect on a downscaled copy for speed, map boxes back to full size.
        scale = 1.0
        detect_img = image
        if max(width, height) > MAX_DETECTION_SIDE:
            scale = MAX_DETECTION_SIDE / max(width, height)
            detect_img = cv2.resize(image, (round(width * scale), round(height * scale)))

        detector.setInputSize((detect_img.shape[1], detect_img.shape[0]))
        _, detections = detector.detect(detect_img)

        photo_id = upsert_photo(
            db, path, width, height, mtime,
            existing_id=existing["id"] if existing is not None else None,
        )

        if detections is not None:
            for det in detections:
                x, y, w, h = clip_box(*(det[:4] / scale).round().astype(int), width, height)
                if w < MIN_FACE_SIZE or h < MIN_FACE_SIZE:
                    continue  # skipped before the (expensive) embedding step

                feature = recognizer.feature(recognizer.alignCrop(detect_img, det))
                embedding = _normalize(feature.flatten().astype(np.float32))
                person_id = self._assign_person(db, persons, embedding)
                db.execute(
                    "INSERT INTO faces (photo_id, person_id, x, y, w, h, score, embedding)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (photo_id, person_id, int(x), int(y), int(w), int(h),
                     float(det[14]), embedding.tobytes()),
                )
        db.commit()

    def _assign_person(
        self,
        db: sqlite3.Connection,
        persons: dict[int, list[np.ndarray]],
        embedding: np.ndarray,
    ) -> int:
        """Greedy mean-linkage match against known persons (live preview only).

        The final grouping is decided by _recluster() at the end of the run.
        """
        best_id, best_sim = None, -1.0
        for person_id, embeddings in persons.items():
            sim = float(np.mean([np.dot(embedding, e) for e in embeddings]))
            if sim > best_sim:
                best_id, best_sim = person_id, sim

        if best_id is not None and best_sim >= GREEDY_THRESHOLD:
            if len(persons[best_id]) < MAX_EMBEDDINGS_PER_PERSON:
                persons[best_id].append(embedding)
            return best_id

        person_id = self._create_person(db)
        persons[person_id] = [embedding]
        return person_id

    @staticmethod
    def _create_person(db: sqlite3.Connection) -> int:
        cur = db.execute("INSERT INTO persons (name) VALUES ('')")
        person_id = cur.lastrowid
        db.execute("UPDATE persons SET name = ? WHERE id = ?", (f"Persona {person_id}", person_id))
        return person_id

    # ----------------------------------------------------------- clustering

    def _recluster(
        self, db: sqlite3.Connection, confirmed_ids: Set[int] = frozenset()
    ) -> None:
        """Re-cluster all faces with average-linkage and remap to existing persons.

        Mapping clusters back to persons uses two passes:
        - Confirmed faces (existed before this run, i.e. previous results plus
          manual renames/merges) vote first and MAY map several clusters onto
          one person — that is what keeps a user's manual merge intact.
        - Faces assigned by this run's greedy pass vote second, one-to-one
          only, so a greedy over-merge cannot pull two clusters together.

        Faces the user assigned by hand are pinned: they vote as confirmed and
        are never moved, however the clustering turns out.
        """
        rows = db.execute("SELECT id, person_id, embedding, pinned FROM faces").fetchall()
        if len(rows) < 2 or len(rows) > MAX_RECLUSTER_FACES:
            return

        pinned_ids = {r["id"] for r in rows if r["pinned"]}
        confirmed_ids = set(confirmed_ids) | pinned_ids

        face_ids = [r["id"] for r in rows]
        old_person = [r["person_id"] for r in rows]
        embeddings = np.stack(
            [_normalize(np.frombuffer(r["embedding"], dtype=np.float32)) for r in rows]
        )
        clusters = _average_linkage(embeddings, CLUSTER_THRESHOLD)

        # Per-cluster overlap counts with previous persons, split by whether
        # the vote comes from a confirmed face or a fresh (greedy) one.
        confirmed_overlaps = []  # (count, cluster_index, person_id)
        fresh_overlaps = []
        for ci, members in enumerate(clusters):
            confirmed_counts, fresh_counts = {}, {}
            for m in members:
                pid = old_person[m]
                if pid is None:
                    continue
                bucket = confirmed_counts if face_ids[m] in confirmed_ids else fresh_counts
                bucket[pid] = bucket.get(pid, 0) + 1
            confirmed_overlaps.extend((n, ci, pid) for pid, n in confirmed_counts.items())
            fresh_overlaps.extend((n, ci, pid) for pid, n in fresh_counts.items())

        cluster_person = {}
        used_persons = set()
        for _count, ci, pid in sorted(confirmed_overlaps, reverse=True):
            if ci in cluster_person:
                continue
            cluster_person[ci] = pid
            used_persons.add(pid)
        for _count, ci, pid in sorted(fresh_overlaps, reverse=True):
            if ci in cluster_person or pid in used_persons:
                continue
            cluster_person[ci] = pid
            used_persons.add(pid)

        for ci, members in enumerate(clusters):
            pid = cluster_person.get(ci)
            if pid is None:
                pid = self._create_person(db)
            db.executemany(
                "UPDATE faces SET person_id = ? WHERE id = ?",
                [(pid, face_ids[m]) for m in members if face_ids[m] not in pinned_ids],
            )

        drop_orphan_persons(db)
        db.commit()

    @staticmethod
    def _read_with_pillow(path: str) -> np.ndarray | None:
        """Fallback reader for formats cv2.imread cannot open.

        EXIF orientation is applied, like cv2.imread and the GUI's
        QImageReader(autoTransform) do, so stored face coordinates line up
        with the image as it is displayed.
        """
        try:
            with Image.open(path) as img:
                upright = ImageOps.exif_transpose(img).convert("RGB")
            return cv2.cvtColor(np.array(upright), cv2.COLOR_RGB2BGR)
        except Exception:  # noqa: BLE001
            return None
