from datetime import datetime

from service.db import (connect, migrate, add_camera, add_employee, insert_event,
                        open_session, close_session, soft_delete_session,
                        force_close_open_sessions)
from reports.query import (sessions_for_date, sessions_for_employee,
                           employee_daily_totals, events_since, SessionRow)


def make_db(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    add_camera(conn, "gate")
    add_employee(conn, "Ayesha", "E1")
    add_employee(conn, "Bilal", "E2")
    return conn


def seed(conn, emp, out, inn, delete=False):
    e1 = insert_event(conn, emp, 1, "OUT", out, "clipA.mp4", 0.9)
    sid = open_session(conn, emp, e1, out, out.date().isoformat())
    e2 = insert_event(conn, emp, 1, "IN", inn, "clipB.mp4", 0.85)
    close_session(conn, sid, e2, inn)
    if delete:
        soft_delete_session(conn, sid, "owner", "test")
    return sid


def test_soft_deleted_excluded(tmp_path):
    conn = make_db(tmp_path)
    seed(conn, 1, datetime(2026, 10, 26, 13, 0), datetime(2026, 10, 26, 13, 15))
    seed(conn, 2, datetime(2026, 10, 26, 14, 0), datetime(2026, 10, 26, 14, 30), delete=True)
    rows = sessions_for_date(conn, "2026-10-26")
    assert len(rows) == 1
    assert isinstance(rows[0], SessionRow)
    assert rows[0].duration_minutes == 15.0
    assert rows[0].out_clip == "clipA.mp4" and rows[0].in_clip == "clipB.mp4"
    assert rows[0].employee_name == "Ayesha" and rows[0].purpose == "break"


def test_totals_sum_per_employee(tmp_path):
    conn = make_db(tmp_path)
    seed(conn, 1, datetime(2026, 10, 26, 13, 0), datetime(2026, 10, 26, 13, 15))
    seed(conn, 1, datetime(2026, 10, 26, 15, 0), datetime(2026, 10, 26, 15, 45))
    seed(conn, 2, datetime(2026, 10, 26, 13, 10), datetime(2026, 10, 26, 13, 20))
    totals = {t["employee_id"]: t for t in employee_daily_totals(conn, "2026-10-26")}
    assert totals[1]["minutes"] == 60.0 and totals[2]["minutes"] == 10.0
    assert totals[1]["trips"] == 2


def test_inferred_return_still_reported(tmp_path):
    conn = make_db(tmp_path)
    ev = insert_event(conn, 1, 1, "OUT", datetime(2026, 10, 26, 22, 0))
    open_session(conn, 1, ev, datetime(2026, 10, 26, 22, 0), "2026-10-26")
    force_close_open_sessions(conn, "2026-10-26")
    rows = sessions_for_date(conn, "2026-10-26")
    assert len(rows) == 1 and rows[0].inferred_return is True and rows[0].in_at


def test_employee_range_and_events_since(tmp_path):
    conn = make_db(tmp_path)
    seed(conn, 1, datetime(2026, 10, 26, 13, 0), datetime(2026, 10, 26, 13, 15))
    seed(conn, 2, datetime(2026, 10, 27, 9, 0), datetime(2026, 10, 27, 9, 5))
    assert len(sessions_for_employee(conn, 1, "2026-10-01", "2026-10-31")) == 1
    assert len(sessions_for_employee(conn, 2, "2026-10-01", "2026-10-26")) == 0
    evs = events_since(conn, "2026-10-27T00:00:00")
    assert len(evs) == 2 and evs[0]["employee_name"] == "Bilal"
