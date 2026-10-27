import React from 'react';
import { api } from '../api.js';
import { formatClock, formatDuration } from '../lib.js';
import SessionRow from './SessionRow.jsx';

export function SessionsTable({ sessions, onChanged }) {
  async function handleDelete(session) {
    const reason = window.prompt(
      `Delete this record?\nReason goes into the audit log:`
    );
    if (!reason) return;
    try {
      await api.deleteSession(session.id, 'owner', reason);
      onChanged?.();
    } catch (err) {
      window.alert(`Could not delete the record: ${err.message}`);
    }
  }

  async function handleTogglePurpose(session) {
    const next = session.purpose === 'delivery' ? 'break' : 'delivery';
    try {
      await api.setSessionPurpose(session.id, next);
      onChanged?.();
    } catch (err) {
      window.alert(`Could not re-tag the trip: ${err.message}`);
    }
  }

  return (
    <section className="panel overflow-hidden">
      <div className="border-b border-ink-500/60 px-4 py-3">
        <h2 className="text-label font-medium uppercase tracking-widest text-paper-faint">
          Today&rsquo;s records
        </h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-ink-500/40 text-label uppercase tracking-widest text-paper-faint">
              <th className="py-2 pl-3 pr-2 text-left font-medium">Employee</th>
              <th className="px-2 text-left font-medium">Out</th>
              <th className="px-2 text-left font-medium">Back</th>
              <th className="px-2 text-left font-medium">Duration</th>
              <th className="px-2 text-left font-medium">Type</th>
              <th className="px-2 text-left font-medium">Return</th>
              <th className="py-2 pl-2 pr-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {sessions.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-8 text-center text-sm text-paper-faint">
                  No records yet. Rows appear as employees go out and come back.
                </td>
              </tr>
            ) : (
              sessions.map((s) => (
                <SessionRow
                  key={s.id}
                  session={s}
                  onDelete={() => handleDelete(s)}
                  onTogglePurpose={() => handleTogglePurpose(s)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="border-t border-ink-500/60 px-4 py-2 text-xs text-paper-faint">
        {formatClock(null) && `${sessions.length} record${sessions.length === 1 ? '' : 's'}`}
        {' — deleting keeps the row in the audit log rather than dropping it; '}
        {'delivery trips are work time and exempt from the out allowance.'}
      </div>
    </section>
  );
}

export default SessionsTable;
