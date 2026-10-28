"""The ONLY place identity is decided. Thresholds come from the spec (§6):
confirm 0.45, uncertain 0.35 — below 0.35 means no record at all, because a
wrong employee on an attendance row is worse than a missed one."""
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

    def resolve(self, track) -> Optional[tuple[int, float, int]]:
        """Decider contract: (employee_id, confidence, uncertain) or None."""
        gallery = getattr(track, "face_gallery", None) or []
        if not gallery:
            return None
        best_q = max(q for q, _ in gallery)
        candidates = [e for q, e in gallery if q >= best_q - 1e-6]
        results = [r for r in (self.resolve_embedding(e) for e in candidates) if r]
        if not results:
            return None
        results.sort(key=lambda r: r[1], reverse=True)
        emp, score, uncertain = results[0]
        return emp, score, int(uncertain)
