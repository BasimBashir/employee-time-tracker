"""Defensive clip helpers. Clips may legitimately be mid-write, unreadable,
or missing — the player degrades gracefully instead of crashing."""
import json
from pathlib import Path
from typing import Optional


def read_timespan_json(path: Path) -> Optional[dict]:
    """The clip's sidecar metadata; None when missing or corrupt."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def probe_clip(path: Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False, "frames": 0, "opened": False, "ready": False}
    try:
        import cv2
        cap = cv2.VideoCapture(str(p))
        opened = cap.isOpened()
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if opened else 0
        cap.release()
    except Exception:
        return {"exists": True, "frames": 0, "opened": False, "ready": False}
    return {"exists": True, "frames": frames, "opened": opened, "ready": frames > 0}
