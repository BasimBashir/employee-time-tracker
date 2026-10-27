"""Frame sources — where frames come from.

A FrameSource never inspects frame contents; that is the detector's job.
Every source is bounded by construction: read() returns frames one at a time
and None at end-of-stream or after stop().
"""
import abc, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import numpy as np


@dataclass
class Frame:
    image: np.ndarray      # H x W x 3, uint8, BGR
    timestamp: float       # epoch seconds, from the source clock
    source: "FrameSource"


class FrameSource(abc.ABC):
    @abc.abstractmethod
    def read(self) -> Optional[Frame]:
        """Next frame, or None at end-of-stream / after stop()."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Release every resource; idempotent."""

    @property
    @abc.abstractmethod
    def fps(self) -> float: ...

    @property
    @abc.abstractmethod
    def width(self) -> int: ...

    @property
    @abc.abstractmethod
    def height(self) -> int: ...


class SyntheticSource(FrameSource):
    """Deterministic frames with a moving bright rectangle. No camera needed."""

    def __init__(self, frame_count: int = 60, width: int = 960,
                 height: int = 540, moving_rect: bool = True):
        self._n = frame_count
        self._w, self._h = width, height
        self._moving = moving_rect
        self._i = 0
        self._stopped = False
        self._base = np.zeros((height, width, 3), dtype=np.uint8)

    def read(self) -> Optional[Frame]:
        if self._stopped or self._i >= self._n:
            return None
        img = self._base.copy()
        if self._moving:
            x = int((self._i / max(self._n - 1, 1)) * (self._w - 80))
            img[200:340, x:x + 80] = (40, 200, 40)
        self._i += 1
        return Frame(image=img, timestamp=time.time(), source=self)

    def stop(self) -> None:
        self._stopped = True

    @property
    def fps(self): return 30.0

    @property
    def width(self): return self._w

    @property
    def height(self): return self._h


class FileSource(FrameSource):
    def __init__(self, path: Path):
        import cv2
        self._cap = cv2.VideoCapture(str(path))
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open {path}")
        self._stopped = False

    def read(self) -> Optional[Frame]:
        if self._stopped:
            return None
        ok, img = self._cap.read()
        if not ok:
            self.stop()
            return None
        return Frame(image=img, timestamp=time.time(), source=self)

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            self._cap.release()

    @property
    def fps(self): return self._cap.get(5) or 25.0

    @property
    def width(self): return int(self._cap.get(3))

    @property
    def height(self): return int(self._cap.get(4))
