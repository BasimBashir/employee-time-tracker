"""Employee profile management: full CRUD over the API, allowed out-time
windows, the avatar upload, and training media as plain files on disk."""
import sqlite3
from datetime import datetime

from fastapi.testclient import TestClient

from service import db as dbmod
from service.api import create_app
from service.config import Config
from service.db import (add_camera, add_employee, connect, insert_event,
                        migrate, open_session, update_employee)


def make_conn(tmp_path) -> sqlite3.Connection:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn


def make_cfg(tmp_path) -> Config:
    cfg = Config()
    cfg.data_root = tmp_path / "root"
    cfg.ensure_dirs()
    return cfg


# --------------------------------------------------------------- windows ----
def test_out_windows_parsing():
    parsed = dbmod.parse_out_windows("12:30-14:00, 16:00-16:30")
    assert dbmod.windows_text(parsed) == "12:30-14:00, 16:00-16:30"
    # canonical: sorted, zero-padded, tolerates sloppy input
    assert dbmod.windows_text(dbmod.parse_out_windows("16:0-17:00 , 9:05-10:00")) \
        == "09:05-10:00, 16:00-17:00"
    for bad in ("12:30", "25:00-26:00", "12:30-11:00", "lunch", "12:30-14:70"):
        try:
            dbmod.parse_out_windows(bad)
            raise SystemExit(f"expected ValueError for {bad!r}")
        except ValueError:
            pass
    assert dbmod.parse_out_windows("") is None
    assert dbmod.parse_out_windows(None) is None


def test_outside_window(tmp_path):
    conn = make_conn(tmp_path)
    emp = add_employee(conn, "Ali")
    update_employee(conn, emp, out_windows="12:30-14:00")
    assert not dbmod.outside_window(conn, emp, datetime(2026, 10, 27, 12, 30))
    assert not dbmod.outside_window(conn, emp, datetime(2026, 10, 27, 13, 59))
    # end is exclusive, and anything outside every window violates
    assert dbmod.outside_window(conn, emp, datetime(2026, 10, 27, 14, 0))
    assert dbmod.outside_window(conn, emp, datetime(2026, 10, 27, 9, 15))
    # no windows set -> never restricted
    emp2 = add_employee(conn, "Bilal")
    assert not dbmod.outside_window(conn, emp2, datetime(2026, 10, 27, 3, 0))


# ------------------------------------------------------------------ crud ----
def test_employee_crud_endpoints(tmp_path):
    conn = make_conn(tmp_path)
    c = TestClient(create_app(conn, None))
    # create: name required, role validated
    assert c.post("/employees", json={}).status_code == 400
    assert c.post("/employees", json={"name": "Ali", "role": "pilot"}).status_code == 400
    r = c.post("/employees", json={"name": "Ali", "employee_code": "E1",
                                   "department": "kitchen"})
    assert r.status_code == 200
    emp = r.json()["employee_id"]
    assert r.json()["name"] == "Ali" and r.json()["has_avatar"] is False
    # list shows the full profile
    listed = c.get("/employees").json()
    assert [e["employee_id"] for e in listed] == [emp]
    assert listed[0]["department"] == "kitchen"
    # edit every field the page exposes
    r = c.patch(f"/employees/{emp}", json={"name": "Ali Raza", "employee_code": "E-01",
                                           "department": "", "out_windows": "12:30-14:00",
                                           "role": "staff", "phone": "+923001234567"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ali Raza" and body["employee_code"] == "E-01"
    assert body["department"] is None  # empty string cleared it
    assert body["out_windows"] == "12:30-14:00"
    # bad window / duplicate code rejected, employee untouched
    assert c.patch(f"/employees/{emp}", json={"out_windows": "25:00-26:00"}).status_code == 400
    other = c.post("/employees", json={"name": "Bilal"}).json()
    assert c.patch(f"/employees/{other['employee_id']}",
                   json={"employee_code": "E-01"}).status_code == 400
    assert c.patch("/employees/999", json={"name": "x"}).status_code == 404
    # deactivate: vanishes from the roster and reports, history stays resolvable
    r = c.request("DELETE", f"/employees/{emp}", content='{"reason": "left"}',
                  headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert [e["employee_id"] for e in c.get("/employees").json()] == [
        other["employee_id"]]
    r = c.request("DELETE", f"/employees/{emp}", content="{}",
                  headers={"Content-Type": "application/json"})
    assert r.status_code == 404
    report = c.get("/report/daily", params={"date": "2026-10-27"}).json()
    assert all(row["employee_id"] != emp for row in report)


# ---------------------------------------------------------------- avatar ----
def test_avatar_upload_and_serve(tmp_path):
    conn = make_conn(tmp_path)
    c = TestClient(create_app(conn, make_cfg(tmp_path)))
    emp = add_employee(conn, "Ali")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = c.post(f"/employees/{emp}/avatar", content=png,
               headers={"Content-Type": "image/png"})
    assert r.status_code == 200 and r.json()["has_avatar"] is True
    r = c.get(f"/employees/{emp}/avatar")
    assert r.status_code == 200 and r.content == png
    assert r.headers["content-type"] == "image/png"
    # replacing swaps the file (no stray avatar_<id>.* left behind)
    jpeg = b"\xff\xd8\xff\xe0" + b"1" * 32
    c.post(f"/employees/{emp}/avatar", content=jpeg,
           headers={"Content-Type": "image/jpeg"})
    media = tmp_path / "root" / "media"
    assert [p.name for p in media.glob(f"avatar_{emp}.*")] == [f"avatar_{emp}.jpg"]
    # rejections: wrong type, oversize, unknown employee, none set yet
    assert c.post(f"/employees/{emp}/avatar", content=b"x",
                  headers={"Content-Type": "image/gif"}).status_code == 415
    assert c.post(f"/employees/{emp}/avatar", content=b"0" * (3 * 1024 * 1024 + 1),
                  headers={"Content-Type": "image/png"}).status_code == 413
    assert c.post("/employees/999/avatar", content=png,
                  headers={"Content-Type": "image/png"}).status_code == 404
    assert c.get(f"/employees/{emp + 50}/avatar").status_code == 404


# ----------------------------------------------------------------- media ----
def test_training_media_files_on_disk(tmp_path):
    conn = make_conn(tmp_path)
    c = TestClient(create_app(conn, make_cfg(tmp_path)))
    emp = add_employee(conn, "Ali")
    video = b"\x00\x00\x00\x18ftypmp42" + b"2" * 128
    r = c.post(f"/employees/{emp}/media", content=video,
               headers={"Content-Type": "video/mp4"})
    assert r.status_code == 200 and r.json()["kind"] == "video"
    png = b"\x89PNG\r\n\x1a\n" + b"3" * 32
    c.post(f"/employees/{emp}/media", content=png, headers={"Content-Type": "image/png"})
    # listing reads the folder, newest last; kind comes from the extension
    listed = c.get(f"/employees/{emp}/media").json()
    assert [f["kind"] for f in listed] == ["video", "image"]
    names = [f["file"] for f in listed]
    # serving round-trips the exact bytes; junk names and traversal 404
    assert c.get(f"/employees/{emp}/media/{names[0]}").content == video
    assert c.get(f"/employees/{emp}/media/{names[0]}").headers["content-type"] == "video/mp4"
    assert c.get(f"/employees/{emp}/media/..%5Cavatar_1.jpg").status_code in (404, 400)
    assert c.get(f"/employees/{emp}/media/nope.jpg").status_code == 404
    # delete removes the file, and it disappears from the listing
    assert c.delete(f"/employees/{emp}/media/{names[0]}").status_code == 200
    assert [f["file"] for f in c.get(f"/employees/{emp}/media").json()] == names[1:]
    media_dir = tmp_path / "root" / "media" / "employees" / str(emp)
    assert list(media_dir.glob("*.mp4")) == []


# --------------------------------------------------- sessions flagging ----
def test_sessions_carry_outside_window_flag(tmp_path):
    conn = make_conn(tmp_path)
    cam = add_camera(conn, "gate")
    emp = add_employee(conn, "Ali")
    update_employee(conn, emp, out_windows="12:30-14:00")
    day = "2026-10-27"
    e1 = insert_event(conn, emp, cam, "OUT", datetime(2026, 10, 27, 13, 0))
    open_session(conn, emp, e1, datetime(2026, 10, 27, 13, 0), day)
    e2 = insert_event(conn, emp, cam, "OUT", datetime(2026, 10, 27, 9, 0))
    open_session(conn, emp, e2, datetime(2026, 10, 27, 9, 0), day)
    rows = TestClient(create_app(conn, None)).get("/sessions", params={"date": day}).json()
    flags = {r["out_at"][:16]: r["outside_window"] for r in rows}
    assert flags == {"2026-10-27T13:00": False, "2026-10-27T09:00": True}
