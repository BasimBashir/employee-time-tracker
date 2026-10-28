# Remaining work (post Phase 6)

## Not built by design (YAGNI)
- Shift schedules, rosters, late-arrival logic, payroll export (spec §2).
- Cloud sync, multi-site, mobile.
- Customer/footfall analytics.
- Re-ID and face fine-tuning (`train_reid.py`, `train_face.py`): Phase 6 built their
  **evaluation** and the promotion gate, not their trainers. Fine-tuning them only
  pays off once real footage exists; the detector trainer is the template to copy.

## Known risks carried forward
1. GStreamer pipeline work is parked behind the cv2 bridge; `gst-plugins-ugly`/`libav`
   license terms were never verified. Confirm before any commercial distribution. (spec §15)
2. Library versions were not pinned from upstream docs. Pin from current docs before
   the first release build. (spec §15)
3. Face detection model choice in `service/faces.py` currently falls back to a
   person-crop heuristic. A dedicated face detector is needed for accuracy; the
   enrollment flow will still work but with worse crops.
4. The installer chains GStreamer's Inno Setup runtime; the combined install has not
   been tested against a clean Windows 11 image.
5. No load test with 3 real RTSP cameras has been run. The CPU budget is estimated
   from synthetic sources; real H.264 decode at 1080p adds cost not measured here.
6. Identity accuracy at a real gate (backlight, masks, hats, sideways walking) is
   unmeasured. The threshold bands (0.45 / 0.35) are starting points from the spec,
   not tuned values.

## Learned while building Phase 6 (2026-10-28)
7. The authoring box's torch is CPU-only (2.14.1+cpu) despite the RTX 3090 — training
   runs on CPU here. Inference-side DML (onnxruntime) is the accelerated path; the
   `cuda` ONNX variant cannot be validated on this machine (validator now reports it
   honestly rather than silently falling back).
8. All three exported "provider variants" are byte-identical fp32 files; only the
   filename and per-provider load validation differ. Real fp16 conversion is not built.
9. ultralytics 8.4 needs `polars` (checkpoint saving) and nests relative `project`
   paths under its own runs dir — `tools/train_detector.py` resolves the path and
   `polars` is in `tools/requirements.txt`.
10. onnx ≥ 1.23 writes IR version 14 which onnxruntime 1.24.x refuses to load;
    `validate_onnx` caps IR to 13 before loading. Revisit when either is upgraded.
11. Deployment of a promoted model: copy `models/active/detector.onnx` (and its
    `.report.json`) to the target's `{data_root}/models/active/` — the service prefers
    it over the stock `yolo11n.onnx` at startup (service/pipeline.py `detector_path`).
