"use client";

/* Top of the main page: live counts and the world map.
   The map uses the same data as the country cards below it: each country in crisis is coloured by its severity
   (click it to open the country), and dots are individual events reported in the news. */

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Card, ErrorBox, Loading, Stat } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { agoWords, num, useNow } from "@/lib/format";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

type Overview = {
  generated_at: string; counts: Record<string, number>;
  data_freshness: Record<string, { last_success_at: string | null; stale: boolean | null } | string | null>;
};
type TodayCountries = {
  summary: { countries: number; by_level: Record<string, number>; violent_events_24h: number; countries_with_violence_24h: number };
  countries: { iso3: string; name: string; headline: string; severity: { level: string; label: string } }[];
  other_signals: { iso3: string; headline: string; severity: { level: string; label: string } }[];
};

// Status colours (same as the severity chips and the left bar of the cards).
export const SEVERITY_FILL: Record<string, string> = { critical: "#d03b3b", high: "#ec835a", elevated: "#fab219", low: "#aab6c5" };
const OTHER_FILL = "#aab6c5";

const SHOW = [["violent", "Violent events"], ["all", "All reported events"]] as const;
const PERIOD = [["1", "Since yesterday"], ["3", "Last 3 days"], ["7", "Last 7 days"]] as const;

const daysAgo = (n: number) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

const FRESH_MINUTES = 45;  // three missed 15-minute updates

/* One plain line about how fresh the news data is; ticks every 30 seconds. */
function DataFreshness({ at }: { at?: string | null }) {
  const now = useNow();
  if (!at) return <p className="text-sm text-serious">⚠ No news data collected yet. The first update arrives within about 15 minutes.</p>;
  const fresh = now - new Date(at).getTime() <= FRESH_MINUTES * 60_000;
  return fresh ? (
    <p className="flex items-center gap-1.5 text-sm text-muted">
      <span aria-hidden className="h-2 w-2 rounded-full bg-good" />
      News data updated {agoWords(at, now)} · checks for new events every 15 minutes
    </p>
  ) : (
    <p className="text-sm text-serious" title="See Admin → Data collection health">
      ⚠ News data last updated {agoWords(at, now)} — the data collector may be stopped
    </p>
  );
}

export default function GlobalOverview() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useApi<Overview>("/api/v1/overview", { refreshInterval: 60_000 });
  const today = useApi<TodayCountries>("/api/v1/today", { refreshInterval: 120_000 });
  const countries = useApi<{ items: { iso3: string; iso_numeric: number | null }[] }>("/api/v1/countries?limit=500");
  const [show, setShow] = useState<"violent" | "all">("violent");
  const [period, setPeriod] = useState("1");
  const [place, setPlace] = useState("");
  type MapEvents = { features: never[]; total?: number; countries?: number };
  const mapUrl = (p: string, violent: boolean, where: string) => `/api/v1/events${qs({
    view: "map", limit: 400, start: daysAgo(Number(p)), quad_class: violent ? 4 : undefined, located_in: where || undefined })}`;
  const events = useApi<MapEvents>(mapUrl(period, show === "violent", place), { refreshInterval: 120_000 });
  // The violent-event tiles read the map's own default view (same request, so the numbers always match the dots).
  const violent24h = useApi<MapEvents>(mapUrl("1", true, ""), { refreshInterval: 120_000 });

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
  const fresh = data.data_freshness as Record<string, { last_success_at: string | null }>;
  const nEvents = events.data?.features.length ?? 0;
  const totalEvents = events.data?.total ?? nEvents;
  const sm = today.data?.summary;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Global Overview</h1>
        <DataFreshness at={fresh.gdelt_events?.last_success_at} />
      </div>

      {/* Same numbers as the map below: countries by colour, and the violent-event dots of the last 24 hours. */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Countries in crisis" value={num(sm?.countries)} hint="coloured on the map" />
        {(["critical", "high", "elevated"] as const).map((l) => (
          <Stat key={l} value={num(sm?.by_level?.[l] ?? 0)} hint={`${l === "elevated" ? "moderate" : l} severity`}
            label={<span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-3 rounded-sm" style={{ background: SEVERITY_FILL[l] }} />
              {{ critical: "Critical", high: "High", elevated: "Moderate" }[l]}</span>} />
        ))}
        <Stat label="Violent events since yesterday" value={num(violent24h.data?.total ?? sm?.violent_events_24h)} hint="dots on the map, deduplicated" />
        <Stat label="Countries with violence since yesterday" value={num(violent24h.data?.countries ?? sm?.countries_with_violence_24h)} hint="where those events happened" />
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
            Each dot = one {show === "violent" ? "violent " : ""}event reported in the news · {PERIOD.find((p) => p[0] === period)?.[1].toLowerCase()}:{" "}
            {totalEvents > nEvents ? `${num(nEvents)} most serious of ${num(totalEvents)} shown` : `${num(nEvents)} events`}</span>
        </div>
      </Card>
    </div>
  );
}
