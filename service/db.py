"""SQLite access. This module owns the connection and the schema; nothing else
writes DDL or opens the database directly."""
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import namaz as namazmod

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
        "phone": "TEXT",
        "avatar_path": "TEXT",
        "out_windows": "TEXT",
        "namaz_prayers": "TEXT",
        "namaz_offset_min": "INTEGER",
        "namaz_duration_min": "INTEGER",
    })
    _add_columns(conn, "sessions", {"purpose": "TEXT NOT NULL DEFAULT 'break'",
                                    "out_reason": "TEXT"})
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


# ------------------------------------------------------- allowed windows ----
# A profile may carry allowed out-time windows ("12:30-14:00, 16:00-16:30"):
# trips starting outside every window are flagged and alerted on. Stored as a
# canonical JSON list of [start, end] "HH:MM" pairs; empty/NULL = unrestricted.
_WINDOW_RE = re.compile(r"^(\d{1,2}):(\d{1,2})\s*-\s*(\d{1,2}):(\d{1,2})$")


def parse_out_windows(text) -> str | None:
    """Validate '12:30-14:00, 16:00-16:30' into canonical JSON (None = unset).
    Raises ValueError on anything malformed."""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    spans = []
    for part in text.split(","):
        m = _WINDOW_RE.match(part.strip().replace("–", "-"))
        if not m:
            raise ValueError(f"not a time window: {part.strip()!r} (use HH:MM-HH:MM)")
        h1, m1, h2, m2 = (int(g) for g in m.groups())
        if h1 > 23 or m1 > 59 or h2 > 23 or m2 > 59:
            raise ValueError(f"not a clock time: {part.strip()!r}")
        if h2 * 60 + m2 <= h1 * 60 + m1:
            raise ValueError(f"window must end after it starts: {part.strip()!r}")
        spans.append((h1 * 60 + m1, h2 * 60 + m2))
    spans.sort()
    return json.dumps([[f"{s // 60:02d}:{s % 60:02d}", f"{e // 60:02d}:{e % 60:02d}"]
                       for s, e in spans])


def windows_text(js) -> str | None:
    """Stored JSON back to the friendly '12:30-14:00, 16:00-16:30' form."""
    if not js:
        return None
    return ", ".join(f"{s}-{e}" for s, e in json.loads(js))


def windows_spans(js) -> list[tuple[int, int]] | None:
    """Stored JSON as minute-of-day spans, or None when unrestricted."""
    if not js:
        return None
    return [(int(s[:2]) * 60 + int(s[3:]), int(e[:2]) * 60 + int(e[3:]))
            for s, e in json.loads(js)]


def violates(spans, out_at: datetime) -> bool:
    """True when windows exist and out_at starts outside every one of them."""
    if not spans:
        return False
    minutes = out_at.hour * 60 + out_at.minute
    return all(not (s <= minutes < e) for s, e in spans)


def outside_window(conn, employee_id, out_at: datetime) -> bool:
    row = conn.execute(
        """SELECT out_windows, namaz_prayers, namaz_offset_min, namaz_duration_min
             FROM employees WHERE id=?""", (employee_id,)).fetchone()
    if row is None:
        return False
    spans = effective_spans(conn, row["out_windows"], out_at.date().isoformat(),
                            row["namaz_prayers"], row["namaz_offset_min"],
                            row["namaz_duration_min"])
    return violates(spans, out_at)


# ----------------------------------------------------------- namaz windows ----
# Employees go out for namaz several times a day, so their allowed out-time
# comes from a per-employee prayer plan on top of any static windows. Zuhr in
# Faisalabad is a fixed 13:30 jamaat year-round (editable); Fajr, Asr, Maghrib
# and Isha follow the sun via service/namaz.py with editable jamaat offsets,
# and any date can be pinned with an override. Employees pick which prayers
# they go out for and get a stagger offset so everyone doesn't leave at once.
_NAMAZ_DEFAULT_JUMMAH_MIN = 13 * 60 + 30


def parse_namaz_prayers(value) -> str | None:
    """Which prayers this employee goes out for: 'zuhr, asr' (or a list) into
    canonical comma form; None/empty = no namaz windows at all."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [p.strip().lower() for p in value.split(",")]
    else:
        parts = [str(p).strip().lower() for p in value]
    parts = [p for p in parts if p]
    if not parts:
        return None
    unknown = [p for p in parts if p not in namazmod.PRAYERS]
    if unknown:
        raise ValueError(f"unknown prayer(s): {', '.join(unknown)}")
    return ",".join(p for p in namazmod.PRAYERS if p in set(parts))


def parse_minute_field(value, lo: int, hi: int, what: str) -> int | None:
    """An int in [lo, hi], or None (= clear / inherit). Raises ValueError."""
    if value is None or value == "":
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be minutes between {lo} and {hi}")
    if not lo <= v <= hi:
        raise ValueError(f"{what} must be between {lo} and {hi}")
    return v


def get_namaz_settings(conn) -> dict:
    """The global timetable settings (settings-table keys namaz_*): per-prayer
    jamaat offsets, the fixed Zuhr clock time, the Friday jummah time and the
    default slot duration."""
    vals = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")
            if r["key"].startswith("namaz_")}

    def setting(key: str, default):
        raw = vals.get(key)
        if raw is None:
            return default
        if raw == "":
            return None  # explicitly cleared → follow the computed sun time
        return int(raw)

    return {
        "offsets": {p: setting(f"namaz_offset_{p}", 0) or 0 for p in namazmod.PRAYERS},
        "zuhr_fixed_min": setting("namaz_zuhr_fixed_min", 13 * 60 + 30),
        "jummah_min": setting("namaz_jummah_min", _NAMAZ_DEFAULT_JUMMAH_MIN),
        "duration_min": setting("namaz_duration_min", 20),
    }


def set_namaz_settings(conn, offsets=None, zuhr_fixed_min=UNSET, jummah_min=UNSET,
                       duration_min=UNSET) -> None:
    """Edit the timetable settings. offsets maps prayer → jamaat offset minutes
    (0-180; None clears back to 0). zuhr_fixed_min / jummah_min are absolute
    minutes-of-day or None (= follow the computed sun time). Audited."""
    detail = {}

    def put(key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key)"
            " DO UPDATE SET value=excluded.value", (key, value))

    if offsets:
        for prayer, minutes in offsets.items():
            if prayer not in namazmod.PRAYERS:
                raise ValueError(f"unknown prayer {prayer!r}")
            minutes = parse_minute_field(minutes, 0, 180, "jamaat offset")
            put(f"namaz_offset_{prayer}", "" if minutes is None else str(minutes))
            detail[f"offset_{prayer}"] = minutes
    if zuhr_fixed_min is not UNSET:
        minutes = parse_minute_field(zuhr_fixed_min, 0, 1439, "zuhr time")
        put("namaz_zuhr_fixed_min", "" if minutes is None else str(minutes))
        detail["zuhr_fixed_min"] = minutes
    if jummah_min is not UNSET:
        minutes = parse_minute_field(jummah_min, 0, 1439, "jummah time")
        put("namaz_jummah_min", "" if minutes is None else str(minutes))
        detail["jummah_min"] = minutes
    if duration_min is not UNSET:
        minutes = parse_minute_field(duration_min, 5, 120, "slot duration")
        put("namaz_duration_min", "" if minutes is None else str(minutes))
        detail["duration_min"] = minutes
    if not detail:
        return
    audit(conn, "dashboard", "set_namaz_settings", "namaz", detail)


def set_namaz_override(conn, date_str: str, prayer: str, minutes, actor="dashboard") -> None:
    """Pin one prayer on one date to an absolute time (minutes-of-day), or
    clear the pin by passing None."""
    if prayer not in namazmod.PRAYERS:
        raise ValueError(f"unknown prayer {prayer!r}")
    datetime.fromisoformat(date_str)  # ValueError on a junk date
    minutes = parse_minute_field(minutes, 0, 1439, "prayer time")
    if minutes is None:
        conn.execute("DELETE FROM namaz_overrides WHERE date=? AND prayer=?",
                     (date_str, prayer))
    else:
        conn.execute(
            "INSERT INTO namaz_overrides (date,prayer,minutes,updated_at)"
            " VALUES (?,?,?,?) ON CONFLICT(date,prayer) DO UPDATE SET"
            " minutes=excluded.minutes, updated_at=excluded.updated_at",
            (date_str, prayer, minutes, _now()))
    conn.commit()
    audit(conn, actor, "set_namaz_override", f"{date_str}/{prayer}", {"minutes": minutes})


def namaz_start(conn, date_str: str, prayer: str, settings: dict | None = None) -> int:
    """Effective start (minutes-of-day) of one prayer on one date: a per-date
    override first, then the Friday jummah time (Zuhr only), then the fixed
    Zuhr clock time, then the computed sun time plus the jamaat offset."""
    row = conn.execute("SELECT minutes FROM namaz_overrides WHERE date=? AND prayer=?",
                       (date_str, prayer)).fetchone()
    if row is not None:
        return row["minutes"]
    st = settings if settings is not None else get_namaz_settings(conn)
    if prayer == "zuhr":
        if date.fromisoformat(date_str).weekday() == 4 and st["jummah_min"] is not None:
            return st["jummah_min"]
        if st["zuhr_fixed_min"] is not None:
            return st["zuhr_fixed_min"]
    return namazmod.day_times(date_str)[prayer] + st["offsets"][prayer]


def namaz_spans(conn, date_str: str, prayers, offset_min=None, duration_min=None,
                settings: dict | None = None) -> list[tuple[int, int]]:
    """One employee's namaz out-slots for a date: each selected prayer opens a
    window at its start plus their stagger, running for the slot duration."""
    if not prayers:
        return []
    st = settings if settings is not None else get_namaz_settings(conn)
    shift = offset_min or 0
    dur = st["duration_min"] if duration_min is None else duration_min
    spans = []
    for p in str(prayers).split(","):
        start = namaz_start(conn, date_str, p.strip(), st) + shift
        spans.append((start, min(start + dur, 1439)))
    return sorted(spans)


def effective_spans(conn, out_windows_js, date_str, prayers=None, offset_min=None,
                    duration_min=None, settings: dict | None = None
                    ) -> list[tuple[int, int]]:
    """Static profile windows plus the day's namaz slots, in time order."""
    spans = list(windows_spans(out_windows_js) or [])
    spans += namaz_spans(conn, date_str, prayers, offset_min, duration_min, settings)
    return sorted(spans)


def spans_text(spans) -> str | None:
    """Minute spans in the friendly '13:35-13:55, 16:20-16:40' form."""
    if not spans:
        return None
    return ", ".join(f"{s // 60:02d}:{s % 60:02d}-{e // 60:02d}:{e % 60:02d}"
                     for s, e in spans)


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


def record_poll_reply(conn, employee_id: int, reply: str, reason: str | None) -> None:
    """Store an inbound WhatsApp answer on today's poll row (or a standalone
    row when no poll was asked); a reason also lands on the open session so
    the trip's record carries why they were out. Audited."""
    today = datetime.now().date().isoformat()
    row = conn.execute(
        "SELECT id FROM poll_replies WHERE employee_id=? AND date=?"
        " ORDER BY id DESC LIMIT 1", (employee_id, today)).fetchone()
    if row is not None:
        conn.execute("UPDATE poll_replies SET reply=?, reason=?, updated_at=? WHERE id=?",
                     (reply, reason, _now(), row["id"]))
    else:
        conn.execute(
            "INSERT INTO poll_replies (employee_id,date,prayer,reply,reason,updated_at)"
            " VALUES (?,?,?,?,?,?)", (employee_id, today, "", reply, reason, _now()))
    if reason:
        conn.execute(
            "UPDATE sessions SET out_reason=? WHERE id=(SELECT id FROM sessions"
            " WHERE employee_id=? AND date=? AND in_at IS NULL AND deleted_at IS NULL"
            " ORDER BY id DESC LIMIT 1)", (reason, employee_id, today))
    conn.commit()
    audit(conn, "whatsapp", "poll_reply", str(employee_id),
          {"reply": reply, "reason": reason})


def update_employee(conn, employee_id, role=None, allowed_out_minutes=UNSET,
                    phone=UNSET, name=UNSET, employee_code=UNSET,
                    department=UNSET, out_windows=UNSET, avatar_path=UNSET,
                    namaz_prayers=UNSET, namaz_offset_min=UNSET,
                    namaz_duration_min=UNSET) -> None:
    """Update any profile field. None means "clear" for the optional ones
    (phone, code, department, out_windows, avatar, namaz plan); blank names
    are refused; out_windows and the namaz fields are validated (see
    parse_out_windows / parse_namaz_prayers). Changes are audited."""
    sets, args, detail = [], [], {}
    if role is not None:
        if role not in ("staff", "delivery"):
            raise ValueError(f"unknown role {role!r}")
        sets.append("role=?")
        args.append(role)
        detail["role"] = role
    if phone is not UNSET:
        if phone is not None:
            phone = str(phone).strip()
            if not re.fullmatch(r"\+?\d[\d\s-]{5,18}", phone):
                raise ValueError(f"not a phone number: {phone!r}")
        sets.append("phone=?")
        args.append(phone)
        detail["phone"] = phone
    if allowed_out_minutes is not UNSET:
        sets.append("allowed_out_minutes=?")
        args.append(allowed_out_minutes)
        detail["allowed_out_minutes"] = allowed_out_minutes
    if name is not UNSET:
        name = str(name or "").strip()
        if not name:
            raise ValueError("name cannot be empty")
        sets.append("name=?")
        args.append(name)
        detail["name"] = name
    for column, value in (("employee_code", employee_code), ("department", department)):
        if value is not UNSET:
            value = str(value).strip() if value is not None else None
            if value == "":
                value = None
            sets.append(f"{column}=?")
            args.append(value)
            detail[column] = value
    if out_windows is not UNSET:
        out_windows = parse_out_windows(out_windows)
        sets.append("out_windows=?")
        args.append(out_windows)
        detail["out_windows"] = out_windows
    if avatar_path is not UNSET:
        sets.append("avatar_path=?")
        args.append(avatar_path)
    if namaz_prayers is not UNSET:
        namaz_prayers = parse_namaz_prayers(namaz_prayers)
        sets.append("namaz_prayers=?")
        args.append(namaz_prayers)
        detail["namaz_prayers"] = namaz_prayers
    if namaz_offset_min is not UNSET:
        minutes = parse_minute_field(namaz_offset_min, 0, 180, "stagger offset")
        sets.append("namaz_offset_min=?")
        args.append(minutes)
        detail["namaz_offset_min"] = minutes
    if namaz_duration_min is not UNSET:
        minutes = parse_minute_field(namaz_duration_min, 5, 120, "slot duration")
        sets.append("namaz_duration_min=?")
        args.append(minutes)
        detail["namaz_duration_min"] = minutes
    if not sets:
        return
    args.append(employee_id)
    conn.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()
    audit(conn, "dashboard", "update_employee", str(employee_id), detail)


def list_employees(conn, windows_date: str | None = None) -> list[dict]:
    """Every active employee with their full profile, for the Employees page.
    With windows_date, each profile also carries effective_windows — the
    static windows plus that day's namaz slots — as friendly text."""
    rows = conn.execute(
        """SELECT id, name, employee_code, department, role, allowed_out_minutes,
                  phone, out_windows, avatar_path, namaz_prayers,
                  namaz_offset_min, namaz_duration_min
             FROM employees WHERE deleted_at IS NULL ORDER BY id""").fetchall()
    settings = get_namaz_settings(conn) if windows_date else None
    out = []
    for r in rows:
        d = {
            "employee_id": r["id"], "name": r["name"], "employee_code": r["employee_code"],
            "department": r["department"], "role": r["role"],
            "allowed_out_minutes": r["allowed_out_minutes"], "phone": r["phone"],
            "out_windows": windows_text(r["out_windows"]),
            "has_avatar": bool(r["avatar_path"]),
            "namaz_prayers": r["namaz_prayers"],
            "namaz_offset_min": r["namaz_offset_min"],
            "namaz_duration_min": r["namaz_duration_min"],
        }
        if windows_date:
            d["effective_windows"] = spans_text(effective_spans(
                conn, r["out_windows"], windows_date, r["namaz_prayers"],
                r["namaz_offset_min"], r["namaz_duration_min"], settings))
        out.append(d)
    return out


def deactivate_employee(conn, employee_id, actor, reason) -> None:
    """Soft delete, like sessions: reports and rosters stop listing them while
    every event and session that references the row keeps resolving."""
    conn.execute("UPDATE employees SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                 (_now(), employee_id))
    conn.commit()
    audit(conn, actor, "deactivate_employee", str(employee_id), {"reason": reason})


# --------------------------------------------------------------- reports ----
def daily_report(conn, on_date: str, default_allowed_minutes: int = 60) -> list[dict]:
    """Per-employee out-time for one day. Delivery trips are work and exempt:
    only break minutes count against the allowance; extra_minutes is the
    overshoot. Zero-filled for employees with no trips that day."""
    rows = conn.execute(
        """SELECT e.id AS employee_id, e.name, e.employee_code, e.department,
                  e.role, e.allowed_out_minutes, e.phone,
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
            "role": r["role"], "phone": r["phone"],
            "trips": r["trips"], "open_trips": r["open_trips"],
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
