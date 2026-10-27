// Local WhatsApp notification bridge.
//
// The Python service never talks to WhatsApp directly — this little Node
// sidecar owns the WhatsApp session (Baileys, a pure-WebSocket protocol
// client: no Chromium, no puppeteer) and exposes three localhost endpoints:
//
//   GET  /status   → { status, qr (data URL), user, lastError }
//   GET  /qr.png   → the pairing QR as a PNG (while unlinked)
//   POST /send     → { to, text }  — queued, jittered, one at a time
//
// Safety posture (unofficial automation, same ToS category as every other
// library): send from a dedicated SIM, keep volumes human (the queue paces
// messages 0.8–2.5 s apart), never bulk. Incoming replies are logged to the
// console today — the future confirmation flow hooks into messages.upsert.
import express from 'express';
import pino from 'pino';
import QRCode from 'qrcode';
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from 'baileys';
import { rmSync } from 'node:fs';

const PORT = Number(process.env.PORT || 8090);
const HOST = '127.0.0.1';
const SESSION_DIR = process.env.SESSION_DIR || './session';
const log = pino({ level: process.env.LOG_LEVEL || 'warn' });

const state = {
  status: 'starting', // starting | qr | connecting | ready | reconnecting | logged_out | error
  qr: null, // data URL while pairing
  user: null, // linked account JID once ready
  lastError: null,
  startedAt: new Date().toISOString(),
};

let sock = null;

async function connect() {
  const { state: auth, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion();
  sock = makeWASocket({ version, auth, logger: log, syncFullHistory: false });
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', (u) => {
    (async () => {
      if (u.qr) {
        state.status = 'qr';
        state.qr = await QRCode.toDataURL(u.qr);
        log.info('qr available for pairing');
      }
      if (u.connection === 'open') {
        state.status = 'ready';
        state.qr = null;
        state.user = sock.user?.id ?? null;
        state.lastError = null;
        console.log(`[bridge] linked as ${state.user}`);
      }
      if (u.connection === 'close') {
        state.user = null;
        const code = u.lastDisconnect?.error?.output?.statusCode;
        if (code === DisconnectReason.loggedOut) {
          state.status = 'logged_out';
          state.lastError = 'Session was logged out from the phone. Delete the session folder and re-scan.';
          console.error('[bridge] logged out');
        } else {
          state.status = 'reconnecting';
          setTimeout(() => connect().catch(fail), 3000);
        }
      }
    })().catch(fail);
  });
  sock.ev.on('messages.upsert', ({ messages }) => {
    // Replies land here. Notification-only today; the confirmation flow
    // ("did you really go out? yes/no") reads this stream later.
    for (const m of messages) {
      if (m.key.fromMe) continue;
      const text = m.message?.conversation
        || m.message?.extendedTextMessage?.text
        || m.message?.imageMessage?.caption;
      if (text) console.log(`[in] ${m.key.remoteJid}: ${text}`);
    }
  });
}

function fail(err) {
  state.status = 'error';
  state.lastError = String(err?.message || err);
  log.error(err);
}

// --- outbound queue: one message at a time with human-like jitter -----------
const queue = [];
let draining = false;

function toJid(phone) {
  const digits = String(phone).replace(/\D/g, '');
  return digits.length >= 7 && digits.length <= 15 ? `${digits}@s.whatsapp.net` : null;
}

async function drain() {
  if (draining) return;
  draining = true;
  while (queue.length) {
    const job = queue.shift();
    try {
      if (state.status !== 'ready') {
        const e = new Error(`bridge is ${state.status}, not linked yet`);
        e.code = 503;
        throw e;
      }
      const jid = toJid(job.to);
      if (!jid) {
        const e = new Error(`not a phone number: ${job.to}`);
        e.code = 400;
        throw e;
      }
      const res = await sock.sendMessage(jid, { text: job.text });
      job.resolve({ ok: true, id: res?.key?.id ?? null });
    } catch (err) {
      job.reject(err);
    }
    await new Promise((r) => setTimeout(r, 800 + Math.random() * 1700));
  }
  draining = false;
}

// --- HTTP surface ------------------------------------------------------------
const app = express();
app.use(express.json());
app.use((req, res, next) => {
  const key = process.env.BRIDGE_KEY;
  if (key && req.get('X-Bridge-Key') !== key) {
    return res.status(401).json({ error: 'bad bridge key' });
  }
  next();
});

app.get('/status', (req, res) => res.json(state));

app.get('/qr.png', (req, res) => {
  if (!state.qr) return res.status(404).type('text').send('no qr — check /status');
  res.type('png').send(Buffer.from(state.qr.split(',')[1], 'base64'));
});

app.post('/send', (req, res) => {
  const { to, text } = req.body ?? {};
  if (!to || !text) return res.status(400).json({ error: 'to and text are required' });
  new Promise((resolve, reject) => queue.push({ to, text, resolve, reject }))
    .then((r) => res.json(r))
    .catch((err) => res.status(err.code ?? 502).json({ error: String(err.message || err) }));
  drain();
});

app.post('/logout', async (req, res) => {
  try {
    await sock?.logout();
  } catch { /* already gone */ }
  rmSync(SESSION_DIR, { recursive: true, force: true });
  state.status = 'starting';
  state.user = null;
  connect().catch(fail);
  res.json({ ok: true });
});

app.listen(PORT, HOST, () => {
  console.log(`[bridge] listening on http://${HOST}:${PORT} — open /status to pair`);
});
connect().catch(fail);
