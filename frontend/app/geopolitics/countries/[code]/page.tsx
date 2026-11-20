"use client";

import Link from "next/link";
import { use } from "react";
import { CountryRiskCard } from "@/components/EarlyWarning";
import { SituationCard, type Story } from "@/components/Today";
import { State } from "@/components/ui";
import { useApi } from "@/lib/api";

type Country = {
  country: { iso3: string; name: string; region: string };
  crises: { id: number; story?: Story }[];
  ml_risk: Parameters<typeof CountryRiskCard>[0]["risk"];
};

export default function CountryPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const d = useApi<Country>(`/api/v1/countries/${code}`);
  return (
    <State {...d} what="country">{(c) => (
      <div className="space-y-4">
        <div>
          <Link href="/" className="link text-sm">← Today</Link>
          <h1 className="text-2xl font-semibold tracking-tight">{c.country.name}</h1>
          <div className="text-xs text-muted">{c.country.region}</div>
        </div>
        <section className="rounded-2xl border border-line bg-surface p-4">
          <h2 className="mb-3 font-semibold">Situations involving {c.country.name}</h2>
          {c.crises.length === 0 ? <p className="text-sm text-muted">No ongoing situation is being followed for this country right now.</p> : (
            <div className="grid gap-3 md:grid-cols-2">
              {c.crises.slice(0, 6).map((x) => x.story ? <SituationCard key={x.id} s={x.story} /> : null)}
            </div>
          )}
        </section>
        <CountryRiskCard risk={c.ml_risk} />
      </div>
    )}</State>
  );
}
