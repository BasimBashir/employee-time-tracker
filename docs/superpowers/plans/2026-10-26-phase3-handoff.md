# Phase 3 Handoff

Phase 3 is complete: line-crossing geometry, the OUT/IN state machine, the JPEG
ring-buffer clip recorder, the decider that glues them to SQLite, zones/clips/
retention API endpoints, and the 23:59 nightly rollover. Full suite: 116 passed,
1 skipped — no camera, no network.

Interfaces Phase 4 (identity) may rely on and must not change:
- `service.decider.Decider(conn, recorder, zone_provider, identity_resolver)`
  - `identity_resolver(track) -> tuple[employee_id, confidence] | None`
  - Returning `None` for a track means "no record at all" — that is the documented
    contract for unenrolled people. Phase 4 replaces the stub resolver with a real one.
- `service.tracker.Track.face_gallery` — list of `(quality_score, embedding_bytes)`,
  capped at 12, sorted best-first. Phase 4 consumes this and nothing else about the track.
- `service.cliprecorder.ClipRecorder(clips_dir, pre_seconds, post_seconds, fps,
  max_seconds)`; `.add_frame(image, timestamp)`; `.capture(event_type, occurred_at,
  employee_id, camera_id) -> Path` — capture blocks until the MP4 is complete
  (pre-roll + post-roll), so the path is always playable on return.
- `service.retention.nightly_retention(cfg, conn)`
- API: GET/POST `/zones`, GET `/clips`, POST `/retention/purge`,
  GET `/events`, GET `/sessions`

Debounce contract (spec §7, enforced in `service/statemachine.py` via the decider):
a side change must persist `debounce_frames` (2) consecutive analysis frames, and
crossings on the same track within `debounce_seconds` (3.0) of the last emitted
crossing are ignored. Crossings are decided from geometry alone — never from a
track disappearing.

Datetimes: everything new stores timezone-aware UTC ISO strings (matches Phase 1's
`db._now()`). `Frame.timestamp` (epoch seconds) is converted with
`datetime.fromtimestamp(t, tz=timezone.utc)`.

Phase 4 adds: enrollment flow, roster CRUD, face embedding, threshold policy.
Do not alter `face_gallery`'s tuple ordering or cap without updating Phase 4's brief.

Deviations from the plan draft (all verified by the tests listed):
1. `test_debounce_drops_rapid_same_track_crossings` rewritten — the plan's version
   was unsatisfiable together with the two-frame persistence test; the rewritten
   test actually seeds a crossing so the 3 s window is genuinely exercised.
2. The decider dispatches only when the state machine confirms a crossing, so the
   spec §7 debounce actually gates recording (the plan draft bypassed it).
3. `ClipRecorder.capture` collects real post-roll and waits for the writer, where
   the plan draft slept without feeding post frames into the file.
4. Side-of-line sign fixed to match the plan's own test table (image coords:
   above a +x line is +1); `cross_direction` resolves a side of 0 by where the
   point lands.
5. Zone/retention/e2e tests insert employee+camera rows first — `zones.camera_id`,
   `events.employee_id` etc. carry FOREIGN KEYs and `connect` enables them.
6. Acceptance soak run in-process over the decider + recorder (900 frames, RSS and
   ring-buffer asserts) instead of a 10-minute pipeline soak against
   `tests/fixtures/synth.mp4`, which does not exist in this worktree; the
   pipeline's own memory behaviour was Phase 2's acceptance gate.
