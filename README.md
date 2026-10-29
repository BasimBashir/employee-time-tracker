# Employee Time Tracker

Offline, Windows-native attendance tracking for small offices, built for one
cheap PC and one IP camera. The camera watches the door; computer vision
(person detection + face recognition + re-identification) decides who went out
and who came back — no badges, no fingerprints. Trips land in a local dashboard
with video clips as proof, namaz-break windows are enforced per employee
(riders and delivery staff exempt), WhatsApp polls ask *"going for Zuhr?"* at
each prayer slot and record the reason on the trip, and every Monday 3 pm each
employee gets a weekly summary on WhatsApp.

Ships as a single offline installer: the tracker runs as an auto-start Windows
service, a tray app manages it, and the dashboard lives in the browser at
`http://127.0.0.1:8080` — fully offline, no cloud, no accounts. Runs CPU-only
on low-end hardware; an NVIDIA GPU is used automatically when present.

```
service/    Windows Service: FastAPI API, SQLite (WAL), CV pipeline, watchdog
tray/       System-tray status icon + dashboard shortcut
ui/         Electron shell + React/Tailwind dashboard (built to ui/dist/renderer)
whatsapp_bridge/  Node/Baileys bridge: WhatsApp link, sends, inbound replies
installer/  Inno Setup script, service-install + autostart helpers
reports/    CSV / XLSX / PDF report writers
tests/      pytest suite (190+ tests, temp DBs only)
```

- Data root: `C:\ProgramData\EmployeeTracker\` (`clips\`, `models\`, `data\`, `logs\`, `config\`, `bridge\`)
- App root: `C:\Program Files\EmployeeTracker\`
- API: `http://127.0.0.1:8080` — localhost only
- Nothing payroll-relevant is hard-deleted: soft delete + `audit_log` only.
- Watchdog ceiling: 1500 MB RSS per process; automatic CPU fallback if the GPU is unusable.

---

## Getting started

### 1. Install

1. Download `EmployeeTrackerSetup.exe` from [Releases](../../releases).
2. Run it (admin). It installs to `C:\Program Files\EmployeeTracker`,
   creates the data folders under `C:\ProgramData\EmployeeTracker`,
   registers **EmployeeTrackerService** as an auto-start Windows service
   (restart-on-failure), and puts the tray app in your Run key.
3. After install the dashboard is live at **http://127.0.0.1:8080** — no
   reboot needed, though the service auto-starts at boot from then on.

Verify the service any time:

```bat
sc query EmployeeTrackerService   & rem STATE: RUNNING
```

The tray icon (green = service up) appears after logon; it opens the dashboard
and can restart the service.

### 2. Point it at the camera

**Settings → WhatsApp & camera**: paste your camera's RTSP URL, press
**Test connection** (shows the stream resolution if reachable), then
**Save & apply** — the analysis pipeline restarts on the new source.

HiKvision example: main stream `rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101`,
sub stream `…/102`.

### 3. Enroll faces

**Employees → Add employee** (name, department, role, WhatsApp number), then
click **Enroll** while the person stands at the door — the service captures
10 poses from the live stream and stores the embeddings. Re-enroll any time
glasses/beard change the face.

### 4. Draw the in/out line

Crossings are counted when a tracked person crosses one line per camera, in a
defined direction. The line is currently set through the API (no drawing UI
yet): coordinates are normalized `0..1`, `direction_out` is which side counts
as "out".

```bash
curl -X POST http://127.0.0.1:8080/zones -H "Content-Type: application/json" ^
  -d "{\"camera_id\": 1, \"line_points\": [[0.5, 0.1], [0.5, 0.9]], \"direction_out\": \"POS_TO_NEG\", \"enabled\": true}"
```

Every crossing records an IN/OUT event; a trip outside with no return is
auto-closed after 12 hours. Each out/in pair shows in **Today's records** with
the clip as proof.

### 5. Allowed time, namaz windows, exemptions

- **Allowed out minutes** per employee (default applies if unset) — the
  dashboard shows allowed vs extra minutes; going out outside allowed windows
  alerts the owner's WhatsApp.
- **Settings → Namaz windows**: the five prayer times per day (editable, with
  per-day overrides). Friday is the day off; the week is Sat–Thu.
- **Employees**: tick which prayers each employee attends; a personal offset
  (minutes) covers "my jamaat is 10 min later". Riders and delivery staff
  (role = delivery) are exempt from allowed-window limits — their trips count
  as work time.

### 6. Link WhatsApp

**Settings → WhatsApp & camera**:

1. Tick **Notifications on** and enter the **owner number** (gets off-window
   alerts and test messages).
2. Scan the pairing QR with the office phone (WhatsApp → Settings → Linked
   devices). The QR refreshes itself; once linked the panel shows the linked
   number. Use a **dedicated office SIM** — the bridge sends and receives as
   that account.
3. Press **Send test** to confirm.

The bridge is bundled with its own portable Node runtime — no Node.js needed
on the machine. Messages are paced like a human (queue, 0.8–2.5 s apart);
out/back notifications go to the employee's number if set.

### 7. Prayer polls and weekly reports

When an employee's prayer slot starts, they get one poll on WhatsApp:

> 🕌 Ali, Zuhr jamaat at 13:00. Are you going? Reply YES or NO — if NO, please
> add why (e.g. "no - doctor").

Replying **NO with a reason** records the reason on the trip in Today's
records (shown as a quoted chip) — that *is* the going-out reason in the
records. Every Monday at 15:00 each employee with a number gets a weekly
summary: minutes out against their allowance, delivery minutes, over/within
budget. Polls are asked exactly once per slot per day; a restart asks late but
never twice.

### 8. Reports

**Reports** page: per-day and per-employee tables, exported as CSV, XLSX and
PDF (saved under `C:\ProgramData\EmployeeTracker\reports`). Deleting an
employee is a soft delete with a reason — history stays intact.

---

## Building from source

Prereqs: Python 3.12+, Node 20+, Inno Setup 6. GPU optional (ONNX Runtime
DML; CPU-only works everywhere).

```bash
python -m pip install -r requirements.txt
python -m pytest -q                       # full suite, temp DBs only

python -m service.service_main run        # API + pipeline in the foreground
cd ui && npm install --legacy-peer-deps && npm run dev   # Vite dev server
python -m tray.tray_app                   # tray icon
```

Seed demo crossings without a camera:

```bash
python -m tools.record_simulator --script '[[1,"2026-10-27T09:00:00","2026-10-27T09:15:00"]]'
```

### Service and tray (PyInstaller)

```bash
python -m pip install -r requirements.txt pyinstaller
# --onedir, not --onefile: a onefile service pays a full self-extract on every
# SCM start and blows Windows' 30 s service-start budget on slow disks.
python -m PyInstaller --onedir --name service_main --paths . --collect-all uvicorn --exclude-module torch --exclude-module torchvision --add-data "service/schema.sql;service" installer/service_main_entry.py
python -m PyInstaller --onedir --windowed --name tray_app --paths . tray/tray_app.py
```

`installer/service_main_entry.py` hosts the service when the Service Control
Manager launches the exe with no arguments; explicit arguments keep the CLI
(`install/start/stop/restart/remove/run/debug`).

### UI

```bash
cd ui && npm install --legacy-peer-deps && npm run build
robocopy dist\renderer ..\installer\dist\ui\dist\renderer /E
```

### WhatsApp bridge (bundled — no Node install on the target)

```bash
mkdir installer\dist\bridge && cp whatsapp_bridge/* installer\dist\bridge/
cd installer\dist\bridge && npm ci --omit=dev
curl -o node.exe https://nodejs.org/dist/latest-v22.x/win-x64/node.exe
```

The only native module (sharp, pulled by Baileys) ships prebuilt win32-x64
binaries — no compiler needed on the target. The bridge installs to
`{commonappdata}\EmployeeTracker\bridge` (users-modify) because it writes its
session and log there at runtime.

### Installer (Inno Setup)

```bash
iscc installer\employee-tracker.iss
```

Produces `installer\dist\EmployeeTrackerSetup.exe`. Uninstall stops and
deletes the service; data under `C:\ProgramData` is kept.

---

## Notes

- **Privacy**: everything stays on the machine — SQLite, clips, embeddings.
  The API binds to localhost only; WhatsApp messages are the only traffic off
  the box (via the office SIM).
- **Offline**: enrollment, tracking, polls (once linked), reports and exports
  all work with no internet beyond WhatsApp's own connection.
- **WhatsApp note**: the bridge uses the Baileys library (unofficial, same
  category as WhatsApp Web). Keep volumes human; a dedicated SIM is strongly
  recommended. Accounts can be banned for automation abuse — that risk sits
  with the operator.
