import time
from service.framesource import SyntheticSource
from service.pipeline import Pipeline

def test_pipeline_runs_and_stops_cleanly():
    src = SyntheticSource(frame_count=40, width=320, height=240)
    pipe = Pipeline(src, detector=None, tracker=None)
    pipe.start()
    assert len(pipe._threads) == 3
    time.sleep(0.6); s = pipe.stats()
    threads = list(pipe._threads)
    pipe.stop()
    assert all(not t.is_alive() for t in threads)
    assert s["dropped_frames"] >= 0 and "analysis_fps" in s

def test_adaptive_interval_bounds():
    pipe = Pipeline(None, None, None)
    pipe._interval = 6
    pipe._tune(500.0)     # slow detector -> stays at the cap of 6
    assert pipe._interval == 6
    pipe._interval = 1
    pipe._tune(5.0)       # fast detector -> stays at the floor of 1
    assert pipe._interval == 1

def test_start_is_idempotent():
    src = SyntheticSource(frame_count=10, width=320, height=240)
    pipe = Pipeline(src, None, None)
    pipe.start(); pipe.start()
    assert len(pipe._threads) == 3
    pipe.stop()

def test_make_pipeline_selects_source_by_scheme(tmp_path):
    from service.config import Config
    from service.framesource import RtspCvSource
    from service.pipeline import make_pipeline
    cfg = Config(data_root=tmp_path)
    cfg._extra["dev_source"] = "rtsp://127.0.0.1:1/x"   # refused instantly
    pipe = make_pipeline(cfg)
    assert isinstance(pipe.source, RtspCvSource)
    pipe.source.stop()
    pipe.stop()

def test_analysis_rate_capped_at_source_fps():
    src = SyntheticSource(frame_count=10, width=320, height=240)
    pipe = Pipeline(src, None, None)
    pipe.start()
    time.sleep(0.5)
    pipe.stop()
    # 10-frame source at 30 fps: must never exceed ~30 analyses per second of
    # wall clock, however fast the machine replays the source.
    assert pipe._frames_analysed <= 16


def test_detector_path_prefers_promoted_active_model(tmp_path):
    from types import SimpleNamespace
    from service.pipeline import detector_path
    cfg = SimpleNamespace(models_dir=tmp_path)
    assert detector_path(cfg) == tmp_path / "yolo11n.onnx"       # stock fallback
    (tmp_path / "active").mkdir()
    (tmp_path / "active" / "detector.onnx").write_bytes(b"x")
    assert detector_path(cfg) == tmp_path / "active" / "detector.onnx"
