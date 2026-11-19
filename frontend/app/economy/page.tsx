"use client";

import { Card, State } from "@/components/ui";
import { useApi } from "@/lib/api";

type Ind = { status: string; items: { indicator_key: string; name: string; source_code: string; unit: string; frequency: string; aggregation_method: string }[]; note: string };

export default function EconomyPage() {
  const ind = useApi<Ind>("/api/v1/economy/indicators");
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Economy <span className="ml-1 rounded bg-line px-1.5 py-0.5 align-middle text-xs uppercase text-muted">planned</span></h1>
        <p className="text-sm text-muted">The Economic Intelligence module (country &amp; global macro, trade, energy, sanctions and combined geopolitical-economic analysis)
          is planned for later phases. The shared schema, country registry, connectors and API routes are in place; <b>no economic statistics are shown</b> until real data is ingested.</p>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {["Country economic dashboard", "Global economic dashboard", "Economic comparison", "Economic news intelligence", "Economic alerts", "AI economic reports"].map((t) => (
          <div key={t} className="card opacity-70"><div className="font-medium">{t}</div><div className="text-xs text-muted">Planned · disabled</div></div>
        ))}
      </div>
      <Card title="Planned indicator definitions (metadata only)">
        <State {...ind} what="indicators">{(d) => (<>
          <table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Indicator</th><th>Source code</th><th>Unit</th><th>Frequency</th><th>Aggregation</th></tr></thead>
            <tbody>{d.items.map((i) => <tr key={i.indicator_key} className="border-t border-line"><td className="py-1">{i.name}</td><td className="text-xs">{i.source_code}</td>
              <td className="text-xs">{i.unit}</td><td className="text-xs">{i.frequency}</td><td className="text-xs">{i.aggregation_method.replace(/_/g, " ")}</td></tr>)}</tbody></table>
          <p className="mt-2 text-xs text-muted">{d.note}</p></>)}</State>
      </Card>
    </div>
  );
}
