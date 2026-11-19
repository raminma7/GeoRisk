"use client";

import maplibregl, { GeoJSONSource, Map as MLMap } from "maplibre-gl";
import { useEffect, useRef } from "react";
import { feature } from "topojson-client";
import countries110 from "world-atlas/countries-110m.json";

export type CrisisMarker = { id: number; uid: string; title: string; latitude: number | null; longitude: number | null;
  priority: { score: number; level: string; direction: string } };
export type EventFeature = { type: "Feature"; geometry: { type: "Point"; coordinates: [number, number] };
  properties: Record<string, unknown> };

const LEVEL_COLOR = ["match", ["get", "level"], "critical", "#d03b3b", "high", "#ec835a", "elevated", "#fab219", "#8f8e88"];

function cssVar(name: string, fallback: string) {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// A self-contained basemap: Natural Earth 110m country polygons (public domain, via world-atlas). No tile server.
const LAND_RAW = feature(countries110 as never, (countries110 as never as { objects: { countries: never } }).objects.countries) as unknown as GeoJSON.FeatureCollection;

// Rings that cross the antimeridian (Russia, Fiji) jump from +180 to -180 and render as a band across the
// map; shift their western points by +360° so each ring stays continuous. Antarctica is left as is.
function unwrapAntimeridian(fc: GeoJSON.FeatureCollection): GeoJSON.FeatureCollection {
  const fixRing = (ring: number[][]) => {
    const jumps = ring.some((p, i) => i > 0 && Math.abs(p[0] - ring[i - 1][0]) > 180);
    return jumps ? ring.map(([x, y]) => [x < 0 ? x + 360 : x, y]) : ring;
  };
  return {
    ...fc,
    features: fc.features.map((f) => {
      if (String(f.id) === "010" || !f.geometry) return f;
      const g = f.geometry as GeoJSON.Polygon | GeoJSON.MultiPolygon;
      const coordinates = g.type === "Polygon"
        ? g.coordinates.map((r) => fixRing(r as number[][]))
        : g.coordinates.map((poly) => poly.map((r) => fixRing(r as number[][])));
      return { ...f, geometry: { ...g, coordinates } as GeoJSON.Geometry };
    }),
  };
}
const LAND = unwrapAntimeridian(LAND_RAW);

export default function WorldMap({ crises = [], events, highlight = [], height = 420, onCrisisClick, fitTo, fills, onCountryClick, fillTitles }: {
  crises?: CrisisMarker[]; events?: EventFeature[]; highlight?: (number | string)[]; height?: number;
  onCrisisClick?: (id: number) => void; fitTo?: [number, number][];
  /** choropleth: ISO numeric id -> color */
  fills?: Record<string, string>; onCountryClick?: (isoNumeric: string) => void;
  /** tooltip text per ISO numeric id (choropleth mode) */
  fillTitles?: Record<string, string>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const ready = useRef(false);
  const latest = useRef({ crises, events, highlight, fills, fillTitles, onCountryClick });
  latest.current = { crises, events, highlight, fills, fillTitles, onCountryClick };

  const sync = () => {
    const m = map.current;
    if (!m || !ready.current) return;
    const { crises: cs, events: ev, highlight: hl } = latest.current;
    (m.getSource("crises") as GeoJSONSource)?.setData({
      type: "FeatureCollection",
      features: cs.filter((c) => c.latitude !== null && c.longitude !== null).map((c) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [c.longitude as number, c.latitude as number] },
        properties: { id: c.id, uid: c.uid, title: c.title, score: c.priority.score, level: c.priority.level, direction: c.priority.direction },
      })),
    });
    (m.getSource("events") as GeoJSONSource)?.setData({ type: "FeatureCollection", features: (ev || []) as never });
    const hset = new Set(hl.map((x) => String(x).padStart(3, "0")));
    m.setFilter("land-active", ["in", ["get", "_id"], ["literal", Array.from(hset)]]);
    const f = latest.current.fills;
    if (f && Object.keys(f).length) {
      const pairs = Object.entries(f).flatMap(([k, v]) => [k.padStart(3, "0"), v]);
      m.setPaintProperty("land-risk", "fill-color", ["match", ["get", "_id"], ...pairs, "rgba(0,0,0,0)"] as never);
      m.setPaintProperty("land-risk", "fill-opacity", 1);
    }
  };

  useEffect(() => {
    if (!ref.current || map.current) return;
    const land = { ...LAND, features: LAND.features.map((f) => ({ ...f, properties: { ...(f.properties || {}), _id: String(f.id ?? "") } })) };
    const m = new maplibregl.Map({
      container: ref.current,
      style: {
        version: 8,
        sources: {
          land: { type: "geojson", data: land as never },
          crises: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
          events: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
        },
        layers: [
          { id: "water", type: "background", paint: { "background-color": cssVar("--water", "#f4f6f8") } },
          { id: "land", type: "fill", source: "land", paint: { "fill-color": cssVar("--land", "#e4e3de") } },
          { id: "land-active", type: "fill", source: "land", filter: ["in", ["get", "_id"], ["literal", []]],
            paint: { "fill-color": cssVar("--land-active", "#cdd9ea") } },
          { id: "land-risk", type: "fill", source: "land", paint: { "fill-color": "rgba(0,0,0,0)", "fill-opacity": 0 } },
          { id: "borders", type: "line", source: "land", paint: { "line-color": cssVar("--chart-surface", "#fff"), "line-width": 0.6 } },
          { id: "events", type: "circle", source: "events", paint: {
            "circle-radius": ["interpolate", ["linear"], ["get", "severity"], 0, 2.5, 1, 5],
            "circle-color": cssVar("--series-1", "#2a78d6"),
            "circle-opacity": ["match", ["get", "verification_status"], "multi_source_reported", 0.75, 0.35],
            "circle-stroke-width": 0 } },
          { id: "crises", type: "circle", source: "crises", paint: {
            "circle-radius": ["interpolate", ["linear"], ["get", "score"], 0, 6, 100, 18],
            "circle-color": LEVEL_COLOR as never, "circle-opacity": 0.85,
            "circle-stroke-color": cssVar("--chart-surface", "#fff"), "circle-stroke-width": 2 } },
        ],
      },
      center: [20, 25], zoom: 1, minZoom: 0.6, maxZoom: 9, attributionControl: false, renderWorldCopies: false,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "Natural Earth · GDELT" }));
    m.on("load", () => { ready.current = true; sync(); });
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
    for (const layer of ["crises", "events"]) {
      m.on("mouseenter", layer, (e) => {
        m.getCanvas().style.cursor = "pointer";
        const p = e.features?.[0]?.properties as Record<string, unknown>;
        if (!p) return;
        // Titles come from external news data: build DOM with textContent only (never innerHTML).
        const lines = layer === "crises"
          ? [`Review priority ${Number(p.score).toFixed(1)} · ${p.level} · ${String(p.direction).replace("_", "-")}`]
          : [`${p.date} · ${p.reports} reports · ${p.sources} outlets`, String(p.verification_status).replace(/_/g, " ")];
        const div = document.createElement("div");
        div.style.fontSize = "12px";
        div.style.maxWidth = "260px";
        const strong = document.createElement("strong");
        strong.textContent = String(p.title ?? "");
        div.appendChild(strong);
        for (const l of lines) {
          const line = document.createElement("div");
          line.textContent = l;
          div.appendChild(line);
        }
        popup.setLngLat(e.lngLat).setDOMContent(div).addTo(m);
      });
      m.on("mouseleave", layer, () => { m.getCanvas().style.cursor = ""; popup.remove(); });
    }
    m.on("mousemove", "land-risk", (e) => {
      const t = latest.current.fillTitles;
      const id = String(e.features?.[0]?.properties?._id ?? "");
      if (!t || !t[id] || m.queryRenderedFeatures(e.point, { layers: ["crises", "events"] }).length) return;
      m.getCanvas().style.cursor = "pointer";
      const div = document.createElement("div");
      div.style.fontSize = "12px";
      div.textContent = t[id];
      popup.setLngLat(e.lngLat).setDOMContent(div).addTo(m);
    });
    m.on("mouseleave", "land-risk", () => { m.getCanvas().style.cursor = ""; popup.remove(); });
    m.on("click", "land-risk", (e) => {
      const id = String(e.features?.[0]?.properties?._id ?? "");
      if (id && latest.current.onCountryClick && latest.current.fillTitles?.[id]) latest.current.onCountryClick(id);
    });
    m.on("click", "crises", (e) => {
      const id = Number(e.features?.[0]?.properties?.id);
      if (id && onCrisisClick) onCrisisClick(id);
    });
    map.current = m;
    // Keep the canvas matched to its container (grid layouts can settle after the map is created).
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); m.remove(); map.current = null; ready.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { sync(); }, [crises, events, highlight, fills]);

  useEffect(() => {
    const m = map.current;
    if (!m || !fitTo || fitTo.length === 0) return;
    const b = new maplibregl.LngLatBounds();
    fitTo.forEach((p) => b.extend(p));
    const go = () => m.fitBounds(b, { padding: 60, maxZoom: 5, duration: 0 });
    if (ready.current) go(); else m.once("load", go);
  }, [fitTo]);

  return <div ref={ref} style={{ height }} className="w-full overflow-hidden rounded-lg border border-line" aria-label="World map" role="img" />;
}
