"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { use, useState } from "react";
import { ComponentBars, DailyStack, PriorityHistory } from "@/components/Charts";
import { AnalysisPanel, ClusterRow, type Cluster, type CrisisCard } from "@/components/domain";
import { SpilloverCard, type Prediction } from "@/components/EarlyWarning";
import { Card, Confidence, CountryChips, Direction, Disclaimer, Empty, LevelBadge, Loading, SourceLink, State, Tabs, Verification } from "@/components/ui";
import { post, qs, useApi } from "@/lib/api";
import { useView } from "@/lib/view";
import SimpleCrisis, { type Incident } from "@/components/SimpleCrisis";
import type { Story } from "@/components/Today";
import { ago, fmtDate, label, num } from "@/lib/format";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

type Detail = CrisisCard & {
  summary: string | null; participants: { key: string; type: string; iso3: string | null; name: string; actor_type: string | null;
    role: string; event_clusters: number; first_seen_at: string }[];
  priority_detail: null | { score: number; level: string; direction: string; explanation: string; components: Record<string, never>;
    triggering_indicators: Record<string, unknown>[]; independent_source_count: number; evidence_confidence: number;
    evidence_confidence_label: string; algorithm_version: string; computed_at: string; weights: Record<string, number> };
  evidence_log: { action: string; reason: string; created_at: string; algorithm_version: string; evidence: Record<string, unknown> }[];
  related_articles: { id: number; title: string; url: string; domain: string; language: string; source_country: string; seen_at: string }[];
  review: { needs_review: boolean; note: string | null; reviewed_by: string | null };
  spillover_risk: Prediction[];
  story: Story; recent_incidents: Incident[];
};
type Timeline = { events: { total: number; items: Cluster[] }; verification_breakdown: Record<string, number>;
  verification_legend: Record<string, string>; daily: { date: string; material_conflict: number; verbal_conflict: number; cooperation: number }[];
  priority_history: { as_of: string; priority: number; level: string; direction: string }[] };

export default function CrisisPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tab, setTab] = useState("overview");
  const [ver, setVer] = useState("");
  const d = useApi<Detail>(`/api/v1/crises/${id}`, { refreshInterval: 120_000 });
  const t = useApi<Timeline>(`/api/v1/crises/${id}/timeline${qs({ limit: 100, verification: ver })}`);
  const mapEvents = useApi<{ features: never[] }>(`/api/v1/events${qs({ view: "map", crisis: id, limit: 300, start: "2000-01-01" })}`);
  const [reviewMsg, setReviewMsg] = useState<string | null>(null);
  const { mode, ready } = useView();

  const review = async (action: string) => {
    setReviewMsg(null);
    try { await post(`/api/v1/crises/${id}/review`, { action }); setReviewMsg(`Saved: ${action}`); d.mutate(); }
    catch (e) { setReviewMsg(`Review failed: ${e instanceof Error ? e.message : e} (analyst API key required — see Settings)`); }
  };

  return (
    <State {...d} what="crisis">{(c) => {
      const p = c.priority_detail;
      const pts = (mapEvents.data?.features || []).map((f: { geometry: { coordinates: [number, number] } }) => f.geometry.coordinates);
      if (!ready) return <Loading what="crisis" />;
      if (mode !== "analyst" && c.story) return (
        <SimpleCrisis story={c.story} incidents={c.recent_incidents || []} spillover={c.spillover_risk} summary={c.summary}
          crisis={{ ...c.story, uid: c.uid }} events={mapEvents.data?.features} fitTo={pts.length ? pts : undefined} />
      );
      return (
        <div className="space-y-4">
          <div>
            <Link href="/geopolitics/crises" className="link text-sm">← Crises</Link>
            <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-xs text-muted">{c.uid} · {c.status} · {label(c.crisis_type)} · {c.region} · first observed {fmtDate(c.first_observed_at)}</div>
                <h1 className="text-xl font-semibold">{c.title}</h1>
                <div className="mt-2"><CountryChips countries={c.countries} /></div>
              </div>
              <div className="text-right">
                <div className="text-3xl font-semibold tabular-nums">{num(c.priority.score, 1)}</div>
                <LevelBadge level={c.priority.level} /> <Direction d={c.priority.direction} />
                <div className="mt-1"><Confidence label={c.evidence_confidence.label} score={c.evidence_confidence.score} /></div>
              </div>
            </div>
          </div>
          {c.needs_review && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warn/50 bg-warn/10 p-3 text-sm">
              ⚠ Some evidence was attached with ambiguous clustering scores and needs human review.
              <button className="btn" onClick={() => review("confirm")}>Confirm crisis</button>
              <button className="btn" onClick={() => review("clear_review")}>Clear review flag</button>
              <button className="btn" onClick={() => review("reject")}>Reject (not a crisis)</button>
              {reviewMsg && <span className="text-xs">{reviewMsg}</span>}
            </div>
          )}
          <Tabs active={tab} onChange={setTab} tabs={[{ id: "overview", label: "Overview" }, { id: "timeline", label: "Timeline & evidence" },
            { id: "participants", label: "Participants" }, { id: "sources", label: "Sources" }, { id: "analysis", label: "AI analysis" },
            { id: "history", label: "Detection history" }]} />

          {tab === "overview" && (
            <div className="grid gap-4 lg:grid-cols-3">
              <Card title="Why this priority" className="lg:col-span-2">
                {p ? (<>
                  <p className="mb-3 text-sm">{p.explanation}</p>
                  <ComponentBars components={p.components} />
                  <div className="mt-3 space-y-1">
                    {p.triggering_indicators.map((ti, i) => (
                      <div key={i} className="text-xs text-ink2"><span className="font-medium">{label(String(ti.indicator))}</span>{" "}
                        {Object.entries(ti).filter(([k]) => !["indicator", "value", "contribution"].includes(k)).map(([k, v]) => `${label(k)}: ${typeof v === "number" ? num(v, 2) : String(v)}`).join(" · ")}</div>
                    ))}
                  </div>
                  <Disclaimer>Algorithm {p.algorithm_version} · computed {fmtDate(p.computed_at)} · {p.independent_source_count} independent outlets.
                    Review priority — not a probability of conflict.</Disclaimer>
                </>) : <Empty>Not scored yet.</Empty>}
              </Card>
              <div className="space-y-4">
                <Card title="Location">
                  <WorldMap crises={[c]} events={mapEvents.data?.features} height={280} fitTo={pts.length ? pts : undefined} />
                </Card>
                {c.spillover_risk?.length > 0 && <SpilloverCard items={c.spillover_risk} />}
              </div>
              <Card title="Daily documented activity" className="lg:col-span-2">
                <State {...t} what="timeline">{(tl) => tl.daily.length ? <DailyStack data={tl.daily} /> : <Empty>No dated evidence.</Empty>}</State>
              </Card>
              <Card title="Review priority over time">
                <State {...t} what="history">{(tl) => tl.priority_history.length ? <PriorityHistory data={tl.priority_history} /> : <Empty>No history yet.</Empty>}</State>
              </Card>
              {c.summary && <Card title="Latest AI summary" className="lg:col-span-3"><p className="text-sm">{c.summary}</p>
                <Disclaimer>AI interpretation of unverified evidence.</Disclaimer></Card>}
            </div>
          )}

          {tab === "timeline" && (
            <Card title="Chronological evidence (deduplicated event clusters)" right={
              <select className="input" value={ver} onChange={(e) => setVer(e.target.value)} aria-label="Verification filter">
                <option value="">All reports</option><option value="multi_source_reported">Multi-source reported</option>
                <option value="machine_extracted">Single extraction (unverified)</option>
              </select>}>
              <State {...t} what="timeline">{(tl) => (<>
                <div className="mb-3 flex flex-wrap gap-3 text-xs">
                  {Object.entries(tl.verification_breakdown).map(([k, v]) => <span key={k}><Verification status={k} /> {v}</span>)}
                </div>
                <ul className="mb-2 text-[11px] text-muted">{Object.entries(tl.verification_legend).map(([k, v]) => <li key={k}><b>{label(k)}</b>: {v}</li>)}</ul>
                {tl.events.items.length === 0 ? <Empty>No evidence for this filter.</Empty> : <ul>{tl.events.items.map((e) => <ClusterRow key={e.id} c={e} />)}</ul>}
                <div className="text-xs text-muted">{tl.events.total} clusters</div>
              </>)}</State>
            </Card>
          )}

          {tab === "participants" && (
            <Card title="Participants (change over time as evidence accumulates)">
              <table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Participant</th><th>Type</th><th>Role</th><th>Event clusters</th><th>First seen</th></tr></thead>
                <tbody>{c.participants.map((x) => (
                  <tr key={x.key} className="border-t border-line"><td className="py-1.5">{x.iso3 ? <Link className="hover:underline" href={`/geopolitics/countries/${x.iso3}`}>{x.name}</Link> : x.name}</td>
                    <td className="text-xs">{x.type === "actor" ? label(x.actor_type) : "country"}</td><td className="text-xs">{x.role}</td>
                    <td className="tabular-nums">{x.event_clusters}</td><td className="text-xs text-muted">{ago(x.first_seen_at)}</td></tr>))}</tbody></table>
              <Disclaimer>A country listed as “location” is where events were reported, which does not imply its government’s involvement.</Disclaimer>
            </Card>
          )}

          {tab === "sources" && (
            <Card title="Related news coverage (headlines & links only)">
              {c.related_articles.length === 0 ? <Empty>No linked articles yet (news discovery runs every 15 minutes).</Empty> : (
                <ul className="space-y-2">{c.related_articles.map((a) => (
                  <li key={a.id} className="text-sm"><SourceLink url={a.url}>{a.title}</SourceLink>
                    <div className="text-[11px] text-muted">{a.domain} · {a.language} · {a.source_country} · {ago(a.seen_at)} · source-reported claim</div></li>))}</ul>
              )}
            </Card>
          )}

          {tab === "analysis" && <Card title="AI crisis analysis"><AnalysisPanel getPath={`/api/v1/crises/${id}/analysis`} runPath={`/api/v1/analysis/crises/${id}`} /></Card>}

          {tab === "history" && (
            <Card title="Why the system created and updated this crisis">
              <ul className="space-y-2">{c.evidence_log.map((e, i) => (
                <li key={i} className="border-b border-line pb-2 text-sm last:border-0">
                  <div className="text-[11px] text-muted">{fmtDate(e.created_at)} · {label(e.action)} · {e.algorithm_version}</div>{e.reason}
                  <details className="mt-1 text-xs text-muted"><summary className="cursor-pointer">evidence</summary>
                    <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(e.evidence, null, 1)}</pre></details>
                </li>))}</ul>
            </Card>
          )}
        </div>
      );
    }}</State>
  );
}
