"use client";

import { use } from "react";
import SimpleCrisis, { type Incident } from "@/components/SimpleCrisis";
import type { Prediction } from "@/components/EarlyWarning";
import type { Story } from "@/components/Today";
import { State } from "@/components/ui";
import { qs, useApi } from "@/lib/api";

type Detail = { uid: string; summary: string | null; spillover_risk: Prediction[]; story: Story; recent_incidents: Incident[] };

export default function CrisisPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const d = useApi<Detail>(`/api/v1/crises/${id}`, { refreshInterval: 120_000 });
  const mapEvents = useApi<{ features: never[] }>(`/api/v1/events${qs({ view: "map", crisis: id, limit: 300, start: "2000-01-01" })}`);
  return (
    <State {...d} what="situation">{(c) => {
      const pts = (mapEvents.data?.features || []).map((f: { geometry: { coordinates: [number, number] } }) => f.geometry.coordinates);
      return (
        <SimpleCrisis story={c.story} incidents={c.recent_incidents || []} spillover={c.spillover_risk} summary={c.summary}
          crisis={{ ...c.story, uid: c.uid }} events={mapEvents.data?.features} fitTo={pts.length ? pts : undefined} />
      );
    }}</State>
  );
}
