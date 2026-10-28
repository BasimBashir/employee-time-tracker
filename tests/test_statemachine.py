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
    assert sm.on_track_position(1, -1, t) == []
    assert sm.on_track_position(1, -1, t) != []            # crossing emitted at t
    assert sm.on_track_position(1, 1, t) == []             # flip back within 3s: ignored
    assert sm.on_track_position(1, 1, t) == []             # still inside the window
    assert sm.on_track_position(1, 1, t.replace(second=10)) == []  # window passed: candidate
    assert sm.on_track_position(1, 1, t.replace(second=10)) != []  # confirmed next frame

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
