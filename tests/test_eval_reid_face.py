import numpy as np
from tools.eval_reid import reid_rank1
from tools.eval_face import face_tar_at_far

def _data(n_per_id=5, ids=("a", "b"), dim=32, jitter=0.01):
    X, Y = [], []
    rng = np.random.default_rng(0)
    for i, who in enumerate(ids):
        center = rng.normal(size=dim)
        for _ in range(n_per_id):
            v = center + rng.normal(scale=jitter, size=dim)
            X.append(v / np.linalg.norm(v)); Y.append(who)
    return X, Y

def test_rank1_perfect_for_tight_clusters():
    X, Y = _data(jitter=0.005)
    m = reid_rank1(X, Y)
    assert m["rank1"] > 0.95 and m["mAP"] > 0.85

def test_rank1_degrades_for_overlapping_clusters():
    X, Y = _data(jitter=5.0)      # centres swamped — effectively one blob
    m = reid_rank1(X, Y)
    assert m["rank1"] < 0.6

def test_tar_uses_resolver_style_thresholds():
    X, Y = _data(jitter=0.005)
    m = face_tar_at_far(X, Y, far=0.001)
    assert 0.0 < m["threshold"] <= 1.0
    assert m["tar"] > 0.9
