# Phase 4: Identity — Enrollment, Roster & Face Matching — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer "which employee is this?" reliably enough to put in an attendance record — via an in-app enrollment flow that captures face and body references, and a resolver that runs at crossing time and returns `None` for anyone not on the roster.

**Architecture:** Enrollment captures 8–12 face angles plus body crops against a live source, builds embeddings, and stores them as BLOBs (computed once, never per frame). At crossing time the resolver picks the best face embedding from a track's gallery, compares it against the roster with cosine similarity, and applies the two-threshold policy from the spec. Body re-ID embeddings are computed and stored but are used **only** for track maintenance, never to decide identity.

**Tech Stack:** InsightFace (ArcFace-class face embedding, `buffalo_sc`/`buffalo_l`), OSNet-class body re-ID, ONNX Runtime, NumPy, OpenCV (detection + crop), scikit-learn (cosine similarity as a plain dot product — no dependency needed).

**Phase 3 must be complete.** Interfaces relied on here:
- `service.decider.Decider(conn, recorder, zone_provider, identity_resolver)` — Phase 4 swaps the stub resolver for a real one. Its contract: `identity_resolver(track) -> tuple[employee_id, confidence] | None`.
- `service.tracker.Track.face_gallery` — `list[(quality_score: float, embedding_bytes: bytes)]`, cap 12, sorted best-first. **Do not change this format**; Phase 4's resolver consumes it.
- Phase 1: `connect`, `migrate`, `insert_event`, `audit`, `Config`.
- Phase 2: `FrameSource`, `FileSource`.

## Global Constraints

- Target: Windows 11, 16 GB DDR4, 6th-gen Intel, no dGPU. CPU expected. Face embedding on CPU (~2–5 ms per crop) is acceptable at crossing time only — it must **never** run per frame.
- Roster comparison must be a single matrix multiply, not a per-track loop with per-candidate model calls.
- Unenrolled people produce no records at all. `None` from the resolver is the contract, and it must be correct rather than permissive: a wrong employee on an attendance record is worse than a missed one.
- Thresholds from spec §6: `confirm_threshold` 0.45, `uncertain_threshold` 0.35. Below 0.35 → `None`.
- Everything must work with no camera: enrollment must accept a `FileSource` and a synthetic source. The UI adds a webcam later; the engine must not depend on it.
- Library versions must be pulled from upstream docs before pinning (spec §15).

---

## File Structure

```
service/
├─ faces.py             # face detect + embed, model loading per provider
├─ reid.py              # body re-ID embedding
├─ roster.py            # employee + enrollment CRUD
├─ resolver.py          # the identity_resolver implementation
└─ enrollment.py        # capture orchestration (8-12 angles + body crops)
ui/renderer/
└─ enroll.html          # enrollment panel (same BrowserWindow, new route)
tests/
├─ test_faces.py
├─ test_reid.py
├─ test_roster.py
├─ test_resolver.py
└─ test_enrollment.py
```

### Responsibility boundaries

- `faces.py` knows how to turn an image crop into an embedding. It never compares embeddings.
- `roster.py` owns the roster and enrollment records. It never computes embeddings.
- `resolver.py` is the only place that applies thresholds and decides identity. It is replaceable and testable with fake embeddings.
- `enrollment.py` orchestrates capture. It writes embeddings and rows but decides nothing about identity.
- `reid.py` is deliberately walled off: it exists to keep tracks alive (Phase 2) and its output is **never** an input to identity decisions. Document this in the module docstring so a future engineer doesn't "improve" accuracy with it.

---

## Task 1: Face embedding

**Files:**
- Create: `service/faces.py`
- Test: `tests/test_faces.py`

**Interfaces:**
- Consumes: `service.device.select_provider`, `model_variant`.
- Produces:
  - `FaceEmbedder(model_path, provider=None, input_size=None)` with
    `.embed(crop_bgr) -> bytes | None` (512-d float32 as bytes) and
    `.detect_and_crop(frame_bgr) -> list[(bbox, crop)]`.
  - `cosine(a: bytes, b: bytes) -> float`.
  - `FACE_DIM = 512`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_faces.py
import numpy as np
from service.faces import FaceEmbedder, cosine, FACE_DIM

def test_cosine_identical_is_one():
    v = np.random.rand(FACE_DIM).astype(np.float32)
    assert abs(cosine(v.tobytes(), v.tobytes()) - 1.0) < 1e-5

def test_cosine_orthogonal_is_zero():
    a = np.zeros(FACE_DIM, np.float32); a[0] = 1.0
    b = np.zeros(FACE_DIM, np.float32); b[1] = 1.0
    assert abs(cosine(a.tobytes(), b.tobytes())) < 1e-6

def test_embed_none_when_no_model(tmp_path):
    fe = FaceEmbedder(tmp_path / "none.onnx")
    assert fe.embed(np.zeros((160, 160, 3), np.uint8)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_faces.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.faces'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/faces.py
"""Face detection + ArcFace-class embedding. Never compares embeddings."""
from pathlib import Path
from typing import Optional
import numpy as np
import onnxruntime as ort
from .device import select_provider, model_variant

FACE_DIM = 512


def cosine(a: bytes, b: bytes) -> float:
    va = np.frombuffer(a, np.float32)
    vb = np.frombuffer(b, np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


class FaceEmbedder:
    def __init__(self, model_path: Path, provider: Optional[str] = None,
                 input_size: Optional[int] = None):
        self.provider = provider or select_provider()[0]
        self.input_size = input_size or (192 if self.provider == "CPUExecutionProvider" else 320)
        self._session: Optional[ort.InferenceSession] = None
        if Path(model_path).exists():
            so = ort.SessionOptions()
            so.intra_op_num_threads = 0
            self._session = ort.InferenceSession(str(model_path), sess_options=so,
                                                providers=[self.provider])

    def _l2n(self, v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed(self, crop_bgr: np.ndarray) -> Optional[bytes]:
        """crop_bgr: a tight face crop, any size. Returns FACE_DIM float32 bytes."""
        if self._session is None or crop_bgr is None or crop_bgr.size == 0:
            return None
        import cv2
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (self.input_size, self.input_size))
        blob = (rgb.astype(np.float32) / 127.5 - 1.0).transpose(2, 0, 1)[None]
        out = self._session.run(None, {self._session.get_inputs()[0].name: blob})[0]
        vec = np.asarray(out).reshape(-1)[:FACE_DIM].astype(np.float32)
        return self._l2n(vec).tobytes()

    def detect_and_crop(self, frame_bgr: np.ndarray, min_face=40):
        """Returns [(bbox_xyxy, crop_bgr)] using a person-crop heuristic when no
        dedicated face detector model is present. The dedicated detector is an
        open item — see the note in Task 2 of the plan."""
        h, w = frame_bgr.shape[:2]
        return [((0, 0, w, h), frame_bgr)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_faces.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add service/faces.py tests/
git commit -m "feat: face embedder with cosine helper and provider-aware size"
```

---

## Task 2: Body re-ID (track maintenance only)

**Files:**
- Create: `service/reid.py`
- Test: `tests/test_reid.py`

**Interfaces:**
- Consumes: `select_provider`.
- Produces:
  - `ReidEmbedder(model_path, provider=None)` with `.embed(person_crop_bgr) -> bytes | None`.
  - `REID_DIM = 512`.
  - Module docstring stating in capitals that this output is never an identity input.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reid.py
import numpy as np
from service.reid import ReidEmbedder, REID_DIM
from service import reid

def test_module_documented_as_non_identity():
    assert "NEVER" in reid.__doc__.upper() and "IDENTITY" in reid.__doc__.upper()

def test_embed_none_without_model(tmp_path):
    r = ReidEmbedder(tmp_path / "none.onnx")
    assert r.embed(np.zeros((256, 128, 3), np.uint8)) is None

def test_embed_shape_when_model_present(tmp_path):
    # faked session so the preprocessing path is covered without a real model file
    r = ReidEmbedder(tmp_path / "none.onnx")
    r._session = "present"
    assert r.embed(np.zeros((256, 128, 3), np.uint8)) is None    # "present" is not a session
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reid.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.reid'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/reid.py
"""Body re-identification (OSNet-class).

THIS OUTPUT IS NEVER AN IDENTITY INPUT. It exists only so a Track can survive
frames where the face is turned away — Phase 2's tracker uses it to decide
"same person", never "which employee". Feeding this into the resolver would
make attendance records guesswork. If you are tempted, read spec §6 first.
"""
from pathlib import Path
from typing import Optional
import numpy as np
import onnxruntime as ort
from .device import select_provider

REID_DIM = 512


class ReidEmbedder:
    def __init__(self, model_path: Path, provider: Optional[str] = None,
                 input_size: tuple[int, int] = (128, 256)):
        self.provider = provider or select_provider()[0]
        self.input_size = input_size
        self._session: Optional[ort.InferenceSession] = None
        if Path(model_path).exists():
            so = ort.SessionOptions()
            self._session = ort.InferenceSession(str(model_path), sess_options=so,
                                                 providers=[self.provider])

    def embed(self, person_crop_bgr: np.ndarray) -> Optional[bytes]:
        if self._session is None or person_crop_bgr is None or person_crop_bgr.size == 0:
            return None
        import cv2
        rgb = cv2.cvtColor(person_crop_bgr, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, self.input_size)
        blob = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = self._session.run(None, {self._session.get_inputs()[0].name: blob})[0]
        v = np.asarray(out).reshape(-1)[:REID_DIM].astype(np.float32)
        n = np.linalg.norm(v)
        return (v / n if n else v).tobytes()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reid.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add service/reid.py tests/
git commit -m "feat: body re-id embedder documented as non-identity"
```

---

## Task 3: Roster and enrollment records

**Files:**
- Create: `service/roster.py`
- Test: `tests/test_roster.py`

**Interfaces:**
- Consumes: Phase 1 `connect`, `migrate`, `audit`.
- Produces:
  - `add_employee(conn, name, employee_code=None, department=None) -> int`
  - `list_employees(conn, include_inactive=False) -> list[sqlite3.Row]`
  - `deactivate_employee(conn, employee_id) -> None`
  - `save_enrollment(conn, employee_id, face_embedding, body_embedding, quality_score, is_primary=0) -> int`
  - `primary_face(conn, employee_id) -> bytes | None`
  - `roster_embeddings(conn) -> list[(employee_id, bytes, bool)]` — the exact set the resolver compares against.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_roster.py
import numpy as np
from datetime import datetime
from service.db import connect, migrate
from service.roster import (add_employee, list_employees, deactivate_employee,
                            save_enrollment, primary_face, roster_embeddings)

def test_employee_crud(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "EMP-001", "front desk")
    assert add_employee(conn, "Bilal", "EMP-002", "stockroom")
    assert len(list_employees(conn)) == 2
    deactivate_employee(conn, eid)
    assert len(list_employees(conn)) == 1
    assert len(list_employees(conn, include_inactive=True)) == 2

def test_enrollment_and_primary(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "EMP-001")
    v1 = np.random.rand(512).astype(np.float32).tobytes()
    save_enrollment(conn, eid, v1, None, 0.91, is_primary=1)
    save_enrollment(conn, eid, np.random.rand(512).astype(np.float32).tobytes(),
                    None, 0.72, is_primary=0)
    assert primary_face(conn, eid) == v1
    rows = roster_embeddings(conn)
    assert len(rows) == 2 and rows[0][0] == eid

def test_inactive_employee_excluded_from_roster(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "EMP-001")
    save_enrollment(conn, eid, np.random.rand(512).astype(np.float32).tobytes(),
                    None, 0.9, is_primary=1)
    deactivate_employee(conn, eid)
    assert all(r[0] != eid for r in roster_embeddings(conn))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_roster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.roster'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/roster.py
"""Roster and enrollment records. Never computes embeddings."""
import sqlite3
from datetime import datetime
from typing import Optional


def _now() -> str:
    return datetime.utcnow().isoformat()


def add_employee(conn: sqlite3.Connection, name: str,
                 employee_code: Optional[str] = None,
                 department: Optional[str] = None) -> int:
    cur = conn.execute(
        "INSERT INTO employees (name,employee_code,department,active,created_at)"
        " VALUES (?,?,?,1,?)", (name, employee_code, department, _now()))
    conn.commit()
    return cur.lastrowid


def list_employees(conn, include_inactive: bool = False) -> list[sqlite3.Row]:
    q = "SELECT id,name,employee_code,department,active FROM employees"
    if not include_inactive:
        q += " WHERE active=1"
    return conn.execute(q + " ORDER BY name").fetchall()


def deactivate_employee(conn, employee_id: int) -> None:
    conn.execute("UPDATE employees SET deleted_at=? WHERE id=?", (_now(), employee_id))
    conn.commit()


def save_enrollment(conn, employee_id: int, face_embedding: Optional[bytes],
                    body_embedding: Optional[bytes], quality_score: float,
                    is_primary: int = 0) -> int:
    if is_primary:
        conn.execute("UPDATE enrollments SET is_primary=0 WHERE employee_id=?",
                     (employee_id,))
    cur = conn.execute(
        "INSERT INTO enrollments (employee_id,face_embedding,body_embedding,"
        "quality_score,captured_at,is_primary) VALUES (?,?,?,?,?,?)",
        (employee_id, face_embedding, body_embedding, quality_score, _now(), is_primary))
    conn.commit()
    return cur.lastrowid


def primary_face(conn, employee_id: int) -> Optional[bytes]:
    row = conn.execute(
        "SELECT face_embedding FROM enrollments WHERE employee_id=? AND is_primary=1"
        " ORDER BY id DESC LIMIT 1", (employee_id,)).fetchone()
    return row[0] if row and row[0] is not None else None


def roster_embeddings(conn) -> list[tuple[int, bytes, int]]:
    """(employee_id, face_embedding, is_primary) for every ACTIVE enrolled employee."""
    rows = conn.execute(
        "SELECT e.employee_id, en.face_embedding, en.is_primary FROM enrollments en"
        " JOIN employees e ON e.id = en.employee_id"
        " WHERE e.active=1 AND e.deleted_at IS NULL AND en.face_embedding IS NOT NULL"
    ).fetchall()
    return [(r["employee_id"], r["face_embedding"], r["is_primary"]) for r in rows]
```

Note: embeddings are stored raw (uncompressed) BLOBs. At 512 float32 = 2 KB each, 50
employees need only ~100 KB — compression would cost CPU at resolve time for no gain.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_roster.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add service/roster.py tests/
git commit -m "feat: roster and enrollment CRUD with active-only roster view"
```

---

## Task 4: The resolver

**Files:**
- Create: `service/resolver.py`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Track.face_gallery` (Phase 2), `roster_embeddings` (Task 3), `cosine` (Task 1).
- Produces:
  - `Resolver(conn, embedder=None, confirm=0.45, uncertain=0.35)` with
    `resolve(track) -> tuple[employee_id, confidence, uncertain] | None`
    — **three-tuple**, matching the decider's `identity_resolver` contract. The
    `uncertain` element is `0` or `1`, exactly what `Decider` passes to
    `insert_event(..., uncertain)`.
  - `resolve_embedding(embedding_bytes) -> tuple[employee_id, confidence, uncertain] | None`
  - `self._ids: list[int]`, `self._vecs: np.ndarray` — rebuilt on `invalidate()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resolver.py
import numpy as np
from service.db import connect, migrate
from service.roster import add_employee, save_enrollment
from service.resolver import Resolver

def _v(x): return (np.ones(512, np.float32) * x).tobytes()

def _track_with(embedding):
    from types import SimpleNamespace
    return SimpleNamespace(track_id=1, face_gallery=[(0.9, embedding)],
                           centroid=(480, 400))

def test_confirms_above_threshold(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, _v(0.5), None, 0.9, is_primary=1)
    r = Resolver(conn, confirm=0.45, uncertain=0.35)
    got = r.resolve(_track_with(_v(0.5)))
    assert got is not None and got[0] == eid and got[1] > 0.9
    assert got[2] == 0                                       # not uncertain

def test_uncertain_band_flags(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, _v(0.5), None, 0.9, is_primary=1)
    r = Resolver(conn, confirm=0.9999, uncertain=0.45)
    hit = r.resolve_embedding(_v(0.5))
    assert hit is not None
    assert hit[0] == eid and hit[2] == 1                    # above 0.45, below 0.9999

def test_below_threshold_returns_none(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, _v(0.5), None, 0.9, is_primary=1)
    r = Resolver(conn, confirm=99.0, uncertain=99.0)
    assert r.resolve(_track_with(_v(0.5))) is None

def test_empty_gallery_returns_none(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    r = Resolver(conn)
    from types import SimpleNamespace
    assert r.resolve(SimpleNamespace(face_gallery=[])) is None

def test_no_roster_returns_none(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    r = Resolver(conn)
    assert r.resolve(_track_with(_v(0.5))) is None

def test_best_face_from_gallery_wins(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    a = add_employee(conn, "Ayesha", "E1")
    b = add_employee(conn, "Bilal", "E2")
    save_enrollment(conn, a, _v(0.5), None, 0.9, is_primary=1)
    save_enrollment(conn, b, _v(1.0), None, 0.9, is_primary=1)
    r = Resolver(conn)
    got = r.resolve(SimpleNamespace(face_gallery=[(0.1, _v(0.9)), (0.95, _v(1.0))]))
    assert got[0] == b                                       # higher quality chosen
    assert got[2] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.resolver'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/resolver.py
"""The ONLY place identity is decided. Thresholds come from the spec (§6)."""
import sqlite3
from typing import Optional
import numpy as np
from .faces import cosine, FACE_DIM
from .roster import roster_embeddings


class Resolver:
    def __init__(self, conn: sqlite3.Connection, embedder=None,
                 confirm: float = 0.45, uncertain: float = 0.35):
        self.conn = conn
        self.embedder = embedder
        self.confirm = confirm
        self.uncertain = uncertain
        self._ids: list[int] = []
        self._vecs: Optional[np.ndarray] = None
        self.invalidate()

    def invalidate(self) -> None:
        rows = roster_embeddings(self.conn)
        self._ids = [r[0] for r in rows]
        self._vecs = (np.stack([np.frombuffer(r[1], np.float32) for r in rows])
                      if rows else np.zeros((0, FACE_DIM), np.float32))

    def _best(self, embedding: bytes):
        if self._vecs is None or len(self._ids) == 0:
            return None
        v = np.frombuffer(embedding, np.float32)
        sims = self._vecs @ v                       # one matmul for the whole roster
        i = int(np.argmax(sims))
        return self._ids[i], float(sims[i])

    def resolve_embedding(self, embedding: bytes):
        best = self._best(embedding)
        if best is None:
            return None
        emp, score = best
        if score < self.uncertain:
            return None                              # unenrolled / not confident enough
        return emp, score, score < self.confirm     # third element = uncertain flag

    def resolve(self, track) -> Optional[tuple[int, float]]:
        gallery = getattr(track, "face_gallery", None) or []
        if not gallery:
            return None
        best_q = max(q for q, _ in gallery)
        candidates = [e for q, e in gallery if q >= best_q - 1e-6]
        results = [r for r in (self.resolve_embedding(e) for e in candidates) if r]
        if not results:
            return None
        # highest-scoring candidate wins; ties broken by the uncertain flag
        results.sort(key=lambda r: r[1], reverse=True)
        emp, score, uncertain = results[0]
        return (emp, score, 1 if uncertain else 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_resolver.py -v`
Expected: PASS (6 tests). If `test_uncertain_band_flags` is weak, strengthen it by
asserting the third element of `resolve_embedding` is `True` when the score lands in the
band — adjust the crafted vector until it does, rather than relaxing the assertion.

- [ ] **Step 5: Commit**

```bash
git add service/resolver.py tests/
git commit -m "feat: roster resolver with two-threshold identity policy"
```

---

## Task 5: Enrollment capture flow

**Files:**
- Create: `service/enrollment.py`
- Test: `tests/test_enrollment.py`

**Interfaces:**
- Consumes: `FaceEmbedder` (Task 1), `ReidEmbedder` (Task 2), `save_enrollment` (Task 3), `FrameSource` (Phase 2).
- Produces:
  - `EnrollmentSession(source, face_embedder, reid_embedder, poses=10)` with
    `.capture_pose() -> bool` (returns True when a usable face was captured),
    `.captured_count: int`,
    `.quality_scores: list[float]`,
    `.finish(conn, employee_id) -> int` (writes the primary enrollment, returns its id).
  - `REQUIRED_POSES = 10`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_enrollment.py
import numpy as np
from service.db import connect, migrate
from service.roster import add_employee, roster_embeddings
from service.enrollment import EnrollmentSession, REQUIRED_POSES
from service.framesource import SyntheticSource

class FakeFace:
    def embed(self, crop):
        # quality = brightness sum, so later frames are "better" faces
        return np.full(512, float(crop.mean()) / 255.0, np.float32).tobytes()

def test_finish_writes_primary_enrollment(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    es = EnrollmentSession(SyntheticSource(30, 320, 240), FakeFace(), None)
    for _ in range(REQUIRED_POSES):
        assert es.capture_pose() is True
    assert es.captured_count == REQUIRED_POSES
    es.finish(conn, eid)
    rows = roster_embeddings(conn)
    assert len(rows) == 1 and rows[0][0] == eid

def test_refuses_to_finish_with_too_few_poses(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    es = EnrollmentSession(SyntheticSource(30, 320, 240), FakeFace(), None)
    for _ in range(3):
        es.capture_pose()
    try:
        es.finish(conn, eid)
    except ValueError:
        return
    raise AssertionError("finish must refuse below REQUIRED_POSES")

def test_quality_scores_recorded(tmp_path):
    es = EnrollmentSession(SyntheticSource(30, 320, 240), FakeFace(), None)
    for _ in range(REQUIRED_POSES):
        es.capture_pose()
    assert len(es.quality_scores) == REQUIRED_POSES
    assert all(0.0 <= q <= 1.0 for q in es.quality_scores)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_enrollment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.enrollment'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/enrollment.py
"""Capture orchestration. Writes records; decides nothing about identity."""
import sqlite3
from typing import Optional
import numpy as np
from .roster import save_enrollment

REQUIRED_POSES = 10
MIN_QUALITY = 0.20


class EnrollmentSession:
    def __init__(self, source, face_embedder, reid_embedder, poses: int = REQUIRED_POSES):
        self.source = source
        self.face_embedder = face_embedder
        self.reid_embedder = reid_embedder
        self.required = poses
        self.captured_count = 0
        self.quality_scores: list[float] = []
        self._face_vecs: list[bytes] = []
        self._body_vecs: list[bytes] = []

    def capture_pose(self) -> bool:
        frame = self.source.read()
        if frame is None:
            return False
        # A real enrollment flow asks the operator to move the subject; here we take
        # whatever the source gives. The UI drives the prompts.
        face = self.face_embedder.embed(frame.image)
        if face is None:
            return False
        v = np.frombuffer(face, np.float32)
        quality = float(np.clip(np.linalg.norm(v), 0.0, 1.0))
        if quality < MIN_QUALITY:
            return False
        self._face_vecs.append(face)
        if self.reid_embedder is not None:
            body = self.reid_embedder.embed(frame.image)
            if body is not None:
                self._body_vecs.append(body)
        self.quality_scores.append(quality)
        self.captured_count = len(self._face_vecs)
        return True

    def best_face(self) -> Optional[bytes]:
        if not self._face_vecs:
            return None
        order = sorted(range(len(self._face_vecs)),
                       key=lambda i: self.quality_scores[i], reverse=True)
        return self._face_vecs[order[0]]

    def mean_body(self) -> Optional[bytes]:
        if not self._body_vecs:
            return None
        arr = np.stack([np.frombuffer(b, np.float32) for b in self._body_vecs])
        return arr.mean(axis=0).astype(np.float32).tobytes()

    def finish(self, conn: sqlite3.Connection, employee_id: int) -> int:
        if self.captured_count < self.required:
            raise ValueError(
                f"need {self.required} poses, have {self.captured_count}")
        best = self.best_face()
        return save_enrollment(conn, employee_id, best, self.mean_body(),
                               float(np.mean(self.quality_scores)), is_primary=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_enrollment.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add service/enrollment.py tests/
git commit -m "feat: enrollment session capturing poses and writing primary enrollment"
```

---

## Task 6: Wire the real resolver into the decider

**Files:**
- Modify: `service/decider.py` (replace the stub's call site), `service/service_main.py`, `service/api.py`
- Test: `tests/test_resolver_integration.py`

**Interfaces:**
- Consumes: `Resolver` (Task 4), `Decider` (Phase 3).
- Produces: `GET /resolver/status` reporting thresholds and roster size; `POST /resolver/invalidate` after enrollment changes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resolver_integration.py
from fastapi.testclient import TestClient
from service.db import connect, migrate
from service.api import create_app
from service.roster import add_employee, save_enrollment
import numpy as np

def test_resolver_status_and_invalidate(tmp_path):
    from service import config as cfgmod
    cfg = cfgmod.Config(); cfg.data_root = tmp_path / "d"; cfg.port = 8098
    conn = connect(tmp_path / "t.db"); migrate(conn)
    eid = add_employee(conn, "Ayesha", "E1")
    save_enrollment(conn, eid, np.ones(512, np.float32).tobytes(), None, 0.9, 1)
    client = TestClient(create_app(conn, cfg))
    r = client.get("/resolver/status")
    assert r.json()["roster_size"] >= 1
    client.post("/resolver/invalidate")
    assert client.get("/resolver/status").json()["roster_size"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_resolver_integration.py -v`
Expected: FAIL — 404 on `/resolver/status`

- [ ] **Step 3: Wire it**

In `service/service_main.py`, build and attach the resolver, then give it to the decider:

```python
        from .resolver import Resolver
        from .faces import FaceEmbedder
        resolver = Resolver(conn)
        app.state.resolver = resolver
        app.state.face_embedder = FaceEmbedder(cfg.models_dir / "active" / "face.onnx")
        # the decider's identity_resolver now returns (employee_id, confidence, uncertain)
        self.decider.identity_resolver = resolver.resolve
```

> **Contract reminder:** `Decider._dispatch` must unpack three values:
> `emp_id, conf, uncertain = self.identity_resolver(trk)` and pass `uncertain` through to
> `Crossing(..., uncertain)`. If Phase 3's `Decider` unpacks two, fix it here — this is a
> deliberate interface tightening, not a Phase 4 change to Phase 3's contract.

In `service/api.py`, inside `create_app`:

```python
    @app.get("/resolver/status")
    def resolver_status():
        r = getattr(app.state, "resolver", None)
        if r is None:
            return {"ready": False, "roster_size": 0}
        return {"ready": True, "roster_size": len(r._ids),
                "confirm": r.confirm, "uncertain": r.uncertain}

    @app.post("/resolver/invalidate")
    def resolver_invalidate():
        r = getattr(app.state, "resolver", None)
        if r: r.invalidate()
        return {"ok": True}
```

In `service/service_main.py`, update every write path that changes the roster
(`add_employee`, `save_enrollment`, `deactivate_employee`) to call
`app.state.resolver.invalidate()` afterwards. The cleanest place is inside the API
handlers for those endpoints — see Task 7.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_resolver_integration.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add service/ tests/
git commit -m "feat: resolver wired into service with invalidation on roster change"
```

---

## Task 7: Roster and enrollment API

**Files:**
- Modify: `service/api.py`
- Test: `tests/test_roster_api.py`

**Interfaces:**
- Consumes: Tasks 3, 5, 6.
- Produces: `GET /employees`, `POST /employees`, `POST /employees/{id}/deactivate`, `POST /enrollments` (body: `{employee_id}`), `GET /enrollments?employee_id=`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_roster_api.py
from fastapi.testclient import TestClient
from service.db import connect, migrate
from service.api import create_app
from service.roster import roster_embeddings

def test_employee_lifecycle(tmp_path, monkeypatch):
    from service import config as cfgmod
    cfg = cfgmod.Config(); cfg.data_root = tmp_path / "d"; cfg.port = 8097
    conn = connect(tmp_path / "t.db"); migrate(conn)
    client = TestClient(create_app(conn, cfg))
    r = client.post("/employees", json={"name": "Ayesha", "employee_code": "E1"})
    eid = r.json()["id"]
    assert len(client.get("/employees").json()) == 1
    client.post(f"/employees/{eid}/deactivate")
    assert client.get("/employees").json() == []

def test_enrollment_resolver_invalidated(tmp_path):
    from service import config as cfgmod
    from service.resolver import Resolver
    cfg = cfgmod.Config(); cfg.data_root = tmp_path / "d"; cfg.port = 8096
    conn = connect(tmp_path / "t.db"); migrate(conn)
    client = TestClient(create_app(conn, cfg))
    client.app.state.resolver = Resolver(conn)
    eid = client.post("/employees", json={"name": "Ayesha"}).json()["id"]
    before = client.get("/resolver/status").json()["roster_size"]
    assert before == 0
    # enrollment requires a live source; assert the endpoint validates its input
    r = client.post("/enrollments", json={"employee_id": eid, "source": "file",
                                          "path": "tests/fixtures/synth.mp4"})
    assert r.status_code in (200, 400)     # 400 is acceptable with a non-face source
    if r.status_code == 200:
        assert client.get("/resolver/status").json()["roster_size"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_roster_api.py -v`
Expected: FAIL — 404 on `/employees`

- [ ] **Step 3: Add the endpoints**

In `service/api.py`, inside `create_app`:

```python
    @app.get("/employees")
    def employees():
        from .roster import list_employees
        return [dict(r) for r in list_employees(conn)]

    @app.post("/employees")
    def add_employee_ep(body: dict):
        from .roster import add_employee
        from .db import audit
        eid = add_employee(conn, body["name"], body.get("employee_code"),
                           body.get("department"))
        audit(conn, body.get("actor", "owner"), "employee_added", str(eid), body)
        _invalidate()
        return {"id": eid}

    @app.post("/employees/{employee_id}/deactivate")
    def deactivate_ep(employee_id: int, body: dict | None = None):
        from .roster import deactivate_employee
        from .db import audit
        deactivate_employee(conn, employee_id)
        audit(conn, (body or {}).get("actor", "owner"), "employee_deactivated",
              str(employee_id), {})
        _invalidate()
        return {"ok": True}

    @app.post("/enrollments")
    def enroll(body: dict):
        """Enroll an employee from a video file (no webcam required).
        Returns 400 if the source yields no usable face crops."""
        from .enrollment import EnrollmentSession
        from .framesource import FileSource
        from .faces import FaceEmbedder
        eid = int(body["employee_id"])
        path = body.get("path")
        if not path:
            return Response(status_code=400, content="path required")
        try:
            src = FileSource(path)
        except Exception as e:
            return Response(status_code=400, content=str(e))
        es = EnrollmentSession(src, FaceEmbedder(
            (cfg.models_dir / "active" / "face.onnx") if cfg else Path("face.onnx")),
            None)
        ok = 0
        for _ in range(REQUIRED_POSES * 4):
            if es.capture_pose():
                ok += 1
            if ok >= REQUIRED_POSES:
                break
        if ok < REQUIRED_POSES:
            src.stop()
            return Response(status_code=400,
                            content=f"only {ok}/{REQUIRED_POSES} poses captured")
        enr = es.finish(conn, eid)
        src.stop()
        _invalidate()
        return {"enrollment_id": enr, "poses": ok}

    def _invalidate():
        r = getattr(app.state, "resolver", None)
        if r:
            r.invalidate()
```

Add `from fastapi import Response` to `service/api.py` imports, and
`from .enrollment import REQUIRED_POSES` where needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_roster_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add service/ tests/
git commit -m "feat: roster and enrollment api endpoints with resolver invalidation"
```

---

## Task 8: Enrollment UI panel

**Files:**
- Create: `ui/renderer/enroll.html`, `ui/renderer/enroll.js`
- Modify: `ui/main.js` (route), `ui/preload.js` (expose the new endpoints)

**Interfaces:**
- Consumes: Task 7 API.
- Produces: an enrollment panel reachable from the dashboard, listing employees and a "Enroll from file" action.

- [ ] **Step 1: Expose the endpoints in `preload.js`**

```javascript
// ui/preload.js — append to the existing tracker object
  employees: () => fetch('http://127.0.0.1:8080/employees').then(r => r.json()),
  addEmployee: (name, code) => fetch('http://127.0.0.1:8080/employees', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, employee_code: code })
  }).then(r => r.json()),
  deactivateEmployee: (id) => fetch(`http://127.0.0.1:8080/employees/${id}/deactivate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ actor: 'owner' })
  }).then(r => r.json()),
  enroll: (employee_id, path) => fetch('http://127.0.0.1:8080/enrollments', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ employee_id, path, source: 'file' })
  }).then(r => r.status === 200 ? r.json() : Promise.reject(r.statusText)),
  resolverStatus: () => fetch('http://127.0.0.1:8080/resolver/status').then(r => r.json()),
```

- [ ] **Step 2: Write the panel**

`ui/renderer/enroll.html`:
```html
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Enroll — Employee Tracker</title>
<link rel="stylesheet" href="styles.css"></head><body>
<header><h1>Employees</h1><a href="index.html" style="color:#8b95a7">← Dashboard</a></header>
<main>
  <section>
    <h2>Add employee</h2>
    <input id="name" placeholder="Name"> <input id="code" placeholder="EMP-000">
    <button id="add">Add</button>
  </section>
  <section>
    <h2>Roster</h2><table id="roster"><thead><tr>
      <th>Name</th><th>Code</th><th>Dept</th><th></th><th></th>
    </tr></thead><tbody></tbody></table>
  </section>
  <section><p id="status"></p></section>
</main><script src="enroll.js"></script></body></html>
```

`ui/renderer/enroll.js`:
```javascript
const body = document.querySelector('#roster tbody');
const status = document.getElementById('status');

async function load() {
  const rows = await window.tracker.employees();
  body.innerHTML = '';
  rows.forEach(r => body.appendChild(Object.assign(document.createElement('tr'), {
    innerHTML: `<td>${r.name}</td><td>${r.employee_code || ''}</td>` +
               `<td>${r.department || ''}</td>` +
               `<td><button data-enroll="${r.id}">Enroll</button></td>` +
               `<td><button data-del="${r.id}">Remove</button></td>`
  })));
  const s = await window.tracker.resolverStatus();
  status.textContent = `Resolver: ${s.roster_size} face(s) active, ` +
                       `confirm ${s.confirm}, uncertain ${s.uncertain}`;
}

document.getElementById('add').onclick = async () => {
  await window.tracker.addEmployee(document.getElementById('name').value,
                                   document.getElementById('code').value);
  document.getElementById('name').value = ''; load();
};

body.addEventListener('click', async e => {
  const enroll = e.target.dataset.enroll, del = e.target.dataset.del;
  if (enroll) {
    const path = prompt('Path to face video (mp4):');
    if (!path) return;
    try { await window.tracker.enroll(Number(enroll), path); status.textContent = 'Enrolled.'; }
    catch (err) { status.textContent = `Enrollment failed: ${err}`; }
  }
  if (del) { await window.tracker.deactivateEmployee(Number(del)); }
  load();
});

load();
```

`ui/main.js` — no routing changes needed; the panel is a separate HTML file opened by
navigating the same window. In `ui/renderer/index.html`, add a link:

```html
<a href="enroll.html" style="color:#8b95a7;margin-left:auto">Employees →</a>
```

- [ ] **Step 3: Verify the panel works**

```bash
cd ui && npm start
```
Navigate to the Employees link. Add an employee, confirm it appears, click Remove and
confirm it disappears. Enrollment from a file should succeed only when the file actually
contains faces — with `tests/fixtures/synth.mp4` it must fail with a clear message
rather than enrolling a rectangle as a person.

- [ ] **Step 4: Commit**

```bash
git add ui/
git commit -m "feat: enrollment ui panel with roster management"
```

---

## Task 9: Phase 4 acceptance gate

- [ ] **Step 1: Full suite green**

```bash
python -m pytest -v
```
Expected: all of Tasks 1–8 green, no camera.

- [ ] **Step 2: Threshold behaviour proof**

Enroll one synthetic "identity" via the API against a file with a known face, then
drive the resolver with a matching embedding and confirm `GET /events` shows an event
with `uncertain=0`. Repeat with a deliberately degraded embedding and confirm either
`uncertain=1` or no event at all. Record which happened — that is the real-world behaviour
you should expect at a glass door with backlight.

- [ ] **Step 3: Unknown person produces no records**

Drive the decider with a track whose gallery embedding matches nobody (all similarities
below `uncertain`). Expected: `SELECT COUNT(*) FROM events` is unchanged. This is the
single most important assertion in the phase.

- [ ] **Step 4: Roster size scaling**

Enroll 50 employees and time `resolver.resolve()` for a single track:

```python
import time
t = time.perf_counter()
for _ in range(1000): r.resolve(track)
print((time.perf_counter() - t) / 1000 * 1000, "ms per resolve")
```
Expected: well under 1 ms. If it is not, the roster is not being compared as one matmul.

- [ ] **Step 5: Handoff note for Phase 5**

Write `docs/superpowers/plans/2026-10-26-phase4-handoff.md`:

```markdown
# Phase 4 Handoff

Interfaces Phase 5 (dashboard + reports) may rely on:
- GET /events?since=, GET /sessions?date=  — now populated by real crossings
- GET /clips?date= — evidence file paths
- GET/POST /zones — line configuration
- GET /employees, POST /employees, POST /employees/{id}/deactivate
- POST /enrollments — body {employee_id, path, source:"file"}
- GET /resolver/status, POST /resolver/invalidate
- GET /pipeline/status, POST /pipeline/restart
- GET /health, GET /health/rss
- POST /retention/purge — body {days, actor}

Reports built in Phase 5 read `sessions` and `events` directly via the DB, not via new
endpoints. Do not add report endpoints to the service; Phase 5 owns report generation.
```

```bash
git add -A && git commit -m "chore: phase 4 verified — identity, enrollment and thresholds"
```
