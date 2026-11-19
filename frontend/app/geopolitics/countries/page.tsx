"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, State } from "@/components/ui";
import { qs, useApi } from "@/lib/api";

export default function CountriesPage() {
  const [q, setQ] = useState("");
  const [region, setRegion] = useState("");
  const regions = useApi<{ items: { code: string; name: string }[] }>("/api/v1/regions?kind=un_subregion");
  const res = useApi<{ total: number; items: { iso3: string; name: string; region: string; continent: string }[] }>(
    `/api/v1/countries${qs({ q, region, limit: 300 })}`);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Countries</h1>
      <div className="flex flex-wrap gap-2">
        <input className="input w-64" placeholder="Search by name, alias or code" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" value={region} onChange={(e) => setRegion(e.target.value)} aria-label="Region">
          <option value="">All regions</option>
          {(regions.data?.items || []).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
        </select>
      </div>
      <Card>
        <State {...res} what="countries" empty={(d) => d.items.length === 0}>{(d) => (
          <ul className="grid gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-4">
            {d.items.map((c) => (
              <li key={c.iso3}><Link className="text-sm hover:underline" href={`/geopolitics/countries/${c.iso3}`}>{c.name}</Link>
                <span className="ml-1 text-[11px] text-muted">{c.iso3} · {c.region}</span></li>
            ))}
          </ul>
        )}</State>
      </Card>
    </div>
  );
}
