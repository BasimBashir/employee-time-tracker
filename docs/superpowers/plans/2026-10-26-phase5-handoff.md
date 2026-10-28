# Phase 5 Handoff — Dashboard, Reports, Evidence, Password Gate

Branch: `claude/ponytail-full-4c1a42` (phase 4 tip `41c682e` as base).
Verified 2026-10-28. Full suite: **164 passed, 1 skipped**, no camera required.

## Plan deviations (approved by the operator)

The phase 5 plan was written before the React dashboard existed; executing it
literally would have shipped dead HTML panels and duplicated working reports.
With the operator's approval, the goal and acceptance criteria were kept and
each task mapped onto the real codebase:

- UI lives in the **React renderer** (`ui/src/`), not `ui/renderer/*.html`:
  `CameraPanel.jsx` (MJPEG + zone overlay), clip player inside
  `SessionsTable.jsx`/`SessionRow.jsx`, XLSX + monthly PDF buttons in
  `ReportsPanel.jsx`. The separate reports page became header controls on the
  existing report panel; there is still exactly one `BrowserWindow`.
- `reports/` is a top-level pure package as planned (`query.py`, `daily.py`,
  `monthly.py`; the SessionRow dataclass lives in `query.py`, no `schema.py`).
  `SessionRow` gained `purpose` (break/delivery), and totals split the two —
  the existing daily report's semantics, which the plan predated.
- Guards went on the endpoints that exist: `POST /employees`,
  `DELETE|PATCH /employees/{id}`, `POST /zones`, `POST /retention/purge`,
  `POST /enrollments`, `DELETE|PATCH /sessions/{id}` (the plan's
  `/employees/{id}/deactivate` never existed — soft delete is `DELETE`).
  `POST /auth/set` replaces the plan's implied "set password" flow and
  requires the current password once one exists.
- The MJPEG publisher occupies `Pipeline.on_tracks` (nothing else used it —
  phase 3 never wired a live Decider). `MjpegServer.attach(pipe)` is also
  re-called by `/pipeline/restart`. **When phase 6 wires the live Decider, it
  must compose with the publisher, not replace it.**
- Report retention: `report_retention_days` (default 365) in Config;
  `nightly_retention` purges `*.csv|*.xlsx|*.pdf` from `<data_root>/reports`.
- Report files are written **flat** into `<data_root>/reports/`
  (`attendance_<date>.csv/xlsx`, `summary_<year-month>.pdf`) and served by the
  `/reports` static mount. The plan's test URL for a nested path was wrong;
  flat is simpler and the mount is the whole contract.

## Interfaces Phase 6 may rely on

- `GET /camera/{id}/stream.mjpeg` — multipart MJPEG ≤ 6 fps; placeholder
  frames when no pipeline is running. `GET /camera/{id}/overlay.json` —
  `{line: [[x,y],[x,y]] | null, direction_out, width: 960, height: 540}`
  normalised to 0–1 (zones are defined in the 960×540 analysis frame space).
- `GET /clips/probe?path=` → `{exists, frames, opened, ready}`;
  `GET /clips/download?path=` — both refuse paths outside `clips_dir`.
- `POST /reports/daily` `{date, formats: ["csv","xlsx"]}` → `{files: [abs paths]}`;
  `POST /reports/monthly` `{year_month}` → `{file}`; downloads at
  `/reports/<filename>`. Both require the password when one is set.
- `GET /auth/status` → `{set}`; `POST /auth/unlock` `{password}` → `{ok}`;
  `POST /auth/set` `{password, current?}`. Header: `X-Tracker-Password`.
- `service.password.PasswordGate(conn)` — `.is_set()/.set()/.verify()/.require()`
  (pbkdf2_sha256, 240k iterations, stored in `settings.password_hash`).
- `service.cap.read_timespan_json(path)` (tolerant sidecar reader),
  `service.mjpeg.MjpegServer` (`.publish/.attach/.subscribe/.stop`).
- `GET /sessions?date=` rows now carry `out_clip` / `in_clip` (event clip paths).
- `reports.query` is the only reader of `sessions`/`events` for reporting:
  `sessions_for_date`, `sessions_for_employee`, `employee_daily_totals`,
  `employees`, `events_since`, `SessionRow`. Soft-delete exclusion is enforced
  in exactly this one module.

## UI contract changes

- `ui/src/api.js`: every mutating call now goes through `send()`, which
  attaches `X-Tracker-Password` (from `sessionStorage["pw"]`), prompts once on
  a 401 and retries, and surfaces the service's `detail` message on failure.
  New helpers: `cameraStreamUrl`, `cameraOverlay`, `clipProbeUrl`,
  `clipDownloadUrl`, `genDaily`, `genMonthly`, `reportFileUrl`.
- Deleting a record, re-tagging a trip, or generating an XLSX/PDF prompts for
  the password the first time; it is kept for the browser session only.

## Acceptance gate results (2026-10-28)

| check | result |
|---|---|
| full suite | 164 passed, 1 skipped |
| renderer build (`npm run build`) | clean, 29 modules |
| report totals vs DB (CSV sum, XLSX Summary TOTAL, PDF roster total) | all 95.0 min, exact match |
| soft-deleted session | absent from query/CSV/XLSX/PDF; row kept with `deleted_at`+reason; events untouched (8 rows); audit logged |
| password gate | 401 without/with wrong header, reads open, `POST /auth/set` needs current password |

## Known limits / not verified here

- Live camera verified only via the placeholder path and unit tests; the
  acceptance plan's "real frames at ~6 fps" needs the phase 6 live wiring
  (detect_and_crop placeholder still blocks real identity anyway).
- Electron RAM with the live view open (acceptance step 5) not measured —
  needs the packaged app on real hardware. If it breaches ~500 MB, lower
  `MjpegServer.max_fps` to 4 first.
- Records clip player against a genuinely mid-write clip is covered by
  `probe_clip` unit tests, not by a live `ClipRecorder.capture()` race.

## Model promotion contract for Phase 6 (unchanged from the plan)

- Trained artefacts go to `<data_root>/models/candidate/<name>.onnx`.
- A human moves them to `<data_root>/models/active/<name>.onnx` after reviewing
  eval output.
- File names the running system expects: `detector.onnx`, `face.onnx`,
  `reid.onnx`.
- Service picks up a new active model on restart only.
