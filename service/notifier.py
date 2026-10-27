"""WhatsApp notifications through the local Node bridge (whatsapp_bridge/).

Fire-and-forget by design: sends run on a daemon thread so the detection
path never waits on the network, and every failure is logged and swallowed —
a notification problem must never break attendance recording.

The bridge is unofficial WhatsApp automation; the operator pairs a
dedicated SIM once via QR (see whatsapp_bridge/README.md). Employees get
notifications when their record has a phone number and notify_enabled is
on in config.
"""
import json
import logging
import threading
import urllib.request

log = logging.getLogger(__name__)

_BRIDGE_TIMEOUT_S = 5


def _headers(cfg) -> dict:
    h = {"Content-Type": "application/json"}
    key = getattr(cfg, "notify_bridge_key", "")
    if key:
        h["X-Bridge-Key"] = key
    return h


def send_sync(cfg, phone: str, text: str) -> None:
    """One notification, synchronously. Raises on any bridge problem."""
    payload = json.dumps({"to": phone, "text": text}).encode()
    req = urllib.request.Request(
        f"{cfg.notify_bridge_url}/send", data=payload, headers=_headers(cfg))
    with urllib.request.urlopen(req, timeout=_BRIDGE_TIMEOUT_S) as r:
        r.read()


def _deliver(cfg, phone: str, text: str) -> None:
    try:
        send_sync(cfg, phone, text)
    except Exception as exc:  # noqa: BLE001 — notifications must never crash a thread
        log.warning("whatsapp notify to %s failed: %s", phone, exc)


def send(cfg, phone, text: str) -> None:
    """Queue a notification unless notifications are off or there is no number."""
    if not getattr(cfg, "notify_enabled", False) or not phone:
        return
    threading.Thread(target=_deliver, args=(cfg, phone, text), daemon=True).start()


def bridge_status(cfg, timeout: float = 3.0) -> dict:
    """Bridge health for the dashboard: reachable plus its link state."""
    key = getattr(cfg, "notify_bridge_key", "")
    req = urllib.request.Request(
        f"{cfg.notify_bridge_url}/status",
        headers={"X-Bridge-Key": key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"reachable": True, "bridge": json.loads(r.read())}
    except Exception as exc:  # noqa: BLE001 — status is informational
        return {"reachable": False, "error": str(exc)}


def bridge_qr(cfg, timeout: float = 3.0) -> bytes | None:
    """The pairing QR as PNG bytes while the bridge is unlinked, else None."""
    key = getattr(cfg, "notify_bridge_key", "")
    req = urllib.request.Request(
        f"{cfg.notify_bridge_url}/qr.png",
        headers={"X-Bridge-Key": key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return r.read()
    except Exception:  # noqa: BLE001 — the QR is best-effort
        pass
    return None


# --------------------------------------------------------------- messages ----
def _label(purpose: str) -> str:
    return "delivery trip" if purpose == "delivery" else "break"


def _duration(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def out_message(name: str, out_at, purpose: str) -> str:
    return f"👋 {name}, you went OUT at {out_at:%H:%M} ({_label(purpose)})."


def back_message(name: str, in_at, minutes: float, purpose: str,
                 inferred: bool = False) -> str:
    tail = " — no return detected, auto-closed" if inferred else ""
    return (f"✅ {name}, you're back at {in_at:%H:%M}. "
            f"Out time: {_duration(minutes)} ({_label(purpose)}){tail}.")


def trip_opened(cfg, name, phone, out_at, purpose: str) -> None:
    send(cfg, phone, out_message(name, out_at, purpose))


def trip_closed(cfg, name, phone, in_at, minutes: float, purpose: str,
                inferred: bool = False) -> None:
    send(cfg, phone, back_message(name, in_at, minutes, purpose, inferred))


def window_violation(cfg, name: str, out_at, windows_text) -> None:
    """Off-window trip: tell the operator, not the employee — this one is a
    monitoring alert, so it goes to notify_owner_phone only."""
    phone = getattr(cfg, "notify_owner_phone", "")
    if not phone:
        return
    send(cfg, phone,
         f"⚠️ {name} went OUT at {out_at:%H:%M} — outside their allowed "
         f"window ({windows_text or 'unspecified'}).")
