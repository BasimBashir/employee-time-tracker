"""Person detector — knows what is in a frame, never which person is which.

Wraps an ONNX YOLO model behind detect() -> list[Detection]. A missing model
file is a deliberate no-op (empty detections, no crash): the pipeline runs and
reports stats before anyone has trained/exported a model.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import logging
import numpy as np
import onnxruntime as ort
from .device import select_provider

log = logging.getLogger(__name__)

PERSON_CLASS_ID = 0   # COCO "person"

@dataclass
class Detection:
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    class_id: int = PERSON_CLASS_ID


class Detector:
    def __init__(self, model_path: Path, provider: Optional[str] = None,
                 input_size: Optional[int] = None, conf_threshold: float = 0.35):
        self.provider = provider or select_provider()[0]
        self.input_size = input_size or 640
        self.conf_threshold = conf_threshold
        self._session: Optional[ort.InferenceSession] = None
        self.model_path = Path(model_path)
        if self.model_path.exists():
            so = ort.SessionOptions()
            so.intra_op_num_threads = 0          # let ORT use physical cores
            try:
                self._session = ort.InferenceSession(
                    str(self.model_path), sess_options=so, providers=[self.provider])
            except Exception:
                # A picked-but-unusable provider (e.g. DML on a VM whose vGPU
                # lacks DX12) must not take the whole service down.
                log.warning("provider %s unusable — falling back to CPU",
                            self.provider, exc_info=True)
                self.provider = "CPUExecutionProvider"
                self._session = ort.InferenceSession(
                    str(self.model_path), sess_options=so, providers=[self.provider])

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self._session is None:
            return []
        h, w = image.shape[:2]
        blob = _letterbox(image, self.input_size)
        out = self._session.run(None, {self._session.get_inputs()[0].name: blob})[0]
        d = np.asarray(out)
        # Accept both (1, N, 6) and (N, 6) layouts: x1,y1,x2,y2,conf,cls
        if d.ndim == 3:
            d = d[0]
        results = []
        for row in d:
            x1, y1, x2, y2, conf, cls = row[:6]
            if int(cls) != PERSON_CLASS_ID or float(conf) < self.conf_threshold:
                continue
            sx, sy = w / self.input_size, h / self.input_size
            results.append(Detection((float(x1) * sx, float(y1) * sy,
                                      float(x2) * sx, float(y2) * sy),
                                     float(conf), int(cls)))
        return results


def _letterbox(image: np.ndarray, size: int) -> np.ndarray:
    import cv2
    r = size / max(image.shape[:2])
    im = cv2.resize(image, (round(image.shape[1] * r), round(image.shape[0] * r)))
    canvas = np.full((size, size, 3), 114, np.uint8)
    canvas[: im.shape[0], : im.shape[1]] = im
    return canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
