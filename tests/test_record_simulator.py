from datetime import datetime
from service.db import connect, migrate, add_employee, add_camera
from tools.record_simulator import simulate


def test_simulate_writes_paired_session(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    emp = add_employee(conn, "Ayesha", "E1")
    add_camera(conn, "Main gate", "rtsp://cam/1")
    simulate(conn, 1, [(emp, datetime(2026,10,26,13,0), datetime(2026,10,26,13,15))],
             pause_seconds=0)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    row = conn.execute("SELECT duration_minutes, inferred_return FROM sessions").fetchone()
    assert row["duration_minutes"] == 15.0
    assert row["inferred_return"] == 0


def test_open_ended_trip_stays_open(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    emp = add_employee(conn, "Ayesha", "E1")
    add_camera(conn, "Main gate", "rtsp://cam/1")
    simulate(conn, 1, [(emp, datetime(2026,10,26,13,0), None)], pause_seconds=0)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    row = conn.execute("SELECT in_at, inferred_return FROM sessions").fetchone()
    assert row["in_at"] is None and row["inferred_return"] == 0
