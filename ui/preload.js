const { contextBridge } = require('electron');

// The UI is a thin client: it never touches SQLite, only this API.
contextBridge.exposeInMainWorld('tracker', {
  apiBase: 'http://127.0.0.1:8080',
  fetchEvents: (since) => fetch(`http://127.0.0.1:8080/events?since=${encodeURIComponent(since)}`)
    .then(r => r.json()),
  fetchSessions: (date) => fetch(`http://127.0.0.1:8080/sessions?date=${date}`)
    .then(r => r.json()),
  deleteSession: (id, actor, reason) => fetch(`http://127.0.0.1:8080/sessions/${id}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ actor, reason })
  }).then(r => r.json())
});
