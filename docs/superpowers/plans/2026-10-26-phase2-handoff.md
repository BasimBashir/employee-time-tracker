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

Phase 3 adds: line-cross state machine, events/sessions writes, clip recording.
Do not modify the tracker's gallery format — Phase 4 consumes it.

## Environment notes (this machine, 2026-10-27)

- **GStreamer/PyGObject deferred** (operator decision): no `gi` on this box, so
  `RtspGstSource` is unverified against a live stream; its tests skip cleanly.
  Everything else runs for real. Installing GStreamer later needs no code change.
- **torch skipped** (operator decision): no model export yet, so `Detector` runs
  model-less (returns `[]`). Provider/decoder plumbing verified end-to-end
  without inference.
- Provider actually selected here: `DmlExecutionProvider` (Intel iGPU); decoder
  fallback `avdec_h264` (no GStreamer to offer d3d11h264dec).
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
