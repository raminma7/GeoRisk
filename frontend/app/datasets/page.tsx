"use client";

import { useState } from "react";
import { Card, Disclaimer, ErrorBox, State } from "@/components/ui";
import { API_URL, getApiKey, post, useApi } from "@/lib/api";
import { fmtDate, num } from "@/lib/format";

type Inventory = { record_counts: Record<string, number>; dataset_versions: { source: string; version: string; released_at: string; records: number; citation: string }[];
  feature_snapshots: { feature_set_version: string; entity_type: string; count: number; first_as_of: string; last_as_of: string }[];
  training_datasets: { id: number; name: string; version: string; rows: number; format: string; created_at: string; download: string | null; licensing: Record<string, unknown> }[];
  export_kinds: Record<string, { sources: string[]; ml: boolean }> };
type Metrics = { roc_auc?: number; pr_auc?: number; brier?: number; base_rate?: number; n?: number };
type Models = { items: { id: number; name: string; version: string; algorithm: string; status: string; notes: string; created_at: string;
  evaluations: { fold: number; test: string[]; metrics: Metrics; baselines: Record<string, Metrics> }[] }[]; disclaimer: string };

export default function DatasetsPage() {
  const inv = useApi<Inventory>("/api/v1/datasets");
  const models = useApi<Models>("/api/v1/models");
  const [exp, setExp] = useState({ kind: "events", format: "parquet", start: "", end: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const run = async () => {
    setMsg(null); setErr(null);
    try {
      const r = await post<{ rows: number; id: number }>("/api/v1/datasets/export", { ...exp, start: exp.start || null, end: exp.end || null });
      setMsg(`Export #${r.id} created with ${num(r.rows)} rows.`); inv.mutate();
    } catch (e) { setErr(e); }
  };
  const download = async (path: string) => {
    const res = await fetch(`${API_URL}${path}`, { headers: { "X-API-Key": getApiKey() } });
    if (!res.ok) { setErr(new Error(`download failed (${res.status}) — analyst API key required`)); return; }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = path.split("/").slice(-2, -1)[0] + "." + exp.format; a.click();
  };
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Datasets & Models</h1>
      <State {...inv} what="dataset inventory">{(d) => (<>
        <Card title="Historical data retained">
          <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-5">
            {Object.entries(d.record_counts).map(([k, v]) => <div key={k}><div className="text-xs text-muted">{k.replace(/_/g, " ")}</div><div className="tabular-nums">{num(v)}</div></div>)}
          </div>
        </Card>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Versioned source datasets">
            {d.dataset_versions.length === 0 ? <p className="text-sm text-muted">No versioned imports yet (run <code>python -m app.cli import-ucdp</code>).</p> :
              <ul className="space-y-2 text-sm">{d.dataset_versions.map((v) => <li key={v.source + v.version}><b>{v.source} {v.version}</b> · {num(v.records)} records · released {fmtDate(v.released_at, false)}
                <div className="text-[11px] text-muted">{v.citation}</div></li>)}</ul>}
          </Card>
          <Card title="Point-in-time feature snapshots">
            {d.feature_snapshots.length === 0 ? <p className="text-sm text-muted">No snapshots yet (built daily).</p> :
              <table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th>Feature set</th><th>Entity</th><th>Count</th><th>Range</th></tr></thead>
                <tbody>{d.feature_snapshots.map((s) => <tr key={s.feature_set_version + s.entity_type} className="border-t border-line"><td className="py-1">{s.feature_set_version}</td><td>{s.entity_type}</td>
                  <td className="tabular-nums">{num(s.count)}</td><td className="text-xs">{fmtDate(s.first_as_of, false)} – {fmtDate(s.last_as_of, false)}</td></tr>)}</tbody></table>}
          </Card>
        </div>
        <Card title="Export (CSV / Parquet, licensing-checked)">
          <div className="flex flex-wrap gap-2">
            <select className="input" value={exp.kind} onChange={(e) => setExp({ ...exp, kind: e.target.value })} aria-label="Dataset">
              {Object.entries(d.export_kinds).map(([k, v]) => <option key={k} value={k}>{k} ({v.sources.join(", ")})</option>)}</select>
            <select className="input" value={exp.format} onChange={(e) => setExp({ ...exp, format: e.target.value })} aria-label="Format">
              <option value="parquet">Parquet</option><option value="csv">CSV</option></select>
            <input className="input" type="date" value={exp.start} onChange={(e) => setExp({ ...exp, start: e.target.value })} aria-label="From" />
            <input className="input" type="date" value={exp.end} onChange={(e) => setExp({ ...exp, end: e.target.value })} aria-label="To" />
            <button className="btn-primary" onClick={run}>Create export</button>
          </div>
          {msg && <p className="mt-2 text-sm text-good">{msg}</p>}
          {err ? <div className="mt-2"><ErrorBox error={err} /></div> : null}
          <Disclaimer>Exports are refused when a source’s licence does not permit export or ML use (ACLED is never exported). News exports contain metadata and links only. Requires an analyst API key.</Disclaimer>
          {d.training_datasets.length > 0 && (
            <table className="mt-3 w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th>Name</th><th>Version</th><th>Rows</th><th>Created</th><th /></tr></thead>
              <tbody>{d.training_datasets.map((t) => <tr key={t.id} className="border-t border-line"><td className="py-1">{t.name}</td><td className="text-xs">{t.version}</td>
                <td className="tabular-nums">{num(t.rows)}</td><td className="text-xs">{fmtDate(t.created_at)}</td>
                <td>{t.download && <button className="link text-xs" onClick={() => download(t.download!)}>download</button>}</td></tr>)}</tbody></table>
          )}
        </Card>
      </>)}</State>
      <Card title="Model evaluations (experimental)">
        <State {...models} what="models" empty={(m) => m.items.length === 0}>{(m) => (<>
          {m.items.map((mv) => (
            <div key={mv.id} className="mb-4">
              <div className="text-sm font-medium">{mv.name} <span className="text-xs text-muted">{mv.version} · {mv.algorithm} · {mv.status}</span></div>
              <div className="overflow-x-auto"><table className="mt-1 w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Out-of-time test</th><th>n</th><th>Base rate</th>
                <th>ROC AUC (model / persistence)</th><th>PR AUC (model / persistence)</th><th>Brier (model / persistence / base rate)</th></tr></thead>
                <tbody>{mv.evaluations.map((e) => (
                  <tr key={e.fold} className="border-t border-line tabular-nums"><td className="py-1 text-xs">{e.test[0]} → {e.test[1]}</td><td>{num(e.metrics.n)}</td><td>{num(e.metrics.base_rate, 3)}</td>
                    <td>{num(e.metrics.roc_auc, 3)} / {num(e.baselines.persistence?.roc_auc, 3)}</td><td>{num(e.metrics.pr_auc, 3)} / {num(e.baselines.persistence?.pr_auc, 3)}</td>
                    <td>{num(e.metrics.brier, 4)} / {num(e.baselines.persistence?.brier, 4)} / {num(e.baselines.base_rate?.brier, 4)}</td></tr>))}</tbody></table></div>
              <p className="text-[11px] text-muted">{mv.notes}</p>
            </div>))}
          <Disclaimer>{m.disclaimer}</Disclaimer></>)}
        </State>
      </Card>
    </div>
  );
}
