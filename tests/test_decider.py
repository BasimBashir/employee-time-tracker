from datetime import datetime, timezone
from types import SimpleNamespace
import numpy as np

from service.db import connect, migrate, add_camera, add_employee
from service.zone import Zone, save_zone, get_active_zone
from service.decider import Decider
from service.statemachine import Crossing
from service.tracker import Track

LINE = [(0.0, 270.0), (960.0, 270.0)]

def make_decider(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    add_camera(conn, "cam"); add_employee(conn, "Emp One")
    save_zone(conn, Zone(None, 1, LINE, "POS_TO_NEG", True))
    rec = SimpleNamespace(add_frame=lambda *a: None,
                          capture=lambda *a, **k: tmp_path / "c.mp4")
    d = Decider(conn, rec, zone_provider=lambda cam: get_active_zone(conn, cam),
                identity_resolver=lambda trk: (1, 0.9))    # everything is employee 1
    return conn, d

def _track(tid, cx, cy):
    return Track(tid, (cx - 20, cy - 60, cx + 20, cy + 60), 0.9)

def drive(decider, sides, t0=0.0, step=4.0):
    """One analysis frame per side, far enough apart to clear the 3s debounce."""
    for i, side in enumerate(sides):
        cy = 100.0 if side == 1 else 400.0
        frame = SimpleNamespace(image=np.zeros((540, 960, 3), np.uint8),
                                timestamp=t0 + i * step, camera_id=1)
        decider.on_tracks([_track(1, 480, cy)], frame)

def test_full_out_and_back_cycle(tmp_path):
    conn, d = make_decider(tmp_path)
    d.on_crossing(Crossing(1, "OUT", datetime(2026,10,26,13,0, tzinfo=timezone.utc), 1, 1, 0.9))
    d.on_crossing(Crossing(1, "IN",  datetime(2026,10,26,13,15, tzinfo=timezone.utc), 1, 1, 0.9))
    rows = conn.execute("SELECT out_event_id, in_event_id, duration_minutes FROM sessions").fetchall()
    assert len(rows) == 1
    assert rows[0]["duration_minutes"] == 15.0
    evs = conn.execute("SELECT id, event_type, clip_path FROM events ORDER BY id").fetchall()
    assert [e["event_type"] for e in evs] == ["OUT", "IN"]
    assert rows[0]["out_event_id"] == evs[0]["id"] and rows[0]["in_event_id"] == evs[1]["id"]
    assert evs[0]["clip_path"].endswith(".mp4")

def test_on_tracks_debounced_drive_opens_and_closes(tmp_path):
    conn, d = make_decider(tmp_path)
    drive(d, [1, 1, -1, -1, 1, 1], t0=1000.0, step=4.0)   # out at frame 2, in at frame 4
    evs = conn.execute("SELECT event_type FROM events ORDER BY id").fetchall()
    assert [e["event_type"] for e in evs] == ["OUT", "IN"]
    sess = conn.execute("SELECT duration_minutes FROM sessions").fetchall()
    assert len(sess) == 1 and sess[0]["duration_minutes"] > 0
    assert d.stats() == {"crossings": 2, "sessions_closed": 1, "events_logged": 2}

def test_single_frame_flicker_records_nothing(tmp_path):
    conn, d = make_decider(tmp_path)
    drive(d, [1, 1, -1, 1, 1, 1], t0=1000.0, step=4.0)    # one frame below the line only
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0

def test_unenrolled_track_records_nothing(tmp_path):
    conn, d = make_decider(tmp_path)
    d.identity_resolver = lambda trk: None
    drive(d, [1, 1, -1, -1, 1, 1], t0=1000.0, step=4.0)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

def test_force_flush_closes_open_session(tmp_path):
    conn, d = make_decider(tmp_path)
    d.on_crossing(Crossing(1, "OUT", datetime(2026,10,26,22,0, tzinfo=timezone.utc), 1, 1, 0.9))
    d.force_flush(datetime(2026,10,26,23,59,59, tzinfo=timezone.utc))
    row = conn.execute("SELECT inferred_return, duration_minutes FROM sessions").fetchone()
    assert row["inferred_return"] == 1 and row["duration_minutes"] > 0
