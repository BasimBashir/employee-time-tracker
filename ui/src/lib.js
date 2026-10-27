import { useEffect, useRef, useState } from 'react';

/** "1:04:02" — hours only when needed. Durations are the product's core number. */
export function formatDuration(minutes) {
  if (minutes === null || minutes === undefined) return '—';
  const total = Math.max(0, Math.round(minutes * 60));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** "13:04" from an ISO timestamp, without dragging in a parse library. */
export function formatClock(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * A ticking clock for one live value.
 *
 * The panel holds a handful of running timers, so each mounts its own interval and
 * clears it on unmount. Anything long-lived and global would be a leak.
 */
export function useInterval(callback, delayMs) {
  const saved = useRef(callback);
  useEffect(() => {
    saved.current = callback;
  }, [callback]);
  useEffect(() => {
    if (delayMs === null) return undefined;
    const id = setInterval(() => saved.current(), delayMs);
    return () => clearInterval(id);
  }, [delayMs]);
}

/** Re-renders every second so live timers advance. */
export function useNow(active, intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useInterval(() => setNow(Date.now()), active ? intervalMs : null);
  return active ? now : null;
}

/** Minutes elapsed since an ISO timestamp, live-ticking. */
export function useElapsedMinutes(iso, active) {
  const now = useNow(active);
  if (!active || now === null) return null;
  const start = new Date(iso).getTime();
  return (now - start) / 60000;
}
