PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  employee_code TEXT UNIQUE,
  department TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  -- 'staff' | 'delivery'. Delivery staff's trips default to purpose='delivery'
  -- (work time, not break time).
  role TEXT NOT NULL DEFAULT 'staff',
  -- Per-employee daily out-of-floor budget in minutes; NULL inherits the
  -- config default (default_allowed_out_minutes).
  allowed_out_minutes INTEGER,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS enrollments (
  id INTEGER PRIMARY KEY,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  face_embedding BLOB,
  body_embedding BLOB,
  quality_score REAL,
  captured_at TEXT NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cameras (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  rtsp_url TEXT,
  decode_element TEXT,
  provider TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY,
  camera_id INTEGER NOT NULL REFERENCES cameras(id),
  line_points TEXT NOT NULL,
  direction_out TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);

-- The immutable raw log. Rows here are never deleted.
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  camera_id INTEGER NOT NULL REFERENCES cameras(id),
  event_type TEXT NOT NULL CHECK (event_type IN ('OUT','IN')),
  occurred_at TEXT NOT NULL,
  clip_path TEXT,
  confidence REAL,
  uncertain INTEGER NOT NULL DEFAULT 0,
  track_id INTEGER
);

-- The pairing of an OUT to its IN. Soft-deleted, never dropped.
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  out_event_id INTEGER NOT NULL REFERENCES events(id),
  in_event_id INTEGER REFERENCES events(id),
  out_at TEXT NOT NULL,
  in_at TEXT,
  duration_minutes REAL,
  inferred_return INTEGER NOT NULL DEFAULT 0,
  -- 'break' counts against the daily out budget; 'delivery' is work time and
  -- does not. Defaults from the employee's role, re-taggable in the dashboard.
  purpose TEXT NOT NULL DEFAULT 'break',
  date TEXT NOT NULL,
  deleted_at TEXT,
  deleted_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date, employee_id);
CREATE INDEX IF NOT EXISTS idx_events_emp ON events(employee_id, occurred_at);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- Editable namaz timetable: pins one prayer on one date to an absolute time
-- (minutes-of-day) for jummah moves, Ramadan shifts and one-offs. No row =
-- resolve from the fixed Zuhr clock time / the computed sun time plus jamaat
-- offsets (settings table keys namaz_*).
CREATE TABLE IF NOT EXISTS namaz_overrides (
  date TEXT NOT NULL,
  prayer TEXT NOT NULL CHECK (prayer IN ('fajr','zuhr','asr','maghrib','isha')),
  minutes INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (date, prayer)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  actor TEXT,
  action TEXT,
  target TEXT,
  detail_json TEXT,
  at TEXT NOT NULL
);
