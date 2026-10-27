"""Scripted crossings, so the whole product is demonstrable without a camera.

Writes real events and sessions at a paused cadence, so the dashboard's live
polling visibly appends them."""
import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path


def simulate(conn: sqlite3.Connection, camera_id: int, script,
             pause_seconds: float = 2.0, cfg=None) -> None:
    """script: iterable of (employee_id, out_at, in_at). in_at may be None.
    cfg, when given, also fires WhatsApp notifications for each trip."""
    from service.db import (insert_event, open_session, close_session,
                            outside_window, windows_text)
    from service import notifier
    for employee_id, out_at, in_at in script:
        out_ev = insert_event(conn, employee_id, camera_id, "OUT", out_at, None, 0.9, 0, 0)
        sid = open_session(conn, employee_id, out_ev, out_at, out_at.date().isoformat())
        emp = conn.execute("SELECT name, phone FROM employees WHERE id=?",
                           (employee_id,)).fetchone()
        sess = conn.execute("SELECT purpose FROM sessions WHERE id=?", (sid,)).fetchone()
        if cfg is not None and emp is not None and sess is not None:
            notifier.trip_opened(cfg, emp["name"], emp["phone"], out_at, sess["purpose"])
            if outside_window(conn, employee_id, out_at):
                stored = conn.execute("SELECT out_windows FROM employees WHERE id=?",
                                      (employee_id,)).fetchone()
                notifier.window_violation(cfg, emp["name"], out_at,
                                          windows_text(stored["out_windows"]))
        if in_at is not None:
            in_ev = insert_event(conn, employee_id, camera_id, "IN", in_at, None, 0.85, 0, 0)
            close_session(conn, sid, in_ev, in_at)
            sess = conn.execute(
                "SELECT purpose, duration_minutes, inferred_return FROM sessions WHERE id=?",
                (sid,)).fetchone()
            if cfg is not None and emp is not None and sess is not None:
                notifier.trip_closed(cfg, emp["name"], emp["phone"], in_at,
                                     sess["duration_minutes"], sess["purpose"],
                                     bool(sess["inferred_return"]))
        if pause_seconds:
            time.sleep(pause_seconds)


SCRIPT_HELP = ('JSON list of trips: [[employee_id, "2026-10-26T13:00:00", '
               '"2026-10-26T13:15:00"], ...]. Pass null for the return time to '
               'leave a trip open.')


def parse_script(raw: str) -> list[tuple[int, datetime, datetime | None]]:
    data = json.loads(raw)
    out = []
    for entry in data:
        emp, out_at, in_at = entry[0], entry[1], entry[2] if len(entry) > 2 else None
        out.append((int(emp), datetime.fromisoformat(out_at),
                    datetime.fromisoformat(in_at) if in_at else None))
    return out


def main(argv=None) -> None:
    from service.config import load_config
    from service.db import connect, migrate, add_employee, add_camera

    p = argparse.ArgumentParser(description="Simulate scripted crossings")
    p.add_argument("--script", required=True, help=SCRIPT_HELP)
    p.add_argument("--employees", action="store_true",
                   help="auto-create any employee the script references")
    p.add_argument("--camera", type=int, default=1, help="camera id to write events against")
    a = p.parse_args(argv)

    script = parse_script(a.script)
    cfg = load_config()
    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    migrate(conn)

    # Events carry a foreign key on camera_id, so the camera must exist first.
    cameras = {r["id"] for r in conn.execute("SELECT id FROM cameras")}
    if a.camera not in cameras:
        add_camera(conn, f"Camera {a.camera}", f"rtsp://camera-{a.camera}/stream")

    if a.employees:
        needed = {emp for emp, _, _ in script}
        existing = {r["id"] for r in conn.execute("SELECT id FROM employees")}
        for emp in sorted(needed - existing):
            add_employee(conn, f"Employee {emp}", f"E{emp}")

    simulate(conn, a.camera, script, cfg=cfg)
    conn.close()
    print(f"simulated {len(script)} trip(s) into {cfg.db_path}")


if __name__ == "__main__":
    main()
