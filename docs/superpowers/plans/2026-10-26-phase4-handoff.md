# Phase 4 Handoff — Identity: Enrollment, Roster & Face Matching

Branch: `claude/friendly-nightingale-d169ae` (phase 3 tip `7f9f5fb` fast-forwarded in first).
Verified 2026-10-28. Full suite: **139 passed, 1 skipped**, no camera required.

## Interfaces Phase 5 (dashboard + reports) may rely on

- `GET /events?since=`, `GET /sessions?date=` — event rows now carry `uncertain`
  (1 = identity below the confirm threshold; treat as "probable", not certain)
- `GET /clips?date=` — evidence file paths
- `GET /zones`, `POST /zones` — line configuration
- `GET /employees`, `POST /employees` (→ `{"employee_id": ...}`),
  `PATCH /employees/{id}`, `DELETE /employees/{id}` body `{actor, reason}`
  (soft delete; this is the deactivation endpoint — there is no
  `/employees/{id}/deactivate`, the plan's name, DELETE with a reason replaces it)
- `POST /enrollments` — body `{employee_id, path, source: "file"}`; 400 with a
  `detail` message when the file is unreadable or fewer than 10 poses are usable
- `GET /resolver/status` → `{ready, roster_size, confirm: 0.45, uncertain: 0.35}`,
  `POST /resolver/invalidate`
- `GET /pipeline/status`, `POST /pipeline/restart`
- `GET /health`, `GET /health/rss`
- `POST /retention/purge` — body `{days, actor}`
- `POST /employees/{id}/avatar`, `POST/GET/DELETE /employees/{id}/media[/{name}]`

Reports read `sessions` and `events` directly via the DB, not via new endpoints.
Do not add report endpoints to the service; Phase 5 owns report generation.

## Identity contract (do not break)

- `Resolver.resolve(track) -> (employee_id, confidence, uncertain) | None`;
  the Decider unpacks this 3-tuple and passes `uncertain` into `insert_event`.
- Thresholds: cosine ≥ **0.45** confirms; **0.35–0.45** resolves but `uncertain=1`;
  < **0.35** → `None` (no record at all). A unknown person leaves `events` unchanged —
  verified as the gate's most important assertion.
- The resolver is the ONLY place identity is decided; re-id output never feeds identity.
- `Track.face_gallery` is `list[(quality_score: float, embedding_bytes: bytes)]`,
  capped at 12, best first.
- Roster compare is one matmul (`_vecs @ v`); 1000 resolves over a 50-person roster
  measured **0.023 ms/resolve**.
- Embeddings are raw 2 KB BLOBs (512 × float32), computed once at enrollment.

## Acceptance gate results (2026-10-28)

Run in-process against a synthetic 50-person roster (no camera; `detect_and_crop`
is still a whole-frame placeholder, so live-video enrollment cannot be exercised yet):

| check | result |
|---|---|
| full suite | 139 passed, 1 skipped |
| matching embedding | `(id, 1.000, uncertain=0)` |
| degraded embedding (cos 0.40) | `(id, 0.400, uncertain=1)` — expect this at a glass door with backlight |
| cosine 0.30 | `None` |
| unknown person | resolver `None`, `events` count unchanged (0 → 0) |
| 50-person roster, 1000 resolves | 0.0228 ms/resolve (budget: < 1 ms) |

## Deviations from the plan (decided, not accidents)

1. **Resolver contract is a 3-tuple** `(employee_id, confidence, uncertain)`; the plan's
   decider text was written for a 2-tuple. `uncertain` flows into the `events` row.
2. **Employee CRUD stays in `db.py`**; `roster.py` owns only `save_enrollment`,
   `primary_face`, `roster_embeddings` (active employees only).
3. **No live Decider in `service_main.py`** — phase 3 never wired one, so "attach
   resolver to the decider" is done by tightening the contract + `app.state.resolver`
   in `create_app`. Wiring the real crossing pipeline remains open for phase 5.
4. **`DELETE /employees/{id}`** replaces the plan's `/employees/{id}/deactivate`;
   the richer endpoint already existed.
5. **Enrollment stores ALL poses** (`is_primary=1` best, others `is_primary=0`);
   the resolver compares against every stored vector. Plan's 6 resolver tests were
   written for scalar `ones()` vectors (every cosine = 1.0); tests use deterministic
   unit vectors instead — 2 of the 6 are unsatisfiable as written and were adapted.
6. **`create_app` guards `if ui_dir and ui_dir.is_dir()`** — `find_ui_dir()` returns
   None in fresh checkouts and previously crashed `create_app`.
7. `preload.js` untouched; the React dashboard uses `api.js`.

## Constraints for the real camera (recorded for phase 5 / installation)

**The gate camera is ceiling-mounted 10–20 ft high and looks down steeply; side-view
classification is required.** Consequences:

- `FaceEmbedder.detect_and_crop` is currently a whole-frame placeholder. The real
  detector must tolerate high pitch (steep downward angle) and profile/side faces —
  enrollment already stores every pose precisely so side views land in the roster,
  and the UI's enroll prompt asks for footage from the angles the camera sees.
- A frontal-only detector pipeline will under-enroll and under-match; choose a
  detector/landmark model with profile tolerance before phase 5's live wiring.

## Open items

- Replace `detect_and_crop` whole-frame placeholder with a real face detector +
  alignment (phase 5, with the pitch/profile constraint above).
- Wire the real Decider into the service pipeline so crossings produce rows live.
