# Employee Time Management

Windows-native employee entry/exit tracking: a Python **Windows Service** owns the
data and a localhost JSON API; a **tray app** shows service status; an **Electron +
React** dashboard renders attendance. Phase 1 ships the skeleton (no CV pipeline
yet) so later phases slot in behind stable interfaces.

```
service/   Windows Service: FastAPI API, SQLite (WAL), watchdog, JSON logs
tray/      System-tray status icon + dashboard shortcut
ui/        Electron shell + React/Tailwind dashboard (built to ui/dist/renderer)
tools/     record_simulator.py — scripted crossings so the skeleton is demonstrable
installer/ Inno Setup script, service-install + autostart helpers
tests/     pytest suite (18 tests)
```

- Data root: `C:\ProgramData\EmployeeTracker\` (`clips\`, `models\`, `data\`, `logs\`, `config\`)
- App root: `C:\Program Files\EmployeeTracker\`
- API: `http://127.0.0.1:8080` (health, events, sessions, soft delete, rss)
- Nothing payroll-relevant is hard-deleted: soft delete + `audit_log` only.
- Watchdog ceiling: 1500 MB RSS per process.

## Development

```bash
python -m pip install -r requirements.txt
python -m pytest -q                       # 18 tests, temp DBs only

python -m service.service_main run        # API + watchdog in the foreground
cd ui && npm install --legacy-peer-deps && npm run dev   # dashboard (Vite dev server)
python -m tray.tray_app                   # tray icon (green = service up)
```

Seed demo crossings without a camera:

```bash
python -m tools.record_simulator --script '[[1,"2026-10-27T09:00:00","2026-10-27T09:15:00"]]'
```

The dashboard also opens straight from the service at
`http://127.0.0.1:8080/` — `create_app` mounts `ui/dist/renderer` when it exists.

# Building

## Service and tray (PyInstaller)

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --onefile --name service_main --paths . --collect-all uvicorn --exclude-module torch --exclude-module torchvision --add-data "service/schema.sql;service" installer/service_main_entry.py
python -m PyInstaller --onefile --name tray_app --paths . tray/tray_app.py
```

`installer/service_main_entry.py` hosts the service when the Service Control
Manager launches the exe with no arguments; explicit arguments keep the CLI
(`install/start/stop/restart/remove/run/debug`).

## UI

```bash
cd ui && npm install --legacy-peer-deps && npm run build
robocopy . ..\dist\ui /E /XD dist node_modules\.cache   # stage the Electron shell
```

The staged `dist\ui` keeps `main.js`, `preload.js` and `node_modules` (which
carries the Electron runtime), with the built renderer under `dist\renderer`.

## Installer (Inno Setup)

```bash
iscc installer\employee-tracker.iss
```

Produces `installer\dist\EmployeeTrackerSetup.exe`, which:

1. lays out `{app}\service`, `{app}\tray`, `{app}\ui` (app root),
2. creates `C:\ProgramData\EmployeeTracker\{clips,data,logs,models,config}`,
3. registers `EmployeeTrackerService` as an auto-start service with restart-on-failure
   (`install-service.bat`, passed the install dir),
4. adds the tray app to the current user's Run key (`register-autostart.ps1`).

Uninstall stops and deletes the service; data under `C:\ProgramData` is kept.

## Verify on the target machine (admin required)

```bash
sc qc EmployeeTrackerService     & rem START_TYPE: 2 AUTO_START
sc query EmployeeTrackerService  & rem STATE: RUNNING
```

Reboot and re-run the query — the service must be RUNNING with no user logged in;
the tray icon appears after logon.
