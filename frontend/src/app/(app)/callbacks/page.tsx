"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Callback, CallbackStatus } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";
import { PageHeader, Badge, Select, Label, Spinner } from "@/components/ui";
import { DataTable, ColumnDef } from "@/components/data-table";
import { RequireHospital } from "@/components/require-hospital";

const ALL_STATUSES: CallbackStatus[] = ["pending", "scheduled", "completed", "cancelled"];

function statusTone(s: CallbackStatus): "yellow" | "blue" | "green" | "red" | "gray" {
  if (s === "pending") return "yellow";
  if (s === "scheduled") return "blue";
  if (s === "completed") return "green";
  if (s === "cancelled") return "red";
  return "gray";
}

function CallbacksInner({ hospitalId }: { hospitalId: string }) {
  const [statusFilter, setStatusFilter] = React.useState<CallbackStatus | "">("");

  const { data = [], isLoading } = useQuery({
    queryKey: ["callbacks", hospitalId, statusFilter],
    queryFn: () => api.listCallbacks(hospitalId, statusFilter || undefined),
  });

  const columns: ColumnDef<Callback, unknown>[] = [
    {
      header: "Patient",
      accessorKey: "patient_name",
      cell: ({ row }) => row.original.patient_name ?? <span className="text-gray-400">—</span>,
    },
    {
      header: "Phone",
      accessorKey: "patient_phone",
    },
    {
      header: "Reason",
      accessorKey: "reason",
      cell: ({ row }) => row.original.reason ?? <span className="text-gray-400">—</span>,
    },
    {
      header: "Preferred Time",
      accessorKey: "preferred_time",
      cell: ({ row }) =>
        row.original.preferred_time
          ? fmtDateTime(row.original.preferred_time)
          : <span className="text-gray-400">—</span>,
    },
    {
      header: "Status",
      accessorKey: "status",
      cell: ({ row }) => (
        <Badge tone={statusTone(row.original.status)}>{row.original.status}</Badge>
      ),
    },
    {
      header: "Created",
      accessorKey: "created_at",
      cell: ({ row }) =>
        row.original.created_at ? fmtDateTime(row.original.created_at) : <span className="text-gray-400">—</span>,
    },
  ];

  return (
    <div>
      <PageHeader title="Callbacks" />
      <div className="mb-4 flex items-center gap-2">
        <Label className="text-sm">Status</Label>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as CallbackStatus | "")}
          className="w-44"
        >
          <option value="">All</option>
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>
      ) : (
        <DataTable
          columns={columns}
          data={data}
          searchPlaceholder="Search callbacks…"
          emptyTitle="No callbacks found"
        />
      )}
    </div>
  );
}

export default function CallbacksPage() {
  return (
    <RequireHospital>
      {(hospitalId) => <CallbacksInner hospitalId={hospitalId} />}
    </RequireHospital>
  );
}
