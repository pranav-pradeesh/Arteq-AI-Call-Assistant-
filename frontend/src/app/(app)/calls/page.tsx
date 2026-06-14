"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fmtDateTime, fmtMs, paiseToRupees } from "@/lib/utils";
import { RequireHospital } from "@/components/require-hospital";
import { DataTable, ColumnDef } from "@/components/data-table";
import { PageHeader, EmptyState, Spinner, Badge, Card, CardBody } from "@/components/ui";
import type { CallLog } from "@/lib/types";

function deriveDuration(started_at?: string | null, ended_at?: string | null): string {
  if (!started_at || !ended_at) return "—";
  const s = new Date(started_at).getTime();
  const e = new Date(ended_at).getTime();
  if (isNaN(s) || isNaN(e) || e <= s) return "—";
  const diff = e - s;
  const secs = Math.floor(diff / 1000);
  const mins = Math.floor(secs / 60);
  const remSecs = secs % 60;
  return mins > 0 ? `${mins}m ${remSecs}s` : `${secs}s`;
}

const callColumns: ColumnDef<CallLog, any>[] = [
  {
    header: "Started",
    accessorKey: "started_at",
    cell: ({ getValue }) => fmtDateTime(getValue()),
  },
  {
    header: "Caller",
    accessorKey: "caller",
    cell: ({ getValue }) => (
      <span className="font-mono text-xs">{getValue() ?? "—"}</span>
    ),
  },
  {
    header: "Duration",
    id: "duration",
    cell: ({ row }) => deriveDuration(row.original.started_at, row.original.ended_at),
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
      if (!v) return <span className="text-gray-400">—</span>;
      const tone =
        v === "resolved"
          ? "green"
          : v === "transferred"
          ? "blue"
          : v === "dropped"
          ? "red"
          : "gray";
      return <Badge tone={tone as any}>{v}</Badge>;
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

function CallsInner({ hospitalId }: { hospitalId: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["calls", hospitalId, 100],
    queryFn: () => api.listCalls(hospitalId, 100),
  });

  return (
    <div className="space-y-4">
      <PageHeader title="Call Logs" />
      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Spinner /> Loading calls…
        </div>
      ) : isError ? (
        <EmptyState title="Could not load calls" hint="Check your connection and try again." />
      ) : (
        <Card>
          <CardBody>
            <DataTable
              columns={callColumns}
              data={data ?? []}
              pageSize={25}
              emptyTitle="No calls recorded yet"
              searchPlaceholder="Search calls…"
            />
          </CardBody>
        </Card>
      )}
    </div>
  );
}

export default function CallsPage() {
  return (
    <RequireHospital>
      {(hid) => <CallsInner hospitalId={hid} />}
    </RequireHospital>
  );
}
