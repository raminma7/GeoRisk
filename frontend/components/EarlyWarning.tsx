"use client";

/* Early-warning (ML) UI building blocks: the outlook per country (could get worse / could get better / likely to
   stay the same). Colours are the reserved status colours and always come with an arrow and a text label. */

import Link from "next/link";

export type Reason = { factor: string; label: string; value: string; direction: "raises" | "lowers"; weight: number };
export type Prediction = {
  iso3: string; name: string | null; status: "predicted" | "top_tier"; probability: number | null; rank: number | null;
  current_tier?: string | null;
  six_months?: { probability: number | null; times_average: number | null; level: Level | null; status: string;
    window: { start: string; end: string } } | null;
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

/* Factors behind a prediction. Red = pushes towards things getting worse, green = towards better. For the
   "could get better" model a factor that raises the chance is good news, so the colours swap (`towardsBetter`). */
export function ReasonList({ reasons, max = 3, compact = false, towardsBetter = false }: { reasons: Reason[]; max?: number;
  compact?: boolean; towardsBetter?: boolean }) {
  const shown = reasons.slice(0, max);
  const bad = (r: Reason) => (r.direction === "raises") !== towardsBetter;
  if (!shown.length) return <p className="text-xs text-muted">No factor explanation available.</p>;
  return (
    <ul className={compact ? "flex flex-wrap gap-1.5" : "space-y-1.5"}>
      {shown.map((r) => (
        <li key={r.factor} className={compact
          ? "inline-flex items-center gap-1 rounded-full bg-raised px-2 py-0.5 text-xs ring-1 ring-inset ring-line"
          : "flex items-center justify-between gap-3 text-sm"}>
          {compact ? (
            <>
              <span aria-hidden className={bad(r) ? "text-crit" : "text-good"}>{r.direction === "raises" ? "↑" : "↓"}</span>
              <span className="text-ink2">{r.label}</span>
              <span className="font-medium tabular-nums">{r.value}</span>
            </>
          ) : (
            <>
              <span className="flex items-center gap-2 text-ink2">
                <span aria-hidden className={`grid h-5 w-5 place-items-center rounded-full text-xs ${bad(r) ? "bg-crit/12 text-crit" : "bg-good/12 text-good"}`}>
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

export function ExperimentalBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-2.5 py-0.5 text-xs font-medium text-accent ring-1 ring-inset ring-accent/25"
      title="Statistical model trained on historical data. Research estimate, not a forecast of events.">
      <span aria-hidden>⚗</span> Experimental model
    </span>
  );
}

export type Direction = "worse" | "better" | "same";
export const OUTLOOK: Record<Direction, { label: string; icon: string; chip: string; fill: string }> = {
  worse: { label: "Could get worse", icon: "↗", chip: "bg-crit/12 text-crit ring-crit/30", fill: "#d03b3b" },
  better: { label: "Could get better", icon: "↘", chip: "bg-good/12 text-good ring-good/30", fill: "#0ca30c" },
  same: { label: "Likely to stay the same", icon: "→", chip: "bg-line/50 text-ink2 ring-line", fill: "#c9c8c1" },
};

/* Outlook chip: direction (with arrow + text) and, for worse / better, the model's chance in %. */
export function OutlookChip({ direction, chance, size = "sm" }: { direction: Direction; chance?: number | null; size?: "sm" | "md" }) {
  const o = OUTLOOK[direction];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full font-medium ring-1 ring-inset ${o.chip} ${size === "md" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs"}`}>
      <span aria-hidden>{o.icon}</span>{direction === "same" ? "Same" : direction === "worse" ? "Worse" : "Better"}
      {direction !== "same" && chance != null && <span className="font-normal opacity-80">· {Math.round(chance * 100)}%</span>}
    </span>
  );
}

export const TIER_LABEL: Record<string, string> = { low: "Low", elevated: "Moderate", high: "High", critical: "Critical" };

type CountryOutlook = { direction: Direction; label: string; chance: number | null; reasons: Reason[];
  window: { start: string; end: string } | null };

export function CountryRiskCard({ risk }: { risk: (Prediction & { outlook?: CountryOutlook | null;
  outlook_6m?: CountryOutlook | null; explainer: { caveat: string } }) | null }) {
  const o = risk?.outlook;
  if (!risk || !o) return null;
  const six = risk.outlook_6m;
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2"><h2 className="h2">Better or worse?</h2><ExperimentalBadge /></div>
        <Link href="/early-warning" className="link text-xs">All countries →</Link>
      </div>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="rounded-xl bg-raised p-4 ring-1 ring-inset ring-line">
          <div className="text-xs text-muted">Next 3 months{o.window ? ` · ${monthRange(o.window)}` : ""}</div>
          <div className="mt-2"><OutlookChip direction={o.direction} chance={o.chance} size="md" /></div>
          <div className="mt-1 text-sm font-medium">{o.label}</div>
        </div>
        {six && (
          <div className="rounded-xl bg-raised p-4 ring-1 ring-inset ring-line">
            <div className="text-xs text-muted">Next 6 months{six.window ? ` · ${monthRange(six.window)}` : ""}</div>
            <div className="mt-2"><OutlookChip direction={six.direction} chance={six.chance} size="md" /></div>
            <div className="mt-1 text-sm font-medium">{six.label}</div>
          </div>
        )}
      </div>
      {o.direction !== "same" && o.reasons.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Why</h3>
          <ReasonList reasons={o.reasons} max={3} towardsBetter={o.direction === "better"} />
        </div>
      )}
    </section>
  );
}

export function SpilloverCard({ items }: { items: Prediction[] }) {
  const shown = items.filter((p) => p.status === "predicted").slice(0, 6);
  const inConflict = items.filter((p) => p.status === "top_tier");
  return (
    <section className="rounded-2xl border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2"><h2 className="h2">Spillover risk</h2><ExperimentalBadge /></div>
      <p className="mb-3 text-xs text-muted">Neighbouring countries not involved in this crisis, by modelled risk of getting worse in the next 3 months.</p>
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
      {inConflict.length > 0 && <p className="mt-3 text-[11px] text-muted">Also bordering, already Critical: {inConflict.map((p) => p.name).join(", ")}</p>}
    </section>
  );
}
