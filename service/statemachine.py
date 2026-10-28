from dataclasses import dataclass
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
    """Pure OUT/IN pairing. No geometry, no DB, no clock reads."""

    def __init__(self, cache, debounce_seconds: float = 3.0,
                 debounce_frames: int = 2, out_grace_seconds: float = 60.0):
        self.cache = cache                      # reserved: Phase 5 resume state
        self.debounce_seconds = debounce_seconds
        self.debounce_frames = debounce_frames
        self.out_grace_seconds = out_grace_seconds
        self.open_sessions: dict[int, dict] = {}
        self.pending_crossings: dict[int, dict] = {}
        self._last_cross_at: dict[int, datetime] = {}

    # -- debounce ------------------------------------------------------------
    def on_track_position(self, track_id: int, side: int, ts: datetime) -> list[Crossing]:
        prev = self.pending_crossings.get(track_id)
        if prev is None:
            self.pending_crossings[track_id] = {"side": side, "ts": ts, "count": 1}
            return []
        if prev["side"] != side:
            last = self._last_cross_at.get(track_id)
            if last is not None and (ts - last).total_seconds() < self.debounce_seconds:
                return []                   # wobble inside the debounce window: ignored
            prev["side"], prev["ts"], prev["count"] = side, ts, 1
            return []                       # candidate registered, needs a confirming frame
        prev["count"] += 1
        if prev["count"] < self.debounce_frames:
            return []
        self._last_cross_at[track_id] = ts
        prev["count"] = 0                   # one crossing per confirmation
        return [Crossing(0, "", ts, 0, track_id, 0.0)]   # employee filled by the decider

    # -- crossing ------------------------------------------------------------
    def on_crossing(self, c: Crossing) -> list[Action]:
        if c.direction == "OUT":
            return self._on_out(c)
        if c.direction == "IN":
            return self._on_in(c)
        raise ValueError(f"bad direction {c.direction!r}")

    def _on_out(self, c: Crossing) -> list[Action]:
        if c.employee_id in self.open_sessions:
            # already marked out: duplicate wobble or re-cross, never a new session
            return [Action("LOG_ONLY", c.employee_id, c.direction, c.occurred_at,
                           track_id=c.track_id, confidence=c.confidence,
                           uncertain=c.uncertain)]
        acts = [Action("OPEN_SESSION", c.employee_id, c.direction, c.occurred_at,
                       track_id=c.track_id, confidence=c.confidence,
                       uncertain=c.uncertain)]
        self.open_sessions[c.employee_id] = {
            "out_at": c.occurred_at, "out_track": c.track_id, "out_conf": c.confidence}
        return acts

    def _on_in(self, c: Crossing) -> list[Action]:
        open_sess = self.open_sessions.get(c.employee_id)
        if open_sess is None:
            return [Action("LOG_ONLY", c.employee_id, c.direction, c.occurred_at,
                           track_id=c.track_id, confidence=c.confidence,
                           uncertain=c.uncertain)]
        del self.open_sessions[c.employee_id]
        return [Action("CLOSE_SESSION", c.employee_id, c.direction, c.occurred_at,
                       track_id=c.track_id, confidence=c.confidence,
                       uncertain=c.uncertain)]

    def force_close_all(self, on_date: str) -> list[Action]:
        acts = []
        for emp, sess in self.open_sessions.items():
            close_at = datetime.fromisoformat(f"{on_date}T23:59:59")
            acts.append(Action("FORCE_CLOSE", emp, "IN", close_at, inferred_return=True))
            sess["inferred_return"] = True
            sess["inferred_at"] = close_at
        return acts
