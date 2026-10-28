import numpy as np
from types import SimpleNamespace
from service.db import connect, migrate
from service.db import add_employee
from service.roster import save_enrollment
from service.resolver import Resolver


def _unit(i):
    v = np.zeros(512, np.float32); v[i] = 1.0
    return v.tobytes()

def _at_cos(c):
    """Unit vector whose cosine against _unit(0) is exactly c."""
    v = np.zeros(512, np.float32)
    v[0] = c
    v[1] = np.sqrt(1.0 - c * c)
    return v.tobytes()

def _track_with(*gallery):
    return SimpleNamespace(track_id=1,
                           face_gallery=list(gallery), centroid=(480, 400))

def test_confirms_above_threshold(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, _unit(0), None, 0.9, is_primary=1)
    r = Resolver(conn, confirm=0.45, uncertain=0.35)
    got = r.resolve(_track_with((0.9, _unit(0))))
    assert got is not None and got[0] == eid and got[1] > 0.9
    assert got[2] == 0                                       # not uncertain

def test_uncertain_band_flags(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, _unit(0), None, 0.9, is_primary=1)
    r = Resolver(conn, confirm=0.9999, uncertain=0.45)
    hit = r.resolve_embedding(_at_cos(0.6))
    assert hit is not None
    assert hit[0] == eid and hit[2] == 1                    # 0.45 <= 0.6 < confirm

def test_below_threshold_returns_none(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, _unit(0), None, 0.9, is_primary=1)
    r = Resolver(conn, confirm=99.0, uncertain=99.0)
    assert r.resolve(_track_with((0.9, _unit(0)))) is None

def test_empty_gallery_returns_none(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    r = Resolver(conn)
    assert r.resolve(SimpleNamespace(face_gallery=[])) is None

def test_no_roster_returns_none(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    r = Resolver(conn)
    assert r.resolve(_track_with((0.9, _unit(0)))) is None

def test_best_face_from_gallery_wins(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    a = add_employee(conn, "Ayesha", "E1")
    b = add_employee(conn, "Bilal", "E2")
    save_enrollment(conn, a, _unit(0), None, 0.9, is_primary=1)
    save_enrollment(conn, b, _unit(1), None, 0.9, is_primary=1)
    r = Resolver(conn)
    got = r.resolve(_track_with((0.1, _unit(0)), (0.95, _unit(1))))
    assert got[0] == b                                       # higher-quality face chosen
    assert got[2] == 0

def test_invalidate_picks_up_new_roster(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    r = Resolver(conn)
    assert r.resolve(_track_with((0.9, _unit(0)))) is None
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, _unit(0), None, 0.9, is_primary=1)
    assert r.resolve(_track_with((0.9, _unit(0)))) is None   # stale until told
    r.invalidate()
    assert r.resolve(_track_with((0.9, _unit(0))))[0] == eid
