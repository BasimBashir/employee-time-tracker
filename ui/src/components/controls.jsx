import React from 'react';
import { api } from '../api.js';

// Small profile-editing controls shared by the Employees page and the
// out-time report cards. Every control commits on blur or Enter and lets the
// server validate — errors surface through the caller's alert.

export function RoleControl({ role, onChange }) {
  const btn = (r, label) => (
    <button
      key={r}
      type="button"
      onClick={() => role !== r && onChange(r)}
      className={`px-2 py-1 text-label transition-colors ${
        role === r ? 'bg-ink-600 text-paper' : 'text-paper-faint hover:bg-ink-800 hover:text-paper'
      }`}
    >
      {label}
    </button>
  );
  return (
    <div className="flex shrink-0 overflow-hidden rounded-md border border-ink-500/60">
      {btn('staff', 'Staff')}
      {btn('delivery', 'Delivery')}
    </div>
  );
}

// Empty means "inherit the config default" (placeholder shows the current
// effective value). Commits on blur or Enter.
export function AllowanceInput({ allowedMinutes, onCommit }) {
  const [value, setValue] = React.useState(allowedMinutes == null ? '' : String(allowedMinutes));
  const commit = () => {
    const t = value.trim();
    if (t === '') {
      if (allowedMinutes == null) return;
      onCommit(null);
      return;
    }
    const n = Math.round(Number(t));
    if (!Number.isFinite(n) || n < 0 || n === allowedMinutes) return;
    onCommit(n);
  };
  return (
    <label className="flex items-center gap-1.5 text-xs text-paper-faint">
      Allowance
      <input
        type="number"
        min="0"
        value={value}
        placeholder={String(allowedMinutes ?? '')}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
        className="num w-14 rounded-md border border-ink-500/60 bg-ink-800 px-1.5 py-0.5 text-right text-xs text-paper placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none"
      />
      min/day
    </label>
  );
}

// WhatsApp number for notifications; empty means none. Commits on blur or
// Enter, same as the allowance input.
export function PhoneInput({ phone, onCommit }) {
  const [value, setValue] = React.useState(phone ?? '');
  const commit = () => {
    const t = value.trim();
    if (t === (phone ?? '')) return;
    onCommit(t === '' ? null : t);
  };
  return (
    <label className="flex items-center gap-1.5 text-xs text-paper-faint">
      WhatsApp
      <input
        type="tel"
        value={value}
        placeholder="not set"
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
        className="num w-28 rounded-md border border-ink-500/60 bg-ink-800 px-1.5 py-0.5 text-xs text-paper placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none"
      />
    </label>
  );
}

// Generic commit-on-blur/Enter text field (name, code, department, out
// window). Resyncs when the stored value changes under it, so a reload from
// another card's edit can't leave a stale string in the box.
export function TextField({
  label, value, placeholder, onCommit, width = 'w-28',
  inputClassName = 'text-xs text-paper',
}) {
  const [text, setText] = React.useState(value ?? '');
  React.useEffect(() => setText(value ?? ''), [value]);
  const commit = () => {
    if (text.trim() === (value ?? '')) return;
    onCommit(text.trim());
  };
  return (
    <label className="flex items-center gap-1.5 text-xs text-paper-faint">
      {label}
      <input
        type="text"
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
        className={`rounded-md border border-ink-500/60 bg-ink-800 px-1.5 py-0.5 placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none ${width} ${inputClassName}`}
      />
    </label>
  );
}

// Which prayers this employee goes out for — toggles in prayer order; each
// click PATCHes the plan, emptying the set clears it (no namaz windows).
export function PrayerToggles({ prayers, onChange }) {
  const active = new Set((prayers ?? '').split(',').filter(Boolean));
  return (
    <div className="flex flex-wrap overflow-hidden rounded-md border border-ink-500/60">
      {Object.entries({ fajr: 'Fajr', zuhr: 'Zuhr', asr: 'Asr', maghrib: 'Maghrib', isha: 'Isha' }).map(
        ([prayer, label]) => (
          <button
            key={prayer}
            type="button"
            title={`${label} out-slot ${active.has(prayer) ? 'on' : 'off'}`}
            onClick={() => {
              const next = Object.keys({ fajr: 0, zuhr: 0, asr: 0, maghrib: 0, isha: 0 })
                .filter((p) => (p === prayer ? !active.has(p) : active.has(p)));
              onChange(next.length ? next.join(',') : null);
            }}
            className={`px-2 py-1 text-label transition-colors ${
              active.has(prayer)
                ? 'bg-ink-600 text-paper'
                : 'text-paper-faint hover:bg-ink-800 hover:text-paper'
            }`}
          >
            {label}
          </button>
        ),
      )}
    </div>
  );
}

// Small whole-number input (stagger offset, slot length). Empty commits null
// (= inherit the global default); out-of-range values are ignored, the server
// re-validates anyway.
export function NumberInput({ label, value, placeholder = '', onCommit, min, max, suffix }) {
  const [text, setText] = React.useState(value == null ? '' : String(value));
  React.useEffect(() => setText(value == null ? '' : String(value)), [value]);
  const commit = () => {
    const t = text.trim();
    if (t === '') {
      if (value == null) return;
      onCommit(null);
      return;
    }
    const n = Math.round(Number(t));
    if (!Number.isFinite(n) || n === value) return;
    if ((min != null && n < min) || (max != null && n > max)) return;
    onCommit(n);
  };
  return (
    <label className="flex items-center gap-1.5 text-xs text-paper-faint">
      {label}
      <input
        type="number"
        min={min}
        max={max}
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
        className="num w-14 rounded-md border border-ink-500/60 bg-ink-800 px-1.5 py-0.5 text-right text-xs text-paper placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none"
      />
      {suffix}
    </label>
  );
}

// Round profile photo with a hover Edit button; uploads straight to the
// service and bumps a cache-busting version so the new image shows at once.
export function EmployeePhoto({ id, hasAvatar, onChanged }) {
  const [version, setVersion] = React.useState(0);
  const inputRef = React.useRef(null);
  async function handleFile(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      await api.uploadAvatar(id, file);
      setVersion((v) => v + 1);
      onChanged?.();
    } catch (err) {
      window.alert(`Could not upload the photo: ${err.message}`);
    }
  }
  const url = hasAvatar ? `${api.avatarUrl(id)}?v=${version}` : null;
  return (
    <div className="relative shrink-0">
      {url ? (
        <img
          src={url}
          alt=""
          className="h-16 w-16 rounded-full border border-ink-500/60 object-cover"
        />
      ) : (
        <div className="flex h-16 w-16 items-center justify-center rounded-full border border-ink-500/60 bg-ink-800 text-lg text-paper-faint">
          ?
        </div>
      )}
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="absolute -bottom-1 -right-1 rounded-full border border-ink-500/60 bg-ink-700 px-1.5 text-[9px] uppercase tracking-wider text-paper-soft transition-colors hover:text-paper"
      >
        Edit
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        className="hidden"
        onChange={handleFile}
      />
    </div>
  );
}
