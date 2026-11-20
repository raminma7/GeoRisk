"use client";

/* Admin panel: sign in with email + password, then edit Settings. */

import { FormEvent, useEffect, useState } from "react";
import { Card, ErrorBox, State } from "@/components/ui";
import { getToken, onTokenChange, post, put, setToken, useApi } from "@/lib/api";
import { ago } from "@/lib/format";

type Settings = { editable: Record<string, unknown>; credentials: Record<string, string>;
  ai: { provider: string; configured: boolean; reason: string | null; model: string | null };
  feature_flags: Record<string, boolean>; schedules_minutes: Record<string, number> };
type Status = { sources: { key: string; name: string; enabled: boolean; last_success_at: string | null; stale: boolean | null;
  failed_runs_24h: number; last_run: { status: string; error: string | null } | null }[];
  workers: { host: string; healthy: boolean; last_heartbeat_seconds_ago: number }[]; redis_ok: boolean };

type TrackEntry = { horizon_months: number; direction: "worse" | "better"; windows_checked: number; windows_pending: number;
  checked?: number; happened?: number; said?: number; said_and_happened?: number; hit_rate?: number | null;
  caught?: number | null; base_rate?: number | null; roc_auc?: number; test_roc_auc?: number | null };
type Retrain = { served_version: string | null; recipe: string; replaced: boolean; reason: string; new_version?: string;
  served_recent: { roc_auc?: number } | null; new_recent: { roc_auc?: number } };
type Quality = { track_record: { computed_at?: string; models?: Record<string, TrackEntry> };
  retraining: { last?: { run_at: string; models: Record<string, Retrain> } };
  snapshots: { days: number; since: string | null; needed_for_training: number } };

const pctText = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const MODEL_LABEL: Record<string, string> = { crisis_escalation_3m: "Worse · 3 months", crisis_escalation_6m: "Worse · 6 months",
  crisis_calming_3m: "Better · 3 months", crisis_calming_6m: "Better · 6 months" };

/* How well the outlook models do: past predictions checked against what happened, the last monthly retraining and
   how much of the site's own daily data has been collected for future training. */
function ModelQuality() {
  const q = useApi<Quality>("/api/v1/settings/model-quality");
  return (
    <Card title="Outlook model quality">
      <State {...q} what="model quality">{(d) => {
        const tr = d.track_record.models || {};
        const rt = d.retraining.last;
        return (<div className="space-y-4 text-sm">
          <div>
            <h3 className="mb-1 font-medium">Track record (past predictions vs what happened)</h3>
            {Object.keys(tr).length === 0 ? <p className="text-muted">Not computed yet (runs monthly, or <code>python -m app.cli track-record</code>).</p> : (
              <div className="overflow-x-auto"><table className="w-full text-left text-xs">
                <thead className="text-muted"><tr><th className="py-1 pr-3">Model</th><th className="pr-3">Months checked</th>
                  <th className="pr-3">Said “could change” → it did</th><th className="pr-3">Changes caught</th><th className="pr-3">ROC-AUC live / test</th></tr></thead>
                <tbody>{Object.entries(tr).map(([k, e]) => (
                  <tr key={k} className="border-t border-line">
                    <td className="py-1 pr-3">{MODEL_LABEL[k] || k}</td>
                    <td className="pr-3">{e.windows_checked}{e.windows_pending ? ` (+${e.windows_pending} waiting for data)` : ""}</td>
                    <td className="pr-3">{e.said ? `${e.said_and_happened} of ${e.said} (${pctText(e.hit_rate)}; normal ${pctText(e.base_rate)})` : "–"}</td>
                    <td className="pr-3">{e.happened ? `${pctText(e.caught)} of ${e.happened}` : "–"}</td>
                    <td className="pr-3">{e.roc_auc ?? "–"} / {e.test_roc_auc ?? "–"}</td>
                  </tr>))}</tbody>
              </table></div>)}
          </div>
          <div>
            <h3 className="mb-1 font-medium">Last monthly retraining{rt ? ` · ${ago(rt.run_at)}` : ""}</h3>
            {!rt ? <p className="text-muted">Not run yet (runs monthly, or <code>python -m app.cli retrain</code>).</p> : (
              <ul className="space-y-0.5 text-xs">{Object.entries(rt.models).map(([k, e]) => (
                <li key={k}>{e.replaced ? "✓ replaced" : "· kept"} {MODEL_LABEL[k] || k}: {e.reason}</li>))}</ul>)}
          </div>
          <p className="text-xs text-muted">Daily country snapshots collected: {d.snapshots.days} days{d.snapshots.since ? ` since ${d.snapshots.since}` : ""}.
            They are tried as extra model inputs once {d.snapshots.needed_for_training} days exist.</p>
        </div>);
      }}</State>
    </Card>
  );
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      const r = await post<{ token: string }>("/api/v1/auth/login", { email, password });
      setToken(r.token);
    } catch (e2) { setErr(errText(e2)); } finally { setBusy(false); }
  };
  return (
    <div className="mx-auto mt-10 max-w-sm">
      <form onSubmit={submit} className="space-y-4 rounded-2xl border border-line bg-surface p-6">
        <div>
          <h1 className="text-xl font-semibold">Admin sign-in</h1>
          <p className="text-sm text-muted">Only the administrator can change settings.</p>
        </div>
        <label className="block text-sm">Email
          <input className="input mt-1 w-full" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block text-sm">Password
          <input className="input mt-1 w-full" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {err && <p className="text-sm text-crit">{err}</p>}
        <button className="btn-primary w-full justify-center" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}

function ChangePassword() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setMsg(null);
    try {
      const r = await post<{ message: string }>("/api/v1/auth/change-password", { current_password: cur, new_password: next });
      setMsg({ ok: true, text: r.message });
      setTimeout(() => setToken(""), 1500);
    } catch (e2) { setMsg({ ok: false, text: errText(e2) }); }
  };
  return (
    <Card title="Change password">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <label className="text-sm">Current password<input className="input mt-1 block" type="password" required value={cur} onChange={(e) => setCur(e.target.value)} /></label>
        <label className="text-sm">New password (8+ characters)<input className="input mt-1 block" type="password" minLength={8} required value={next} onChange={(e) => setNext(e.target.value)} /></label>
        <button className="btn-primary">Change</button>
        {msg && <span className={`text-sm ${msg.ok ? "text-good" : "text-crit"}`}>{msg.text}</span>}
      </form>
    </Card>
  );
}

function Panel() {
  const me = useApi<{ email: string }>("/api/v1/auth/me");
  const s = useApi<Settings>("/api/v1/settings");
  const st = useApi<Status>("/api/v1/monitoring/status", { refreshInterval: 60_000 });
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
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
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Admin · Settings</h1>
          <p className="text-sm text-muted">Signed in as {me.data?.email || "…"}</p>
        </div>
        <button className="btn" onClick={() => setToken("")}>Log out</button>
      </div>
      <State {...s} what="settings">{(d) => (<>
        <Card title="Watchlist, thresholds & monitoring parameters">
          {err ? <ErrorBox error={err} /> : null}
          <div className="space-y-2">{Object.keys(d.editable).map((k) => (
            <div key={k} className="flex flex-wrap items-center gap-2">
              <label className="w-56 text-sm" htmlFor={k}>{k}</label>
              <input id={k} className="input min-w-[280px] flex-1 font-mono text-xs" value={drafts[k] ?? ""} onChange={(e) => setDrafts({ ...drafts, [k]: e.target.value })} />
              <button className="btn" onClick={() => save(k)}>Save</button>{saved === k && <span className="text-xs text-good">saved</span>}
            </div>))}</div>
          <p className="mt-2 text-xs text-muted">Values are JSON (e.g. the watchlist is a list of ISO3 codes: [&quot;UKR&quot;, &quot;SDN&quot;]). Changes are logged.</p>
        </Card>
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="AI provider"><div className="text-sm">{d.ai.provider} · {d.ai.configured ? `configured (${d.ai.model})` : `not configured — ${d.ai.reason}`}</div></Card>
          <Card title="Credentials">{Object.entries(d.credentials).map(([k, v]) => <div key={k} className="text-sm">{k}: <span className={v === "configured" ? "text-good" : "text-muted"}>{v.replace("_", " ")}</span></div>)}
            <p className="mt-1 text-xs text-muted">Provider keys are set in the server&apos;s .env file and never shown.</p></Card>
          <Card title="Schedules">{Object.entries(d.schedules_minutes).map(([k, v]) => <div key={k} className="text-sm">{k}: every {v} min</div>)}</Card>
        </div>
      </>)}</State>
      <Card title="Data collection health">
        <State {...st} what="status">{(d) => (<>
          <ul className="space-y-1 text-sm">{d.sources.filter((x) => x.enabled).map((x) => (
            <li key={x.key}>{x.stale ? "⚠" : "●"} {x.name} · last success {ago(x.last_success_at)}
              {x.failed_runs_24h ? <span className="text-serious"> · {x.failed_runs_24h} failed runs (24h)</span> : null}</li>))}</ul>
          <div className="mt-2 text-xs text-muted">
            Workers: {d.workers.length ? d.workers.map((w) => `${w.healthy ? "healthy" : "unhealthy"} (${w.host})`).join(", ") : "no heartbeat"} · Redis {d.redis_ok ? "reachable" : "unreachable"}
          </div>
        </>)}</State>
      </Card>
      <ModelQuality />
      <ChangePassword />
    </div>
  );
}

export default function AdminPage() {
  const [ready, setReady] = useState(false);
  const [signed, setSigned] = useState(false);
  useEffect(() => {
    const f = () => setSigned(Boolean(getToken()));
    f(); setReady(true);
    return onTokenChange(f);
  }, []);
  if (!ready) return null;
  return signed ? <Panel /> : <Login />;
}
