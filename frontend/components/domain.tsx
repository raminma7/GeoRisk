"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, post, useApi } from "@/lib/api";
import { ago, fmtDate, label, num } from "@/lib/format";
import { Confidence, CountryChips, Direction, Empty, ErrorBox, LevelBadge, Loading, SourceLink, Verification } from "./ui";

export type CrisisCard = {
  id: number; uid: string; title: string; status: string; crisis_type: string; region: string | null;
  countries: { iso3: string; name: string }[]; location_countries: string[]; first_observed_at: string;
  last_event_at: string; last_updated_at: string; latitude: number | null; longitude: number | null;
  priority: { score: number; level: string; direction: string; note: string };
  indicator_change: { clusters_24h?: number; clusters_prev_24h?: number; material_conflict_24h?: number;
    diplomatic_signals_24h?: number; change?: { method?: string; zscore?: number; note?: string } };
  evidence_confidence: { score?: number; label?: string }; independent_sources_24h?: number;
  verification_status: string; needs_review: boolean; event_cluster_count: number;
};

export type Cluster = {
  id: number; title: string; event_date: string; category_label: string; countries: string[]; location_country: string | null;
  last_seen_at: string; reports: number; independent_sources: number; source_domains: string[]; max_severity: number;
  verification_status: string; example_source_url: string | null; match_needs_review?: boolean; quad_class?: string;
};

export function PriorityCard({ c }: { c: CrisisCard }) {
  const ch = c.indicator_change || {};
  const delta = (ch.clusters_24h ?? 0) - (ch.clusters_prev_24h ?? 0);
  return (
    <Link href={`/geopolitics/crises/${c.id}`} className="card block transition hover:border-accent">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[11px] text-muted">{c.uid} · {c.region || "region n/a"} · {label(c.crisis_type)}</div>
          <h3 className="mt-0.5 font-medium leading-snug">{c.title}</h3>
        </div>
        <div className="text-right">
          <div className="text-xl font-semibold tabular-nums">{num(c.priority.score, 0)}</div>
          <LevelBadge level={c.priority.level} />
        </div>
      </div>
      <div className="mt-2"><CountryChips countries={c.countries.slice(0, 6)} /></div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div><div className="text-muted">24h clusters</div><div className="tabular-nums">{num(ch.clusters_24h)}
          {ch.clusters_prev_24h !== undefined && <span className={delta > 0 ? "text-crit" : delta < 0 ? "text-good" : "text-muted"}> ({delta >= 0 ? "+" : ""}{delta})</span>}</div></div>
        <div><div className="text-muted">Material conflict</div><div className="tabular-nums">{num(ch.material_conflict_24h)}</div></div>
        <div><div className="text-muted">Outlets 24h</div><div className="tabular-nums">{num(c.independent_sources_24h)}</div></div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Direction d={c.priority.direction} />
        <Confidence label={c.evidence_confidence?.label} score={c.evidence_confidence?.score} />
        <Verification status={c.verification_status} />
        {c.needs_review && <span className="rounded bg-warn/20 px-1.5 py-0.5 text-[11px]">needs review</span>}
      </div>
      <div className="mt-2 text-[11px] text-muted">Last evidence {ago(c.last_event_at)} · updated {ago(c.last_updated_at)}</div>
    </Link>
  );
}

export function ClusterRow({ c }: { c: Cluster }) {
  return (
    <li className="border-b border-line py-2 last:border-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm leading-snug">{c.title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-muted">
            <span>{c.event_date}</span><span>·</span><span>{c.reports} reports</span><span>·</span>
            <span>{c.independent_sources} outlets</span>
            {c.countries?.length > 0 && <><span>·</span><span>{c.countries.join(", ")}</span></>}
            <Verification status={c.verification_status} />
            {c.match_needs_review && <span className="rounded bg-warn/20 px-1">ambiguous match</span>}
          </div>
        </div>
        <div className="shrink-0 text-right"><SourceLink url={c.example_source_url}>source</SourceLink></div>
      </div>
    </li>
  );
}

type AnalysisResp = {
  status: string; message?: string; ai_provider: { provider: string; configured: boolean; reason?: string | null; model?: string | null };
  analysis: null | { id: number; provider: string; model: string; prompt_version: string; created_at: string; trigger: string;
    output: AIOutput | null; error: string | null; evidence_ids: Record<string, unknown[]> };
};
type Item = { description: string; evidence_ids: string[] };
type AIOutput = {
  summary: string; countries_and_actors: { name: string; iso3: string | null; role: string }[];
  important_documented_events: Item[]; recent_changes: Item[]; supporting_evidence: Item[]; conflicting_information: Item[];
  unverified_claims: Item[]; de_escalation_developments: Item[]; missing_information: string[];
  signal_classification: { diplomatic: string; escalation: string; de_escalation: string; rationale: string };
  historical_comparison: string; evidence_quality: { level: string; rationale: string };
  source_references: { evidence_id: string; url: string | null }[]; _meta?: { disclaimer: string; dropped_unknown_evidence_ids: string[] };
};

function Section({ title, items, refs }: { title: string; items: Item[]; refs: Map<string, string | null> }) {
  if (!items?.length) return null;
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold uppercase text-muted">{title}</h4>
      <ul className="list-disc space-y-1 pl-5 text-sm">
        {items.map((it, i) => (
          <li key={i}>{it.description}{" "}
            {it.evidence_ids.map((e) => refs.get(e)
              ? <a key={e} href={refs.get(e) as string} target="_blank" rel="noopener noreferrer nofollow" className="link text-[11px]">[{e}]</a>
              : <span key={e} className="text-[11px] text-muted">[{e}]</span>)}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AnalysisPanel({ getPath, runPath, runBody }: { getPath: string | null; runPath?: string; runBody?: unknown }) {
  const { data, error, isLoading, mutate } = useApi<AnalysisResp>(getPath);
  const [running, setRunning] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [local, setLocal] = useState<AnalysisResp | null>(null);
  const d = local || data;
  const run = async () => {
    if (!runPath) return;
    setRunning(true); setRunErr(null);
    try {
      const r = await post<AnalysisResp>(runPath, runBody ?? {});
      setLocal(r); mutate();
    } catch (e) { setRunErr(e instanceof Error ? e.message : String(e)); }
    setRunning(false);
  };
  if (error && !local) return <ErrorBox error={error} retry={mutate} />;
  if ((isLoading || !d) && getPath) return <Loading what="analysis" />;
  const a = d?.analysis;
  const o = a?.output;
  const refs = new Map((o?.source_references || []).map((r) => [r.evidence_id, r.url]));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="rounded bg-accent/10 px-2 py-0.5 text-[11px] text-accent">AI interpretation · not verified ground truth</span>
        {runPath && <button className="btn" disabled={running || (d ? !d.ai_provider.configured : false)} onClick={run}>
          {running ? "Analyzing…" : "Run analysis"}</button>}
      </div>
      {runErr && <ErrorBox error={runErr} />}
      {d && !d.ai_provider.configured && (
        <Empty>AI analysis is unavailable: {d.ai_provider.reason}. The platform keeps working without AI —
          configure <code>AI_PROVIDER</code> and an API key to enable it.</Empty>
      )}
      {d?.ai_provider.configured && !a && <Empty>No analysis generated yet for this scope.</Empty>}
      {a && a.error && <ErrorBox error={`${d?.status}: ${a.error}`} />}
      {o && (
        <div className="space-y-3">
          <p className="text-sm leading-relaxed">{o.summary}</p>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded border border-line px-1.5">diplomatic signal: {o.signal_classification.diplomatic}</span>
            <span className="rounded border border-line px-1.5">escalation signal: {o.signal_classification.escalation}</span>
            <span className="rounded border border-line px-1.5">de-escalation signal: {o.signal_classification.de_escalation}</span>
            <span className="rounded border border-line px-1.5">evidence quality: {o.evidence_quality.level}</span>
          </div>
          {o.countries_and_actors?.length > 0 && <div className="text-sm"><span className="text-xs font-semibold uppercase text-muted">Actors: </span>
            {o.countries_and_actors.map((x) => `${x.name} (${x.role})`).join("; ")}</div>}
          <Section title="Important documented events" items={o.important_documented_events} refs={refs} />
          <Section title="Recent changes" items={o.recent_changes} refs={refs} />
          <Section title="Supporting evidence" items={o.supporting_evidence} refs={refs} />
          <Section title="Conflicting information" items={o.conflicting_information} refs={refs} />
          <Section title="Unverified claims" items={o.unverified_claims} refs={refs} />
          <Section title="De-escalation developments" items={o.de_escalation_developments} refs={refs} />
          {o.missing_information?.length > 0 && <div><h4 className="mb-1 text-xs font-semibold uppercase text-muted">Missing information</h4>
            <ul className="list-disc pl-5 text-sm">{o.missing_information.map((m, i) => <li key={i}>{m}</li>)}</ul></div>}
          <p className="text-sm"><span className="text-xs font-semibold uppercase text-muted">Historical comparison: </span>{o.historical_comparison}</p>
          <p className="text-sm"><span className="text-xs font-semibold uppercase text-muted">Evidence quality: </span>{o.evidence_quality.rationale}</p>
          <p className="text-[11px] text-muted">{a?.provider} · {a?.model} · prompt {a?.prompt_version} · {fmtDate(a?.created_at)} · {a?.trigger}.
            {" "}{o._meta?.disclaimer}</p>
        </div>
      )}
    </div>
  );
}

type CountryLite = { iso3: string; name: string; region: string | null };

export function CountryPicker({ value, onChange, max = 25 }: { value: string[]; onChange: (v: string[]) => void; max?: number }) {
  const [q, setQ] = useState("");
  const [opts, setOpts] = useState<CountryLite[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const all = useApi<{ items: CountryLite[] }>("/api/v1/countries?limit=500");
  const names = useMemo(() => new Map((all.data?.items || []).map((c) => [c.iso3, c.name])), [all.data]);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!q) { setOpts([]); return; }
    timer.current = setTimeout(async () => {
      try { setOpts((await api<{ items: CountryLite[] }>(`/api/v1/countries?q=${encodeURIComponent(q)}&limit=12`)).items); } catch { setOpts([]); }
    }, 200);
  }, [q]);
  const add = (iso: string) => { if (!value.includes(iso) && value.length < max) onChange([...value, iso]); setQ(""); setOpen(false); };
  return (
    <div className="relative">
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line bg-raised p-1.5">
        {value.map((iso) => (
          <span key={iso} className="inline-flex items-center gap-1 rounded bg-accent/10 px-2 py-0.5 text-sm text-accent">
            {names.get(iso) || iso}
            <button aria-label={`Remove ${iso}`} onClick={() => onChange(value.filter((x) => x !== iso))}>×</button>
          </span>
        ))}
        <input className="min-w-[160px] flex-1 bg-transparent px-1 py-0.5 text-sm outline-none" placeholder={value.length >= max ? `max ${max}` : "Search countries…"}
          value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
          onKeyDown={(e) => { if (e.key === "Enter" && opts[0]) add(opts[0].iso3); }} disabled={value.length >= max} />
      </div>
      {open && opts.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-line bg-raised shadow-lg">
          {opts.map((o) => (
            <li key={o.iso3}><button className="w-full px-3 py-1.5 text-left text-sm hover:bg-accent/10" onClick={() => add(o.iso3)}>
              {o.name} <span className="text-muted">{o.iso3} · {o.region}</span></button></li>
          ))}
        </ul>
      )}
    </div>
  );
}
