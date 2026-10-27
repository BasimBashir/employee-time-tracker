"""Frame sources — where frames come from.

A FrameSource never inspects frame contents; that is the detector's job.
Every source is bounded by construction: read() returns frames one at a time
and None at end-of-stream or after stop().
"""
import abc, threading, time
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


class RtspCvSource(FrameSource):
    """RTSP via OpenCV/FFmpeg — the bridge source while GStreamer/PyGObject has
    no Windows wheels for this Python. Same observable surface as
    RtspGstSource (state, reconnect_attempts) so /pipeline/status is identical.

    shortcut: FFmpeg's own connect timeout is long, so a TCP probe gates every
    open; once a stream is live a dying mid-read still blocks until it fails.
    Upgrade to RtspGstSource when gi becomes installable.
    """

    def __init__(self, rtsp_url: str, timeout_s: float = 5.0):
        import os
        import cv2
        self._cv2 = cv2
        self.url = rtsp_url
        self.timeout_s = timeout_s
        self.reconnect_attempts = 0
        self.state = "starting"
        self._cap = None
        self._stopped = False
        self._backoff = 1.0
        self._wake = threading.Event()
        # Socket timeout so a dead peer fails read() instead of freezing it.
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            f"rtsp_transport;tcp|timeout;{int(timeout_s * 1e6)}")
        self._open()

    def _open(self) -> None:
        # A dead peer makes isOpened() block for FFmpeg's own connect timeout
        # (tens of seconds); a cheap TCP probe first keeps backoff meaningful.
        if not self._port_open():
            self.state = "reconnecting"
            return
        cap = self._cv2.VideoCapture(self.url, self._cv2.CAP_FFMPEG)
        if cap.isOpened():
            self._cap = cap
            self.state = "streaming"
        else:
            self.state = "reconnecting"

    def _port_open(self) -> bool:
        host, port = self._url_host_port()
        if host is None:
            return True                     # unparsable: let FFmpeg decide
        import socket
        try:
            with socket.create_connection((host, port), timeout=self.timeout_s):
                return True
        except OSError:
            return False

    def _url_host_port(self):
        from urllib.parse import urlparse
        u = urlparse(self.url)
        if not u.hostname:
            return None, None
        return u.hostname, u.port or 554

    def _reconnect(self) -> None:
        self.reconnect_attempts += 1
        self.state = "reconnecting"
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._wake.wait(min(self._backoff, 30.0))
        self._backoff = min(self._backoff * 2, 30.0)
        if not self._stopped:
            self._open()

    def read(self) -> Optional[Frame]:
        if self._stopped:
            return None
        if self._cap is None:
            self._reconnect()
            if self._cap is None:
                return None
        ok, img = self._cap.read()
        if not ok:
            self._reconnect()
            return None
        return Frame(image=img, timestamp=time.time(), source=self)

    def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def fps(self): return (self._cap.get(5) if self._cap else 0) or 25.0

    @property
    def width(self): return int(self._cap.get(3)) if self._cap else 0

    @property
    def height(self): return int(self._cap.get(4)) if self._cap else 0
