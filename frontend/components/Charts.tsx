"use client";

/* Charts follow the dataviz reference palette: categorical slots in fixed order
   (blue, orange, aqua) for quad classes, single blue for magnitude, status colors
   only for priority levels. Thin marks, recessive grid, hover tooltips. */

import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

const axis = { stroke: "var(--chart-text)", fontSize: 11, tickLine: false, axisLine: false } as const;
const tooltipStyle = {
  contentStyle: { background: "rgb(var(--raised))", border: "1px solid rgb(var(--line))", borderRadius: 8, fontSize: 12,
    color: "rgb(var(--ink))" },
  labelStyle: { color: "rgb(var(--ink))", fontWeight: 600 },
  itemStyle: { color: "rgb(var(--ink2))" },
};

export type DailyPoint = { date: string; material_conflict: number; verbal_conflict: number; cooperation: number };

const SERIES = [
  { key: "material_conflict", name: "Material conflict", color: "var(--series-1)" },
  { key: "verbal_conflict", name: "Verbal conflict", color: "var(--series-2)" },
  { key: "cooperation", name: "Cooperation", color: "var(--series-3)" },
];

export function DailyStack({ data, height = 220 }: { data: DailyPoint[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="date" {...axis} minTickGap={24} />
        <YAxis {...axis} allowDecimals={false} />
        <Tooltip {...tooltipStyle} cursor={{ fill: "rgb(var(--line) / 0.35)" }} />
        <Legend wrapperStyle={{ fontSize: 12, color: "var(--chart-text)" }} iconType="square" iconSize={10} />
        {SERIES.map((s, i) => (
          <Bar key={s.key} dataKey={s.key} name={s.name} stackId="a" fill={s.color} stroke="var(--chart-surface)" strokeWidth={1}
            radius={i === SERIES.length - 1 ? [4, 4, 0, 0] : 0} maxBarSize={28} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function MultiLine({ data, keys, height = 240 }: { data: Record<string, number | string>[]; keys: { key: string; name: string }[]; height?: number }) {
  // Color follows the entity (selection order), never rank; > 8 series share the last neutral.
  const colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="date" {...axis} minTickGap={24} />
        <YAxis {...axis} allowDecimals={false} />
        <Tooltip {...tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 12 }} iconType="plainline" />
        {keys.map((k, i) => (
          <Line key={k.key} type="linear" dataKey={k.key} name={k.name} dot={false} strokeWidth={2}
            stroke={colors[i] || "#8f8e88"} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--chart-surface)" }} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function PriorityHistory({ data, levels, height = 200 }: { data: { as_of: string; priority: number }[];
  levels?: Record<string, number>; height?: number }) {
  const lv = levels || { critical: 75, high: 55, elevated: 35 };
  const fmt = data.map((d) => ({ ...d, t: new Date(d.as_of).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit" }) }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={fmt} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="t" {...axis} minTickGap={30} />
        <YAxis domain={[0, 100]} {...axis} />
        <Tooltip {...tooltipStyle} formatter={(v: number) => [v.toFixed(1), "Review priority"]} />
        {Object.entries(lv).map(([k, v]) => (
          <ReferenceLine key={k} y={v} stroke="var(--chart-grid)" strokeDasharray="3 3"
            label={{ value: k, position: "insideTopRight", fontSize: 10, fill: "var(--chart-text)" }} />
        ))}
        <Area type="linear" dataKey="priority" stroke="var(--series-1)" strokeWidth={2} fill="var(--series-1)" fillOpacity={0.12}
          dot={fmt.length < 3 ? { r: 4 } : false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function ComponentBars({ components }: { components: Record<string, { value: number; weight: number; contribution: number }> }) {
  const data = Object.entries(components)
    .filter(([k, v]) => !k.startsWith("_") && typeof v === "object" && "contribution" in v)
    .map(([k, v]) => ({ name: k.replace(/_/g, " "), contribution: v.contribution, value: v.value, weight: v.weight }))
    .sort((a, b) => b.contribution - a.contribution);
  return (
    <ResponsiveContainer width="100%" height={Math.max(160, data.length * 30)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 10 }}>
        <XAxis type="number" hide domain={[0, "dataMax"]} />
        <YAxis type="category" dataKey="name" width={150} {...axis} />
        <Tooltip {...tooltipStyle} cursor={{ fill: "rgb(var(--line) / 0.35)" }}
          formatter={(v: number, _n, p) => [`${v.toFixed(1)} pts (value ${(p.payload.value as number).toFixed(2)} × weight ${p.payload.weight})`, "Contribution"]} />
        <Bar dataKey="contribution" radius={[0, 4, 4, 0]} maxBarSize={16} label={{ position: "right", fontSize: 11, fill: "var(--chart-text)", formatter: (v: number) => v.toFixed(1) }}>
          {data.map((d) => <Cell key={d.name} fill="var(--series-1)" />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
