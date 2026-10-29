// The dashboard is a thin client: it fetches and renders, it never computes
// durations or aggregates. The service owns the data.

// In dev, Vite serves the UI on 5173 while the API stays on 8080. In
// production the service itself serves this bundle from 8080, so the API is
// same-origin and the base is empty — no CORS, and the dashboard works on
// whatever host the service is reached through.
const API_BASE = import.meta.env.DEV ? 'http://127.0.0.1:8080' : '';

// The local password gate: mutating endpoints demand X-Tracker-Password once
// the operator has set one. Held in memory + sessionStorage for this session
// only — never persisted anywhere else.
let pw = sessionStorage.getItem('pw') || '';

async function get(path) {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// One mutating path for every verb: attaches the password, and on a 401 asks
// for it once and retries before surfacing the failure.
async function send(method, path, body, retry = true) {
  const headers = { 'Content-Type': 'application/json' };
  if (pw) headers['X-Tracker-Password'] = pw;
  const r = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && retry) {
    pw = window.prompt('This action needs the local password:') || '';
    sessionStorage.setItem('pw', pw);
    return send(method, path, body, false);
  }
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail || `${r.status} ${r.statusText}`);
  }
  return r.json();
}

const del = (path, body) => send('DELETE', path, body);
const patch = (path, body) => send('PATCH', path, body);
const post = (path, body) => send('POST', path, body);

// Uploads stream the raw file with its own content type — the service stores
// media as plain files, so there is no JSON envelope.
async function upload(url, file) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  health: () => get('/health'),
  rss: () => get('/health/rss'),

  // `since` is an ISO timestamp; the service returns events at or after it.
  events: (since) => get(`/events?since=${encodeURIComponent(since)}`),

  sessions: (date) => get(`/sessions?date=${date}`),

  // Soft delete: the row survives with a reason, recorded in the audit log.
  deleteSession: (id, actor, reason) =>
    del(`/sessions/${id}`, { actor, reason }),

  // Re-tag a trip. 'delivery' minutes are work time and exempt from the
  // daily out allowance; 'break' minutes count against it.
  setSessionPurpose: (id, purpose, actor = 'owner') =>
    patch(`/sessions/${id}`, { purpose, actor }),

  // role: 'staff' | 'delivery'; allowed_out_minutes: number or null (inherit).
  updateEmployee: (id, body) => patch(`/employees/${id}`, body),

  // Roster with full profiles (role, phone, out windows, namaz plan,
  // avatar/media flags). With `date`, each profile also carries
  // effective_windows — their windows plus that day's namaz slots.
  employees: (date) => get(`/employees${date ? `?date=${encodeURIComponent(date)}` : ''}`),
  addEmployee: (body) => post('/employees', body),
  // Soft delete: the reason lands in the audit log; history stays resolvable.
  removeEmployee: (id, reason) => del(`/employees/${id}`, { actor: 'owner', reason }),

  // Phase 4 identity: enroll from a video file the service can read. The
  // service's error detail (e.g. "only 3/10 poses captured") comes through.
  enroll: (employeeId, path) =>
    post('/enrollments', { employee_id: employeeId, path, source: 'file' }),
  // {ready, roster_size, confirm, uncertain} — the identity chip's data.
  resolverStatus: () => get('/resolver/status'),

  // Editable namaz timetable: a month (or whole year) of effective prayer
  // start times plus the global settings. `time` null clears an override.
  namaz: (year, month) => get(`/namaz?year=${year}${month ? `&month=${month}` : ''}`),
  patchNamaz: (date, prayer, time) => patch('/namaz', { date, prayer, time }),
  // offsets: {prayer: minutes}; zuhr/jummah: 'HH:MM' or null (follow the sun).
  patchNamazSettings: (body) => patch('/namaz/settings', body),

  // Profile photo (avatar_<id>.jpg) and per-employee training media, both
  // plain files under the service's media folder — no database rows.
  avatarUrl: (id) => `${API_BASE}/employees/${id}/avatar`,
  uploadAvatar: (id, file) => upload(`${API_BASE}/employees/${id}/avatar`, file),
  mediaList: (id) => get(`/employees/${id}/media`),
  mediaUrl: (id, name) => `${API_BASE}/employees/${id}/media/${encodeURIComponent(name)}`,
  uploadMedia: (id, file) => upload(`${API_BASE}/employees/${id}/media`, file),
  deleteMedia: (id, name) => del(`${API_BASE}/employees/${id}/media/${encodeURIComponent(name)}`),

  dailyReport: (date) => get(`/report/daily?date=${date}`),
  weeklyReport: (end, days = 7) => get(`/report/weekly?end=${end}&days=${days}`),
  reportCsvUrl: (date) => `${API_BASE}/report/daily.csv?date=${date}`,

  // Generated reports (XLSX/PDF). The service writes into its reports folder;
  // the returned path maps onto the /reports static mount for download.
  genDaily: (date, formats) => post('/reports/daily', { date, formats }),
  genMonthly: (yearMonth) => post('/reports/monthly', { year_month: yearMonth }),
  reportFileUrl: (path) => {
    const i = path.replace(/\\/g, '/').indexOf('/reports/');
    return i < 0 ? path : `${API_BASE}${path.replace(/\\/g, '/').slice(i)}`;
  },

  // Live camera: one MJPEG stream plus the normalized zone line for the
  // overlay. Camera 1 is the only camera today.
  cameraStreamUrl: () => `${API_BASE}/camera/1/stream.mjpeg`,
  cameraOverlay: () => get('/camera/1/overlay.json'),

  // Evidence clips: probe first (a clip may still be writing), then play.
  clipProbeUrl: (path) => `${API_BASE}/clips/probe?path=${encodeURIComponent(path)}`,
  clipDownloadUrl: (path) => `${API_BASE}/clips/download?path=${encodeURIComponent(path)}`,

  // WhatsApp link state for the header chip: `{enabled: false}` when
  // notifications are off, otherwise `{enabled, reachable, bridge}` — plus
  // owner_phone and dev_source for the settings panel.
  notifyStatus: () => get('/notify/status'),
  // Operator settings: notifications on/off, owner number, camera RTSP URL.
  patchNotifySettings: (body) => patch('/notify/settings', body),
  // One message to the owner's number, synchronously — proves the chain.
  notifyTest: () => post('/notify/test', {}),
  // Restart the analysis pipeline (after changing the RTSP source).
  pipelineRestart: () => post('/pipeline/restart', {}),
  // 'Test connection': open the RTSP URL and grab one frame, server-side.
  testCamera: (url) => post('/camera/test', { url }),
  // The pairing QR (PNG) is fetched by URL, not through get() — it's an image.
  notifyQrUrl: () => `${API_BASE}/notify/qr`,

  today: () => new Date().toISOString().slice(0, 10),
};
