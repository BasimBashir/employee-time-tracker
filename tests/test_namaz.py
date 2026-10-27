"""The namaz layer: Faisalabad sun-time computation, the editable timetable
(fixed Zuhr clock time, jamaat offsets, per-date overrides), per-employee
namaz plans with stagger, and how the resulting slots drive the off-window
flag."""
import sqlite3
from datetime import datetime

from fastapi.testclient import TestClient

from service import db as dbmod
from service import namaz
from service.api import create_app
from service.db import (add_camera, add_employee, connect, insert_event,
                        migrate, open_session, update_employee)


def make_conn(tmp_path) -> sqlite3.Connection:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn


# ----------------------------------------------------------- computation ----
def test_faisalabad_day_times():
    # praytimes.net, Faisalabad 27 Oct 2026: fajr 04:58, midday 12:00,
    # hanafi asr 15:54, maghrib 17:30, isha 18:49 (their tables add a few
    # minutes of temkin, hence the small tolerances).
    t = namaz.day_times("2026-10-27")
    assert t["fajr"] == 4 * 60 + 58
    assert abs(t["zuhr"] - 12 * 60) <= 8      # awwal midday; jamaat is fixed
    assert abs(t["asr"] - (15 * 60 + 54)) <= 5
    assert abs(t["maghrib"] - (17 * 60 + 30)) <= 4
    assert abs(t["isha"] - (18 * 60 + 49)) <= 5
    # seasonal extremes stay physical, and the day order never inverts
    jun = namaz.day_times("2026-06-21")
    dec = namaz.day_times("2026-12-21")
    assert 19 * 60 + 10 <= jun["maghrib"] <= 19 * 60 + 25
    assert 17 * 60 + 5 <= dec["maghrib"] <= 17 * 60 + 20
    for day in (jun, dec):
        assert day["fajr"] < day["zuhr"] < day["asr"] < day["maghrib"] < day["isha"]


# -------------------------------------------------------------- timetable ----
def test_timetable_defaults_and_editing(tmp_path):
    conn = make_conn(tmp_path)
    st = dbmod.get_namaz_settings(conn)
    # Faisalabad practice: Zuhr jamaat at a fixed 13:30 all year, on any weekday
    assert st["zuhr_fixed_min"] == 13 * 60 + 30
    assert st["jummah_min"] == 13 * 60 + 30
    assert st["duration_min"] == 20
    assert st["offsets"] == {p: 0 for p in namaz.PRAYERS}
    for ds in ("2026-10-27", "2026-10-30"):  # Tuesday, Friday
        assert dbmod.namaz_start(conn, ds, "zuhr", st) == 13 * 60 + 30
    # the sun prayers start from the computed time with no offset
    assert dbmod.namaz_start(conn, "2026-10-27", "maghrib", st) == \
        namaz.day_times("2026-10-27")["maghrib"]
    # a jamaat offset shifts the prayer every day
    dbmod.set_namaz_settings(conn, offsets={"asr": 30})
    st = dbmod.get_namaz_settings(conn)
    assert dbmod.namaz_start(conn, "2026-10-27", "asr", st) == \
        namaz.day_times("2026-10-27")["asr"] + 30
    # a per-date override wins over everything; clearing it reverts (to jummah)
    dbmod.set_namaz_override(conn, "2026-10-30", "zuhr", 13 * 60 + 45)
    assert dbmod.namaz_start(conn, "2026-10-30", "zuhr", st) == 13 * 60 + 45
    dbmod.set_namaz_override(conn, "2026-10-30", "zuhr", None)
    assert dbmod.namaz_start(conn, "2026-10-30", "zuhr", st) == 13 * 60 + 30
    # validation: unknown prayer, out-of-range time, junk date
    for date_s, prayer, minutes in (("2026-10-30", "brunch", 800),
                                    ("2026-10-30", "zuhr", 1500),
                                    ("not-a-date", "zuhr", 800)):
        try:
            dbmod.set_namaz_override(conn, date_s, prayer, minutes)
            raise SystemExit(f"expected ValueError for {prayer} {minutes}")
        except ValueError:
            pass
    # clearing the fixed Zuhr falls back to the computed midday + offset
    dbmod.set_namaz_settings(conn, zuhr_fixed_min=None)
    st = dbmod.get_namaz_settings(conn)
    assert st["zuhr_fixed_min"] is None
    assert dbmod.namaz_start(conn, "2026-10-27", "zuhr", st) == \
        namaz.day_times("2026-10-27")["zuhr"]


# ------------------------------------------------------- employee windows ----
def test_employee_namaz_windows(tmp_path):
    conn = make_conn(tmp_path)
    emp = add_employee(conn, "Ali")
    update_employee(conn, emp, namaz_prayers="zuhr, asr", namaz_offset_min=5)
    row = conn.execute("SELECT namaz_prayers FROM employees WHERE id=?",
                       (emp,)).fetchone()
    assert row["namaz_prayers"] == "zuhr,asr"  # canonical order
    st = dbmod.get_namaz_settings(conn)
    spans = dbmod.effective_spans(conn, None, "2026-10-27", "zuhr,asr", 5, None, st)
    # Zuhr slot opens 13:30 + their 5-minute stagger, 20-minute default duration
    assert spans[0] == (13 * 60 + 35, 13 * 60 + 55)
    asr = namaz.day_times("2026-10-27")["asr"]
    assert spans[1] == (asr + 5, asr + 25)
    # static profile windows merge alongside the namaz slots
    update_employee(conn, emp, out_windows="12:30-12:40")
    js = conn.execute("SELECT out_windows FROM employees WHERE id=?",
                      (emp,)).fetchone()["out_windows"]
    spans = dbmod.effective_spans(conn, js, "2026-10-27", "zuhr,asr", 5, None, st)
    assert spans[0] == (12 * 60 + 30, 12 * 60 + 40)
    assert dbmod.spans_text(spans).startswith("12:30-12:40, 13:35-13:55")
    # trips inside the zuhr slot pass; anything else violates
    assert not dbmod.outside_window(conn, emp, datetime(2026, 10, 27, 13, 40))
    assert not dbmod.outside_window(conn, emp, datetime(2026, 10, 27, 13, 35))
    assert dbmod.outside_window(conn, emp, datetime(2026, 10, 27, 14, 0))
    assert dbmod.outside_window(conn, emp, datetime(2026, 10, 27, 9, 15))
    # an employee with no plan and no windows is never restricted
    other = add_employee(conn, "Bilal")
    assert not dbmod.outside_window(conn, other, datetime(2026, 10, 27, 3, 0))


# -------------------------------------------------------------------- api ----
def test_namaz_api(tmp_path):
    conn = make_conn(tmp_path)
    c = TestClient(create_app(conn, None))
    body = c.get("/namaz", params={"year": 2026, "month": 10}).json()
    assert body["year"] == 2026 and len(body["days"]) == 31
    assert body["settings"]["duration_min"] == 20
    tuesday = next(d for d in body["days"] if d["date"] == "2026-10-27")
    assert tuesday["zuhr"] == "13:30" and tuesday["jummah"] is False
    friday = next(d for d in body["days"] if d["date"] == "2026-10-30")
    assert friday["jummah"] is True and friday["zuhr"] == "13:30"
    # edit one cell, then clear it; both land
    assert c.patch("/namaz", json={"date": "2026-10-27", "prayer": "maghrib",
                                   "time": "17:45"}).status_code == 200
    body = c.get("/namaz", params={"year": 2026, "month": 10}).json()
    day = next(d for d in body["days"] if d["date"] == "2026-10-27")
    assert day["maghrib"] == "17:45" and day["overridden"] == ["maghrib"]
    assert c.patch("/namaz", json={"date": "2026-10-27", "prayer": "maghrib",
                                   "time": None}).status_code == 200
    # junk is rejected: prayer name, date, time format, settings ranges
    assert c.patch("/namaz", json={"date": "2026-10-27", "prayer": "tea",
                                   "time": "17:00"}).status_code == 400
    assert c.patch("/namaz", json={"date": "2026-13-40", "prayer": "zuhr",
                                   "time": "17:00"}).status_code == 400
    assert c.patch("/namaz", json={"date": "2026-10-27", "prayer": "zuhr",
                                   "time": "25:99"}).status_code == 400
    r = c.patch("/namaz/settings", json={"offsets": {"asr": 25}, "duration_min": 30})
    assert r.status_code == 200 and r.json()["duration_min"] == 30
    assert c.patch("/namaz/settings", json={"offsets": {"asr": 500}}).status_code == 400
    assert c.patch("/namaz/settings", json={"offsets": {"brunch": 5}}).status_code == 400
    assert c.patch("/namaz/settings", json={"jummah": "not-a-time"}).status_code == 400

    # employee namaz plan over the API, and the roster carries effective windows
    emp = add_employee(conn, "Ali")
    r = c.patch(f"/employees/{emp}", json={"namaz_prayers": "zuhr,asr",
                                           "namaz_offset_min": 10})
    assert r.status_code == 200 and r.json()["namaz_prayers"] == "zuhr,asr"
    assert c.patch(f"/employees/{emp}", json={"namaz_prayers": "iftar"}).status_code == 400
    assert c.patch(f"/employees/{emp}", json={"namaz_offset_min": 999}).status_code == 400
    assert c.patch(f"/employees/{emp}", json={"namaz_duration_min": "soon"}).status_code == 400
    listed = c.get("/employees", params={"date": "2026-10-27"}).json()[0]
    # 30-minute slots (set above): zuhr opens 13:30 + their 10-minute stagger,
    # asr opens at the computed asr + the 25-minute jamaat offset + stagger
    assert listed["effective_windows"] == "13:40-14:10, 16:26-16:56"
    assert listed["namaz_offset_min"] == 10

    # the sessions flag now follows the namaz slots
    cam = add_camera(conn, "gate")
    day = "2026-10-27"
    for h, m in ((13, 45), (15, 0)):
        e = insert_event(conn, emp, cam, "OUT", datetime(2026, 10, 27, h, m))
        open_session(conn, emp, e, datetime(2026, 10, 27, h, m), day)
    rows = c.get("/sessions", params={"date": day}).json()
    flags = {r["out_at"][:16]: r["outside_window"] for r in rows}
    assert flags == {"2026-10-27T13:45": False, "2026-10-27T15:00": True}
    # and the row shape is unchanged otherwise (no rule columns leak)
    assert "namaz_prayers" not in rows[0]
