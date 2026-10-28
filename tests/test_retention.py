import os
import time
from datetime import datetime
from pathlib import Path
from service.retention import purge_clips_older_than, disk_free_gb, clear_clip_paths

def _make(path_tail: str, age_days: int) -> Path:
    p = Path(path_tail)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    old = time.time() - age_days * 86400
    os.utime(p, (old, old))
    return p

def test_purge_removes_only_old(tmp_path):
    clips = tmp_path / "clips"
    recent = _make(str(clips / "2026/10/26" / "keep.mp4"), 5)
    old = _make(str(clips / "2026/06/01" / "drop.mp4"), 120)
    hit = purge_clips_older_than(clips, 90)
    assert old in hit and recent not in hit
    assert not old.exists() and recent.exists()

def test_dry_run_deletes_nothing(tmp_path):
    clips = tmp_path / "clips"
    old = _make(str(clips / "2026/06/01" / "drop.mp4"), 120)
    hit = purge_clips_older_than(clips, 90, dry_run=True)
    assert old in hit and old.exists()

def test_clear_clip_paths_nulls_events(tmp_path):
    from service.db import connect, migrate, insert_event, add_employee, add_camera
    conn = connect(tmp_path / "t.db"); migrate(conn)
    add_employee(conn, "Emp One"); add_camera(conn, "cam")
    insert_event(conn, 1, 1, "OUT", datetime(2026,10,26,13,0),
                 str(tmp_path / "clips" / "drop.mp4"), 0.9, 0, 1)
    n = clear_clip_paths(conn, [tmp_path / "clips" / "drop.mp4"])
    assert n == 1
    assert conn.execute("SELECT clip_path FROM events").fetchone()[0] is None

def test_disk_free_gb_positive(tmp_path):
    assert disk_free_gb(tmp_path) > 0
