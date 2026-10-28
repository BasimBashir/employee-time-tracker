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
        # shortcut: norm of an L2-normalized embedding is always 1.0, so this
        # ranks nothing today — real quality needs the detector's blur/pose
        # estimate, upgrade when the dedicated face detector lands
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
        # every pose becomes a roster reference (side views included); the best
        # one is the employee's primary face
        best = self.best_face()
        enr = save_enrollment(conn, employee_id, best, self.mean_body(),
                              float(np.mean(self.quality_scores)), is_primary=1)
        for vec, q in zip(self._face_vecs, self.quality_scores):
            if vec is not best:
                save_enrollment(conn, employee_id, vec, None, q, is_primary=0)
        return enr
