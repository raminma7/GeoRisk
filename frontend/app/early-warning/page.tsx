"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  CountryRiskRow, ExperimentalBadge, LEVEL_META, Level, ONGOING_COLOR, Prediction, RISK_RAMP, monthLabel, monthRange, riskColor,
} from "@/components/EarlyWarning";
import { Empty, ErrorBox, Loading } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

type WatchList = {
  meta: null | {
    model_version: string; window: { start: string; end: string }; data_through: string; generated_at: string; trained_at?: string;
    test_quality: null | { roc_auc: number; top20_share_caught: number; test_years: number[]; onsets_tested: number };
    levels: Partial<Record<Level, number>>;
    explainer: { question: string; how: string; levels: Record<string, string>; caveat: string };
  };
  items: Prediction[]; ongoing_conflict: { iso3: string; name: string }[]; total_predicted: number; message?: string;
  setup?: null | { state: "running" | "failed" | "done"; step?: string | null; step_label?: string | null; error?: string | null;
    progress?: null | { done: number; total: number | null }; updated_at?: string };
  setup_steps?: { id: string; label: string }[];
};

function SetupCard({ data }: { data: WatchList }) {
  const st = data.setup;
  const steps = data.setup_steps || [];
  const current = steps.findIndex((s) => s.id === st?.step);
  const pct = st?.progress?.total ? Math.round((st.progress.done / st.progress.total) * 100) : null;
  return (
    <section className="mx-auto max-w-2xl rounded-2xl border border-line bg-surface p-6 md:p-8">
      <div className="mb-3 flex items-center gap-2"><ExperimentalBadge /></div>
      <h1 className="text-2xl font-semibold tracking-tight">
        {st?.state === "failed" ? "Early-warning setup needs attention" : "Setting up the early-warning model…"}
      </h1>
      <p className="mt-2 text-ink2">
        On a new installation GeoRisk first collects the data the model needs. This runs automatically in the
        background and usually takes 15–30 minutes. This page updates by itself.
      </p>
      <ol className="mt-6 space-y-3">
        {steps.map((s, i) => {
          const done = st?.state === "done" || (current >= 0 && i < current);
          const active = st?.state === "running" && i === current;
          const failed = st?.state === "failed" && i === current;
          return (
            <li key={s.id} className="flex items-start gap-3">
              <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-semibold ${
                failed ? "bg-crit text-white" : done ? "bg-good text-white" : active ? "bg-accent text-white" : "bg-line text-muted"}`}>
                {failed ? "!" : done ? "✓" : i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className={active ? "font-medium" : done ? "text-ink2" : "text-muted"}>{s.label}</div>
                {active && pct !== null && (
                  <div className="mt-1.5">
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line/60"><div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>
                    <div className="mt-1 text-xs text-muted">{st?.progress?.done} of {st?.progress?.total} news archive files</div>
                  </div>
                )}
                {active && pct === null && <div className="mt-1 h-1.5 w-1/3 animate-pulse rounded-full bg-accent/40" />}
              </div>
            </li>
          );
        })}
      </ol>
      {st?.state === "failed" && <div className="mt-5 rounded-lg border border-crit/40 bg-crit/10 p-3 text-sm text-crit">{st.error}</div>}
      {!st && <p className="mt-5 text-sm text-muted">Waiting for the background worker to start the setup…</p>}
      <details className="mt-6 text-sm">
        <summary className="cursor-pointer text-muted">Run it manually</summary>
        <pre className="mt-2 overflow-x-auto rounded-lg bg-raised p-3 text-xs ring-1 ring-line">docker compose exec api python -m app.cli setup-early-warning</pre>
      </details>
    </section>
  );
}

export default function EarlyWarningPage() {
  const router = useRouter();
  const [level, setLevel] = useState<"" | Level>("");
  const [showAll, setShowAll] = useState(false);
  const [region, setRegion] = useState("");
  const { data, error, isLoading, mutate } = useApi<WatchList>(`/api/v1/predictions${qs({ limit: 300, region })}`,
    { refreshInterval: (d?: WatchList) => (d && !d.meta ? 10_000 : 0) });
  const regions = useApi<{ items: { code: string; name: string }[] }>("/api/v1/regions?kind=un_subregion");
  const countries = useApi<{ items: { iso3: string; iso_numeric: number | null }[] }>("/api/v1/countries?limit=500");

  const { fills, titles, numToIso } = useMemo(() => {
    const num = new Map((countries.data?.items || []).map((c) => [c.iso3, c.iso_numeric]));
    const f: Record<string, string> = {}, t: Record<string, string> = {}, back: Record<string, string> = {};
    for (const p of data?.items || []) {
      const n = num.get(p.iso3);
      if (!n) continue;
      const k = String(n).padStart(3, "0");
      f[k] = riskColor(p.times_average);
      t[k] = `${p.name}: ${p.times_average?.toFixed(1)}× average · ${p.level ? LEVEL_META[p.level].label : ""} (#${p.rank})`;
      back[k] = p.iso3;
    }
    for (const o of data?.ongoing_conflict || []) {
      const n = num.get(o.iso3);
      if (!n) continue;
      const k = String(n).padStart(3, "0");
      f[k] = ONGOING_COLOR;
      t[k] = `${o.name}: already in active conflict`;
      back[k] = o.iso3;
    }
    return { fills: f, titles: t, numToIso: back };
  }, [data, countries.data]);

  if (error) return <ErrorBox error={error} retry={mutate} />;
  if (isLoading || !data) return <Loading what="early-warning watch list" />;
  if (!data.meta) return <SetupCard data={data} />;

  const m = data.meta;
  const items = data.items.filter((p) => !level || p.level === level);
  const visible = showAll ? items : items.slice(0, 20);
  const max = Math.max(...data.items.map((p) => p.times_average || 0), 1);
  const q = m.test_quality;

  return (
    <div className="space-y-6">
      {/* Hero */}
      <section className="relative overflow-hidden rounded-2xl border border-line bg-surface p-6 md:p-8">
        <div className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-accent/10 blur-3xl" aria-hidden />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <div className="mb-3 flex flex-wrap items-center gap-2"><ExperimentalBadge />
              <span className="text-xs text-muted">Updated {fmtDate(m.generated_at, false)} · model {m.model_version}</span></div>
            <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Where could a new conflict start?</h1>
            <p className="mt-2 text-ink2">
              Countries ranked by their modelled risk of a <b>new</b> armed conflict in <b>{monthRange(m.window)}</b> —
              among countries that have had no conflict deaths for two years.
            </p>
            <p className="mt-1 text-sm text-muted">Based on verified conflict data through {monthLabel(m.data_through)}.</p>
          </div>
          {q && (
            <div className="rounded-xl border border-line bg-raised p-4 text-sm">
              <div className="text-xs uppercase tracking-wide text-muted">Track record</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{Math.round(q.top20_share_caught * 10)} in 10</div>
              <div className="max-w-[15rem] text-xs text-muted">new conflicts in {q.test_years[0]}–{q.test_years[1]} were in the model’s
                monthly top 20 (tested on years it had never seen)</div>
            </div>
          )}
        </div>
        {/* Summary tiles */}
        <div className="relative mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          {(["very_high", "high", "elevated"] as Level[]).map((l) => (
            <button key={l} onClick={() => setLevel(level === l ? "" : l)}
              className={`rounded-xl border p-3 text-left transition ${level === l ? "border-accent bg-accent/5" : "border-line bg-raised hover:border-accent/50"}`}>
              <div className="flex items-center gap-2 text-xs text-muted"><span className={`h-2 w-2 rounded-full ${LEVEL_META[l].dot}`} />{LEVEL_META[l].label} risk</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{m.levels[l] || 0}</div>
              <div className="text-xs text-muted">{m.explainer.levels[l]}</div>
            </button>
          ))}
          <div className="rounded-xl border border-line bg-raised p-3">
            <div className="flex items-center gap-2 text-xs text-muted"><span className="h-2 w-2 rounded-full" style={{ background: ONGOING_COLOR }} />Already in conflict</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{data.ongoing_conflict.length}</div>
            <div className="text-xs text-muted">tracked by live monitoring instead</div>
          </div>
        </div>
      </section>

      {/* Map */}
      <section className="rounded-2xl border border-line bg-surface p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="h2">Risk map</h2>
          <div className="flex flex-wrap items-center gap-3 text-xs text-ink2">
            {RISK_RAMP.map((r) => <span key={r.label} className="inline-flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm" style={{ background: r.color }} />{r.label}</span>)}
            <span className="inline-flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm" style={{ background: ONGOING_COLOR }} />in conflict</span>
          </div>
        </div>
        <WorldMap fills={fills} fillTitles={titles} height={380} onCountryClick={(id) => numToIso[id] && router.push(`/geopolitics/countries/${numToIso[id]}`)} />
      </section>

      {/* Ranked list */}
      <section className="rounded-2xl border border-line bg-surface p-2 md:p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 px-2 pb-2">
          <h2 className="h2">{level ? `${LEVEL_META[level].label} risk` : "Watch list"} <span className="font-normal normal-case text-muted">· {items.length} countries</span></h2>
          <div className="flex items-center gap-2">
            {level && <button className="btn text-xs" onClick={() => setLevel("")}>Clear filter ✕</button>}
            <select className="input text-xs" value={region} onChange={(e) => setRegion(e.target.value)} aria-label="Region">
              <option value="">All regions</option>
              {(regions.data?.items || []).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
            </select>
          </div>
        </div>
        <div className="hidden grid-cols-[2.5rem_14rem_1fr_10rem] gap-3 px-3 pb-1 text-[11px] uppercase tracking-wide text-muted md:grid">
          <span>Rank</span><span>Country</span><span>Main reasons</span><span className="text-right">Risk vs. average</span>
        </div>
        {visible.length === 0 ? <Empty>No countries match this filter.</Empty> : (
          <div className="divide-y divide-line">{visible.map((p) => <CountryRiskRow key={p.iso3} p={p} max={max} />)}</div>
        )}
        {items.length > 20 && (
          <div className="pt-3 text-center">
            <button className="btn" onClick={() => setShowAll(!showAll)}>{showAll ? "Show top 20" : `Show all ${items.length}`}</button>
          </div>
        )}
      </section>

      {/* Explainer */}
      <section className="grid gap-4 md:grid-cols-3">
        <div className="rounded-2xl border border-line bg-surface p-5">
          <h3 className="font-medium">What does it predict?</h3>
          <p className="mt-1 text-sm text-ink2">{m.explainer.question}</p>
        </div>
        <div className="rounded-2xl border border-line bg-surface p-5">
          <h3 className="font-medium">How does it work?</h3>
          <p className="mt-1 text-sm text-ink2">{m.explainer.how} “×N average” compares each country with the average quiet country.</p>
        </div>
        <div className="rounded-2xl border border-warn/40 bg-warn/5 p-5">
          <h3 className="font-medium">Read with care</h3>
          <p className="mt-1 text-sm text-ink2">{m.explainer.caveat}</p>
        </div>
      </section>

      <details className="rounded-2xl border border-line bg-surface p-4">
        <summary className="cursor-pointer text-sm font-medium">Countries already in active conflict ({data.ongoing_conflict.length})</summary>
        <p className="mt-2 text-xs text-muted">The model only scores countries without recent conflict deaths. Ongoing conflicts are covered by live monitoring on the Overview.</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {data.ongoing_conflict.map((c) => <Link key={c.iso3} href={`/geopolitics/countries/${c.iso3}`}
            className="rounded-full bg-raised px-2.5 py-0.5 text-xs ring-1 ring-inset ring-line hover:ring-accent">{c.name}</Link>)}
        </div>
      </details>
    </div>
  );
}
