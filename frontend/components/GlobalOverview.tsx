"use client";

/* Top of the main page: live counts and the global event map with filters. */

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import type { CrisisMarker } from "@/components/WorldMap";
import { Card, ErrorBox, LevelBadge, Loading, Stale, Stat, Verification } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { ago, num } from "@/lib/format";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

type Overview = {
  generated_at: string; counts: Record<string, number>;
  data_freshness: Record<string, { last_success_at: string | null; stale: boolean | null } | string | null>;
};
type CrisisItem = CrisisMarker & { headline: string; severity?: { level: string }; location_countries: string[]; countries: { iso3: string; name: string }[] };

const CATEGORIES = [["", "All categories"], ["fight", "Fight"], ["assault", "Assault"], ["mass_violence", "Mass violence"],
  ["coerce", "Coerce"], ["force_posture", "Military posture"], ["threaten", "Threaten"], ["protest", "Protest"],
  ["reduce_relations", "Reduce relations"], ["yield", "Yield / de-escalate"], ["diplomatic_cooperation", "Diplomatic cooperation"]];

export default function GlobalOverview() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useApi<Overview>("/api/v1/overview", { refreshInterval: 60_000 });
  const [filters, setFilters] = useState({ category: "", verification: "", country: "", start: "", crisis: "" });
  const map = useApi<{ features: never[] }>(`/api/v1/events${qs({ view: "map", limit: 400, ...filters })}`, { refreshInterval: 120_000 });
  const crises = useApi<{ items: CrisisItem[] }>("/api/v1/crises?limit=200", { refreshInterval: 120_000 });
  const countries = useApi<{ items: { iso3: string; iso_numeric: number | null }[] }>("/api/v1/countries?limit=500");

  const highlight = useMemo(() => {
    const n = new Map((countries.data?.items || []).map((c) => [c.iso3, c.iso_numeric]));
    const s = new Set<number>();
    (crises.data?.items || []).forEach((c) => c.location_countries.forEach((iso) => { const v = n.get(iso); if (v) s.add(v); }));
    return Array.from(s);
  }, [crises.data, countries.data]);

  // Map markers and the dropdown use the plain headline.
  const items = (crises.data?.items || []).map((c) => ({ ...c, title: c.headline || c.title,
    priority: { ...c.priority, level: c.severity?.level || c.priority.level } }));
  const visible = items.filter((c) => !filters.crisis || String(c.id) === filters.crisis)
    .filter((c) => !filters.country || c.countries.some((x) => x.iso3 === filters.country.toUpperCase()));

  if (error) return <ErrorBox error={error} retry={mutate} />;
  if (isLoading || !data) return <Loading what="overview" />;
  const fresh = data.data_freshness as Record<string, { last_success_at: string | null }> & { latest_event_available_at?: string };

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

      <Card title="Global event map" right={<span className="text-xs text-muted">circles: situations by severity · dots: reported events</span>}>
        <div className="mb-3 flex flex-wrap gap-2">
          <select className="input" value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value })} aria-label="Event category">
            {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select className="input" value={filters.verification} onChange={(e) => setFilters({ ...filters, verification: e.target.value })} aria-label="Verification">
            <option value="">All verification statuses</option><option value="multi_source_reported">Multi-source reported</option>
            <option value="machine_extracted">Single extraction</option>
          </select>
          <input className="input w-28" placeholder="Country ISO3" value={filters.country} maxLength={3}
            onChange={(e) => setFilters({ ...filters, country: e.target.value.toUpperCase() })} aria-label="Country" />
          <input className="input" type="date" value={filters.start} onChange={(e) => setFilters({ ...filters, start: e.target.value })} aria-label="From date" />
          <select className="input max-w-xs" value={filters.crisis} onChange={(e) => setFilters({ ...filters, crisis: e.target.value })} aria-label="Situation">
            <option value="">All situations</option>
            {items.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </select>
        </div>
        <WorldMap crises={visible} events={map.data?.features} highlight={highlight} onCrisisClick={(id) => router.push(`/geopolitics/crises/${id}`)} />
        <div className="mt-2 flex flex-wrap gap-3 text-xs text-ink2">
          {["critical", "high", "elevated", "low"].map((l) => <LevelBadge key={l} level={l} />)}
          <span>· dot opacity: <Verification status="multi_source_reported" /> vs <Verification status="machine_extracted" /></span>
        </div>
      </Card>
    </div>
  );
}
