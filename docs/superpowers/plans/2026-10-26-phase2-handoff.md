# Phase 2 Handoff

Interfaces Phase 3 may rely on:
- `service.framesource.FrameSource` / `Frame` (`image` H×W×3 BGR uint8, `timestamp` epoch s)
- `service.framesource.SyntheticSource(frame_count, width, height)` / `FileSource(path)`
- `service.detector.Detector.detect(image) -> list[Detection]` with
  `Detection.bbox_xyxy`, `.confidence`; a missing model file is a deliberate
  no-op (`detect` returns `[]`)
- `service.tracker.Tracker.update(detections) -> list[Track]` with
  `Track.track_id`, `Track.centroid`, `Track.face_gallery`, `Track.hits`, `Track.confidence`
- `service.pipeline.Pipeline(source, detector, tracker, on_tracks=..., cfg=...)` with
  `.start()` (idempotent), `.stop()` (joins, idempotent), `.stats()` →
  `analysis_fps`, `detector_latency_ms`, `dropped_frames`, `interval`
- `service.pipeline.make_pipeline(cfg)` — service wiring + `/pipeline/restart` both build through it
- `service.device.select_provider()`, `.pick_decoder()`, `.model_variant(provider)`
  (CUDA → Dml → CPU; `_cuda_available()` is the torch-guarded test seam)
- `service.capture.RtspGstSource` with `.state`, `.reconnect_attempts`, `_next_delay()`
  and `build_pipeline_string(url, decoder, latency_ms)`
- `service.framesource.RtspCvSource(url, timeout_s)` — the live RTSP bridge while
  GStreamer is parked; same `.state`/`.reconnect_attempts` surface as RtspGstSource.
  `make_pipeline` picks it for `rtsp://` `dev_source`, `FileSource` otherwise.
  shortcut: a mid-read stall can't be interrupted (FFmpeg blocks); upgrade to
  RtspGstSource when `gi` is installable.

Phase 3 adds: line-cross state machine, events/sessions writes, clip recording.
Do not modify the tracker's gallery format — Phase 4 consumes it.

## Environment notes (this machine, 2026-10-27)

- **GStreamer/PyGObject parked** (operator decision): no official PyGObject wheels
  for CPython 3.14 (verified via `pip download`); routes to un-park are recorded
  in `service/capture.py`'s docstring. `RtspCvSource` is the live bridge meanwhile.
- **Real model in place** (2026-10-27): `yolo11n` exported to
  `C:\ProgramData\EmployeeTracker\models\yolo11n.onnx` with
  `YOLO('yolo11n.pt').export(format='onnx', imgsz=640, simplify=True, nms=True)`.
  `nms=True` is required: the plain export emits `(1, 84, 8400)` which
  `Detector.detect` cannot parse; the nms export emits `(1, 300, 6)`, which it
  reads directly (verified: 4 persons found in ultralytics' `bus.jpg`).
- **Export quirks on this box**: ultralytics' AutoUpdate tries to pip-install
  plain `onnxruntime` mid-export and hangs for tens of minutes — install
  `onnxruntime` manually first. It cannot coexist with `onnxruntime-directml`
  (same import name; whichever installs last shadows the other) — after
  exporting, `pip uninstall onnxruntime` and `pip install --force-reinstall
  --no-deps onnxruntime-directml`.
- Provider actually selected here: `DmlExecutionProvider` (this box has an
  RTX 3090 — Dml works over DX12; CUDA would need a CUDA torch build, not yet
  installed). Measured `yolo11n` latency: ~8 ms median on Dml (p90 ~9 ms),
  ~32 ms on CPU. Decoder fallback `avdec_h264` (no GStreamer to offer d3d11h264dec).
- Live camera: Android IP Webcam at `rtsp://192.168.18.226:8080/h264_ulaw.sdp`
  (ports 8080/554 both serve RTSP via the app; SDP advertises 1080p59.94, real
  delivery after decode measured ~36 fps). The app stops serving when it loses
  the foreground/screen; backoff reconnect then walks 1→2→4→8→16→30 s (cap
  verified) with flat RSS.
- The Windows service is **not installed** on this dev box; it runs in
  foreground (`python -m service.service_main run`). End-to-end verification
  used a temp data root + `dev_source` on port 8099.

## Known plan deviations (all intentional)

- `tests`: motion test asserts frames differ (pixel-sum version could never pass);
  `model_variant` returns 2 keys (matches pinned equality test, "detector" key unused);
  CUDA test patches `_cuda_available` (torch optional); backoff test uses jitter
  ranges; tracker ages only pre-existing tracks (newborns no longer age at birth);
  max-age test actually proves GC (max_age=2); pipeline increments `_frames_analysed`,
  doesn't busy-spin at end-of-stream, `start()` idempotent; `stop_pipeline` +
  `make_pipeline` added; `/pipeline/restart` actually rebuilds instead of lying.
