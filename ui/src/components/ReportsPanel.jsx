import React from 'react';
import { api } from '../api.js';
import { formatDuration } from '../lib.js';

// Short human label for badges and annotations ("12m", "1h 5m"). The main
// figures use formatDuration (H:MM:SS); these stay scannable at small sizes.
const mins = (m) => {
  const v = Math.round(m);
  if (v >= 60) {
    const h = Math.floor(v / 60);
    return (v % 60 ? `${h}h ${v % 60}m` : `${h}h`);
  }
  return `${v}m`;
};

// Budget bar: break + delivery minutes against the employee's daily
// allowance. The tick marks the allowance; the bar scale is the larger of
// the day's total and the allowance, so "under" and "over" both read.
function DailyBar({ breakMin, deliveryMin, allowed }) {
  const scale = Math.max(breakMin + deliveryMin, allowed, 1);
  const pct = (m) => `${(m / scale) * 100}%`;
  const tickLeft = Math.min(Math.max((allowed / scale) * 100, 6), 94);
  return (
    <div>
      <div className="relative h-2.5">
        <div className="absolute inset-0 flex overflow-hidden rounded-full bg-ink-600">
          {breakMin > 0 && <div className="h-full bg-rose" style={{ width: pct(breakMin) }} />}
          {breakMin > 0 && deliveryMin > 0 && <div className="w-0.5 shrink-0" />}
          {deliveryMin > 0 && <div className="h-full bg-azure" style={{ width: pct(deliveryMin) }} />}
        </div>
        <div
          className="absolute bottom-[-3px] top-[-3px] w-px bg-paper-faint/70"
          style={{ left: pct(allowed) }}
          title={`Allowed ${mins(allowed)} per day`}
        />
      </div>
      <div className="relative mt-1 h-3 text-[9px] text-paper-faint">
        <span className="num absolute -translate-x-1/2" style={{ left: `${tickLeft}%` }}>
          {mins(allowed)} allowed
        </span>
      </div>
    </div>
  );
}

// Seven tiny stacked columns, break at the baseline and delivery above,
// with a hover tooltip carrying the day's exact split in text.
function WeekPattern({ series, dates }) {
  if (!dates.length) return null;
  const today = api.today();
  const peak = Math.max(1, ...series.map((s) => s.break_minutes + s.delivery_minutes));
  const hpx = (m) => (m > 0 ? `${Math.max((m / peak) * 52, 4)}px` : '0px');
  const asDate = (d) => new Date(`${d}T12:00:00`);
  return (
    <div>
      <div className="flex h-14 items-end gap-1.5 border-b border-ink-500/60 pb-px">
        {series.map((s, i) => {
          const label = `${asDate(dates[i]).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric' })} · Break ${mins(s.break_minutes)} · Delivery ${mins(s.delivery_minutes)}`;
          return (
            <div key={i} className="group relative flex-1">
              <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 hidden -translate-x-1/2 whitespace-nowrap rounded-md border border-ink-500 bg-ink-800 px-2 py-1 text-[10px] text-paper shadow-lg group-hover:block">
                {label}
              </div>
              <div title={label} className="flex h-14 flex-col justify-end gap-0.5">
                {s.break_minutes > 0 && <div className="bg-rose" style={{ height: hpx(s.break_minutes) }} />}
                {s.delivery_minutes > 0 && <div className="bg-azure" style={{ height: hpx(s.delivery_minutes) }} />}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-1.5">
        {dates.map((d) => (
          <span
            key={d}
            className={`num flex-1 text-center text-[9px] ${d === today ? 'text-paper' : 'text-paper-faint'}`}
          >
            {asDate(d).getDate()}
            {d === today ? '*' : ''}
          </span>
        ))}
      </div>
      <div className="mt-0.5 text-[9px] uppercase tracking-widest text-paper-faint/70">
        Last 7 days (* today) — hover a column for the split
      </div>
    </div>
  );
}

function RoleControl({ role, onChange }) {
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
function AllowanceInput({ allowedMinutes, onCommit }) {
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

function EmployeeReportCard({ emp, week, onChanged }) {
  const left = Math.max(0, emp.allowed_minutes - emp.break_minutes);
  const over = emp.extra_minutes > 0;

  async function setRole(role) {
    try {
      await api.updateEmployee(emp.employee_id, { role });
      onChanged?.();
    } catch (err) {
      window.alert(`Could not update the role: ${err.message}`);
    }
  }

  async function setAllowance(v) {
    try {
      await api.updateEmployee(emp.employee_id, { allowed_out_minutes: v });
      onChanged?.();
    } catch (err) {
      window.alert(`Could not update the allowance: ${err.message}`);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-panel border border-ink-500/60 bg-ink-700 p-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-paper">{emp.name}</div>
          <div className="num text-label text-paper-faint">
            #{emp.employee_id}
            {emp.employee_code ? ` · ${emp.employee_code}` : ''}
            {emp.department ? ` · ${emp.department}` : ''}
          </div>
        </div>
        <RoleControl role={emp.role} onChange={setRole} />
      </div>

      <DailyBar breakMin={emp.break_minutes} deliveryMin={emp.delivery_minutes} allowed={emp.allowed_minutes} />

      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
        <span className="num text-paper">
          {formatDuration(emp.break_minutes)}
          <span className="ml-1 text-[10px] uppercase tracking-widest text-paper-faint">break</span>
        </span>
        <span className="num text-paper">
          {formatDuration(emp.delivery_minutes)}
          <span className="ml-1 text-[10px] uppercase tracking-widest text-paper-faint">delivery</span>
        </span>
        <span className="num ml-auto text-paper-faint">{formatDuration(emp.total_minutes)} out</span>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <AllowanceInput allowedMinutes={emp.allowed_out_minutes} onCommit={setAllowance} />
        {over ? (
          <span className="flex shrink-0 items-center gap-1 rounded-md border border-rose/40 bg-rose/10 px-2 py-0.5 text-xs font-medium text-rose">
            <svg width="9" height="8" viewBox="0 0 9 8" fill="currentColor" aria-hidden="true">
              <path d="M4.5 0L9 8H0z" />
            </svg>
            {mins(emp.extra_minutes)} over
          </span>
        ) : (
          <span className="num shrink-0 text-xs text-paper-faint">{mins(left)} left</span>
        )}
      </div>

      <WeekPattern series={week?.series ?? []} dates={week?.dates ?? []} />

      {emp.open_trips > 0 && (
        <div className="text-[10px] text-amber">
          Still out right now — today&rsquo;s total is not final.
        </div>
      )}
    </div>
  );
}

export function ReportsPanel({ report, date, onChanged }) {
  const weeklyById = React.useMemo(() => {
    const m = new Map();
    (report?.weekly ?? []).forEach((w) => m.set(w.employee_id, w));
    return m;
  }, [report]);

  return (
    <section className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-500/60 px-4 py-3">
        <h2 className="text-label font-medium uppercase tracking-widest text-paper-faint">
          Out-time report — {date}
        </h2>
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5 text-xs text-paper-soft">
            <span className="h-2 w-2 rounded-full bg-rose" aria-hidden="true" />Break
          </span>
          <span className="flex items-center gap-1.5 text-xs text-paper-soft">
            <span className="h-2 w-2 rounded-full bg-azure" aria-hidden="true" />Delivery
          </span>
          <a
            href={api.reportCsvUrl(date)}
            download
            className="rounded-md border border-ink-500/60 bg-ink-800 px-2.5 py-1 text-label text-paper transition-colors hover:bg-ink-700"
          >
            Export CSV
          </a>
        </div>
      </div>
      <div className="grid gap-4 p-4 md:grid-cols-2 xl:grid-cols-3">
        {!report ? (
          <p className="col-span-full py-6 text-center text-sm text-paper-faint">Loading report…</p>
        ) : report.daily.length === 0 ? (
          <p className="col-span-full py-6 text-center text-sm text-paper-faint">
            Add employees to see their out-time here.
          </p>
        ) : (
          report.daily.map((emp) => (
            <EmployeeReportCard
              key={emp.employee_id}
              emp={emp}
              week={weeklyById.get(emp.employee_id)}
              onChanged={onChanged}
            />
          ))
        )}
      </div>
    </section>
  );
}

export default ReportsPanel;
