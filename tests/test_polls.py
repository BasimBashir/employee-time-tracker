"""Prayer polls: reply parsing, ask-once dispatch, inbound replies landing on
records, the weekly summary text, and the operator-settings endpoint."""
import sqlite3
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from service import notifier, polls
from service.api import create_app
from service.db import (add_camera, add_employee, connect, insert_event,
                        migrate, open_session, set_namaz_override,
                        update_employee)

# A port with nothing listening, so "bridge down" paths are deterministic.
DEAD_URL = "http://127.0.0.1:59999"


def make_conn(tmp_path) -> sqlite3.Connection:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    add_camera(conn, "gate")
    return conn


# ------------------------------------------------------------ parsing -----
def test_parse_reply_yes_no_reason():
    assert polls.parse_reply("Yes") == ("yes", None)
    assert polls.parse_reply("no - doctor visit") == ("no", "doctor visit")
    assert polls.parse_reply("NO reason: shop delivery") == ("no", "shop delivery")
    assert polls.parse_reply("yes") == ("yes", None)
    assert polls.parse_reply("nope") is None        # not an answer
    assert polls.parse_reply("nothing") is None
    assert polls.parse_reply("nominated") is None   # 'no' + letters
    assert polls.parse_reply("") is None


# --------------------------------------------------------- ask-once -----
def test_send_prayer_polls_asks_once_per_day(tmp_path, monkeypatch):
    conn = make_conn(tmp_path)
    emp = add_employee(conn, "Basim")
    update_employee(conn, emp, phone="+92 301 7144499", namaz_prayers="zuhr")
    set_namaz_override(conn, "2026-10-29", "zuhr", 13 * 60)  # 13:00
    captured = []
    monkeypatch.setattr(notifier, "send_sync",
                        lambda cfg, phone, text: captured.append((phone, text)))
    cfg = SimpleNamespace(notify_enabled=True, notify_bridge_url=DEAD_URL,
                          notify_bridge_key="")

    now = datetime(2026, 10, 29, 13, 5)
    assert polls.send_prayer_polls(conn, cfg, now) == 1
    assert len(captured) == 1
    phone, text = captured[0]
    assert phone == "+92 301 7144499" and "Zuhr" in text and "13:00" in text
    # before the slot: nothing; same tick again: the UNIQUE row blocks re-ask
    assert polls.send_prayer_polls(conn, cfg, datetime(2026, 10, 29, 12, 0)) == 0
    assert polls.send_prayer_polls(conn, cfg, now) == 0
    assert len(captured) == 1
    # a failed send un-marks the ask, so the next tick retries it
    def boom(cfg, phone, text):
        raise OSError("bridge down")
    monkeypatch.setattr(notifier, "send_sync", boom)
    set_namaz_override(conn, "2026-10-29", "asr", 13 * 60)
    update_employee(conn, emp, namaz_prayers="zuhr,asr")
    assert polls.send_prayer_polls(conn, cfg, now) == 0
    monkeypatch.setattr(notifier, "send_sync",
                        lambda cfg, phone, text: captured.append((phone, text)))
    assert polls.send_prayer_polls(conn, cfg, now) == 1  # asr, retried
    assert len(captured) == 2


# ----------------------------------------------------------- inbound -----
def test_record_poll_reply_attaches_reason_to_open_session(tmp_path):
    conn = make_conn(tmp_path)
    emp = add_employee(conn, "Basim")
    update_employee(conn, emp, phone="+923017144499")
    out_at = datetime.now().replace(hour=13, minute=2, second=0, microsecond=0)
    ev = insert_event(conn, emp, 1, "OUT", out_at)
    open_session(conn, emp, ev, out_at, out_at.date().isoformat())
    polls.handle_inbound(conn, None, "923017144499@s.whatsapp.net",
                         "no reason: clinic visit")
    row = conn.execute("SELECT out_reason FROM sessions WHERE id=1").fetchone()
    assert row["out_reason"] == "clinic visit"
    poll = conn.execute("SELECT reply, reason FROM poll_replies").fetchone()
    assert poll["reply"] == "no" and poll["reason"] == "clinic visit"


def test_handle_inbound_matching_and_junk(tmp_path):
    conn = make_conn(tmp_path)
    emp = add_employee(conn, "Basim")
    update_employee(conn, emp, phone="+923017144499")
    cfg = SimpleNamespace(notify_enabled=True, notify_bridge_url=DEAD_URL,
                          notify_bridge_key="")
    # right number, junk text → not accepted
    r = polls.handle_inbound(conn, cfg, "923017144499@s.whatsapp.net", "salam")
    assert r == {"matched": True, "accepted": False}
    # unknown number, valid text → not matched
    r = polls.handle_inbound(conn, cfg, "923000000000@s.whatsapp.net", "yes")
    assert r == {"matched": False, "accepted": False}
    # right number, valid text → stored as a standalone row (no poll asked)
    r = polls.handle_inbound(conn, cfg, "923017144499@s.whatsapp.net", "yes")
    assert r["accepted"] is True and r["reply"] == "yes"


# ------------------------------------------------------------ weekly -----
def test_weekly_summary_text_and_monday_gate(tmp_path, monkeypatch):
    conn = make_conn(tmp_path)
    assert "150m" in polls.weekly_summary_text("Basim", 150, 0, 60, 0)
    assert "over budget" in polls.weekly_summary_text("Basim", 150, 0, 60, 30)
    assert "within budget" in polls.weekly_summary_text("Basim", 40, 0, 60, 0)

    captured = []
    monkeypatch.setattr(notifier, "send",
                        lambda cfg, phone, text: captured.append((phone, text)))
    emp = add_employee(conn, "Basim")
    update_employee(conn, emp, phone="+923017144499")
    cfg = SimpleNamespace(notify_enabled=True, notify_bridge_url=DEAD_URL,
                          notify_bridge_key="")
    monday = datetime(2026, 10, 26, 15, 0)  # a Monday 15:00
    assert polls.maybe_weekly_report(conn, cfg, None, monday) == "2026-10-26"
    assert len(captured) == 1 and "Basim" in captured[0][1]
    # same day re-entry (restart or loop tick): the marker blocks a resend
    assert polls.maybe_weekly_report(conn, cfg, None, monday) == "2026-10-26"
    assert len(captured) == 1
    # not Monday 15:00 → nothing
    assert polls.maybe_weekly_report(conn, cfg, None,
                                     datetime(2026, 10, 27, 10, 0)) is None
    assert len(captured) == 1


# --------------------------------------------------------- endpoints -----
def test_patch_notify_settings_endpoint(tmp_path):
    conn = make_conn(tmp_path)
    saved = []
    cfg = SimpleNamespace(notify_enabled=False, notify_owner_phone="",
                          notify_bridge_url=DEAD_URL, notify_bridge_key="",
                          _extra={}, save=lambda: saved.append(1))
    c = TestClient(create_app(conn, cfg))
    r = c.patch("/notify/settings", json={
        "notify_enabled": True,
        "notify_owner_phone": "+92 301 7144499",
        "dev_source": "rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/101"})
    assert r.status_code == 200 and r.json()["restart_recommended"] is True
    assert cfg.notify_enabled is True
    assert cfg.notify_owner_phone == "+92 301 7144499"
    assert cfg._extra["dev_source"].startswith("rtsp://")
    assert saved == [1]
    # validation
    assert c.patch("/notify/settings",
                   json={"notify_owner_phone": "hello"}).status_code == 400
    assert c.patch("/notify/settings",
                   json={"dev_source": "http://x"}).status_code == 400
    # status echoes the settings back for the panel
    st = c.get("/notify/status").json()
    assert st["enabled"] is True and st["owner_phone"] == "+92 301 7144499"
    assert st["dev_source"].startswith("rtsp://")


def test_camera_test_rejects_non_rtsp(tmp_path):
    conn = make_conn(tmp_path)
    c = TestClient(create_app(conn, None))
    assert c.post("/camera/test", json={"url": "http://x"}).status_code == 400
