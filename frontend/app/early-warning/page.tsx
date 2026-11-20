"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  ExperimentalBadge, OUTLOOK, OutlookChip, ReasonList, TIER_LABEL, monthLabel, monthRange, type Direction, type Reason,
} from "@/components/EarlyWarning";
import { SeverityChip } from "@/components/Today";
import { Empty, ErrorBox, Loading } from "@/components/ui";
import { qs, useApi } from "@/lib/api";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

type Item = {
  iso3: string; name: string; region: string | null; current_tier: string | null; direction: Direction; label: string;
  chance: number | null; reasons: Reason[]; note?: string | null;
  six_months: null | { direction: Direction; label: string; chance: number | null; to_tier?: string | null };
  to_tier?: string | null;
};

type WatchList = {
  meta: null | {
    model_version: string; window: { start: string; end: string }; data_through: string; generated_at: string; trained_at?: string;
    window_6m?: { start: string; end: string } | null;
    explainer: { caveat: string };
  };
  items: Item[]; counts: Partial<Record<Direction, number>>; total: number; message?: string;
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
  const [direction, setDirection] = useState<"" | Direction>("");
  const [showAll, setShowAll] = useState(false);
  const [region, setRegion] = useState("");
  const { data, error, isLoading, mutate } = useApi<WatchList>(`/api/v1/predictions${qs({ region })}`,
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
      f[k] = OUTLOOK[p.direction].fill;
      t[k] = `${p.name}: ${p.label}`;
      back[k] = p.iso3;
    }
    return { fills: f, titles: t, numToIso: back };
  }, [data, countries.data]);

  if (error) return <ErrorBox error={error} retry={mutate} />;
  if (isLoading || !data) return <Loading what="outlook" />;
  if (!data.meta) return <SetupCard data={data} />;

  const m = data.meta;
  const items = data.items.filter((p) => !direction || p.direction === direction);
  const visible = showAll ? items : items.slice(0, 30);
  const tabs: ["" | Direction, string, number][] = [
    ["", "All", data.total], ["worse", OUTLOOK.worse.label, data.counts.worse || 0],
    ["better", OUTLOOK.better.label, data.counts.better || 0], ["same", OUTLOOK.same.label, data.counts.same || 0],
    ["uncertain", OUTLOOK.uncertain.label, data.counts.uncertain || 0],
  ];

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-line bg-surface p-6 md:p-8">
        <div className="mb-3 flex flex-wrap items-center gap-2"><ExperimentalBadge />
          <span className="text-xs text-muted">Data through {monthLabel(m.data_through)}</span></div>
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Better or worse?</h1>
        <p className="mt-2 text-ink2">Where each country is heading in the next 3 months ({monthRange(m.window)}) and 6 months.</p>
        <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-5" role="tablist" aria-label="Filter by outlook">
          {tabs.map(([d, l, n]) => (
            <button key={d || "all"} role="tab" aria-selected={direction === d} onClick={() => { setDirection(d); setShowAll(false); }}
              className={`rounded-xl border p-3 text-left transition ${direction === d ? "border-accent bg-accent/5" : "border-line bg-raised hover:border-accent/50"}`}>
              <div className="flex items-center gap-2 text-xs text-muted">
                {d && <span className="h-2.5 w-2.5 rounded-full" style={{ background: OUTLOOK[d].fill }} />}{l}
              </div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{n}</div>
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-line bg-surface p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="h2">Next 3 months</h2>
          <div className="flex flex-wrap items-center gap-3 text-xs text-ink2">
            {(["worse", "better", "same", "uncertain"] as Direction[]).map((d) => (
              <span key={d} className="inline-flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm" style={{ background: OUTLOOK[d].fill }} />{OUTLOOK[d].label}</span>
            ))}
          </div>
        </div>
        <WorldMap fills={fills} fillTitles={titles} height={380} onCountryClick={(id) => numToIso[id] && router.push(`/geopolitics/countries/${numToIso[id]}`)} />
      </section>

      <section className="rounded-2xl border border-line bg-surface p-2 md:p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 px-2 pb-2">
          <h2 className="h2">{direction ? OUTLOOK[direction].label : "All countries"} <span className="font-normal normal-case text-muted">· {items.length}</span></h2>
          <select className="input text-xs" value={region} onChange={(e) => setRegion(e.target.value)} aria-label="Region">
            <option value="">All regions</option>
            {(regions.data?.items || []).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
          </select>
        </div>
        <div className="hidden grid-cols-[14rem_7rem_11rem_11rem_1fr] gap-3 px-3 pb-1 text-[11px] uppercase tracking-wide text-muted md:grid">
          <span>Country</span><span>Now</span><span>Next 3 months</span><span>Next 6 months</span><span>Main reasons</span>
        </div>
        {visible.length === 0 ? <Empty>No countries match this filter.</Empty> : (
          <div className="divide-y divide-line">
            {visible.map((p) => (
              <Link key={p.iso3} href={`/geopolitics/countries/${p.iso3}`}
                className="group grid grid-cols-2 items-center gap-x-3 gap-y-2 rounded-xl px-3 py-3 transition hover:bg-accent/5 md:grid-cols-[14rem_7rem_11rem_11rem_1fr]">
                <div className="col-span-2 min-w-0 md:col-span-1">
                  <div className="truncate font-medium group-hover:text-accent">{p.name}</div>
                  <div className="truncate text-xs text-muted">{p.region}</div>
                </div>
                <div>{p.current_tier && <SeverityChip s={{ level: p.current_tier, label: TIER_LABEL[p.current_tier] || p.current_tier }} />}</div>
                <div><OutlookChip direction={p.direction} chance={p.chance} />
                  {(p.direction === "worse" || p.direction === "better") && p.to_tier && <div className="mt-0.5 text-[11px] text-muted">→ {TIER_LABEL[p.to_tier]}</div>}</div>
                <div className="col-span-2 md:col-span-1">{p.six_months && <span className="mr-1 text-xs text-muted md:hidden">6 months:</span>}
                  {p.six_months && <OutlookChip direction={p.six_months.direction} chance={p.six_months.chance} />}</div>
                <div className="col-span-2 min-w-0 md:col-span-1">{p.direction === "uncertain" ? <span className="text-xs text-ink2">{p.note}</span>
                  : p.direction !== "same" && <ReasonList reasons={p.reasons} max={2} compact towardsBetter={p.direction === "better"} />}</div>
              </Link>
            ))}
          </div>
        )}
        {items.length > 30 && (
          <div className="pt-3 text-center">
            <button className="btn" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${items.length}`}</button>
          </div>
        )}
      </section>
      <p className="text-xs text-muted">{m.explainer.caveat}</p>
    </div>
  );
}
