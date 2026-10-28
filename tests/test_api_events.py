from fastapi.testclient import TestClient
from service.db import connect, migrate, add_camera
from service.api import create_app

def _client(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    add_camera(conn, "cam")
    return conn, TestClient(create_app(conn, None))

def test_zones_roundtrip(tmp_path):
    conn, client = _client(tmp_path)
    r = client.post("/zones", json={"camera_id": 1,
                                    "line_points": [[0, 270], [960, 270]],
                                    "direction_out": "POS_TO_NEG"})
    assert r.status_code == 200
    zones = client.get("/zones", params={"camera_id": 1}).json()
    assert zones[0]["direction_out"] == "POS_TO_NEG"
    assert zones[0]["line_points"] == [[0, 270], [960, 270]]

def test_zones_rejects_unknown_camera_and_bad_direction(tmp_path):
    _, client = _client(tmp_path)
    assert client.post("/zones", json={"camera_id": 99,
                                       "line_points": [[0, 0], [1, 1]],
                                       "direction_out": "POS_TO_NEG"}).status_code == 400
    assert client.post("/zones", json={"camera_id": 1,
                                       "line_points": [[0, 0], [1, 1]],
                                       "direction_out": "SIDEWAYS"}).status_code == 400

def test_second_zone_disables_first(tmp_path):
    _, client = _client(tmp_path)
    client.post("/zones", json={"camera_id": 1, "line_points": [[0, 270], [960, 270]],
                                "direction_out": "POS_TO_NEG"})
    client.post("/zones", json={"camera_id": 1, "line_points": [[0, 300], [960, 300]],
                                "direction_out": "NEG_TO_POS"})
    zones = client.get("/zones", params={"camera_id": 1}).json()
    assert len(zones) == 2
    assert [z["enabled"] for z in zones] == [False, True]

def test_clips_listing_empty(tmp_path):
    _, client = _client(tmp_path)
    assert client.get("/clips", params={"date": "2026-10-26"}).json() == []

def test_clips_rejects_bad_date(tmp_path):
    _, client = _client(tmp_path)
    assert client.get("/clips", params={"date": "../../etc"}).status_code == 400
