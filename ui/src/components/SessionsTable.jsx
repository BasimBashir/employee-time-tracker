import React from 'react';
import { api } from '../api.js';
import { formatClock, formatDuration } from '../lib.js';
import SessionRow from './SessionRow.jsx';

// Evidence clip viewer. A clip may legitimately still be encoding when the
// row appears (capture returns before the file is finished), so we probe and
// retry every 2 s until the service reports it playable.
function ClipPlayer({ path, onClose }) {
  const [info, setInfo] = React.useState(null);

  React.useEffect(() => {
    let alive = true;
    let timer;
    const check = () => {
      fetch(api.clipProbeUrl(path))
        .then((r) => r.json())
        .then((i) => {
          if (!alive) return;
          setInfo(i);
          if (!i.ready) timer = setTimeout(check, 2000);
        })
        .catch(() => {
          if (alive) timer = setTimeout(check, 2000);
        });
    };
    check();
    return () => { alive = false; clearTimeout(timer); };
  }, [path]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/80 p-6"
      onClick={onClose}
    >
      <div
        className="panel w-full max-w-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-ink-500/60 px-4 py-3">
          <h3 className="text-label font-medium uppercase tracking-widest text-paper-faint">
            Evidence clip
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-paper-faint transition-colors hover:text-paper"
            aria-label="Close clip player"
          >
            ✕
          </button>
        </div>
        <div className="p-4">
          {info?.ready ? (
            <video key={path} src={api.clipDownloadUrl(path)} controls className="w-full" />
          ) : (
            <div className="flex h-48 items-center justify-center text-sm text-paper-faint">
              {info && !info.exists
                ? 'This clip is gone — it may have been purged by retention.'
                : 'Clip still being written or unavailable — retrying…'}
            </div>
          )}
          {info?.ready && (
            <p className="mt-2 text-xs text-paper-faint">
              {info.frames} frames, ready to play.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export function SessionsTable({ sessions, onChanged }) {
  const [clipPath, setClipPath] = React.useState(null);

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
              <th className="px-2 text-left font-medium">Clip</th>
              <th className="py-2 pl-2 pr-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {sessions.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-sm text-paper-faint">
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
                  onPlayClip={() => setClipPath(s.out_clip)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
      {clipPath && <ClipPlayer path={clipPath} onClose={() => setClipPath(null)} />}
      <div className="border-t border-ink-500/60 px-4 py-2 text-xs text-paper-faint">
        {formatClock(null) && `${sessions.length} record${sessions.length === 1 ? '' : 's'}`}
        {' — deleting keeps the row in the audit log rather than dropping it; '}
        {'delivery trips are work time and exempt from the out allowance.'}
      </div>
    </section>
  );
}

export default SessionsTable;
