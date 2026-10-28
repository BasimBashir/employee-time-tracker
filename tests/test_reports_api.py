from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from service.db import connect, migrate, add_camera, add_employee, insert_event, \
    open_session, close_session
from service.api import create_app
from service.config import Config


def make_client(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    add_camera(conn, "gate")
    add_employee(conn, "Ayesha", "E1")
    e1 = insert_event(conn, 1, 1, "OUT", datetime(2026, 10, 26, 13, 0))
    sid = open_session(conn, 1, e1, datetime(2026, 10, 26, 13, 0), "2026-10-26")
    e2 = insert_event(conn, 1, 1, "IN", datetime(2026, 10, 26, 13, 15))
    close_session(conn, sid, e2, datetime(2026, 10, 26, 13, 15))
    cfg = Config()
    cfg.data_root = tmp_path / "d"
    cfg.ensure_dirs()
    return TestClient(create_app(conn, cfg))


def test_daily_report_generation(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/reports/daily", json={"date": "2026-10-26",
                                            "formats": ["csv", "xlsx"]})
    assert r.status_code == 200
    for p in r.json()["files"]:
        assert Path(p).exists()


def test_monthly_report_generation(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/reports/monthly", json={"year_month": "2026-10"})
    assert r.status_code == 200 and Path(r.json()["file"]).exists()


def test_report_downloadable(tmp_path):
    client = make_client(tmp_path)
    client.post("/reports/daily", json={"date": "2026-10-26", "formats": ["csv"]})
    r = client.get("/reports/attendance_2026-10-26.csv")
    assert r.status_code == 200 and "employee_id" in r.text


def test_reports_respect_password_gate(tmp_path):
    client = make_client(tmp_path)
    client.post("/auth/set", json={"password": "hunter2"})
    assert client.post("/reports/daily", json={"date": "2026-10-26"}).status_code == 401
    r = client.post("/reports/daily", json={"date": "2026-10-26", "formats": ["csv"]},
                    headers={"X-Tracker-Password": "hunter2"})
    assert r.status_code == 200
