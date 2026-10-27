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
