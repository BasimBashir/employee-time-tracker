import shutil
import sqlite3
import time
from pathlib import Path

from .db import audit


def purge_clips_older_than(clips_dir: Path, days: int, dry_run: bool = False) -> list[Path]:
    cutoff = time.time() - days * 86400
    hits = []
    root = Path(clips_dir)
    if not root.is_dir():
        return hits
    for p in root.rglob("*.mp4"):
        try:
            if p.stat().st_mtime < cutoff:
                hits.append(p)
                if not dry_run:
                    p.unlink()
        except (FileNotFoundError, PermissionError):
            continue        # already gone, or locked by a player — catch it tomorrow
    return hits


def clear_clip_paths(conn: sqlite3.Connection, purged) -> int:
    n = 0
    for p in purged:
        cur = conn.execute("UPDATE events SET clip_path=NULL WHERE clip_path=?", (str(p),))
        n += cur.rowcount or 0
    conn.commit()
    return n


def disk_free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(str(path)).free / 1024 ** 3
    except Exception:
        return -1.0


def nightly_retention(cfg, conn) -> dict:
    purged = purge_clips_older_than(cfg.clips_dir, cfg.clip_retention_days)
    cleared = clear_clip_paths(conn, purged)
    free = disk_free_gb(cfg.clips_dir)
    extra = []
    if 0 <= free < cfg.disk_floor_gb:
        extra = purge_clips_older_than(cfg.clips_dir, 14)
        cleared += clear_clip_paths(conn, extra)
    audit(conn, "system", "nightly_retention", str(cfg.clip_retention_days),
          {"purged": len(purged), "cleared_paths": cleared, "free_gb": round(free, 2),
           "early_purge": len(extra)})
    return {"purged": len(purged), "cleared": cleared, "free_gb": round(free, 2)}
