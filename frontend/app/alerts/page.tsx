"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, ErrorBox, LevelBadge, SourceLink, State, Verification } from "@/components/ui";
import { post, qs, useApi } from "@/lib/api";
import { fmtDate, label } from "@/lib/format";

type Alert = { id: number; type: string; severity: string; title: string; message: string; crisis_id: number | null; crisis_uid: string | null;
  status: string; requires_review: boolean; created_at: string; reviewed_by: string | null;
  evidence: { triggering_indicators?: Record<string, unknown>[]; supporting_event_clusters?: { event_cluster_id: number; title: string;
    event_date: string; verification_status: string; independent_sources: number; example_source_url: string | null }[];
    independent_source_count?: number; evidence_confidence_label?: string; algorithm_version?: string; caveat?: string } };

export default function AlertsPage() {
  const [f, setF] = useState({ status: "open", type: "", requires_review: "" });
  const res = useApi<{ total: number; items: Alert[] }>(`/api/v1/monitoring/alerts${qs({ ...f, limit: 100 })}`, { refreshInterval: 60_000 });
  const [err, setErr] = useState<unknown>(null);
  const review = async (id: number, status: string) => {
    setErr(null);
    try { await post(`/api/v1/monitoring/alerts/${id}/review`, { status }); res.mutate(); } catch (e) { setErr(e); }
  };
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Monitoring alerts</h1>
      <p className="text-sm text-muted">Generated automatically when configured thresholds are crossed. Every alert carries its supporting evidence. Major alerts require human review.</p>
      <div className="flex flex-wrap gap-2">
        <select className="input" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} aria-label="Status">
          <option value="">Any status</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option><option value="dismissed">Dismissed</option></select>
        <select className="input" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} aria-label="Type">
          <option value="">All types</option>{["new_crisis", "priority_threshold", "escalation", "de_escalation"].map((t) => <option key={t} value={t}>{label(t)}</option>)}</select>
        <select className="input" value={f.requires_review} onChange={(e) => setF({ ...f, requires_review: e.target.value })} aria-label="Review">
          <option value="">Any</option><option value="true">Requires review</option></select>
      </div>
      {err ? <ErrorBox error={`${err instanceof Error ? err.message : err} — reviewing requires an analyst API key (Settings)`} /> : null}
      <State {...res} what="alerts" empty={(d) => d.items.length === 0}>{(d) => (
        <div className="space-y-3">{d.items.map((a) => (
          <Card key={a.id}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted"><LevelBadge level={a.severity} />{label(a.type)} · {fmtDate(a.created_at)} · {a.status}
                  {a.requires_review && <span className="rounded bg-warn/20 px-1.5 text-ink">requires review</span>}</div>
                <h3 className="mt-1 font-medium">{a.crisis_id ? <Link className="hover:underline" href={`/geopolitics/crises/${a.crisis_id}`}>{a.title}</Link> : a.title}</h3>
              </div>
              {a.status === "open" && <div className="flex gap-2"><button className="btn" onClick={() => review(a.id, "acknowledged")}>Acknowledge</button>
                <button className="btn" onClick={() => review(a.id, "dismissed")}>Dismiss</button></div>}
            </div>
            <p className="mt-2 text-sm">{a.message}</p>
            {a.evidence?.supporting_event_clusters && (
              <details className="mt-2"><summary className="cursor-pointer text-sm text-accent">Supporting evidence ({a.evidence.supporting_event_clusters.length} clusters · {a.evidence.independent_source_count} outlets · confidence {a.evidence.evidence_confidence_label})</summary>
                <ul className="mt-2 space-y-1">{a.evidence.supporting_event_clusters.map((c) => (
                  <li key={c.event_cluster_id} className="flex flex-wrap items-center gap-2 text-xs"><span className="text-muted">{c.event_date}</span>{c.title}
                    <Verification status={c.verification_status} /><SourceLink url={c.example_source_url} /></li>))}</ul>
                <p className="mt-1 text-[11px] text-muted">{a.evidence.caveat} Algorithm {a.evidence.algorithm_version}.</p>
              </details>
            )}
          </Card>))}</div>
      )}</State>
    </div>
  );
}
