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
