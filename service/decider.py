"""The only module that knows geometry + state + clips + DB together."""
from datetime import datetime, timezone
from typing import Callable, Optional

from . import linecross
from .statemachine import StateMachine, Crossing
from .zone import Zone
from .db import (insert_event, open_session, close_session, audit,
                 force_close_open_sessions)


class Decider:
    def __init__(self, conn, recorder,
                 zone_provider: Callable[[int], Optional[Zone]],
                 identity_resolver: Optional[Callable] = None):
        self.conn = conn
        self.recorder = recorder
        self.zone_provider = zone_provider
        self.identity_resolver = identity_resolver or (lambda trk: None)
        self.sm = StateMachine(cache=conn)
        self._sides: dict[int, int] = {}          # track_id -> last confirmed side
        self._open_rows: dict[int, int] = {}      # employee_id -> open session row id
        self.stats_counters = {"crossings": 0, "sessions_closed": 0, "events_logged": 0}

    # -- frame ingest --------------------------------------------------------
    def on_frame(self, image, timestamp: float) -> None:
        self.recorder.add_frame(image, timestamp)

    # -- the Phase 2 pipeline callback ---------------------------------------
    def on_tracks(self, tracks: list, frame) -> None:
        zone = self.zone_provider(getattr(frame, "camera_id", 1))
        if zone is None or not zone.enabled:
            return
        ts = _ts(frame)
        for trk in tracks:
            emp_conf = self.identity_resolver(trk)
            if emp_conf is None:
                continue                          # unenrolled -> no records at all
            emp_id, conf = emp_conf
            side = linecross.side_of(trk.centroid, zone.line_points)
            crosses = self.sm.on_track_position(trk.track_id, side, ts)
            if not crosses:
                continue                          # debounce owns when a side change counts
            prev = self._sides.get(trk.track_id, side)
            direction = linecross.cross_direction(prev, side, zone.direction_out)
            self._sides[trk.track_id] = side
            if direction is not None:
                self.stats_counters["crossings"] += 1
                self._apply_all(self.sm.on_crossing(
                    Crossing(emp_id, direction, ts, zone.camera_id, trk.track_id, conf)))

    # -- direct seam for tests and manual triggers (bypasses debounce) --------
    def on_crossing(self, c: Crossing) -> None:
        self.stats_counters["crossings"] += 1
        self._apply_all(self.sm.on_crossing(c))

    # -- actions -> DB --------------------------------------------------------
    def _apply_all(self, acts: list) -> None:
        for act in acts:
            self._apply(act)

    def _apply(self, act) -> None:
        ts = act.occurred_at or _now()
        camera_id = act.camera_id
        if act.kind == "LOG_ONLY":
            self._log_event(act, camera_id, ts, clip=False)
        elif act.kind == "OPEN_SESSION":
            ev = self._log_event(act, camera_id, ts, clip=True)
            sid = open_session(self.conn, act.employee_id, ev, ts, ts.date().isoformat())
            self._open_rows[act.employee_id] = sid
        elif act.kind == "CLOSE_SESSION":
            row_id = self._open_rows.pop(act.employee_id, None)
            ev = self._log_event(act, camera_id, ts, clip=True)
            if row_id is not None and not self._already_closed(row_id):
                minutes = close_session(self.conn, row_id, ev, ts)
                self.stats_counters["sessions_closed"] += 1
                audit(self.conn, "system", "session_closed", str(row_id),
                      {"employee_id": act.employee_id, "minutes": minutes})
        elif act.kind == "FORCE_CLOSE":
            self.force_flush(ts)

    def _already_closed(self, session_id: int) -> bool:
        row = self.conn.execute("SELECT in_at FROM sessions WHERE id=?", (session_id,)).fetchone()
        return row is None or row["in_at"] is not None

    def _log_event(self, act, camera_id: Optional[int], ts: datetime, clip: bool) -> int:
        clip_path = None
        if clip and camera_id is not None:
            try:
                clip_path = str(self.recorder.capture(
                    act.direction or "OUT", ts, act.employee_id, camera_id))
            except Exception:
                clip_path = None       # a failed clip must never block the record
        ev = insert_event(self.conn, act.employee_id, camera_id or 1,
                          act.direction or "OUT", ts, clip_path,
                          act.confidence, act.uncertain, act.track_id)
        self.stats_counters["events_logged"] += 1
        return ev

    def force_flush(self, now: Optional[datetime] = None) -> int:
        now = now or _now()
        n = force_close_open_sessions(self.conn, now.date().isoformat())
        audit(self.conn, "system", "midnight_rollover", now.date().isoformat(), {"forced": n})
        return n

    def stats(self) -> dict:
        return dict(self.stats_counters)


def _ts(frame) -> datetime:
    t = getattr(frame, "timestamp", 0.0) or 0.0
    if t > 0:
        return datetime.fromtimestamp(t, tz=timezone.utc)
    return _now()


def _now() -> datetime:
    return datetime.now(timezone.utc)
