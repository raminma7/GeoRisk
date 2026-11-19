"use client";

import Link from "next/link";
import { use, useState } from "react";
import { DailyStack } from "@/components/Charts";
import { AnalysisPanel, PriorityCard, type CrisisCard } from "@/components/domain";
import { Card, Empty, SourceLink, State, Stat, Tabs, Verification } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { ago, label, num } from "@/lib/format";

type Country = {
  country: { iso3: string; name: string; official_name: string; region: string; continent: string; groups: string[]; aliases: string[] };
  indicators: { as_of?: string; values?: Record<string, { value: number; baseline_mean: number | null; zscore: number | null }> };
  crises: CrisisCard[];
  relationships: { country: string; name: string; interaction_clusters_7d: number; material_conflict: number; verbal_conflict: number;
    verbal_cooperation: number; material_cooperation: number; avg_goldstein: number | null }[];
  timeline: { date: string; material_conflict: number; verbal_conflict: number; cooperation: number }[];
  modules: { economy: { status: string } };
};
type Ev = { id: number; event_date: string; available_at: string; category_label: string; actor1: { name: string; country: string };
  actor2: { name: string; country: string }; location: { name: string; country: string }; source_url: string; verification_status: string;
  num_sources: number; severity: number };

const ROLES = [["", "Any role"], ["occurred_in", "Occurred in the country"], ["government_involved", "Government involved"],
  ["actor1_affiliation", "Country actor as initiator"], ["actor2_affiliation", "Country actor as target"],
  ["non_state_cross_border", "Non-state actors abroad"]];

export default function CountryPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const [tab, setTab] = useState("overview");
  const [role, setRole] = useState("");
  const [cat, setCat] = useState("");
  const d = useApi<Country>(`/api/v1/countries/${code}`);
  const ev = useApi<{ total: number; items: Ev[] }>(tab === "events" || tab === "overview"
    ? `/api/v1/countries/${code}/events${qs({ role, category: cat, limit: tab === "overview" ? 10 : 100, min_severity: tab === "overview" ? 0.4 : 0 })}` : null);
  return (
    <State {...d} what="country">{(c) => {
      const v = c.indicators.values || {};
      return (
        <div className="space-y-4">
          <div>
            <Link href="/geopolitics/countries" className="link text-sm">← Countries</Link>
            <h1 className="text-xl font-semibold">{c.country.name}</h1>
            <div className="text-xs text-muted">{c.country.official_name} · {c.country.region} · {c.country.continent}
              {c.country.groups.length > 0 && ` · ${c.country.groups.join(", ")}`}</div>
          </div>
          <Tabs active={tab} onChange={setTab} tabs={[{ id: "overview", label: "Overview" }, { id: "events", label: "Events" },
            { id: "relations", label: "International relations" }, { id: "crises", label: `Crises (${c.crises.length})` },
            { id: "analysis", label: "AI analysis" }, { id: "economy", label: "Economy (planned)", disabled: true }]} />

          {tab === "overview" && (<>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              {(["clusters", "material_conflict", "verbal_conflict", "diplomatic_signals", "cooperation"] as const).map((k) => (
                <Stat key={k} label={`${label(k)} (24h)`} value={num(v[k]?.value)}
                  hint={v[k]?.zscore != null ? `z = ${num(v[k].zscore, 1)} vs 30-day baseline` : "baseline not yet available"} />
              ))}
            </div>
            <div className="grid gap-4 lg:grid-cols-3">
              <Card title="Historical timeline (deduplicated event clusters)" className="lg:col-span-2">
                {c.timeline.length ? <DailyStack data={c.timeline} /> : <Empty>No history yet.</Empty>}
              </Card>
              <Card title="Current monitored crises">
                {c.crises.length === 0 ? <Empty>Not part of a monitored crisis.</Empty> :
                  <ul className="space-y-1 text-sm">{c.crises.slice(0, 8).map((x) => <li key={x.id}><Link className="hover:underline" href={`/geopolitics/crises/${x.id}`}>{x.title}</Link>
                    <span className="text-xs text-muted"> · {num(x.priority.score)}</span></li>)}</ul>}
              </Card>
            </div>
            <Card title="Important documented conflict-related events">
              <State {...ev} what="events" empty={(e) => e.items.length === 0}>{(e) => <EventTable items={e.items} />}</State>
            </Card>
          </>)}

          {tab === "events" && (
            <Card title="Events involving this country" right={
              <div className="flex gap-2">
                <select className="input" value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">{ROLES.map(([v2, l]) => <option key={v2} value={v2}>{l}</option>)}</select>
                <select className="input" value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category">
                  <option value="">All categories</option>
                  {["fight", "assault", "coerce", "threaten", "protest", "reduce_relations", "consult", "diplomatic_cooperation", "yield"].map((x) => <option key={x} value={x}>{label(x)}</option>)}
                </select></div>}>
              <p className="mb-2 text-xs text-muted">Roles distinguish where events occurred from involvement of the country’s government or its nationals.</p>
              <State {...ev} what="events" empty={(e) => e.items.length === 0}>{(e) => <><EventTable items={e.items} /><div className="mt-2 text-xs text-muted">{e.total} events</div></>}</State>
            </Card>
          )}

          {tab === "relations" && (
            <Card title="Documented interactions with other countries (last 7 days)">
              {c.relationships.length === 0 ? <Empty>No bilateral interactions recorded yet.</Empty> : (
                <table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Country</th><th>Clusters</th><th>Material conflict</th><th>Verbal conflict</th><th>Cooperation</th><th>Avg Goldstein</th><th /></tr></thead>
                  <tbody>{c.relationships.map((r) => (
                    <tr key={r.country} className="border-t border-line"><td className="py-1.5"><Link className="hover:underline" href={`/geopolitics/countries/${r.country}`}>{r.name}</Link></td>
                      <td className="tabular-nums">{r.interaction_clusters_7d}</td><td className="tabular-nums">{r.material_conflict}</td><td className="tabular-nums">{r.verbal_conflict}</td>
                      <td className="tabular-nums">{r.verbal_cooperation + r.material_cooperation}</td><td className="tabular-nums">{num(r.avg_goldstein, 1)}</td>
                      <td><Link className="link text-xs" href={`/geopolitics/compare?c=${c.country.iso3},${r.country}`}>compare</Link></td></tr>))}</tbody></table>
              )}
            </Card>
          )}

          {tab === "crises" && (c.crises.length === 0 ? <Empty>Not part of a monitored crisis.</Empty> :
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{c.crises.map((x) => <PriorityCard key={x.id} c={x} />)}</div>)}

          {tab === "analysis" && <Card title="AI country analysis"><AnalysisPanel getPath={`/api/v1/countries/${code}/analysis`} runPath={`/api/v1/analysis/countries/${code}`} /></Card>}
        </div>
      );
    }}</State>
  );
}

function EventTable({ items }: { items: Ev[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted"><tr><th className="py-1">Date</th><th>Event</th><th>Actors</th><th>Location</th><th>Status</th><th>Source</th></tr></thead>
        <tbody>{items.map((e) => (
          <tr key={e.id} className="border-t border-line align-top">
            <td className="whitespace-nowrap py-1.5 pr-2 text-xs">{e.event_date}<div className="text-muted">{ago(e.available_at)}</div></td>
            <td className="pr-2">{e.category_label}</td>
            <td className="pr-2 text-xs">{e.actor1.name || "—"} ({e.actor1.country || "?"}) → {e.actor2.name || "—"} ({e.actor2.country || "?"})</td>
            <td className="pr-2 text-xs">{e.location.name || e.location.country || "—"}</td>
            <td className="pr-2"><Verification status={e.verification_status} /></td>
            <td><SourceLink url={e.source_url} /></td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}
