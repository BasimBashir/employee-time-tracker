import numpy as np
from service.reid import ReidEmbedder, REID_DIM
from service import reid

def test_module_documented_as_non_identity():
    assert "NEVER" in reid.__doc__.upper() and "IDENTITY" in reid.__doc__.upper()

def test_embed_none_without_model(tmp_path):
    r = ReidEmbedder(tmp_path / "none.onnx")
    assert r.embed(np.zeros((256, 128, 3), np.uint8)) is None

class _FakeSession:
    # covers preprocessing + postprocessing without a real model file
    def get_inputs(self):
        return [type("I", (), {"name": "x"})()]
    def run(self, *_a, **_k):
        return [np.ones((1, REID_DIM), np.float32)]

def test_embed_roundtrip_with_stub_session(tmp_path):
    r = ReidEmbedder(tmp_path / "none.onnx")
    r._session = _FakeSession()
    out = r.embed(np.zeros((256, 128, 3), np.uint8))
    assert out is not None and len(out) == REID_DIM * 4
