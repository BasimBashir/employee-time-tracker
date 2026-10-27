# Phase 1: Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three-process shell — Windows Service, tray app, Electron dashboard — with SQLite schema, config, autostart, and a packaging pipeline that produces a working `.exe` that starts at Windows boot.

**Architecture:** A Python Windows Service owns state and data; a Python tray app and an Electron dashboard are thin clients over a localhost JSON API. Phase 1 ships no CV pipeline — it proves the skeleton (service running at boot, UI connecting, schema in place, events append live) so Phases 2–6 slot in behind stable interfaces.

**Tech Stack:** Python 3.11+, pywin32 (Windows service), FastAPI/uvicorn (localhost JSON API), SQLite (WAL), Electron + electron-builder, pytest.

## Global Constraints

- Target: Windows 11, low-end hardware (16 GB DDR4, 6th-gen Intel, no dGPU). No process may exceed 1.5 GB RSS (watchdog ceiling).
- Nothing payroll-relevant is hard-deleted: soft-delete + `audit_log` rows only.
- Data root: `C:\ProgramData\EmployeeTracker\` (`clips\`, `models\`, `data\`, `logs\`, `config\`).
- App root: `C:\Program Files\EmployeeTracker\`.
- All services/workers: `daemon=False`, explicit `stop()` + `join(timeout)`.
- Test DB is always temp/in-memory, never the live DB.
- Python 3.11+, Windows 11. Library versions must be pulled from upstream docs at implementation time (see spec §15) — do not pin from memory.

---

## File Structure

```
Employee-Time-Management/
├─ service/
│  ├─ __init__.py
│  ├─ config.py            # load/save config.json, defaults, paths
│  ├─ db.py                # connection, WAL, schema migration runner
│  ├─ schema.sql           # full DDL (spec §8)
│  ├─ api.py               # FastAPI app factory + routes
│  ├─ service_main.py      # pywin32 Windows Service wrapper
│  ├─ watchdog.py          # RSS/thread sampler, worker restarts
│  └─ logging_setup.py     # JSON-lines rotating logs
├─ tray/
│  └─ tray_app.py          # status icon, show UI, quit
├─ ui/                     # Electron dashboard
│  ├─ package.json
│  ├─ main.js              # single BrowserWindow
│  ├─ preload.js
│  └─ renderer/
│     ├─ index.html
│     ├─ app.js
│     └─ styles.css
├─ tools/
│  └─ record_simulator.py  # scripted crossings for --simulate
├─ installer/
│  └─ employee-tracker.iss # Inno Setup (chains GStreamer later; Phase 1 app only)
├─ tests/
│  ├─ conftest.py
│  ├─ test_config.py
│  ├─ test_db.py
│  ├─ test_api.py
│  └─ test_watchdog.py
├─ requirements.txt
└─ README.md
```

### Responsibility boundaries

- `config.py` knows paths and defaults; nothing else builds a path by hand.
- `db.py` owns the connection and schema; nothing else writes DDL.
- `api.py` is the only HTTP surface; service, tray and UI never touch the DB directly except through `db.py`.
- `watchdog.py` observes; it never writes attendance data.

---

## Task 1: Project scaffold and config

**Files:**
- Create: `requirements.txt`, `service/__init__.py`, `service/config.py`
- Test: `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_config(path=None) -> Config`, `Config.data_root: Path`, `Config.app_root: Path`, `Config.port: int`, `Config.logs_dir: Path`, `Config.clips_dir: Path`, `Config.db_path: Path`, `Config.save() -> None`, `Config.get(key, default)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import json
from pathlib import Path
from service.config import load_config

def test_defaults_and_override(tmp_path):
    cfgpath = tmp_path / "config.json"
    cfgpath.write_text(json.dumps({"port": 9090}))
    cfg = load_config(cfgpath)
    assert cfg.port == 9090
    assert cfg.data_root == Path(r"C:\ProgramData\EmployeeTracker")

def test_data_root_creates_dirs(tmp_path):
    cfgpath = tmp_path / "config.json"
    cfg = load_config(cfgpath)
    cfg.data_root = tmp_path / "data"
    cfg.ensure_dirs()
    assert (tmp_path / "data" / "logs").is_dir()
    assert (tmp_path / "data" / "clips").is_dir()
    assert (tmp_path / "data" / "models").is_dir()
    assert (tmp_path / "data" / "tracker.db").name == "tracker.db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.config'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/config.py
import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_ROOT = Path(r"C:\ProgramData\EmployeeTracker")

@dataclass
class Config:
    data_root: Path = DEFAULT_DATA_ROOT
    port: int = 8080
    watchdog_rss_ceiling_mb: int = 1500
    clip_retention_days: int = 90
    disk_floor_gb: int = 10
    analysis_target_latency_ms: int = 120
    confirm_threshold: float = 0.45
    uncertain_threshold: float = 0.35
    _extra: dict = field(default_factory=dict)

    @property
    def app_root(self) -> Path:
        return Path(r"C:\Program Files\EmployeeTracker")

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def clips_dir(self) -> Path:
        return self.data_root / "clips"

    @property
    def models_dir(self) -> Path:
        return self.data_root / "models"

    @property
    def db_path(self) -> Path:
        return self.data_root / "data" / "tracker.db"

    @property
    def config_path(self) -> Path:
        return self.data_root / "config" / "config.json"

    def ensure_dirs(self) -> None:
        for d in (self.logs_dir, self.clips_dir, self.models_dir,
                  self.data_root / "data", self.config_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    def get(self, key, default=None):
        return self._extra.get(key, default)

    def save(self) -> None:
        self.ensure_dirs()
        payload = {
            "port": self.port,
            "watchdog_rss_ceiling_mb": self.watchdog_rss_ceiling_mb,
            "clip_retention_days": self.clip_retention_days,
            "disk_floor_gb": self.disk_floor_gb,
            "analysis_target_latency_ms": self.analysis_target_latency_ms,
            "confirm_threshold": self.confirm_threshold,
            "uncertain_threshold": self.uncertain_threshold,
            **self._extra,
        }
        self.config_path.write_text(json.dumps(payload, indent=2))

def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    if path is None:
        path = cfg.config_path
    if Path(path).exists():
        data = json.loads(Path(path).read_text())
        known = {k: v for k, v in data.items() if hasattr(Config(), k)}
        extra = {k: v for k, v in data.items() if k not in known}
        for k, v in known.items():
            setattr(cfg, k, Path(v) if k == "data_root" else v)
        cfg._extra = extra
    return cfg
```

`requirements.txt`:
```
fastapi
uvicorn[standard]
pywin32
psutil
pytest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt service/ tests/
git commit -m "feat: project scaffold and config with data-root dirs"
```

---

## Task 2: SQLite schema, WAL, and soft-delete helpers

**Files:**
- Create: `service/schema.sql`, `service/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Config` from Task 1 (`cfg.db_path`).
- Produces: `connect(cfg) -> sqlite3.Connection`, `migrate(conn) -> None`, `insert_event(conn, employee_id, camera_id, event_type, occurred_at, clip_path, confidence, uncertain, track_id) -> int`, `open_session(conn, employee_id, out_event_id, out_at, date) -> int`, `close_session(conn, session_id, in_event_id, in_at) -> float` (returns duration minutes), `force_close_open_sessions(conn, on_date) -> int`, `soft_delete_session(conn, session_id, actor, reason) -> None`, `audit(conn, actor, action, target, detail) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import sqlite3, tempfile
from datetime import datetime
from pathlib import Path
from service.db import connect, migrate, insert_event, open_session, close_session, force_close_open_sessions, soft_delete_session, audit

@pytest.fixture
def conn(tmp_path):
    c = connect(Path(tmp_path / "t.db")); migrate(c); return c

def test_wal_enabled(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

def test_session_roundtrip(conn):
    out_id = insert_event(conn, 1, 1, "OUT", datetime(2026,10,26,13,0), None, 0.9, 0, 7)
    sid = open_session(conn, 1, out_id, datetime(2026,10,26,13,0), "2026-10-26")
    assert close_session(conn, sid, 0, datetime(2026,10,26,13,15)) == 15.0

def test_midnight_force_close_flags_inferred(conn):
    out_id = insert_event(conn, 1, 1, "OUT", datetime(2026,10,26,22,0), None, 0.9, 0, 7)
    open_session(conn, 1, out_id, datetime(2026,10,26,22,0), "2026-10-26")
    n = force_close_open_sessions(conn, "2026-10-26")
    assert n == 1
    row = conn.execute("SELECT inferred_return FROM sessions").fetchone()
    assert row[0] == 1

def test_soft_delete_keeps_row_and_audits(conn):
    out_id = insert_event(conn, 1, 1, "OUT", datetime(2026,10,26,13,0), None, 0.9, 0, 7)
    sid = open_session(conn, 1, out_id, datetime(2026,10,26,13,0), "2026-10-26")
    soft_delete_session(conn, sid, "owner", "disputed")
    assert conn.execute("SELECT deleted_at FROM sessions WHERE id=?", (sid,)).fetchone()[0] is not None
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='delete_session'").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.db'`

- [ ] **Step 3: Write the minimal implementation**

```sql
-- service/schema.sql
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, employee_code TEXT,
  department TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS enrollments (
  id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
  face_embedding BLOB, body_embedding BLOB, quality_score REAL,
  captured_at TEXT NOT NULL, is_primary INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cameras (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, rtsp_url TEXT,
  decode_element TEXT, provider TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY, camera_id INTEGER NOT NULL REFERENCES cameras(id),
  line_points TEXT NOT NULL, direction_out TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
  camera_id INTEGER NOT NULL REFERENCES cameras(id),
  event_type TEXT NOT NULL CHECK (event_type IN ('OUT','IN')),
  occurred_at TEXT NOT NULL, clip_path TEXT, confidence REAL,
  uncertain INTEGER NOT NULL DEFAULT 0, track_id INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
  out_event_id INTEGER NOT NULL REFERENCES events(id),
  in_event_id INTEGER REFERENCES events(id),
  out_at TEXT NOT NULL, in_at TEXT,
  duration_minutes REAL, inferred_return INTEGER NOT NULL DEFAULT 0,
  date TEXT NOT NULL, deleted_at TEXT, deleted_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date, employee_id);
CREATE INDEX IF NOT EXISTS idx_events_emp ON events(employee_id, occurred_at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY, actor TEXT, action TEXT, target TEXT,
  detail_json TEXT, at TEXT NOT NULL
);
```

```python
# service/db.py
import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

def connect(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c

def migrate(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()

def insert_event(conn, employee_id, camera_id, event_type, occurred_at,
                 clip_path=None, confidence=0.0, uncertain=0, track_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO events (employee_id,camera_id,event_type,occurred_at,clip_path,confidence,uncertain,track_id)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (employee_id, camera_id, event_type, occurred_at.isoformat(), clip_path, confidence, uncertain, track_id))
    conn.commit()
    return cur.lastrowid

def open_session(conn, employee_id, out_event_id, out_at, date) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (employee_id,out_event_id,out_at,date) VALUES (?,?,?,?)",
        (employee_id, out_event_id, out_at.isoformat(), date))
    conn.commit()
    return cur.lastrowid

def close_session(conn, session_id, in_event_id, in_at) -> float:
    row = conn.execute("SELECT out_at FROM sessions WHERE id=?", (session_id,)).fetchone()
    out_at = datetime.fromisoformat(row["out_at"])
    minutes = (in_at - out_at).total_seconds() / 60.0
    conn.execute(
        "UPDATE sessions SET in_event_id=?, in_at=?, duration_minutes=? WHERE id=?",
        (in_event_id, in_at.isoformat(), minutes, session_id))
    conn.commit()
    return minutes

def force_close_open_sessions(conn, on_date) -> int:
    rows = conn.execute(
        "SELECT id FROM sessions WHERE date=? AND in_at IS NULL", (on_date,)).fetchall()
    close_at = datetime.fromisoformat(f"{on_date}T23:59:59")
    for r in rows:
        conn.execute(
            "UPDATE sessions SET in_at=?, duration_minutes=?, inferred_return=1 WHERE id=?",
            (close_at.isoformat(),
             (close_at - datetime.fromisoformat(on_date + "T00:00:00")).total_seconds()/60.0,
             r["id"]))
    conn.commit()
    return len(rows)

def soft_delete_session(conn, session_id, actor, reason) -> None:
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE sessions SET deleted_at=?, deleted_reason=? WHERE id=?", (now, reason, session_id))
    conn.commit()
    audit(conn, actor, "delete_session", str(session_id), {"reason": reason})

def audit(conn, actor, action, target, detail) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor,action,target,detail_json,at) VALUES (?,?,?,?,?)",
        (actor, action, target, json.dumps(detail), datetime.utcnow().isoformat()))
    conn.commit()
```

`service/__init__.py`:
```python
# service/__init__.py
```

`tests/conftest.py`:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

Add to `requirements.txt`: nothing new (`sqlite3` and `json` are stdlib).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add service/ tests/
git commit -m "feat: SQLite schema with WAL, sessions, soft-delete, audit"
```

---

## Task 3: Localhost JSON API

**Files:**
- Create: `service/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `connect`, `migrate` (Task 2); `Config` (Task 1).
- Produces: `create_app(conn, cfg) -> FastAPI`, routes `GET /health`, `GET /events?since=<iso>`, `GET /sessions?date=YYYY-MM-DD`, `DELETE /sessions/{id}` (body: `{actor, reason}`), `GET /health/rss`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api.py
import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from service.db import connect, migrate, insert_event, open_session, close_session
from service.api import create_app

def make_client(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    return TestClient(create_app(conn, None))

def test_health(tmp_path):
    r = make_client(tmp_path).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

def test_events_since(tmp_path):
    c = make_client(tmp_path)
    insert_event(c.app.state.conn, 1, 1, "OUT", datetime(2026,10,26,13,0), None, 0.9, 0, 7)
    r = c.get("/events", params={"since": "2026-10-26T00:00:00"})
    assert len(r.json()) == 1

def test_soft_delete_via_api(tmp_path):
    c = make_client(tmp_path)
    ev = insert_event(c.app.state.conn, 1, 1, "OUT", datetime(2026,10,26,13,0), None, 0.9, 0, 7)
    sid = open_session(c.app.state.conn, 1, ev, datetime(2026,10,26,13,0), "2026-10-26")
    r = c.delete(f"/sessions/{sid}", json={"actor": "owner", "reason": "disputed"})
    assert r.status_code == 200
    rows = c.get("/sessions", params={"date": "2026-10-26"}).json()
    assert rows[0]["deleted_reason"] == "disputed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.api'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/api.py
import os, psutil
from fastapi import FastAPI, Body
from . import db as dbmod

def create_app(conn, cfg) -> FastAPI:
    app = FastAPI()
    app.state.conn = conn
    app.state.cfg = cfg

    @app.get("/health")
    def health():
        return {"status": "ok", "pid": os.getpid()}

    @app.get("/health/rss")
    def rss():
        p = psutil.Process(os.getpid())
        return {"rss_mb": round(p.memory_info().rss / 1048576, 1)}

    @app.get("/events")
    def events(since: str):
        rows = conn.execute(
            "SELECT * FROM events WHERE occurred_at >= ? ORDER BY id", (since,)).fetchall()
        return [dict(r) for r in rows]

    @app.get("/sessions")
    def sessions(date: str):
        rows = conn.execute(
            "SELECT * FROM sessions WHERE date = ? ORDER BY id", (date,)).fetchall()
        return [dict(r) for r in rows]

    @app.delete("/sessions/{session_id}")
    def del_session(session_id: int, body: dict = Body(...)):
        dbmod.soft_delete_session(conn, session_id, body.get("actor", "?"), body.get("reason", ""))
        return {"deleted": session_id}

    return app
```

Add to `requirements.txt`: `httpx` (TestClient dependency). `fastapi` and `uvicorn` are already present.

Also create `setup.cfg` so `service` and `tools` import as packages from the repo root:
```ini
[options]
packages = find:
```
And create `pytest.ini`:
```ini
[pytest]
testpaths = tests
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add service/ tests/ requirements.txt
git commit -m "feat: localhost JSON API with health, events, sessions, soft delete"
```

---

## Task 4: Watchdog

**Files:**
- Create: `service/watchdog.py`
- Test: `tests/test_watchdog.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `psutil`.
- Produces: `class Watchdog` with `__init__(cfg, restart_callback)`, `sample() -> dict` (`{"rss_mb": float, "threads": int, "over_ceiling": bool}`), `start()`, `stop()`, and a `restart_requested: bool` flag.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watchdog.py
import time
from service.watchdog import Watchdog
from service.config import Config

def test_sample_reports_fields_and_flag():
    cfg = Config(); cfg.watchdog_rss_ceiling_mb = 1  # force over-ceiling
    w = Watchdog(cfg, restart_callback=lambda: None)
    s = w.sample()
    assert "rss_mb" in s and "threads" in s
    assert s["over_ceiling"] is True

def test_stop_is_clean():
    w = Watchdog(Config(), restart_callback=lambda: None)
    w.start(); time.sleep(0.2); w.stop()
    assert w.thread.is_alive() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_watchdog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.watchdog'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/watchdog.py
import os, threading, time, psutil

class Watchdog:
    def __init__(self, cfg, restart_callback):
        self.cfg = cfg
        self.restart_callback = restart_callback
        self.restart_requested = False
        self._stop = threading.Event()
        self.thread = None

    def sample(self) -> dict:
        p = psutil.Process(os.getpid())
        rss_mb = p.memory_info().rss / 1048576
        return {
            "rss_mb": round(rss_mb, 1),
            "threads": p.num_threads(),
            "over_ceiling": rss_mb > self.cfg.watchdog_rss_ceiling_mb,
        }

    def _run(self):
        while not self._stop.wait(60):
            s = self.sample()
            if s["over_ceiling"] and not self.restart_requested:
                self.restart_requested = True
                self.restart_callback()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=False)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=5)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_watchdog.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add service/ tests/
git commit -m "feat: watchdog RSS/thread sampler with supervised restart hook"
```

---

## Task 5: Windows Service wrapper

**Files:**
- Create: `service/service_main.py`, `service/logging_setup.py`
- Test: `tests/test_logging_setup.py`

**Interfaces:**
- Consumes: `Config`, `create_app`, `Watchdog` (Tasks 1, 3, 4).
- Produces: `build_server(cfg) -> tuple[uvicorn.Server, Watchdog]`, `class TrackerService(win32serviceutil.ServiceFramework)` named `EmployeeTrackerService`, `service_logger(name) -> logging.Logger`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logging_setup.py
import json, logging
from pathlib import Path
from service.logging_setup import service_logger

def test_json_lines_format(tmp_path):
    log = service_logger("test_jsonlines", tmp_path)
    log.info("hello %s", "world")
    lines = (tmp_path / "service.log").read_text().strip().splitlines()
    rec = json.loads(lines[0])
    assert rec["msg"] == "hello world"
    assert rec["logger"] == "test_jsonlines"
    assert rec["level"] == "INFO"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logging_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service.logging_setup'`

- [ ] **Step 3: Write the minimal implementation**

```python
# service/logging_setup.py
import logging, logging.handlers, json
from pathlib import Path

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "thread": record.threadName,
        })

def service_logger(name, log_dir: Path | None = None) -> logging.Logger:
    """Named logger writing JSON-lines to `log_dir/service.log` (rotating)."""
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:
        return log
    if log_dir is None:
        log_dir = Path(r"C:\ProgramData\EmployeeTracker\logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(
        log_dir / "service.log", maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h.setFormatter(JsonFormatter())
    log.addHandler(h)
    stream = logging.StreamHandler()
    stream.setFormatter(JsonFormatter())
    log.addHandler(stream)
    return log
```

```python
# service/service_main.py
import sys, threading, time, uvicorn
import win32serviceutil, win32service, win32event, servicemanager
from .config import load_config
from .db import connect, migrate
from .api import create_app
from .watchdog import Watchdog
from .logging_setup import service_logger

log = service_logger("service_main")

def build_server(cfg):
    conn = connect(cfg.db_path)
    migrate(conn)
    app = create_app(conn, cfg)
    config = uvicorn.Config(app, host="127.0.0.1", port=cfg.port, log_level="warning")
    server = uvicorn.Server(config)
    return server, Watchdog(cfg, restart_callback=lambda: log.warning("restart requested"))

class TrackerService(win32serviceutil.ServiceFramework):
    _svc_name_ = "EmployeeTrackerService"
    _svc_display_name_ = "Employee Tracker Service"
    _svc_description_ = "Captures entry/exit crossings and records attendance."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server = None
        self.watchdog = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        cfg = load_config()
        self.server, self.watchdog = build_server(cfg)
        self.watchdog.start()
        t = threading.Thread(target=self.server.run, daemon=False)
        t.start()
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        self.server.should_exit = True
        t.join(timeout=10)
        self.watchdog.stop()

if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(TrackerService)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logging_setup.py -v`
Expected: PASS (1 test). The service itself is verified by Step 5's manual command; pywin32 code paths are not unit-testable headlessly.

- [ ] **Step 5: Verify service install/start manually**

```bash
python service/service_main.py --startup auto install
python service/service_main.py start
python service/service_main.py debug
```

Then in a second shell confirm the API answers:

```bash
curl http://127.0.0.1:8080/health
```

Expected: `{"status":"ok","pid":...}`. Stop with `python service/service_main.py stop`.

- [ ] **Step 6: Commit**

```bash
git add service/ tests/
git commit -m "feat: Windows Service wrapper with structured logging and watchdog"
```

---

## Task 6: Tray app

**Files:**
- Create: `tray/tray_app.py`
- Test: none (UI shell; verified manually in Step 5)

**Interfaces:**
- Consumes: `Config.port` (Task 1) via `load_config`.
- Produces: `run_tray() -> None`; opens `http://127.0.0.1:<port>/health` to show status.

- [ ] **Step 1: Write the tray app**

```python
# tray/tray_app.py
import sys, threading, urllib.request, json
import pystray
from PIL import Image, ImageDraw

def _icon(color):
    img = Image.new("RGB", (64, 64), color)
    d = ImageDraw.Draw(img); d.rectangle([16, 16, 48, 48], fill="white")
    return img

def _service_ok(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.load(r).get("status") == "ok"
    except Exception:
        return False

def run_tray():
    from .config import load_config
    port = load_config().port
    ok = _service_ok(port)

    def on_open(icon, item):
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/ui")

    def on_quit(icon, item):
        icon.stop()

    icon = pystray.Icon("employee_tracker", _icon((0, 160, 60) if ok else (200, 40, 40)),
                        "Employee Tracker", menu=pystray.Menu(
                            pystray.MenuItem("Open dashboard", on_open),
                            pystray.MenuItem("Quit", on_quit)))
    icon.run()

if __name__ == "__main__":
    run_tray()
```

Add to `requirements.txt`: `pystray`, `pillow`.

- [ ] **Step 2: Verify tray launches**

Run: `python -m tray.tray_app`
Expected: a green (service up) or red (service down) tray icon appears with an
"Open dashboard" and "Quit" menu. Quit exits cleanly with no lingering process.

- [ ] **Step 3: Commit**

```bash
git add tray/ requirements.txt
git commit -m "feat: tray app with service status indicator"
```

---

## Task 7: Electron dashboard shell

**Files:**
- Create: `ui/package.json`, `ui/main.js`, `ui/preload.js`, `ui/renderer/index.html`, `ui/renderer/app.js`, `ui/renderer/styles.css`

**Interfaces:**
- Consumes: Task 3 API on `127.0.0.1:<port>`.
- Produces: a single `BrowserWindow` app whose renderer polls `GET /events?since=…` every 2 s and appends rows live.

- [ ] **Step 1: Write the Electron shell**

`ui/package.json`:
```json
{
  "name": "employee-tracker-ui",
  "version": "0.1.0",
  "main": "main.js",
  "scripts": { "start": "electron .", "dist": "electron-builder --win" },
  "devDependencies": { "electron": "^31.0.0", "electron-builder": "^24.13.3" },
  "build": {
    "appId": "com.employeetracker.ui",
    "win": { "target": "nsis" },
    "nsis": { "oneClick": false, "allowToChangeInstallationDirectory": true }
  }
}
```

`ui/main.js`:
```javascript
const { app, BrowserWindow } = require('electron');
const path = require('path');

app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');
app.disableHardwareAcceleration(); // low-end target: no GPU process churn

function createWindow() {
  const win = new BrowserWindow({
    width: 1280, height: 800,
    backgroundColor: '#12151c',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      backgroundThrottling: false,
      sandbox: true
    }
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
```

`ui/preload.js`:
```javascript
const { contextBridge } = require('electron');
contextBridge.exposeInMainWorld('tracker', {
  apiBase: 'http://127.0.0.1:8080',
  fetchEvents: (since) => fetch(`http://127.0.0.1:8080/events?since=${encodeURIComponent(since)}`).then(r => r.json()),
  fetchSessions: (date) => fetch(`http://127.0.0.1:8080/sessions?date=${date}`).then(r => r.json()),
  deleteSession: (id, actor, reason) => fetch(`http://127.0.0.1:8080/sessions/${id}`, {
    method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ actor, reason })
  }).then(r => r.json())
});
```

`ui/renderer/index.html`:
```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Employee Tracker</title>
<link rel="stylesheet" href="styles.css"></head>
<body>
  <header><h1>Employee Tracker</h1><span id="status" class="dot"></span></header>
  <main>
    <section><h2>Live events</h2><table id="events"><thead><tr>
      <th>Time</th><th>Employee</th><th>Type</th><th>Confidence</th>
    </tr></thead><tbody></tbody></table></section>
    <section><h2>Sessions</h2><table id="sessions"><thead><tr>
      <th>Employee</th><th>Out</th><th>In</th><th>Minutes</th><th></th>
    </tr></thead><tbody></tbody></table></section>
  </main>
  <script src="app.js"></script>
</body></html>
```

`ui/renderer/styles.css`:
```css
:root { color-scheme: dark; }
body { margin: 0; font-family: "Segoe UI", sans-serif; background: #12151c; color: #e6e9ef; }
header { display: flex; align-items: center; gap: 10px; padding: 12px 20px; background: #171b24; }
h1 { font-size: 16px; margin: 0; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #555; }
.dot.ok { background: #2ecc71; } .dot.bad { background: #e74c3c; }
main { padding: 20px; display: grid; gap: 24px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #232936; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: #8b95a7; }
button { background: #232936; color: #e6e9ef; border: 1px solid #303948; border-radius: 4px; padding: 3px 8px; cursor: pointer; }
```

`ui/renderer/app.js`:
```javascript
let since = new Date(0).toISOString();
const evBody = document.querySelector('#events tbody');
const sesBody = document.querySelector('#sessions tbody');
const dot = document.getElementById('status');

function row(cells, onClick) {
  const tr = document.createElement('tr');
  cells.forEach(c => { const td = document.createElement('td'); td.textContent = c; tr.appendChild(td); });
  if (onClick) { const td = document.createElement('td'); const b = document.createElement('button');
    b.textContent = 'Delete'; b.onclick = onClick; td.appendChild(b); tr.appendChild(td); }
  return tr;
}

async function pollEvents() {
  try {
    const evs = await window.tracker.fetchEvents(since);
    evs.forEach(e => { evBody.prepend(row([e.occurred_at, `#${e.employee_id}`, e.event_type, e.confidence]));
      since = e.occurred_at; });
    dot.className = 'dot ok';
  } catch { dot.className = 'dot bad'; }
}

async function loadSessions() {
  const date = new Date().toISOString().slice(0, 10);
  const rows = await window.tracker.fetchSessions(date);
  sesBody.innerHTML = '';
  rows.filter(r => !r.deleted_at).forEach(r => sesBody.appendChild(
    row([`#${r.employee_id}`, r.out_at, r.in_at || '—', r.duration_minutes ?? '—'],
      () => { const reason = prompt('Reason?'); if (reason)
        window.tracker.deleteSession(r.id, 'owner', reason).then(loadSessions); })));
}

pollEvents(); loadSessions(); setInterval(pollEvents, 2000);
```

- [ ] **Step 2: Verify the dashboard builds and renders**

```bash
cd ui && npm install && npm start
```

Expected: a dark window titled "Employee Tracker" showing a green status dot (service
running from Task 5) and two empty tables that populate as `--simulate` events arrive.

- [ ] **Step 3: Commit**

```bash
git add ui/
git commit -m "feat: Electron dashboard shell polling live events and sessions"
```

---

## Task 8: `--simulate` end-to-end proof

**Files:**
- Create: `tools/record_simulator.py`

**Interfaces:**
- Consumes: `insert_event`, `open_session`, `close_session` (Task 2); `Config.port`.
- Produces: `simulate(conn, camera_id, script) -> None` where `script` is a list of
  `(employee_id, out_iso, in_iso)` tuples; `python -m tools.record_simulator --script …`
  writes events and sessions at 2 s intervals so the UI visibly appends.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_record_simulator.py
from datetime import datetime, timedelta
from service.db import connect, migrate
from tools.record_simulator import simulate

def test_simulate_writes_paired_session(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    simulate(conn, 1, [(1, datetime(2026,10,26,13,0), datetime(2026,10,26,13,15))])
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    row = conn.execute("SELECT duration_minutes, inferred_return FROM sessions").fetchone()
    assert row["duration_minutes"] == 15.0 and row["inferred_return"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_record_simulator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.record_simulator'`

- [ ] **Step 3: Write the minimal implementation**

```python
# tools/record_simulator.py
"""Scripted crossings, so the whole product is demonstrable without a camera."""
import argparse, sqlite3, time
from datetime import datetime
from service.db import insert_event, open_session, close_session
from service.config import load_config

def simulate(conn: sqlite3.Connection, camera_id: int, script) -> None:
    for employee_id, out_at, in_at in script:
        out_ev = insert_event(conn, employee_id, camera_id, "OUT", out_at, None, 0.9, 0, 0)
        sid = open_session(conn, employee_id, out_ev, out_at, out_at.date().isoformat())
        if in_at is not None:
            in_ev = insert_event(conn, employee_id, camera_id, "IN", in_at, None, 0.85, 0, 0)
            close_session(conn, sid, in_ev, in_at)
        time.sleep(2)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--script", required=True,
                   help='JSON list of [employee_id,"2026-10-26T13:00","2026-10-26T13:15"]')
    a = p.parse_args()
    cfg = load_config()
    cfg.ensure_dirs()
    conn = sqlite3.connect(str(cfg.db_path))
    data = json.loads(a.script)
    simulate(conn, 1, [(int(e[0]), datetime.fromisoformat(e[1]),
                        datetime.fromisoformat(e[2]) if e[2] else None) for e in data])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_record_simulator.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run the end-to-end demo**

Terminal 1:
```bash
python -m service.service_main debug
```
Terminal 2:
```bash
python -m tools.record_simulator --script '[[1,"2026-10-26T13:00:00","2026-10-26T13:15:00"],[1,"2026-10-26T13:20:00","2026-10-26T13:35:00"]]'
```
Terminal 3:
```bash
cd ui && npm start
```
Expected: rows appear in the dashboard's Live events within ~2 s of each simulated
crossing, and the Sessions table shows a 15-minute session with a working Delete button
that prompts for a reason and clears the row.

- [ ] **Step 6: Commit**

```bash
git add tools/ tests/ service/
git commit -m "feat: record simulator making the skeleton demonstrable without RTSP"
```

---

## Task 9: Installer and autostart packaging

**Files:**
- Create: `installer/employee-tracker.iss`, `installer/install-service.bat`, `installer/register-autostart.ps1`, `README.md`

**Interfaces:**
- Consumes: Tasks 5–8 artifacts.
- Produces: an NSIS/Inno installer that installs the service (auto-start), writes the
  Run-key entry for the tray app, and lays out the app/data roots per Global Constraints.

- [ ] **Step 1: Write the install scripts**

`installer/install-service.bat`:
```bat
@echo off
sc create EmployeeTrackerService binPath= "\"%CD%\service\service_main.exe\"" start= auto DisplayName= "Employee Tracker Service"
sc description EmployeeTrackerService "Captures entry/exit crossings and records attendance."
sc failure EmployeeTrackerService reset= 30 actions= restart/5000/restart/10000/restart/30000
net start EmployeeTrackerService
```

`installer/register-autostart.ps1`:
```powershell
$path = "$env:ProgramFiles\EmployeeTracker\tray\tray_app.exe"
New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
  -Name "EmployeeTrackerTray" -Value $path -PropertyType String -Force
```

- [ ] **Step 2: Write the Inno Setup script**

`installer/employee-tracker.iss`:
```ini
[Setup]
AppName=Employee Tracker
AppVersion=0.1.0
DefaultDirName={autopf}\EmployeeTracker
DefaultGroupName=Employee Tracker
OutputBaseFilename=EmployeeTrackerSetup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin

[Files]
Source: "dist\service\*"; DestDir: "{app}\service"; Flags: recursesubdirs
Source: "dist\tray\*"; DestDir: "{app}\tray"; Flags: recursesubdirs
Source: "dist\ui\*"; DestDir: "{app}\ui"; Flags: recursesubdirs

[Dirs]
Name: "{commonappdata}\EmployeeTracker\clips"
Name: "{commonappdata}\EmployeeTracker\data"
Name: "{commonappdata}\EmployeeTracker\logs"
Name: "{commonappdata}\EmployeeTracker\models"
Name: "{commonappdata}\EmployeeTracker\config"

[Run]
Filename: "{app}\installer\install-service.bat"; Flags: runhidden
Filename: "powershell"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\installer\register-autostart.ps1"""

[UninstallRun]
Filename: "sc"; Parameters: "stop EmployeeTrackerService"; Flags: runhidden
Filename: "sc"; Parameters: "delete EmployeeTrackerService"; Flags: runhidden
```

- [ ] **Step 3: Add build steps to `README.md`**

```markdown
# Building

## Service and tray (PyInstaller)
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --onefile --name service_main service/service_main.py
python -m PyInstaller --onefile --name tray_app tray/tray_app.py

## UI
cd ui && npm install && npm run dist

## Installer
ISCC installer\employee-tracker.iss
```

- [ ] **Step 4: Verify the installer registers the service**

Run `ISCC installer\employee-tracker.iss` as administrator, then:

```bash
sc qc EmployeeTrackerService
sc query EmployeeTrackerService
```
Expected: `START_TYPE` is `2 AUTO_START` and the service `STATE` is `RUNNING`.
Reboot and re-run the query: the service is still RUNNING.

- [ ] **Step 5: Commit**

```bash
git add installer/ README.md
git commit -m "feat: Inno installer registering auto-start service and tray autostart"
```

---

## Task 10: Full-suite verification

**Files:**
- Modify: none (verification gate).

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Run the whole test suite**

```bash
python -m pytest -v
```
Expected: every test green across `test_config`, `test_db`, `test_api`, `test_watchdog`, `test_logging_setup`, `test_record_simulator`.

- [ ] **Step 2: Confirm no leaks under a sustained run**

Start the service, run the simulator in a loop for 10 minutes, then:

```bash
curl http://127.0.0.1:8080/health/rss
```
Expected: RSS stays well under the 1500 MB ceiling and threads stay bounded
(watchdog fires no restart).

- [ ] **Step 3: Confirm boot autostart and clean shutdown**

Reboot the machine, then `sc query EmployeeTrackerService` shows RUNNING without any
user logged in. Run `python service/service_main.py stop`, confirm the process exits
with no orphaned worker threads (Task 4's `stop()` joined flag).

- [ ] **Step 4: Commit the verification note**

```bash
git add -A && git commit -m "chore: phase 1 verified — skeleton runs at boot, UI appends, no leaks"
```
