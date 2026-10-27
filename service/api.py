"""Localhost JSON API — the only HTTP surface.

The service, the tray app and the UI all talk to the system through this module.
Nobody opens the database directly except service/db.py.

The service also serves the built dashboard from disk, so the tray's "Open
dashboard" shortcut and any browser on the machine can load it without Electron
having to run its own server.
"""
import csv
import io
import os
from pathlib import Path

import psutil
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db as dbmod
from . import notifier as notifiermod

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

    # --- reports -----------------------------------------------------------
    # Delivery trips are work and exempt; the allowance resolves per employee,
    # falling back to the config default (60 when cfg is None, as in tests).
    def _allowed_default() -> int:
        if cfg is None:
            return 60
        return getattr(cfg, "default_allowed_out_minutes", 60)

    @app.get("/report/daily")
    def report_daily(date: str):
        return dbmod.daily_report(conn, date, _allowed_default())

    @app.get("/report/weekly")
    def report_weekly(end: str, days: int = 7):
        return dbmod.weekly_report(conn, end, days, _allowed_default())

    @app.get("/report/daily.csv")
    def report_daily_csv(date: str):
        """The employer's export: one row per employee for the day."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["employee_id", "name", "code", "department", "role",
                         "trips", "open_trips", "break_min", "delivery_min",
                         "total_min", "allowed_min", "extra_min"])
        for r in dbmod.daily_report(conn, date, _allowed_default()):
            writer.writerow([r["employee_id"], r["name"], r["employee_code"] or "",
                             r["department"] or "", r["role"], r["trips"],
                             r["open_trips"], r["break_minutes"],
                             r["delivery_minutes"], r["total_minutes"],
                             r["allowed_minutes"], r["extra_minutes"]])
        return Response(
            buf.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=out-time-{date}.csv"})

    @app.patch("/employees/{employee_id}")
    def patch_employee(employee_id: int, body: dict = Body(...)):
        if conn.execute("SELECT 1 FROM employees WHERE id=?",
                        (employee_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"no employee {employee_id}")
        allowance = (body["allowed_out_minutes"]
                     if "allowed_out_minutes" in body else dbmod.UNSET)
        phone = body["phone"] if "phone" in body else dbmod.UNSET
        try:
            dbmod.update_employee(conn, employee_id, role=body.get("role"),
                                  allowed_out_minutes=allowance, phone=phone)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = conn.execute(
            "SELECT id, name, role, allowed_out_minutes, phone FROM employees WHERE id=?",
            (employee_id,)).fetchone()
        return dict(row)

    @app.get("/notify/status")
    def notify_status():
        """WhatsApp bridge link state for the dashboard; never raises."""
        if cfg is None or not getattr(cfg, "notify_enabled", False):
            return {"enabled": False}
        return {"enabled": True, **notifiermod.bridge_status(cfg)}

    @app.get("/notify/qr")
    def notify_qr():
        """The bridge's pairing QR, proxied so the dashboard stays same-origin."""
        if cfg is None or not getattr(cfg, "notify_enabled", False):
            raise HTTPException(status_code=404)
        png = notifiermod.bridge_qr(cfg)
        if png is None:
            raise HTTPException(status_code=404)
        return Response(png, media_type="image/png")

    @app.patch("/sessions/{session_id}")
    def patch_session(session_id: int, body: dict = Body(...)):
        if "purpose" not in body:
            raise HTTPException(status_code=400, detail="purpose is required")
        if conn.execute("SELECT 1 FROM sessions WHERE id=?",
                        (session_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id}")
        try:
            dbmod.set_session_purpose(conn, session_id, body["purpose"],
                                      body.get("actor", "?"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"updated": session_id, "purpose": body["purpose"]}

    # --- dashboard ---------------------------------------------------------
    if ui_dir.is_dir():
        # Mounted last so /health, /events and /sessions keep their meanings.
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    return app
