import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

JPEG_QUALITY = [int(cv2.IMWRITE_JPEG_QUALITY), 75]


class ClipRecorder:
    """
    Rolling JPEG window per camera so memory stays flat: 15 s @ 15 fps @ ~60 KB
    is ~14 MB, versus ~350 MB for raw BGR frames — raw frames are not acceptable
    on the 16 GB target machine. capture() writes pre-roll plus whatever frames
    arrive during the post window, and returns only once the file is complete.
    """

    def __init__(self, clips_dir: Path, pre_seconds: int = 10, post_seconds: int = 5,
                 fps: float = 15.0, max_seconds: float = 15.0):
        self.clips_dir = Path(clips_dir)
        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self.fps = fps
        self.max_seconds = max_seconds
        self._cap = max(1, int(fps * max_seconds))
        self._buffer: deque[tuple[float, bytes]] = deque(maxlen=self._cap)
        self._lock = threading.Lock()
        self._captures: list[dict] = []          # open captures collecting post-roll
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._writer_thread: Optional[threading.Thread] = None

    # -- ingest --------------------------------------------------------------
    def add_frame(self, image: np.ndarray, timestamp: float) -> None:
        ok, jpg = cv2.imencode(".jpg", image, JPEG_QUALITY)
        if not ok:
            return
        item = (timestamp, jpg.tobytes())
        with self._lock:
            self._buffer.append(item)
            now = time.monotonic()
            for cap in self._captures:
                if now <= cap["deadline"]:
                    cap["post"].append(item)

    # -- outtake -------------------------------------------------------------
    def capture(self, event_type: str, occurred_at: datetime,
                employee_id: int, camera_id: int) -> Path:
        day_dir = self.clips_dir / occurred_at.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        out = day_dir / (f"emp{employee_id}_cam{camera_id}_{event_type}_"
                         f"{occurred_at.strftime('%H%M%S')}.mp4")
        cap = {"out": out, "pre": self._snapshot_now(), "post": [],
               "deadline": time.monotonic() + self.post_seconds,
               "done": threading.Event()}
        with self._lock:
            self._captures.append(cap)
        self._ensure_writer()
        self._q.put(cap)
        cap["done"].wait(timeout=self.post_seconds + 15)
        with self._lock:
            self._captures.remove(cap)
        return out

    def _snapshot_now(self) -> list[tuple[float, bytes]]:
        with self._lock:
            return list(self._buffer)

    # -- writer thread -------------------------------------------------------
    def _ensure_writer(self) -> None:
        if self._writer_thread and self._writer_thread.is_alive():
            return
        self._writer_thread = threading.Thread(target=self._writer_loop,
                                               name="clipwriter", daemon=False)
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        while True:
            try:
                cap = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    break
                continue
            self._write(cap)

    def _write(self, cap: dict) -> None:
        try:
            frames = list(cap["pre"])
            frames.extend(cap["post"])
            if not frames:
                return
            first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
            h, w = first.shape[:2]
            writer = cv2.VideoWriter(str(cap["out"]), cv2.VideoWriter_fourcc(*"mp4v"),
                                     self.fps, (w, h))
            try:
                for _, jpg in frames:
                    self._write_jpg(writer, jpg)
                while time.monotonic() < cap["deadline"]:
                    with self._lock:
                        extra, cap["post"] = cap["post"], []
                    for _, jpg in extra:
                        self._write_jpg(writer, jpg)
                    if not extra:
                        time.sleep(0.05)
                with self._lock:                 # stragglers from the final tick
                    extra, cap["post"] = cap["post"], []
                for _, jpg in extra:
                    self._write_jpg(writer, jpg)
            finally:
                writer.release()
        finally:
            cap["done"].set()

    @staticmethod
    def _write_jpg(writer: cv2.VideoWriter, jpg: bytes) -> None:
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            writer.write(img)

    def memory_bytes_estimate(self) -> int:
        with self._lock:
            return sum(len(j) for _, j in self._buffer)

    def stop(self) -> None:
        self._stop.set()
        if self._writer_thread:
            self._writer_thread.join(timeout=15)
            self._writer_thread = None
