import json
from pathlib import Path

from fastapi.testclient import TestClient

from service.db import connect, migrate, add_camera
from service.api import create_app
from service.cap import read_timespan_json, probe_clip


def make_client(tmp_path, cfg=None):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    add_camera(conn, "gate")
    return TestClient(create_app(conn, cfg)), conn


def test_timespan_tolerant(tmp_path):
    p = tmp_path / "clips" / "2026" / "10" / "26" / "x_timespan.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"started_at": "t0", "ended_at": None}), encoding="utf-8")
    ts = read_timespan_json(p)
    assert ts["started_at"] == "t0" and ts["ended_at"] is None
    assert read_timespan_json(tmp_path / "missing.json") is None
    (tmp_path / "junk.json").write_text("{not json", encoding="utf-8")
    assert read_timespan_json(tmp_path / "junk.json") is None


def test_probe_partial_records_not_opened(tmp_path):
    # A clip that is still being written: exists, zero readable frames.
    p = tmp_path / "partial.mp4"
    p.write_bytes(b"")
    info = probe_clip(p)
    assert info == {"exists": True, "frames": 0, "opened": False, "ready": False}
    assert probe_clip(tmp_path / "nope.mp4")["exists"] is False


def test_probe_and_download_endpoints(tmp_path):
    from service.config import Config
    cfg = Config()
    cfg.data_root = tmp_path / "d"
    cfg.ensure_dirs()
    client, _conn = make_client(tmp_path, cfg)
    clip = cfg.clips_dir / "2026" / "10" / "26" / "clip.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"not really an mp4 but a file")

    # probe: outside the clips dir is refused, inside is probed
    assert client.get("/clips/probe",
                      params={"path": str(tmp_path / "secret.mp4")}).status_code == 400
    r = client.get("/clips/probe", params={"path": str(clip)})
    assert r.status_code == 200 and r.json()["exists"] is True

    # download: traversal refused, real file served
    assert client.get("/clips/download",
                      params={"path": str(tmp_path / "secret.mp4")}).status_code == 400
    r = client.get("/clips/download", params={"path": str(clip)})
    assert r.status_code == 200 and r.content == b"not really an mp4 but a file"
    assert client.get("/clips/download",
                      params={"path": str(cfg.clips_dir / "gone.mp4")}).status_code == 404
