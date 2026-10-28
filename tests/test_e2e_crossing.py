"""Two synthetic crossings -> two sessions with correct durations.

The regression net for the whole product: tracks walk across the line, the
debounce confirms side changes, the state machine pairs OUT/IN, SQLite gets
events and sessions, and the clip recorder never grows unbounded.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from service.db import connect, migrate, add_camera, add_employee
from service.zone import Zone
from service.decider import Decider
from service.cliprecorder import ClipRecorder
from service.tracker import Track

LINE = [(0.0, 270.0), (960.0, 270.0)]

def _track(tid, cx, cy):
    return Track(tid, (cx - 20, cy - 60, cx + 20, cy + 60), 0.9)

def test_two_excursions(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    add_camera(conn, "cam"); add_employee(conn, "Emp One")
    rec = ClipRecorder(tmp_path / "clips", pre_seconds=1, post_seconds=0, fps=10)
    d = Decider(conn, rec,
                zone_provider=lambda c: Zone(1, c, LINE, "POS_TO_NEG", True),
                identity_resolver=lambda t: (1, 0.92))
    # person 1: above the line, walks below it (OUT), returns (IN), twice —
    # frames 4 s apart so each confirmed crossing clears the 3 s debounce
    walk = [100, 200, 320, 400, 400, 320, 200, 100,
            100, 200, 320, 400, 400, 400, 400, 320, 200, 100]
    for i, cy in enumerate(walk):
        frame = SimpleNamespace(image=np.zeros((540, 960, 3), np.uint8),
                                timestamp=1780000000.0 + i * 4.0, camera_id=1)
        d.on_tracks([_track(1, 480, float(cy))], frame)
        d.on_frame(frame.image, frame.timestamp)
    rows = conn.execute(
        "SELECT out_at, in_at, duration_minutes, inferred_return FROM sessions").fetchall()
    assert len(rows) == 2, rows
    assert all(r["duration_minutes"] is not None for r in rows)
    assert rows[-1]["duration_minutes"] > rows[0]["duration_minutes"]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE event_type='OUT'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM events WHERE event_type='IN'").fetchone()[0] == 2
    rec.stop()

def test_sustained_ingest_stays_bounded(tmp_path):
    """Memory sanity for the phase's biggest risk: the clip ring buffer."""
    import os
    conn = connect(tmp_path / "t.db"); migrate(conn)
    rec = ClipRecorder(tmp_path / "clips", fps=15, max_seconds=15)   # defaults
    d = Decider(conn, rec, zone_provider=lambda c: None,
                identity_resolver=lambda t: None)
    img = np.zeros((540, 960, 3), np.uint8)
    for i in range(900):                      # a minute of 15 fps ingest
        d.on_frame(img, 1000.0 + i / 15.0)
    assert len(rec._buffer) == 15 * 15        # ring buffer: one window, flat
    assert rec.memory_bytes_estimate() < 25 * 1024 * 1024
    import psutil
    rss_mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    assert rss_mb < 1500, rss_mb
    rec.stop()
