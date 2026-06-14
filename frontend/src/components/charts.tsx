"use client";
import * as React from "react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, PieChart, Pie, Cell,
} from "recharts";
import { EmptyState } from "@/components/ui";
import type { AnalyticsPoint } from "@/lib/types";

const BRAND = "#6366f1";
const COLORS = [
  "#6366f1", "#22d3ee", "#f59e0b", "#10b981", "#ef4444",
  "#8b5cf6", "#f97316", "#14b8a6", "#ec4899", "#84cc16",
];

// ── Calls Trend ─────────────────────────────────────────────────────────────

export function CallsTrend({ data }: { data?: AnalyticsPoint[] }) {
  if (!data || data.length === 0) {
    return <EmptyState title="No call trend data" hint="Data will appear once calls are recorded." />;
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
        <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
        <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
        <Tooltip
          contentStyle={{ fontSize: 12, borderRadius: 8 }}
          labelFormatter={(l) => `Bucket: ${l}`}
        />
        <Line
          type="monotone"
          dataKey="calls"
          stroke={BRAND}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Latency Trend ────────────────────────────────────────────────────────────

export function LatencyTrend({ data }: { data?: AnalyticsPoint[] }) {
  if (!data || data.length === 0) {
    return <EmptyState title="No latency data" hint="Data will appear once calls are recorded." />;
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
        <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
        <YAxis tick={{ fontSize: 11 }} unit="ms" />
        <Tooltip
          contentStyle={{ fontSize: 12, borderRadius: 8 }}
          formatter={(v: number) => [`${v}ms`, "Avg Latency"]}
          labelFormatter={(l) => `Bucket: ${l}`}
        />
        <Line
          type="monotone"
          dataKey="avg_latency_ms"
          stroke="#22d3ee"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Cost Trend ───────────────────────────────────────────────────────────────

function costInRupees(paise: number) {
  return paise / 100;
}

export function CostTrend({ data }: { data?: AnalyticsPoint[] }) {
  if (!data || data.length === 0) {
    return <EmptyState title="No cost data" hint="Data will appear once calls are recorded." />;
  }
  const mapped = data.map((d) => ({ ...d, cost_rupees: costInRupees(d.cost_paise) }));
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={mapped} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
        <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={(v) => v.slice(5)} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
        <Tooltip
          contentStyle={{ fontSize: 12, borderRadius: 8 }}
          formatter={(v: number) => [`₹${v.toFixed(2)}`, "Cost"]}
          labelFormatter={(l) => `Bucket: ${l}`}
        />
        <Bar dataKey="cost_rupees" fill="#f59e0b" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Breakdown Bar ────────────────────────────────────────────────────────────

export function BreakdownBar({
  data,
  title,
}: {
  data?: Record<string, number>;
  title?: string;
}) {
  if (!data || Object.keys(data).length === 0) {
    return (
      <EmptyState
        title={title ? `No ${title.toLowerCase()} data` : "No breakdown data"}
      />
    );
  }
  const chartData = Object.entries(data).map(([name, value]) => ({ name, value }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 24, left: 8, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
        <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
        <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={90} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
        <Bar dataKey="value" fill={BRAND} radius={[0, 3, 3, 0]}>
          {chartData.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Breakdown Pie ────────────────────────────────────────────────────────────

export function BreakdownPie({ data }: { data?: Record<string, number> }) {
  if (!data || Object.keys(data).length === 0) {
    return <EmptyState title="No breakdown data" />;
  }
  const chartData = Object.entries(data).map(([name, value]) => ({ name, value }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie
          data={chartData}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          outerRadius={80}
          label={({ name, percent }) =>
            `${name} ${(percent * 100).toFixed(0)}%`
          }
          labelLine={true}
        >
          {chartData.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}
