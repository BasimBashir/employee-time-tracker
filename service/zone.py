import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class Zone:
    id: Optional[int]
    camera_id: int
    line_points: list[tuple[float, float]]
    direction_out: str                       # "POS_TO_NEG" | "NEG_TO_POS"
    enabled: bool = True


def save_zone(conn: sqlite3.Connection, zone: Zone) -> int:
    if zone.enabled:
        # exactly one authoritative line per camera: disable any other enabled zone
        conn.execute("UPDATE zones SET enabled=0 WHERE camera_id=? AND enabled=1",
                     (zone.camera_id,))
    cur = conn.execute(
        "INSERT INTO zones (camera_id,line_points,direction_out,enabled) VALUES (?,?,?,?)",
        (zone.camera_id, json.dumps(zone.line_points), zone.direction_out, int(zone.enabled)))
    conn.commit()
    return cur.lastrowid


def _row_to_zone(r) -> Zone:
    return Zone(id=r["id"], camera_id=r["camera_id"],
                line_points=[tuple(p) for p in json.loads(r["line_points"])],
                direction_out=r["direction_out"], enabled=bool(r["enabled"]))


def load_zones(conn: sqlite3.Connection, camera_id: int) -> list[Zone]:
    rows = conn.execute("SELECT * FROM zones WHERE camera_id=? ORDER BY id", (camera_id,)).fetchall()
    return [_row_to_zone(r) for r in rows]


def get_active_zone(conn: sqlite3.Connection, camera_id: int) -> Optional[Zone]:
    rows = conn.execute(
        "SELECT * FROM zones WHERE camera_id=? AND enabled=1 ORDER BY id DESC LIMIT 1",
        (camera_id,)).fetchall()
    return _row_to_zone(rows[0]) if rows else None
