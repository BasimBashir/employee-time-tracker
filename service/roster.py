"""Enrollment records. Never computes embeddings, never decides identity.

Employee CRUD lives in service/db.py (it is profile data, shared with the
dashboard); this module owns only the enrollments table and the exact view
the resolver compares against.
"""
import sqlite3
from datetime import datetime
from typing import Optional


def _now() -> str:
    return datetime.now().isoformat()


def save_enrollment(conn, employee_id: int, face_embedding: Optional[bytes],
                    body_embedding: Optional[bytes], quality_score: float,
                    is_primary: int = 0) -> int:
    if is_primary:
        conn.execute("UPDATE enrollments SET is_primary=0 WHERE employee_id=?",
                     (employee_id,))
    cur = conn.execute(
        "INSERT INTO enrollments (employee_id,face_embedding,body_embedding,"
        "quality_score,captured_at,is_primary) VALUES (?,?,?,?,?,?)",
        (employee_id, face_embedding, body_embedding, quality_score, _now(), is_primary))
    conn.commit()
    return cur.lastrowid


def primary_face(conn, employee_id: int) -> Optional[bytes]:
    row = conn.execute(
        "SELECT face_embedding FROM enrollments WHERE employee_id=? AND is_primary=1"
        " ORDER BY id DESC LIMIT 1", (employee_id,)).fetchone()
    return row[0] if row and row[0] is not None else None


def roster_embeddings(conn) -> list[tuple[int, bytes, int]]:
    """(employee_id, face_embedding, is_primary) for every ACTIVE enrolled employee."""
    rows = conn.execute(
        "SELECT en.employee_id, en.face_embedding, en.is_primary FROM enrollments en"
        " JOIN employees e ON e.id = en.employee_id"
        " WHERE e.active=1 AND e.deleted_at IS NULL AND en.face_embedding IS NOT NULL"
    ).fetchall()
    return [(r["employee_id"], r["face_embedding"], r["is_primary"]) for r in rows]
