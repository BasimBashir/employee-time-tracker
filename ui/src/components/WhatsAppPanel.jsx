import React from 'react';
import { api } from '../api.js';
import { PhoneInput } from './controls.jsx';

// WhatsApp + camera settings: the bridge link state (with the pairing QR
// while unlinked), the notification switch and owner number, and the RTSP
// source URL with a server-side "test connection" probe.
export function WhatsAppPanel() {
  const [st, setSt] = React.useState(null);
  const [url, setUrl] = React.useState('');
  const [probe, setProbe] = React.useState(null);
  const [probing, setProbing] = React.useState(false);
  const [testMsg, setTestMsg] = React.useState(null);

  const load = React.useCallback(() => {
    api.notifyStatus().then(setSt).catch(() => setSt(null));
  }, []);
  React.useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  // WhatsApp rotates the pairing QR every ~20s — re-fetch the image or the
  // user scans a dead code and gets "Couldn't link device" on the phone.
  const [qrTick, setQrTick] = React.useState(0);
  React.useEffect(() => {
    if (st?.bridge?.status !== 'qr') return;
    setQrTick((t) => t + 1);
    const id = setInterval(() => setQrTick((t) => t + 1), 15000);
    return () => clearInterval(id);
  }, [st?.bridge?.status]);

  // Adopt the saved RTSP URL into the input once; afterwards typing stays free.
  const savedSource = React.useRef(null);
  React.useEffect(() => {
    if (st?.dev_source && savedSource.current !== st.dev_source) {
      savedSource.current = st.dev_source;
      setUrl(st.dev_source);
    }
  }, [st]);

  async function patch(body) {
    try {
      const r = await api.patchNotifySettings(body);
      if (r.restart_recommended) await api.pipelineRestart();
      load();
    } catch (err) {
      window.alert(`Could not save: ${err.message}`);
    }
  }

  async function testStream() {
    setProbing(true);
    setProbe(null);
    try {
      setProbe(await api.testCamera(url));
    } catch (err) {
      setProbe({ ok: false, error: err.message });
    } finally {
      setProbing(false);
    }
  }

  async function sendTest() {
    setTestMsg(null);
    try {
      const r = await api.notifyTest();
      setTestMsg({ ok: true, text: `Sent to ${r.sent}` });
    } catch (err) {
      setTestMsg({ ok: false, text: err.message });
    }
  }

  const bridge = st?.bridge;
  const linkState = !st
    ? '…'
    : !st.enabled
      ? 'Notifications are off — turn them on to link WhatsApp.'
      : bridge?.status === 'ready'
        ? `Linked as ${bridge.user ?? 'WhatsApp'} — notifications live.`
        : bridge?.status === 'qr'
          ? 'Scan the QR below: phone WhatsApp → Settings → Linked devices.'
          : bridge?.status === 'logged_out'
            ? 'Logged out from the phone — remove the session folder to re-pair.'
            : bridge
              ? 'Linking… (if this stays, check the bridge log)'
              : 'Bridge not reachable — is it running?';

  const btn =
    'rounded-md border border-ink-500/60 bg-ink-800 px-2.5 py-1 text-label ' +
    'text-paper transition-colors hover:bg-ink-700 disabled:opacity-50';

  return (
    <section className="panel p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-label font-medium uppercase tracking-widest text-paper-faint">
          WhatsApp &amp; camera
        </h2>
        <span className="text-[10px] text-paper-faint/70">
          polls at each namaz slot · weekly summary Mondays 15:00
        </span>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <label className="flex items-center gap-2 text-sm text-paper">
              <input
                type="checkbox"
                checked={!!st?.enabled}
                onChange={(e) => patch({ notify_enabled: e.target.checked })}
                className="accent-jade"
              />
              Notifications on
            </label>
            <PhoneInput
              key={st?.owner_phone ?? 'none'}
              phone={st?.owner_phone ?? ''}
              onCommit={(v) => patch({ notify_owner_phone: v })}
            />
            <button type="button" onClick={sendTest} className={btn}>
              Send test
            </button>
          </div>
          <p className="text-xs text-paper-soft">{linkState}</p>
          {st?.enabled && bridge?.status === 'qr' && (
            <img
              src={`${api.notifyQrUrl()}?t=${qrTick}`}
              alt="WhatsApp pairing QR"
              className="w-48 rounded-md border border-ink-500/60 bg-white p-1"
            />
          )}
          {testMsg && (
            <p className={`text-xs ${testMsg.ok ? 'text-jade' : 'text-rose'}`}>
              {testMsg.ok ? '✅ ' : '⚠ '}
              {testMsg.text}
            </p>
          )}
          <p className="text-[10px] text-paper-faint/70">
            Employees with a WhatsApp number get a YES/NO poll when their namaz
            slot starts; a reply with a reason lands on their open trip in
            Today&rsquo;s records. The owner number gets off-window alerts.
          </p>
        </div>

        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="rtsp://user:pass@camera-ip:554/Streaming/Channels/101"
              className="num w-full max-w-md rounded-md border border-ink-500/60 bg-ink-800 px-2 py-1 text-sm text-paper placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={testStream} disabled={probing || !url} className={btn}>
              {probing ? 'Testing…' : 'Test connection'}
            </button>
            <button
              type="button"
              onClick={() => patch({ dev_source: url || null })}
              disabled={!url}
              className={btn}
            >
              Save &amp; apply
            </button>
          </div>
          {probe && (
            <p className={`text-xs ${probe.ok ? 'text-jade' : 'text-rose'}`}>
              {probe.ok ? `✅ Stream is live — ${probe.width}×${probe.height}` : `⚠ ${probe.error}`}
            </p>
          )}
          <p className="text-[10px] text-paper-faint/70">
            HiKvision example: main stream …/Streaming/Channels/101, sub stream
            …/102. &ldquo;Save &amp; apply&rdquo; restarts the analysis pipeline.
          </p>
        </div>
      </div>
    </section>
  );
}

export default WhatsAppPanel;
