"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Callback, CallbackStatus } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";
import { PageHeader, Badge, Button, Select, Label, Spinner } from "@/components/ui";
import { DataTable, ColumnDef } from "@/components/data-table";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

const ALL_STATUSES: CallbackStatus[] = ["pending", "scheduled", "completed", "cancelled"];

function statusTone(s: CallbackStatus): "yellow" | "blue" | "green" | "red" | "gray" {
  if (s === "pending") return "yellow";
  if (s === "scheduled") return "blue";
  if (s === "completed") return "green";
  if (s === "cancelled") return "red";
  return "gray";
}

function CallbacksInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = React.useState<CallbackStatus | "">("");

  const { data = [], isLoading } = useQuery({
    queryKey: ["callbacks", hospitalId, statusFilter],
    queryFn: () => api.listCallbacks(hospitalId, statusFilter || undefined),
  });

  const statusMut = useMutation({
    mutationFn: (v: { id: string; status: string }) => api.updateCallbackStatus(hospitalId, v.id, v.status),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["callbacks", hospitalId] }); toast("Callback updated", "ok"); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const redialMut = useMutation({
    mutationFn: (id: string) => api.redialCallback(hospitalId, id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["callbacks", hospitalId] }); toast("Re-dial queued — the AI will call back", "ok"); },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const busy = statusMut.isPending || redialMut.isPending;

  const columns: ColumnDef<Callback, unknown>[] = [
    { header: "Patient", accessorKey: "patient_name",
      cell: ({ row }) => row.original.patient_name ?? <span className="text-gray-400">—</span> },
    { header: "Phone", accessorKey: "patient_phone" },
    { header: "Reason", accessorKey: "reason",
      cell: ({ row }) => row.original.reason ?? <span className="text-gray-400">—</span> },
    { header: "Preferred Time", accessorKey: "preferred_time",
      cell: ({ row }) => row.original.preferred_time ? fmtDateTime(row.original.preferred_time) : <span className="text-gray-400">—</span> },
    { header: "Status", accessorKey: "status",
      cell: ({ row }) => <Badge tone={statusTone(row.original.status)}>{row.original.status}</Badge> },
    { header: "Created", accessorKey: "created_at",
      cell: ({ row }) => row.original.created_at ? fmtDateTime(row.original.created_at) : <span className="text-gray-400">—</span> },
    {
      header: "Actions",
      id: "actions",
      cell: ({ row }) => {
        const cb = row.original;
        return (
          <div className="flex gap-1.5">
            <Button variant="outline" className="px-2 py-1 text-xs" disabled={busy}
                    onClick={() => redialMut.mutate(cb.id)}>Re-dial</Button>
            {cb.status !== "completed" && (
              <Button variant="outline" className="px-2 py-1 text-xs" disabled={busy}
                      onClick={() => statusMut.mutate({ id: cb.id, status: "completed" })}>Done</Button>
            )}
            {cb.status !== "cancelled" && (
              <Button variant="danger" className="px-2 py-1 text-xs" disabled={busy}
                      onClick={() => statusMut.mutate({ id: cb.id, status: "cancelled" })}>Cancel</Button>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <PageHeader title="Callbacks" />
      <div className="mb-4 flex items-center gap-2">
        <Label className="text-sm">Status</Label>
        <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as CallbackStatus | "")} className="w-44">
          <option value="">All</option>
          {ALL_STATUSES.map((s) => (<option key={s} value={s}>{s}</option>))}
        </Select>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>
      ) : (
        <DataTable columns={columns} data={data} searchPlaceholder="Search callbacks…" emptyTitle="No callbacks found" />
      )}
    </div>
  );
}

export default function CallbacksPage() {
  return <RequireHospital>{(hospitalId) => <CallbacksInner hospitalId={hospitalId} />}</RequireHospital>;
}
