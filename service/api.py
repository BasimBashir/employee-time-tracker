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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db as dbmod

# Where the built UI lands (ui/dist/renderer), most-specific first: the
# installed layout, then the repo checkout for dev. Overridable via ui_dir so
# tests can point elsewhere without touching either.
_UI_DIRS = (
    Path(r"C:\Program Files\EmployeeTracker\ui\dist\renderer"),
    Path(__file__).resolve().parents[1] / "ui" / "dist" / "renderer",
)

# Only the Vite dev server is cross-origin to the API. The packaged dashboard
# is served by this service itself (same origin), so it needs no CORS entry —
# and the API must stay invisible to arbitrary web pages in the user's browser.
_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def find_ui_dir() -> Path | None:
    for d in _UI_DIRS:
        if d.is_dir():
            return d
    return None


def create_app(conn, cfg, ui_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Employee Tracker")
    app.state.conn = conn
    app.state.cfg = cfg

    ui_dir = Path(ui_dir) if ui_dir else find_ui_dir()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_methods=["GET", "DELETE"],
        allow_headers=["Content-Type"],
    )

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
