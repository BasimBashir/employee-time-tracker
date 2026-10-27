import React from 'react';

const LIVE_SLUG = 'TVL-LIVE-01';

export function Header({ status }) {
  const up = status === 'up';
  return (
    <header className="flex items-center gap-3 border-b border-ink-500/60 bg-ink-800 px-5 py-3">
      <h1 className="text-base font-semibold tracking-tight">Employee tracker</h1>
      <span className="font-mono text-sm text-paper-faint">{LIVE_SLUG}</span>
      <span className="ml-auto flex items-center gap-2 text-sm text-paper-soft">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            up ? 'bg-jade' : 'bg-rose'
          }`}
          aria-hidden="true"
        />
        {up ? 'Service up' : 'Service down'}
      </span>
    </header>
  );
}

export default Header;
