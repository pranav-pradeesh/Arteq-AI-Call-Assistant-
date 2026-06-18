"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fmtMs, paiseToRupees } from "@/lib/utils";
import { RequireHospital } from "@/components/require-hospital";
import { StatCard } from "@/components/stat-card";
import {
  CallsTrend, LatencyTrend, CostTrend, BreakdownBar, BreakdownPie,
} from "@/components/charts";
import {
  PageHeader, EmptyState, Spinner, Card, CardBody, CardHeader, Label, Select, Input,
} from "@/components/ui";

function toIsoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultRange() {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - 30);
  return { from: toIsoDate(from), to: toIsoDate(to) };
}

function AnalyticsInner({ hospitalId }: { hospitalId: string }) {
  const { from: defaultFrom, to: defaultTo } = defaultRange();
  const [from, setFrom] = React.useState(defaultFrom);
  const [to, setTo] = React.useState(defaultTo);
  const [bucket, setBucket] = React.useState<"day" | "hour">("day");

  const summaryQuery = useQuery({
    queryKey: ["analyticsSummary", hospitalId, from, to],
    queryFn: () => api.analyticsSummary(hospitalId, from, to),
    retry: false,
  });

  const pointsQuery = useQuery({
    queryKey: ["analytics", hospitalId, from, to, bucket],
    queryFn: () => api.analytics(hospitalId, from, to, bucket),
    retry: false,
  });

  const summary = summaryQuery.data;
  const points = pointsQuery.data;

  const summaryUnavailable =
    summaryQuery.isError || (!summaryQuery.isLoading && !summary);
  const pointsUnavailable =
    pointsQuery.isError || (!pointsQuery.isLoading && !points);

  return (
    <div className="space-y-6">
      <PageHeader title="Analytics" />

      {/* Date range + bucket controls */}
      <Card>
        <CardBody>
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <Label htmlFor="an-from">From</Label>
              <Input
                id="an-from"
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                className="w-40"
              />
            </div>
            <div>
              <Label htmlFor="an-to">To</Label>
              <Input
                id="an-to"
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                className="w-40"
              />
            </div>
            <div>
              <Label htmlFor="an-bucket">Bucket</Label>
              <Select
                id="an-bucket"
                value={bucket}
                onChange={(e) => setBucket(e.target.value as "day" | "hour")}
                className="w-28"
              >
                <option value="day">Day</option>
                <option value="hour">Hour</option>
              </Select>
            </div>
          </div>
        </CardBody>
      </Card>

      {/* KPI summary row */}
      {summaryQuery.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Spinner /> Loading summary…
        </div>
      ) : summaryUnavailable ? (
        <EmptyState
          title="Analytics endpoint not available yet"
          hint="Summary ships with the backend additions."
        />
      ) : summary ? (
        <div className="flex flex-wrap gap-4">
          <StatCard
            label="Total Calls"
            value={String(summary.total_calls)}
            delta={summary.delta_calls_pct}
            hint="vs previous period"
          />
          <StatCard
            label="Total Cost"
            value={paiseToRupees(summary.total_cost_paise)}
          />
          <StatCard
            label="Avg Latency"
            value={fmtMs(summary.avg_latency_ms)}
          />
          <StatCard
            label="Avg Turns"
            value={summary.avg_turns.toFixed(1)}
          />
        </div>
      ) : null}

      {/* Trend charts */}
      {pointsQuery.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Spinner /> Loading trends…
        </div>
      ) : pointsUnavailable ? (
        <EmptyState
          title="Analytics endpoint not available yet"
          hint="Trend charts ship with the backend additions."
        />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold text-gray-700">Calls Over Time</h2>
            </CardHeader>
            <CardBody>
              <CallsTrend data={points} />
            </CardBody>
          </Card>
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold text-gray-700">Avg Latency</h2>
            </CardHeader>
            <CardBody>
              <LatencyTrend data={points} />
            </CardBody>
          </Card>
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold text-gray-700">Cost</h2>
            </CardHeader>
            <CardBody>
              <CostTrend data={points} />
            </CardBody>
          </Card>
        </div>
      )}

      {/* Breakdown charts from summary */}
      {!summaryUnavailable && summary && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold text-gray-700">Intents</h2>
            </CardHeader>
            <CardBody>
              <BreakdownBar data={summary.intents} title="Intents" />
            </CardBody>
          </Card>
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold text-gray-700">Languages</h2>
            </CardHeader>
            <CardBody>
              <BreakdownPie data={summary.languages} />
            </CardBody>
          </Card>
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold text-gray-700">Outcomes</h2>
            </CardHeader>
            <CardBody>
              <BreakdownBar data={summary.outcomes} title="Outcomes" />
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}

export default function AnalyticsPage() {
  return (
    <RequireHospital>
      {(hid) => <AnalyticsInner hospitalId={hid} />}
    </RequireHospital>
  );
}
