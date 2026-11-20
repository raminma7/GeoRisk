"use client";

/* "Crisis in <country>": how serious, why, what happened recently, where, and the model's
   risk of a new conflict. */

import dynamic from "next/dynamic";
import Link from "next/link";
import { use } from "react";
import { CountryRiskCard } from "@/components/EarlyWarning";
import type { Incident } from "@/components/SimpleCrisis";
import { ActivityBars, ConfirmedBars, SeverityChip, Trend, type CountryStory } from "@/components/Today";
import { Loading, SourceLink, State } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { ago } from "@/lib/format";

const WorldMap = dynamic(() => import("@/components/WorldMap"), { ssr: false, loading: () => <Loading what="map" /> });

type Country = {
  country: { iso3: string; name: string; region: string };
  story: CountryStory | null;
  recent_incidents: Incident[];
  ml_risk: Parameters<typeof CountryRiskCard>[0]["risk"];
};

const weekAgo = () => new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10);

export default function CountryPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const d = useApi<Country>(`/api/v1/countries/${code}`, { refreshInterval: 120_000 });
  const iso3 = d.data?.country.iso3;
  const ev = useApi<{ features: never[] }>(iso3 ? `/api/v1/events${qs({ view: "map", located_in: iso3, limit: 300, start: weekAgo() })}` : null);
  return (
    <State {...d} what="country">{(c) => {
      const s = c.story;
      const pts = (ev.data?.features || []).map((f: { geometry: { coordinates: [number, number] } }) => f.geometry.coordinates);
      const total7 = s ? s.activity.reduce((a, x) => a + x.violent, 0) : 0;
      return (
        <div className="space-y-5">
          <div>
            <Link href="/" className="link text-sm">← Today</Link>
            <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs font-medium uppercase tracking-wide text-muted">{s ? s.kind_label : c.country.region}</div>
                <h1 className="text-2xl font-semibold tracking-tight">{s ? s.headline : c.country.name}</h1>
                {s && s.also_involved.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted">also involves
                    {s.also_involved.map((x) => <Link key={x.iso3} href={`/geopolitics/countries/${x.iso3}`} className="rounded-full bg-raised px-2 py-0.5 text-ink ring-1 ring-inset ring-line hover:text-accent">{x.name}</Link>)}
                  </div>
                )}
              </div>
              {s && <div className="flex flex-col items-end gap-1.5"><SeverityChip s={s.severity} size="md" /><Trend t={s.trend} /></div>}
            </div>
          </div>

          {!s ? (
            <section className="rounded-2xl border border-line bg-surface p-5 text-sm text-muted">No crisis is being followed in {c.country.name} right now.</section>
          ) : (
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="space-y-4 lg:col-span-2">
                <section className="rounded-2xl border border-line bg-surface p-5">
                  <h2 className="mb-2 font-semibold">Why it is rated {s.severity.label.toLowerCase()}</h2>
                  <ul className="space-y-1.5 text-sm">
                    {s.reasons.map((r) => <li key={r} className="flex gap-2"><span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />{r}</li>)}
                  </ul>
                  <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted"><ConfirmedBars c={s.confidence} /> · {s.confidence.detail}</div>
                </section>

                <section className="rounded-2xl border border-line bg-surface p-5">
                  <div className="mb-2 flex items-baseline justify-between gap-2">
                    <h2 className="font-semibold">Recent events in {c.country.name}</h2>
                    <span className="text-xs text-muted">last 3 days · reported by 2+ outlets</span>
                  </div>
                  {c.recent_incidents.length === 0 ? <p className="text-sm text-muted">No event reported by more than one outlet in the last 3 days.</p> : (
                    <ul className="divide-y divide-line">
                      {c.recent_incidents.map((i) => (
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
                  <p className="mt-2 text-[11px] text-muted">Events are extracted automatically from news and not verified one by one.</p>
                </section>

              </div>

              <aside className="space-y-4">
                <section className="rounded-2xl border border-line bg-surface p-4">
                  <h2 className="font-semibold">Violent reports, last 7 days</h2>
                  <p className="mb-3 text-xs text-muted">{total7} in total · today is the darker bar</p>
                  <ActivityBars data={s.activity} height={70} />
                </section>
                <section className="overflow-hidden rounded-2xl border border-line bg-surface">
                  <WorldMap events={ev.data?.features} height={240} fitTo={pts.length ? pts : undefined} />
                </section>
                <CountryRiskCard risk={c.ml_risk} />
              </aside>
            </div>
          )}
          {!s && <CountryRiskCard risk={c.ml_risk} />}
        </div>
      );
    }}</State>
  );
}
