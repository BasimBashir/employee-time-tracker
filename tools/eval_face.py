"""Face metrics at the resolver's own thresholds (spec §6: 0.45 confirm, 0.35 uncertain)."""
from typing import Sequence
import numpy as np

CONFIRM = 0.45
UNCERTAIN = 0.35


def face_tar_at_far(embeddings: Sequence, identities: Sequence,
                    far: float = 0.001) -> dict:
    X = np.stack([np.asarray(np.frombuffer(e, np.float32)) if isinstance(e, (bytes, bytearray))
                  else np.asarray(e, np.float32) for e in embeddings])
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    Y = list(identities)
    sims = X @ X.T
    n = len(Y)
    # genuine = same identity, impostor = different identity
    gen = [sims[i, j] for i in range(n) for j in range(i + 1, n) if Y[i] == Y[j]]
    imp = [sims[i, j] for i in range(n) for j in range(i + 1, n) if Y[i] != Y[j]]
    if not gen or not imp:
        return {"tar": 0.0, "threshold": UNCERTAIN, "far": far}
    gen_scores = np.array(gen)
    # threshold that keeps the impostor false-accept rate at `far`
    imp_scores = np.sort(imp)[::-1]
    k = max(int(round(far * len(imp_scores))), 0)
    threshold = float(imp_scores[k]) if k < len(imp_scores) else 0.0
    tar = float((gen_scores >= threshold).mean())
    return {"tar": tar, "threshold": threshold, "far": far,
            "confirm": CONFIRM, "uncertain": UNCERTAIN}
