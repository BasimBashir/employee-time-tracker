import numpy as np
from service.db import connect, migrate, add_employee, deactivate_employee
from service.roster import save_enrollment, primary_face, roster_embeddings

def test_enrollment_and_primary(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "EMP-001")
    v1 = np.random.rand(512).astype(np.float32).tobytes()
    save_enrollment(conn, eid, v1, None, 0.91, is_primary=1)
    save_enrollment(conn, eid, np.random.rand(512).astype(np.float32).tobytes(),
                    None, 0.72, is_primary=0)
    assert primary_face(conn, eid) == v1
    rows = roster_embeddings(conn)
    assert len(rows) == 2 and rows[0][0] == eid

def test_inactive_employee_excluded_from_roster(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "EMP-001")
    save_enrollment(conn, eid, np.random.rand(512).astype(np.float32).tobytes(),
                    None, 0.9, is_primary=1)
    deactivate_employee(conn, eid, "test", "left")
    assert all(r[0] != eid for r in roster_embeddings(conn))

def test_new_primary_demotes_the_old_one(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "EMP-001")
    v1 = np.random.rand(512).astype(np.float32).tobytes()
    v2 = np.random.rand(512).astype(np.float32).tobytes()
    save_enrollment(conn, eid, v1, None, 0.91, is_primary=1)
    save_enrollment(conn, eid, v2, None, 0.95, is_primary=1)
    assert primary_face(conn, eid) == v2
