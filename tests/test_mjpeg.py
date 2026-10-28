from fastapi.testclient import TestClient

from service.db import connect, migrate, add_camera
from service.api import create_app
from service.mjpeg import MjpegServer
from service.zone import Zone, save_zone


def make_client(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    add_camera(conn, "gate")
    return TestClient(create_app(conn, None))


def test_stream_serves_placeholder_without_pipeline(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/camera/1/stream.mjpeg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert r.content.count(b"\xff\xd8") >= 1          # at least one JPEG SOI marker


def test_publish_delivers_frames_to_subscribers():
    import numpy as np
    import cv2
    srv = MjpegServer(max_fps=6.0)
    q = srv.subscribe()
    srv.publish(np.zeros((54, 96, 3), np.uint8))
    jpg = q.get(timeout=2)
    assert jpg[:2] == b"\xff\xd8"
    ok, decoded = cv2.imencode(".jpg", np.zeros((54, 96, 3), np.uint8))
    assert ok and len(jpg) > 100
    srv.unsubscribe(q)
    srv.stop()


def test_overlay_normalised(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    add_camera(conn, "gate")
    save_zone(conn, Zone(None, 1, [(0.0, 270.0), (960.0, 540.0)], "POS_TO_NEG", True))
    client = TestClient(create_app(conn, None))
    ov = client.get("/camera/1/overlay.json").json()
    assert ov["line"][0] == [0.0, 0.5] and ov["line"][1] == [1.0, 1.0]
    assert ov["direction_out"] == "POS_TO_NEG"


def test_overlay_without_zone(tmp_path):
    client = make_client(tmp_path)
    ov = client.get("/camera/1/overlay.json").json()
    assert ov["line"] is None
