import React from 'react';

const fmtMins = (m) =>
  m === null || m === undefined
    ? '—'
    : m < 1
      ? `${Math.round(m * 60)}s`
      : `${Math.round(m)}m`;

function ActionButton({ label, tone = 'ink', onClick }) {
  const toneClass = {
    ink: 'border-ink-500/60 bg-ink-800 text-paper hover:bg-ink-700',
    danger: 'border-rose/40 bg-rose/10 text-rose hover:bg-rose/20',
  }[tone];
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md border px-2.5 py-1 text-label font-medium tracking-normal transition-colors ${toneClass}`}
    >
      {label}
    </button>
  );
}

export function SessionRow({ session, onDelete }) {
  const inferred = session.inferred_return === 1;
  return (
    <tr className="group border-b border-ink-500/40">
      <td className="py-2 pl-3 pr-2">
        <span className="font-medium">#{session.employee_id}</span>
        {session.employee_name && (
          <span className="ml-2 text-paper-soft">{session.employee_name}</span>
        )}
      </td>
      <td className="num px-2 text-paper-soft">
        {session.out_at ? new Date(session.out_at).toLocaleTimeString('en-GB') : '—'}
      </td>
      <td className="num px-2 text-paper-soft">
        {session.in_at ? new Date(session.in_at).toLocaleTimeString('en-GB') : '—'}
      </td>
      <td className="num px-2">{fmtMins(session.duration_minutes)}</td>
      <td className="px-2">
        {inferred ? (
          <span className="text-amber" title="No return detected; closed at midnight">
            —
          </span>
        ) : (
          <span className="text-paper-faint">returned</span>
        )}
      </td>
      <td className="py-2 pl-2 pr-3 text-right">
        <ActionButton label="Delete" tone="danger" onClick={onDelete} />
      </td>
    </tr>
  );
}

export default SessionRow;
