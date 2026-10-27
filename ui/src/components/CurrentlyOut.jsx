import React from 'react';
import { formatDuration, useElapsedMinutes } from '../lib.js';

/**
 * "Currently out" is the state a shop owner actually stares at: who is off the
 * floor right now and for how long. A row here has an open OUT with no matching IN,
 * so its timer is running.
 */
function OutRow({ session }) {
  const minutes = useElapsedMinutes(session.out_at, true);
  return (
    <li className="gate-rule flex items-baseline gap-3 py-2.5 pl-4">
      <span className="font-medium">#{session.employee_id}</span>
      {session.employee_name && (
        <span className="text-sm text-paper-soft">{session.employee_name}</span>
      )}
      <time className="num ml-auto text-rose tabular">
        {formatDuration(minutes)}
      </time>
    </li>
  );
}

export function CurrentlyOut({ sessions }) {
  const out = sessions.filter((s) => s.in_at === null && s.inferred_return !== 1);

  return (
    <section className="panel">
      <div className="flex items-center gap-2 border-b border-ink-500/60 px-4 py-3">
        <h2 className="text-label font-medium uppercase tracking-widest text-paper-faint">
          Currently out
        </h2>
        <span className="num ml-auto text-sm text-paper-soft">{out.length}</span>
      </div>
      {out.length === 0 ? (
        <p className="px-4 py-8 text-center text-sm text-paper-faint">
          Everyone is on the floor.
        </p>
      ) : (
        <ul className="px-4">
          {out.map((s) => <OutRow key={s.id} session={s} />)}
        </ul>
      )}
    </section>
  );
}

export default CurrentlyOut;
