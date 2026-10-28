import numpy as np
from fastapi.testclient import TestClient
from service.db import connect, migrate, add_employee
from service.api import create_app
from service.roster import save_enrollment

def test_resolver_status_and_invalidate(tmp_path):
    from service import config as cfgmod
    cfg = cfgmod.Config(); cfg.data_root = tmp_path / "d"; cfg.port = 8098
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, np.ones(512, np.float32).tobytes(), None, 0.9, 1)
    client = TestClient(create_app(conn, cfg))
    r = client.get("/resolver/status")
    assert r.json()["roster_size"] >= 1
    client.post("/resolver/invalidate")
    assert client.get("/resolver/status").json()["roster_size"] >= 1
