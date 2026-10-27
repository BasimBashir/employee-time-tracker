"""Localhost JSON API — the only HTTP surface.

The service, the tray app and the UI all talk to the system through this module.
Nobody opens the database directly except service/db.py.

The service also serves the built dashboard from disk, so the tray's "Open
dashboard" shortcut and any browser on the machine can load it without Electron
having to run its own server.
"""
import os
from pathlib import Path

import psutil
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db as dbmod

# Where the UI build lands (ui/dist/renderer). Overridable so tests can point
# elsewhere without touching the installed layout.
UI_DIR = Path(r"C:\Program Files\EmployeeTracker\ui\dist\renderer")


def create_app(conn, cfg, ui_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Employee Tracker")
    app.state.conn = conn
    app.state.cfg = cfg

    ui_dir = Path(ui_dir) if ui_dir else UI_DIR

    @app.get("/health")
    def health():
        return {"status": "ok", "pid": os.getpid()}

    @app.get("/health/rss")
    def rss():
        p = psutil.Process(os.getpid())
        return {"rss_mb": round(p.memory_info().rss / 1048576, 1)}

    @app.get("/events")
    def events(since: str):
        rows = conn.execute(
            "SELECT * FROM events WHERE occurred_at >= ? ORDER BY id", (since,)).fetchall()
        return [dict(r) for r in rows]

    @app.get("/sessions")
    def sessions(date: str):
        rows = conn.execute(
            """SELECT s.*, e.name AS employee_name FROM sessions s
                 JOIN employees e ON e.id = s.employee_id
                WHERE s.date = ? AND s.deleted_at IS NULL ORDER BY s.id""",
            (date,)).fetchall()
        return [dict(r) for r in rows]

    @app.delete("/sessions/{session_id}")
    def del_session(session_id: int, body: dict = Body(...)):
        exists = conn.execute("SELECT 1 FROM sessions WHERE id=?",
                              (session_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id}")
        dbmod.soft_delete_session(conn, session_id,
                                  body.get("actor", "?"), body.get("reason", ""))
        return {"deleted": session_id}

    # --- dashboard ---------------------------------------------------------
    if ui_dir.is_dir():
        # Mounted last so /health, /events and /sessions keep their meanings.
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    return app
