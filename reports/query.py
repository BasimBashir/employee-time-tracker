"""The only module that reads sessions/events for reporting. Every query here
excludes soft-deleted rows — this is the single enforcement point of that rule."""
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionRow:
    id: int
    employee_id: int
    employee_name: str
    out_at: str
    in_at: Optional[str]
    duration_minutes: Optional[float]
    inferred_return: bool
    purpose: str
    date: str
    out_clip: Optional[str]
    in_clip: Optional[str]


_SELECT = """SELECT s.id, s.employee_id, e.name AS employee_name, s.out_at, s.in_at,
                    s.duration_minutes, s.inferred_return, s.purpose, s.date,
                    eo.clip_path AS out_clip, ei.clip_path AS in_clip
               FROM sessions s
               JOIN employees e ON e.id = s.employee_id
               LEFT JOIN events eo ON eo.id = s.out_event_id
               LEFT JOIN events ei ON ei.id = s.in_event_id
              WHERE s.deleted_at IS NULL"""


def sessions_for_date(conn: sqlite3.Connection, date: str) -> list[SessionRow]:
    rows = conn.execute(_SELECT + " AND s.date = ? ORDER BY s.out_at", (date,)).fetchall()
    return [_row(r) for r in rows]


def sessions_for_employee(conn: sqlite3.Connection, employee_id: int,
                          start: str, end: str) -> list[SessionRow]:
    rows = conn.execute(
        _SELECT + " AND s.employee_id = ? AND s.date BETWEEN ? AND ? ORDER BY s.out_at",
        (employee_id, start, end)).fetchall()
    return [_row(r) for r in rows]


def employee_daily_totals(conn: sqlite3.Connection, date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT employee_id, COUNT(*) AS trips,
                  COALESCE(SUM(duration_minutes), 0) AS minutes,
                  COALESCE(SUM(CASE WHEN purpose='break' THEN duration_minutes END), 0)
                      AS break_minutes,
                  COALESCE(SUM(CASE WHEN purpose='delivery' THEN duration_minutes END), 0)
                      AS delivery_minutes
             FROM sessions
            WHERE date = ? AND deleted_at IS NULL
            GROUP BY employee_id ORDER BY employee_id""", (date,)).fetchall()
    return [dict(r) for r in rows]


def employees(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, employee_code FROM employees WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()


def events_since(conn: sqlite3.Connection, iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT ev.*, e.name AS employee_name FROM events ev
             JOIN employees e ON e.id = ev.employee_id
            WHERE ev.occurred_at >= ? ORDER BY ev.id""", (iso,)).fetchall()


def _row(r: sqlite3.Row) -> SessionRow:
    return SessionRow(
        id=r["id"], employee_id=r["employee_id"], employee_name=r["employee_name"],
        out_at=r["out_at"], in_at=r["in_at"],
        duration_minutes=r["duration_minutes"],
        inferred_return=bool(r["inferred_return"]), purpose=r["purpose"], date=r["date"],
        out_clip=r["out_clip"], in_clip=r["in_clip"])
