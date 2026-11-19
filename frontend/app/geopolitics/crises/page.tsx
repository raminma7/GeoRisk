"use client";

import Link from "next/link";
import { useState } from "react";
import type { CrisisCard } from "@/components/domain";
import { Card, Direction, LevelBadge, State, Verification } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { ago, label, num } from "@/lib/format";

export default function CrisesPage() {
  const [f, setF] = useState({ status: "", country: "", sort: "priority", needs_review: "", min_priority: "" });
  const [offset, setOffset] = useState(0);
  const res = useApi<{ total: number; items: CrisisCard[] }>(`/api/v1/crises${qs({ ...f, limit: 50, offset })}`, { refreshInterval: 120_000 });
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Monitored crises</h1>
      <div className="flex flex-wrap gap-2">
        <select className="input" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} aria-label="Status">
          <option value="">Candidate, active & dormant</option>
          {["candidate", "active", "dormant", "resolved", "rejected"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input className="input w-32" placeholder="Country ISO3" value={f.country} onChange={(e) => setF({ ...f, country: e.target.value.toUpperCase() })} />
        <input className="input w-36" type="number" placeholder="Min priority" value={f.min_priority} onChange={(e) => setF({ ...f, min_priority: e.target.value })} />
        <select className="input" value={f.needs_review} onChange={(e) => setF({ ...f, needs_review: e.target.value })} aria-label="Review">
          <option value="">Any review state</option><option value="true">Needs review</option><option value="false">Reviewed / clear</option>
        </select>
        <select className="input" value={f.sort} onChange={(e) => setF({ ...f, sort: e.target.value })} aria-label="Sort">
          <option value="priority">Sort: review priority</option><option value="recent">Sort: latest evidence</option>
          <option value="first_observed">Sort: newest</option>
        </select>
      </div>
      <Card>
        <State {...res} what="crises" empty={(d) => d.items.length === 0}>{(d) => (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted"><tr>
                  <th className="py-2">Situation</th><th>Countries</th><th>Priority</th><th>Trend</th><th>Evidence</th><th>Last evidence</th></tr></thead>
                <tbody>
                  {d.items.map((c) => (
                    <tr key={c.id} className="border-t border-line align-top">
                      <td className="py-2 pr-3"><Link className="font-medium hover:underline" href={`/geopolitics/crises/${c.id}`}>{c.title}</Link>
                        <div className="text-[11px] text-muted">{c.uid} · {c.status} · {label(c.crisis_type)} · {c.region}{c.needs_review ? " · needs review" : ""}</div></td>
                      <td className="pr-3 text-xs">{c.countries.map((x) => x.iso3).join(", ")}</td>
                      <td className="pr-3"><span className="mr-1 tabular-nums">{num(c.priority.score, 1)}</span><LevelBadge level={c.priority.level} /></td>
                      <td className="pr-3"><Direction d={c.priority.direction} /></td>
                      <td className="pr-3"><Verification status={c.verification_status} /><div className="text-[11px] text-muted">{c.event_cluster_count} clusters</div></td>
                      <td className="text-xs text-muted">{ago(c.last_event_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex items-center justify-between text-sm">
              <span className="text-muted">{d.total} crises</span>
              <div className="flex gap-2">
                <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>← Prev</button>
                <button className="btn" disabled={offset + 50 >= d.total} onClick={() => setOffset(offset + 50)}>Next →</button>
              </div>
            </div>
          </>
        )}</State>
      </Card>
    </div>
  );
}
