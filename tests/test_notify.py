"""WhatsApp notification plumbing: message text, bridge dispatch, phone
storage, the /notify/status endpoint, and the simulator's notification hook."""
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from service import notifier
from service.api import create_app
from service.db import (add_camera, add_employee, connect, insert_event,
                        migrate, open_session, update_employee)
from tools.record_simulator import simulate

# A port with nothing listening, so "bridge down" paths are deterministic.
DEAD_URL = "http://127.0.0.1:59999"
CFG = SimpleNamespace(notify_enabled=True, notify_bridge_url=DEAD_URL,
                      notify_bridge_key="k3y")


def make_conn(tmp_path) -> sqlite3.Connection:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn


# ------------------------------------------------------------- messages -----
def test_out_and_back_message_text():
    out = notifier.out_message("Ali", datetime(2026, 10, 27, 14, 32), "break")
    assert "Ali" in out and "14:32" in out and "break" in out
    back = notifier.back_message("Ali", datetime(2026, 10, 27, 14, 47), 15, "break")
    assert "14:47" in back and "15m" in back
    delivery = notifier.out_message("Bilal", datetime(2026, 10, 27, 9, 0), "delivery")
    assert "delivery trip" in delivery
    inferred = notifier.back_message("Ali", datetime(2026, 10, 27, 23, 59), 725,
                                     "break", inferred=True)
    assert "auto-closed" in inferred and "12h 5m" in inferred


def test_send_sync_posts_to_bridge(tmp_path, monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return FakeResponse()

    monkeypatch.setattr(notifier.urllib.request, "urlopen", fake_urlopen)
    notifier.send_sync(CFG, "+92 300 1234567", "hello")
    assert captured["url"] == f"{DEAD_URL}/send"
    lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert lower.get("x-bridge-key") == "k3y"
    assert json.loads(captured["body"]) == {"to": "+92 300 1234567", "text": "hello"}


def test_send_skips_when_disabled_or_numberless(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not dispatch")
    monkeypatch.setattr(notifier, "_deliver", boom)
    off = SimpleNamespace(notify_enabled=False, notify_bridge_url=DEAD_URL,
                          notify_bridge_key="")
    notifier.send(off, "+923001234567", "x")
    notifier.send(CFG, None, "x")
    notifier.send(CFG, "", "x")


# ---------------------------------------------------------------- phones ----
def test_update_employee_phone_set_clear_invalid(tmp_path):
    conn = make_conn(tmp_path)
    emp = add_employee(conn, "Ali")
    update_employee(conn, emp, phone="+92 300-1234567")
    assert conn.execute("SELECT phone FROM employees WHERE id=?",
                        (emp,)).fetchone()["phone"] == "+92 300-1234567"
    update_employee(conn, emp, phone=None)
    assert conn.execute("SELECT phone FROM employees WHERE id=?",
                        (emp,)).fetchone()["phone"] is None
    try:
        update_employee(conn, emp, phone="hello")
        raise SystemExit("expected ValueError")
    except ValueError:
        pass


# ------------------------------------------------------------ endpoints -----
def test_patch_employee_phone_endpoint(tmp_path):
    conn = make_conn(tmp_path)
    cam = add_camera(conn, "gate")
    emp = add_employee(conn, "Ali")
    c = TestClient(create_app(conn, None))
    r = c.patch(f"/employees/{emp}", json={"phone": "+923001234567"})
    assert r.status_code == 200 and r.json()["phone"] == "+923001234567"
    r = c.patch(f"/employees/{emp}", json={"phone": "not-a-phone"})
    assert r.status_code == 400
    r = c.patch("/employees/999", json={"phone": "+923001234567"})
    assert r.status_code == 404
    # clearing works over the API too
    assert c.patch(f"/employees/{emp}", json={"phone": None}).json()["phone"] is None


def test_notify_status_endpoint(tmp_path):
    conn = make_conn(tmp_path)
    # cfg=None (and any cfg with notify_enabled off) reports plainly disabled.
    c = TestClient(create_app(conn, None))
    assert c.get("/notify/status").json() == {"enabled": False}
    off = SimpleNamespace(notify_enabled=False, notify_bridge_url=DEAD_URL,
                          notify_bridge_key="")
    c = TestClient(create_app(conn, off))
    assert c.get("/notify/status").json() == {"enabled": False}
    # enabled but bridge unreachable: informational, still a 200.
    c = TestClient(create_app(conn, CFG))
    body = c.get("/notify/status").json()
    assert body["enabled"] is True and body["reachable"] is False


def test_bridge_qr_returns_bytes_or_none(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"QRPNG"

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return FakeResponse()

    monkeypatch.setattr(notifier.urllib.request, "urlopen", fake_urlopen)
    assert notifier.bridge_qr(CFG) == b"QRPNG"
    assert seen["url"] == f"{DEAD_URL}/qr.png"

    def down(req, timeout=None):
        raise OSError("bridge unreachable")

    monkeypatch.setattr(notifier.urllib.request, "urlopen", down)
    assert notifier.bridge_qr(CFG) is None


def test_notify_qr_endpoint(tmp_path, monkeypatch):
    conn = make_conn(tmp_path)
    # Disabled (or no cfg): no QR exists to serve.
    c = TestClient(create_app(conn, None))
    assert c.get("/notify/qr").status_code == 404
    # Enabled: the endpoint proxies whatever bridge_qr fetched.
    monkeypatch.setattr(notifier, "bridge_qr", lambda cfg, timeout=3.0: b"QRPNG")
    c = TestClient(create_app(conn, CFG))
    r = c.get("/notify/qr")
    assert r.status_code == 200 and r.content == b"QRPNG"
    assert r.headers["content-type"] == "image/png"
    # Bridge down or still linking: bridge_qr comes back None → 404.
    monkeypatch.setattr(notifier, "bridge_qr", lambda cfg, timeout=3.0: None)
    assert c.get("/notify/qr").status_code == 404


# ----------------------------------------------------- off-window alerts ----
def test_window_violation_alerts_owner_only(monkeypatch):
    captured = []
    monkeypatch.setattr(notifier, "send",
                        lambda cfg, phone, text: captured.append((phone, text)))
    owner = SimpleNamespace(notify_enabled=True, notify_bridge_url=DEAD_URL,
                            notify_bridge_key="", notify_owner_phone="+923001234567")
    notifier.window_violation(owner, "Ali", datetime(2026, 10, 27, 9, 15),
                              "12:30-14:00")
    phone, text = captured[0]
    assert phone == "+923001234567"
    assert "Ali" in text and "09:15" in text and "12:30-14:00" in text
    # no owner number configured -> nothing is sent anywhere
    notifier.window_violation(CFG, "Ali", datetime(2026, 10, 27, 9, 15), "12:30-14:00")
    assert len(captured) == 1


def test_simulator_flags_window_violation(tmp_path, monkeypatch):
    conn = make_conn(tmp_path)
    add_camera(conn, "gate")
    emp = add_employee(conn, "Bilal")
    update_employee(conn, emp, out_windows="12:30-14:00")
    captured = []

    def fake_send(cfg, phone, text):
        if phone:  # same guard as the real send: no number, no dispatch
            captured.append((phone, text))

    monkeypatch.setattr(notifier, "send", fake_send)
    owner = SimpleNamespace(notify_enabled=True, notify_bridge_url=DEAD_URL,
                            notify_bridge_key="", notify_owner_phone="+923001234567")
    # 09:00 is outside the window -> the owner hears about it (the employee
    # has no number, so the only send is the alert)
    simulate(conn, 1, [(emp, datetime(2026, 10, 27, 9, 0),
                        datetime(2026, 10, 27, 9, 10))], pause_seconds=0, cfg=owner)
    assert len(captured) == 1 and "outside their allowed window" in captured[0][1]
    # 13:00 sits inside the window -> no alert
    captured.clear()
    simulate(conn, 1, [(emp, datetime(2026, 10, 27, 13, 0), None)],
             pause_seconds=0, cfg=owner)
    assert captured == []


# ------------------------------------------------------------ simulator -----
def test_simulator_fires_notifications(tmp_path, monkeypatch):
    conn = make_conn(tmp_path)
    add_camera(conn, "gate")
    emp = add_employee(conn, "Ali", role="delivery", allowed_out_minutes=None)
    captured = []
    monkeypatch.setattr(notifier, "send",
                        lambda cfg, phone, text: captured.append((phone, text)))
    out_at = datetime(2026, 10, 27, 9, 0)
    back_at = datetime(2026, 10, 27, 9, 15)
    simulate(conn, 1, [(emp, out_at, back_at)], pause_seconds=0, cfg=CFG)
    assert len(captured) == 2
    phone, out_msg = captured[0]
    assert phone is None  # no number on the record; the message is built anyway
    assert "OUT" in out_msg and "delivery trip" in out_msg
    _, back_msg = captured[1]
    assert "back" in back_msg and "15m" in back_msg
