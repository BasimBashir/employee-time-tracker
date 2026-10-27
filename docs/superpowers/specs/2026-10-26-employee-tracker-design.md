# Employee Entry/Exit & Out-of-Zone Time Tracker — Design

**Date:** 2026-10-26
**Status:** Approved for planning
**Target:** Windows 11 desktop, low-end hardware (16 GB DDR4, 6th-gen Intel CPU, no dGPU)

---

## 1. Problem

A retail shop has one employee entry/exit point (also used by customers). The owner
needs to know, per employee, when they left the floor and when they came back, with
the total minutes away, plus video evidence for each crossing.

Cameras: 1–3 Hikvision RTSP cameras covering the same gate (up to ~50 employees).
Most crossings are of enrolled employees; **customers and other unenrolled people
must be ignored entirely** — no records, no review queue.

---

## 2. Scope Boundaries

**In scope:** capture, person detection, tracking, identity, line-cross state machine,
duration computation, evidence clips, SQLite records, dashboard, reports, enrollment,
model training tooling, packaging to `.exe`, Windows autostart.

**Deliberately out of scope (YAGNI):**

- Shift schedules, rosters, late-arrival logic, payroll integration. The owner asked
  for entry/exit timing only. Late arrival is explicitly *not* a first-release feature
  because the system is not running for the whole working day yet.
- Customer analytics, footfall counting, dwell-time heatmaps.
- Cloud sync, multi-site, mobile apps.
- Body re-ID as an *identity* mechanism — it maintains tracks, it does not identify.

---

## 3. Architecture — Three Processes

The core decision, and the reason the system can run all day without leaks.

```
┌─────────────────────────────┐    localhost WS:8080    ┌────────────────────────┐
│  TrackerService (Windows    │◄────────────────────────│  Tray App (user, tiny) │
│  Service, LocalSystem)      │                         │  status + Show UI      │
│                             │                         └────────────────────────┘
│  ┌───────────────────────┐  │    localhost WS:8080    ┌────────────────────────┐
│  │ GStreamer capture ×N   │  │◄────────────────────────│  Electron Dashboard    │
│  │ Inference (ONNX RT)   │  │                         │  (Chromium renderer)   │
│  │ Tracker + re-ID       │  │                         └────────────────────────┘
│  │ Crossing state machine│  │
│  │ Clip recorder          │  │
│  │ SQLite (WAL)           │  │
│  └───────────────────────┘  │
└─────────────────────────────┘
              │
              ▼
   C:\ProgramData\EmployeeTracker\
      ├─ clips\          (90-day auto-purge + manual)
      ├─ models\active\
      ├─ data\tracker.db
      ├─ logs\
      └─ config\config.json
```

**Why three processes, not one exe.** The requirement is "starts at Windows boot, runs
until shutdown." A single process that owns both the CV pipeline and the UI fails that
requirement: if the UI crashes or is closed, the gate stops logging. The service owns
everything that must keep running and is restartable independently of the UI. The UI
is a *client* — closing it changes nothing about attendance recording.

The `.exe` the user runs is the tray app + dashboard. The service is installed as a
real Windows Service so it starts before any user logs in.

### 3.1 Process responsibilities

**TrackerService** (Python, headless)

- One capture worker per camera; each runs its own GStreamer pipeline.
- One inference worker per camera (CPU-bound work is the bottleneck; one per camera
  keeps them isolated and independently restartable).
- Shared: tracker, identity resolver, state machine, DB writer, clip recorder, watchdog.
- Exposes a JSON API over `localhost` WebSocket (not raw TCP) for the UI and tray.

**Tray app** (Python, ~40 MB RAM): icon, status colour, "Open dashboard",
"Restart worker", "Quit". No frame decoding at all.

**Dashboard** (Electron): live view, event log, records, reports, settings, enrollment.

---

## 4. Runtime & Model Selection

Provider selection happens **once at service startup**, is written to config, and is
displayed in the UI so the owner can see what was chosen:

```
1. CUDAExecutionProvider   (NVIDIA dGPU, Turing+)
2. DmlExecutionProvider    (AMD / Intel iGPU via DirectML)
3. CPUExecutionProvider    (fallback — the expected case on target hardware)
```

Model variant is chosen *with* the device, same code path, different asset files:

| | CPU (target) | GPU |
|---|---|---|
| Person detector | `yolo11n`-class @ 640 | `yolo11s`/`m` @ 640 |
| Face model | `buffalo_sc` @ 192 | `buffalo_l` @ 320 |
| Body re-ID | OSNet-class, 256×128 | same, larger variant |
| Inference precision | FP32 (INT8 only if calibrated) | FP16 |

**Frame-rate budget:** the detector is the constraint. Analysis rate is adaptive — a
controller keeps detector latency under a target (default 120 ms) by dropping analysis
frames, never by growing a queue. With 2 cameras on a 6th-gen CPU this lands around
8–12 analysis fps per camera, which is plenty for a doorway where a crossing takes
~1 s of walking.

**Explicitly not used: NVIDIA DeepStream.** It requires an NVIDIA dGPU (Pascal was
dropped in DS 7.0; host support starts at Turing) and is x86_64 Ubuntu-only. The target
machine has an Intel iGPU and runs Windows 11. DeepStream is wrong on OS, wrong on
hardware, and wrong on problem size (it exists for multicam NVDEC throughput).

---

## 5. Capture — GStreamer

`rtspsrc` replaces OpenCV's `VideoCapture`, which leaks sockets on reconnect and
offers no latency control — the overnight camera reboot is *the* failure mode for an
all-day logger.

```
rtspsrc location=rtsp://user:pass@ip/Streaming/Channels/101 latency=200
  protocols=tcp buffer-mode=0 drop-on-latency=true
  ! rtph264depay ! h264parse ! d3d11h264dec ! videoconvert
  ! video/x-raw,format=BGR ! appsink max-buffers=2 drop=true sync=false
```

`d3d11h264dec` falls back to `avdec_h264` when unavailable. The choice is probed at
startup with `gst-inspect-1.0` and recorded in config.

**Reconnect:** exponential backoff, 1 s → 2 s → 4 s … capped at 30 s, with jitter. A
camera that drops at 03:00 recovers unattended. Bus-state transitions are logged.

**Bounded at every hop.** `appsink max-buffers=2 drop=true` plus bounded Python queues
(size 3, drop-oldest). A slow detector cannot grow memory — the cap is structural, not
a hope.

### 5.1 Packaging consequence

GStreamer's Windows 1.28 installers are **Inno Setup, not MSI** (unattended flags:
`/SILENT`, `/DIR=`, `/ALLUSERS`). Our installer must therefore chain GStreamer's own
runtime installer as a step, rather than declaring a dependency. This is an accepted
cost: it adds a ~300 MB install step for the shop owner in exchange for the reconnect
robustness the product depends on.

**Open item — licensing.** The H.264 decoders live in `gst-plugins-ugly`/`libav`; their
license terms were **not verified** at spec time. This must be confirmed before any
commercial distribution. Not a blocker for internal single-site retail use.

---

## 6. CV Pipeline

Three bounded stages, each restartable without losing state:

```
[GStreamer worker] → q(3) → [Inference worker] → q(3) → [Decider + DB writer]
```

1. **Detect** — person class only (no COCO object classes). Person crops are all the
   downstream stages need.
2. **Track** — ByteTrack-class association. Each track has an id, a rolling history,
   and a face gallery (best-quality embedding per recent second).
3. **Resolve identity** — at the crossing moment, the best face embedding from the last
   ~1.5 s is cosine-compared against the enrolled roster:
   - ≥ `confirm_threshold` (default 0.45) → accepted
   - 0.35–0.45 → accepted but flagged `uncertain` for review
   - < 0.35 → **discarded**. Unenrolled people produce no records at all.

   Body re-ID embedding is computed every N frames purely to keep a track alive when
   the face turns away. It never selects identity.

Roster comparison is one matrix multiply against ≤50 cached vectors — microseconds.
Enrollment embeddings are computed once and stored as BLOBs, never recomputed per frame.

---

## 7. Crossing State Machine

The heart of the product. One configurable line per camera, two directions.

```
                 cross in OUT direction
      ┌──────────────────────────────────────┐
      │                                      ▼
   ┌──────┐   ── out_at ──►   ┌─────┐  ── in_at ──►  ┌────────┐
   │ IDLE │                   │ OUT │                │ RETURNED│
   └──────┘  ◄── (reset) ───  └─────┘                └────────┘
      ▲         already open      │
      └────────────────────────────┘  cross IN without open OUT → ignored
```

- **`cross_line(direction)`** fires when a track's centroid crosses the polyline in the
  configured direction, computed via signed side-change of successive positions.
- **Debounce:** the new side must persist for 2 consecutive analysis frames, and
  crossings on the same track within 3 s are ignored. People hover at doorways.
- **Already-OUT guard:** a second OUT while open is queued, not paired — nobody's return
  should be matched to the wrong departure.
- **IN without OUT** is ignored, not an error.
- **Open record at midnight** (or service shutdown) is force-closed at 23:59:59 and
  flagged `inferred_return = 1`, so a missing return never becomes an infinite timer.
- **Duration** = `in_at - out_at` in minutes, computed on write and never stored as a
  mutable aggregate.

**Evidence clips.** A 15 s rolling buffer per camera (10 s pre-event + 5 s post-event)
is written on every *accepted* crossing as MP4/H.264 — NVENC if available, `x264`/
Media Foundation otherwise, muted. Both OUT and IN crossings save a clip.

---

## 8. Data Model

SQLite in WAL mode (one writer, many readers — the correct choice here).
Nothing is ever hard-deleted; attendance records get disputed.

```
employees(id, name, employee_code, department, active, created_at, deleted_at)
enrollments(id, employee_id, face_embedding BLOB, body_embedding BLOB,
            quality_score, captured_at, is_primary)
cameras(id, name, rtsp_url, decode_element, provider, active)
zones(id, camera_id, line_points JSON, direction_out, enabled)
events(id, employee_id, camera_id, event_type['OUT'|'IN'],
       occurred_at, clip_path, confidence, uncertain, track_id)
sessions(id, employee_id, out_event_id, in_event_id, out_at, in_at,
         duration_minutes, inferred_return, date, deleted_at, deleted_reason)
settings(key, value)
audit_log(id, actor, action, target, detail_json, at)
```

Reports read `sessions`. Events are the immutable raw log; sessions are the pairing.
Deleting a session sets `deleted_at`/`deleted_reason` and writes `audit_log` — the
event rows stay.

---

## 9. Dashboard (Electron)

Deliberately constrained to survive a low-end box.

- Exactly **one** `BrowserWindow`. Multi-window is how Electron RAM balloons.
- `backgroundThrottling: false` so the live view updates when the window is behind
  other windows.
- Live view renders a **single MJPEG `<img>` from the service** — never base64 frames
  over IPC. Per-frame `webContents.send` of an image buffer is the most common way
  apps like this die on a constrained machine.
- GPU process capped at ~30 % of system RAM; `sandbox` permissive only where the
  renderer needs it for local media.
- One renderer bundle, no per-panel lazy chunks spawning extra processes.
- Dark theme default (back-office use), light toggle.

**Panels:** Live view (per camera, with the configured line overlaid and crossings
highlighted; a single MJPEG element shown at a time, switching cameras via a selector
rather than running N concurrent decoders in the renderer); Events (live-appending,
filterable); Sessions/records (per-employee,
per-day, with duration and both clips); Reports; Employees & enrollment; Settings;
System health (RSS, thread count, worker states, disk space, log viewer).

**Auth:** dashboard and reports are open all day. Editing or deleting records,
deleting clips, changing zone config, and enrollment require a local password
(set on first run). Nothing payroll-relevant is editable by anyone standing at the
counter.

---

## 10. Records & Retention

- **90-day clip auto-purge**, nightly job: delete clip files older than 90 days, set
  `events.clip_path = NULL` (the event survives; only the video is gone).
- **Manual cleanup** behind the password: "delete clips older than N days", with a
  confirmation and an `audit_log` entry.
- **Disk guard:** if free space drops below 10 GB, older clips are purged ahead of
  schedule and the tray app shows a warning banner.
- **Append-as-it-happens:** accepted events are inserted on the decider thread
  immediately, in their own transaction, so the UI log grows in real time.

---

## 11. Health, Leaks, Resource Management

The user called these out explicitly, so they are first-class requirements.

- Every decoded frame buffer is explicitly released; GStreamer's `drop=true` and the
  bounded Python queues make memory capped by construction.
- **Watchdog thread** in the service, every 60 s: samples RSS and thread count.
  Above the configured ceiling (default 1.5 GB) it logs, raises a tray alert, and
  performs a **supervised worker restart** — the worker restarts without dropping the
  DB or losing already-written clips. In-memory track state is lost; recorded events are not.
- Capture reconnect with exponential backoff (see §5).
- All worker threads are `daemon=False` with explicit `stop()` then
  `join(timeout)` on shutdown, so Ctrl-C and service-stop both clean up.
- Structured JSON-lines logging to `logs\service.log`, rotating at 20 MB × 5 files,
  mirrored into a UI-log viewer panel.
- Track-state GC: tracks unseen for 30 s are dropped; face galleries are capped.

---

## 12. Training Module

A separate lifecycle from the running app — different inputs, different outputs.

```
tools/
  dataset.py       # YOLO-format loading, augmentation, splits
  train_detector.py# fine-tune person detector
  train_reid.py    # body re-ID fine-tune
  train_face.py    # face embedding fine-tune on the enrolled roster
  export.py        # → ONNX; emits provider-matched variants
  eval.py          # mAP / re-ID rank-1 / face TAR@FAR; writes runs/
```

**Bootstrap plan for the detector (no labelled data yet):** run a pretrained COCO
person model over recorded gate footage, accept detections above threshold, correct by
hand, then fine-tune. This is how a gate-tuned detector is produced with a small
labelling budget. Expected effort is called out here so it isn't a surprise:
budget roughly 300–600 hand-corrected person crops for a usable v1 detector.

**Enrollment captures ≠ training data.** Enrollment crops are clean frontal poses;
training needs the hard cases (sideways, masked, backlit, partial occlusion, low light
at closing time). The UI labels enrollment captures separately so the two sets are not
silently mixed.

**Model promotion:** new models export into `models/candidate/`; a person moves them to
`models/active/` after reviewing `eval.py` output. The service reloads on a config flag.

---

## 13. Packaging

- **Service:** Python → single exe via PyInstaller or Nuitka (to be chosen in the plan;
  Nuitka preferred if build time is acceptable, for the memory/speed benefit).
- **Installer:** electron-builder NSIS. At install it chains GStreamer's runtime
  installer (§5.1), then registers the service via `sc.exe create` / WiX.
- **Layout on the target machine:**
  `C:\Program Files\EmployeeTracker\` (app) and
  `C:\ProgramData\EmployeeTracker\` (data, clips, logs, models).
- **Autostart:** the Windows Service starts at boot before logon. The tray app starts
  at user logon (Run key or a scheduled task) and is purely a control surface.

---

## 14. Testing Without RTSP

A hard requirement, since the owner has no camera access yet.

- **Frame-source abstraction.** The pipeline accepts `RtspSource` or `FileSource`.
  CI and development run against a recorded MP4 or a synthetic generator that draws
  moving rectangles crossing the line — the state machine, timing and duration maths are
  fully testable today, before any camera exists.
- **Table-driven tests for `cross_line`:** exact input positions → exact expected
  events, including the debounce, already-OUT, and IN-without-OUT cases.
- **`--simulate` mode** plays a scripted set of crossings end-to-end (service +
  dashboard + clips + records), so the whole product is demonstrable to the employer
  before the camera arrives.
- In-memory or temp-file SQLite for tests; never the live DB.

---

## 15. Open Items — must be resolved during implementation

1. **GStreamer plugin licensing** (§5.1) — verify `gst-plugins-ugly`/`libav` terms before
   any commercial distribution. Confirm at implementation time, do not assume.
2. **Library versions** — YOLO/Ultralytics, InsightFace, ONNX Runtime, GStreamer and
   PyGObject bindings must have their current stable versions pulled from upstream docs
   before any requirement is pinned. Context7 lookups were timing out at spec time.
3. **Software vs hardware verify of the 15 s clip buffer's encoder choice** on a
   machine with no NVIDIA encoder.
4. Whether Nuitka's build time is acceptable on the authoring machine, else PyInstaller.

---

## 16. Phasing

Single spec, phased build — each phase independently verifiable:

1. **Skeleton:** 3-process structure, service/tray/Electron shell, SQLite schema, config,
   autostart, packaging pipeline to a working `.exe`.
2. **Capture + detect + track:** GStreamer source, detector, tracker, synthetic and
   file sources, health/watchdog.
3. **Crossing logic:** state machine, events, sessions, durations, clips — the core
   value, fully tested without a real camera.
4. **Identity:** enrollment flow, roster, face matching, re-ID track maintenance.
5. **Dashboard + reports** (CSV/XLSX/PDF) and enrollment UI.
6. **Training tooling** and model promotion.
