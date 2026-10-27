"""SQLite access. This module owns the connection and the schema; nothing else
writes DDL or opens the database directly."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

# Sentinel for "argument not provided" — distinct from None, which means
# "clear the override" (inherit the config default).
UNSET = object()


def connect(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Columns added after the phase-1 schema; ALTER only what an older DB lacks
    # so the live database migrates in place on first start.
    _add_columns(conn, "employees", {
        "role": "TEXT NOT NULL DEFAULT 'staff'",
        "allowed_out_minutes": "INTEGER",
    })
    _add_columns(conn, "sessions", {"purpose": "TEXT NOT NULL DEFAULT 'break'"})
    conn.commit()


def _add_columns(conn, table, columns) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- roster ----
def add_employee(conn, name: str, employee_code=None, department=None,
                 role="staff", allowed_out_minutes=None) -> int:
    cur = conn.execute(
        "INSERT INTO employees (name,employee_code,department,active,role,"
        "allowed_out_minutes,created_at) VALUES (?,?,?,1,?,?,?)",
        (name, employee_code, department, role, allowed_out_minutes, _now()))
    conn.commit()
    return cur.lastrowid


def add_camera(conn, name: str, rtsp_url=None, decode_element=None, provider=None) -> int:
    cur = conn.execute(
        "INSERT INTO cameras (name,rtsp_url,decode_element,provider,active)"
        " VALUES (?,?,?,?,1)", (name, rtsp_url, decode_element, provider))
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------- events ----
def insert_event(conn, employee_id, camera_id, event_type, occurred_at,
                 clip_path=None, confidence=0.0, uncertain=0, track_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO events (employee_id,camera_id,event_type,occurred_at,clip_path,"
        "confidence,uncertain,track_id) VALUES (?,?,?,?,?,?,?,?)",
        (employee_id, camera_id, event_type, occurred_at.isoformat(), clip_path,
         confidence, uncertain, track_id))
    conn.commit()
    return cur.lastrowid


# -------------------------------------------------------------- sessions ----
def open_session(conn, employee_id, out_event_id, out_at, date) -> int:
    # Purpose defaults from the employee's role: a delivery rider's trip is
    # work time ('delivery'), everyone else's is 'break'. Re-taggable later.
    cur = conn.execute(
        "INSERT INTO sessions (employee_id,out_event_id,out_at,date,purpose)"
        " VALUES (?,?,?,?,CASE WHEN"
        " (SELECT role FROM employees WHERE id=?)='delivery' THEN 'delivery'"
        " ELSE 'break' END)",
        (employee_id, out_event_id, out_at.isoformat(), date, employee_id))
    conn.commit()
    return cur.lastrowid


def close_session(conn, session_id, in_event_id, in_at) -> float:
    row = conn.execute("SELECT out_at FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        raise ValueError(f"no session {session_id}")
    minutes = (in_at - datetime.fromisoformat(row["out_at"])).total_seconds() / 60.0
    conn.execute(
        "UPDATE sessions SET in_event_id=?, in_at=?, duration_minutes=? WHERE id=?",
        (in_event_id, in_at.isoformat(), minutes, session_id))
    conn.commit()
    return minutes


def force_close_open_sessions(conn, on_date) -> int:
    """Midnight rollover: close anything still open at 23:59:59 and flag it so a
    missing return never becomes an infinite timer."""
    rows = conn.execute(
        "SELECT id FROM sessions WHERE date=? AND in_at IS NULL", (on_date,)).fetchall()
    day_start = datetime.fromisoformat(on_date + "T00:00:00")
    close_at = datetime.fromisoformat(on_date + "T23:59:59")
    minutes = (close_at - day_start).total_seconds() / 60.0
    for r in rows:
        conn.execute(
            "UPDATE sessions SET in_at=?, duration_minutes=?, inferred_return=1 WHERE id=?",
            (close_at.isoformat(), minutes, r["id"]))
    conn.commit()
    return len(rows)


def soft_delete_session(conn, session_id, actor, reason) -> None:
    """Attendance records get disputed. Never drop one."""
    conn.execute(
        "UPDATE sessions SET deleted_at=?, deleted_reason=? WHERE id=?",
        (_now(), reason, session_id))
    conn.commit()
    audit(conn, actor, "delete_session", str(session_id), {"reason": reason})


def set_session_purpose(conn, session_id, purpose: str, actor: str) -> None:
    """Re-tag a trip as 'break' or 'delivery' (delivery is exempt from the
    out-time budget). Audited, like every correction to attendance."""
    if purpose not in ("break", "delivery"):
        raise ValueError(f"unknown purpose {purpose!r}")
    conn.execute("UPDATE sessions SET purpose=? WHERE id=?", (purpose, session_id))
    conn.commit()
    audit(conn, actor, "set_session_purpose", str(session_id), {"purpose": purpose})


def update_employee(conn, employee_id, role=None, allowed_out_minutes=UNSET) -> None:
    """Set role ('staff' | 'delivery') and/or the daily allowance in minutes.
    allowed_out_minutes=None clears the override (inherits the config default).
    Both changes are audited."""
    sets, args, detail = [], [], {}
    if role is not None:
        if role not in ("staff", "delivery"):
            raise ValueError(f"unknown role {role!r}")
        sets.append("role=?")
        args.append(role)
        detail["role"] = role
    if allowed_out_minutes is not UNSET:
        sets.append("allowed_out_minutes=?")
        args.append(allowed_out_minutes)
        detail["allowed_out_minutes"] = allowed_out_minutes
    if not sets:
        return
    args.append(employee_id)
    conn.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()
    audit(conn, "dashboard", "update_employee", str(employee_id), detail)


# --------------------------------------------------------------- reports ----
def daily_report(conn, on_date: str, default_allowed_minutes: int = 60) -> list[dict]:
    """Per-employee out-time for one day. Delivery trips are work and exempt:
    only break minutes count against the allowance; extra_minutes is the
    overshoot. Zero-filled for employees with no trips that day."""
    rows = conn.execute(
        """SELECT e.id AS employee_id, e.name, e.employee_code, e.department,
                  e.role, e.allowed_out_minutes,
                  COALESCE(SUM(CASE WHEN s.purpose='break'
                                    THEN s.duration_minutes END), 0) AS break_minutes,
                  COALESCE(SUM(CASE WHEN s.purpose='delivery'
                                    THEN s.duration_minutes END), 0) AS delivery_minutes,
                  COUNT(s.id) AS trips,
                  COALESCE(SUM(CASE WHEN s.in_at IS NULL THEN 1 ELSE 0 END), 0) AS open_trips
             FROM employees e
             LEFT JOIN sessions s
               ON s.employee_id = e.id AND s.date = ? AND s.deleted_at IS NULL
            WHERE e.deleted_at IS NULL
            GROUP BY e.id ORDER BY e.id""", (on_date,)).fetchall()
    report = []
    for r in rows:
        allowed = r["allowed_out_minutes"]
        if allowed is None:
            allowed = default_allowed_minutes
        break_minutes = round(r["break_minutes"] or 0, 1)
        delivery_minutes = round(r["delivery_minutes"] or 0, 1)
        report.append({
            "employee_id": r["employee_id"], "name": r["name"],
            "employee_code": r["employee_code"], "department": r["department"],
            "role": r["role"], "trips": r["trips"], "open_trips": r["open_trips"],
            "break_minutes": break_minutes, "delivery_minutes": delivery_minutes,
            "total_minutes": round(break_minutes + delivery_minutes, 1),
            "allowed_minutes": allowed,
            "extra_minutes": round(max(0.0, break_minutes - allowed), 1),
        })
    return report


def weekly_report(conn, end_date: str, days: int = 7,
                  default_allowed_minutes: int = 60) -> list[dict]:
    """Per-employee, per-day break/delivery minutes for the `days` ending at
    end_date (inclusive), zero-filled — the pattern view for the dashboard."""
    end = datetime.fromisoformat(end_date)
    start = (end - timedelta(days=days - 1)).date().isoformat()
    all_dates = [(end - timedelta(days=i)).date().isoformat()
                 for i in range(days - 1, -1, -1)]
    by_emp_date: dict[int, dict[str, dict]] = {}
    for r in conn.execute(
            """SELECT s.employee_id, s.date,
                      COALESCE(SUM(CASE WHEN s.purpose='break'
                                        THEN s.duration_minutes END), 0) AS break_minutes,
                      COALESCE(SUM(CASE WHEN s.purpose='delivery'
                                        THEN s.duration_minutes END), 0) AS delivery_minutes
                 FROM sessions s JOIN employees e ON e.id = s.employee_id
                WHERE s.date BETWEEN ? AND ? AND s.deleted_at IS NULL
                  AND e.deleted_at IS NULL
                GROUP BY s.employee_id, s.date""", (start, end_date)):
        by_emp_date.setdefault(r["employee_id"], {})[r["date"]] = {
            "break_minutes": round(r["break_minutes"], 1),
            "delivery_minutes": round(r["delivery_minutes"], 1)}
    report = []
    for e in conn.execute(
            "SELECT id, name, role, allowed_out_minutes FROM employees"
            " WHERE deleted_at IS NULL ORDER BY id"):
        allowed = (e["allowed_out_minutes"] if e["allowed_out_minutes"] is not None
                   else default_allowed_minutes)
        series = [by_emp_date.get(e["id"], {}).get(
                      d, {"break_minutes": 0.0, "delivery_minutes": 0.0})
                  for d in all_dates]
        report.append({"employee_id": e["id"], "name": e["name"], "role": e["role"],
                       "allowed_minutes": allowed, "dates": all_dates,
                       "series": series})
    return report


# ----------------------------------------------------------------- audit ----
def audit(conn, actor, action, target, detail) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor,action,target,detail_json,at) VALUES (?,?,?,?,?)",
        (actor, action, target, json.dumps(detail), _now()))
    conn.commit()
