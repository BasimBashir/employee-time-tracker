"""Resource watchdog.

Observes only — it never writes attendance data. When RSS passes the ceiling it
raises the restart callback once, which the service turns into a supervised worker
restart that keeps the database and already-written clips intact."""
import os
import threading
import time

import psutil

DEFAULT_INTERVAL_SECONDS = 60


class Watchdog:
    def __init__(self, cfg, restart_callback, interval_seconds: int = DEFAULT_INTERVAL_SECONDS):
        self.cfg = cfg
        self.restart_callback = restart_callback
        self.restart_requested = False
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def sample(self) -> dict:
        p = psutil.Process(os.getpid())
        rss_mb = p.memory_info().rss / 1048576
        return {
            "rss_mb": round(rss_mb, 1),
            "threads": p.num_threads(),
            "over_ceiling": rss_mb > self.cfg.watchdog_rss_ceiling_mb,
        }

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.check()

    def check(self) -> None:
        """Sample and fire the callback at most once; callable from tests."""
        s = self.sample()
        if s["over_ceiling"] and not self.restart_requested:
            self.restart_requested = True
            self.restart_callback()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="watchdog", daemon=False)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=5)
