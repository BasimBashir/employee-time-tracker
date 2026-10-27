"""SQLite access. This module owns the connection and the schema; nothing else
writes DDL or opens the database directly."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def connect(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- roster ----
def add_employee(conn, name: str, employee_code=None, department=None) -> int:
    cur = conn.execute(
        "INSERT INTO employees (name,employee_code,department,active,created_at)"
        " VALUES (?,?,?,1,?)", (name, employee_code, department, _now()))
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
    cur = conn.execute(
        "INSERT INTO sessions (employee_id,out_event_id,out_at,date) VALUES (?,?,?,?)",
        (employee_id, out_event_id, out_at.isoformat(), date))
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


# ----------------------------------------------------------------- audit ----
def audit(conn, actor, action, target, detail) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor,action,target,detail_json,at) VALUES (?,?,?,?,?)",
        (actor, action, target, json.dumps(detail), _now()))
    conn.commit()
