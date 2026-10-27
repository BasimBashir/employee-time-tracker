import pytest
from datetime import datetime
from pathlib import Path
from service.db import (connect, migrate, add_employee, add_camera, insert_event,
                        open_session, close_session, force_close_open_sessions,
                        soft_delete_session, audit)


@pytest.fixture
def conn(tmp_path):
    c = connect(Path(tmp_path / "t.db")); migrate(c); return c


def _seed(conn, code="E1"):
    """An event needs both an employee and a camera to satisfy the FKs."""
    emp = add_employee(conn, "Ayesha", code)
    cam = add_camera(conn, "Main gate", "rtsp://cam/1")
    return emp, cam


def test_wal_enabled(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_session_roundtrip(conn):
    emp, cam = _seed(conn)
    out_id = insert_event(conn, emp, cam, "OUT", datetime(2026,10,26,13,0), None, 0.9, 0, 7)
    sid = open_session(conn, emp, out_id, datetime(2026,10,26,13,0), "2026-10-26")
    in_id = insert_event(conn, emp, cam, "IN", datetime(2026,10,26,13,15), None, 0.85, 0, 7)
    assert close_session(conn, sid, in_id, datetime(2026,10,26,13,15)) == 15.0


def test_midnight_force_close_flags_inferred(conn):
    emp, cam = _seed(conn)
    out_id = insert_event(conn, emp, cam, "OUT", datetime(2026,10,26,22,0), None, 0.9, 0, 7)
    open_session(conn, emp, out_id, datetime(2026,10,26,22,0), "2026-10-26")
    assert force_close_open_sessions(conn, "2026-10-26") == 1
    assert conn.execute("SELECT inferred_return FROM sessions").fetchone()[0] == 1


def test_soft_delete_keeps_row_and_audits(conn):
    emp, cam = _seed(conn)
    out_id = insert_event(conn, emp, cam, "OUT", datetime(2026,10,26,13,0), None, 0.9, 0, 7)
    sid = open_session(conn, emp, out_id, datetime(2026,10,26,13,0), "2026-10-26")
    soft_delete_session(conn, sid, "owner", "disputed")
    assert conn.execute("SELECT deleted_at FROM sessions WHERE id=?",
                        (sid,)).fetchone()[0] is not None
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='delete_session'"
                        ).fetchone()[0] == 1
