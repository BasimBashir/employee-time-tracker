import React from 'react';
import Header from './Header.jsx';
import SummaryStrip from './SummaryStrip.jsx';
import CurrentlyOut from './CurrentlyOut.jsx';
import LiveFeed from './LiveFeed.jsx';
import SessionsTable from './SessionsTable.jsx';
import { api } from '../api.js';

export function App() {
  const [status, setStatus] = React.useState(null);
  const [sessions, setSessions] = React.useState([]);
  const [events, setEvents] = React.useState([]);
  const [date, setDate] = React.useState(() => api.today());

  React.useEffect(() => {
    api.health().then(() => setStatus('up')).catch(() => setStatus('down'));
  }, []);

  React.useEffect(() => {
    api.sessions(date).then(setSessions).catch(() => {});
  }, [date]);

  React.useEffect(() => {
    let since = new Date(0).toISOString();
    const seen = new Set();
    const poll = () => {
      api.events(since)
        .then((rows) => {
          // The API filters with occurred_at >= since, so the newest row can
          // re-arrive on the next poll — dedupe by primary key, then advance
          // the cursor. seen also makes the feed safe if timestamps tie.
          const fresh = rows.filter((r) => !seen.has(r.id));
          if (fresh.length) {
            fresh.forEach((r) => seen.add(r.id));
            setEvents((prev) => [...fresh].reverse().concat(prev).slice(0, 300));
            since = rows[rows.length - 1].occurred_at;
          }
          setStatus('up');
        })
        .catch(() => setStatus('down'));
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, []);

  function reload() {
    api.sessions(date).then(setSessions).catch(() => {});
  }

  return (
    <div className="flex h-full flex-col">
      <Header status={status} />
      <main className="flex flex-col gap-5 overflow-y-auto p-5">
        <SummaryStrip sessions={sessions} events={events} />
        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
          <LiveFeed events={events} />
          <CurrentlyOut sessions={sessions} />
        </div>
        <SessionsTable sessions={sessions} onDeleted={reload} />
      </main>
    </div>
  );
}

export default App;
