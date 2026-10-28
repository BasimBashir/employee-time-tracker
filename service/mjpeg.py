"""Multipart MJPEG feed from the analysis pipeline. Deliberately cheap: frames
are encoded once and shared by reference, the overlay carries the meaning."""
import queue
import threading

import cv2
import numpy as np


class MjpegServer:
    def __init__(self, max_fps: float = 6.0):
        self.max_fps = max_fps
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._stop = threading.Event()

    def publish(self, image: np.ndarray) -> None:
        if self._stop.is_set():
            return
        ok, jpg = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if not ok:
            return
        data = jpg.tobytes()
        with self._lock:
            self._frame = data
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(data)      # slow viewers drop frames, never block analysis
            except queue.Full:
                pass

    def attach(self, pipe) -> None:
        """Occupy the pipeline's on_tracks seam. When the live Decider is wired
        (phase 6+), it must compose with this, not replace it."""
        pipe.on_tracks = lambda tracks, frame: self.publish(frame.image)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2)
        with self._lock:
            if self._frame is not None:
                q.put(self._frame)      # new viewer sees the current frame at once
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def stop(self) -> None:
        self._stop.set()
