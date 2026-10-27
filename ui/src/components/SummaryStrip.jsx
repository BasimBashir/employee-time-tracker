import React from 'react';
import { formatDuration } from '../lib.js';

function Stat({ value, label, sub, secondsOrder }) {
  return (
    <div className="panel flex flex-col gap-1 px-4 py-3">
      <span className="num text-display-md text-paper">{value}</span>
      <span className="text-label font-medium uppercase tracking-widest text-paper-faint">
        {label}
      </span>
      {sub && <span className="text-sm text-paper-soft">{sub}</span>}
    </div>
  );
}

export function SummaryStrip({ sessions, events }) {
  // Only returned trips have a real duration; open ones are excluded from totals
  // so a running timer doesn't inflate the day's numbers.
  const closed = sessions.filter((s) => s.in_at !== null && s.inferred_return !== 1);
  const minutes = closed.reduce((sum, s) => sum + (s.duration_minutes ?? 0), 0);
  const trips = closed.length;
  const outToday = events.filter((e) => e.event_type === 'OUT').length;
  const inferred = sessions.filter((s) => s.inferred_return === 1).length;

  return (
    <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Stat value={formatDuration(minutes)} label="Total time out" sub="Closed trips only" />
      <Stat value={String(trips)} label="Completed trips" sub="Out and back" />
      <Stat value={String(outToday)} label="Departures seen" sub="Includes still-out" />
      <Stat
        value={String(inferred)}
        label="Missing returns"
        sub={inferred ? 'Closed at midnight' : 'None today'}
      />
    </section>
  );
}

export default SummaryStrip;
