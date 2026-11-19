"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ClusterRow, CrisisCard, PriorityCard, type Cluster } from "@/components/domain";
import { WatchListStrip, type Prediction } from "@/components/EarlyWarning";
import { Card, Disclaimer, Empty, ErrorBox, LevelBadge, Loading, Stale, Stat, Verification } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { ago, label, num } from "@/lib/format";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

type Overview = {
  generated_at: string; counts: Record<string, number>; priority_situations: CrisisCard[]; changing_situations: CrisisCard[];
  latest_developments: Cluster[]; alerts: { id: number; type: string; severity: string; title: string; created_at: string;
    crisis_id: number | null; requires_review: boolean; status: string }[];
  latest_reports: { id: number; scope_type: string; scope_key: string; created_at: string; summary: string | null }[];
  data_freshness: Record<string, { last_success_at: string | null; stale: boolean | null } | string | null>;
  ai_enabled: boolean; disclaimer: string;
  early_warning: null | { window: { start: string; end: string }; data_through: string; items: Prediction[] };
};

const CATEGORIES = [["", "All categories"], ["fight", "Fight"], ["assault", "Assault"], ["mass_violence", "Mass violence"],
  ["coerce", "Coerce"], ["force_posture", "Military posture"], ["threaten", "Threaten"], ["protest", "Protest"],
  ["reduce_relations", "Reduce relations"], ["yield", "Yield / de-escalate"], ["diplomatic_cooperation", "Diplomatic cooperation"]];

export default function OverviewPage() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useApi<Overview>("/api/v1/overview", { refreshInterval: 60_000 });
  const [filters, setFilters] = useState({ category: "", verification: "", country: "", start: "", crisis: "" });
  const map = useApi<{ features: never[] }>(`/api/v1/events${qs({ view: "map", limit: 400, ...filters })}`, { refreshInterval: 120_000 });
  const crises = useApi<{ items: CrisisCard[] }>("/api/v1/crises?limit=200", { refreshInterval: 120_000 });
  const countries = useApi<{ items: { iso3: string; iso_numeric: number | null; name: string }[] }>("/api/v1/countries?limit=500");
  const status = useApi<{ sources: { key: string; name: string; enabled: boolean; stale: boolean | null; last_success_at: string | null;
    last_run: { status: string; error: string | null } | null; batches: Record<string, number> }[];
    workers: { host: string; healthy: boolean; last_heartbeat_seconds_ago: number }[]; redis_ok: boolean;
    coverage: Record<string, number | string>; delayed_or_failed_batches: unknown[] }>("/api/v1/monitoring/status", { refreshInterval: 60_000 });

  const highlight = useMemo(() => {
    const num = new Map((countries.data?.items || []).map((c) => [c.iso3, c.iso_numeric]));
    const s = new Set<number>();
    (crises.data?.items || []).forEach((c) => c.location_countries.forEach((iso) => { const n = num.get(iso); if (n) s.add(n); }));
    return Array.from(s);
  }, [crises.data, countries.data]);

  const visibleCrises = (crises.data?.items || []).filter((c) => !filters.crisis || String(c.id) === filters.crisis)
    .filter((c) => !filters.country || c.countries.some((x) => x.iso3 === filters.country.toUpperCase()));

  if (error) return <ErrorBox error={error} retry={mutate} />;
  if (isLoading || !data) return <Loading what="overview" />;
  const fresh = data.data_freshness as Record<string, { last_success_at: string | null }> & { latest_event_available_at?: string };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Global Overview</h1>
          <p className="text-sm text-muted">Automatically populated by background monitoring — no selection required. Refreshed {ago(data.generated_at)}.</p>
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

      <Card title="Global event map" right={<span className="text-xs text-muted">circles: crises by review priority · dots: event clusters</span>}>
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
          <select className="input max-w-xs" value={filters.crisis} onChange={(e) => setFilters({ ...filters, crisis: e.target.value })} aria-label="Crisis">
            <option value="">All crises</option>
            {(crises.data?.items || []).map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </select>
        </div>
        <WorldMap crises={visibleCrises} events={map.data?.features} highlight={highlight} onCrisisClick={(id) => router.push(`/geopolitics/crises/${id}`)} />
        <div className="mt-2 flex flex-wrap gap-3 text-xs text-ink2">
          {["critical", "high", "elevated", "low"].map((l) => <LevelBadge key={l} level={l} />)}
          <span>· dot opacity: <Verification status="multi_source_reported" /> vs <Verification status="machine_extracted" /></span>
        </div>
      </Card>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="h2">Priority situations</h2>
          <Link href="/geopolitics/crises" className="link text-sm">All crises →</Link>
        </div>
        {data.priority_situations.length === 0
          ? <Empty>No monitored situations yet. The crisis engine creates them automatically as GDELT data is ingested (every 15 minutes).</Empty>
          : <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">{data.priority_situations.map((c) => <PriorityCard key={c.id} c={c} />)}</div>}
        <Disclaimer>{data.disclaimer}</Disclaimer>
      </section>

      {data.early_warning && data.early_warning.items.length > 0 && <WatchListStrip data={data.early_warning} />}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Latest developments" className="xl:col-span-2">
          {data.latest_developments.length === 0 ? <Empty>No recent conflict or diplomatic developments.</Empty>
            : <ul>{data.latest_developments.map((c) => <ClusterRow key={c.id} c={c} />)}</ul>}
        </Card>
        <div className="space-y-4">
          <Card title="Monitoring alerts" right={<Link href="/alerts" className="link text-xs">All →</Link>}>
            {data.alerts.length === 0 ? <Empty>No alerts.</Empty> : (
              <ul className="space-y-2">
                {data.alerts.slice(0, 8).map((a) => (
                  <li key={a.id} className="text-sm">
                    <div className="flex items-center gap-2"><LevelBadge level={a.severity} /><span className="text-[11px] text-muted">{label(a.type)} · {ago(a.created_at)}</span></div>
                    <Link className="hover:underline" href={a.crisis_id ? `/geopolitics/crises/${a.crisis_id}` : "/alerts"}>{a.title}</Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Changing situations">
            {data.changing_situations.length === 0 ? <Empty>No crises currently show escalation or de-escalation indicators.</Empty> : (
              <ul className="space-y-1 text-sm">{data.changing_situations.map((c) => (
                <li key={c.id}><Link href={`/geopolitics/crises/${c.id}`} className="hover:underline">{c.title}</Link>{" "}
                  <span className={c.priority.direction === "escalating" ? "text-crit" : "text-good"}>{label(c.priority.direction)}</span></li>))}</ul>
            )}
          </Card>
          <Card title="Latest intelligence reports">
            {!data.ai_enabled && data.latest_reports.length === 0 ? <Empty>AI reports are disabled (no AI provider configured). Monitoring continues without AI.</Empty>
              : data.latest_reports.length === 0 ? <Empty>No reports yet.</Empty> : (
                <ul className="space-y-2 text-sm">{data.latest_reports.map((r) => (
                  <li key={r.id}><div className="text-[11px] text-muted">{label(r.scope_type)} · {ago(r.created_at)} · AI interpretation</div>
                    <p className="line-clamp-3">{r.summary}</p></li>))}</ul>
              )}
          </Card>
        </div>
      </div>

      <Card title="Monitoring status" right={<Link href="/settings#status" className="link text-xs">Details →</Link>}>
        {status.error ? <ErrorBox error={status.error} /> : !status.data ? <Loading /> : (
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <h3 className="mb-1 text-xs font-semibold text-muted">Data sources</h3>
              <ul className="space-y-1 text-sm">
                {status.data.sources.filter((s) => s.enabled).map((s) => (
                  <li key={s.key} className="flex items-center justify-between gap-2">
                    <span>{s.name}</span>
                    <span className="text-xs">{s.stale ? <span className="text-serious">⚠ delayed</span> : <span className="text-good">● ok</span>}
                      <span className="text-muted"> · last success {ago(s.last_success_at)}{s.last_run?.status === "failed" ? " · last run failed" : ""}</span></span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold text-muted">Coverage</h3>
              <div className="text-sm">{num(status.data.coverage.events_24h as number)} events / 24h across {num(status.data.coverage.countries_with_events_24h as number)} countries</div>
              <div className="text-xs text-muted">History since {String(status.data.coverage.earliest_event_available_at || "—").slice(0, 16).replace("T", " ")}</div>
              <div className="mt-1 text-xs">{status.data.delayed_or_failed_batches.length} delayed/failed GDELT batches queued for recovery</div>
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold text-muted">Workers</h3>
              {status.data.workers.length === 0 ? <div className="text-sm text-serious">⚠ No worker heartbeat (start Celery worker + beat)</div> :
                status.data.workers.map((w) => <div key={w.host} className="text-sm">{w.healthy ? "● " : "⚠ "}{w.host} · {Math.round(w.last_heartbeat_seconds_ago)}s ago</div>)}
              <div className="text-xs text-muted">Redis {status.data.redis_ok ? "reachable" : "unreachable"}</div>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
