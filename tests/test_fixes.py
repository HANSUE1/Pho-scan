"""Regression tests for bugs fixed in the 1.0.5 cleanup."""

import os
from datetime import datetime

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

import analyzer
from gui import data
from timeutil import format_timestamp, from_timestamp, to_timestamp


def _photo_paths(db):
    conn = db.get_db()
    paths = {r["path"] for r in conn.execute("SELECT path FROM photos")}
    conn.close()
    return paths


class TestPruneMissingPhotos:
    def test_deleted_files_are_removed_with_their_faces_and_tags(self, db, seeded, tmp_path):
        tag = data.create_tag("trip")
        data.set_photo_tag([seeded["photo_ids"][0]], tag, True)
        gone = str(tmp_path / "img1.jpg")
        present = _photo_paths(db) - {gone}

        conn = db.get_db()
        removed = analyzer.prune_missing_photos(conn, str(tmp_path), present)
        conn.close()

        assert removed == 1
        assert gone not in _photo_paths(db)
        assert data.list_tags()[0]["photo_count"] == 0  # link cascaded away
        # Alice keeps her face on img2; nothing else changed.
        assert {p["name"] for p in data.list_persons()} == {"Alice", "Bob"}

    def test_photos_outside_the_scanned_folder_are_kept(self, db, seeded, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        conn = db.get_db()
        removed = analyzer.prune_missing_photos(conn, str(other), set())
        conn.close()
        assert removed == 0
        assert len(_photo_paths(db)) == 3

    def test_unreadable_directories_are_left_alone(self, db, seeded, tmp_path):
        conn = db.get_db()
        removed = analyzer.prune_missing_photos(
            conn, str(tmp_path), set(), unreadable_dirs=[str(tmp_path)]
        )
        conn.close()
        assert removed == 0

    def test_missing_folder_is_an_error_not_an_empty_library(self, db, seeded, tmp_path):
        a = analyzer.Analyzer()
        a.start(str(tmp_path / "unplugged-drive"))
        assert a.wait(10)
        progress = a.get_progress()
        assert progress["status"] == "error"
        assert "Folder not found" in progress["error"]
        assert len(_photo_paths(db)) == 3  # nothing was pruned


class TestClipBox:
    def test_box_past_left_top_edge_is_trimmed_not_shifted(self):
        # A 50x50 box starting at (-10, -20) covers x 0..40 and y 0..30.
        assert analyzer.clip_box(-10, -20, 50, 50, 100, 100) == (0, 0, 40, 30)

    def test_box_past_right_bottom_edge_is_trimmed(self):
        assert analyzer.clip_box(80, 90, 50, 50, 100, 100) == (80, 90, 20, 10)

    def test_inside_box_is_unchanged(self):
        assert analyzer.clip_box(10, 10, 30, 40, 100, 100) == (10, 10, 30, 40)


class TestPillowFallbackOrientation:
    def test_exif_rotation_is_applied(self, tmp_path):
        path = tmp_path / "rotated.jpg"
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation: rotate 90° CW to display
        Image.new("RGB", (40, 20)).save(path, exif=exif)
        image = analyzer.Analyzer._read_with_pillow(str(path))
        assert image.shape[:2] == (40, 20)  # (height, width) after rotation


class TestPre1970Dates:
    def test_round_trip(self):
        dt = datetime(1965, 5, 1, 12, 30)
        assert from_timestamp(to_timestamp(dt)) == dt

    def test_format_handles_missing_and_old_values(self):
        assert format_timestamp(None, "%Y") == ""
        assert format_timestamp(to_timestamp(datetime(1950, 1, 2)), "%Y-%m-%d") == "1950-01-02"

    def test_exif_capture_date_before_1970_is_read(self, tmp_path):
        path = tmp_path / "old.jpg"
        exif = Image.Exif()
        exif[analyzer.TAG_DATETIME] = "1962:07:04 10:00:00"
        Image.new("RGB", (10, 10)).save(path, exif=exif)
        taken = analyzer.read_taken_at(str(path))
        assert taken is not None
        assert format_timestamp(taken, "%Y-%m-%d") == "1962-07-04"


class TestDataGuards:
    def test_merging_a_person_into_itself_keeps_its_faces(self, seeded):
        data.merge_person(seeded["alice"], seeded["alice"])
        alice = next(p for p in data.list_persons() if p["name"] == "Alice")
        assert alice["face_count"] == 2

    def test_renaming_a_tag_to_an_existing_name_is_a_clear_error(self, seeded):
        data.create_tag("Beach")
        mountains = data.create_tag("Mountains")
        with pytest.raises(data.DuplicateNameError, match="already exists"):
            data.rename_tag(mountains, "beach")  # case-insensitive clash
        assert sorted(t["name"] for t in data.list_tags()) == ["Beach", "Mountains"]


class TestThumbnailCache:
    def test_changed_crop_misses_the_memory_cache(self, qapp, window):
        from tests.conftest import wait_idle

        photo = window.gallery.photos()[0]
        assert window.loader.thumbnail(photo, window.aspect_key) is not None
        moved = dict(photo, crop=dict(photo["crop"], x=photo["crop"]["x"] + 1))
        # A different crop (e.g. after a face box was deleted) must not
        # reuse the pixmap that was cut for the old crop.
        assert window.loader.thumbnail(moved, window.aspect_key) is None
        wait_idle(qapp, window)
        assert window.loader.thumbnail(moved, window.aspect_key) is not None

    def test_superseded_cache_files_are_deleted(self, qapp, window, tmp_path):
        from tests.conftest import wait_idle

        photo = window.gallery.photos()[0]
        cache_dir = tmp_path / "thumbs_cache"
        prefix = f"{photo['id']}-"
        before = [f for f in os.listdir(cache_dir) if f.startswith(prefix)]
        assert len(before) == 1
        moved = dict(photo, crop=dict(photo["crop"], x=photo["crop"]["x"] + 1))
        window.loader.thumbnail(moved, window.aspect_key)
        wait_idle(qapp, window)
        after = [f for f in os.listdir(cache_dir) if f.startswith(prefix)]
        assert len(after) == 1 and after != before

    def test_memory_cache_is_bounded(self):
        from gui.thumbs import _LruCache

        cache = _LruCache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")  # touch: "b" is now the oldest
        cache.put("c", 3)
        assert list(cache) == ["a", "c"]


class TestWindowLifecycle:
    def test_closing_stops_timers_and_watching(self, qapp, window, tmp_path):
        window.watch_enabled = True
        window._setup_watch(str(tmp_path))
        window.rescan_timer.start()
        window.poll_timer.start()
        window.close()
        assert not window.rescan_timer.isActive()
        assert not window.poll_timer.isActive()
        assert window.watcher.directories() == []
        window._on_dir_changed(str(tmp_path))
        window._auto_rescan()  # no folder any more: must not start anything
        assert not window.analyzer.is_running()

    def test_change_during_a_run_is_retried_afterwards(self, window, tmp_path, monkeypatch):
        window.watch_enabled = True
        window.watched_folder = str(tmp_path)
        monkeypatch.setattr(window.analyzer, "is_running", lambda: True)
        window._on_dir_changed(str(tmp_path))
        assert window.rescan_timer.isActive()  # armed even mid-run
        window.rescan_timer.stop()
        window._auto_rescan()
        assert window.rescan_timer.isActive()  # re-armed, not dropped
        window.rescan_timer.stop()

    def test_resumes_watching_the_last_folder_on_startup(self, qapp, seeded, isolated_settings, tmp_path):
        from gui.window import MainWindow

        isolated_settings().setValue("lastFolder", str(tmp_path))
        win = MainWindow()
        try:
            assert str(tmp_path) in win.watcher.directories()
        finally:
            win.close()
            win.deleteLater()
            qapp.processEvents()


class TestEmptyStates:
    def test_filter_without_matches_explains_itself(self, qapp, window):
        window._toggle_location_filter()
        alice = next(p for p in window.people.persons() if p["name"] == "Alice")
        window._on_person_clicked(alice["id"])  # Alice has no geotagged photo
        assert window.stack.currentIndex() == 1
        assert "match the current filter" in window.empty_label.text()
        window.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert window.stack.currentIndex() == 0

    def test_banner_shows_the_photo_count(self, qapp, window):
        alice = next(p for p in window.people.persons() if p["name"] == "Alice")
        window._on_person_clicked(alice["id"])
        assert "Showing 2 photos" in window.filter_label.text()
