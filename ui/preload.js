import { contextBridge } from 'electron';

// The UI is a thin client: it never touches SQLite, only this API.
// The port must match service/config.py's default.
contextBridge.exposeInMainWorld('tracker', {
  apiBase: 'http://127.0.0.1:8080',
});
