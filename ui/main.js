import { app, BrowserWindow } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Low-end target: skip occlusion calculation and GPU process churn.
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');
app.disableHardwareAcceleration();

let mainWindow = null;

// The renderer is built to dist/renderer by `npm run build`. In dev, Vite serves it
// on 5173 instead — main.js can only load files, so dev runs against the last build.
const RENDERER_INDEX = path.join(__dirname, 'dist', 'renderer', 'index.html');

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 420,
    minHeight: 480,
    backgroundColor: '#0B0E13',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      backgroundThrottling: false,
      sandbox: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  // Prefer the dashboard as served by the service: same origin as the API, so
  // fetches are never CORS-bound. Fall back to the local build when the
  // service is down, so the window still opens (UI shows its "service down"
  // state and recovers once the service is back).
  try {
    await mainWindow.loadURL('http://127.0.0.1:8080/');
  } catch {
    await mainWindow.loadFile(RENDERER_INDEX);
  }
}

// One window only: multi-window is how Electron memory balloons on a 16 GB box.
app.whenReady().then(createWindow);
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
