"""Read/write queries used by the GUI.

Every function opens its own short-lived connection through
`database.connect()`, which commits on success, rolls back on error and
always closes — so a failing query can never leak a connection.
"""

import sqlite3

from database import connect, drop_orphan_persons

# Thumbnails are fixed-aspect crops centered on the detected faces.
THUMB_ASPECTS = {"34": 3 / 4, "43": 4 / 3}
THUMB_MAX = {"34": (450, 600), "43": (600, 450)}
PORTRAIT_SIZE = 128


class DuplicateNameError(ValueError):
    """Raised when a rename would collide with an existing (unique) name."""


def compute_crop(
    width: int, height: int, faces: list[dict], aspect: float
) -> dict[str, int]:
    """Fixed-aspect crop window centered on the faces' bounding box (or the middle).

    Returned in original-image coordinates; the same window is used to build
    the thumbnail and to place face boxes on previews.
    """
    if width / height > aspect:
        crop_w, crop_h = round(height * aspect), height
    else:
        crop_w, crop_h = width, round(width / aspect)

    if faces:
        x1 = min(f["x"] for f in faces)
        y1 = min(f["y"] for f in faces)
        x2 = max(f["x"] + f["w"] for f in faces)
        y2 = max(f["y"] + f["h"] for f in faces)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    else:
        cx, cy = width / 2, height / 2

    x = min(max(round(cx - crop_w / 2), 0), width - crop_w)
    y = min(max(round(cy - crop_h / 2), 0), height - crop_h)
    return {"x": x, "y": y, "w": crop_w, "h": crop_h}


def _all_of(column: str, table: str, ids: list[int]) -> tuple[str, list]:
    """SQL condition: photo carries ALL the given ids in `table.column`."""
    marks = ",".join("?" * len(ids))
    sql = (
        f"p.id IN (SELECT photo_id FROM {table} WHERE {column} IN ({marks})"
        f" GROUP BY photo_id HAVING COUNT(DISTINCT {column}) = ?)"
    )
    return sql, [*ids, len(ids)]


def list_photos(
    person_ids: list[int] | None = None,
    aspect_key: str = "34",
    order: str = "name",
    tag_ids: list[int] | None = None,
    with_location: bool = False,
) -> list[dict]:
    """Photos with faces, tags and crops.

    person_ids and tag_ids filter to photos matching ALL of them (AND);
    with_location keeps only photos that carry EXIF coordinates. order is
    "name" or "date" (EXIF capture time, falling back to file mtime).
    """
    order_sql = (
        "ORDER BY COALESCE(taken_at, mtime), filename"
        if order == "date"
        else "ORDER BY filename"
    )
    where, params = [], []
    if person_ids:
        sql, args = _all_of("person_id", "faces", person_ids)
        where.append(sql)
        params.extend(args)
    if tag_ids:
        sql, args = _all_of("tag_id", "photo_tags", tag_ids)
        where.append(sql)
        params.extend(args)
    if with_location:
        where.append("p.lat IS NOT NULL AND p.lon IS NOT NULL")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    # Faces and tags are only fetched for the photos that pass the filter.
    selected = f"SELECT p.id FROM photos p {where_sql}"

    with connect() as db:
        rows = db.execute(f"SELECT p.* FROM photos p {where_sql} {order_sql}", params).fetchall()
        face_rows = db.execute(
            "SELECT f.id, f.photo_id, f.person_id, f.x, f.y, f.w, f.h,"
            " pe.name AS person_name"
            " FROM faces f LEFT JOIN persons pe ON pe.id = f.person_id"
            f" WHERE f.photo_id IN ({selected})",
            params,
        ).fetchall()
        tag_rows = db.execute(
            "SELECT pt.photo_id, t.id, t.name FROM photo_tags pt"
            " JOIN tags t ON t.id = pt.tag_id"
            f" WHERE pt.photo_id IN ({selected}) ORDER BY t.name",
            params,
        ).fetchall()

    tags_by_photo: dict[int, list[dict]] = {}
    for t in tag_rows:
        tags_by_photo.setdefault(t["photo_id"], []).append({"id": t["id"], "name": t["name"]})

    faces_by_photo: dict[int, list[dict]] = {}
    for f in face_rows:
        faces_by_photo.setdefault(f["photo_id"], []).append(
            {
                "id": f["id"],
                "person_id": f["person_id"],
                "person_name": f["person_name"],
                "x": f["x"], "y": f["y"], "w": f["w"], "h": f["h"],
            }
        )

    aspect = THUMB_ASPECTS[aspect_key]
    photos = []
    for p in rows:
        faces = faces_by_photo.get(p["id"], [])
        photos.append(
            {
                "id": p["id"],
                "filename": p["filename"],
                "path": p["path"],
                "width": p["width"],
                "height": p["height"],
                "mtime": p["mtime"],
                "taken_at": p["taken_at"],
                "lat": p["lat"],
                "lon": p["lon"],
                "tags": tags_by_photo.get(p["id"], []),
                "faces": faces,
                "crop": compute_crop(p["width"], p["height"], faces, aspect),
            }
        )
    return photos


# ------------------------------------------------------------------ persons


def list_persons() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT pe.id, pe.name,"
            "       COUNT(DISTINCT f.photo_id) AS photo_count,"
            "       COUNT(f.id) AS face_count,"
            "       (SELECT id FROM faces WHERE person_id = pe.id"
            "        ORDER BY score DESC, w * h DESC LIMIT 1) AS portrait_face_id"
            " FROM persons pe LEFT JOIN faces f ON f.person_id = pe.id"
            " GROUP BY pe.id HAVING face_count > 0 ORDER BY photo_count DESC, pe.id"
        ).fetchall()
    return [dict(r) for r in rows]


def rename_person(person_id: int, name: str) -> None:
    with connect() as db:
        db.execute("UPDATE persons SET name = ? WHERE id = ?", (name, person_id))


def delete_person(person_id: int) -> None:
    """Delete a person together with its face boxes (e.g. false detections)."""
    with connect() as db:
        db.execute("DELETE FROM faces WHERE person_id = ?", (person_id,))
        db.execute("DELETE FROM persons WHERE id = ?", (person_id,))


def merge_person(source_id: int, target_id: int) -> None:
    """Move all faces of source_id into target_id and delete source_id."""
    if source_id == target_id:
        # Merging into itself would delete the person and orphan its faces.
        return
    with connect() as db:
        db.execute("UPDATE faces SET person_id = ? WHERE person_id = ?", (target_id, source_id))
        db.execute("DELETE FROM persons WHERE id = ?", (source_id,))


# -------------------------------------------------------------------- faces


def assign_face(face_id: int, person_id: int) -> None:
    """Move one face to another person and pin it against re-clustering."""
    with connect() as db:
        db.execute(
            "UPDATE faces SET person_id = ?, pinned = 1 WHERE id = ?", (person_id, face_id)
        )
        drop_orphan_persons(db)


def split_face_to_new_person(face_id: int, name: str) -> int:
    """Move one face to a brand-new person (a wrongly merged face)."""
    with connect() as db:
        person_id = db.execute("INSERT INTO persons (name) VALUES (?)", (name,)).lastrowid
        db.execute(
            "UPDATE faces SET person_id = ?, pinned = 1 WHERE id = ?", (person_id, face_id)
        )
        drop_orphan_persons(db)
    return person_id


def delete_face(face_id: int) -> None:
    """Remove a face box (a false detection); the photo is kept."""
    with connect() as db:
        db.execute("DELETE FROM faces WHERE id = ?", (face_id,))
        drop_orphan_persons(db)


def face_portrait_source(face_id: int) -> dict | None:
    """Photo path and face rect for a person's avatar, or None if missing."""
    with connect() as db:
        row = db.execute(
            "SELECT f.x, f.y, f.w, f.h, p.path FROM faces f"
            " JOIN photos p ON p.id = f.photo_id WHERE f.id = ?",
            (face_id,),
        ).fetchone()
    return dict(row) if row is not None else None


# --------------------------------------------------------------------- tags


def list_tags() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT t.id, t.name, COUNT(pt.photo_id) AS photo_count"
            " FROM tags t LEFT JOIN photo_tags pt ON pt.tag_id = t.id"
            " GROUP BY t.id ORDER BY t.name"
        ).fetchall()
    return [dict(r) for r in rows]


def create_tag(name: str) -> int:
    """Create the tag (or return the id of an existing one, case-insensitive)."""
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        return db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]


def rename_tag(tag_id: int, name: str) -> None:
    """Rename a tag; raises DuplicateNameError if another tag has that name."""
    try:
        with connect() as db:
            db.execute("UPDATE tags SET name = ? WHERE id = ?", (name, tag_id))
    except sqlite3.IntegrityError as exc:
        raise DuplicateNameError(f"A tag named “{name}” already exists.") from exc


def delete_tag(tag_id: int) -> None:
    """Delete a tag; the photos themselves are untouched."""
    with connect() as db:
        db.execute("DELETE FROM photo_tags WHERE tag_id = ?", (tag_id,))
        db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))


def set_photo_tag(photo_ids: list[int], tag_id: int, attached: bool) -> None:
    """Attach or detach one tag on several photos at once."""
    sql = (
        "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)"
        if attached
        else "DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?"
    )
    with connect() as db:
        db.executemany(sql, [(photo_id, tag_id) for photo_id in photo_ids])
