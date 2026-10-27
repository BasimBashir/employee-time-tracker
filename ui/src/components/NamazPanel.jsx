import React from 'react';
import { api } from '../api.js';

// The Settings page: the editable namaz timetable for Faisalabad. Zuhr is a
// fixed 13:30 jamaat year-round (editable, with a "follow the sun" fallback);
// the other four are computed and tuned via jamaat offsets. Every cell can be
// pinned per date; the times here are what every employee's namaz out-slots
// open from.
const PRAYERS = ['fajr', 'zuhr', 'asr', 'maghrib', 'isha'];
const LABELS = { fajr: 'Fajr', zuhr: 'Zuhr', asr: 'Asr', maghrib: 'Maghrib', isha: 'Isha' };
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December'];

function Cell({ day, prayer, editing, onEdit, onCommit }) {
  const isEditing = editing && editing.date === day.date && editing.prayer === prayer;
  const overridden = day.overridden.includes(prayer);
  if (isEditing) {
    return (
      <td className="px-1 py-0.5">
        <input
          autoFocus
          value={editing.text}
          placeholder={day[prayer]}
          onChange={(e) => onEdit({ ...editing, text: e.target.value })}
          onBlur={() => onCommit(day, prayer, editing.text)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.target.blur();
            if (e.key === 'Escape') onEdit(null);
          }}
          className="num w-14 rounded border border-paper-faint bg-ink-800 px-1 py-0.5 text-center text-xs text-paper placeholder:text-paper-faint/50 focus:outline-none"
        />
      </td>
    );
  }
  return (
    <td className="px-1 py-0.5">
      <button
        type="button"
        title={`Edit ${LABELS[prayer]} for ${day.date}${overridden ? ' — pinned, clear the field to follow the timetable' : ''}`}
        onClick={() => onEdit({ date: day.date, prayer, text: day[prayer] })}
        className={`num w-14 rounded px-1 py-0.5 text-center text-xs transition-colors ${
          overridden
            ? 'bg-ink-600 text-paper ring-1 ring-amber/50 hover:bg-ink-500'
            : 'text-paper-soft hover:bg-ink-700 hover:text-paper'
        }`}
      >
        {day[prayer]}
      </button>
    </td>
  );
}

export function NamazPanel({ date }) {
  const [cursor, setCursor] = React.useState(() => {
    const [year, month] = date.split('-').map(Number);
    return { year, month };
  });
  const [data, setData] = React.useState(null);
  const [editing, setEditing] = React.useState(null);
  const load = React.useCallback(() => {
    api.namaz(cursor.year, cursor.month).then(setData).catch(() => setData(null));
  }, [cursor]);
  React.useEffect(load, [load]);

  const shift = (delta) =>
    setCursor(({ year, month }) => {
      const m = month + delta;
      return m < 1 ? { year: year - 1, month: 12 } : m > 12 ? { year: year + 1, month: 1 } : { year, month: m };
    });

  async function commitCell(day, prayer, text) {
    setEditing(null);
    const t = text.trim();
    if (t === day[prayer]) return;
    try {
      await api.patchNamaz(day.date, prayer, t === '' ? null : t);
      load();
    } catch (err) {
      window.alert(`Could not set ${LABELS[prayer]}: ${err.message}`);
    }
  }

  async function commitSetting(body) {
    try {
      await api.patchNamazSettings(body);
      load();
    } catch (err) {
      window.alert(`Could not save the setting: ${err.message}`);
    }
  }

  const settings = data?.settings;
  return (
    <section className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-500/60 px-4 py-3">
        <h2 className="text-label font-medium uppercase tracking-widest text-paper-faint">
          Settings — namaz timetable
        </h2>
        <span className="text-xs text-paper-faint">
          Faisalabad · these times open every employee&rsquo;s namaz out-slots
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-ink-500/60 px-4 py-2.5">
        <span className="text-label uppercase tracking-widest text-paper-faint/70">
          Jamaat offset
        </span>
        {PRAYERS.map((p) => (
          <label key={p} className="flex items-center gap-1 text-xs text-paper-faint">
            {LABELS[p]}
            <input
              type="number" min="0" max="180"
              defaultValue={settings ? String(settings.offsets[p] ?? 0) : ''}
              key={`offset-${p}-${settings ? settings.offsets[p] : 'x'}`}
              placeholder="0"
              onBlur={(e) => {
                const n = Math.round(Number(e.target.value));
                if (e.target.value.trim() !== '' && Number.isFinite(n) && n >= 0
                    && n <= 180 && n !== (settings?.offsets[p] ?? 0)) {
                  commitSetting({ offsets: { [p]: n } });
                } else {
                  e.target.value = String(settings?.offsets[p] ?? 0);
                }
              }}
              onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
              className="num w-12 rounded-md border border-ink-500/60 bg-ink-800 px-1 py-0.5 text-right text-xs text-paper placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none"
            />
          </label>
        ))}
        <label className="flex items-center gap-1 text-xs text-paper-faint">
          Slot
          <input
            type="number" min="5" max="120"
            key={`slot-${settings ? settings.duration_min : 'x'}`}
            defaultValue={settings ? String(settings.duration_min) : ''}
            placeholder="20"
            onBlur={(e) => {
              const n = Math.round(Number(e.target.value));
              if (e.target.value.trim() !== '' && Number.isFinite(n) && n >= 5
                  && n <= 120 && n !== settings?.duration_min) {
                commitSetting({ duration_min: n });
              } else {
                e.target.value = String(settings?.duration_min ?? 20);
              }
            }}
            onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
            className="num w-12 rounded-md border border-ink-500/60 bg-ink-800 px-1 py-0.5 text-right text-xs text-paper placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none"
          />
          min
        </label>
        <label className="flex items-center gap-1 text-xs text-paper-faint">
          Zuhr
          <input
            type="text"
            key={`zuhr-${settings ? settings.zuhr : 'x'}`}
            defaultValue={settings ? settings.zuhr ?? '' : ''}
            placeholder="13:30"
            title="Fixed Zuhr jamaat time year-round — clear to follow the computed midday"
            onBlur={(e) => {
              const t = e.target.value.trim();
              if (t !== (settings?.zuhr ?? '')) commitSetting({ zuhr: t === '' ? null : t });
            }}
            onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
            className="num w-14 rounded-md border border-ink-500/60 bg-ink-800 px-1 py-0.5 text-xs text-paper placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none"
          />
        </label>
        <label className="flex items-center gap-1 text-xs text-paper-faint">
          Jummah (Fri)
          <input
            type="text"
            key={`jummah-${settings ? settings.jummah : 'x'}`}
            defaultValue={settings ? settings.jummah ?? '' : ''}
            placeholder="13:30"
            title="Friday jummah time — clear to follow the Zuhr setting"
            onBlur={(e) => {
              const t = e.target.value.trim();
              if (t !== (settings?.jummah ?? '')) commitSetting({ jummah: t === '' ? null : t });
            }}
            onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
            className="num w-14 rounded-md border border-ink-500/60 bg-ink-800 px-1 py-0.5 text-xs text-paper placeholder:text-paper-faint/50 focus:border-paper-faint focus:outline-none"
          />
        </label>
      </div>

      <div className="flex items-center justify-between px-4 py-2">
        <div className="flex items-center gap-2">
          <button
            type="button" onClick={() => shift(-1)}
            className="rounded-md border border-ink-500/60 bg-ink-800 px-2 py-0.5 text-xs text-paper transition-colors hover:bg-ink-700"
          >
            ‹
          </button>
          <span className="text-sm font-medium text-paper">
            {MONTHS[cursor.month - 1]} {cursor.year}
          </span>
          <button
            type="button" onClick={() => shift(1)}
            className="rounded-md border border-ink-500/60 bg-ink-800 px-2 py-0.5 text-xs text-paper transition-colors hover:bg-ink-700"
          >
            ›
          </button>
        </div>
        <span className="text-[10px] text-paper-faint/70">
          click a time to edit it — clear the field to follow the timetable again
        </span>
      </div>

      <div className="max-h-[32rem] overflow-y-auto px-4 pb-4">
        <table className="w-full border-collapse text-left">
          <thead className="sticky top-0 bg-ink-800">
            <tr className="text-[9px] uppercase tracking-widest text-paper-faint/70">
              <th className="py-1 pl-1 pr-2 font-medium">Date</th>
              {PRAYERS.map((p) => (
                <th key={p} className="px-1 py-1 text-center font-medium">{LABELS[p]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(data?.days ?? []).map((day) => (
              <tr key={day.date} className="border-t border-ink-500/30">
                <td className="py-0.5 pl-1 pr-2">
                  <span className="num text-xs text-paper-soft">
                    {Number(day.date.slice(8, 10))}
                  </span>
                  <span className="ml-1.5 text-[9px] uppercase tracking-wider text-paper-faint/70">
                    {new Date(`${day.date}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short' })}
                  </span>
                  {day.jummah && (
                    <span className="ml-1.5 rounded border border-azure/40 bg-azure/10 px-1 text-[9px] tracking-normal text-azure">
                      jummah
                    </span>
                  )}
                </td>
                {PRAYERS.map((p) => (
                  <Cell
                    key={p} day={day} prayer={p}
                    editing={editing} onEdit={setEditing} onCommit={commitCell}
                  />
                ))}
              </tr>
            ))}
            {!data && (
              <tr>
                <td colSpan={6} className="py-6 text-center text-sm text-paper-faint">
                  Loading timetable…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default NamazPanel;
