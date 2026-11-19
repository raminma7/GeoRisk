"use client";

/* Early-warning (experimental ML) UI building blocks. Risk is shown as "× the average country" — a relative
   number people read easily — never as a "chance of war". Levels use the reserved status colors and always carry
   an icon + text label. Choropleth uses one sequential hue (blue), light→dark. */

import Link from "next/link";

export type Reason = { factor: string; label: string; value: string; direction: "raises" | "lowers"; weight: number };
export type Prediction = {
  iso3: string; name: string | null; status: "predicted" | "ongoing_conflict"; probability: number | null; rank: number | null;
  out_of: number | null; times_average: number | null; level: Level | null; reasons: Reason[];
  window: { start: string; end: string }; data_through: string; model_version: string; region?: string | null;
  borders?: string[];
};
export type Level = "very_high" | "high" | "elevated" | "low";

export const LEVEL_META: Record<Level, { label: string; icon: string; chip: string; bar: string; dot: string }> = {
  very_high: { label: "Very high", icon: "▲▲", chip: "bg-crit/12 text-crit ring-crit/30", bar: "bg-crit", dot: "bg-crit" },
  high: { label: "High", icon: "▲", chip: "bg-serious/15 text-serious ring-serious/30", bar: "bg-serious", dot: "bg-serious" },
  elevated: { label: "Elevated", icon: "◆", chip: "bg-warn/15 text-[rgb(150_95_0)] dark:text-warn ring-warn/40", bar: "bg-warn", dot: "bg-warn" },
  low: { label: "Low", icon: "●", chip: "bg-line/50 text-ink2 ring-line", bar: "bg-ink2/40", dot: "bg-ink2/50" },
};

// Sequential blue ramp (reference palette steps 150 → 650) for the risk map.
export const RISK_RAMP = [
  { min: 10, color: "#104281", label: "≥10× average" },
  { min: 4, color: "#256abf", label: "4–10×" },
  { min: 1.5, color: "#6da7ec", label: "1.5–4×" },
  { min: 0, color: "#b7d3f6", label: "below 1.5×" },
];
export const ONGOING_COLOR = "#8f8e88";

export function riskColor(timesAvg: number | null): string {
  if (timesAvg === null) return ONGOING_COLOR;
  return (RISK_RAMP.find((r) => timesAvg >= r.min) || RISK_RAMP[RISK_RAMP.length - 1]).color;
}

export function monthRange(w: { start: string; end: string }): string {
  const s = new Date(w.start + "T00:00:00Z"), e = new Date(w.end + "T00:00:00Z");
  const m = (d: Date, y = false) => d.toLocaleDateString(undefined, { month: "short", ...(y ? { year: "numeric" } : {}), timeZone: "UTC" });
  return `${m(s)} – ${m(e, true)}`;
}

export function monthLabel(iso: string): string {
  return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
}

export function LevelChip({ level, size = "sm" }: { level: Level | null; size?: "sm" | "md" }) {
  if (!level) return null;
  const m = LEVEL_META[level];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full font-medium ring-1 ring-inset ${m.chip} ${size === "md" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs"}`}>
      <span aria-hidden className="text-[0.7em]">{m.icon}</span>{m.label}
    </span>
  );
}

export function TimesAverage({ value, big = false }: { value: number | null; big?: boolean }) {
  if (value === null) return null;
  return (
    <span className={`tabular-nums ${big ? "text-3xl font-semibold tracking-tight" : "text-sm font-semibold"}`}>
      {value >= 10 ? value.toFixed(0) : value.toFixed(1)}×<span className={`ml-1 font-normal text-muted ${big ? "text-base" : "text-xs"}`}>average</span>
    </span>
  );
}

export function RiskBar({ value, max, level }: { value: number; max: number; level: Level | null }) {
  const pct = Math.max(3, Math.min(100, (Math.log1p(value) / Math.log1p(Math.max(max, 1))) * 100));
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-line/60" role="img" aria-label={`${value.toFixed(1)} times average risk`}>
      <div className={`h-full rounded-full ${level ? LEVEL_META[level].bar : "bg-ink2/40"}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function ReasonList({ reasons, max = 3, compact = false }: { reasons: Reason[]; max?: number; compact?: boolean }) {
  const shown = reasons.slice(0, max);
  if (!shown.length) return <p className="text-xs text-muted">No factor explanation available.</p>;
  return (
    <ul className={compact ? "flex flex-wrap gap-1.5" : "space-y-1.5"}>
      {shown.map((r) => (
        <li key={r.factor} className={compact
          ? "inline-flex items-center gap-1 rounded-full bg-raised px-2 py-0.5 text-xs ring-1 ring-inset ring-line"
          : "flex items-center justify-between gap-3 text-sm"}>
          {compact ? (
            <>
              <span aria-hidden className={r.direction === "raises" ? "text-crit" : "text-good"}>{r.direction === "raises" ? "↑" : "↓"}</span>
              <span className="text-ink2">{r.label}</span>
              <span className="font-medium tabular-nums">{r.value}</span>
            </>
          ) : (
            <>
              <span className="flex items-center gap-2 text-ink2">
                <span aria-hidden className={`grid h-5 w-5 place-items-center rounded-full text-xs ${r.direction === "raises" ? "bg-crit/12 text-crit" : "bg-good/12 text-good"}`}>
                  {r.direction === "raises" ? "↑" : "↓"}
                </span>
                {r.label}
              </span>
              <span className="font-medium tabular-nums">{r.value}</span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

/* Semicircle gauge on a log scale from 0.1× to 20× the average country. */
export function RiskGauge({ value, level }: { value: number; level: Level | null }) {
  const lo = Math.log(0.1), hi = Math.log(20);
  const t = Math.max(0, Math.min(1, (Math.log(Math.max(value, 0.1)) - lo) / (hi - lo)));
  const angle = Math.PI * (1 - t);
  const r = 70, cx = 90, cy = 85;
  const x = cx + r * Math.cos(angle), y = cy - r * Math.sin(angle);
  const arc = (a0: number, a1: number) => {
    const p0 = [cx + r * Math.cos(Math.PI * (1 - a0)), cy - r * Math.sin(Math.PI * (1 - a0))];
    const p1 = [cx + r * Math.cos(Math.PI * (1 - a1)), cy - r * Math.sin(Math.PI * (1 - a1))];
    return `M ${p0[0]} ${p0[1]} A ${r} ${r} 0 0 1 ${p1[0]} ${p1[1]}`;
  };
  const stop = (v: number) => (Math.log(v) - lo) / (hi - lo);
  const segs: [number, number, string][] = [[0, stop(1.5), "rgb(var(--line))"], [stop(1.5), stop(4), "rgb(var(--warn))"],
    [stop(4), stop(10), "rgb(var(--serious))"], [stop(10), 1, "rgb(var(--crit))"]];
  return (
    <svg viewBox="0 0 180 100" className="w-full max-w-[220px]" role="img" aria-label={`Risk gauge: ${value.toFixed(1)} times the average country`}>
      {segs.map(([a, b, c], i) => <path key={i} d={arc(a, b - 0.004)} stroke={c} strokeWidth={12} fill="none" strokeLinecap="butt" />)}
      <line x1={cx} y1={cy} x2={x} y2={y} stroke="rgb(var(--ink))" strokeWidth={3} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={5} fill="rgb(var(--ink))" />
      <text x={20} y={98} fontSize={9} fill="rgb(var(--muted))">low</text>
      <text x={147} y={98} fontSize={9} fill="rgb(var(--muted))">very high</text>
      {level && <title>{LEVEL_META[level].label}</title>}
    </svg>
  );
}

export function Sparkline({ points }: { points: { window_start: string; times_average: number | null }[] }) {
  const pts = points.filter((p) => p.times_average !== null) as { window_start: string; times_average: number }[];
  if (pts.length < 2) return <p className="text-xs text-muted">Trend appears after the next monthly update.</p>;
  const w = 220, h = 44, max = Math.max(...pts.map((p) => p.times_average), 1);
  const xs = pts.map((_, i) => (i / (pts.length - 1)) * (w - 8) + 4);
  const ys = pts.map((p) => h - 4 - (p.times_average / max) * (h - 8));
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-11 w-full" role="img" aria-label="Monthly risk trend">
      <polyline points={xs.map((x, i) => `${x},${ys[i]}`).join(" ")} fill="none" stroke="var(--series-1)" strokeWidth={2} />
      <circle cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r={4} fill="var(--series-1)" stroke="var(--chart-surface)" strokeWidth={2} />
    </svg>
  );
}

export function ExperimentalBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-2.5 py-0.5 text-xs font-medium text-accent ring-1 ring-inset ring-accent/25"
      title="Statistical model trained on historical data. Research estimate, not a forecast of war.">
      <span aria-hidden>⚗</span> Experimental model
    </span>
  );
}

export function CountryRiskRow({ p, max }: { p: Prediction; max: number }) {
  return (
    <Link href={`/geopolitics/countries/${p.iso3}`}
      className="group grid grid-cols-[2.5rem_1fr] gap-x-3 gap-y-2 rounded-xl px-3 py-3 transition hover:bg-accent/5 md:grid-cols-[2.5rem_14rem_1fr_10rem]">
      <div className="row-span-2 text-lg font-semibold tabular-nums text-muted md:row-span-1">#{p.rank}</div>
      <div className="min-w-0">
        <div className="truncate font-medium group-hover:text-accent">{p.name || p.iso3}</div>
        <div className="truncate text-xs text-muted">{p.region || p.iso3}</div>
      </div>
      <div className="col-start-2 min-w-0 md:col-start-auto"><ReasonList reasons={p.reasons} compact /></div>
      <div className="col-start-2 flex items-center gap-3 md:col-start-auto md:flex-col md:items-end md:gap-1.5">
        <div className="flex items-center gap-2"><TimesAverage value={p.times_average} /><LevelChip level={p.level} /></div>
        <div className="w-28 md:w-full"><RiskBar value={p.times_average || 0} max={max} level={p.level} /></div>
      </div>
    </Link>
  );
}


export function WatchListStrip({ data }: { data: { window: { start: string; end: string }; items: Prediction[] } }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="h2">Early-warning watch list</h2><ExperimentalBadge />
          <span className="text-xs text-muted">Risk of a new conflict · {monthRange(data.window)}</span>
        </div>
        <Link href="/early-warning" className="link text-sm">Full watch list →</Link>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {data.items.map((p) => (
          <Link key={p.iso3} href={`/geopolitics/countries/${p.iso3}`}
            className="rounded-xl border border-line bg-raised p-3 transition hover:-translate-y-0.5 hover:border-accent hover:shadow-sm">
            <div className="flex items-center justify-between text-xs text-muted"><span>#{p.rank}</span><LevelChip level={p.level} /></div>
            <div className="mt-1 truncate font-medium">{p.name}</div>
            <div className="mt-0.5"><TimesAverage value={p.times_average} /></div>
            {p.reasons[0] && <div className="mt-2 truncate text-xs text-ink2" title={`${p.reasons[0].label}: ${p.reasons[0].value}`}>
              <span className={p.reasons[0].direction === "raises" ? "text-crit" : "text-good"}>{p.reasons[0].direction === "raises" ? "↑" : "↓"}</span> {p.reasons[0].label}</div>}
          </Link>
        ))}
      </div>
    </section>
  );
}

export function CountryRiskCard({ risk }: { risk: (Prediction & { history: { window_start: string; times_average: number | null }[];
  explainer: { caveat: string } }) | null }) {
  if (!risk) return null;
  if (risk.status === "ongoing_conflict") {
    return (
      <section className="rounded-2xl border border-line bg-surface p-5">
        <div className="flex flex-wrap items-center gap-2"><h2 className="h2">Early warning</h2><ExperimentalBadge /></div>
        <p className="mt-2 text-sm text-ink2">This country already has recent conflict deaths, so the new-conflict model does not apply.
          Its situation is tracked by live crisis monitoring above.</p>
      </section>
    );
  }
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2"><h2 className="h2">Early warning · risk of new conflict</h2><ExperimentalBadge /></div>
        <Link href="/early-warning" className="link text-xs">Compare all countries →</Link>
      </div>
      <div className="mt-4 grid gap-6 md:grid-cols-[220px_1fr]">
        <div className="flex flex-col items-center text-center">
          <RiskGauge value={risk.times_average || 0} level={risk.level} />
          <TimesAverage value={risk.times_average} big />
          <div className="mt-1 flex items-center gap-2"><LevelChip level={risk.level} size="md" /></div>
          <div className="mt-2 text-xs text-muted">Rank #{risk.rank} of {risk.out_of} quiet countries · {monthRange(risk.window)}</div>
        </div>
        <div className="space-y-4">
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Why</h3>
            <ReasonList reasons={risk.reasons} max={5} />
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Monthly trend</h3>
            <Sparkline points={risk.history} />
          </div>
          <p className="text-xs text-muted">{risk.explainer.caveat}</p>
        </div>
      </div>
    </section>
  );
}

export function SpilloverCard({ items }: { items: Prediction[] }) {
  const shown = items.filter((p) => p.status === "predicted").slice(0, 6);
  const inConflict = items.filter((p) => p.status === "ongoing_conflict");
  return (
    <section className="rounded-2xl border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2"><h2 className="h2">Spillover risk</h2><ExperimentalBadge /></div>
      <p className="mb-3 text-xs text-muted">Neighbouring countries not involved in this crisis, by modelled risk of a new conflict.</p>
      {shown.length === 0 ? <p className="text-sm text-muted">No uninvolved neighbours with a prediction.</p> : (
        <ul className="space-y-2">
          {shown.map((p) => (
            <li key={p.iso3}>
              <Link href={`/geopolitics/countries/${p.iso3}`} className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 hover:bg-accent/5">
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{p.name}</span>
                  <span className="block text-[11px] text-muted">borders {p.borders?.join(", ")}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2"><TimesAverage value={p.times_average} /><LevelChip level={p.level} /></span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {inConflict.length > 0 && <p className="mt-3 text-[11px] text-muted">Also bordering, already in conflict: {inConflict.map((p) => p.name).join(", ")}</p>}
    </section>
  );
}
