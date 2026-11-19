"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { MultiLine } from "@/components/Charts";
import { AnalysisPanel, PriorityCard, type CrisisCard } from "@/components/domain";
import { CountryPicker } from "@/components/domain";
import { Card, Empty, ErrorBox, Loading, SourceLink, Verification } from "@/components/ui";
import { api, post } from "@/lib/api";
import { num } from "@/lib/format";

type Compare = {
  countries: { iso3: string; name: string; region: string; event_clusters: number; material_conflict: number; verbal_conflict: number;
    cooperation: number; multi_source_clusters: number }[];
  interaction_graph: { nodes: { id: string; name: string }[]; edges: { source: string; target: string; interaction_clusters: number;
    material_conflict: number; verbal_conflict: number; cooperation: number; avg_goldstein: number | null }[] };
  shared_crises: (CrisisCard & { selected_countries_involved: string[] })[];
  timelines: Record<string, { date: string; total: number; material_conflict: number }[]>;
  interaction_evidence: { id: number; event_date: string; category_label: string; actor1: { name: string; country: string };
    actor2: { name: string; country: string }; source_url: string; verification_status: string }[];
  note: string;
};

function Graph({ g }: { g: Compare["interaction_graph"] }) {
  const n = g.nodes.length;
  const R = 120, cx = 170, cy = 150;
  const pos = new Map(g.nodes.map((node, i) => [node.id, { x: cx + R * Math.cos((2 * Math.PI * i) / n - Math.PI / 2), y: cy + R * Math.sin((2 * Math.PI * i) / n - Math.PI / 2), name: node.name }]));
  const max = Math.max(1, ...g.edges.map((e) => e.interaction_clusters));
  return (
    <svg viewBox="0 0 340 300" className="h-auto w-full max-w-md" role="img" aria-label="Interaction graph">
      {g.edges.map((e) => {
        const a = pos.get(e.source)!, b = pos.get(e.target)!;
        const conflictShare = (e.material_conflict + e.verbal_conflict) / Math.max(1, e.interaction_clusters);
        return <line key={e.source + e.target} x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={1 + 6 * (e.interaction_clusters / max)}
          stroke={conflictShare > 0.5 ? "var(--series-2)" : "var(--series-1)"} strokeOpacity={0.8}>
          <title>{`${e.source}–${e.target}: ${e.interaction_clusters} interaction clusters (${e.material_conflict} material conflict, ${e.verbal_conflict} verbal conflict, ${e.cooperation} cooperation)`}</title></line>;
      })}
      {Array.from(pos.entries()).map(([id, p]) => (
        <g key={id}><circle cx={p.x} cy={p.y} r={18} fill="rgb(var(--raised))" stroke="rgb(var(--line))" strokeWidth={2} />
          <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize={11} fill="rgb(var(--ink))">{id}</text></g>
      ))}
    </svg>
  );
}

function CompareInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const [sel, setSel] = useState<string[]>((sp.get("c") || "").split(",").filter(Boolean));
  const [data, setData] = useState<Compare | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [regionNote, setRegionNote] = useState<string | null>(null);

  useEffect(() => {
    const region = sp.get("region");
    if (!region) return;
    api<{ total: number; items: { iso3: string }[] }>(`/api/v1/countries?region=${encodeURIComponent(region)}&limit=25`).then((r) => {
      setSel(r.items.map((x) => x.iso3));
      if (r.total > 25) setRegionNote(`Region has ${r.total} countries; the first 25 are compared (query limit).`);
    }).catch(setErr);
  }, [sp]);

  useEffect(() => {
    if (sel.length === 0) { setData(null); return; }
    router.replace(`/geopolitics/compare?c=${sel.join(",")}`, { scroll: false });
    setLoading(true); setErr(null);
    post<Compare>("/api/v1/countries/compare", { countries: sel }).then(setData).catch(setErr).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel.join(",")]);

  const series = useMemo(() => {
    if (!data) return [];
    const byDate = new Map<string, Record<string, number | string>>();
    Object.entries(data.timelines).forEach(([iso, pts]) => pts.forEach((p) => {
      const row = byDate.get(p.date) || { date: p.date };
      row[iso] = p.total;
      byDate.set(p.date, row);
    }));
    return Array.from(byDate.values()).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  }, [data]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Multi-country comparison</h1>
      <Card title="Selected countries">
        <CountryPicker value={sel} onChange={setSel} />
        {regionNote && <p className="mt-2 text-xs text-muted">{regionNote}</p>}
      </Card>
      {err ? <ErrorBox error={err} /> : null}
      {loading && <Loading what="comparison" />}
      {!data && !loading && <Empty>Select one or more countries to compare developments, interactions and shared crises.</Empty>}
      {data && !loading && (<>
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Country comparison (last 30 days)" className="lg:col-span-2">
            <div className="overflow-x-auto"><table className="w-full text-sm">
              <thead className="text-left text-xs text-muted"><tr><th className="py-1">Country</th><th>Event clusters</th><th>Material conflict</th><th>Verbal conflict</th><th>Cooperation</th><th>Multi-source</th></tr></thead>
              <tbody>{data.countries.map((c) => (
                <tr key={c.iso3} className="border-t border-line"><td className="py-1.5"><Link className="hover:underline" href={`/geopolitics/countries/${c.iso3}`}>{c.name}</Link></td>
                  <td className="tabular-nums">{num(c.event_clusters)}</td><td className="tabular-nums">{num(c.material_conflict)}</td>
                  <td className="tabular-nums">{num(c.verbal_conflict)}</td><td className="tabular-nums">{num(c.cooperation)}</td><td className="tabular-nums">{num(c.multi_source_clusters)}</td></tr>))}</tbody>
            </table></div>
          </Card>
          <Card title="Directly documented interactions">
            {data.interaction_graph.edges.length === 0 ? <Empty>No direct interactions between the selected countries in this window.</Empty> : <Graph g={data.interaction_graph} />}
            <p className="text-[11px] text-muted">Line width: number of interaction clusters · orange: mostly conflict-coded · blue: mostly cooperative.</p>
          </Card>
        </div>
        <Card title="Comparative timeline (daily deduplicated event clusters)">
          {series.length ? <MultiLine data={series} keys={sel.map((s) => ({ key: s, name: data.countries.find((c) => c.iso3 === s)?.name || s }))} /> : <Empty>No history yet.</Empty>}
        </Card>
        <section>
          <h2 className="h2 mb-2">Shared crises</h2>
          {data.shared_crises.length === 0 ? <Empty>No monitored crisis involves more than one of the selected countries.</Empty> :
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{data.shared_crises.map((c) => <PriorityCard key={c.id} c={c} />)}</div>}
        </section>
        <Card title="Interaction evidence">
          {data.interaction_evidence.length === 0 ? <Empty>No interaction events.</Empty> : (
            <ul className="space-y-1.5 text-sm">{data.interaction_evidence.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted">{e.event_date}</span>
                <span>{e.category_label}: {e.actor1.name} ({e.actor1.country}) → {e.actor2.name} ({e.actor2.country})</span>
                <Verification status={e.verification_status} /><SourceLink url={e.source_url} /></li>))}</ul>
          )}
          <p className="mt-2 text-[11px] text-muted">{data.note}</p>
        </Card>
        <Card title="AI combined analysis">
          <AnalysisPanel getPath={null} runPath="/api/v1/analysis/multi-country" runBody={{ countries: sel }} />
        </Card>
      </>)}
    </div>
  );
}

export default function ComparePage() {
  return <Suspense fallback={<Loading />}><CompareInner /></Suspense>;
}
