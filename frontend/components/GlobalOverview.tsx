"use client";

/* Top of the main page: live counts and the world map.
   The map uses the same data as the country cards below it: each country in crisis is coloured by its severity
   (click it to open the country), and dots are individual events reported in the news. */

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Card, ErrorBox, Loading, Stale, Stat } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { ago, num } from "@/lib/format";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

type Overview = {
  generated_at: string; counts: Record<string, number>;
  data_freshness: Record<string, { last_success_at: string | null; stale: boolean | null } | string | null>;
};
type TodayCountries = {
  countries: { iso3: string; name: string; headline: string; severity: { level: string; label: string } }[];
  other_signals: { iso3: string; headline: string; severity: { level: string; label: string } }[];
};

// Status colours (same as the severity chips and the left bar of the cards).
export const SEVERITY_FILL: Record<string, string> = { critical: "#d03b3b", high: "#ec835a", elevated: "#fab219", low: "#aab6c5" };
const OTHER_FILL = "#aab6c5";

const SHOW = [["violent", "Violent events"], ["all", "All reported events"]] as const;
const PERIOD = [["1", "Last 24 hours"], ["3", "Last 3 days"], ["7", "Last 7 days"]] as const;

const daysAgo = (n: number) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

export default function GlobalOverview() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useApi<Overview>("/api/v1/overview", { refreshInterval: 60_000 });
  const today = useApi<TodayCountries>("/api/v1/today", { refreshInterval: 120_000 });
  const countries = useApi<{ items: { iso3: string; iso_numeric: number | null }[] }>("/api/v1/countries?limit=500");
  const [show, setShow] = useState<"violent" | "all">("violent");
  const [period, setPeriod] = useState("1");
  const [place, setPlace] = useState("");
  const events = useApi<{ features: never[] }>(`/api/v1/events${qs({
    view: "map", limit: 400, start: daysAgo(Number(period)), quad_class: show === "violent" ? 4 : undefined, located_in: place || undefined,
  })}`, { refreshInterval: 120_000 });

  const { fills, titles, numToIso } = useMemo(() => {
    const toNum = new Map((countries.data?.items || []).filter((c) => c.iso_numeric).map((c) => [c.iso3, String(c.iso_numeric).padStart(3, "0")]));
    const fills: Record<string, string> = {}, titles: Record<string, string> = {}, numToIso: Record<string, string> = {};
    for (const o of today.data?.other_signals || []) {
      const n = toNum.get(o.iso3); if (!n) continue;
      fills[n] = OTHER_FILL; titles[n] = `${o.headline} · local or weakly confirmed reports`; numToIso[n] = o.iso3;
    }
    for (const c of today.data?.countries || []) {
      const n = toNum.get(c.iso3); if (!n) continue;
      fills[n] = SEVERITY_FILL[c.severity.level] || SEVERITY_FILL.low; titles[n] = `${c.headline} · ${c.severity.label} · click for details`;
      numToIso[n] = c.iso3;
    }
    return { fills, titles, numToIso };
  }, [today.data, countries.data]);

  if (error) return <ErrorBox error={error} retry={mutate} />;
  if (isLoading || !data) return <Loading what="overview" />;
  const fresh = data.data_freshness as Record<string, { last_success_at: string | null }> & { latest_event_available_at?: string };
  const nEvents = events.data?.features.length ?? 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Global Overview</h1>
          <p className="text-sm text-muted">Updated automatically from news worldwide. Refreshed {ago(data.generated_at)}.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted">GDELT events</span><Stale at={fresh.gdelt_events?.last_success_at} />
          <span className="text-muted">News</span><Stale at={fresh.gdelt_doc?.last_success_at} maxMinutes={90} />
          <span className="text-muted">Latest event</span><span>{ago(fresh.latest_event_available_at as unknown as string)}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Active monitored crises" value={num(data.counts.active_crises)} />
        <Stat label="New crises (48h)" value={num(data.counts.new_crises_48h)} />
        <Stat label="Needing review" value={num(data.counts.crises_needing_review)} />
        <Stat label="Events ingested (24h)" value={num(data.counts.events_24h)} hint={`${num(data.counts.event_clusters_24h)} deduplicated`} />
        <Stat label="Countries with events (24h)" value={num(data.counts.countries_with_events_24h)} />
        <Stat label="Open alerts" value={num(data.counts.open_alerts)} />
      </div>

      <Card title="Global event map" right={<span className="text-xs text-muted">Click a coloured country for details</span>}>
        <div className="mb-3 flex flex-wrap gap-2">
          <select className="input" value={show} onChange={(e) => setShow(e.target.value as "violent" | "all")} aria-label="Dots to show">
            {SHOW.map(([v, l]) => <option key={v} value={v}>Dots: {l}</option>)}
          </select>
          <select className="input" value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period">
            {PERIOD.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select className="input max-w-xs" value={place} onChange={(e) => setPlace(e.target.value)} aria-label="Country">
            <option value="">All countries</option>
            {(today.data?.countries || []).map((c) => <option key={c.iso3} value={c.iso3}>{c.headline}</option>)}
          </select>
        </div>
        <WorldMap events={events.data?.features} fills={fills} fillTitles={titles}
          onCountryClick={(n) => numToIso[n] && router.push(`/geopolitics/countries/${numToIso[n]}`)} />
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-ink2">
          <span className="font-medium text-ink">Country colour = crisis severity:</span>
          {(["critical", "high", "elevated"] as const).map((l) => (
            <span key={l} className="inline-flex items-center gap-1.5"><span className="h-3 w-4 rounded-sm" style={{ background: SEVERITY_FILL[l] }} />
              {{ critical: "Critical", high: "High", elevated: "Moderate" }[l]}</span>
          ))}
          <span className="inline-flex items-center gap-1.5"><span className="h-3 w-4 rounded-sm" style={{ background: OTHER_FILL }} />Local reports only</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--series-1)" }} />
            Each dot = one {show === "violent" ? "violent " : ""}event reported in the news ({nEvents} shown, {PERIOD.find((p) => p[0] === period)?.[1].toLowerCase()})</span>
        </div>
      </Card>
    </div>
  );
}
