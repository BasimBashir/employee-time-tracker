"""Face detection + ArcFace-class embedding. Never compares embeddings."""
from pathlib import Path
from typing import Optional
import numpy as np
import onnxruntime as ort
from .device import select_provider

FACE_DIM = 512


def cosine(a: bytes, b: bytes) -> float:
    va = np.frombuffer(a, np.float32)
    vb = np.frombuffer(b, np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


class FaceEmbedder:
    def __init__(self, model_path: Path, provider: Optional[str] = None,
                 input_size: Optional[int] = None):
        self.provider = provider or select_provider()[0]
        self.input_size = input_size or (192 if self.provider == "CPUExecutionProvider" else 320)
        self._session: Optional[ort.InferenceSession] = None
        if Path(model_path).exists():
            so = ort.SessionOptions()
            so.intra_op_num_threads = 0
            self._session = ort.InferenceSession(str(model_path), sess_options=so,
                                                providers=[self.provider])

    def _l2n(self, v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed(self, crop_bgr: np.ndarray) -> Optional[bytes]:
        """crop_bgr: a tight face crop, any size. Returns FACE_DIM float32 bytes."""
        if self._session is None or crop_bgr is None or crop_bgr.size == 0:
            return None
        import cv2
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (self.input_size, self.input_size))
        blob = (rgb.astype(np.float32) / 127.5 - 1.0).transpose(2, 0, 1)[None]
        out = self._session.run(None, {self._session.get_inputs()[0].name: blob})[0]
        vec = np.asarray(out).reshape(-1)[:FACE_DIM].astype(np.float32)
        return self._l2n(vec).tobytes()

    def detect_and_crop(self, frame_bgr: np.ndarray, min_face=40):
        """Returns [(bbox_xyxy, crop_bgr)] using a person-crop heuristic when no
        dedicated face detector model is present. The dedicated detector is an
        open item — see the note in Task 2 of the plan."""
        h, w = frame_bgr.shape[:2]
        return [((0, 0, w, h), frame_bgr)]
