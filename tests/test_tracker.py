from service.detector import Detection
from service.tracker import Tracker

def _det(cx, cy, conf=0.9):
    return Detection(bbox_xyxy=(cx - 20, cy - 60, cx + 20, cy + 60), confidence=conf)

def test_new_track_confirms_after_n_init():
    t = Tracker(n_init=3)
    for i in range(3):
        t.update([_det(100 + i * 5, 200)])
    live = [tr for tr in t.active_tracks() if tr.hits >= 3]
    assert len(live) == 1

def test_iou_associates_same_object():
    t = Tracker(n_init=1)
    t.update([_det(100, 200)])
    first = t.active_tracks()[0].track_id
    t.update([_det(104, 202)])       # small movement -> same track
    t.update([_det(108, 204)])
    assert t.active_tracks()[0].track_id == first

def test_gallery_capped_and_face_attach():
    t = Tracker(n_init=1); t.update([_det(100, 200)])
    tid = t.active_tracks()[0].track_id
    for i in range(20):
        t.add_face(tid, b"x" * 8, 0.9)
    tr = [x for x in t.active_tracks() if x.track_id == tid][0]
    assert len(tr.face_gallery) == 12
    assert all(q == 0.9 for q, _ in tr.face_gallery)

def test_track_dies_after_max_age():
    t = Tracker(n_init=1, max_age=2)
    t.update([_det(100, 200)])
    for _ in range(3):
        t.update([_det(900, 40)])    # unrelated object elsewhere
    assert not any(tr.bbox_xyxy == _det(100, 200).bbox_xyxy for tr in t._tracks)

def test_centroid():
    from service.tracker import Track
    tr = Track(1, (0, 0, 10, 20), 0.9)
    assert tr.centroid == (5.0, 10.0)
