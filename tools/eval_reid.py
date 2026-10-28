"""Re-ID metrics. Metrics only — never promotes, never selects identity."""
from typing import Sequence
import numpy as np


def _matrix(embeddings: Sequence) -> np.ndarray:
    X = np.stack([np.asarray(np.frombuffer(e, np.float32)) if isinstance(e, (bytes, bytearray))
                  else np.asarray(e, np.float32) for e in embeddings])
    return X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)


def reid_rank1(embeddings: Sequence, identities: Sequence) -> dict:
    X = _matrix(embeddings)
    Y = list(identities)
    sims = X @ X.T
    np.fill_diagonal(sims, -1.0)
    correct = 0
    aps = []
    for i in range(len(Y)):
        order = np.argsort(-sims[i])
        ranked = [Y[j] for j in order]
        if ranked and ranked[0] == Y[i]:
            correct += 1
        rel = np.array([1 if y == Y[i] else 0 for y in ranked])
        if rel.sum():
            cum = np.cumsum(rel)
            precision = cum / (np.arange(len(rel)) + 1)
            aps.append(float((precision * rel).sum() / rel.sum()))
    return {"rank1": correct / len(Y) if Y else 0.0, "mAP": float(np.mean(aps)) if aps else 0.0}
