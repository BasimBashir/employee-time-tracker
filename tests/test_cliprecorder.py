from datetime import datetime
import numpy as np
from service.cliprecorder import ClipRecorder

def test_ring_buffer_is_bounded(tmp_path):
    rec = ClipRecorder(tmp_path / "clips", fps=15, max_seconds=15)
    for i in range(300):                      # way more than 15 s worth
        rec.add_frame(np.full((180, 320, 3), i % 255, np.uint8), float(i) / 15.0)
    assert len(rec._buffer) == 15 * 15        # exactly one window, no growth
    assert rec.memory_bytes_estimate() < 25 * 1024 * 1024   # < 25 MB, low-end box

def test_capture_writes_mp4(tmp_path):
    rec = ClipRecorder(tmp_path / "clips", pre_seconds=2, post_seconds=1, fps=10)
    for i in range(40):
        rec.add_frame(np.full((180, 320, 3), 120, np.uint8), float(i) / 10.0)
    path = rec.capture("OUT", datetime(2026, 10, 26, 13, 0), 1, 1)
    assert path.exists() and path.suffix == ".mp4"
    import cv2
    cap = cv2.VideoCapture(str(path))
    assert cap.get(5) > 0                    # readable, non-zero fps
    cap.release(); rec.stop()

def test_capture_includes_post_roll(tmp_path):
    rec = ClipRecorder(tmp_path / "clips", pre_seconds=1, post_seconds=1, fps=10)
    rec.add_frame(np.full((180, 320, 3), 120, np.uint8), 0.0)
    import threading
    def feed():
        for i in range(10):                   # frames landing after the event
            rec.add_frame(np.full((180, 320, 3), 200, np.uint8), 1.0 + i / 10.0)
            threading.Event().wait(0.05)
    threading.Thread(target=feed).start()
    path = rec.capture("IN", datetime(2026, 10, 26, 13, 5), 7, 1)
    import cv2
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(7))                        # frame count: pre-roll + post-roll
    cap.release()
    assert n > 1, n                            # more than the single pre frame
    rec.stop()

def test_capture_falls_back_when_no_post_frames(tmp_path):
    rec = ClipRecorder(tmp_path / "clips", pre_seconds=1, post_seconds=1, fps=10)
    rec.add_frame(np.full((180, 320, 3), 90, np.uint8), 0.0)
    path = rec.capture("IN", datetime(2026, 10, 26, 13, 5), 7, 1)
    assert path.exists()                      # pre-roll only, still written
    rec.stop()

def test_stop_is_idempotent_and_joins(tmp_path):
    rec = ClipRecorder(tmp_path / "clips")
    rec.stop(); rec.stop()
    assert rec._writer_thread is None or not rec._writer_thread.is_alive()
