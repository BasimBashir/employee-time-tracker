import pytest

from service.db import connect, migrate
from service.password import PasswordGate


def make_gate(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return PasswordGate(conn)


def test_unset_gate_reports_not_set(tmp_path):
    assert make_gate(tmp_path).is_set() is False


def test_set_then_verify(tmp_path):
    g = make_gate(tmp_path)
    g.set("hunter2")
    assert g.is_set() is True
    assert g.verify("hunter2") is True
    assert g.verify("wrong") is False


def test_require_raises_on_bad_password(tmp_path):
    g = make_gate(tmp_path)
    g.set("hunter2")
    with pytest.raises(PermissionError):
        g.require("bad")
    g.require("hunter2")  # correct password passes


def test_stored_value_is_never_plaintext(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    PasswordGate(conn).set("hunter2")
    stored = conn.execute(
        "SELECT value FROM settings WHERE key='password_hash'").fetchone()[0]
    assert "hunter2" not in stored and stored.startswith("pbkdf2_sha256$")


def test_api_gate_locks_and_unlocks(tmp_path):
    from fastapi.testclient import TestClient
    from service.api import create_app
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    client = TestClient(create_app(conn, None))
    # No password configured: mutations stay open (first run).
    assert client.post("/employees", json={"name": "A"}).status_code == 200
    assert client.post("/auth/set", json={"password": "hunter2"}).status_code == 200
    assert client.get("/auth/status").json() == {"set": True}
    # Guarded endpoints now reject missing/wrong passwords and accept the right one.
    assert client.post("/employees", json={"name": "B"}).status_code == 401
    r = client.post("/employees", json={"name": "B"},
                    headers={"X-Tracker-Password": "hunter2"})
    assert r.status_code == 200
    # Reads are never gated.
    assert client.get("/employees").status_code == 200
    # Unlock is a pure check; changing the password needs the current one.
    assert client.post("/auth/unlock", json={"password": "nope"}).json() == {"ok": False}
    assert client.post("/auth/unlock", json={"password": "hunter2"}).json() == {"ok": True}
    assert client.post("/auth/set", json={"password": "newpass"}).status_code == 401
    assert client.post("/auth/set", json={"current": "hunter2", "password": "newpass"}).status_code == 200
