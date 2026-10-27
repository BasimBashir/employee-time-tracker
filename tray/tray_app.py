"""System-tray control surface.

Deliberately tiny — no frame decoding happens here. The tray polls the service's
/health endpoint once at startup to pick the icon colour, and offers a shortcut
to the dashboard plus a quit.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

# The tray is run as a script, so the repo root has to be on the path explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.config import load_config  # noqa: E402

GREEN = (46, 204, 113)
RED = (231, 76, 60)


def _icon(color):
    img = Image.new("RGB", (64, 64), color)
    ImageDraw.Draw(img).rectangle([16, 16, 48, 48], fill="white")
    return img


def service_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.load(r).get("status") == "ok"
    except Exception:
        return False


def run_tray() -> None:
    port = load_config().port
    ok = service_ok(port)

    def on_open(icon, item):
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/")

    def on_recheck(icon, item):
        green = service_ok(port)
        icon.icon = _icon(GREEN if green else RED)
        icon.title = f"Employee Tracker — {'service up' if green else 'service DOWN'}"

    def on_quit(icon, item):
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", on_open),
        pystray.MenuItem("Re-check service", on_recheck),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon(
        "employee_tracker", _icon(GREEN if ok else RED),
        f"Employee Tracker — {'service up' if ok else 'service DOWN'}", menu=menu)
    icon.run()


if __name__ == "__main__":
    run_tray()
