import { useEffect, useState } from "react";

export function ago(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return "just now";
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function fmtDate(iso?: string | null, withTime = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return withTime
    ? d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function num(n?: number | null, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (Number.isInteger(n)) digits = 0;
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export const label = (s?: string | null) => (s ? s.replace(/_/g, " ") : "—");

/** A clock that re-renders the caller every `intervalMs`, so "x min ago" texts keep counting. */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

/** "6 min ago", "3 h ago", "2 days ago" — readable, relative to `now`. */
export function agoWords(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (s < 60) return "less than a minute ago";
  if (s < 5400) { const m = Math.round(s / 60); return `${m} min ago`; }
  if (s < 172800) { const h = Math.round(s / 3600); return `${h} h ago`; }
  return `${Math.round(s / 86400)} days ago`;
}
