"""IoU tracker — knows which detection is which person over time.

ByteTrack-class association: greedy IoU matching, tracks born on first sight,
confirmed after n_init consecutive hits, garbage-collected after max_age
misses. Each track carries a small face gallery (Phase 4 consumes it — do not
change its shape).
"""
from dataclasses import dataclass, field
import numpy as np
from .detector import Detection

FACE_GALLERY_CAP = 12

@dataclass
class Track:
    track_id: int
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    age: int = 0
    hits: int = 1
    time_since_update: int = 0
    face_gallery: list = field(default_factory=list)  # (quality, embedding_bytes), best first

    @property
    def centroid(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox_xyxy
        return ((x1 + x2) / 2, (y1 + y2) / 2)


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a; bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


class Tracker:
    def __init__(self, iou_threshold: float = 0.3, max_age: int = 30, n_init: int = 3):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.n_init = n_init
        self._tracks: list[Track] = []
        self._next_id = 1

    def update(self, detections: list[Detection]) -> list[Track]:
        dets = list(detections)
        matched_dets, matched_tracks = set(), set()
        pairs = []
        for di, d in enumerate(dets):
            for ti, tr in enumerate(self._tracks):
                s = _iou(d.bbox_xyxy, tr.bbox_xyxy)
                if s >= self.iou_threshold:
                    pairs.append((s, di, ti))
        pairs.sort(reverse=True)
        for s, di, ti in pairs:
            if di in matched_dets or ti in matched_tracks:
                continue
            matched_dets.add(di); matched_tracks.add(ti)
            tr = self._tracks[ti]
            d = dets[di]
            tr.bbox_xyxy = d.bbox_xyxy
            tr.confidence = d.confidence
            tr.hits += 1
            tr.time_since_update = 0
            tr.age += 1

        pre_n = len(self._tracks)   # age only tracks that existed before this update
        for di, d in enumerate(dets):
            if di not in matched_dets:
                self._tracks.append(
                    Track(self._next_id, d.bbox_xyxy, d.confidence))
                self._next_id += 1

        for ti, tr in enumerate(self._tracks[:pre_n]):
            if ti not in matched_tracks:
                tr.age += 1
                tr.time_since_update += 1

        self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]
        return self.active_tracks()

    def add_face(self, track_id: int, embedding_bytes: bytes, quality_score: float) -> None:
        for tr in self._tracks:
            if tr.track_id == track_id:
                tr.face_gallery.append((quality_score, embedding_bytes))
                if len(tr.face_gallery) > FACE_GALLERY_CAP:
                    tr.face_gallery = sorted(
                        tr.face_gallery, key=lambda e: e[0], reverse=True)[:FACE_GALLERY_CAP]
                return

    def active_tracks(self) -> list[Track]:
        return [t for t in self._tracks if t.hits >= self.n_init]
