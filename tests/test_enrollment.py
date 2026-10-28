import numpy as np
from service.db import connect, migrate, add_employee
from service.roster import roster_embeddings, primary_face
from service.enrollment import EnrollmentSession, REQUIRED_POSES
from service.framesource import SyntheticSource

class FakeFace:
    def embed(self, crop):
        return np.full(512, float(crop.mean()) / 255.0, np.float32).tobytes()

def test_finish_writes_primary_enrollment(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    es = EnrollmentSession(SyntheticSource(30, 320, 240), FakeFace(), None)
    for _ in range(REQUIRED_POSES):
        assert es.capture_pose() is True
    assert es.captured_count == REQUIRED_POSES
    es.finish(conn, eid)
    rows = roster_embeddings(conn)
    # every captured pose is a roster reference; the best one is primary
    assert len(rows) == REQUIRED_POSES and all(r[0] == eid for r in rows)
    assert primary_face(conn, eid) is not None

def test_refuses_to_finish_with_too_few_poses(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    es = EnrollmentSession(SyntheticSource(30, 320, 240), FakeFace(), None)
    for _ in range(3):
        es.capture_pose()
    try:
        es.finish(conn, eid)
    except ValueError:
        return
    raise AssertionError("finish must refuse below REQUIRED_POSES")

def test_quality_scores_recorded(tmp_path):
    es = EnrollmentSession(SyntheticSource(30, 320, 240), FakeFace(), None)
    for _ in range(REQUIRED_POSES):
        es.capture_pose()
    assert len(es.quality_scores) == REQUIRED_POSES
    assert all(0.0 <= q <= 1.0 for q in es.quality_scores)

def test_capture_returns_false_when_source_exhausted():
    es = EnrollmentSession(SyntheticSource(2, 320, 240), FakeFace(), None)
    assert es.capture_pose() is True
    assert es.capture_pose() is True
    assert es.capture_pose() is False
