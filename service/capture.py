"""GStreamer RTSP capture with supervised reconnect.

`gi` is imported lazily on purpose: machines without GStreamer (and every test
run) can import this module and use build_pipeline_string(); only constructing
an RtspGstSource requires the GStreamer runtime.
"""
import random
import threading
import time
from typing import Optional

import numpy as np

from .device import pick_decoder
from .framesource import FrameSource, Frame


def build_pipeline_string(url: str, decoder: str, latency_ms: int) -> str:
    return (
        f"rtspsrc location={url} latency={latency_ms} protocols=tcp "
        f"buffer-mode=0 drop-on-latency=true ! "
        f"rtph264depay ! h264parse ! {decoder} ! videoconvert ! "
        f"video/x-raw,format=BGR ! appsink max-buffers=2 drop=true sync=false"
    )


class RtspGstSource(FrameSource):
    """GStreamer appsink capture with exponential-backoff reconnect.

    Bounded by construction: appsink holds at most max_buffers and drops, so a
    slow consumer never grows memory no matter how long the camera runs.
    """

    def __init__(self, rtsp_url: str, decoder: Optional[str] = None,
                 latency_ms: int = 200, max_buffers: int = 2):
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        self._Gst = Gst
        Gst.init(None)
        self.url = rtsp_url
        self.decoder = decoder or pick_decoder()
        self.latency_ms = latency_ms
        self.max_buffers = max_buffers
        self.reconnect_attempts = 0
        self.state = "starting"
        self._backoff = 1.0
        self._stopped = False
        self._pipeline = None
        self._lock = threading.Lock()
        self._start()

    # -- lifecycle -----------------------------------------------------------
    def _start(self) -> None:
        spec = build_pipeline_string(self.url, self.decoder, self.latency_ms)
        self._pipeline = self._Gst.parse_launch(spec)
        self._pipeline.set_state(self._Gst.State.PLAYING)
        self.state = "streaming"

    def _next_delay(self) -> float:
        d = min(self._backoff, 30.0)
        self._backoff = min(self._backoff * 2, 30.0)
        return d + random.uniform(0, 0.5)     # jitter; avoids thundering herd

    def _reconnect(self) -> None:
        self.reconnect_attempts += 1
        self.state = "reconnecting"
        with self._lock:
            try:
                self._pipeline.set_state(self._Gst.State.NULL)
                self._pipeline = None
            except Exception:
                pass
        time.sleep(self._next_delay())
        if not self._stopped:
            self._start()

    # -- FrameSource ---------------------------------------------------------
    def read(self) -> Optional[Frame]:
        if self._stopped or self._pipeline is None:
            return None
        try:
            sample = self._find_sink().try_pull_sample(100 * self._Gst.MSECOND)
        except Exception:
            self._reconnect()
            return None
        if sample is None:
            return None
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(self._Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            # Frame geometry lives in the caps — never assume the camera's
            # resolution, reshape from what actually arrived.
            s = sample.get_caps().get_structure(0)
            h, w = s.get_value("height"), s.get_value("width")
            arr = np.frombuffer(bytes(mapinfo.data), np.uint8)
            img = arr[: h * w * 3].reshape(h, w, 3)
        finally:
            buf.unmap(mapinfo)
        return Frame(image=np.ascontiguousarray(img),
                     timestamp=buf.pts / self._Gst.SECOND, source=self)

    def _find_sink(self):
        for el in self._pipeline.iterate_elements():
            if el.get_factory().get_name() == "appsink":
                return el
        raise RuntimeError("appsink not found in pipeline")

    def stop(self) -> None:
        self._stopped = True
        with self._lock:
            if self._pipeline is not None:
                self._pipeline.set_state(self._Gst.State.NULL)
                self._pipeline = None

    @property
    def fps(self): return 25.0

    @property
    def width(self): return 1920

    @property
    def height(self): return 1080
