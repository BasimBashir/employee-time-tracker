from datetime import datetime

from service.db import connect, migrate, add_camera, add_employee, insert_event, \
    open_session, close_session
from reports.monthly import write_monthly_pdf


def make_db(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    add_camera(conn, "gate")
    add_employee(conn, "Ayesha", "E1")
    return conn


def seed(conn, emp, out, inn):
    e1 = insert_event(conn, emp, 1, "OUT", out)
    sid = open_session(conn, emp, e1, out, out.date().isoformat())
    e2 = insert_event(conn, emp, 1, "IN", inn)
    close_session(conn, sid, e2, inn)


def test_pdf_created_and_readable(tmp_path):
    conn = make_db(tmp_path)
    seed(conn, 1, datetime(2026, 10, 26, 13, 0), datetime(2026, 10, 26, 13, 15))
    p = write_monthly_pdf(conn, "2026-10", tmp_path)
    assert p.exists() and p.stat().st_size > 1000
    from pypdf import PdfReader
    text = "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)
    assert "Ayesha" in text and "15" in text


def test_month_with_no_data_still_produces_pdf(tmp_path):
    conn = make_db(tmp_path)
    p = write_monthly_pdf(conn, "2027-01", tmp_path)
    assert p.exists()
