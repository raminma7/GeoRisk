"use client";

import Link from "next/link";
import { ReactNode } from "react";
import { ago, label } from "@/lib/format";

/* Status colors carry level meaning and always ship with an icon + text label. */
const LEVEL: Record<string, { cls: string; icon: string }> = {
  critical: { cls: "bg-crit/15 text-crit border-crit/40", icon: "▲▲" },
  high: { cls: "bg-serious/15 text-serious border-serious/40", icon: "▲" },
  elevated: { cls: "bg-warn/15 text-[rgb(170_110_0)] dark:text-warn border-warn/40", icon: "◆" },
  low: { cls: "bg-line/40 text-ink2 border-line", icon: "●" },
  info: { cls: "bg-accent/10 text-accent border-accent/30", icon: "ⓘ" },
};

export function LevelBadge({ level }: { level?: string }) {
  const l = LEVEL[level || "low"] || LEVEL.low;
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium ${l.cls}`}>
      <span aria-hidden>{l.icon}</span>
      {label(level)}
    </span>
  );
}

const DIR: Record<string, string> = { escalating: "↑ escalating", de_escalating: "↓ de-escalating", stable: "→ stable" };
export function Direction({ d }: { d?: string }) {
  const cls = d === "escalating" ? "text-crit" : d === "de_escalating" ? "text-good" : "text-muted";
  return <span className={`text-xs font-medium ${cls}`}>{DIR[d || "stable"] || d}</span>;
}

const VER: Record<string, { text: string; cls: string; title: string }> = {
  machine_extracted: { text: "unverified · single extraction", cls: "border-line text-muted",
    title: "Automatically extracted from news (GDELT). Not independently verified." },
  multi_source_reported: { text: "unverified · multi-source", cls: "border-accent/40 text-accent",
    title: "Reported by ≥3 independent outlets. Still not independently verified." },
  curated_dataset: { text: "curated dataset", cls: "border-good/40 text-good", title: "Human-coded research dataset (UCDP)." },
};
export function Verification({ status }: { status?: string }) {
  const v = VER[status || "machine_extracted"] || VER.machine_extracted;
  return <span title={v.title} className={`inline-block rounded border px-1.5 py-0.5 text-[11px] ${v.cls}`}>{v.text}</span>;
}

export function Confidence({ label: l, score }: { label?: string | null; score?: number | null }) {
  if (!l) return <span className="text-xs text-muted">confidence n/a</span>;
  const bars = l === "high" ? 3 : l === "medium" ? 2 : 1;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-ink2" title={`Evidence confidence ${score ?? ""}`}>
      <span className="inline-flex gap-0.5" aria-hidden>
        {[1, 2, 3].map((i) => <span key={i} className={`h-2.5 w-1 rounded-sm ${i <= bars ? "bg-ink2" : "bg-line"}`} />)}
      </span>
      evidence {l}
    </span>
  );
}

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="h2">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label: l, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="card">
      <div className="text-xs text-muted">{l}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Loading({ what = "data" }: { what?: string }) {
  return (
    <div className="animate-pulse space-y-2 py-4" role="status" aria-label={`Loading ${what}`}>
      <div className="h-3 w-2/3 rounded bg-line" />
      <div className="h-3 w-1/2 rounded bg-line" />
      <div className="h-3 w-3/4 rounded bg-line" />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line p-4 text-sm text-muted">{children}</div>;
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded-lg border border-crit/40 bg-crit/10 p-3 text-sm text-crit" role="alert">
      Could not load data: {msg}. Is the API running?{" "}
      {retry && <button className="underline" onClick={retry}>Retry</button>}
    </div>
  );
}

export function Stale({ at, maxMinutes = 45 }: { at?: string | null; maxMinutes?: number }) {
  if (!at) return <span className="rounded bg-warn/20 px-1.5 py-0.5 text-xs">⚠ no data yet</span>;
  const stale = Date.now() - new Date(at).getTime() > maxMinutes * 60000;
  return stale ? (
    <span className="rounded bg-warn/20 px-1.5 py-0.5 text-xs" title="Data may be outdated">⚠ stale · {ago(at)}</span>
  ) : (
    <span className="rounded bg-good/15 px-1.5 py-0.5 text-xs text-good">● fresh · {ago(at)}</span>
  );
}

export function State<T>({ data, error, isLoading, mutate, children, empty, what }: {
  data: T | undefined; error: unknown; isLoading: boolean; mutate?: () => void;
  children: (d: T) => ReactNode; empty?: (d: T) => boolean; what?: string;
}) {
  if (error) return <ErrorBox error={error} retry={mutate} />;
  if (isLoading || data === undefined) return <Loading what={what} />;
  if (empty && empty(data)) return <Empty>No {what || "data"} yet. Background monitoring populates this automatically.</Empty>;
  return <>{children(data)}</>;
}

export function CountryChips({ countries }: { countries: { iso3: string; name: string }[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {countries.map((c) => (
        <Link key={c.iso3} href={`/geopolitics/countries/${c.iso3}`}
          className="rounded border border-line px-1.5 py-0.5 text-xs text-ink2 hover:border-accent">{c.name}</Link>
      ))}
    </div>
  );
}

export function SourceLink({ url, children }: { url?: string | null; children?: ReactNode }) {
  if (!url) return null;
  let host = url;
  try { host = new URL(url).hostname.replace(/^www\./, ""); } catch { /* keep raw */ }
  return <a href={url} target="_blank" rel="noopener noreferrer nofollow" className="link text-xs">{children || host} ↗</a>;
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: string; disabled?: boolean }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="mb-4 flex flex-wrap gap-1 border-b border-line" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={active === t.id} disabled={t.disabled} onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm ${active === t.id ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"} disabled:cursor-not-allowed disabled:opacity-50`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Disclaimer({ children }: { children: ReactNode }) {
  return <p className="text-xs text-muted">{children}</p>;
}
