"use client";

import Link from "next/link";
import { useState } from "react";
import { CountryPicker, PriorityCard, type CrisisCard } from "@/components/domain";
import { Card, State } from "@/components/ui";
import { useApi } from "@/lib/api";
import { useRouter } from "next/navigation";

export default function GeopoliticsHub() {
  const router = useRouter();
  const [sel, setSel] = useState<string[]>([]);
  const crises = useApi<{ items: CrisisCard[] }>("/api/v1/crises?limit=8");
  const regions = useApi<{ items: { code: string; name: string; kind: string }[] }>("/api/v1/regions?kind=un_subregion");
  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Geopolitics</h1>
      <div className="grid gap-4 md:grid-cols-3">
        <Card title="Single or multi-country analysis" className="md:col-span-2">
          <p className="mb-2 text-sm text-muted">Select any number of countries. One country opens its intelligence page; several open the comparison.</p>
          <CountryPicker value={sel} onChange={setSel} />
          <div className="mt-3 flex gap-2">
            <button className="btn-primary" disabled={sel.length === 0}
              onClick={() => router.push(sel.length === 1 ? `/geopolitics/countries/${sel[0]}` : `/geopolitics/compare?c=${sel.join(",")}`)}>
              {sel.length <= 1 ? "Open country" : `Compare ${sel.length} countries`}</button>
            <Link className="btn" href="/geopolitics/countries">Browse all countries</Link>
          </div>
        </Card>
        <Card title="Regional analysis">
          <State {...regions} what="regions">{(d) => (
            <select className="input w-full" defaultValue="" onChange={(e) => e.target.value && router.push(`/geopolitics/compare?region=${encodeURIComponent(e.target.value)}`)}>
              <option value="">Select a region…</option>
              {d.items.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
            </select>
          )}</State>
        </Card>
      </div>
      <section>
        <div className="mb-2 flex items-center justify-between"><h2 className="h2">Crisis analysis</h2>
          <Link className="link text-sm" href="/geopolitics/crises">All crises →</Link></div>
        <State {...crises} what="crises" empty={(d) => d.items.length === 0}>{(d) => (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">{d.items.map((c) => <PriorityCard key={c.id} c={c} />)}</div>
        )}</State>
      </section>
    </div>
  );
}
