import React from 'react';
import { formatClock } from '../lib.js';

const TONE = {
  OUT: { text: 'text-rose', bg: 'bg-rose-bg', dot: 'bg-rose', label: 'Out' },
  IN: { text: 'text-jade', bg: 'bg-jade-bg', dot: 'bg-jade', label: 'In' },
};

function FeedRow({ event }) {
  const tone = TONE[event.event_type] ?? TONE.IN;
  return (
    <li className="flex items-center gap-3 border-b border-ink-500/40 py-2.5 last:border-b-0">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone.dot}`} aria-hidden="true" />
      <time className="num shrink-0 text-paper-faint">{formatClock(event.occurred_at)}</time>
      <span className="font-medium">#{event.employee_id}</span>
      <span className={`text-label font-semibold uppercase ${tone.text}`}>
        {tone.label}
      </span>
      <span className="num ml-auto shrink-0 text-paper-faint">
        {Number(event.confidence).toFixed(2)}
      </span>
    </li>
  );
}

export function LiveFeed({ events }) {
  return (
    <section className="panel overflow-hidden">
      <div className="flex items-center gap-2 border-b border-ink-500/60 px-4 py-3">
        <h2 className="text-label font-medium uppercase tracking-widest text-paper-faint">
          Live events
        </h2>
        <span
          className="ml-auto h-2 w-2 rounded-full bg-jade/70 animate-pulse-ring"
          aria-hidden="true"
        />
      </div>
      {events.length === 0 ? (
        <p className="px-4 py-8 text-center text-sm text-paper-faint">
          No crossings yet. Events appear here the moment someone crosses the line.
        </p>
      ) : (
        <ul className="max-h-[26rem] overflow-y-auto px-4">
          {events.map((e) => <FeedRow key={e.id} event={e} />)}
        </ul>
      )}
    </section>
  );
}

export default LiveFeed;
