import numpy as np
from fastapi.testclient import TestClient
from service.db import connect, migrate, add_employee
from service.api import create_app
from service.roster import save_enrollment


def make_client(tmp_path):
    from service import config as cfgmod
    cfg = cfgmod.Config(); cfg.data_root = tmp_path / "d"; cfg.port = 8095
    conn = connect(tmp_path / "t.db"); migrate(conn)
    return TestClient(create_app(conn, cfg)), conn


def test_deactivate_invalidates_resolver(tmp_path):
    from service import config as cfgmod
    cfg = cfgmod.Config(); cfg.data_root = tmp_path / "d"; cfg.port = 8095
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, np.ones(512, np.float32).tobytes(), None, 0.9, 1)
    client = TestClient(create_app(conn, cfg))
    assert client.get("/resolver/status").json()["roster_size"] == 1
    # the DELETE handler must rebuild the matrix, not just flip the row
    r = client.request("DELETE", f"/employees/{eid}",
                       content='{"reason": "left"}',
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert client.get("/resolver/status").json()["roster_size"] == 0


def test_enrollment_endpoint_validates_input(tmp_path):
    client, conn = make_client(tmp_path)
    eid = add_employee(conn, "Ayesha", "E1")
    # missing path
    assert client.post("/enrollments", json={"employee_id": eid}).status_code == 400
    # unopenable file
    r = client.post("/enrollments", json={"employee_id": eid, "path": "nope.mp4"})
    assert r.status_code == 400
    # unknown employee
    r = client.post("/enrollments", json={"employee_id": 999, "path": "nope.mp4"})
    assert r.status_code == 404
    assert client.get("/resolver/status").json()["roster_size"] == 0
