"use client";

import { useState } from "react";
import { ClusterRow, type Cluster } from "@/components/domain";
import { Card, SourceLink, State, Tabs, Verification } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { ago, label } from "@/lib/format";

type Article = { id: number; title: string; url: string; domain: string; language: string; source_country: string; seen_at: string;
  topic: string | null; countries: string[] };
type NewsCluster = Cluster & { articles: Article[] };
type Ev = { id: number; event_date: string; category_label: string; actor1: { name: string; country: string }; actor2: { name: string; country: string };
  location: { name: string }; source_url: string; verification_status: string; num_sources: number };

export default function NewsPage() {
  const [tab, setTab] = useState("clusters");
  const [f, setF] = useState({ q: "", country: "", region: "", topic: "", source: "", language: "", start: "", end: "", crisis: "",
    category: "", verification: "" });
  const [offset, setOffset] = useState(0);
  const regions = useApi<{ items: { code: string; name: string }[] }>("/api/v1/regions?kind=un_subregion");
  const crises = useApi<{ items: { id: number; title: string }[] }>("/api/v1/crises?limit=200");
  const newsQ = { q: f.q, country: f.country, region: f.region, topic: f.topic, source: f.source, language: f.language, start: f.start,
    end: f.end, crisis: f.crisis, limit: 50, offset };
  const evQ = { country: f.country, region: f.region, category: f.category, verification: f.verification, start: f.start, end: f.end,
    crisis: f.crisis, limit: 50, offset };
  const arts = useApi<{ total: number; items: Article[] }>(tab === "articles" ? `/api/v1/news${qs(newsQ)}` : null);
  const clusters = useApi<{ total: number; items: NewsCluster[] }>(tab === "clusters" ? `/api/v1/news${qs({ ...newsQ, clustered: true })}` : null);
  const events = useApi<{ total: number; items: Ev[] }>(tab === "events" ? `/api/v1/events${qs(evQ)}` : null);
  const evClusters = useApi<{ total: number; items: Cluster[] }>(tab === "event_clusters" ? `/api/v1/events${qs({ ...evQ, view: "clusters" })}` : null);
  const set = (k: string, v: string) => { setF({ ...f, [k]: v }); setOffset(0); };
  const pager = (total: number) => (
    <div className="mt-3 flex items-center justify-between text-sm"><span className="text-muted">{total} results</span>
      <div className="flex gap-2"><button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>← Prev</button>
        <button className="btn" disabled={offset + 50 >= total} onClick={() => setOffset(offset + 50)}>Next →</button></div></div>);
  const isNews = tab === "articles" || tab === "clusters";

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">News & Events explorer</h1>
        <p className="text-sm text-muted">News metadata and links discovered via GDELT DOC (not an exhaustive firehose); events machine-extracted by GDELT. Full text stays with publishers.</p>
      </div>
      <Tabs active={tab} onChange={(t) => { setTab(t); setOffset(0); }} tabs={[{ id: "clusters", label: "News clusters (deduplicated)" },
        { id: "articles", label: "Articles" }, { id: "event_clusters", label: "Event clusters" }, { id: "events", label: "Raw normalized events" }]} />
      <div className="flex flex-wrap gap-2">
        {isNews && <input className="input w-56" placeholder="Search headlines" value={f.q} onChange={(e) => set("q", e.target.value)} />}
        <input className="input w-28" placeholder="Country ISO3" value={f.country} maxLength={3} onChange={(e) => set("country", e.target.value.toUpperCase())} />
        <select className="input" value={f.region} onChange={(e) => set("region", e.target.value)} aria-label="Region">
          <option value="">All regions</option>{(regions.data?.items || []).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}</select>
        {isNews ? (<>
          <select className="input" value={f.topic} onChange={(e) => set("topic", e.target.value)} aria-label="Topic">
            <option value="">All topics</option>{["armed_conflict", "diplomacy", "de_escalation", "instability"].map((t) => <option key={t} value={t}>{label(t)}</option>)}</select>
          <input className="input w-36" placeholder="Source domain" value={f.source} onChange={(e) => set("source", e.target.value)} />
          <input className="input w-28" placeholder="Language" value={f.language} onChange={(e) => set("language", e.target.value)} />
        </>) : (<>
          <select className="input" value={f.category} onChange={(e) => set("category", e.target.value)} aria-label="Event type">
            <option value="">All event types</option>
            {["fight", "assault", "mass_violence", "coerce", "force_posture", "threaten", "protest", "reduce_relations", "demand", "consult", "diplomatic_cooperation", "yield"].map((x) => <option key={x} value={x}>{label(x)}</option>)}</select>
          <select className="input" value={f.verification} onChange={(e) => set("verification", e.target.value)} aria-label="Verification">
            <option value="">Any verification</option><option value="multi_source_reported">Multi-source reported</option><option value="machine_extracted">Single extraction</option></select>
        </>)}
        <input className="input" type="date" value={f.start} onChange={(e) => set("start", e.target.value)} aria-label="From" />
        <input className="input" type="date" value={f.end} onChange={(e) => set("end", e.target.value)} aria-label="To" />
        <select className="input max-w-xs" value={f.crisis} onChange={(e) => set("crisis", e.target.value)} aria-label="Crisis">
          <option value="">Any crisis</option>{(crises.data?.items || []).map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}</select>
      </div>

      {tab === "clusters" && <Card><State {...clusters} what="news clusters" empty={(d) => d.items.length === 0}>{(d) => (<>
        <ul className="space-y-3">{d.items.map((c) => (
          <li key={c.id} className="border-b border-line pb-3 last:border-0">
            <div className="text-sm font-medium">{c.title}</div>
            <div className="text-[11px] text-muted">{c.reports} articles · {c.independent_sources} outlets · {c.countries?.join(", ")} · last seen {ago(c.last_seen_at)}</div>
            <ul className="mt-1 space-y-0.5 pl-3">{c.articles.map((a) => <li key={a.id} className="text-xs"><SourceLink url={a.url}>{a.domain}</SourceLink>
              <span className="text-muted"> · {a.language} · {a.source_country}</span></li>)}</ul>
          </li>))}</ul>{pager(d.total)}</>)}</State></Card>}

      {tab === "articles" && <Card><State {...arts} what="articles" empty={(d) => d.items.length === 0}>{(d) => (<>
        <ul className="space-y-2">{d.items.map((a) => (
          <li key={a.id} className="text-sm"><SourceLink url={a.url}>{a.title}</SourceLink>
            <div className="text-[11px] text-muted">{a.domain} · {a.language} · outlet country: {a.source_country || "—"} · about: {a.countries.join(", ") || "—"} · {label(a.topic)} · {ago(a.seen_at)}</div></li>))}</ul>
        {pager(d.total)}</>)}</State></Card>}

      {tab === "event_clusters" && <Card><State {...evClusters} what="event clusters" empty={(d) => d.items.length === 0}>{(d) => (<>
        <ul>{d.items.map((c) => <ClusterRow key={c.id} c={c} />)}</ul>{pager(d.total)}</>)}</State></Card>}

      {tab === "events" && <Card><State {...events} what="events" empty={(d) => d.items.length === 0}>{(d) => (<>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Date</th><th>Type</th><th>Actors</th><th>Location</th><th>Sources</th><th>Status</th><th /></tr></thead>
          <tbody>{d.items.map((e) => (
            <tr key={e.id} className="border-t border-line"><td className="py-1.5 text-xs">{e.event_date}</td><td>{e.category_label}</td>
              <td className="text-xs">{e.actor1.name || "—"} ({e.actor1.country || "?"}) → {e.actor2.name || "—"} ({e.actor2.country || "?"})</td>
              <td className="text-xs">{e.location.name || "—"}</td><td className="tabular-nums">{e.num_sources}</td>
              <td><Verification status={e.verification_status} /></td><td><SourceLink url={e.source_url} /></td></tr>))}</tbody></table></div>
        {pager(d.total)}</>)}</State></Card>}
    </div>
  );
}
