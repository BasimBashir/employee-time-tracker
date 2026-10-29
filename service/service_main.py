"""Windows Service wrapper.

The service owns the API server, the watchdog and every worker that must keep
running from boot until shutdown. The UI is a client: closing it changes nothing.

Run as a service (installed with `python service/service_main.py install`), or in
the foreground with `python service/service_main.py debug` / `run`.
"""
import argparse
import threading
import time

import uvicorn
import win32event
import win32service
import win32serviceutil
import servicemanager

from .api import create_app
from .config import load_config
from .db import connect, migrate
from .logging_setup import service_logger
from .watchdog import Watchdog

log = service_logger("service_main")


def build_server(cfg):
    """Build (uvicorn.Server, Watchdog). Importable without pywin32 so it can be
    tested headlessly; the service class below is the only pywin32-dependent part."""
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    migrate(conn)
    app = create_app(conn, cfg)

    # Analysis pipeline (Phase 2). Hollow without a `dev_source`; starts only
    # when there is something to capture.
    from .pipeline import make_pipeline
    pipe = make_pipeline(cfg)
    app.state.pipeline = pipe

    # Live camera feed (Phase 5): publish analysed frames to MJPEG viewers.
    from .mjpeg import MjpegServer
    mjpeg = MjpegServer(max_fps=6.0)
    app.state.mjpeg = mjpeg
    mjpeg.attach(pipe)

    if pipe.source is not None:
        pipe.start()

    # log_config=None: uvicorn's default formatter calls sys.stdout.isatty(),
    # and a service process has no console (stdout is None) — dictConfig raised
    # "Unable to configure formatter 'default'" and killed SvcDoRun. Our JSON
    # logging via service_logger is the only config the API needs.
    config = uvicorn.Config(app, host="127.0.0.1", port=cfg.port,
                            log_level="warning", log_config=None)
    server = uvicorn.Server(config)

    def request_restart():
        log.warning("watchdog: rss over ceiling — restart requested")

    watchdog = Watchdog(cfg, restart_callback=request_restart)
    return server, watchdog


def stop_pipeline(server) -> None:
    """Stop the analysis pipeline attached to the server's app, if any."""
    if server is None:
        return
    app = getattr(server.config, "app", None)
    pipe = getattr(app.state, "pipeline", None) if app is not None else None
    if pipe is not None:
        pipe.stop()
    mjpeg = getattr(app.state, "mjpeg", None) if app is not None else None
    if mjpeg is not None:
        mjpeg.stop()


def _nightly_loop(conn, cfg, stop: threading.Event) -> None:
    """23:59 daily housekeeping: force-close sessions still open so a missing
    return never becomes an infinite timer, then purge expired clips."""
    from datetime import datetime
    from .db import force_close_open_sessions, audit
    from .retention import nightly_retention
    fired_on = None
    while not stop.is_set():
        now = datetime.now()
        if now.hour == 23 and now.minute >= 59 and fired_on != now.date().isoformat():
            try:
                n = force_close_open_sessions(conn, now.date().isoformat())
                audit(conn, "system", "midnight_rollover", now.date().isoformat(),
                      {"forced": n})
                nightly_retention(cfg, conn)
                fired_on = now.date().isoformat()
            except Exception:
                log.exception("nightly rollover failed")
        stop.wait(timeout=30)


def start_nightly(conn, cfg) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    t = threading.Thread(target=_nightly_loop, args=(conn, cfg, stop),
                         name="nightly", daemon=False)
    t.start()
    return t, stop


class TrackerService(win32serviceutil.ServiceFramework):
    _svc_name_ = "EmployeeTrackerService"
    _svc_display_name_ = "Employee Tracker Service"
    _svc_description_ = "Captures entry/exit crossings and records attendance."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server = None
        self.watchdog = None
        self.nightly_stop: threading.Event | None = None
        self._threads: list[threading.Thread] = []

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        try:
            self._run()
        except Exception:
            # pywin32 only reports this to the event log; service.log must hold
            # the traceback too, or a dead service looks undiagnosable.
            log.exception("service crashed in SvcDoRun")
            raise

    def _run(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        cfg = load_config()
        self.server, self.watchdog = build_server(cfg)
        self.watchdog.start()
        conn = self.server.config.app.state.conn
        nightly_t, self.nightly_stop = start_nightly(conn, cfg)
        t = threading.Thread(target=self._serve, name="uvicorn", daemon=False)
        t.start()
        self._threads = [self.watchdog.thread, t, nightly_t]
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        self._shutdown()

    def _serve(self) -> None:
        """uvicorn's blocking form; runs on the worker thread."""
        import asyncio
        asyncio.run(self.server.serve())

    def _shutdown(self):
        if self.server:
            stop_pipeline(self.server)
            self.server.should_exit = True
        if self.nightly_stop:
            self.nightly_stop.set()
        for t in self._threads:
            if t:
                t.join(timeout=10)
        if self.watchdog:
            self.watchdog.stop()


def run_foreground(cfg=None) -> None:
    """Run the API + watchdog in this process (console / debug mode).

    `Server.run()` is not reliable as the blocking entry point here — it returns
    instead of serving. `asyncio.run(server.serve())` is uvicorn's documented
    blocking form and holds the loop open until shutdown.
    """
    import asyncio
    cfg = cfg or load_config()
    server, watchdog = build_server(cfg)
    watchdog.start()
    nightly_t, nightly_stop = start_nightly(server.config.app.state.conn, cfg)
    try:
        asyncio.run(server.serve())
    finally:
        nightly_stop.set()
        nightly_t.join(timeout=5)
        stop_pipeline(server)
        server.should_exit = True
        watchdog.stop()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Employee Tracker service control")
    parser.add_argument("action", nargs="?", default="run",
                        choices=["run", "debug", "install", "start", "stop", "restart",
                                 "remove"])
    args = parser.parse_args(argv)
    if args.action in ("run", "debug"):
        # both run in the foreground: debug is a console alias used when developing
        run_foreground()
    else:
        # install/start/stop/restart/remove go through pywin32. HandleCommandLine
        # parses argv[1:], so the program name has to head the list.
        win32serviceutil.HandleCommandLine(TrackerService, argv=[sys.argv[0], args.action])


if __name__ == "__main__":
    main()
