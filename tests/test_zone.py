from service.db import connect, migrate, add_camera
from service.zone import Zone, save_zone, load_zones, get_active_zone

def test_save_and_load_zone(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn); add_camera(conn, "cam")
    z = Zone(id=None, camera_id=1, line_points=[(0.0, 270.0), (960.0, 270.0)],
             direction_out="POS_TO_NEG", enabled=True)
    zid = save_zone(conn, z)
    loaded = get_active_zone(conn, 1)
    assert loaded.id == zid
    assert loaded.line_points == [(0.0, 270.0), (960.0, 270.0)]
    assert loaded.direction_out == "POS_TO_NEG"
    assert loaded.enabled is True

def test_disabled_zone_not_returned(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn); add_camera(conn, "cam")
    save_zone(conn, Zone(None, 1, [(0.0, 270.0), (960.0, 270.0)], "POS_TO_NEG", False))
    assert get_active_zone(conn, 1) is None
    assert len(load_zones(conn, 1)) == 1

def test_only_one_active_zone_per_camera(tmp_path):
    """Saving a second enabled zone must disable the first — one line is authoritative."""
    conn = connect(tmp_path / "t.db"); migrate(conn); add_camera(conn, "cam")
    first = save_zone(conn, Zone(None, 1, [(0.0, 270.0), (960.0, 270.0)], "POS_TO_NEG", True))
    second = save_zone(conn, Zone(None, 1, [(0.0, 300.0), (960.0, 300.0)], "NEG_TO_POS", True))
    actives = [z for z in load_zones(conn, 1) if z.enabled]
    assert len(actives) == 1
    assert actives[0].id == second
    assert [z.id for z in load_zones(conn, 1)] == [first, second]     # nothing deleted
