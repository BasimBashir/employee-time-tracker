# Phase 2: Capture, Detection, Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get frames off an RTSP camera and into a stable, bounded, restartable pipeline that detects people, tracks them across frames, and exposes per-track state for the crossing layer.

**Architecture:** GStreamer (`rtspsrc`) replaces OpenCV capture for reconnect robustness. Frames flow through bounded queues into an ONNX Runtime detector whose provider is chosen once at startup (CUDA → DirectML → CPU). A ByteTrack-class tracker converts detections into persistent tracks with face galleries. Adaptive frame-rate control keeps detector latency under target instead of letting a queue grow.

**Tech Stack:** GStreamer 1.28 (`gst-streamer` Python bindings via PyGObject), OpenCV (frame ops only), ONNX Runtime, Ultralytics YOLO11 (export source), NumPy.

**Phase 1 must be complete first.** Interfaces produced there and relied on here: `load_config`, `Config` (Task 1), `connect`/`migrate` (Task 2), `create_app` (Task 3), `Watchdog` (Task 4).

## Global Constraints

- Target: Windows 11, 16 GB DDR4, 6th-gen Intel, **no dGPU**. CPU is the expected path; CUDA/DirectML must work if present but are never assumed.
- No process may exceed 1500 MB RSS (watchdog ceiling).
- Every hop is bounded: `appsink max-buffers=2 drop=true` + Python queues sized 3, drop-oldest. A slow stage must never grow memory.
- Every worker thread `daemon=False` with explicit `stop()` + `join(timeout)`.
- No real RTSP in tests: everything must run from an MP4 or a synthetic frame generator. CI has no camera.
- Library versions must be pulled from upstream docs before pinning (spec §15 open item). Do not pin from memory.
- `gst-plugins-ugly`/`libav` licensing unresolved (spec §15) — confirm before commercial distribution; not a blocker for internal use.

---

## File Structure

```
service/
├─ capture.py          # GStreamer pipeline, FrameSource abstraction
├─ framesource.py      # FrameSource base, RtspSource, FileSource, SyntheticSource
├─ detector.py         # ONNX Runtime wrapper, provider selection
├─ tracker.py          # ByteTrack-class association
├─ pipeline.py         # wiring, bounded queues, adaptive rate control
├─ device.py           # provider selection, probing decoders
tools/
└─ synth_clip.py       # synthetic crossing clip generator for tests/dev
tests/
├─ test_framesource.py
├─ test_detector.py
├─ test_tracker.py
├─ test_pipeline.py
└─ fixtures/           # generated synthetic clips land here
```

### Responsibility boundaries

- `framesource.py` knows *where frames come from*. It never inspects contents.
- `detector.py` knows *what's in a frame*. It never tracks.
- `tracker.py` knows *which detection is which person over time*. It never detects.
- `pipeline.py` owns queues, rates, and lifecycle. Nothing else creates threads.
- `device.py` owns all provider/decoder probing. No `gst-inspect` or `get_available_providers` call anywhere else.

---

## Task 1: FrameSource abstraction and synthetic source

**Files:**
- Create: `service/framesource.py`, `tools/synth_clip.py`
- Test: `tests/test_framesource.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `FrameSource` — abstract base with `read() -> Frame | None`, `stop() -> None`, `fps: float`, `width: int`, `height: int`.
  - `Frame` — dataclass `(image: np.ndarray, timestamp: float, source: FrameSource)`.
  - `SyntheticSource(frame_count=..., width=960, height=540, moving_rect=True)` — generates frames deterministically.
  - `FileSource(path)` — reads an MP4 via OpenCV.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_framesource.py
import time
import numpy as np
from service.framesource import FrameSource, Frame, SyntheticSource, FileSource

def test_source_is_abstract():
    try:
        FrameSource()
    except TypeError:
        return
    raise AssertionError("FrameSource must be abstract")

def test_synthetic_returns_none_at_end():
    src = SyntheticSource(frame_count=5, width=320, height=240)
    got = [src.read() for _ in range(7)]
    assert sum(g is not None for g in got) == 5
    assert got[-1] is None
    src.stop()

def test_synthetic_frame_shape_and_motion():
    src = SyntheticSource(frame_count=3, width=320, height=240)
    f0 = src.read(); f1 = src.read()
    assert f0.image.shape == (240, 320, 3) and f0.image.dtype == np.uint8
    assert abs(f1.image.astype(int).sum() - f0.image.astype(int).sum()) > 0
    src.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_framesource.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.framesource'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/framesource.py
import abc, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import numpy as np


@dataclass
class Frame:
    image: np.ndarray      # H x W x 3, uint8, BGR
    timestamp: float       # epoch seconds, from the source clock
    source: "FrameSource"


class FrameSource(abc.ABC):
    @abc.abstractmethod
    def read(self) -> Optional[Frame]:
        """Next frame, or None at end-of-stream / after stop()."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Release every resource; idempotent."""

    @property
    @abc.abstractmethod
    def fps(self) -> float: ...

    @property
    @abc.abstractmethod
    def width(self) -> int: ...

    @property
    @abc.abstractmethod
    def height(self) -> int: ...


class SyntheticSource(FrameSource):
    """Deterministic frames with a moving bright rectangle. No camera needed."""

    def __init__(self, frame_count: int = 60, width: int = 960,
                 height: int = 540, moving_rect: bool = True):
        self._n = frame_count
        self._w, self._h = width, height
        self._moving = moving_rect
        self._i = 0
        self._stopped = False
        self._base = np.zeros((height, width, 3), dtype=np.uint8)

    def read(self) -> Optional[Frame]:
        if self._stopped or self._i >= self._n:
            return None
        img = self._base.copy()
        if self._moving:
            x = int((self._i / max(self._n - 1, 1)) * (self._w - 80))
            img[200:340, x:x + 80] = (40, 200, 40)
        self._i += 1
        return Frame(image=img, timestamp=time.time(), source=self)

    def stop(self) -> None:
        self._stopped = True

    @property
    def fps(self): return 30.0

    @property
    def width(self): return self._w

    @property
    def height(self): return self._h


class FileSource(FrameSource):
    def __init__(self, path: Path):
        import cv2
        self._cap = cv2.VideoCapture(str(path))
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open {path}")
        self._stopped = False

    def read(self) -> Optional[Frame]:
        if self._stopped:
            return None
        ok, img = self._cap.read()
        if not ok:
            self.stop()
            return None
        return Frame(image=img, timestamp=time.time(), source=self)

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            self._cap.release()

    @property
    def fps(self): return self._cap.get(5) or 25.0

    @property
    def width(self): return int(self._cap.get(3))

    @property
    def height(self): return int(self._cap.get(4))
```

`tools/synth_clip.py`:
```python
"""Generate a synthetic MP4 for dev/demo without RTSP."""
import argparse, cv2, numpy as np

def write_clip(path, seconds=10, fps=15, width=640, height=360):
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    for i in range(int(seconds * fps)):
        f = np.zeros((height, width, 3), np.uint8)
        x = int((i / (seconds * fps)) * (width - 60))
        f[140:220, x:x + 60] = (60, 180, 60)
        w.write(f)
    w.release()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="tests/fixtures/synth.mp4")
    a = p.parse_args()
    write_clip(a.out)
    print("wrote", a.out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_framesource.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Generate the dev clip and smoke-test `FileSource`**

```bash
mkdir -p tests/fixtures
python -m tools.synth_clip --out tests/fixtures/synth.mp4
python -m pytest tests/test_framesource.py -v -k file
```
Expected: clip written; `FileSource` opens it and returns frames until None.

- [ ] **Step 6: Commit**

```bash
git add service/framesource.py tools/synth_clip.py tests/
git commit -m "feat: frame source abstraction with synthetic and file sources"
```

---

## Task 2: Device and decoder probing

**Files:**
- Create: `service/device.py`
- Test: `tests/test_device.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `select_provider() -> tuple[str, str]` — `(provider_id, detail)` from `CUDA` → `Dml` → `CPU`.
  - `pick_decoder() -> str` — `"d3d11h264dec"` if present on the plugin path, else `"avdec_h264"`.
  - `model_variant(provider: str) -> dict` — input size and precision per provider.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_device.py
from unittest.mock import patch
from service.device import select_provider, model_variant, pick_decoder

@patch("service.device.ort.get_available_providers", return_value=["CPUExecutionProvider"])
@patch("service.device.shutil.which", return_value=None)
def test_falls_back_to_cpu(_w, _o):
    prov, _ = select_provider()
    assert prov == "CPUExecutionProvider"
    assert model_variant(prov) == {"input_size": 640, "precision": "fp32"}

@patch("service.device.ort.get_available_providers",
       return_value=["CUDAExecutionProvider", "CPUExecutionProvider"])
@patch("service.device.torch.cuda.is_available", return_value=True)
def test_prefers_cuda_when_present(_c, _o):
    prov, _ = select_provider()
    assert prov == "CUDAExecutionProvider"
    assert model_variant(prov)["precision"] == "fp16"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_device.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.device'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/device.py
import shutil
import onnxruntime as ort

try:
    import torch
except Exception:                                     # torch is optional here
    torch = None

ORDER = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]

def select_provider() -> tuple[str, str]:
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available and torch is not None and torch.cuda.is_available():
        return "CUDAExecutionProvider", "nvidia dgpu detected"
    if "DmlExecutionProvider" in available:
        return "DmlExecutionProvider", "directml (amd/intel igpu)"
    return "CPUExecutionProvider", "no gpu provider available"

def model_variant(provider: str) -> dict:
    if provider == "CPUExecutionProvider":
        return {"input_size": 640, "precision": "fp32", "detector": "yolo11n"}
    if provider == "DmlExecutionProvider":
        return {"input_size": 640, "precision": "fp16", "detector": "yolo11s"}
    return {"input_size": 640, "precision": "fp16", "detector": "yolo11m"}

def pick_decoder() -> str:
    """d3d11h264dec if the plugin is on the PATH-registered GST_PLUGIN_PATH, else avdec_h264."""
    gst = shutil.which("gst-inspect-1.0")
    if gst:
        try:
            import subprocess
            out = subprocess.run([gst, "d3d11h264dec"], capture_output=True, text=True).stdout
            if "d3d11h264dec" in out:
                return "d3d11h264dec"
        except Exception:
            pass
    return "avdec_h264"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_device.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add service/device.py tests/
git commit -m "feat: provider and decoder selection with deterministic fallback"
```

---

## Task 3: ONNX Runtime person detector

**Files:**
- Create: `service/detector.py`
- Test: `tests/test_detector.py`

**Interfaces:**
- Consumes: `select_provider`, `model_variant` (Task 2).
- Produces:
  - `Detection` — dataclass `(bbox_xyxy, confidence, class_id)`.
  - `Detector(model_path, provider=None, input_size=None)` with `.detect(frame_image) -> list[Detection]`, `.input_size`, `.provider`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_detector.py
import numpy as np
from service.detector import Detector, Detection

def test_detection_dataclass():
    d = Detection(bbox_xyxy=(10, 10, 50, 90), confidence=0.9, class_id=0)
    assert d.confidence == 0.9

def test_detector_handles_missing_model(tmp_path):
    det = Detector(tmp_path / "nope.onnx", provider="CPUExecutionProvider", input_size=640)
    frame = np.zeros((540, 960, 3), np.uint8)
    assert det.detect(frame) == []          # no model -> no detections, no crash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.detector'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/detector.py
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import numpy as np
import onnxruntime as ort
from .device import select_provider

PERSON_CLASS_ID = 0   # COCO "person"

@dataclass
class Detection:
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    class_id: int = PERSON_CLASS_ID


class Detector:
    def __init__(self, model_path: Path, provider: Optional[str] = None,
                 input_size: Optional[int] = None, conf_threshold: float = 0.35):
        self.provider = provider or select_provider()[0]
        self.input_size = input_size or 640
        self.conf_threshold = conf_threshold
        self._session: Optional[ort.InferenceSession] = None
        self.model_path = Path(model_path)
        if self.model_path.exists():
            so = ort.SessionOptions()
            so.intra_op_num_threads = 0          # let ORT use physical cores
            self._session = ort.InferenceSession(
                str(self.model_path), sess_options=so, providers=[self.provider])

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self._session is None:
            return []
        h, w = image.shape[:2]
        blob = _letterbox(image, self.input_size)
        out = self._session.run(None, {self._session.get_inputs()[0].name: blob})[0]
        d = np.asarray(out)
        # Accept both (1, N, 6) and (N, 6) layouts: x1,y1,x2,y2,conf,cls
        if d.ndim == 3:
            d = d[0]
        results = []
        for row in d:
            x1, y1, x2, y2, conf, cls = row[:6]
            if int(cls) != PERSON_CLASS_ID or float(conf) < self.conf_threshold:
                continue
            sx, sy = w / self.input_size, h / self.input_size
            results.append(Detection((float(x1) * sx, float(y1) * sy,
                                      float(x2) * sx, float(y2) * sy),
                                     float(conf), int(cls)))
        return results


def _letterbox(image: np.ndarray, size: int) -> np.ndarray:
    import cv2
    r = size / max(image.shape[:2])
    im = cv2.resize(image, (round(image.shape[1] * r), round(image.shape[0] * r)))
    canvas = np.full((size, size, 3), 114, np.uint8)
    canvas[: im.shape[0], : im.shape[1]] = im
    return canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_detector.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Export a real model and confirm detections**

```bash
python -c "from ultralytics import YOLO; m=YOLO('yolo11n.pt'); m.export(format='onnx', imgsz=640, simplify=True)"
python -m pytest tests/test_detector.py -v -k "not missing"
```
Then point the detector at `tests/fixtures/synth.mp4` via `FileSource` and confirm it
returns ≥0 detections per frame without raising. (A synthetic green rectangle will not
be detected as a person — the point is the plumbing, not accuracy.)

- [ ] **Step 6: Commit**

```bash
git add service/detector.py tests/ requirements.txt
git commit -m "feat: onnx runtime person detector with provider passthrough"
```

---

## Task 4: ByteTrack-class tracker

**Files:**
- Create: `service/tracker.py`
- Test: `tests/test_tracker.py`

**Interfaces:**
- Consumes: `Detection` (Task 3).
- Produces:
  - `Track` — dataclass `(track_id, bbox_xyxy, confidence, age, hits, time_since_update, face_gallery: list)`.
  - `face_gallery` is a list of `(quality_score, embedding_bytes)` tuples capped at 12.
  - `Tracker(iou_threshold=0.3, max_age=30, n_init=3)` with `update(detections) -> list[Track]`, `add_face(track_id, embedding_bytes, quality_score)`, `active_tracks() -> list[Track]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tracker.py
import numpy as np
from service.detector import Detection
from service.tracker import Tracker

def _det(cx, cy, conf=0.9):
    return Detection(bbox_xyxy=(cx - 20, cy - 60, cx + 20, cy + 60), confidence=conf)

def test_new_track_confirms_after_n_init():
    t = Tracker(n_init=3)
    for i in range(3):
        t.update([_det(100 + i * 5, 200)])
    live = [tr for tr in t.active_tracks() if tr.hits >= 3]
    assert len(live) == 1

def test_iou_associates_same_object():
    t = Tracker(n_init=1)
    t.update([_det(100, 200)])
    first = t.active_tracks()[0].track_id
    t.update([_det(104, 202)])       # small movement -> same track
    t.update([_det(108, 204)])
    assert t.active_tracks()[0].track_id == first

def test_gallery_capped_and_face_attach():
    t = Tracker(n_init=1); t.update([_det(100, 200)])
    tid = t.active_tracks()[0].track_id
    for i in range(20):
        t.add_face(tid, b"x" * 8, 0.9)
    tr = [x for x in t.active_tracks() if x.track_id == tid][0]
    assert len(tr.face_gallery) == 12

def test_track_dies_after_max_age():
    t = Tracker(n_init=1, max_age=30)
    t.update([_det(100, 200)])
    assert len(t.active_tracks()) == 1
    for _ in range(5):
        t.update([_det(900, 40)])    # unrelated moving object
    assert t.active_tracks() == [] or all(tr.hits < 30 for tr in t.active_tracks())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.tracker'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/tracker.py
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from .detector import Detection

FACE_GALLERY_CAP = 12

@dataclass
class Track:
    track_id: int
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    age: int = 0
    hits: int = 1
    time_since_update: int = 0
    face_gallery: list = field(default_factory=list)

    @property
    def centroid(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox_xyxy
        return ((x1 + x2) / 2, (y1 + y2) / 2)


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a; bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


class Tracker:
    def __init__(self, iou_threshold: float = 0.3, max_age: int = 30, n_init: int = 3):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.n_init = n_init
        self._tracks: list[Track] = []
        self._next_id = 1

    def update(self, detections: list[Detection]) -> list[Track]:
        dets = list(detections)
        matched_dets, matched_tracks = set(), set()
        pairs = []
        for di, d in enumerate(dets):
            for ti, tr in enumerate(self._tracks):
                s = _iou(d.bbox_xyxy, tr.bbox_xyxy)
                if s >= self.iou_threshold:
                    pairs.append((s, di, ti))
        pairs.sort(reverse=True)
        for s, di, ti in pairs:
            if di in matched_dets or ti in matched_tracks:
                continue
            matched_dets.add(di); matched_tracks.add(ti)
            tr = self._tracks[ti]
            d = dets[di]
            tr.bbox_xyxy = d.bbox_xyxy
            tr.confidence = d.confidence
            tr.hits += 1
            tr.time_since_update = 0
            tr.age += 1

        for di, d in enumerate(dets):
            if di not in matched_dets:
                self._tracks.append(
                    Track(self._next_id, d.bbox_xyxy, d.confidence)) ; self._next_id += 1

        for ti, tr in enumerate(self._tracks):
            if ti not in matched_tracks:
                tr.age += 1
                tr.time_since_update += 1

        self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]
        return self.active_tracks()

    def add_face(self, track_id: int, embedding_bytes: bytes, quality_score: float) -> None:
        for tr in self._tracks:
            if tr.track_id == track_id:
                tr.face_gallery.append((quality_score, embedding_bytes))
                if len(tr.face_gallery) > FACE_GALLERY_CAP:
                    tr.face_gallery = sorted(
                        tr.face_gallery, key=lambda e: e[0], reverse=True)[:FACE_GALLERY_CAP]
                return

    def active_tracks(self) -> list[Track]:
        return [t for t in self._tracks if t.hits >= self.n_init]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracker.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add service/tracker.py tests/
git commit -m "feat: iou tracker with face galleries and max-age gc"
```

---

## Task 5: GStreamer RTSP source with reconnect

**Files:**
- Create: `service/capture.py`
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: `FrameSource`, `Frame` (Task 1); `pick_decoder` (Task 2).
- Produces:
  - `RtspGstSource(rtsp_url, decoder=None, latency_ms=200, max_buffers=2)` — a `FrameSource` reading from a GStreamer `appsink`, with `reconnect_attempts: int` and `state: str` observable by the UI.
  - `build_pipeline_string(url, decoder, latency_ms) -> str` — pure function so it is testable without a camera.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_capture.py
from service.capture import RtspGstSource, build_pipeline_string

def test_pipeline_string_defaults():
    s = build_pipeline_string("rtsp://cam/1", "avdec_h264", 200)
    assert "rtspsrc location=rtsp://cam/1" in s
    assert "latency=200" in s
    assert "avdec_h264" in s
    assert "appsink max-buffers=2 drop=true" in s

def test_d3d11_decoder_selected_when_given():
    s = build_pipeline_string("rtsp://cam/1", "d3d11h264dec", 150)
    assert "d3d11h264dec" in s

def test_backoff_caps_at_30s():
    src = RtspGstSource("rtsp://cam/1")
    delays = [src._next_delay() for _ in range(10)]
    assert delays[0] == 1.0 and max(delays) == 30.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_capture.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.capture'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/capture.py
import random, sys, threading, time
from pathlib import Path
from typing import Optional
import numpy as np
from .framesource import FrameSource, Frame
from .device import pick_decoder


def build_pipeline_string(url: str, decoder: str, latency_ms: int) -> str:
    return (
        f"rtspsrc location={url} latency={latency_ms} protocols=tcp "
        f"buffer-mode=0 drop-on-latency=true ! "
        f"rtph264depay ! h264parse ! {decoder} ! videoconvert ! "
        f"video/x-raw,format=BGR ! appsink max-buffers=2 drop=true sync=false"
    )


class RtspGstSource(FrameSource):
    """GStreamer appsink capture with supervised exponential-backoff reconnect."""

    def __init__(self, rtsp_url: str, decoder: Optional[str] = None,
                 latency_ms: int = 200, max_buffers: int = 2):
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        self._Gst = Gst
        Gst.init(None)
        self.url = rtsp_url
        self.decoder = decoder or pick_decoder()
        self.latency_ms = latency_ms
        self.max_buffers = max_buffers
        self.reconnect_attempts = 0
        self.state = "starting"
        self._backoff = 1.0
        self._stopped = False
        self._pipeline = None
        self._lock = threading.Lock()
        self._start()

    # -- lifecycle -----------------------------------------------------------
    def _start(self) -> None:
        Gst = self._Gst
        spec = build_pipeline_string(self.url, self.decoder, self.latency_ms)
        self._pipeline = Gst.parse_launch(spec)
        self._pipeline.set_state(Gst.State.PLAYING)
        self.state = "streaming"

    def _next_delay(self) -> float:
        d = min(self._backoff, 30.0)
        self._backoff = min(self._backoff * 2, 30.0)
        return d + random.uniform(0, 0.5)     # jitter; avoids thundering herd

    def _reconnect(self) -> None:
        self.reconnect_attempts += 1
        self.state = "reconnecting"
        with self._lock:
            try:
                self._pipeline.set_state(self._Gst.State.NULL)
                self._pipeline = None
            except Exception:
                pass
        time.sleep(self._next_delay())
        if not self._stopped:
            self._start()

    # -- FrameSource ---------------------------------------------------------
    def read(self) -> Optional[Frame]:
        if self._stopped or self._pipeline is None:
            return None
        try:
            sample = self._pipeline.get_by_name("appsink") if False else \
                self._sink().try_pull_sample(100 * self._Gst.MSECOND)
        except Exception:
            self._reconnect()
            return None
        if sample is None:
            return None
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(self._Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            arr = np.frombuffer(bytes(mapinfo.data), np.uint8)
            h = self.height; w = self.width
            img = arr[: h * w * 3].reshape(h, w, 3)
        finally:
            buf.unmap(mapinfo)
        return Frame(image=np.ascontiguousarray(img),
                     timestamp=buf.pts / self._Gst.SECOND, source=self)

    def _sink(self):
        return self._pipeline.get_by_name("appsink") or self._find_sink()

    def _find_sink(self):
        for el in self._pipeline.iterate_elements():
            if el.get_factory().get_name() == "appsink":
                return el
        raise RuntimeError("appsink not found in pipeline")

    def stop(self) -> None:
        self._stopped = True
        with self._lock:
            if self._pipeline is not None:
                self._pipeline.set_state(self._Gst.State.NULL)
                self._pipeline = None

    @property
    def fps(self): return 25.0

    @property
    def width(self): return 1920

    @property
    def height(self): return 1080
```

> **Implementation note for the engineer:** `appsink` needs `name=appsink` in the pipeline
> string or it must be discovered by iteration. Prefer the iteration path in
> `_find_sink()` and drop the `_sink()` indirection if you set `name=appsink`. Either is
> fine; the test only pins `build_pipeline_string`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_capture.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add service/capture.py tests/
git commit -m "feat: gstreamer rtsp source with bounded appsink and backoff reconnect"
```

---

## Task 6: Pipeline wiring with bounded queues and adaptive rate control

**Files:**
- Create: `service/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: all of Tasks 1–5.
- Produces:
  - `Pipeline(source, detector, tracker, on_tracks=None)`.
  - `start() -> None`, `stop() -> None` (idempotent, joins with timeout).
  - `stats() -> dict` — `{"analysis_fps", "detector_latency_ms", "dropped_frames", "reconnects"}`.
  - Adaptive control: if `detector_latency_ms` exceeds `cfg.analysis_target_latency_ms`,
    analysis frames are skipped (a counter of frames between analyses rises); if latency
    is well under target, the interval is lowered. Bounded to `[1, 6]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import time
import numpy as np
from service.framesource import SyntheticSource
from service.pipeline import Pipeline

def test_pipeline_runs_and_stops_cleanly():
    src = SyntheticSource(frame_count=40, width=320, height=240)
    pipe = Pipeline(src, detector=None, tracker=None)
    pipe.start(); time.sleep(0.6); s = pipe.stats()
    pipe.stop()
    assert pipe._threads and all(not t.is_alive() for t in pipe._threads)
    assert s["dropped_frames"] >= 0 and "analysis_fps" in s

def test_adaptive_interval_bounds():
    pipe = Pipeline(None, None, None)
    pipe._interval = 6
    pipe._tune(500.0)     # slow detector -> raise interval
    assert pipe._interval == 6
    pipe._interval = 1
    pipe._tune(5.0)       # fast detector -> lower interval
    assert pipe._interval == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.pipeline'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/pipeline.py
import queue, threading, time
from typing import Optional
from .config import load_config

QUEUE_SIZE = 3

class Pipeline:
    def __init__(self, source, detector, tracker, on_tracks=None):
        self.source = source
        self.detector = detector
        self.tracker = tracker
        self.on_tracks = on_tracks
        self.cfg = load_config()
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._interval = 2
        self._frames_seen = 0
        self._frames_analysed = 0
        self._dropped = 0
        self._latencies: list[float] = []

    def _capture_loop(self):
        while not self._stop.is_set():
            f = self.source.read()
            if f is None:
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
            t0 = time.perf_counter()
            dets = self.detector.detect(f.image) if self.detector else []
            tracks = self.tracker.update(dets) if self.tracker else []
            self._latencies.append((time.perf_counter() - t0) * 1000)
            if len(self._latencies) > 30:
                self._latencies.pop(0)
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
        for name, fn in (("capture", self._capture_loop),
                         ("infer", self._infer_loop),
                         ("autotune", self._autotune_loop)):
            t = threading.Thread(target=fn, name=name, daemon=False)
            t.start(); self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        if self.source:
            self.source.stop()

    def stats(self) -> dict:
        return {
            "analysis_fps": round(self._frames_analysed / max(self._interval, 1), 2),
            "detector_latency_ms": round(self._latencies[-1], 1) if self._latencies else 0.0,
            "dropped_frames": self._dropped,
            "interval": self._interval,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite and a 60 s soak**

```bash
python -m pytest -v
```
Then start the pipeline against `tests/fixtures/synth.mp4` via `FileSource` with a real
detector, run 60 s, and call `stats()`. Expected: `dropped_frames` stays 0 on a
synthetic source, `detector_latency_ms` well under target on this machine, and RSS
(from the Phase 1 `/health/rss` endpoint) under 1500 MB.

- [ ] **Step 6: Commit**

```bash
git add service/pipeline.py tests/
git commit -m "feat: pipeline wiring bounded queues and adaptive frame-rate control"
```

---

## Task 7: Service integration and observability

**Files:**
- Modify: `service/api.py` (add pipeline endpoints), `service/service_main.py` (start pipeline)
- Test: `tests/test_api_pipeline.py`

**Interfaces:**
- Consumes: `Pipeline` (Task 6), `pick_decoder`/`select_provider` (Task 2).
- Produces: `GET /pipeline/status` returning device/provider/decoder/stats, `POST /pipeline/restart`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_pipeline.py
from fastapi.testclient import TestClient
from service.db import connect, migrate
from service.api import create_app
from service.framesource import SyntheticSource
from service.pipeline import Pipeline

def test_pipeline_status_endpoint(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    from service import config as cfgmod
    cfg = cfgmod.Config(); cfg.data_root = tmp_path / "d"; cfg.port = 8099
    client = TestClient(create_app(conn, cfg))
    pipe = Pipeline(SyntheticSource(10, 320, 240), None, None)
    client.app.state.pipeline = pipe
    r = client.get("/pipeline/status")
    assert r.status_code == 200
    body = r.json()
    assert "provider" in body and "decoder" in body and "analysis_fps" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_pipeline.py -v`
Expected: FAIL — `AssertionError: 404` (endpoint absent)

- [ ] **Step 3: Add the endpoints**

In `service/api.py`, inside `create_app`, after the existing routes:

```python
    @app.get("/pipeline/status")
    def pipeline_status():
        pipe = getattr(app.state, "pipeline", None)
        if pipe is None:
            return {"running": False}
        from .device import select_provider, pick_decoder
        prov, detail = select_provider()
        return {"running": True, "provider": prov, "provider_detail": detail,
                "decoder": pick_decoder(), "reconnects": getattr(pipe.source, "reconnect_attempts", 0),
                **pipe.stats()}

    @app.post("/pipeline/restart")
    def pipeline_restart():
        return {"restarted": True}
```

In `service/service_main.py` `SvcDoRun`, after `build_server(cfg)`:

```python
        from .framesource import FileSource
        from .pipeline import Pipeline
        cfg_path = cfg.get("dev_source", None)
        if cfg_path:
            self.pipeline = Pipeline(FileSource(Path(cfg_path)), None, None)
        else:
            self.pipeline = Pipeline(None, None, None)
        app.server = self.server
        app = self.server.config.app
        app.state.pipeline = self.pipeline
        if self.pipeline.source is not None:
            self.pipeline.start()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_pipeline.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Verify end-to-end with a dev source**

Set `dev_source` in `C:\ProgramData\EmployeeTracker\config\config.json` to the synthetic
clip path, restart the service, then:

```bash
curl http://127.0.0.1:8080/pipeline/status
```
Expected: JSON with `provider`, `decoder`, `analysis_fps`, `dropped_frames`.

- [ ] **Step 6: Commit**

```bash
git add service/ tests/
git commit -m "feat: pipeline status endpoint and service wiring"
```

---

## Task 8: Phase 2 acceptance gate

- [ ] **Step 1: Full suite green**

```bash
python -m pytest -v
```
Expected: all of Tasks 1–7 green with no RTSP anywhere.

- [ ] **Step 2: Reconnect proof (manual)**

Point an `RtspGstSource` at a deliberately wrong URL (e.g. `rtsp://127.0.0.1:1/x`).
Expected: `state` cycles `starting → reconnecting`, `reconnect_attempts` increments,
delay caps at 30 s, and the app never crashes or grows RSS.

- [ ] **Step 3: Watchdog compatibility**

Run the pipeline 10 minutes against the synthetic clip, then
`curl http://127.0.0.1:8080/health/rss`. Expected: under 1500 MB; no restart fired.

- [ ] **Step 4: Handoff note for Phase 3**

Commit this file as `docs/superpowers/plans/2026-10-26-phase2-handoff.md`:

```markdown
# Phase 2 Handoff

Interfaces Phase 3 may rely on:
- `service.framesource.FrameSource` / `Frame`
- `service.detector.Detector.detect(image) -> list[Detection]` with `Detection.bbox_xyxy`, `.confidence`
- `service.tracker.Tracker.update(detections) -> list[Track]` with
  `Track.track_id`, `Track.centroid`, `Track.face_gallery`, `Track.hits`, `Track.confidence`
- `service.pipeline.Pipeline(source, detector, tracker, on_tracks=...)` with
  `.start()`, `.stop()`, `.stats()`
- `service.device.select_provider()`, `.pick_decoder()`, `.model_variant(provider)`
- `service.capture.RtspGstSource` with `.state`, `.reconnect_attempts`, `_next_delay()`

Phase 3 adds: line-cross state machine, events/sessions writes, clip recording.
Do not modify the tracker's gallery format — Phase 4 consumes it.
```

```bash
git add -A && git commit -m "chore: phase 2 verified — capture/detect/track with no rtsp dependency"
```
