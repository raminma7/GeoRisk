"use client";

/* "Today": the simple, readable front page. One summary line, situations as cards (headline, severity, trend,
   2–3 plain reasons, how well confirmed, last-7-days activity), what changed, and countries that could see a new
   conflict. Severity colours are the reserved status colours and always come with an icon + text label. */

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { LevelChip, monthRange, type Prediction } from "@/components/EarlyWarning";
import { ErrorBox, Loading } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ago } from "@/lib/format";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

export type Story = {
  id: number; uid: string; headline: string; kind: "conflict" | "tension" | "domestic"; kind_label: string;
  severity: { level: string; label: string }; trend: { code: "up" | "down" | "flat"; label: string }; reasons: string[];
  confidence: { level: string; label: string; detail: string }; countries: { iso3: string; name: string }[];
  activity: { date: string; violent: number }[]; last_event_at: string | null; deaths_12m: number | null;
  title: string; latitude: number | null; longitude: number | null; priority: { score: number; level: string; direction: string };
};
type Today = {
  generated_at: string;
  summary: { situations: number; serious: number; getting_worse: number; calming: number; countries_at_risk: number };
  situations: Story[]; other_signals: Pick<Story, "id" | "headline" | "severity" | "confidence" | "countries" | "reasons">[];
  other_signals_total: number;
  what_changed: { items: { kind: "worse" | "better" | "new"; crisis_id: number; text: string }[]; note: string | null };
  could_start_soon: null | { window: { start: string; end: string }; items: Prediction[] };
  data_freshness: { latest_event_available_at: string | null };
  how_to_read: Record<string, string>;
};

export const SEV: Record<string, { icon: string; chip: string; bar: string }> = {
  critical: { icon: "▲▲", chip: "bg-crit/12 text-crit ring-crit/30", bar: "bg-crit" },
  high: { icon: "▲", chip: "bg-serious/15 text-serious ring-serious/30", bar: "bg-serious" },
  elevated: { icon: "◆", chip: "bg-warn/15 text-[rgb(150_95_0)] dark:text-warn ring-warn/40", bar: "bg-warn" },
  low: { icon: "●", chip: "bg-line/50 text-ink2 ring-line", bar: "bg-ink2/40" },
};

export function SeverityChip({ s, size = "sm" }: { s: Story["severity"]; size?: "sm" | "md" }) {
  const m = SEV[s.level] || SEV.low;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full font-medium ring-1 ring-inset ${m.chip} ${size === "md" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs"}`}>
      <span aria-hidden className="text-[0.7em]">{m.icon}</span>{s.label}
    </span>
  );
}

export function Trend({ t }: { t: Story["trend"] }) {
  const cls = t.code === "up" ? "text-crit" : t.code === "down" ? "text-good" : "text-muted";
  const icon = t.code === "up" ? "↗" : t.code === "down" ? "↘" : "→";
  return <span className={`inline-flex items-center gap-1 text-xs font-medium ${cls}`}><span aria-hidden>{icon}</span>{t.label}</span>;
}

export function ConfirmedBars({ c }: { c: Story["confidence"] }) {
  const n = c.level === "high" ? 3 : c.level === "medium" ? 2 : 1;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink2" title={c.detail}>
      <span className="inline-flex items-end gap-0.5" aria-hidden>
        {[1, 2, 3].map((i) => <span key={i} className={`w-1 rounded-sm ${i <= n ? "bg-ink2" : "bg-line"}`} style={{ height: 4 + i * 3 }} />)}
      </span>
      {c.label}
    </span>
  );
}

/* Seven small bars: violent reports per day. One series, so one colour; the latest day is emphasised. */
export function ActivityBars({ data, height = 28 }: { data: Story["activity"]; height?: number }) {
  if (!data?.length) return null;
  const max = Math.max(...data.map((d) => d.violent), 1);
  const total = data.reduce((a, d) => a + d.violent, 0);
  return (
    <div className="flex items-end gap-1" role="img" aria-label={`${total} violent reports in the last 7 days`} style={{ height }}>
      {data.map((d, i) => (
        <span key={d.date} title={`${new Date(d.date + "T00:00:00Z").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })}: ${d.violent} violent reports`}
          className="w-2 rounded-t-sm" style={{ height: Math.max(2, (d.violent / max) * height), background: "var(--series-1)",
            opacity: i === data.length - 1 ? 1 : 0.45 }} />
      ))}
    </div>
  );
}

export function SituationCard({ s }: { s: Story }) {
  return (
    <Link href={`/geopolitics/crises/${s.id}`}
      className="group relative flex flex-col gap-3 overflow-hidden rounded-2xl border border-line bg-surface p-4 pl-5 transition hover:-translate-y-0.5 hover:border-accent/60 hover:shadow-sm">
      <span aria-hidden className={`absolute inset-y-0 left-0 w-1.5 ${(SEV[s.severity.level] || SEV.low).bar}`} />
      <div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted">{s.kind_label}</span>
          <SeverityChip s={s.severity} />
        </div>
        <h3 className="mt-1 text-base font-semibold leading-snug group-hover:text-accent">{s.headline}</h3>
        <div className="mt-0.5"><Trend t={s.trend} /></div>
      </div>
      <ul className="space-y-1 text-sm text-ink2">
        {s.reasons.map((r) => <li key={r} className="flex gap-2"><span aria-hidden className="mt-2 h-1 w-1 shrink-0 rounded-full bg-muted" />{r}</li>)}
      </ul>
      <div className="mt-auto flex items-end justify-between gap-3 border-t border-line pt-3">
        <ConfirmedBars c={s.confidence} />
        {s.activity?.length > 0 && (
          <div className="flex items-end gap-2">
            <span className="text-[11px] text-muted">violent reports, 7 days</span>
            <ActivityBars data={s.activity} />
          </div>
        )}
      </div>
    </Link>
  );
}

function SummaryLine({ t }: { t: Today }) {
  const s = t.summary;
  const items: [number, string, string][] = [
    [s.situations, "situations followed", "text-ink"],
    [s.serious, "serious", "text-serious"],
    [s.getting_worse, "getting worse", "text-crit"],
    [s.calming, "calming down", "text-good"],
    [s.countries_at_risk, "countries at risk of a new conflict", "text-accent"],
  ];
  return (
    <div className="flex flex-wrap gap-2">
      {items.map(([n, l, cls]) => (
        <span key={l} className="inline-flex items-baseline gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-sm">
          <b className={`tabular-nums ${cls}`}>{n}</b><span className="text-ink2">{l}</span>
        </span>
      ))}
    </div>
  );
}

function WhatChanged({ w }: { w: Today["what_changed"] }) {
  const icon = { worse: ["↗", "text-crit bg-crit/10"], better: ["↘", "text-good bg-good/10"], new: ["✦", "text-accent bg-accent/10"] };
  return (
    <section className="rounded-2xl border border-line bg-surface p-4">
      <h2 className="mb-2 font-semibold">What changed</h2>
      {w.items.length > 0 && (
        <ul className="space-y-2">
          {w.items.map((i, k) => (
            <li key={k}><Link href={`/geopolitics/crises/${i.crisis_id}`} className="flex items-start gap-2 text-sm hover:text-accent">
              <span aria-hidden className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-xs ${icon[i.kind][1]}`}>{icon[i.kind][0]}</span>{i.text}
            </Link></li>
          ))}
        </ul>
      )}
      {w.note && <p className="text-sm text-muted">{w.note}</p>}
    </section>
  );
}

function CouldStartSoon({ ew }: { ew: Today["could_start_soon"] }) {
  if (!ew) return (
    <section className="rounded-2xl border border-line bg-surface p-4">
      <h2 className="font-semibold">Could a new conflict start?</h2>
      <p className="mt-1 text-sm text-muted">The early-warning model is being set up. <Link className="link" href="/early-warning">See progress</Link></p>
    </section>
  );
  return (
    <section className="rounded-2xl border border-line bg-surface p-4">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <h2 className="font-semibold">Could a new conflict start?</h2>
        <Link href="/early-warning" className="link text-xs">All countries →</Link>
      </div>
      <p className="mb-3 text-xs text-muted">Peaceful countries with the highest risk of new fighting, {monthRange(ew.window)}. Even these usually stay peaceful.</p>
      <ol className="space-y-2">
        {ew.items.map((p) => (
          <li key={p.iso3}>
            <Link href={`/geopolitics/countries/${p.iso3}`} className="flex items-center justify-between gap-2 rounded-lg px-1 py-0.5 text-sm hover:bg-accent/5">
              <span className="min-w-0">
                <span className="font-medium">{p.name}</span>
                {p.reasons[0] && <span className="block truncate text-xs text-muted">{p.reasons[0].label}: {p.reasons[0].value}</span>}
              </span>
              <LevelChip level={p.level} />
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function TodayView() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useApi<Today>("/api/v1/today", { refreshInterval: 120_000 });
  const [all, setAll] = useState(false);
  if (error) return <ErrorBox error={error} retry={() => mutate()} />;
  if (isLoading || !data) return <Loading what="today's overview" />;
  const shown = all ? data.situations : data.situations.slice(0, 8);
  return (
    <div className="space-y-5">
      <header className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">What is happening in the world</h1>
            <p className="text-sm text-muted">Updated automatically from news worldwide · latest report {ago(data.data_freshness.latest_event_available_at)}</p>
          </div>
        </div>
        <SummaryLine t={data} />
      </header>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="grid gap-3 md:grid-cols-2">
            {shown.map((s) => <SituationCard key={s.id} s={s} />)}
          </div>
          {data.situations.length > 8 && (
            <button className="btn w-full justify-center" onClick={() => setAll(!all)}>
              {all ? "Show fewer" : `Show all ${data.situations.length} situations`}
            </button>
          )}
          {data.other_signals.length > 0 && (
            <details className="rounded-2xl border border-line bg-surface p-4">
              <summary className="cursor-pointer text-sm font-medium">
                Other signals ({data.other_signals_total}) <span className="font-normal text-muted">— local incidents or weakly confirmed reports</span>
              </summary>
              <p className="mt-2 text-xs text-muted">{data.how_to_read.other_signals}</p>
              <ul className="mt-3 divide-y divide-line">
                {data.other_signals.map((o) => (
                  <li key={o.id}><Link href={`/geopolitics/crises/${o.id}`} className="flex items-center justify-between gap-3 py-2 text-sm hover:text-accent">
                    <span className="min-w-0 truncate">{o.headline}</span>
                    <span className="flex shrink-0 items-center gap-3"><ConfirmedBars c={o.confidence} /><SeverityChip s={o.severity} /></span>
                  </Link></li>
                ))}
              </ul>
            </details>
          )}
        </div>

        <aside className="space-y-4">
          <section className="overflow-hidden rounded-2xl border border-line bg-surface">
            <WorldMap crises={data.situations} height={260} onCrisisClick={(id) => router.push(`/geopolitics/crises/${id}`)} />
            <div className="flex flex-wrap gap-3 px-4 py-2 text-[11px] text-muted">
              {(["critical", "high", "elevated"] as const).map((l) => (
                <span key={l} className="inline-flex items-center gap-1"><span className={`h-2 w-2 rounded-full ${SEV[l].bar}`} />
                  {{ critical: "Critical", high: "High", elevated: "Moderate" }[l]}</span>
              ))}
            </div>
          </section>
          <WhatChanged w={data.what_changed} />
          <CouldStartSoon ew={data.could_start_soon} />
          <details className="rounded-2xl border border-line bg-surface p-4 text-sm">
            <summary className="cursor-pointer font-medium">How to read this page</summary>
            <dl className="mt-2 space-y-2 text-xs text-ink2">
              <div><dt className="font-medium text-ink">Severity</dt><dd>{data.how_to_read.severity}</dd></div>
              <div><dt className="font-medium text-ink">Trend</dt><dd>{data.how_to_read.trend}</dd></div>
              <div><dt className="font-medium text-ink">Confirmed</dt><dd>{data.how_to_read.confirmed}</dd></div>
            </dl>
          </details>
        </aside>
      </div>
    </div>
  );
}
