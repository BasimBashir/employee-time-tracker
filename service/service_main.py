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
    config = uvicorn.Config(app, host="127.0.0.1", port=cfg.port, log_level="warning")
    server = uvicorn.Server(config)

    def request_restart():
        log.warning("watchdog: rss over ceiling — restart requested")

    watchdog = Watchdog(cfg, restart_callback=request_restart)
    return server, watchdog


class TrackerService(win32serviceutil.ServiceFramework):
    _svc_name_ = "EmployeeTrackerService"
    _svc_display_name_ = "Employee Tracker Service"
    _svc_description_ = "Captures entry/exit crossings and records attendance."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server = None
        self.watchdog = None
        self._threads: list[threading.Thread] = []

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        cfg = load_config()
        self.server, self.watchdog = build_server(cfg)
        self.watchdog.start()
        t = threading.Thread(target=self.server.run, name="uvicorn", daemon=False)
        t.start()
        self._threads = [self.watchdog.thread, t]
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        self._shutdown()

    def _shutdown(self):
        if self.server:
            self.server.should_exit = True
        for t in self._threads:
            if t:
                t.join(timeout=10)
        if self.watchdog:
            self.watchdog.stop()


def run_foreground(cfg=None) -> None:
    """Run the API + watchdog in this process (console / debug mode)."""
    cfg = cfg or load_config()
    server, watchdog = build_server(cfg)
    watchdog.start()
    try:
        server.run()
    finally:
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
        # install/start/stop/restart/remove go through pywin32
        win32serviceutil.HandleCommandLine(TrackerService, argv=[args.action])


if __name__ == "__main__":
    main()
