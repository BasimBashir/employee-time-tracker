# WhatsApp bridge

A tiny local sidecar the employee tracker uses to send WhatsApp
notifications to employees (one-way today; the confirmation flow later
reads replies through the same session).

Built on [Baileys](https://github.com/WhiskeySockets/Baileys) — a
pure-WebSocket WhatsApp Web protocol client. No Chromium, no puppeteer,
a few tens of MB of RAM.

## Honest safety notes — read once

- This is **unofficial automation**. Meta's terms don't allow it, exactly
  like every free route (OpenWA, whatsapp-web.js, Baileys are all in the
  same ToS category; only the paid Meta Cloud API is official).
- **Use a dedicated, cheap SIM for the sender** — never your personal
  number. If WhatsApp ever bans the sender, you lose that SIM, nothing else.
- Ban risk at this project's volume (a few messages per employee per day,
  to employees who know you) is low. Risk comes from bulk/spam patterns —
  the bridge paces sends 0.8–2.5 s apart, one at a time, on purpose.
- The pairing QR and session live only in `./session` on this machine.

## Why Baileys and not OpenWA / whatsapp-web.js

Checked before choosing (2026-10): OpenWA works and its license (H-DNH)
permits this use, but it drives a headless Chrome (~200 MB RAM) and its
ethical license adds redistribution obligations. whatsapp-web.js moved
repos and collects ban/reliability complaints. Baileys is MIT, the most
actively maintained (commits daily), lightest on memory — matters under
the service's 1500 MB ceiling.

## Run it

```bash
cd whatsapp_bridge
npm install
npm start
```

Then open <http://127.0.0.1:8090/status> in a browser and scan the QR
with the **sender phone**: WhatsApp → Settings → Linked devices →
Link a device. The session persists in `./session` — one scan, ever
(until the phone unlinks; `POST /logout` wipes and restarts pairing).

## Wire the service to it

In `C:\ProgramData\EmployeeTracker\config\config.json`:

```json
{
  "notify_enabled": true,
  "notify_bridge_url": "http://127.0.0.1:8090",
  "notify_bridge_key": ""
}
```

`notify_bridge_key` is optional; set it and export the same value as the
`BRIDGE_KEY` env var when starting the bridge to require the header.

Set each employee's WhatsApp number on the dashboard report card (the
"WhatsApp" field, international format like `+923001234567`). Employees
without a number simply get no notifications.

## Endpoints

| Method | Path        | Purpose                                          |
|--------|-------------|--------------------------------------------------|
| GET    | `/status`   | Link state; `qr` is a data URL while pairing     |
| GET    | `/qr.png`   | The pairing QR as PNG                            |
| POST   | `/send`     | `{ "to": "+92300…", "text": "…" }` (queued)      |
| POST   | `/logout`   | Unlink and wipe the session, re-pair             |

## Auto-start with Windows

```powershell
powershell -ExecutionPolicy Bypass -File whatsapp_bridge\register-autostart.ps1
```

That registers an HKCU Run key (`EmployeeTrackerWhatsAppBridge`, same
pattern as the tray app — no admin needed) pointing at
`start-hidden.vbs`, which launches node with no console window, logging
to `whatsapp_bridge\bridge.log`. It also starts the bridge immediately,
so you only scan the QR once. The bridge resolves its session folder
from its own file location, so the working directory doesn't matter.

To undo: delete the `EmployeeTrackerWhatsAppBridge` value from
`HKCU:\Software\Microsoft\Windows\CurrentVersion\Run`.

The dashboard header shows the link state (linked / scan QR / linking /
logged out / down) once `notify_enabled` is on; "scan QR" opens the
pairing image directly (`/notify/qr` on the service, which proxies the
bridge — the dashboard stays same-origin).
