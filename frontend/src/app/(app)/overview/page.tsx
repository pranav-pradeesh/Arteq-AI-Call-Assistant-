"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fmtDateTime, fmtMs, paiseToRupees } from "@/lib/utils";
import { RequireHospital } from "@/components/require-hospital";
import { StatCard } from "@/components/stat-card";
import { DataTable, ColumnDef } from "@/components/data-table";
import { PageHeader, EmptyState, Spinner, Badge, Card, CardBody, CardHeader } from "@/components/ui";
import type { CallLog } from "@/lib/types";

function OverviewInner({ hospitalId }: { hospitalId: string }) {
  const statsQuery = useQuery({
    queryKey: ["stats", hospitalId],
    queryFn: () => api.getStats(hospitalId),
  });

  const callsQuery = useQuery({
    queryKey: ["calls", hospitalId, 20],
    queryFn: () => api.listCalls(hospitalId, 20),
  });

  const activeQuery = useQuery({
    queryKey: ["activeCalls", hospitalId],
    queryFn: () => api.activeCalls(hospitalId),
    retry: false,
    refetchInterval: 10_000,
  });

  const stats = statsQuery.data;

  const callColumns: ColumnDef<CallLog, any>[] = [
    {
      header: "Started",
      accessorKey: "started_at",
      cell: ({ getValue }) => fmtDateTime(getValue()),
    },
    {
      header: "Caller",
      accessorKey: "caller",
      cell: ({ getValue }) => <span className="font-mono text-xs">{getValue() ?? "—"}</span>,
    },
    {
      header: "Turns",
      accessorKey: "total_turns",
    },
    {
      header: "Latency",
      accessorKey: "latency_avg_ms",
      cell: ({ getValue }) => fmtMs(getValue()),
    },
    {
      header: "Cost",
      accessorKey: "cost_paise",
      cell: ({ getValue }) => paiseToRupees(getValue()),
    },
    {
      header: "Outcome",
      accessorKey: "outcome",
      cell: ({ getValue }) => {
        const v: string | null = getValue();
        return v ? <Badge tone="blue">{v}</Badge> : <span className="text-gray-400">—</span>;
      },
    },
    {
      header: "",
      id: "link",
      cell: ({ row }) => (
        <Link
          href={`/calls/${row.original.id}`}
          className="text-xs text-indigo-600 hover:underline"
        >
          View
        </Link>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title="Overview" />

      {/* KPI row */}
      <div className="flex flex-wrap gap-4">
        {statsQuery.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Spinner /> Loading stats…
          </div>
        ) : statsQuery.isError ? (
          <EmptyState title="Could not load stats" hint="Check your connection and try again." />
        ) : stats ? (
          <>
            <StatCard label="Total Calls" value={String(stats.total_calls)} />
            <StatCard label="Avg Latency" value={fmtMs(stats.avg_latency_ms)} />
            <StatCard label="Avg Turns" value={stats.avg_turns.toFixed(1)} />
            <StatCard label="Transfers" value={String(stats.transfers)} />
          </>
        ) : null}
      </div>

      {/* Active calls strip */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-gray-700">Active Calls</h2>
        </CardHeader>
        <CardBody>
          {activeQuery.isError ? (
            <EmptyState
              title="Live calls not available yet"
              hint="Active call monitoring ships with the backend additions."
            />
          ) : activeQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Spinner /> Loading…
            </div>
          ) : !activeQuery.data || activeQuery.data.length === 0 ? (
            <EmptyState title="No active calls" hint="Active calls will appear here in real time." />
          ) : (
            <div className="flex flex-wrap gap-3">
              {activeQuery.data.map((c) => (
                <div
                  key={c.id}
                  className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm"
                >
                  <span className="font-mono text-xs text-gray-600">{c.caller ?? "Unknown"}</span>
                  {c.outcome && <Badge tone="green" className="ml-2">{c.outcome}</Badge>}
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      {/* Recent calls table */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-gray-700">Recent Calls</h2>
        </CardHeader>
        <CardBody>
          {callsQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Spinner /> Loading calls…
            </div>
          ) : callsQuery.isError ? (
            <EmptyState title="Could not load calls" hint="Check your connection and try again." />
          ) : (
            <DataTable
              columns={callColumns}
              data={callsQuery.data ?? []}
              pageSize={20}
              emptyTitle="No calls recorded yet"
            />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

export default function OverviewPage() {
  return (
    <RequireHospital>
      {(hid) => <OverviewInner hospitalId={hid} />}
    </RequireHospital>
  );
}
