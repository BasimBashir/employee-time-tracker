import json
import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from service.db import (connect, migrate, add_employee, add_camera, insert_event,
                        open_session, close_session)
from service.api import create_app


def make_client(tmp_path, cfg=None):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    return TestClient(create_app(conn, cfg))


def _seed(conn, code="E1"):
    emp = add_employee(conn, "Ayesha", code)
    cam = add_camera(conn, "Main gate", "rtsp://cam/1")
    return emp, cam


def test_health(tmp_path):
    r = make_client(tmp_path).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_events_since(tmp_path):
    c = make_client(tmp_path)
    emp, cam = _seed(c.app.state.conn)
    insert_event(c.app.state.conn, emp, cam, "OUT", datetime(2026,10,26,13,0), None, 0.9, 0, 7)
    r = c.get("/events", params={"since": "2026-10-26T00:00:00"})
    assert len(r.json()) == 1


def test_soft_delete_via_api(tmp_path):
    c = make_client(tmp_path)
    emp, cam = _seed(c.app.state.conn)
    out = insert_event(c.app.state.conn, emp, cam, "OUT", datetime(2026,10,26,13,0), None, 0.9, 0, 7)
    sid = open_session(c.app.state.conn, emp, out, datetime(2026,10,26,13,0), "2026-10-26")
    r = c.request("DELETE", f"/sessions/{sid}",
                  content=json.dumps({"actor": "owner", "reason": "disputed"}),
                  headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    # soft delete: the API view excludes it, but the row survives for dispute handling
    assert c.get("/sessions", params={"date": "2026-10-26"}).json() == []
    row = c.app.state.conn.execute(
        "SELECT deleted_reason FROM sessions WHERE id=?", (sid,)).fetchone()
    assert row["deleted_reason"] == "disputed"
