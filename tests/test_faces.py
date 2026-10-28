import numpy as np
from service.faces import FaceEmbedder, cosine, FACE_DIM

def test_cosine_identical_is_one():
    v = np.random.rand(FACE_DIM).astype(np.float32)
    assert abs(cosine(v.tobytes(), v.tobytes()) - 1.0) < 1e-5

def test_cosine_orthogonal_is_zero():
    a = np.zeros(FACE_DIM, np.float32); a[0] = 1.0
    b = np.zeros(FACE_DIM, np.float32); b[1] = 1.0
    assert abs(cosine(a.tobytes(), b.tobytes())) < 1e-6

def test_embed_none_when_no_model(tmp_path):
    fe = FaceEmbedder(tmp_path / "none.onnx")
    assert fe.embed(np.zeros((160, 160, 3), np.uint8)) is None
