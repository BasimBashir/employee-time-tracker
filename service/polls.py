"""WhatsApp prayer polls and the Monday-15:00 weekly summary.

Sent through the bridge only while notifications are enabled; every send is
best-effort (notifier swallows failures). Each prayer poll fires once per
employee per day — the poll_replies UNIQUE row is the guard, so a restart
can ask late but never twice. Replies arrive via POST /notify/inbound.
"""
import logging
from datetime import datetime

from . import db as dbmod
from . import notifier

log = logging.getLogger(__name__)

_PRAYER_LABEL = {"fajr": "Fajr", "zuhr": "Zuhr", "asr": "Asr",
                 "maghrib": "Maghrib", "isha": "Isha"}


def _reason_tail(text: str, skip: int) -> str | None:
    rest = text.strip()[skip:].strip()
    for sep in (":", "-", "–", ",", "."):
        if rest.startswith(sep):
            rest = rest[1:].strip()
    if rest.lower().startswith("reason"):
        rest = rest[6:].lstrip(": ").strip()
    return rest or None


def parse_reply(text: str) -> tuple[str, str | None] | None:
    """"yes" / "no <reason>" (case-insensitive, optional "reason:"/"-" before
    the why) -> (reply, reason). Anything else is not a poll answer."""
    t = (text or "").strip().lower()
    if t.startswith("yes") and (len(t) == 3 or not t[3].isalpha()):
        return "yes", _reason_tail(text, 3)
    if t.startswith("no") and (len(t) == 2 or not t[2].isalpha()):
        return "no", _reason_tail(text, 2)
    return None


def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def find_employee_by_phone(conn, sender: str):
    """Match the bridge's JID ('92300...@s.whatsapp.net') to a stored number
    by their last 9 digits — tolerant of +, spaces and leading-zero styles."""
    tail = _digits(sender)[-9:]
    if not tail:
        return None
    for emp in dbmod.list_employees(conn):
        if emp["phone"] and _digits(emp["phone"])[-9:] == tail:
            return emp
    return None


def send_prayer_polls(conn, cfg, now: datetime | None = None) -> int:
    """Ask every employee whose namaz slot has started and who has a number.
    Late service starts catch up on the next tick, still asked exactly once."""
    now = now or datetime.now()
    today = now.date().isoformat()
    settings = dbmod.get_namaz_settings(conn)
    asked = 0
    for emp in dbmod.list_employees(conn):
        if not emp["phone"] or not emp["namaz_prayers"]:
            continue
        for prayer in str(emp["namaz_prayers"]).split(","):
            prayer = prayer.strip()
            start = (dbmod.namaz_start(conn, today, prayer, settings)
                     + (emp["namaz_offset_min"] or 0))
            if now.hour * 60 + now.minute < start:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO poll_replies"
                " (employee_id,date,prayer,reply,updated_at) VALUES (?,?,?,'asked',?)",
                (emp["employee_id"], today, prayer, now.isoformat()))
            if cur.rowcount == 0:
                continue
            conn.commit()
            hhmm = f"{start // 60:02d}:{start % 60:02d}"
            notifier.send(
                cfg, emp["phone"],
                f"🕌 {emp['name']}, {_PRAYER_LABEL.get(prayer, prayer)} jamaat at "
                f"{hhmm}. Are you going? Reply YES or NO — if NO, please add why "
                f"(e.g. \"no - doctor\").")
            asked += 1
    return asked


def handle_inbound(conn, cfg, sender: str, text: str) -> dict:
    """A WhatsApp reply arrived (bridge → POST /notify/inbound). A 'no' gets a
    one-line ack so the employee knows it was recorded."""
    parsed = parse_reply(text)
    emp = find_employee_by_phone(conn, sender)
    if emp is None or parsed is None:
        return {"matched": emp is not None, "accepted": False}
    reply, reason = parsed
    dbmod.record_poll_reply(conn, emp["employee_id"], reply, reason)
    if cfg is not None and reply == "no":
        notifier.send(cfg, emp["phone"], f"Noted, {emp['name']} — thanks.")
    return {"matched": True, "accepted": True, "reply": reply, "reason": reason}


def weekly_summary_text(name: str, break_min: float, delivery_min: float,
                        allowed: int, extra: float) -> str:
    msg = (f"📊 {name}, last week: {break_min:.0f}m out (break) against "
           f"{allowed}m/day allowed")
    if delivery_min:
        msg += f", {delivery_min:.0f}m delivery (work time)"
    msg += f" — {extra:.0f}m over budget." if extra > 0 else " — within budget."
    return msg


def maybe_weekly_report(conn, cfg, fired_on: str | None,
                        now: datetime | None = None) -> str | None:
    """Monday 15:00 (first loop tick after it counts): one summary per employee
    with a number. Friday is the day off, so Monday reports the full Sat–Thu
    week. The settings marker survives restarts; `fired_on` covers re-entry."""
    now = now or datetime.now()
    today = now.date().isoformat()
    if now.weekday() != 0 or now.hour < 15 or fired_on == today:
        return fired_on
    marker = conn.execute(
        "SELECT value FROM settings WHERE key='weekly_report_sent'").fetchone()
    if marker is not None and marker["value"] == today:
        return today
    conn.execute(
        "INSERT INTO settings (key,value) VALUES ('weekly_report_sent',?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (today,))
    conn.commit()
    weekly = dbmod.weekly_report(conn, today, 7)
    for row in weekly:
        phone_row = conn.execute("SELECT phone FROM employees WHERE id=?",
                                 (row["employee_id"],)).fetchone()
        phone = phone_row["phone"] if phone_row else None
        if not phone:
            continue
        break_min = sum(d["break_minutes"] for d in row["series"])
        delivery_min = sum(d["delivery_minutes"] for d in row["series"])
        extra = max(0.0, break_min - row["allowed_minutes"] * len(row["series"]))
        notifier.send(cfg, phone, weekly_summary_text(
            row["name"], break_min, delivery_min, row["allowed_minutes"], extra))
    return today
