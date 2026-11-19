"use client";

import { useEffect, useState } from "react";
import { Card, ErrorBox, State } from "@/components/ui";
import { getApiKey, put, setApiKey, useApi } from "@/lib/api";
import { ago } from "@/lib/format";

type Settings = { editable: Record<string, unknown>; credentials: Record<string, string>; ai: { provider: string; configured: boolean; reason: string | null; model: string | null };
  feature_flags: Record<string, boolean>; schedules_minutes: Record<string, number>; your_role: string; note: string };
type Status = { sources: { key: string; name: string; module: string; enabled: boolean; license: string; last_success_at: string | null; stale: boolean | null;
  batches: Record<string, number>; failed_runs_24h: number; last_run: { status: string; error: string | null; records_inserted: number } | null;
  permissions: Record<string, boolean> | null }[]; delayed_or_failed_batches: { batch: string; status: string; attempts: number; error: string | null }[];
  workers: { host: string; healthy: boolean; last_heartbeat_seconds_ago: number }[]; redis_ok: boolean };

export default function SettingsPage() {
  const s = useApi<Settings>("/api/v1/settings");
  const st = useApi<Status>("/api/v1/monitoring/status", { refreshInterval: 60_000 });
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  useEffect(() => setKey(getApiKey()), []);
  useEffect(() => {
    if (s.data) setDrafts(Object.fromEntries(Object.entries(s.data.editable).map(([k, v]) => [k, JSON.stringify(v)])));
  }, [s.data]);
  const save = async (k: string) => {
    setErr(null); setSaved(null);
    try { await put(`/api/v1/settings/${k}`, { value: JSON.parse(drafts[k]) }); setSaved(k); s.mutate(); }
    catch (e) { setErr(e instanceof SyntaxError ? new Error(`${k}: invalid JSON`) : e); }
  };
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Settings</h1>
      <Card title="API access">
        <div className="flex flex-wrap items-center gap-2">
          <input className="input w-80" type="password" placeholder="X-API-Key (analyst or admin)" value={key} onChange={(e) => setKey(e.target.value)} />
          <button className="btn-primary" onClick={() => { setApiKey(key); s.mutate(); }}>Save key in this browser</button>
          <button className="btn" onClick={() => { setApiKey(""); setKey(""); s.mutate(); }}>Clear</button>
          {s.data && <span className="text-sm text-muted">Current role: <b>{s.data.your_role}</b></span>}
        </div>
        <p className="mt-2 text-xs text-muted">Keys are issued via the API_KEYS environment variable. The key is stored only in this browser. Provider API keys (Anthropic, OpenAI, ACLED) are configured server-side and never shown.</p>
      </Card>
      <State {...s} what="settings">{(d) => (<>
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="AI provider"><div className="text-sm">{d.ai.provider} · {d.ai.configured ? `configured (${d.ai.model})` : `not configured — ${d.ai.reason}`}</div></Card>
          <Card title="Credentials">{Object.entries(d.credentials).map(([k, v]) => <div key={k} className="text-sm">{k}: <span className={v === "configured" ? "text-good" : "text-muted"}>{v.replace("_", " ")}</span></div>)}</Card>
          <Card title="Feature flags & schedules">
            {Object.entries(d.feature_flags).map(([k, v]) => <div key={k} className="text-sm">{k}: {v ? "on" : "off"}</div>)}
            {Object.entries(d.schedules_minutes).map(([k, v]) => <div key={k} className="text-xs text-muted">{k}: every {v} min</div>)}
          </Card>
        </div>
        <Card title="Watchlist, thresholds & monitoring parameters (admin)">
          {err ? <ErrorBox error={err} /> : null}
          <div className="space-y-2">{Object.keys(d.editable).map((k) => (
            <div key={k} className="flex flex-wrap items-center gap-2">
              <label className="w-56 text-sm" htmlFor={k}>{k}</label>
              <input id={k} className="input min-w-[280px] flex-1 font-mono text-xs" value={drafts[k] ?? ""} onChange={(e) => setDrafts({ ...drafts, [k]: e.target.value })} />
              <button className="btn" onClick={() => save(k)}>Save</button>{saved === k && <span className="text-xs text-good">saved</span>}
            </div>))}</div>
          <p className="mt-2 text-xs text-muted">Values are JSON. Changes are audited. Economy stays “planned” even if its flag is enabled, until the module is implemented.</p>
        </Card>
      </>)}</State>
      <div id="status" />
      <Card title="Data sources & ingestion health">
        <State {...st} what="status">{(d) => (<>
          <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Source</th><th>Module</th><th>Enabled</th><th>Last success</th><th>Last run</th><th>Batches</th><th>Permissions</th></tr></thead>
            <tbody>{d.sources.map((x) => (
              <tr key={x.key} className="border-t border-line align-top"><td className="py-1.5">{x.name}<div className="max-w-xs text-[11px] text-muted">{x.license}</div></td>
                <td className="text-xs">{x.module}</td><td className="text-xs">{x.enabled ? "yes" : "no"}</td>
                <td className="text-xs">{x.stale ? <span className="text-serious">⚠ </span> : null}{ago(x.last_success_at)}</td>
                <td className="text-xs">{x.last_run ? `${x.last_run.status}${x.last_run.error ? ` — ${x.last_run.error.slice(0, 80)}` : ""}` : "—"}{x.failed_runs_24h ? <div className="text-serious">{x.failed_runs_24h} failed (24h)</div> : null}</td>
                <td className="text-xs">{Object.entries(x.batches).map(([k, v]) => `${k}: ${v}`).join(", ") || "—"}</td>
                <td className="text-[11px]">{x.permissions ? Object.entries(x.permissions).filter(([, v]) => v).map(([k]) => k.replace("allow_", "")).join(", ") : "—"}</td></tr>))}</tbody></table></div>
          <h3 className="mt-3 text-xs font-semibold text-muted">Delayed or failed batches (auto-recovered)</h3>
          {d.delayed_or_failed_batches.length === 0 ? <p className="text-sm text-good">None</p> :
            <ul className="text-xs">{d.delayed_or_failed_batches.map((b) => <li key={b.batch}>{b.batch} · {b.status} · {b.attempts} attempts {b.error ? `· ${b.error}` : ""}</li>)}</ul>}
          <h3 className="mt-3 text-xs font-semibold text-muted">Workers</h3>
          {d.workers.length === 0 ? <p className="text-sm text-serious">⚠ No worker heartbeat. Start the Celery worker and beat services.</p> :
            d.workers.map((w) => <div key={w.host} className="text-sm">{w.healthy ? "● healthy" : "⚠ unhealthy"} · {w.host} · heartbeat {Math.round(w.last_heartbeat_seconds_ago)}s ago</div>)}
          <div className="text-xs text-muted">Redis {d.redis_ok ? "reachable" : "unreachable"}</div>
        </>)}</State>
      </Card>
    </div>
  );
}
