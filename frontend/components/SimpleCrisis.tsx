"use client";

/* Readable crisis page (simple view): what it is, how serious, why, what happened recently, where, who could be affected. */

import dynamic from "next/dynamic";
import Link from "next/link";
import { ActivityBars, ConfirmedBars, SeverityChip, Trend, type Story } from "@/components/Today";
import { SpilloverCard, type Prediction } from "@/components/EarlyWarning";
import { Loading, SourceLink } from "@/components/ui";
import { ago } from "@/lib/format";
import { useView } from "@/lib/view";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

export type Incident = { id: number; text: string; when: string | null; outlets: number; violent: boolean; source_url: string | null };

export default function SimpleCrisis({ story, incidents, spillover, summary, crisis, events, fitTo }: {
  story: Story; incidents: Incident[]; spillover: Prediction[]; summary: string | null;
  crisis: Story & { uid: string }; events?: never[]; fitTo?: [number, number][];
}) {
  const { setMode } = useView();
  const s = story;
  const total7 = s.activity.reduce((a, d) => a + d.violent, 0);
  return (
    <div className="space-y-5">
      <div>
        <Link href="/" className="link text-sm">← Today</Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-xs font-medium uppercase tracking-wide text-muted">{s.kind_label}</div>
            <h1 className="text-2xl font-semibold tracking-tight">{s.headline}</h1>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {s.countries.map((c) => <span key={c.iso3} className="rounded-full bg-raised px-2 py-0.5 text-xs ring-1 ring-inset ring-line">{c.name}</span>)}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5"><SeverityChip s={s.severity} size="md" /><Trend t={s.trend} /></div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <section className="rounded-2xl border border-line bg-surface p-5">
            <h2 className="mb-2 font-semibold">Why it is rated {s.severity.label.toLowerCase()}</h2>
            <ul className="space-y-1.5 text-sm">
              {s.reasons.map((r) => <li key={r} className="flex gap-2"><span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />{r}</li>)}
            </ul>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted"><ConfirmedBars c={s.confidence} /> · {s.confidence.detail}</div>
          </section>

          {summary && (
            <section className="rounded-2xl border border-line bg-surface p-5">
              <h2 className="mb-1 font-semibold">Summary</h2>
              <p className="text-sm text-ink2">{summary}</p>
              <p className="mt-2 text-[11px] text-muted">Written by AI from news reports; may contain errors.</p>
            </section>
          )}

          <section className="rounded-2xl border border-line bg-surface p-5">
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h2 className="font-semibold">Recent incidents</h2>
              <span className="text-xs text-muted">last 3 days · reported by 2+ outlets</span>
            </div>
            {incidents.length === 0 ? <p className="text-sm text-muted">No incident reported by more than one outlet in the last 3 days.</p> : (
              <ul className="divide-y divide-line">
                {incidents.map((i) => (
                  <li key={i.id} className="flex items-start gap-3 py-2.5 text-sm">
                    <span aria-hidden className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${i.violent ? "bg-crit" : "bg-warn"}`} />
                    <div className="min-w-0 flex-1">
                      <div>{i.text}</div>
                      <div className="text-xs text-muted">{ago(i.when)} · {i.outlets} outlets{i.source_url && <> · <SourceLink url={i.source_url}>example report</SourceLink></>}</div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-[11px] text-muted">Incidents are extracted automatically from news and not verified one by one; names are as the news reported them.</p>
          </section>
        </div>

        <aside className="space-y-4">
          <section className="rounded-2xl border border-line bg-surface p-4">
            <h2 className="font-semibold">Violent reports, last 7 days</h2>
            <p className="mb-3 text-xs text-muted">{total7} in total · today is the darker bar</p>
            <ActivityBars data={s.activity} height={70} />
            <div className="mt-1 flex justify-between text-[10px] text-muted">
              <span>{s.activity[0] && new Date(s.activity[0].date + "T00:00:00Z").toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" })}</span>
              <span>today</span>
            </div>
          </section>
          <section className="overflow-hidden rounded-2xl border border-line bg-surface">
            <WorldMap crises={[crisis]} events={events} height={240} fitTo={fitTo} />
          </section>
          {spillover?.length > 0 && <SpilloverCard items={spillover} />}
          <p className="text-xs text-muted">
            Want the scores, raw evidence and sources? <button className="link" onClick={() => setMode("analyst")}>Turn on Analyst view</button>
          </p>
        </aside>
      </div>
    </div>
  );
}
