import json
from datetime import datetime
from fastapi.testclient import TestClient

from service.db import (connect, migrate, add_employee, add_camera, insert_event,
                        open_session, close_session, set_session_purpose,
                        update_employee, daily_report, weekly_report)
from service.api import create_app


def make_client(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return TestClient(create_app(conn, None))


def _trip(conn, emp, cam, out_at, in_at, date):
    out = insert_event(conn, emp, cam, "OUT", out_at)
    sid = open_session(conn, emp, out, out_at, date)
    if in_at is not None:
        back = insert_event(conn, emp, cam, "IN", in_at)
        close_session(conn, sid, back, in_at)
    return sid


def test_migration_adds_columns_to_phase1_db(tmp_path):
    """A database created before role/purpose existed migrates in place."""
    conn = connect(tmp_path / "t.db")
    conn.executescript(
        """
        CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
          employee_code TEXT, department TEXT, active INTEGER DEFAULT 1,
          created_at TEXT, deleted_at TEXT);
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, employee_id INTEGER,
          out_event_id INTEGER, in_event_id INTEGER, out_at TEXT, in_at TEXT,
          duration_minutes REAL, inferred_return INTEGER DEFAULT 0, date TEXT,
          deleted_at TEXT, deleted_reason TEXT);
        """
    )
    migrate(conn)
    emp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(employees)")}
    ses_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert {"role", "allowed_out_minutes"} <= emp_cols
    assert "purpose" in ses_cols


def test_open_session_purpose_defaults_from_role(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    cam = add_camera(conn, "gate")
    staff = add_employee(conn, "Ayesha", "E1")
    rider = add_employee(conn, "Bilal", "E2", role="delivery")
    out1 = insert_event(conn, staff, cam, "OUT", datetime(2026, 10, 27, 9, 0))
    s1 = open_session(conn, staff, out1, datetime(2026, 10, 27, 9, 0), "2026-10-27")
    out2 = insert_event(conn, rider, cam, "OUT", datetime(2026, 10, 27, 9, 5))
    s2 = open_session(conn, rider, out2, datetime(2026, 10, 27, 9, 5), "2026-10-27")
    purposes = {r["id"]: r["purpose"]
                for r in conn.execute("SELECT id, purpose FROM sessions")}
    assert purposes[s1] == "break"
    assert purposes[s2] == "delivery"


def test_purpose_retag_audits(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    emp = add_employee(conn, "Ayesha", "E1")
    cam = add_camera(conn, "gate")
    sid = _trip(conn, emp, cam, datetime(2026, 10, 27, 9, 0),
                datetime(2026, 10, 27, 9, 30), "2026-10-27")
    set_session_purpose(conn, sid, "delivery", "owner")
    assert conn.execute("SELECT purpose FROM sessions WHERE id=?",
                        (sid,)).fetchone()[0] == "delivery"
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE"
                        " action='set_session_purpose'").fetchone()[0] == 1


def test_daily_report_delivery_exempt_and_extra(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    emp = add_employee(conn, "Ayesha", "E1", allowed_out_minutes=60)
    cam = add_camera(conn, "gate")
    _trip(conn, emp, cam, datetime(2026, 10, 27, 9, 0),
          datetime(2026, 10, 27, 9, 30), "2026-10-27")   # 30 min break
    s2 = _trip(conn, emp, cam, datetime(2026, 10, 27, 10, 0),
               datetime(2026, 10, 27, 11, 0), "2026-10-27")  # 60 min delivery
    set_session_purpose(conn, s2, "delivery", "owner")
    row = daily_report(conn, "2026-10-27", default_allowed_minutes=15)[0]
    assert row["break_minutes"] == 30.0 and row["delivery_minutes"] == 60.0
    assert row["allowed_minutes"] == 60 and row["extra_minutes"] == 0.0

    # Override the allowance down: only break minutes count toward extra.
    update_employee(conn, emp, allowed_out_minutes=20)
    row = daily_report(conn, "2026-10-27")[0]
    assert row["allowed_minutes"] == 20 and row["extra_minutes"] == 10.0


def test_daily_report_inherits_default_and_zero_fills(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    add_employee(conn, "Ayesha", "E1")
    row = daily_report(conn, "2026-10-27", default_allowed_minutes=45)[0]
    assert row["allowed_minutes"] == 45
    assert row["break_minutes"] == 0.0 and row["trips"] == 0
    assert row["extra_minutes"] == 0.0


def test_weekly_report_zero_fills_days(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    emp = add_employee(conn, "Ayesha", "E1")
    cam = add_camera(conn, "gate")
    _trip(conn, emp, cam, datetime(2026, 10, 27, 9, 0),
          datetime(2026, 10, 27, 9, 15), "2026-10-27")
    rep = weekly_report(conn, "2026-10-27", days=7)
    assert len(rep[0]["dates"]) == 7 and rep[0]["dates"][-1] == "2026-10-27"
    assert rep[0]["series"][-1]["break_minutes"] == 15.0
    assert rep[0]["series"][0] == {"break_minutes": 0.0, "delivery_minutes": 0.0}


def test_api_patch_employee_and_session(tmp_path):
    c = make_client(tmp_path)
    conn = c.app.state.conn
    emp = add_employee(conn, "Ayesha", "E1")
    cam = add_camera(conn, "gate")
    sid = _trip(conn, emp, cam, datetime(2026, 10, 27, 9, 0),
                datetime(2026, 10, 27, 9, 30), "2026-10-27")

    r = c.request("PATCH", f"/employees/{emp}",
                  content=json.dumps({"role": "delivery", "allowed_out_minutes": 45}),
                  headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json() == {
        "id": emp, "name": "Ayesha", "role": "delivery", "allowed_out_minutes": 45,
        "phone": None}

    # Clearing the override inherits the config default.
    r = c.request("PATCH", f"/employees/{emp}",
                  content=json.dumps({"allowed_out_minutes": None}),
                  headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["allowed_out_minutes"] is None

    r = c.request("PATCH", f"/sessions/{sid}",
                  content=json.dumps({"purpose": "delivery", "actor": "owner"}),
                  headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["purpose"] == "delivery"

    assert c.request("PATCH", f"/sessions/{sid}",
                     content=json.dumps({"purpose": "lunch"}),
                     headers={"Content-Type": "application/json"}).status_code == 400
    assert c.request("PATCH", "/employees/999",
                     content=json.dumps({"role": "delivery"}),
                     headers={"Content-Type": "application/json"}).status_code == 404


def test_api_report_endpoints(tmp_path):
    c = make_client(tmp_path)
    conn = c.app.state.conn
    emp = add_employee(conn, "Ayesha", "E1", allowed_out_minutes=30)
    cam = add_camera(conn, "gate")
    _trip(conn, emp, cam, datetime(2026, 10, 27, 9, 0),
          datetime(2026, 10, 27, 9, 40), "2026-10-27")  # 40 min break → 10 over

    daily = c.get("/report/daily", params={"date": "2026-10-27"}).json()
    assert daily[0]["extra_minutes"] == 10.0

    weekly = c.get("/report/weekly", params={"end": "2026-10-27"}).json()
    assert weekly[0]["employee_id"] == emp and len(weekly[0]["series"]) == 7

    csv_resp = c.get("/report/daily.csv", params={"date": "2026-10-27"})
    assert "text/csv" in csv_resp.headers["content-type"]
    lines = csv_resp.text.strip().splitlines()
    assert lines[0].startswith("employee_id,name")
    assert "Ayesha" in lines[1] and ",10.0" in lines[1]
