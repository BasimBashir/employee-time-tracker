import React from 'react';
import { api } from '../api';

const LIVE_SLUG = 'TVL-LIVE-01';

// WhatsApp link state, polled every 10s. Hidden entirely while notifications
// are disabled. The label always names the state — color alone never carries
// it — and "scan QR" opens the pairing image the employer points a phone at.
function WhatsAppChip() {
  const [st, setSt] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    const poll = () =>
      api
        .notifyStatus()
        .then((s) => alive && setSt(s))
        .catch(() => alive && setSt({ enabled: false }));
    poll();
    const id = setInterval(poll, 10000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
  if (!st || !st.enabled) return null;

  const s = st.bridge?.status;
  const state =
    s === 'ready'
      ? { dot: 'bg-jade', label: 'WhatsApp linked' }
      : s === 'qr'
        ? { dot: 'bg-amber', label: 'WhatsApp: scan QR', href: api.notifyQrUrl() }
        : s === 'logged_out'
          ? { dot: 'bg-rose', label: 'WhatsApp logged out' }
          : s === 'starting' || s === 'connecting' || s === 'reconnecting'
            ? { dot: 'bg-amber', label: 'WhatsApp linking…' }
            : { dot: 'bg-amber', label: 'WhatsApp down' };
  const inner = (
    <>
      <span
        className={`inline-block h-2 w-2 rounded-full ${state.dot}`}
        aria-hidden="true"
      />
      {state.label}
    </>
  );
  return state.href ? (
    <a
      href={state.href}
      target="_blank"
      rel="noreferrer"
      title="Open the pairing QR to link WhatsApp"
      className="flex items-center gap-2 text-sm text-paper-soft hover:text-paper"
    >
      {inner}
    </a>
  ) : (
    <span className="flex items-center gap-2 text-sm text-paper-soft">{inner}</span>
  );
}

export function Header({ status }) {
  const up = status === 'up';
  return (
    <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-ink-500/60 bg-ink-800 px-5 py-3">
      <h1 className="text-base font-semibold tracking-tight">Employee tracker</h1>
      <span className="font-mono text-sm text-paper-faint">{LIVE_SLUG}</span>
      <span className="ml-auto flex items-center gap-4">
        <WhatsAppChip />
        <span className="flex items-center gap-2 text-sm text-paper-soft">
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              up ? 'bg-jade' : 'bg-rose'
            }`}
            aria-hidden="true"
          />
          {up ? 'Service up' : 'Service down'}
        </span>
      </span>
    </header>
  );
}

export default Header;
