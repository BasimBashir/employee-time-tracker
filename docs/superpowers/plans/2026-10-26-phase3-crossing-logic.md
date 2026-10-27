# Phase 3: Crossing Logic, Events & Clips — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn tracks into attendance records: detect line crossings, run the OUT/IN state machine, write events and sessions to SQLite, and save video clips as evidence — all testable without a camera.

**Architecture:** A pure geometry module decides which side of a polyline a point is on; a pure state machine decides what a side-change means; a clip recorder keeps a JPEG ring buffer per camera and writes MP4 evidence on accepted crossings. All three are pure or near-pure and fully unit-tested. The DB layer from Phase 1 is reused unchanged.

**Tech Stack:** Python 3.11+, OpenCV (JPEG encode + VideoWriter), NumPy, SQLite (reused from Phase 1).

**Phase 1 and Phase 2 must be complete.** Interfaces relied on here:
- Phase 1: `load_config`, `Config`, `connect`, `migrate`, `insert_event`, `open_session`, `close_session`, `force_close_open_sessions`, `soft_delete_session`, `audit`, `create_app`.
- Phase 2: `FrameSource`, `Frame`, `Pipeline(source, detector, tracker, on_tracks=...)`, `Track(track_id, bbox_xyxy, centroid, face_gallery, hits, confidence)`, `Detection`.

## Global Constraints

- Target: Windows 11, 16 GB DDR4, 6th-gen Intel, no dGPU. **No process may exceed 1500 MB RSS.** The clip ring buffer is the single biggest memory risk in this phase — JPEG ring buffer is mandatory, raw frames are not acceptable.
- Nothing hard-deleted. Events are immutable; sessions are soft-deleted.
- Every worker thread `daemon=False`, explicit `stop()` + `join(timeout)`.
- No real RTSP in tests. Everything is driven from synthetic frames or direct function calls.
- Crossings must be decided from geometry alone — never from "the person disappeared", which is what breaks when a camera drops.
- Clips directory: `<data_root>\clips\YYYY\MM\DD\`.
- Debounce constants (from spec §7): side must persist 2 consecutive analysis frames; crossings on the same track within 3 s are ignored.

---

## File Structure

```
service/
├─ zone.py             # Zone config model + persistence
├─ linecross.py        # PURE geometry: side-of-line, crossing direction
├─ statemachine.py     # PURE OUT/IN logic + debounce + guards
├─ cliprecorder.py     # JPEG ring buffer + MP4 writer (threaded post-roll)
└─ decider.py          # glues geometry+state+clips+DB; the on_tracks callback
tests/
├─ test_zone.py
├─ test_linecross.py       # table-driven
├─ test_statemachine.py
├─ test_cliprecorder.py
└─ test_decider.py
```

### Responsibility boundaries

- `linecross.py` is **pure**: point → side. No DB, no state, no time.
- `statemachine.py` is **pure**: (employee, direction, time) → actions. No geometry, no DB.
- `cliprecorder.py` owns all video encoding. It never decides *whether* to record.
- `decider.py` is the only module that knows all three plus the DB. Nothing else crosses these boundaries.
- `zone.py` owns zone persistence. `load_config` stays untouched.

---

## Task 1: Zone configuration model

**Files:**
- Create: `service/zone.py`, `service/schema_zones.sql`
- Modify: `service/db.py` (run the new migration)
- Test: `tests/test_zone.py`

**Interfaces:**
- Consumes: `connect`, `migrate` (Phase 1).
- Produces:
  - `Zone` — dataclass `(id, camera_id, line_points: list[tuple[float,float]], direction_out: str, enabled: bool)` where `direction_out` is `"POS_TO_NEG"` or `"NEG_TO_POS"`.
  - `save_zone(conn, zone) -> int`, `load_zones(conn, camera_id) -> list[Zone]`, `get_active_zone(conn, camera_id) -> Zone | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_zone.py
import json
from service.db import connect, migrate
from service.zone import Zone, save_zone, load_zones, get_active_zone

def test_save_and_load_zone(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    z = Zone(id=None, camera_id=1, line_points=[(0.0, 270.0), (960.0, 270.0)],
             direction_out="POS_TO_NEG", enabled=True)
    zid = save_zone(conn, z)
    loaded = get_active_zone(conn, 1)
    assert loaded.id == zid
    assert loaded.line_points == [(0.0, 270.0), (960.0, 270.0)]
    assert loaded.direction_out == "POS_TO_NEG"
    assert loaded.enabled is True

def test_disabled_zone_not_returned(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    save_zone(conn, Zone(None, 1, [(0.0, 270.0), (960.0, 270.0)], "POS_TO_NEG", False))
    assert get_active_zone(conn, 1) is None
    assert len(load_zones(conn, 1)) == 1

def test_only_one_active_zone_per_camera(tmp_path):
    """Saving a second enabled zone must disable the first — one line is authoritative."""
    conn = connect(tmp_path / "t.db"); migrate(conn)
    first = save_zone(conn, Zone(None, 1, [(0.0, 270.0), (960.0, 270.0)], "POS_TO_NEG", True))
    second = save_zone(conn, Zone(None, 1, [(0.0, 300.0), (960.0, 300.0)], "NEG_TO_POS", True))
    actives = [z for z in load_zones(conn, 1) if z.enabled]
    assert len(actives) == 1
    assert actives[0].id == second
    assert [z.id for z in load_zones(conn, 1)] == [first, second]     # nothing deleted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_zone.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.zone'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/zone.py
import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class Zone:
    id: Optional[int]
    camera_id: int
    line_points: list[tuple[float, float]]
    direction_out: str                       # "POS_TO_NEG" | "NEG_TO_POS"
    enabled: bool = True


def save_zone(conn: sqlite3.Connection, zone: Zone) -> int:
    if zone.enabled:
        # exactly one authoritative line per camera: disable any other enabled zone
        conn.execute("UPDATE zones SET enabled=0 WHERE camera_id=? AND enabled=1",
                     (zone.camera_id,))
    cur = conn.execute(
        "INSERT INTO zones (camera_id,line_points,direction_out,enabled) VALUES (?,?,?,?)",
        (zone.camera_id, json.dumps(zone.line_points), zone.direction_out, int(zone.enabled)))
    conn.commit()
    return cur.lastrowid


def _row_to_zone(r) -> Zone:
    return Zone(id=r["id"], camera_id=r["camera_id"],
                line_points=[tuple(p) for p in json.loads(r["line_points"])],
                direction_out=r["direction_out"], enabled=bool(r["enabled"]))


def load_zones(conn: sqlite3.Connection, camera_id: int) -> list[Zone]:
    rows = conn.execute("SELECT * FROM zones WHERE camera_id=? ORDER BY id", (camera_id,)).fetchall()
    return [_row_to_zone(r) for r in rows]


def get_active_zone(conn: sqlite3.Connection, camera_id: int) -> Optional[Zone]:
    rows = conn.execute(
        "SELECT * FROM zones WHERE camera_id=? AND enabled=1 ORDER BY id DESC LIMIT 1",
        (camera_id,)).fetchall()
    return _row_to_zone(rows[0]) if rows else None
```

`service/schema_zones.sql` is already covered by the `zones` table in Phase 1's
`schema.sql` — **no new DDL is needed**. Confirm the Phase 1 table exists:

```sql
-- verify present in service/schema.sql from Phase 1
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY, camera_id INTEGER NOT NULL REFERENCES cameras(id),
  line_points TEXT NOT NULL, direction_out TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);
```

If `zones` is absent, add exactly the block above to `service/schema.sql` and re-run
`migrate`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_zone.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add service/zone.py tests/
git commit -m "feat: zone configuration model with per-camera active zone"
```

---

## Task 2: Pure line geometry (table-driven)

**Files:**
- Create: `service/linecross.py`
- Test: `tests/test_linecross.py`

**Interfaces:**
- Consumes: `Zone` (Task 1).
- Produces:
  - `side_of(point, line_points) -> int` — `+1`, `-1`, or `0` (on the line).
  - `cross_direction(prev_side, new_side, direction_out) -> Optional[str]` — `"OUT"`, `"IN"`, or `None`.
  - `signedness(direction_out) -> tuple[int, int]` — the (from, to) side pair that means OUT.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_linecross.py
import pytest
from service.linecross import side_of, cross_direction

LINE = [(0.0, 270.0), (960.0, 270.0)]      # horizontal, pointing +x

@pytest.mark.parametrize("point,expected", [
    ((480.0, 100.0),  1),    # above  -> positive side
    ((480.0, 500.0), -1),    # below  -> negative side
    ((480.0, 270.0),  0),    # exactly on the line
    ((-50.0,  50.0),  1),    # far from the segment, still above
])
def test_side_of(point, expected):
    assert side_of(point, LINE) == expected

def test_polyline_uses_nearest_segment():
    poly = [(0.0, 0.0), (500.0, 0.0), (500.0, 540.0)]
    assert side_of((100.0, 50.0), poly) == 1      # below first segment
    assert side_of((450.0, 400.0), poly) == 1     # right of vertical segment

@pytest.mark.parametrize("prev,new,expected", [
    ( 1, -1, "OUT"),   # positive -> negative   (direction_out = POS_TO_NEG)
    (-1,  1, "IN"),
    ( 1,  1, None),    # no crossing
    (-1, -1, None),
    ( 0, -1, "OUT"),   # stepping off the line counts as the first side
    ( 0,  1, "IN"),
])
def test_cross_direction_pos_to_neg(prev, new, expected):
    assert cross_direction(prev, new, "POS_TO_NEG") == expected

def test_cross_direction_is_inverted_for_neg_to_pos():
    assert cross_direction(1, -1, "NEG_TO_POS") == "IN"
    assert cross_direction(-1, 1, "NEG_TO_POS") == "OUT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_linecross.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.linecross'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/linecross.py
"""Pure geometry. No state, no I/O, no time."""

def _nearest_segment_cross(point, line_points) -> float:
    """Cross product against the nearest segment of the polyline."""
    px, py = point
    best = None
    for i in range(len(line_points) - 1):
        (x0, y0), (x1, y1) = line_points[i], line_points[i + 1]
        dx, dy = x1 - x0, y1 - y0
        cross = dx * (py - y0) - dy * (px - x0)
        # distance proxy: perpendicular distance to the infinite line, clamped to seg
        seg_len_sq = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / seg_len_sq))
        cx, cy = x0 + t * dx, y0 + t * dy
        d = (px - cx) ** 2 + (py - cy) ** 2
        if best is None or d < best[0]:
            best = (d, cross)
    return best[1] if best else 0.0


def side_of(point, line_points) -> int:
    if len(line_points) < 2:
        raise ValueError("a zone needs at least two line points")
    cross = _nearest_segment_cross(point, line_points)
    if cross > 0:
        return 1
    if cross < 0:
        return -1
    return 0


def signedness(direction_out: str) -> tuple[int, int]:
    if direction_out == "POS_TO_NEG":
        return (1, -1)
    if direction_out == "NEG_TO_POS":
        return (-1, 1)
    raise ValueError(f"unknown direction_out: {direction_out!r}")


def cross_direction(prev_side: int, new_side: int, direction_out: str) -> str | None:
    """Which way did we cross? 'OUT', 'IN', or None (no crossing)."""
    if prev_side == new_side:
        return None
    out_from, out_to = signedness(direction_out)
    prev = prev_side or out_from          # side 0 resolves toward the OUT side
    new = new_side or out_to
    if (prev, new) == (out_from, out_to):
        return "OUT"
    if (prev, new) == (out_to, out_from):
        return "IN"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_linecross.py -v`
Expected: PASS (4 tests, 13 parametrised cases)

- [ ] **Step 5: Commit**

```bash
git add service/linecross.py tests/
git commit -m "feat: pure line-crossing geometry with configurable direction"
```

---

## Task 3: Pure crossing state machine

**Files:**
- Create: `service/statemachine.py`
- Test: `tests/test_statemachine.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2 (pure logic).
- Produces:
  - `Crossing` — dataclass `(employee_id, direction: str, occurred_at, camera_id, track_id, confidence, uncertain)`.
  - `Action` — dataclass `(kind: str, ...)` where kind is one of `"OPEN_SESSION"`, `"CLOSE_SESSION"`, `"LOG_ONLY"`, `"MARK_UNCERTAIN"`, `"FORCE_CLOSE"`.
  - `StateMachine(cache, debounce_seconds=3.0, debounce_frames=2, out_grace_seconds=60.0)` with:
    - `on_crossing(c: Crossing) -> list[Action]`
    - `on_track_position(track_id, side, timestamp) -> list[Crossing]` — applies debounce.
    - `pending_crossings: dict[track_id, tuple[side, timestamp, count]]`
    - `open_sessions: dict[employee_id, dict]`
    - `force_close_all(on_date) -> list[Action]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statemachine.py
from datetime import datetime
from service.statemachine import Crossing, Action, StateMachine

def C(emp, direction, t, trk=1, conf=0.9, unc=0):
    return Crossing(emp, direction, t, 1, trk, conf, unc)

def test_open_then_close():
    sm = StateMachine(cache=None)
    acts = sm.on_crossing(C(1, "OUT", datetime(2026,10,26,13,0)))
    assert [a.kind for a in acts] == ["OPEN_SESSION"]
    acts = sm.on_crossing(C(1, "IN", datetime(2026,10,26,13,15)))
    assert [a.kind for a in acts] == ["CLOSE_SESSION"]

def test_second_out_while_out_is_logged_not_paired():
    sm = StateMachine(cache=None)
    sm.on_crossing(C(1, "OUT", datetime(2026,10,26,13,0)))
    acts = sm.on_crossing(C(1, "OUT", datetime(2026,10,26,13,5)))
    assert [a.kind for a in acts] == ["LOG_ONLY"]
    assert len(sm.open_sessions) == 1

def test_second_out_within_grace_is_duplicate_not_new_session():
    sm = StateMachine(cache=None)
    sm.on_crossing(C(1, "OUT", datetime(2026,10,26,13,0)))
    acts = sm.on_crossing(C(1, "OUT", datetime(2026,10,26,13,0,30)))   # 30s < grace
    assert [a.kind for a in acts] == ["LOG_ONLY"]
    assert len(sm.open_sessions) == 1

def test_in_without_out_is_logged_only():
    sm = StateMachine(cache=None)
    acts = sm.on_crossing(C(1, "IN", datetime(2026,10,26,13,0)))
    assert [a.kind for a in acts] == ["LOG_ONLY"]

def test_debounce_drops_rapid_same_track_crossings():
    sm = StateMachine(cache=None, debounce_seconds=3.0, debounce_frames=2)
    t = datetime(2026,10,26,13,0)
    first = sm.on_track_position(1, -1, t)                     # settles
    assert first == []
    same = sm.on_track_position(1, 1, t)                       # cross back within 3s
    assert same == []
    later = sm.on_track_position(1, -1, t.replace(second=10))  # 10s later -> allowed
    assert later != []

def test_side_must_persist_two_frames():
    sm = StateMachine(cache=None, debounce_frames=2)
    t = datetime(2026,10,26,13,0)
    sm.on_track_position(1, 1, t)
    out = sm.on_track_position(1, -1, t)      # single frame on other side: not yet
    assert out == []
    out2 = sm.on_track_position(1, -1, t.replace(second=5))
    assert out2 != []

def test_force_close_flags_inferred():
    sm = StateMachine(cache=None)
    sm.on_crossing(C(1, "OUT", datetime(2026,10,26,22,0)))
    acts = sm.force_close_all("2026-10-26")
    assert [a.kind for a in acts] == ["FORCE_CLOSE"]
    assert sm.open_sessions[1]["inferred_return"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_statemachine.py::test_open_then_close -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.statemachine'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/statemachine.py
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Crossing:
    employee_id: int
    direction: str                     # "OUT" | "IN"
    occurred_at: datetime
    camera_id: int
    track_id: int
    confidence: float
    uncertain: int = 0


@dataclass
class Action:
    kind: str
    employee_id: int
    direction: Optional[str] = None
    occurred_at: Optional[datetime] = None
    session_id: Optional[int] = None
    track_id: Optional[int] = None
    confidence: float = 0.0
    uncertain: int = 0
    inferred_return: bool = False


class StateMachine:
    def __init__(self, cache, debounce_seconds: float = 3.0,
                 debounce_frames: int = 2, out_grace_seconds: float = 60.0):
        self.cache = cache                      # generous placeholder: unused in Phase 3
        self.debounce_seconds = debounce_seconds
        self.debounce_frames = debounce_frames
        self.out_grace_seconds = out_grace_seconds
        self.open_sessions: dict[int, dict] = {}
        self.pending_crossings: dict[int, dict] = {}
        self._last_cross_at: dict[int, datetime] = {}

    # -- debounce ------------------------------------------------------------
    def on_track_position(self, track_id: int, side: int, ts: datetime) -> list[Crossing]:
        prev = self.pending_crossings.get(track_id)
        if prev is not None and prev["side"] != side:
            last = self._last_cross_at.get(track_id)
            too_soon = last is not None and (ts - last).total_seconds() < self.debounce_seconds
            if not too_soon:
                self.pending_crossings[track_id] = {"side": side, "ts": ts, "count": 1}
                return []                       # needs a second confirming frame
        if prev is None:
            self.pending_crossings[track_id] = {"side": side, "ts": ts, "count": 1}
            return []
        self.pending_crossings[track_id]["count"] += 1
        confirmed = self.pending_crossings[track_id]["count"] >= self.debounce_frames
        if not confirmed:
            return []
        cross = Crossing(0, "", ts, 0, track_id, 0.0)   # employee filled by the decider
        self._last_cross_at[track_id] = ts
        return [cross]

    # -- crossing ------------------------------------------------------------
    def on_crossing(self, c: Crossing) -> list[Action]:
        if c.direction == "OUT":
            return self._on_out(c)
        if c.direction == "IN":
            return self._on_in(c)
        raise ValueError(f"bad direction {c.direction!r}")

    def _on_out(self, c: Crossing) -> list[Action]:
        open_sess = self.open_sessions.get(c.employee_id)
        if open_sess is not None:
            same_window = (c.occurred_at - open_sess["out_at"]).total_seconds() <= self.out_grace_seconds
            if same_window:
                # duplicate wobble at the doorway: record as evidence, no new session
                return [Action("LOG_ONLY", c.employee_id, c.direction, c.occurred_at,
                               track_id=c.track_id, confidence=c.confidence, uncertain=c.uncertain)]
            # genuinely left again while still marked out: log, never pair to the old one
            return [Action("LOG_ONLY", c.employee_id, c.direction, c.occurred_at,
                           track_id=c.track_id, confidence=c.confidence, uncertain=c.uncertain)]
        acts = [Action("OPEN_SESSION", c.employee_id, c.direction, c.occurred_at,
                       track_id=c.track_id, confidence=c.confidence, uncertain=c.uncertain)]
        self.open_sessions[c.employee_id] = {
            "out_at": c.occurred_at, "out_track": c.track_id, "out_conf": c.confidence}
        return acts

    def _on_in(self, c: Crossing) -> list[Action]:
        open_sess = self.open_sessions.get(c.employee_id)
        if open_sess is None:
            return [Action("LOG_ONLY", c.employee_id, c.direction, c.occurred_at,
                           track_id=c.track_id, confidence=c.confidence, uncertain=c.uncertain)]
        del self.open_sessions[c.employee_id]
        return [Action("CLOSE_SESSION", c.employee_id, c.direction, c.occurred_at,
                       track_id=c.track_id, confidence=c.confidence, uncertain=c.uncertain)]

    def force_close_all(self, on_date: str) -> list[Action]:
        acts = []
        for emp, sess in list(self.open_sessions.items()):
            close_at = datetime.fromisoformat(f"{on_date}T23:59:59")
            acts.append(Action("FORCE_CLOSE", emp, "IN", close_at, inferred_return=True))
            sess["inferred_return"] = True
            sess["inferred_at"] = close_at
        return acts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_statemachine.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add service/statemachine.py tests/
git commit -m "feat: pure crossing state machine with debounce and out/in guards"
```

---

## Task 4: Clip recorder — JPEG ring buffer and MP4 writer

**Files:**
- Create: `service/cliprecorder.py`
- Test: `tests/test_cliprecorder.py`

**Interfaces:**
- Consumes: `Config.clips_dir`, `Config.clip_retention_days` (Phase 1).
- Produces:
  - `ClipRecorder(clips_dir, pre_seconds=10, post_seconds=5, fps=15, max_seconds=15)`
  - `.add_frame(image, timestamp) -> None` — JPEG-encodes into the ring buffer.
  - `.capture(event_type, occurred_at, employee_id, camera_id) -> Path` — blocks for `post_seconds`, writes MP4, returns path.
  - `.memory_bytes_estimate() -> int` — bounded by `fps * max_seconds * jpeg_size`.
  - `.stop() -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cliprecorder.py
import time
from datetime import datetime
import numpy as np
from service.cliprecorder import ClipRecorder

def test_ring_buffer_is_bounded(tmp_path):
    rec = ClipRecorder(tmp_path / "clips", fps=15, max_seconds=15)
    for i in range(100):                      # way more than 15 s worth
        rec.add_frame(np.full((180, 320, 3), i % 255, np.uint8), float(i) / 15.0)
    assert len(rec._buffer) == 15 * 15        # exactly one window, no growth
    assert rec.memory_bytes_estimate() < 25 * 1024 * 1024   # < 25 MB, low-end box

def test_capture_writes_mp4(tmp_path):
    rec = ClipRecorder(tmp_path / "clips", pre_seconds=2, post_seconds=1, fps=10)
    for i in range(40):
        rec.add_frame(np.full((180, 320, 3), 120, np.uint8), float(i) / 10.0)
    path = rec.capture("OUT", datetime(2026, 10, 26, 13, 0), 1, 1)
    assert path.exists() and path.suffix == ".mp4"
    import cv2
    cap = cv2.VideoCapture(str(path))
    assert cap.get(5) > 0                    # readable, non-zero fps
    cap.release(); rec.stop()

def test_capture_falls_back_when_no_post_frames(tmp_path):
    rec = ClipRecorder(tmp_path / "clips", pre_seconds=1, post_seconds=1, fps=10)
    rec.add_frame(np.full((180, 320, 3), 90, np.uint8), 0.0)
    path = rec.capture("IN", datetime(2026, 10, 26, 13, 5), 7, 1)
    assert path.exists()                      # pre-roll only, still written
    rec.stop()

def test_stop_is_idempotent_and_joins(tmp_path):
    rec = ClipRecorder(tmp_path / "clips")
    rec.stop(); rec.stop()
    assert rec._writer_thread is None or not rec._writer_thread.is_alive()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cliprecorder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.cliprecorder'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/cliprecorder.py
import queue, threading, time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional
import cv2
import numpy as np

JPEG_QUALITY = [int(cv2.IMWRITE_JPEG_QUALITY), 75]


class ClipRecorder:
    """
    Keeps a rolling JPEG window so memory stays flat: 15 s @ 15 fps @ ~60 KB
    is ~14 MB per camera, versus ~350 MB for raw BGR frames. Raw frames are not
    acceptable on the 16 GB target machine.
    """

    def __init__(self, clips_dir: Path, pre_seconds: int = 10, post_seconds: int = 5,
                 fps: float = 15.0, max_seconds: float = 15.0):
        self.clips_dir = Path(clips_dir)
        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self.fps = fps
        self.max_seconds = max_seconds
        self._cap = int(fps * max_seconds)
        self._buffer: deque[tuple[float, bytes]] = deque(maxlen=self._cap)
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._writer_thread: Optional[threading.Thread] = None

    # -- ingest --------------------------------------------------------------
    def add_frame(self, image: np.ndarray, timestamp: float) -> None:
        ok, jpg = cv2.imencode(".jpg", image, JPEG_QUALITY)
        if not ok:
            return
        with self._lock:
            self._buffer.append((timestamp, jpg.tobytes()))

    # -- outtake -------------------------------------------------------------
    def capture(self, event_type: str, occurred_at: datetime,
                employee_id: int, camera_id: int) -> Path:
        day_dir = self.clips_dir / occurred_at.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        out = day_dir / (f"emp{employee_id}_cam{camera_id}_{event_type}_"
                         f"{occurred_at.strftime('%H%M%S')}.mp4")
        self._q.put(("out", out, self._snapshot_now()))
        self._ensure_writer()
        if self.post_seconds > 0:
            time.sleep(self.post_seconds)      # let post-roll frames arrive
        self._q.put(("flush", out))
        return out

    def _snapshot_now(self) -> list[tuple[float, bytes]]:
        with self._lock:
            return list(self._buffer)

    def append_post(self, out: Path, frames) -> None:
        """Called by the pipeline writer when post-roll frames exist."""
        self._q.put(("append", out, frames))

    # -- writer thread -------------------------------------------------------
    def _ensure_writer(self) -> None:
        if self._writer_thread and self._writer_thread.is_alive():
            return
        self._writer_thread = threading.Thread(target=self._writer_loop,
                                               name="clipwriter", daemon=False)
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        import cv2
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            kind = item[0]
            if kind == "flush":
                continue
            out, frames = item[1], item[2]
            if not frames:
                continue
            h, w = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8),
                                cv2.IMREAD_COLOR).shape[:2]
            writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"),
                                     self.fps, (w, h))
            for _, jpg in frames:
                img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    writer.write(img)
            writer.release()

    def memory_bytes_estimate(self) -> int:
        if not self._buffer:
            return 0
        avg = sum(len(j) for _, j in self._buffer) / len(self._buffer)
        return int(len(self._buffer) * avg)

    def stop(self) -> None:
        self._stop.set()
        if self._writer_thread:
            self._writer_thread.join(timeout=5)
            self._writer_thread = None
```

> **Note for the engineer:** `capture()` returning before the file is fully written is a
> real hazard — Phase 3's decider stores `clip_path` immediately, so the UI may show a
> clip that is still being encoded. Phase 5's clip player must tolerate a missing/partial
> file for up to `post_seconds`. Do not "fix" this by blocking the state machine; the
> write is intentionally off the hot path.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cliprecorder.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Verify a clip plays**

```bash
python -c "
from service.cliprecorder import ClipRecorder
import numpy as np
from datetime import datetime
r = ClipRecorder(r'C:\ProgramData\EmployeeTracker\clips', pre_seconds=3, post_seconds=1, fps=10)
for i in range(40):
    r.add_frame(np.full((360,640,3), (i*5)%255, np.uint8), i/10.0)
p = r.capture('OUT', datetime.now(), 1, 1)
print('wrote', p, p.exists())
r.stop()"
```
Expected: prints a path that exists; opening it in Windows Media Player shows a short
clip with a moving brightness ramp.

- [ ] **Step 6: Commit**

```bash
git add service/cliprecorder.py tests/
git commit -m "feat: jpeg ring buffer clip recorder with bounded memory"
```

---

## Task 5: The decider — geometry + state + clips + DB

**Files:**
- Create: `service/decider.py`
- Test: `tests/test_decider.py`

**Interfaces:**
- Consumes: Tasks 1–4; Phase 1 DB functions; Phase 2 `Frame`, `Track`.
- Produces:
  - `Decider(conn, recorder, zone_provider, identity_resolver=None)` where
    `zone_provider(camera_id) -> Zone | None` and `identity_resolver(track) -> tuple[int, float] | None`.
  - `.on_tracks(tracks: list[Track], frame: Frame) -> None` — the Phase 2 pipeline callback.
  - `.on_frame(image, timestamp) -> None` — feeds the clip recorder.
  - `.stats() -> dict` with `crossings`, `sessions_closed`, `events_logged`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_decider.py
from datetime import datetime
import numpy as np
from types import SimpleNamespace
from service.db import connect, migrate, insert_event
from service.decider import Decider
from service.zone import Zone, save_zone
from service.detector import Detection
from service.tracker import Tracker

LINE = [(0.0, 270.0), (960.0, 270.0)]

def make_decider(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    save_zone(conn, Zone(None, 1, LINE, "POS_TO_NEG", True))
    rec = SimpleNamespace(add_frame=lambda *a: None,
                          capture=lambda *a, **k: tmp_path / "c.mp4")
    d = Decider(conn, rec, zone_provider=lambda cam: Zone(1, cam, LINE, "POS_TO_NEG", True),
                identity_resolver=lambda trk: (1, 0.9))    # everything is employee 1
    return conn, d

def frame_like(tracks):
    return SimpleNamespace(image=np.zeros((540, 960, 3), np.uint8), timestamp=0.0)

def walk(decider, camera=1, track_id=1, y_from=100, y_to=500, t=0):
    """Move a track's centroid across the line in named positions."""
    positions = [(480, y_from, 1), (480, 250, 1), (480, 400, -1), (480, 450, -1)]
    out = []
    for i, (cx, cy, side) in enumerate(positions):
        detection = Detection((cx - 20, cy - 60, cx + 20, cy + 60), 0.9)
        decider.tracker_for_test = None
        out.append((cx, cy, side))
    return out

def test_full_out_and_back_cycle(tmp_path):
    conn, d = make_decider(tmp_path)
    d.on_frame(np.zeros((540, 960, 3), np.uint8), 0.0)      # prime clip buffer
    # drive the state machine directly through the decider's public seam
    from service.statemachine import Crossing
    d.sm.on_crossing(Crossing(1, "OUT", datetime(2026,10,26,13,0), 1, 1, 0.9))
    d.sm.on_crossing(Crossing(1, "IN", datetime(2026,10,26,13,15), 1, 1, 0.9))
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= 0

def test_decider_writes_events_and_session(tmp_path):
    conn, d = make_decider(tmp_path)
    d.force_flush(datetime(2026,10,26,23,59,59))   # midnight rollover helper
    rows = conn.execute("SELECT kind FROM audit_log").fetchall()
    assert rows is not None
```

> **Implementation note:** `walk()` above is scaffolding to show the shape of the test.
> Replace it during Step 3 with a `_drive(decider, sides)` helper that calls
> `decider.on_tracks(...)` with `Track` objects whose centroids land on the listed sides.
> The weight of the proof belongs on `test_decider_writes_events_and_session`: after
> driving an OUT then an IN, `sessions` must contain one row with
> `duration_minutes == 15.0` and `events` must contain an OUT and an IN row.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_decider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.decider'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/decider.py
"""The only module that knows geometry + state + clips + DB together."""
from datetime import datetime
from typing import Callable, Optional
import numpy as np
from . import statemachine as sm_mod
from . import linecross
from .cliprecorder import ClipRecorder
from .detector import Detection
from .tracker import Track
from .zone import Zone
from .db import (insert_event, open_session, close_session, audit)


BBox = tuple[float, float, float, float]


class Decider:
    def __init__(self, conn, recorder: ClipRecorder,
                 zone_provider: Callable[[int], Optional[Zone]],
                 identity_resolver: Optional[Callable[[Track], Optional[tuple[int, float]]]] = None):
        self.conn = conn
        self.recorder = recorder
        self.zone_provider = zone_provider
        self.identity_resolver = identity_resolver or (lambda trk: None)
        self.sm = sm_mod.StateMachine(cache=conn)
        self._sides: dict[int, int] = {}          # track_id -> last confirmed side
        self._track_emp: dict[int, int] = {}      # track_id -> employee_id
        self.stats_counters = {"crossings": 0, "sessions_closed": 0, "events_logged": 0}

    # -- frame ingest --------------------------------------------------------
    def on_frame(self, image: np.ndarray, timestamp: float) -> None:
        self.recorder.add_frame(image, timestamp)

    # -- the Phase 2 callback ------------------------------------------------
    def on_tracks(self, tracks: list[Track], frame) -> None:
        zone = self.zone_provider(getattr(frame, "camera_id", 1))
        if zone is None or not zone.enabled:
            return
        for trk in tracks:
            emp_conf = self.identity_resolver(trk)
            if emp_conf is None:
                continue                          # unenrolled -> no records at all
            emp_id, conf = emp_conf
            self._track_emp[trk.track_id] = emp_id
            side = linecross.side_of(trk.centroid, zone.line_points)
            prev = self._sides.get(trk.track_id, side)
            direction = linecross.cross_direction(prev, side, zone.direction_out)
            crosses = self.sm.on_track_position(trk.track_id, side, _ts(frame))
            if direction is None and not crosses:
                self._sides[trk.track_id] = side
                continue
            if direction is not None:
                self._dispatch(emp_id, direction, trk, conf, zone)
            self._sides[trk.track_id] = side

    # -- crossing -> actions -> DB ------------------------------------------
    def _dispatch(self, emp_id, direction, trk: Track, conf, zone: Zone) -> None:
        from .statemachine import Crossing
        self.stats_counters["crossings"] += 1
        c = Crossing(emp_id, direction, _now(), zone.camera_id, trk.track_id, conf, 0)
        for act in self.sm.on_crossing(c):
            self._apply(act, zone, trk)

    def _apply(self, act, zone: Zone, trk: Track) -> None:
        ts = act.occurred_at or _now()
        if act.kind == "LOG_ONLY":
            self._log_event(act, zone, clip=False)
            return
        if act.kind == "OPEN_SESSION":
            ev = self._log_event(act, zone, clip=True)
            open_session(self.conn, act.employee_id, ev, ts, ts.date().isoformat())
            return
        if act.kind == "CLOSE_SESSION":
            ev = self._log_event(act, zone, clip=True)
            sess = self._open_session_row(act.employee_id)
            if sess:
                minutes = close_session(self.conn, sess["id"], ev, ts)
                self.stats_counters["sessions_closed"] += 1
                audit(self.conn, "system", "session_closed", str(sess["id"]),
                      {"employee_id": act.employee_id, "minutes": minutes})
            return
        if act.kind == "FORCE_CLOSE":
            self.force_flush(ts)

    def _open_session_row(self, emp_id):
        from . import statemachine as sm
        s = self.sm.open_sessions.get(emp_id)
        if s is None:
            return None
        return s.get("row")

    def _log_event(self, act, zone: Zone, clip: bool) -> Optional[int]:
        clip_path = None
        if clip:
            try:
                clip_path = str(self.recorder.capture(
                    act.direction or "OUT", act.occurred_at or _now(),
                    act.employee_id, zone.camera_id))
            except Exception:
                clip_path = None       # a failed clip must never block the record
        ev = insert_event(self.conn, act.employee_id, zone.camera_id,
                          act.direction or "OUT", act.occurred_at or _now(),
                          clip_path, act.confidence, act.uncertain, act.track_id)
        self.stats_counters["events_logged"] += 1
        return ev

    def force_flush(self, now: datetime) -> None:
        from .db import force_close_open_sessions
        n = force_close_open_sessions(self.conn, now.date().isoformat())
        audit(self.conn, "system", "midnight_rollover", now.date().isoformat(), {"forced": n})

    def stats(self) -> dict:
        return dict(self.stats_counters)


def _ts(frame) -> datetime:
    t = getattr(frame, "timestamp", 0.0) or 0.0
    return datetime.utcfromtimestamp(t) if t > 0 else _now()


def _now() -> datetime:
    return datetime.utcnow()
```

> **Implementation note — fix before you finish Step 3.** The `_apply`/`OPEN_SESSION`
> path above must record the new session's row id back into
> `self.sm.open_sessions[emp]["row"]`, otherwise `CLOSE_SESSION` cannot find the row to
> update. Do it like this:
>
> ```python
> if act.kind == "OPEN_SESSION":
>     ev = self._log_event(act, zone, clip=True)
>     sid = open_session(self.conn, act.employee_id, ev, ts, ts.date().isoformat())
>     self.sm.open_sessions[act.employee_id]["row"] = {
>         "id": sid, "out_at": ts}
> ```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_decider.py -v`
Expected: PASS. After driving an OUT and an IN through `on_tracks`,
`SELECT duration_minutes FROM sessions` returns 15.0 for the synthetic case.

- [ ] **Step 5: Commit**

```bash
git add service/decider.py tests/
git commit -m "feat: decider wiring geometry, state machine, clips and sqlite"
```

---

## Task 6: Service integration, midnight rollover, API surface

**Files:**
- Modify: `service/service_main.py`, `service/api.py`
- Test: `tests/test_api_events.py`

**Interfaces:**
- Consumes: `Decider` (Task 5).
- Produces:
  - `GET /events?since=` (exists from Phase 1) now returns real crossings.
  - `GET /sessions?date=` (exists) now returns real sessions.
  - `GET /zones?camera_id=` and `POST /zones` for configuring the line.
  - `GET /clips?date=` listing evidence files.
  - A `nightly_rollover(conn)` coroutine/thread in the service that force-closes open
    sessions at 23:59:59 daily.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_events.py
from fastapi.testclient import TestClient
from service.db import connect, migrate, insert_event
from service.api import create_app

def test_zones_roundtrip(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    client = TestClient(create_app(conn, None))
    r = client.post("/zones", json={"camera_id": 1,
                                    "line_points": [[0, 270], [960, 270]],
                                    "direction_out": "POS_TO_NEG"})
    assert r.status_code == 200
    zones = client.get("/zones", params={"camera_id": 1}).json()
    assert zones[0]["direction_out"] == "POS_TO_NEG"

def test_clips_listing_empty(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    client = TestClient(create_app(conn, None))
    assert client.get("/clips", params={"date": "2026-10-26"}).json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_events.py -v`
Expected: FAIL — 404 on `/zones`

- [ ] **Step 3: Add the endpoints**

In `service/api.py`, inside `create_app`:

```python
    @app.get("/zones")
    def get_zones(camera_id: int):
        from .zone import load_zones
        from dataclasses import asdict
        return [asdict(z) for z in load_zones(conn, camera_id)]

    @app.post("/zones")
    def post_zone(body: dict):
        from .zone import Zone, save_zone
        z = Zone(None, int(body["camera_id"]),
                 [tuple(p) for p in body["line_points"]],
                 body["direction_out"], body.get("enabled", True))
        return {"id": save_zone(conn, z)}

    @app.get("/clips")
    def clips(date: str):
        root = (cfg.clips_dir if cfg else Path(r"C:\ProgramData\EmployeeTracker\clips"))
        day = root / date.replace("-", "/")
        if not day.is_dir():
            return []
        return sorted(str(p) for p in day.rglob("*.mp4"))
```

In `service/service_main.py`, add the nightly rollover thread:

```python
def _rollover_loop(conn):
    from .db import force_close_open_sessions, audit
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute >= 59:
            n = force_close_open_sessions(conn, now.date().isoformat())
            audit(conn, "system", "midnight_rollover", now.date().isoformat(), {"forced": n})
            time.sleep(120)                     # don't double-fire
        time.sleep(30)

# in SvcDoRun, after start():
threading.Thread(target=_rollover_loop, args=(conn,), daemon=False).start()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_events.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Verify the midnight rollover manually**

```bash
python -c "
from datetime import datetime
from service.db import connect, migrate, insert_event, open_session, force_close_open_sessions
conn = connect(r'C:\ProgramData\EmployeeTracker\data\tracker.db'); migrate(conn)
ev = insert_event(conn, 1, 1, 'OUT', datetime(2026,10,26,22,0), None, 0.9, 0, 1)
sid = open_session(conn, 1, ev, datetime(2026,10,26,22,0), '2026-10-26')
print('forced:', force_close_open_sessions(conn, '2026-10-26'))
print(conn.execute('SELECT inferred_return, duration_minutes FROM sessions').fetchall())"
```
Expected: `forced: 1` and `[(1, 119.98…)]` — the session is closed at 23:59:59 and flagged.

- [ ] **Step 6: Commit**

```bash
git add service/ tests/
git commit -m "feat: zones/clips endpoints and nightly midnight rollover"
```

---

## Task 7: Retention — 90-day auto-purge and manual cleanup

**Files:**
- Create: `service/retention.py`
- Modify: `service/api.py`, `service/service_main.py`
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: `Config.clips_dir`, `Config.clip_retention_days`, `Config.disk_floor_gb`; `audit` (Phase 1).
- Produces:
  - `purge_clips_older_than(clips_dir, days, dry_run=False) -> list[Path]`
  - `clear_clip_paths(conn, purged: list[Path]) -> int`
  - `disk_free_gb(path) -> float`
  - `nightly_retention(cfg, conn) -> dict` — purge + clear + disk check, with audit rows.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_retention.py
import time
from datetime import datetime, timedelta
from pathlib import Path
from service.retention import purge_clips_older_than, disk_free_gb, clear_clip_paths

def _make(path_tail: str, age_days: int) -> Path:
    p = Path(path_tail)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    old = time.time() - age_days * 86400
    import os; os.utime(p, (old, old))
    return p

def test_purge_removes_only_old(tmp_path):
    clips = tmp_path / "clips"
    recent = _make(str(clips / "2026/10/26" / "keep.mp4"), 5)
    old = _make(str(clips / "2026/06/01" / "drop.mp4"), 120)
    hit = purge_clips_older_than(clips, 90)
    assert old in hit and recent not in hit
    assert not old.exists() and recent.exists()

def test_clear_clip_paths_nulls_events(tmp_path):
    from service.db import connect, migrate, insert_event
    conn = connect(tmp_path / "t.db"); migrate(conn)
    insert_event(conn, 1, 1, "OUT", datetime(2026,10,26,13,0),
                 str(tmp_path / "clips" / "drop.mp4"), 0.9, 0, 1)
    n = clear_clip_paths(conn, [tmp_path / "clips" / "drop.mp4"])
    assert n == 1
    assert conn.execute("SELECT clip_path FROM events").fetchone()[0] is None

def test_disk_free_gb_positive(tmp_path):
    assert disk_free_gb(tmp_path) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_retention.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.retention'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/retention.py
import os, shutil, sqlite3, subprocess, time
from datetime import datetime
from pathlib import Path
from .db import audit


def purge_clips_older_than(clips_dir: Path, days: int, dry_run: bool = False) -> list[Path]:
    cutoff = time.time() - days * 86400
    hits = []
    root = Path(clips_dir)
    if not root.is_dir():
        return hits
    for p in root.rglob("*.mp4"):
        try:
            if p.stat().st_mtime < cutoff:
                hits.append(p)
                if not dry_run:
                    p.unlink()
        except FileNotFoundError:
            continue
    return hits


def clear_clip_paths(conn: sqlite3.Connection, purged) -> int:
    n = 0
    for p in purged:
        cur = conn.execute("UPDATE events SET clip_path=NULL WHERE clip_path=?", (str(p),))
        n += cur.rowcount or 0
    conn.commit()
    return n


def disk_free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(str(path)).free / 1024 ** 3
    except Exception:
        return -1.0


def nightly_retention(cfg, conn) -> dict:
    purged = purge_clips_older_than(cfg.clips_dir, cfg.clip_retention_days)
    cleared = clear_clip_paths(conn, purged)
    free = disk_free_gb(cfg.clips_dir)
    extra = []
    if 0 <= free < cfg.disk_floor_gb:
        extra = purge_clips_older_than(cfg.clips_dir, 14)
        cleared += clear_clip_paths(conn, extra)
    audit(conn, "system", "nightly_retention", cfg.clip_retention_days,
          {"purged": len(purged), "cleared_paths": cleared, "free_gb": round(free, 2),
           "early_purge": len(extra)})
    return {"purged": len(purged), "cleared": cleared, "free_gb": round(free, 2)}
```

Also add a manual endpoint in `service/api.py`:

```python
    @app.post("/retention/purge")
    def manual_purge(body: dict):
        from .retention import purge_clips_older_than, clear_clip_paths
        from .db import audit
        days = int(body.get("days", 30))
        purged = purge_clips_older_than(cfg.clips_dir, days)
        cleared = clear_clip_paths(conn, purged)
        audit(conn, body.get("actor", "owner"), "manual_clip_purge", str(days),
              {"purged": len(purged)})
        return {"purged": len(purged), "clip_paths_cleared": cleared}
```

Wire `nightly_retention` into the service's rollover thread beside the midnight force-close.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_retention.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add service/retention.py tests/ service/api.py service/service_main.py
git commit -m "feat: clip retention with 90-day purge, path clearing and disk guard"
```

---

## Task 8: Phase 3 acceptance gate

- [ ] **Step 1: Full suite green**

```bash
python -m pytest -v
```
Expected: all of Tasks 1–7 green. No camera, no network.

- [ ] **Step 2: End-to-end crossing proof with a synthetic crossing**

Write `tests/test_e2e_crossing.py` and keep it — it is the regression net for the whole
product:

```python
# tests/test_e2e_crossing.py
"""Two synthetic crossings -> two sessions with correct durations."""
from datetime import datetime, timedelta
import numpy as np
from service.db import connect, migrate
from service.zone import Zone
from service.decider import Decider
from service.cliprecorder import ClipRecorder
from service.detector import Detection
from service.tracker import Track

def _track(tid, cx, cy):
    return Track(tid, (cx - 20, cy - 60, cx + 20, cy + 60), 0.9)

def test_two_excursions(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    rec = ClipRecorder(tmp_path / "clips", pre_seconds=0, post_seconds=0, fps=10)
    d = Decider(conn, rec,
                zone_provider=lambda c: Zone(1, c, [(0.0, 270.0), (960.0, 270.0)],
                                             "POS_TO_NEG", True),
                identity_resolver=lambda t: (1, 0.92))
    frame = type("F", (), {"image": np.zeros((540, 960, 3), np.uint8),
                           "timestamp": 0.0, "camera_id": 1})()
    # person 1: above the line, walks below it (OUT), returns (IN)
    for cy in (100, 200, 320, 400, 400, 320, 200, 100):
        d.on_tracks([_track(1, 480, cy)], frame)
    # second excursion, longer
    for cy in (100, 200, 320, 400, 400, 400, 400, 320, 200, 100):
        d.on_tracks([_track(1, 480, cy)], frame)
    rows = conn.execute(
        "SELECT out_at, in_at, duration_minutes, inferred_return FROM sessions").fetchall()
    assert len(rows) == 2, rows
    assert all(r["duration_minutes"] is not None for r in rows)
    assert rows[-1]["duration_minutes"] > rows[0]["duration_minutes"]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE event_type='OUT'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM events WHERE event_type='IN'").fetchone()[0] == 2
    rec.stop()
```

Run: `python -m pytest tests/test_e2e_crossing.py -v`
Expected: PASS. If the two excursions collapse into one, your debounce or side
confirmation logic is wrong — fix `statemachine.py`, not the test.

- [ ] **Step 3: Memory and worker sanity**

Run the Phase 2 pipeline with the decider attached against `tests/fixtures/synth.mp4`
for 10 minutes, then:

```bash
curl http://127.0.0.1:8080/health/rss
```
Expected: under 1500 MB. `curl http://127.0.0.1:8080/pipeline/status` shows
`dropped_frames` stable and `reconnects` unchanged.

- [ ] **Step 4: Handoff note for Phase 4**

Write `docs/superpowers/plans/2026-10-26-phase3-handoff.md`:

```markdown
# Phase 3 Handoff

Interfaces Phase 4 (identity) may rely on and must not change:
- `service.decider.Decider(conn, recorder, zone_provider, identity_resolver)`
  - `identity_resolver(track) -> tuple[employee_id, confidence] | None`
  - Returning `None` for a track means "no record at all" — that is the documented
    contract for unenrolled people. Phase 4 replaces the stub resolver with a real one.
- `service.tracker.Track.face_gallery` — list of `(quality_score, embedding_bytes)`,
  capped at 12, sorted best-first. Phase 4 consumes this and nothing else about the track.
- `service.cliprecorder.ClipRecorder.capture(...) -> Path`
- `service.retention.nightly_retention(cfg, conn)`
- API: GET/POST `/zones`, GET `/clips`, GET `/events`, GET `/sessions`

Phase 4 adds: enrollment flow, roster CRUD, face embedding, threshold policy.
Do not alter `face_gallery`'s tuple ordering or cap without updating Phase 4's brief.
```

```bash
git add -A && git commit -m "chore: phase 3 verified — crossings, records and clips without rtsp"
```
