"""Pipeline wiring — the only module that creates worker threads.

source -> bounded queue -> detector -> tracker -> on_tracks callback. Every hop
is bounded: the queue holds QUEUE_SIZE frames and drops the incoming one when
full, so a slow detector can never grow memory. Adaptive interval control
skips analysis frames to keep detector latency under the config target.
"""
import queue
import threading
import time
from pathlib import Path

from .config import load_config

QUEUE_SIZE = 3

class Pipeline:
    def __init__(self, source, detector, tracker, on_tracks=None, cfg=None):
        self.source = source
        self.detector = detector
        self.tracker = tracker
        self.on_tracks = on_tracks
        self.cfg = cfg or load_config()
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._interval = 2
        self._frames_seen = 0
        self._frames_analysed = 0
        self._dropped = 0
        self._latencies: list[float] = []
        self._started_at: float | None = None
        self._ended_at: float | None = None
        # Analysis is capped at the source's own frame rate; SyntheticSource and
        # FileSource replay far faster than live, which would otherwise flood
        # on_tracks and distort the fps numbers.
        self._source_fps = float(getattr(source, "fps", 0) or 0) or 25.0
        self._next_analysis_at = 0.0

    def _due_for_analysis(self) -> bool:
        now = time.monotonic()
        if now < self._next_analysis_at:
            return False
        self._next_analysis_at = now + 1.0 / self._source_fps
        return True

    def _capture_loop(self):
        while not self._stop.is_set():
            f = self.source.read()
            if f is None:
                # End-of-stream or no buffer yet: don't busy-spin.
                self._stop.wait(0.05)
                continue
            try:
                self._q.put(f, timeout=0.1)
            except queue.Full:
                self._dropped += 1        # bounded: never grow, just drop

    def _infer_loop(self):
        while not self._stop.is_set():
            try:
                f = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            self._frames_seen += 1
            if self._frames_seen % self._interval:
                continue                  # adaptive skip
            if not self._due_for_analysis():
                continue                  # never analyse faster than the source
            self._frames_analysed += 1
            t0 = time.perf_counter()
            dets = self.detector.detect(f.image) if self.detector else []
            tracks = self.tracker.update(dets) if self.tracker else []
            self._latencies.append((time.perf_counter() - t0) * 1000)
            if len(self._latencies) > 30:
                self._latencies.pop(0)
            self._tune(self._latencies[-1])
            if self.on_tracks:
                self.on_tracks(tracks, f)

    def _tune(self, latency_ms: float) -> None:
        target = self.cfg.analysis_target_latency_ms
        if latency_ms > target and self._interval < 6:
            self._interval += 1
        elif latency_ms < target * 0.4 and self._interval > 1:
            self._interval -= 1

    def _autotune_loop(self):
        while not self._stop.wait(5):
            if self._latencies:
                self._tune(sorted(self._latencies)[len(self._latencies) // 2])

    def start(self) -> None:
        if self._threads:
            return                        # idempotent
        self._started_at = time.monotonic()
        self._ended_at = None
        for name, fn in (("capture", self._capture_loop),
                         ("infer", self._infer_loop),
                         ("autotune", self._autotune_loop)):
            t = threading.Thread(target=fn, name=name, daemon=False)
            t.start(); self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()
        self._ended_at = time.monotonic()
        if self.source:
            self.source.stop()

    def stats(self) -> dict:
        if self._started_at is None:
            fps = 0.0                     # never started: no rate exists yet
        else:
            elapsed = (self._ended_at or time.monotonic()) - self._started_at
            fps = round(self._frames_analysed / max(elapsed, 1e-6), 1)
        return {
            "analysis_fps": fps,
            "detector_latency_ms": round(self._latencies[-1], 1) if self._latencies else 0.0,
            "dropped_frames": self._dropped,
            "interval": self._interval,
        }


def make_pipeline(cfg) -> Pipeline:
    """Build the service pipeline from config. Without a `dev_source` the
    pipeline exists but has nothing to capture; with one it runs the real
    path (a missing model file makes the detector a no-op, by design)."""
    from .detector import Detector
    from .framesource import FileSource, RtspCvSource
    from .tracker import Tracker
    src = cfg.get("dev_source")
    if not src:
        return Pipeline(None, None, None, cfg=cfg)
    if str(src).startswith("rtsp://"):
        source = RtspCvSource(src)
    else:
        source = FileSource(Path(src))
    return Pipeline(source, Detector(cfg.models_dir / "yolo11n.onnx"),
                    Tracker(), cfg=cfg)
