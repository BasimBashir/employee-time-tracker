from fastapi.testclient import TestClient

from service.api import create_app
from service.config import Config
from service.db import connect, migrate
from service.framesource import SyntheticSource
from service.pipeline import Pipeline


def test_pipeline_status_endpoint(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    cfg = Config(); cfg.data_root = tmp_path / "d"
    client = TestClient(create_app(conn, cfg))
    pipe = Pipeline(SyntheticSource(10, 320, 240), None, None, cfg=cfg)
    client.app.state.pipeline = pipe
    r = client.get("/pipeline/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is True
    assert "provider" in body and "decoder" in body and "analysis_fps" in body

def test_pipeline_status_without_pipeline(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    cfg = Config(); cfg.data_root = tmp_path / "d"
    client = TestClient(create_app(conn, cfg))
    r = client.get("/pipeline/status")
    assert r.status_code == 200
    assert r.json() == {"running": False}

def test_pipeline_restart(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    cfg = Config(); cfg.data_root = tmp_path / "d"
    client = TestClient(create_app(conn, cfg))
    pipe = Pipeline(SyntheticSource(3, 320, 240), None, None, cfg=cfg)
    client.app.state.pipeline = pipe
    r = client.post("/pipeline/restart")
    assert r.status_code == 200
    assert r.json()["restarted"] is True
    # hollow rebuild (no dev_source): attached, not started, stats available
    new_pipe = client.app.state.pipeline
    assert new_pipe is not pipe
    assert new_pipe.source is None and new_pipe._threads == []
