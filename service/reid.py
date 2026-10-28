"""Body re-identification (OSNet-class).

THIS OUTPUT IS NEVER AN IDENTITY INPUT. It exists only so a Track can survive
frames where the face is turned away — Phase 2's tracker uses it to decide
"same person", never "which employee". Feeding this into the resolver would
make attendance records guesswork. If you are tempted, read spec §6 first.
"""
from pathlib import Path
from typing import Optional
import numpy as np
import onnxruntime as ort
from .device import select_provider

REID_DIM = 512


class ReidEmbedder:
    def __init__(self, model_path: Path, provider: Optional[str] = None,
                 input_size: tuple[int, int] = (128, 256)):
        self.provider = provider or select_provider()[0]
        self.input_size = input_size
        self._session: Optional[ort.InferenceSession] = None
        if Path(model_path).exists():
            so = ort.SessionOptions()
            self._session = ort.InferenceSession(str(model_path), sess_options=so,
                                                 providers=[self.provider])

    def embed(self, person_crop_bgr: np.ndarray) -> Optional[bytes]:
        if self._session is None or person_crop_bgr is None or person_crop_bgr.size == 0:
            return None
        import cv2
        rgb = cv2.cvtColor(person_crop_bgr, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, self.input_size)
        blob = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = self._session.run(None, {self._session.get_inputs()[0].name: blob})[0]
        v = np.asarray(out).reshape(-1)[:REID_DIM].astype(np.float32)
        n = np.linalg.norm(v)
        return (v / n if n else v).tobytes()
