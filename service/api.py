"""Localhost JSON API — the only HTTP surface.

The service, the tray app and the UI all talk to the system through this module.
Nobody opens the database directly except service/db.py.

The service also serves the built dashboard from disk, so the tray's "Open
dashboard" shortcut and any browser on the machine can load it without Electron
having to run its own server.
"""
import calendar
import csv
import io
import itertools
import os
import queue
import re
import sqlite3
import time
import uuid
from datetime import date as datecls, datetime
from pathlib import Path

import psutil
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db as dbmod
from . import namaz as namazmod
from . import notifier as notifiermod
from .config import DEFAULT_DATA_ROOT

# Upload types: avatars are small images; training media may be video too.
_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_VIDEO_TYPES = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}
_MEDIA_TYPES = {**_IMAGE_TYPES, **_VIDEO_TYPES}
_SUFFIX_TYPES = {v: k for k, v in _MEDIA_TYPES.items()}
_AVATAR_MAX_BYTES = 3 * 1024 * 1024
_MEDIA_MAX_BYTES = 100 * 1024 * 1024
_MEDIA_SEQ = itertools.count()

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
    from .resolver import Resolver
    app.state.resolver = Resolver(conn)

    ui_dir = Path(ui_dir) if ui_dir else find_ui_dir()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_methods=["GET", "DELETE", "PATCH", "POST"],
        allow_headers=["Content-Type", "X-Tracker-Password"],
    )

    # --- local password gate (Phase 5) ---------------------------------------
    # Mutating endpoints take this dependency; the UI sends the password in the
    # X-Tracker-Password header. No password configured -> open (first run).
    def _require_auth(x_tracker_password: str = Header(default="")):
        from .password import PasswordGate
        gate = PasswordGate(conn)
        if not gate.is_set():
            return
        try:
            gate.require(x_tracker_password)
        except PermissionError:
            dbmod.audit(conn, "unknown", "unauthorized_write", "", {})
            raise HTTPException(status_code=401, detail="password required or incorrect")

    @app.get("/auth/status")
    def auth_status():
        from .password import PasswordGate
        return {"set": PasswordGate(conn).is_set()}

    @app.post("/auth/set")
    def auth_set(body: dict = Body(...)):
        from .password import PasswordGate
        gate = PasswordGate(conn)
        if gate.is_set() and not gate.verify(body.get("current", "")):
            dbmod.audit(conn, "unknown", "password_set_denied", "", {})
            raise HTTPException(status_code=401, detail="current password incorrect")
        pw = str(body.get("password") or "")
        if len(pw) < 4:
            raise HTTPException(status_code=400, detail="password must be at least 4 characters")
        gate.set(pw)
        dbmod.audit(conn, "owner", "password_set", "", {})
        return {"ok": True}

    @app.post("/auth/unlock")
    def auth_unlock(body: dict = Body(...)):
        from .password import PasswordGate
        ok = PasswordGate(conn).verify(body.get("password", ""))
        if not ok:
            dbmod.audit(conn, "unknown", "unlock_failed", "", {})
        return {"ok": ok}


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
            """SELECT s.*, e.name AS employee_name, e.out_windows,
                      e.namaz_prayers, e.namaz_offset_min, e.namaz_duration_min,
                      eo.clip_path AS out_clip, ei.clip_path AS in_clip
                 FROM sessions s
                 JOIN employees e ON e.id = s.employee_id
                 LEFT JOIN events eo ON eo.id = s.out_event_id
                 LEFT JOIN events ei ON ei.id = s.in_event_id
                WHERE s.date = ? AND s.deleted_at IS NULL ORDER BY s.id""",
            (date,)).fetchall()
        # Flag trips that started outside the employee's allowed windows —
        # profile windows plus that day's namaz slots — so the table can tag
        # them; no windows at all means never flagged.
        settings = dbmod.get_namaz_settings(conn)
        rule_cols = ("out_windows", "namaz_prayers", "namaz_offset_min",
                     "namaz_duration_min")
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys() if k not in rule_cols}
            spans = dbmod.effective_spans(
                conn, r["out_windows"], date, r["namaz_prayers"],
                r["namaz_offset_min"], r["namaz_duration_min"], settings)
            d["outside_window"] = dbmod.violates(
                spans, datetime.fromisoformat(d["out_at"]))
            out.append(d)
        return out

    @app.delete("/sessions/{session_id}", dependencies=[Depends(_require_auth)])
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

    def _live_employee(employee_id: int) -> None:
        if conn.execute("SELECT 1 FROM employees WHERE id=? AND deleted_at IS NULL",
                        (employee_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"no employee {employee_id}")

    def _profile(employee_id: int) -> dict:
        row = conn.execute(
            """SELECT id, name, employee_code, department, role, allowed_out_minutes,
                      phone, out_windows, avatar_path, namaz_prayers,
                      namaz_offset_min, namaz_duration_min
                 FROM employees WHERE id=?""", (employee_id,)).fetchone()
        d = dict(row)
        d["employee_id"] = d.pop("id")
        d["out_windows"] = dbmod.windows_text(d["out_windows"])
        d["has_avatar"] = bool(d.pop("avatar_path"))
        return d

    # --- namaz timetable ---------------------------------------------------
    def _hhmm(minutes: int | None) -> str | None:
        return None if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"

    def _parse_hhmm(text, what: str) -> int | None:
        """"HH:MM" (or None = clear) into minutes-of-day; 400 on junk."""
        if text is None or text == "":
            return None
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(text).strip())
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise HTTPException(status_code=400, detail=f"{what} must be HH:MM")
        return int(m.group(1)) * 60 + int(m.group(2))

    def _namaz_settings_view() -> dict:
        st = dbmod.get_namaz_settings(conn)
        return {"offsets": st["offsets"], "zuhr": _hhmm(st["zuhr_fixed_min"]),
                "jummah": _hhmm(st["jummah_min"]), "duration_min": st["duration_min"]}

    @app.get("/namaz")
    def namaz_timetable(year: int | None = None, month: int | None = None):
        """The editable timetable: one row per day (the month, or the whole
        year when month is omitted). Each cell is the effective start —
        fixed Zuhr clock time or computed sun time plus jamaat offsets —
        with any per-date overrides marked."""
        today = datecls.today()
        year = year or today.year
        settings = dbmod.get_namaz_settings(conn)
        days = []
        for m in (month,) if month else range(1, 13):
            for day in range(1, calendar.monthrange(year, m)[1] + 1):
                d = datecls(year, m, day)
                ds = d.isoformat()
                overridden = {r["prayer"] for r in conn.execute(
                    "SELECT prayer FROM namaz_overrides WHERE date=?", (ds,))}
                days.append({
                    "date": ds,
                    **{p: _hhmm(dbmod.namaz_start(conn, ds, p, settings))
                       for p in namazmod.PRAYERS},
                    "jummah": d.weekday() == 4,
                    "overridden": sorted(overridden),
                })
        return {"year": year, "month": month,
                "settings": _namaz_settings_view(), "days": days}

    @app.patch("/namaz")
    def patch_namaz(body: dict = Body(...)):
        try:
            minutes = _parse_hhmm(body.get("time"), "time")
            dbmod.set_namaz_override(conn, str(body.get("date") or ""),
                                     body.get("prayer") or "", minutes,
                                     body.get("actor", "dashboard"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"date": body.get("date"), "prayer": body.get("prayer"),
                "time": body.get("time")}

    @app.patch("/namaz/settings")
    def patch_namaz_settings(body: dict = Body(...)):
        picked = lambda key: (body[key] if key in body else dbmod.UNSET)
        try:
            dbmod.set_namaz_settings(
                conn,
                offsets=body.get("offsets") if isinstance(body.get("offsets"), dict) else None,
                zuhr_fixed_min=_parse_hhmm(body["zuhr"], "zuhr")
                if "zuhr" in body else dbmod.UNSET,
                jummah_min=_parse_hhmm(body["jummah"], "jummah")
                if "jummah" in body else dbmod.UNSET,
                duration_min=picked("duration_min"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _namaz_settings_view()

    # --- employees ---------------------------------------------------------
    @app.get("/employees")
    def employees_list(date: str | None = None):
        return dbmod.list_employees(conn, windows_date=date)

    @app.post("/employees", dependencies=[Depends(_require_auth)])
    def add_employee(body: dict = Body(...)):
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        role = body.get("role") or "staff"
        if role not in ("staff", "delivery"):
            raise HTTPException(status_code=400, detail=f"unknown role {role!r}")
        try:
            employee_id = dbmod.add_employee(conn, name, body.get("employee_code"),
                                             body.get("department"), role)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _invalidate()
        return _profile(employee_id)

    @app.delete("/employees/{employee_id}", dependencies=[Depends(_require_auth)])
    def remove_employee(employee_id: int, body: dict = Body(...)):
        _live_employee(employee_id)
        dbmod.deactivate_employee(conn, employee_id,
                                  body.get("actor", "?"), body.get("reason", ""))
        _invalidate()
        return {"deleted": employee_id}

    @app.patch("/employees/{employee_id}", dependencies=[Depends(_require_auth)])
    def patch_employee(employee_id: int, body: dict = Body(...)):
        _live_employee(employee_id)
        picked = lambda key: (body[key] if key in body else dbmod.UNSET)
        try:
            dbmod.update_employee(conn, employee_id, role=body.get("role"),
                                  allowed_out_minutes=picked("allowed_out_minutes"),
                                  phone=picked("phone"), name=picked("name"),
                                  employee_code=picked("employee_code"),
                                  department=picked("department"),
                                  out_windows=picked("out_windows"),
                                  namaz_prayers=picked("namaz_prayers"),
                                  namaz_offset_min=picked("namaz_offset_min"),
                                  namaz_duration_min=picked("namaz_duration_min"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _profile(employee_id)

    @app.post("/employees/{employee_id}/avatar")
    async def upload_avatar(employee_id: int, request: Request):
        _live_employee(employee_id)
        if cfg is None:
            raise HTTPException(status_code=404)
        ext = _IMAGE_TYPES.get((request.headers.get("content-type") or "")
                               .split(";")[0].strip().lower())
        if ext is None:
            raise HTTPException(status_code=415, detail="upload a jpeg, png or webp image")
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty upload")
        if len(data) > _AVATAR_MAX_BYTES:
            raise HTTPException(status_code=413, detail="image larger than 3 MB")
        media = cfg.media_dir
        media.mkdir(parents=True, exist_ok=True)
        for old in media.glob(f"avatar_{employee_id}.*"):
            old.unlink()
        path = media / f"avatar_{employee_id}{ext}"
        path.write_bytes(data)
        dbmod.update_employee(conn, employee_id, avatar_path=str(path))
        return _profile(employee_id)

    @app.get("/employees/{employee_id}/avatar")
    def employee_avatar(employee_id: int):
        row = conn.execute("SELECT avatar_path FROM employees WHERE id=?",
                           (employee_id,)).fetchone()
        if row is None or not row["avatar_path"] or not Path(row["avatar_path"]).is_file():
            raise HTTPException(status_code=404)
        suffix = Path(row["avatar_path"]).suffix.lower()
        return FileResponse(row["avatar_path"], media_type=_SUFFIX_TYPES.get(suffix, "image/jpeg"))

    # Training captures: plain files under media/employees/<id>/ on purpose —
    # the recognition pipeline scans folders, nothing about them is in the DB.
    @app.post("/employees/{employee_id}/media")
    async def upload_media(employee_id: int, request: Request):
        _live_employee(employee_id)
        if cfg is None:
            raise HTTPException(status_code=404)
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        ext = _MEDIA_TYPES.get(ctype)
        if ext is None:
            raise HTTPException(status_code=415,
                                detail="upload a jpeg/png/webp image or mp4/webm/mov video")
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty upload")
        if len(data) > _MEDIA_MAX_BYTES:
            raise HTTPException(status_code=413, detail="file larger than 100 MB")
        d = cfg.employee_media_dir(employee_id)
        d.mkdir(parents=True, exist_ok=True)
        # The sequence keeps same-second uploads sortable by name even when
        # the filesystem rounds their mtimes together; the uuid avoids
        # collisions across service restarts.
        name = (f"{datetime.now():%Y%m%d-%H%M%S}-{next(_MEDIA_SEQ):04d}"
                f"-{uuid.uuid4().hex[:6]}{ext}")
        (d / name).write_bytes(data)
        return {"file": name, "kind": "video" if ext in (".mp4", ".webm", ".mov") else "image",
                "bytes": len(data)}

    @app.get("/employees/{employee_id}/media")
    def media_list(employee_id: int):
        _live_employee(employee_id)
        d = cfg.employee_media_dir(employee_id) if cfg else None
        files = []
        if d and d.is_dir():
            # Newest last; same-second uploads tie on the timestamped name,
            # so mtime breaks the order and name keeps it stable.
            for f in sorted(d.iterdir(),
                            key=lambda f: (f.stat().st_mtime_ns, f.name)):
                if f.is_file() and f.suffix.lower() in _SUFFIX_TYPES:
                    files.append({
                        "file": f.name,
                        "kind": "video" if f.suffix.lower() in (".mp4", ".webm", ".mov")
                                else "image",
                        "bytes": f.stat().st_size})
        return files

    def _media_path(employee_id: int, name: str) -> Path:
        if cfg is None or Path(name).name != name:  # bare filenames only
            raise HTTPException(status_code=404)
        f = cfg.employee_media_dir(employee_id) / name
        if not f.is_file():
            raise HTTPException(status_code=404)
        return f

    @app.get("/employees/{employee_id}/media/{name}")
    def media_file(employee_id: int, name: str):
        f = _media_path(employee_id, name)
        return FileResponse(f, media_type=_SUFFIX_TYPES.get(f.suffix.lower(),
                                                            "application/octet-stream"))

    @app.delete("/employees/{employee_id}/media/{name}")
    def media_delete(employee_id: int, name: str):
        _media_path(employee_id, name).unlink()
        return {"deleted": name}

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

    @app.patch("/sessions/{session_id}", dependencies=[Depends(_require_auth)])
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

    # --- analysis pipeline --------------------------------------------------
    @app.get("/pipeline/status")
    def pipeline_status():
        pipe = getattr(app.state, "pipeline", None)
        if pipe is None:
            return {"running": False}
        from .device import select_provider, pick_decoder
        prov, detail = select_provider()
        return {"running": True, "provider": prov, "provider_detail": detail,
                "decoder": pick_decoder(),
                "reconnects": getattr(pipe.source, "reconnect_attempts", 0),
                **pipe.stats()}

    @app.post("/pipeline/restart")
    def pipeline_restart():
        pipe = getattr(app.state, "pipeline", None)
        if pipe is None:
            raise HTTPException(status_code=404, detail="no pipeline configured")
        from .pipeline import make_pipeline
        pipe.stop()
        pipe = make_pipeline(cfg)
        app.state.pipeline = pipe
        mjpeg = getattr(app.state, "mjpeg", None)
        if mjpeg is not None:
            mjpeg.attach(pipe)
        if pipe.source is not None:
            pipe.start()
        return {"restarted": True, **pipe.stats()}

    # --- live camera feed (Phase 5) -----------------------------------------
    @app.get("/camera/{camera_id}/stream.mjpeg")
    def camera_stream(camera_id: int):
        from fastapi.responses import StreamingResponse
        server = getattr(app.state, "mjpeg", None)
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

        def gen():
            if server is None:
                # No analysis pipeline: a still placeholder keeps the live box
                # showing something instead of a broken image.
                import numpy as np
                import cv2
                ok, jpg = cv2.imencode(".jpg", np.zeros((270, 480, 3), np.uint8))
                if ok:
                    for _ in range(3):
                        yield boundary + jpg.tobytes() + b"\r\n"
                        time.sleep(0.3)
                return
            q = server.subscribe()
            try:
                while True:
                    try:
                        jpg = q.get(timeout=5)
                    except queue.Empty:
                        continue        # camera dark; keep the stream open
                    yield boundary + jpg + b"\r\n"
                    time.sleep(1.0 / server.max_fps)
            finally:
                server.unsubscribe(q)

        return StreamingResponse(gen(),
                                 media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/camera/{camera_id}/overlay.json")
    def camera_overlay(camera_id: int):
        from .zone import get_active_zone
        z = get_active_zone(conn, camera_id)
        if z is None:
            return {"line": None, "direction_out": None}
        # line_points live in the 960x540 analysis frame space; normalise so the
        # renderer can draw over an <img> of any displayed size.
        pts = [(p[0] / 960.0, p[1] / 540.0) for p in z.line_points]
        return {"line": pts, "direction_out": z.direction_out,
                "width": 960, "height": 540}

    # --- crossing zones and evidence clips (Phase 3) ------------------------
    @app.get("/zones")
    def get_zones(camera_id: int):
        from dataclasses import asdict
        from .zone import load_zones
        return [asdict(z) for z in load_zones(conn, camera_id)]

    @app.post("/zones", dependencies=[Depends(_require_auth)])
    def post_zone(body: dict = Body(...)):
        from .zone import Zone, save_zone
        if body.get("direction_out") not in ("POS_TO_NEG", "NEG_TO_POS"):
            raise HTTPException(status_code=400, detail="direction_out must be POS_TO_NEG or NEG_TO_POS")
        if not isinstance(body.get("line_points"), list) or len(body["line_points"]) < 2:
            raise HTTPException(status_code=400, detail="line_points needs at least two points")
        if conn.execute("SELECT 1 FROM cameras WHERE id=?", (body.get("camera_id"),)).fetchone() is None:
            raise HTTPException(status_code=400, detail=f"unknown camera {body.get('camera_id')}")
        z = Zone(None, int(body["camera_id"]),
                 [(float(p[0]), float(p[1])) for p in body["line_points"]],
                 body["direction_out"], bool(body.get("enabled", True)))
        return {"id": save_zone(conn, z)}

    @app.get("/clips")
    def clips(date: str):
        try:
            datecls.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
        root = cfg.clips_dir if cfg else (DEFAULT_DATA_ROOT / "clips")
        day = root / date.replace("-", "/")
        if not day.is_dir():
            return []
        return sorted(str(p) for p in day.rglob("*.mp4"))

    def _clip_path(path: str) -> Path:
        """Resolve a clip request against the clips dir; refuse everything else."""
        root = (cfg.clips_dir if cfg else (DEFAULT_DATA_ROOT / "clips")).resolve()
        target = Path(path).resolve()
        if not str(target).startswith(str(root)):
            raise HTTPException(status_code=400, detail="path outside clips dir")
        return target

    @app.get("/clips/probe")
    def clips_probe(path: str):
        from .cap import probe_clip
        return probe_clip(_clip_path(path))

    @app.get("/clips/download")
    def clips_download(path: str):
        target = _clip_path(path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="clip missing")
        return FileResponse(str(target), media_type="video/mp4")

    @app.post("/retention/purge", dependencies=[Depends(_require_auth)])
    def manual_purge(body: dict = Body(...)):
        from .retention import purge_clips_older_than, clear_clip_paths
        from .db import audit
        days = int(body.get("days", 30))
        root = cfg.clips_dir if cfg else (DEFAULT_DATA_ROOT / "clips")
        purged = purge_clips_older_than(root, days)
        cleared = clear_clip_paths(conn, purged)
        audit(conn, body.get("actor", "owner"), "manual_clip_purge", str(days),
              {"purged": len(purged)})
        return {"purged": len(purged), "clip_paths_cleared": cleared}

    # --- identity (Phase 4) -------------------------------------------------
    def _invalidate():
        r = getattr(app.state, "resolver", None)
        if r:
            r.invalidate()

    @app.get("/resolver/status")
    def resolver_status():
        r = getattr(app.state, "resolver", None)
        if r is None:
            return {"ready": False, "roster_size": 0}
        return {"ready": True, "roster_size": len(r._ids),
                "confirm": r.confirm, "uncertain": r.uncertain}

    @app.post("/resolver/invalidate")
    def resolver_invalidate():
        _invalidate()
        return {"ok": True}

    @app.post("/enrollments", dependencies=[Depends(_require_auth)])
    def enroll(body: dict = Body(...)):
        """Enroll an employee from a video file (no webcam required).
        400 when the file yields no usable face — a rectangle must never
        become a person."""
        from .enrollment import EnrollmentSession, REQUIRED_POSES
        from .faces import FaceEmbedder
        from .framesource import FileSource
        employee_id = int(body.get("employee_id", 0))
        _live_employee(employee_id)
        path = body.get("path")
        if not path:
            raise HTTPException(status_code=400, detail="path required")
        try:
            src = FileSource(Path(path))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        model = (cfg.models_dir / "active" / "face.onnx") if cfg else Path("face.onnx")
        es = EnrollmentSession(src, FaceEmbedder(model), None)
        ok = 0
        for _ in range(REQUIRED_POSES * 4):
            if es.capture_pose():
                ok += 1
            if ok >= REQUIRED_POSES:
                break
        src.stop()
        if ok < REQUIRED_POSES:
            raise HTTPException(status_code=400,
                                detail=f"only {ok}/{REQUIRED_POSES} poses captured")
        dbmod.audit(conn, body.get("actor", "owner"), "enrollment_saved",
                    str(employee_id), {"poses": ok})
        enr = es.finish(conn, employee_id)
        _invalidate()
        return {"enrollment_id": enr, "poses": ok}

    # --- report generation (Phase 5) ----------------------------------------
    # The service owns the filesystem and the DB; the UI only downloads.
    # Needs a real Config (a data root); tests that pass a bare stub or None
    # simply have no report surface, like avatar uploads.
    data_root = getattr(cfg, "data_root", None)
    reports_dir = (Path(data_root) / "reports") if data_root is not None else None
    if reports_dir is not None:
        reports_dir.mkdir(parents=True, exist_ok=True)

    @app.post("/reports/daily", dependencies=[Depends(_require_auth)])
    def gen_daily(body: dict = Body(...)):
        if reports_dir is None:
            raise HTTPException(status_code=404, detail="no data root configured")
        from reports import write_daily_csv, write_daily_xlsx
        date = body.get("date") or ""
        try:
            datecls.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
        files = []
        for fmt in body.get("formats") or ["csv"]:
            if fmt == "csv":
                files.append(str(write_daily_csv(conn, date, reports_dir)))
            elif fmt == "xlsx":
                files.append(str(write_daily_xlsx(conn, date, reports_dir)))
            else:
                raise HTTPException(status_code=400, detail=f"unknown format {fmt!r}")
        dbmod.audit(conn, "owner", "report_generated", date,
                    {"formats": [Path(f).name for f in files]})
        return {"files": files}

    @app.post("/reports/monthly", dependencies=[Depends(_require_auth)])
    def gen_monthly(body: dict = Body(...)):
        if reports_dir is None:
            raise HTTPException(status_code=404, detail="no data root configured")
        from reports import write_monthly_pdf
        ym = body.get("year_month") or ""
        if not re.fullmatch(r"\d{4}-\d{2}", ym):
            raise HTTPException(status_code=400, detail="year_month must be YYYY-MM")
        path = write_monthly_pdf(conn, ym, reports_dir)
        dbmod.audit(conn, "owner", "report_generated", ym, {"file": Path(path).name})
        return {"file": str(path)}

    if reports_dir is not None:
        # Mounted after the /reports/* POST routes so they keep their meanings;
        # mounted before the UI catch-all at "/" for the same reason.
        app.mount("/reports", StaticFiles(directory=str(reports_dir)), name="reports")

    # --- dashboard ---------------------------------------------------------
    if ui_dir and ui_dir.is_dir():
        # Mounted last so /health, /events and /sessions keep their meanings.
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    return app
